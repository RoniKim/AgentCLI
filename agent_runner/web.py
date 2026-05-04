from __future__ import annotations

import argparse
import ipaddress
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import os
import re
import shutil
import socket
import sqlite3
import threading
import time
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import (
    AGENT_WORK_DIR,
    app_home,
    default_config_path,
    default_prompts_dir,
    legacy_default_config_path,
    load_config,
    builtin_roles,
    normalize_config_value,
    normalize_config_list_value,
    resolve_config_path,
    resolve_prompts_dir,
    normalize_roles_value,
    validate_roles_value,
)
from .cli import DEFAULTS as CLI_DEFAULTS
from .goals import GOALS_INCOMPLETE_STATUS, goals_path, resolve_goals_completion_level
from .gitops import (
    apply_pending_worktree_merge,
    discard_pending_worktree_merge,
    find_pending_worktree_merge,
    git_head,
    git_show_toplevel,
    read_pending_worktree_merge,
    reconcile_cleanup_failed_artifacts,
    scan_worktree_diagnostics,
    summarize_worktree_diff,
    summarize_worktree_preflight,
    worktree_resolution_actions,
    WorktreeSafetyError,
)
from .pr_queue import PrQueueMergeError, merge_review_packet
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
)
from .pr_queue import load_branch_index, pr_packet_path, pr_queue_root
from .process_guard import _pid_alive, _pid_create_time_ticks, _pid_executable_path, init_process_guard, terminate_all_children
from .run_dir import find_latest_run_dir
from .shared import coerce_roles_arg
from . import web_config as _web_config
from .remote.controller import (
    RUNNER_CONTROL_EVENT_FILE,
    RunnerController,
    build_runner_start_options_contract,
    normalize_runner_start_options,
    read_runner_control_event,
    write_runner_control_event,
)
from .runtime_contract import CODEX_MODEL_FIELD_SPECS, PIPELINE_ROLE_FIELD_SPEC, PIPELINE_STAGE_ORDER, ROLE_SPEC_CANONICALS
from .stop_progress import normalize_stop_progress_payload, summarize_stop_progress_liveness
from .state import TaskItem, count_state_task_ids, load_backlog_json, load_backlog_task_ids, load_state, parse_backlog_md
from .failure_policy import (
    STATUS_GROUP_BLOCKED_ENV,
    STATUS_GROUP_REGRESSION,
    STATUS_GROUP_REVIEW,
    count_task_status_groups,
)
from .utils import atomic_write_json, atomic_write_text, now_iso, run_cmd, STOP_REASON_PROJECT_COMPLETE
from . import web_payloads as _web_payloads
from .web_redaction import (
    _lan_safety_blocks_mutations,
    _redact_config,
    _redact_web_backlog_item,
    _redact_web_backlog_payload,
    _redact_web_config_contract,
    _redact_web_config_payload,
    _redact_web_goal_warning,
    _redact_web_goals_payload,
    _redact_web_history_item,
    _redact_web_history_payload,
    _redact_web_history_summary,
    _redact_web_log_entry,
    _redact_web_log_payload,
    _redact_web_notification_item,
    _redact_web_notifications_payload,
    _redact_web_pr_queue_payload,
    _redact_web_prompt_item,
    _redact_web_prompts_payload,
    _redact_web_runner_control,
    _redact_web_runner_result,
    _redact_web_runner_start_options,
    _redact_web_runner_status_payload,
    _redact_web_stage,
    _redact_web_stages_payload,
    _redact_web_text,
    _web_apply_redaction,
    _web_redaction_active,
    _web_redaction_meta,
)
from .web_goals import (
    GOALS_SAVE_CONFIRMATION_PHRASE,
    GoalSaveFailure,
    GoalSavePlan,
    _build_goals_payload,
    _goal_items,
    _goal_save_backup_path,
    _goal_save_body,
    _goal_save_commit,
    _goal_save_has_required_sections,
    _goal_save_item_identity,
    _goal_save_item_lines,
    _goal_save_item_signature,
    _goal_save_normalize_draft,
    _goal_save_normalize_item,
    _goal_save_note_comment_line,
    _goal_save_risk_report,
    _goal_save_section_lines,
    _goal_save_serialize_draft,
    _goal_save_validate_request,
    _parse_goal_items_and_warnings,
)
from .web_prompts import (
    PROMPT_RESTORE_CONFIRMATION_PHRASE,
    PROMPT_SPECS,
    _load_prompt_items,
    _prompt_backup_candidates,
    _prompt_backup_path,
    _prompt_default_text,
    _prompt_file_name_is_bare,
    _prompt_inventory_item,
    _prompt_preview,
    _prompt_profile,
    _prompt_read_payload,
    _prompt_resolved_path,
    _prompt_spec_map,
    _prompt_summary,
    _prompt_template_dir,
    _prompt_template_resolved_path,
    _prompt_validation_payload,
    _prompt_variables,
    _read_prompt_text,
    resolve_prompt_target,
    restore_prompt,
    save_prompt,
)
from .web_logs import (
    _build_log_tail_payload,
    _log_tail_entry_matches,
    _log_tail_entry_search_text,
    _log_tail_source_catalog,
    _normalize_log_tail_level,
    _normalize_plain_log_tail_entry,
    _normalize_structured_log_tail_entry,
    _parse_log_tail_entry,
    _resolve_log_tail_source,
    _resolve_log_tail_source_record,
)

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


STAGE_ORDER = {stage.lower(): index for index, stage in enumerate(PIPELINE_STAGE_ORDER)}
RUNNER_CONTROL_CONFIRMATIONS = {
    "start": "START RUNNER",
    "stop": "STOP RUNNER",
    "reload": "RELOAD RUNNER",
    "restart": "RESTART RUNNER",
}
CONFIG_RESTORE_CONFIRMATION_PHRASE = "RESTORE CONFIG BACKUP"
RUN_DIR_ARTIFACT_NAMES = {
    "BACKLOG.json",
    "BACKLOG.md",
    "STATE.json",
    "STOP",
    "cycle_summary.log",
    "cycle_change_summary.json",
    "cycle_change_summary.md",
    "last_run_summary.json",
    "metrics.jsonl",
    "FINAL_RUN_REPORT.json",
    "FINAL_RUN_REPORT.md",
    "QA_VALIDATION_REPORT.json",
    "QA_VALIDATION_REPORT.md",
    "failed_tasks.json",
    "failed_tasks.md",
    RUNNER_CONTROL_EVENT_FILE,
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
SENSITIVE_CONFIG_TOKENS = _web_config.SENSITIVE_CONFIG_TOKENS
REDACTED_VALUE = _web_config.REDACTED_VALUE
LAN_SAFETY_MUTATION_DISABLED_MESSAGE = (
    "LAN safety blocks mutating web actions until authentication or a stronger trusted-operator gate is implemented."
)
LAN_SAFETY_PROMPT_READ_DISABLED_MESSAGE = (
    "LAN safety blocks raw prompt content reads. Redacted prompt inventory remains available."
)
WEB_INSTANCE_LOCK_FILENAME = "web_console.lock.json"
_WEB_INSTANCE_LOCAL_HOLDS: dict[str, dict[str, Any]] = {}
_WEB_INSTANCE_LOCAL_HOLDS_LOCK = threading.Lock()
PR_QUEUE_MAX_ITEMS = 50
PR_QUEUE_MAX_NOTES = 12
PR_QUEUE_MAX_GOAL_REFS = 16
PR_QUEUE_MAX_COMMITS = 20
PR_QUEUE_MAX_CHANGED_FILES = 80
PR_QUEUE_MAX_VALIDATION_ARTIFACTS = 16
PR_QUEUE_ARTIFACT_PREVIEW_LINES = 80
PR_QUEUE_ARTIFACT_PREVIEW_CHARS = 8000
EXPERIENCE_MAX_ITEMS = 6
EXPERIENCE_MAX_EVIDENCE_ITEMS = 3
EXPERIENCE_UNAVAILABLE_MESSAGE = "Experience DB is unavailable. No read-only Experience data was found."
EXPERIENCE_EMPTY_MESSAGE = "Experience DB is available, but no recent lessons or blockers were recorded yet."
EXPERIENCE_SUMMARY_FALLBACK_MESSAGE = "Experience DB is unavailable. Showing cached analyzer summary data only."

CONFIG_CONTRACT_GROUPS = _web_config.CONFIG_CONTRACT_GROUPS
CONFIG_CONTRACT_FIELDS = _web_config.CONFIG_CONTRACT_FIELDS
ConfigMutationError = _web_config.ConfigMutationError
_is_sensitive_config_key = _web_config._is_sensitive_config_key
_config_path_get = _web_config._config_path_get
_config_path_set = _web_config._config_path_set
_merge_config_tree = _web_config._merge_config_tree
_normalize_config_list = _web_config._normalize_config_list
_normalize_config_contract_value = _web_config._normalize_config_contract_value
_normalize_config_for_launch = _web_config._normalize_config_for_launch
_build_config_contract = _web_config._build_config_contract
_config_save_backup_path = _web_config._config_save_backup_path
_config_backup_candidates = _web_config._config_backup_candidates
_config_resolve_backup_selection = _web_config._config_resolve_backup_selection
_config_save_validate_change = _web_config._config_save_validate_change
_config_save_changes = _web_config._config_save_changes
_config_restore_backup = _web_config._config_restore_backup


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
        "experience": EXPERIENCE_UNAVAILABLE_MESSAGE,
        "worktree": "No pending worktree merge is available.",
        "prQueue": "No PR queue packets are available.",
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
        "cleanupReconciliation": {},
        "cleanup_reconciliation": {},
        "cleanupState": "none",
        "resolutionActions": [],
        "resolution_actions": [],
        "summary": "No pending worktree merge.",
        "risk": "No isolated worktree patch is pending review.",
        "changedFiles": [],
        "changed_files": [],
        "preflight": {},
        "applyCheck": {},
        "sourceRepoState": "",
        "source_repo_state": "",
        "sourceHead": "",
        "source_head": "",
        "expectedBaseRef": "",
        "expected_base_ref": "",
        "patchHash": "",
        "patch_hash": "",
        "fastForwardRef": "",
        "fast_forward_ref": "",
        "dirtyPatchPath": "",
        "dirty_patch_path": "",
        "dirtyPatchHash": "",
        "dirty_patch_hash": "",
        "dirtyPatchCheck": None,
        "dirty_patch_check": None,
        "dirtyPatchApplied": None,
        "dirty_patch_applied": None,
        "pendingMarkerPath": "",
        "pending_marker_path": "",
        "checklist": list(WORKTREE_REVIEW_CHECKLIST),
        "runDir": run_dir_value,
        "runnerRc": 0,
        "lastRc": 0,
    }


def _worktree_changed_files_from_patch(patch_path: str, *, allow_placeholder: bool = True) -> list[dict[str, Any]]:
    if not patch_path:
        return []
    try:
        return summarize_worktree_diff(Path(patch_path), allow_placeholder=allow_placeholder)
    except Exception:
        if allow_placeholder:
            return [{"path": patch_path, "kind": "modified", "note": "patch export", "summary": "Patch export"}]
        return []


def _worktree_normalize_changed_files(raw_changed_files: Any, patch_path: str, *, allow_placeholder: bool = True) -> list[dict[str, Any]]:
    changed_files: list[dict[str, Any]] = []
    if isinstance(raw_changed_files, list):
        for item in raw_changed_files:
            if isinstance(item, dict):
                path = _pick_text(item.get("path"), item.get("file"), item.get("name"))
                if not path:
                    continue
                raw_hunks = item.get("hunks") if isinstance(item.get("hunks"), list) else []
                changed_files.append(
                    {
                        "path": path,
                        "oldPath": _pick_text(item.get("oldPath"), item.get("old_path"), item.get("sourcePath"), item.get("source_path"), path),
                        "newPath": _pick_text(item.get("newPath"), item.get("new_path"), item.get("targetPath"), item.get("target_path"), path),
                        "kind": _pick_text(item.get("kind"), item.get("type"), "modified"),
                        "state": _pick_text(item.get("state"), item.get("kind"), item.get("type"), "modified"),
                        "note": _pick_text(item.get("note"), item.get("message")),
                        "summary": _pick_text(item.get("summary"), item.get("title"), item.get("note"), item.get("message")),
                        "binary": bool(item.get("binary")),
                        "deleted": bool(item.get("deleted")),
                        "renamed": bool(item.get("renamed")),
                        "large": bool(item.get("large")),
                        "truncated": bool(item.get("truncated")),
                        "lineCount": _coerce_optional_int(item.get("lineCount") or item.get("line_count")) or 0,
                        "hunks": [
                            {
                                "header": _pick_text(hunk.get("header"), hunk.get("hunkHeader")),
                                "oldStart": _coerce_optional_int(hunk.get("oldStart") or hunk.get("old_start")) or 0,
                                "oldCount": _coerce_optional_int(hunk.get("oldCount") or hunk.get("old_count")) or 0,
                                "newStart": _coerce_optional_int(hunk.get("newStart") or hunk.get("new_start")) or 0,
                                "newCount": _coerce_optional_int(hunk.get("newCount") or hunk.get("new_count")) or 0,
                                "lines": [str(line) for line in hunk.get("lines", []) if line is not None] if isinstance(hunk, dict) else [],
                                "truncated": bool(hunk.get("truncated")) if isinstance(hunk, dict) else False,
                                "lineCount": _coerce_optional_int(hunk.get("lineCount") or hunk.get("line_count")) or 0 if isinstance(hunk, dict) else 0,
                            }
                            for hunk in raw_hunks
                            if isinstance(hunk, dict)
                        ],
                    }
                )
            else:
                path = _pick_text(item)
                if path:
                    changed_files.append({"path": path, "oldPath": path, "newPath": path, "kind": "modified", "state": "modified", "note": "", "summary": "Patch export", "binary": False, "deleted": False, "renamed": False, "large": False, "truncated": False, "hunks": [], "lineCount": 0})
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
    cleanup_reconciliation = raw.get("cleanup_reconciliation") if isinstance(raw.get("cleanup_reconciliation"), dict) else raw.get("cleanupReconciliation")
    if not isinstance(cleanup_reconciliation, dict):
        cleanup_reconciliation = {}
    pending_file = pending_path.as_posix() if pending_path is not None else ""
    status_file = artifact_path.as_posix()
    changed_files = _worktree_normalize_changed_files(raw.get("changedFiles") or raw.get("changed_files"), patch_path, allow_placeholder=status not in {"error"})
    raw_preflight = raw.get("preflight") if isinstance(raw.get("preflight"), dict) else raw.get("mergePreflight")
    if not isinstance(raw_preflight, dict):
        raw_preflight = {}
    preflight = dict(raw_preflight)
    source_repo_path: Path | None = None
    try:
        if source_repo_value:
            source_repo_path = Path(source_repo_value).expanduser().resolve()
    except Exception:
        source_repo_path = None
    if status in {"pending", "pending review"} and source_repo_path is not None and patch_path and Path(patch_path).exists():
        try:
            if source_repo_path.exists() and git_show_toplevel(source_repo_path):
                live_preflight = summarize_worktree_preflight(
                    source_repo_path,
                    Path(patch_path),
                    base_ref=base_ref or str(raw_preflight.get("expectedBaseRef") or raw_preflight.get("expected_base_ref") or base_ref),
                    pending_path=pending_path,
                )
                if live_preflight:
                    preflight.update(live_preflight)
        except Exception:
            pass
    source_repo_state = str(preflight.get("sourceRepoState") or raw.get("source_repo_state") or raw.get("sourceRepoState") or "")
    source_head = str(preflight.get("sourceHead") or raw.get("source_head") or raw.get("sourceHead") or raw.get("head_ref") or raw.get("headRef") or "")
    expected_base_ref = str(preflight.get("expectedBaseRef") or raw.get("expected_base_ref") or raw.get("expectedBaseRef") or base_ref).strip()
    patch_hash_value = str(preflight.get("patchHash") or raw.get("patch_hash") or raw.get("patchHash") or "").strip()
    fast_forward_ref = str(raw.get("fast_forward_ref") or raw.get("fastForwardRef") or "").strip()
    dirty_patch_path = str(raw.get("dirty_patch_path") or raw.get("dirtyPatchPath") or "").strip()
    dirty_patch_hash = str(raw.get("dirty_patch_hash") or raw.get("dirtyPatchHash") or "").strip()
    raw_dirty_patch_check_value = raw.get("dirty_patch_check")
    if not (isinstance(raw_dirty_patch_check_value, dict) and raw_dirty_patch_check_value):
        raw_dirty_patch_check_value = raw.get("dirtyPatchCheck")
    dirty_patch_check = (
        dict(raw_dirty_patch_check_value)
        if isinstance(raw_dirty_patch_check_value, dict) and raw_dirty_patch_check_value
        else None
    )
    dirty_patch_applied = _coerce_optional_bool(raw.get("dirty_patch_applied") if raw.get("dirty_patch_applied") is not None else raw.get("dirtyPatchApplied"))
    pending_marker_path = str(preflight.get("pendingMarkerPath") or preflight.get("pendingFile") or raw.get("pending_marker_path") or raw.get("pendingMarkerPath") or pending_file).strip()
    apply_check = preflight.get("applyCheck") if isinstance(preflight.get("applyCheck"), dict) else raw.get("applyCheck")
    if not isinstance(apply_check, dict):
        apply_check = raw.get("apply_check") if isinstance(raw.get("apply_check"), dict) else {}
    if not isinstance(apply_check, dict):
        apply_check = {}
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

    raw_resolution_actions = raw.get("resolution_actions") if isinstance(raw.get("resolution_actions"), list) else raw.get("resolutionActions")
    if isinstance(raw_resolution_actions, list):
        resolution_actions = [dict(item) for item in raw_resolution_actions if isinstance(item, dict)]
    else:
        resolution_actions = worktree_resolution_actions(
            "stale_pending_marker" if status == "error" and pending_path is not None else status,
            source_repo=source_repo_value,
            worktree_dir=worktree_dir,
            cleanup_path=cleanup_path,
            pending_paths=[pending_file] if pending_file else [],
            cleanup_message=cleanup_message,
            artifact_path=status_file,
            reconciliation=cleanup_reconciliation,
        )

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
        "sourceRepoState": source_repo_state,
        "source_repo_state": source_repo_state,
        "sourceHead": source_head,
        "source_head": source_head,
        "sourceBranch": source_branch,
        "branch": source_branch,
        "baseRef": base_ref,
        "expectedBaseRef": expected_base_ref,
        "expected_base_ref": expected_base_ref,
        "headRef": head_ref,
        "worktreeDir": worktree_dir,
        "worktree": worktree_dir,
        "patchPath": patch_path,
        "patch": patch_path,
        "patchHash": patch_hash_value,
        "patch_hash": patch_hash_value,
        "fastForwardRef": fast_forward_ref,
        "fast_forward_ref": fast_forward_ref,
        "dirtyPatchPath": dirty_patch_path,
        "dirty_patch_path": dirty_patch_path,
        "dirtyPatchHash": dirty_patch_hash,
        "dirty_patch_hash": dirty_patch_hash,
        "dirtyPatchCheck": dirty_patch_check,
        "dirty_patch_check": dirty_patch_check,
        "dirtyPatchApplied": dirty_patch_applied,
        "dirty_patch_applied": dirty_patch_applied,
        "pendingFile": pending_file,
        "pendingMarkerPath": pending_marker_path,
        "pending_marker_path": pending_marker_path,
        "statusFile": status_file,
        "cleanupPath": cleanup_path,
        "cleanupMessage": cleanup_message,
        "cleanupDetails": cleanup_details,
        "cleanupAttempts": cleanup_attempts,
        "cleanupReconciliation": cleanup_reconciliation,
        "cleanup_reconciliation": cleanup_reconciliation,
        "cleanupState": cleanup_state,
        "resolutionActions": resolution_actions,
        "resolution_actions": resolution_actions,
        "summary": summary,
        "risk": risk,
        "changedFiles": changed_files,
        "changed_files": changed_files,
        "preflight": preflight,
        "applyCheck": apply_check,
        "apply_check": apply_check,
        "sourceRepoDirty": source_repo_state != "clean" if source_repo_state else False,
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


