from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Mapping

from .utils import now_iso, atomic_write_json, atomic_write_text, safe_write_text, eprint


_TASK_PRIORITY_ALIASES = {
    "p0": "P0",
    "0": "P0",
    "high": "P0",
    "critical": "P0",
    "urgent": "P0",
    "p1": "P1",
    "1": "P1",
    "medium": "P1",
    "normal": "P1",
    "default": "P1",
    "p2": "P2",
    "2": "P2",
    "low": "P2",
    "p3": "P3",
    "3": "P3",
}

_TASK_EFFORT_ALIASES = {
    "s": "S",
    "small": "S",
    "xs": "S",
    "m": "M",
    "medium": "M",
    "med": "M",
    "l": "L",
    "large": "L",
    "xl": "L",
}

_TASK_P0_HINTS = (
    "bug",
    "fix",
    "crash",
    "block",
    "security",
    "regression",
    "failure",
    "urgent",
    "hotfix",
)
_TASK_TEST_HINTS = ("test", "qa", "verify", "validation", "smoke", "playwright")
_TASK_FRONTEND_HINTS = ("web", "ui", "blazor", "razor", "xaml", "component", "page", "screen")
_TASK_BACKEND_HINTS = ("agent_runner", "runner", "backlog", "pipeline", "runtime", "state", "scheduler")
_TASK_DOC_HINTS = ("docs", "doc", "readme", "notes")
_TASK_LARGE_HINTS = (
    "refactor",
    "overhaul",
    "scheduler",
    "pipeline",
    "runtime",
    "migration",
    "cross-cutting",
    "end-to-end",
    "multi-step",
    "large",
)
_TASK_CORE_PATH_HINTS = (
    "agent_runner/cycle.py",
    "agent_runner/web.py",
    "agent_runner/pipeline/shared_runtime.py",
    "agent_runner/backends/claudecode.py",
    "web_console/app.js",
)


def _ordered_unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def _clean_task_string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = [part.strip() for part in value.split(",")]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    cleaned = [str(item).replace("\\", "/").strip() for item in raw_items if str(item).strip()]
    return _ordered_unique_text(cleaned)


def normalize_task_priority(
    value: Any,
    *,
    title: str = "",
    prompt: str = "",
    files: list[str] | None = None,
) -> str:
    text = str(value or "").strip().lower()
    if text in _TASK_PRIORITY_ALIASES:
        return _TASK_PRIORITY_ALIASES[text]

    blob = " ".join(
        [
            str(title or ""),
            str(prompt or ""),
            " ".join(_clean_task_string_list(files or [])),
        ]
    ).lower()
    if any(token in blob for token in _TASK_P0_HINTS):
        return "P0"
    if any(token in blob for token in _TASK_TEST_HINTS):
        return "P1"
    if any(path.startswith(".doc/") for path in _clean_task_string_list(files or [])):
        return "P2"
    return "P1"


def normalize_task_touched_file_globs(
    value: Any,
    *,
    files: list[str] | None = None,
    title: str = "",
    prompt: str = "",
) -> list[str]:
    explicit = _clean_task_string_list(value)
    if explicit:
        return explicit

    file_globs = _clean_task_string_list(files or [])
    if file_globs:
        return file_globs

    blob = f"{title} {prompt}".lower()
    inferred: list[str] = []
    if any(token in blob for token in _TASK_FRONTEND_HINTS):
        inferred.append("web_console/**/*")
    if any(token in blob for token in _TASK_BACKEND_HINTS):
        inferred.append("agent_runner/**/*")
    if any(token in blob for token in _TASK_TEST_HINTS):
        inferred.append("tests/**/*")
    if any(token in blob for token in _TASK_DOC_HINTS):
        inferred.append(".doc/**/*")
    if not inferred:
        inferred.append("**/*")
    return _ordered_unique_text(inferred)


