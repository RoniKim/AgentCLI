from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple


def force_utf8_stdio() -> None:
    """Best-effort UTF-8 IO for Windows/CI."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # Locale envs are best-effort (may not exist on some environments)
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
    """Run a subprocess and capture combined stdout+stderr (sync).

    NOTE: Prefer :func:`run_cmd_async` when running inside asyncio.
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
        return int(r.returncode), out.strip()
    except subprocess.TimeoutExpired as te:
        out = (te.stdout or "") + ("\n" + te.stderr if te.stderr else "")
        return 124, (out + f"\n[TIMEOUT] exceeded {timeout_sec}s\n").strip()
    except Exception as ex:
        return 1, f"[EXCEPTION] {type(ex).__name__}: {ex}".strip()


@dataclass
class _OutputLimiter:
    """Keep a bounded amount of subprocess output.

    This prevents runaway logs (e.g., huge build output) from exploding disk usage.
    We keep a head + tail and insert a truncation banner in the middle.
    """

    max_bytes: int = 2_000_000  # ~2MB

    def __post_init__(self) -> None:
        self.head_max = max(0, self.max_bytes // 2)
        self.tail_max = max(0, self.max_bytes - self.head_max)
        self._head = bytearray()
        self._tail = bytearray()
        self._truncated = False

    def add(self, chunk: bytes) -> None:
        if not chunk or self.max_bytes <= 0:
            return

        # fill head first
        if len(self._head) < self.head_max:
            take = min(self.head_max - len(self._head), len(chunk))
            self._head.extend(chunk[:take])
            chunk = chunk[take:]

        if not chunk:
            return

        # keep tail (rolling)
        self._truncated = True
        if self.tail_max <= 0:
            return
        if len(chunk) >= self.tail_max:
            self._tail = bytearray(chunk[-self.tail_max :])
        else:
            combined = self._tail + chunk
            self._tail = bytearray(combined[-self.tail_max :])

    def render_text(self) -> str:
        if not self._truncated:
            return bytes(self._head).decode("utf-8", errors="replace")
        banner = (
            f"\n... output truncated (kept ~{self.head_max}B head + ~{self.tail_max}B tail) ...\n"
        ).encode("utf-8", errors="replace")
        data = bytes(self._head) + banner + bytes(self._tail)
        return data.decode("utf-8", errors="replace")


async def _kill_process_tree_windows(pid: int, *, timeout_sec: float = 10.0) -> None:
    """Best-effort kill process tree on Windows using taskkill."""
    # taskkill returns non-zero if the process is already gone; ignore failures.
    cmd = ["taskkill", "/PID", str(pid), "/T", "/F"]
    try:
        await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
        )
    except Exception:
        pass


def _kill_process_group_posix(pid: int, sig: int) -> None:
    """Send signal to process group on POSIX."""
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        # fallback to single-process
        try:
            os.kill(pid, sig)
        except Exception:
            pass


async def run_cmd_async(
    cmd: Sequence[str],
    cwd: Path,
    timeout_sec: int = 600,
    *,
    stop_path: Optional[Path] = None,
    poll_interval_sec: float = 0.25,
    max_output_bytes: int = 2_000_000,
    kill_grace_sec: float = 3.0,
) -> Tuple[int, str]:
    """Run a subprocess without blocking the asyncio event loop.

    Features:
      - STOP file polling: if stop_path exists, terminate process promptly.
      - Timeout enforcement: terminate process when timeout_sec is exceeded.
      - Output limiting: cap captured output to avoid massive log files.

    Return codes (AgentCLI-defined):
      - 0..255: process return code
      - 124: timeout triggered by AgentCLI
      - 130: stop file observed; process terminated by AgentCLI
    """

    limiter = _OutputLimiter(max_bytes=max_output_bytes)
    start = time.monotonic()

    # Windows/POSIX process group options
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        popen_kwargs["start_new_session"] = True

    proc = await asyncio.create_subprocess_exec(
        *list(cmd),
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        **popen_kwargs,
    )

    assert proc.stdout is not None

    read_task = asyncio.create_task(proc.stdout.read(4096))
    wait_task = asyncio.create_task(proc.wait())

    async def terminate(reason: str) -> int:
        """Terminate the subprocess (and best-effort children) and return rc marker."""
        rc_marker = 130 if reason == "stop" else 124

        pid = proc.pid
        try:
            if os.name == "nt":
                if pid:
                    await _kill_process_tree_windows(pid, timeout_sec=kill_grace_sec)
                else:
                    proc.terminate()
            else:
                if pid:
                    _kill_process_group_posix(pid, signal.SIGTERM)
                else:
                    proc.terminate()
        except ProcessLookupError:
            return rc_marker
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass

        try:
            await asyncio.wait_for(wait_task, timeout=kill_grace_sec)
        except asyncio.TimeoutError:
            try:
                if os.name == "nt":
                    if pid:
                        await _kill_process_tree_windows(pid, timeout_sec=kill_grace_sec)
                    else:
                        proc.kill()
                else:
                    if pid:
                        _kill_process_group_posix(pid, signal.SIGKILL)
                    else:
                        proc.kill()
            except Exception:
                pass
        return rc_marker

    stop_triggered = False
    timeout_triggered = False

    while True:
        if (not stop_triggered) and stop_path is not None and stop_path.exists():
            stop_triggered = True
            rc = await terminate("stop")
            limiter.add(b"\n[STOP] stop file observed; process terminated by AgentCLI\n")
            # Drain whatever remains (best effort)
            try:
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    limiter.add(chunk)
            except Exception:
                pass
            return rc, limiter.render_text().strip()

        if (not timeout_triggered) and timeout_sec and (time.monotonic() - start) > float(timeout_sec):
            timeout_triggered = True
            rc = await terminate("timeout")
            limiter.add(f"\n[TIMEOUT] exceeded {timeout_sec}s\n".encode("utf-8", errors="replace"))
            try:
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    limiter.add(chunk)
            except Exception:
                pass
            return rc, limiter.render_text().strip()

        done, _ = await asyncio.wait(
            [read_task, wait_task],
            timeout=poll_interval_sec,
            return_when=asyncio.FIRST_COMPLETED,
        )

        if wait_task in done:
            rc = int(proc.returncode or 0)
            # Drain remaining output
            try:
                while True:
                    chunk = await proc.stdout.read(4096)
                    if not chunk:
                        break
                    limiter.add(chunk)
            except Exception:
                pass
            return rc, limiter.render_text().strip()

        if read_task in done:
            try:
                chunk = read_task.result()
            except Exception:
                chunk = b""
            if chunk:
                limiter.add(chunk)
                read_task = asyncio.create_task(proc.stdout.read(4096))
            else:
                # EOF - wait for process
                try:
                    await wait_task
                except Exception:
                    pass
                rc = int(proc.returncode or 0)
                return rc, limiter.render_text().strip()


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
    return p.resolve() if p.is_absolute() else (repo / p).resolve()


def safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")
