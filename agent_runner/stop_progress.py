from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from .state import count_state_task_ids, load_backlog_task_ids, load_state
from .utils import STOP_REASON_STOP_FILE, atomic_write_json, detect_stop_reason, now_iso

STOP_PROGRESS_FILE = "STOP_PROGRESS.json"
STOP_PROGRESS_LOG_FILE = "stop_progress.log"
STOP_SNAPSHOT_FILE = "STOP_SNAPSHOT.json"
STOP_SNAPSHOT_LOG_FILE = "STOP_SNAPSHOT.log"
STOP_RECONCILIATION_FILE = "STOP_RECONCILIATION.json"
STOP_RECONCILIATION_LOG_FILE = "STOP_RECONCILIATION.jsonl"
DEFAULT_STALE_STOP_RECONCILE_STOP_AGE_SECONDS = 15 * 60
DEFAULT_STALE_STOP_RECONCILE_HEARTBEAT_AGE_SECONDS = 15 * 60

STOP_PROGRESS_PHASE_ALIASES = {
    "requested": "request",
    "stop_file_written": "stop_file_write",
    "terminating_children": "child_termination",
    "waiting_runner": "runner_wait",
    "waiting_subprocess": "runner_wait",
    "forcing_process_tree": "child_termination",
    "waiting_forced_exit": "runner_wait",
    "stop_requested": "runner_wait",
    "collecting_artifacts": "final_artifact_collection",
}

STOP_PROGRESS_PHASE_LABELS = {
    "request": "Request",
    "stop_file_write": "Stop file write",
    "child_termination": "Child termination",
    "runner_wait": "Runner wait",
    "final_artifact_collection": "Final artifact collection",
    "timeout": "Timeout",
    "finalized": "Finalized",
    "failed": "Failed",
    "not_running": "Not running",
}