def normalize_task_effort(
    value: Any,
    *,
    title: str = "",
    prompt: str = "",
    done_when: str = "",
    files: list[str] | None = None,
    touched_file_globs: list[str] | None = None,
    depends_on: list[str] | None = None,
) -> str:
    text = str(value or "").strip().lower()
    if text in _TASK_EFFORT_ALIASES:
        return _TASK_EFFORT_ALIASES[text]

    normalized_files = _clean_task_string_list(files or [])
    normalized_globs = normalize_task_touched_file_globs(
        touched_file_globs,
        files=normalized_files,
        title=title,
        prompt=prompt,
    )
    normalized_deps = _clean_task_string_list(depends_on or [])
    blob = " ".join(
        [
            str(title or ""),
            str(prompt or ""),
            str(done_when or ""),
            " ".join(normalized_files),
            " ".join(normalized_globs),
        ]
    ).lower()

    score = 1
    scope_count = max(len(normalized_files), len(normalized_globs))
    if scope_count >= 4:
        score += 2
    elif scope_count >= 2:
        score += 1
    if any("*" in item for item in normalized_globs):
        score += 1
    if len(normalized_deps) >= 2:
        score += 1
    if len(str(prompt or "").split()) >= 80 or len(str(done_when or "").split()) >= 40:
        score += 1
    if any(token in blob for token in _TASK_LARGE_HINTS):
        score += 1
    if any(path_hint in item.lower() for path_hint in _TASK_CORE_PATH_HINTS for item in normalized_files + normalized_globs):
        score += 1

    if score <= 1:
        return "S"
    if score <= 3:
        return "M"
    return "L"


def normalize_task_scheduling_metadata(
    *,
    title: str,
    prompt: str,
    done_when: str,
    files: Any,
    depends_on: Any,
    effort: Any = None,
    priority: Any = None,
    touched_file_globs: Any = None,
) -> dict[str, Any]:
    normalized_files = _clean_task_string_list(files)
    normalized_deps = _clean_task_string_list(depends_on)
    normalized_globs = normalize_task_touched_file_globs(
        touched_file_globs,
        files=normalized_files,
        title=title,
        prompt=prompt,
    )
    normalized_priority = normalize_task_priority(
        priority,
        title=title,
        prompt=prompt,
        files=normalized_files,
    )
    normalized_effort = normalize_task_effort(
        effort,
        title=title,
        prompt=prompt,
        done_when=done_when,
        files=normalized_files,
        touched_file_globs=normalized_globs,
        depends_on=normalized_deps,
    )
    return {
        "files": normalized_files,
        "depends_on": normalized_deps,
        "priority": normalized_priority,
        "effort": normalized_effort,
        "touched_file_globs": normalized_globs,
    }


def task_priority_rank(value: Any) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(
        normalize_task_priority(value),
        9,
    )


def task_effort_rank(value: Any) -> int:
    return {"S": 0, "M": 1, "L": 2}.get(normalize_task_effort(value), 9)


@dataclass
class TaskItem:
    id: str
    title: str
    prompt: str
    files: list[str]
    done_when: str
    skills: list[str]
    skills_rationale: str | None
    depends_on: list[str]
    effort: str = "M"
    priority: str = "P1"
    touched_file_globs: list[str] = field(default_factory=list)
    goal_trace: list[dict[str, Any]] | None = None


