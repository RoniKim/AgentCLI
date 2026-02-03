from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Sequence, Tuple, Any
import asyncio


def force_utf8_stdio() -> None:
    """Best-effort UTF-8 IO for Windows/CI.

    The original implementation sets several environment variables and
    reconfigures standard streams to use UTF-8 encoding. This helper
    preserves that behavior.
    """
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
    """Return the current time in ISO format (seconds precision)."""
    return datetime.now().isoformat(timespec="seconds")


def eprint(msg: str) -> None:
    """Print a message to stderr. Simplifies logging to the console."""
    print(msg, file=sys.stderr)


def run_cmd(cmd: Sequence[str], cwd: Path, timeout_sec: int = 600) -> Tuple[int, str]:
    """Run a subprocess and capture combined stdout and stderr.

    This synchronous helper remains available for backwards compatibility.
    It invokes ``subprocess.run`` with a timeout and returns a tuple
    ``(returncode, output)`` where output includes both stdout and stderr.
    """
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
        )
        out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT: {' '.join(cmd)}"


async def run_cmd_async(cmd: Sequence[str], cwd: Path, timeout_sec: int = 600) -> Tuple[int, str]:
    """Execute a command in a background thread to avoid blocking the event loop.

    This asynchronous wrapper delegates to :func:`run_cmd` using an executor.
    It should be used when running within an asyncio coroutine to prevent
    long-running subprocesses from blocking other tasks.

    Args:
        cmd: The command and its arguments to run.
        cwd: The working directory for the subprocess.
        timeout_sec: Maximum seconds to allow the process to run.

    Returns:
        A tuple of the return code and the combined stdout/stderr output.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: run_cmd(cmd, cwd, timeout_sec))


def read_text_robust(path: Path) -> tuple[str, str]:
    """Return ``(text, status)`` for the given path.

    The status is one of ``ok``, ``binary``, ``missing``, or ``error``.
    """
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
    """Load JSON from a file if it exists, otherwise return the default value."""
    try:
        if path.exists():
            import json
            raw = path.read_text(encoding="utf-8", errors="replace") or ""
            return json.loads(raw) if raw.strip() else default
    except Exception:
        pass
    return default


def ensure_relative_to_repo(repo: Path, maybe_rel: str) -> Path:
    """Resolve a path relative to the repository if it is not absolute."""
    p = Path(maybe_rel)
    return p.resolve() if p.is_absolute() else (repo / p).resolve()


def safe_write_text(path: Path, content: str) -> None:
    """Write text to a path, creating parent directories if necessary."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")