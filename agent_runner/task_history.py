"""SQLite-based cross-run task history.

Stores completed/failed tasks per-project so the PM can avoid
duplicating work or retrying the same failing approach.

Design principles:
- **never-raise**: every public function catches all exceptions internally.
- **connection-per-call**: low frequency (~30 calls/run) makes pooling unnecessary.
- **standard library only**: uses sqlite3 (no extra dependencies).
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Sequence

from .config import default_database_path
from .utils import eprint

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS task_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,
    title        TEXT NOT NULL,
    status       TEXT NOT NULL,
    task_status  TEXT DEFAULT '',
    reason       TEXT DEFAULT '',
    detail       TEXT DEFAULT '',
    files        TEXT DEFAULT '[]',
    cycle_idx    INTEGER DEFAULT 0,
    attempt      INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 1,
    run_id       TEXT DEFAULT '',
    backend      TEXT DEFAULT '',
    recorded_at  TEXT NOT NULL
);
"""

_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_task_history_status_id ON task_history(status, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_task_history_title_id ON task_history(title, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_task_history_task_id_id ON task_history(task_id, id DESC);",
    "CREATE INDEX IF NOT EXISTS idx_task_history_task_status_id ON task_history(task_status, id DESC);",
]

# Columns added after initial release — migration is best-effort.
_MIGRATIONS = [
    "ALTER TABLE task_history ADD COLUMN attempt INTEGER DEFAULT 0;",
    "ALTER TABLE task_history ADD COLUMN max_attempts INTEGER DEFAULT 1;",
    "ALTER TABLE task_history ADD COLUMN task_status TEXT DEFAULT '';",
]

_MAX_DETAIL_LEN = 500
_MAX_PROMPT_TEXT_LEN = 240
_MAX_PROMPT_FAILURES = 12
_MAX_PROMPT_ARTIFACT_LINKS = 6

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _db_path(repo: Path) -> Path:
    return default_database_path(repo)


def _connect(repo: Path) -> sqlite3.Connection:
    db = _db_path(repo)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute(_SCHEMA_SQL)
        # best-effort migration for DBs created before attempt/max_attempts columns
        for sql in _MIGRATIONS:
            try:
                conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # column already exists
        for sql in _INDEX_SQL:
            conn.execute(sql)
        conn.commit()
    except Exception:
        conn.close()
        raise
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_task(
    repo: Path,
    *,
    task_id: str,
    title: str,
    status: str,
    task_status: str = "",
    reason: str = "",
    detail: str = "",
    files: Optional[Sequence[str]] = None,
    cycle_idx: int = 0,
    attempt: int = 0,
    max_attempts: int = 1,
    run_id: str = "",
    backend: str = "",
) -> None:
    """Record a completed or failed task. Never raises."""
    try:
        conn = _connect(repo)
        try:
            truncated_detail = (detail or "")[:_MAX_DETAIL_LEN]
            files_json = json.dumps(list(files) if files else [], ensure_ascii=False)
            recorded_at = datetime.now(timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO task_history "
                "(task_id, title, status, task_status, reason, detail, files, cycle_idx, attempt, max_attempts, run_id, backend, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(task_id),
                    str(title),
                    str(status),
                    str(task_status or ""),
                    str(reason or ""),
                    truncated_detail,
                    files_json,
                    int(cycle_idx),
                    int(attempt),
                    int(max_attempts),
                    str(run_id),
                    str(backend),
                    recorded_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] task_history.record_task failed: {exc}")


def query_history(
    repo: Path,
    *,
    max_items: int = 50,
    status_filter: Optional[str] = None,
) -> List[dict[str, Any]]:
    """Return recent task history rows as dicts. Never raises."""
    try:
        conn = _connect(repo)
        try:
            if status_filter:
                rows = conn.execute(
                    "SELECT task_id, title, status, task_status, reason, detail, files, cycle_idx, "
                    "attempt, max_attempts, run_id, backend, recorded_at "
                    "FROM task_history WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (str(status_filter), int(max_items)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT task_id, title, status, task_status, reason, detail, files, cycle_idx, "
                    "attempt, max_attempts, run_id, backend, recorded_at "
                    "FROM task_history ORDER BY id DESC LIMIT ?",
                    (int(max_items),),
                ).fetchall()
            cols = [
                "task_id", "title", "status", "task_status", "reason", "detail", "files",
                "cycle_idx", "attempt", "max_attempts", "run_id", "backend", "recorded_at",
            ]
            result = []
            for row in rows:
                d = dict(zip(cols, row))
                try:
                    d["files"] = json.loads(d.get("files") or "[]")
                except Exception:
                    d["files"] = []
                result.append(d)
            return result
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] task_history.query_history failed: {exc}")
        return []


