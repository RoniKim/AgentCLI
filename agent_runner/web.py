from __future__ import annotations

import argparse
import ipaddress
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
import re
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import (
    default_config_path,
    default_prompts_dir,
    legacy_default_config_path,
    load_config,
    resolve_config_path,
    resolve_prompts_dir,
)
from .cli import DEFAULTS as CLI_DEFAULTS
from .goals import goals_path, parse_goals_completion, read_goals, resolve_goals_completion_level
from .gitops import (
    apply_pending_worktree_merge,
    discard_pending_worktree_merge,
    find_pending_worktree_merge,
    git_head,
    read_pending_worktree_merge,
    scan_worktree_diagnostics,
    WorktreeSafetyError,
)
from .prompts import (
    DEV_INSTRUCTIONS_DEFAULT,
    DEV_TASK_TEMPLATE_DEFAULT,
    PM_BOOTSTRAP_TEMPLATE_DEFAULT,
    PM_INCREMENTAL_TEMPLATE_DEFAULT,
    PM_INSTRUCTIONS_DEFAULT,
    PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT,
    PromptStore,
    QA_INSTRUCTIONS_DEFAULT,
    QA_TEMPLATE_DEFAULT,
    REPORTER_INSTRUCTIONS_DEFAULT,
    _read_text_robust,
)
from .process_guard import init_process_guard, terminate_all_children
from .run_dir import find_latest_run_dir
from .remote.controller import (
    RunnerController,
    build_runner_start_options_contract,
    normalize_runner_start_options,
)
from .stop_progress import normalize_stop_progress_payload
from .state import TaskItem, count_state_task_ids, load_backlog_json, load_backlog_task_ids, load_state, parse_backlog_md
from .utils import atomic_write_json, atomic_write_text, now_iso, run_cmd

try:  # Optional dependency: the app must still import when FastAPI is absent.
    from fastapi import Body, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
except Exception:  # pragma: no cover - exercised in dependency-missing environments
    Body = None  # type: ignore[assignment]
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    FileResponse = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]

try:  # Optional dependency for the run helper.
    import uvicorn
except Exception:  # pragma: no cover - exercised in dependency-missing environments
    uvicorn = None  # type: ignore[assignment]


PROMPT_SPECS: list[dict[str, str]] = [
    {
        "id": "pm_instructions",
        "file": "pm_instructions.md",
        "scope": "PM",
        "default": PM_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "dev_instructions",
        "file": "dev_instructions.md",
        "scope": "Dev",
        "default": DEV_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "qa_instructions",
        "file": "qa_instructions.md",
        "scope": "QA",
        "default": QA_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "pm_bootstrap",
        "file": "pm_bootstrap_prompt.md",
        "scope": "PM",
        "default": PM_BOOTSTRAP_TEMPLATE_DEFAULT,
    },
    {
        "id": "pm_incremental",
        "file": "pm_incremental_prompt.md",
        "scope": "PM",
        "default": PM_INCREMENTAL_TEMPLATE_DEFAULT,
    },
    {
        "id": "dev_task",
        "file": "dev_task_prompt.md",
        "scope": "Dev",
        "default": DEV_TASK_TEMPLATE_DEFAULT,
    },
    {
        "id": "qa_prompt",
        "file": "qa_prompt.md",
        "scope": "QA",
        "default": QA_TEMPLATE_DEFAULT,
    },
    {
        "id": "reporter_instructions",
        "file": "reporter_instructions.md",
        "scope": "Reporter",
        "default": REPORTER_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "pm_shutdown_report",
        "file": "pm_shutdown_report_prompt.md",
        "scope": "Reporter",
        "default": PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT,
    },
]

STAGE_ORDER = {"pm": 0, "dev": 1, "qa": 2, "security": 3, "reporter": 4}
RUNNER_CONTROL_CONFIRMATIONS = {
    "start": "START RUNNER",
    "stop": "STOP RUNNER",
    "reload": "RELOAD RUNNER",
    "restart": "RESTART RUNNER",
}
GOALS_SAVE_CONFIRMATION_PHRASE = "DELETE OR DOWNGRADE UNMET P0 GOALS"
RUN_DIR_ARTIFACT_NAMES = {
    "BACKLOG.json",
    "BACKLOG.md",
    "STATE.json",
    "STOP",
    "cycle_summary.log",
    "last_run_summary.json",
    "metrics.jsonl",
    "run_summary.json",
    "WORKTREE_APPLY_FAILURE.md",
    "WORKTREE_MERGE_APPLIED.json",
    "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
    "WORKTREE_MERGE_DISCARDED.json",
    "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json",
    "WORKTREE_MERGE_PENDING.json",
    "WORKTREE_PATCH_NOT_APPLIED.md",
    "WORKTREE_NOT_APPLIED.md",
    "worktree.patch",
}
RUNNER_CONTROL_TRUTHY = {"1", "true", "yes", "on", "enabled"}
RUNNER_CONTROL_FALSY = {"0", "false", "no", "off", "disabled"}
SENSITIVE_CONFIG_TOKENS = {
    "api",
    "apikey",
    "api_key",
    "auth",
    "bearer",
    "bot",
    "chat",
    "client_secret",
    "credential",
    "key",
    "password",
    "secret",
    "session",
    "token",
    "webhook",
}
REDACTED_VALUE = "[redacted]"


def _repo_root(repo: Path | str | None) -> Path:
    if repo is None:
        return Path(__file__).resolve().parents[1]
    return Path(repo).expanduser().resolve()


def _path_text(value: Path | str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().as_posix()
    except Exception:
        return raw.replace("\\", "/")


def buildSectionState(kind: str, rawStatus: str, message: str, source: str = "api") -> dict[str, str]:
    status = str(rawStatus or "ready").strip() or "ready"
    return {
        "kind": str(kind),
        "state": status,
        "status": status,
        "message": str(message or ""),
        "source": str(source or "api"),
    }


def fallbackSectionMessage(kind: str) -> str:
    messages = {
        "activeRun": "No active run is published yet.",
        "stages": "No lifecycle records were published yet.",
        "backlog": "No backlog artifacts were published yet.",
        "goals": "No goals were found in GOALS.md.",
        "config": "Config snapshot is incomplete.",
        "prompts": "Prompt inventory is empty.",
        "logs": "No log entries are available yet.",
        "notifications": "No notifications have been recorded yet.",
        "metrics": "No metrics snapshot is available yet.",
        "history": "Run history is empty.",
        "worktree": "No pending worktree merge is available.",
        "runnerControl": "Runner controls are unavailable in fallback mode.",
    }
    return messages.get(kind, "No data available yet.")


def _run_dir_has_observable_artifacts(run_dir: Path | None) -> bool:
    if run_dir is None:
        return False
    try:
        if not run_dir.exists() or not run_dir.is_dir():
            return False
        for name in RUN_DIR_ARTIFACT_NAMES:
            if (run_dir / name).exists():
                return True
        logs_dir = run_dir / "logs"
        if logs_dir.exists() and logs_dir.is_dir():
            return any(item.is_file() for item in logs_dir.iterdir())
    except OSError:
        return False
    return False


def _history_worktree_outcome(run_dir: Path) -> str:
    artifacts = (
        ("WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json", "applied_cleanup_failed"),
        ("WORKTREE_MERGE_APPLIED.json", "applied"),
        ("WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json", "discard_cleanup_failed"),
        ("WORKTREE_MERGE_DISCARDED.json", "discarded"),
        ("WORKTREE_PATCH_NOT_APPLIED.md", "patch_not_applied"),
        ("WORKTREE_NOT_APPLIED.md", "not_applied"),
        ("WORKTREE_APPLY_FAILURE.md", "apply_failed"),
        ("WORKTREE_MERGE_PENDING.json", "pending"),
    )
    candidates = [(outcome, run_dir / name) for name, outcome in artifacts if (run_dir / name).exists()]
    selected = _worktree_select_artifact(candidates)
    return selected[0] if selected is not None else "none"


WORKTREE_REVIEW_CHECKLIST = [
    "Inspect patch hunks",
    "Verify no secret leakage",
    "Approve merge only after review",
    "Discard only after archival copy",
]
WORKTREE_ACTION_CONFIRMATIONS = {
    "merge": "MERGE WORKTREE",
    "discard": "DISCARD WORKTREE",
}
WORKTREE_JSON_STATUS_ARTIFACTS = [
    ("pending", "WORKTREE_MERGE_PENDING.json"),
    ("applied_cleanup_failed", "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json"),
    ("discard_cleanup_failed", "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json"),
    ("applied", "WORKTREE_MERGE_APPLIED.json"),
    ("discarded", "WORKTREE_MERGE_DISCARDED.json"),
]
WORKTREE_TEXT_STATUS_ARTIFACTS = [
    ("apply_failed", "WORKTREE_APPLY_FAILURE.md"),
    ("patch_not_applied", "WORKTREE_PATCH_NOT_APPLIED.md"),
    ("not_applied", "WORKTREE_NOT_APPLIED.md"),
]
WORKTREE_STATUS_PRIORITY = {
    "applied": 2,
    "discarded": 2,
    "applied_cleanup_failed": 1,
    "discard_cleanup_failed": 1,
    "apply_failed": 1,
    "patch_not_applied": 1,
    "not_applied": 1,
}


def _worktree_artifact_sort_key(status: str, path: Path) -> tuple[int, int]:
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return mtime_ns, WORKTREE_STATUS_PRIORITY.get(status, 0)


def _worktree_select_artifact(artifacts: list[tuple[str, Path]]) -> tuple[str, Path] | None:
    best: tuple[str, Path] | None = None
    best_score = (-1, -1)
    for artifact_status, artifact_path in artifacts:
        score = _worktree_artifact_sort_key(artifact_status, artifact_path)
        if best is None or score > best_score:
            best = (artifact_status, artifact_path)
            best_score = score
    return best


def _worktree_default_payload(repo_root: Path, run_dir: Path | None, branch: str) -> dict[str, Any]:
    source_branch = branch or "HEAD"
    run_dir_value = run_dir.as_posix() if run_dir else ""
    return {
        "status": "none",
        "mode": "manual",
        "reviewRequired": False,
        "reviewRequiredMessage": "No pending worktree merge.",
        "sourceRepo": repo_root.as_posix(),
        "sourceBranch": source_branch,
        "branch": source_branch,
        "baseRef": "",
        "headRef": "",
        "worktreeDir": "",
        "worktree": "",
        "patchPath": "",
        "patch": "",
        "pendingFile": "",
        "statusFile": "",
        "cleanupPath": "",
        "cleanupMessage": "No cleanup state is available.",
        "cleanupDetails": {},
        "cleanupAttempts": [],
        "cleanupState": "none",
        "summary": "No pending worktree merge.",
        "risk": "No isolated worktree patch is pending review.",
        "changedFiles": [],
        "checklist": list(WORKTREE_REVIEW_CHECKLIST),
        "runDir": run_dir_value,
        "runnerRc": 0,
        "lastRc": 0,
    }


def _worktree_changed_files_from_patch(patch_path: str, *, allow_placeholder: bool = True) -> list[dict[str, Any]]:
    changed_files: list[dict[str, Any]] = []
    if patch_path:
        patch_file = Path(patch_path)
        if patch_file.exists():
            try:
                text = patch_file.read_text(encoding="utf-8", errors="replace")
                seen: set[str] = set()
                for line in text.splitlines():
                    match = re.match(r"^\+\+\+\s+b/(.+)$", line)
                    if not match:
                        match = re.match(r"^diff --git a/.+ b/(.+)$", line)
                    if not match:
                        continue
                    path = match.group(1).strip()
                    if not path or path in seen:
                        continue
                    seen.add(path)
                    changed_files.append({"path": path, "kind": "modified", "note": ""})
            except Exception:
                changed_files = []
    if not changed_files and allow_placeholder and patch_path:
        changed_files = [{"path": patch_path, "kind": "modified", "note": "patch export"}]
    return changed_files


def _worktree_normalize_changed_files(raw_changed_files: Any, patch_path: str, *, allow_placeholder: bool = True) -> list[dict[str, Any]]:
    changed_files: list[dict[str, Any]] = []
    if isinstance(raw_changed_files, list):
        for item in raw_changed_files:
            if isinstance(item, dict):
                path = _pick_text(item.get("path"), item.get("file"), item.get("name"))
                if not path:
                    continue
                changed_files.append(
                    {
                        "path": path,
                        "kind": _pick_text(item.get("kind"), item.get("type"), "modified"),
                        "note": _pick_text(item.get("note"), item.get("message")),
                    }
                )
            else:
                path = _pick_text(item)
                if path:
                    changed_files.append({"path": path, "kind": "modified", "note": ""})
    if changed_files:
        return changed_files
    return _worktree_changed_files_from_patch(patch_path, allow_placeholder=allow_placeholder)


def _worktree_pending_is_stale(payload: dict[str, Any], pending_path: Path) -> str:
    run_dir_value = str(payload.get("run_dir") or "").strip()
    patch_path = str(payload.get("patch_path") or "").strip()
    status = str(payload.get("status") or "").strip().lower()
    required_fields = ("source_repo", "run_dir", "worktree_dir", "patch_path", "base_ref", "head_ref")
    missing_fields = [field for field in required_fields if not str(payload.get(field) or "").strip()]
    if missing_fields:
        return f"missing required fields ({', '.join(missing_fields)})"
    if status != "pending":
        return f"pending marker status must be pending (got {status or 'empty'})"
    if not patch_path or not Path(patch_path).exists():
        return "patch path is missing or no longer exists"
    if run_dir_value:
        expected_pending = Path(run_dir_value) / "WORKTREE_MERGE_PENDING.json"
        if pending_path.resolve() != expected_pending.resolve() and not expected_pending.exists():
            return f"run-local pending file is missing ({expected_pending.as_posix()})"
    return ""


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.expanduser().resolve().relative_to(parent.expanduser().resolve())
        return True
    except Exception:
        return False


def _worktree_status_artifacts(repo_root: Path, run_dir: Path | None) -> list[tuple[str, Path]]:
    search_dirs: list[Path] = []
    if run_dir is not None:
        search_dirs.append(run_dir)
    search_dirs.append(repo_root / ".AgentCLI")
    runs_root = repo_root / ".AgentCLI" / "agent_runs"
    if runs_root.exists():
        for candidate in sorted([path for path in runs_root.iterdir() if path.is_dir()], key=lambda path: path.name, reverse=True):
            search_dirs.append(candidate)
    artifacts: list[tuple[str, Path]] = []
    for directory in search_dirs:
        for status, name in WORKTREE_JSON_STATUS_ARTIFACTS + WORKTREE_TEXT_STATUS_ARTIFACTS:
            candidate = directory / name
            if candidate.exists():
                artifacts.append((status, candidate))
    return artifacts


def _worktree_status_payload(
    repo_root: Path,
    run_dir: Path | None,
    branch: str,
    *,
    status: str,
    artifact_path: Path,
    payload: dict[str, Any] | None = None,
    pending_path: Path | None = None,
) -> dict[str, Any]:
    base = _worktree_default_payload(repo_root, run_dir, branch)
    raw = payload if isinstance(payload, dict) else {}
    source_repo_value = str(raw.get("source_repo") or raw.get("sourceRepo") or base["sourceRepo"]).strip() or base["sourceRepo"]
    run_dir_value = str(raw.get("run_dir") or raw.get("runDir") or base["runDir"]).strip() or base["runDir"]
    worktree_dir = str(raw.get("worktree_dir") or raw.get("worktreeDir") or raw.get("worktree") or "").strip()
    patch_path = str(raw.get("patch_path") or raw.get("patchPath") or raw.get("patch") or "").strip()
    base_ref = str(raw.get("base_ref") or raw.get("baseRef") or "").strip()
    head_ref = str(raw.get("head_ref") or raw.get("headRef") or "").strip()
    source_branch = base_ref or str(raw.get("source_branch") or raw.get("sourceBranch") or base["sourceBranch"]).strip() or base["sourceBranch"]
    runner_rc = _coerce_optional_int(raw.get("last_rc") if raw.get("last_rc") is not None else raw.get("lastRc")) or 0
    cleanup_path = str(raw.get("cleanup_path") or raw.get("cleanupPath") or worktree_dir).strip()
    cleanup_message = str(
        raw.get("cleanup_message")
        or raw.get("cleanupMessage")
        or raw.get("message")
        or raw.get("cleanup_error")
        or ""
    ).strip()
    cleanup_details = raw.get("cleanup_details") if isinstance(raw.get("cleanup_details"), dict) else raw.get("cleanupDetails")
    if not isinstance(cleanup_details, dict):
        cleanup_details = {}
    cleanup_attempts = raw.get("cleanup_attempts") if isinstance(raw.get("cleanup_attempts"), list) else raw.get("cleanupAttempts")
    if not isinstance(cleanup_attempts, list):
        cleanup_attempts = []
    pending_file = pending_path.as_posix() if pending_path is not None else ""
    status_file = artifact_path.as_posix()
    changed_files = _worktree_normalize_changed_files(raw.get("changedFiles") or raw.get("changed_files"), patch_path, allow_placeholder=status not in {"error"})
    review_required = status in {
        "pending",
        "pending review",
        "error",
        "apply_failed",
        "patch_not_applied",
        "not_applied",
        "applied_cleanup_failed",
        "discard_cleanup_failed",
    }
    cleanup_state = "none"
    if status in {"pending", "pending review"}:
        cleanup_state = "pending"
    elif status in {"applied", "discarded"}:
        cleanup_state = "done"
    elif status in {"applied_cleanup_failed", "discard_cleanup_failed"}:
        cleanup_state = "failed"
    elif status in {"apply_failed", "patch_not_applied", "not_applied"}:
        cleanup_state = "none"

    summary = base["summary"]
    review_required_message = base["reviewRequiredMessage"]
    risk = base["risk"]
    if status in {"pending", "pending review"}:
        summary = "Worktree produced a patch that must be reviewed before merge."
        if runner_rc != 0:
            summary = f"Patch export completed with runner rc={runner_rc}."
        if patch_path:
            review_required_message = (
                f"Review the pending patch at {patch_path} before confirming merge or discard. "
                "Merging applies it to the source repository without creating a commit. "
                "Use /merge-worktree or /discard-worktree from the CLI when ready."
            )
        else:
            review_required_message = (
                "Review required before applying the patch to the source repository. "
                "Merging applies it to the source repository without creating a commit. "
                "Use /merge-worktree or /discard-worktree from the CLI when ready."
            )
        risk = "Manual review required before applying the patch to the source repository."
        if base_ref and head_ref:
            risk = f"Review patch from {base_ref} to {head_ref} before applying."
        cleanup_message = cleanup_message or "Cleanup has not run yet."
    elif status == "applied":
        summary = "Worktree patch applied to the source repository."
        review_required_message = "Patch applied to the source repository."
        risk = "Review the merged source-repository changes before committing."
        cleanup_message = cleanup_message or "Worktree removed after merge."
    elif status == "discarded":
        summary = "Worktree result discarded without applying the patch."
        review_required_message = "Pending worktree result discarded."
        risk = "The worktree was removed without changing the source repository."
        cleanup_message = cleanup_message or "Worktree removed after discard."
    elif status == "applied_cleanup_failed":
        summary = "Patch applied, but worktree cleanup failed."
        review_required_message = "Patch applied, but worktree cleanup failed."
        risk = "The source repository was updated, but the isolated worktree still needs cleanup."
        cleanup_message = cleanup_message or "Worktree cleanup failed after merge."
    elif status == "discard_cleanup_failed":
        summary = "Discard recorded, but worktree cleanup failed."
        review_required_message = "Discard recorded, but worktree cleanup failed."
        risk = "The isolated worktree still needs cleanup after the discard was recorded."
        cleanup_message = cleanup_message or "Worktree cleanup failed after discard."
    elif status == "error":
        summary = "Pending worktree merge file is malformed."
        if cleanup_message:
            review_required_message = cleanup_message
        else:
            review_required_message = "Pending worktree merge file is malformed."
        risk = "Fix or delete the pending merge file before applying any source-repo change."
        cleanup_message = cleanup_message or "Cleanup state is unavailable until the marker is repaired."
    elif status == "apply_failed":
        summary = "Worktree patch export failed."
        review_required_message = summary
        risk = "Manual recovery is required before the source repository can be reviewed."
        cleanup_message = cleanup_message or "Cleanup state is unavailable because patch export failed."
    elif status == "patch_not_applied":
        summary = "Worktree patch was exported but not auto-applied."
        review_required_message = summary
        risk = "Review the exported patch before applying it manually."
        cleanup_message = cleanup_message or "Cleanup has not run yet."
    elif status == "not_applied":
        summary = "Worktree patch was not applied."
        review_required_message = summary
        risk = "Review the exported patch and apply it manually when ready."
        cleanup_message = cleanup_message or "Cleanup has not run yet."

    result = {
        "status": status,
        "mode": "manual",
        "reviewRequired": review_required,
        "reviewRequiredMessage": review_required_message,
        "sourceRepo": source_repo_value,
        "sourceBranch": source_branch,
        "branch": source_branch,
        "baseRef": base_ref,
        "headRef": head_ref,
        "worktreeDir": worktree_dir,
        "worktree": worktree_dir,
        "patchPath": patch_path,
        "patch": patch_path,
        "pendingFile": pending_file,
        "statusFile": status_file,
        "cleanupPath": cleanup_path,
        "cleanupMessage": cleanup_message,
        "cleanupDetails": cleanup_details,
        "cleanupAttempts": cleanup_attempts,
        "cleanupState": cleanup_state,
        "summary": summary,
        "risk": risk,
        "changedFiles": changed_files,
        "checklist": list(WORKTREE_REVIEW_CHECKLIST),
        "runDir": run_dir_value,
        "runnerRc": runner_rc,
        "lastRc": runner_rc,
    }
    return result


def _safe_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return fallback
        payload = json.loads(raw)
        return payload if payload is not None else fallback
    except Exception:
        return fallback


def _safe_jsonl(path: Path, *, max_items: int = 400) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_items)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except Exception:
        return []
    return list(rows)


EXECUTION_STATUS_ALIASES = {
    "complete": "completed",
    "completed": "completed",
    "done": "completed",
    "finished": "stopped",
    "halted": "stopped",
    "stopping": "stopped",
    "stopped": "stopped",
    "stop_requested": "stopped",
    "cancelled": "stopped",
    "canceled": "stopped",
    "aborted": "stopped",
    "error": "failed",
    "ok": "completed",
    "prepared_only": "completed",
    "success": "completed",
}
EXECUTION_STATUS_VALUES = {"idle", "running", "stopped", "failed", "completed"}


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except Exception:
        return None


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


_QUOTA_WINDOWS = {"5h", "7d"}


def _quota_has_real_usage(window: Any, used: Any) -> bool:
    used_value = _coerce_optional_float(used)
    window_value = _pick_text(window)
    return used_value is not None and bool(window_value) and window_value.lower() in _QUOTA_WINDOWS


def _quota_payload(window: Any = "", used: Any = None) -> dict[str, Any]:
    used_value = _coerce_optional_float(used)
    window_value = _pick_text(window)
    available = _quota_has_real_usage(window_value, used_value)
    return {
        "window": window_value if available else "",
        "used": round(used_value, 3) if available else None,
        "available": available,
    }


def _quota_payload_from_source(source: Any) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    quota = raw.get("quota") if isinstance(raw.get("quota"), dict) else {}
    return _quota_payload(
        _pick_text(quota.get("window"), raw.get("quota_window"), raw.get("quotaWindow")),
        _pick_value(quota.get("used"), raw.get("quota_used"), raw.get("quotaUsed")),
    )


def _pick_quota_payload(*sources: Any) -> dict[str, Any]:
    for source in sources:
        quota = _quota_payload_from_source(source)
        if quota.get("available"):
            return quota
    return _quota_payload("", None)


def _coerce_optional_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(float(value))
        except Exception:
            return None
    raw = str(value).strip()
    if not raw:
        return None
    numeric = _coerce_optional_int(raw)
    if numeric is not None:
        return numeric
    ms = _iso_to_ms(raw)
    return ms or None


def _pick_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _pick_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalize_execution_status(
    raw_status: Any,
    *,
    running: bool = False,
    exit_code: Any = None,
    final_reason: str = "",
    stop_file_exists: bool = False,
    has_run_dir: bool = False,
) -> str:
    status = _pick_text(raw_status).lower()
    status = EXECUTION_STATUS_ALIASES.get(status, status)
    if status in EXECUTION_STATUS_VALUES:
        return status
    if status == "no-run":
        return "idle"
    if running:
        return "running"

    reason = _pick_text(final_reason).lower()
    if reason in {"project_complete", "all_tasks_done", "completed", "success", "ok", "done"}:
        return "completed"
    if reason in {"stop_file", "stop_requested", "stopped", "user_stop", "manual_stop"} or stop_file_exists:
        return "stopped"

    rc = _coerce_optional_int(exit_code)
    if rc is not None:
        if rc == 0 and reason in {"", "ok", "prepared_only", "completed", "success", "project_complete", "all_tasks_done", "done"} and has_run_dir:
            return "completed"
        if rc != 0:
            return "failed"

    if reason in {"failed", "error", "exception", "abandoned", "abandon_failed", "build_failed", "test_failed", "policy_violation", "exhausted_attempts"}:
        return "failed"
    return "running" if has_run_dir else "idle"


def _normalize_run_status(
    raw_status: Any,
    *,
    running: bool = False,
    exit_code: Any = None,
    final_reason: str = "",
    stop_file_exists: bool = False,
    has_run_dir: bool = False,
    project_complete: bool = False,
) -> str:
    execution_status = _normalize_execution_status(
        raw_status,
        running=running,
        exit_code=exit_code,
        final_reason=final_reason,
        stop_file_exists=stop_file_exists,
        has_run_dir=has_run_dir,
    )
    if project_complete and execution_status == "completed":
        return "success"
    return execution_status


def _project_completion_status(
    goals: dict[str, Any],
    *,
    tasks_total: int = 0,
    tasks_done: int = 0,
    tasks_failed: int = 0,
) -> dict[str, Any]:
    completion = goals.get("completion") if isinstance(goals.get("completion"), dict) else {}
    goals_complete = bool(completion.get("project_complete"))
    has_goals = bool(completion.get("has_goals"))
    backlog_complete = tasks_total == 0 or (tasks_done >= tasks_total and tasks_failed == 0)
    project_complete = bool(goals_complete and backlog_complete)
    return {
        "has_goals": has_goals,
        "goals_complete": goals_complete,
        "backlog_complete": backlog_complete,
        "project_complete": project_complete,
        "project_status": "complete" if project_complete else "incomplete",
    }


def _is_sensitive_config_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if not normalized:
        return False
    parts = {part for part in normalized.split("_") if part}
    if "pairing" in parts and "code" in parts:
        return True
    return normalized in SENSITIVE_CONFIG_TOKENS or bool(parts & SENSITIVE_CONFIG_TOKENS)


