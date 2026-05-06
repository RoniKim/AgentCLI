from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from itertools import count
from pathlib import Path
from typing import Sequence, Tuple, Any, Optional, Iterable


STOP_REASON_QUOTA_UTILIZATION = "quota_utilization"
STOP_REASON_QUOTA = "quota_exhausted"
STOP_REASON_STOP_FILE = "stop_file"
STOP_REASON_ALL_TASKS_DONE = "all_tasks_done"
STOP_REASON_PROJECT_COMPLETE = "project_complete"
STOP_REASON_ALL_TASKS_ATTEMPTED = "all_tasks_attempted"
STOP_REASON_PREPARED_ONLY = "prepared_only"
STOP_REASON_NO_TASKS = "no_tasks"
STOP_REASON_PM_REFRESH_NO_BACKLOG = "pm_refresh_no_backlog"
STOP_REASON_IDLE_EXIT = "idle_exit"
STOP_REASON_OK = "ok"

STOP_REASON_PRIORITY: list[str] = [
    STOP_REASON_QUOTA,
    STOP_REASON_QUOTA_UTILIZATION,
    STOP_REASON_STOP_FILE,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_ALL_TASKS_DONE,
    STOP_REASON_ALL_TASKS_ATTEMPTED,
    STOP_REASON_PREPARED_ONLY,
    STOP_REASON_IDLE_EXIT,
    STOP_REASON_OK,
]


def choose_stop_reason(reasons: Iterable[str]) -> str:
    candidates = [str(r).strip() for r in reasons if str(r).strip()]
    if not candidates:
        return ""
    priority = {reason: idx for idx, reason in enumerate(STOP_REASON_PRIORITY)}
    best = candidates[0]
    best_rank = priority.get(best, len(priority))
    for r in candidates[1:]:
        rank = priority.get(r, len(priority))
        if rank < best_rank:
            best = r
            best_rank = rank
    return best


