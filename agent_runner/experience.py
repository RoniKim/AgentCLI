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

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import AGENT_WORK_DIR
from .gates import looks_like_no_tests_found
from .task_status import TASK_STATUS_BLOCKED_ENV, classify_task_failure
from .utils import eprint, now_iso


VALIDATION_EXPERIENCE_CLASSIFICATIONS: tuple[str, ...] = (
    "validation_pending",
    "tests_skipped",
    "no_tests_found",
    "validation_failed",
    "blocked_env",
    "validation_passed",
)

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS validation_experiences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_tx_id TEXT NOT NULL UNIQUE,
    source_kind TEXT NOT NULL,
    run_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    task_title TEXT NOT NULL DEFAULT '',
    task_ids_json TEXT NOT NULL DEFAULT '[]',
    packet_id TEXT NOT NULL DEFAULT '',
    gate TEXT NOT NULL DEFAULT '',
    command_hash TEXT NOT NULL DEFAULT '',
    return_code INTEGER,
    status TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    artifact_path TEXT NOT NULL DEFAULT '',
    artifact_paths_json TEXT NOT NULL DEFAULT '[]',
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_validation_experiences_run_id ON validation_experiences(run_id);
CREATE INDEX IF NOT EXISTS idx_validation_experiences_task_id ON validation_experiences(task_id);
CREATE INDEX IF NOT EXISTS idx_validation_experiences_packet_id ON validation_experiences(packet_id);
CREATE INDEX IF NOT EXISTS idx_validation_experiences_classification ON validation_experiences(classification);
"""

_MAX_SUMMARY_LINES = 4
_MAX_SUMMARY_CHARS = 320
_DIFF_LINE_RE = re.compile(r"^(diff --git |index [0-9a-f]+\.\.[0-9a-f]+|@@ |--- |\+\+\+ )", re.IGNORECASE)
_PROMPT_LINE_RE = re.compile(
    r"(?i)(^<[/]?(system|assistant|user|developer)>|ignore previous instructions|you are the |implementation instructions:)"
)
_SKIP_TEXT_RE = re.compile(
    r"(?i)(disabled by run configuration|not applicable|intentionally skipped|skipped by policy|skip requested)"
)
_PENDING_TEXT_RE = re.compile(
    r"(?i)(not run because|validation deferred|deferred to pr|awaiting validation|validation pending|not reached)"
)


def experience_root(repo: Path) -> Path:
    return Path(repo).expanduser().resolve() / AGENT_WORK_DIR / "experience"


def experience_db_path(repo: Path) -> Path:
    return experience_root(repo) / "experience.db"


def _connect(repo: Path) -> sqlite3.Connection:
    root = experience_root(repo)
    root.mkdir(parents=True, exist_ok=True)
    try:
        (root / "schema_version").write_text("1\n", encoding="utf-8")
    except Exception:
        pass
    conn = sqlite3.connect(str(experience_db_path(repo)), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def _normalize_str_list(value: Sequence[object] | object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        try:
            items = list(value)  # type: ignore[arg-type]
        except TypeError:
            items = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _relative_artifact_path(repo: Path, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        path = Path(text).expanduser()
        if path.is_absolute():
            resolved = path.resolve()
            try:
                return resolved.relative_to(Path(repo).expanduser().resolve()).as_posix()
            except Exception:
                return resolved.as_posix()
        return Path(text).as_posix()
    except Exception:
        return text.replace("\\", "/")


def _normalize_artifact_paths(
    repo: Path,
    *groups: Sequence[object] | object | None,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in _normalize_str_list(group):
            normalized = _relative_artifact_path(repo, item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
    return out


def _normalize_recorded_at(record: Mapping[str, Any] | None) -> str:
    if record:
        for key in ("ended_at", "endedAt", "started_at", "startedAt", "recorded_at", "recordedAt"):
            value = str(record.get(key) or "").strip()
            if value:
                return value
    return now_iso()


def _normalize_gate(record: Mapping[str, Any] | None, fallback: str = "") -> str:
    if record:
        for key in ("gate", "kind", "name"):
            value = str(record.get(key) or "").strip()
            if value:
                return value
    return str(fallback or "").strip()


def _record_status(record: Mapping[str, Any] | None, explicit_status: object = "") -> str:
    for value in (
        explicit_status,
        record.get("classification") if record else "",
        record.get("validation_status") if record else "",
        record.get("validationStatus") if record else "",
        record.get("status") if record else "",
    ):
        text = str(value or "").strip().lower()
        if text:
            return text
    return ""


def _record_reason(record: Mapping[str, Any] | None, explicit_reason: object = "") -> str:
    for value in (
        explicit_reason,
        record.get("reason") if record else "",
        record.get("validation_reason") if record else "",
        record.get("validationReason") if record else "",
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _record_summary(record: Mapping[str, Any] | None, explicit_detail: object = "") -> str:
    parts: list[str] = []
    for value in (
        explicit_detail,
        record.get("summary") if record else "",
        record.get("failure_summary") if record else "",
        record.get("failureSummary") if record else "",
        record.get("detail") if record else "",
        record.get("validation_detail") if record else "",
        record.get("validationDetail") if record else "",
        record.get("note") if record else "",
    ):
        text = str(value or "").strip()
        if text and text not in parts:
            parts.append(text)
    return "\n".join(parts)


def _record_return_code(record: Mapping[str, Any] | None) -> int | None:
    if record is None:
        return None
    value = record.get("rc")
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _record_ok(record: Mapping[str, Any] | None) -> bool | None:
    if record is None or "ok" not in record:
        return None
    try:
        return bool(record.get("ok"))
    except Exception:
        return None


def redact_validation_summary(text: object) -> str:
    lines: list[str] = []
    saw_diff = False
    saw_prompt = False
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if "```" in line:
            saw_prompt = True
            continue
        if _DIFF_LINE_RE.search(line):
            saw_diff = True
            continue
        if _PROMPT_LINE_RE.search(line):
            saw_prompt = True
            continue
        lines.append(line)
        if len(lines) >= _MAX_SUMMARY_LINES:
            break
    summary = " ".join(lines)
    summary = re.sub(r"\s+", " ", summary).strip()
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS].rstrip()
    if saw_diff and "[diff redacted]" not in summary:
        summary = (summary + " [diff redacted]").strip() if summary else "[diff redacted]"
    if saw_prompt and "[prompt-like text redacted]" not in summary:
        summary = (summary + " [prompt-like text redacted]").strip() if summary else "[prompt-like text redacted]"
    return summary


def _normalized_command_payload(record: Mapping[str, Any] | None) -> list[object]:
    if record is None:
        return []
    cmd = record.get("cmd")
    if isinstance(cmd, list):
        return [str(part).strip() for part in cmd if str(part).strip()]
    if isinstance(cmd, str) and cmd.strip():
        return [cmd.strip()]
    commands = record.get("commands")
    if isinstance(commands, list):
        payload: list[object] = []
        for item in commands:
            if isinstance(item, Mapping):
                nested_cmd = item.get("cmd")
                if isinstance(nested_cmd, list):
                    payload.append([str(part).strip() for part in nested_cmd if str(part).strip()])
                elif nested_cmd:
                    payload.append(str(nested_cmd).strip())
                elif item.get("test_file"):
                    payload.append(str(item.get("test_file")).strip())
        return payload
    return []


def hash_validation_command(record: Mapping[str, Any] | None) -> str:
    payload = _normalized_command_payload(record)
    if not payload:
        return ""
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]


def classify_validation_experience(
    record: Mapping[str, Any] | None = None,
    *,
    status: object = "",
    reason: object = "",
    detail: object = "",
) -> str:
    raw_status = _record_status(record, status)
    raw_reason = _record_reason(record, reason).strip().lower()
    summary = _record_summary(record, detail)
    lowered = summary.lower()
    rc = _record_return_code(record)
    ok = _record_ok(record)

    if looks_like_no_tests_found(summary):
        return "no_tests_found"
    if raw_status in {"blocked_env"}:
        return "blocked_env"
    if raw_status in {"tests_skipped", "skipped"}:
        return "tests_skipped"
    if _SKIP_TEXT_RE.search(summary):
        return "tests_skipped"
    if raw_status in {"validation_pending", "pending", "running", "stopped", "timeout"}:
        return "validation_pending"
    if _PENDING_TEXT_RE.search(summary):
        return "validation_pending"
    if raw_status in {"validation_failed", "failed", "fail", "error"}:
        task_status = classify_task_failure(raw_reason or "validation_failed", validations=[dict(record or {})], detail=summary)
        return "blocked_env" if task_status == TASK_STATUS_BLOCKED_ENV else "validation_failed"
    if rc is not None and rc != 0:
        task_status = classify_task_failure(raw_reason or "validation_failed", validations=[dict(record or {})], detail=summary)
        return "blocked_env" if task_status == TASK_STATUS_BLOCKED_ENV else "validation_failed"
    if ok is False:
        task_status = classify_task_failure(raw_reason or "validation_failed", validations=[dict(record or {})], detail=summary)
        return "blocked_env" if task_status == TASK_STATUS_BLOCKED_ENV else "validation_failed"
    if raw_status in {"validation_passed", "passed", "pass", "success", "completed", "ok"}:
        return "validation_passed"
    if ok is True:
        return "validation_passed"
    if rc == 0 and (_normalized_command_payload(record) or summary):
        return "validation_passed"
    return "validation_pending"


def classify_validation_experience_group(
    *,
    status: object = "",
    reason: object = "",
    detail: object = "",
    validation_records: Sequence[Mapping[str, Any]] | None = None,
) -> str:
    child_classifications = [
        classify_validation_experience(record, reason=reason)
        for record in (validation_records or [])
        if isinstance(record, Mapping)
    ]
    for value in ("blocked_env", "validation_failed", "no_tests_found", "tests_skipped", "validation_pending"):
        if value in child_classifications:
            return value
    return classify_validation_experience(status=status, reason=reason, detail=detail)


def build_validation_experience_rows(
    repo: Path,
    *,
    source_kind: str,
    run_id: str,
    task_id: str = "",
    task_title: str = "",
    task_ids: Sequence[object] | object | None = None,
    packet_id: str = "",
    validation_status: str = "",
    validation_reason: str = "",
    validation_detail: str = "",
    validation_artifact_path: str = "",
    validation_artifacts: Sequence[object] | object | None = None,
    validation_records: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    repo_path = Path(repo).expanduser().resolve()
    task_ids_list = _normalize_str_list(task_ids or task_id)
    record_items = [dict(item) for item in (validation_records or []) if isinstance(item, Mapping)]
    rows: list[dict[str, Any]] = []
    for item in record_items:
        gate = _normalize_gate(item)
        summary = redact_validation_summary(_record_summary(item))
        artifact_paths = _normalize_artifact_paths(
            repo_path,
            item.get("artifact_path"),
            item.get("artifactPath"),
            item.get("log_path"),
            item.get("logPath"),
        )
        row = {
            "source_kind": str(source_kind or "").strip(),
            "run_id": str(run_id or "").strip(),
            "task_id": str(task_id or "").strip(),
            "task_title": str(task_title or "").strip(),
            "task_ids": list(task_ids_list),
            "packet_id": str(packet_id or "").strip(),
            "gate": gate,
            "command_hash": hash_validation_command(item),
            "return_code": _record_return_code(item),
            "status": _record_status(item),
            "classification": classify_validation_experience(item, reason=validation_reason),
            "reason": _record_reason(item, validation_reason),
            "summary": summary,
            "artifact_path": artifact_paths[0] if artifact_paths else "",
            "artifact_paths": artifact_paths,
            "recorded_at": _normalize_recorded_at(item),
        }
        row["client_tx_id"] = hashlib.sha256(
            json.dumps(
                {
                    "source_kind": row["source_kind"],
                    "run_id": row["run_id"],
                    "task_id": row["task_id"],
                    "packet_id": row["packet_id"],
                    "gate": row["gate"],
                    "artifact_path": row["artifact_path"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8", errors="ignore")
        ).hexdigest()
        rows.append(row)

    aggregate_artifact_paths = _normalize_artifact_paths(
        repo_path,
        validation_artifact_path,
        validation_artifacts,
        [row["artifact_path"] for row in rows],
    )
    aggregate_return_code = next((row["return_code"] for row in rows if row.get("return_code") not in (None, 0)), 0 if rows else None)
    aggregate_gate = "pr_queue_validation" if str(source_kind or "").strip() == "pr_queue_validation" else "task_validation"
    aggregate_row = {
        "source_kind": str(source_kind or "").strip(),
        "run_id": str(run_id or "").strip(),
        "task_id": str(task_id or "").strip(),
        "task_title": str(task_title or "").strip(),
        "task_ids": list(task_ids_list),
        "packet_id": str(packet_id or "").strip(),
        "gate": aggregate_gate,
        "command_hash": "",
        "return_code": aggregate_return_code,
        "status": str(validation_status or "").strip().lower(),
        "classification": classify_validation_experience_group(
            status=validation_status,
            reason=validation_reason,
            detail=validation_detail,
            validation_records=record_items,
        ),
        "reason": str(validation_reason or "").strip(),
        "summary": redact_validation_summary(validation_detail),
        "artifact_path": aggregate_artifact_paths[0] if aggregate_artifact_paths else "",
        "artifact_paths": aggregate_artifact_paths,
        "recorded_at": now_iso(),
    }
    aggregate_row["client_tx_id"] = hashlib.sha256(
        json.dumps(
            {
                "source_kind": aggregate_row["source_kind"],
                "run_id": aggregate_row["run_id"],
                "task_id": aggregate_row["task_id"],
                "packet_id": aggregate_row["packet_id"],
                "gate": aggregate_row["gate"],
                "artifact_path": aggregate_row["artifact_path"],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8", errors="ignore")
    ).hexdigest()
    return [aggregate_row, *rows]


def record_validation_experiences(
    repo: Path,
    *,
    source_kind: str,
    run_id: str,
    task_id: str = "",
    task_title: str = "",
    task_ids: Sequence[object] | object | None = None,
    packet_id: str = "",
    validation_status: str = "",
    validation_reason: str = "",
    validation_detail: str = "",
    validation_artifact_path: str = "",
    validation_artifacts: Sequence[object] | object | None = None,
    validation_records: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    try:
        rows = build_validation_experience_rows(
            repo,
            source_kind=source_kind,
            run_id=run_id,
            task_id=task_id,
            task_title=task_title,
            task_ids=task_ids,
            packet_id=packet_id,
            validation_status=validation_status,
            validation_reason=validation_reason,
            validation_detail=validation_detail,
            validation_artifact_path=validation_artifact_path,
            validation_artifacts=validation_artifacts,
            validation_records=validation_records,
        )
        conn = _connect(repo)
        try:
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO validation_experiences (
                        client_tx_id,
                        source_kind,
                        run_id,
                        task_id,
                        task_title,
                        task_ids_json,
                        packet_id,
                        gate,
                        command_hash,
                        return_code,
                        status,
                        classification,
                        reason,
                        summary,
                        artifact_path,
                        artifact_paths_json,
                        recorded_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(client_tx_id) DO UPDATE SET
                        source_kind=excluded.source_kind,
                        run_id=excluded.run_id,
                        task_id=excluded.task_id,
                        task_title=excluded.task_title,
                        task_ids_json=excluded.task_ids_json,
                        packet_id=excluded.packet_id,
                        gate=excluded.gate,
                        command_hash=excluded.command_hash,
                        return_code=excluded.return_code,
                        status=excluded.status,
                        classification=excluded.classification,
                        reason=excluded.reason,
                        summary=excluded.summary,
                        artifact_path=excluded.artifact_path,
                        artifact_paths_json=excluded.artifact_paths_json,
                        recorded_at=excluded.recorded_at
                    """,
                    (
                        row["client_tx_id"],
                        row["source_kind"],
                        row["run_id"],
                        row["task_id"],
                        row["task_title"],
                        json.dumps(list(row["task_ids"]), ensure_ascii=False),
                        row["packet_id"],
                        row["gate"],
                        row["command_hash"],
                        row["return_code"],
                        row["status"],
                        row["classification"],
                        row["reason"],
                        row["summary"],
                        row["artifact_path"],
                        json.dumps(list(row["artifact_paths"]), ensure_ascii=False),
                        row["recorded_at"],
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return rows
    except Exception as exc:
        eprint(f"[WARN] experience.record_validation_experiences failed: {exc}")
        return []


def load_validation_experiences(
    repo: Path,
    *,
    run_id: str = "",
    task_id: str = "",
    packet_id: str = "",
) -> list[dict[str, Any]]:
    db = experience_db_path(repo)
    if not db.exists():
        return []
    where: list[str] = []
    params: list[object] = []
    if str(run_id or "").strip():
        where.append("run_id = ?")
        params.append(str(run_id).strip())
    if str(task_id or "").strip():
        where.append("task_id = ?")
        params.append(str(task_id).strip())
    if str(packet_id or "").strip():
        where.append("packet_id = ?")
        params.append(str(packet_id).strip())
    sql = (
        "SELECT client_tx_id, source_kind, run_id, task_id, task_title, task_ids_json, packet_id, gate, "
        "command_hash, return_code, status, classification, reason, summary, artifact_path, artifact_paths_json, recorded_at "
        "FROM validation_experiences"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id ASC"
    try:
        conn = sqlite3.connect(str(db), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] experience.load_validation_experiences failed: {exc}")
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        task_ids_raw = row["task_ids_json"] or "[]"
        artifact_paths_raw = row["artifact_paths_json"] or "[]"
        try:
            task_ids_value = json.loads(task_ids_raw)
        except Exception:
            task_ids_value = []
        try:
            artifact_paths_value = json.loads(artifact_paths_raw)
        except Exception:
            artifact_paths_value = []
        out.append(
            {
                "client_tx_id": row["client_tx_id"],
                "source_kind": row["source_kind"],
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "task_title": row["task_title"],
                "task_ids": list(task_ids_value) if isinstance(task_ids_value, list) else [],
                "packet_id": row["packet_id"],
                "gate": row["gate"],
                "command_hash": row["command_hash"],
                "return_code": row["return_code"],
                "status": row["status"],
                "classification": row["classification"],
                "reason": row["reason"],
                "summary": row["summary"],
                "artifact_path": row["artifact_path"],
                "artifact_paths": list(artifact_paths_value) if isinstance(artifact_paths_value, list) else [],
                "recorded_at": row["recorded_at"],
            }
        )
    return out

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from .utils import eprint, now_iso


_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS pr_queue_experiences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key       TEXT NOT NULL UNIQUE,
    recorded_at     TEXT NOT NULL,
    source_repo     TEXT NOT NULL,
    run_id          TEXT DEFAULT '',
    packet_id       TEXT NOT NULL,
    task_id         TEXT DEFAULT '',
    signal_kind     TEXT NOT NULL,
    decision_status TEXT NOT NULL,
    reason          TEXT DEFAULT '',
    branch          TEXT DEFAULT '',
    base_ref        TEXT DEFAULT '',
    head_ref        TEXT DEFAULT '',
    source_head     TEXT DEFAULT '',
    goal_trace      TEXT DEFAULT '[]',
    evidence        TEXT DEFAULT '[]',
    metadata        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_pr_queue_experiences_packet
    ON pr_queue_experiences(packet_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_pr_queue_experiences_task
    ON pr_queue_experiences(task_id, id DESC);

CREATE INDEX IF NOT EXISTS idx_pr_queue_experiences_signal
    ON pr_queue_experiences(signal_kind, decision_status, id DESC);
"""


def experience_root(source_repo: Path) -> Path:
    return Path(source_repo).expanduser().resolve() / ".AgentCLI" / "experience"


def experience_db_path(source_repo: Path) -> Path:
    return experience_root(source_repo) / "experience.db"


def _connect(source_repo: Path) -> sqlite3.Connection:
    db_path = experience_db_path(source_repo)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


def _normalize_list(value: Sequence[object] | object | None) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return [value]


def _normalize_str_list(value: Sequence[object] | object | None) -> list[str]:
    items = _normalize_list(value)
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _normalize_evidence_pointers(value: Sequence[object] | object | None) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in _normalize_list(value):
        pointer: dict[str, object]
        if isinstance(item, dict):
            pointer = {
                str(key): raw_value
                for key, raw_value in item.items()
                if raw_value not in (None, "", [], {})
            }
            path_text = str(pointer.get("path") or "").strip()
            if path_text:
                pointer["path"] = path_text
        else:
            path_text = str(item or "").strip()
            if not path_text:
                continue
            pointer = {
                "kind": "artifact",
                "path": path_text,
            }
        if not pointer:
            continue
        pointer_key = json.dumps(pointer, ensure_ascii=False, sort_keys=True, default=str)
        if pointer_key in seen:
            continue
        seen.add(pointer_key)
        result.append(pointer)
    return result


def _json_loads(value: object, default: object) -> object:
    text = str(value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def record_pr_queue_signal(
    source_repo: Path,
    *,
    packet: dict[str, Any],
    signal_kind: str,
    decision_status: str,
    reason: str = "",
    evidence: Sequence[object] | object | None = None,
    metadata: dict[str, object] | None = None,
    recorded_at: str | None = None,
    packet_id: str | None = None,
    task_ids: Sequence[object] | object | None = None,
) -> list[dict[str, Any]]:
    """Record PR queue decision signals. Failures are logged and ignored."""
    try:
        source_repo_path = Path(source_repo).expanduser().resolve()
        packet_id_text = str(packet_id or packet.get("packet_id") or packet.get("packetId") or "").strip()
        signal_kind_text = str(signal_kind or "").strip().lower()
        decision_status_text = str(decision_status or "").strip().lower()
        if not packet_id_text or not signal_kind_text or not decision_status_text:
            return []

        metadata_value = dict(metadata or {})
        reason_text = str(reason or "").strip()
        recorded_at_text = str(recorded_at or metadata_value.get("recorded_at") or now_iso()).strip() or now_iso()
        task_id_values = _normalize_str_list(task_ids or packet.get("task_ids") or packet.get("taskIds"))
        if not task_id_values:
            task_id_values = [""]

        run_id_text = str(packet.get("run_id") or packet.get("runId") or "").strip()
        branch_text = str(packet.get("branch") or "").strip()
        base_ref_text = str(packet.get("base_ref") or packet.get("baseRef") or "").strip()
        head_ref_text = str(packet.get("head_ref") or packet.get("headRef") or "").strip()
        source_head_text = str(
            metadata_value.get("source_head")
            or metadata_value.get("sourceHead")
            or packet.get("source_head_after")
            or packet.get("sourceHeadAfter")
            or packet.get("source_head_before")
            or packet.get("sourceHeadBefore")
            or ""
        ).strip()
        goal_trace_value = _normalize_list(packet.get("goal_trace") or packet.get("goalTrace"))
        evidence_value = _normalize_evidence_pointers(evidence)

        conn = _connect(source_repo_path)
        try:
            for task_id_text in task_id_values:
                event_key = "|".join(
                    (
                        packet_id_text,
                        task_id_text,
                        signal_kind_text,
                        decision_status_text,
                        reason_text.lower(),
                    )
                )
                conn.execute(
                    """
                    INSERT INTO pr_queue_experiences (
                        event_key,
                        recorded_at,
                        source_repo,
                        run_id,
                        packet_id,
                        task_id,
                        signal_kind,
                        decision_status,
                        reason,
                        branch,
                        base_ref,
                        head_ref,
                        source_head,
                        goal_trace,
                        evidence,
                        metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(event_key) DO UPDATE SET
                        recorded_at = excluded.recorded_at,
                        reason = excluded.reason,
                        branch = excluded.branch,
                        base_ref = excluded.base_ref,
                        head_ref = excluded.head_ref,
                        source_head = excluded.source_head,
                        goal_trace = excluded.goal_trace,
                        evidence = excluded.evidence,
                        metadata = excluded.metadata
                    """,
                    (
                        event_key,
                        recorded_at_text,
                        source_repo_path.as_posix(),
                        run_id_text,
                        packet_id_text,
                        task_id_text,
                        signal_kind_text,
                        decision_status_text,
                        reason_text,
                        branch_text,
                        base_ref_text,
                        head_ref_text,
                        source_head_text,
                        json.dumps(goal_trace_value, ensure_ascii=False, default=str),
                        json.dumps(evidence_value, ensure_ascii=False, default=str),
                        json.dumps(metadata_value, ensure_ascii=False, default=str),
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        return query_pr_queue_signals(
            source_repo_path,
            packet_id=packet_id_text,
            signal_kind=signal_kind_text,
            max_items=max(1, len(task_id_values)),
        )
    except Exception as exc:
        eprint(f"[WARN] experience.record_pr_queue_signal failed: {exc}")
        return []


def query_pr_queue_signals(
    source_repo: Path,
    *,
    packet_id: str | None = None,
    task_id: str | None = None,
    signal_kind: str | None = None,
    decision_status: str | None = None,
    max_items: int = 100,
) -> list[dict[str, Any]]:
    try:
        source_repo_path = Path(source_repo).expanduser().resolve()
        conn = _connect(source_repo_path)
        try:
            where: list[str] = []
            params: list[object] = []
            if packet_id:
                where.append("packet_id = ?")
                params.append(str(packet_id).strip())
            if task_id:
                where.append("task_id = ?")
                params.append(str(task_id).strip())
            if signal_kind:
                where.append("signal_kind = ?")
                params.append(str(signal_kind).strip().lower())
            if decision_status:
                where.append("decision_status = ?")
                params.append(str(decision_status).strip().lower())
            query = (
                "SELECT recorded_at, source_repo, run_id, packet_id, task_id, signal_kind, decision_status, "
                "reason, branch, base_ref, head_ref, source_head, goal_trace, evidence, metadata "
                "FROM pr_queue_experiences"
            )
            if where:
                query += " WHERE " + " AND ".join(where)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(int(max_items))
            rows = conn.execute(query, tuple(params)).fetchall()
        finally:
            conn.close()

        columns = [
            "recorded_at",
            "source_repo",
            "run_id",
            "packet_id",
            "task_id",
            "signal_kind",
            "decision_status",
            "reason",
            "branch",
            "base_ref",
            "head_ref",
            "source_head",
            "goal_trace",
            "evidence",
            "metadata",
        ]
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(zip(columns, row))
            item["goal_trace"] = _json_loads(item.get("goal_trace"), [])
            item["evidence"] = _json_loads(item.get("evidence"), [])
            item["metadata"] = _json_loads(item.get("metadata"), {})
            result.append(item)
        return result
    except Exception as exc:
        eprint(f"[WARN] experience.query_pr_queue_signals failed: {exc}")
        return []

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXPERIENCE_RECORD_FILENAMES = (
    "EXPERIENCE_UPDATES.jsonl",
    "experience_updates.jsonl",
    "experience_records.jsonl",
    "experience.jsonl",
)


def _relative_run_path(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return records
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            item = dict(payload)
            item.setdefault("record_line", line_no)
            records.append(item)
    return records


def load_run_experience_records(run_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root = Path(run_dir).expanduser()
    for filename in EXPERIENCE_RECORD_FILENAMES:
        for path in sorted(root.rglob(filename)):
            relative_path = _relative_run_path(root, path)
            for item in _load_jsonl_records(path):
                record = dict(item)
                record.setdefault("record_path", relative_path)
                records.append(record)
    return records


from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Sequence

from .config import AGENT_WORK_DIR
from .utils import eprint, now_iso

EXPERIENCE_SCHEMA_VERSION = 1
_LESSONS_TABLE = "lessons"
_MAX_EVIDENCE_POINTERS = 8
_MAX_LESSON_CHARS = 240

_CREATE_SCHEMA_SQL = f"""\
CREATE TABLE IF NOT EXISTS {_LESSONS_TABLE} (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    normalized_trigger TEXT NOT NULL,
    lesson TEXT NOT NULL,
    goal_refs TEXT NOT NULL DEFAULT '[]',
    file_globs TEXT NOT NULL DEFAULT '[]',
    gate TEXT NOT NULL DEFAULT '',
    task_status TEXT NOT NULL DEFAULT '',
    validation_status TEXT NOT NULL DEFAULT '',
    evidence_pointers TEXT NOT NULL DEFAULT '[]',
    confidence REAL NOT NULL DEFAULT 0.50,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_applied_at TEXT DEFAULT '',
    last_applied_run_id TEXT DEFAULT '',
    last_applied_task_id TEXT DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_lessons_kind_trigger
    ON {_LESSONS_TABLE}(kind, normalized_trigger);
"""

_CANONICAL_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bno[\s_-]*tests?[\s_-]*found\b", re.IGNORECASE), "no_tests_found"),
    (re.compile(r"\btests?[\s_-]*skipped\b", re.IGNORECASE), "tests_skipped"),
    (re.compile(r"\bvalidation[\s_-]*pending\b", re.IGNORECASE), "validation_pending"),
    (re.compile(r"\bvalidation[\s_-]*failed\b", re.IGNORECASE), "validation_failed"),
    (re.compile(r"\bblocked[\s_-]*env(?:ironment)?\b", re.IGNORECASE), "blocked_env"),
    (re.compile(r"\breview[\s_-]*required\b", re.IGNORECASE), "review_required"),
    (re.compile(r"\bpr[\s_-]*review\b", re.IGNORECASE), "pr_review"),
)

_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|cron_secret|service_role_key)\b\s*[:=]\s*\S+"
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?i)(ignore\s+previous\s+instructions|system\s+prompt|developer\s+message|assistant:|user:)"
)
_RAW_EXCERPT_RE = re.compile(
    r"(?is)(diff\s+--git|^@@|^\+\+\+\s|^---\s|traceback\s+\(most\s+recent\s+call\s+last\)|begin\s+prompt)"
)


def experience_root(repo: Path) -> Path:
    return Path(repo).expanduser().resolve() / AGENT_WORK_DIR / "experience"


def experience_db_path(repo: Path) -> Path:
    return experience_root(repo) / "experience.db"


def normalize_trigger(*parts: object) -> str:
    tokens: list[str] = []
    for part in parts:
        tokens.extend(_flatten_trigger_tokens(part))
    deduped: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return ":".join(deduped[:12])


def sanitize_lesson_record(repo: Path, lesson: dict[str, Any], *, now: str | None = None) -> dict[str, Any]:
    now_text = str(now or now_iso())
    kind = _normalize_token(lesson.get("kind") or "lesson") or "lesson"
    goal_refs = _normalize_goal_refs(
        lesson.get("goal_refs")
        or lesson.get("goalRefs")
        or lesson.get("applies_to_goal_refs")
        or lesson.get("goal_trace")
        or lesson.get("goalTrace")
    )
    file_globs = _normalize_file_globs(
        repo,
        lesson.get("file_globs")
        or lesson.get("fileGlobs")
        or lesson.get("applies_to_file_globs")
        or lesson.get("files")
        or lesson.get("changed_files")
        or lesson.get("changedFiles"),
    )
    gate = _first_token(
        lesson.get("gate")
        or lesson.get("gates")
        or lesson.get("applies_to_gates")
    )
    task_status = _first_token(
        lesson.get("task_status")
        or lesson.get("taskStatus")
        or lesson.get("applies_to_statuses")
        or lesson.get("status")
    )
    validation_status = _first_token(
        lesson.get("validation_status")
        or lesson.get("validationStatus")
        or lesson.get("applies_to_validation_statuses")
    )
    lesson_text = _sanitize_free_text(lesson.get("lesson") or lesson.get("summary") or "")
    if not lesson_text:
        lesson_text = _default_lesson_text(kind=kind, gate=gate, task_status=task_status, validation_status=validation_status)
    normalized_trigger = _normalize_token(lesson.get("normalized_trigger"))
    if not normalized_trigger:
        normalized_trigger = normalize_trigger(
            lesson.get("trigger"),
            lesson.get("title"),
            lesson.get("task_id"),
            gate,
            task_status,
            validation_status,
            goal_refs,
            file_globs,
        )
    evidence_pointers = _normalize_evidence_pointers(
        repo,
        lesson.get("evidence_pointers")
        or lesson.get("evidencePointers")
        or lesson.get("evidence")
        or [],
    )
    created_at = _clean_timestamp(lesson.get("created_at") or lesson.get("createdAt") or now_text, fallback=now_text)
    updated_at = _clean_timestamp(lesson.get("updated_at") or lesson.get("updatedAt") or now_text, fallback=now_text)
    last_applied = _normalize_last_applied(
        lesson.get("last_applied")
        or lesson.get("lastApplied")
        or {
            "at": lesson.get("last_applied_at") or lesson.get("lastAppliedAt"),
            "run_id": lesson.get("last_applied_run_id") or lesson.get("lastAppliedRunId"),
            "task_id": lesson.get("last_applied_task_id") or lesson.get("lastAppliedTaskId"),
        }
    )
    try:
        confidence = float(lesson.get("confidence") or 0.50)
    except Exception:
        confidence = 0.50
    confidence = max(0.05, min(0.95, confidence))

    return {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "id": _lesson_id(kind, normalized_trigger),
        "kind": kind,
        "normalized_trigger": normalized_trigger or kind,
        "lesson": lesson_text,
        "goal_refs": goal_refs,
        "file_globs": file_globs,
        "gate": gate,
        "task_status": task_status,
        "validation_status": validation_status,
        "evidence_pointers": evidence_pointers,
        "confidence": round(confidence, 2),
        "created_at": created_at,
        "updated_at": updated_at,
        "last_applied": last_applied,
    }


def upsert_lessons(repo: Path, lessons: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    stored: list[dict[str, Any]] = []
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(repo)
        now_text = now_iso()
        for raw in lessons:
            sanitized = sanitize_lesson_record(repo, dict(raw), now=now_text)
            row = conn.execute(
                f"SELECT id, kind, normalized_trigger, lesson, goal_refs, file_globs, gate, task_status, "
                f"validation_status, evidence_pointers, confidence, created_at, updated_at, "
                f"last_applied_at, last_applied_run_id, last_applied_task_id "
                f"FROM {_LESSONS_TABLE} WHERE kind = ? AND normalized_trigger = ?",
                (sanitized["kind"], sanitized["normalized_trigger"]),
            ).fetchone()
            if row:
                existing = _row_to_record(row)
                merged = _merge_lesson_records(existing, sanitized, updated_at=now_text)
                conn.execute(
                    f"UPDATE {_LESSONS_TABLE} SET lesson = ?, goal_refs = ?, file_globs = ?, gate = ?, task_status = ?, "
                    f"validation_status = ?, evidence_pointers = ?, confidence = ?, updated_at = ?, "
                    f"last_applied_at = ?, last_applied_run_id = ?, last_applied_task_id = ? WHERE id = ?",
                    (
                        merged["lesson"],
                        json.dumps(merged["goal_refs"], ensure_ascii=False),
                        json.dumps(merged["file_globs"], ensure_ascii=False),
                        merged["gate"],
                        merged["task_status"],
                        merged["validation_status"],
                        json.dumps(merged["evidence_pointers"], ensure_ascii=False),
                        float(merged["confidence"]),
                        merged["updated_at"],
                        merged["last_applied"]["at"],
                        merged["last_applied"]["run_id"],
                        merged["last_applied"]["task_id"],
                        merged["id"],
                    ),
                )
                stored.append(merged)
                continue

            conn.execute(
                f"INSERT INTO {_LESSONS_TABLE} (id, kind, normalized_trigger, lesson, goal_refs, file_globs, gate, "
                f"task_status, validation_status, evidence_pointers, confidence, created_at, updated_at, "
                f"last_applied_at, last_applied_run_id, last_applied_task_id) "
                f"VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sanitized["id"],
                    sanitized["kind"],
                    sanitized["normalized_trigger"],
                    sanitized["lesson"],
                    json.dumps(sanitized["goal_refs"], ensure_ascii=False),
                    json.dumps(sanitized["file_globs"], ensure_ascii=False),
                    sanitized["gate"],
                    sanitized["task_status"],
                    sanitized["validation_status"],
                    json.dumps(sanitized["evidence_pointers"], ensure_ascii=False),
                    float(sanitized["confidence"]),
                    sanitized["created_at"],
                    sanitized["updated_at"],
                    sanitized["last_applied"]["at"],
                    sanitized["last_applied"]["run_id"],
                    sanitized["last_applied"]["task_id"],
                ),
            )
            stored.append(sanitized)
        conn.commit()
    except Exception as exc:
        eprint(f"[WARN] experience.upsert_lessons failed: {exc}")
        return []
    finally:
        if conn is not None:
            conn.close()
    return stored


def list_lessons(repo: Path) -> list[dict[str, Any]]:
    conn: sqlite3.Connection | None = None
    try:
        conn = _connect(repo)
        rows = conn.execute(
            f"SELECT id, kind, normalized_trigger, lesson, goal_refs, file_globs, gate, task_status, "
            f"validation_status, evidence_pointers, confidence, created_at, updated_at, "
            f"last_applied_at, last_applied_run_id, last_applied_task_id "
            f"FROM {_LESSONS_TABLE} ORDER BY updated_at DESC, created_at DESC"
        ).fetchall()
        return [_row_to_record(row) for row in rows]
    except Exception as exc:
        eprint(f"[WARN] experience.list_lessons failed: {exc}")
        return []
    finally:
        if conn is not None:
            conn.close()


def _connect(repo: Path) -> sqlite3.Connection:
    db_path = experience_db_path(repo)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.executescript(_CREATE_SCHEMA_SQL)
    conn.commit()
    return conn


def _flatten_trigger_tokens(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        tokens: list[str] = []
        for key in ("goal_ref", "goal_id", "goal_refs", "path", "gate", "status", "task_id", "title"):
            if key in value:
                tokens.extend(_flatten_trigger_tokens(value.get(key)))
        return tokens
    if isinstance(value, (list, tuple, set)):
        tokens: list[str] = []
        for item in value:
            tokens.extend(_flatten_trigger_tokens(item))
        return tokens
    token = _normalize_token(value)
    return [token] if token else []


def _normalize_token(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    for pattern, replacement in _CANONICAL_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    text = _SECRET_RE.sub(r"\1=[redacted]", text)
    text = _PROMPT_INJECTION_RE.sub("redacted", text)
    text = _RAW_EXCERPT_RE.sub("redacted", text)
    text = text.lower()
    text = re.sub(r"[^a-z0-9*./:_-]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._:-_")
    return text


def _first_token(value: object) -> str:
    if isinstance(value, (list, tuple, set)):
        for item in value:
            token = _first_token(item)
            if token:
                return token
        return ""
    return _normalize_token(value)


def _sanitize_free_text(value: object) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    text = _SECRET_RE.sub(r"\1=[redacted]", text)
    if _PROMPT_INJECTION_RE.search(text) or _RAW_EXCERPT_RE.search(text):
        return "[redacted]"
    if len(text) > _MAX_LESSON_CHARS:
        text = text[:_MAX_LESSON_CHARS].rstrip() + "..."
    return text


def _normalize_goal_refs(value: object) -> list[str]:
    items: list[str] = []
    for entry in _as_list(value):
        if isinstance(entry, dict):
            text = str(entry.get("goal_ref") or entry.get("goal_id") or entry.get("id") or "").strip()
        else:
            text = str(entry or "").strip()
        if text:
            items.append(text)
    return _unique_sorted(items)


def _normalize_file_globs(repo: Path, value: object) -> list[str]:
    globs: list[str] = []
    for entry in _as_list(value):
        text = _normalize_path_hint(repo, entry)
        if text:
            globs.append(text)
    return _unique_sorted(globs)


def _normalize_evidence_pointers(repo: Path, value: object) -> list[dict[str, Any]]:
    pointers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in _as_list(value):
        pointer = _normalize_evidence_pointer(repo, entry)
        if not pointer:
            continue
        key = json.dumps(pointer, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        pointers.append(pointer)
        if len(pointers) >= _MAX_EVIDENCE_POINTERS:
            break
    return pointers


def _normalize_evidence_pointer(repo: Path, value: object) -> dict[str, Any] | None:
    if isinstance(value, dict):
        pointer: dict[str, Any] = {}
        kind = _normalize_token(value.get("kind") or "artifact") or "artifact"
        pointer["kind"] = kind
        path = _normalize_path_hint(repo, value.get("path") or value.get("artifact_path") or value.get("artifactPath"))
        if path:
            pointer["path"] = path
        for key in ("run_id", "task_id", "packet_id"):
            raw = str(value.get(key) or value.get(_camel_case(key)) or "").strip()
            if raw:
                pointer[key] = raw
        gate = _normalize_token(value.get("gate"))
        status = _normalize_token(value.get("status") or value.get("validation_status") or value.get("validationStatus"))
        label = _sanitize_free_text(value.get("label") or value.get("name") or "")
        if gate:
            pointer["gate"] = gate
        if status:
            pointer["status"] = status
        if label and label != "[redacted]":
            pointer["label"] = label
        if len(pointer) == 1 and "kind" in pointer:
            return None
        return pointer

    path = _normalize_path_hint(repo, value)
    if not path:
        return None
    return {"kind": "artifact", "path": path}


def _normalize_path_hint(repo: Path, value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    path = Path(text)
    if path.is_absolute():
        try:
            text = path.resolve().relative_to(Path(repo).expanduser().resolve()).as_posix()
        except Exception:
            text = path.name
    if text.startswith("./"):
        text = text[2:]
    if not text:
        return ""
    return re.sub(r"/{2,}", "/", text)


def _normalize_last_applied(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {"at": "", "run_id": "", "task_id": ""}
    return {
        "at": _clean_timestamp(value.get("at") or value.get("last_applied_at") or value.get("lastAppliedAt") or "", fallback=""),
        "run_id": str(value.get("run_id") or value.get("runId") or "").strip(),
        "task_id": str(value.get("task_id") or value.get("taskId") or "").strip(),
    }


def _clean_timestamp(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _lesson_id(kind: str, normalized_trigger: str) -> str:
    seed = f"{kind}|{normalized_trigger}".encode("utf-8", errors="ignore")
    return hashlib.sha1(seed).hexdigest()[:16]


def _row_to_record(row: Sequence[object]) -> dict[str, Any]:
    (
        lesson_id,
        kind,
        normalized_trigger,
        lesson_text,
        goal_refs_json,
        file_globs_json,
        gate,
        task_status,
        validation_status,
        evidence_json,
        confidence,
        created_at,
        updated_at,
        last_applied_at,
        last_applied_run_id,
        last_applied_task_id,
    ) = row
    return {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "id": str(lesson_id),
        "kind": str(kind),
        "normalized_trigger": str(normalized_trigger),
        "lesson": str(lesson_text),
        "goal_refs": _json_list(goal_refs_json),
        "file_globs": _json_list(file_globs_json),
        "gate": str(gate or ""),
        "task_status": str(task_status or ""),
        "validation_status": str(validation_status or ""),
        "evidence_pointers": _json_list(evidence_json),
        "confidence": round(float(confidence or 0.50), 2),
        "created_at": str(created_at or ""),
        "updated_at": str(updated_at or ""),
        "last_applied": {
            "at": str(last_applied_at or ""),
            "run_id": str(last_applied_run_id or ""),
            "task_id": str(last_applied_task_id or ""),
        },
    }


def _merge_lesson_records(existing: dict[str, Any], new: dict[str, Any], *, updated_at: str) -> dict[str, Any]:
    merged_goal_refs = _unique_sorted([*existing.get("goal_refs", []), *new.get("goal_refs", [])])
    merged_file_globs = _unique_sorted([*existing.get("file_globs", []), *new.get("file_globs", [])])
    merged_evidence = _merge_evidence(existing.get("evidence_pointers", []), new.get("evidence_pointers", []))
    evidence_growth = max(0, len(merged_evidence) - len(existing.get("evidence_pointers", [])))
    confidence = max(float(existing.get("confidence") or 0.50), float(new.get("confidence") or 0.50))
    confidence = min(0.95, confidence + (0.03 * evidence_growth))
    last_applied = _normalize_last_applied(new.get("last_applied") or existing.get("last_applied") or {})
    return {
        "schema_version": EXPERIENCE_SCHEMA_VERSION,
        "id": str(existing.get("id") or new.get("id") or ""),
        "kind": str(existing.get("kind") or new.get("kind") or "lesson"),
        "normalized_trigger": str(existing.get("normalized_trigger") or new.get("normalized_trigger") or ""),
        "lesson": str(new.get("lesson") or existing.get("lesson") or ""),
        "goal_refs": merged_goal_refs,
        "file_globs": merged_file_globs,
        "gate": str(new.get("gate") or existing.get("gate") or ""),
        "task_status": str(new.get("task_status") or existing.get("task_status") or ""),
        "validation_status": str(new.get("validation_status") or existing.get("validation_status") or ""),
        "evidence_pointers": merged_evidence,
        "confidence": round(confidence, 2),
        "created_at": str(existing.get("created_at") or new.get("created_at") or updated_at),
        "updated_at": str(updated_at),
        "last_applied": last_applied,
    }


def _merge_evidence(existing: object, new: object) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for collection in (new, existing):
        for entry in _as_list(collection):
            if not isinstance(entry, dict):
                continue
            key = json.dumps(entry, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(entry))
            if len(merged) >= _MAX_EVIDENCE_POINTERS:
                return merged
    return merged


def _default_lesson_text(*, kind: str, gate: str, task_status: str, validation_status: str) -> str:
    if kind == "merge":
        return "Preserve GOALS-linked PR decisions with validation evidence pointers."
    if validation_status in {"no_tests_found", "tests_skipped", "validation_pending"}:
        return "Keep validation-gap work in a non-passed state until the required gate evidence exists."
    if kind == "env":
        return "Record blocked environment evidence before retrying the same task shape."
    gate_text = gate or "validation"
    status_text = task_status or validation_status or "failed"
    return f"Preserve {gate_text} evidence for repeated {status_text} work before retrying."


def _json_list(value: object) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except Exception:
        return []
    return parsed if isinstance(parsed, list) else []


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _unique_sorted(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return sorted(out)


def _camel_case(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.capitalize() for part in tail)

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any

_RAW_LOG_NAMES = {
    "build.txt",
    "dev_output.txt",
    "error.log",
    "metrics.jsonl",
    "run.log",
    "test.txt",
    "validation.txt",
}
_RAW_DIFF_SUFFIXES = {".diff", ".patch"}
_RAW_DIFF_NAMES = {"patch.diff"}
_PROMPT_NAMES = {"dev_task_prompt.md", "pm_bootstrap_prompt.md", "pm_incremental_prompt.md"}
_TRANSCRIPT_NAMES = {"telegram_runner_subprocess.log"}
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|xoxb)-[A-Za-z0-9._-]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ExperienceSummaryConfig:
    max_items: int = 12
    max_chars: int = 4000
    lesson_max_chars: int = 240
    evidence_max_items: int = 3


@dataclass(frozen=True)
class ExperienceRenderContext:
    goal_refs: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    validation_gates: tuple[str, ...] = ()
    task_statuses: tuple[str, ...] = ()
    validation_statuses: tuple[str, ...] = ()

    @classmethod
    def from_values(
        cls,
        *,
        goal_refs: Iterable[str] = (),
        changed_files: Iterable[str] = (),
        validation_gates: Iterable[str] = (),
        task_statuses: Iterable[str] = (),
        validation_statuses: Iterable[str] = (),
    ) -> ExperienceRenderContext:
        return cls(
            goal_refs=_normalize_string_tuple(goal_refs),
            changed_files=_normalize_path_tuple(changed_files),
            validation_gates=_normalize_string_tuple(validation_gates),
            task_statuses=_normalize_string_tuple(task_statuses),
            validation_statuses=_normalize_string_tuple(validation_statuses),
        )


@dataclass(frozen=True)
class _PreparedLesson:
    score: float
    line: str


def render_experience_summary(
    lessons: Iterable[Mapping[str, Any]],
    *,
    config: ExperienceSummaryConfig | None = None,
    context: ExperienceRenderContext | None = None,
) -> str:
    cfg = config or ExperienceSummaryConfig()
    if cfg.max_items <= 0 or cfg.max_chars <= 0:
        return ""

    render_context = context or ExperienceRenderContext()
    prepared = _prepare_lessons(lessons, config=cfg, context=render_context)
    if not prepared:
        return ""

    selected: list[str] = []
    for lesson in prepared:
        if len(selected) >= cfg.max_items:
            break
        candidate = [*selected, lesson.line]
        omitted = max(0, len(prepared) - len(candidate))
        block = _format_summary_block(candidate, omitted=omitted, config=cfg)
        if len(block) <= cfg.max_chars:
            selected = candidate
        else:
            break

    omitted = max(0, len(prepared) - len(selected))
    block = _format_summary_block(selected, omitted=omitted, config=cfg)
    while selected and len(block) > cfg.max_chars:
        selected.pop()
        omitted = max(0, len(prepared) - len(selected))
        block = _format_summary_block(selected, omitted=omitted, config=cfg)

    return block if len(block) <= cfg.max_chars else ""


def _prepare_lessons(
    lessons: Iterable[Mapping[str, Any]],
    *,
    config: ExperienceSummaryConfig,
    context: ExperienceRenderContext,
) -> list[_PreparedLesson]:
    prepared: list[_PreparedLesson] = []
    for lesson in lessons:
        summary = _sanitize_summary(_first_text(lesson, "summary", "lesson", "text"), max_chars=config.lesson_max_chars)
        if not summary:
            continue
        evidence = _normalize_evidence_list(lesson, max_items=config.evidence_max_items)
        evidence_count = len(evidence) or _coerce_int(lesson.get("evidence_count")) or _coerce_len(lesson.get("evidence"))
        line = _render_lesson_line(
            kind=_first_text(lesson, "kind", default="general") or "general",
            summary=summary,
            confidence=_coerce_float(lesson.get("confidence")),
            evidence=evidence,
            evidence_count=evidence_count,
        )
        prepared.append(
            _PreparedLesson(
                score=_lesson_score(lesson, context=context, evidence_count=evidence_count),
                line=line,
            )
        )
    prepared.sort(key=lambda item: item.score, reverse=True)
    return prepared


def _format_summary_block(lines: list[str], *, omitted: int, config: ExperienceSummaryConfig) -> str:
    attrs = (
        f'version="1" items="{len(lines)}" omitted="{max(0, omitted)}" '
        f'max_items="{config.max_items}" max_chars="{config.max_chars}" authority="advisory"'
    )
    if lines:
        body = "\n".join(lines)
        return f"<pm_experience_summary {attrs}>\n{body}\n</pm_experience_summary>"
    return f"<pm_experience_summary {attrs}>\n</pm_experience_summary>"


def _render_lesson_line(
    *,
    kind: str,
    summary: str,
    confidence: float | None,
    evidence: list[str],
    evidence_count: int,
) -> str:
    parts = [kind.lower()]
    if confidence is not None:
        parts.append(_confidence_band(confidence))
        parts.append(f"conf={confidence:.2f}")
    if evidence_count > 0:
        parts.append(f"evidence={evidence_count}")
    line = f"- [{' '.join(parts)}] {summary}"
    if evidence:
        line += " | evidence: " + "; ".join(evidence)
    return line


def _lesson_score(
    lesson: Mapping[str, Any],
    *,
    context: ExperienceRenderContext,
    evidence_count: int,
) -> float:
    explicit = _coerce_float(lesson.get("relevance_score"))
    if explicit is None:
        explicit = _coerce_float(lesson.get("score"))
    if explicit is not None:
        return explicit

    score = 0.0
    goal_refs = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_goal_refs"))))
    file_globs = _normalize_path_tuple(_coerce_iterable(lesson.get("applies_to_file_globs")))
    gates = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_gates"))))
    statuses = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_statuses"))))
    validation_statuses = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_validation_statuses"))))

    if goal_refs and goal_refs.intersection(context.goal_refs):
        score += 5.0
    if file_globs and any(fnmatch(changed_file, pattern) for pattern in file_globs for changed_file in context.changed_files):
        score += 4.0
    if gates and gates.intersection(context.validation_gates):
        score += 3.0
    if validation_statuses and validation_statuses.intersection(context.validation_statuses):
        score += 3.0
    if statuses and statuses.intersection(context.task_statuses):
        score += 2.0

    confidence = _coerce_float(lesson.get("confidence"))
    if confidence is not None:
        score += max(0.0, min(confidence, 1.0)) * 2.0

    if evidence_count > 0:
        score += math.log1p(evidence_count)
    return score


def _normalize_evidence_list(lesson: Mapping[str, Any], *, max_items: int) -> list[str]:
    if max_items <= 0:
        return []

    evidence = lesson.get("evidence_pointers", lesson.get("evidence"))
    if evidence is None:
        return []

    pointers: list[str] = []
    for item in _coerce_iterable(evidence):
        pointer = _normalize_evidence_pointer(item)
        if not pointer or pointer in pointers:
            continue
        pointers.append(pointer)
        if len(pointers) >= max_items:
            break
    return pointers


def _normalize_evidence_pointer(value: Any) -> str:
    if isinstance(value, Mapping):
        text = _first_value_text(value, "path", "pointer", "artifact_path", "artifactPath", "file", "label")
        kind_hint = _first_value_text(value, "kind", "type", "label")
    else:
        text = str(value or "").strip()
        kind_hint = ""
    if not text:
        return ""

    normalized = text.replace("\\", "/").strip().strip("`")
    line_suffix = ""
    match = re.match(r"^(.*?)(:\d+)?$", normalized)
    if match:
        normalized = match.group(1) or normalized
        line_suffix = match.group(2) or ""

    segments = [segment for segment in normalized.split("/") if segment not in {"", "."}]
    if not segments:
        return ""

    prefix = "repo"
    if "agent_runs" in segments:
        start = segments.index("agent_runs")
        segments = segments[start + 1 :]
        prefix = "run"
    elif re.match(r"^[A-Za-z]:$", segments[0]) or normalized.startswith("/"):
        prefix = "path"
        segments = segments[-4:]

    if not segments:
        return ""

    artifact_token = _artifact_placeholder(segments[-1], kind_hint)
    if artifact_token:
        segments[-1] = artifact_token
    elif prefix == "path" and len(segments) > 3:
        segments = ["..."] + segments[-3:]

    pointer = f"{prefix}:{'/'.join(segments)}{line_suffix}"
    return _truncate_middle(pointer, 96)


def _artifact_placeholder(file_name: str, kind_hint: str) -> str:
    name = file_name.strip().lower()
    kind = (kind_hint or "").strip().lower()
    if name in _RAW_LOG_NAMES or "log" in kind:
        return "[log]"
    if any(name.endswith(suffix) for suffix in _RAW_DIFF_SUFFIXES) or name in _RAW_DIFF_NAMES or "diff" in kind or "patch" in kind:
        return "[diff]"
    if name in _PROMPT_NAMES or "prompt" in kind:
        return "[prompt]"
    if name in _TRANSCRIPT_NAMES or "transcript" in kind:
        return "[transcript]"
    return ""


def _sanitize_summary(text: str, *, max_chars: int) -> str:
    raw = str(text or "").strip()
    if not raw or _is_excluded_summary(raw):
        return ""
    sanitized = _redact_secret_tokens(raw)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" -")
    if not sanitized:
        return ""
    return _truncate_text(sanitized, max_chars)


def _is_excluded_summary(text: str) -> bool:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    lowered = text.lower()

    if "```" in text:
        return True
    if "diff --git " in lowered or "\n@@ " in text or "\n+++ " in text or "\n--- " in text:
        return True
    if "traceback (most recent call last)" in lowered:
        return True
    if "ignore previous instructions" in lowered:
        return True
    if lowered.startswith("you are ") and (
        "implementation instructions:" in lowered
        or "when editing files" in lowered
        or "selected skills" in lowered
    ):
        return True
    if len(lines) >= 8:
        return True

    transcript_markers = sum(1 for line in lines if re.match(r"^(assistant|user|system|tool):", line.strip(), re.IGNORECASE))
    if transcript_markers >= 2:
        return True

    shell_like_lines = sum(
        1
        for line in lines
        if re.match(r"^(PS>|>>>|\$|INFO\b|WARN\b|ERROR\b|\d{4}-\d{2}-\d{2}[ T])", line.strip(), re.IGNORECASE)
    )
    if len(lines) >= 4 and shell_like_lines >= max(2, len(lines) // 2):
        return True

    return False


def _redact_secret_tokens(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _truncate_middle(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 5:
        return text[:max_chars]
    keep = (max_chars - 3) // 2
    tail = max_chars - 3 - keep
    return text[:keep] + "..." + text[-tail:]


def _confidence_band(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"


def _first_text(source: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _first_value_text(source: Mapping[str, Any], *keys: str) -> str:
    return _first_text(source, *keys, default="")


def _coerce_iterable(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _normalize_string_tuple(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if text:
            normalized.append(text)
    return tuple(normalized)


def _normalize_path_tuple(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip().replace("\\", "/")
        if not text:
            continue
        normalized.append(text.lstrip("./").lower())
    return tuple(normalized)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_len(value: Any) -> int:
    items = _coerce_iterable(value)
    return len(items)
