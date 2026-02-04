from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..docs import load_dotenv_best_effort
from ..gates import run_build_gate, run_test_gate
from ..gitops import (
    git_head,
    git_changed_files,
    git_porcelain,
    repo_fingerprint,
    create_checkpoint,
    restore_checkpoint,
    RepoCheckpoint,
)
from ..inventory import build_repo_inventory, write_repo_inventory_files
from ..metrics import MetricsLogger
from ..pipeline import PipelineManager, make_stages
from ..pipeline.session import PipelineSession
from ..pipeline.stages.base import StageOutcome
from ..run_dir import make_run_dir, find_latest_run_dir
from ..schemas import pm_output_json_schema
from ..state import (
    load_backlog_json,
    parse_backlog_md,
    load_state,
    save_state,
    write_backlog_files,
    mark_backlog_done,
    write_default_p0_backlog,
    TaskItem,
)
from ..structured import parse_pm_output, dump_pretty, describe_parse_failure
from ..utils import force_utf8_stdio, eprint


class StopRequested(Exception):
    pass


def _as_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, list):
        out: list[str] = []
        for it in val:
            s = str(it).strip()
            if s:
                out.append(s)
        return out
    s = str(val).strip()
    if not s:
        return []
    if "," in s:
        return [p.strip() for p in s.split(",") if p.strip()]
    return [p for p in s.split() if p]


def _norm_backend_str(s: str) -> str:
    return (s or "").strip().lower().replace("-", "").replace("_", "")


def _parse_setting_sources(val: Any) -> Optional[list[str]]:
    # Claude SDK default is None (meaning: do NOT load filesystem settings)
    xs = _as_list(val)
    if not xs:
        return None
    norm: list[str] = []
    for x in xs:
        x = (x or "").strip().lower()
        if not x:
            continue
        if x in {"user", "project", "local"}:
            norm.append(x)
    return norm or None


@dataclass
class ClaudeCodeConfig:
    model: Optional[str]
    permission_mode: Optional[str]
    max_turns: Optional[int]
    allowed_tools_pm: list[str]
    disallowed_tools_pm: list[str]
    allowed_tools_dev: list[str]
    disallowed_tools_dev: list[str]
    allowed_tools_qa: list[str]
    disallowed_tools_qa: list[str]
    system_prompt_append: str
    setting_sources: Optional[list[str]]
    continue_conversation: bool
    resume: Optional[str]
    enable_file_checkpointing: bool


def _load_claudecode_cfg(args: argparse.Namespace) -> ClaudeCodeConfig:
    model = getattr(args, "claudecode_model", None)
    permission_mode = getattr(args, "claudecode_permission_mode", None)
    max_turns = getattr(args, "claudecode_max_turns", None)

    allowed_tools_pm = _as_list(getattr(args, "claudecode_pm_allowed_tools", None))
    disallowed_tools_pm = _as_list(getattr(args, "claudecode_pm_disallowed_tools", None))
    allowed_tools_dev = _as_list(getattr(args, "claudecode_dev_allowed_tools", None))
    disallowed_tools_dev = _as_list(getattr(args, "claudecode_dev_disallowed_tools", None))
    allowed_tools_qa = _as_list(getattr(args, "claudecode_qa_allowed_tools", None))
    disallowed_tools_qa = _as_list(getattr(args, "claudecode_qa_disallowed_tools", None))

    system_prompt_append = str(getattr(args, "claudecode_system_prompt_append", "") or "")
    setting_sources = _parse_setting_sources(getattr(args, "claudecode_setting_sources", None))

    continue_conversation = bool(getattr(args, "claudecode_continue_conversation", False))
    resume = getattr(args, "claudecode_resume", None)
    enable_file_checkpointing = bool(getattr(args, "claudecode_enable_file_checkpointing", False))

    return ClaudeCodeConfig(
        model=str(model).strip() or None,
        permission_mode=str(permission_mode).strip() or None,
        max_turns=int(max_turns) if isinstance(max_turns, int) and max_turns > 0 else None,
        allowed_tools_pm=allowed_tools_pm,
        disallowed_tools_pm=disallowed_tools_pm,
        allowed_tools_dev=allowed_tools_dev,
        disallowed_tools_dev=disallowed_tools_dev,
        allowed_tools_qa=allowed_tools_qa,
        disallowed_tools_qa=disallowed_tools_qa,
        system_prompt_append=system_prompt_append,
        setting_sources=setting_sources,
        continue_conversation=continue_conversation,
        resume=str(resume).strip() or None,
        enable_file_checkpointing=enable_file_checkpointing,
    )


