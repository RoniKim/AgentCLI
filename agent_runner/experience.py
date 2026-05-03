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