def _text(value: Any, default: str = "", *, max_chars: int = 0) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if max_chars and len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _done_ids_set(done_ids: set[str] | Sequence[str] | None) -> set[str]:
    result: set[str] = set()
    for item in done_ids or []:
        text = _text(item)
        if text:
            result.add(text)
    return result


def _history_rows_by_task(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    rows_by_task: dict[str, list[dict[str, Any]]] = {}
    rows_by_title: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        task_id = _text(row.get("task_id"))
        title = _text(row.get("title"))
        if task_id:
            rows_by_task.setdefault(task_id, []).append(dict(row))
        if title:
            rows_by_title.setdefault(title.lower(), []).append(dict(row))
    return {"task": rows_by_task, "title": rows_by_title}


def _collect_unresolved_failure_rows(
    rows: Sequence[dict[str, Any]],
    *,
    done_ids: set[str] | Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    done = _done_ids_set(done_ids)
    done_entries: list[tuple[str, str]] = []
    failed_rows: list[dict[str, Any]] = []
    failed_keys: set[str] = set()

    for row in rows:
        status = _text(row.get("status"), "").upper()
        task_id = _text(row.get("task_id"))
        title = _text(row.get("title"))
        if status == "DONE":
            done_entries.append((task_id, title))
            continue
        key = task_id or title.lower()
        if not key or key in failed_keys:
            continue
        failed_keys.add(key)
        failed_rows.append(dict(row))

    done_id_set = {task_id for task_id, _title in done_entries if task_id}
    done_title_corpus = " ||| ".join(title.lower().strip() for _task_id, title in done_entries if title)

    unresolved: list[dict[str, Any]] = []
    for row in failed_rows:
        task_id = _text(row.get("task_id"))
        title = _text(row.get("title"))
        if task_id and (task_id in done_id_set or task_id in done):
            continue
        if title and done_title_corpus and _title_resolved(title, done_title_corpus):
            continue
        unresolved.append(row)

    return unresolved


def collect_unresolved_failure_items(
    repo: Path,
    *,
    done_ids: set[str] | Sequence[str] | None = None,
    max_items: int = 200,
) -> list[dict[str, Any]]:
    """Return unresolved failure rows from task history, newest-first."""
    try:
        rows = query_history(repo, max_items=max_items)
        return _collect_unresolved_failure_rows(rows, done_ids=done_ids)
    except Exception as exc:
        eprint(f"[WARN] task_history.collect_unresolved_failure_items failed: {exc}")
        return []


def _relative_run_path(run_dir: Path, path: Path | str) -> str:
    try:
        path_obj = Path(path)
    except Exception:
        return _text(path)
    try:
        return path_obj.resolve().relative_to(run_dir.resolve()).as_posix()
    except Exception:
        try:
            return path_obj.as_posix()
        except Exception:
            return _text(path)


def _collect_task_artifact_links(
    run_dir: Path,
    task_id: str,
    *,
    limit: int = _MAX_PROMPT_ARTIFACT_LINKS,
) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    if not run_dir or not run_dir.exists() or not run_dir.is_dir():
        return links

    tasks_root = run_dir / "tasks"
    if not tasks_root.exists() or not tasks_root.is_dir():
        return links

    candidates: list[Path] = []
    for child in tasks_root.iterdir():
        if child.is_dir() and task_id and task_id in child.name:
            candidates.append(child)
    candidates.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)

    file_names = (
        ("validation.json", "validation"),
        ("validation.txt", "validation text"),
        ("dev_output.txt", "dev output"),
        ("build.txt", "build log"),
        ("test.txt", "test log"),
        ("NOTES.md", "notes"),
    )

    for task_root in candidates[:1]:
        attempt_dirs = [p for p in task_root.iterdir() if p.is_dir() and p.name.startswith("attempt_")]
        attempt_dirs.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        for attempt_dir in attempt_dirs:
            for file_name, label in file_names:
                artifact = attempt_dir / file_name
                if not artifact.exists() or not artifact.is_file():
                    continue
                links.append(
                    {
                        "label": f"{label} ({attempt_dir.name})",
                        "path": _relative_run_path(run_dir, artifact),
                    }
                )
                if len(links) >= limit:
                    return links
        if not links:
            links.append({"label": task_root.name, "path": _relative_run_path(run_dir, task_root)})
            break

    return links[:limit]


def _build_failed_task_item(
    repo: Path,
    run_dir: Path,
    row: dict[str, Any],
    *,
    source: str,
    task_lookup: dict[str, str] | None = None,
    history_rows_by_task: dict[str, list[dict[str, Any]]] | None = None,
    history_rows_by_title: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    task_id = _text(row.get("task_id") or row.get("task") or row.get("taskId") or row.get("id"))
    title = _text(row.get("title") or row.get("task_title") or row.get("taskTitle"))
    if task_lookup and task_id:
        title = _text(task_lookup.get(task_id), title)

    matching_rows = []
    if history_rows_by_task and task_id:
        matching_rows = list(history_rows_by_task.get(task_id, []))
    if not matching_rows and history_rows_by_title and title:
        matching_rows = list(history_rows_by_title.get(title.lower(), []))

    if not title and matching_rows:
        title = _text(matching_rows[0].get("title"), task_id)
    if not title:
        title = task_id or _text(row.get("task_title") or row.get("taskTitle"), "unknown")

    reason = _text(row.get("reason"), "")
    task_status = _text(
        row.get("task_status") or row.get("taskStatus") or row.get("outcome_status") or row.get("outcomeStatus") or row.get("status"),
        "",
    )
    detail = _text(row.get("detail"), "", max_chars=_MAX_PROMPT_TEXT_LEN)
    current_attempt = _int(row.get("attempt"), 0)
    max_attempts = _int(row.get("max_attempts"), 0)
    cycle_idx = _int(row.get("cycle_idx") or row.get("cycle"), 0)
    step = _int(row.get("step"), 0)
    recorded_at = _text(row.get("recorded_at"), "")
    run_id = _text(row.get("run_id"), "")
    backend = _text(row.get("backend"), "")

    if not matching_rows and reason:
        matching_rows = [dict(row)]

    if not reason and matching_rows:
        reason = _text(matching_rows[0].get("reason"), "")
    if not task_status and matching_rows:
        task_status = _text(
            matching_rows[0].get("task_status") or matching_rows[0].get("taskStatus") or matching_rows[0].get("status"),
            "",
        )
    reason = reason or "unknown"

    if current_attempt <= 0 and matching_rows:
        current_attempt = _int(matching_rows[0].get("attempt"), current_attempt)
    if max_attempts <= 0 and matching_rows:
        max_attempts = _int(matching_rows[0].get("max_attempts"), max_attempts)

    history_count = len(matching_rows) if matching_rows else 1
    consecutive_failures = count_consecutive_title_failures(repo, title) if title else 0
    artifact_links = _collect_task_artifact_links(run_dir, task_id)
    remaining_attempts = max(0, max_attempts - current_attempt) if max_attempts > 0 and current_attempt > 0 else None
    split_required = reason in {"persistent_failure", "persistent_skip"} or consecutive_failures >= 3
    retry_allowed = not split_required and (remaining_attempts is None or remaining_attempts > 0 or max_attempts <= 0)

    attempts = {
        "current": current_attempt,
        "max": max_attempts,
        "history_count": history_count,
        "consecutive_failures": consecutive_failures,
        "remaining": remaining_attempts,
        "recorded_at": recorded_at,
        "run_id": run_id,
        "backend": backend,
    }
    retry_constraints = {
        "must_use_different_approach": True,
        "split_required": split_required,
        "new_task_id_required": split_required,
        "retry_allowed": retry_allowed,
        "remaining_attempts": remaining_attempts,
        "max_attempts": max_attempts,
        "reason": (
            "Persistent failures must be split into smaller subtasks with new task IDs."
            if split_required
            else "Retry with a different approach and avoid repeating the same failure cause."
        ),
    }

    item = {
        "task_id": task_id,
        "taskId": task_id,
        "title": title,
        "task_title": title,
        "taskTitle": title,
        "reason": reason,
        "status": task_status or "failed",
        "task_status": task_status or "failed",
        "taskStatus": task_status or "failed",
        "detail": detail,
        "attempts": attempts,
        "artifact_links": artifact_links,
        "artifactLinks": artifact_links,
        "retry_constraints": retry_constraints,
        "retryConstraints": retry_constraints,
        "cycle_idx": cycle_idx,
        "cycle": cycle_idx,
        "step": step,
        "run_id": run_id,
        "backend": backend,
        "recorded_at": recorded_at,
        "source": source,
        "summary": f"{task_id or '?'} | {title} | {reason} | {current_attempt or '?'}{'/' + str(max_attempts) if max_attempts else ''}",
    }
    blockers = (
        row.get("blocked_dependencies")
        or row.get("blockedDependencies")
        or row.get("blocking_dependencies")
        or row.get("blockingDependencies")
    )
    if isinstance(blockers, list):
        normalized_blockers = [dict(blocker) for blocker in blockers if isinstance(blocker, dict)]
        if normalized_blockers:
            item["blocked_dependencies"] = normalized_blockers
            item["blockedDependencies"] = normalized_blockers
            item["blocking_dependencies"] = normalized_blockers
            item["blockingDependencies"] = normalized_blockers
            next_action = _text(row.get("next_action") or row.get("nextAction"), "")
            item["next_action"] = next_action
            item["nextAction"] = next_action
    return item


def build_failed_tasks_artifact(
    repo: Path,
    run_dir: Path,
    *,
    failed_items: Sequence[dict[str, Any]] | None = None,
    task_lookup: dict[str, str] | None = None,
    done_ids: set[str] | Sequence[str] | None = None,
    max_items: int = 200,
    source: str = "state",
) -> dict[str, Any]:
    """Build a structured failed-tasks artifact for prompts and history."""
    try:
        rows = query_history(repo, max_items=max_items)
        history_index = _history_rows_by_task(rows)
        if failed_items is None:
            failed_items = collect_unresolved_failure_items(repo, done_ids=done_ids, max_items=max_items)
            source = "history"
        normalized_failed: list[dict[str, Any]] = []
        for raw in list(failed_items or [])[:_MAX_PROMPT_FAILURES]:
            if not isinstance(raw, dict):
                continue
            normalized_failed.append(
                _build_failed_task_item(
                    repo,
                    run_dir,
                    dict(raw),
                    source=source,
                    task_lookup=task_lookup,
                    history_rows_by_task=history_index["task"],
                    history_rows_by_title=history_index["title"],
                )
            )
        unresolved_count = len(normalized_failed)
        top_reason = normalized_failed[0]["reason"] if normalized_failed else ""
        summary = (
            f"{unresolved_count} unresolved failed task(s)"
            if unresolved_count
            else "No unresolved failed tasks"
        )
        if top_reason:
            summary += f"; latest reason: {top_reason}"
        return {
            "schema_version": 1,
            "kind": "failed_tasks",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo": str(repo),
            "run_dir": str(run_dir),
            "run_id": run_dir.name if run_dir else "",
            "source": source,
            "unresolved_count": unresolved_count,
            "summary": summary,
            "items": normalized_failed,
            "artifacts": {
                "json": (run_dir / "failed_tasks.json").as_posix(),
                "markdown": (run_dir / "failed_tasks.md").as_posix(),
            },
        }
    except Exception as exc:
        eprint(f"[WARN] task_history.build_failed_tasks_artifact failed: {exc}")
        return {
            "schema_version": 1,
            "kind": "failed_tasks",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "repo": str(repo),
            "run_dir": str(run_dir),
            "run_id": run_dir.name if run_dir else "",
            "source": source,
            "unresolved_count": 0,
            "summary": "Failed tasks unavailable",
            "items": [],
            "artifacts": {
                "json": (run_dir / "failed_tasks.json").as_posix(),
                "markdown": (run_dir / "failed_tasks.md").as_posix(),
            },
        }


def render_failed_tasks_block(artifact: dict[str, Any]) -> str:
    """Render a failed-tasks artifact as a JSON prompt block."""
    try:
        items = artifact.get("items") if isinstance(artifact, dict) else None
        if not items:
            return "(none)"
        prompt_safe_artifact = dict(artifact)
        prompt_safe_items: list[dict[str, Any]] = []
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            # PM only needs classification metadata here; raw failure output stays in artifacts.
            item.pop("detail", None)
            prompt_safe_items.append(item)
        prompt_safe_artifact["items"] = prompt_safe_items
        return "```json\n" + json.dumps(prompt_safe_artifact, ensure_ascii=False, indent=2, default=str) + "\n```"
    except Exception as exc:
        eprint(f"[WARN] task_history.render_failed_tasks_block failed: {exc}")
        return "(none)"


def format_history_block(
    repo: Path,
    *,
    max_items: int = 50,
) -> str:
    """Format task history as a compact text block for PM prompt injection.

    Output examples:
      - [DONE] T01: breaker_tools.py 프로토콜 준수 수정 (2026-02-09)
      - [FAIL/no_diff 3/3] T03: validation_service 검증 추가 (2026-02-09) — No code changes
      - [FAIL/build_failed 1/3] T04: mcp_server 에러 핸들링 (2026-02-08) — CS1002 error

    Never raises.
    """
    try:
        rows = query_history(repo, max_items=max_items)
        if not rows:
            return "(no history)"
        lines: list[str] = []
        for r in reversed(rows):  # oldest first
            status_tag = r["status"].upper()
            reason = r.get("reason") or ""
            att = int(r.get("attempt") or 0)
            max_att = int(r.get("max_attempts") or 0)
            date_str = (r.get("recorded_at") or "")[:10]

            if status_tag == "DONE":
                lines.append(f"- [DONE] {r['task_id']}: {r['title']} ({date_str})")
            else:
                # Build tag: FAIL/reason + attempt info
                tag = f"{status_tag}/{reason}" if reason else status_tag
                if att > 0 and max_att > 0:
                    tag += f" {att}/{max_att}"
                # Append truncated detail
                detail = (r.get("detail") or "").strip()
                if detail:
                    # Keep detail short for prompt (max 80 chars)
                    if len(detail) > 80:
                        detail = detail[:77] + "..."
                    lines.append(f"- [{tag}] {r['task_id']}: {r['title']} ({date_str}) — {detail}")
                else:
                    lines.append(f"- [{tag}] {r['task_id']}: {r['title']} ({date_str})")
        return "\n".join(lines)
    except Exception as exc:
        eprint(f"[WARN] task_history.format_history_block failed: {exc}")
        return "(history unavailable)"


def format_split_history_blocks(
    repo: Path,
    *,
    max_items: int = 50,
) -> tuple[str, str]:
    """Return (done_block, failed_block) separated for PM prompt injection.

    done_block: completed tasks (PM should not re-create)
    failed_block: failed tasks with details (PM MUST address with different approach)

    Never raises.
    """
    try:
        rows = query_history(repo, max_items=max_items)
        if not rows:
            return "(no history)", "(none)"

        done_lines: list[str] = []

        for r in reversed(rows):  # oldest first
            status = r["status"].upper()
            date_str = (r.get("recorded_at") or "")[:10]

            if status == "DONE":
                done_lines.append(f"- [DONE] {r['task_id']}: {r['title']} ({date_str})")

        done_block = "\n".join(done_lines) if done_lines else "(no completed tasks)"
        try:
            from .run_dir import find_latest_run_dir

            latest_run_dir = find_latest_run_dir(repo) or repo
        except Exception:
            latest_run_dir = repo
        failed_artifact = build_failed_tasks_artifact(
            repo,
            latest_run_dir,
            failed_items=None,
            max_items=max_items,
            source="history",
        )
        failed_block = render_failed_tasks_block(failed_artifact)

        return done_block, failed_block
    except Exception as exc:
        eprint(f"[WARN] task_history.format_split_history_blocks failed: {exc}")
        return "(history unavailable)", "(none)"


def count_unresolved_failures(
    repo: Path,
    done_ids: set[str] | None = None,
    *,
    max_items: int = 200,
) -> int:
    """Count tasks that failed and were NOT subsequently completed.

    Used by the completion evaluator to determine if there are outstanding failures.

    Resolution logic (handles PM task-ID recycling):
      1. Exact task_id match: failed T03 resolved if later T03 DONE exists.
      2. Title similarity: failed T03 "Add validation" resolved if later T01 DONE
         has a similar title (keyword overlap ≥ 60%), since PM may assign new IDs
         when retrying failed tasks in a fresh backlog.
      3. done_ids from current cycle's STATE.json done set (catches same-cycle resolution).
    """
    try:
        return len(collect_unresolved_failure_items(repo, done_ids=done_ids, max_items=max_items))
    except Exception as exc:
        eprint(f"[WARN] task_history.count_unresolved_failures failed: {exc}")
        return 0


def count_consecutive_title_failures(
    repo: Path,
    title: str,
    *,
    max_lookback: int = 20,
    excluded_task_statuses: Sequence[str] | None = None,
) -> int:
    """Count how many times a task with the given title failed consecutively (most-recent first).

    Scans the most recent ``max_lookback`` records matching *title*.
    Counts backwards from newest: increments for each FAILED/failed status,
    stops at the first DONE or non-failure status.

    Returns 0 if no failures are found or on any error. Never raises.
    """
    try:
        conn = _connect(repo)
        try:
            excluded = {
                str(value or "").strip().lower()
                for value in (excluded_task_statuses if excluded_task_statuses is not None else ("blocked_env", "test_contract_changed"))
                if str(value or "").strip()
            }
            rows = conn.execute(
                "SELECT status, task_status FROM task_history "
                "WHERE title = ? ORDER BY id DESC LIMIT ?",
                (str(title), int(max_lookback)),
            ).fetchall()
            count = 0
            for status, task_status in rows:
                normalized_task_status = str(task_status or "").strip().lower()
                if normalized_task_status in excluded:
                    continue
                if str(status or "").upper() in ("FAILED", "FAIL"):
                    count += 1
                else:
                    break
            return count
        finally:
            conn.close()
    except Exception as exc:
        eprint(f"[WARN] task_history.count_consecutive_title_failures failed: {exc}")
        return 0


def _title_resolved(failed_title: str, done_corpus: str) -> bool:
    """Check if a failed task's title is semantically covered by done tasks.

    Uses keyword overlap: extract significant words from the failed title,
    require ≥ 40% to appear in the done corpus. Minimum 2 keyword matches required.

    Korean words use a 2-char minimum (Korean words are commonly 2 chars).
    ASCII/English words use a 3-char minimum.
    """
    import re as _re
    noise = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has",
        "been", "are", "was", "were", "will", "can", "not", "all", "but",
        "add", "fix", "update", "implement", "create", "remove", "delete",
        "없음", "있음", "동작", "기능", "정상", "성공", "완료", "추가", "수정",
        "항목", "필요", "처리", "사용", "적용", "구현",
    }
    words = _re.findall(r'[\w가-힣]+', failed_title.lower())
    # Korean 2+ chars, ASCII/English 3+ chars
    def _sig(w: str) -> bool:
        if _re.search(r'[가-힣]', w):
            return len(w) >= 2 and w not in noise
        return len(w) >= 3 and w not in noise
    keywords = [w for w in words if _sig(w)]

    if len(keywords) < 2:
        return False

    match_count = sum(1 for kw in keywords if kw in done_corpus)
    threshold = max(2, int(len(keywords) * 0.4))
    return match_count >= threshold
