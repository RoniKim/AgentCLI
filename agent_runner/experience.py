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
