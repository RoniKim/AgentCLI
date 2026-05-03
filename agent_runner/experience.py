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
