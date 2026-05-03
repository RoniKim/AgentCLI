"""Structured Experience DB helpers for failed/review-required task outcomes."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .config import default_database_path
from .utils import eprint

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS task_experiences (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT DEFAULT '',
    backend            TEXT DEFAULT '',
    task_id            TEXT NOT NULL,
    title              TEXT NOT NULL,
    status             TEXT NOT NULL,
    task_status        TEXT DEFAULT '',
    reason             TEXT DEFAULT '',
    cycle_idx          INTEGER DEFAULT 0,
    step_idx           INTEGER DEFAULT 0,
    attempt            INTEGER DEFAULT 0,
    max_attempts       INTEGER DEFAULT 0,
    validation_status  TEXT DEFAULT '',
    outcome_action     TEXT DEFAULT '',
    experience_payload TEXT NOT NULL DEFAULT '{}',
    recorded_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_experiences_task_id
    ON task_experiences(task_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_task_experiences_run_id
    ON task_experiences(run_id, recorded_at DESC);
"""

_MAX_SUMMARY_LEN = 240
_MAX_OUTCOME_NOTE_LEN = 160
_RAW_CONTENT_MARKERS = (
    "assistant:",
    "user:",
    "tool_result",
    "shell_command",
    "backend transcript",
    "transcript:",
    "stdout:",
    "stderr:",
    "traceback",
    "stack trace",
    "```",
)
_VALIDATION_REASON_SUMMARIES = {
    "build_failed": "Build validation failed.",
    "test_failed": "Test validation failed.",
    "fast_regression_failed": "Fast regression validation failed.",
    "policy_violation": "Policy validation failed.",
}


def _db_path(repo: Path) -> Path:
    return default_database_path(repo)


def _connect(repo: Path) -> sqlite3.Connection:
    db = _db_path(repo)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _normalize_pointer(value: Any) -> str:
    text = _text(value)
    return text.replace("\\", "/") if text else ""


def _looks_like_raw_content(text: str) -> bool:
    normalized = _text(text)
    if not normalized:
        return False
    lowered = normalized.lower()
    if any(marker in lowered for marker in _RAW_CONTENT_MARKERS):
        return True
    if normalized.count("\n") >= 2:
        return True
    return len(normalized) > _MAX_SUMMARY_LEN