def force_utf8_stdio() -> None:
    """Best-effort UTF-8 IO for Windows/CI."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("LANG", "ko_KR.UTF-8")
    os.environ.setdefault("LC_ALL", "ko_KR.UTF-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def coerce_nonnegative_int(value: Any, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, int(default))


def loop_cycle_indices(loop_enabled: bool, loop_max_cycles: Any) -> Iterable[int]:
    """Return cycle indexes; loop_max_cycles <= 0 means unbounded loop mode."""
    if not loop_enabled:
        return range(1)
    max_cycles = coerce_nonnegative_int(loop_max_cycles, 0)
    if max_cycles > 0:
        return range(max_cycles)
    return count()


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def subprocess_close_fds_kwargs() -> dict[str, Any]:
    """Return subprocess kwargs with explicit child-handle inheritance policy.

    ``close_fds=True`` is intentional on Windows too. Python still wires the
    redirected stdio handles we pass explicitly, while preventing unrelated file
    handles from leaking into child processes. Runner subprocess tests assert
    this contract for the Windows launch paths that matter here.
    """
    kwargs: dict[str, Any] = {"close_fds": True}
    if sys.platform == "win32":
        kwargs["creationflags"] = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return kwargs


def run_cmd(cmd: Sequence[str], cwd: Path, timeout_sec: int = 600) -> Tuple[int, str]:
    """Run a subprocess and capture output (stdout+stderr)."""
    if not cmd:
        return (1, "empty command")
    try:
        r = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            stdin=subprocess.DEVNULL,
            **subprocess_close_fds_kwargs(),
        )
        out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT: {' '.join(cmd)}"
    except (OSError, FileNotFoundError) as e:
        return (127, str(e))


async def run_cmd_async(
    cmd: Sequence[str],
    cwd: Path,
    log_path: Path,
    *,
    timeout_sec: int = 600,
    stop_path: Optional[Path] = None,
    max_output_bytes: int = 10_000_000,
) -> tuple[int, str]:
    """Run a subprocess asynchronously, streaming output to log_path.

    Returns (returncode, summary). Output is streamed to disk with a hard cap; excess output is discarded
    and a TRUNCATED marker is appended once.
    """
    if not cmd:
        return (1, "empty command")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *list(cmd),
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            **subprocess_close_fds_kwargs(),
        )
    except (OSError, FileNotFoundError) as e:
        return (127, str(e))
    registered_pid = int(proc.pid) if proc.pid else None
    if registered_pid is not None:
        try:
            from .process_guard import register_pid

            register_pid(registered_pid)
        except Exception:
            pass
    observed_child_pids: set[int] = set()
    tree_watch_task: asyncio.Task[None] | None = None
    if registered_pid is not None:
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

        tree_watch_task = asyncio.create_task(_tree_watch_loop(registered_pid))
    truncated = False
    written = 0
    log_fh = log_path.open("ab")
    reader_tasks: list[asyncio.Task] = []
    summary = ""
    forced_rc: int | None = None

    async def _terminate_running_process(reason: str, *, rc: int) -> int:
        nonlocal summary
        summary = reason
        if proc.returncode is not None:
            try:
                await asyncio.wait_for(proc.wait(), timeout=1)
            except Exception:
                pass
            return rc
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                return rc
        return rc

    try:
        async def _reader(stream: asyncio.StreamReader, label: str) -> None:
            nonlocal written, truncated
            if stream is None:
                return
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                if written >= max_output_bytes:
                    if truncated:
                        return
                    log_fh.write(b"\n[TRUNCATED OUTPUT]\n")
                    truncated = True
                    continue
                remaining = max_output_bytes - written
                data = chunk[:remaining]
                log_fh.write(data)
                written += len(data)
                if len(chunk) > remaining and not truncated:
                    log_fh.write(b"\n[TRUNCATED OUTPUT]\n")
                    truncated = True

        reader_tasks = [
            asyncio.create_task(_reader(proc.stdout, "stdout")),
            asyncio.create_task(_reader(proc.stderr, "stderr")),
        ]

        while True:
            if stop_path is not None and stop_path.exists():
                forced_rc = await _terminate_running_process("stopped", rc=130)
                break
            if timeout_sec and (time.monotonic() - start) > timeout_sec:
                forced_rc = await _terminate_running_process("timeout", rc=124)
                break
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.2)
        rc = forced_rc if forced_rc is not None else await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        rc = await _terminate_running_process("timeout", rc=124)
    finally:
        # Ensure subprocess is terminated on any exit path (CancelledError, etc.)
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        if tree_watch_task is not None:
            tree_watch_task.cancel()
        if registered_pid is not None:
            try:
                from .process_guard import terminate_process_tree

                terminate_process_tree(registered_pid, include_root=proc.returncode is None)
            except Exception:
                pass
        if observed_child_pids:
            try:
                from .process_guard import terminate_process_tree

                for child_pid in sorted(observed_child_pids, reverse=True):
                    try:
                        terminate_process_tree(child_pid, include_root=True)
                    except Exception:
                        pass
            except Exception:
                pass
        if reader_tasks:
            done, pending = await asyncio.wait(reader_tasks, timeout=5)
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if done:
                await asyncio.gather(*done, return_exceptions=True)
        try:
            log_fh.flush()
        except Exception:
            pass
        log_fh.close()
        if registered_pid is not None:
            try:
                from .process_guard import unregister_pid_if_exited

                unregister_pid_if_exited(registered_pid)
            except Exception:
                pass
    if truncated:
        summary = (summary + " " if summary else "") + "truncated"
    if not summary:
        summary = "ok"
    return rc, summary


def read_text_robust(path: Path) -> tuple[str, str]:
    """Return (text, status). status is ok|binary|missing|error."""
    if not path.exists():
        return "", "missing"
    try:
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            return "", "binary"
        return data.decode("utf-8", errors="replace"), "ok"
    except Exception:
        return "", "error"


def load_json_if_exists(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            import json
            raw = path.read_text(encoding="utf-8", errors="replace") or ""
            return json.loads(raw) if raw.strip() else default
    except Exception:
        pass
    return default


def ensure_relative_to_repo(repo: Path, maybe_rel: str) -> Path:
    p = Path(maybe_rel)
    resolved = p.resolve() if p.is_absolute() else (repo / p).resolve()
    try:
        resolved.relative_to(repo.resolve())
    except Exception as ex:
        raise ValueError(f"Path escapes repo: {maybe_rel}") from ex
    return resolved


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", errors="replace", delete=False, dir=str(path.parent)) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, data)


def safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")


def rotate_log_file(
    path: Path,
    *,
    max_bytes: int = 5_000_000,
    backup_count: int = 5,
    max_age_days: int = 14,
) -> None:
    """Rotate log file by size and prune aged/overflow backups.

    Backups are stored as ``<name>.1``, ``<name>.2``, ... where ``.1`` is newest.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        return

    try:
        size = int(path.stat().st_size) if path.exists() else 0
    except Exception:
        size = 0

    max_bytes_i = max(1_024, int(max_bytes))
    backup_count_i = max(1, int(backup_count))
    max_age_days_i = max(0, int(max_age_days))

    # Size-based rotation.
    if size >= max_bytes_i and path.exists():
        try:
            oldest = Path(str(path) + f".{backup_count_i}")
            if oldest.exists():
                oldest.unlink(missing_ok=True)
        except Exception:
            pass

        for idx in range(backup_count_i - 1, 0, -1):
            src = Path(str(path) + f".{idx}")
            dst = Path(str(path) + f".{idx + 1}")
            try:
                if src.exists():
                    src.replace(dst)
            except Exception:
                pass

        try:
            path.replace(Path(str(path) + ".1"))
        except Exception:
            pass

    # Time/count-based retention for numeric backups.
    threshold = time.time() - (max_age_days_i * 86_400) if max_age_days_i > 0 else None
    prefix = path.name + "."
    try:
        for cand in path.parent.glob(path.name + ".*"):
            suffix = cand.name[len(prefix):]
            if not suffix.isdigit():
                continue
            idx = int(suffix)
            delete = idx > backup_count_i
            if not delete and threshold is not None:
                try:
                    delete = cand.stat().st_mtime < threshold
                except Exception:
                    delete = False
            if delete:
                try:
                    cand.unlink(missing_ok=True)
                except Exception:
                    pass
    except Exception:
        pass