def task_scheduling_snapshot(task: Any) -> dict[str, Any]:
    if isinstance(task, dict):
        raw = task
        title = str(raw.get("title") or "").strip()
        prompt = str(raw.get("prompt") or "").strip()
        done_when = str(raw.get("done_when") or "").strip()
        files = raw.get("files") or []
        depends_on = raw.get("depends_on") or []
        effort = raw.get("effort")
        priority = raw.get("priority")
        touched_file_globs = raw.get("touched_file_globs")
    else:
        title = str(getattr(task, "title", "") or "").strip()
        prompt = str(getattr(task, "prompt", "") or "").strip()
        done_when = str(getattr(task, "done_when", "") or "").strip()
        files = getattr(task, "files", []) or []
        depends_on = getattr(task, "depends_on", []) or []
        effort = getattr(task, "effort", None)
        priority = getattr(task, "priority", None)
        touched_file_globs = getattr(task, "touched_file_globs", None)

    meta = normalize_task_scheduling_metadata(
        title=title,
        prompt=prompt,
        done_when=done_when,
        files=files,
        depends_on=depends_on,
        effort=effort,
        priority=priority,
        touched_file_globs=touched_file_globs,
    )
    risk = "high" if (
        task_effort_rank(meta["effort"]) >= 2
        or len(meta["depends_on"]) >= 2
        or any("*" in item for item in meta["touched_file_globs"])
        or any(path_hint in item.lower() for path_hint in _TASK_CORE_PATH_HINTS for item in meta["files"] + meta["touched_file_globs"])
    ) else "normal"
    return {
        "priority": meta["priority"],
        "effort": meta["effort"],
        "touched_file_globs": meta["touched_file_globs"],
        "depends_on": meta["depends_on"],
        "files": meta["files"],
        "risk": risk,
        "risk_rank": 1 if risk == "high" else 0,
        "priority_rank": task_priority_rank(meta["priority"]),
        "effort_rank": task_effort_rank(meta["effort"]),
    }


