from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence, Tuple, Any, Optional


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


async def run_cmd_async(
    cmd: Sequence[str],
    cwd: Path,
    *,
    log_path: Path,
    timeout_sec: int = 600,
    stop_path: Optional[Path] = None,
    max_output_bytes: int = 10_000_000,
    terminate_grace_sec: float = 2.0,
) -> tuple[int, str]:
    """Run a subprocess with streaming logs and cancellation support."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *list(cmd),
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    output_bytes = 0
    truncated = False

    async def _read_stream(stream: asyncio.StreamReader, label: str, handle) -> None:
        nonlocal output_bytes, truncated
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            if output_bytes < max_output_bytes:
                remaining = max_output_bytes - output_bytes
                to_write = chunk[:remaining]
                handle.write(to_write)
                output_bytes += len(to_write)
                if len(to_write) < len(chunk) and not truncated:
                    handle.write(b"\n[TRUNCATED]\n")
                    truncated = True
            else:
                if not truncated:
                    handle.write(b"\n[TRUNCATED]\n")
                    truncated = True
        handle.flush()

    async def _terminate(reason: str) -> None:
        try:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=terminate_grace_sec)
                except asyncio.TimeoutError:
                    proc.kill()
        except ProcessLookupError:
            pass

    with log_path.open("ab") as handle:
        handle.write(f"$ {' '.join(cmd)}\n".encode("utf-8", errors="replace"))
        reader_tasks = [
            asyncio.create_task(_read_stream(proc.stdout, "stdout", handle)),
            asyncio.create_task(_read_stream(proc.stderr, "stderr", handle)),
        ]

        while True:
            if stop_path is not None and stop_path.exists():
                handle.write(b"\n[STOP REQUESTED]\n")
                await _terminate("stop")
                break
            if timeout_sec and (time.monotonic() - start) > timeout_sec:
                handle.write(b"\n[TIMEOUT]\n")
                await _terminate("timeout")
                break
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.2)

        await proc.wait()
        await asyncio.gather(*reader_tasks, return_exceptions=True)

    rc = proc.returncode if proc.returncode is not None else 1
    return rc, str(log_path)


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
    except Exception as exc:
        raise ValueError(f"Path escapes repo: {maybe_rel}") from exc
    return resolved


def safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", errors="replace", delete=False, dir=str(path.parent)) as tmp:
        tmp.write(content)
        temp_name = tmp.name
    os.replace(temp_name, path)


def atomic_write_json(path: Path, payload: Any) -> None:
    import json

    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, data)


def has_path_traversal(raw: str) -> bool:
    p = Path(raw)
    return any(part == ".." for part in p.parts)


def write_validation_failure(run_dir: Path, message: str) -> None:
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "VALIDATION_FAILURE.md").write_text(message + "\n", encoding="utf-8", errors="replace")
    except Exception:
        pass
