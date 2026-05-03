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
