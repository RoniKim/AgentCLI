"""codex exec subprocess wrapper.

Replaces the OpenAI Agents SDK (Agent + Runner.run) with direct ``codex exec``
subprocess calls so that **all** token usage is billed through Codex credits
(ChatGPT subscription) instead of per-token API charges.

Usage::

    result = await codex_exec(
        "Summarise the project",
        instructions="You are a senior developer.",
        model="gpt-5.5",
        cwd=repo,
    )
    print(result.final_output)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Callable
from typing import Any

from .utils import has_quota_text


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class CodexExecResult:
    """Structured result from a ``codex exec`` invocation."""

    exit_code: int = 1
    final_output: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    error: str | None = None
    thread_id: str | None = None
    is_quota_exhausted: bool = False
    is_timeout: bool = False


# ---------------------------------------------------------------------------
# JSONL event parser (resilient — handles multiple Codex CLI versions)
# ---------------------------------------------------------------------------

def _parse_events(raw_lines: list[str]) -> tuple[list[dict[str, Any]], str, str | None, int, int]:
    """Parse ``codex exec --json`` JSONL output.

    Handles multiple event format versions:
    - thread.started / turn.completed / thread.completed (older)
    - item/completed / item/message (newer app-server format)
    - Generic message/response/text events

    Returns (events, final_output, thread_id, input_tokens, output_tokens).
    """
    events: list[dict[str, Any]] = []
    final_output = ""
    thread_id: str | None = None
    inp_tokens = 0
    out_tokens = 0
    # Collect all text parts for fallback assembly
    text_parts: list[str] = []

    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Non-JSON line — accumulate as plain text
            text_parts.append(line)
            continue
        if not isinstance(ev, dict):
            text_parts.append(str(ev))
            continue
        events.append(ev)

        ev_type = str(ev.get("type") or ev.get("event") or "").strip()

        # --- Format A: thread-level events (legacy / standard) ---
        if ev_type == "thread.started":
            thread_id = str(ev.get("thread_id") or ev.get("id") or thread_id or "")

        elif ev_type == "turn.completed":
            usage = ev.get("usage") or {}
            inp_tokens += int(usage.get("input_tokens") or 0)
            out_tokens += int(usage.get("output_tokens") or 0)

        elif ev_type == "thread.completed":
            final_output = str(ev.get("final_output") or ev.get("output") or final_output or "")
            total_usage = ev.get("total_usage") or ev.get("usage") or {}
            if total_usage:
                inp_tokens = int(total_usage.get("input_tokens") or inp_tokens)
                out_tokens = int(total_usage.get("output_tokens") or out_tokens)

        elif ev_type == "thread.failed":
            err = str(ev.get("error") or ev.get("message") or "")
            if err:
                final_output = err

        elif ev_type == "error":
            err = str(ev.get("error") or ev.get("message") or "")
            if err:
                final_output = err

        # --- Format B: item-based events (newer app-server) ---
        elif ev_type == "item/completed":
            item = ev.get("item") or {}
            item_type = str(item.get("type") or "")
            if item_type == "message":
                text = str(item.get("content") or item.get("text") or "")
                if text:
                    final_output = text
            elif item_type == "commandExecution":
                agg = str(item.get("aggregatedOutput") or "")
                if agg:
                    text_parts.append(agg)

        elif ev_type.startswith("item/message"):
            text = str(ev.get("text") or ev.get("content") or ev.get("delta") or "")
            if text:
                text_parts.append(text)

        # --- Format C: generic events ---
        elif ev_type in ("message", "response", "text"):
            text = str(ev.get("text") or ev.get("content") or ev.get("output") or "")
            if text:
                final_output = text

        # --- Usage from any event that carries it ---
        if not ev_type.startswith("turn.") and not ev_type.startswith("thread."):
            usage = ev.get("usage") or {}
            if usage:
                _i = int(usage.get("input_tokens") or 0)
                _o = int(usage.get("output_tokens") or 0)
                if _i > inp_tokens:
                    inp_tokens = _i
                if _o > out_tokens:
                    out_tokens = _o

    # Fallback: assemble text_parts if no structured final_output was found
    if not final_output and text_parts:
        final_output = "\n".join(text_parts).strip()

    return events, final_output, thread_id, inp_tokens, out_tokens


# ---------------------------------------------------------------------------
# Core async function
# ---------------------------------------------------------------------------

_PROMPT_FILE_THRESHOLD = 7000  # Legacy threshold (unused on Windows)

# On Windows, ALWAYS deliver prompts via stdin pipe.  The npm-installed
# codex CLI is a .CMD shim that passes %* through cmd.exe, which garbles
# Unicode / Korean text and special characters in CLI arguments.
_ALWAYS_USE_STDIN = os.name == "nt"


def _resolve_codex_path() -> str:
    """Find the ``codex`` executable, handling Windows .cmd wrappers."""
    found = shutil.which("codex")
    return found if found else "codex"


async def codex_exec(
    prompt: str,
    *,
    instructions: str = "",
    model: str = "gpt-5.5",
    reasoning_effort: str = "",
    full_auto: bool = False,
    cwd: Path | None = None,
    timeout_seconds: int = 900,
    env: dict[str, str] | None = None,
    heartbeat_callback: Callable[[], None] | None = None,
    heartbeat_interval_seconds: int = 120,
) -> CodexExecResult:
    """Run ``codex exec`` as an async subprocess and return structured results.

    Parameters
    ----------
    prompt:
        The user-facing prompt (task description).
    instructions:
        System instructions prepended to the prompt.
    model:
        Model identifier passed to ``codex exec -m``.
    reasoning_effort:
        Optional codex reasoning effort override. When provided, passed as
        ``-c model_reasoning_effort="<value>"``.
    full_auto:
        Pass ``--full-auto`` (unattended write/exec approval).
    cwd:
        Working directory for the subprocess.
    timeout_seconds:
        Hard timeout; the process is killed after this.
    env:
        Extra environment variables merged on top of ``os.environ``.
    heartbeat_callback:
        Optional callable invoked periodically while the subprocess runs.
        Used to update metrics mtime and prevent stalled-process false positives.
    heartbeat_interval_seconds:
        Interval between heartbeat callback invocations (default 120s).
    """

    # Task prompt first, instructions after — codex models treat the initial
    # message as an actionable request.  Putting instructions (role/rules) first
    # causes the model to enter "assistant-ready" mode instead of executing.
    full_prompt = f"{prompt}\n\n{instructions}".strip() if instructions else prompt

    codex_bin = _resolve_codex_path()

    # Build command
    cmd: list[str] = [codex_bin, "exec", "--json"]
    if model:
        cmd.extend(["-m", model])
    _effort = str(reasoning_effort or "").strip().lower()
    if _effort:
        cmd.extend(["-c", f'model_reasoning_effort="{_effort}"'])
    if full_auto:
        cmd.append("--full-auto")

    # Use --output-last-message (-o) for reliable output capture as JSONL parsing fallback
    last_msg_file: str | None = None
    try:
        fd, last_msg_file = tempfile.mkstemp(prefix="codex_out_", suffix=".txt")
        os.close(fd)
        cmd.extend(["--output-last-message", last_msg_file])
    except Exception:
        last_msg_file = None

    # Windows: always pipe via stdin to bypass .CMD shim argument garbling.
    # Other platforms: pipe only when prompt exceeds shell arg limits.
    use_stdin = _ALWAYS_USE_STDIN or len(full_prompt) > _PROMPT_FILE_THRESHOLD

    if use_stdin:
        # codex exec reads from stdin when `-` is passed as the prompt argument
        cmd.append("-")
    else:
        cmd.append(full_prompt)

    proc_env = dict(os.environ)
    if env:
        proc_env.update(env)

    t0 = time.time()
    result = CodexExecResult()
    proc: asyncio.subprocess.Process | None = None
    observed_child_pids: set[int] = set()

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.PIPE if use_stdin else asyncio.subprocess.DEVNULL,
            cwd=str(cwd) if cwd else None,
            env=proc_env,
        )

        # Register with process guard for cleanup
        try:
            from .process_guard import register_pid
            if proc.pid:
                register_pid(proc.pid)
        except Exception:
            pass

        tree_watch_task: asyncio.Task[None] | None = None
        if proc.pid:
            async def _tree_watch_loop(root_pid: int) -> None:
                while True:
                    try:
                        from .process_guard import process_descendant_pids

                        for child_pid in process_descendant_pids(root_pid):
                            if child_pid not in observed_child_pids:
                                observed_child_pids.add(child_pid)
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)

            tree_watch_task = asyncio.create_task(_tree_watch_loop(int(proc.pid)))

        stdin_bytes = full_prompt.encode("utf-8") if use_stdin else None

        # Periodic heartbeat to keep metrics mtime fresh
        hb_task: asyncio.Task[None] | None = None
        if heartbeat_callback:
            async def _hb_loop() -> None:
                while True:
                    await asyncio.sleep(heartbeat_interval_seconds)
                    try:
                        heartbeat_callback()
                    except Exception:
                        pass
            hb_task = asyncio.create_task(_hb_loop())

        async def _read_stream(stream: asyncio.StreamReader | None) -> bytes:
            if stream is None:
                return b""
            chunks: list[bytes] = []
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)

        async def _write_stdin() -> None:
            if proc is None or proc.stdin is None:
                return
            try:
                if stdin_bytes:
                    proc.stdin.write(stdin_bytes)
                    await proc.stdin.drain()
                proc.stdin.close()
            except Exception:
                pass

        def _terminate_observed_tree(*, include_root: bool) -> None:
            try:
                from .process_guard import terminate_process_tree

                if proc is not None and proc.pid:
                    terminate_process_tree(proc.pid, include_root=include_root)
                for child_pid in sorted(observed_child_pids, reverse=True):
                    terminate_process_tree(child_pid, include_root=True)
            except Exception:
                pass

        async def _collect_reader_output(
            stdout_task: asyncio.Task[bytes],
            stderr_task: asyncio.Task[bytes],
            *,
            timeout: float,
        ) -> tuple[bytes, bytes]:
            await asyncio.wait({stdout_task, stderr_task}, timeout=timeout)
            for task in (stdout_task, stderr_task):
                if not task.done():
                    task.cancel()

            def _result(task: asyncio.Task[bytes]) -> bytes:
                if not task.done() or task.cancelled():
                    return b""
                try:
                    return task.result()
                except Exception:
                    return b""

            return _result(stdout_task), _result(stderr_task)

        stdout_task = asyncio.create_task(_read_stream(proc.stdout))
        stderr_task = asyncio.create_task(_read_stream(proc.stderr))

        try:
            await _write_stdin()
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
                _terminate_observed_tree(include_root=False)
                stdout_bytes, stderr_bytes = await _collect_reader_output(
                    stdout_task,
                    stderr_task,
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                result.is_timeout = True
                _terminate_observed_tree(include_root=True)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except (asyncio.TimeoutError, Exception):
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    _terminate_observed_tree(include_root=True)
                stdout_bytes, stderr_bytes = await _collect_reader_output(
                    stdout_task,
                    stderr_task,
                    timeout=2.0,
                )
        finally:
            if hb_task is not None:
                hb_task.cancel()
            if tree_watch_task is not None:
                tree_watch_task.cancel()

        result.duration_seconds = time.time() - t0
        result.exit_code = proc.returncode if proc.returncode is not None else -1

        stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
        stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

        # Parse JSONL events from stdout
        if stdout_text.strip():
            raw_lines = stdout_text.strip().splitlines()
            events, final_output, thread_id, inp, out = _parse_events(raw_lines)
            result.events = events
            result.final_output = final_output
            result.thread_id = thread_id
            result.input_tokens = inp
            result.output_tokens = out

        # If no structured output, use raw stdout as final output
        if not result.final_output and stdout_text.strip():
            if not result.events:
                result.final_output = stdout_text.strip()

        # Fallback: read --output-last-message file if JSONL parsing didn't yield output
        if not result.final_output and last_msg_file:
            try:
                lm_path = Path(last_msg_file)
                if lm_path.exists() and lm_path.stat().st_size > 0:
                    result.final_output = lm_path.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                pass

        # Capture errors
        if stderr_text.strip():
            result.error = stderr_text.strip()

        if result.exit_code != 0 and not result.error:
            result.error = f"codex exec exited with code {result.exit_code}"

        if result.final_output.strip():
            try:
                from .structured import is_model_error_payload, model_error_message

                payload = json.loads(result.final_output)
                if is_model_error_payload(payload):
                    msg = model_error_message(payload) or "codex model error"
                    result.error = msg
                    if result.exit_code == 0:
                        result.exit_code = 1
            except Exception:
                pass

        # Quota detection
        combined = "\n".join(filter(None, [result.final_output, result.error or "", stderr_text]))
        result.is_quota_exhausted = has_quota_text(combined)

    except FileNotFoundError:
        result.duration_seconds = time.time() - t0
        result.exit_code = 127
        result.error = "codex CLI not found in PATH. Install: npm install -g @openai/codex"
    except Exception as exc:
        result.duration_seconds = time.time() - t0
        result.exit_code = -1
        result.error = str(exc)
        result.is_quota_exhausted = has_quota_text(str(exc))
    finally:
        try:
            from .process_guard import terminate_process_tree, unregister_pid_if_exited
        except Exception:
            terminate_process_tree = None  # type: ignore[assignment]
            unregister_pid_if_exited = None  # type: ignore[assignment]

        if proc is not None and proc.pid and terminate_process_tree is not None and unregister_pid_if_exited is not None:
            try:
                terminate_process_tree(proc.pid, include_root=proc.returncode is None)
                unregister_pid_if_exited(proc.pid)
            except Exception:
                pass
        if terminate_process_tree is not None and unregister_pid_if_exited is not None:
            for child_pid in sorted(observed_child_pids, reverse=True):
                try:
                    terminate_process_tree(child_pid, include_root=True)
                except Exception:
                    pass
        # Clean up temp files
        if last_msg_file:
            try:
                os.unlink(last_msg_file)
            except Exception:
                pass

    return result
