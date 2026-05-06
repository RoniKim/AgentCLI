from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from .utils import now_iso


WEB_ACTION_AUDIT_FILE = "WEB_ACTION_AUDIT.jsonl"
WEB_ACTION_AUDIT_SCHEMA_VERSION = 1
WEB_ACTION_AUDIT_ACTOR = "local_web"

_AUDIT_LOCK = threading.Lock()
_MAX_STRING_LENGTH = 500
_MAX_COLLECTION_ITEMS = 80
_MAX_DEPTH = 6

_RAW_CONTENT_KEYS = {
    "body",
    "config",
    "content",
    "data",
    "diff",
    "draft",
    "log",
    "logs",
    "commandoutput",
    "description",
    "erroroutput",
    "excerpt",
    "note",
    "output",
    "patch",
    "preview",
    "prompt",
    "prompts",
    "raw",
    "rawoutput",
    "rawtext",
    "requestbody",
    "snapshot",
    "stderr",
    "stdout",
    "text",
    "traceback",
    "transcript",
    "value",
    "values",
}
_SENSITIVE_KEY_MARKERS = {
    "apikey",
    "authorization",
    "bottoken",
    "cookie",
    "pairingcode",
    "password",
    "secret",
    "token",
}


def _normalize_key(key: Any) -> str:
    return "".join(ch for ch in str(key or "").strip().lower() if ch.isalnum())


def _path_text(value: Path | str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().resolve().as_posix()
    except Exception:
        return raw.replace("\\", "/")


def _path_is_within(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def web_action_audit_path(repo: Path, run_dir: Path | str | None = None) -> Path:
    repo_root = Path(repo).expanduser().resolve()
    if run_dir not in (None, "", False):
        try:
            candidate = Path(str(run_dir)).expanduser().resolve()
            runs_root = (repo_root / ".AgentCLI" / "agent_runs").resolve()
            if _path_is_within(candidate, runs_root):
                return candidate / WEB_ACTION_AUDIT_FILE
        except Exception:
            pass
    return repo_root / ".AgentCLI" / WEB_ACTION_AUDIT_FILE


def _key_is_sensitive(key: Any) -> bool:
    normalized = _normalize_key(key)
    if not normalized:
        return False
    if normalized in _RAW_CONTENT_KEYS:
        return True
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return _path_text(value)
    text = str(value)
    if len(text) > _MAX_STRING_LENGTH:
        return text[:_MAX_STRING_LENGTH] + "...[truncated]"
    return text


def sanitize_web_action_audit_value(value: Any, *, key: Any = "", depth: int = 0) -> Any:
    if _key_is_sensitive(key):
        return "[redacted]"
    if depth >= _MAX_DEPTH:
        return "[truncated]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for item_key, item_value in list(value.items())[:_MAX_COLLECTION_ITEMS]:
            sanitized[str(item_key)] = sanitize_web_action_audit_value(
                item_value,
                key=item_key,
                depth=depth + 1,
            )
        if len(value) > _MAX_COLLECTION_ITEMS:
            sanitized["truncated_item_count"] = len(value) - _MAX_COLLECTION_ITEMS
        return sanitized
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        sanitized_items = [
            sanitize_web_action_audit_value(item, key=key, depth=depth + 1)
            for item in items[:_MAX_COLLECTION_ITEMS]
        ]
        if len(items) > _MAX_COLLECTION_ITEMS:
            sanitized_items.append({"truncated_item_count": len(items) - _MAX_COLLECTION_ITEMS})
        return sanitized_items
    return _safe_scalar(value)


def record_web_action_audit(
    repo: Path,
    *,
    action: str,
    status: str,
    ok: bool,
    route: str = "",
    method: str = "POST",
    message: str = "",
    error_code: str = "",
    details: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    run_dir: Path | str | None = None,
    actor: str = WEB_ACTION_AUDIT_ACTOR,
) -> dict[str, Any]:
    path = web_action_audit_path(repo, run_dir)
    record = {
        "schema_version": WEB_ACTION_AUDIT_SCHEMA_VERSION,
        "kind": "web_action_audit",
        "timestamp": now_iso(),
        "timestamp_epoch": time.time(),
        "actor": str(actor or WEB_ACTION_AUDIT_ACTOR),
        "source": "web",
        "action": str(action or "").strip(),
        "route": str(route or "").strip(),
        "method": str(method or "POST").strip().upper(),
        "status": str(status or "").strip().lower(),
        "ok": bool(ok),
        "message": sanitize_web_action_audit_value(message, key="message"),
        "error_code": str(error_code or "").strip(),
        "repo": _path_text(repo),
        "run_dir": _path_text(run_dir) if run_dir not in (None, "", False) else "",
        "details": sanitize_web_action_audit_value(details or {}, key="details"),
        "result": sanitize_web_action_audit_value(result or {}, key="result"),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK:
            with path.open("a", encoding="utf-8", errors="replace") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        written = dict(record)
        written["audit_path"] = path.as_posix()
        written["auditPath"] = path.as_posix()
        return written
    except Exception as ex:
        return {
            "ok": False,
            "audit_path": path.as_posix(),
            "auditPath": path.as_posix(),
            "error": str(ex),
        }
