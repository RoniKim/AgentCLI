from __future__ import annotations

import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple


from .config import AGENT_WORK_DIR, ensure_work_dir

LAST_TODO_POINTER = "LAST_TODO.txt"
TODO_PREVIEW_LINES_DEFAULT = 40
TODO_PREVIEW_MAX_CHARS_DEFAULT = 4000
TODO_CONTENT_MAX_CHARS = 12000
TODO_FRESH_HOURS_DEFAULT = 24


def _sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _repo_relative(repo: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _file_mtime_utc(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        return None


def todo_dir(repo: Path) -> Path:
    """Todo storage directory under the *target repo*.

    Stored in the repo's .AgentCLI tree so:
    - They are colocated with agent runs
    - They survive AgentCLI restarts
    """
    return repo / AGENT_WORK_DIR / "todo"


def last_pointer_path(repo: Path) -> Path:
    return todo_dir(repo) / LAST_TODO_POINTER


def _today_key(repo: Path) -> str:
    # Keep stable per-day per-repo without requiring git.
    day = datetime.now().strftime("%Y-%m-%d")
    base = f"{repo.as_posix()}|{day}"
    return _sha1_hex(base)[:10]


def today_todo_path(repo: Path) -> Path:
    return todo_dir(repo) / f"Today_{_today_key(repo)}.md"


DEFAULT_TODO_TEMPLATE = """# TODO (Today)

- created_at: {created_at}
- repo: {repo}

## Priorities

- [ ] (write the most important goal)

## Tasks

- [ ] 
- [ ] 

## Notes

- 
"""


def ensure_todo_file(repo: Path) -> Path:
    """Create today's todo file if missing, and update LAST_TODO pointer."""
    ensure_work_dir(repo)
    td = todo_dir(repo)
    td.mkdir(parents=True, exist_ok=True)

    p = today_todo_path(repo)
    if not p.exists():
        p.write_text(
            DEFAULT_TODO_TEMPLATE.format(created_at=datetime.now().isoformat(timespec="seconds"), repo=str(repo)),
            encoding="utf-8",
            errors="replace",
        )

    set_current_todo(repo, p)
    return p


def set_current_todo(repo: Path, todo_path: Path) -> None:
    try:
        last_pointer_path(repo).parent.mkdir(parents=True, exist_ok=True)
        rel = todo_path.relative_to(repo).as_posix() if repo in todo_path.parents else todo_path.as_posix()
        last_pointer_path(repo).write_text(rel + "\n", encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_current_todo_path(repo: Path) -> Optional[Path]:
    """Return currently selected todo path.

    Selection order:
    1) .AgentCLI/todo/LAST_TODO.txt
    2) latest modified *.md under .AgentCLI/todo
    """
    repo_root = Path(repo).expanduser().resolve()
    td = todo_dir(repo_root).resolve()
    try:
        ptr = last_pointer_path(repo_root)
        if ptr.exists():
            rel = (ptr.read_text(encoding="utf-8", errors="replace").strip() or "").strip()
            if rel:
                p = (repo_root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
                if p.exists() and p.is_file() and _path_is_within(p, td):
                    return p
    except Exception:
        pass

    if not td.exists():
        return None
    mds = sorted(td.glob("*.md"), key=lambda x: x.stat().st_mtime)
    return mds[-1] if mds else None


def read_current_todo(repo: Path, max_chars: int = 12000) -> Tuple[Optional[Path], Optional[str]]:
    p = get_current_todo_path(repo)
    if not p:
        return None, None
    try:
        txt = p.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return p, None

    if max_chars and len(txt) > max_chars:
        txt = txt[:max_chars] + "\n\n...(truncated)"
    return p, txt


def _todo_source(repo: Path, path: Path | None) -> str:
    if path is None:
        return "none"
    try:
        ptr = last_pointer_path(repo)
        if ptr.exists():
            raw = ptr.read_text(encoding="utf-8", errors="replace").strip()
            if raw:
                selected = (repo / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
                if selected == path.resolve():
                    return "pointer"
    except Exception:
        pass
    return "latest"


def _todo_preview(text: str, *, max_lines: int, max_chars: int) -> tuple[str, list[str], bool]:
    lines = text.splitlines()
    bounded_lines = lines[: max(0, int(max_lines))]
    preview = "\n".join(bounded_lines)
    truncated = len(lines) > len(bounded_lines)
    if max_chars and len(preview) > max_chars:
        preview = preview[:max_chars].rstrip() + "\n...(truncated)"
        truncated = True
        bounded_lines = preview.splitlines()
    return preview, bounded_lines, truncated


def build_todo_status(
    repo: Path,
    *,
    include_preview: bool = False,
    preview_lines: int = TODO_PREVIEW_LINES_DEFAULT,
    preview_max_chars: int = TODO_PREVIEW_MAX_CHARS_DEFAULT,
    freshness_hours: int = TODO_FRESH_HOURS_DEFAULT,
) -> dict[str, Any]:
    """Return compact TODO status safe for shell and web status payloads."""
    repo_root = Path(repo).expanduser().resolve()
    td = todo_dir(repo_root)
    pointer = last_pointer_path(repo_root)
    path, text = read_current_todo(repo_root, max_chars=TODO_CONTENT_MAX_CHARS)
    source = _todo_source(repo_root, path)
    exists = bool(path and path.exists() and path.is_file())
    read_error = bool(path and exists and text is None)
    text_value = text or ""
    stripped = text_value.strip()
    has_content = bool(stripped)
    mtime = _file_mtime_utc(path) if path else None
    age_seconds = int((_now_utc() - mtime).total_seconds()) if mtime is not None else None
    fresh_limit = max(0, int(freshness_hours)) * 3600
    if not path:
        state = "missing"
        freshness = "missing"
        message = "No TODO file is selected."
    elif read_error:
        state = "error"
        freshness = "unknown"
        message = "The active TODO file could not be read."
    elif not has_content:
        state = "empty"
        freshness = "empty"
        message = "The active TODO file is empty."
    else:
        state = "ready"
        freshness = "fresh" if age_seconds is not None and (fresh_limit <= 0 or age_seconds <= fresh_limit) else "stale"
        message = "TODO is ready for PM context injection." if freshness == "fresh" else "TODO is present but stale."

    preview_text = ""
    preview_line_values: list[str] = []
    preview_truncated = False
    if include_preview and text_value:
        preview_text, preview_line_values, preview_truncated = _todo_preview(
            text_value,
            max_lines=preview_lines,
            max_chars=preview_max_chars,
        )

    line_count = len(text_value.splitlines()) if text_value else 0
    byte_count = 0
    try:
        byte_count = int(path.stat().st_size) if path else 0
    except Exception:
        byte_count = 0
    injected_lines = min(120, line_count) if has_content else 0
    injection_enabled = has_content and not read_error
    pm_injection = {
        "enabled": injection_enabled,
        "state": "ready" if injection_enabled else state,
        "template_field": "todo_block",
        "templateField": "todo_block",
        "max_chars": TODO_CONTENT_MAX_CHARS,
        "maxChars": TODO_CONTENT_MAX_CHARS,
        "max_lines": 120,
        "maxLines": 120,
        "injected_lines": injected_lines,
        "injectedLines": injected_lines,
        "priority_policy": "goals_first",
        "priorityPolicy": "goals_first",
        "does_not_override_goals": True,
        "doesNotOverrideGoals": True,
        "summary": "TODO ranks and enriches backlog work only inside unmet GOALS constraints.",
    }
    controls = {
        "preview": {
            "enabled": exists,
            "endpoint": "/api/todo",
            "method": "GET",
            "max_lines": preview_lines,
            "maxLines": preview_lines,
        },
        "edit": {
            "enabled": True,
            "endpoint": "/api/todo/save",
            "method": "POST",
            "requires_opt_in": True,
            "requiresOptIn": True,
            "max_chars": TODO_CONTENT_MAX_CHARS,
            "maxChars": TODO_CONTENT_MAX_CHARS,
        },
    }
    return {
        "ok": state != "error",
        "available": exists,
        "state": state,
        "message": message,
        "dir": td.as_posix(),
        "todo_dir": td.as_posix(),
        "todoDir": td.as_posix(),
        "pointer_path": pointer.as_posix(),
        "pointerPath": pointer.as_posix(),
        "active_path": path.as_posix() if path else "",
        "activePath": path.as_posix() if path else "",
        "active_relative_path": _repo_relative(repo_root, path),
        "activeRelativePath": _repo_relative(repo_root, path),
        "source": source,
        "exists": exists,
        "has_content": has_content,
        "hasContent": has_content,
        "freshness": freshness,
        "freshness_hours": freshness_hours,
        "freshnessHours": freshness_hours,
        "age_seconds": age_seconds,
        "ageSeconds": age_seconds,
        "mtime": mtime.isoformat() if mtime is not None else "",
        "updated_at": mtime.isoformat() if mtime is not None else "",
        "updatedAt": mtime.isoformat() if mtime is not None else "",
        "bytes": byte_count,
        "line_count": line_count,
        "lineCount": line_count,
        "content_chars": len(text_value),
        "contentChars": len(text_value),
        "preview": {
            "included": bool(include_preview),
            "text": preview_text,
            "lines": preview_line_values,
            "truncated": preview_truncated,
            "max_lines": preview_lines,
            "maxLines": preview_lines,
            "max_chars": preview_max_chars,
            "maxChars": preview_max_chars,
        },
        "pm_injection": pm_injection,
        "pmInjection": pm_injection,
        "controls": controls,
    }


def _resolve_web_todo_edit_path(repo: Path) -> Path:
    repo_root = Path(repo).expanduser().resolve()
    td = todo_dir(repo_root).resolve()
    try:
        ptr = last_pointer_path(repo_root)
        if ptr.exists():
            rel = (ptr.read_text(encoding="utf-8", errors="replace").strip() or "").strip()
            if rel:
                selected = (repo_root / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
                if not _path_is_within(selected, td):
                    raise ValueError("Active TODO must stay inside .AgentCLI/todo for web edits.")
    except ValueError:
        raise
    except Exception:
        pass
    current = get_current_todo_path(repo_root)
    if current is None:
        return ensure_todo_file(repo_root)
    resolved = current.expanduser().resolve()
    if not _path_is_within(resolved, td):
        raise ValueError("Active TODO must stay inside .AgentCLI/todo for web edits.")
    return resolved


def save_current_todo_text(
    repo: Path,
    content: str,
    *,
    create_if_missing: bool = True,
    backup: bool = True,
    max_chars: int = TODO_CONTENT_MAX_CHARS,
) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    text = str(content or "")
    if len(text) > max_chars:
        raise ValueError(f"TODO content exceeds {max_chars} characters.")
    path = _resolve_web_todo_edit_path(repo_root) if create_if_missing else get_current_todo_path(repo_root)
    if path is None:
        raise FileNotFoundError("No active TODO file is selected.")
    path = path.expanduser().resolve()
    td = todo_dir(repo_root).resolve()
    if not _path_is_within(path, td):
        raise ValueError("TODO edits must stay inside .AgentCLI/todo.")
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if backup and path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = path.with_name(f"{path.stem}.{stamp}.bak{path.suffix}")
        backup_path.write_text(path.read_text(encoding="utf-8", errors="replace"), encoding="utf-8", errors="replace")
    path.write_text(text, encoding="utf-8", errors="replace")
    set_current_todo(repo_root, path)
    status = build_todo_status(repo_root, include_preview=True)
    return {
        "ok": True,
        "path": path.as_posix(),
        "active_path": path.as_posix(),
        "activePath": path.as_posix(),
        "backup_path": backup_path.as_posix() if backup_path else "",
        "backupPath": backup_path.as_posix() if backup_path else "",
        "todo": status,
    }


def open_path(p: Path) -> bool:
    """Best-effort open a file with the OS default app.

    This is used only from the interactive shell.
    """
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
            return True
        # macOS
        if subprocess.call(["which", "open"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            subprocess.Popen(["open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        # Linux
        if subprocess.call(["which", "xdg-open"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        return False
    return False


def format_todo_block(todo_path: Optional[Path], todo_text: Optional[str]) -> str:
    if not todo_path or not todo_text:
        return "(none)"
    # Keep it compact for token-saving.
    lines = todo_text.strip().splitlines()
    head = lines[:120]
    return (
        f"# TODO SOURCE: {todo_path.as_posix()}\n"
        "# TODO POLICY: Rank or enrich work inside unmet GOALS constraints; do not override GOALS-first gating.\n"
        + "\n".join(head)
    )
