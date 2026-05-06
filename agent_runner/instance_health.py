from __future__ import annotations

from pathlib import Path
from typing import Any

from .gitops import scan_worktree_diagnostics
from .preflight import RUNNER_WAIT_BLOCKING_PHASES, _collect_lock_diagnostics, _stop_artifact_details
from .process_guard import (
    find_windows_handle_diagnostics_source,
    process_guard_state,
    read_windows_handle_diagnostics_jsonl,
    tracked_pid_details,
)
from .utils import now_iso


INSTANCE_HEALTH_SCHEMA = "agentcli.instance_health.v1"


def _safe_path(value: Path | str | None) -> str:
    if value is None:
        return ""
    try:
        return Path(value).expanduser().resolve().as_posix()
    except Exception:
        return str(value or "").strip()


def _status_from_counts(*, blockers: int = 0, warnings: int = 0) -> str:
    if blockers > 0:
        return "error"
    if warnings > 0:
        return "warning"
    return "ok"


def _handle_diagnostic_warning_count(payload: dict[str, Any]) -> int:
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    errors = payload.get("errors") if isinstance(payload.get("errors"), list) else []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    status = str(payload.get("status") or "").strip().lower()
    warning_count = len(warnings)
    if errors:
        warning_count += 1
    if status in {"malformed", "partial", "error"}:
        warning_count += 1
    if summary.get("healthy") is False:
        warning_count += 1
    return warning_count


def _tracked_children_payload() -> dict[str, Any]:
    records = tracked_pid_details(alive_only=False)
    alive_count = len([item for item in records if bool(item.get("alive"))])
    missing_session_count = len([item for item in records if not bool(item.get("session_exists"))])
    return {
        "items": records,
        "pids": [int(item["pid"]) for item in records if item.get("pid") is not None],
        "summary": {
            "total": len(records),
            "alive": alive_count,
            "exited": max(0, len(records) - alive_count),
            "missingSessionFiles": missing_session_count,
            "missing_session_files": missing_session_count,
        },
    }


def _windows_handle_diagnostics(repo: Path, run_dir: Path | None) -> dict[str, Any]:
    if run_dir is None:
        return {
            "status": "missing",
            "source_path": "",
            "sourcePath": "",
            "source_exists": False,
            "sourceExists": False,
            "warnings": [],
            "summary": {"healthy": True, "warning_count": 0, "warningCount": 0},
        }
    source = find_windows_handle_diagnostics_source(repo, run_dir)
    if source is None:
        return {
            "status": "missing",
            "source_path": "",
            "sourcePath": "",
            "source_exists": False,
            "sourceExists": False,
            "warnings": [],
            "summary": {"healthy": True, "warning_count": 0, "warningCount": 0},
        }
    payload = read_windows_handle_diagnostics_jsonl(source)
    payload["sourcePath"] = payload.get("source_path", source.as_posix())
    payload["sourceExists"] = bool(payload.get("source_exists"))
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    summary["warningCount"] = int(summary.get("warning_count") or len(payload.get("warnings") or []))
    payload["summary"] = summary
    return payload


def _stale_artifact_risks(repo: Path, run_dir: Path | None) -> dict[str, Any]:
    stop_artifacts: dict[str, Any] = {}
    if run_dir is not None:
        stop_artifacts = _stop_artifact_details(run_dir, "STOP")
    stop_phase = str(stop_artifacts.get("stop_progress_phase") or "").strip().lower()
    control_phase = str(stop_artifacts.get("runner_control_phase") or "").strip().lower()
    stop_file_exists = bool(stop_artifacts.get("stop_file_exists"))
    stale_runner_wait = stop_phase in RUNNER_WAIT_BLOCKING_PHASES or control_phase in RUNNER_WAIT_BLOCKING_PHASES

    try:
        worktree_diagnostics = scan_worktree_diagnostics(repo)
    except Exception as ex:
        worktree_diagnostics = {
            "status": "error",
            "summary": {},
            "issues": [{"kind": "worktree_diagnostics_error", "message": str(ex)}],
        }
    worktree_summary = dict(worktree_diagnostics.get("summary") or {})
    cleanup_failed = [dict(item) for item in worktree_diagnostics.get("cleanup_failed", []) if isinstance(item, dict)]
    pending_markers = [dict(item) for item in worktree_diagnostics.get("pending_markers", []) if isinstance(item, dict)]
    generated_worktrees = [dict(item) for item in worktree_diagnostics.get("generated_worktrees", []) if isinstance(item, dict)]
    stale_task_branches = [dict(item) for item in worktree_diagnostics.get("stale_task_branches", []) if isinstance(item, dict)]
    interrupted_attempts = [dict(item) for item in worktree_diagnostics.get("interrupted_attempts", []) if isinstance(item, dict)]
    stale_pending_count = len(
        [
            item
            for item in pending_markers
            if bool(item.get("stale")) or str(item.get("status") or "").strip().lower() == "stale"
        ]
    )
    orphaned_count = len([item for item in generated_worktrees if bool(item.get("orphaned"))])
    blocker_count = int(stop_file_exists) + int(stale_runner_wait) + len(cleanup_failed)
    warning_count = stale_pending_count + orphaned_count + len(stale_task_branches) + len(interrupted_attempts)
    if str(worktree_diagnostics.get("status") or "").strip().lower() == "error":
        warning_count += 1
    return {
        "status": _status_from_counts(blockers=blocker_count, warnings=warning_count),
        "stop_artifacts": stop_artifacts,
        "stopArtifacts": stop_artifacts,
        "worktree_diagnostics": {
            "status": str(worktree_diagnostics.get("status") or "ok"),
            "summary": worktree_summary,
            "cleanup_failed": cleanup_failed,
            "cleanupFailed": cleanup_failed,
            "pending_markers": pending_markers,
            "pendingMarkers": pending_markers,
            "generated_worktrees": generated_worktrees,
            "generatedWorktrees": generated_worktrees,
            "stale_task_branches": stale_task_branches,
            "staleTaskBranches": stale_task_branches,
            "interrupted_attempts": interrupted_attempts,
            "interruptedAttempts": interrupted_attempts,
        },
        "worktreeDiagnostics": {
            "status": str(worktree_diagnostics.get("status") or "ok"),
            "summary": worktree_summary,
        },
        "summary": {
            "stopFile": int(stop_file_exists),
            "stop_file": int(stop_file_exists),
            "runnerWait": int(stale_runner_wait),
            "runner_wait": int(stale_runner_wait),
            "cleanupFailed": len(cleanup_failed),
            "cleanup_failed": len(cleanup_failed),
            "stalePendingMarkers": stale_pending_count,
            "stale_pending_markers": stale_pending_count,
            "orphanedWorktrees": orphaned_count,
            "orphaned_worktrees": orphaned_count,
            "staleTaskBranches": len(stale_task_branches),
            "stale_task_branches": len(stale_task_branches),
            "interruptedAttempts": len(interrupted_attempts),
            "interrupted_attempts": len(interrupted_attempts),
            "blockers": blocker_count,
            "warnings": warning_count,
        },
    }