def _sanitize_summary_text(value: Any, *, max_chars: int = _MAX_SUMMARY_LEN) -> str:
    text = _text(value)
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    candidate = lines[0] if lines else text
    if _looks_like_raw_content(text):
        if _looks_like_raw_content(candidate):
            return ""
        text = candidate
    else:
        text = candidate
    text = " ".join(text.split())
    if len(text) > max_chars:
        text = text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def _dedupe_strings(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = _normalize_pointer(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _sanitize_blocked_dependencies(blocked_dependencies: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in blocked_dependencies or []:
        if not isinstance(item, dict):
            continue
        entry = {
            "task_id": _text(item.get("task_id") or item.get("taskId")),
            "title": _text(item.get("title")),
            "status": _text(item.get("status")),
            "reason": _text(item.get("reason")),
            "validation_summary": _sanitize_summary_text(item.get("validation_summary") or item.get("validationSummary")),
            "next_action": _sanitize_summary_text(item.get("next_action") or item.get("nextAction")),
        }
        sanitized.append({key: value for key, value in entry.items() if value not in ("", None)})
    return sanitized


def _sanitize_validation_records(validations: Sequence[dict[str, Any]] | None) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for item in validations or []:
        if not isinstance(item, dict):
            continue
        summary = _sanitize_summary_text(
            item.get("failure_summary")
            or item.get("failureSummary")
            or item.get("summary")
        )
        artifact_pointer = _normalize_pointer(
            item.get("artifact_path")
            or item.get("artifactPath")
            or item.get("log_path")
            or item.get("logPath")
        )
        record = {
            "gate": _text(item.get("gate") or item.get("name") or item.get("kind")),
            "status": _text(item.get("status")),
            "rc": _int(item.get("rc"), 0),
            "summary": summary,
            "artifact_pointer": artifact_pointer,
        }
        sanitized.append({key: value for key, value in record.items() if value not in ("", None)})
    return sanitized


def _collect_artifact_pointers(
    artifact_pointers: Sequence[str] | None,
    validations: Sequence[dict[str, Any]] | None,
) -> list[str]:
    collected: list[str] = list(artifact_pointers or [])
    for item in validations or []:
        if not isinstance(item, dict):
            continue
        for key in ("artifact_path", "artifactPath", "log_path", "logPath"):
            pointer = _normalize_pointer(item.get(key))
            if pointer:
                collected.append(pointer)
    return _dedupe_strings(collected)


def _resolve_validation_summary(
    *,
    reason: str,
    validation_summary: str,
    validations: Sequence[dict[str, Any]] | None,
    detail: str,
) -> str:
    summary = _sanitize_summary_text(validation_summary)
    if summary:
        return summary
    for item in validations or []:
        if not isinstance(item, dict):
            continue
        summary = _sanitize_summary_text(
            item.get("failure_summary")
            or item.get("failureSummary")
            or item.get("summary")
        )
        if summary:
            return summary
    summary = _sanitize_summary_text(detail)
    if summary:
        return summary
    return _VALIDATION_REASON_SUMMARIES.get(_text(reason).lower(), "")


def _normalize_validation_status(
    *,
    validation_status: str,
    validations: Sequence[dict[str, Any]] | None,
    validation_summary: str,
    artifact_pointers: Sequence[str],
) -> str:
    normalized = _text(validation_status).lower()
    if normalized:
        return normalized
    for item in validations or []:
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status")).lower()
        if status == "failed":
            return "validation_failed"
        if status:
            return status
    if validation_summary or artifact_pointers:
        return "validation_failed"
    return ""


def _default_outcome_action(*, reason: str, task_status: str) -> str:
    normalized_reason = _text(reason).lower()
    normalized_status = _text(task_status).lower()
    if normalized_reason.startswith("dependency_"):
        return "not_run_dependency_blocked"
    if normalized_reason == "persistent_failure":
        return "skipped_after_repeated_failures"
    if normalized_status in {"review_required", "blocked_env", "test_contract_changed"}:
        return "preserved_for_review"
    return "discarded"


def record_task_experience(
    repo: Path,
    *,
    run_id: str,
    backend: str,
    task_id: str,
    title: str,
    status: str,
    reason: str = "",
    task_status: str = "",
    cycle_idx: int = 0,
    step_idx: int = 0,
    attempt: int = 0,
    max_attempts: int = 0,
    validation_status: str = "",
    validation_summary: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    blocked_dependencies: Sequence[dict[str, Any]] | None = None,
    artifact_pointers: Sequence[str] | None = None,
    outcome_action: str = "",
    outcome_note: str = "",
    detail: str = "",
) -> None:
    """Persist a structured failure/review-required task experience. Never raises."""
    try:
        normalized_status = _text(status).lower()
        normalized_task_status = _text(task_status, normalized_status).lower()
        normalized_reason = _text(reason).lower()
        sanitized_blockers = _sanitize_blocked_dependencies(blocked_dependencies)
        sanitized_records = _sanitize_validation_records(validations)
        sanitized_artifact_pointers = _collect_artifact_pointers(artifact_pointers, validations)
        sanitized_validation_summary = _resolve_validation_summary(
            reason=normalized_reason,
            validation_summary=validation_summary,
            validations=validations,
            detail=detail,
        )
        normalized_validation_status = _normalize_validation_status(
            validation_status=validation_status,
            validations=validations,
            validation_summary=sanitized_validation_summary,
            artifact_pointers=sanitized_artifact_pointers,
        )
        normalized_outcome_action = _text(
            outcome_action,
            _default_outcome_action(reason=normalized_reason, task_status=normalized_task_status),
        ).lower()
        payload = {
            "schema_version": 1,
            "status": normalized_status,
            "task_status": normalized_task_status,
            "reason": normalized_reason,
            "blocked_dependencies": sanitized_blockers,
            "artifact_pointers": sanitized_artifact_pointers,
            "validation": {
                "status": normalized_validation_status,
                "summary": sanitized_validation_summary,
                "artifact_pointers": sanitized_artifact_pointers,
                "records": sanitized_records,
            },
            "attempt": {
                "cycle": _int(cycle_idx, 0),
                "step": _int(step_idx, 0),
                "attempt": _int(attempt, 0),
                "max_attempts": _int(max_attempts, 0),
            },
            "outcome": {
                "action": normalized_outcome_action,
                "note": _sanitize_summary_text(outcome_note, max_chars=_MAX_OUTCOME_NOTE_LEN),
            },
        }
        conn = _connect(repo)
        try:
            conn.execute(
                "INSERT INTO task_experiences "
                "(run_id, backend, task_id, title, status, task_status, reason, cycle_idx, step_idx, attempt, max_attempts, validation_status, outcome_action, experience_payload, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _text(run_id),
                    _text(backend),
                    _text(task_id),
                    _text(title),
                    normalized_status,
                    normalized_task_status,
                    normalized_reason,
                    _int(cycle_idx, 0),
                    _int(step_idx, 0),
                    _int(attempt, 0),
                    _int(max_attempts, 0),
                    normalized_validation_status,
                    normalized_outcome_action,
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] experience.record_task_experience failed: {exc}")


def query_task_experiences(
    repo: Path,
    *,
    task_id: str = "",
    max_items: int = 50,
) -> list[dict[str, Any]]:
    """Return recent structured task experiences. Never raises."""
    try:
        conn = _connect(repo)
        try:
            sql = (
                "SELECT run_id, backend, task_id, title, status, task_status, reason, cycle_idx, step_idx, "
                "attempt, max_attempts, validation_status, outcome_action, experience_payload, recorded_at "
                "FROM task_experiences "
            )
            params: tuple[Any, ...]
            normalized_task_id = _text(task_id)
            if normalized_task_id:
                sql += "WHERE task_id = ? ORDER BY id DESC LIMIT ?"
                params = (normalized_task_id, _int(max_items, 50))
            else:
                sql += "ORDER BY id DESC LIMIT ?"
                params = (_int(max_items, 50),)
            rows = conn.execute(sql, params).fetchall()
            columns = [
                "run_id",
                "backend",
                "task_id",
                "title",
                "status",
                "task_status",
                "reason",
                "cycle_idx",
                "step_idx",
                "attempt",
                "max_attempts",
                "validation_status",
                "outcome_action",
                "experience_payload",
                "recorded_at",
            ]
            result: list[dict[str, Any]] = []
            for row in rows:
                item = dict(zip(columns, row))
                try:
                    item["payload"] = json.loads(item.get("experience_payload") or "{}")
                except Exception:
                    item["payload"] = {}
                result.append(item)
            return result
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] experience.query_task_experiences failed: {exc}")
        return []