FINAL_STOP_PHASES = frozenset({"finalized", "timeout", "failed", "not_running"})


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def canonical_stop_phase(phase: object) -> str:
    raw = str(phase or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return "unknown"
    return STOP_PROGRESS_PHASE_ALIASES.get(raw, raw)


def stop_progress_phase_label(phase: object) -> str:
    canonical = canonical_stop_phase(phase)
    if canonical in STOP_PROGRESS_PHASE_LABELS:
        return STOP_PROGRESS_PHASE_LABELS[canonical]
    if not canonical or canonical == "unknown":
        return "Unknown"
    return canonical.replace("_", " ").title()


def _normalize_text(value: object) -> str:
    return str(value or "").strip()


def _normalize_bool(value: object) -> bool | None:
    if value in (None, "", False):
        return False if value is False else None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    raw = _normalize_text(value).lower()
    if raw in {"1", "true", "yes", "on", "alive", "active", "running", "recoverable", "retryable"}:
        return True
    if raw in {"0", "false", "no", "off", "dead", "inactive", "stopped", "unrecoverable", "not_recoverable"}:
        return False
    return None


def _normalize_path_text(value: object) -> str:
    raw = _normalize_text(value)
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().as_posix()
    except Exception:
        return raw.replace("\\", "/")


def _normalize_text_list(raw: object) -> list[str]:
    if raw in (None, "", False):
        return []
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [raw]
    items: list[str] = []
    for value in values:
        text = _normalize_text(value)
        if text and text not in items:
            items.append(text)
    return items


def _normalize_int_list(raw: object) -> list[int]:
    if raw in (None, "", False):
        return []
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [raw]
    items: list[int] = []
    for value in values:
        try:
            number = int(value)
        except Exception:
            continue
        if number > 0 and number not in items:
            items.append(number)
    return items


def _normalize_path_list(raw: object) -> list[str]:
    if raw in (None, "", False):
        return []
    if isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [raw]
    items: list[str] = []
    for value in values:
        text = _normalize_path_text(value)
        if text and text not in items:
            items.append(text)
    return items


def _normalize_signal_payload(raw: object, *, kind: str | None = None) -> dict[str, Any] | None:
    if raw in (None, "", False):
        return None
    if isinstance(raw, dict):
        payload = dict(raw)
    else:
        payload = {"path": raw}
    if payload.get("path") not in (None, "", False):
        payload["path"] = _normalize_path_text(payload["path"])
    elif payload.get("pathText") not in (None, "", False):
        payload["path"] = _normalize_path_text(payload["pathText"])
    if payload.get("paths") not in (None, "", False):
        payload["paths"] = _normalize_path_list(payload["paths"])
    if payload.get("updated_at") in (None, "", False) and payload.get("updatedAt") not in (None, "", False):
        payload["updated_at"] = _normalize_text(payload.pop("updatedAt"))
    elif payload.get("updated_at") not in (None, "", False):
        payload["updated_at"] = _normalize_text(payload["updated_at"])
    if payload.get("updated_at_epoch") in (None, "", False) and payload.get("updatedAtEpoch") not in (None, "", False):
        try:
            payload["updated_at_epoch"] = float(payload.pop("updatedAtEpoch"))
        except Exception:
            pass
    if payload.get("size_bytes") in (None, "", False) and payload.get("sizeBytes") not in (None, "", False):
        try:
            payload["size_bytes"] = int(payload.pop("sizeBytes"))
        except Exception:
            pass
    if kind and payload.get("kind") in (None, "", False):
        payload["kind"] = kind
    return payload


def _normalize_timeout_guidance(raw: object) -> dict[str, Any]:
    if raw in (None, "", False):
        return {}
    if isinstance(raw, dict):
        guidance = dict(raw)
    else:
        guidance = {"summary": raw}
    summary = guidance.get("summary") or guidance.get("message") or guidance.get("text") or ""
    guidance["summary"] = _normalize_text(summary)
    guidance["message"] = guidance["summary"]
    steps = guidance.get("steps")
    if steps in (None, "", False):
        steps = guidance.get("next_steps") or guidance.get("nextSteps")
    guidance["steps"] = _normalize_text_list(steps)
    manual_cleanup_hints = guidance.get("manual_cleanup_hints")
    if manual_cleanup_hints in (None, "", False):
        manual_cleanup_hints = guidance.get("manualCleanupHints")
    guidance["manual_cleanup_hints"] = _normalize_text_list(manual_cleanup_hints)
    locked_file_paths = guidance.get("locked_file_paths")
    if locked_file_paths in (None, "", False):
        locked_file_paths = guidance.get("lockedFilePaths")
    guidance["locked_file_paths"] = _normalize_path_list(locked_file_paths)
    recoverable = _normalize_bool(guidance.get("recoverable"))
    if recoverable is None:
        recoverable = _normalize_bool(guidance.get("can_retry"))
    if recoverable is None:
        recoverable = _normalize_bool(guidance.get("retryable"))
    guidance["recoverable"] = bool(recoverable)
    guidance["can_retry"] = bool(guidance["recoverable"])
    guidance["canRetry"] = bool(guidance["recoverable"])
    return guidance


def _normalize_stop_file_paths(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    paths: dict[str, str] = {}
    for key, value in raw.items():
        text = _normalize_path_text(value)
        if text:
            paths[str(key)] = text
    return paths


def _normalize_tracked_child_processes(raw: object) -> list[dict[str, Any]]:
    if raw in (None, "", False):
        return []
    if isinstance(raw, dict):
        values = [raw]
    elif isinstance(raw, (list, tuple, set)):
        values = list(raw)
    else:
        values = [raw]
    records: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        record = dict(value)
        pid = record.get("pid")
        if pid in (None, "", False):
            pid = record.get("child_pid") or record.get("childPid")
        try:
            pid_value = int(pid)
        except Exception:
            continue
        if pid_value <= 0:
            continue
        record["pid"] = pid_value
        record["child_pid"] = pid_value
        record["childPid"] = pid_value
        alive = _normalize_bool(record.get("alive"))
        if alive is None:
            alive = _normalize_bool(record.get("running"))
        record["alive"] = bool(alive)
        session_file = record.get("session_file")
        if session_file in (None, "", False):
            session_file = record.get("sessionFile") or record.get("session_path") or record.get("sessionPath")
        if session_file not in (None, "", False):
            record["session_file"] = _normalize_path_text(session_file)
            record["sessionFile"] = record["session_file"]
        else:
            record["session_file"] = ""
            record["sessionFile"] = ""
        session_exists = _normalize_bool(record.get("session_exists"))
        if session_exists is None:
            session_exists = _normalize_bool(record.get("sessionExists"))
        if session_exists is not None:
            record["session_exists"] = bool(session_exists)
            record["sessionExists"] = bool(session_exists)
        records.append(record)
    return records


def file_write_signal(path: Path, *, kind: str | None = None) -> dict[str, Any] | None:
    try:
        if not path.exists() or not path.is_file():
            return None
        stat = path.stat()
    except Exception:
        return None

    payload: dict[str, Any] = {
        "path": path.as_posix(),
        "updated_at": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "updated_at_epoch": float(stat.st_mtime),
        "size_bytes": int(stat.st_size),
    }
    if kind:
        payload["kind"] = kind
    return payload


def build_stop_file_paths(
    run_dirs: Sequence[Path] | Path,
    stop_file_name: str,
    *,
    runner_process_log_path: Path | None = None,
) -> dict[str, str]:
    if isinstance(run_dirs, Path):
        values = [run_dirs]
    else:
        values = [value for value in run_dirs if isinstance(value, Path)]
    paths: dict[str, str] = {}
    for idx, run_dir in enumerate(values):
        suffix = "" if idx == 0 else f"_{idx + 1}"
        paths[f"stop_file_path{suffix}"] = (run_dir / stop_file_name).as_posix()
        paths[f"stop_progress_path{suffix}"] = (run_dir / STOP_PROGRESS_FILE).as_posix()
        paths[f"stop_progress_log_path{suffix}"] = (run_dir / STOP_PROGRESS_LOG_FILE).as_posix()
    if runner_process_log_path is not None:
        try:
            paths["runner_process_log_path"] = runner_process_log_path.as_posix()
        except Exception:
            paths["runner_process_log_path"] = str(runner_process_log_path).replace("\\", "/")
    return paths


def _stop_progress_phase_entry(raw: dict[str, Any], *, fallback_phase: object | None = None) -> dict[str, Any]:
    entry = {key: value for key, value in raw.items() if key not in {"history", "phase_history", "phaseHistory", "current_phase", "currentPhase"}}
    phase = canonical_stop_phase(entry.get("phase") or fallback_phase)
    if phase == "unknown" and fallback_phase not in (None, ""):
        phase = canonical_stop_phase(fallback_phase)
    entry["phase"] = phase
    entry["phase_label"] = stop_progress_phase_label(phase)
    entry["message"] = str(entry.get("message") or "").strip()
    updated_at = entry.get("updated_at") or entry.get("updatedAt") or entry.get("updated")
    if updated_at not in (None, ""):
        entry["updated_at"] = str(updated_at).strip()
    elapsed = entry.get("elapsed_seconds")
    if elapsed in (None, ""):
        elapsed = entry.get("elapsedSeconds")
    try:
        entry["elapsed_seconds"] = max(0, int(elapsed or 0))
    except Exception:
        entry["elapsed_seconds"] = 0
    requested_at = entry.get("requested_at")
    if requested_at in (None, ""):
        requested_at = entry.get("requestedAt")
    if requested_at not in (None, ""):
        entry["requested_at"] = requested_at
    return entry


def _stop_progress_history(history: object, current_phase: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(history, list):
        for raw in history:
            if not isinstance(raw, dict):
                continue
            entry = _stop_progress_phase_entry(raw)
            if entry["phase"] == "unknown" and raw.get("phase") in (None, ""):
                continue
            items.append(
                {
                    "phase": entry["phase"],
                    "phase_label": entry["phase_label"],
                    "message": entry["message"],
                    "updated_at": entry.get("updated_at", ""),
                    "elapsed_seconds": entry.get("elapsed_seconds", 0),
                }
            )

    deduped: list[dict[str, Any]] = []
    for item in items:
        if deduped and deduped[-1]["phase"] == item["phase"]:
            deduped[-1] = item
        else:
            deduped.append(item)
    items = deduped

    if not items or items[-1]["phase"] != current_phase["phase"]:
        items.append(
            {
                "phase": current_phase["phase"],
                "phase_label": current_phase["phase_label"],
                "message": current_phase["message"],
                "updated_at": current_phase.get("updated_at", ""),
                "elapsed_seconds": current_phase.get("elapsed_seconds", 0),
            }
        )
    return items


def normalize_stop_progress_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        return {}

    raw = dict(payload)
    current_phase_raw = raw.get("current_phase")
    if not isinstance(current_phase_raw, dict):
        current_phase_raw = raw.get("currentPhase")

    current_source = dict(raw)
    if isinstance(current_phase_raw, dict):
        current_source.update(current_phase_raw)
    current_phase = _stop_progress_phase_entry(current_source, fallback_phase=current_source.get("phase") or raw.get("phase"))
    history_source = raw.get("history")
    if not isinstance(history_source, list):
        history_source = raw.get("phase_history")
    if not isinstance(history_source, list):
        history_source = raw.get("phaseHistory")
    history = _stop_progress_history(history_source, current_phase)

    normalized = dict(raw)
    normalized.update(current_phase)
    normalized["phase"] = current_phase["phase"]
    normalized["phase_label"] = current_phase["phase_label"]
    normalized["message"] = current_phase["message"]
    normalized["updated_at"] = current_phase.get("updated_at", "")
    normalized["elapsed_seconds"] = current_phase.get("elapsed_seconds", 0)
    normalized["current_phase"] = current_phase
    normalized["currentPhase"] = current_phase
    normalized["history"] = history
    normalized["phase_history"] = history
    normalized["phaseHistory"] = history
    normalized["phase_index"] = max(0, len(history) - 1)
    normalized["history_count"] = len(history)
    runner_alive = _normalize_bool(raw.get("runner_alive"))
    if runner_alive is None:
        runner_alive = _normalize_bool(raw.get("runnerAlive"))
    if runner_alive is None:
        runner_alive = _normalize_bool(raw.get("running"))
    if runner_alive is None:
        runner_alive = _normalize_bool(current_phase.get("runner_alive"))
    if runner_alive is None:
        runner_alive = _normalize_bool(current_phase.get("runnerAlive"))
    if runner_alive is None:
        runner_alive = _normalize_bool(current_phase.get("running"))
    normalized["runner_alive"] = bool(runner_alive)
    normalized["runnerAlive"] = bool(runner_alive)
    running = _normalize_bool(raw.get("running"))
    if running is None:
        running = normalized["runner_alive"]
    normalized["running"] = bool(running)

    tracked_child_pids = raw.get("tracked_child_pids")
    if tracked_child_pids in (None, "", False):
        tracked_child_pids = raw.get("trackedChildPids")
    if tracked_child_pids in (None, "", False):
        tracked_child_pids = current_phase.get("tracked_child_pids")
    if tracked_child_pids in (None, "", False):
        tracked_child_pids = current_phase.get("trackedChildPids")
    normalized["tracked_child_pids"] = _normalize_int_list(tracked_child_pids)
    normalized["trackedChildPids"] = list(normalized["tracked_child_pids"])

    tracked_child_processes = raw.get("tracked_child_processes")
    if not isinstance(tracked_child_processes, list):
        tracked_child_processes = raw.get("trackedChildProcesses")
    if not isinstance(tracked_child_processes, list):
        tracked_child_processes = current_phase.get("tracked_child_processes")
    if not isinstance(tracked_child_processes, list):
        tracked_child_processes = current_phase.get("trackedChildProcesses")
    normalized["tracked_child_processes"] = _normalize_tracked_child_processes(tracked_child_processes)
    normalized["trackedChildProcesses"] = list(normalized["tracked_child_processes"])

    stop_file_paths = raw.get("stop_file_paths")
    if not isinstance(stop_file_paths, dict):
        stop_file_paths = raw.get("stopFilePaths")
    if not isinstance(stop_file_paths, dict):
        stop_file_paths = current_phase.get("stop_file_paths")
    if not isinstance(stop_file_paths, dict):
        stop_file_paths = current_phase.get("stopFilePaths")
    normalized["stop_file_paths"] = _normalize_stop_file_paths(stop_file_paths)
    normalized["stopFilePaths"] = dict(normalized["stop_file_paths"])

    last_artifact_signal = raw.get("last_artifact_signal")
    if last_artifact_signal in (None, "", False):
        last_artifact_signal = raw.get("lastArtifactSignal")
    if last_artifact_signal in (None, "", False):
        last_artifact_signal = current_phase.get("last_artifact_signal")
    if last_artifact_signal in (None, "", False):
        last_artifact_signal = current_phase.get("lastArtifactSignal")
    normalized["last_artifact_signal"] = _normalize_signal_payload(last_artifact_signal, kind="artifact")
    normalized["lastArtifactSignal"] = normalized["last_artifact_signal"]

    last_log_signal = raw.get("last_log_signal")
    if last_log_signal in (None, "", False):
        last_log_signal = raw.get("lastLogSignal")
    if last_log_signal in (None, "", False):
        last_log_signal = current_phase.get("last_log_signal")
    if last_log_signal in (None, "", False):
        last_log_signal = current_phase.get("lastLogSignal")
    normalized["last_log_signal"] = _normalize_signal_payload(last_log_signal, kind="log")
    normalized["lastLogSignal"] = normalized["last_log_signal"]

    timeout_guidance = raw.get("timeout_guidance")
    if timeout_guidance in (None, "", False):
        timeout_guidance = raw.get("timeoutGuidance")
    if timeout_guidance in (None, "", False):
        timeout_guidance = current_phase.get("timeout_guidance")
    if timeout_guidance in (None, "", False):
        timeout_guidance = current_phase.get("timeoutGuidance")
    normalized["timeout_guidance"] = _normalize_timeout_guidance(timeout_guidance)
    normalized["timeoutGuidance"] = dict(normalized["timeout_guidance"])

    manual_cleanup_hints = raw.get("manual_cleanup_hints")
    if manual_cleanup_hints in (None, "", False):
        manual_cleanup_hints = raw.get("manualCleanupHints")
    if manual_cleanup_hints in (None, "", False):
        manual_cleanup_hints = normalized["timeout_guidance"].get("manual_cleanup_hints")
    normalized["manual_cleanup_hints"] = _normalize_text_list(manual_cleanup_hints)
    normalized["manualCleanupHints"] = list(normalized["manual_cleanup_hints"])

    locked_file_paths = raw.get("locked_file_paths")
    if locked_file_paths in (None, "", False):
        locked_file_paths = raw.get("lockedFilePaths")
    if locked_file_paths in (None, "", False):
        locked_file_paths = normalized["timeout_guidance"].get("locked_file_paths")
    normalized["locked_file_paths"] = _normalize_path_list(locked_file_paths)
    normalized["lockedFilePaths"] = list(normalized["locked_file_paths"])
    normalized["active"] = bool(current_phase["phase"] and current_phase["phase"] not in FINAL_STOP_PHASES)
    return normalized


def summarize_stop_progress_liveness(payload: dict[str, Any] | None) -> dict[str, Any]:
    normalized = normalize_stop_progress_payload(payload)
    current_phase = normalized.get("current_phase") if isinstance(normalized.get("current_phase"), dict) else {}
    tracked_child_processes = normalized.get("tracked_child_processes")
    if not isinstance(tracked_child_processes, list):
        tracked_child_processes = []
    tracked_child_pids = normalized.get("tracked_child_pids")
    if not isinstance(tracked_child_pids, list):
        tracked_child_pids = []
    tracked_alive_count = 0
    for record in tracked_child_processes:
        if isinstance(record, dict) and bool(record.get("alive")):
            tracked_alive_count += 1
    tracked_count = len(tracked_child_processes) if tracked_child_processes else len(tracked_child_pids)
    artifact_phase = str(normalized.get("phase") or current_phase.get("phase") or "").strip().lower()
    artifact_flushing = bool(
        artifact_phase == "final_artifact_collection"
        or (artifact_phase in {"runner_wait", "timeout"} and bool(normalized.get("last_artifact_signal")))
    )
    return {
        "phase": artifact_phase,
        "runner_alive": bool(normalized.get("runner_alive")),
        "tracked_child_pids": list(tracked_child_pids),
        "tracked_child_processes": list(tracked_child_processes),
        "tracked_count": max(0, int(tracked_count)),
        "tracked_alive_count": max(0, int(tracked_alive_count)),
        "artifact_phase": artifact_phase,
        "artifact_flushing": artifact_flushing,
        "stop_file_paths": dict(normalized.get("stop_file_paths") or {}),
        "last_artifact_signal": dict(normalized.get("last_artifact_signal") or {}),
        "last_log_signal": dict(normalized.get("last_log_signal") or {}),
        "timeout_guidance": dict(normalized.get("timeout_guidance") or {}),
        "current_phase": current_phase,
        "active": bool(normalized.get("active")),
    }


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
        return normalize_stop_progress_payload(payload if isinstance(payload, dict) else {})
    except Exception:
        return {}


def record_stop_progress(
    run_dir: Path,
    *,
    phase: str,
    message: str = "",
    requested_at_monotonic: float | None = None,
    context_fields: dict[str, Any] | None = None,
    **fields: Any,
) -> dict[str, Any]:
    payload_fields = dict(context_fields or {})
    for key, value in fields.items():
        if value is not None:
            payload_fields[key] = value
    runner_alive_raw = payload_fields.get("runner_alive")
    if runner_alive_raw in (None, ""):
        runner_alive_raw = payload_fields.get("runnerAlive")
    running_raw = payload_fields.get("running")
    if running_raw in (None, "") and runner_alive_raw not in (None, ""):
        running_raw = runner_alive_raw
    if runner_alive_raw not in (None, ""):
        runner_alive = bool(runner_alive_raw)
        payload_fields["runner_alive"] = runner_alive
        payload_fields["runnerAlive"] = runner_alive
    if running_raw not in (None, ""):
        payload_fields["running"] = bool(running_raw)
    return write_stop_progress(
        run_dir,
        phase=phase,
        message=message,
        requested_at_monotonic=requested_at_monotonic,
        **payload_fields,
    )


def clear_stop_progress(run_dir: Path | None) -> None:
    if run_dir is None:
        return
    for name in (STOP_PROGRESS_FILE, STOP_PROGRESS_LOG_FILE):
        try:
            (run_dir / name).unlink(missing_ok=True)
        except Exception:
            pass


def _stop_reconcile_path_text(path: Path) -> str:
    try:
        return path.expanduser().resolve().as_posix()
    except Exception:
        return path.as_posix()


def _stop_reconcile_age_seconds(path: Path, *, now_epoch: float) -> float | None:
    try:
        return max(0.0, float(now_epoch) - float(path.stat().st_mtime))
    except Exception:
        return None


def _stop_reconcile_int_seconds(value: Any, fallback: int) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return max(0, int(fallback))


def _unique_stop_reconcile_paths(paths: Sequence[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = _stop_reconcile_path_text(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _heartbeat_reconcile_details(heartbeat_path: Path, *, now_epoch: float, stale_age_seconds: int) -> dict[str, Any]:
    age_seconds = _stop_reconcile_age_seconds(heartbeat_path, now_epoch=now_epoch) if heartbeat_path.exists() else None
    state = "missing"
    if heartbeat_path.exists():
        state = "stale" if age_seconds is not None and age_seconds >= stale_age_seconds else "fresh"
    return {
        "path": _stop_reconcile_path_text(heartbeat_path),
        "exists": heartbeat_path.exists(),
        "age_seconds": round(float(age_seconds), 3) if age_seconds is not None else None,
        "state": state,
    }


def _write_stop_reconciliation_audit(run_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    audit_path = run_dir / STOP_RECONCILIATION_FILE
    audit_log_path = run_dir / STOP_RECONCILIATION_LOG_FILE
    payload["audit_path"] = _stop_reconcile_path_text(audit_path)
    payload["audit_log_path"] = _stop_reconcile_path_text(audit_log_path)
    try:
        atomic_write_json(audit_path, payload)
    except Exception:
        pass
    try:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_log_path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return payload


def reconcile_stale_stop_files(
    run_dir: Path | None,
    *,
    stop_file: str = "STOP",
    heartbeat_file: str = "HEARTBEAT",
    stop_stale_age_seconds: int = DEFAULT_STALE_STOP_RECONCILE_STOP_AGE_SECONDS,
    heartbeat_stale_age_seconds: int = DEFAULT_STALE_STOP_RECONCILE_HEARTBEAT_AGE_SECONDS,
    allow_missing_heartbeat: bool = False,
    source: str = "",
    now_epoch: float | None = None,
) -> dict[str, Any]:
    """Reconcile stale STOP artifacts before a new long-running launch.

    A STOP file is only removed when it is old enough and the heartbeat is also
    old enough.  Missing heartbeats are preserved unless an explicit caller opts
    into missing-heartbeat reconciliation.
    """
    if run_dir is None:
        return {"ok": True, "decision": "no_run_dir", "action_taken": "none", "message": ""}

    run_dir_path = Path(run_dir).expanduser().resolve()
    stop_file_name = str(stop_file or "STOP").strip() or "STOP"
    heartbeat_file_name = str(heartbeat_file or "HEARTBEAT").strip() or "HEARTBEAT"
    stop_threshold = _stop_reconcile_int_seconds(stop_stale_age_seconds, DEFAULT_STALE_STOP_RECONCILE_STOP_AGE_SECONDS)
    heartbeat_threshold = _stop_reconcile_int_seconds(
        heartbeat_stale_age_seconds,
        DEFAULT_STALE_STOP_RECONCILE_HEARTBEAT_AGE_SECONDS,
    )
    now_value = float(now_epoch if now_epoch is not None else time.time())
    stop_paths = _unique_stop_reconcile_paths([run_dir_path / stop_file_name, run_dir_path / "STOP"])
    existing_stop_paths = [path for path in stop_paths if path.exists()]
    heartbeat_path = run_dir_path / heartbeat_file_name

    if not existing_stop_paths:
        return {
            "ok": True,
            "decision": "no_stop_file",
            "action_taken": "none",
            "message": "",
            "run_dir": _stop_reconcile_path_text(run_dir_path),
            "stop_path": _stop_reconcile_path_text(stop_paths[0]),
            "stop_paths": [_stop_reconcile_path_text(path) for path in stop_paths],
            "heartbeat_path": _stop_reconcile_path_text(heartbeat_path),
        }

    stop_details: list[dict[str, Any]] = []
    fresh_stop_paths: list[str] = []
    for path in existing_stop_paths:
        age_seconds = _stop_reconcile_age_seconds(path, now_epoch=now_value)
        stale = bool(age_seconds is not None and age_seconds >= stop_threshold)
        path_text = _stop_reconcile_path_text(path)
        if not stale:
            fresh_stop_paths.append(path_text)
        stop_details.append(
            {
                "path": path_text,
                "exists": True,
                "age_seconds": round(float(age_seconds), 3) if age_seconds is not None else None,
                "state": "stale" if stale else "fresh",
            }
        )

    heartbeat = _heartbeat_reconcile_details(heartbeat_path, now_epoch=now_value, stale_age_seconds=heartbeat_threshold)
    criteria = {
        "stop_stale_age_seconds": stop_threshold,
        "heartbeat_stale_age_seconds": heartbeat_threshold,
        "allow_missing_heartbeat": bool(allow_missing_heartbeat),
    }
    primary_stop = existing_stop_paths[0]
    primary_stop_detail = stop_details[0] if stop_details else {}
    decision = "reconcile_stale_stop"
    action_taken = "deleted_stop_files"
    ok = True
    message = "Stale STOP file reconciled before runner start."

    if fresh_stop_paths:
        decision = "block_fresh_stop"
        action_taken = "preserved_stop_files"
        ok = False
        message = "Runner start blocked because a fresh STOP request is present."
    elif heartbeat["state"] == "fresh":
        decision = "block_active_heartbeat"
        action_taken = "preserved_stop_files"
        ok = False
        message = "Runner start blocked because the STOP file is stale but HEARTBEAT is still fresh."
    elif heartbeat["state"] == "missing" and not allow_missing_heartbeat:
        decision = "block_missing_heartbeat"
        action_taken = "preserved_stop_files"
        ok = False
        message = "Runner start blocked because a STOP file exists and HEARTBEAT is missing."
    elif heartbeat["state"] == "missing":
        decision = "reconcile_missing_heartbeat_explicit"
        message = "Stale STOP file reconciled with explicit missing-heartbeat criteria."

    deleted_paths: list[str] = []
    if ok and action_taken == "deleted_stop_files":
        for path in existing_stop_paths:
            try:
                path.unlink()
                deleted_paths.append(_stop_reconcile_path_text(path))
            except Exception:
                ok = False
                decision = "delete_failed"
                action_taken = "preserved_stop_files"
                message = "Runner start blocked because stale STOP reconciliation could not remove the STOP file."
                break
        if ok:
            clear_stop_progress(run_dir_path)

    payload: dict[str, Any] = {
        "schema_version": 1,
        "event": "stale_stop_reconciliation",
        "ts": now_iso(),
        "source": str(source or "").strip(),
        "ok": bool(ok),
        "run_dir": _stop_reconcile_path_text(run_dir_path),
        "stop_path": _stop_reconcile_path_text(primary_stop),
        "stop_paths": [_stop_reconcile_path_text(path) for path in existing_stop_paths],
        "stop_age_seconds": primary_stop_detail.get("age_seconds"),
        "stop_file_state": primary_stop_detail.get("state") or "",
        "stop_files": stop_details,
        "heartbeat_path": str(heartbeat.get("path") or ""),
        "heartbeat_age_seconds": heartbeat.get("age_seconds"),
        "heartbeat_state": str(heartbeat.get("state") or ""),
        "heartbeat": heartbeat,
        "criteria": criteria,
        "decision": decision,
        "action_taken": action_taken,
        "deleted_paths": deleted_paths,
        "message": message,
    }
    return _write_stop_reconciliation_audit(run_dir_path, payload)


def reconcile_stale_stop_files_for_args(run_dir: Path | None, args: Any, *, source: str = "") -> dict[str, Any]:
    return reconcile_stale_stop_files(
        run_dir,
        stop_file=str(getattr(args, "stop_file", "STOP") or "STOP"),
        stop_stale_age_seconds=getattr(
            args,
            "stale_stop_reconcile_stop_age_seconds",
            DEFAULT_STALE_STOP_RECONCILE_STOP_AGE_SECONDS,
        ),
        heartbeat_stale_age_seconds=getattr(
            args,
            "stale_stop_reconcile_heartbeat_age_seconds",
            DEFAULT_STALE_STOP_RECONCILE_HEARTBEAT_AGE_SECONDS,
        ),
        allow_missing_heartbeat=bool(getattr(args, "stale_stop_reconcile_allow_missing_heartbeat", False)),
        source=source,
    )


def write_stop_progress(
    run_dir: Path,
    *,
    phase: str,
    message: str = "",
    requested_at_monotonic: float | None = None,
    **fields: Any,
) -> dict[str, Any]:
    previous = normalize_stop_progress_payload(read_stop_progress(run_dir))
    now_mono = time.monotonic()
    requested_at = requested_at_monotonic
    if requested_at is None:
        requested_at = previous.get("_requested_at_monotonic")
        if not isinstance(requested_at, (int, float)):
            requested_at = now_mono

    canonical_phase = canonical_stop_phase(phase)
    current_phase: dict[str, Any] = {
        "phase": canonical_phase,
        "phase_label": stop_progress_phase_label(canonical_phase),
        "message": str(message or "").strip(),
        "updated_at": _now_iso(),
        "elapsed_seconds": max(0, int(now_mono - float(requested_at))),
        "_requested_at_monotonic": float(requested_at),
    }
    if previous.get("requested_at"):
        current_phase["requested_at"] = previous.get("requested_at")
    else:
        current_phase["requested_at"] = _now_iso()
    for key, value in fields.items():
        if value is not None:
            current_phase[key] = value

    history = _stop_progress_history(previous.get("history") or previous.get("phase_history"), current_phase)

    payload: dict[str, Any] = dict(previous)
    payload.update(current_phase)
    payload["phase"] = canonical_phase
    payload["phase_label"] = current_phase["phase_label"]
    payload["message"] = current_phase["message"]
    payload["updated_at"] = current_phase["updated_at"]
    payload["elapsed_seconds"] = current_phase["elapsed_seconds"]
    payload["_requested_at_monotonic"] = float(requested_at)
    payload["requested_at"] = current_phase["requested_at"]
    payload["current_phase"] = current_phase
    payload["currentPhase"] = current_phase
    payload["history"] = history
    payload["phase_history"] = history
    payload["phaseHistory"] = history
    payload["phase_index"] = max(0, len(history) - 1)
    payload["history_count"] = len(history)
    payload["active"] = bool(canonical_phase and canonical_phase not in FINAL_STOP_PHASES)

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


def read_stop_snapshot(run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {}
    path = run_dir / STOP_SNAPSHOT_FILE
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace").strip() or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _stop_snapshot_state_counts(run_dir: Path) -> dict[str, int] | None:
    try:
        state_path = run_dir / "STATE.json"
        if not state_path.exists():
            return None
        state_obj = load_state(state_path)
        backlog_task_ids = load_backlog_task_ids(run_dir / "BACKLOG.json")
        state_counts = count_state_task_ids(state_obj, backlog_task_ids)
        return {
            "done": state_counts["done"],
            "failed": state_counts["failed"],
            "warnings": state_counts["warnings"],
        }
    except Exception:
        return None


def write_stop_snapshot(
    run_dir: Path,
    *,
    stage: str,
    cycle: int,
    step: int = -1,
    task_id: str = "",
    attempt: int | None = None,
    message: str = "",
    stop_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    paths = list(stop_paths or [run_dir / "STOP"])
    try:
        reason = detect_stop_reason(paths) or STOP_REASON_STOP_FILE
    except Exception:
        reason = STOP_REASON_STOP_FILE

    payload: dict[str, Any] = {
        "ts": now_iso(),
        "reason": reason,
        "stage": str(stage or ""),
        "cycle": int(cycle),
        "step": int(step),
        "task_id": str(task_id or ""),
        "message": str(message or ""),
        "run_dir": str(run_dir),
    }
    if attempt is not None:
        payload["attempt"] = int(attempt)
    try:
        progress_path = run_dir / "progress.txt"
        if progress_path.exists():
            payload["progress"] = progress_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        pass
    state_counts = _stop_snapshot_state_counts(run_dir)
    if state_counts is not None:
        payload["state_counts"] = state_counts

    path = run_dir / STOP_SNAPSHOT_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")
        tmp.replace(path)
    except Exception:
        pass
    try:
        with (run_dir / STOP_SNAPSHOT_LOG_FILE).open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass
    return payload


def stop_progress_is_active(progress: dict[str, Any]) -> bool:
    phase = canonical_stop_phase(progress.get("phase") or progress.get("current_phase", {}).get("phase"))
    return bool(phase and phase not in FINAL_STOP_PHASES)

