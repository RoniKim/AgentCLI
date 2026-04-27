from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


STOP_PROGRESS_FILE = "STOP_PROGRESS.json"
STOP_PROGRESS_LOG_FILE = "stop_progress.log"

FINAL_STOP_PHASES = frozenset({"finalized", "timeout", "failed", "not_running"})


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_stop_progress(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    path = run_dir / STOP_PROGRESS_FILE
    try:
        if not path.exists() or not path.is_file():
            return {}
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return {}
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def clear_stop_progress(run_dir: Path | None) -> None:
    if run_dir is None:
        return
    for name in (STOP_PROGRESS_FILE, STOP_PROGRESS_LOG_FILE):
        try:
            (run_dir / name).unlink(missing_ok=True)
        except Exception:
            pass


def write_stop_progress(
    run_dir: Path,
    *,
    phase: str,
    message: str = "",
    requested_at_monotonic: float | None = None,
    **fields: Any,
) -> dict[str, Any]:
    previous = read_stop_progress(run_dir)
    now_mono = time.monotonic()
    requested_at = requested_at_monotonic
    if requested_at is None:
        requested_at = previous.get("_requested_at_monotonic")
        if not isinstance(requested_at, (int, float)):
            requested_at = now_mono

    payload: dict[str, Any] = {
        "phase": str(phase or "").strip() or "unknown",
        "message": str(message or "").strip(),
        "updated_at": _now_iso(),
        "elapsed_seconds": max(0, int(now_mono - float(requested_at))),
        "_requested_at_monotonic": float(requested_at),
    }
    if previous.get("requested_at"):
        payload["requested_at"] = previous.get("requested_at")
    else:
        payload["requested_at"] = _now_iso()
    for key, value in fields.items():
        if value is not None:
            payload[key] = value

    path = run_dir / STOP_PROGRESS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")
        tmp.replace(path)
    except Exception:
        pass

    try:
        log_line = (
            f"{payload['updated_at']} phase={payload['phase']} "
            f"elapsed={payload['elapsed_seconds']}s message={payload['message']}"
        ).rstrip()
        with (run_dir / STOP_PROGRESS_LOG_FILE).open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(log_line + "\n")
    except Exception:
        pass

    return payload


def stop_progress_is_active(progress: dict[str, Any]) -> bool:
    phase = str(progress.get("phase") or "").strip().lower()
    return bool(phase and phase not in FINAL_STOP_PHASES)