def _has_quota_text(text: str) -> bool:
    """Canonical quota/billing/rate-limit needle list.

    This is the **single source of truth** — backends should call
    ``has_quota_text`` instead of maintaining their own needle lists.
    """
    s = (text or "").lower()
    if not s:
        return False
    needles = (
        # OpenAI / generic billing
        "insufficient_quota",
        "quota exceeded",
        "exceeded your current quota",
        "quota exhausted",
        "billing hard limit",
        "hard limit",
        "plan and billing",
        "plans & billing",
        "payment required",
        "budgetexceeded",
        # Usage-limit strings (Codex / CLI)
        "you've hit your usage limit",
        "you've hit your limit",
        "hit your limit",
        "purchase more credits",
        "upgrade to pro",
        "codex/settings/usage",
        "usage limit",
        "user limit",
        "user_limit",
        "credit balance is too low",
        "insufficient credits",
        "purchase credits",
        "spend limit",
        "monthly spend limit",
        # Claude Code CLI rate-limit patterns
        "usage cap",
        "reached your",
        "token limit exceeded",
        "account limit",
        "api key limit",
        "limit resets",
    )
    return any(n in s for n in needles)




# Public alias (used by backends) for quota/credits text detection
has_quota_text = _has_quota_text


def write_heartbeat(run_dir: Path) -> None:
    """Write a HEARTBEAT file for external monitoring."""
    try:
        (run_dir / "HEARTBEAT").write_text(now_iso() + "\n", encoding="utf-8")
    except Exception:
        pass


def severity_at_or_above(found: str, threshold: str) -> bool:
    """Return True if *found* severity is at or above *threshold*."""
    order = {"low": 0, "medium": 1, "high": 2}
    return order.get(found, 1) >= order.get(threshold, 1)


def budget_exceeded(key: str, current: int, limit: int) -> bool:
    """Return True if *current* has reached or exceeded *limit* (0 means unlimited)."""
    if limit <= 0:
        return False
    return current >= limit


def is_unsafe_path(raw: str) -> bool:
    """Return True if *raw* contains path-traversal patterns."""
    try:
        return ".." in Path(raw).parts
    except Exception:
        return True


def hash_prompt(text: str) -> str:
    """Return a short SHA-256 digest of *text* (10 hex chars)."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:10]


# All known stop reason constant values for direct STOP-file matching
_KNOWN_STOP_REASONS: frozenset[str] = frozenset({
    STOP_REASON_QUOTA,
    STOP_REASON_QUOTA_UTILIZATION,
    STOP_REASON_STOP_FILE,
    STOP_REASON_ALL_TASKS_DONE,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_ALL_TASKS_ATTEMPTED,
    STOP_REASON_PREPARED_ONLY,
    STOP_REASON_NO_TASKS,
    STOP_REASON_PM_REFRESH_NO_BACKLOG,
    STOP_REASON_IDLE_EXIT,
    STOP_REASON_OK,
})


def detect_stop_reason(stop_paths: Sequence[Path]) -> str:
    """Detect stop reason from one of the provided stop files.

    If the file content exactly matches a known stop reason constant,
    return that constant directly.  Otherwise fall back to quota-text
    heuristic or generic ``STOP_REASON_STOP_FILE``.
    """
    for path in stop_paths:
        try:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            normalized = text.strip().lower()
            if normalized in _KNOWN_STOP_REASONS:
                return normalized
            if _has_quota_text(text):
                return STOP_REASON_QUOTA
            return STOP_REASON_STOP_FILE
        except Exception:
            continue
    return ""
