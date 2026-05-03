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