def _experience_root_candidates(repo: Path) -> list[Path]:
    roots: list[Path] = []
    for candidate in (repo / AGENT_WORK_DIR / "experience", app_home() / "experience"):
        resolved = candidate.expanduser().resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def _experience_db_candidates(repo: Path) -> list[Path]:
    return [root / "experience.db" for root in _experience_root_candidates(repo)]


def _experience_summary_candidates(repo: Path, run_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []
    for root in _experience_root_candidates(repo):
        candidates.append(root / "latest_summary.json")
    if run_dir is not None:
        candidates.append(run_dir / "ANALYZER_SUMMARY.json")
    return candidates


def _experience_text(value: Any, *, max_chars: int = 0) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    if max_chars and len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def _experience_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except Exception:
            return [raw]
        if isinstance(parsed, list):
            return list(parsed)
        if isinstance(parsed, tuple):
            return list(parsed)
        if parsed is None:
            return []
        return [parsed]
    if value is None:
        return []
    return [value]


def _experience_artifact_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "artifact reference"
    if "prompt" in text:
        return "prompt artifact"
    if "patch" in text or text.endswith(".diff") or text.endswith(".patch") or "diff --git" in text:
        return "diff artifact"
    if "log" in text or "trace" in text or text.endswith(".log") or text.endswith(".txt") or text.endswith(".jsonl"):
        return "log artifact"
    if "report" in text or text.endswith(".json") or text.endswith(".md"):
        return "report artifact"
    return "artifact reference"


def _experience_evidence_pointer(
    raw: Any,
    *,
    run_id: str = "",
    task_id: str = "",
    gate: str = "",
    pr_id: str = "",
) -> str:
    parts: list[str] = []
    if run_id:
        parts.append(f"run {run_id}")
    if task_id:
        parts.append(f"task {task_id}")
    if pr_id:
        parts.append(f"pr {pr_id}")
    if gate:
        parts.append(f"gate {gate}")
    parts.append(_experience_artifact_kind(raw))
    return " | ".join(parts)


def _experience_evidence_pointers(
    value: Any,
    *,
    run_id: str = "",
    task_id: str = "",
    gate: str = "",
    pr_id: str = "",
    limit: int = EXPERIENCE_MAX_EVIDENCE_ITEMS,
) -> list[str]:
    pointers: list[str] = []
    seen: set[str] = set()
    for item in _experience_json_list(value):
        item_run_id = run_id
        item_task_id = task_id
        item_gate = gate
        item_pr_id = pr_id
        item_raw = item
        if isinstance(item, dict):
            item_run_id = _pick_text(item.get("run_id"), item.get("runId"), run_id)
            item_task_id = _pick_text(item.get("task_id"), item.get("taskId"), task_id)
            item_gate = _pick_text(item.get("gate"), gate)
            item_pr_id = _pick_text(item.get("pr_id"), item.get("prId"), pr_id)
            item_raw = _pick_text(
                item.get("artifact_path"),
                item.get("artifactPath"),
                item.get("path"),
                item.get("file"),
                item.get("ref"),
                item.get("pointer"),
                item.get("evidence"),
            )
        pointer = _experience_evidence_pointer(
            item_raw,
            run_id=item_run_id,
            task_id=item_task_id,
            gate=item_gate,
            pr_id=item_pr_id,
        )
        if not pointer or pointer in seen:
            continue
        seen.add(pointer)
        pointers.append(pointer)
        if len(pointers) >= max(1, int(limit)):
            break
    if pointers:
        return pointers
    if run_id or task_id or gate or pr_id:
        return [_experience_evidence_pointer("", run_id=run_id, task_id=task_id, gate=gate, pr_id=pr_id)]
    return []


def _experience_table_rows(db_path: Path, table: str) -> list[dict[str, Any]]:
    if not db_path.exists() or not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=2)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        finally:
            conn.close()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _experience_sort_key(*values: Any) -> tuple[str, int]:
    timestamp = ""
    for value in values:
        text = _experience_text(value)
        if text:
            timestamp = text
            break
    return timestamp, len(timestamp)