def _pick_run_dir(args: argparse.Namespace, repo: Path) -> Path:
    if getattr(args, "run_dir", ""):
        return Path(args.run_dir).expanduser().resolve()
    if bool(getattr(args, "resume_latest", False)):
        latest = find_latest_run_dir(repo)
        return latest.expanduser().resolve() if latest is not None else make_run_dir(repo)

    latest = find_latest_run_dir(repo)
    if latest is not None and (bool(getattr(args, "loop", False)) or bool(getattr(args, "continuous", False))):
        eprint(f"[WARN] No --run-dir specified. A previous run exists: {latest}. Use --resume-latest or --run-dir to resume.")
    return make_run_dir(repo)


def _pm_prompt(repo: Path, run_dir: Path, inv_md: Path, changed_files: list[str], head: str) -> str:
    # Keep the prompt short; Claude Code will read files as needed.
    cf = "\n".join([f"- {x}" for x in changed_files[:200]]) if changed_files else "(none)"
    return (
        "You are the PM agent for an automated dev loop.\n"
        "Goal: produce a prioritized engineering backlog for this repository.\n\n"
        f"Repository root: {repo.as_posix()}\n"
        f"Run directory: {run_dir.as_posix()}\n"
        f"Git HEAD: {head}\n\n"
        "Repository inventory is available here (read it first):\n"
        f"- {inv_md.as_posix()}\n\n"
        "Recent changed files (if any):\n"
        f"{cf}\n\n"
        "Requirements:\n"
        "- Output MUST be valid JSON matching the provided schema (no markdown fences).\n"
        "- Create 3~10 tasks, each with clear prompts and acceptance criteria (done_when).\n"
        "- Prefer safe, incremental improvements; avoid speculative rewrites.\n"
        "- Use repo conventions; if tests/build exist, include them in done_when.\n"
    )


def _dev_prompt(repo: Path, task: TaskItem, build_cmd: Any, test_cmd: Any) -> str:
    bc = str(build_cmd) if build_cmd else "(auto)"
    tc = str(test_cmd) if test_cmd else "(auto)"
    files_hint = "\n".join([f"- {x}" for x in (task.files or [])]) if task.files else "(not specified)"
    return (
        "You are the Dev agent. Implement exactly ONE backlog task.\n\n"
        f"Repository root: {repo.as_posix()}\n\n"
        f"Task ID: {task.id}\n"
        f"Title: {task.title}\n\n"
        "Task instructions:\n"
        f"{task.prompt}\n\n"
        "Files to focus (if provided):\n"
        f"{files_hint}\n\n"
        "Definition of done:\n"
        f"{task.done_when}\n\n"
        "Constraints:\n"
        "- Make minimal, targeted changes; avoid unrelated refactors.\n"
        "- Prefer editing existing code over adding new dependencies.\n"
        "- Keep logs concise.\n\n"
        "After implementing, run build/tests if available:\n"
        f"- build_cmd: {bc}\n"
        f"- test_cmd:  {tc}\n\n"
        "Finally, reply with a short summary and the list of changed files.\n"
    )


def _qa_prompt(repo: Path, run_dir: Path, done_ids: list[str]) -> str:
    dids = ", ".join(done_ids) if done_ids else "(none)"
    return (
        "You are the QA agent. Review the latest changes in the repository and identify issues.\n\n"
        f"Repository root: {repo.as_posix()}\n"
        f"Run directory: {run_dir.as_posix()}\n"
        f"Tasks completed in this cycle: {dids}\n\n"
        "Instructions:\n"
        "- Use git diff / file inspection to verify changes are correct.\n"
        "- Focus on correctness, regression risk, and missing edge cases.\n"
        "- Output a concise markdown report with: Findings, Suggested Fixes, Risk Level.\n"
    )