def build_instance_health(
    repo: Path | str,
    *,
    run_dir: Path | str | None = None,
    web_instance: dict[str, Any] | None = None,
    live_state: dict[str, Any] | None = None,
    runner_control: dict[str, Any] | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    run_dir_path = Path(run_dir).expanduser().resolve() if run_dir else None
    process_guard = process_guard_state()
    tracked_children = _tracked_children_payload()
    handle_diagnostics = _windows_handle_diagnostics(repo_root, run_dir_path)
    try:
        lock_diagnostics = _collect_lock_diagnostics(repo_root)
    except Exception as ex:
        lock_diagnostics = {
            "status": "warning",
            "summary": {"total": 0, "active": 0, "stale": 0, "unknown": 1},
            "items": [{"code": "lock_diagnostics_error", "state": "unknown", "message": str(ex)}],
        }
    stale_risks = _stale_artifact_risks(repo_root, run_dir_path)

    handle_warnings = _handle_diagnostic_warning_count(handle_diagnostics)
    lock_summary = lock_diagnostics.get("summary") if isinstance(lock_diagnostics.get("summary"), dict) else {}
    lock_stale = int(lock_summary.get("stale") or 0)
    lock_unknown = int(lock_summary.get("unknown") or 0)
    stale_summary = stale_risks.get("summary") if isinstance(stale_risks.get("summary"), dict) else {}
    blocker_count = int(stale_summary.get("blockers") or 0) + lock_stale
    warning_count = int(stale_summary.get("warnings") or 0) + lock_unknown + handle_warnings
    status = _status_from_counts(blockers=blocker_count, warnings=warning_count)
    generated_at = now_iso()
    return {
        "schema": INSTANCE_HEALTH_SCHEMA,
        "status": status,
        "ok": status != "error",
        "generated_at": generated_at,
        "generatedAt": generated_at,
        "repo": repo_root.as_posix(),
        "run_dir": _safe_path(run_dir_path),
        "runDir": _safe_path(run_dir_path),
        "process_guard": process_guard,
        "processGuard": process_guard,
        "tracked_children": tracked_children,
        "trackedChildren": tracked_children,
        "handle_diagnostics": handle_diagnostics,
        "handleDiagnostics": handle_diagnostics,
        "lock_diagnostics": lock_diagnostics,
        "lockDiagnostics": lock_diagnostics,
        "web_instance": dict(web_instance or {}),
        "webInstance": dict(web_instance or {}),
        "stale_artifacts": stale_risks,
        "staleArtifacts": stale_risks,
        "live_state": dict(live_state or {}),
        "liveState": dict(live_state or {}),
        "runner_control": dict(runner_control or {}),
        "runnerControl": dict(runner_control or {}),
        "summary": {
            "trackedPids": len(tracked_children.get("items") or []),
            "tracked_pids": len(tracked_children.get("items") or []),
            "aliveTrackedPids": int(tracked_children.get("summary", {}).get("alive") or 0),
            "alive_tracked_pids": int(tracked_children.get("summary", {}).get("alive") or 0),
            "handleWarnings": handle_warnings,
            "handle_warnings": handle_warnings,
            "staleLocks": lock_stale,
            "stale_locks": lock_stale,
            "unknownLocks": lock_unknown,
            "unknown_locks": lock_unknown,
            "staleArtifactBlockers": int(stale_summary.get("blockers") or 0),
            "stale_artifact_blockers": int(stale_summary.get("blockers") or 0),
            "staleArtifactWarnings": int(stale_summary.get("warnings") or 0),
            "stale_artifact_warnings": int(stale_summary.get("warnings") or 0),
        },
    }