def _redact_config(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_config_key(key):
        if isinstance(value, bool) or value in (None, ""):
            return value
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {str(item_key): _redact_config(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _web_redaction_active(bind_host: str) -> bool:
    return not _host_is_loopback(bind_host)


def _redact_web_text(value: Any) -> Any:
    if value in (None, "", False):
        return value
    return REDACTED_VALUE


def _web_redaction_meta(*fields: str) -> dict[str, Any]:
    cleaned = [str(field).strip() for field in fields if str(field).strip()]
    return {
        "active": True,
        "placeholder": REDACTED_VALUE,
        "fields": list(dict.fromkeys(cleaned)),
        "scope": "lan",
    }


def _web_apply_redaction(payload: Any, *, active: bool, redactor: Any | None = None) -> Any:
    if not active or redactor is None:
        return payload
    try:
        return redactor(payload)
    except Exception:
        return payload


def _redact_web_log_entry(entry: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(entry)
    for key in ("msg", "message", "raw", "text", "reason", "detail", "excerpt", "output", "content", "preview", "path", "trace", "stack"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    return redacted


def _redact_web_log_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    entries = redacted.get("entries")
    if isinstance(entries, list):
        redacted["entries"] = [_redact_web_log_entry(entry) if isinstance(entry, dict) else entry for entry in entries]
    tail = redacted.get("tail")
    if tail not in (None, "", False):
        redacted["tail"] = REDACTED_VALUE
    files = redacted.get("files")
    if isinstance(files, dict):
        files_copy = deepcopy(files)
        redaction_fields: list[str] = []
        for key, value in list(files_copy.items()):
            if value not in (None, "", False):
                files_copy[key] = REDACTED_VALUE
                redaction_fields.append(f"files.{str(key).strip()}")
        redacted["files"] = files_copy
    else:
        redaction_fields = []
    for key in ("source_file", "source_path", "error"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    source = redacted.get("source")
    if isinstance(source, dict):
        source_copy = deepcopy(source)
        if source_copy.get("path") not in (None, "", False):
            source_copy["path"] = REDACTED_VALUE
        if source_copy.get("name") not in (None, "", False):
            source_copy["name"] = REDACTED_VALUE
        redacted["source"] = source_copy
    redacted["redaction"] = _web_redaction_meta(
        "entries.msg",
        "entries.message",
        "entries.raw",
        "entries.text",
        "entries.reason",
        "entries.detail",
        "entries.excerpt",
        "entries.output",
        "entries.content",
        "entries.preview",
        "entries.path",
        "entries.trace",
        "entries.stack",
        "tail",
        *redaction_fields,
        "source.path",
        "source.name",
        "source_file",
        "source_path",
        "error",
    )
    return redacted


def _redact_web_goal_warning(warning: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(warning)
    for key in ("line", "raw", "text", "excerpt", "message", "detail"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    return redacted


def _redact_web_goals_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    if redacted.get("raw_text") not in (None, "", False):
        redacted["raw_text"] = REDACTED_VALUE
    if redacted.get("rawText") not in (None, "", False):
        redacted["rawText"] = REDACTED_VALUE
    warnings = redacted.get("warnings")
    if isinstance(warnings, list):
        redacted["warnings"] = [_redact_web_goal_warning(warning) if isinstance(warning, dict) else warning for warning in warnings]
    redacted["redaction"] = _web_redaction_meta("raw_text", "rawText", "warnings.raw", "warnings.text", "warnings.excerpt")
    return redacted


def _redact_web_backlog_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    for key in (
        "prompt",
        "description",
        "skills_rationale",
        "skillsRationale",
        "recent_output",
        "recentOutput",
        "failure_detail",
        "failureDetail",
        "output",
        "output_excerpt",
        "outputExcerpt",
        "excerpt",
        "detail",
        "trace",
        "stack",
    ):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    failure = redacted.get("failure")
    if isinstance(failure, dict):
        failure_copy = deepcopy(failure)
        for key in ("detail", "message", "output", "excerpt", "trace", "stack"):
            if failure_copy.get(key) not in (None, "", False):
                failure_copy[key] = REDACTED_VALUE
        redacted["failure"] = failure_copy
    return redacted


def _redact_web_backlog_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    items = redacted.get("items")
    if isinstance(items, list):
        redacted["items"] = [_redact_web_backlog_item(item) if isinstance(item, dict) else item for item in items]
    redacted["redaction"] = _web_redaction_meta(
        "items.recent_output",
        "items.recentOutput",
        "items.output",
        "items.outputExcerpt",
        "items.excerpt",
        "items.detail",
        "items.trace",
        "items.stack",
        "items.failure.detail",
        "items.failure.message",
        "items.failure.output",
        "items.failure.excerpt",
        "items.failure.trace",
        "items.failure.stack",
        "items.failureDetail",
    )
    return redacted


def _redact_web_stage(stage: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(stage)
    changed = False
    for key in ("recentOutput", "recent_output"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            changed = True
    if changed:
        redacted["redaction"] = _web_redaction_meta("recentOutput", "recent_output")
    return redacted


def _redact_web_stages_payload(stages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_redact_web_stage(stage) if isinstance(stage, dict) else stage for stage in stages]


def _redact_web_prompt_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    for key in ("preview", "content"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    return redacted


def _redact_web_prompts_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    if redacted.get("dir") not in (None, "", False):
        redacted["dir"] = REDACTED_VALUE
    items = redacted.get("items")
    if isinstance(items, list):
        redacted["items"] = [_redact_web_prompt_item(item) if isinstance(item, dict) else item for item in items]
    redacted["redaction"] = _web_redaction_meta("dir", "items.preview", "items.content")
    return redacted


def _redact_web_notification_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    for key in ("text", "message", "detail"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    redacted["redaction"] = _web_redaction_meta("text", "message", "detail")
    return redacted


def _redact_web_notifications_payload(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_redact_web_notification_item(item) if isinstance(item, dict) else item for item in items]


def _redact_web_history_item(item: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(item)
    redaction_fields: list[str] = []
    for key in ("lastCycle", "last_cycle"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    for key in ("runSummary", "run_summary", "lastRunSummary", "last_run_summary"):
        summary = redacted.get(key)
        if isinstance(summary, dict):
            redacted[key] = _redact_web_history_summary(summary)
            redaction_fields.append(key)
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_history_summary(summary: Any) -> Any:
    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            redacted_value: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in {
                    "path",
                    "config_path",
                    "configPath",
                    "defaults_path",
                    "defaultsPath",
                    "recentOutput",
                    "recent_output",
                    "output",
                    "outputExcerpt",
                    "output_excerpt",
                    "excerpt",
                    "raw_text",
                    "rawText",
                    "content",
                    "text",
                }:
                    redacted_value[key] = REDACTED_VALUE if item not in (None, "", False) else item
                elif key_text in {"start_options", "startOptions"} and isinstance(item, dict):
                    redacted_value[key] = _redact_web_runner_start_options(item)
                else:
                    redacted_value[key] = _walk(item)
            return redacted_value
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(deepcopy(summary))


def _redact_web_history_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    items = redacted.get("items")
    if isinstance(items, list):
        redacted["items"] = [_redact_web_history_item(item) if isinstance(item, dict) else item for item in items]
    redacted["redaction"] = _web_redaction_meta(
        "items.lastCycle",
        "items.last_cycle",
        "items.runSummary",
        "items.run_summary",
        "items.lastRunSummary",
        "items.last_run_summary",
    )
    return redacted


def _redact_web_runner_start_options(start_options: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(start_options)
    redaction = dict(redacted.get("redaction") or {})
    redaction["active"] = True
    redaction["placeholder"] = REDACTED_VALUE
    paths_value = redaction.get("paths")
    redaction_paths = []
    if isinstance(paths_value, list):
        redaction_paths = list(dict.fromkeys([str(path) for path in paths_value if str(path).strip()]))
    for path in ("repo", "path", "defaults_path", "values.config_path", "defaults.config_path", "values.run_dir", "defaults.run_dir"):
        if path not in redaction_paths:
            redaction_paths.append(path)
    redaction["paths"] = redaction_paths
    redacted["redaction"] = redaction
    redacted["redacted"] = True
    for key in ("repo", "path", "defaults_path"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
    for key in ("values", "defaults"):
        section = redacted.get(key)
        if isinstance(section, dict):
            section_copy = deepcopy(section)
            if section_copy.get("config_path") not in (None, "", False):
                section_copy["config_path"] = REDACTED_VALUE
            if section_copy.get("run_dir") not in (None, "", False):
                section_copy["run_dir"] = REDACTED_VALUE
            redacted[key] = section_copy
    argv_preview = redacted.get("argv_preview")
    if isinstance(argv_preview, list):
        preview = list(argv_preview)
        for idx, token in enumerate(preview):
            if token in {"--repo", "--config", "--run-dir"} and idx + 1 < len(preview):
                preview[idx + 1] = REDACTED_VALUE
        redacted["argv_preview"] = preview
    return redacted


def _redact_web_runner_status_payload(status: dict[str, Any], *, redact_start_options: bool = False) -> dict[str, Any]:
    redacted = deepcopy(status)
    redaction_fields: list[str] = []
    for key in ("config_path", "configPath"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    start_options = redacted.get("start_options")
    if redact_start_options and isinstance(start_options, dict):
        redacted_start_options = _redact_web_runner_start_options(start_options)
        redacted["start_options"] = redacted_start_options
        redacted["startOptions"] = redacted_start_options
        redaction_fields.extend(
            [
                "start_options.repo",
                "start_options.path",
                "start_options.defaults_path",
                "start_options.values.config_path",
                "start_options.defaults.config_path",
                "start_options.values.run_dir",
                "start_options.defaults.run_dir",
                "start_options.argv_preview",
            ]
        )
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_runner_control(control: dict[str, Any], *, redact_start_options: bool = False) -> dict[str, Any]:
    redacted = deepcopy(control)
    redaction_fields: list[str] = []
    status = redacted.get("status")
    if isinstance(status, dict):
        status_copy = _redact_web_runner_status_payload(status, redact_start_options=redact_start_options)
        redacted["status"] = status_copy
        for key in ("config_path", "configPath"):
            if status.get(key) not in (None, "", False):
                redaction_fields.append(f"status.{key}")
            if redact_start_options and isinstance(status.get("start_options"), dict):
                redaction_fields.extend(
                    [
                        "status.start_options.repo",
                        "status.start_options.path",
                        "status.start_options.defaults_path",
                        "status.start_options.values.config_path",
                        "status.start_options.defaults.config_path",
                        "status.start_options.values.run_dir",
                        "status.start_options.defaults.run_dir",
                        "status.start_options.argv_preview",
                    ]
                )
    start_options = redacted.get("start_options")
    if redact_start_options and isinstance(start_options, dict):
        redacted_start_options = _redact_web_runner_start_options(start_options)
        redacted["start_options"] = redacted_start_options
        redacted["startOptions"] = redacted_start_options
        redaction_fields.extend(
            [
                "start_options.repo",
                "start_options.path",
                "start_options.defaults_path",
                "start_options.values.config_path",
                "start_options.defaults.config_path",
                "start_options.values.run_dir",
                "start_options.defaults.run_dir",
                "start_options.argv_preview",
            ]
        )
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_runner_result(result: dict[str, Any]) -> dict[str, Any]:
    def _walk(value: Any) -> Any:
        if isinstance(value, dict):
            redacted_value: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if key_text in {"config_path", "configPath"}:
                    redacted_value[key] = REDACTED_VALUE if item not in (None, "", False) else item
                elif key_text in {"repo", "repoPath", "run_dir", "runDir", "worktree_dir", "worktreeDir"}:
                    redacted_value[key] = _path_text(item)
                elif key_text in {"start_options", "startOptions"} and isinstance(item, dict):
                    redacted_value[key] = _redact_web_runner_start_options(item)
                else:
                    redacted_value[key] = _walk(item)
            return redacted_value
        if isinstance(value, list):
            return [_walk(item) for item in value]
        return value

    return _walk(deepcopy(result))


def _redact_web_config_payload(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    redaction_fields: list[str] = []
    for key in ("path", "resolved_prompts_dir"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    meta = redacted.get("meta")
    if isinstance(meta, dict):
        meta_copy = deepcopy(meta)
        for key in ("path", "resolved_prompts_dir"):
            if meta_copy.get(key) not in (None, "", False):
                meta_copy[key] = REDACTED_VALUE
                redaction_fields.append(f"meta.{key}")
        redacted["meta"] = meta_copy
    if redaction_fields:
        redacted["redaction"] = _web_redaction_meta(*redaction_fields)
    return redacted


def _redact_web_config_contract(payload: dict[str, Any]) -> dict[str, Any]:
    redacted = deepcopy(payload)
    redaction_fields: list[str] = []
    for key in ("path", "resolved_prompts_dir"):
        if redacted.get(key) not in (None, "", False):
            redacted[key] = REDACTED_VALUE
            redaction_fields.append(key)
    meta = redacted.get("meta")
    if isinstance(meta, dict):
        meta_copy = deepcopy(meta)
        for key in ("path", "resolved_prompts_dir"):
            if meta_copy.get(key) not in (None, "", False):
                meta_copy[key] = REDACTED_VALUE
                redaction_fields.append(f"meta.{key}")
        redacted["meta"] = meta_copy
    if redaction_fields:
        redaction = dict(redacted.get("redaction") or {})
        redaction["active"] = True
        redaction["placeholder"] = REDACTED_VALUE
        redaction["scope"] = "lan"
        redaction_fields_existing: list[str] = []
        for field in list(redaction.get("fields") or []):
            field_text = str(field).strip()
            if field_text:
                redaction_fields_existing.append(field_text)
        redaction["fields"] = list(dict.fromkeys(redaction_fields_existing + redaction_fields))
        redacted["redaction"] = redaction
    return redacted


CONFIG_CONTRACT_GROUPS: list[dict[str, Any]] = [
    {
        "id": "project",
        "title": "Project",
        "paths": ["repo", "profile", "execution_backend", "roles"],
    },
    {
        "id": "runner",
        "title": "Runner",
        "paths": [
            "autopilot",
            "continuous",
            "iterations",
            "max_turns_per_task",
            "loop",
            "loop_sleep_seconds",
            "loop_max_cycles",
            "loop_idle_exit_after",
            "idle_exit_cycles",
            "max_consecutive_failed_cycles",
            "run_tests",
            "budget_reset_per_cycle",
        ],
    },
    {
        "id": "quota",
        "title": "Quota",
        "paths": [
            "quota_check_enabled",
            "quota_five_hour_max_utilization",
            "quota_seven_day_max_utilization",
            "quota_wait_for_reset",
        ],
    },
    {
        "id": "worktree",
        "title": "Worktree",
        "paths": [
            "worktree_isolation",
            "isolate_task",
            "gitops.worktree_merge_mode",
            "gitops.untracked_exclude_globs",
        ],
    },
    {
        "id": "prompts",
        "title": "Prompt Paths",
        "paths": ["prompts_dir"],
    },
    {
        "id": "codex_models",
        "title": "Codex Models",
        "paths": [
            "pm_model",
            "dev_model",
            "dev_model_tier1",
            "dev_model_tier2",
            "qa_model",
            "reporter_model",
        ],
    },
    {
        "id": "pm_refresh",
        "title": "PM Refresh",
        "paths": ["pm_refresh_backlog", "pm_refresh_every_cycles", "pm_include_working_tree"],
    },
    {
        "id": "budget",
        "title": "Budget",
        "paths": [
            "budgets.max_pm_structured_retries",
            "budgets.max_dev_escalations_per_task",
            "budgets.max_dev_continuations_per_task",
            "budgets.max_total_escalations_per_run",
            "budgets.max_total_continuations_per_run",
            "budgets.max_total_repair_attempts_per_run",
        ],
    },
    {
        "id": "telegram",
        "title": "Telegram",
        "paths": [
            "telegram.enabled",
            "telegram.runner_mode",
            "telegram.poll_timeout_seconds",
            "telegram.allowed_chat_ids",
            "telegram.bot_token",
            "telegram.pairing_code",
            "telegram.instance_name",
            "telegram.notify_events",
            "telegram.send_cycle_summary",
            "telegram.notify_poll_interval_seconds",
            "telegram.stalled_seconds",
            "telegram.tail_lines_default",
        ],
    },
    {
        "id": "goals",
        "title": "Goals",
        "paths": [
            "goals_enabled",
            "goals_auto_generate",
            "goals_auto_check",
            "goals_auto_refresh",
            "goals_refresh_max_per_run",
            "goals_completion_level",
        ],
    },
]

CONFIG_CONTRACT_FIELDS: list[dict[str, Any]] = [
    {"path": "repo", "group": "project", "kind": "text", "label": "Repository", "restart": True, "allow_empty": False, "desc": "Repository root the runner targets.", "hint": "Set automatically from the repo the server serves."},
    {"path": "profile", "group": "project", "kind": "enum", "label": "Profile", "restart": True, "options": ["personal", "enterprise"], "allow_empty": False, "desc": "Default safety profile used to derive runner limits.", "hint": "Enterprise raises several guardrails."},
    {"path": "execution_backend", "group": "project", "kind": "enum", "label": "Execution backend", "restart": True, "options": ["codex", "claudecode"], "allow_empty": False, "desc": "Backend used for Dev and QA stages.", "hint": "codex = OpenAI Codex CLI, claudecode = Claude Code."},
    {"path": "roles", "group": "project", "kind": "multienum", "label": "Pipeline roles", "options": ["PM", "Security", "Dev", "QA"], "allow_empty": False, "desc": "Stages enabled in the pipeline.", "hint": "PM usually runs first. Security requires security.enabled=true and can run before Dev."},
    {"path": "autopilot", "group": "runner", "kind": "bool", "label": "Autopilot", "allow_empty": True, "desc": "Skip interactive confirmation prompts.", "hint": "When off, the runner pauses between stages."},
    {"path": "continuous", "group": "runner", "kind": "bool", "label": "Continuous", "allow_empty": True, "desc": "Keep chaining cycles without manual stopping.", "hint": "Best paired with autopilot for unattended runs."},
    {"path": "iterations", "group": "runner", "kind": "number", "label": "Iterations", "min": 1, "allow_empty": False, "desc": "Maximum run iterations.", "hint": "One iteration equals one PM -> Dev -> QA cycle."},
    {"path": "max_turns_per_task", "group": "runner", "kind": "number", "label": "Max turns per task", "min": 1, "allow_empty": False, "desc": "Upper bound for per-task model turns.", "hint": "Keeps a single task from spinning forever."},
    {"path": "stop_wait_timeout_seconds", "group": "runner", "kind": "number", "label": "Stop wait timeout seconds", "min": 1, "allow_empty": False, "desc": "How long /stop --wait and web stop wait for runner finalization before reporting timeout.", "hint": "The runner still honors STOP; this only controls the operator wait window."},
    {"path": "loop", "group": "runner", "kind": "bool", "label": "Loop", "allow_empty": True, "desc": "Keep the runner cycling after a run completes.", "hint": "Pair with loop_sleep_seconds to avoid busy looping."},
    {"path": "loop_sleep_seconds", "group": "runner", "kind": "number", "label": "Loop sleep seconds", "min": 1, "allow_empty": False, "desc": "Delay between looped runs.", "hint": "Longer sleeps reduce churn when no work is queued."},
    {"path": "loop_max_cycles", "group": "runner", "kind": "number", "label": "Loop max cycles", "min": 0, "allow_empty": False, "desc": "Hard cap on loop cycles.", "hint": "Zero means no extra cap beyond the rest of the runner."},
    {"path": "loop_idle_exit_after", "group": "runner", "kind": "number", "label": "Loop idle exit after", "min": 0, "allow_empty": False, "desc": "Exit after this many idle loop passes.", "hint": "Zero keeps the loop running until a different stop condition fires."},
    {"path": "idle_exit_cycles", "group": "runner", "kind": "number", "label": "Idle exit cycles", "min": 1, "allow_empty": False, "desc": "How many idle cycles trigger shutdown.", "hint": "Useful for unattended runs that should stop when no work remains."},
    {"path": "max_consecutive_failed_cycles", "group": "runner", "kind": "number", "label": "Max consecutive failed cycles", "min": 0, "allow_empty": False, "desc": "Stop after this many failed cycles in a row.", "hint": "Prevents the runner from grinding through repeated failures."},
    {"path": "run_tests", "group": "runner", "kind": "bool", "label": "Run tests", "allow_empty": True, "desc": "Run the test suite during QA.", "hint": "Keeps verification inside the task loop."},
    {"path": "budget_reset_per_cycle", "group": "runner", "kind": "bool", "label": "Budget reset per cycle", "allow_empty": True, "desc": "Reset cycle-level budget tracking every cycle.", "hint": "Useful when cycle-level guardrails matter more than the full run."},
    {"path": "quota_check_enabled", "group": "quota", "kind": "bool", "label": "Quota checks", "allow_empty": True, "desc": "Enable quota utilization checks.", "hint": "Disabling this removes the quota guardrails from the runner."},
    {"path": "quota_five_hour_max_utilization", "group": "quota", "kind": "number", "label": "5h max utilization", "min": 0, "max": 100, "allow_empty": False, "desc": "Five-hour quota utilization ceiling.", "hint": "Percent used before the runner stops or pauses."},
    {"path": "quota_seven_day_max_utilization", "group": "quota", "kind": "number", "label": "7d max utilization", "min": 0, "max": 100, "allow_empty": False, "desc": "Seven-day quota utilization ceiling.", "hint": "Percent used before the runner stops or pauses."},
    {"path": "quota_wait_for_reset", "group": "quota", "kind": "bool", "label": "Wait for reset", "allow_empty": True, "desc": "Pause until quota resets instead of failing fast.", "hint": "Keeps the runner from hammering an exhausted quota window."},
    {"path": "worktree_isolation", "group": "worktree", "kind": "bool", "label": "Worktree isolation", "restart": True, "allow_empty": True, "desc": "Run tasks in an isolated git worktree.", "hint": "Recommended for shared machines and safety-sensitive changes."},
    {"path": "isolate_task", "group": "worktree", "kind": "bool", "label": "Isolate task", "allow_empty": True, "desc": "Give each task an isolated workspace.", "hint": "Helps keep per-task edits clean when the runner fans out."},
    {"path": "gitops.worktree_merge_mode", "group": "worktree", "kind": "enum", "label": "Merge mode", "restart": True, "options": ["manual", "auto"], "allow_empty": False, "desc": "How worktree patches are merged.", "hint": "Manual mode keeps review in the loop."},
    {"path": "gitops.untracked_exclude_globs", "group": "worktree", "kind": "list", "label": "Untracked exclude globs", "item_kind": "text", "allow_empty": True, "desc": "Comma-separated globs ignored by worktree review.", "hint": "Keep generated files out of merge noise."},
    {"path": "prompts_dir", "group": "prompts", "kind": "text", "label": "Prompts directory", "restart": True, "allow_empty": True, "desc": "Directory that stores repo-specific prompt templates.", "hint": "Empty means the repo-specific default prompts directory."},
    {"path": "pm_model", "group": "codex_models", "kind": "text", "label": "PM model", "allow_empty": False, "desc": "Model used for PM planning and backlog generation.", "hint": "Usually a lightweight Codex model."},
    {"path": "dev_model", "group": "codex_models", "kind": "text", "label": "Dev model", "allow_empty": False, "desc": "Model used for the main Dev pass.", "hint": "This is the default model for code changes."},
    {"path": "dev_model_tier1", "group": "codex_models", "kind": "text", "label": "Dev model tier 1", "allow_empty": False, "desc": "First escalation model for Dev.", "hint": "Used after retries or capped responses."},
    {"path": "dev_model_tier2", "group": "codex_models", "kind": "text", "label": "Dev model tier 2", "allow_empty": False, "desc": "Second escalation model for Dev.", "hint": "Used when tier 1 still cannot finish the task."},
    {"path": "qa_model", "group": "codex_models", "kind": "text", "label": "QA model", "allow_empty": False, "desc": "Model used for QA verification.", "hint": "Usually matches the cheaper Codex tier."},
    {"path": "reporter_model", "group": "codex_models", "kind": "text", "label": "Reporter model", "allow_empty": False, "desc": "Model used for close-out reporting.", "hint": "Generates the final run summary."},
    {"path": "pm_refresh_backlog", "group": "pm_refresh", "kind": "bool", "label": "Refresh backlog", "allow_empty": True, "desc": "Let PM refresh the backlog from live context.", "hint": "Useful when the backlog should absorb new work after a run."},
    {"path": "pm_refresh_every_cycles", "group": "pm_refresh", "kind": "number", "label": "Refresh every cycles", "min": 0, "allow_empty": False, "desc": "Refresh cadence for PM backlog updates.", "hint": "Zero disables periodic refreshes."},
    {"path": "pm_include_working_tree", "group": "pm_refresh", "kind": "bool", "label": "Include working tree", "allow_empty": True, "desc": "Let PM inspect the working tree during refresh.", "hint": "Helps PM pick up local edits while refreshing the backlog."},
    {"path": "budgets.max_pm_structured_retries", "group": "budget", "kind": "number", "label": "PM structured retries", "min": 0, "allow_empty": False, "desc": "Retry cap for structured PM output.", "hint": "Prevents retry loops when PM output keeps failing schema checks."},
    {"path": "budgets.max_dev_escalations_per_task", "group": "budget", "kind": "number", "label": "Dev escalations per task", "min": 0, "allow_empty": False, "desc": "Escalation budget for a single Dev task.", "hint": "Used to cap repeated model escalations."},
    {"path": "budgets.max_dev_continuations_per_task", "group": "budget", "kind": "number", "label": "Dev continuations per task", "min": 0, "allow_empty": False, "desc": "Continuation budget for a single Dev task.", "hint": "Keeps partial response continuations bounded."},
    {"path": "budgets.max_total_escalations_per_run", "group": "budget", "kind": "number", "label": "Total escalations per run", "min": 0, "allow_empty": False, "desc": "Escalation budget for the full run.", "hint": "Set to zero to disable the cap."},
    {"path": "budgets.max_total_continuations_per_run", "group": "budget", "kind": "number", "label": "Total continuations per run", "min": 0, "allow_empty": False, "desc": "Continuation budget for the full run.", "hint": "Set to zero to disable the cap."},
    {"path": "budgets.max_total_repair_attempts_per_run", "group": "budget", "kind": "number", "label": "Total repair attempts", "min": 0, "allow_empty": False, "desc": "Repair budget for the full run.", "hint": "Limits repeated repair loops across stages."},
    {"path": "telegram.enabled", "group": "telegram", "kind": "bool", "label": "Enabled", "restart": True, "allow_empty": True, "desc": "Mirror run events to Telegram.", "hint": "Local notification bridge only."},
    {"path": "telegram.runner_mode", "group": "telegram", "kind": "enum", "label": "Runner mode", "restart": True, "options": ["thread", "subprocess"], "allow_empty": False, "desc": "How the Telegram runner is hosted.", "hint": "Thread mode stays in-process. Subprocess mode isolates the service."},
    {"path": "telegram.poll_timeout_seconds", "group": "telegram", "kind": "number", "label": "Poll timeout seconds", "min": 1, "allow_empty": False, "desc": "Long-poll timeout for Telegram control-plane requests.", "hint": "Longer timeouts reduce polling chatter."},
    {"path": "telegram.allowed_chat_ids", "group": "telegram", "kind": "list", "label": "Allowed chat IDs", "item_kind": "int", "allow_empty": True, "desc": "Comma-separated allowlisted Telegram chat IDs.", "hint": "Empty means any chat id is currently allowed by policy."},
    {"path": "telegram.bot_token", "group": "telegram", "kind": "text", "label": "Bot token", "restart": True, "redacted": True, "allow_empty": True, "desc": "Telegram bot token used for remote control.", "hint": "Shown as redacted in the browser."},
    {"path": "telegram.pairing_code", "group": "telegram", "kind": "text", "label": "Pairing code", "restart": True, "redacted": True, "allow_empty": True, "desc": "One-time pairing code for Telegram control.", "hint": "Shown as redacted in the browser."},
    {"path": "telegram.instance_name", "group": "telegram", "kind": "text", "label": "Instance name", "allow_empty": True, "desc": "Friendly label surfaced in Telegram messages.", "hint": "Useful when multiple runners share one chat."},
    {"path": "telegram.notify_events", "group": "telegram", "kind": "list", "label": "Notify events", "item_kind": "text", "allow_empty": True, "desc": "Comma-separated push events for Telegram notifications.", "hint": "Examples: run_start, task_done, quota."},
    {"path": "telegram.send_cycle_summary", "group": "telegram", "kind": "bool", "label": "Send cycle summary", "allow_empty": True, "desc": "Push new cycle summary lines to Telegram.", "hint": "Helpful when the runner is unattended."},
    {"path": "telegram.notify_poll_interval_seconds", "group": "telegram", "kind": "number", "label": "Notify poll interval", "min": 2, "allow_empty": False, "desc": "Polling interval used by Telegram notification refresh.", "hint": "Longer intervals reduce background polling."},
    {"path": "telegram.stalled_seconds", "group": "telegram", "kind": "number", "label": "Stalled seconds", "min": 60, "allow_empty": False, "desc": "Threshold before a run is considered stalled.", "hint": "Helps identify slow or hung runs."},
    {"path": "telegram.tail_lines_default", "group": "telegram", "kind": "number", "label": "Tail lines default", "min": 1, "allow_empty": False, "desc": "Default number of log lines included in Telegram pushes.", "hint": "Keeps notifications compact."},
    {"path": "goals_enabled", "group": "goals", "kind": "bool", "label": "Goals enabled", "allow_empty": True, "desc": "Enable GOALS.md tracking.", "hint": "Disabling this turns off the goals completion gate."},
    {"path": "goals_auto_generate", "group": "goals", "kind": "bool", "label": "Auto-generate goals", "allow_empty": True, "desc": "Auto-generate goals content from PM context.", "hint": "Useful when goals are derived from the current task set."},
    {"path": "goals_auto_check", "group": "goals", "kind": "bool", "label": "Auto-check goals", "allow_empty": True, "desc": "Re-check goals completion automatically.", "hint": "Keeps completion status in sync with the latest snapshot."},
    {"path": "goals_auto_refresh", "group": "goals", "kind": "bool", "label": "Auto-refresh goals", "allow_empty": True, "desc": "Refresh GOALS.md after project completion.", "hint": "Useful for the next run once the current project is complete."},
    {"path": "goals_refresh_max_per_run", "group": "goals", "kind": "number", "label": "Goals refresh max per run", "min": 0, "allow_empty": False, "desc": "Hard cap on goals refresh attempts per run.", "hint": "Zero disables refresh retries."},
    {"path": "goals_completion_level", "group": "goals", "kind": "enum", "label": "Goals completion level", "options": ["p0", "p1", "all"], "allow_empty": False, "desc": "Which goals must be satisfied to treat the project as complete.", "hint": "p0 is legacy, p1 includes P1, all requires every checkbox."},
]


def _config_path_get(tree: Any, path: str) -> Any:
    current = tree
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        if part not in current:
            return None
        current = current[part]
    return current


def _config_path_set(tree: dict[str, Any], path: str, value: Any) -> None:
    current = tree
    parts = path.split(".")
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _merge_config_tree(base: Any, overlay: Any) -> Any:
    if not isinstance(base, dict):
        return deepcopy(overlay)
    result = deepcopy(base)
    if not isinstance(overlay, dict):
        return result
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_config_tree(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _normalize_config_list(value: Any, *, item_kind: str = "text") -> list[Any]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    elif value in (None, ""):
        raw_items = []
    else:
        raw_items = [value]

    items: list[Any] = []
    for item in raw_items:
        if item_kind in {"int", "number"}:
            try:
                items.append(int(str(item).strip()))
                continue
            except Exception:
                pass
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def _normalize_config_contract_value(value: Any, spec: dict[str, Any]) -> Any:
    kind = str(spec.get("kind") or "text")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "enabled"}:
                return True
            if normalized in {"0", "false", "no", "off", "disabled"}:
                return False
        return bool(value)
    if kind == "number":
        if value in (None, ""):
            return None
        try:
            number = int(str(value).strip())
        except Exception:
            try:
                number = int(float(str(value).strip()))
            except Exception:
                return value
        return number
    if kind in {"multienum", "list"}:
        items = _normalize_config_list(value, item_kind=str(spec.get("item_kind") or "text"))
        options = spec.get("options")
        if kind == "multienum" and isinstance(options, list) and options:
            return [item for item in items if item in options]
        return items
    if kind in {"enum", "text"}:
        if value is None:
            return ""
        return str(value)
    return deepcopy(value)


def _build_config_contract(
    repo_root: Path,
    cfg: dict[str, Any],
    cfg_path: Path,
    cfg_source: str,
    prompts_dir: Path,
    *,
    save_enabled: bool = False,
    save_endpoint: str = "/api/config/save",
    save_requires_opt_in: bool = True,
) -> dict[str, Any]:
    effective_cfg = _merge_config_tree(CLI_DEFAULTS, cfg)
    effective_cfg["repo"] = repo_root.as_posix()
    default_cfg = _merge_config_tree(CLI_DEFAULTS, {})
    default_cfg["repo"] = ""

    values: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    schema: dict[str, Any] = {}
    restart_required_paths: list[str] = []
    redacted_paths: list[str] = []

    for spec in CONFIG_CONTRACT_FIELDS:
        path = str(spec["path"])
        field_schema = {
            "path": path,
            "kind": spec.get("kind", "text"),
            "label": spec.get("label", path.rsplit(".", 1)[-1].replace("_", " ").title()),
            "group": spec.get("group", ""),
            "desc": spec.get("desc", ""),
            "hint": spec.get("hint", ""),
            "restart": bool(spec.get("restart", False)),
            "editable": bool(spec.get("editable", True)),
            "redacted": bool(spec.get("redacted", False)),
            "allow_empty": bool(spec.get("allow_empty", False)),
        }
        if spec.get("options") is not None:
            field_schema["options"] = list(spec["options"])
        if spec.get("min") is not None:
            field_schema["min"] = spec["min"]
        if spec.get("max") is not None:
            field_schema["max"] = spec["max"]
        if spec.get("step") is not None:
            field_schema["step"] = spec["step"]
        if spec.get("item_kind") is not None:
            field_schema["item_kind"] = spec["item_kind"]

        raw_value = _config_path_get(effective_cfg, path)
        raw_default = _config_path_get(default_cfg, path)
        normalized_value = _normalize_config_contract_value(raw_value, spec)
        normalized_default = _normalize_config_contract_value(raw_default, spec)
        _config_path_set(values, path, normalized_value)
        _config_path_set(defaults, path, normalized_default)
        schema[path] = field_schema
        if field_schema["restart"]:
            restart_required_paths.append(path)
        if field_schema["redacted"] or _is_sensitive_config_key(path):
            redacted_paths.append(path)

    redaction = {
        "placeholder": REDACTED_VALUE,
        "paths": list(dict.fromkeys(redacted_paths)),
        "tokens": sorted(SENSITIVE_CONFIG_TOKENS),
    }
    restart_required_paths = list(dict.fromkeys(restart_required_paths))
    redacted_values = _redact_config(values)
    redacted_defaults = _redact_config(defaults)
    for path in redaction["paths"]:
        if _config_path_get(redacted_values, path) not in (None, "", False):
            _config_path_set(redacted_values, path, REDACTED_VALUE)
        if _config_path_get(redacted_defaults, path) not in (None, "", False):
            _config_path_set(redacted_defaults, path, REDACTED_VALUE)

    return {
        "path": cfg_path.as_posix(),
        "source": cfg_source,
        "resolved_prompts_dir": prompts_dir.as_posix(),
        "values": redacted_values,
        "defaults": redacted_defaults,
        "schema": schema,
        "groups": CONFIG_CONTRACT_GROUPS,
        "redaction": redaction,
        "restart_required_paths": restart_required_paths,
        "meta": {
            "path": cfg_path.as_posix(),
            "source": cfg_source,
            "resolved_prompts_dir": prompts_dir.as_posix(),
            "save_enabled": bool(save_enabled),
            "save_endpoint": str(save_endpoint or "/api/config/save"),
            "save_requires_opt_in": bool(save_requires_opt_in),
        },
    }


def _config_save_backup_path(cfg_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return cfg_path.with_name(f"{cfg_path.stem}.{stamp}.bak{cfg_path.suffix}")


def _config_save_validate_change(path: str, raw_value: Any, schema: dict[str, Any], current_value: Any) -> tuple[Any, str, dict[str, Any]]:
    kind = str(schema.get("kind") or "text")
    allow_empty = bool(schema.get("allow_empty", False))

    if path == "repo" and raw_value != current_value:
        return raw_value, "config_path_unsafe", {"path": path, "reason": "Repository root is managed by the server."}

    if bool(schema.get("redacted")) and raw_value == REDACTED_VALUE and raw_value != current_value:
        return raw_value, "config_redacted_placeholder", {"path": path, "placeholder": REDACTED_VALUE}

    if kind == "bool":
        if isinstance(raw_value, bool):
            return raw_value, "", {}
        return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "boolean"}

    if kind == "number":
        if raw_value in (None, ""):
            if allow_empty:
                return "", "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        if isinstance(raw_value, bool):
            return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "number"}
        if isinstance(raw_value, (int, float)):
            number: int | float = raw_value
        elif isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return raw_value, "config_value_required", {"path": path, "kind": kind}
            try:
                number = int(text)
            except Exception:
                try:
                    number = float(text)
                except Exception:
                    return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "number"}
        else:
            return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "number"}
        if isinstance(number, float) and number.is_integer():
            number = int(number)
        min_value = schema.get("min")
        max_value = schema.get("max")
        if min_value is not None and number < min_value:
            return number, "config_value_out_of_range", {"path": path, "kind": kind, "min": min_value}
        if max_value is not None and number > max_value:
            return number, "config_value_out_of_range", {"path": path, "kind": kind, "max": max_value}
        return number, "", {}

    if kind == "text":
        if raw_value in (None, ""):
            if allow_empty:
                return "", "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        if not isinstance(raw_value, str):
            raw_value = str(raw_value)
        if not raw_value.strip() and not allow_empty:
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        return raw_value, "", {}

    if kind == "enum":
        options = [str(option) for option in schema.get("options") or []]
        if raw_value in (None, ""):
            if allow_empty:
                return "", "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        if not isinstance(raw_value, str):
            raw_value = str(raw_value)
        if raw_value not in options:
            return raw_value, "config_value_invalid_choice", {"path": path, "kind": kind, "options": options}
        return raw_value, "", {}

    if kind == "multienum":
        options = [str(option) for option in schema.get("options") or []]
        items = raw_value if isinstance(raw_value, list) else _normalize_config_list(raw_value, item_kind="text")
        items = [str(item) for item in items if str(item).strip()]
        if not items:
            if allow_empty:
                return [], "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        invalid = [item for item in items if item not in options]
        if invalid:
            return items, "config_value_invalid_choice", {"path": path, "kind": kind, "invalid": invalid, "options": options}
        return items, "", {}

    if kind == "list":
        item_kind = str(schema.get("item_kind") or "text")
        items = raw_value if isinstance(raw_value, list) else _normalize_config_list(raw_value, item_kind=item_kind)
        if not items:
            if allow_empty:
                return [], "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        normalized_items: list[Any] = []
        if item_kind in {"int", "number"}:
            for item in items:
                if isinstance(item, bool):
                    return items, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": item_kind}
                if isinstance(item, (int, float)):
                    number = int(item) if float(item).is_integer() else item
                elif isinstance(item, str):
                    text = item.strip()
                    if not text:
                        return items, "config_value_required", {"path": path, "kind": kind}
                    try:
                        number = int(text)
                    except Exception:
                        try:
                            number = float(text)
                        except Exception:
                            return items, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": item_kind}
                else:
                    return items, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": item_kind}
                if isinstance(number, float) and number.is_integer():
                    number = int(number)
                normalized_items.append(number)
            return normalized_items, "", {}
        normalized_items = [str(item) for item in items]
        return normalized_items, "", {}

    return raw_value, "", {}


def _epoch_ms(value: Any) -> int:
    try:
        return int(float(value) * 1000)
    except Exception:
        return 0


def _iso_to_ms(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        return 0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _fmt_clock(value: Any) -> str:
    ms = _epoch_ms(value) or _iso_to_ms(value)
    if not ms:
        return ""
    dt = datetime.fromtimestamp(ms / 1000.0)
    return dt.strftime("%H:%M:%S")


def _fmt_mtime(value: float) -> str:
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "template"


def _tail_text(path: Path, lines: int = 50) -> str:
    if not path.exists() or not path.is_file():
        return ""
    dq: deque[str] = deque(maxlen=max(1, int(lines)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                dq.append(line.rstrip("\n"))
    except Exception:
        return ""
    return "\n".join(dq).strip()


def _host_is_loopback(bind_host: str) -> bool:
    host = str(bind_host or "").strip()
    if not host:
        return True
    if host.lower() == "localhost":
        return True
    candidate = host
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _resolve_trusted_network_enabled(explicit: bool | None = None) -> tuple[bool, str]:
    if explicit is not None:
        return bool(explicit), "cli:--trusted-network"

    raw = (os.getenv("AGENTCLI_WEB_TRUSTED_NETWORK") or "").strip().lower()
    if raw in RUNNER_CONTROL_TRUTHY:
        return True, "env:AGENTCLI_WEB_TRUSTED_NETWORK"
    if raw in RUNNER_CONTROL_FALSY:
        return False, "env:AGENTCLI_WEB_TRUSTED_NETWORK"
    return False, "default"


def _resolve_runner_controls_enabled(
    explicit: bool | None = None,
    *,
    bind_host: str = "127.0.0.1",
    trusted_network: bool | None = None,
) -> tuple[bool, str, str]:
    if explicit is not None:
        enabled = bool(explicit)
        source = "cli"
    else:
        raw = (os.getenv("AGENTCLI_WEB_RUNNER_CONTROLS") or "").strip().lower()
        if raw in RUNNER_CONTROL_TRUTHY:
            enabled = True
            source = "env:AGENTCLI_WEB_RUNNER_CONTROLS"
        elif raw in RUNNER_CONTROL_FALSY:
            enabled = False
            source = "env:AGENTCLI_WEB_RUNNER_CONTROLS"
        else:
            enabled = False
            source = "default"

    disabled_reason = (
        "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls."
    )
    if not enabled:
        return False, source, disabled_reason

    if _host_is_loopback(bind_host):
        return True, source, ""

    trusted_enabled, trusted_source = _resolve_trusted_network_enabled(trusted_network)
    if trusted_enabled:
        combined_source = f"{source};{trusted_source}" if source else trusted_source
        return True, combined_source, ""

    host_label = str(bind_host or "").strip() or "0.0.0.0"
    return False, source, f"Runner controls stay disabled on {host_label} until --trusted-network is set."


def _runner_control_confirmation(action: str) -> str:
    key = str(action or "").strip().lower()
    return RUNNER_CONTROL_CONFIRMATIONS.get(key, "")


def _runner_control_message(
    *,
    enabled: bool,
    source: str,
    running: bool,
    controller_available: bool,
    disabled_reason: str = "",
) -> str:
    if not controller_available:
        return "Runner controller is unavailable."
    if not enabled:
        return disabled_reason or "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls."
    if running:
        return f"Runner controls enabled via {source}. Controller reports the runner is running."
    return f"Runner controls enabled via {source}. Controller reports the runner is stopped."


def _controller_status_payload(controller: RunnerController | None) -> dict[str, Any]:
    if controller is None:
        return {}
    try:
        payload = controller.status()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _runner_control_status_payload(
    controller: RunnerController | None,
    *,
    repo: Path,
    config_path: str = "",
    current_run_dir: str = "",
    cfg: dict[str, Any] | None = None,
    cfg_path: Path | str | None = None,
    redact_sensitive: bool = False,
) -> dict[str, Any]:
    base_args = getattr(controller, "base_args", None)
    fallback_config_path = _path_text(config_path or getattr(base_args, "config_path", getattr(base_args, "config", "")) or "")
    start_options_contract = _runner_control_start_options_contract(
        controller,
        repo=repo,
        cfg=cfg,
        cfg_path=cfg_path,
        redact_paths=redact_sensitive,
    )
    if controller is None:
        config_path_value = fallback_config_path
        if redact_sensitive and config_path_value:
            config_path_value = REDACTED_VALUE
        return {
            "running": False,
            "runner_mode": "unknown",
            "repo": _path_text(repo),
            "config_path": config_path_value,
            "run_dir": _path_text(current_run_dir),
            "uptime_seconds": 0,
            "exit_code": None,
            "stop_file": "STOP",
            "stop_file_exists": False,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "state_counts": {"done": 0, "failed": 0, "warnings": 0},
            "reason": "",
            "last_event": "",
            "stop_progress": {},
            "start_options": start_options_contract,
            "startOptions": start_options_contract,
        }

    try:
        try:
            status = controller.status(redact_paths=redact_sensitive)
        except TypeError:
            status = controller.status()
    except Exception as ex:
        config_path_value = fallback_config_path
        if redact_sensitive and config_path_value:
            config_path_value = REDACTED_VALUE
        return {
            "running": False,
            "runner_mode": "unknown",
            "repo": _path_text(repo),
            "config_path": config_path_value,
            "run_dir": _path_text(current_run_dir),
            "uptime_seconds": 0,
            "exit_code": None,
            "stop_file": "STOP",
            "stop_file_exists": False,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "state_counts": {"done": 0, "failed": 0, "warnings": 0},
            "reason": REDACTED_VALUE if redact_sensitive else f"status_error: {ex}",
            "last_event": "",
            "stop_progress": {},
            "start_options": start_options_contract,
            "startOptions": start_options_contract,
        }

    if not isinstance(status, dict):
        status = {}
    if redact_sensitive and isinstance(status, dict):
        status = _redact_web_runner_status_payload(status, redact_start_options=True)

    stop_progress = status.get("stop_progress")
    if not isinstance(stop_progress, dict):
        stop_progress = {}
    else:
        stop_progress = normalize_stop_progress_payload(stop_progress)
    state_counts = status.get("state_counts")
    if not isinstance(state_counts, dict):
        state_counts = {}
    start_options = status.get("start_options")
    if not isinstance(start_options, dict) or not start_options:
        start_options = start_options_contract

    return {
        "running": bool(status.get("running")),
        "runner_mode": str(status.get("runner_mode") or "thread").strip() or "thread",
        "repo": _path_text(status.get("repo") or repo),
        "config_path": REDACTED_VALUE if redact_sensitive and _path_text(status.get("config_path") or status.get("configPath") or fallback_config_path) else _path_text(status.get("config_path") or status.get("configPath") or fallback_config_path),
        "run_dir": _path_text(status.get("run_dir") or current_run_dir or ""),
        "uptime_seconds": int(status.get("uptime_seconds") or 0),
        "exit_code": status.get("exit_code"),
        "stop_file": str(status.get("stop_file") or "STOP"),
        "stop_file_exists": bool(status.get("stop_file_exists")),
        "done": int(status.get("done") or 0),
        "failed": int(status.get("failed") or 0),
        "warnings": int(status.get("warnings") or 0),
        "state_counts": {
            "done": int(state_counts.get("done") or status.get("done") or 0),
            "failed": int(state_counts.get("failed") or status.get("failed") or 0),
            "warnings": int(state_counts.get("warnings") or status.get("warnings") or 0),
        },
        "reason": str(status.get("reason") or "").strip(),
        "last_event": str(status.get("last_event") or "").strip(),
        "stop_progress": stop_progress,
        "start_options": start_options,
        "startOptions": start_options,
    }


def _runner_control_actions(
    enabled: bool,
    status_payload: dict[str, Any],
    *,
    controller_available: bool,
    busy: bool = False,
) -> dict[str, dict[str, Any]]:
    running = bool(status_payload.get("running"))
    status_reason = str(status_payload.get("reason") or "").strip()
    status_error = status_reason.startswith("status_error:")
    stop_progress = normalize_stop_progress_payload(status_payload.get("stop_progress"))
    stop_timeout_retryable = bool(
        str(stop_progress.get("phase") or "").strip().lower() == "timeout"
        and stop_progress.get("timeout_guidance", {}).get("can_retry", True) is not False
    )
    disabled_reason = "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls."
    controller_reason = "Runner controller is unavailable."
    busy_reason = "A runner control request is already in flight."
    controller_status_reason = status_reason or "Runner controller status is unavailable."

    def _action(enabled_flag: bool, reason: str) -> dict[str, Any]:
        return {
            "enabled": bool(enabled_flag and not busy),
            "disabled_reason": reason,
            "busy": bool(busy),
        }

    return {
        "start": _action(
            enabled and controller_available and not running and not status_error,
            busy_reason if busy else (
                disabled_reason
                if not enabled
                else (
                    controller_reason
                    if not controller_available
                    else (
                        controller_status_reason
                        if status_error
                        else ("Runner is already running." if running else "")
                    )
                )
            ),
        ),
        "stop": _action(
            enabled and controller_available and (running or stop_timeout_retryable) and not status_error,
            busy_reason if busy else (
                disabled_reason
                if not enabled
                else (
                    controller_reason
                    if not controller_available
                    else (
                        controller_status_reason
                        if status_error
                        else ("" if (running or stop_timeout_retryable) else "Runner is not running.")
                    )
                )
            ),
        ),
        "reload": _action(
            enabled and controller_available and not status_error,
            busy_reason if busy else (
                disabled_reason
                if not enabled
                else (
                    controller_reason
                    if not controller_available
                    else controller_status_reason
                    if status_error
                    else ""
                )
            ),
        ),
        "restart": _action(
            enabled and controller_available and not status_error,
            busy_reason if busy else (
                disabled_reason
                if not enabled
                else (
                    controller_reason
                    if not controller_available
                    else controller_status_reason
                    if status_error
                    else ""
                )
            ),
        ),
    }


def _runner_control_payload(
    controller: RunnerController | None,
    *,
    repo: Path,
    enabled: bool,
    source: str,
    disabled_reason: str = "",
    config_path: str = "",
    current_run_dir: str = "",
    last_action: str = "",
    last_message: str = "",
    last_error: str = "",
    run_status: str = "",
    execution_status: str = "",
    project_complete: bool = False,
    project_status: str = "",
    goals_complete: bool = False,
    backlog_complete: bool = False,
    busy: bool = False,
    cfg: dict[str, Any] | None = None,
    cfg_path: Path | str | None = None,
    redact_sensitive: bool = False,
) -> dict[str, Any]:
    status_payload = _runner_control_status_payload(
        controller,
        repo=repo,
        config_path=config_path,
        current_run_dir=current_run_dir,
        cfg=cfg,
        cfg_path=cfg_path,
        redact_sensitive=redact_sensitive,
    )
    controller_available = controller is not None
    actions = _runner_control_actions(enabled, status_payload, controller_available=controller_available, busy=busy)
    message = _runner_control_message(
        enabled=enabled,
        source=source,
        running=bool(status_payload.get("running")),
        controller_available=controller_available,
        disabled_reason=disabled_reason,
    )
    status_reason = str(status_payload.get("reason") or "").strip()
    message_sensitive = False
    if last_error:
        message = last_error
        message_sensitive = True
    elif status_reason.startswith("status_error:"):
        message = status_reason
        message_sensitive = True
    elif busy:
        message = _runner_control_message(
            enabled=enabled,
            source=source,
            running=bool(status_payload.get("running")),
            controller_available=controller_available,
            disabled_reason=disabled_reason,
        )
    elif last_message and enabled:
        message = last_message
    elif not enabled and disabled_reason:
        message = disabled_reason
    if redact_sensitive and message_sensitive:
        message = REDACTED_VALUE
    start_options = status_payload.get("start_options")
    if not isinstance(start_options, dict) or not start_options:
        start_options = status_payload.get("startOptions")
    if not isinstance(start_options, dict) or not start_options:
        start_options = _runner_control_start_options_contract(
            controller,
            repo=repo,
            cfg=cfg,
            cfg_path=cfg_path,
            redact_paths=redact_sensitive,
        )
    return {
        "enabled": bool(enabled),
        "source": source,
        "controller_available": controller_available,
        "message": message,
        "status": status_payload,
        "run_status": str(run_status or "").strip(),
        "execution_status": str(execution_status or "").strip(),
        "project_complete": bool(project_complete),
        "project_status": str(project_status or ("complete" if project_complete else "incomplete")).strip() or ("complete" if project_complete else "incomplete"),
        "goals_complete": bool(goals_complete),
        "backlog_complete": bool(backlog_complete),
        "start_options": start_options,
        "startOptions": start_options,
        "actions": actions,
        "confirmation": dict(RUNNER_CONTROL_CONFIRMATIONS),
        "last_action": last_action,
        "last_message": last_message,
        "last_error": last_error,
        "busy": bool(busy),
    }


def _runner_control_confirmation_value(payload: dict[str, Any] | None) -> str:
    data = payload if isinstance(payload, dict) else {}
    for key in ("confirmation", "confirm", "token", "phrase"):
        raw = data.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def _runner_control_request_options(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    options: dict[str, Any] = {}
    for key in ("start_options", "startOptions", "runner_options", "runnerOptions", "options"):
        nested = data.get(key)
        if isinstance(nested, dict):
            options.update(nested)
    for key in (
        "autopilot",
        "continuous",
        "loop",
        "one_shot",
        "one-shot",
        "oneShot",
        "run_mode",
        "runMode",
        "mode",
        "loop_max_cycles",
        "loopMaxCycles",
        "max_cycles",
        "maxCycles",
        "profile",
        "execution_backend",
        "executionBackend",
        "backend",
        "config_path",
        "configPath",
        "config",
        "run_dir",
        "runDir",
        "resume_latest",
        "resumeLatest",
        "resume-latest",
    ):
        if key in data:
            options[key] = data[key]
    return options


def _strip_run_dir_intent(cfg: dict[str, Any] | None) -> dict[str, Any]:
    overrides = deepcopy(cfg) if isinstance(cfg, dict) else {}
    overrides.pop("run_dir", None)
    overrides.pop("resume_latest", None)
    return overrides


def _runner_control_start_options_contract(
    controller: RunnerController | None,
    *,
    repo: Path,
    cfg: dict[str, Any] | None = None,
    cfg_path: Path | str | None = None,
    redact_paths: bool = False,
) -> dict[str, Any]:
    if controller is not None:
        contract_fn = getattr(controller, "start_options_contract", None)
        if callable(contract_fn):
            try:
                contract = contract_fn(redact_paths=redact_paths)
            except TypeError:
                try:
                    contract = contract_fn()
                except Exception:
                    contract = None
            except Exception:
                contract = None
            if isinstance(contract, dict) and contract:
                return contract
    base_args = getattr(controller, "base_args", None) if controller is not None else None
    if base_args is None:
        resolved_cfg_path = Path(str(cfg_path).strip()) if cfg_path is not None and str(cfg_path).strip() else default_config_path(repo)
        base_args = _build_runner_base_args(repo, cfg or {}, resolved_cfg_path)
    try:
        return build_runner_start_options_contract(repo, base_args, redact_paths=redact_paths)
    except Exception:
        return build_runner_start_options_contract(repo, argparse.Namespace(), redact_paths=redact_paths)


def _build_runner_base_args(repo: Path, cfg: dict[str, Any], cfg_path: Path) -> argparse.Namespace:
    payload = _strip_run_dir_intent(cfg)
    payload["repo"] = _path_text(repo)
    payload["config"] = _path_text(cfg_path)
    payload["config_path"] = _path_text(cfg_path)
    return argparse.Namespace(**payload)


def _runner_mode_from_config(cfg: dict[str, Any]) -> str:
    telegram_cfg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    raw = cfg.get("runner_mode") or telegram_cfg.get("runner_mode") or "thread"
    mode = str(raw or "thread").strip().lower()
    return mode if mode in {"thread", "subprocess"} else "thread"


def _build_runner_controller(repo: Path, cfg: dict[str, Any], cfg_path: Path) -> RunnerController | None:
    if RunnerController is None:
        return None
    try:
        return RunnerController(
            repo=repo,
            base_args=_build_runner_base_args(repo, cfg, cfg_path),
            runner_mode=_runner_mode_from_config(cfg),
        )
    except Exception:
        return None


def _runner_control_response(
    *,
    action: str,
    ok: bool,
    message: str,
    snapshot: dict[str, Any],
    status_code: int = 200,
    error_code: str = "",
    details: dict[str, Any] | None = None,
) -> Any:
    if JSONResponse is None:
        raise RuntimeError("JSONResponse is unavailable.")
    payload: dict[str, Any] = {
        "ok": bool(ok),
        "action": action,
        "message": message,
        "runner_control": snapshot.get("runner_control", {}),
        "snapshot": snapshot,
    }
    if not ok:
        payload["error"] = {
            "code": error_code or "runner_control_error",
            "message": message,
            "details": details or {},
        }
    return JSONResponse(payload, status_code=status_code)


def _wait_for_runner_idle(controller: RunnerController, timeout_sec: float = 10.0) -> bool:
    deadline = time.time() + max(0.5, float(timeout_sec))
    while time.time() < deadline:
        try:
            status = controller.status()
        except Exception:
            status = {}
        if not bool(status.get("running")):
            return True
        time.sleep(0.25)
    try:
        return not bool(controller.status().get("running"))
    except Exception:
        return False


def _text_excerpt(text: str, *, max_lines: int = 8, max_chars: int = 280) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    lines = clean.splitlines()
    excerpt = "\n".join(lines[:max_lines]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 3].rstrip() + "..."
    return excerpt


def _branch_name(repo: Path) -> str:
    rc, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout_sec=10)
    if rc != 0:
        return ""
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        return ""
    branch = lines[-1]
    return branch if branch != "HEAD" else ""


def _git_head_short(repo: Path) -> str:
    try:
        head = git_head(repo).strip()
        return head[:8] if head else ""
    except Exception:
        return ""


def _load_config_payload(repo: Path, explicit: str | None = None) -> tuple[Path, dict[str, Any], str]:
    cfg_path = resolve_config_path(repo, explicit)
    source = "explicit" if explicit and str(explicit).strip() else "default"
    if not cfg_path.exists():
        legacy = legacy_default_config_path(repo)
        if legacy is not None:
            cfg_path = legacy
            source = "legacy"
    payload: dict[str, Any] = {}
    try:
        payload = load_config(cfg_path)
    except Exception:
        payload = {}
    return cfg_path, payload, source


def _load_tasks(run_dir: Path | None) -> list[TaskItem]:
    if run_dir is None:
        return []
    backlog_json = run_dir / "BACKLOG.json"
    backlog_md = run_dir / "BACKLOG.md"
    tasks: list[TaskItem] = []
    if backlog_json.exists():
        try:
            tasks = load_backlog_json(backlog_json)
        except Exception:
            tasks = []
    if not tasks and backlog_md.exists():
        try:
            tasks = parse_backlog_md(backlog_md)
        except Exception:
            tasks = []
    return tasks


def _task_priority(task: TaskItem, index: int) -> str:
    text = f"{task.title} {task.prompt}".lower()
    if any(token in text for token in ("test", "qa", "verify", "regression")):
        return "P1"
    if any(token in text for token in ("bug", "fix", "crash", "block", "security")):
        return "P0"
    return "P0" if index < 3 else "P1"


def _task_estimate(task: TaskItem) -> str:
    score = len(task.files) + max(1, len(task.prompt.split()) // 45)
    if score <= 1:
        return "S"
    if score <= 3:
        return "M"
    return "L"


def _task_tags(task: TaskItem) -> list[str]:
    tags: list[str] = []
    for skill in task.skills:
        if skill and skill not in tags:
            tags.append(skill)
    for path in task.files:
        clean = str(path).replace("\\", "/").strip()
        if not clean:
            continue
        head = clean.split("/", 1)[0]
        if head and head not in tags and head not in {".", ".."}:
            tags.append(head)
    if not tags:
        text = f"{task.title} {task.prompt}".lower()
        if "test" in text and "test" not in tags:
            tags.append("test")
        if "ui" in text and "ui" not in tags:
            tags.append("ui")
    return tags[:4]


def _parse_goal_items_and_warnings(goals_text: str | None) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {"p0": [], "p1": []}
    warnings: list[dict[str, Any]] = []
    if not goals_text or not goals_text.strip():
        return items, warnings

    checkbox_re = re.compile(r"^\s*-\s*\[(x| )\]\s*(.*)$", re.IGNORECASE)
    list_re = re.compile(r"^\s*[-*+]\s+")
    current_bucket: str | None = None
    ignore_outside_list_items = False
    last_item: dict[str, Any] | None = None

    def _parse_goal_note_comment(comment_text: str) -> str:
        payload = comment_text.strip()
        if not payload:
            return ""
        try:
            parsed = json.loads(payload)
        except Exception:
            return payload.strip().strip('"').strip("'")
        if parsed is None:
            return ""
        if isinstance(parsed, str):
            return parsed
        return str(parsed)

    for line_number, line in enumerate(goals_text.splitlines(), start=1):
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped:
            continue
        heading = re.match(r"^(#+)\s+(.+)$", stripped)
        if heading:
            last_item = None
            level = len(heading.group(1))
            title = heading.group(2).strip().lower()
            if level == 2 and re.match(r"p0\b", title):
                current_bucket = "p0"
                ignore_outside_list_items = False
                continue
            if level == 2 and re.match(r"p1\b", title):
                current_bucket = "p1"
                ignore_outside_list_items = False
                continue
            if level == 2 and re.match(r"p[\s_-]*[01]\b", title):
                warnings.append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "reason": "malformed_priority_section_heading",
                        "message": "Priority section headings must use ## P0 or ## P1.",
                    }
                )
            if level == 2:
                current_bucket = None
                ignore_outside_list_items = title.startswith("completion criteria")
                last_item = None
                continue
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            comment_body = stripped[4:-3].strip()
            if current_bucket in ("p0", "p1") and last_item is not None and comment_body.lower().startswith("goal-note:"):
                note_text = _parse_goal_note_comment(comment_body[len("goal-note:"):].strip())
                if note_text:
                    existing_note = str(last_item.get("note") or "")
                    last_item["note"] = f"{existing_note}\n{note_text}".strip() if existing_note else note_text
                continue
            continue

        match = checkbox_re.match(line)
        if match:
            done = match.group(1).strip().lower() == "x"
            if current_bucket in ("p0", "p1"):
                item_text = match.group(2).strip()
                item = {
                    "done": done,
                    "checked": done,
                    "checkbox": "[x]" if done else "[ ]",
                    "text": item_text,
                    "note": "",
                    "line_number": line_number,
                    "line": line_number,
                }
                items[current_bucket].append(item)
                last_item = item
            else:
                warnings.append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "reason": "checkbox_outside_goal_section",
                        "message": "Checkbox item outside P0/P1 was ignored.",
                    }
                )
            continue

        if current_bucket in ("p0", "p1"):
            last_item = None
            warnings.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "unsupported_goal_line",
                    "message": "Non-checkbox content inside a GOALS section was ignored.",
                }
            )
            continue

        if list_re.match(line) and not ignore_outside_list_items:
            warnings.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "unsupported_list_item",
                    "message": "List item outside P0/P1 was ignored.",
                }
            )
            last_item = None

    return items, warnings


def _build_goals_payload(repo: Path, *, completion_level: str = "all") -> dict[str, Any]:
    completion_level = resolve_goals_completion_level(completion_level)
    goal_path = goals_path(repo)
    exists = False
    mtime = None
    size = None
    try:
        exists = goal_path.exists() and goal_path.is_file()
    except OSError:
        exists = False
    if exists:
        try:
            stat = goal_path.stat()
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError:
            mtime = None
            size = None

    _path, raw_text = read_goals(repo)
    raw_text = raw_text or ""
    items, warnings = _parse_goal_items_and_warnings(raw_text)
    completion = parse_goals_completion(raw_text, completion_level=completion_level)
    p0_total = len(items["p0"])
    p1_total = len(items["p1"])
    p0_done = len([item for item in items["p0"] if item.get("done")])
    p1_done = len([item for item in items["p1"] if item.get("done")])
    total = p0_total + p1_total
    done = p0_done + p1_done
    missing_sections = list(completion.get("missing_sections") or [])
    summary = {
        "has_goals": bool(completion.get("has_goals")),
        "project_complete": bool(completion.get("project_complete")),
        "valid": bool(completion.get("valid", False)),
        "missing_sections": missing_sections,
        "p0_total": p0_total,
        "p0_done": p0_done,
        "p1_total": p1_total,
        "p1_done": p1_done,
        "all_total": int(completion.get("all_total") or total),
        "all_done": int(completion.get("all_done") or done),
        "total": total,
        "done": done,
        "unchecked": max(0, total - done),
        "warnings": len(warnings),
    }
    return {
        "path": goal_path.as_posix(),
        "exists": bool(exists),
        "mtime": mtime,
        "size": size,
        "raw_text": raw_text,
        "items": items,
        "completion": completion,
        "summary": summary,
        "warnings": warnings,
        "completion_level": completion_level,
    }


def _goal_items(goals_text: str | None) -> dict[str, list[dict[str, Any]]]:
    return _parse_goal_items_and_warnings(goals_text)[0]


def _goal_save_backup_path(goal_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return goal_path.with_name(f"{goal_path.stem}.{stamp}.bak{goal_path.suffix}")


def _goal_save_item_identity(item: dict[str, Any], *, use_line_number: bool = True) -> str:
    line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
    if use_line_number and line_number > 0:
        return f"line:{line_number}"
    return f"sig:{_goal_save_item_signature(item)}"


def _goal_save_item_signature(item: dict[str, Any]) -> str:
    return json.dumps(
        {
            "done": bool(item.get("done")),
            "checked": bool(item.get("checked", item.get("done"))),
            "checkbox": "[x]" if bool(item.get("done") or item.get("checked")) else "[ ]",
            "text": str(item.get("text") or "").strip(),
            "note": str(item.get("note") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _goal_save_normalize_item(raw_item: Any) -> dict[str, Any]:
    item = raw_item if isinstance(raw_item, dict) else {}
    done = bool(item.get("done") if "done" in item else item.get("checked"))
    checked = bool(item.get("checked") if "checked" in item else done)
    line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
    return {
        "done": done,
        "checked": checked,
        "checkbox": "[x]" if done else "[ ]",
        "text": str(item.get("text") or "").strip(),
        "note": str(item.get("note") or ""),
        "line_number": line_number,
        "lineNumber": line_number,
        "line": line_number,
    }


def _goal_save_normalize_draft(raw_draft: Any) -> dict[str, list[dict[str, Any]]]:
    raw = raw_draft if isinstance(raw_draft, dict) else {}
    items = raw.get("items") if isinstance(raw.get("items"), dict) else raw
    normalized: dict[str, list[dict[str, Any]]] = {"p0": [], "p1": []}
    for bucket in ("p0", "p1"):
        raw_bucket = items.get(bucket) if isinstance(items, dict) else []
        if not isinstance(raw_bucket, list):
            raw_bucket = []
        normalized[bucket] = [_goal_save_normalize_item(item) for item in raw_bucket]
    return normalized


def _goal_save_note_comment_line(note_line: str) -> str:
    return f"<!-- goal-note: {json.dumps(note_line, ensure_ascii=False)} -->"


def _goal_save_item_lines(item: dict[str, Any]) -> list[str]:
    text = str(item.get("text") or "").strip()
    if not text:
        raise ValueError("Goal text cannot be empty.")
    done = bool(item.get("done") or item.get("checked"))
    lines = [f"- [{'x' if done else ' '}] {text}"]
    note = str(item.get("note") or "")
    if note:
        for note_line in note.splitlines():
            lines.append(_goal_save_note_comment_line(note_line))
    return lines


def _goal_save_section_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = [""]
    for item in items:
        lines.extend(_goal_save_item_lines(item))
        lines.append("")
    return lines


def _goal_save_serialize_draft(draft: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = ["# Project Goals", ""]
    lines.append("## P0")
    lines.extend(_goal_save_section_lines(list(draft.get("p0") or [])))
    lines.append("## P1")
    lines.extend(_goal_save_section_lines(list(draft.get("p1") or [])))
    return "\n".join(lines).rstrip() + "\n"


def _goal_save_has_required_sections(raw_text: str) -> bool:
    return bool(
        re.search(r"^##\s+p0\b", raw_text or "", re.IGNORECASE | re.MULTILINE)
        and re.search(r"^##\s+p1\b", raw_text or "", re.IGNORECASE | re.MULTILINE)
    )


def _goal_save_risk_report(
    current_items: dict[str, list[dict[str, Any]]],
    next_items: dict[str, list[dict[str, Any]]],
    *,
    use_line_numbers: bool,
) -> dict[str, Any]:
    next_line_index: dict[str, dict[str, int]] = {"p0": {}, "p1": {}}
    next_signature_index: dict[str, dict[str, int]] = {"p0": {}, "p1": {}}
    for bucket in ("p0", "p1"):
        for item in next_items.get(bucket, []):
            signature = _goal_save_item_signature(item)
            next_signature_index[bucket][signature] = next_signature_index[bucket].get(signature, 0) + 1
            if use_line_numbers:
                line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
                if line_number > 0:
                    line_identity = f"line:{line_number}"
                    next_line_index[bucket][line_identity] = next_line_index[bucket].get(line_identity, 0) + 1

    deleted: list[dict[str, Any]] = []
    downgraded: list[dict[str, Any]] = []

    def _consume(index: dict[str, dict[str, int]], bucket: str, identity: str) -> bool:
        if not identity:
            return False
        current_count = index[bucket].get(identity, 0)
        if current_count <= 0:
            return False
        index[bucket][identity] = current_count - 1
        return True

    for item in current_items.get("p0", []):
        if bool(item.get("done") or item.get("checked")):
            continue
        signature = _goal_save_item_signature(item)
        line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
        line_identity = f"line:{line_number}" if use_line_numbers and line_number > 0 else ""
        if _consume(next_line_index, "p0", line_identity) or _consume(next_signature_index, "p0", signature):
            continue
        if _consume(next_line_index, "p1", line_identity) or _consume(next_signature_index, "p1", signature):
            downgraded.append(item)
        else:
            deleted.append(item)

    return {
        "requires_confirmation": bool(deleted or downgraded),
        "requiresConfirmation": bool(deleted or downgraded),
        "confirmation_phrase": GOALS_SAVE_CONFIRMATION_PHRASE,
        "confirmationPhrase": GOALS_SAVE_CONFIRMATION_PHRASE,
        "deleted_unchecked_p0": deleted,
        "deletedUncheckedP0": deleted,
        "downgraded_unchecked_p0": downgraded,
        "downgradedUncheckedP0": downgraded,
        "risk_count": len(deleted) + len(downgraded),
        "riskCount": len(deleted) + len(downgraded),
    }


async def _goal_save_body(request: Request) -> dict[str, Any] | None:
    try:
        payload = await request.json()
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _goal_save_error(status_code: int, code: str, message: str, **details: Any) -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": False,
        "action": "goals-save",
        "status": "error",
        "message": message,
        "error": {
            "code": code,
            "message": message,
        },
    }
    if details:
        payload["error"]["details"] = details
        if "risk" in details:
            payload["risk"] = details["risk"]
        if "backup_path" in details:
            payload["backup_path"] = details["backup_path"]
        if "confirmation_phrase" in details:
            payload["confirmation_phrase"] = details["confirmation_phrase"]
    return JSONResponse(status_code=status_code, content=payload)


def _prompt_preview(text: str) -> str:
    preview = _text_excerpt(text, max_lines=6, max_chars=420)
    return preview or "(empty)"


def _prompt_summary(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return "(empty)"
    for line in lines:
        if not line.startswith("#"):
            return line[:180]
    return lines[0][:180]


def _prompt_variables(text: str) -> list[str]:
    seen: set[str] = set()
    variables: list[str] = []
    for match in re.finditer(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_.-]*)\}(?!\})", text or ""):
        name = match.group(1)
        if name in seen:
            continue
        seen.add(name)
        variables.append(name)
    return variables


def _prompt_profile(cfg: dict[str, Any]) -> str:
    return _pick_text(cfg.get("profile"), CLI_DEFAULTS.get("profile"), "personal") or "personal"


def _prompt_spec_map() -> dict[str, dict[str, str]]:
    return {str(spec["id"]): spec for spec in PROMPT_SPECS}


def _prompt_template_dir(repo_root: Path) -> Path:
    return (repo_root / "templates" / "agent_prompts").resolve()


def _prompt_template_resolved_path(repo_root: Path, spec: dict[str, str]) -> Path | None:
    template_dir = _prompt_template_dir(repo_root)
    candidate = (template_dir / str(spec.get("file") or "")).expanduser()
    resolved = candidate.resolve()
    try:
        resolved.relative_to(template_dir)
    except Exception:
        return None
    return resolved


def _prompt_default_text(repo_root: Path, spec: dict[str, str]) -> str:
    template_path = _prompt_template_resolved_path(repo_root, spec)
    if template_path is None:
        return spec["default"]
    if template_path.exists() and template_path.is_file():
        try:
            return _read_text_robust(template_path)
        except Exception:
            pass
    return spec["default"]


def _read_prompt_text(prompt_path: Path, default_text: str) -> tuple[str, bool]:
    if prompt_path.exists() and prompt_path.is_file():
        try:
            return _read_text_robust(prompt_path), True
        except Exception:
            return "", True
    return default_text, False


def _prompt_resolved_path(prompts_dir: Path, file_name: str) -> Path:
    return (prompts_dir / file_name).resolve()


def _prompt_file_name_is_bare(file_name: str) -> bool:
    candidate = str(file_name or "").strip().replace("\\", "/")
    if not candidate:
        return False
    if candidate in {".", ".."}:
        return False
    if "/" in candidate or ":" in candidate:
        return False
    return Path(candidate).name == candidate


def _prompt_backup_path(prompt_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return prompt_path.with_name(f"{prompt_path.stem}.{stamp}.bak{prompt_path.suffix}")


def _prompt_backup_candidates(prompt_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    pattern = f"{prompt_path.stem}.*.bak{prompt_path.suffix}"
    try:
        parent = prompt_path.parent
        if parent.exists() and parent.is_dir():
            for candidate in sorted(
                [path for path in parent.glob(pattern) if path.is_file()],
                key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
                reverse=True,
            )[: max(0, int(limit)) or 0]:
                try:
                    stats = candidate.stat()
                except Exception:
                    continue
                candidates.append(
                    {
                        "path": candidate.as_posix(),
                        "name": candidate.name,
                        "updated": _fmt_mtime(stats.st_mtime),
                        "size": stats.st_size,
                        "summary": f"{_fmt_mtime(stats.st_mtime)} | {stats.st_size} bytes",
                    }
                )
    except Exception:
        return []
    return candidates


def _prompt_validation_payload(
    *,
    file_name: str,
    expected_file: str,
    content: str,
    required_variables: list[str],
) -> dict[str, Any]:
    draft_file = str(file_name or "").strip()
    required = [str(name) for name in required_variables if str(name).strip()]
    draft_variables = _prompt_variables(content)
    missing_variables = [name for name in required if name not in draft_variables]
    file_error = ""
    file_error_code = ""
    if not draft_file:
        file_error = "Filename cannot be empty."
        file_error_code = "prompt_file_required"
    elif not _prompt_file_name_is_bare(draft_file):
        file_error = "Filename must be a bare filename within the resolved prompts directory."
        file_error_code = "prompt_file_invalid"
    elif expected_file and draft_file != expected_file:
        file_error = f"Filename must stay {expected_file}."
        file_error_code = "prompt_file_mismatch"
    content_error = "" if str(content or "").strip() else "Prompt content cannot be empty."
    content_error_code = "prompt_content_required" if content_error else ""
    template_error = ""
    if missing_variables:
        template_error = f"Missing template variables: {', '.join(f'{{{name}}}' for name in missing_variables)}"
    template_error_code = "prompt_template_variables_missing" if template_error else ""
    errors: list[dict[str, str]] = []
    if file_error:
        errors.append(
            {
                "field": "file",
                "code": file_error_code or "prompt_file_required",
                "message": file_error,
            }
        )
    if content_error:
        errors.append(
            {
                "field": "content",
                "code": "prompt_content_required",
                "message": content_error,
            }
        )
    if template_error:
        errors.append(
            {
                "field": "content",
                "code": "prompt_template_variables_missing",
                "message": template_error,
            }
        )
    return {
        "ok": not errors,
        "file_error": file_error,
        "file_error_code": file_error_code,
        "content_error": content_error,
        "content_error_code": content_error_code,
        "template_error": template_error,
        "template_error_code": template_error_code,
        "required_variables": required,
        "draft_variables": draft_variables,
        "missing_variables": missing_variables,
        "errors": errors,
    }


def _prompt_inventory_item(
    spec: dict[str, str],
    prompts_dir: Path,
    repo_root: Path,
    *,
    profile: str,
) -> dict[str, Any]:
    prompt_path = _prompt_resolved_path(prompts_dir, spec["file"])
    default_text = _prompt_default_text(repo_root, spec)
    content, exists = _read_prompt_text(prompt_path, default_text)
    mode = "override" if exists else "template"
    source = prompts_dir.as_posix() if exists else "templates/agent_prompts"
    updated = _fmt_mtime(prompt_path.stat().st_mtime) if exists else "template"
    variables = _prompt_variables(content)
    required_variables = _prompt_variables(default_text)
    content_length = len(content or "")
    return {
        "id": spec["id"],
        "file": spec["file"],
        "path": prompt_path.as_posix(),
        "scope": spec["scope"],
        "profile": profile,
        "source": source,
        "mode": mode,
        "updated": updated,
        "summary": f"{profile} profile | {mode.title()} prompt available ({content_length} characters).",
        "preview": REDACTED_VALUE,
        "content_length": content_length,
        "template_variables": variables,
        "required_template_variables": required_variables,
    }


def _prompt_read_payload(
    spec: dict[str, str],
    prompts_dir: Path,
    repo_root: Path,
    *,
    profile: str,
) -> dict[str, Any]:
    prompt_path = _prompt_resolved_path(prompts_dir, spec["file"])
    default_text = _prompt_default_text(repo_root, spec)
    content, exists = _read_prompt_text(prompt_path, default_text)
    mode = "override" if exists else "template"
    source = prompts_dir.as_posix() if exists else "templates/agent_prompts"
    updated = _fmt_mtime(prompt_path.stat().st_mtime) if exists else "template"
    variables = _prompt_variables(content)
    required_variables = _prompt_variables(default_text)
    validation = _prompt_validation_payload(
        file_name=spec["file"],
        expected_file=spec["file"],
        content=content,
        required_variables=required_variables,
    )
    return {
        "ok": True,
        "id": spec["id"],
        "file": spec["file"],
        "path": prompt_path.as_posix(),
        "scope": spec["scope"],
        "profile": profile,
        "source": source,
        "mode": mode,
        "updated": updated,
        "exists": exists,
        "content": content,
        "content_length": len(content or ""),
        "preview": _prompt_preview(content),
        "summary": _prompt_summary(content),
        "template_variables": variables,
        "required_template_variables": required_variables,
        "validation": validation,
        "backups": _prompt_backup_candidates(prompt_path),
    }


def _load_prompt_items(repo: Path, prompts_dir: Path, *, profile: str) -> list[dict[str, Any]]:
    _ = repo
    items: list[dict[str, Any]] = []
    for spec in PROMPT_SPECS:
        items.append(_prompt_inventory_item(spec, prompts_dir, repo, profile=profile))
    return items


def _load_backlog_payload(
    run_dir: Path | None,
    state: dict[str, Any],
    *,
    current_task_id: str = "",
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tasks = _load_tasks(run_dir)
    done_ids = set(str(item) for item in (state.get("done") or []) if str(item).strip())
    failed_items = state.get("failed") if isinstance(state.get("failed"), list) else []
    failed_lookup: dict[str, dict[str, Any]] = {}
    for item in failed_items:
        if not isinstance(item, dict):
            continue
        task_id = _pick_text(item.get("task"), item.get("task_id"))
        if not task_id:
            continue
        failed_lookup[task_id] = {
            "reason": _pick_text(item.get("reason"), item.get("status")),
            "detail": _pick_text(item.get("detail"), item.get("message")),
            "attempt": _coerce_optional_int(item.get("attempt")),
            "cycle": _coerce_optional_int(item.get("cycle")),
            "step": _coerce_optional_int(item.get("step")),
            "rc": _coerce_optional_int(item.get("rc")),
        }

    runtime_index = _task_runtime_index(events or [])

    backlog: list[dict[str, Any]] = []
    selected_id = ""
    selected_started_at = -1
    for index, task in enumerate(tasks):
        status = "pending"
        if task.id in done_ids:
            status = "done"
        elif task.id in failed_lookup:
            status = "failed"

        runtime = runtime_index.get(task.id, {})
        started_at = _coerce_optional_int(runtime.get("startedAt"))
        ended_at = _coerce_optional_int(runtime.get("endedAt"))
        runtime_attempt = _coerce_optional_int(runtime.get("attempt"))
        runtime_cycle = _coerce_optional_int(runtime.get("cycle"))
        runtime_step = _coerce_optional_int(runtime.get("step"))
        runtime_reason = _pick_text(runtime.get("reason"))
        runtime_message = _pick_text(runtime.get("lastMessage"), runtime.get("lastEvent"))
        runtime_status = _normalize_lifecycle_status(
            runtime.get("status"),
            rc=runtime.get("rc"),
            reason=runtime_reason,
            running=bool(started_at is not None and ended_at is None and status not in {"done", "failed"}),
            has_activity=started_at is not None or ended_at is not None or bool(runtime_reason or runtime_message),
            default=status,
        )
        if task.id == current_task_id:
            status = "in_progress"
        elif task.id in done_ids:
            status = "done"
        elif task.id in failed_lookup:
            status = "failed"
        elif runtime_status == "running" or runtime_status == "in_progress":
            status = "in_progress"
        elif runtime_status == "done":
            status = "done"
        elif runtime_status == "failed":
            status = "failed"

        if status == "in_progress":
            if task.id == current_task_id or (started_at is not None and started_at > selected_started_at):
                selected_id = task.id
                selected_started_at = started_at or selected_started_at
            if task.id == current_task_id and selected_started_at < 0:
                selected_started_at = started_at or 0

        failure = failed_lookup.get(task.id, {})
        failure_reason = _pick_text(failure.get("reason"))
        failure_detail = _pick_text(failure.get("detail"))
        if status == "failed":
            if not failure_reason:
                failure_reason = runtime_reason
            if not failure_detail:
                failure_detail = runtime_message
        attempt = _coerce_optional_int(_pick_value(failure.get("attempt"), runtime_attempt))
        file_scope = _task_file_scope(task.files)
        recent_output = _task_output_excerpt(
            run_dir,
            stage_name="Dev",
            cycle=runtime_cycle,
            step=runtime_step,
            task_id=task.id,
            attempt=attempt,
            reason=failure_reason,
            fallback_text=failure_detail or runtime_message,
        )

        backlog.append(
            {
                "id": task.id,
                "title": task.title,
                "prompt": task.prompt,
                "files": task.files,
                "file_scope": file_scope,
                "done_when": task.done_when,
                "skills": task.skills,
                "skills_rationale": task.skills_rationale,
                "depends_on": task.depends_on,
                "status": status,
                "priority": _task_priority(task, index),
                "tags": _task_tags(task),
                "estimate": _task_estimate(task),
                "skill": task.skills[0] if task.skills else None,
                "attempt": attempt,
                "failure": {
                    "reason": failure_reason,
                    "detail": failure_detail,
                    "cycle": failure.get("cycle"),
                    "step": failure.get("step"),
                    "rc": failure.get("rc"),
                },
                "failure_reason": failure_reason,
                "failure_detail": failure_detail,
                "recent_output": recent_output,
                "cycle": runtime_cycle,
                "step": runtime_step,
                "task_title": runtime.get("taskTitle") or task.title,
                "model": runtime.get("model") or "",
                "started_at": started_at,
                "ended_at": ended_at,
            }
        )

    if current_task_id and any(item["id"] == current_task_id for item in backlog):
        selected_id = current_task_id

    counts = {
        "pending": len([item for item in backlog if item["status"] == "pending"]),
        "in_progress": len([item for item in backlog if item["status"] == "in_progress"]),
        "done": len([item for item in backlog if item["status"] == "done"]),
        "failed": len([item for item in backlog if item["status"] == "failed"]),
    }
    return {
        "items": backlog,
        "counts": counts,
        "selected_id": selected_id,
    }


def _cycle_events(run_dir: Path | None) -> list[dict[str, Any]]:
    if run_dir is None:
        return []
    return _safe_jsonl(run_dir / "metrics.jsonl", max_items=500)


def _event_stage(event: dict[str, Any]) -> str:
    stage = str(event.get("stage") or "").strip()
    if stage:
        return stage
    event_type = str(event.get("event") or event.get("type") or "").strip().lower()
    if event_type.startswith("pm_"):
        return "PM"
    if event_type.startswith("dev_") or event_type.startswith("task_"):
        return "Dev"
    if event_type.startswith("qa_"):
        return "QA"
    if event_type.startswith("security_"):
        return "Security"
    if event_type.startswith("reporter_"):
        return "Reporter"
    return "boot"


def _event_level(event: dict[str, Any]) -> str:
    level = str(event.get("level") or "").strip().lower()
    if level in {"debug", "info", "warning", "error"}:
        return {"warning": "warn", "error": "err"}.get(level, level)
    event_type = str(event.get("event") or event.get("type") or "").strip().lower()
    if "error" in event_type or "fail" in event_type or "exception" in event_type:
        return "err"
    if "warn" in event_type or "stop" in event_type or "retry" in event_type:
        return "warn"
    return "info"


def _event_message(event: dict[str, Any]) -> str:
    message = event.get("message")
    if message:
        return str(message)
    event_type = str(event.get("event") or event.get("type") or "").strip()
    if event_type:
        return event_type
    return ""


def _normalize_stage_name(value: Any) -> str:
    raw = _pick_text(value).strip().lower()
    if not raw:
        return ""
    if raw.startswith("pm") or raw in {"planner", "planning", "pm_stage"}:
        return "PM"
    if raw.startswith("dev") or raw.startswith("task") or raw.startswith("build") or raw.startswith("test") or raw in {"implementation"}:
        return "Dev"
    if raw.startswith("qa") or raw in {"verification", "qa_stage"}:
        return "QA"
    return ""


def _normalize_lifecycle_status(
    raw_status: Any,
    *,
    rc: Any = None,
    reason: str = "",
    running: bool = False,
    has_activity: bool = False,
    default: str = "pending",
) -> str:
    normalized = _pick_text(raw_status).strip().lower()
    aliases = {
        "complete": "done",
        "completed": "done",
        "done": "done",
        "ok": "done",
        "success": "done",
        "skip": "skipped",
        "skipped": "skipped",
        "stop": "stopped",
        "stopped": "stopped",
        "halted": "stopped",
        "cancelled": "stopped",
        "canceled": "stopped",
        "fail": "failed",
        "failed": "failed",
        "error": "failed",
        "running": "running",
        "active": "running",
        "in_progress": "running",
        "pending": "pending",
        "idle": "pending",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"done", "running", "pending", "failed", "stopped", "skipped"}:
        if running and normalized in {"pending", "skipped"}:
            return "running"
        return normalized

    if running:
        return "running"

    rc_value = _coerce_optional_int(rc)
    if rc_value is not None:
        if rc_value == 0:
            reason_value = _pick_text(reason).strip().lower()
            if reason_value in {"stop_file", "stop_requested", "stopped", "manual_stop"}:
                return "stopped"
            return "done"
        return "failed"

    reason_value = _pick_text(reason).strip().lower()
    if reason_value in {"project_complete", "all_tasks_done", "completed", "success", "ok", "done"}:
        return "done"
    if reason_value in {"stop_file", "stop_requested", "stopped", "manual_stop", "quota_exhausted"}:
        return "stopped"
    if reason_value in {"failed", "error", "exception", "abandoned", "abandon_failed", "build_failed", "test_failed", "policy_violation", "exhausted_attempts", "needs_dependency", "blocked_dependency", "no_diff"}:
        return "failed"
    return "running" if has_activity else default


def _task_file_scope(files: list[str], *, limit: int = 3) -> str:
    cleaned: list[str] = []
    for path in files:
        text = str(path).replace("\\", "/").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        return ""
    if len(cleaned) <= max(1, int(limit)):
        return ", ".join(cleaned)
    limit_i = max(1, int(limit))
    return ", ".join(cleaned[:limit_i]) + f" (+{len(cleaned) - limit_i} more)"


def _task_runtime_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        task_id = _pick_text(event.get("task_id"), event.get("task"))
        if not task_id:
            continue

        entry = index.setdefault(
            task_id,
            {
                "taskId": task_id,
                "taskTitle": "",
                "cycle": None,
                "step": None,
                "attempt": None,
                "model": "",
                "startedAt": None,
                "endedAt": None,
                "rc": None,
                "reason": "",
                "lastMessage": "",
                "lastEvent": "",
            },
        )

        event_type = str(event.get("event") or event.get("type") or "").strip().lower()
        ts = _iso_to_ms(event.get("ts"))
        cycle = _coerce_optional_int(event.get("cycle"))
        step = _coerce_optional_int(event.get("step"))
        attempt = _coerce_optional_int(event.get("attempt"))
        model = _pick_text(event.get("model"))
        task_title = _pick_text(event.get("task_title"), event.get("title"))
        reason = _pick_text(event.get("reason"))
        message = _event_message(event)

        if cycle is not None:
            current_cycle = _coerce_optional_int(entry.get("cycle"))
            entry["cycle"] = cycle if current_cycle is None else max(current_cycle, cycle)
        if step is not None and entry.get("step") is None:
            entry["step"] = step
        if attempt is not None:
            current_attempt = _coerce_optional_int(entry.get("attempt"))
            entry["attempt"] = attempt if current_attempt is None else max(current_attempt, attempt)
        if model and not entry.get("model"):
            entry["model"] = model
        if task_title and not entry.get("taskTitle"):
            entry["taskTitle"] = task_title
        if reason and not entry.get("reason"):
            entry["reason"] = reason
        if reason or message:
            entry["lastMessage"] = reason or message
            entry["lastEvent"] = message or reason

        if event_type in {"task_start", "dev_attempt_start"}:
            if ts and (entry.get("startedAt") is None or ts < int(entry.get("startedAt") or 0)):
                entry["startedAt"] = ts
            if event_type == "dev_attempt_start" and model:
                entry["model"] = model
            if event_type == "dev_attempt_start" and attempt is not None:
                current_attempt = _coerce_optional_int(entry.get("attempt"))
                entry["attempt"] = attempt if current_attempt is None else max(current_attempt, attempt)
        elif event_type in {"task_end", "build_end", "test_end"}:
            if ts and (entry.get("endedAt") is None or ts > int(entry.get("endedAt") or 0)):
                entry["endedAt"] = ts
            rc = _coerce_optional_int(event.get("rc"))
            if rc is not None:
                entry["rc"] = rc
            if reason:
                entry["reason"] = reason
        elif event_type == "dev_attempt_retry":
            if ts and (entry.get("endedAt") is None or ts > int(entry.get("endedAt") or 0)):
                entry["endedAt"] = ts
            if reason:
                entry["reason"] = reason
            if attempt is not None:
                current_attempt = _coerce_optional_int(entry.get("attempt"))
                entry["attempt"] = (attempt + 1) if current_attempt is None else max(current_attempt, attempt + 1)

    for entry in index.values():
        started_at = _coerce_optional_int(entry.get("startedAt"))
        ended_at = _coerce_optional_int(entry.get("endedAt"))
        if started_at is not None and ended_at is not None and ended_at >= started_at:
            entry["durationSec"] = round((ended_at - started_at) / 1000.0, 3)
        else:
            entry["durationSec"] = None
    return index


def _task_output_excerpt(
    run_dir: Path | None,
    *,
    stage_name: str,
    cycle: int | None = None,
    step: int | None = None,
    task_id: str = "",
    attempt: int | None = None,
    reason: str = "",
    fallback_text: str = "",
) -> str:
    if run_dir is None:
        return _pick_text(fallback_text)

    candidates: list[Path] = []
    stage_key = _normalize_stage_name(stage_name)
    cycle_i = _coerce_optional_int(cycle)
    step_i = _coerce_optional_int(step)
    attempt_i = _coerce_optional_int(attempt)

    if stage_key == "PM" and cycle_i is not None:
        candidates.extend(
            [
                run_dir / f"pm_final_output_cycle_{cycle_i:03d}.txt",
                run_dir / "NOTES_PM.md",
                run_dir / "cycle_summary.log",
                run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
            ]
        )
    elif stage_key == "QA" and cycle_i is not None:
        candidates.extend(
            [
                run_dir / f"qa_followups_cycle_{cycle_i:03d}.json",
                run_dir / "cycle_summary.log",
                run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
            ]
        )
    elif task_id and cycle_i is not None and step_i is not None and attempt_i is not None:
        task_dir = run_dir / "tasks" / f"c{cycle_i:03d}_s{step_i:03d}_{task_id}" / f"attempt_{attempt_i:02d}"
        reason_text = _pick_text(reason).lower()
        if "build" in reason_text:
            candidates.append(task_dir / "build.txt")
        if "test" in reason_text:
            candidates.append(task_dir / "test.txt")
        candidates.extend(
            [
                task_dir / "dev_output.txt",
                task_dir / "NOTES.md",
                task_dir / "DEPENDENCY_REQUIRED.md",
                run_dir / "dev_logs" / f"c{cycle_i:03d}_s{step_i:03d}_{task_id}_a{attempt_i:02d}.txt",
            ]
        )
    elif task_id and cycle_i is not None:
        candidates.extend(
            [
                run_dir / "cycle_summary.log",
                run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
            ]
        )

    for candidate in candidates:
        text = _tail_text(candidate, 12)
        if text:
            return text
    return _pick_text(fallback_text)


def _load_log_entries(run_dir: Path | None) -> list[dict[str, Any]]:
    events = _cycle_events(run_dir)
    if events:
        rows: list[dict[str, Any]] = []
        for event in events:
            rows.append(
                {
                    "t": _fmt_clock(event.get("ts")),
                    "lvl": _event_level(event),
                    "stage": _event_stage(event),
                    "msg": _event_message(event),
                }
            )
        return rows

    if run_dir is None:
        return []

    fallback = run_dir / "logs" / "run.log"
    raw = _tail_text(fallback, lines=200)
    if not raw:
        return []

    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^(?:(?P<date>\d{4}-\d{2}-\d{2})\s+)?(?P<time>\d{2}:\d{2}:\d{2})\s+\[(?P<level>[A-Z]+)\]\s*(?P<msg>.*)$"
    )
    for line in raw.splitlines():
        match = pattern.match(line)
        if match:
            level = match.group("level").lower()
            rows.append(
                {
                    "t": match.group("time"),
                    "lvl": {"warning": "warn", "error": "err"}.get(level, level),
                    "stage": "boot",
                    "msg": match.group("msg").strip(),
                }
            )
        else:
            rows.append({"t": "", "lvl": "info", "stage": "boot", "msg": line.strip()})
    return rows


def _normalize_log_tail_level(value: Any) -> str:
    level = str(value or "").strip().lower()
    if level == "warning":
        return "warn"
    if level == "error":
        return "err"
    return level


def _log_tail_source_candidates(run_dir: Path | None) -> list[Path]:
    if run_dir is None:
        return []
    return [
        run_dir / "metrics.jsonl",
        run_dir / "logs" / "events.jsonl",
        run_dir / "logs" / "run.log",
    ]


def _resolve_log_tail_source(run_dir: Path | None) -> Path | None:
    candidates = _log_tail_source_candidates(run_dir)
    if not candidates:
        return None
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except OSError:
            continue
    return candidates[0]


def _log_tail_entry_search_text(entry: dict[str, Any]) -> str:
    parts = [
        entry.get("t"),
        entry.get("ts"),
        entry.get("lvl"),
        entry.get("level"),
        entry.get("stage"),
        entry.get("task_id"),
        entry.get("taskId"),
        entry.get("task_title"),
        entry.get("taskTitle"),
        entry.get("event"),
        entry.get("type"),
        entry.get("msg"),
        entry.get("message"),
        entry.get("text"),
        entry.get("reason"),
        entry.get("raw"),
    ]
    return " ".join(str(part).lower() for part in parts if part is not None and str(part).strip())


def _normalize_structured_log_tail_entry(payload: dict[str, Any], *, raw_line: str, line_number: int) -> dict[str, Any]:
    ts = _pick_text(payload.get("ts"), payload.get("timestamp"), payload.get("time"))
    level = _normalize_log_tail_level(_pick_text(payload.get("lvl"), payload.get("level"), _event_level(payload)))
    stage = _pick_text(payload.get("stage"), payload.get("component"), payload.get("scope")) or _event_stage(payload)
    message = _pick_text(payload.get("msg"), payload.get("message"), payload.get("text"), _event_message(payload), raw_line)
    task_id = _pick_text(payload.get("task_id"), payload.get("taskId"))
    task_title = _pick_text(payload.get("task_title"), payload.get("taskTitle"))
    event = _pick_text(payload.get("event"), payload.get("type"))
    return {
        "cursor": line_number,
        "line_number": line_number,
        "raw": raw_line,
        "ts": ts,
        "t": _fmt_clock(ts) if ts else "",
        "lvl": level or "info",
        "level": level or "info",
        "stage": stage or "boot",
        "msg": message,
        "message": message,
        "task_id": task_id,
        "taskId": task_id,
        "task_title": task_title,
        "taskTitle": task_title,
        "event": event,
        "type": event,
        "cycle": _coerce_optional_int(payload.get("cycle")),
        "step": _coerce_optional_int(payload.get("step")),
        "attempt": _coerce_optional_int(payload.get("attempt")),
        "reason": _pick_text(payload.get("reason")),
        "rc": _coerce_optional_int(payload.get("rc")),
    }


def _normalize_plain_log_tail_entry(raw_line: str, *, line_number: int) -> dict[str, Any]:
    pattern = re.compile(r"^(?:(?P<date>\d{4}-\d{2}-\d{2})\s+)?(?P<time>\d{2}:\d{2}:\d{2})\s+\[(?P<level>[A-Z]+)\]\s*(?P<msg>.*)$")
    match = pattern.match(raw_line)
    if match:
        level = _normalize_log_tail_level(match.group("level"))
        msg = match.group("msg").strip()
        ts = " ".join(part for part in (match.group("date"), match.group("time")) if part).strip()
        return {
            "cursor": line_number,
            "line_number": line_number,
            "raw": raw_line,
            "ts": ts,
            "t": match.group("time"),
            "lvl": level or "info",
            "level": level or "info",
            "stage": "boot",
            "msg": msg,
            "message": msg,
            "task_id": "",
            "taskId": "",
            "task_title": "",
            "taskTitle": "",
            "event": "",
            "type": "",
            "cycle": None,
            "step": None,
            "attempt": None,
            "reason": "",
            "rc": None,
        }
    msg = raw_line.strip()
    return {
        "cursor": line_number,
        "line_number": line_number,
        "raw": raw_line,
        "ts": "",
        "t": "",
        "lvl": "info",
        "level": "info",
        "stage": "boot",
        "msg": msg,
        "message": msg,
        "task_id": "",
        "taskId": "",
        "task_title": "",
        "taskTitle": "",
        "event": "",
        "type": "",
        "cycle": None,
        "step": None,
        "attempt": None,
        "reason": "",
        "rc": None,
    }


def _parse_log_tail_entry(raw_line: str, *, line_number: int, source_path: Path) -> tuple[dict[str, Any] | None, bool]:
    raw = raw_line.rstrip("\n")
    if not raw.strip():
        return None, False
    if source_path.suffix.lower() == ".jsonl":
        try:
            payload = json.loads(raw)
        except Exception:
            return None, True
        if not isinstance(payload, dict):
            return None, True
        return _normalize_structured_log_tail_entry(payload, raw_line=raw, line_number=line_number), False
    return _normalize_plain_log_tail_entry(raw, line_number=line_number), False


def _log_tail_entry_matches(
    entry: dict[str, Any],
    *,
    level: str = "",
    stage: str = "",
    task_id: str = "",
    search: str = "",
) -> bool:
    level_filter = _normalize_log_tail_level(level)
    if level_filter and level_filter not in {"all", "any", "*"}:
        entry_level = _normalize_log_tail_level(entry.get("lvl") or entry.get("level"))
        if entry_level != level_filter:
            return False

    stage_filter = _pick_text(stage).lower()
    if stage_filter and stage_filter not in {"all", "any", "*"}:
        entry_stage = _pick_text(entry.get("stage")).lower()
        if entry_stage != stage_filter:
            return False

    task_filter = _pick_text(task_id).lower()
    if task_filter:
        entry_task = _pick_text(entry.get("task_id"), entry.get("taskId")).lower()
        if entry_task != task_filter:
            return False

    search_filter = _pick_text(search).lower()
    if search_filter and search_filter not in _log_tail_entry_search_text(entry):
        return False

    return True


def _build_log_tail_payload(
    source_path: Path,
    *,
    cursor: int | None,
    max_lines: int,
    level: str = "",
    stage: str = "",
    task_id: str = "",
    search: str = "",
    live: bool = False,
) -> dict[str, Any]:
    max_lines = max(1, int(max_lines))
    source_file = source_path.expanduser().resolve()
    entries: list[dict[str, Any]] = []
    malformed_count = 0
    total_lines = 0
    next_cursor = 0
    cursor_mode = cursor is not None
    start_cursor = max(0, int(cursor or 0))

    try:
        with source_file.open("r", encoding="utf-8", errors="replace") as handle:
            if cursor_mode:
                for line_number, raw_line in enumerate(handle, start=1):
                    total_lines = line_number
                    if line_number <= start_cursor:
                        continue
                    next_cursor = line_number
                    entry, malformed = _parse_log_tail_entry(raw_line, line_number=line_number, source_path=source_file)
                    if malformed:
                        malformed_count += 1
                        continue
                    if entry is None or not _log_tail_entry_matches(entry, level=level, stage=stage, task_id=task_id, search=search):
                        continue
                    entries.append(entry)
                    if len(entries) >= max_lines:
                        break
            else:
                matched: deque[dict[str, Any]] = deque(maxlen=max_lines)
                for line_number, raw_line in enumerate(handle, start=1):
                    total_lines = line_number
                    next_cursor = line_number
                    entry, malformed = _parse_log_tail_entry(raw_line, line_number=line_number, source_path=source_file)
                    if malformed:
                        malformed_count += 1
                        continue
                    if entry is None or not _log_tail_entry_matches(entry, level=level, stage=stage, task_id=task_id, search=search):
                        continue
                    matched.append(entry)
                entries = list(matched)
    except FileNotFoundError:
        return {
            "ok": False,
            "state": "missing_file",
            "entries": [],
            "next_cursor": 0,
            "source_file": source_file.as_posix(),
            "source_path": source_file.as_posix(),
            "source": {
                "path": source_file.as_posix(),
                "name": source_file.name,
                "exists": False,
            },
            "malformed_lines": 0,
        }
    except Exception as ex:
        return {
            "ok": False,
            "state": "read_error",
            "entries": [],
            "next_cursor": start_cursor,
            "source_file": source_file.as_posix(),
            "source_path": source_file.as_posix(),
            "source": {
                "path": source_file.as_posix(),
                "name": source_file.name,
                "exists": source_file.exists(),
            },
            "error": str(ex).strip() or ex.__class__.__name__,
            "malformed_lines": malformed_count,
        }

    if cursor_mode and next_cursor == 0:
        next_cursor = total_lines

    state = "loading" if (entries or live or (cursor_mode and total_lines > start_cursor)) else "empty"
    if malformed_count:
        state = "malformed_line"

    return {
        "ok": True,
        "state": state,
        "entries": entries,
        "next_cursor": next_cursor if cursor_mode else total_lines,
        "source_file": source_file.as_posix(),
        "source_path": source_file.as_posix(),
        "source": {
            "path": source_file.as_posix(),
            "name": source_file.name,
            "exists": source_file.exists(),
        },
        "cursor": start_cursor if cursor_mode else None,
        "max_lines": max_lines,
        "malformed_lines": malformed_count,
    }


def _build_notifications(
    *,
    run_id: str,
    started_at_ms: int,
    branch: str,
    active_status: str,
    state: dict[str, Any],
    backlog: list[dict[str, Any]],
    events: list[dict[str, Any]],
    final_reason: str,
) -> list[dict[str, Any]]:
    backlog_by_id = {item["id"]: item for item in backlog}
    notifications: list[dict[str, Any]] = []

    if started_at_ms:
        notifications.append(
            {
                "t": started_at_ms,
                "kind": "run_start",
                "text": f"Run started | {branch or run_id}",
                "run": run_id,
            }
        )

    done_ids = [str(item) for item in (state.get("done") or []) if str(item).strip()]
    for task_id in done_ids[-4:]:
        item = backlog_by_id.get(task_id)
        title = item["title"] if item else task_id
        notifications.append(
            {
                "t": started_at_ms or _epoch_ms(datetime.now(timezone.utc).timestamp()),
                "kind": "task_done",
                "text": f"{task_id} | {title}",
                "run": run_id,
            }
        )

    failed_list = state.get("failed") if isinstance(state.get("failed"), list) else []
    for raw in failed_list[-4:]:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task") or "").strip()
        if not task_id:
            continue
        item = backlog_by_id.get(task_id)
        title = item["title"] if item else task_id
        notifications.append(
            {
                "t": started_at_ms or _epoch_ms(datetime.now(timezone.utc).timestamp()),
                "kind": "task_failed",
                "text": f"{task_id} | {title}",
                "run": run_id,
            }
        )

    for event in events:
        event_type = str(event.get("event") or event.get("type") or "").strip().lower()
        task_id = str(event.get("task_id") or "").strip()
        message = _event_message(event)
        ts = _iso_to_ms(event.get("ts"))

        kind: str | None = None
        text = message or event_type
        if event_type in {"task_end", "task_done"}:
            if str(event.get("success", True)).lower() in {"false", "0"} or str(event.get("status", "")).lower() in {"failed", "fail"}:
                kind = "task_failed"
            else:
                kind = "task_done"
        elif "quota" in event_type:
            kind = "quota"
        elif "fail" in event_type or "error" in event_type or "exception" in event_type:
            kind = "error"
        elif "stalled" in event_type:
            kind = "stalled"
        elif event_type in {"cycle_start", "pm_start"}:
            kind = "run_start"
        elif event_type in {"cycle_end", "pm_end", "qa_end", "dev_end"} and active_status in {"stopped", "failed"}:
            kind = "run_stop"

        if kind:
            notifications.append(
                {
                    "t": ts or started_at_ms,
                    "kind": kind,
                    "text": text or event_type,
                    "run": run_id,
                }
            )

    if final_reason:
        final_kind = "task_done" if final_reason in {"project_complete", "all_tasks_done"} else "run_stop"
        final_text = f"Run finished | {final_reason}"
        if final_kind == "task_done" and final_reason == "project_complete":
            final_text = "Project complete"
        notifications.append(
            {
                "t": _epoch_ms(datetime.now(timezone.utc).timestamp()),
                "kind": final_kind,
                "text": final_text,
                "run": run_id,
            }
        )

    notifications = [item for item in notifications if item.get("kind")]
    notifications.sort(key=lambda item: int(item.get("t") or 0), reverse=True)
    return notifications[:20]


def _active_run_payload(
    *,
    repo: Path,
    run_dir: Path | None,
    config: dict[str, Any],
    run_summary: dict[str, Any],
    last_run_summary: dict[str, Any],
    state: dict[str, Any],
    backlog: list[dict[str, Any]],
    progress: dict[str, Any],
    metrics: dict[str, Any],
    branch: str,
    controller_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    repo_path = repo.as_posix()
    run_id = _pick_text(controller_data.get("run_id"), controller_data.get("runId"), run_dir.name if run_dir else "", "no-run")
    run_id = run_id or "no-run"
    run_dir_text = _pick_text(controller_data.get("run_dir"), run_dir.as_posix() if run_dir else "")
    run_dir_exists = False
    if run_dir is not None:
        try:
            run_dir_exists = run_dir.exists() and run_dir.is_dir()
        except OSError:
            run_dir_exists = False
    started_at_ms = _coerce_optional_ms(_pick_value(controller_data.get("startedAt"), controller_data.get("started_at")))
    uptime_seconds = _coerce_optional_int(_pick_value(controller_data.get("uptime_seconds"), controller_data.get("uptimeSeconds")))
    if started_at_ms is None and run_dir_exists:
        try:
            started_at_ms = _epoch_ms(run_dir.stat().st_ctime)
        except OSError:
            started_at_ms = None
    if started_at_ms is None and uptime_seconds is not None:
        started_at_ms = max(0, _epoch_ms(time.time()) - (uptime_seconds * 1000))
    started_at_ms = started_at_ms or 0
    ended_at_ms = 0
    if run_dir_exists:
        try:
            ended_at_ms = _epoch_ms(run_dir.stat().st_mtime)
        except OSError:
            ended_at_ms = 0
    elapsed_sec = _coerce_optional_int(_pick_value(controller_data.get("elapsedSec"), controller_data.get("elapsed_seconds"), controller_data.get("elapsedSeconds")))
    if elapsed_sec is None and uptime_seconds is not None:
        elapsed_sec = max(0, uptime_seconds)
    if elapsed_sec is None and started_at_ms and ended_at_ms:
        elapsed_sec = max(0, int((ended_at_ms - started_at_ms) / 1000))
    if elapsed_sec is None:
        elapsed_sec = 0

    cycles = run_summary.get("cycles") if isinstance(run_summary.get("cycles"), list) else []
    final_reason_text = _pick_text(
        controller_data.get("final_reason"),
        controller_data.get("reason"),
        progress.get("final_reason"),
        last_run_summary.get("stop_reason"),
        run_summary.get("final").get("reason") if isinstance(run_summary.get("final"), dict) else "",
    )

    tasks_total = _coerce_optional_int(progress.get("tasks_total"))
    if tasks_total is None:
        tasks_total = len(backlog)
    tasks_done = int(progress.get("tasks_done") or 0)
    tasks_failed = int(progress.get("tasks_failed") or 0)
    progress_goals = progress.get("goals") if isinstance(progress.get("goals"), dict) else {}
    project_completion = _project_completion_status(
        progress_goals,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        tasks_failed=tasks_failed,
    )
    progress_available = bool(progress.get("progress_available"))
    progress_value = _coerce_optional_float(_pick_value(progress.get("progress"), progress.get("progress_value"), progress.get("progressValue")))
    if not progress_available:
        progress_value = None

    execution_status = _normalize_execution_status(
        controller_data.get("run_status") or controller_data.get("status") or progress.get("run_status") or progress.get("status"),
        running=bool(controller_data.get("running")),
        exit_code=controller_data.get("exit_code"),
        final_reason=final_reason_text,
        stop_file_exists=bool(controller_data.get("stop_file_exists") or (run_dir_exists and (run_dir / "STOP").exists())),
        has_run_dir=bool(run_dir),
    )
    run_status = _normalize_run_status(
        controller_data.get("run_status") or controller_data.get("status") or progress.get("run_status") or progress.get("status"),
        running=bool(controller_data.get("running")),
        exit_code=controller_data.get("exit_code"),
        final_reason=final_reason_text,
        stop_file_exists=bool(controller_data.get("stop_file_exists") or (run_dir_exists and (run_dir / "STOP").exists())),
        has_run_dir=bool(run_dir),
        project_complete=project_completion["project_complete"],
    )

    last_event_stage = _pick_text(controller_data.get("stage"), controller_data.get("current_stage"), metrics.get("last_stage"), progress.get("last_stage"))
    if not last_event_stage:
        if run_status == "success":
            last_event_stage = "QA"
        elif run_status == "idle":
            last_event_stage = "idle"
        else:
            last_event_stage = "Dev"

    task_id = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    )

    task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
        progress.get("current_task_title"),
    )
    if not task_title and task_id:
        task_title = next((item["title"] for item in backlog if item.get("id") == task_id), "")

    attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            progress.get("attempt"),
            progress.get("current_attempt"),
            run_summary.get("attempt"),
            last_run_summary.get("attempt"),
        )
    )

    worktree_mode = _pick_text(
        controller_data.get("worktree_mode"),
        controller_data.get("worktreeMode"),
        progress.get("worktree_mode"),
        progress.get("worktreeMode"),
    )
    if not worktree_mode:
        worktree_mode = "manual"
    iteration_value = _pick_value(_coerce_optional_int(progress.get("iterations")), len(cycles) or None)

    tokens = metrics.get("tokens") if isinstance(metrics.get("tokens"), dict) else {}
    tokens_available = bool(metrics.get("tokens_available") or metrics.get("tokensAvailable"))
    token_in = _coerce_optional_int(tokens.get("in"))
    token_out = _coerce_optional_int(tokens.get("out"))
    if not tokens_available:
        token_in = None
        token_out = None

    quota = _pick_quota_payload(controller_data, metrics)
    quota_available = bool(quota.get("available"))
    quota_used = quota.get("used")
    quota_window = str(quota.get("window") or "")

    budget_available = bool(metrics.get("budget_available") or metrics.get("budgetAvailable"))
    budget_used = _coerce_optional_float(_pick_value(metrics.get("budget_used"), metrics.get("budgetUsed")))
    if not budget_available:
        budget_used = None

    branch_value = _pick_text(controller_data.get("branch"), branch, run_summary.get("branch"), last_run_summary.get("branch"))

    return {
        "id": run_id,
        "repo": repo_path,
        "repoLabel": repo.name or repo_path.rsplit("/", 1)[-1],
        "branch": branch_value or "HEAD",
        "backend": str(config.get("execution_backend") or "codex"),
        "startedAt": started_at_ms,
        "stage": last_event_stage,
        "stageIndex": STAGE_ORDER.get(last_event_stage.lower(), 0),
        "iteration": 0 if run_status == "idle" else int(iteration_value if iteration_value is not None else 1),
        "maxIterations": int(config.get("iterations") or 5),
        "runDir": run_dir_text,
        "attempt": attempt,
        "worktreeMode": worktree_mode,
        "finalReason": final_reason_text,
        "progressAvailable": progress_available,
        "progress": round(progress_value, 3) if progress_value is not None else None,
        "executionStatus": execution_status,
        "execution_status": execution_status,
        "goalsComplete": project_completion["goals_complete"],
        "goals_complete": project_completion["goals_complete"],
        "backlogComplete": project_completion["backlog_complete"],
        "backlog_complete": project_completion["backlog_complete"],
        "projectComplete": project_completion["project_complete"],
        "project_complete": project_completion["project_complete"],
        "projectStatus": project_completion["project_status"],
        "project_status": project_completion["project_status"],
        "budgetAvailable": budget_available,
        "budgetUsed": round(budget_used, 3) if budget_used is not None else None,
        "tokensAvailable": tokens_available,
        "tokens": {
            "in": token_in,
            "out": token_out,
            "available": tokens_available,
        },
        "quotaAvailable": quota_available,
        "quotaWindow": quota_window,
        "quotaUsed": round(quota_used, 3) if quota_used is not None else None,
        "quota_available": quota_available,
        "quota_window": quota_window,
        "quota_used": round(quota_used, 3) if quota_used is not None else None,
        "quota": quota,
        "elapsedSec": elapsed_sec,
        "status": run_status,
        "task": task_id or "",
        "taskTitle": task_title or "",
    }


def _stage_payload(
    repo: Path,
    active_run: dict[str, Any],
    progress: dict[str, Any],
    config: dict[str, Any],
    *,
    run_dir: Path | None = None,
    run_summary: dict[str, Any] | None = None,
    last_run_summary: dict[str, Any] | None = None,
    controller_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    run_summary = run_summary if isinstance(run_summary, dict) else {}
    last_run_summary = last_run_summary if isinstance(last_run_summary, dict) else {}
    events = list(events) if isinstance(events, list) else (_cycle_events(run_dir) if run_dir is not None else [])
    task_runtime = _task_runtime_index(events)

    stage_titles = {
        "PM": "Backlog planning",
        "Dev": "Implementation",
        "QA": "Verification",
    }
    stage_model_defaults = {
        "PM": str(config.get("pm_model") or "gpt-5.5"),
        "Dev": str(config.get("dev_model") or "gpt-5.4-mini"),
        "QA": str(config.get("qa_model") or "gpt-5.5"),
    }

    active_status = str(active_run.get("status") or progress.get("run_status") or "idle").strip().lower()
    current_stage = _normalize_stage_name(
        _pick_text(controller_data.get("stage"), controller_data.get("current_stage"), active_run.get("stage"), progress.get("current_stage"))
    )
    if not current_stage and active_status == "running" and _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        active_run.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    ):
        current_stage = "Dev"
    current_task_id = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        active_run.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    )
    current_task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
        active_run.get("taskTitle"),
        progress.get("current_task_title"),
    )
    current_attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            active_run.get("attempt"),
            progress.get("attempt"),
            progress.get("current_attempt"),
            last_run_summary.get("attempt"),
            run_summary.get("attempt"),
        )
    )
    cycles = run_summary.get("cycles") if isinstance(run_summary.get("cycles"), list) else []
    cycle_entries = [entry for entry in cycles if isinstance(entry, dict)]
    latest_summary_cycle: int | None = None
    for entry in cycle_entries:
        cycle_value = _coerce_optional_int(entry.get("cycle"))
        if cycle_value is not None and (latest_summary_cycle is None or cycle_value > latest_summary_cycle):
            latest_summary_cycle = cycle_value

    current_cycle = _coerce_optional_int(
        _pick_value(
            controller_data.get("cycle"),
            controller_data.get("current_cycle"),
            active_run.get("iteration"),
            progress.get("iterations"),
            last_run_summary.get("cycle"),
            latest_summary_cycle,
        )
    )
    event_cycles = [cycle_value for cycle_value in (_coerce_optional_int(event.get("cycle")) for event in events) if cycle_value is not None]
    latest_event_cycle = max(event_cycles) if event_cycles else None
    if active_status == "running":
        target_cycle = current_cycle or latest_event_cycle or latest_summary_cycle
    else:
        target_cycle = latest_summary_cycle or latest_event_cycle or current_cycle
    target_cycle = _coerce_optional_int(target_cycle)

    target_cycle_entry: dict[str, Any] = {}
    if target_cycle is not None:
        for entry in reversed(cycle_entries):
            if _coerce_optional_int(entry.get("cycle")) == target_cycle:
                target_cycle_entry = entry
                break

    stage_summary_map: dict[str, dict[str, Any]] = {}
    for raw_stage in target_cycle_entry.get("stages") if isinstance(target_cycle_entry.get("stages"), list) else []:
        if not isinstance(raw_stage, dict):
            continue
        stage_name = _normalize_stage_name(raw_stage.get("name"))
        if stage_name in stage_titles:
            stage_summary_map[stage_name] = raw_stage

    running_stage_name = current_stage if active_status == "running" else ""
    relevant_events = [event for event in events if target_cycle is None or _coerce_optional_int(event.get("cycle")) in {None, target_cycle}]

    stage_event_map: dict[str, dict[str, Any]] = {}
    for event in relevant_events:
        event_type = str(event.get("event") or event.get("type") or "").strip().lower()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        stage_name = _normalize_stage_name(_pick_value(event.get("stage"), payload.get("stage")))
        if not stage_name and (event_type.startswith("pm_") or event_type.startswith("pm_stage_")):
            stage_name = "PM"
        elif not stage_name and (event_type.startswith("qa_") or event_type.startswith("qa_stage_")):
            stage_name = "QA"
        if stage_name not in {"PM", "QA"}:
            continue
        if event_type not in {"pm_start", "pm_end", "pm_stage_start", "pm_stage_end", "qa_start", "qa_end", "qa_stage_start", "qa_stage_end", "stage_event"}:
            continue
        entry = stage_event_map.setdefault(
            stage_name,
            {
                "cycle": None,
                "status": "",
                "startedAt": None,
                "endedAt": None,
                "rc": None,
                "reason": "",
                "model": "",
                "taskId": "",
                "taskTitle": "",
                "attempt": None,
                "step": None,
                "lastMessage": "",
                "lastEvent": "",
            },
        )
        ts = _iso_to_ms(event.get("ts"))
        if ts is not None:
            started_at = _coerce_optional_int(entry.get("startedAt"))
            ended_at = _coerce_optional_int(entry.get("endedAt"))
            if event_type.endswith("start") and (started_at is None or ts < started_at):
                entry["startedAt"] = ts
            if event_type.endswith("end") and (ended_at is None or ts > ended_at):
                entry["endedAt"] = ts
        cycle_value = _coerce_optional_int(_pick_value(event.get("cycle"), payload.get("cycle")))
        if cycle_value is not None and entry["cycle"] is None:
            entry["cycle"] = cycle_value
        reason = _pick_text(event.get("reason"), payload.get("reason"), payload.get("detail"))
        inner_event = _pick_text(payload.get("event")) if event_type == "stage_event" else ""
        if event_type == "stage_event":
            reason = _pick_text(reason, inner_event)
        if reason:
            entry["reason"] = reason
        model = _pick_text(event.get("model"), payload.get("model"))
        if model and not entry["model"]:
            entry["model"] = model
        task_id = _pick_text(event.get("task_id"), event.get("task"), payload.get("task_id"), payload.get("task"))
        if task_id and not entry["taskId"]:
            entry["taskId"] = task_id
        task_title = _pick_text(event.get("task_title"), event.get("taskTitle"), event.get("title"), payload.get("task_title"), payload.get("taskTitle"), payload.get("title"))
        if task_title and not entry["taskTitle"]:
            entry["taskTitle"] = task_title
        attempt = _coerce_optional_int(_pick_value(event.get("attempt"), payload.get("attempt")))
        if attempt is not None:
            entry["attempt"] = attempt
        step = _coerce_optional_int(_pick_value(event.get("step"), payload.get("step")))
        if step is not None and entry["step"] is None:
            entry["step"] = step
        if event_type.endswith("start"):
            entry["status"] = "running"
        elif event_type.endswith("end"):
            rc = _coerce_optional_int(_pick_value(event.get("rc"), payload.get("rc")))
            if rc is not None:
                entry["rc"] = rc
                entry["status"] = "done" if rc == 0 else "failed"
            elif reason:
                entry["status"] = _normalize_lifecycle_status(reason, reason=reason, default="done")
            elif not entry["status"]:
                entry["status"] = "done"
        elif event_type == "stage_event":
            if inner_event in {"error", "quota_exhausted"} or reason in {"error", "quota_exhausted"}:
                entry["status"] = "failed"
            elif not entry["status"]:
                entry["status"] = "running"
        message = _pick_text(payload.get("detail"), payload.get("reason"), reason, inner_event, _event_message(event))
        if message:
            entry["lastMessage"] = message
        event_label = inner_event or _event_message(event)
        if event_label:
            entry["lastEvent"] = event_label

    def _runtime_key(entry: dict[str, Any]) -> tuple[int, int, int, int, int]:
        return (
            _coerce_optional_int(entry.get("cycle")) or -1,
            _coerce_optional_int(entry.get("attempt")) or -1,
            _coerce_optional_int(entry.get("step")) or -1,
            _coerce_optional_int(entry.get("startedAt")) or -1,
            _coerce_optional_int(entry.get("endedAt")) or -1,
        )

    cycle_task_runtimes = [entry for entry in task_runtime.values() if target_cycle is None or _coerce_optional_int(entry.get("cycle")) in {None, target_cycle}]
    latest_task_runtime: dict[str, Any] = {}
    if cycle_task_runtimes:
        if current_task_id and current_task_id in task_runtime and (target_cycle is None or _coerce_optional_int(task_runtime[current_task_id].get("cycle")) in {None, target_cycle}):
            latest_task_runtime = task_runtime[current_task_id]
        else:
            latest_task_runtime = max(cycle_task_runtimes, key=_runtime_key)

    records: list[dict[str, Any]] = []
    for stage_name in ("PM", "Dev", "QA"):
        summary = stage_summary_map.get(stage_name, {})
        stage_runtime = latest_task_runtime if stage_name == "Dev" else stage_event_map.get(stage_name, {})

        summary_status = _pick_text(summary.get("status"))
        summary_reason = _pick_text(summary.get("reason"), summary.get("message"))
        summary_cycle = _coerce_optional_int(summary.get("cycle"))
        summary_step = _coerce_optional_int(summary.get("step"))
        runtime_cycle = _coerce_optional_int(stage_runtime.get("cycle"))
        cycle_value = summary_cycle if summary_cycle is not None else runtime_cycle
        if cycle_value is None:
            cycle_value = current_cycle if stage_name == running_stage_name and active_status == "running" else target_cycle

        started_at = _coerce_optional_ms(
            _pick_value(
                summary.get("startedAt"),
                summary.get("started_at"),
                stage_runtime.get("startedAt"),
                stage_runtime.get("started_at"),
            )
        )
        ended_at = _coerce_optional_ms(
            _pick_value(
                summary.get("endedAt"),
                summary.get("ended_at"),
                stage_runtime.get("endedAt"),
                stage_runtime.get("ended_at"),
            )
        )
        rc = _coerce_optional_int(_pick_value(summary.get("rc"), stage_runtime.get("rc")))
        reason = _pick_text(summary_reason, stage_runtime.get("reason"))
        task_id = _pick_text(summary.get("taskId"), summary.get("task_id"), stage_runtime.get("taskId"))
        task_title = _pick_text(summary.get("taskTitle"), summary.get("task_title"), stage_runtime.get("taskTitle"))
        step = _coerce_optional_int(_pick_value(summary_step, stage_runtime.get("step")))
        attempt = _coerce_optional_int(
            _pick_value(
                summary.get("attempt"),
                summary.get("currentAttempt"),
                stage_runtime.get("attempt"),
                current_attempt if stage_name in {"Dev", "PM", "QA"} else None,
            )
        )
        model = _pick_text(
            summary.get("model"),
            stage_runtime.get("model"),
            stage_model_defaults.get(stage_name, ""),
        )

        if stage_name == "Dev":
            if not task_id:
                task_id = current_task_id or _pick_text(stage_runtime.get("taskId"))
            if not task_title:
                task_title = current_task_title or _pick_text(stage_runtime.get("taskTitle"))
        elif stage_name == running_stage_name and active_status == "running":
            if not task_id:
                task_id = current_task_id
            if not task_title:
                task_title = current_task_title

        running = stage_name == running_stage_name and active_status == "running"
        if running:
            started_at = started_at or _coerce_optional_int(active_run.get("startedAt")) or _coerce_optional_int(controller_data.get("startedAt"))
            ended_at = None
            if attempt is None:
                attempt = current_attempt
            if not task_id:
                task_id = current_task_id or _pick_text(active_run.get("task"))
            if not task_title:
                task_title = current_task_title or _pick_text(active_run.get("taskTitle"))

        duration_sec = _coerce_optional_float(
            _pick_value(
                summary.get("durationSec"),
                summary.get("duration_seconds"),
                stage_runtime.get("durationSec"),
                stage_runtime.get("duration_seconds"),
            )
        )
        if duration_sec is None and started_at is not None and ended_at is not None and ended_at >= started_at:
            duration_sec = round((ended_at - started_at) / 1000.0, 3)
        if duration_sec is None and running:
            duration_sec = _coerce_optional_float(_pick_value(active_run.get("elapsedSec"), controller_data.get("elapsedSec"), controller_data.get("elapsed_seconds")))

        output_reason = reason or summary_reason or _pick_text(stage_runtime.get("reason"))
        recent_output = _pick_text(summary.get("recentOutput"), summary.get("recent_output"))
        if not recent_output:
            recent_output = _task_output_excerpt(
                run_dir,
                stage_name=stage_name,
                cycle=cycle_value,
                step=step,
                task_id=task_id or current_task_id,
                attempt=attempt,
                reason=output_reason,
                fallback_text=_pick_text(stage_runtime.get("lastMessage"), stage_runtime.get("lastEvent"), output_reason),
            )

        status = _normalize_lifecycle_status(
            summary_status or stage_runtime.get("status"),
            rc=rc,
            reason=reason,
            running=running,
            has_activity=bool(summary or stage_runtime or task_id or task_title or recent_output),
            default="pending",
        )

        if not summary and not stage_runtime and not running and not recent_output and not task_id and not task_title and not reason and status == "pending":
            continue

        if running:
            status = "running"

        records.append(
            {
                "id": stage_name,
                "label": stage_name,
                "title": task_title or stage_titles.get(stage_name, stage_name),
                "status": status,
                "cycle": cycle_value,
                "startedAt": started_at,
                "endedAt": ended_at,
                "durationSec": duration_sec,
                "model": model,
                "taskId": task_id,
                "taskTitle": task_title,
                "attempt": attempt,
                "step": step,
                "recentOutput": recent_output,
                "reason": reason,
                "rc": rc,
            }
        )

    return records


def _history_item(
    repo: Path,
    run_dir: Path,
    *,
    branch: str,
    completion_level: str = "all",
) -> dict[str, Any]:
    state = load_state(run_dir / "STATE.json")
    backlog = _load_tasks(run_dir)
    backlog_task_ids = load_backlog_task_ids(run_dir / "BACKLOG.json")
    state_counts = count_state_task_ids(state, backlog_task_ids)
    goals = _build_goals_payload(repo, completion_level=completion_level)
    run_summary = _safe_json(run_dir / "run_summary.json", {})
    last_summary = _safe_json(run_dir / "last_run_summary.json", {})

    final = run_summary.get("final") if isinstance(run_summary.get("final"), dict) else {}
    final_reason = _pick_text(final.get("reason"), last_summary.get("reason"), last_summary.get("stop_reason"))
    shutdown_reason = _pick_text(last_summary.get("stop_reason"), final_reason)
    if not final_reason:
        final_reason = shutdown_reason

    tasks_done = state_counts["done"]

    tasks_total = _coerce_optional_int(_pick_value(last_summary.get("total_tasks"), last_summary.get("tasks_total")))
    if tasks_total is None:
        tasks_total = len(backlog)

    tasks_failed = state_counts["failed"]

    tasks_skipped = _coerce_optional_int(_pick_value(last_summary.get("skipped"), last_summary.get("tasks_skipped")))
    if tasks_skipped is None:
        tasks_skipped = 0

    cycle_count = len(run_summary.get("cycles") or [])
    project_completion = _project_completion_status(
        goals,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        tasks_failed=tasks_failed,
    )

    rc = _coerce_optional_int(final.get("rc"))
    if rc is None:
        rc = _coerce_optional_int(last_summary.get("rc"))
    stop_exists = (run_dir / "STOP").exists()
    if not shutdown_reason and stop_exists:
        shutdown_reason = "stop_file"
    if not final_reason and stop_exists:
        final_reason = shutdown_reason
    execution_status = _normalize_execution_status(
        "",
        running=False,
        exit_code=rc,
        final_reason=shutdown_reason,
        stop_file_exists=stop_exists,
        has_run_dir=True,
    )
    status = _normalize_run_status(
        "",
        running=False,
        exit_code=rc,
        final_reason=shutdown_reason,
        stop_file_exists=stop_exists,
        has_run_dir=True,
        project_complete=project_completion["project_complete"],
    )

    started_at = _epoch_ms(run_dir.stat().st_ctime)
    ended_at = _epoch_ms(run_dir.stat().st_mtime)
    duration_sec = _coerce_optional_int(
        _pick_value(
            last_summary.get("duration_seconds"),
            last_summary.get("durationSec"),
            run_summary.get("duration_seconds"),
            run_summary.get("durationSec"),
        )
    )
    if duration_sec is None:
        duration_sec = max(0, int((ended_at - started_at) / 1000)) if started_at and ended_at else 0
    if duration_sec == 0:
        duration_sec = max(0, cycle_count * 60)

    branch_value = _pick_text(run_summary.get("branch"), last_summary.get("branch"), branch, "HEAD")
    worktree_outcome = _history_worktree_outcome(run_dir)

    last_cycle = _tail_text(run_dir / "cycle_summary.log", 1).strip()
    return {
        "id": run_dir.name,
        "startedAt": started_at,
        "endedAt": ended_at,
        "status": status,
        "executionStatus": execution_status,
        "execution_status": execution_status,
        "projectComplete": project_completion["project_complete"],
        "project_complete": project_completion["project_complete"],
        "projectStatus": project_completion["project_status"],
        "project_status": project_completion["project_status"],
        "goalsComplete": project_completion["goals_complete"],
        "goals_complete": project_completion["goals_complete"],
        "backlogComplete": project_completion["backlog_complete"],
        "backlog_complete": project_completion["backlog_complete"],
        "tasksDone": tasks_done,
        "tasksTotal": tasks_total,
        "tasksFailed": tasks_failed,
        "tasksSkipped": tasks_skipped,
        "state_counts": state_counts,
        "taskCounts": {
            "done": tasks_done,
            "failed": tasks_failed,
            "skipped": tasks_skipped,
            "total": tasks_total,
            "cycles": cycle_count,
        },
        "branch": branch_value,
        "durationSec": duration_sec,
        "finalReason": final_reason,
        "shutdownReason": shutdown_reason,
        "stopReason": shutdown_reason,
        "runDir": run_dir.as_posix(),
        "lastCycle": last_cycle,
        "runSummary": run_summary,
        "lastRunSummary": last_summary,
        "worktreeOutcome": worktree_outcome,
    }


def _history_payload(
    repo: Path,
    run_dirs: list[Path],
    *,
    branch: str,
    completion_level: str = "all",
) -> dict[str, Any]:
    items = [_history_item(repo, run_dir, branch=branch, completion_level=completion_level) for run_dir in run_dirs]
    items.sort(key=lambda item: int(item.get("startedAt") or 0), reverse=True)
    successes = len([item for item in items if item["status"] == "success"])
    failures = len([item for item in items if item["status"] == "failed"])
    stopped = len([item for item in items if item["status"] == "stopped"])
    total_tasks = sum(int(item.get("tasksTotal") or 0) for item in items)
    done_tasks = sum(int(item.get("tasksDone") or 0) for item in items)
    failed_tasks = sum(int(item.get("tasksFailed") or 0) for item in items)
    skipped_tasks = sum(int(item.get("tasksSkipped") or 0) for item in items)
    return {
        "items": items,
        "summary": {
            "runs": len(items),
            "successes": successes,
            "failures": failures,
            "stopped": stopped,
            "tasksDone": done_tasks,
            "tasksTotal": total_tasks,
            "tasksFailed": failed_tasks,
            "tasksSkipped": skipped_tasks,
        },
    }


def _run_dirs(repo: Path) -> list[Path]:
    roots = [repo / ".AgentCLI" / "agent_runs", repo / ".doc" / "agent_runs"]
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for candidate in root.iterdir():
            if not candidate.is_dir():
                continue
            has_artifacts = _run_dir_has_observable_artifacts(candidate)
            if not (re.match(r"^\d{8}-\d{6}$", candidate.name) or has_artifacts):
                continue
            if not has_artifacts:
                continue
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)
    found.sort(key=lambda item: item.name, reverse=True)
    return found


def _latest_observable_run_dir(repo: Path) -> Path | None:
    latest = find_latest_run_dir(repo)
    if _run_dir_has_observable_artifacts(latest):
        return latest
    run_dirs = _run_dirs(repo)
    return run_dirs[0] if run_dirs else None


def _controller_run_dir_is_current_or_terminal(controller_data: dict[str, Any]) -> bool:
    if bool(controller_data.get("running")):
        return True
    status = _normalize_execution_status(
        _pick_text(controller_data.get("run_status"), controller_data.get("status")),
        running=bool(controller_data.get("running")),
        exit_code=controller_data.get("exit_code"),
        final_reason=_pick_text(controller_data.get("final_reason"), controller_data.get("reason")),
        stop_file_exists=bool(controller_data.get("stop_file_exists")),
        has_run_dir=bool(controller_data.get("run_dir")),
    )
    if status in {"completed", "stopped", "failed"}:
        return True
    reason = _pick_text(controller_data.get("final_reason"), controller_data.get("reason")).lower()
    terminal_reasons = {
        "project_complete",
        "all_tasks_done",
        "completed",
        "success",
        "ok",
        "prepared_only",
        "stop_file",
        "stop_requested",
        "stopped",
        "user_stop",
        "manual_stop",
        "failed",
        "error",
        "exception",
        "abandoned",
        "abandon_failed",
        "build_failed",
        "test_failed",
        "policy_violation",
        "exhausted_attempts",
    }
    if reason in terminal_reasons:
        return True
    return _coerce_optional_int(controller_data.get("exit_code")) is not None or bool(controller_data.get("stop_file_exists"))


def _resolve_latest_run_dir(
    repo: Path,
    controller_status: dict[str, Any] | None,
    controller: RunnerController | None,
) -> Path | None:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    controller_run_dir_text = _pick_text(controller_data.get("run_dir"), getattr(controller, "run_dir", None))
    if controller_run_dir_text:
        try:
            candidate = Path(controller_run_dir_text).expanduser().resolve()
            if candidate.exists() and candidate.is_dir():
                if _controller_run_dir_is_current_or_terminal(controller_data) or _run_dir_has_observable_artifacts(candidate):
                    return candidate
        except OSError:
            pass
    return _latest_observable_run_dir(repo)


def _build_metrics_payload(
    run_dir: Path | None,
    progress: dict[str, Any],
    controller_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    events = _cycle_events(run_dir)
    cycle_end_events = [event for event in events if str(event.get("event") or event.get("type") or "").strip().lower() == "cycle_end"]
    tokens24h: list[int] = []
    success24h: list[int] = []
    budget: list[float] = []
    last_tokens = {"in": None, "out": None}
    last_stage = ""
    quota = _quota_payload("", None)
    budget_used: float | None = None
    tokens_available = False
    budget_available = False

    for event in cycle_end_events:
        tokens = event.get("tokens") if isinstance(event.get("tokens"), dict) else {}
        total = tokens.get("_total") if isinstance(tokens.get("_total"), dict) else {}
        total_tokens = _coerce_optional_int(total.get("total"))
        if total_tokens is not None:
            tokens24h.append(total_tokens)
            tokens_available = True
        success24h.append(1 if int(event.get("rc") or 0) == 0 else 0)
        event_budget = _coerce_optional_float(
            _pick_value(event.get("budget"), event.get("budget_used"), event.get("budgetUsed"))
        )
        if event_budget is not None:
            budget.append(round(max(0.0, min(1.0, event_budget)), 3))
            budget_available = True
            budget_used = max(budget_used or 0.0, event_budget)
        input_tokens = _coerce_optional_int(total.get("input"))
        output_tokens = _coerce_optional_int(total.get("output"))
        if input_tokens is not None or output_tokens is not None:
            last_tokens = {
                "in": input_tokens,
                "out": output_tokens,
            }
            tokens_available = True
        last_stage = str(event.get("stage") or event.get("name") or "").strip() or last_stage
        event_quota = _quota_payload_from_source(event)
        if event_quota.get("available"):
            quota = event_quota
    controller_tokens = controller_data.get("tokens") if isinstance(controller_data.get("tokens"), dict) else {}
    input_tokens = _coerce_optional_int(controller_tokens.get("in"))
    output_tokens = _coerce_optional_int(controller_tokens.get("out"))
    if input_tokens is not None or output_tokens is not None:
        last_tokens = {
            "in": input_tokens,
            "out": output_tokens,
        }
        tokens_available = True

    controller_quota = _quota_payload_from_source(controller_data)
    if controller_quota.get("available"):
        quota = controller_quota

    controller_budget = _coerce_optional_float(_pick_value(controller_data.get("budget_used"), controller_data.get("budgetUsed")))
    if controller_budget is not None:
        budget_used = controller_budget
        budget_available = True
        if not budget:
            budget.append(round(max(0.0, min(1.0, controller_budget)), 3))
    last_stage = _pick_text(controller_data.get("last_stage"), controller_data.get("stage"), last_stage)
    quota_available = bool(quota.get("available"))
    quota_window = str(quota.get("window") or "")
    quota_used = quota.get("used")

    return {
        "tokens24h": tokens24h,
        "success24h": success24h,
        "budget": budget,
        "tokens": last_tokens,
        "tokens_available": tokens_available,
        "budget_available": budget_available,
        "quota_available": quota_available,
        "quotaAvailable": quota_available,
        "quota_window": quota.get("window"),
        "quotaWindow": quota.get("window"),
        "last_stage": last_stage,
        "quota_used": quota.get("used"),
        "quotaUsed": quota.get("used"),
        "budget_used": budget_used,
        "quota": quota,
    }


def _build_progress_payload(
    *,
    repo: Path,
    run_dir: Path | None,
    config: dict[str, Any],
    branch: str,
    controller_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    state = load_state(run_dir / "STATE.json") if run_dir else {"done": [], "failed": [], "warnings": []}
    backlog = _load_backlog_payload(run_dir, state, current_task_id=_pick_text(controller_data.get("current_task_id"), controller_data.get("task_id"), controller_data.get("task")), events=events)
    backlog_task_ids = load_backlog_task_ids(run_dir / "BACKLOG.json") if run_dir else set()
    state_counts = count_state_task_ids(state, backlog_task_ids)
    completion_level = resolve_goals_completion_level(config.get("goals_completion_level"))
    goals = _build_goals_payload(repo, completion_level=completion_level)
    backlog_items = backlog["items"]
    tasks_total = len(backlog_items)
    tasks_done = state_counts["done"]
    tasks_failed = state_counts["failed"]
    project_completion = _project_completion_status(
        goals,
        tasks_total=tasks_total,
        tasks_done=tasks_done,
        tasks_failed=tasks_failed,
    )
    run_summary = _safe_json(run_dir / "run_summary.json", {}) if run_dir else {}
    last_run_summary = _safe_json(run_dir / "last_run_summary.json", {}) if run_dir else {}
    final = run_summary.get("final") if isinstance(run_summary.get("final"), dict) else {}
    final_reason = _pick_text(
        controller_data.get("final_reason"),
        controller_data.get("reason"),
        final.get("reason") if isinstance(final, dict) else "",
        last_run_summary.get("stop_reason"),
    )
    final_rc = _coerce_optional_int(controller_data.get("exit_code"))
    if final_rc is None:
        final_rc = _coerce_optional_int(final.get("rc") if isinstance(final, dict) else None)
    if final_rc is None:
        final_rc = _coerce_optional_int(last_run_summary.get("rc") if isinstance(last_run_summary, dict) else None)
    stop_file_exists = bool(run_dir and (run_dir / "STOP").exists())
    execution_status = _normalize_execution_status(
        _pick_text(
            controller_data.get("run_status"),
            controller_data.get("status"),
            final.get("status") if isinstance(final, dict) else "",
            last_run_summary.get("status"),
        ),
        running=bool(controller_data.get("running")),
        exit_code=final_rc,
        final_reason=final_reason,
        stop_file_exists=stop_file_exists,
        has_run_dir=bool(run_dir),
    )
    run_status = _normalize_run_status(
        _pick_text(
            controller_data.get("run_status"),
            controller_data.get("status"),
            final.get("status") if isinstance(final, dict) else "",
            last_run_summary.get("status"),
        ),
        running=bool(controller_data.get("running")),
        exit_code=final_rc,
        final_reason=final_reason,
        stop_file_exists=stop_file_exists,
        has_run_dir=bool(run_dir),
        project_complete=project_completion["project_complete"],
    )
    current_task = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        backlog["selected_id"],
    )
    if not current_task:
        current_task = backlog["selected_id"] if backlog["selected_id"] else ""
    current_task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
    )
    if not current_task_title and current_task:
        current_task_title = next((item["title"] for item in backlog_items if item["id"] == current_task), "")
    progress_value = _coerce_optional_float(
        _pick_value(
            controller_data.get("progress"),
            controller_data.get("progress_ratio"),
            controller_data.get("progressValue"),
            run_summary.get("progress"),
        )
    )
    progress_available = progress_value is not None
    attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            run_summary.get("attempt"),
            last_run_summary.get("attempt"),
        )
    )
    worktree_mode = _pick_text(controller_data.get("worktree_mode"), controller_data.get("worktreeMode"))

    return {
        "latest_run_dir": run_dir.as_posix() if run_dir else None,
        "run_status": run_status,
        "tasks_done": tasks_done,
        "tasks_total": tasks_total,
        "tasks_failed": tasks_failed,
        "state_counts": state_counts,
        "progress": round(progress_value, 3) if progress_value is not None else None,
        "progress_available": progress_available,
        "execution_status": execution_status,
        "executionStatus": execution_status,
        "project_complete": project_completion["project_complete"],
        "projectComplete": project_completion["project_complete"],
        "project_status": project_completion["project_status"],
        "projectStatus": project_completion["project_status"],
        "goals_complete": project_completion["goals_complete"],
        "goalsComplete": project_completion["goals_complete"],
        "backlog_complete": project_completion["backlog_complete"],
        "backlogComplete": project_completion["backlog_complete"],
        "current_task_id": current_task,
        "current_task_title": current_task_title,
        "attempt": attempt,
        "worktree_mode": worktree_mode,
        "iterations": len(run_summary.get("cycles") or []),
        "goals": goals,
        "backlog": backlog,
        "state": state,
        "final_reason": final_reason,
        "final_rc": final_rc,
    }


def _build_worktree_payload(repo: Path, run_dir: Path | None, branch: str) -> dict[str, Any]:
    repo_root = _repo_root(repo)
    run_dir_value = run_dir.as_posix() if run_dir else ""
    source_branch = branch or "HEAD"
    pending_path = find_pending_worktree_merge(repo_root, run_dir)
    if pending_path is not None:
        try:
            raw_payload = read_pending_worktree_merge(pending_path)
            if not isinstance(raw_payload, dict):
                raise TypeError("Pending merge payload must be a JSON object.")
        except Exception as ex:
            error_message = f"Pending worktree merge file is malformed: {str(ex).strip() or ex.__class__.__name__}"
            payload = {}
            if pending_path.exists():
                try:
                    payload = _safe_json(pending_path, {})
                except Exception:
                    payload = {}
                if not isinstance(payload, dict):
                    payload = {}
            invalid = _worktree_default_payload(repo_root, run_dir, branch)
            invalid.update(
                {
                    "status": "error",
                    "reviewRequired": True,
                    "reviewRequiredMessage": error_message,
                    "sourceRepo": str(payload.get("source_repo") or payload.get("sourceRepo") or invalid["sourceRepo"]).strip() or invalid["sourceRepo"],
                    "sourceBranch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "branch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "baseRef": str(payload.get("base_ref") or payload.get("baseRef") or "").strip(),
                    "headRef": str(payload.get("head_ref") or payload.get("headRef") or "").strip(),
                    "worktreeDir": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "worktree": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "patchPath": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "patch": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "pendingFile": pending_path.as_posix(),
                    "statusFile": pending_path.as_posix(),
                    "cleanupPath": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "cleanupMessage": "Cleanup state is unavailable until the marker is repaired.",
                    "cleanupState": "none",
                    "summary": "Pending worktree merge file is malformed.",
                    "risk": "Fix or delete the pending merge file before applying any source-repo change.",
                    "changedFiles": [],
                    "runDir": str(payload.get("run_dir") or payload.get("runDir") or run_dir_value or "").strip(),
                    "runnerRc": 0,
                    "lastRc": 0,
                }
            )
            return invalid

        payload = raw_payload
        stale_reason = _worktree_pending_is_stale(payload, pending_path)
        if stale_reason:
            invalid = _worktree_default_payload(repo_root, run_dir, branch)
            invalid.update(
                {
                    "status": "error",
                    "reviewRequired": True,
                    "reviewRequiredMessage": f"Pending worktree merge file is stale: {stale_reason}",
                    "sourceRepo": str(payload.get("source_repo") or payload.get("sourceRepo") or invalid["sourceRepo"]).strip() or invalid["sourceRepo"],
                    "sourceBranch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "branch": str(payload.get("base_ref") or payload.get("baseRef") or source_branch).strip() or source_branch,
                    "baseRef": str(payload.get("base_ref") or payload.get("baseRef") or "").strip(),
                    "headRef": str(payload.get("head_ref") or payload.get("headRef") or "").strip(),
                    "worktreeDir": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "worktree": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "patchPath": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "patch": str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip(),
                    "pendingFile": pending_path.as_posix(),
                    "statusFile": pending_path.as_posix(),
                    "cleanupPath": str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip(),
                    "cleanupMessage": "Cleanup state is unavailable until the marker is repaired.",
                    "cleanupState": "none",
                    "summary": "Pending worktree merge file is stale.",
                    "risk": "Fix or delete the stale pending merge file before applying any source-repo change.",
                    "changedFiles": [],
                    "runDir": str(payload.get("run_dir") or payload.get("runDir") or run_dir_value or "").strip(),
                    "runnerRc": 0,
                    "lastRc": 0,
                }
            )
            return invalid

        return _worktree_status_payload(
            repo_root,
            run_dir,
            branch,
            status="pending review",
            artifact_path=pending_path,
            payload=payload,
            pending_path=pending_path,
        )

    artifact_candidates = [
        (artifact_status, artifact_path)
        for artifact_status, artifact_path in _worktree_status_artifacts(repo_root, run_dir)
        if artifact_status != "pending" and artifact_path.exists()
    ]
    selected_artifact = _worktree_select_artifact(artifact_candidates)
    if selected_artifact is not None:
        artifact_status, artifact_path = selected_artifact
        if artifact_status in {"applied", "discarded", "applied_cleanup_failed", "discard_cleanup_failed"}:
            payload = _safe_json(artifact_path, {})
            if not isinstance(payload, dict):
                payload = {}
            return _worktree_status_payload(
                repo_root,
                run_dir,
                branch,
                status=artifact_status,
                artifact_path=artifact_path,
                payload=payload,
                pending_path=artifact_path.with_name("WORKTREE_MERGE_PENDING.json"),
            )
        if artifact_status in {"apply_failed", "patch_not_applied", "not_applied"}:
            payload = _worktree_default_payload(repo_root, run_dir, branch)
            payload.update(
                {
                    "status": artifact_status,
                    "reviewRequired": True,
                    "reviewRequiredMessage": {
                        "apply_failed": "Worktree patch export failed.",
                        "patch_not_applied": "Worktree patch was exported but not auto-applied.",
                        "not_applied": "Worktree patch was not applied.",
                    }[artifact_status],
                    "summary": {
                        "apply_failed": "Worktree patch export failed.",
                        "patch_not_applied": "Worktree patch was exported but not auto-applied.",
                        "not_applied": "Worktree patch was not applied.",
                    }[artifact_status],
                    "risk": {
                        "apply_failed": "Manual recovery is required before the source repository can be reviewed.",
                        "patch_not_applied": "Review the exported patch before applying it manually.",
                        "not_applied": "Review the exported patch and apply it manually when ready.",
                    }[artifact_status],
                    "cleanupPath": "",
                    "cleanupMessage": "Cleanup state is unavailable because no merge marker was written.",
                    "cleanupDetails": {},
                    "cleanupAttempts": [],
                    "cleanupState": "none",
                    "statusFile": artifact_path.as_posix(),
                    "pendingFile": "",
                    "changedFiles": [],
                    "runDir": run_dir.as_posix() if run_dir else "",
                }
            )
            return payload

    return _worktree_default_payload(repo_root, run_dir, branch)


def build_snapshot(
    repo: Path | str | None = None,
    *,
    config_path: str | None = None,
    bind_host: str = "127.0.0.1",
    trusted_network: bool | None = None,
    runner_controller: RunnerController | None = None,
    runner_controls_enabled: bool | None = None,
    runner_controls_source: str | None = None,
    runner_controls_disabled_reason: str = "",
    runner_control_busy: bool = False,
    runner_control_last_action: str = "",
    runner_control_last_message: str = "",
    runner_control_last_error: str = "",
    runner_controller_auto_build: bool = True,
) -> dict[str, Any]:
    repo_root = _repo_root(repo)
    cfg_path, cfg, cfg_source = _load_config_payload(repo_root, config_path)
    prompts_dir = resolve_prompts_dir(repo_root, str(cfg.get("prompts_dir") or ""))
    if not prompts_dir:
        prompts_dir = default_prompts_dir(repo_root)
    if runner_controls_enabled is None and runner_controls_source is None and not runner_controls_disabled_reason:
        control_enabled, resolved_source, resolved_disabled_reason = _resolve_runner_controls_enabled(
            None,
            bind_host=bind_host,
            trusted_network=trusted_network,
        )
    else:
        control_enabled = bool(runner_controls_enabled)
        resolved_source = runner_controls_source or ("cli" if control_enabled else "default")
        resolved_disabled_reason = runner_controls_disabled_reason or (
            "" if control_enabled else "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls."
        )
    redaction_active = _web_redaction_active(bind_host)
    config_contract = _build_config_contract(
        repo_root,
        cfg,
        cfg_path,
        cfg_source,
        prompts_dir,
        save_enabled=control_enabled,
        save_endpoint="/api/config/save",
        save_requires_opt_in=True,
    )
    profile = _prompt_profile(cfg)
    goals_completion_level = resolve_goals_completion_level(cfg.get("goals_completion_level"))
    goals = _build_goals_payload(repo_root, completion_level=goals_completion_level)
    prompt_items = _load_prompt_items(repo_root, prompts_dir, profile=profile)
    branch = _branch_name(repo_root)
    controller = runner_controller
    if controller is None and runner_controller_auto_build:
        controller = _build_runner_controller(repo_root, cfg, cfg_path)
    controller_status = _controller_status_payload(controller)
    latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
    logs_events = _cycle_events(latest_run_dir)
    progress = _build_progress_payload(
        repo=repo_root,
        run_dir=latest_run_dir,
        config=cfg,
        branch=branch,
        controller_status=controller_status,
        events=logs_events,
    )
    state = progress["state"] if isinstance(progress.get("state"), dict) else {"done": [], "failed": [], "warnings": []}
    backlog = progress["backlog"] if isinstance(progress.get("backlog"), dict) else {"items": [], "counts": {}, "selected_id": ""}
    run_summary = _safe_json(latest_run_dir / "run_summary.json", {}) if latest_run_dir else {}
    last_run_summary = _safe_json(latest_run_dir / "last_run_summary.json", {}) if latest_run_dir else {}
    log_entries = _load_log_entries(latest_run_dir)
    metrics = _build_metrics_payload(latest_run_dir, progress, controller_status=controller_status)
    active_run = _active_run_payload(
        repo=repo_root,
        run_dir=latest_run_dir,
        config=cfg,
        run_summary=run_summary,
        last_run_summary=last_run_summary,
        state=state,
        backlog=backlog.get("items", []),
        progress=progress,
        metrics=metrics,
        branch=branch,
        controller_status=controller_status,
    )
    active_run_quota = active_run.get("quota") if isinstance(active_run.get("quota"), dict) else {}
    metrics_quota = metrics.get("quota") if isinstance(metrics.get("quota"), dict) else {}
    if bool(active_run_quota.get("available")) and metrics_quota != active_run_quota:
        quota_window = str(active_run_quota.get("window") or "")
        quota_used = active_run_quota.get("used")
        metrics = dict(metrics)
        metrics["quota"] = dict(active_run_quota)
        metrics["quota_available"] = True
        metrics["quotaAvailable"] = True
        metrics["quota_window"] = quota_window
        metrics["quotaWindow"] = quota_window
        metrics["quota_used"] = quota_used
        metrics["quotaUsed"] = quota_used
    state_counts = progress.get("state_counts") if isinstance(progress.get("state_counts"), dict) else {
        "done": 0,
        "failed": 0,
        "warnings": 0,
    }
    stages = _stage_payload(repo_root, active_run, progress, cfg, run_dir=latest_run_dir, run_summary=run_summary, last_run_summary=last_run_summary, controller_status=controller_status, events=logs_events)
    history = _history_payload(
        repo_root,
        _run_dirs(repo_root),
        branch=branch,
        completion_level=resolve_goals_completion_level(cfg.get("goals_completion_level")),
    )
    notifications = _build_notifications(
        run_id=active_run["id"],
        started_at_ms=int(active_run.get("startedAt") or 0),
        branch=active_run.get("branch") or branch,
        active_status=str(progress.get("run_status") or "idle"),
        state=state,
        backlog=backlog.get("items", []),
        events=logs_events,
        final_reason=str(progress.get("final_reason") or ""),
    )
    worktree = _build_worktree_payload(repo_root, latest_run_dir, branch=branch)
    worktree_diagnostics = scan_worktree_diagnostics(repo_root)
    log_tail = _tail_text((latest_run_dir / "cycle_summary.log") if latest_run_dir else Path(""), 80)
    log_files = {
        "cycle_summary": (latest_run_dir / "cycle_summary.log").as_posix() if latest_run_dir else "",
        "run_log": (latest_run_dir / "logs" / "run.log").as_posix() if latest_run_dir else "",
        "metrics": (latest_run_dir / "metrics.jsonl").as_posix() if latest_run_dir else "",
    }
    runner_control = _runner_control_payload(
        controller,
        repo=repo_root,
        enabled=control_enabled,
        source=runner_controls_source or resolved_source,
        disabled_reason=resolved_disabled_reason,
        config_path=cfg_path.as_posix(),
        current_run_dir=latest_run_dir.as_posix() if latest_run_dir else "",
        last_action=runner_control_last_action,
        last_message=runner_control_last_message,
        last_error=runner_control_last_error,
        run_status=str(progress.get("run_status") or "idle"),
        execution_status=str(progress.get("execution_status") or progress.get("executionStatus") or ""),
        project_complete=bool(progress.get("project_complete", False)),
        project_status=str(progress.get("project_status") or progress.get("projectStatus") or ""),
        goals_complete=bool(progress.get("goals_complete", False)),
        backlog_complete=bool(progress.get("backlog_complete", False)),
        busy=bool(runner_control_busy),
        cfg=cfg,
        cfg_path=cfg_path,
        redact_sensitive=redaction_active,
    )
    goals = _web_apply_redaction(goals, active=redaction_active, redactor=_redact_web_goals_payload)
    backlog = _web_apply_redaction(backlog, active=redaction_active, redactor=_redact_web_backlog_payload)
    progress = dict(progress)
    progress["goals"] = goals
    progress["backlog"] = backlog
    log_payload = _web_apply_redaction(
        {"entries": log_entries, "tail": log_tail, "files": log_files},
        active=redaction_active,
        redactor=_redact_web_log_payload,
    )
    logs_redaction = log_payload.get("redaction") if isinstance(log_payload, dict) else {}
    log_entries = list(log_payload.get("entries") or []) if isinstance(log_payload, dict) else list(log_entries)
    log_tail = str(log_payload.get("tail") or "") if isinstance(log_payload, dict) else log_tail
    log_files = dict(log_payload.get("files") or log_files) if isinstance(log_payload, dict) else log_files
    stages = _web_apply_redaction(stages, active=redaction_active, redactor=_redact_web_stages_payload)
    config_payload = _web_apply_redaction(
        {
            "path": cfg_path.as_posix(),
            "source": cfg_source,
            "data": _redact_config(cfg),
            "resolved_prompts_dir": prompts_dir.as_posix(),
            "meta": {
                "path": cfg_path.as_posix(),
                "source": cfg_source,
                "resolved_prompts_dir": prompts_dir.as_posix(),
            },
        },
        active=redaction_active,
        redactor=_redact_web_config_payload,
    )
    config_contract = _web_apply_redaction(config_contract, active=redaction_active, redactor=_redact_web_config_contract)
    prompt_payload = _web_apply_redaction({"dir": prompts_dir.as_posix(), "items": prompt_items}, active=redaction_active, redactor=_redact_web_prompts_payload)
    prompts_redaction = prompt_payload.get("redaction") if isinstance(prompt_payload, dict) else {}
    prompt_items = list(prompt_payload.get("items") or []) if isinstance(prompt_payload, dict) else list(prompt_items)
    notifications = _web_apply_redaction(notifications, active=redaction_active, redactor=_redact_web_notifications_payload)
    history = _web_apply_redaction(history, active=redaction_active, redactor=_redact_web_history_payload)
    runner_control = _web_apply_redaction(runner_control, active=redaction_active, redactor=lambda value: _redact_web_runner_control(value, redact_start_options=True))
    if redaction_active:
        progress["redaction"] = _web_redaction_meta("goals", "backlog")
    else:
        logs_redaction = {}
        prompts_redaction = {}
    active_run_empty = active_run["status"] == "idle" and not active_run.get("task") and not active_run.get("startedAt")
    runner_control_status = runner_control.get("status") if isinstance(runner_control.get("status"), dict) else {}
    runner_control_status_reason = str(runner_control_status.get("reason") or "").strip()
    if runner_control.get("busy"):
        runner_control_state = "busy"
    elif runner_control.get("last_error") or runner_control_status_reason.startswith("status_error:") or not runner_control.get("controller_available"):
        runner_control_state = "error"
    elif not runner_control.get("enabled"):
        runner_control_state = "disabled"
    elif runner_control.get("last_message"):
        runner_control_state = "success"
    else:
        runner_control_state = "ready"
    goals_summary = goals.get("summary") if isinstance(goals.get("summary"), dict) else {}
    goals_total = int(goals_summary.get("total") or 0)
    if not goals_total:
        goals_items = goals.get("items") if isinstance(goals.get("items"), dict) else {}
        goals_total = sum(len(items) for items in goals_items.values()) if isinstance(goals_items, dict) else 0
    has_metrics = bool(
        metrics.get("tokens24h")
        or metrics.get("success24h")
        or metrics.get("budget")
        or metrics.get("last_stage")
        or metrics.get("quota_used") is not None
        or metrics.get("budget_used") is not None
        or metrics.get("tokens_available")
        or metrics.get("budget_available")
        or metrics.get("quota_available")
    )
    active_run_section_state = buildSectionState("activeRun", "empty" if active_run_empty else "ready", fallbackSectionMessage("activeRun") if active_run_empty else "")
    stages_section_state = buildSectionState(
        "stages",
        "ready" if len(stages) >= 3 else ("partial" if stages else "empty"),
        "" if len(stages) >= 3 else ("Only some lifecycle records were published." if stages else fallbackSectionMessage("stages")),
    )
    backlog_section_state = buildSectionState("backlog", "ready" if backlog.get("items") else "empty", "" if backlog.get("items") else fallbackSectionMessage("backlog"))
    goals_section_state = buildSectionState("goals", "ready" if goals_total else "empty", "" if goals_total else fallbackSectionMessage("goals"))
    config_section_state = buildSectionState("config", "ready" if config_contract.get("schema") else "empty", "")
    prompts_section_state = buildSectionState("prompts", "ready" if prompt_items else "empty", "" if prompt_items else fallbackSectionMessage("prompts"))
    logs_section_state = buildSectionState("logs", "ready" if log_entries else "empty", "" if log_entries else fallbackSectionMessage("logs"))
    notifications_section_state = buildSectionState("notifications", "ready" if notifications else "empty", "" if notifications else fallbackSectionMessage("notifications"))
    metrics_section_state = buildSectionState("metrics", "ready" if has_metrics else "empty", "" if has_metrics else fallbackSectionMessage("metrics"))
    history_section_state = buildSectionState("history", "ready" if history.get("items") else "empty", "" if history.get("items") else fallbackSectionMessage("history"))
    worktree_status = str(worktree.get("status") or "none").strip()
    if worktree_status == "none":
        worktree_section_status = "empty"
    elif worktree_status == "error":
        worktree_section_status = "error"
    elif worktree_status in {"applied", "discarded"}:
        worktree_section_status = "ready"
    else:
        worktree_section_status = "partial"
    worktree_section_message = (
        worktree.get("reviewRequiredMessage")
        or worktree.get("cleanupMessage")
        or worktree.get("summary")
        or (fallbackSectionMessage("worktree") if worktree_status == "none" else "")
    )
    worktree_section_state = buildSectionState("worktree", worktree_section_status, worktree_section_message)
    runner_control_section_state = buildSectionState(
        "runnerControl",
        runner_control_state,
        runner_control.get("message") or fallbackSectionMessage("runnerControl"),
    )

    return {
        "ok": True,
        "repo": {
            "path": repo_root.as_posix(),
            "name": repo_root.name or repo_root.as_posix().rsplit("/", 1)[-1],
            "head": _git_head_short(repo_root),
            "branch": branch or "HEAD",
        },
        "latest_run_dir": latest_run_dir.as_posix() if latest_run_dir else None,
        "active_run": active_run,
        "stages": stages,
        "backlog": backlog,
        "goals": goals,
        "logs": {
            "entries": log_entries,
            "tail": log_tail,
            "files": log_files,
            "redaction": logs_redaction,
        },
        "config": config_payload,
        "config_contract": config_contract,
        "prompts": {
            "dir": prompt_payload.get("dir", prompts_dir.as_posix()) if isinstance(prompt_payload, dict) else prompts_dir.as_posix(),
            "exists": prompts_dir.exists(),
            "items": prompt_items,
            "redaction": prompts_redaction,
        },
        "history": history,
        "metrics": metrics,
        "notifications": notifications,
        "worktree": worktree,
        "worktree_diagnostics": worktree_diagnostics,
        "runner_control": runner_control,
        "redaction": {
            "active": redaction_active,
            "placeholder": REDACTED_VALUE,
            "scope": "lan" if redaction_active else "local",
        },
        "progress": {
            "latest_run_dir": latest_run_dir.as_posix() if latest_run_dir else None,
            "run_status": progress.get("run_status"),
            "tasks_done": progress.get("tasks_done", 0),
            "tasks_total": progress.get("tasks_total", 0),
            "tasks_failed": progress.get("tasks_failed", 0),
            "state_counts": state_counts,
            "stateCounts": state_counts,
            "progress": progress.get("progress"),
            "progress_available": progress.get("progress_available", False),
            "current_task_id": progress.get("current_task_id", ""),
            "current_task_title": progress.get("current_task_title", ""),
            "attempt": progress.get("attempt"),
            "worktree_mode": progress.get("worktree_mode", ""),
            "execution_status": progress.get("execution_status", ""),
            "executionStatus": progress.get("executionStatus", progress.get("execution_status", "")),
            "project_complete": bool(progress.get("project_complete", False)),
            "projectComplete": bool(progress.get("projectComplete", progress.get("project_complete", False))),
            "project_status": progress.get("project_status", ""),
            "projectStatus": progress.get("projectStatus", progress.get("project_status", "")),
            "goals_complete": bool(progress.get("goals_complete", False)),
            "goalsComplete": bool(progress.get("goalsComplete", progress.get("goals_complete", False))),
            "backlog_complete": bool(progress.get("backlog_complete", False)),
            "backlogComplete": bool(progress.get("backlogComplete", progress.get("backlog_complete", False))),
            "goals": progress.get("goals", {}),
            "backlog": backlog,
            "final_reason": progress.get("final_reason", ""),
            "final_rc": progress.get("final_rc"),
            "state": state,
        },
        "execution_status": progress.get("execution_status", ""),
        "executionStatus": progress.get("executionStatus", progress.get("execution_status", "")),
        "project_complete": bool(progress.get("project_complete", False)),
        "projectComplete": bool(progress.get("projectComplete", progress.get("project_complete", False))),
        "project_status": progress.get("project_status", ""),
        "projectStatus": progress.get("projectStatus", progress.get("project_status", "")),
        "goals_complete": bool(progress.get("goals_complete", False)),
        "goalsComplete": bool(progress.get("goalsComplete", progress.get("goals_complete", False))),
        "backlog_complete": bool(progress.get("backlog_complete", False)),
        "backlogComplete": bool(progress.get("backlogComplete", progress.get("backlog_complete", False))),
        "sectionState": {
            "activeRun": active_run_section_state,
            "stages": stages_section_state,
            "backlog": backlog_section_state,
            "goals": goals_section_state,
            "config": config_section_state,
            "prompts": prompts_section_state,
            "logs": logs_section_state,
            "notifications": notifications_section_state,
            "metrics": metrics_section_state,
            "history": history_section_state,
            "worktree": worktree_section_state,
            "runnerControl": runner_control_section_state,
        },
        "run_summary": run_summary,
        "last_run_summary": last_run_summary,
    }


def build_health(repo: Path | str | None = None) -> dict[str, Any]:
    snapshot = build_snapshot(repo)
    progress = snapshot.get("progress", {}) if isinstance(snapshot.get("progress"), dict) else {}
    runner_control = snapshot.get("runner_control", {}) if isinstance(snapshot.get("runner_control"), dict) else {}
    return {
        "ok": bool(snapshot.get("ok", False)),
        "repo": snapshot.get("repo", {}),
        "latest_run_dir": snapshot.get("latest_run_dir"),
        "status": progress.get("run_status", "idle"),
        "runner_control": runner_control,
        "timestamp": now_iso(),
    }


def _ensure_fastapi() -> None:
    if FastAPI is None or FileResponse is None:
        raise RuntimeError("FastAPI is not installed. Add the declared dependencies before serving the web console.")


def _resolve_web_dir(web_dir: Path | str | None) -> Path:
    if web_dir is not None and str(web_dir).strip():
        return Path(web_dir).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "web_console"


def create_app(
    repo: Path | str | None = None,
    *,
    web_dir: Path | str | None = None,
    config_path: str | None = None,
    bind_host: str = "127.0.0.1",
    trusted_network: bool | None = None,
    enable_runner_controls: bool | None = None,
) -> Any:
    _ensure_fastapi()
    try:
        init_process_guard()
    except Exception:
        pass
    repo_root = _repo_root(repo)
    static_root = _resolve_web_dir(web_dir)
    cfg_path, cfg, _ = _load_config_payload(repo_root, config_path)
    controller = _build_runner_controller(repo_root, cfg, cfg_path)

    controls_enabled, controls_source, controls_disabled_reason = _resolve_runner_controls_enabled(
        enable_runner_controls,
        bind_host=bind_host,
        trusted_network=trusted_network,
    )
    control_state: dict[str, str] = {
        "last_action": "",
        "last_message": "",
        "last_error": "",
    }
    control_lock = threading.Lock()

    def _shutdown_process_guard() -> None:
        try:
            if controller is not None:
                status = controller.status()
                if isinstance(status, dict) and bool(status.get("running")):
                    controller.stop(wait=True)
        except Exception:
            pass
        try:
            terminate_all_children()
        except Exception:
            pass

    @asynccontextmanager
    async def _lifespan(_app: Any) -> Any:
        try:
            yield
        finally:
            _shutdown_process_guard()

    app = FastAPI(
        title="AgentCLI Web Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=_lifespan,
    )
    app.state.repo = repo_root
    app.state.web_dir = static_root
    app.state.config_path = config_path
    app.state.bind_host = bind_host
    app.state.trusted_network = trusted_network
    app.state.runner_controller = controller
    app.state.runner_controls_enabled = controls_enabled
    app.state.runner_controls_source = controls_source
    app.state.runner_controls_disabled_reason = controls_disabled_reason
    app.state.runner_control_lock = control_lock
    web_redaction_active = _web_redaction_active(bind_host)

    def _snapshot(*, busy_override: bool | None = None) -> dict[str, Any]:
        return build_snapshot(
            repo_root,
            config_path=config_path,
            bind_host=bind_host,
            trusted_network=trusted_network,
            runner_controller=controller,
            runner_controller_auto_build=controller is not None,
            runner_controls_enabled=controls_enabled,
            runner_controls_source=controls_source,
            runner_controls_disabled_reason=controls_disabled_reason,
            runner_control_busy=control_lock.locked() if busy_override is None else bool(busy_override),
            runner_control_last_action=control_state["last_action"],
            runner_control_last_message=control_state["last_message"],
            runner_control_last_error=control_state["last_error"],
        )

    def _section(name: str) -> Any:
        return _snapshot()[name]

    def _goals() -> dict[str, Any]:
        completion_level = resolve_goals_completion_level(cfg.get("goals_completion_level"))
        payload = _build_goals_payload(repo_root, completion_level=completion_level)
        return _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_goals_payload)

    def _runner_control_snapshot() -> dict[str, Any]:
        snap = _snapshot()
        control = snap.get("runner_control", {})
        progress = snap.get("progress", {}) if isinstance(snap.get("progress"), dict) else {}
        return {
            "ok": bool(snap.get("ok", False)),
            "repo": snap.get("repo", {}),
            "latest_run_dir": snap.get("latest_run_dir"),
            "progress": progress,
            "runner_control": control,
            "source": control.get("source", ""),
            "enabled": bool(control.get("enabled")),
            "controller_available": bool(control.get("controller_available")),
            "busy": bool(control.get("busy")),
            "run_status": control.get("run_status", ""),
            "execution_status": control.get("execution_status", progress.get("execution_status", "")),
            "executionStatus": control.get("execution_status", progress.get("executionStatus", progress.get("execution_status", ""))),
            "project_complete": bool(control.get("project_complete", progress.get("project_complete", False))),
            "projectComplete": bool(control.get("project_complete", progress.get("projectComplete", progress.get("project_complete", False)))),
            "project_status": control.get("project_status", progress.get("project_status", "")),
            "projectStatus": control.get("project_status", progress.get("projectStatus", progress.get("project_status", ""))),
            "goals_complete": bool(control.get("goals_complete", progress.get("goals_complete", False))),
            "goalsComplete": bool(control.get("goals_complete", progress.get("goalsComplete", progress.get("goals_complete", False)))),
            "backlog_complete": bool(control.get("backlog_complete", progress.get("backlog_complete", False))),
            "backlogComplete": bool(control.get("backlog_complete", progress.get("backlogComplete", progress.get("backlog_complete", False)))),
            "status": control.get("status", {}),
            "message": control.get("message", ""),
            "actions": control.get("actions", {}),
            "confirmation": control.get("confirmation", {}),
            "last_action": control.get("last_action", ""),
            "last_message": control.get("last_message", ""),
            "last_error": control.get("last_error", ""),
        }

    def _runner_control_response(
        *,
        action: str,
        status_code: int,
        ok: bool,
        status: str,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        busy_override: bool | None = None,
        redact_sensitive: bool | None = None,
    ) -> Any:
        if redact_sensitive is None:
            redact_sensitive = web_redaction_active
        snapshot = _snapshot(busy_override=busy_override)
        safe_message = message

        def _redact_nested(value: Any) -> Any:
            if isinstance(value, dict):
                redacted_value: dict[str, Any] = {}
                for key, item in value.items():
                    key_text = str(key)
                    if key_text in {"config_path", "configPath"}:
                        redacted_value[key] = REDACTED_VALUE if item not in (None, "", False) else item
                    elif key_text in {"start_options", "startOptions"} and isinstance(item, dict):
                        redacted_value[key] = _redact_web_runner_start_options(item)
                    else:
                        redacted_value[key] = _redact_nested(item)
                return redacted_value
            if isinstance(value, list):
                return [_redact_nested(item) for item in value]
            return value

        payload: dict[str, Any] = {
            "ok": ok,
            "action": action,
            "status": status,
            "message": safe_message,
            "runner_control": snapshot.get("runner_control", {}),
            "snapshot": snapshot,
            "repo": snapshot.get("repo", {}),
            "latest_run_dir": snapshot.get("latest_run_dir"),
            "progress": snapshot.get("progress", {}),
        }
        if error_code:
            payload["error"] = {
                "code": error_code,
                "message": safe_message,
            }
        if not ok and details is not None:
            if "error" not in payload:
                payload["error"] = {
                    "code": error_code or "runner_control_error",
                    "message": safe_message,
                }
            payload["error"]["details"] = _redact_nested(details) if redact_sensitive else details
        if result is not None:
            payload["result"] = _redact_nested(result) if redact_sensitive and not ok else (_redact_web_runner_result(result) if redact_sensitive and isinstance(result, dict) else result)
        return JSONResponse(status_code=status_code, content=payload)

    def _runner_control_disabled(action: str) -> Any:
        control = _runner_control_snapshot().get("runner_control", {})
        message = str(control.get("message") or "Runner controls are disabled.")
        return _runner_control_response(
            action=action,
            status_code=403,
            ok=False,
            status="disabled",
            message=message,
            error_code="runner_controls_disabled",
            details={
                "enabled": bool(control.get("enabled")),
                "source": control.get("source", ""),
                "bind_host": bind_host,
                "trusted_network": bool(trusted_network),
                "reason": str(control.get("message") or controls_disabled_reason or ""),
            },
        )

    def _runner_control_unavailable(action: str) -> Any:
        return _runner_control_response(
            action=action,
            status_code=503,
            ok=False,
            status="error",
            message="Runner controller is unavailable.",
            error_code="runner_controller_unavailable",
        )

    async def _runner_control_body(request: Request) -> dict[str, Any] | None:
        try:
            payload = await request.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    async def _handle_runner_action(action: str, request: Request) -> Any:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"start", "stop", "reload", "restart"}:
            return _runner_control_response(
                action=normalized_action or "unknown",
                status_code=400,
                ok=False,
                status="error",
                message="Unknown runner action.",
                error_code="runner_action_unknown",
            )

        if not controls_enabled:
            return _runner_control_disabled(normalized_action)

        if controller is None:
            return _runner_control_unavailable(normalized_action)

        if not control_lock.acquire(blocking=False):
            return _runner_control_response(
                action=normalized_action,
                status_code=409,
                ok=False,
                status="busy",
                message="A runner control request is already in flight.",
                error_code="runner_controls_busy",
            )

        try:
            body = await _runner_control_body(request)
            if body is None:
                return _runner_control_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="error",
                    message="Runner control request body must be JSON.",
                    error_code="invalid_json",
                    busy_override=False,
                )
            provided = _runner_control_confirmation_value(body)
            expected = _runner_control_confirmation(normalized_action)
            if not provided:
                return _runner_control_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="confirmation_required",
                    message=f'Type "{expected}" to confirm this action.',
                    error_code="confirmation_required",
                    details={"expected": expected},
                    busy_override=False,
                )
            if provided != expected:
                return _runner_control_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="confirmation_mismatch",
                    message=f'Confirmation phrase must be "{expected}".',
                    error_code="confirmation_mismatch",
                    details={"expected": expected},
                    busy_override=False,
                )

            start_overrides: dict[str, Any] = {}
            if normalized_action in {"start", "reload", "restart"}:
                request_options = _runner_control_request_options(body)
                start_options_contract = _runner_control_start_options_contract(
                    controller,
                    repo=repo_root,
                    cfg=cfg,
                    cfg_path=cfg_path,
                )
                start_overrides, validation_error = normalize_runner_start_options(
                    repo_root,
                    request_options,
                    base_args=_build_runner_base_args(repo_root, cfg, cfg_path),
                    contract=start_options_contract,
                )
                if validation_error:
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=400,
                        ok=False,
                        status="error",
                        message=str(validation_error.get("message") or "Runner start options are invalid."),
                        error_code=str(validation_error.get("code") or "runner_start_options_invalid"),
                        details=validation_error.get("details") or {},
                        busy_override=False,
                    )
                start_overrides["repo"] = repo_root.as_posix()

            current_status = _runner_control_status_payload(
                controller,
                repo=repo_root,
                config_path=cfg_path.as_posix(),
                current_run_dir=str(getattr(controller, "run_dir", "") or ""),
                cfg=cfg,
                cfg_path=cfg_path,
            )
            status_reason = str(current_status.get("reason") or "").strip()
            if status_reason.startswith("status_error:"):
                message = status_reason
                control_state["last_action"] = normalized_action
                control_state["last_message"] = ""
                control_state["last_error"] = message
                return _runner_control_response(
                    action=normalized_action,
                    status_code=503,
                    ok=False,
                    status="error",
                    message=message,
                    error_code="runner_controller_status_error",
                    details={"reason": status_reason},
                    busy_override=False,
                )

            if normalized_action == "start":
                result = controller.start(start_overrides)
                if not bool(result.get("ok")):
                    message = str(result.get("message") or "Runner start failed.")
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code="runner_start_failed",
                        result=result,
                        busy_override=False,
                    )
                message = str(result.get("message") or "Runner started.")
                control_state["last_action"] = normalized_action
                control_state["last_message"] = message
                control_state["last_error"] = ""
                return _runner_control_response(
                    action=normalized_action,
                    status_code=200,
                    ok=True,
                    status="started",
                    message=message,
                    result=result,
                    busy_override=False,
                )

            if normalized_action == "stop":
                result = controller.stop(wait=True)
                if not bool(result.get("ok")):
                    message = str(result.get("message") or "Runner stop failed.")
                    stop_progress = result.get("stop_progress")
                    if not isinstance(stop_progress, dict):
                        stop_progress = {}
                    stop_phase = str(
                        stop_progress.get("phase")
                        or (stop_progress.get("current_phase") or {}).get("phase")
                        or ""
                    ).strip().lower()
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    error_code = "runner_stop_timeout" if stop_phase == "timeout" else "runner_stop_failed"
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="timeout" if stop_phase == "timeout" else "error",
                        message=message,
                        error_code=error_code,
                        details={"stop_progress": stop_progress} if stop_phase == "timeout" else None,
                        result=result,
                        busy_override=False,
                    )
                if not _wait_for_runner_idle(controller, timeout_sec=12.0):
                    message = "Runner did not stop before the timeout expired."
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code="runner_stop_timeout",
                        result=result,
                        busy_override=False,
                    )
                message = str(result.get("message") or "Runner stopped.")
                control_state["last_action"] = normalized_action
                control_state["last_message"] = message
                control_state["last_error"] = ""
                return _runner_control_response(
                    action=normalized_action,
                    status_code=200,
                    ok=True,
                    status="stopped",
                    message=message,
                    result=result,
                    busy_override=False,
                )

            flow_name = "restart" if normalized_action == "restart" else "reload"
            should_stop = bool(current_status.get("running")) or bool(str(current_status.get("run_dir") or "").strip())
            stop_result: dict[str, Any] = {}
            if should_stop:
                stop_result = controller.stop(wait=False)
                if not bool(stop_result.get("ok")):
                    message = str(stop_result.get("message") or f"Runner {flow_name} stop failed.")
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code=f"runner_{flow_name}_stop_failed",
                        result={"stop": stop_result},
                        busy_override=False,
                    )
                if not _wait_for_runner_idle(controller, timeout_sec=12.0):
                    message = f"Runner did not stop before {flow_name}."
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code=f"runner_{flow_name}_timeout",
                        result={"stop": stop_result},
                        busy_override=False,
                    )

            result = controller.start(start_overrides)
            if not bool(result.get("ok")):
                message = str(result.get("message") or f"Runner {flow_name} failed.")
                control_state["last_action"] = normalized_action
                control_state["last_message"] = ""
                control_state["last_error"] = message
                return _runner_control_response(
                    action=normalized_action,
                    status_code=409,
                    ok=False,
                    status="error",
                    message=message,
                    error_code=f"runner_{flow_name}_failed",
                    result={"stop": stop_result, "start": result},
                    busy_override=False,
                )

            success_message = "Runner restarted." if normalized_action == "restart" else "Runner reloaded."
            message = success_message
            control_state["last_action"] = normalized_action
            control_state["last_message"] = message
            control_state["last_error"] = ""
            return _runner_control_response(
                action=normalized_action,
                status_code=200,
                ok=True,
                status="restarted" if normalized_action == "restart" else "reloaded",
                message=message,
                result={"stop": stop_result, "start": result},
                busy_override=False,
            )
        except Exception as ex:
            message = f"Runner control failed: {ex}"
            control_state["last_action"] = normalized_action
            control_state["last_message"] = ""
            control_state["last_error"] = message
            return _runner_control_response(
                action=normalized_action,
                status_code=500,
                ok=False,
                status="error",
                message=message,
                error_code="runner_control_exception",
                busy_override=False,
            )
        finally:
            control_lock.release()

    def _worktree_action_confirmation(action: str) -> str:
        return WORKTREE_ACTION_CONFIRMATIONS.get(action, WORKTREE_ACTION_CONFIRMATIONS["discard"])

    def _worktree_action_response(
        *,
        action: str,
        status_code: int,
        ok: bool,
        status: str,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        busy_override: bool | None = None,
    ) -> Any:
        snapshot = _snapshot(busy_override=busy_override)
        payload = {
            "ok": ok,
            "action": action,
            "status": status,
            "message": message,
            "worktree": snapshot.get("worktree", {}),
            "runner_control": snapshot.get("runner_control", {}),
            "snapshot": snapshot,
            "repo": snapshot.get("repo", {}),
            "latest_run_dir": snapshot.get("latest_run_dir"),
            "progress": snapshot.get("progress", {}),
        }
        if error_code:
            payload["error"] = {
                "code": error_code,
                "message": message,
            }
            if details:
                payload["error"]["details"] = details
        if result is not None:
            payload["result"] = result
        return JSONResponse(status_code=status_code, content=payload)

    def _worktree_action_disabled(action: str) -> Any:
        control = _runner_control_snapshot().get("runner_control", {})
        message = str(control.get("message") or "Worktree review actions are disabled.")
        return _worktree_action_response(
            action=action,
            status_code=403,
            ok=False,
            status="disabled",
            message=message,
            error_code="worktree_actions_disabled",
            details={
                "enabled": bool(control.get("enabled")),
                "source": control.get("source", ""),
                "bind_host": bind_host,
                "trusted_network": bool(trusted_network),
                "reason": str(control.get("message") or controls_disabled_reason or ""),
            },
        )

    async def _worktree_action_body(request: Request) -> dict[str, Any] | None:
        try:
            payload = await request.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    def _worktree_validate_action_request(
        *,
        action: str,
        body: dict[str, Any],
        pending_path: Path,
        payload: dict[str, Any],
    ) -> tuple[Path, Path, Path, Path, str, str] | Any:
        expected_confirmation = _worktree_action_confirmation(action)
        provided_confirmation = _pick_text(body.get("confirmation"), body.get("confirm"), body.get("phrase"), body.get("token"))
        if not provided_confirmation:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="confirmation_required",
                message=f'Type "{expected_confirmation}" to confirm this worktree action.',
                error_code="confirmation_required",
                details={"expected": expected_confirmation},
                busy_override=False,
            )
        if provided_confirmation != expected_confirmation:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="confirmation_mismatch",
                message=f'Confirmation phrase must be "{expected_confirmation}".',
                error_code="confirmation_mismatch",
                details={"expected": expected_confirmation},
                busy_override=False,
            )

        source_repo_text = _pick_text(body.get("sourceRepo"), body.get("source_repo"))
        run_dir_text = _pick_text(body.get("runDir"), body.get("run_dir"))
        worktree_dir_text = _pick_text(body.get("worktreeDir"), body.get("worktree_dir"), body.get("worktree"))
        patch_path_text = _pick_text(body.get("patchPath"), body.get("patch_path"), body.get("patch"))
        pending_file_text = _pick_text(body.get("pendingFile"), body.get("pending_file"), body.get("statusFile"), body.get("status_file"))
        status_file_text = _pick_text(body.get("statusFile"), body.get("status_file"), pending_file_text)
        base_ref_text = _pick_text(body.get("baseRef"), body.get("base_ref"))
        head_ref_text = _pick_text(body.get("headRef"), body.get("head_ref"))

        required_fields = {
            "sourceRepo": source_repo_text,
            "runDir": run_dir_text,
            "worktreeDir": worktree_dir_text,
            "patchPath": patch_path_text,
            "pendingFile": pending_file_text,
            "baseRef": base_ref_text,
            "headRef": head_ref_text,
        }
        missing_fields = [field for field, value in required_fields.items() if not value]
        if missing_fields:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Worktree action request is missing required metadata.",
                error_code="worktree_metadata_required",
                details={"missing": missing_fields},
                busy_override=False,
            )

        repo_resolved = repo_root.expanduser().resolve()
        marker_source_repo = str(payload.get("source_repo") or payload.get("sourceRepo") or "").strip()
        marker_run_dir = str(payload.get("run_dir") or payload.get("runDir") or "").strip()
        marker_worktree_dir = str(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree") or "").strip()
        marker_patch_path = str(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch") or "").strip()
        marker_base_ref = str(payload.get("base_ref") or payload.get("baseRef") or "").strip()
        marker_head_ref = str(payload.get("head_ref") or payload.get("headRef") or "").strip()
        marker_status = str(payload.get("status") or "").strip().lower()
        marker_schema = _coerce_optional_int(payload.get("schema_version") or payload.get("schemaVersion")) or 0

        if marker_schema not in {0, 1}:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Pending worktree marker schema is not supported.",
                error_code="worktree_schema_unsupported",
                details={"schema_version": marker_schema},
                busy_override=False,
            )
        if marker_status != "pending":
            return _worktree_action_response(
                action=action,
                status_code=409,
                ok=False,
                status="unavailable",
                message="No pending worktree merge is available.",
                error_code="worktree_pending_not_found",
                details={"pending_file": pending_path.as_posix(), "status": marker_status or ""},
                busy_override=False,
            )

        try:
            marker_source_repo_path = Path(marker_source_repo).expanduser().resolve()
        except Exception:
            marker_source_repo_path = Path(marker_source_repo or repo_root).expanduser().resolve()
        if marker_source_repo_path != repo_resolved:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Pending worktree marker points at a different source repository.",
                error_code="worktree_source_repo_mismatch",
                details={"expected": repo_resolved.as_posix(), "actual": marker_source_repo_path.as_posix()},
                busy_override=False,
            )

        try:
            marker_run_dir_path = Path(marker_run_dir).expanduser().resolve()
        except Exception:
            marker_run_dir_path = Path(marker_run_dir).expanduser()
        expected_runs_root = (repo_resolved / ".AgentCLI" / "agent_runs").resolve()
        if not _path_is_within(marker_run_dir_path, expected_runs_root):
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Pending worktree run directory is outside the source repository run root.",
                error_code="worktree_run_dir_outside_repo",
                details={"expected_root": expected_runs_root.as_posix(), "actual": marker_run_dir_path.as_posix()},
                busy_override=False,
            )

        try:
            marker_worktree_dir_path = Path(marker_worktree_dir).expanduser().resolve()
        except Exception:
            marker_worktree_dir_path = Path(marker_worktree_dir).expanduser()
        if _path_is_within(marker_worktree_dir_path, repo_resolved):
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Pending worktree path must stay outside the source repository.",
                error_code="worktree_path_inside_source_repo",
                details={"path": marker_worktree_dir_path.as_posix(), "source_repo": repo_resolved.as_posix()},
                busy_override=False,
            )

        try:
            marker_patch_path_path = Path(marker_patch_path).expanduser().resolve()
        except Exception:
            marker_patch_path_path = Path(marker_patch_path).expanduser()
        if not _path_is_within(marker_patch_path_path, marker_run_dir_path):
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Pending patch path must stay within the run directory.",
                error_code="worktree_patch_path_outside_run_dir",
                details={"path": marker_patch_path_path.as_posix(), "run_dir": marker_run_dir_path.as_posix()},
                busy_override=False,
            )
        if not marker_patch_path_path.exists() or not marker_patch_path_path.is_file():
            return _worktree_action_response(
                action=action,
                status_code=404,
                ok=False,
                status="invalid_request",
                message="Pending worktree patch file is missing.",
                error_code="worktree_patch_missing",
                details={"path": marker_patch_path_path.as_posix()},
                busy_override=False,
            )

        expected_pending_path = pending_path.resolve()
        marker_pending_path = (marker_run_dir_path / "WORKTREE_MERGE_PENDING.json").resolve()
        repo_pending_path = (repo_resolved / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json").resolve()
        accepted_pending_paths = {expected_pending_path, marker_pending_path, repo_pending_path}

        def _resolve_candidate_path(text: str) -> Path:
            try:
                return Path(text).expanduser().resolve()
            except Exception:
                return Path(text).expanduser()

        if _resolve_candidate_path(pending_file_text) not in accepted_pending_paths:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Pending file path does not match the active marker.",
                error_code="worktree_pending_path_mismatch",
                details={
                    "expected": expected_pending_path.as_posix(),
                    "actual": _resolve_candidate_path(pending_file_text).as_posix(),
                },
                busy_override=False,
            )
        if status_file_text and _resolve_candidate_path(status_file_text) not in accepted_pending_paths:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Status file path does not match the active marker.",
                error_code="worktree_pending_path_mismatch",
                details={
                    "expected": expected_pending_path.as_posix(),
                    "actual": _resolve_candidate_path(status_file_text).as_posix(),
                },
                busy_override=False,
            )

        if marker_source_repo and Path(source_repo_text).expanduser().resolve() != repo_resolved:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Requested source repository does not match the server repository.",
                error_code="worktree_source_repo_mismatch",
                details={"expected": repo_resolved.as_posix(), "actual": Path(source_repo_text).expanduser().resolve().as_posix()},
                busy_override=False,
            )
        if Path(run_dir_text).expanduser().resolve() != marker_run_dir_path:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Requested run directory does not match the pending marker.",
                error_code="worktree_run_dir_mismatch",
                details={"expected": marker_run_dir_path.as_posix(), "actual": Path(run_dir_text).expanduser().resolve().as_posix()},
                busy_override=False,
            )
        if Path(worktree_dir_text).expanduser().resolve() != marker_worktree_dir_path:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Requested worktree directory does not match the pending marker.",
                error_code="worktree_worktree_dir_mismatch",
                details={"expected": marker_worktree_dir_path.as_posix(), "actual": Path(worktree_dir_text).expanduser().resolve().as_posix()},
                busy_override=False,
            )
        if Path(patch_path_text).expanduser().resolve() != marker_patch_path_path:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Requested patch path does not match the pending marker.",
                error_code="worktree_patch_path_mismatch",
                details={"expected": marker_patch_path_path.as_posix(), "actual": Path(patch_path_text).expanduser().resolve().as_posix()},
                busy_override=False,
            )
        if base_ref_text != marker_base_ref or head_ref_text != marker_head_ref:
            return _worktree_action_response(
                action=action,
                status_code=400,
                ok=False,
                status="invalid_request",
                message="Requested merge refs do not match the pending marker.",
                error_code="worktree_merge_refs_mismatch",
                details={
                    "expected": {"baseRef": marker_base_ref, "headRef": marker_head_ref},
                    "actual": {"baseRef": base_ref_text, "headRef": head_ref_text},
                },
                busy_override=False,
            )

        stale_reason = _worktree_pending_is_stale(payload, pending_path)
        if stale_reason:
            return _worktree_action_response(
                action=action,
                status_code=409,
                ok=False,
                status="unavailable",
                message=f"Pending worktree marker is stale: {stale_reason}",
                error_code="worktree_pending_stale",
                details={"reason": stale_reason, "pending_file": pending_path.as_posix()},
                busy_override=False,
            )

        return pending_path, marker_source_repo_path, marker_run_dir_path, marker_worktree_dir_path, marker_patch_path_path, expected_pending_path

    async def _handle_worktree_action(action: str, request: Request) -> Any:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"merge", "discard"}:
            return _worktree_action_response(
                action=normalized_action or "unknown",
                status_code=400,
                ok=False,
                status="error",
                message="Unknown worktree action.",
                error_code="worktree_action_unknown",
                busy_override=False,
            )

        if not controls_enabled:
            return _worktree_action_disabled(normalized_action)

        if not control_lock.acquire(blocking=False):
            return _worktree_action_response(
                action=normalized_action,
                status_code=409,
                ok=False,
                status="busy",
                message="A worktree review request is already in flight.",
                error_code="worktree_actions_busy",
                busy_override=True,
            )

        try:
            body = await _worktree_action_body(request)
            if body is None:
                return _worktree_action_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="error",
                    message="Worktree action request body must be JSON.",
                    error_code="invalid_json",
                    busy_override=False,
                )

            pending_path = find_pending_worktree_merge(repo_root)
            if pending_path is None:
                return _worktree_action_response(
                    action=normalized_action,
                    status_code=409,
                    ok=False,
                    status="unavailable",
                    message="No pending worktree merge is available.",
                    error_code="worktree_pending_not_found",
                    busy_override=False,
                )

            try:
                pending_payload = read_pending_worktree_merge(pending_path)
                if not isinstance(pending_payload, dict):
                    raise TypeError("Pending merge payload must be a JSON object.")
            except Exception as ex:
                return _worktree_action_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="error",
                    message=f"Pending worktree merge file is malformed: {str(ex).strip() or ex.__class__.__name__}",
                    error_code="worktree_pending_invalid",
                    details={"pending_file": pending_path.as_posix()},
                    busy_override=False,
                )

            validated = _worktree_validate_action_request(
                action=normalized_action,
                body=body,
                pending_path=pending_path,
                payload=pending_payload,
            )
            if isinstance(validated, JSONResponse):
                return validated
            validated_pending_path, source_repo_path, run_dir_path, worktree_dir_path, patch_path_path, _expected_pending_path = validated

            if normalized_action == "merge":
                try:
                    result = apply_pending_worktree_merge(validated_pending_path)
                except WorktreeSafetyError as ex:
                    return _worktree_action_response(
                        action=normalized_action,
                        status_code=ex.status_code,
                        ok=False,
                        status=ex.status,
                        message=str(ex),
                        error_code=ex.code,
                        details=ex.details or None,
                        busy_override=False,
                    )
                except Exception as ex:
                    return _worktree_action_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="conflict",
                        message=f"Worktree merge failed: {ex}",
                        error_code="worktree_merge_failed",
                        details={
                            "path": patch_path_path.as_posix(),
                            "source_repo": source_repo_path.as_posix(),
                            "run_dir": run_dir_path.as_posix(),
                            "worktree_dir": worktree_dir_path.as_posix(),
                        },
                        busy_override=False,
                    )
                result_status = str(result.get("status") or "applied").strip() or "applied"
                cleanup_error = str(result.get("cleanup_error") or "").strip()
                if result_status == "applied_cleanup_failed":
                    message = cleanup_error or "Worktree patch applied, but cleanup failed."
                else:
                    message = "Worktree patch applied to the source repository without creating a commit."
                return _worktree_action_response(
                    action=normalized_action,
                    status_code=200,
                    ok=True,
                    status=result_status,
                    message=message,
                    result=result,
                    busy_override=False,
                )

            try:
                result = discard_pending_worktree_merge(validated_pending_path)
            except Exception as ex:
                return _worktree_action_response(
                    action=normalized_action,
                    status_code=500,
                    ok=False,
                    status="error",
                    message=f"Worktree discard failed: {ex}",
                    error_code="worktree_discard_failed",
                    details={
                        "path": patch_path_path.as_posix(),
                        "source_repo": source_repo_path.as_posix(),
                        "run_dir": run_dir_path.as_posix(),
                        "worktree_dir": worktree_dir_path.as_posix(),
                    },
                    busy_override=False,
                )
            result_status = str(result.get("status") or "discarded").strip() or "discarded"
            cleanup_error = str(result.get("cleanup_error") or "").strip()
            if result_status == "discard_cleanup_failed":
                message = cleanup_error or "Worktree discard recorded, but cleanup failed."
            else:
                message = "Pending worktree result discarded without changing the source repository."
            return _worktree_action_response(
                action=normalized_action,
                status_code=200,
                ok=True,
                status=result_status,
                message=message,
                result=result,
                busy_override=False,
            )
        finally:
            control_lock.release()

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        snap = _snapshot()
        progress = snap.get("progress", {}) if isinstance(snap.get("progress"), dict) else {}
        return {
            "ok": bool(snap.get("ok", False)),
            "repo": snap.get("repo", {}),
            "latest_run_dir": snap.get("latest_run_dir"),
            "status": progress.get("run_status", "idle"),
            "runner_control": snap.get("runner_control", {}),
            "timestamp": now_iso(),
        }

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return _snapshot()

    @app.get("/api/progress")
    def api_progress() -> dict[str, Any]:
        snap = _snapshot()
        progress = snap.get("progress", {}) if isinstance(snap.get("progress"), dict) else {}
        return {
            "ok": bool(snap.get("ok", False)),
            "repo": snap.get("repo", {}),
            "latest_run_dir": snap.get("latest_run_dir"),
            "active_run": snap.get("active_run", {}),
            "stages": snap.get("stages", []),
            "backlog": snap.get("backlog", {}),
            "goals": snap.get("goals", {}),
            "logs": snap.get("logs", {}),
            "config": snap.get("config", {}),
            "prompts": snap.get("prompts", {}),
            "history": snap.get("history", {}),
            "metrics": snap.get("metrics", {}),
            "notifications": snap.get("notifications", []),
            "worktree": snap.get("worktree", {}),
            "runner_control": snap.get("runner_control", {}),
            "progress": progress,
            "tasks_done": progress.get("tasks_done", 0),
            "tasks_total": progress.get("tasks_total", 0),
            "tasks_failed": progress.get("tasks_failed", 0),
            "current_task_id": progress.get("current_task_id", ""),
            "current_task_title": progress.get("current_task_title", ""),
            "run_status": progress.get("run_status", "idle"),
            "final_reason": progress.get("final_reason", ""),
            "state": progress.get("state", {}),
            "execution_status": progress.get("execution_status", ""),
            "executionStatus": progress.get("executionStatus", progress.get("execution_status", "")),
            "project_complete": bool(progress.get("project_complete", False)),
            "projectComplete": bool(progress.get("projectComplete", progress.get("project_complete", False))),
            "project_status": progress.get("project_status", ""),
            "projectStatus": progress.get("projectStatus", progress.get("project_status", "")),
            "goals_complete": bool(progress.get("goals_complete", False)),
            "goalsComplete": bool(progress.get("goalsComplete", progress.get("goals_complete", False))),
            "backlog_complete": bool(progress.get("backlog_complete", False)),
            "backlogComplete": bool(progress.get("backlogComplete", progress.get("backlog_complete", False))),
        }

    @app.get("/api/runner/status")
    def api_runner_status() -> Any:
        return _runner_control_snapshot()

    @app.post("/api/runner/start")
    async def api_runner_start(request: Request) -> Any:
        return await _handle_runner_action("start", request)

    @app.post("/api/runner/stop")
    async def api_runner_stop(request: Request) -> Any:
        return await _handle_runner_action("stop", request)

    @app.post("/api/runner/reload")
    async def api_runner_reload(request: Request) -> Any:
        return await _handle_runner_action("reload", request)

    @app.post("/api/runner/restart")
    async def api_runner_restart(request: Request) -> Any:
        return await _handle_runner_action("restart", request)

    @app.post("/api/worktree/merge")
    async def api_worktree_merge(request: Request) -> Any:
        return await _handle_worktree_action("merge", request)

    @app.post("/api/worktree/discard")
    async def api_worktree_discard(request: Request) -> Any:
        return await _handle_worktree_action("discard", request)

    @app.get("/api/logs")
    def api_logs() -> dict[str, Any]:
        return _section("logs")

    @app.get("/api/logs/tail")
    @app.get("/api/logs/live")
    def api_logs_tail(request: Request) -> Any:
        query = request.query_params
        cursor_raw = query.get("cursor")
        max_lines_raw = query.get("max_lines") or query.get("lines")
        level = str(query.get("level") or "").strip()
        stage = str(query.get("stage") or "").strip()
        task_id = str(query.get("task_id") or query.get("taskId") or "").strip()
        search = str(query.get("search") or query.get("q") or "").strip()
        cursor: int | None = None
        if cursor_raw is not None and str(cursor_raw).strip():
            cursor = _coerce_optional_int(cursor_raw)
            if cursor is None:
                cursor = 0
        max_lines = _coerce_optional_int(max_lines_raw) if max_lines_raw is not None else 50
        if max_lines is None or max_lines <= 0:
            max_lines = 50
        max_lines = min(max_lines, 500)

        try:
            controller_status = _controller_status_payload(controller)
            latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
            source_path = _resolve_log_tail_source(latest_run_dir)
            if source_path is None:
                source_path = (latest_run_dir / "metrics.jsonl") if latest_run_dir is not None else None
            if source_path is None:
                payload = {
                    "ok": False,
                    "state": "missing_file",
                    "entries": [],
                    "next_cursor": 0,
                    "source_file": "",
                    "source_path": "",
                    "source": {
                        "path": "",
                        "name": "",
                        "exists": False,
                    },
                    "malformed_lines": 0,
                }
                return _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_log_payload)
            live = bool(controller_status.get("running"))
            payload = _build_log_tail_payload(
                source_path,
                cursor=cursor,
                max_lines=max_lines,
                level=level,
                stage=stage,
                task_id=task_id,
                search=search,
                live=live,
            )
            return _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_log_payload)
        except Exception as ex:
            source_text = ""
            try:
                controller_status = _controller_status_payload(controller)
                latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
                source_path = _resolve_log_tail_source(latest_run_dir)
                if source_path is not None:
                    source_text = source_path.as_posix()
            except Exception:
                source_text = ""
            payload = {
                "ok": False,
                "state": "read_error",
                "entries": [],
                "next_cursor": 0,
                "source_file": source_text,
                "source_path": source_text,
                "source": {
                    "path": source_text,
                    "name": Path(source_text).name if source_text else "",
                    "exists": bool(source_text and Path(source_text).exists()),
                },
                "error": str(ex).strip() or ex.__class__.__name__,
                "malformed_lines": 0,
            }
            return _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_log_payload)

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return _snapshot().get("config_contract") or _section("config")

    @app.post("/api/config/save")
    async def api_config_save(request: Request) -> Any:
        nonlocal cfg
        if not controls_enabled:
            return _config_save_error(
                403,
                "config_save_disabled",
                "Config saves are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _config_save_error(409, "config_save_busy", "A mutating action is already in flight.")

        backup_path: Path | None = None
        try:
            if cfg_path.exists() and not cfg_path.is_file():
                return _config_save_error(400, "config_path_not_file", "Config path must reference a JSON file.", path=cfg_path.as_posix())

            body = await _config_save_body(request)
            if body is None:
                return _config_save_error(400, "invalid_json", "Config save request body must be JSON.")

            raw_changes = body.get("changes")
            if raw_changes is None:
                raw_changes = body.get("diffs")
            if isinstance(raw_changes, dict):
                raw_changes = [{"path": key, "value": value} for key, value in raw_changes.items()]
            if not isinstance(raw_changes, list):
                return _config_save_error(
                    400,
                    "config_changes_required",
                    "Config save request must include a changes array.",
                    field="changes",
                )

            snapshot = _snapshot(busy_override=False)
            config_contract = snapshot.get("config_contract") if isinstance(snapshot.get("config_contract"), dict) else {}
            schema = config_contract.get("schema") if isinstance(config_contract.get("schema"), dict) else {}
            restart_required_paths = {
                str(path)
                for path in (config_contract.get("restart_required_paths") or [])
                if str(path).strip()
            }

            try:
                current_raw = load_config(cfg_path)
            except Exception as ex:
                return _config_save_error(
                    400,
                    "config_read_error",
                    "Existing config file could not be read.",
                    path=cfg_path.as_posix(),
                    error=str(ex).strip() or ex.__class__.__name__,
                )
            if not isinstance(current_raw, dict):
                return _config_save_error(400, "config_read_error", "Existing config file could not be read.", path=cfg_path.as_posix())

            updated_raw = deepcopy(current_raw)
            changed_paths: list[str] = []
            reload_required_paths: list[str] = []

            for entry in raw_changes:
                if not isinstance(entry, dict):
                    return _config_save_error(400, "config_change_invalid", "Each config change must be an object.")
                path = str(entry.get("path") or entry.get("field") or entry.get("name") or "").strip()
                if not path:
                    return _config_save_error(400, "config_path_required", "Each config change must include a path.")
                field_schema = schema.get(path)
                if not isinstance(field_schema, dict):
                    return _config_save_error(400, "config_unknown_path", "Config field is not part of the save schema.", path=path)
                if not bool(field_schema.get("editable", True)):
                    return _config_save_error(400, "config_field_not_editable", "Config field cannot be edited.", path=path)

                raw_value = entry.get("value")
                if "value" not in entry and "to" in entry:
                    raw_value = entry.get("to")
                if "value" not in entry and "to" not in entry and "next" in entry:
                    raw_value = entry.get("next")

                current_value = _config_path_get(current_raw, path)
                normalized_value, error_code, error_details = _config_save_validate_change(path, raw_value, field_schema, current_value)
                if error_code:
                    return _config_save_error(
                        400,
                        error_code,
                        "Config save payload is not valid for this field.",
                        **error_details,
                    )
                if normalized_value == current_value:
                    continue
                _config_path_set(updated_raw, path, normalized_value)
                changed_paths.append(path)
                if path in restart_required_paths or bool(field_schema.get("restart", False)):
                    reload_required_paths.append(path)

            changed_paths = list(dict.fromkeys(changed_paths))
            reload_required_paths = list(dict.fromkeys(reload_required_paths))
            if not changed_paths:
                return _config_save_error(400, "config_no_changes", "No config changes were supplied.")

            # Session-only run selection intent should never persist in config.
            updated_raw.pop("run_dir", None)
            updated_raw.pop("resume_latest", None)

            backup_path = _config_save_backup_path(cfg_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if cfg_path.exists():
                shutil.copy2(cfg_path, backup_path)
            else:
                atomic_write_json(backup_path, current_raw)

            atomic_write_json(cfg_path, updated_raw)
            cfg = updated_raw
            if controller is not None and hasattr(controller, "base_args"):
                try:
                    controller.base_args = _build_runner_base_args(repo_root, updated_raw, cfg_path)
                except Exception:
                    pass
                try:
                    if hasattr(controller, "runner_mode"):
                        controller.runner_mode = _runner_mode_from_config(updated_raw)
                except Exception:
                    pass

            snapshot = _snapshot(busy_override=False)
            message = f"Config saved. Backup written to {backup_path.as_posix()}."
            response_payload: dict[str, Any] = {
                "ok": True,
                "action": "config-save",
                "status": "saved",
                "message": message,
                "config_path": cfg_path.as_posix(),
                "backup_path": backup_path.as_posix(),
                "changed_paths": changed_paths,
                "reload_required_paths": reload_required_paths,
                "snapshot": snapshot,
            }
            return JSONResponse(status_code=200, content=response_payload)
        except Exception as ex:
            details: dict[str, Any] = {"path": cfg_path.as_posix()}
            if backup_path is not None:
                details["backup_path"] = backup_path.as_posix()
            return _config_save_error(500, "config_save_failed", f"Config save failed: {ex}", **details)
        finally:
            control_lock.release()

    @app.get("/api/prompts")
    def api_prompts() -> dict[str, Any]:
        return _section("prompts")

    def _prompt_error(status_code: int, code: str, message: str, **details: Any) -> JSONResponse:
        payload: dict[str, Any] = {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
            },
        }
        if details:
            payload["error"]["details"] = details
        return JSONResponse(status_code=status_code, content=payload)

    def _prompt_action_error(status_code: int, action: str, code: str, message: str, **details: Any) -> JSONResponse:
        payload: dict[str, Any] = {
            "ok": False,
            "action": action,
            "status": "error",
            "message": message,
            "error": {
                "code": code,
                "message": message,
            },
        }
        if details:
            payload["error"]["details"] = details
        return JSONResponse(status_code=status_code, content=payload)

    def _prompt_action_body(request: Request) -> dict[str, Any] | None:
        return _config_save_body(request)

    def _resolve_prompt_target(
        prompt_dir: Path,
        prompt_id: str,
        prompt_file: str,
    ) -> tuple[dict[str, str] | None, Path | None, JSONResponse | None]:
        spec = _prompt_spec_map().get(prompt_id)
        if spec is None:
            return None, None, _prompt_error(404, "prompt_not_found", "The requested prompt id was not found.", id=prompt_id)

        expected_rel = Path(spec["file"]).as_posix()
        requested_file = str(prompt_file or "").strip()
        candidate = Path(requested_file.replace("\\", "/")).expanduser()
        resolved = candidate.resolve() if candidate.is_absolute() else (prompt_dir / candidate).resolve()

        try:
            resolved.relative_to(prompt_dir)
        except Exception:
            return (
                spec,
                None,
                _prompt_error(
                    400,
                    "prompt_path_outside_prompts_dir",
                    "Prompt file must stay within the resolved prompts directory.",
                    path=resolved.as_posix(),
                    prompts_dir=prompt_dir.as_posix(),
                ),
            )

        resolved_rel = resolved.relative_to(prompt_dir).as_posix()
        if resolved_rel != expected_rel:
            return (
                spec,
                None,
                _prompt_error(
                    400,
                    "prompt_file_mismatch",
                    "The requested prompt file does not match the prompt id.",
                    expected=expected_rel,
                    actual=resolved_rel,
                ),
            )

        if not _prompt_file_name_is_bare(requested_file):
            return (
                spec,
                None,
                _prompt_error(
                    400,
                    "prompt_file_invalid",
                    "Prompt file must be a bare filename within the resolved prompts directory.",
                    file=requested_file,
                    expected=expected_rel,
                ),
            )

        return spec, resolved, None

    def _prompt_target_payload(
        spec: dict[str, str],
        prompt_dir: Path,
        *,
        profile: str,
        repo_root: Path,
    ) -> dict[str, Any]:
        return _prompt_read_payload(spec, prompt_dir, repo_root, profile=profile)

    def _config_save_error(status_code: int, code: str, message: str, **details: Any) -> JSONResponse:
        payload: dict[str, Any] = {
            "ok": False,
            "action": "config-save",
            "status": "error",
            "message": message,
            "error": {
                "code": code,
                "message": message,
            },
        }
        if details:
            payload["error"]["details"] = details
        return JSONResponse(status_code=status_code, content=payload)

    async def _config_save_body(request: Request) -> dict[str, Any] | None:
        try:
            payload = await request.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @app.api_route("/api/prompts/read", methods=["GET", "POST"])
    @app.api_route("/api/prompts/content", methods=["GET", "POST"])
    async def api_prompt_read(request: Request) -> Any:
        prompt_dir = resolve_prompts_dir(repo_root, str(cfg.get("prompts_dir") or ""))
        if not prompt_dir:
            prompt_dir = default_prompts_dir(repo_root)
        profile = _prompt_profile(cfg)

        query = request.query_params
        prompt_id = _pick_text(query.get("id"), query.get("prompt_id"), query.get("promptId"))
        prompt_file = _pick_text(query.get("file"), query.get("path"), query.get("prompt_file"), query.get("promptFile"))

        if request.method.upper() == "POST":
            try:
                body = await request.json()
            except Exception:
                return _prompt_error(400, "invalid_json", "Prompt read request body must be JSON.")
            if isinstance(body, dict):
                prompt_id = _pick_text(prompt_id, body.get("id"), body.get("prompt_id"), body.get("promptId"))
                prompt_file = _pick_text(prompt_file, body.get("file"), body.get("path"), body.get("prompt_file"), body.get("promptFile"))

        if not prompt_id:
            return _prompt_error(400, "prompt_id_required", "A prompt id is required.", field="id")
        if not prompt_file:
            return _prompt_error(400, "prompt_file_required", "A prompt file path is required.", field="file")
        spec, _, error = _resolve_prompt_target(prompt_dir, prompt_id, prompt_file)
        if error is not None:
            return error
        return _prompt_read_payload(spec, prompt_dir, repo_root, profile=profile)

    @app.post("/api/prompts/save")
    async def api_prompt_save(request: Request) -> Any:
        nonlocal cfg
        if not controls_enabled:
            return _prompt_action_error(
                403,
                "prompt-save",
                "prompt_mutation_disabled",
                "Prompt saves are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _prompt_action_error(409, "prompt-save", "prompt_save_busy", "A prompt mutation is already in flight.")

        backup_path: Path | None = None
        try:
            prompt_dir = resolve_prompts_dir(repo_root, str(cfg.get("prompts_dir") or ""))
            if not prompt_dir:
                prompt_dir = default_prompts_dir(repo_root)
            profile = _prompt_profile(cfg)

            body = await _prompt_action_body(request)
            if body is None:
                return _prompt_action_error(400, "prompt-save", "invalid_json", "Prompt save request body must be JSON.")

            prompt_id = _pick_text(body.get("id"), body.get("prompt_id"), body.get("promptId"))
            prompt_file = _pick_text(body.get("file"), body.get("path"), body.get("prompt_file"), body.get("promptFile"))
            content = body.get("content")
            if prompt_file is None:
                prompt_file = ""
            if content is None:
                content = ""
            if not prompt_id:
                return _prompt_action_error(400, "prompt-save", "prompt_id_required", "A prompt id is required.", field="id")
            if not prompt_file:
                return _prompt_action_error(400, "prompt-save", "prompt_file_required", "A prompt file path is required.", field="file")

            spec, prompt_path, error = _resolve_prompt_target(prompt_dir, prompt_id, prompt_file)
            if error is not None or spec is None or prompt_path is None:
                return error if error is not None else _prompt_action_error(404, "prompt-save", "prompt_not_found", "The requested prompt id was not found.", id=prompt_id)

            if not isinstance(content, str):
                content = str(content)

            required_variables = _prompt_variables(_prompt_default_text(repo_root, spec))
            validation = _prompt_validation_payload(
                file_name=str(prompt_file).strip(),
                expected_file=spec["file"],
                content=content,
                required_variables=required_variables,
            )
            if not validation["ok"]:
                first_error = validation["errors"][0] if validation["errors"] else {"code": "prompt_validation_failed", "message": "Prompt validation failed."}
                return _prompt_action_error(
                    400,
                    "prompt-save",
                    str(first_error.get("code") or "prompt_validation_failed"),
                    str(first_error.get("message") or "Prompt validation failed."),
                    path=prompt_path.as_posix(),
                    validation=validation,
                )

            current_content, current_exists = _read_prompt_text(prompt_path, _prompt_default_text(repo_root, spec))
            backup_path = _prompt_backup_path(prompt_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if current_exists:
                shutil.copy2(prompt_path, backup_path)
            else:
                atomic_write_text(backup_path, current_content)

            atomic_write_text(prompt_path, content)
            saved_prompt = _prompt_target_payload(spec, prompt_dir, profile=profile, repo_root=repo_root)
            response_payload: dict[str, Any] = {
                "ok": True,
                "action": "prompt-save",
                "status": "saved",
                "message": f"Prompt saved. Backup written to {backup_path.as_posix()}.",
                "prompt": saved_prompt,
                "backup_path": backup_path.as_posix(),
                "saved_path": prompt_path.as_posix(),
            }
            return JSONResponse(status_code=200, content=response_payload)
        except Exception as ex:
            details: dict[str, Any] = {"path": ""}
            if backup_path is not None:
                details["backup_path"] = backup_path.as_posix()
            return _prompt_action_error(500, "prompt-save", "prompt_save_failed", f"Prompt save failed: {ex}", **details)
        finally:
            control_lock.release()

    @app.post("/api/prompts/restore")
    async def api_prompt_restore(request: Request) -> Any:
        nonlocal cfg
        if not controls_enabled:
            return _prompt_action_error(
                403,
                "prompt-restore",
                "prompt_mutation_disabled",
                "Prompt restores are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _prompt_action_error(409, "prompt-restore", "prompt_restore_busy", "A prompt mutation is already in flight.")

        backup_path: Path | None = None
        try:
            prompt_dir = resolve_prompts_dir(repo_root, str(cfg.get("prompts_dir") or ""))
            if not prompt_dir:
                prompt_dir = default_prompts_dir(repo_root)
            profile = _prompt_profile(cfg)

            body = await _prompt_action_body(request)
            if body is None:
                return _prompt_action_error(400, "prompt-restore", "invalid_json", "Prompt restore request body must be JSON.")

            prompt_id = _pick_text(body.get("id"), body.get("prompt_id"), body.get("promptId"))
            prompt_file = _pick_text(body.get("file"), body.get("path"), body.get("prompt_file"), body.get("promptFile"))
            restore_path_value = _pick_text(
                body.get("backup_path"),
                body.get("backupPath"),
                body.get("restore_path"),
                body.get("restorePath"),
                body.get("selected_backup_path"),
                body.get("selectedBackupPath"),
            )
            confirmation = _pick_text(body.get("confirm"), body.get("confirmation"), body.get("phrase"), body.get("token"))

            if not prompt_id:
                return _prompt_action_error(400, "prompt-restore", "prompt_id_required", "A prompt id is required.", field="id")
            if not prompt_file:
                return _prompt_action_error(400, "prompt-restore", "prompt_file_required", "A prompt file path is required.", field="file")
            if not restore_path_value:
                return _prompt_action_error(400, "prompt-restore", "prompt_backup_path_required", "A backup path is required.", field="backup_path")

            spec, prompt_path, error = _resolve_prompt_target(prompt_dir, prompt_id, prompt_file)
            if error is not None or spec is None or prompt_path is None:
                return error if error is not None else _prompt_action_error(404, "prompt-restore", "prompt_not_found", "The requested prompt id was not found.", id=prompt_id)

            expected_confirmation = "RESTORE BACKUP"
            if not confirmation:
                return _prompt_action_error(
                    400,
                    "prompt-restore",
                    "prompt_restore_confirmation_required",
                    "A restore confirmation phrase is required.",
                    expected=expected_confirmation,
                )
            if confirmation != expected_confirmation:
                return _prompt_action_error(
                    400,
                    "prompt-restore",
                    "prompt_restore_confirmation_mismatch",
                    "The restore confirmation phrase did not match.",
                    expected=expected_confirmation,
                )

            candidate = Path(str(restore_path_value).strip().replace("\\", "/")).expanduser()
            restored_from = candidate.resolve() if candidate.is_absolute() else (prompt_dir / candidate).resolve()
            try:
                restored_from.relative_to(prompt_dir)
            except Exception:
                return _prompt_action_error(
                    400,
                    "prompt-restore",
                    "prompt_backup_path_outside_prompts_dir",
                    "Backup path must stay within the resolved prompts directory.",
                    path=restored_from.as_posix(),
                    prompts_dir=prompt_dir.as_posix(),
                )

            if not restored_from.exists() or not restored_from.is_file():
                return _prompt_action_error(
                    404,
                    "prompt-restore",
                    "prompt_backup_not_found",
                    "The selected backup file was not found.",
                    path=restored_from.as_posix(),
                )

            backup_pattern = f"{prompt_path.stem}.*.bak{prompt_path.suffix}"
            if restored_from.parent != prompt_path.parent or not restored_from.name.startswith(f"{prompt_path.stem}.") or not restored_from.name.endswith(f".bak{prompt_path.suffix}") or restored_from.name not in {path.name for path in prompt_path.parent.glob(backup_pattern)}:
                return _prompt_action_error(
                    400,
                    "prompt_backup_not_found",
                    "The selected backup file is not available for this prompt.",
                    path=restored_from.as_posix(),
                )

            current_content, current_exists = _read_prompt_text(prompt_path, _prompt_default_text(repo_root, spec))
            backup_path = _prompt_backup_path(prompt_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if current_exists:
                shutil.copy2(prompt_path, backup_path)
            else:
                atomic_write_text(backup_path, current_content)

            restore_text = _read_text_robust(restored_from)
            atomic_write_text(prompt_path, restore_text)
            restored_prompt = _prompt_target_payload(spec, prompt_dir, profile=profile, repo_root=repo_root)
            response_payload: dict[str, Any] = {
                "ok": True,
                "action": "prompt-restore",
                "status": "restored",
                "message": f"Prompt restored from {restored_from.as_posix()}. Backup written to {backup_path.as_posix()}.",
                "prompt": restored_prompt,
                "backup_path": backup_path.as_posix(),
                "restored_from_path": restored_from.as_posix(),
                "saved_path": prompt_path.as_posix(),
            }
            return JSONResponse(status_code=200, content=response_payload)
        except Exception as ex:
            details: dict[str, Any] = {"path": ""}
            if backup_path is not None:
                details["backup_path"] = backup_path.as_posix()
            return _prompt_action_error(500, "prompt-restore", "prompt_restore_failed", f"Prompt restore failed: {ex}", **details)
        finally:
            control_lock.release()

    @app.get("/api/goals")
    def api_goals() -> dict[str, Any]:
        return _goals()

    @app.post("/api/goals/save")
    async def api_goals_save(request: Request) -> Any:
        goal_path = goals_path(repo_root)
        backup_path: Path | None = None
        if not controls_enabled:
            return _goal_save_error(
                403,
                "goals_save_disabled",
                "GOALS saves are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _goal_save_error(409, "goals_save_busy", "A goal save is already in flight.")

        try:
            if goal_path.exists() and not goal_path.is_file():
                return _goal_save_error(400, "goals_path_not_file", "GOALS.md path must reference a file.", path=goal_path.as_posix())

            body = await _goal_save_body(request)
            if body is None:
                return _goal_save_error(400, "invalid_json", "Goals save request body must be JSON.")

            raw_draft = body.get("draft")
            if raw_draft is None:
                raw_draft = body.get("goals")
            raw_text_value = body.get("raw_text")
            if raw_text_value is None:
                raw_text_value = body.get("text")
            if raw_text_value is None:
                raw_text_value = body.get("content")

            next_items: dict[str, list[dict[str, Any]]] | None = None
            next_text = ""
            use_line_numbers = False
            if isinstance(raw_draft, dict):
                next_items = _goal_save_normalize_draft(raw_draft)
                next_text = _goal_save_serialize_draft(next_items)
                use_line_numbers = True
            elif raw_text_value is not None:
                if not isinstance(raw_text_value, str):
                    return _goal_save_error(400, "goals_raw_text_invalid", "Goals save raw text must be a string.", field="raw_text")
                next_text = raw_text_value
                next_items = _goal_items(next_text)
            else:
                return _goal_save_error(400, "goals_input_required", "Goals save request must include draft or raw_text.", field="draft")

            next_items = _goal_save_normalize_draft(next_items)
            if not _goal_save_has_required_sections(next_text):
                return _goal_save_error(400, "goals_sections_required", "GOALS.md must include both ## P0 and ## P1 sections.", path=goal_path.as_posix())

            blank_item = next(
                (
                    {
                        "bucket": bucket,
                        "line_number": int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0),
                    }
                    for bucket in ("p0", "p1")
                    for item in next_items.get(bucket, [])
                    if not str(item.get("text") or "").strip()
                ),
                None,
            )
            if blank_item is not None:
                return _goal_save_error(
                    400,
                    "goals_item_text_required",
                    "Goal text cannot be empty.",
                    path=goal_path.as_posix(),
                    bucket=blank_item["bucket"],
                    line_number=blank_item["line_number"],
                )

            current_path, current_raw = read_goals(repo_root)
            if current_path is not None and current_raw is None:
                return _goal_save_error(400, "goals_read_error", "Existing GOALS.md could not be read.", path=goal_path.as_posix())

            current_text = current_raw or ""
            current_items = _goal_save_normalize_draft(_goal_items(current_text))
            risk_report = _goal_save_risk_report(current_items, next_items, use_line_numbers=use_line_numbers)

            confirm_raw = body.get("confirm")
            if confirm_raw is None:
                confirm_raw = body.get("confirmation")
            if confirm_raw is None:
                confirm_raw = body.get("confirmation_phrase")
            confirmation = str(confirm_raw).strip() if confirm_raw is not None else ""
            if risk_report["requires_confirmation"]:
                if not confirmation:
                    return _goal_save_error(
                        400,
                        "goals_confirmation_required",
                        "Deleting or downgrading unmet P0 goals requires the exact confirmation phrase.",
                        path=goal_path.as_posix(),
                        confirmation_phrase=risk_report["confirmation_phrase"],
                        risk=risk_report,
                    )
                if confirmation != risk_report["confirmation_phrase"]:
                    return _goal_save_error(
                        400,
                        "goals_confirmation_mismatch",
                        "The goals confirmation phrase did not match.",
                        path=goal_path.as_posix(),
                        confirmation_phrase=risk_report["confirmation_phrase"],
                        risk=risk_report,
                    )

            if next_text == current_text:
                return _goal_save_error(400, "goals_no_changes", "No goal changes were supplied.", path=goal_path.as_posix())

            backup_path = _goal_save_backup_path(goal_path)
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            if current_path is not None and goal_path.exists():
                shutil.copy2(goal_path, backup_path)
            else:
                atomic_write_text(backup_path, current_text)

            atomic_write_text(goal_path, next_text)
            snapshot = _snapshot(busy_override=False)
            response_payload: dict[str, Any] = {
                "ok": True,
                "action": "goals-save",
                "status": "saved",
                "message": f"Goals saved. Backup written to {backup_path.as_posix()}.",
                "goals_path": goal_path.as_posix(),
                "saved_path": goal_path.as_posix(),
                "backup_path": backup_path.as_posix(),
                "risk": risk_report,
                "risk_report": risk_report,
                "snapshot": snapshot,
            }
            return JSONResponse(status_code=200, content=response_payload)
        except Exception as ex:
            details: dict[str, Any] = {"path": goal_path.as_posix()}
            if backup_path is not None:
                details["backup_path"] = backup_path.as_posix()
            return _goal_save_error(500, "goals_save_failed", f"Goals save failed: {ex}", **details)
        finally:
            control_lock.release()

    @app.get("/api/history")
    def api_history() -> dict[str, Any]:
        return _section("history")

    @app.get("/api/worktree")
    def api_worktree() -> dict[str, Any]:
        return _section("worktree")

    @app.get("/api/worktree/diagnostics")
    def api_worktree_diagnostics() -> dict[str, Any]:
        return scan_worktree_diagnostics(repo_root)

    def _serve_static_file(request_path: str = "") -> Any:
        clean = request_path.strip().lstrip("/").replace("\\", "/")
        if not clean or clean == "index.html":
            target = static_root / "index.html"
        else:
            target = (static_root / clean).resolve()
            try:
                target.relative_to(static_root.resolve())
            except Exception:
                target = static_root / "index.html"
        if not target.exists() or not target.is_file():
            target = static_root / "index.html"
        media_type = "application/javascript" if target.suffix.lower() == ".js" else None
        return FileResponse(target, media_type=media_type)

    @app.get("/")
    def root() -> Any:
        return _serve_static_file("")

    @app.get("/index.html")
    def index_html() -> Any:
        return _serve_static_file("index.html")

    @app.get("/{request_path:path}")
    def static_assets(request_path: str) -> Any:
        if request_path.startswith("api/"):
            if HTTPException is None:
                raise RuntimeError("API routes should not reach the static catch-all.")
            raise HTTPException(status_code=404)
        return _serve_static_file(request_path)

    return app


def serve(
    repo: Path | str | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    web_dir: Path | str | None = None,
    config_path: str | None = None,
    trusted_network: bool | None = None,
    enable_runner_controls: bool | None = None,
) -> None:
    if uvicorn is None:
        raise RuntimeError("uvicorn is not installed. Add the declared dependencies before serving the web console.")
    app = create_app(
        repo,
        web_dir=web_dir,
        config_path=config_path,
        bind_host=host,
        trusted_network=trusted_network,
        enable_runner_controls=enable_runner_controls,
    )
    uvicorn.run(app, host=host, port=int(port))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the AgentCLI web console with FastAPI.")
    parser.add_argument("--repo", default="", help="Repo root (default: the repository containing agent_runner/web.py)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--web-dir", default="", help="Static web console directory override")
    parser.add_argument("--config-path", default="", help="Optional explicit config path")
    parser.add_argument(
        "--enable-runner-controls",
        action="store_true",
        default=None,
        help="Allow POST runner control APIs (start/stop/reload/restart) when confirmation phrases are supplied.",
    )
    parser.add_argument(
        "--trusted-network",
        action="store_true",
        default=None,
        help="Allow runner controls on non-loopback binds when the network is trusted.",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve() if str(args.repo).strip() else None
    web_dir = Path(args.web_dir).expanduser().resolve() if str(args.web_dir).strip() else None
    config_path = str(args.config_path).strip() or None
    serve(
        repo=repo,
        host=args.host,
        port=args.port,
        web_dir=web_dir,
        config_path=config_path,
        trusted_network=getattr(args, "trusted_network", None),
        enable_runner_controls=getattr(args, "enable_runner_controls", None),
    )
    return 0


if FastAPI is not None:
    try:
        app = create_app()
    except Exception:
        app = None
else:
    app = None


if __name__ == "__main__":
    raise SystemExit(main())
