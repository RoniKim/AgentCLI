"""Repo-local Experience DB initialization and schema migration helpers.

This module stores only compact structured experience metadata. It does not
store raw prompts, raw logs, or raw diffs.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from .config import ensure_work_dir, resolve_experience_dir
from .utils import atomic_write_text

EXPERIENCE_DB_FILENAME: Final[str] = "experience.db"
SCHEMA_VERSION_FILENAME: Final[str] = "schema_version"
EXPERIENCE_SCHEMA_VERSION: Final[int] = 1

_MIGRATION_TRACKING_SQL = """\
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
"""

_MIGRATIONS: Final[dict[int, tuple[str, ...]]] = {
    1: (
        """\
CREATE TABLE IF NOT EXISTS runs (
    run_id        TEXT PRIMARY KEY,
    started_at    TEXT NOT NULL,
    ended_at      TEXT NOT NULL DEFAULT '',
    backend       TEXT NOT NULL DEFAULT '',
    source_head   TEXT NOT NULL DEFAULT '',
    stop_reason   TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT ''
);
""",
        """\
CREATE TABLE IF NOT EXISTS task_experiences (
    run_id              TEXT NOT NULL,
    task_id             TEXT NOT NULL,
    title               TEXT NOT NULL,
    goal_refs           TEXT NOT NULL DEFAULT '[]',
    files               TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT '',
    reason              TEXT NOT NULL DEFAULT '',
    task_status         TEXT NOT NULL DEFAULT '',
    attempts            INTEGER NOT NULL DEFAULT 0,
    validation_status   TEXT NOT NULL DEFAULT '',
    branch              TEXT NOT NULL DEFAULT '',
    pr_id               TEXT NOT NULL DEFAULT '',
    lesson              TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (run_id, task_id),
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
""",
        """\
CREATE TABLE IF NOT EXISTS validation_experiences (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT NOT NULL,
    task_id         TEXT NOT NULL DEFAULT '',
    gate            TEXT NOT NULL,
    cmd_hash        TEXT NOT NULL DEFAULT '',
    rc              INTEGER,
    status          TEXT NOT NULL DEFAULT '',
    classification  TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    artifact_path   TEXT NOT NULL DEFAULT '',
    FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
);
""",
        """\
CREATE TABLE IF NOT EXISTS file_patterns (
    path            TEXT PRIMARY KEY,
    touch_count     INTEGER NOT NULL DEFAULT 0,
    success_count   INTEGER NOT NULL DEFAULT 0,
    failure_count   INTEGER NOT NULL DEFAULT 0,
    common_gates    TEXT NOT NULL DEFAULT '[]',
    risk_notes      TEXT NOT NULL DEFAULT ''
);
""",
        """\
CREATE TABLE IF NOT EXISTS lessons (
    id                               INTEGER PRIMARY KEY,
    kind                             TEXT NOT NULL,
    severity                         TEXT NOT NULL DEFAULT '',
    confidence                       REAL NOT NULL DEFAULT 0.0,
    trigger                          TEXT NOT NULL DEFAULT '',
    lesson                           TEXT NOT NULL DEFAULT '',
    evidence                         TEXT NOT NULL DEFAULT '[]',
    created_at                       TEXT NOT NULL DEFAULT '',
    last_seen_at                     TEXT NOT NULL DEFAULT '',
    seen_count                       INTEGER NOT NULL DEFAULT 0,
    applies_to_goal_refs             TEXT NOT NULL DEFAULT '[]',
    applies_to_file_globs            TEXT NOT NULL DEFAULT '[]',
    applies_to_gates                 TEXT NOT NULL DEFAULT '[]',
    applies_to_statuses              TEXT NOT NULL DEFAULT '[]',
    applies_to_validation_statuses   TEXT NOT NULL DEFAULT '[]',
    negative_patterns                TEXT NOT NULL DEFAULT '[]',
    last_applied_at                  TEXT NOT NULL DEFAULT '',
    last_helpful_at                  TEXT NOT NULL DEFAULT '',
    suppressed_until                 TEXT NOT NULL DEFAULT ''
);
""",
        "CREATE INDEX IF NOT EXISTS idx_task_experiences_status ON task_experiences(status, validation_status);",
        "CREATE INDEX IF NOT EXISTS idx_validation_experiences_task ON validation_experiences(run_id, task_id, gate);",
        "CREATE INDEX IF NOT EXISTS idx_lessons_kind_severity ON lessons(kind, severity);",
    ),
}


@dataclass(frozen=True)
class ExperienceDbPaths:
    root_dir: Path
    db_path: Path
    schema_version_path: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def experience_db_paths(
    repo: Path,
    *,
    experience_dir: str | Path | None = None,
) -> ExperienceDbPaths:
    root_dir = resolve_experience_dir(repo, str(experience_dir) if experience_dir is not None else None)
    return ExperienceDbPaths(
        root_dir=root_dir,
        db_path=(root_dir / EXPERIENCE_DB_FILENAME).resolve(),
        schema_version_path=(root_dir / SCHEMA_VERSION_FILENAME).resolve(),
    )


def initialize_experience_db(
    repo: Path,
    *,
    experience_dir: str | Path | None = None,
) -> ExperienceDbPaths:
    """Create or migrate the repo-local Experience DB and return its paths."""
    paths = experience_db_paths(repo, experience_dir=experience_dir)

    # Validate first; only create runtime directories once the target is known
    # to stay inside repo/.AgentCLI/experience.
    ensure_work_dir(repo)
    paths.root_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(paths.db_path), timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute(_MIGRATION_TRACKING_SQL)

        applied_versions = {int(row[0]) for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}
        for version in sorted(_MIGRATIONS):
            if version in applied_versions:
                continue
            for sql in _MIGRATIONS[version]:
                conn.execute(sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (version, _utc_now()),
            )

        conn.execute(f"PRAGMA user_version = {EXPERIENCE_SCHEMA_VERSION};")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    atomic_write_text(paths.schema_version_path, f"{EXPERIENCE_SCHEMA_VERSION}\n")
    return paths

"""Completed-task experience records stored on top of task history DB."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .task_history import _connect
from .utils import eprint

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS task_experiences (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id               TEXT NOT NULL,
    task_id              TEXT NOT NULL,
    title                TEXT NOT NULL,
    status               TEXT NOT NULL,
    task_status          TEXT DEFAULT '',
    validation_status    TEXT DEFAULT '',
    goal_refs            TEXT DEFAULT '[]',
    changed_files        TEXT DEFAULT '[]',
    branch_ref           TEXT DEFAULT '',
    head_ref             TEXT DEFAULT '',
    base_ref             TEXT DEFAULT '',
    validation_artifacts TEXT DEFAULT '[]',
    pr_packet_ids        TEXT DEFAULT '[]',
    recorded_at          TEXT NOT NULL,
    UNIQUE(run_id, task_id, status)
);
"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sequence_items(value: Sequence[object] | object | None) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray, Path)):
        return [value]
    if isinstance(value, Sequence):
        return list(value)
    return [value]


def _normalize_repo_pointer(repo: Path, value: object) -> str:
    text = _text(value)
    if not text:
        return ""
    candidate = Path(text).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        try:
            return resolved.relative_to(repo).as_posix()
        except ValueError:
            return resolved.as_posix()
    return Path(text.replace("\\", "/")).as_posix()


def _normalize_goal_refs(goal_trace: Sequence[object] | object | None) -> list[str]:
    refs: list[str] = []
    for item in _sequence_items(goal_trace):
        if not isinstance(item, dict):
            continue
        ref = _text(item.get("goal_ref") or item.get("goal_id") or item.get("id"))
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _normalize_changed_files(repo: Path, changed_files: Sequence[object] | object | None) -> list[str]:
    normalized: list[str] = []
    for item in _sequence_items(changed_files):
        if isinstance(item, dict):
            value = item.get("path") or item.get("file") or item.get("name") or ""
        else:
            value = item
        pointer = _normalize_repo_pointer(repo, value)
        if pointer and pointer not in normalized:
            normalized.append(pointer)
    return normalized


def _is_validation_artifact_pointer(path_text: str) -> bool:
    name = Path(str(path_text or "")).name.lower()
    if not name:
        return False
    if name.endswith(".patch"):
        return False
    if name.startswith("worktree_merge_pending"):
        return False
    if name.startswith("worktree_merge_applied"):
        return False
    if name.startswith("worktree_merge_discarded"):
        return False
    if name.startswith("worktree_apply_failure"):
        return False
    return True


def _normalize_validation_artifacts(
    repo: Path,
    *,
    validation_artifacts: Sequence[object] | object | None = None,
    validation_records: Sequence[object] | object | None = None,
) -> list[str]:
    artifact_values: list[str] = []
    for item in _sequence_items(validation_artifacts):
        if isinstance(item, dict):
            value = item.get("artifact_path") or item.get("log_path") or item.get("path") or ""
        else:
            value = item
        pointer = _normalize_repo_pointer(repo, value)
        if pointer and _is_validation_artifact_pointer(pointer) and pointer not in artifact_values:
            artifact_values.append(pointer)
    for record in _sequence_items(validation_records):
        if not isinstance(record, dict):
            continue
        value = record.get("artifact_path") or record.get("log_path") or record.get("path") or ""
        pointer = _normalize_repo_pointer(repo, value)
        if pointer and _is_validation_artifact_pointer(pointer) and pointer not in artifact_values:
            artifact_values.append(pointer)
    return artifact_values


def _normalize_pr_packet_ids(pr_packet_ids: Sequence[object] | object | None) -> list[str]:
    packet_ids: list[str] = []
    for item in _sequence_items(pr_packet_ids):
        if isinstance(item, dict):
            value = item.get("packet_id") or item.get("id") or ""
        else:
            value = item
        packet_id = _text(value)
        if packet_id and packet_id not in packet_ids:
            packet_ids.append(packet_id)
    return packet_ids


def record_completed_task_experience(
    repo: Path,
    *,
    run_id: str,
    task_id: str,
    title: str,
    status: str,
    task_status: str = "",
    validation_status: str = "",
    goal_trace: Sequence[object] | object | None = None,
    changed_files: Sequence[object] | object | None = None,
    branch_ref: str = "",
    head_ref: str = "",
    base_ref: str = "",
    validation_artifacts: Sequence[object] | object | None = None,
    validation_records: Sequence[object] | object | None = None,
    pr_packet_ids: Sequence[object] | object | None = None,
) -> None:
    """Persist a redacted completed-task experience record. Never raises."""
    try:
        repo_path = Path(repo).expanduser().resolve()
        conn = _connect(repo_path)
        try:
            conn.execute(_SCHEMA_SQL)
            payload = {
                "run_id": _text(run_id),
                "task_id": _text(task_id),
                "title": _text(title),
                "status": _text(status),
                "task_status": _text(task_status),
                "validation_status": _text(validation_status),
                "goal_refs": _normalize_goal_refs(goal_trace),
                "changed_files": _normalize_changed_files(repo_path, changed_files),
                "branch_ref": _text(branch_ref),
                "head_ref": _text(head_ref),
                "base_ref": _text(base_ref),
                "validation_artifacts": _normalize_validation_artifacts(
                    repo_path,
                    validation_artifacts=validation_artifacts,
                    validation_records=validation_records,
                ),
                "pr_packet_ids": _normalize_pr_packet_ids(pr_packet_ids),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            conn.execute(
                "INSERT INTO task_experiences "
                "("
                "run_id, task_id, title, status, task_status, validation_status, goal_refs, "
                "changed_files, branch_ref, head_ref, base_ref, validation_artifacts, pr_packet_ids, recorded_at"
                ") "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, task_id, status) DO UPDATE SET "
                "title=excluded.title, "
                "task_status=excluded.task_status, "
                "validation_status=excluded.validation_status, "
                "goal_refs=excluded.goal_refs, "
                "changed_files=excluded.changed_files, "
                "branch_ref=excluded.branch_ref, "
                "head_ref=excluded.head_ref, "
                "base_ref=excluded.base_ref, "
                "validation_artifacts=excluded.validation_artifacts, "
                "pr_packet_ids=excluded.pr_packet_ids, "
                "recorded_at=excluded.recorded_at",
                (
                    payload["run_id"],
                    payload["task_id"],
                    payload["title"],
                    payload["status"],
                    payload["task_status"],
                    payload["validation_status"],
                    json.dumps(payload["goal_refs"], ensure_ascii=False),
                    json.dumps(payload["changed_files"], ensure_ascii=False),
                    payload["branch_ref"],
                    payload["head_ref"],
                    payload["base_ref"],
                    json.dumps(payload["validation_artifacts"], ensure_ascii=False),
                    json.dumps(payload["pr_packet_ids"], ensure_ascii=False),
                    payload["recorded_at"],
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] experience.record_completed_task_experience failed: {exc}")


def query_completed_task_experiences(
    repo: Path,
    *,
    run_id: str | None = None,
    task_id: str | None = None,
    max_items: int = 50,
) -> list[dict[str, Any]]:
    """Return completed-task experience rows as dicts. Never raises."""
    try:
        repo_path = Path(repo).expanduser().resolve()
        conn = _connect(repo_path)
        try:
            conn.execute(_SCHEMA_SQL)
            sql = (
                "SELECT run_id, task_id, title, status, task_status, validation_status, goal_refs, "
                "changed_files, branch_ref, head_ref, base_ref, validation_artifacts, pr_packet_ids, recorded_at "
                "FROM task_experiences"
            )
            params: list[object] = []
            clauses: list[str] = []
            if _text(run_id):
                clauses.append("run_id = ?")
                params.append(_text(run_id))
            if _text(task_id):
                clauses.append("task_id = ?")
                params.append(_text(task_id))
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(int(max_items))
            rows = conn.execute(sql, tuple(params)).fetchall()
            result: list[dict[str, Any]] = []
            for row in rows:
                record = dict(
                    zip(
                        [
                            "run_id",
                            "task_id",
                            "title",
                            "status",
                            "task_status",
                            "validation_status",
                            "goal_refs",
                            "changed_files",
                            "branch_ref",
                            "head_ref",
                            "base_ref",
                            "validation_artifacts",
                            "pr_packet_ids",
                            "recorded_at",
                        ],
                        row,
                    )
                )
                for key in ("goal_refs", "changed_files", "validation_artifacts", "pr_packet_ids"):
                    try:
                        record[key] = json.loads(record.get(key) or "[]")
                    except Exception:
                        record[key] = []
                result.append(record)
            return result
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] experience.query_completed_task_experiences failed: {exc}")
        return []

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