def load_backlog_json(path: Path) -> list[TaskItem]:
    """Load backlog tasks from BACKLOG.json.

    Supported shapes:
    - {"generated_at":..., "tasks":[...]}
    - {"tasks":[...]}
    - [...] (list of tasks)

    This loader is intentionally tolerant of PM output drift:
    - task keys may be {id,title,prompt,files,done_when} or include {description,files_changed,definition_of_done,...}
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (json.JSONDecodeError, ValueError) as exc:
        eprint(f"[WARN] BACKLOG.json parse error: {exc}")
        return []

    if isinstance(data, list):
        raw_tasks = data
    elif isinstance(data, dict):
        raw_tasks = data.get("tasks")
        if raw_tasks is None:
            raw_tasks = data.get("items") or data.get("backlog") or []
        if not isinstance(raw_tasks, list):
            raw_tasks = []
    else:
        raw_tasks = []

    items: list[TaskItem] = []
    auto_i = 1
    for x in raw_tasks:
        if not isinstance(x, dict):
            continue

        tid = str(x.get("id") or x.get("task_id") or x.get("key") or "").strip()
        if not tid:
            tid = f"T{auto_i:02d}"
        # Sanitize task ID to prevent path traversal (task IDs become directory names)
        tid = re.sub(r'[^A-Za-z0-9_\-]', '_', tid)[:64]
        if not tid:
            tid = f"T{auto_i:02d}"
        auto_i += 1

        title = str(x.get("title") or x.get("name") or "").strip() or tid
        prompt = str(x.get("prompt") or x.get("description") or x.get("details") or "").strip()
        if not prompt:
            prompt = f"Implement {tid}: {title}"

        files_val = x.get("files")
        if not isinstance(files_val, list):
            files_val = x.get("files_changed") if isinstance(x.get("files_changed"), list) else []
        files = [str(p).strip() for p in (files_val or []) if str(p).strip()]

        done_when = str(
            x.get("done_when")
            or x.get("definition_of_done")
            or x.get("acceptance_criteria")
            or x.get("dod")
            or ""
        ).strip()
        if not done_when:
            done_when = "Git diff exists and build passes."

        skills_val = x.get("skills") or []
        if isinstance(skills_val, str):
            skills = [p.strip() for p in skills_val.split(",") if p.strip()]
        elif isinstance(skills_val, list):
            skills = [str(p).strip() for p in skills_val if str(p).strip()]
        else:
            skills = []

        skills_rationale = x.get("skills_rationale")
        if skills_rationale is not None:
            skills_rationale = str(skills_rationale)

        depends_on_val = x.get("depends_on") or []
        scheduling = normalize_task_scheduling_metadata(
            title=title,
            prompt=prompt,
            done_when=done_when,
            files=files,
            depends_on=depends_on_val,
            effort=x.get("effort"),
            priority=x.get("priority"),
            touched_file_globs=(
                x.get("touched_file_globs")
                if x.get("touched_file_globs") is not None
                else x.get("touchedFilesGlobs")
            ),
        )

        goal_trace_val = x.get("goal_trace") or []
        goal_trace: list[dict[str, Any]] = []
        if isinstance(goal_trace_val, dict):
            goal_trace_val = [goal_trace_val]
        if isinstance(goal_trace_val, list):
            for trace in goal_trace_val:
                if isinstance(trace, dict):
                    goal_trace.append(dict(trace))

        # drop empty id/title/prompt
        if tid and title and prompt:
            items.append(
                TaskItem(
                    id=tid,
                    title=title,
                    prompt=prompt,
                    files=files,
                    done_when=done_when,
                    skills=skills,
                    skills_rationale=skills_rationale,
                    depends_on=scheduling["depends_on"],
                    effort=scheduling["effort"],
                    priority=scheduling["priority"],
                    touched_file_globs=scheduling["touched_file_globs"],
                    goal_trace=goal_trace,
                )
            )

    return items


def write_backlog_files(run_dir: Path, tasks: List[dict[str, Any]]) -> tuple[Path, Path]:
    """Write BACKLOG.json and BACKLOG.md from a normalized task dict list."""
    backlog = {"generated_at": now_iso(), "tasks": tasks}
    run_dir.mkdir(parents=True, exist_ok=True)
    bj = run_dir / "BACKLOG.json"
    try:
        atomic_write_json(bj, backlog)
    except Exception as ex:
        eprint(f"[WARN] Failed to write BACKLOG.json atomically: {ex}")
        safe_write_text(bj, json.dumps(backlog, ensure_ascii=False, indent=2) + "\n")

    md_lines = ["# BACKLOG", ""]
    for t in tasks:
        deps = t.get("depends_on") or []
        dep_str = f"  (depends_on: {deps})" if deps else ""
        md_lines.append(f"- [ ] {t.get('id','')} {t.get('title','')}{dep_str}")
    md_lines.append("")
    bm = run_dir / "BACKLOG.md"
    try:
        atomic_write_text(bm, "\n".join(md_lines) + "\n")
    except Exception as ex:
        eprint(f"[WARN] Failed to write BACKLOG.md atomically: {ex}")
        safe_write_text(bm, "\n".join(md_lines) + "\n")
    return bj, bm


def parse_backlog_md(path: Path) -> list[TaskItem]:
    """Legacy fallback: parse simple checklist backlog."""
    txt = path.read_text(encoding="utf-8-sig", errors="replace")
    items: list[TaskItem] = []
    for line in txt.splitlines():
        m = re.match(r"^\s*-\s*\[\s*\]\s*(T\d+)\s+(.*)$", line)
        if not m:
            continue
        tid = m.group(1).strip()
        title = m.group(2).strip()
        scheduling = normalize_task_scheduling_metadata(
            title=title,
            prompt=f"Implement {tid}: {title}",
            done_when="Git diff exists and build passes.",
            files=[],
            depends_on=[],
        )
        items.append(
            TaskItem(
                id=tid,
                title=title,
                prompt=f"Implement {tid}: {title}",
                files=scheduling["files"],
                done_when="Git diff exists and build passes.",
                skills=[],
                skills_rationale=None,
                depends_on=scheduling["depends_on"],
                effort=scheduling["effort"],
                priority=scheduling["priority"],
                touched_file_globs=scheduling["touched_file_globs"],
                goal_trace=[],
            )
        )
    return items


def load_state(path: Path) -> dict[str, Any]:
    state, origin = _read_state_unlocked(Path(path))
    return _StateSnapshot(state, origin=origin)


_MAX_FAILED_ENTRIES = 200
_MAX_WARNING_ENTRIES = 200
_STATE_LOCK_TIMEOUT_SEC = 30.0
_STATE_LOCK_POLL_SEC = 0.05
_STATE_LIST_KEYS = ("done", "failed", "warnings", "pending_review")
_STATE_THREAD_LOCKS: dict[str, threading.RLock] = {}
_STATE_THREAD_LOCKS_GUARD = threading.Lock()


class _StateSnapshot(dict):
    """dict-compatible state snapshot with enough origin data for merge-on-save."""

    _state_origin: dict[str, Any]

    def __init__(self, payload: Mapping[str, Any], *, origin: Mapping[str, Any] | None = None) -> None:
        super().__init__(payload)
        self._state_origin = _copy_state_payload(origin if origin is not None else payload)


def _copy_state_payload(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    try:
        return deepcopy(dict(payload or {}))
    except Exception:
        return dict(payload or {})


def _state_lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


def _state_thread_lock(lock_path: Path) -> threading.RLock:
    key = lock_path.expanduser().resolve().as_posix()
    with _STATE_THREAD_LOCKS_GUARD:
        lock = _STATE_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _STATE_THREAD_LOCKS[key] = lock
        return lock


def _prepare_state_lock_handle(handle: Any) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)


def _try_acquire_state_file_lock(handle: Any) -> bool:
    _prepare_state_lock_handle(handle)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _release_state_file_lock(handle: Any) -> None:
    _prepare_state_lock_handle(handle)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _state_write_lock(path: Path):
    state_path = Path(path).expanduser()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _state_lock_path(state_path)
    lock = _state_thread_lock(lock_path)
    started = time.monotonic()
    with lock, lock_path.open("a+b") as handle:
        while True:
            if _try_acquire_state_file_lock(handle):
                break
            if (time.monotonic() - started) >= _STATE_LOCK_TIMEOUT_SEC:
                raise RuntimeError(f"Timed out acquiring STATE.json lock: {lock_path}")
            time.sleep(_STATE_LOCK_POLL_SEC)
        try:
            yield
        finally:
            _release_state_file_lock(handle)


def _default_state() -> dict[str, Any]:
    return {"done": [], "failed": [], "warnings": []}


def _normalize_state_payload(state: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(state or {}) if isinstance(state, Mapping) else {}
    for key in _STATE_LIST_KEYS:
        if key in payload and not isinstance(payload.get(key), list):
            payload[key] = []
    for key in ("done", "failed", "warnings"):
        if not isinstance(payload.get(key), list):
            payload[key] = []
    return payload


def _state_recovery_warning(path: Path, reason: str, detail: str) -> dict[str, Any]:
    return {
        "task": "__state__",
        "reason": "state_recovered",
        "kind": "state_recovered",
        "source": "STATE.json",
        "path": path.as_posix(),
        "recovery_reason": reason,
        "detail": detail,
    }


def _backup_corrupt_state(path: Path) -> None:
    try:
        if path.exists():
            shutil.copy2(path, path.with_suffix(".json.corrupt"))
    except Exception:
        pass


def _read_state_unlocked(path: Path, *, recovery_warning: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    if path.exists():
        try:
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
            if not raw.strip():
                _backup_corrupt_state(path)
                state = _default_state()
                if recovery_warning:
                    state["warnings"].append(
                        _state_recovery_warning(
                            path,
                            "empty",
                            "STATE.json was empty; recovered with a default state before applying the mutation.",
                        )
                    )
                return state, _default_state()
            data = json.loads(raw)
            if not isinstance(data, dict):
                data = _default_state()
                if recovery_warning:
                    data["warnings"].append(
                        _state_recovery_warning(
                            path,
                            "non_object",
                            "STATE.json did not contain a JSON object; recovered with a default state before applying the mutation.",
                        )
                    )
                return _normalize_state_payload(data), _default_state()
            state = _normalize_state_payload(data)
            return state, _copy_state_payload(state)
        except (json.JSONDecodeError, ValueError) as exc:
            eprint(f"[WARN] STATE.json is corrupt ({exc}); backing up and resetting.")
            _backup_corrupt_state(path)
            state = _default_state()
            if recovery_warning:
                state["warnings"].append(
                    _state_recovery_warning(
                        path,
                        "corrupt",
                        f"STATE.json was corrupt; recovered with a default state before applying the mutation: {exc}",
                    )
                )
            return state, _default_state()
    return _default_state(), _default_state()


def _state_entry_fingerprint(entry: Any) -> str:
    try:
        return json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        return repr(entry)


def _merge_state_entry_list(latest: list[Any], requested: list[Any], origin: list[Any] | None) -> list[Any]:
    origin_items = origin if isinstance(origin, list) else []
    origin_fps = {_state_entry_fingerprint(item) for item in origin_items}
    requested_fps = {_state_entry_fingerprint(item) for item in requested}
    removed_fps = origin_fps - requested_fps
    additions = [item for item in requested if _state_entry_fingerprint(item) not in origin_fps]

    merged: list[Any] = []
    seen: set[str] = set()
    for item in [*latest, *additions]:
        fp = _state_entry_fingerprint(item)
        if fp in removed_fps or fp in seen:
            continue
        seen.add(fp)
        merged.append(item)
    return merged


def _state_entry_task_id(entry: Any) -> str:
    return _task_id_text(entry)


def _cap_state_lists(state: dict[str, Any]) -> dict[str, Any]:
    state = _normalize_state_payload(state)
    if len(state["failed"]) > _MAX_FAILED_ENTRIES:
        state["failed"] = state["failed"][-_MAX_FAILED_ENTRIES:]
    if len(state["warnings"]) > _MAX_WARNING_ENTRIES:
        state["warnings"] = state["warnings"][-_MAX_WARNING_ENTRIES:]
    return state


def _sync_state_mapping(target: Any, source: Mapping[str, Any]) -> None:
    if not isinstance(target, dict):
        return
    target.clear()
    target.update(_copy_state_payload(source))
    if isinstance(target, _StateSnapshot):
        target._state_origin = _copy_state_payload(source)


def _merge_state_payload(
    latest: Mapping[str, Any],
    requested: Mapping[str, Any],
    origin: Mapping[str, Any] | None,
) -> dict[str, Any]:
    latest_state = _normalize_state_payload(latest)
    requested_state = _normalize_state_payload(requested)
    origin_state = _normalize_state_payload(origin or {})

    merged = dict(latest_state)
    for key, value in requested_state.items():
        if key not in _STATE_LIST_KEYS:
            merged[key] = value

    latest_done = [str(item).strip() for item in latest_state.get("done", []) if str(item).strip()]
    requested_done = [str(item).strip() for item in requested_state.get("done", []) if str(item).strip()]
    origin_done = [str(item).strip() for item in origin_state.get("done", []) if str(item).strip()]
    removed_done = set(origin_done) - set(requested_done)

    done: list[str] = []
    seen_done: set[str] = set()
    for task_id in [*latest_done, *requested_done]:
        if task_id in removed_done or task_id in seen_done:
            continue
        seen_done.add(task_id)
        done.append(task_id)
    merged["done"] = sorted(done)
    done_ids = set(merged["done"])

    merged["failed"] = [
        item
        for item in _merge_state_entry_list(
            list(latest_state.get("failed") or []),
            list(requested_state.get("failed") or []),
            list(origin_state.get("failed") or []),
        )
        if _state_entry_task_id(item) not in done_ids
    ]
    merged["warnings"] = _merge_state_entry_list(
        list(latest_state.get("warnings") or []),
        list(requested_state.get("warnings") or []),
        list(origin_state.get("warnings") or []),
    )
    if "pending_review" in latest_state or "pending_review" in requested_state or "pending_review" in origin_state:
        merged["pending_review"] = _merge_state_entry_list(
            list(latest_state.get("pending_review") or []),
            list(requested_state.get("pending_review") or []),
            list(origin_state.get("pending_review") or []),
        )
    return _cap_state_lists(merged)


def _write_state_unlocked(path: Path, state: dict[str, Any]) -> None:
    state = _cap_state_lists(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(path, state)
    except Exception as ex:
        eprint(f"[WARN] Failed to write state atomically: {ex}")
        safe_write_text(path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def save_state(path: Path, state: dict[str, Any]) -> None:
    origin = getattr(state, "_state_origin", None)
    with _state_write_lock(Path(path)):
        latest, _latest_origin = _read_state_unlocked(Path(path), recovery_warning=True)
        merged = _merge_state_payload(latest, state, origin if isinstance(origin, Mapping) else None)
        _write_state_unlocked(Path(path), merged)
        _sync_state_mapping(state, merged)


def mutate_state(path: Path, mutator: Callable[[dict[str, Any]], dict[str, Any] | None]) -> dict[str, Any]:
    """Read, mutate, merge, and write STATE.json under the shared state lock."""
    with _state_write_lock(Path(path)):
        latest, origin = _read_state_unlocked(Path(path), recovery_warning=True)
        working = _StateSnapshot(latest, origin=origin)
        result = mutator(working)
        requested = result if isinstance(result, Mapping) else working
        merged = _merge_state_payload(latest, requested, origin)
        _write_state_unlocked(Path(path), merged)
        return _StateSnapshot(merged, origin=merged)


def append_state_entry(
    path: Path,
    bucket: str,
    entry: Mapping[str, Any] | Any,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bucket_name = str(bucket or "").strip() or "warnings"

    def _mutate(current: dict[str, Any]) -> dict[str, Any]:
        items = current.setdefault(bucket_name, [])
        if not isinstance(items, list):
            items = []
            current[bucket_name] = items
        items.append(dict(entry) if isinstance(entry, Mapping) else entry)
        return current

    updated = mutate_state(path, _mutate)
    if state is not None:
        _sync_state_mapping(state, updated)
    return updated


def append_state_warning(
    path: Path,
    warning: Mapping[str, Any] | Any,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return append_state_entry(path, "warnings", warning, state=state)


def append_state_failure(
    path: Path,
    entry: Mapping[str, Any],
    *,
    bucket: str = "failed",
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return append_state_entry(path, bucket, entry, state=state)


def mark_state_task_done(
    path: Path,
    task_id: str,
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id_text = str(task_id or "").strip()

    def _mutate(current: dict[str, Any]) -> dict[str, Any]:
        done = [str(item).strip() for item in current.get("done", []) if str(item).strip()]
        if task_id_text and task_id_text not in done:
            done.append(task_id_text)
        current["done"] = sorted(done)
        if task_id_text:
            current["failed"] = [
                item for item in current.get("failed", []) if _state_entry_task_id(item) != task_id_text
            ]
        return current

    updated = mutate_state(path, _mutate)
    if state is not None:
        _sync_state_mapping(state, updated)
    return updated


def clear_state_done_ids(
    path: Path,
    task_ids: set[str] | list[str] | tuple[str, ...],
    *,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    remove_ids = {str(task_id).strip() for task_id in task_ids if str(task_id).strip()}

    def _mutate(current: dict[str, Any]) -> dict[str, Any]:
        current["done"] = [
            str(item).strip()
            for item in current.get("done", [])
            if str(item).strip() and str(item).strip() not in remove_ids
        ]
        return current

    updated = mutate_state(path, _mutate)
    if state is not None:
        _sync_state_mapping(state, updated)
    return updated


def _task_id_text(value: Any) -> str:
    if isinstance(value, TaskItem):
        return str(value.id or "").strip()
    if isinstance(value, dict):
        for key in ("task", "task_id", "id", "key"):
            text = str(value.get(key) or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _task_id_set(items: Any) -> set[str]:
    if items is None:
        return set()
    if isinstance(items, (str, bytes)):
        text = str(items).strip()
        return {text} if text else set()
    if isinstance(items, dict) or isinstance(items, TaskItem):
        text = _task_id_text(items)
        return {text} if text else set()
    try:
        iterator = iter(items)
    except TypeError:
        text = _task_id_text(items)
        return {text} if text else set()

    out: set[str] = set()
    for item in iterator:
        text = _task_id_text(item)
        if text:
            out.add(text)
    return out


def load_backlog_task_ids(path: Path) -> set[str]:
    """Return the sanitized task IDs from the current BACKLOG.json generation."""
    if not path.exists():
        return set()
    try:
        return _task_id_set(load_backlog_json(path))
    except Exception:
        return set()


def count_state_task_ids(state: dict[str, Any], backlog_task_ids: Any) -> dict[str, int]:
    """Count state task IDs that belong to the current backlog generation only."""
    allowed_ids = _task_id_set(backlog_task_ids)
    if not allowed_ids:
        return {"done": 0, "failed": 0, "warnings": 0}

    def _count(entries: Any) -> int:
        if not isinstance(entries, list):
            return 0
        seen: set[str] = set()
        for entry in entries:
            task_id = _task_id_text(entry)
            if not task_id or task_id not in allowed_ids or task_id in seen:
                continue
            seen.add(task_id)
        return len(seen)

    state = state if isinstance(state, dict) else {}
    return {
        "done": _count(state.get("done")),
        "failed": _count(state.get("failed")),
        "warnings": _count(state.get("warnings")),
    }


def mark_backlog_done(backlog_md: Path, task_id: str) -> None:
    if not backlog_md.exists():
        return
    txt = backlog_md.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[str] = []
    changed = False
    for line in txt:
        if re.match(rf"^\s*-\s*\[\s*\]\s*{re.escape(task_id)}\b", line):
            out.append(re.sub(r"^\s*-\s*\[\s*\]", "- [x]", line))
            changed = True
        else:
            out.append(line)
    if changed:
        atomic_write_text(backlog_md, "\n".join(out) + "\n")


def write_default_p0_backlog(run_dir: Path) -> None:
    """Fallback backlog used if PM fails to generate one.

    IMPORTANT: this must be repository-agnostic. We avoid hardcoded language/framework
    tasks to prevent the Dev agent from doing irrelevant work.
    """
    backlog = {
        "generated_at": now_iso(),
        "tasks": [
            {
                "id": "T0",
                "title": "PM failed to generate BACKLOG: write a diagnostic report and stop",
                "prompt": (
                    "PM 단계가 BACKLOG를 생성하지 못했습니다. 레포의 실제 코드는 수정하지 마세요. "
                    "현재 run_dir(이번 실행 폴더)에 `PM_FAILURE.md`를 생성하고 다음 내용을 기록하세요: "
                    "(1) 어떤 에러/원인으로 PM이 실패했는지 추정, (2) 재현/재시도 방법(예: --debug, --pm-model, --pm-timeout-seconds), "
                    "(3) 사용자가 확인해야 할 설정(execution_backend, CLI 로그인 상태, docs_dir 등). "
                    "작성 후 작업을 종료(추가 코드 변경 없이)하세요."
                ),
                "files": ["(run_dir)/PM_FAILURE.md"],
                "done_when": "PM_FAILURE.md exists in run_dir and no product/source files were modified.",
                "skills": [],
                "skills_rationale": None,
                "depends_on": [],
                "effort": "S",
                "priority": "P0",
                "touched_file_globs": ["(run_dir)/PM_FAILURE.md"],
            }
        ],
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(run_dir / "BACKLOG.json", backlog)
    except Exception as ex:
        eprint(f"[WARN] Failed to write default BACKLOG.json atomically: {ex}")
        safe_write_text(run_dir / "BACKLOG.json", json.dumps(backlog, ensure_ascii=False, indent=2) + "\n")

    md_lines = ["# BACKLOG", ""]
    for t in backlog["tasks"]:
        md_lines.append(f"- [ ] {t['id']} {t['title']}")
    md_lines.append("")
    try:
        atomic_write_text(run_dir / "BACKLOG.md", "\n".join(md_lines) + "\n")
    except Exception as ex:
        eprint(f"[WARN] Failed to write default BACKLOG.md atomically: {ex}")
        safe_write_text(run_dir / "BACKLOG.md", "\n".join(md_lines) + "\n")