async def _drain_messages(client: Any, *, stop_path: Path, debug: bool = False) -> str:
    """Drain streaming messages into a single text blob.

    IMPORTANT: Claude SDK warns against breaking early in the iterator.
    """

    try:
        from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage  # type: ignore
    except Exception:
        AssistantMessage = object  # type: ignore
        TextBlock = object  # type: ignore
        ResultMessage = object  # type: ignore

    parts: list[str] = []
    # Prefer receive_response() if present; fallback to receive_messages().
    recv = getattr(client, "receive_response", None) or getattr(client, "receive_messages", None)
    if recv is None:
        raise RuntimeError("ClaudeSDKClient missing receive_response/receive_messages")

    async for msg in recv():
        if stop_path.exists():
            # Best-effort interrupt.
            intr = getattr(client, "interrupt", None)
            if intr is not None:
                try:
                    await intr()
                except Exception:
                    pass
            raise StopRequested()

        # Extract assistant text blocks.
        if isinstance(msg, AssistantMessage):
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, TextBlock):
                        t = getattr(block, "text", "")
                        if t:
                            parts.append(str(t))
                            if debug:
                                eprint(f"[claude] {t}")
        elif isinstance(msg, ResultMessage):
            # ResultMessage may contain final output; stringify as fallback.
            try:
                parts.append(str(msg))
            except Exception:
                pass
        else:
            # Fallback: stringify other message types (tool calls etc.) only in debug.
            if debug:
                try:
                    eprint(f"[claude-msg] {msg}")
                except Exception:
                    pass

    return "\n".join([p for p in parts if p.strip()]).strip()


def _build_options(cfg: ClaudeCodeConfig, *, repo: Path, stage: str) -> Any:
    """Build ClaudeAgentOptions for a given stage."""

    from claude_agent_sdk import ClaudeAgentOptions  # type: ignore

    if stage == "PM":
        allowed_tools = cfg.allowed_tools_pm
        disallowed_tools = cfg.disallowed_tools_pm
        permission_mode = "default"  # PM should not need write permissions
    elif stage == "QA":
        allowed_tools = cfg.allowed_tools_qa
        disallowed_tools = cfg.disallowed_tools_qa
        permission_mode = "default"
    else:
        allowed_tools = cfg.allowed_tools_dev
        disallowed_tools = cfg.disallowed_tools_dev
        permission_mode = cfg.permission_mode or "acceptEdits"

    # Default tool sets if user didn't configure anything.
    if not allowed_tools:
        if stage == "PM":
            allowed_tools = ["Read", "Grep", "Glob"]
        elif stage == "QA":
            allowed_tools = ["Read", "Grep", "Glob", "Bash"]
        else:
            allowed_tools = ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]

    # Claude Code system prompt preset + optional append.
    system_prompt: Any = {"type": "preset", "preset": "claude_code"}
    if cfg.system_prompt_append:
        system_prompt["append"] = cfg.system_prompt_append

    # Output format: PM uses json schema.
    output_format = None
    if stage == "PM":
        output_format = {"type": "json_schema", "schema": pm_output_json_schema()}

    return ClaudeAgentOptions(
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        system_prompt=system_prompt,
        permission_mode=permission_mode,
        model=cfg.model,
        max_turns=cfg.max_turns,
        cwd=repo,
        setting_sources=cfg.setting_sources,
        continue_conversation=cfg.continue_conversation,
        resume=cfg.resume,
        enable_file_checkpointing=cfg.enable_file_checkpointing,
        output_format=output_format,
        include_partial_messages=False,
    )


