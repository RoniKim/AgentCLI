from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any


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


def _pick_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


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


def _normalize_log_tail_level(value: Any) -> str:
    level = str(value or "").strip().lower()
    if level == "warning":
        return "warn"
    if level == "error":
        return "err"
    return level


def _log_tail_source_catalog(run_dir: Path | None) -> list[dict[str, Any]]:
    if run_dir is None:
        return []
    sources: list[dict[str, Any]] = []

    def add(source_id: str, relative_path: str, label: str, *, kind: str = "log") -> None:
        source_path = run_dir / relative_path
        exists = False
        try:
            exists = source_path.exists() and source_path.is_file()
        except OSError:
            exists = False
        sources.append(
            {
                "id": source_id,
                "label": label,
                "name": source_path.name,
                "path": source_path.as_posix(),
                "exists": exists,
                "available": exists,
                "kind": kind,
                "selected": False,
                "unavailable_reason": "" if exists else "missing",
            }
        )

    add("run_log", "logs/run.log", "run.log")
    add("error_log", "logs/error.log", "error.log")
    add("events_jsonl", "logs/events.jsonl", "events.jsonl")
    add("cycle_summary", "cycle_summary.log", "cycle_summary.log")
    add("backend_transcript", "telegram_runner_subprocess.log", "backend transcript", kind="transcript")
    return sources


def _resolve_log_tail_source_record(run_dir: Path | None, source_id: str = "") -> dict[str, Any] | None:
    catalog = _log_tail_source_catalog(run_dir)
    if not catalog:
        return None
    normalized_source_id = str(source_id or "").strip()
    if normalized_source_id:
        for source in catalog:
            if str(source.get("id") or "").strip() == normalized_source_id:
                return source
    for source in catalog:
        if bool(source.get("available")):
            return source
    return catalog[0]


def _resolve_log_tail_source(run_dir: Path | None, source_id: str = "") -> Path | None:
    source = _resolve_log_tail_source_record(run_dir, source_id)
    if not source:
        return None
    source_path = str(source.get("path") or "").strip()
    return Path(source_path) if source_path else None


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
    source: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
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

    source_payload = deepcopy(source) if isinstance(source, dict) else {}
    source_payload["path"] = source_file.as_posix()
    source_payload["name"] = source_file.name
    source_payload["exists"] = False
    source_payload["available"] = False
    source_payload["selected"] = True
    if not source_payload.get("label"):
        source_payload["label"] = source_file.name
    if source_payload.get("kind") is None:
        source_payload["kind"] = "log"

    source_catalog: list[dict[str, Any]] = []
    if isinstance(sources, list):
        for item in sources:
            if not isinstance(item, dict):
                continue
            item_copy = deepcopy(item)
            item_copy["selected"] = bool(str(item_copy.get("id") or "").strip() == str(source_payload.get("id") or "").strip())
            source_catalog.append(item_copy)
    if not source_catalog and source_payload.get("id"):
        source_catalog.append(deepcopy(source_payload))

    try:
        source_exists = source_file.exists() and source_file.is_file()
        source_payload["exists"] = source_exists
        source_payload["available"] = source_exists
        source_payload["unavailable_reason"] = "" if source_exists else str(source_payload.get("unavailable_reason") or "missing").strip() or "missing"
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
            "source": source_payload,
            "source_id": str(source_payload.get("id") or ""),
            "selected_source_id": str(source_payload.get("id") or ""),
            "sources": source_catalog,
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
            "source": source_payload,
            "source_id": str(source_payload.get("id") or ""),
            "selected_source_id": str(source_payload.get("id") or ""),
            "sources": source_catalog,
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
        "source": source_payload,
        "source_id": str(source_payload.get("id") or ""),
        "selected_source_id": str(source_payload.get("id") or ""),
        "sources": source_catalog,
        "cursor": start_cursor if cursor_mode else None,
        "max_lines": max_lines,
        "malformed_lines": malformed_count,
    }


__all__ = [
    "_build_log_tail_payload",
    "_log_tail_entry_matches",
    "_log_tail_entry_search_text",
    "_log_tail_source_catalog",
    "_normalize_log_tail_level",
    "_normalize_plain_log_tail_entry",
    "_normalize_structured_log_tail_entry",
    "_parse_log_tail_entry",
    "_resolve_log_tail_source",
    "_resolve_log_tail_source_record",
]