def _experience_lesson_items(lesson_rows: list[dict[str, Any]], task_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    source_rows = lesson_rows
    if not source_rows:
        source_rows = [row for row in task_rows if _experience_text(row.get("lesson"))]
    for row in source_rows:
        lesson = _experience_text(row.get("lesson"), max_chars=240)
        if not lesson:
            continue
        run_id = _experience_text(row.get("run_id"), max_chars=32)
        task_id = _experience_text(row.get("task_id"), max_chars=32)
        trigger = _experience_text(row.get("trigger"), max_chars=140)
        evidence = _experience_evidence_pointers(
            row.get("evidence"),
            run_id=run_id,
            task_id=task_id,
            pr_id=_experience_text(row.get("pr_id"), max_chars=32),
        )
        items.append(
            {
                "kind": _experience_text(row.get("kind"), max_chars=32) or "general",
                "severity": _experience_text(row.get("severity"), max_chars=16) or "medium",
                "confidence": _coerce_optional_float(row.get("confidence")) or 0.0,
                "trigger": trigger,
                "lesson": lesson,
                "evidenceCount": max(len(_experience_json_list(row.get("evidence"))), len(evidence)),
                "evidencePointers": evidence,
                "lastSeenAt": _experience_text(row.get("last_seen_at"), max_chars=64) or _experience_text(row.get("created_at"), max_chars=64),
                "createdAt": _experience_text(row.get("created_at"), max_chars=64),
                "seenCount": _coerce_optional_int(row.get("seen_count")) or 0,
                "runId": run_id,
                "taskId": task_id,
            }
        )
    items.sort(
        key=lambda item: (
            _experience_sort_key(item.get("lastSeenAt"), item.get("createdAt")),
            float(item.get("confidence") or 0.0),
            int(item.get("seenCount") or 0),
        ),
        reverse=True,
    )
    return items[:EXPERIENCE_MAX_ITEMS]


def _experience_failure_pattern_label(classification: str, gate: str, status: str) -> str:
    label_map = {
        "blocked_env": "Environment blocker repeated",
        "regression_failed": "Regression failure repeated",
        "test_contract_changed": "Contract drift repeated",
        "no_tests_found": "No-tests finding repeated",
        "failed": "Validation failure repeated",
        "timeout": "Validation timeout repeated",
        "stopped": "Validation stop repeated",
    }
    key = classification or status
    if key in label_map:
        return label_map[key]
    if gate and key:
        return f"{gate} {key} repeated"
    if gate:
        return f"{gate} repeated"
    if key:
        return f"{key} repeated"
    return "Validation failure repeated"


def _experience_failure_patterns(validation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in validation_rows:
        status = _experience_text(row.get("status"), max_chars=32).lower()
        classification = _experience_text(row.get("classification"), max_chars=48).lower()
        gate = _experience_text(row.get("gate"), max_chars=48).lower()
        if not (classification or status):
            continue
        key = (classification or status, gate, status)
        bucket = grouped.setdefault(
            key,
            {
                "classification": classification or status,
                "gate": gate,
                "status": status,
                "occurrences": 0,
                "lastSeenAt": "",
                "evidencePointers": [],
                "evidenceSeen": set(),
            },
        )
        bucket["occurrences"] += 1
        bucket["lastSeenAt"] = max(bucket["lastSeenAt"], _experience_text(row.get("recorded_at"), max_chars=64))
        for pointer in _experience_evidence_pointers(
            row.get("artifact_path"),
            run_id=_experience_text(row.get("run_id"), max_chars=32),
            task_id=_experience_text(row.get("task_id"), max_chars=32),
            gate=gate,
        ):
            if pointer in bucket["evidenceSeen"]:
                continue
            bucket["evidenceSeen"].add(pointer)
            if len(bucket["evidencePointers"]) < EXPERIENCE_MAX_EVIDENCE_ITEMS:
                bucket["evidencePointers"].append(pointer)
    items: list[dict[str, Any]] = []
    for bucket in grouped.values():
        if int(bucket.get("occurrences") or 0) < 2:
            continue
        items.append(
            {
                "classification": bucket["classification"],
                "gate": bucket["gate"],
                "status": bucket["status"],
                "occurrences": int(bucket.get("occurrences") or 0),
                "summary": _experience_failure_pattern_label(
                    str(bucket.get("classification") or ""),
                    str(bucket.get("gate") or ""),
                    str(bucket.get("status") or ""),
                ),
                "evidenceCount": len(bucket["evidenceSeen"]),
                "evidencePointers": list(bucket.get("evidencePointers") or []),
                "lastSeenAt": bucket.get("lastSeenAt") or "",
            }
        )
    items.sort(key=lambda item: (_experience_sort_key(item.get("lastSeenAt")), int(item.get("occurrences") or 0)), reverse=True)
    return items[:EXPERIENCE_MAX_ITEMS]


def _experience_validation_gap_label(validation_status: str, gate: str, classification: str) -> str:
    if validation_status == "validation_pending":
        return "Validation is pending before merge"
    if validation_status == "no_tests_found" or classification == "no_tests_found":
        return "No tests were found for the affected change"
    if validation_status == "skipped":
        return f"{gate or 'Validation'} was skipped"
    if classification == "blocked_env":
        return "Validation was blocked by the environment"
    return "Validation coverage gap detected"


def _experience_validation_gaps(task_rows: list[dict[str, Any]], validation_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}

    def _add_gap(*, validation_status: str, gate: str, classification: str, run_id: str, task_id: str, pr_id: str = "") -> None:
        key = (validation_status or classification or "gap", gate, classification)
        bucket = grouped.setdefault(
            key,
            {
                "validationStatus": validation_status,
                "gate": gate,
                "classification": classification,
                "occurrences": 0,
                "lastSeenAt": "",
                "evidencePointers": [],
                "evidenceSeen": set(),
            },
        )
        bucket["occurrences"] += 1
        bucket["lastSeenAt"] = max(bucket["lastSeenAt"], run_id)
        for pointer in _experience_evidence_pointers(
            [],
            run_id=run_id,
            task_id=task_id,
            gate=gate,
            pr_id=pr_id,
        ):
            if pointer in bucket["evidenceSeen"]:
                continue
            bucket["evidenceSeen"].add(pointer)
            if len(bucket["evidencePointers"]) < EXPERIENCE_MAX_EVIDENCE_ITEMS:
                bucket["evidencePointers"].append(pointer)

    for row in task_rows:
        validation_status = _experience_text(row.get("validation_status"), max_chars=32).lower()
        if validation_status not in {"validation_pending", "skipped", "no_tests_found"}:
            continue
        _add_gap(
            validation_status=validation_status,
            gate="",
            classification="",
            run_id=_experience_text(row.get("run_id"), max_chars=32),
            task_id=_experience_text(row.get("task_id"), max_chars=32),
            pr_id=_experience_text(row.get("pr_id"), max_chars=32),
        )
    for row in validation_rows:
        validation_status = _experience_text(row.get("status"), max_chars=32).lower()
        classification = _experience_text(row.get("classification"), max_chars=48).lower()
        if validation_status not in {"skipped"} and classification not in {"no_tests_found", "blocked_env"}:
            continue
        _add_gap(
            validation_status=validation_status,
            gate=_experience_text(row.get("gate"), max_chars=48).lower(),
            classification=classification,
            run_id=_experience_text(row.get("run_id"), max_chars=32),
            task_id=_experience_text(row.get("task_id"), max_chars=32),
        )
    items: list[dict[str, Any]] = []
    for bucket in grouped.values():
        items.append(
            {
                "validationStatus": bucket["validationStatus"],
                "gate": bucket["gate"],
                "classification": bucket["classification"],
                "occurrences": int(bucket.get("occurrences") or 0),
                "summary": _experience_validation_gap_label(
                    str(bucket.get("validationStatus") or ""),
                    str(bucket.get("gate") or ""),
                    str(bucket.get("classification") or ""),
                ),
                "evidenceCount": len(bucket["evidenceSeen"]),
                "evidencePointers": list(bucket.get("evidencePointers") or []),
                "lastSeenAt": bucket.get("lastSeenAt") or "",
            }
        )
    items.sort(key=lambda item: (_experience_sort_key(item.get("lastSeenAt")), int(item.get("occurrences") or 0)), reverse=True)
    return items[:EXPERIENCE_MAX_ITEMS]


def _experience_merge_blocker_label(status: str, task_status: str, validation_status: str) -> str:
    blocker = task_status or status or validation_status
    if blocker == "review_required":
        return "Manual review is still required before merge"
    if blocker == "blocked_env":
        return "Environment blocker still prevents merge"
    if blocker == "test_contract_changed":
        return "Contract update review still blocks merge"
    if blocker == "regression_failed":
        return "Regression failure still blocks merge"
    if validation_status in {"validation_pending", "skipped", "no_tests_found"}:
        return "Merge is blocked on incomplete validation"
    return "Merge remains blocked"


def _experience_merge_blockers(task_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for row in task_rows:
        status = _experience_text(row.get("status"), max_chars=32).lower()
        task_status = _experience_text(row.get("task_status"), max_chars=32).lower()
        validation_status = _experience_text(row.get("validation_status"), max_chars=32).lower()
        blocker_key = task_status or status or validation_status
        if blocker_key not in {
            "review_required",
            "blocked_env",
            "test_contract_changed",
            "regression_failed",
            "validation_pending",
            "skipped",
            "no_tests_found",
        }:
            continue
        run_id = _experience_text(row.get("run_id"), max_chars=32)
        task_id = _experience_text(row.get("task_id"), max_chars=32)
        pr_id = _experience_text(row.get("pr_id"), max_chars=32)
        key = (pr_id or task_id or run_id or blocker_key, status, task_status, validation_status)
        bucket = grouped.setdefault(
            key,
            {
                "status": status,
                "taskStatus": task_status,
                "validationStatus": validation_status,
                "taskId": task_id,
                "prId": pr_id,
                "occurrences": 0,
                "lastSeenAt": "",
                "evidencePointers": [],
                "evidenceSeen": set(),
            },
        )
        bucket["occurrences"] += 1
        bucket["lastSeenAt"] = max(bucket["lastSeenAt"], run_id)
        for pointer in _experience_evidence_pointers([], run_id=run_id, task_id=task_id, pr_id=pr_id):
            if pointer in bucket["evidenceSeen"]:
                continue
            bucket["evidenceSeen"].add(pointer)
            if len(bucket["evidencePointers"]) < EXPERIENCE_MAX_EVIDENCE_ITEMS:
                bucket["evidencePointers"].append(pointer)
    items: list[dict[str, Any]] = []
    for bucket in grouped.values():
        items.append(
            {
                "status": bucket["status"],
                "taskStatus": bucket["taskStatus"],
                "validationStatus": bucket["validationStatus"],
                "taskId": bucket["taskId"],
                "prId": bucket["prId"],
                "occurrences": int(bucket.get("occurrences") or 0),
                "summary": _experience_merge_blocker_label(
                    str(bucket.get("status") or ""),
                    str(bucket.get("taskStatus") or ""),
                    str(bucket.get("validationStatus") or ""),
                ),
                "evidenceCount": len(bucket["evidenceSeen"]),
                "evidencePointers": list(bucket.get("evidencePointers") or []),
                "lastSeenAt": bucket.get("lastSeenAt") or "",
            }
        )
    items.sort(key=lambda item: (_experience_sort_key(item.get("lastSeenAt")), int(item.get("occurrences") or 0)), reverse=True)
    return items[:EXPERIENCE_MAX_ITEMS]


def _experience_from_summary_payload(summary_payload: dict[str, Any]) -> dict[str, Any]:
    run_id = _experience_text(summary_payload.get("run_id"), max_chars=32)
    recent_lessons: list[dict[str, Any]] = []
    for item in list(_experience_json_list(summary_payload.get("task_lessons"))) + list(_experience_json_list(summary_payload.get("validation_lessons"))):
        if not isinstance(item, dict):
            continue
        lesson = _experience_text(item.get("lesson"), max_chars=240)
        if not lesson:
            continue
        task_id = _experience_text(item.get("task_id"), max_chars=32)
        evidence = _experience_evidence_pointers(item.get("evidence"), run_id=run_id, task_id=task_id)
        recent_lessons.append(
            {
                "kind": _experience_text(item.get("kind"), max_chars=32) or "general",
                "severity": _experience_text(item.get("severity"), max_chars=16) or "medium",
                "confidence": _coerce_optional_float(item.get("confidence")) or 0.0,
                "trigger": "",
                "lesson": lesson,
                "evidenceCount": max(len(_experience_json_list(item.get("evidence"))), len(evidence)),
                "evidencePointers": evidence,
                "lastSeenAt": run_id,
                "createdAt": run_id,
                "seenCount": 1,
                "runId": run_id,
                "taskId": task_id,
            }
        )
    merge_blockers: list[dict[str, Any]] = []
    for hint in _experience_json_list(summary_payload.get("merge_hints")):
        text = _experience_text(hint, max_chars=180)
        if not text:
            continue
        merge_blockers.append(
            {
                "status": "review_required",
                "taskStatus": "review_required",
                "validationStatus": "",
                "taskId": "",
                "prId": "",
                "occurrences": 1,
                "summary": text,
                "evidenceCount": 1 if run_id else 0,
                "evidencePointers": _experience_evidence_pointers([], run_id=run_id),
                "lastSeenAt": run_id,
            }
        )
    validation_gaps: list[dict[str, Any]] = []
    for hint in _experience_json_list(summary_payload.get("pm_hints")):
        text = _experience_text(hint, max_chars=180)
        lowered = text.lower()
        if not text or not any(token in lowered for token in ("validation", "test", "no_tests", "no tests", "skipped")):
            continue
        validation_gaps.append(
            {
                "validationStatus": "validation_pending",
                "gate": "",
                "classification": "",
                "occurrences": 1,
                "summary": text,
                "evidenceCount": 1 if run_id else 0,
                "evidencePointers": _experience_evidence_pointers([], run_id=run_id),
                "lastSeenAt": run_id,
            }
        )
    return {
        "recentLessons": recent_lessons[:EXPERIENCE_MAX_ITEMS],
        "failurePatterns": [],
        "validationGaps": validation_gaps[:EXPERIENCE_MAX_ITEMS],
        "mergeBlockers": merge_blockers[:EXPERIENCE_MAX_ITEMS],
        "summaryText": _experience_text(summary_payload.get("summary"), max_chars=240),
    }


def _build_experience_payload(repo: Path, run_dir: Path | None) -> dict[str, Any]:
    db_path = next((path for path in _experience_db_candidates(repo) if path.exists() and path.is_file()), None)
    summary_payload: dict[str, Any] = {}
    summary_source = ""
    for candidate in _experience_summary_candidates(repo, run_dir):
        payload = _safe_json(candidate, {})
        if isinstance(payload, dict) and payload:
            summary_payload = payload
            summary_source = candidate.name
            break

    recent_lessons: list[dict[str, Any]] = []
    failure_patterns: list[dict[str, Any]] = []
    validation_gaps: list[dict[str, Any]] = []
    merge_blockers: list[dict[str, Any]] = []
    source = "unavailable"
    available = False
    message = EXPERIENCE_UNAVAILABLE_MESSAGE
    summary_text = _experience_text(summary_payload.get("summary"), max_chars=240)

    if db_path is not None:
        lesson_rows = _experience_table_rows(db_path, "lessons")
        task_rows = _experience_table_rows(db_path, "task_experiences")
        validation_rows = _experience_table_rows(db_path, "validation_experiences")
        recent_lessons = _experience_lesson_items(lesson_rows, task_rows)
        failure_patterns = _experience_failure_patterns(validation_rows)
        validation_gaps = _experience_validation_gaps(task_rows, validation_rows)
        merge_blockers = _experience_merge_blockers(task_rows)
        available = True
        source = "experience_db"
        message = summary_text or EXPERIENCE_EMPTY_MESSAGE
        if recent_lessons or failure_patterns or validation_gaps or merge_blockers:
            message = summary_text or "Read-only Experience insights were loaded from the Experience DB."
    elif summary_payload:
        summary_data = _experience_from_summary_payload(summary_payload)
        recent_lessons = list(summary_data.get("recentLessons") or [])
        failure_patterns = list(summary_data.get("failurePatterns") or [])
        validation_gaps = list(summary_data.get("validationGaps") or [])
        merge_blockers = list(summary_data.get("mergeBlockers") or [])
        summary_text = _experience_text(summary_data.get("summaryText"), max_chars=240)
        available = True
        source = "summary"
        message = summary_text or EXPERIENCE_SUMMARY_FALLBACK_MESSAGE

    total_items = len(recent_lessons) + len(failure_patterns) + len(validation_gaps) + len(merge_blockers)
    state = "ready" if total_items else ("partial" if source == "summary" else ("empty" if available else "unavailable"))
    if state == "empty":
        message = summary_text or EXPERIENCE_EMPTY_MESSAGE
    elif state == "unavailable":
        message = EXPERIENCE_UNAVAILABLE_MESSAGE

    payload = {
        "available": available,
        "state": state,
        "source": source,
        "message": message,
        "summaryText": summary_text,
        "summary_text": summary_text,
        "recentLessons": recent_lessons,
        "recent_lessons": recent_lessons,
        "failurePatterns": failure_patterns,
        "failure_patterns": failure_patterns,
        "validationGaps": validation_gaps,
        "validation_gaps": validation_gaps,
        "mergeBlockers": merge_blockers,
        "merge_blockers": merge_blockers,
        "summary": {
            "source": source,
            "dbAvailable": db_path is not None,
            "db_available": db_path is not None,
            "summaryAvailable": bool(summary_payload),
            "summary_available": bool(summary_payload),
            "summarySource": summary_source,
            "summary_source": summary_source,
            "lessons": len(recent_lessons),
            "failurePatterns": len(failure_patterns),
            "failure_patterns": len(failure_patterns),
            "validationGaps": len(validation_gaps),
            "validation_gaps": len(validation_gaps),
            "mergeBlockers": len(merge_blockers),
            "merge_blockers": len(merge_blockers),
            "total": total_items,
        },
    }
    return payload


def _pr_queue_string_list(value: Any, *, limit: int = 100) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        try:
            raw_items = list(value)
        except Exception:
            raw_items = [value]
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max(1, int(limit)):
            break
    return items


def _pr_queue_object_list(value: Any, *, limit: int = 100) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple):
        raw_items = list(value)
    else:
        raw_items = [value]
    return [item for item in raw_items if item not in (None, "")][: max(1, int(limit))]


def _pr_queue_goal_ref(item: Any) -> str:
    if isinstance(item, dict):
        return _pick_text(
            item.get("goal_ref"),
            item.get("goalRef"),
            item.get("ref"),
            item.get("id"),
            item.get("goal_id"),
            item.get("goalId"),
            item.get("text"),
            item.get("goal_text"),
            item.get("goalText"),
        )
    return str(item or "").strip()


def _pr_queue_run_dir(repo_root: Path, packet: dict[str, Any]) -> Path | None:
    run_id = _pick_text(packet.get("run_id"), packet.get("runId"))
    run_dir_text = _pick_text(packet.get("run_dir"), packet.get("runDir"))
    candidates: list[Path] = []
    if run_dir_text:
        candidates.append(Path(run_dir_text).expanduser())
    if run_id:
        candidates.append(repo_root / ".AgentCLI" / "agent_runs" / run_id)
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved.exists() and resolved.is_dir():
            return resolved
    return None


def _pr_queue_resolve_agent_artifact(repo_root: Path, path_text: str) -> Path | None:
    raw = str(path_text or "").strip()
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = repo_root / raw
        resolved = candidate.resolve()
    except Exception:
        return None
    agent_root = (repo_root / ".AgentCLI").resolve()
    try:
        if not _path_is_within(resolved, agent_root):
            return None
    except Exception:
        return None
    return resolved


def _pr_queue_packet_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        return ""
    return text


def _pr_queue_packet_paths(repo_root: Path) -> list[Path]:
    queue_root = pr_queue_root(repo_root)
    if not queue_root.exists() or not queue_root.is_dir():
        return []
    paths = [
        path
        for path in queue_root.glob("*.json")
        if path.is_file() and path.name != "branch_index.json"
    ]
    try:
        index = load_branch_index(repo_root)
    except Exception:
        index = {"entries": []}
    ordered: list[Path] = []
    seen: set[str] = set()
    for entry in index.get("entries", []) if isinstance(index, dict) else []:
        if not isinstance(entry, dict):
            continue
        packet_path_text = _pick_text(entry.get("packet_path"), entry.get("packetPath"))
        packet_id = _pr_queue_packet_id(entry.get("id"))
        candidate: Path | None = None
        if packet_path_text:
            candidate = _pr_queue_resolve_agent_artifact(repo_root, packet_path_text)
        if candidate is None and packet_id:
            try:
                candidate = pr_packet_path(repo_root, packet_id).resolve()
            except Exception:
                candidate = None
        if candidate is not None and candidate.exists() and candidate.is_file():
            key = candidate.as_posix()
            if key not in seen:
                seen.add(key)
                ordered.append(candidate)
    for path in sorted(paths, key=lambda item: (item.stat().st_mtime if item.exists() else 0, item.name), reverse=True):
        try:
            key = path.resolve().as_posix()
        except Exception:
            key = path.as_posix()
        if key not in seen:
            seen.add(key)
            ordered.append(path)
    return ordered[:PR_QUEUE_MAX_ITEMS]


def _pr_queue_load_packet(path: Path) -> dict[str, Any]:
    payload = _safe_json(path, {})
    return payload if isinstance(payload, dict) else {}


def _pr_queue_find_packet(repo_root: Path, packet_id: str) -> tuple[Path, dict[str, Any]] | None:
    packet_id_text = _pr_queue_packet_id(packet_id)
    if not packet_id_text:
        return None
    queue_root = pr_queue_root(repo_root).resolve()
    try:
        candidate = pr_packet_path(repo_root, packet_id_text).resolve()
    except Exception:
        return None
    if not _path_is_within(candidate, queue_root) or not candidate.exists() or not candidate.is_file():
        return None
    packet = _pr_queue_load_packet(candidate)
    if not packet:
        return None
    return candidate, packet


def _pr_queue_artifact_preview(path: Path) -> dict[str, Any]:
    exists = path.exists() and path.is_file()
    preview = ""
    truncated = False
    size = 0
    if exists:
        try:
            size = path.stat().st_size
        except Exception:
            size = 0
        dq: deque[str] = deque(maxlen=PR_QUEUE_ARTIFACT_PREVIEW_LINES)
        char_count = 0
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    text = line.rstrip("\n")
                    char_count += len(text) + 1
                    if char_count > PR_QUEUE_ARTIFACT_PREVIEW_CHARS:
                        truncated = True
                        break
                    dq.append(text)
            preview = "\n".join(dq).strip()
            if size > PR_QUEUE_ARTIFACT_PREVIEW_CHARS:
                truncated = True
        except Exception:
            preview = ""
    return {
        "path": path.as_posix(),
        "name": path.name,
        "exists": exists,
        "size": size,
        "preview": preview,
        "truncated": truncated,
    }


def _pr_queue_artifact_records(repo_root: Path, paths: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path_text in paths:
        resolved = _pr_queue_resolve_agent_artifact(repo_root, path_text)
        if resolved is None:
            records.append(
                {
                    "path": str(path_text or ""),
                    "name": Path(str(path_text or "")).name,
                    "exists": False,
                    "size": 0,
                    "preview": "",
                    "truncated": False,
                    "blocked": True,
                    "reason": "outside_agentcli_artifact_root",
                }
            )
            continue
        key = resolved.as_posix()
        if key in seen:
            continue
        seen.add(key)
        records.append(_pr_queue_artifact_preview(resolved))
        if len(records) >= PR_QUEUE_MAX_VALIDATION_ARTIFACTS:
            break
    return records


def _pr_queue_diff_artifact_paths(repo_root: Path, packet: dict[str, Any], run_dir: Path | None) -> list[str]:
    paths = _pr_queue_string_list(
        _pick_value(packet.get("diff_artifacts"), packet.get("diffArtifacts")),
        limit=PR_QUEUE_MAX_VALIDATION_ARTIFACTS,
    )
    for key in ("patch_path", "patchPath", "patch", "diff_artifact", "diffArtifact"):
        value = _pick_text(packet.get(key))
        if value and value not in paths:
            paths.append(value)
    preflight = packet.get("merge_preflight") if isinstance(packet.get("merge_preflight"), dict) else packet.get("mergePreflight")
    if isinstance(preflight, dict):
        for key in ("patch_path", "patchPath", "patch", "diff_artifact", "diffArtifact"):
            value = _pick_text(preflight.get(key))
            if value and value not in paths:
                paths.append(value)
    if run_dir is not None:
        for name in ("worktree.patch", "worktree_dirty_uncommitted.patch"):
            candidate = run_dir / name
            if candidate.exists():
                value = candidate.as_posix()
                if value not in paths:
                    paths.append(value)
    return paths[:PR_QUEUE_MAX_VALIDATION_ARTIFACTS]


def _pr_queue_changed_files(repo_root: Path, packet: dict[str, Any], run_dir: Path | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    diff_artifact_paths = _pr_queue_diff_artifact_paths(repo_root, packet, run_dir)
    diff_artifacts = _pr_queue_artifact_records(repo_root, diff_artifact_paths)
    diff_files: list[dict[str, Any]] = []
    for artifact in diff_artifacts:
        path = _pr_queue_resolve_agent_artifact(repo_root, str(artifact.get("path") or ""))
        if path is None or not path.exists() or path.suffix.lower() != ".patch":
            continue
        try:
            diff_files = summarize_worktree_diff(
                path,
                allow_placeholder=False,
                max_files=PR_QUEUE_MAX_CHANGED_FILES,
                max_hunks_per_file=4,
                max_hunk_lines=10,
                max_preview_chars=PR_QUEUE_ARTIFACT_PREVIEW_CHARS,
            )
        except Exception:
            diff_files = []
        if diff_files:
            break
    if diff_files:
        return diff_files[:PR_QUEUE_MAX_CHANGED_FILES], diff_artifacts

    raw_changed = _pick_value(packet.get("changed_files"), packet.get("changedFiles"))
    changed: list[dict[str, Any]] = []
    for item in _pr_queue_object_list(raw_changed, limit=PR_QUEUE_MAX_CHANGED_FILES):
        if isinstance(item, dict):
            path = _pick_text(item.get("path"), item.get("file"), item.get("name"), item.get("newPath"), item.get("new_path"))
            if not path:
                continue
            changed.append(
                {
                    "path": path,
                    "oldPath": _pick_text(item.get("oldPath"), item.get("old_path"), item.get("sourcePath"), item.get("source_path"), path),
                    "newPath": _pick_text(item.get("newPath"), item.get("new_path"), item.get("targetPath"), item.get("target_path"), path),
                    "kind": _pick_text(item.get("kind"), item.get("state"), item.get("type"), "modified"),
                    "state": _pick_text(item.get("state"), item.get("kind"), item.get("type"), "modified"),
                    "summary": _pick_text(item.get("summary"), item.get("note"), item.get("message"), "Packet changed file"),
                    "note": _pick_text(item.get("note"), item.get("message")),
                    "binary": bool(item.get("binary")),
                    "deleted": bool(item.get("deleted")),
                    "renamed": bool(item.get("renamed")),
                    "large": bool(item.get("large")),
                    "truncated": bool(item.get("truncated")),
                    "hunks": item.get("hunks") if isinstance(item.get("hunks"), list) else [],
                    "lineCount": _coerce_optional_int(item.get("lineCount") or item.get("line_count")) or 0,
                }
            )
        else:
            path = str(item or "").strip()
            if path:
                changed.append(
                    {
                        "path": path,
                        "oldPath": path,
                        "newPath": path,
                        "kind": "modified",
                        "state": "modified",
                        "summary": "Packet changed file",
                        "note": "",
                        "binary": False,
                        "deleted": False,
                        "renamed": False,
                        "large": False,
                        "truncated": False,
                        "hunks": [],
                        "lineCount": 0,
                    }
                )
    return changed, diff_artifacts


def _pr_queue_validation_payload(repo_root: Path, packet: dict[str, Any]) -> dict[str, Any]:
    validation_artifacts = _pr_queue_string_list(
        _pick_value(packet.get("validation_artifacts"), packet.get("validationArtifacts")),
        limit=PR_QUEUE_MAX_VALIDATION_ARTIFACTS,
    )
    artifact_path = _pick_text(packet.get("validation_artifact_path"), packet.get("validationArtifactPath"))
    if artifact_path and artifact_path not in validation_artifacts:
        validation_artifacts.insert(0, artifact_path)
    artifact_records = _pr_queue_artifact_records(repo_root, validation_artifacts)
    summary_payload: dict[str, Any] = {}
    if artifact_path:
        resolved = _pr_queue_resolve_agent_artifact(repo_root, artifact_path)
        if resolved is not None:
            summary_payload = _safe_json(resolved, {})
            if not isinstance(summary_payload, dict):
                summary_payload = {}
    validation_records = summary_payload.get("validation_records") if isinstance(summary_payload.get("validation_records"), list) else summary_payload.get("validationRecords")
    if not isinstance(validation_records, list):
        validation_records = []
    validation_summary = summary_payload.get("validation_summary") if isinstance(summary_payload.get("validation_summary"), dict) else summary_payload.get("validationSummary")
    if not isinstance(validation_summary, dict):
        validation_summary = {}
    return {
        "status": _pick_text(packet.get("validation_status"), packet.get("validationStatus"), summary_payload.get("validation_status"), summary_payload.get("validationStatus"), summary_payload.get("status"), "validation_pending"),
        "reason": _pick_text(packet.get("validation_reason"), packet.get("validationReason"), summary_payload.get("validation_reason"), summary_payload.get("validationReason"), summary_payload.get("reason")),
        "detail": _pick_text(packet.get("validation_detail"), packet.get("validationDetail"), summary_payload.get("validation_detail"), summary_payload.get("validationDetail"), summary_payload.get("detail")),
        "artifactPath": artifact_path,
        "artifact_path": artifact_path,
        "artifacts": artifact_records,
        "validation_artifacts": artifact_records,
        "records": [dict(item) for item in validation_records if isinstance(item, dict)][:PR_QUEUE_MAX_VALIDATION_ARTIFACTS],
        "summary": dict(validation_summary),
    }


def _pr_queue_preflight_payload(packet: dict[str, Any]) -> dict[str, Any]:
    preflight = packet.get("merge_preflight") if isinstance(packet.get("merge_preflight"), dict) else packet.get("mergePreflight")
    preflight_payload = dict(preflight) if isinstance(preflight, dict) else {}
    preflight_payload.setdefault("base_ref", _pick_text(packet.get("base_ref"), packet.get("baseRef")))
    preflight_payload.setdefault("head_ref", _pick_text(packet.get("head_ref"), packet.get("headRef")))
    preflight_payload.setdefault("branch", _pick_text(packet.get("branch")))
    source_main_mutated = bool(packet.get("source_main_mutated") or packet.get("sourceMainMutated") or preflight_payload.get("source_main_mutated") or preflight_payload.get("sourceMainMutated"))
    preflight_payload["source_main_mutated"] = source_main_mutated
    preflight_payload["sourceMainMutated"] = source_main_mutated
    apply_check = preflight_payload.get("applyCheck") if isinstance(preflight_payload.get("applyCheck"), dict) else preflight_payload.get("apply_check")
    if isinstance(apply_check, dict):
        preflight_payload["applyCheck"] = dict(apply_check)
        preflight_payload["apply_check"] = dict(apply_check)
    return preflight_payload


def _pr_queue_blocking_reasons(packet: dict[str, Any], validation: dict[str, Any], preflight: dict[str, Any], changed_files: list[dict[str, Any]]) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []

    def add(kind: str, message: str, detail: str = "") -> None:
        if not message:
            return
        item = {"kind": kind, "message": message, "detail": detail}
        if item not in reasons:
            reasons.append(item)

    packet_status = _pick_text(packet.get("status"), "pr_queued")
    if packet_status not in {"pr_queued", "queued", "review_required"}:
        add("packet_status", f"Packet status is {packet_status}.", _pick_text(packet.get("recoverable_reason"), packet.get("recoverableReason")))
    recoverable_reason = _pick_text(packet.get("recoverable_reason"), packet.get("recoverableReason"))
    if recoverable_reason:
        add("recoverable", recoverable_reason)
    for field in ("base_ref", "head_ref", "branch"):
        if not _pick_text(packet.get(field), packet.get(field.replace("_", ""))):
            add("missing_metadata", f"Missing {field}.")
    validation_status = _pick_text(validation.get("status"), "validation_pending")
    if validation_status != "validation_passed":
        add("validation", f"Validation status is {validation_status}.", _pick_text(validation.get("reason"), validation.get("detail")))
    if validation.get("detail") and validation_status in {"validation_failed", "blocked_env", "test_contract_changed"}:
        add("validation_detail", _pick_text(validation.get("detail")), _pick_text(validation.get("reason")))
    if bool(preflight.get("source_main_mutated") or preflight.get("sourceMainMutated")):
        add("source_head", "Source repository HEAD changed during validation.")
    source_state = _pick_text(preflight.get("source_repo_state"), preflight.get("sourceRepoState"))
    if source_state and source_state != "clean":
        add("source_dirty", f"Source repository state is {source_state}.")
    for key in ("applyCheck", "apply_check", "dirtyPatchCheck", "dirty_patch_check"):
        check = preflight.get(key)
        if isinstance(check, dict) and check:
            ok_value = check.get("ok")
            status = _pick_text(check.get("status"))
            if ok_value is False or status in {"failed", "error"}:
                add("merge_preflight", _pick_text(check.get("message"), f"{key} failed."), _pick_text(check.get("output"), check.get("reason")))
    if not changed_files:
        add("diff", "No changed files are available for this packet.")
    return reasons


def _pr_queue_packet_payload(repo_root: Path, path: Path, packet: dict[str, Any], *, detail: bool = False) -> dict[str, Any]:
    run_dir = _pr_queue_run_dir(repo_root, packet)
    changed_files, diff_artifacts = _pr_queue_changed_files(repo_root, packet, run_dir)
    validation = _pr_queue_validation_payload(repo_root, packet)
    preflight = _pr_queue_preflight_payload(packet)
    goal_trace = _pr_queue_object_list(_pick_value(packet.get("goal_trace"), packet.get("goalTrace")), limit=PR_QUEUE_MAX_GOAL_REFS)
    goal_refs = [_pr_queue_goal_ref(item) for item in goal_trace]
    goal_refs = [item for item in goal_refs if item][:PR_QUEUE_MAX_GOAL_REFS]
    task_ids = _pr_queue_string_list(_pick_value(packet.get("task_ids"), packet.get("taskIds")), limit=20)
    commits = _pr_queue_object_list(_pick_value(packet.get("commits")), limit=PR_QUEUE_MAX_COMMITS)
    qa_notes = _pr_queue_string_list(_pick_value(packet.get("qa_notes"), packet.get("qaNotes")), limit=PR_QUEUE_MAX_NOTES)
    blocking_reasons = _pr_queue_blocking_reasons(packet, validation, preflight, changed_files)
    packet_id = _pick_text(packet.get("id"), path.stem)
    base_ref = _pick_text(packet.get("base_ref"), packet.get("baseRef"), preflight.get("base_ref"), preflight.get("baseRef"))
    head_ref = _pick_text(packet.get("head_ref"), packet.get("headRef"), preflight.get("head_ref"), preflight.get("headRef"))
    branch = _pick_text(packet.get("branch"), preflight.get("branch"))
    status = _pick_text(packet.get("status"), "pr_queued")
    common = {
        "id": packet_id,
        "packetId": packet_id,
        "status": status,
        "runId": _pick_text(packet.get("run_id"), packet.get("runId")),
        "run_id": _pick_text(packet.get("run_id"), packet.get("runId")),
        "taskIds": task_ids,
        "task_ids": task_ids,
        "goalTrace": goal_trace,
        "goal_trace": goal_trace,
        "goalRefs": goal_refs,
        "goal_refs": goal_refs,
        "branch": branch,
        "baseRef": base_ref,
        "base_ref": base_ref,
        "headRef": head_ref,
        "head_ref": head_ref,
        "createdAt": _pick_text(packet.get("created_at"), packet.get("createdAt")),
        "created_at": _pick_text(packet.get("created_at"), packet.get("createdAt")),
        "updatedAt": _pick_text(packet.get("updated_at"), packet.get("updatedAt")),
        "updated_at": _pick_text(packet.get("updated_at"), packet.get("updatedAt")),
        "sourceRepo": _pick_text(packet.get("source_repo"), packet.get("sourceRepo"), repo_root.as_posix()),
        "source_repo": _pick_text(packet.get("source_repo"), packet.get("sourceRepo"), repo_root.as_posix()),
        "worktreeDir": _pick_text(packet.get("worktree_dir"), packet.get("worktreeDir")),
        "worktree_dir": _pick_text(packet.get("worktree_dir"), packet.get("worktreeDir")),
        "packetPath": path.as_posix(),
        "packet_path": path.as_posix(),
        "validationStatus": validation["status"],
        "validation_status": validation["status"],
        "mergePreflight": preflight,
        "merge_preflight": preflight,
        "mergePreflightStatus": "blocked" if any(item["kind"] in {"merge_preflight", "source_head", "source_dirty"} for item in blocking_reasons) else "ready",
        "merge_preflight_status": "blocked" if any(item["kind"] in {"merge_preflight", "source_head", "source_dirty"} for item in blocking_reasons) else "ready",
        "changedFileCount": len(changed_files),
        "changed_file_count": len(changed_files),
        "qaNotes": qa_notes,
        "qa_notes": qa_notes,
        "blockingReasons": blocking_reasons,
        "blocking_reasons": blocking_reasons,
        "commits": commits,
    }
    if not detail:
        return common
    common.update(
        {
            "changedFiles": changed_files,
            "changed_files": changed_files,
            "diffFiles": changed_files,
            "diff_files": changed_files,
            "diffArtifacts": diff_artifacts,
            "diff_artifacts": diff_artifacts,
            "validation": validation,
            "raw": {},
        }
    )
    return common


def _build_pr_queue_payload(repo_root: Path, *, packet_id: str | None = None, detail: bool = False) -> dict[str, Any]:
    queue_root = pr_queue_root(repo_root)
    if packet_id:
        found = _pr_queue_find_packet(repo_root, packet_id)
        if found is None:
            return {
                "ok": False,
                "state": "missing",
                "queueRoot": queue_root.as_posix(),
                "queue_root": queue_root.as_posix(),
                "items": [],
                "detail": None,
                "message": f"PR queue packet not found: {packet_id}",
            }
        path, packet = found
        detail_payload = _pr_queue_packet_payload(repo_root, path, packet, detail=True)
        return {
            "ok": True,
            "state": "ready",
            "queueRoot": queue_root.as_posix(),
            "queue_root": queue_root.as_posix(),
            "items": [detail_payload],
            "detail": detail_payload,
            "selectedId": detail_payload["id"],
            "selected_id": detail_payload["id"],
            "summary": {"total": 1, "blocked": len(detail_payload.get("blockingReasons", []))},
        }

    paths = _pr_queue_packet_paths(repo_root)
    items: list[dict[str, Any]] = []
    for path in paths:
        packet = _pr_queue_load_packet(path)
        if not packet:
            continue
        items.append(_pr_queue_packet_payload(repo_root, path, packet, detail=detail))
    selected_id = items[0]["id"] if items else ""
    detail_payload = items[0] if detail and items else None
    summary = {
        "total": len(items),
        "blocked": len([item for item in items if item.get("blockingReasons")]),
        "validationPassed": len([item for item in items if item.get("validationStatus") == "validation_passed"]),
        "validationPending": len([item for item in items if item.get("validationStatus") == "validation_pending"]),
        "validationFailed": len([item for item in items if item.get("validationStatus") == "validation_failed"]),
        "blockedEnv": len([item for item in items if item.get("validationStatus") == "blocked_env"]),
    }
    return {
        "ok": True,
        "state": "ready" if items else "empty",
        "queueRoot": queue_root.as_posix(),
        "queue_root": queue_root.as_posix(),
        "items": items,
        "detail": detail_payload,
        "selectedId": selected_id,
        "selected_id": selected_id,
        "summary": summary,
        "message": "" if items else fallbackSectionMessage("prQueue"),
    }


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


def _coerce_optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
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
    if reason in {"project_complete", "all_tasks_done", "completed", "success", "ok", "done", GOALS_INCOMPLETE_STATUS}:
        return "completed"
    if reason in {"stop_file", "stop_requested", "stopped", "user_stop", "manual_stop"} or stop_file_exists:
        return "stopped"

    rc = _coerce_optional_int(exit_code)
    if rc is not None:
        if rc == 0 and reason in {"", "ok", "prepared_only", "completed", "success", "project_complete", "all_tasks_done", "done", GOALS_INCOMPLETE_STATUS} and has_run_dir:
            return "completed"
        if rc != 0:
            return "failed"

    if reason in {"failed", "error", "exception", "abandoned", "abandon_failed", "build_failed", "test_failed", "fast_regression_failed", "policy_violation", "exhausted_attempts"}:
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


def _completion_status_payload(run_dir: Path | None, *, final_reason: str = "") -> dict[str, Any]:
    completion_payload: dict[str, Any] = {}
    if run_dir is not None:
        payload = _safe_json(run_dir / "COMPLETION_STATUS.json", {})
        if isinstance(payload, dict):
            completion_payload = payload

    completion_status = _pick_text(
        completion_payload.get("completion_status"),
        completion_payload.get("completionStatus"),
    ).strip().lower()
    completion_reason = _pick_text(
        completion_payload.get("completion_reason"),
        completion_payload.get("completionReason"),
    ).strip().lower()
    resolved_final_reason = _pick_text(final_reason).strip().lower()

    if not completion_status and resolved_final_reason in {STOP_REASON_PROJECT_COMPLETE, GOALS_INCOMPLETE_STATUS}:
        completion_status = resolved_final_reason
    if not completion_reason and completion_status:
        completion_reason = completion_status
    if completion_status in {STOP_REASON_PROJECT_COMPLETE, GOALS_INCOMPLETE_STATUS} and resolved_final_reason in {
        "",
        "ok",
        STOP_REASON_PROJECT_COMPLETE,
        GOALS_INCOMPLETE_STATUS,
    }:
        resolved_final_reason = completion_status

    return {
        "completion_status": completion_status,
        "completionStatus": completion_status,
        "completion_reason": completion_reason,
        "completionReason": completion_reason,
        "final_reason": resolved_final_reason,
        "finalReason": resolved_final_reason,
    }




def _config_restore_error(status_code: int, code: str, message: str, **details: Any) -> JSONResponse:
    payload: dict[str, Any] = {
        "ok": False,
        "action": "config-restore",
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


def _path_mtime_ms(path: Path | None) -> int | None:
    if path is None:
        return None
    try:
        return int(path.stat().st_mtime_ns / 1_000_000)
    except Exception:
        return None


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


def _resolve_trusted_operator_gate_enabled() -> tuple[bool, str]:
    # Placeholder for a future authenticated or otherwise stronger operator gate.
    return False, "not-implemented"


def _lan_safety_details(bind_host: str, *, trusted_network: bool | None = None) -> dict[str, Any]:
    gate_enabled, gate_source = _resolve_trusted_operator_gate_enabled()
    return {
        "safety": "lan",
        "scope": "lan",
        "bind_host": str(bind_host or "").strip() or "0.0.0.0",
        "trusted_network": bool(trusted_network),
        "trusted_operator_gate": bool(gate_enabled),
        "trusted_operator_gate_source": gate_source,
    }


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
    host_label = str(bind_host or "").strip() or "0.0.0.0"
    combined_source = f"{source};{trusted_source}" if trusted_enabled and source else (trusted_source if trusted_enabled else source)
    gate_enabled, gate_source = _resolve_trusted_operator_gate_enabled()
    if trusted_enabled and gate_enabled:
        return True, f"{combined_source};{gate_source}", ""

    if trusted_enabled:
        return False, combined_source, LAN_SAFETY_MUTATION_DISABLED_MESSAGE
    return False, combined_source, f"{LAN_SAFETY_MUTATION_DISABLED_MESSAGE} Bind host: {host_label}."


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
    control_event: dict[str, Any] = {}
    if str(current_run_dir or "").strip():
        try:
            control_event = read_runner_control_event(Path(current_run_dir))
        except Exception:
            control_event = {}
    control_event_current = control_event.get("current_event") if isinstance(control_event.get("current_event"), dict) else {}
    control_event_history = list(control_event.get("history") or [])
    control_event_last_event = str(
        (control_event_current.get("phase") if isinstance(control_event_current, dict) else "")
        or (control_event_current.get("status") if isinstance(control_event_current, dict) else "")
        or control_event.get("phase")
        or control_event.get("status")
        or ""
    ).strip()
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
        stop_progress = control_event.get("stop_progress")
        if not isinstance(stop_progress, dict):
            stop_progress = {}
        else:
            stop_progress = normalize_stop_progress_payload(stop_progress)
        return {
            "running": bool(control_event.get("running")) if control_event else False,
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
            "last_event": control_event_last_event,
            "last_action": str(control_event.get("last_action") or control_event.get("action") or ""),
            "lastAction": str(control_event.get("last_action") or control_event.get("action") or ""),
            "last_message": str(control_event.get("last_message") or control_event.get("message") or ""),
            "lastMessage": str(control_event.get("last_message") or control_event.get("message") or ""),
            "last_error": str(control_event.get("last_error") or control_event.get("error") or ""),
            "lastError": str(control_event.get("last_error") or control_event.get("error") or ""),
            "current_event": control_event_current,
            "currentEvent": control_event_current,
            "history": control_event_history,
            "event_history": control_event_history,
            "eventHistory": control_event_history,
            "event_count": len(control_event_history),
            "eventCount": len(control_event_history),
            "stop_progress": stop_progress,
            "stopProgress": stop_progress,
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
        stop_progress = control_event.get("stop_progress")
        if not isinstance(stop_progress, dict):
            stop_progress = {}
        else:
            stop_progress = normalize_stop_progress_payload(stop_progress)
        return {
            "running": bool(control_event.get("running")) if control_event else False,
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
            "last_event": control_event_last_event,
            "last_action": str(control_event.get("last_action") or control_event.get("action") or ""),
            "lastAction": str(control_event.get("last_action") or control_event.get("action") or ""),
            "last_message": str(control_event.get("last_message") or control_event.get("message") or ""),
            "lastMessage": str(control_event.get("last_message") or control_event.get("message") or ""),
            "last_error": REDACTED_VALUE if redact_sensitive else str(control_event.get("last_error") or control_event.get("error") or f"status_error: {ex}"),
            "lastError": REDACTED_VALUE if redact_sensitive else str(control_event.get("last_error") or control_event.get("error") or f"status_error: {ex}"),
            "current_event": control_event_current,
            "currentEvent": control_event_current,
            "history": control_event_history,
            "event_history": control_event_history,
            "eventHistory": control_event_history,
            "event_count": len(control_event_history),
            "eventCount": len(control_event_history),
            "stop_progress": stop_progress,
            "stopProgress": stop_progress,
            "start_options": start_options_contract,
            "startOptions": start_options_contract,
        }

    if not isinstance(status, dict):
        status = {}
    if redact_sensitive and isinstance(status, dict):
        status = _redact_web_runner_status_payload(status, redact_start_options=True)

    stop_progress = status.get("stop_progress")
    if not isinstance(stop_progress, dict):
        stop_progress = control_event.get("stop_progress") if isinstance(control_event.get("stop_progress"), dict) else {}
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
        "last_event": str(status.get("last_event") or control_event_last_event or "").strip(),
        "last_action": str(status.get("last_action") or status.get("lastAction") or control_event.get("last_action") or control_event.get("action") or "").strip(),
        "lastAction": str(status.get("last_action") or status.get("lastAction") or control_event.get("last_action") or control_event.get("action") or "").strip(),
        "last_message": str(status.get("last_message") or status.get("lastMessage") or control_event.get("last_message") or control_event.get("message") or "").strip(),
        "lastMessage": str(status.get("last_message") or status.get("lastMessage") or control_event.get("last_message") or control_event.get("message") or "").strip(),
        "last_error": str(status.get("last_error") or status.get("lastError") or control_event.get("last_error") or control_event.get("error") or "").strip(),
        "lastError": str(status.get("last_error") or status.get("lastError") or control_event.get("last_error") or control_event.get("error") or "").strip(),
        "current_event": status.get("current_event") if isinstance(status.get("current_event"), dict) else control_event_current,
        "currentEvent": status.get("current_event") if isinstance(status.get("current_event"), dict) else control_event_current,
        "history": list(status.get("history") or control_event_history),
        "event_history": list(status.get("event_history") or status.get("eventHistory") or control_event_history),
        "eventHistory": list(status.get("eventHistory") or status.get("history") or control_event_history),
        "event_count": int(status.get("event_count") or status.get("eventCount") or len(control_event_history)),
        "eventCount": int(status.get("eventCount") or status.get("event_count") or len(control_event_history)),
        "stop_progress": stop_progress,
        "stopProgress": stop_progress,
        "start_options": start_options,
        "startOptions": start_options,
    }


def _live_state_status_label(status: str) -> str:
    value = str(status or "").strip().lower()
    labels = {
        "alive": "Alive",
        "flushing": "Flushing",
        "idle": "Idle",
        "stopped": "Stopped",
        "unavailable": "Unavailable",
    }
    return labels.get(value, value.replace("_", " ").title() if value else "Unavailable")


def _live_state_entry(
    kind: str,
    *,
    available: bool,
    status: str,
    source: str = "",
    alive: bool | None = None,
    flushing: bool | None = None,
    count: int | None = None,
    alive_count: int | None = None,
    phase: str = "",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": str(kind),
        "available": bool(available),
        "status": str(status or "").strip().lower() or "unavailable",
        "source": str(source or "").strip(),
    }
    if alive is not None:
        entry["alive"] = bool(alive)
    if flushing is not None:
        entry["flushing"] = bool(flushing)
    if count is not None:
        entry["count"] = max(0, int(count))
    if alive_count is not None:
        entry["alive_count"] = max(0, int(alive_count))
        entry["aliveCount"] = entry["alive_count"]
    if phase:
        entry["phase"] = str(phase).strip()
    entry["status_label"] = _live_state_status_label(entry["status"])
    entry["statusLabel"] = entry["status_label"]
    if not entry["available"]:
        entry["status"] = "unavailable"
        entry["status_label"] = _live_state_status_label("unavailable")
        entry["statusLabel"] = entry["status_label"]
    return entry


def _build_live_state_payload(
    controller_status: dict[str, Any] | None,
    *,
    progress: dict[str, Any] | None = None,
    active_run: dict[str, Any] | None = None,
    controller_available: bool = False,
) -> dict[str, Any]:
    return _web_payloads.build_live_state_payload(
        sys.modules[__name__],
        controller_status,
        progress=progress,
        active_run=active_run,
        controller_available=controller_available,
    )


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
    live_state: dict[str, Any] | None = None,
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
    web_instance: dict[str, Any] | None = None,
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
    status_last_action = _pick_text(last_action, status_payload.get("last_action"), status_payload.get("lastAction"))
    status_last_message = _pick_text(last_message, status_payload.get("last_message"), status_payload.get("lastMessage"))
    status_last_error = _pick_text(last_error, status_payload.get("last_error"), status_payload.get("lastError"))
    message = _runner_control_message(
        enabled=enabled,
        source=source,
        running=bool(status_payload.get("running")),
        controller_available=controller_available,
        disabled_reason=disabled_reason,
    )
    status_reason = str(status_payload.get("reason") or "").strip()
    message_sensitive = False
    if not enabled and disabled_reason == LAN_SAFETY_MUTATION_DISABLED_MESSAGE:
        message = disabled_reason
    elif status_last_error:
        message = status_last_error
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
    elif status_last_message and enabled:
        message = status_last_message
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
    instance_state = ""
    read_only = False
    duplicate_instance = False
    if isinstance(web_instance, dict):
        instance_state = str(web_instance.get("state") or "").strip().lower()
        read_only = str(web_instance.get("mode") or "").strip().lower() == "read_only"
        duplicate_instance = instance_state == "duplicate"
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
        "live_state": live_state if isinstance(live_state, dict) else {},
        "liveState": live_state if isinstance(live_state, dict) else {},
        "last_action": status_last_action,
        "lastAction": status_last_action,
        "last_message": status_last_message,
        "lastMessage": status_last_message,
        "last_error": status_last_error,
        "lastError": status_last_error,
        "busy": bool(busy),
        "instance_state": instance_state,
        "instanceState": instance_state,
        "read_only": bool(read_only),
        "readOnly": bool(read_only),
        "duplicate_instance": bool(duplicate_instance),
        "duplicateInstance": bool(duplicate_instance),
        "web_instance": dict(web_instance) if isinstance(web_instance, dict) else {},
        "webInstance": dict(web_instance) if isinstance(web_instance, dict) else {},
    }


def _live_run_payload(
    *,
    repo: Path,
    branch: str,
    latest_run_dir: Path | None,
    active_run: dict[str, Any],
    progress: dict[str, Any],
    stages: list[dict[str, Any]],
    logs: dict[str, Any],
    notifications: list[dict[str, Any]],
    runner_control: dict[str, Any],
    live_state: dict[str, Any] | None = None,
    controller_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active = active_run if isinstance(active_run, dict) else {}
    progress_data = progress if isinstance(progress, dict) else {}
    stage_items = stages if isinstance(stages, list) else []
    log_data = logs if isinstance(logs, dict) else {}
    notification_items = notifications if isinstance(notifications, list) else []
    control = runner_control if isinstance(runner_control, dict) else {}
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    live_state_data = live_state if isinstance(live_state, dict) else {}

    repo_path = str(active.get("repo") or _path_text(repo)).strip() or _path_text(repo)
    run_id = _pick_text(active.get("id"), active.get("runId"), progress_data.get("run_id"), "no-run")
    run_dir = _pick_text(active.get("runDir"), active.get("run_dir"), progress_data.get("latest_run_dir"), "")
    repo_label = _pick_text(active.get("repoLabel"), repo.name or repo_path.rsplit("/", 1)[-1], repo.name or "agentcli")
    branch_value = _pick_text(active.get("branch"), branch, progress_data.get("branch"), "HEAD")
    backend_value = _pick_text(active.get("backend"), progress_data.get("backend"), "codex")

    run_status = _pick_text(active.get("status"), progress_data.get("run_status"), "idle")
    execution_status = _pick_text(
        active.get("executionStatus"),
        active.get("execution_status"),
        progress_data.get("execution_status"),
        run_status,
    )
    project_status = _pick_text(
        active.get("projectStatus"),
        active.get("project_status"),
        progress_data.get("project_status"),
        "incomplete",
    )
    project_complete = bool(active.get("projectComplete", active.get("project_complete", progress_data.get("project_complete", False))))
    goals_complete = bool(active.get("goalsComplete", active.get("goals_complete", progress_data.get("goals_complete", False))))
    backlog_complete = bool(active.get("backlogComplete", active.get("backlog_complete", progress_data.get("backlog_complete", False))))
    stage_value = _pick_text(active.get("stage"), progress_data.get("current_stage"), controller_data.get("stage"), "idle")
    stage_index = _coerce_optional_int(_pick_value(active.get("stageIndex"), STAGE_ORDER.get(stage_value.lower(), 0)))
    if stage_index is None:
        stage_index = 0
    iteration_value = _coerce_optional_int(_pick_value(active.get("iteration"), progress_data.get("iterations")))
    if iteration_value is None:
        iteration_value = 0
    max_iterations = _coerce_optional_int(_pick_value(active.get("maxIterations"), progress_data.get("iterations"), 1))
    if max_iterations is None or max_iterations <= 0:
        max_iterations = 1
    progress_value = active.get("progress")
    if progress_value is None:
        progress_value = progress_data.get("progress")
    progress_available = bool(active.get("progressAvailable", active.get("progress_available", progress_data.get("progress_available", False))))
    attempt_value = _coerce_optional_int(_pick_value(active.get("attempt"), progress_data.get("attempt"), progress_data.get("current_attempt")))
    worktree_mode = _pick_text(active.get("worktreeMode"), active.get("worktree_mode"), progress_data.get("worktree_mode"), controller_data.get("worktree_mode"), "")
    final_reason = _pick_text(active.get("finalReason"), active.get("final_reason"), progress_data.get("final_reason"), controller_data.get("final_reason"), "")
    completion = _completion_status_payload(latest_run_dir, final_reason=final_reason)
    final_reason = completion["final_reason"]
    started_at = _coerce_optional_ms(_pick_value(active.get("startedAt"), active.get("started_at")))
    if started_at is None:
        started_at = 0
    ended_at = _coerce_optional_ms(_pick_value(active.get("endedAt"), active.get("ended_at")))
    if ended_at is None:
        ended_at = 0
    elapsed_sec = _coerce_optional_int(_pick_value(active.get("elapsedSec"), active.get("elapsed_seconds")))
    if elapsed_sec is None:
        elapsed_sec = 0

    current_task_id = _pick_text(
        active.get("task"),
        progress_data.get("current_task_id"),
        controller_data.get("current_task_id"),
        "",
    )
    current_task_title = _pick_text(
        active.get("taskTitle"),
        progress_data.get("current_task_title"),
        controller_data.get("current_task_title"),
        "",
    )
    current_task_step = _coerce_optional_int(_pick_value(progress_data.get("step"), controller_data.get("step")))
    current_task_cycle = _coerce_optional_int(_pick_value(progress_data.get("cycle"), controller_data.get("cycle")))

    log_source_payload = log_data.get("source") if isinstance(log_data.get("source"), dict) else {}
    log_cursor = _coerce_optional_int(_pick_value(log_data.get("cursor"), log_data.get("nextCursor"), log_data.get("next_cursor")))
    if log_cursor is None:
        log_cursor = 0
    log_state = _pick_text(log_data.get("state"), "loading" if run_status == "running" else "empty")
    log_entries = log_data.get("entries") if isinstance(log_data.get("entries"), list) else []
    log_files = log_data.get("files") if isinstance(log_data.get("files"), dict) else {}
    log_summary = {
        "source": {
            "path": _pick_text(log_source_payload.get("path"), ""),
            "name": _pick_text(log_source_payload.get("name"), ""),
            "exists": bool(log_source_payload.get("exists")),
        },
        "cursor": log_cursor,
        "nextCursor": log_cursor,
        "state": log_state,
        "entries": list(log_entries),
        "tail": str(log_data.get("tail") or ""),
        "files": dict(log_files),
        "ok": bool(log_data.get("ok", True)),
        "malformedLines": _coerce_optional_int(_pick_value(log_data.get("malformedLines"), log_data.get("malformed_lines"), 0)) or 0,
    }

    notification_counts: dict[str, int] = {}
    for item in notification_items:
        if not isinstance(item, dict):
            continue
        kind = _pick_text(item.get("kind"), "")
        if not kind:
            continue
        notification_counts[kind] = notification_counts.get(kind, 0) + 1
    latest_notification = notification_items[0] if notification_items else None
    control_status = control.get("status") if isinstance(control.get("status"), dict) else {}
    control_message = _pick_text(control.get("message"), fallbackSectionMessage("runnerControl"))
    control_plane_event = _pick_text(control_status.get("last_event"), control.get("last_action"), control.get("last_message"), "")
    control_plane_snapshot = _pick_text(control.get("last_message"), control.get("last_error"), "")
    notifications_summary = {
        "items": list(notification_items),
        "count": len(notification_items),
        "kinds": notification_counts,
        "latest": latest_notification,
        "controlPlaneStatus": control_message,
        "controlPlaneEvent": control_plane_event,
        "controlPlaneSnapshot": control_plane_snapshot,
    }

    stale_reasons: list[str] = []
    controller_running = bool(control_status.get("running"))
    controller_run_dir = _pick_text(control_status.get("run_dir"), control_status.get("runDir"), "")
    latest_run_dir_text = latest_run_dir.as_posix() if latest_run_dir is not None else ""

    def add_stale_reason(reason: str) -> None:
        if reason and reason not in stale_reasons:
            stale_reasons.append(reason)

    if latest_run_dir is not None and log_state in {"missing_file", "read_error"}:
        add_stale_reason(f"log_{log_state}")
    if latest_run_dir is not None and not bool(log_summary["source"].get("exists", True)):
        add_stale_reason("log_source_missing")
    control_status_reason = _pick_text(control_status.get("reason"), "")
    if control_status_reason.startswith("status_error:"):
        add_stale_reason("controller_status_error")
    if latest_run_dir_text and run_dir and run_dir != latest_run_dir_text:
        add_stale_reason("run_dir_mismatch")
    if latest_run_dir_text and controller_run_dir and controller_run_dir != latest_run_dir_text:
        add_stale_reason("controller_run_dir_mismatch")
    if controller_running and run_status in {"completed", "success", "stopped", "failed"}:
        add_stale_reason("controller_run_mismatch")
    if not controller_running and run_status == "running" and latest_run_dir_text:
        add_stale_reason("controller_run_mismatch")
    if latest_run_dir is None and (run_id != "no-run" or run_status != "idle" or current_task_id):
        add_stale_reason("missing_run_dir")

    live_run = {
        "identity": {
            "id": run_id,
            "runId": run_id,
            "repo": repo_path,
            "repoLabel": repo_label,
            "branch": branch_value,
            "backend": backend_value,
            "runDir": run_dir,
        },
        "activeRun": active,
        "progress": progress_data,
        "completion_status": completion["completion_status"],
        "completionStatus": completion["completionStatus"],
        "completion_reason": completion["completion_reason"],
        "completionReason": completion["completionReason"],
        "status": {
            "run": run_status,
            "runStatus": run_status,
            "execution": execution_status,
            "executionStatus": execution_status,
            "completionStatus": completion["completionStatus"],
            "completionReason": completion["completionReason"],
            "project": project_status,
            "projectStatus": project_status,
            "projectComplete": project_complete,
            "goalsComplete": goals_complete,
            "backlogComplete": backlog_complete,
            "stage": stage_value,
            "stageIndex": stage_index,
            "iteration": iteration_value,
            "maxIterations": max_iterations,
            "progress": round(float(progress_value), 3) if progress_value is not None else None,
            "progressAvailable": progress_available,
            "finalReason": final_reason,
        },
        "currentTask": {
            "id": current_task_id,
            "title": current_task_title,
            "attempt": attempt_value,
            "worktreeMode": worktree_mode,
            "step": current_task_step,
            "cycle": current_task_cycle,
        },
        "stages": {
            "items": list(stage_items),
            "count": len(stage_items),
            "currentStage": stage_value,
            "currentStageIndex": stage_index,
            "currentTaskId": current_task_id,
            "currentTaskTitle": current_task_title,
        },
        "stageSummaries": list(stage_items),
        "log": log_summary,
        "notifications": notifications_summary,
        "runnerControl": control,
        "control": control,
        "process": {
            "status": control_status,
            "running": bool(control_status.get("running")),
            "runnerMode": _pick_text(control_status.get("runner_mode"), control_status.get("runnerMode"), "unknown"),
            "repo": _pick_text(control_status.get("repo"), repo_path),
            "configPath": _pick_text(control_status.get("config_path"), control_status.get("configPath"), ""),
            "runDir": _pick_text(control_status.get("run_dir"), control_status.get("runDir"), run_dir),
            "uptimeSeconds": _coerce_optional_int(_pick_value(control_status.get("uptime_seconds"), control_status.get("uptimeSeconds"), 0)) or 0,
            "exitCode": control_status.get("exit_code"),
            "stopFile": _pick_text(control_status.get("stop_file"), "STOP"),
            "stopFileExists": bool(control_status.get("stop_file_exists")),
            "done": _coerce_optional_int(_pick_value(control_status.get("done"), 0)) or 0,
            "failed": _coerce_optional_int(_pick_value(control_status.get("failed"), 0)) or 0,
            "warnings": _coerce_optional_int(_pick_value(control_status.get("warnings"), 0)) or 0,
            "stateCounts": control_status.get("state_counts") if isinstance(control_status.get("state_counts"), dict) else {"done": 0, "failed": 0, "warnings": 0},
            "reason": _pick_text(control_status.get("reason"), ""),
            "lastEvent": _pick_text(control_status.get("last_event"), control_status.get("lastEvent"), ""),
            "stopProgress": normalize_stop_progress_payload(control_status.get("stop_progress")),
            "liveState": live_state_data,
            "live_state": live_state_data,
        },
        "timestamps": {
            "startedAt": started_at,
            "endedAt": ended_at,
            "elapsedSec": elapsed_sec,
            "logCursor": log_cursor,
        },
        "stale": {
            "value": bool(stale_reasons),
            "reasons": stale_reasons,
            "logs": log_state in {"missing_file", "read_error"},
            "control": bool(
                control_status_reason.startswith("status_error:")
                or not bool(control.get("controller_available", True))
                or "run_dir_mismatch" in stale_reasons
                or "controller_run_dir_mismatch" in stale_reasons
                or "controller_run_mismatch" in stale_reasons
            ),
            "process": bool("controller_run_mismatch" in stale_reasons),
        },
        "runId": run_id,
        "runDir": run_dir,
        "repo": repo_path,
        "repoLabel": repo_label,
        "branch": branch_value,
        "backend": backend_value,
        "runStatus": run_status,
        "executionStatus": execution_status,
        "projectStatus": project_status,
        "projectComplete": project_complete,
        "goalsComplete": goals_complete,
        "backlogComplete": backlog_complete,
        "stage": stage_value,
        "stageIndex": stage_index,
        "iteration": iteration_value,
        "maxIterations": max_iterations,
        "progress": round(float(progress_value), 3) if progress_value is not None else None,
        "progressAvailable": progress_available,
        "currentTaskId": current_task_id,
        "currentTaskTitle": current_task_title,
        "attempt": attempt_value,
        "worktreeMode": worktree_mode,
        "finalReason": final_reason,
        "logSource": log_summary["source"],
        "logCursor": log_cursor,
        "logState": log_state,
        "liveState": live_state_data,
        "live_state": live_state_data,
    }
    return live_run


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


def _runner_control_approved_run_root(repo: Path) -> Path:
    return (repo.expanduser().resolve() / AGENT_WORK_DIR / "agent_runs").resolve()


def _runner_control_approved_config_roots(repo: Path, cfg_path: Path) -> list[Path]:
    roots = [
        (app_home() / "configs").resolve(),
        default_config_path(repo).parent.resolve(),
        cfg_path.expanduser().resolve().parent,
    ]
    legacy = legacy_default_config_path(repo)
    if legacy is not None:
        roots.append(legacy.parent.resolve())

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = root.as_posix().lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


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
    payload = _normalize_config_for_launch(_strip_run_dir_intent(cfg))
    payload["repo"] = _path_text(repo)
    payload["config"] = _path_text(cfg_path)
    payload["config_path"] = _path_text(cfg_path)
    try:
        payload["prompts_dir"] = resolve_prompts_dir(repo, str(payload.get("prompts_dir") or "")).as_posix()
    except Exception:
        payload["prompts_dir"] = str(payload.get("prompts_dir") or "")
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
    if not (repo / ".git").exists():
        return ""
    rc, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout_sec=10)
    if rc != 0:
        return ""
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        return ""
    branch = lines[-1]
    return branch if branch != "HEAD" else ""


def _git_head_short(repo: Path) -> str:
    if not (repo / ".git").exists():
        return ""
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
        failure_status = _pick_text(item.get("status"), item.get("task_status"), item.get("outcome_status"))
        failed_lookup[task_id] = {
            "reason": _pick_text(item.get("reason"), item.get("status")),
            "status": failure_status,
            "task_status": failure_status,
            "review_required": bool(item.get("review_required") or item.get("reviewRequired")),
            "auto_merge_allowed": bool(item.get("auto_merge_allowed") or item.get("autoMergeAllowed")),
            "detail": _pick_text(item.get("detail"), item.get("message")),
            "blocked_dependencies": item.get("blocked_dependencies") or item.get("blockedDependencies") or [],
            "next_action": _pick_text(item.get("next_action"), item.get("nextAction")),
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
            status = _pick_text(failed_lookup[task.id].get("status"), "failed")

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
            status = _pick_text(failed_lookup[task.id].get("status"), "failed")
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
                    "status": failure.get("status"),
                    "task_status": failure.get("task_status"),
                    "review_required": failure.get("review_required"),
                    "auto_merge_allowed": failure.get("auto_merge_allowed"),
                    "detail": failure_detail,
                    "blocked_dependencies": failure.get("blocked_dependencies") or [],
                    "blockedDependencies": failure.get("blocked_dependencies") or [],
                    "next_action": failure.get("next_action"),
                    "nextAction": failure.get("next_action"),
                    "cycle": failure.get("cycle"),
                    "step": failure.get("step"),
                    "rc": failure.get("rc"),
                },
                "failure_reason": failure_reason,
                "failure_detail": failure_detail,
                "task_status": failure.get("task_status") or status,
                "review_required": failure.get("review_required") if failure else status in {"review_required", "blocked_env", "test_contract_changed", "regression_failed"},
                "auto_merge_allowed": failure.get("auto_merge_allowed") if failure else status == "done",
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
        "failed": len([
            item for item in backlog
            if item["status"] in {"failed", "review_required", "blocked_env", "test_contract_changed", "regression_failed"}
        ]),
    }
    status_counts: dict[str, int] = {}
    for item in backlog:
        key = _pick_text(item.get("task_status"), item.get("status"), "unknown")
        status_counts[key] = status_counts.get(key, 0) + 1
    failure_group_counts = count_task_status_groups(
        [
            _pick_text(item.get("task_status"), item.get("status"), "unknown")
            for item in backlog
            if item["status"] in {"failed", "review_required", "blocked_env", "test_contract_changed", "regression_failed"}
        ]
    )
    counts.update(
        {
            "regressed": failure_group_counts.get(STATUS_GROUP_REGRESSION, 0),
            "review": failure_group_counts.get(STATUS_GROUP_REVIEW, 0),
            "blocked_env": failure_group_counts.get(STATUS_GROUP_BLOCKED_ENV, 0),
            "tasks_regressed": failure_group_counts.get(STATUS_GROUP_REGRESSION, 0),
            "tasks_review": failure_group_counts.get(STATUS_GROUP_REVIEW, 0),
            "tasks_blocked_env": failure_group_counts.get(STATUS_GROUP_BLOCKED_ENV, 0),
        }
    )
    return {
        "items": backlog,
        "counts": counts,
        "status_counts": status_counts,
        "statusCounts": status_counts,
        "failure_group_counts": failure_group_counts,
        "failureGroupCounts": failure_group_counts,
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
    raw_text = _pick_text(value).strip()
    if not raw_text:
        return ""
    raw = raw_text.lower()
    if raw in {"idle", "boot", "pending", "unknown", "none", "unavailable"}:
        return ""
    builtin = ROLE_SPEC_CANONICALS.get(raw)
    if builtin:
        return builtin
    if raw.startswith("pm") or raw in {"planner", "planning", "pm_stage"}:
        return "PM"
    if raw in {"pl_stage", "backlog_refiner_stage", "backlog_refinement"}:
        return "PL"
    if raw.startswith("dev") or raw.startswith("task") or raw.startswith("build") or raw.startswith("test") or raw in {"implementation"}:
        return "Dev"
    if raw.startswith("qa") or raw in {"verification", "qa_stage"}:
        return "QA"
    if raw.startswith("security") or raw in {"security_stage"}:
        return "Security"
    if raw.startswith("reporter") or raw in {"reporting", "report"}:
        return "Reporter"
    return raw_text


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
    if reason_value in {"project_complete", "all_tasks_done", "completed", "success", "ok", "done", GOALS_INCOMPLETE_STATUS}:
        return "done"
    if reason_value in {"stop_file", "stop_requested", "stopped", "manual_stop", "quota_exhausted"}:
        return "stopped"
    if reason_value in {"failed", "error", "exception", "abandoned", "abandon_failed", "build_failed", "test_failed", "fast_regression_failed", "policy_violation", "exhausted_attempts", "needs_dependency", "blocked_dependency", "no_diff"}:
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
                "lastEventAt": None,
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
        if ts:
            current_last_event_at = _coerce_optional_int(entry.get("lastEventAt"))
            entry["lastEventAt"] = ts if current_last_event_at is None else max(current_last_event_at, ts)

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

    for candidate in _task_output_candidates(
        run_dir,
        stage_name=stage_name,
        cycle=cycle,
        step=step,
        task_id=task_id,
        attempt=attempt,
        reason=reason,
        include_summary_artifacts=True,
    ):
        text = _tail_text(candidate, 12)
        if text:
            return text
    return _pick_text(fallback_text)


def _task_output_candidates(
    run_dir: Path | None,
    *,
    stage_name: str,
    cycle: int | None = None,
    step: int | None = None,
    task_id: str = "",
    attempt: int | None = None,
    reason: str = "",
    include_summary_artifacts: bool = True,
) -> list[Path]:
    if run_dir is None:
        return []

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
            ]
        )
        if include_summary_artifacts:
            candidates.extend(
                [
                    run_dir / "cycle_summary.log",
                    run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
                ]
            )
    elif stage_key == "PL" and cycle_i is not None:
        candidates.extend(
            [
                run_dir / f"PL_OUTPUT_cycle_{cycle_i:03d}.json",
                run_dir / f"BACKLOG_REFINEMENT_cycle_{cycle_i:03d}.json",
                run_dir / "NOTES_PL.md",
            ]
        )
        if include_summary_artifacts:
            candidates.extend(
                [
                    run_dir / "cycle_summary.log",
                    run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
                ]
            )
    elif stage_key == "QA" and cycle_i is not None:
        candidates.append(run_dir / f"qa_followups_cycle_{cycle_i:03d}.json")
        if include_summary_artifacts:
            candidates.extend(
                [
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
    elif task_id and cycle_i is not None and include_summary_artifacts:
        candidates.extend(
            [
                run_dir / "cycle_summary.log",
                run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
            ]
        )

    return candidates


def _task_output_signal(
    run_dir: Path | None,
    *,
    stage_name: str,
    cycle: int | None = None,
    step: int | None = None,
    task_id: str = "",
    attempt: int | None = None,
    reason: str = "",
    include_summary_artifacts: bool = False,
) -> dict[str, Any]:
    if run_dir is None:
        return {"text": "", "path": "", "mtimeMs": None}

    best: dict[str, Any] | None = None
    best_score = (-1, -1)
    for index, candidate in enumerate(
        _task_output_candidates(
            run_dir,
            stage_name=stage_name,
            cycle=cycle,
            step=step,
            task_id=task_id,
            attempt=attempt,
            reason=reason,
            include_summary_artifacts=include_summary_artifacts,
        )
    ):
        text = _tail_text(candidate, 1)
        if not text:
            continue
        mtime_ms = _path_mtime_ms(candidate)
        score = (mtime_ms or -1, index)
        if best is None or score > best_score:
            best = {
                "text": text,
                "path": candidate.as_posix(),
                "mtimeMs": mtime_ms,
            }
            best_score = score
    return best or {"text": "", "path": "", "mtimeMs": None}


def _stage_output_stall_threshold_seconds(config: dict[str, Any]) -> int:
    telegram_cfg = config.get("telegram") if isinstance(config, dict) else {}
    stalled_seconds = _coerce_optional_int(
        _pick_value(
            telegram_cfg.get("stalled_seconds") if isinstance(telegram_cfg, dict) else None,
            config.get("telegram_stalled_seconds") if isinstance(config, dict) else None,
        )
    )
    if stalled_seconds is None:
        stalled_seconds = 600
    return max(60, stalled_seconds)


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
    completion = _completion_status_payload(run_dir, final_reason=final_reason_text)
    final_reason_text = completion["final_reason"]

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
        "completion_status": completion["completion_status"],
        "completionStatus": completion["completionStatus"],
        "completion_reason": completion["completion_reason"],
        "completionReason": completion["completionReason"],
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
    return _web_payloads.build_stage_payload(
        sys.modules[__name__],
        repo,
        active_run,
        progress,
        config,
        run_dir=run_dir,
        run_summary=run_summary,
        last_run_summary=last_run_summary,
        controller_status=controller_status,
        events=events,
    )


def _history_item(
    repo: Path,
    run_dir: Path,
    *,
    branch: str,
    completion_level: str = "all",
) -> dict[str, Any]:
    return _web_payloads.build_history_item(
        sys.modules[__name__],
        repo,
        run_dir,
        branch=branch,
        completion_level=completion_level,
    )


def _history_payload(
    repo: Path,
    run_dirs: list[Path],
    *,
    branch: str,
    completion_level: str = "all",
) -> dict[str, Any]:
    return _web_payloads.build_history_payload(
        sys.modules[__name__],
        repo,
        run_dirs,
        branch=branch,
        completion_level=completion_level,
    )


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
        GOALS_INCOMPLETE_STATUS,
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
        "fast_regression_failed",
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
    return _web_payloads.build_metrics_payload(
        sys.modules[__name__],
        run_dir,
        progress,
        controller_status=controller_status,
    )


def _build_progress_payload(
    *,
    repo: Path,
    run_dir: Path | None,
    config: dict[str, Any],
    branch: str,
    controller_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _web_payloads.build_progress_payload(
        sys.modules[__name__],
        repo=repo,
        run_dir=run_dir,
        config=config,
        branch=branch,
        controller_status=controller_status,
        events=events,
    )


def _build_worktree_payload(repo: Path, run_dir: Path | None, branch: str) -> dict[str, Any]:
    return _web_payloads.build_worktree_payload(sys.modules[__name__], repo, run_dir, branch)


def build_snapshot(
    repo: Path | str | None = None,
    *,
    config_path: str | None = None,
    bind_host: str = "127.0.0.1",
    bind_port: int = 8000,
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
    web_instance_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _web_payloads.build_snapshot(
        sys.modules[__name__],
        repo,
        config_path=config_path,
        bind_host=bind_host,
        bind_port=bind_port,
        trusted_network=trusted_network,
        runner_controller=runner_controller,
        runner_controls_enabled=runner_controls_enabled,
        runner_controls_source=runner_controls_source,
        runner_controls_disabled_reason=runner_controls_disabled_reason,
        runner_control_busy=runner_control_busy,
        runner_control_last_action=runner_control_last_action,
        runner_control_last_message=runner_control_last_message,
        runner_control_last_error=runner_control_last_error,
        runner_controller_auto_build=runner_controller_auto_build,
        web_instance_state=web_instance_state,
    )


def build_health(repo: Path | str | None = None) -> dict[str, Any]:
    snapshot = build_snapshot(repo)
    progress = snapshot.get("progress", {}) if isinstance(snapshot.get("progress"), dict) else {}
    runner_control = snapshot.get("runner_control", {}) if isinstance(snapshot.get("runner_control"), dict) else {}
    web_instance = snapshot.get("web_instance", {}) if isinstance(snapshot.get("web_instance"), dict) else {}
    return {
        "ok": bool(snapshot.get("ok", False)),
        "repo": snapshot.get("repo", {}),
        "latest_run_dir": snapshot.get("latest_run_dir"),
        "status": progress.get("run_status", "idle"),
        "runner_control": runner_control,
        "web_instance": web_instance,
        "webInstance": web_instance,
        "timestamp": now_iso(),
    }


def _ensure_fastapi() -> None:
    if FastAPI is None or FileResponse is None:
        raise RuntimeError("FastAPI is not installed. Add the declared dependencies before serving the web console.")


def _resolve_web_dir(web_dir: Path | str | None) -> Path:
    if web_dir is not None and str(web_dir).strip():
        return Path(web_dir).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "web_console"


def _web_instance_lock_path(repo_root: Path) -> Path:
    return repo_root / ".AgentCLI" / WEB_INSTANCE_LOCK_FILENAME


def _web_instance_registry_key(path: Path) -> str:
    resolved = path.expanduser()
    try:
        resolved = resolved.resolve()
    except Exception:
        pass
    key = resolved.as_posix()
    return key.lower() if os.name == "nt" else key


def _web_instance_hostname() -> str:
    try:
        value = socket.gethostname()
    except Exception:
        value = ""
    return str(value or os.getenv("COMPUTERNAME") or "").strip()


def _current_process_create_time() -> int | None:
    try:
        return _pid_create_time_ticks(os.getpid())
    except Exception:
        return None


def _web_instance_normalize_executable(value: object) -> str:
    text = _path_text(value)
    if not text:
        return ""
    return text.lower() if os.name == "nt" else text


def _current_process_executable_path() -> str:
    try:
        path = _pid_executable_path(os.getpid())
    except Exception:
        path = None
    if not path:
        path = getattr(sys, "executable", "")
    return _web_instance_normalize_executable(path)


def _web_instance_signature_executable(existing: dict[str, Any]) -> str:
    for key in ("process_executable", "processExecutable", "executable", "exe", "process_path", "processPath"):
        value = existing.get(key)
        if value not in (None, "", False):
            return _web_instance_normalize_executable(value)
    return ""


def _web_instance_payload(
    repo_root: Path,
    *,
    bind_host: str,
    bind_port: int,
    state: str,
    mode: str,
    reason: str,
    created_at: str,
    runner_control_requested: bool,
    runner_control_enabled: bool,
    runner_control_state: str,
    owner: dict[str, Any] | None = None,
    liveness: dict[str, Any] | None = None,
    stale_reclaimed: bool = False,
    same_owner: bool = False,
    lock_held: bool = False,
) -> dict[str, Any]:
    pid = int(os.getpid())
    pid_create_time = _current_process_create_time()
    process_executable = _current_process_executable_path()
    payload = {
        "schema_version": 1,
        "schemaVersion": 1,
        "state": str(state or "primary").strip() or "primary",
        "mode": str(mode or "read_write").strip() or "read_write",
        "duplicate": str(state or "").strip().lower() == "duplicate",
        "read_only": str(mode or "").strip().lower() == "read_only",
        "readOnly": str(mode or "").strip().lower() == "read_only",
        "reason": str(reason or "").strip(),
        "repo_root": repo_root.as_posix(),
        "repoRoot": repo_root.as_posix(),
        "lock_path": _web_instance_lock_path(repo_root).as_posix(),
        "lockPath": _web_instance_lock_path(repo_root).as_posix(),
        "pid": pid,
        "pid_create_time": pid_create_time,
        "pidCreateTime": pid_create_time,
        "process_executable": process_executable,
        "processExecutable": process_executable,
        "created_at": str(created_at or now_iso()),
        "createdAt": str(created_at or now_iso()),
        "host": str(bind_host or "").strip() or "127.0.0.1",
        "port": int(bind_port or 0),
        "hostname": _web_instance_hostname(),
        "runner_control_requested": bool(runner_control_requested),
        "runnerControlRequested": bool(runner_control_requested),
        "runner_control_enabled": bool(runner_control_enabled),
        "runnerControlEnabled": bool(runner_control_enabled),
        "runner_control_state": str(runner_control_state or "disabled").strip() or "disabled",
        "runnerControlState": str(runner_control_state or "disabled").strip() or "disabled",
        "stale_reclaimed": bool(stale_reclaimed),
        "staleReclaimed": bool(stale_reclaimed),
        "same_owner": bool(same_owner),
        "sameOwner": bool(same_owner),
        "lock_held": bool(lock_held),
        "lockHeld": bool(lock_held),
        "owner": dict(owner) if isinstance(owner, dict) else {},
        "active_lock": dict(owner) if isinstance(owner, dict) else {},
        "activeLock": dict(owner) if isinstance(owner, dict) else {},
        "liveness": dict(liveness) if isinstance(liveness, dict) else {},
    }
    return payload


def _read_web_instance_lock(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _web_instance_same_owner(
    existing: dict[str, Any],
    *,
    pid: int,
    pid_create_time: int | None,
    process_executable: str = "",
) -> bool:
    existing_pid = _coerce_optional_int(existing.get("pid"))
    if existing_pid is None or existing_pid != int(pid):
        return False
    existing_create_time = _coerce_optional_int(
        existing.get("pid_create_time")
        if existing.get("pid_create_time") is not None
        else existing.get("pidCreateTime")
    )
    if existing_create_time is not None and pid_create_time is not None and existing_create_time != int(pid_create_time):
        return False
    existing_executable = _web_instance_signature_executable(existing)
    current_executable = _web_instance_normalize_executable(process_executable)
    if existing_executable and current_executable and existing_executable != current_executable:
        return False
    return True


def _web_instance_lock_liveness(existing: dict[str, Any]) -> dict[str, Any]:
    pid = _coerce_optional_int(existing.get("pid")) or 0
    recorded_create_time = _coerce_optional_int(
        existing.get("pid_create_time")
        if existing.get("pid_create_time") is not None
        else existing.get("pidCreateTime")
    )
    recorded_executable = _web_instance_signature_executable(existing)

    def _result(
        *,
        live: bool,
        deterministic: bool,
        reason: str,
        live_create_time: int | None = None,
        live_executable: str = "",
    ) -> dict[str, Any]:
        return {
            "live": bool(live),
            "deterministic": bool(deterministic),
            "reason": str(reason),
            "pid": pid,
            "pid_create_time": recorded_create_time,
            "pidCreateTime": recorded_create_time,
            "live_pid_create_time": live_create_time,
            "livePidCreateTime": live_create_time,
            "process_executable": recorded_executable,
            "processExecutable": recorded_executable,
            "live_process_executable": live_executable,
            "liveProcessExecutable": live_executable,
        }

    if pid <= 0:
        return _result(live=False, deterministic=True, reason="missing_pid")
    if not _pid_alive(pid):
        return _result(live=False, deterministic=True, reason="pid_not_alive")
    live_create_time = _pid_create_time_ticks(pid)
    live_executable = _web_instance_normalize_executable(_pid_executable_path(pid))
    if recorded_create_time is not None and live_create_time is not None and recorded_create_time != live_create_time:
        return _result(
            live=False,
            deterministic=True,
            reason="pid_reused",
            live_create_time=live_create_time,
            live_executable=live_executable,
        )
    if recorded_executable and live_executable and recorded_executable != live_executable:
        return _result(
            live=False,
            deterministic=True,
            reason="process_executable_mismatch",
            live_create_time=live_create_time,
            live_executable=live_executable,
        )
    deterministic = bool(
        (recorded_create_time is not None and live_create_time is not None)
        or (recorded_executable and live_executable)
    )
    return _result(
        live=True,
        deterministic=deterministic,
        reason="pid_alive_signature_match" if deterministic else "pid_alive_signature_unavailable",
        live_create_time=live_create_time,
        live_executable=live_executable,
    )


def _web_instance_duplicate_reason(existing: dict[str, Any]) -> str:
    pid = _coerce_optional_int(existing.get("pid"))
    host = str(existing.get("host") or "").strip()
    port = _coerce_optional_int(existing.get("port"))
    address = ""
    if host and port:
        address = f"{host}:{port}"
    elif host:
        address = host
    elif port:
        address = f"port {port}"
    started = str(existing.get("created_at") or existing.get("createdAt") or "").strip()
    details = [f"pid {pid}" if pid else "", address, started]
    joined = ", ".join(part for part in details if part)
    suffix = f" ({joined})" if joined else ""
    return f"Mutating web controls are locked by another web console for this repo{suffix}. This instance is read-only."


class _RepoWebInstanceLock:
    def __init__(self, repo_root: Path, *, bind_host: str, bind_port: int) -> None:
        self.repo_root = repo_root
        self.bind_host = str(bind_host or "").strip() or "127.0.0.1"
        self.bind_port = int(bind_port or 0)
        self.path = _web_instance_lock_path(repo_root)
        self.created_at = now_iso()
        self.pid = int(os.getpid())
        self.pid_create_time = _current_process_create_time()
        self.process_executable = _current_process_executable_path()
        self.state = "primary"
        self.mode = "read_write"
        self.reason = ""
        self.owner: dict[str, Any] = {}
        self.liveness: dict[str, Any] = {}
        self.stale_reclaimed = False
        self.same_owner = False
        self._registered_hold = False
        self._registry_key = _web_instance_registry_key(self.path)
        self._owner_key = f"{self.pid}:{self.pid_create_time if self.pid_create_time is not None else 'unknown'}"

    def _register_local_hold(self) -> None:
        if self._registered_hold:
            return
        with _WEB_INSTANCE_LOCAL_HOLDS_LOCK:
            record = _WEB_INSTANCE_LOCAL_HOLDS.get(self._registry_key)
            if record and record.get("owner_key") == self._owner_key:
                record["count"] = int(record.get("count") or 0) + 1
            else:
                _WEB_INSTANCE_LOCAL_HOLDS[self._registry_key] = {
                    "owner_key": self._owner_key,
                    "count": 1,
                }
        self._registered_hold = True

    def _write_primary_lock(self, *, runner_control_requested: bool = False, runner_control_enabled: bool = False, runner_control_state: str = "disabled") -> None:
        payload = _web_instance_payload(
            self.repo_root,
            bind_host=self.bind_host,
            bind_port=self.bind_port,
            state="primary",
            mode="read_write",
            reason="",
            created_at=self.created_at,
            runner_control_requested=runner_control_requested,
            runner_control_enabled=runner_control_enabled,
            runner_control_state=runner_control_state,
            owner={},
            liveness={},
            stale_reclaimed=self.stale_reclaimed,
            same_owner=self.same_owner,
            lock_held=self._registered_hold,
        )
        payload["owner"] = {
            key: value
            for key, value in payload.items()
            if key not in {"owner", "active_lock", "activeLock", "liveness"}
        }
        payload["active_lock"] = dict(payload["owner"])
        payload["activeLock"] = dict(payload["owner"])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, payload)
        self.owner = dict(payload["owner"])

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        attempts = 0
        while attempts < 2:
            try:
                payload = _web_instance_payload(
                    self.repo_root,
                    bind_host=self.bind_host,
                    bind_port=self.bind_port,
                    state="primary",
                    mode="read_write",
                    reason="",
                    created_at=self.created_at,
                    runner_control_requested=False,
                    runner_control_enabled=False,
                    runner_control_state="disabled",
                )
                payload["owner"] = {
                    key: value
                    for key, value in payload.items()
                    if key not in {"owner", "active_lock", "activeLock", "liveness"}
                }
                payload["active_lock"] = dict(payload["owner"])
                payload["activeLock"] = dict(payload["owner"])
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                self.owner = dict(payload["owner"])
                self.state = "primary"
                self.mode = "read_write"
                self.reason = ""
                self._register_local_hold()
                return
            except FileExistsError:
                existing = _read_web_instance_lock(self.path)
                if _web_instance_same_owner(
                    existing,
                    pid=self.pid,
                    pid_create_time=self.pid_create_time,
                    process_executable=self.process_executable,
                ):
                    self.same_owner = True
                    self.state = "primary"
                    self.mode = "read_write"
                    self.reason = ""
                    self.owner = dict(existing) if existing else {}
                    self._register_local_hold()
                    return
                liveness = _web_instance_lock_liveness(existing)
                self.liveness = dict(liveness)
                if not liveness.get("live"):
                    try:
                        self.path.unlink(missing_ok=True)
                        self.stale_reclaimed = True
                        attempts += 1
                        continue
                    except Exception as ex:
                        self.state = "degraded"
                        self.mode = "read_only"
                        self.reason = f"Mutating web controls are disabled because the repo web lock could not be reclaimed: {ex}"
                        self.owner = dict(existing) if existing else {}
                        return
                self.state = "duplicate"
                self.mode = "read_only"
                self.reason = _web_instance_duplicate_reason(existing)
                self.owner = dict(existing) if existing else {}
                return
            except Exception as ex:
                self.state = "degraded"
                self.mode = "read_only"
                self.reason = f"Mutating web controls are disabled because the repo web lock could not be acquired: {ex}"
                return
        if self.state == "primary" and not self._registered_hold:
            self.state = "degraded"
            self.mode = "read_only"
            self.reason = "Mutating web controls are disabled because the repo web lock could not be acquired."

    def snapshot(self, *, runner_control_requested: bool, runner_control_enabled: bool, runner_control_state: str) -> dict[str, Any]:
        if self.state == "primary" and self._registered_hold:
            try:
                self._write_primary_lock(
                    runner_control_requested=runner_control_requested,
                    runner_control_enabled=runner_control_enabled,
                    runner_control_state=runner_control_state,
                )
            except Exception:
                pass
        owner = self.owner
        if self.state == "primary" and not owner:
            owner = {
                "repo_root": self.repo_root.as_posix(),
                "repoRoot": self.repo_root.as_posix(),
                "pid": self.pid,
                "pid_create_time": self.pid_create_time,
                "pidCreateTime": self.pid_create_time,
                "process_executable": self.process_executable,
                "processExecutable": self.process_executable,
                "created_at": self.created_at,
                "createdAt": self.created_at,
                "host": self.bind_host,
                "port": self.bind_port,
                "hostname": _web_instance_hostname(),
                "state": "primary",
                "mode": "read_write",
                "runner_control_state": runner_control_state,
                "runnerControlState": runner_control_state,
                "runner_control_enabled": bool(runner_control_enabled),
                "runnerControlEnabled": bool(runner_control_enabled),
                "runner_control_requested": bool(runner_control_requested),
                "runnerControlRequested": bool(runner_control_requested),
            }
        return _web_instance_payload(
            self.repo_root,
            bind_host=self.bind_host,
            bind_port=self.bind_port,
            state=self.state,
            mode=self.mode,
            reason=self.reason,
            created_at=self.created_at,
            runner_control_requested=runner_control_requested,
            runner_control_enabled=runner_control_enabled,
            runner_control_state=runner_control_state,
            owner=owner,
            liveness=self.liveness,
            stale_reclaimed=self.stale_reclaimed,
            same_owner=self.same_owner,
            lock_held=self._registered_hold,
        )

    def release(self) -> None:
        if not self._registered_hold:
            return
        remove_file = False
        with _WEB_INSTANCE_LOCAL_HOLDS_LOCK:
            record = _WEB_INSTANCE_LOCAL_HOLDS.get(self._registry_key)
            if record and record.get("owner_key") == self._owner_key:
                remaining = int(record.get("count") or 0) - 1
                if remaining > 0:
                    record["count"] = remaining
                else:
                    _WEB_INSTANCE_LOCAL_HOLDS.pop(self._registry_key, None)
                    remove_file = True
            else:
                remove_file = True
        self._registered_hold = False
        if not remove_file:
            return
        try:
            existing = _read_web_instance_lock(self.path)
            if not existing or _web_instance_same_owner(
                existing,
                pid=self.pid,
                pid_create_time=self.pid_create_time,
                process_executable=self.process_executable,
            ):
                self.path.unlink(missing_ok=True)
        except Exception:
            pass


def create_app(
    repo: Path | str | None = None,
    *,
    web_dir: Path | str | None = None,
    config_path: str | None = None,
    bind_host: str = "127.0.0.1",
    bind_port: int = 8000,
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
    web_instance_lock = _RepoWebInstanceLock(repo_root, bind_host=bind_host, bind_port=bind_port)
    web_instance_lock.acquire()
    if web_instance_lock.mode == "read_only":
        controls_enabled = False
        controls_disabled_reason = web_instance_lock.reason or controls_disabled_reason
    control_state: dict[str, str] = {
        "last_action": "",
        "last_message": "",
        "last_error": "",
    }
    control_lock = threading.Lock()

    def _web_instance_snapshot() -> dict[str, Any]:
        runner_control_state = "duplicate" if web_instance_lock.state == "duplicate" else "degraded" if web_instance_lock.state == "degraded" else "enabled" if controls_enabled else "disabled"
        return web_instance_lock.snapshot(
            runner_control_requested=bool(enable_runner_controls),
            runner_control_enabled=bool(controls_enabled),
            runner_control_state=runner_control_state,
        )

    def _shutdown_process_guard() -> None:
        try:
            if controller is not None and web_instance_lock.mode != "read_only":
                status = controller.status()
                if isinstance(status, dict) and bool(status.get("running")):
                    controller.stop(wait=True)
        except Exception:
            pass
        try:
            terminate_all_children()
        except Exception:
            pass
        try:
            web_instance_lock.release()
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
    app.state.bind_port = int(bind_port)
    app.state.trusted_network = trusted_network
    app.state.runner_controller = controller
    app.state.runner_controls_enabled = controls_enabled
    app.state.runner_controls_source = controls_source
    app.state.runner_controls_disabled_reason = controls_disabled_reason
    app.state.runner_control_lock = control_lock
    app.state.web_instance = _web_instance_snapshot()
    app.state.web_instance_lock = web_instance_lock
    web_redaction_active = _web_redaction_active(bind_host)
    lan_safety_active = _lan_safety_blocks_mutations(bind_host)
    app.state.lan_safety_active = lan_safety_active

    def _lan_safety_error_details(action: str, *, reason: str = LAN_SAFETY_MUTATION_DISABLED_MESSAGE) -> dict[str, Any]:
        details = _lan_safety_details(bind_host, trusted_network=trusted_network)
        details["blocked_action"] = action
        details["reason"] = reason
        return details

    def _snapshot(*, busy_override: bool | None = None) -> dict[str, Any]:
        snapshot = build_snapshot(
            repo_root,
            config_path=config_path,
            bind_host=bind_host,
            bind_port=bind_port,
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
            web_instance_state=_web_instance_snapshot(),
        )
        app.state.web_instance = snapshot.get("web_instance", {})
        return snapshot

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
        web_instance = snap.get("web_instance", {}) if isinstance(snap.get("web_instance"), dict) else {}
        return {
            "ok": bool(snap.get("ok", False)),
            "repo": snap.get("repo", {}),
            "latest_run_dir": snap.get("latest_run_dir"),
            "progress": progress,
            "runner_control": control,
            "web_instance": web_instance,
            "webInstance": web_instance,
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
            "liveRun": snap.get("liveRun", {}),
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
        disabled_snapshot = _runner_control_snapshot()
        control = disabled_snapshot.get("runner_control", {})
        web_instance = disabled_snapshot.get("web_instance", {}) if isinstance(disabled_snapshot.get("web_instance"), dict) else {}
        duplicate_instance = bool(
            control.get("duplicate_instance")
            or control.get("duplicateInstance")
            or str(web_instance.get("state") or "").strip().lower() == "duplicate"
        )
        message = str(
            LAN_SAFETY_MUTATION_DISABLED_MESSAGE
            if lan_safety_active and not duplicate_instance
            else control.get("message") or "Runner controls are disabled."
        )
        error_code = (
            "runner_controls_duplicate_instance"
            if duplicate_instance
            else "lan_safety_mutation_blocked"
            if lan_safety_active
            else "runner_controls_disabled"
        )
        details = {
            "enabled": bool(control.get("enabled")),
            "source": control.get("source", ""),
            "bind_host": bind_host,
            "bind_port": int(bind_port),
            "trusted_network": bool(trusted_network),
            "reason": str(message or controls_disabled_reason or ""),
            "web_instance": web_instance,
        }
        if lan_safety_active and not duplicate_instance:
            details.update(_lan_safety_error_details(f"runner-{action}"))
        _record_runner_control_event(
            action,
            status="duplicate" if duplicate_instance else "disabled",
            message="",
            error=message,
            details=details,
            controller_status=control.get("status") if isinstance(control.get("status"), dict) else None,
        )
        return _runner_control_response(
            action=action,
            status_code=409 if duplicate_instance else 403,
            ok=False,
            status="duplicate" if duplicate_instance else "lan_safety_blocked" if lan_safety_active else "disabled",
            message=message,
            error_code=error_code,
            details=details,
        )

    def _runner_control_unavailable(action: str) -> Any:
        _record_runner_control_event(
            action,
            status="controller_error",
            message="",
            error="Runner controller is unavailable.",
        )
        return _runner_control_response(
            action=action,
            status_code=503,
            ok=False,
            status="error",
            message="Runner controller is unavailable.",
            error_code="runner_controller_unavailable",
        )

    def _runner_control_artifact_run_dir(controller_status: dict[str, Any] | None = None) -> Path | None:
        status_data = controller_status if isinstance(controller_status, dict) else _controller_status_payload(controller)
        try:
            run_dir = _resolve_latest_run_dir(repo_root, status_data, controller)
        except Exception:
            run_dir = None
        if run_dir is not None:
            return run_dir
        run_dir_text = str(
            status_data.get("run_dir")
            or status_data.get("runDir")
            or getattr(controller, "run_dir", "")
            or ""
        ).strip()
        if not run_dir_text:
            return None
        try:
            return Path(run_dir_text).expanduser().resolve()
        except Exception:
            try:
                return Path(run_dir_text).expanduser()
            except Exception:
                return None

    def _record_runner_control_event(
        action: str,
        *,
        status: str,
        message: str = "",
        error: str = "",
        details: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        controller_status: dict[str, Any] | None = None,
    ) -> None:
        run_dir = _runner_control_artifact_run_dir(controller_status)
        if run_dir is None:
            return
        status_key = str(status or "").strip().lower()
        ok = status_key in {"started", "stopped", "reloaded", "restarted", "stopping"}
        write_runner_control_event(
            run_dir,
            action=action,
            status=status_key,
            message=message if ok else "",
            error="" if ok else error or message,
            ok=ok,
            source="web",
            repo=repo_root.as_posix(),
            config_path=cfg_path.as_posix(),
            controller_available=bool(controller is not None),
            running=bool(controller_status.get("running")) if isinstance(controller_status, dict) else None,
            details=details or {},
            result=result or {},
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

        def _invoke_runner_start(start_overrides: dict[str, Any], *, control_action: str) -> dict[str, Any]:
            try:
                return controller.start(start_overrides, control_action=control_action)
            except TypeError as ex:
                if "control_action" not in str(ex):
                    raise
            return controller.start(start_overrides)

        def _invoke_runner_stop(*, wait: bool, control_action: str) -> dict[str, Any]:
            try:
                return controller.stop(wait=wait, control_action=control_action)
            except TypeError as ex:
                if "control_action" not in str(ex):
                    raise
            return controller.stop(wait=wait)

        def _runner_start_failure_details(result: dict[str, Any], default_code: str) -> tuple[str, dict[str, Any] | None]:
            error = result.get("error") if isinstance(result.get("error"), dict) else {}
            error_code = str(error.get("code") or default_code).strip() or default_code
            details = error.get("details") if isinstance(error.get("details"), dict) else None
            if details is None and isinstance(result.get("readiness"), dict):
                details = {"readiness": dict(result.get("readiness") or {})}
            return error_code, details

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
            current_status = _runner_control_status_payload(
                controller,
                repo=repo_root,
                config_path=cfg_path.as_posix(),
                current_run_dir=str(getattr(controller, "run_dir", "") or ""),
                cfg=cfg,
                cfg_path=cfg_path,
            )
            provided = _runner_control_confirmation_value(body)
            expected = _runner_control_confirmation(normalized_action)
            if not provided:
                _record_runner_control_event(
                    normalized_action,
                    status="confirmation_mismatch",
                    message="",
                    error=f'Type "{expected}" to confirm this action.',
                    details={"expected": expected},
                    controller_status=current_status,
                )
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
                _record_runner_control_event(
                    normalized_action,
                    status="confirmation_mismatch",
                    message="",
                    error=f'Confirmation phrase must be "{expected}".',
                    details={"expected": expected},
                    controller_status=current_status,
                )
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
                    approved_run_root=_runner_control_approved_run_root(repo_root),
                    approved_config_roots=_runner_control_approved_config_roots(repo_root, cfg_path),
                )
                if validation_error:
                    _record_runner_control_event(
                        normalized_action,
                        status="error",
                        message="",
                        error=str(validation_error.get("message") or "Runner start options are invalid."),
                        details=validation_error,
                        controller_status=current_status,
                    )
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
            status_reason = str(current_status.get("reason") or "").strip()
            if status_reason.startswith("status_error:"):
                message = status_reason
                control_state["last_action"] = normalized_action
                control_state["last_message"] = ""
                control_state["last_error"] = message
                _record_runner_control_event(
                    normalized_action,
                    status="controller_error",
                    message="",
                    error=message,
                    details={"reason": status_reason},
                    controller_status=current_status,
                )
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
                result = _invoke_runner_start(start_overrides, control_action=normalized_action)
                if not bool(result.get("ok")):
                    message = str(result.get("message") or "Runner start failed.")
                    error_code, error_details = _runner_start_failure_details(result, "runner_start_failed")
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    _record_runner_control_event(
                        normalized_action,
                        status="error",
                        message="",
                        error=message,
                        details=error_details or {"result": result},
                        result=result if isinstance(result, dict) else None,
                        controller_status=current_status,
                    )
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code=error_code,
                        details=error_details,
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
                result = _invoke_runner_stop(wait=True, control_action=normalized_action)
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
                    _record_runner_control_event(
                        normalized_action,
                        status="timeout" if stop_phase == "timeout" else "error",
                        message="",
                        error=message,
                        details={"stop_progress": stop_progress} if stop_phase == "timeout" else {"result": result},
                        result=result if isinstance(result, dict) else None,
                        controller_status=current_status,
                    )
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
                    _record_runner_control_event(
                        normalized_action,
                        status="timeout",
                        message="",
                        error=message,
                        details={"stop_progress": result.get("stop_progress") if isinstance(result.get("stop_progress"), dict) else {}},
                        result=result if isinstance(result, dict) else None,
                        controller_status=current_status,
                    )
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
            should_stop = bool(_coerce_optional_bool(current_status.get("running")))
            stop_result: dict[str, Any] = {}
            if should_stop:
                stop_result = _invoke_runner_stop(wait=False, control_action=normalized_action)
                if not bool(stop_result.get("ok")):
                    message = str(stop_result.get("message") or f"Runner {flow_name} stop failed.")
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    _record_runner_control_event(
                        normalized_action,
                        status="error",
                        message="",
                        error=message,
                        details={"stop": stop_result},
                        result={"stop": stop_result},
                        controller_status=current_status,
                    )
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
                    _record_runner_control_event(
                        normalized_action,
                        status="timeout",
                        message="",
                        error=message,
                        details={"stop": stop_result},
                        result={"stop": stop_result},
                        controller_status=current_status,
                    )
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
            else:
                stop_result = {
                    "ok": True,
                    "skipped": True,
                    "reason": "runner_not_running",
                    "message": f"Runner was already stopped; {flow_name} will start without writing stop artifacts.",
                    "running": False,
                    "run_dir": str(current_status.get("run_dir") or ""),
                }

            result = _invoke_runner_start(start_overrides, control_action=normalized_action)
            if not bool(result.get("ok")):
                message = str(result.get("message") or f"Runner {flow_name} failed.")
                error_code, error_details = _runner_start_failure_details(result, f"runner_{flow_name}_failed")
                control_state["last_action"] = normalized_action
                control_state["last_message"] = ""
                control_state["last_error"] = message
                _record_runner_control_event(
                    normalized_action,
                    status="error",
                    message="",
                    error=message,
                    details=error_details or {"stop": stop_result, "start": result},
                    result={"stop": stop_result, "start": result},
                    controller_status=current_status,
                )
                return _runner_control_response(
                    action=normalized_action,
                    status_code=409,
                    ok=False,
                    status="error",
                    message=message,
                    error_code=error_code,
                    details=error_details,
                    result={"stop": stop_result, "start": result},
                    busy_override=False,
                )

            start_only = bool(stop_result.get("skipped"))
            if start_only:
                success_message = (
                    "Runner was stopped; started without writing stop artifacts."
                    if normalized_action == "restart"
                    else "Runner was stopped; reloaded by starting without writing stop artifacts."
                )
            else:
                success_message = "Runner restarted." if normalized_action == "restart" else "Runner reloaded."
            message = success_message
            control_state["last_action"] = normalized_action
            control_state["last_message"] = message
            control_state["last_error"] = ""
            return _runner_control_response(
                action=normalized_action,
                status_code=200,
                ok=True,
                status=f"{flow_name}_started" if start_only else ("restarted" if normalized_action == "restart" else "reloaded"),
                message=message,
                result={"stop": stop_result, "start": result},
                busy_override=False,
            )
        except Exception as ex:
            message = f"Runner control failed: {ex}"
            control_state["last_action"] = normalized_action
            control_state["last_message"] = ""
            control_state["last_error"] = message
            _record_runner_control_event(
                normalized_action,
                status="controller_error",
                message="",
                error=message,
                details={"exception": str(ex)},
                controller_status=current_status if "current_status" in locals() and isinstance(current_status, dict) else None,
            )
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
        message = str(LAN_SAFETY_MUTATION_DISABLED_MESSAGE if lan_safety_active else control.get("message") or "Worktree review actions are disabled.")
        details = {
            "enabled": bool(control.get("enabled")),
            "source": control.get("source", ""),
            "bind_host": bind_host,
            "trusted_network": bool(trusted_network),
            "reason": str(message or controls_disabled_reason or ""),
        }
        if lan_safety_active:
            details.update(_lan_safety_error_details(f"worktree-{action}"))
        return _worktree_action_response(
            action=action,
            status_code=403,
            ok=False,
            status="lan_safety_blocked" if lan_safety_active else "disabled",
            message=message,
            error_code="lan_safety_mutation_blocked" if lan_safety_active else "worktree_actions_disabled",
            details=details,
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

    def _pr_queue_action_response(
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
        payload: dict[str, Any] = {
            "ok": ok,
            "action": action,
            "status": status,
            "message": message,
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

    def _pr_queue_action_disabled(action: str) -> Any:
        control = _runner_control_snapshot().get("runner_control", {})
        message = str(LAN_SAFETY_MUTATION_DISABLED_MESSAGE if lan_safety_active else control.get("message") or "PR queue merge actions are disabled.")
        details = {
            "enabled": bool(control.get("enabled")),
            "source": control.get("source", ""),
            "bind_host": bind_host,
            "trusted_network": bool(trusted_network),
            "reason": str(message or controls_disabled_reason or ""),
        }
        if lan_safety_active:
            details.update(_lan_safety_error_details(f"pr-queue-{action}"))
        return _pr_queue_action_response(
            action=action,
            status_code=403,
            ok=False,
            status="lan_safety_blocked" if lan_safety_active else "disabled",
            message=message,
            error_code="lan_safety_mutation_blocked" if lan_safety_active else "pr_queue_actions_disabled",
            details=details,
            busy_override=False,
        )

    async def _pr_queue_action_body(request: Request) -> dict[str, Any] | None:
        try:
            payload = await request.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    @app.post("/api/pr-queue/merge")
    @app.post("/api/pr_queue/merge")
    async def api_pr_queue_merge(request: Request) -> Any:
        if not controls_enabled:
            return _pr_queue_action_disabled("merge")
        if not control_lock.acquire(blocking=False):
            return _pr_queue_action_response(
                action="merge",
                status_code=409,
                ok=False,
                status="busy",
                message="A PR queue merge request is already in flight.",
                error_code="pr_queue_actions_busy",
                busy_override=True,
            )

        try:
            body = await _pr_queue_action_body(request)
            if body is None:
                return _pr_queue_action_response(
                    action="merge",
                    status_code=400,
                    ok=False,
                    status="error",
                    message="PR queue merge request body must be JSON.",
                    error_code="invalid_json",
                    busy_override=False,
                )

            packet_id = _pick_text(body.get("packetId"), body.get("packet_id"), body.get("id"))
            if not packet_id:
                return _pr_queue_action_response(
                    action="merge",
                    status_code=400,
                    ok=False,
                    status="invalid_request",
                    message="A PR packet id is required.",
                    error_code="pr_queue_packet_id_required",
                    busy_override=False,
                )

            approval_phrase = _pick_text(body.get("confirmation"), body.get("confirm"), body.get("phrase"), body.get("token"))

            try:
                result = merge_review_packet(repo_root, packet_id, approval_phrase=approval_phrase)
            except PrQueueMergeError as ex:
                return _pr_queue_action_response(
                    action="merge",
                    status_code=ex.status_code,
                    ok=False,
                    status=ex.status,
                    message=str(ex),
                    error_code=ex.code,
                    details=ex.details or None,
                    busy_override=False,
                )
            except Exception as ex:
                return _pr_queue_action_response(
                    action="merge",
                    status_code=500,
                    ok=False,
                    status="error",
                    message=f"PR queue merge failed: {ex}",
                    error_code="pr_queue_merge_failed",
                    details={"packet_id": packet_id},
                    busy_override=False,
                )

            return _pr_queue_action_response(
                action="merge",
                status_code=200,
                ok=True,
                status="approved",
                message="PR packet approval recorded without auto-committing source changes.",
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
            "completion_status": progress.get("completion_status", ""),
            "completionStatus": progress.get("completionStatus", progress.get("completion_status", "")),
            "completion_reason": progress.get("completion_reason", ""),
            "completionReason": progress.get("completionReason", progress.get("completion_reason", "")),
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
            "liveRun": snap.get("liveRun", {}),
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
        source_id = str(query.get("source") or query.get("source_id") or query.get("sourceId") or "").strip()
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
            source_catalog = _log_tail_source_catalog(latest_run_dir)
            source_record = _resolve_log_tail_source_record(latest_run_dir, source_id=source_id)
            source_path = Path(str(source_record.get("path") or "")) if source_record and str(source_record.get("path") or "").strip() else None
            if source_path is None:
                payload = {
                    "ok": False,
                    "state": "missing_file",
                    "entries": [],
                    "next_cursor": 0,
                    "source_file": "",
                    "source_path": "",
                    "source": {
                        "id": "",
                        "label": "",
                        "path": "",
                        "name": "",
                        "exists": False,
                        "available": False,
                        "selected": False,
                        "kind": "log",
                        "unavailable_reason": "missing",
                    },
                    "source_id": "",
                    "selected_source_id": "",
                    "sources": source_catalog,
                    "malformed_lines": 0,
                }
                return _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_log_payload)
            live = bool(controller_status.get("running"))
            payload = _build_log_tail_payload(
                source_path,
                source=source_record,
                sources=source_catalog,
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
            source_record: dict[str, Any] | None = None
            source_catalog: list[dict[str, Any]] = []
            try:
                controller_status = _controller_status_payload(controller)
                latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
                source_catalog = _log_tail_source_catalog(latest_run_dir)
                source_record = _resolve_log_tail_source_record(latest_run_dir, source_id=source_id)
                source_path = Path(str(source_record.get("path") or "")) if source_record and str(source_record.get("path") or "").strip() else None
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
                    "id": str(source_record.get("id") or "") if source_record else "",
                    "label": str(source_record.get("label") or "") if source_record else "",
                    "path": source_text,
                    "name": Path(source_text).name if source_text else "",
                    "exists": bool(source_text and Path(source_text).exists()),
                    "available": bool(source_text and Path(source_text).exists()),
                    "selected": bool(source_record),
                    "kind": str(source_record.get("kind") or "log") if source_record else "log",
                    "unavailable_reason": str(source_record.get("unavailable_reason") or "read_error") if source_record else "read_error",
                },
                "source_id": str(source_record.get("id") or "") if source_record else "",
                "selected_source_id": str(source_record.get("id") or "") if source_record else "",
                "sources": source_catalog,
                "error": str(ex).strip() or ex.__class__.__name__,
                "malformed_lines": 0,
            }
            return _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_log_payload)

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return _snapshot().get("config_contract") or _section("config")

    @app.post("/api/config/restore")
    async def api_config_restore(request: Request) -> Any:
        nonlocal cfg
        if not controls_enabled:
            if lan_safety_active:
                return _config_restore_error(
                    403,
                    "lan_safety_mutation_blocked",
                    LAN_SAFETY_MUTATION_DISABLED_MESSAGE,
                    **_lan_safety_error_details("config-restore"),
                )
            return _config_restore_error(
                403,
                "config_restore_disabled",
                "Config restores are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _config_restore_error(409, "config_restore_busy", "A mutating action is already in flight.")

        backup_path: Path | None = None
        restored_from_path = ""
        try:
            if cfg_path.exists() and not cfg_path.is_file():
                return _config_restore_error(400, "config_path_not_file", "Config path must reference a JSON file.", path=cfg_path.as_posix())

            body = await _config_save_body(request)
            if body is None:
                return _config_restore_error(400, "invalid_json", "Config restore request body must be JSON.")

            requested_backup_path = _pick_text(
                body.get("backup_path"),
                body.get("backupPath"),
                body.get("selected_backup_path"),
                body.get("selectedBackupPath"),
            )
            confirmation = _pick_text(
                body.get("confirm"),
                body.get("confirmation"),
                body.get("restore_confirmation"),
                body.get("restoreConfirmation"),
            )
            if not requested_backup_path:
                return _config_restore_error(400, "config_backup_path_required", "A backup path is required.", field="backup_path")
            restore_result, restore_error = _config_restore_backup(
                cfg_path,
                requested_backup_path,
                confirmation,
                confirmation_phrase=CONFIG_RESTORE_CONFIRMATION_PHRASE,
            )
            if restore_error is not None or restore_result is None:
                if restore_error is None:
                    return _config_restore_error(500, "config_restore_failed", "Config restore failed.")
                return _config_restore_error(
                    restore_error.status_code,
                    restore_error.code,
                    restore_error.message,
                    **restore_error.details,
                )

            backup_path = restore_result.backup_path
            restored_from_path = restore_result.restored_from.as_posix()
            cfg = restore_result.restored_raw
            if controller is not None and hasattr(controller, "base_args"):
                try:
                    controller.base_args = _build_runner_base_args(repo_root, cfg, cfg_path)
                except Exception:
                    pass
                try:
                    if hasattr(controller, "runner_mode"):
                        controller.runner_mode = _runner_mode_from_config(cfg)
                except Exception:
                    pass

            snapshot = _snapshot(busy_override=False)
            message = f"Config restored from {restored_from_path}. Backup written to {backup_path.as_posix()}."
            response_payload: dict[str, Any] = {
                "ok": True,
                "action": "config-restore",
                "status": "restored",
                "message": message,
                "config_path": cfg_path.as_posix(),
                "backup_path": backup_path.as_posix(),
                "restored_from_path": restored_from_path,
                "validation": {
                    "current": {"ok": True, "path": cfg_path.as_posix()},
                    "backup": {"ok": True, "path": restored_from_path},
                    "restored": {"ok": True, "path": cfg_path.as_posix()},
                },
                "snapshot": snapshot,
            }
            return JSONResponse(status_code=200, content=response_payload)
        except Exception as ex:
            details: dict[str, Any] = {"path": cfg_path.as_posix()}
            if backup_path is not None:
                details["backup_path"] = backup_path.as_posix()
            if restored_from_path:
                details["restored_from_path"] = restored_from_path
            return _config_restore_error(500, "config_restore_failed", f"Config restore failed: {ex}", **details)
        finally:
            control_lock.release()

    @app.post("/api/config/save")
    async def api_config_save(request: Request) -> Any:
        nonlocal cfg
        if not controls_enabled:
            if lan_safety_active:
                return _config_save_error(
                    403,
                    "lan_safety_mutation_blocked",
                    LAN_SAFETY_MUTATION_DISABLED_MESSAGE,
                    **_lan_safety_error_details("config-save"),
                )
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

            snapshot = _snapshot(busy_override=False)
            config_contract = snapshot.get("config_contract") if isinstance(snapshot.get("config_contract"), dict) else {}
            schema = config_contract.get("schema") if isinstance(config_contract.get("schema"), dict) else {}
            restart_required_paths = {
                str(path)
                for path in (config_contract.get("restart_required_paths") or [])
                if str(path).strip()
            }
            save_result, save_error = _config_save_changes(
                cfg_path,
                raw_changes,
                schema=schema,
                restart_required_paths=restart_required_paths,
            )
            if save_error is not None or save_result is None:
                if save_error is None:
                    return _config_save_error(500, "config_save_failed", "Config save failed.")
                return _config_save_error(
                    save_error.status_code,
                    save_error.code,
                    save_error.message,
                    **save_error.details,
                )

            backup_path = save_result.backup_path
            cfg = save_result.updated_raw
            changed_paths = save_result.changed_paths
            reload_required_paths = save_result.reload_required_paths
            if controller is not None and hasattr(controller, "base_args"):
                try:
                    controller.base_args = _build_runner_base_args(repo_root, cfg, cfg_path)
                except Exception:
                    pass
                try:
                    if hasattr(controller, "runner_mode"):
                        controller.runner_mode = _runner_mode_from_config(cfg)
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

    def _prompt_error_from_payload(error: dict[str, Any], *, action: str | None = None) -> JSONResponse:
        status_code = int(error.get("status_code") or 500)
        code = str(error.get("code") or "prompt_error")
        message = str(error.get("message") or "Prompt request failed.")
        details = error.get("details") if isinstance(error.get("details"), dict) else {}
        if action:
            return _prompt_action_error(status_code, action, code, message, **details)
        return _prompt_error(status_code, code, message, **details)

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
        if lan_safety_active:
            return _prompt_error(
                403,
                "lan_safety_prompt_read_blocked",
                LAN_SAFETY_PROMPT_READ_DISABLED_MESSAGE,
                **_lan_safety_error_details("prompt-read", reason=LAN_SAFETY_PROMPT_READ_DISABLED_MESSAGE),
            )
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
        spec, _, error = resolve_prompt_target(prompt_dir, prompt_id, prompt_file)
        if error is not None:
            return _prompt_error_from_payload(error)
        return _prompt_read_payload(spec, prompt_dir, repo_root, profile=profile)

    @app.post("/api/prompts/save")
    async def api_prompt_save(request: Request) -> Any:
        nonlocal cfg
        if not controls_enabled:
            if lan_safety_active:
                return _prompt_action_error(
                    403,
                    "prompt-save",
                    "lan_safety_mutation_blocked",
                    LAN_SAFETY_MUTATION_DISABLED_MESSAGE,
                    **_lan_safety_error_details("prompt-save"),
                )
            return _prompt_action_error(
                403,
                "prompt-save",
                "prompt_mutation_disabled",
                "Prompt saves are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _prompt_action_error(409, "prompt-save", "prompt_save_busy", "A prompt mutation is already in flight.")

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

            response_payload, error = save_prompt(
                repo_root=repo_root,
                prompt_dir=prompt_dir,
                profile=profile,
                prompt_id=prompt_id,
                prompt_file=prompt_file,
                content=content,
            )
            if error is not None:
                return _prompt_error_from_payload(error, action="prompt-save")
            return JSONResponse(status_code=200, content=response_payload)
        finally:
            control_lock.release()

    @app.post("/api/prompts/restore")
    async def api_prompt_restore(request: Request) -> Any:
        nonlocal cfg
        if not controls_enabled:
            if lan_safety_active:
                return _prompt_action_error(
                    403,
                    "prompt-restore",
                    "lan_safety_mutation_blocked",
                    LAN_SAFETY_MUTATION_DISABLED_MESSAGE,
                    **_lan_safety_error_details("prompt-restore"),
                )
            return _prompt_action_error(
                403,
                "prompt-restore",
                "prompt_mutation_disabled",
                "Prompt restores are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _prompt_action_error(409, "prompt-restore", "prompt_restore_busy", "A prompt mutation is already in flight.")

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

            response_payload, error = restore_prompt(
                repo_root=repo_root,
                prompt_dir=prompt_dir,
                profile=profile,
                prompt_id=prompt_id,
                prompt_file=prompt_file,
                restore_path_value=restore_path_value,
                confirmation=confirmation,
            )
            if error is not None:
                return _prompt_error_from_payload(error, action="prompt-restore")
            return JSONResponse(status_code=200, content=response_payload)
        finally:
            control_lock.release()

    @app.get("/api/goals")
    def api_goals() -> dict[str, Any]:
        return _goals()

    @app.post("/api/goals/save")
    async def api_goals_save(request: Request) -> Any:
        goal_path = goals_path(repo_root)
        plan: GoalSavePlan | None = None
        if not controls_enabled:
            if lan_safety_active:
                return _goal_save_error(
                    403,
                    "lan_safety_mutation_blocked",
                    LAN_SAFETY_MUTATION_DISABLED_MESSAGE,
                    **_lan_safety_error_details("goals-save"),
                )
            return _goal_save_error(
                403,
                "goals_save_disabled",
                "GOALS saves are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
            )
        if not control_lock.acquire(blocking=False):
            return _goal_save_error(409, "goals_save_busy", "A goal save is already in flight.")

        try:
            body = await _goal_save_body(request)
            if body is None:
                return _goal_save_error(400, "invalid_json", "Goals save request body must be JSON.")

            plan = _goal_save_validate_request(body, repo_root=repo_root, goal_path=goal_path)
            response_payload = _goal_save_commit(plan, snapshot_factory=lambda: _snapshot(busy_override=False))
            return JSONResponse(status_code=200, content=response_payload)
        except GoalSaveFailure as ex:
            return _goal_save_error(ex.status_code, ex.code, ex.message, **ex.details)
        except Exception as ex:
            details: dict[str, Any] = {"path": goal_path.as_posix()}
            if plan is not None:
                details["backup_path"] = plan.backup_path.as_posix()
            return _goal_save_error(500, "goals_save_failed", f"Goals save failed: {ex}", **details)
        finally:
            control_lock.release()

    @app.get("/api/history")
    def api_history() -> dict[str, Any]:
        return _section("history")

    @app.get("/api/experience")
    def api_experience() -> dict[str, Any]:
        return _section("experience")

    @app.get("/api/pr-queue")
    def api_pr_queue() -> dict[str, Any]:
        payload = _build_pr_queue_payload(repo_root, detail=False)
        return _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_pr_queue_payload)

    @app.get("/api/pr-queue/{packet_id}")
    def api_pr_queue_detail(packet_id: str) -> Any:
        payload = _build_pr_queue_payload(repo_root, packet_id=packet_id, detail=True)
        payload = _web_apply_redaction(payload, active=web_redaction_active, redactor=_redact_web_pr_queue_payload)
        if not bool(payload.get("ok")):
            return JSONResponse(status_code=404, content=payload)
        return payload

    @app.get("/api/worktree")
    def api_worktree() -> dict[str, Any]:
        return _section("worktree")

    @app.get("/api/worktree/diagnostics")
    def api_worktree_diagnostics(request: Request) -> dict[str, Any]:
        categories = request.query_params.getlist("categories")
        if not categories:
            raw_categories = str(request.query_params.get("categories", "") or "").strip()
            categories = [raw_categories] if raw_categories else []
        return scan_worktree_diagnostics(repo_root, categories=categories)

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
        bind_port=int(port),
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