async def main_async_claudecode(args: argparse.Namespace, repo: Path) -> int:
    """Claude Code execution backend.

    This backend uses the Claude Agent SDK (Python) which delegates tool execution to the Claude Code CLI.
    """

    force_utf8_stdio()

    # Load env (.env) best-effort; ANTHROPIC_API_KEY is required.
    env_debug = load_dotenv_best_effort(repo, explicit_env_file=getattr(args, "env_file", ""), override=True)
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        eprint("ERROR: ANTHROPIC_API_KEY is not set.")
        eprint("Tried loading .env from:")
        for pth in env_debug.get("tried", []):
            eprint(f" - {pth}")
        eprint("Loaded from:")
        for pth in env_debug.get("loaded", []):
            eprint(f" - {pth}")
        eprint("Fix: set ANTHROPIC_API_KEY env var, or pass --env-file /path/to/.env")
        return 2

    if shutil.which("claude") is None:
        eprint("ERROR: 'claude' CLI not found on PATH.")
        eprint("Install Claude Code and ensure the 'claude' executable is available.")
        return 2

    try:
        from claude_agent_sdk import ClaudeSDKClient  # type: ignore
    except Exception:
        eprint("Missing dependency: claude-agent-sdk. Install: pip install -U claude-agent-sdk")
        return 2

    # Ensure tools run inside repo
    os.chdir(repo)

    cfg = _load_claudecode_cfg(args)

    run_dir = _pick_run_dir(args, repo)
    run_dir.mkdir(parents=True, exist_ok=True)
    stop_file = str(getattr(args, "stop_file", "") or "STOP")
    stop_path = run_dir / stop_file

    metrics = MetricsLogger(run_dir / "metrics.jsonl", enabled=bool(getattr(args, "debug", False)))

    # Pipeline
    roles = str(getattr(args, "roles", "PM,Dev,QA") or "PM,Dev,QA")
    stages = make_stages(roles)
    pipeline_mgr = PipelineManager(stages)

    state_path = run_dir / "STATE.json"
    backlog_json_path = run_dir / "BACKLOG.json"
    backlog_md_path = run_dir / "BACKLOG.md"

    async def ensure_backlog() -> bool:
        return backlog_json_path.exists() or backlog_md_path.exists()

    async def load_tasks() -> list[TaskItem]:
        if backlog_json_path.exists():
            return load_backlog_json(backlog_json_path)
        if backlog_md_path.exists():
            return parse_backlog_md(backlog_md_path)
        return []

    # Small cache file to skip PM when repo fingerprint unchanged.
    cache_path = run_dir / "CLAUDECODE_CACHE.json"
    cache_obj: dict[str, Any] = {}
    if cache_path.exists():
        try:
            cache_obj = json.loads(cache_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            cache_obj = {}

    pm_cache_dir = repo / ".doc" / "PM_CACHE"

    async def pm_phase(cycle_idx: int) -> StageOutcome:
        # Skip PM if backlog exists and fingerprint unchanged (unless refresh requested)
        refresh_backlog = bool(getattr(args, "pm_refresh_backlog", False))
        every = int(getattr(args, "pm_refresh_every_cycles", 0) or 0)
        if every > 0 and cycle_idx > 0 and (cycle_idx % every) == 0:
            refresh_backlog = True

        head = git_head(repo)
        changed_files = git_changed_files(repo, include_worktree=bool(getattr(args, "pm_include_working_tree", False)))
        fp = repo_fingerprint(repo)

        last_fp = str(cache_obj.get("repo_fp", "") or "")
        if (not refresh_backlog) and (await ensure_backlog()) and last_fp and (last_fp == fp):
            return StageOutcome(action="skip", reason="pm_skip_fingerprint_unchanged")

        # Build inventory files for Claude
        inv = build_repo_inventory(repo)
        inv_json, inv_md = write_repo_inventory_files(repo, pm_cache_dir, inv)

        prompt = _pm_prompt(repo, run_dir, inv_md, changed_files, head)

        options = _build_options(cfg, repo=repo, stage="PM")

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                text = await _drain_messages(client, stop_path=stop_path, debug=bool(getattr(args, "debug", False)))
        except StopRequested:
            return StageOutcome(action="stop", reason="stop_requested", rc=130)
        except Exception as ex:
            eprint(f"[PM] Claude error: {ex}")
            if bool(getattr(args, "debug", False)):
                eprint(traceback.format_exc())
            # Fallback backlog: generic, safe
            write_default_p0_backlog(run_dir)
            return StageOutcome(action="continue", reason="pm_failed_default_backlog")

        pm_out = parse_pm_output(text, kind_hint="bootstrap")
        if pm_out is None:
            describe_parse_failure("PM", text)
            write_default_p0_backlog(run_dir)
            return StageOutcome(action="continue", reason="pm_parse_failed_default_backlog")

        # Write backlog files
        tasks_dicts: list[dict[str, Any]] = []
        try:
            for t in pm_out.tasks:
                tasks_dicts.append(t.model_dump())  # pydantic v2
        except Exception:
            try:
                tasks_dicts = [dict(t) for t in pm_out.tasks]  # type: ignore
            except Exception:
                tasks_dicts = []

        if not tasks_dicts:
            write_default_p0_backlog(run_dir)
            return StageOutcome(action="continue", reason="pm_empty_default_backlog")

        write_backlog_files(run_dir, tasks_dicts)
        # Cache fingerprint
        cache_obj["repo_fp"] = fp
        cache_obj["head"] = head
        cache_obj["inv_md"] = inv_md.as_posix()
        cache_obj["inv_json"] = inv_json.as_posix()
        cache_path.write_text(json.dumps(cache_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        return StageOutcome(action="continue", reason="pm_ok")

    async def dev_phase(cycle_idx: int) -> StageOutcome:
        if not await ensure_backlog():
            return StageOutcome(action="stop", reason="no_backlog", rc=2)

        tasks = await load_tasks()
        if not tasks:
            return StageOutcome(action="stop", reason="no_tasks", rc=0)

        state = load_state(state_path)
        done_set = set(state.get("done", []))
        before_done_count = len(done_set)

        # Behavior knobs
        iterations = int(getattr(args, "iterations", 5) or 5)
        stop_on_no_diff = not bool(getattr(args, "allow_no_diff", False))

        build_enabled = (not bool(getattr(args, "no_build", False))) or bool(getattr(args, "require_build", False))
        run_tests = bool(getattr(args, "run_tests", False))

        build_cmd = getattr(args, "build_cmd", [])
        test_cmd = getattr(args, "test_cmd", [])

        options = _build_options(cfg, repo=repo, stage="Dev")

        completed_this_cycle: list[str] = []

        async with ClaudeSDKClient(options=options) as client:
            # Run up to N tasks
            for _ in range(iterations):
                if stop_path.exists():
                    return StageOutcome(action="stop", reason="stop_requested", rc=130)

                next_task = None
                for t in tasks:
                    if t.id not in done_set:
                        next_task = t
                        break
                if next_task is None:
                    # all done
                    save_state(state_path, state)
                    return StageOutcome(action="stop", reason="all_tasks_done", rc=0)

                before = git_porcelain(repo)
                cp: Optional[RepoCheckpoint] = None
                try:
                    cp = create_checkpoint(repo, label=next_task.id)
                except Exception:
                    cp = None

                prompt = _dev_prompt(repo, next_task, build_cmd, test_cmd)

                try:
                    await client.query(prompt)
                    _ = await _drain_messages(client, stop_path=stop_path, debug=bool(getattr(args, "debug", False)))
                except StopRequested:
                    if cp:
                        restore_checkpoint(repo, cp)
                    return StageOutcome(action="stop", reason="stop_requested", rc=130)
                except Exception as ex:
                    eprint(f"[DEV] Claude error: {ex}")
                    if bool(getattr(args, "debug", False)):
                        eprint(traceback.format_exc())
                    if cp:
                        restore_checkpoint(repo, cp)
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "exception", "detail": str(ex)})
                    save_state(state_path, state)
                    return StageOutcome(action="stop", reason="dev_exception", rc=1)

                after = git_porcelain(repo)
                changed = (before != after)
                if stop_on_no_diff and (not changed):
                    eprint(f"[STOP] No diff produced for {next_task.id}.")
                    if cp:
                        restore_checkpoint(repo, cp)
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "no_diff"})
                    save_state(state_path, state)
                    return StageOutcome(action="stop", reason="no_diff", rc=1)

                # Gates (run in background thread so we don't block event loop)
                if build_enabled:
                    ok = await asyncio.to_thread(
                        run_build_gate,
                        repo=repo,
                        build_cmd=build_cmd,
                        build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800) or 1800),
                        legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                        log_path=(run_dir / "attempts" / next_task.id / "build.txt"),
                    )
                    if not ok:
                        eprint(f"[STOP] Build failed after {next_task.id}.")
                        if cp:
                            restore_checkpoint(repo, cp)
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "build_failed"})
                        save_state(state_path, state)
                        return StageOutcome(action="stop", reason="build_failed", rc=1)

                if run_tests:
                    ok = await asyncio.to_thread(
                        run_test_gate,
                        repo=repo,
                        test_cmd=test_cmd,
                        test_timeout_sec=int(getattr(args, "test_timeout_seconds", 3600) or 3600),
                        legacy_test_target=str(getattr(args, "dotnet_test_target", "") or ""),
                        legacy_test_filter=str(getattr(args, "dotnet_test_filter", "") or ""),
                        log_path=(run_dir / "attempts" / next_task.id / "test.txt"),
                    )
                    if not ok:
                        eprint(f"[STOP] Tests failed after {next_task.id}.")
                        if cp:
                            restore_checkpoint(repo, cp)
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "test_failed"})
                        save_state(state_path, state)
                        return StageOutcome(action="stop", reason="test_failed", rc=1)

                # Mark done
                done_set.add(next_task.id)
                state.setdefault("done", []).append(next_task.id)
                save_state(state_path, state)
                try:
                    mark_backlog_done(backlog_md_path, next_task.id)
                except Exception:
                    pass

                completed_this_cycle.append(next_task.id)

        # After loop ends naturally
        save_state(state_path, state)
        if len(done_set) > before_done_count:
            return StageOutcome(action="continue", reason="tasks_completed", meta={"done": completed_this_cycle})
        return StageOutcome(action="continue", reason="no_tasks_completed")

    async def qa_phase(cycle_idx: int) -> StageOutcome:
        # Run QA only if configured or tasks happened.
        qa_always = bool(getattr(args, "qa_always", False))

        state = load_state(state_path)
        done_ids: list[str] = []
        try:
            done_ids = list(state.get("done", []))
        except Exception:
            done_ids = []

        if (not qa_always) and (not done_ids):
            return StageOutcome(action="skip", reason="qa_skip_no_done")

        prompt = _qa_prompt(repo, run_dir, done_ids[-10:])
        options = _build_options(cfg, repo=repo, stage="QA")

        try:
            async with ClaudeSDKClient(options=options) as client:
                await client.query(prompt)
                md = await _drain_messages(client, stop_path=stop_path, debug=bool(getattr(args, "debug", False)))
        except StopRequested:
            return StageOutcome(action="stop", reason="stop_requested", rc=130)
        except Exception as ex:
            eprint(f"[QA] Claude error: {ex}")
            if bool(getattr(args, "debug", False)):
                eprint(traceback.format_exc())
            return StageOutcome(action="continue", reason="qa_failed")

        out_path = run_dir / "QA_REPORT.md"
        out_path.write_text(md + "\n", encoding="utf-8", errors="replace")
        return StageOutcome(action="continue", reason="qa_ok")

    session = PipelineSession(
        args=args,
        repo=repo,
        run_dir=run_dir,
        stop_path=stop_path,
        ensure_backlog=ensure_backlog,
        load_tasks=load_tasks,
        pm_phase=pm_phase,
        dev_phase=dev_phase,
        qa_phase=qa_phase,
    )

    async def run_one_cycle(cycle_idx: int) -> StageOutcome:
        head = git_head(repo)
        changed = git_changed_files(repo, include_worktree=bool(getattr(args, "pm_include_working_tree", False)))
        fp = repo_fingerprint(repo)
        try:
            out = await pipeline_mgr.run_cycle(session, cycle_idx=cycle_idx, head=head, changed_files=changed, repo_fp=fp)
            return out
        except StopRequested:
            return StageOutcome(action="stop", reason="stop_requested", rc=130)

    # ---- Execution modes ----
    loop = bool(getattr(args, "loop", False))
    continuous = bool(getattr(args, "continuous", False))
    sleep_s = int(getattr(args, "loop_sleep_seconds", 60) or 60)
    max_cycles = int(getattr(args, "loop_max_cycles", 0) or 0)

    if loop or continuous:
        cycle_idx = 0
        while True:
            if stop_path.exists():
                return 0
            out = await run_one_cycle(cycle_idx)
            if out.action == "stop":
                return int(out.rc)
            cycle_idx += 1
            if (max_cycles > 0) and (cycle_idx >= max_cycles):
                return 0
            await asyncio.sleep(max(1, sleep_s))

    # Single run
    out = await run_one_cycle(0)
    return int(out.rc)
