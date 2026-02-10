from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence, Tuple, Any, Optional, Iterable


STOP_REASON_QUOTA = "quota_exhausted"
STOP_REASON_STOP_FILE = "stop_file"
STOP_REASON_ALL_TASKS_DONE = "all_tasks_done"
STOP_REASON_PROJECT_COMPLETE = "project_complete"
STOP_REASON_PREPARED_ONLY = "prepared_only"
STOP_REASON_IDLE_EXIT = "idle_exit"
STOP_REASON_OK = "ok"

STOP_REASON_PRIORITY: list[str] = [
    STOP_REASON_QUOTA,
    STOP_REASON_STOP_FILE,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_ALL_TASKS_DONE,
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


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


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
        )
    except (OSError, FileNotFoundError) as e:
        return (127, str(e))
    truncated = False
    written = 0
    log_fh = log_path.open("ab")

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

    summary = ""
    try:
        while True:
            if stop_path is not None and stop_path.exists():
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                summary = "stopped"
                break
            if timeout_sec and (time.monotonic() - start) > timeout_sec:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                summary = "timeout"
                break
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.2)
        rc = await proc.wait()
    finally:
        await asyncio.gather(*reader_tasks, return_exceptions=True)
        log_fh.close()

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


def _has_quota_text(text: str) -> bool:
    s = (text or "").lower()
    if not s:
        return False
    needles = (
        "insufficient_quota",
        "quota exceeded",
        "exceeded your current quota",
        "billing hard limit",
        "hard limit",
        "payment required",
        "you've hit your usage limit",
        "purchase more credits",
        "upgrade to pro",
        "codex/settings/usage",
        "usage limit",
        "quota exhausted",
        "user limit",
        "user_limit",
        "credit balance is too low",
        "insufficient credits",
        "plans & billing",
        "purchase credits",
        "spend limit",
        "monthly spend limit",
        # Claude-specific patterns
        "usage cap",
        "reached your",
        "token limit exceeded",
        "account limit",
        "api key limit",
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


def detect_stop_reason(stop_paths: Sequence[Path]) -> str:
    """Detect stop reason from one of the provided stop files."""
    for path in stop_paths:
        try:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _has_quota_text(text):
                return STOP_REASON_QUOTA
            return STOP_REASON_STOP_FILE
        except Exception:
            continue
    return ""
