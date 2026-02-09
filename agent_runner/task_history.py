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

from .config import app_home, _repo_slug
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

# Columns added after initial release — migration is best-effort.
_MIGRATIONS = [
    "ALTER TABLE task_history ADD COLUMN attempt INTEGER DEFAULT 0;",
    "ALTER TABLE task_history ADD COLUMN max_attempts INTEGER DEFAULT 1;",
]

_MAX_DETAIL_LEN = 500

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _db_path(repo: Path) -> Path:
    return (app_home() / "databases" / f"{_repo_slug(repo)}.db").resolve()


def _connect(repo: Path) -> sqlite3.Connection:
    db = _db_path(repo)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.execute(_SCHEMA_SQL)
    # best-effort migration for DBs created before attempt/max_attempts columns
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
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
                "(task_id, title, status, reason, detail, files, cycle_idx, attempt, max_attempts, run_id, backend, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(task_id),
                    str(title),
                    str(status),
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
                    "SELECT task_id, title, status, reason, detail, files, cycle_idx, "
                    "attempt, max_attempts, run_id, backend, recorded_at "
                    "FROM task_history WHERE status = ? ORDER BY id DESC LIMIT ?",
                    (str(status_filter), int(max_items)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT task_id, title, status, reason, detail, files, cycle_idx, "
                    "attempt, max_attempts, run_id, backend, recorded_at "
                    "FROM task_history ORDER BY id DESC LIMIT ?",
                    (int(max_items),),
                ).fetchall()
            cols = [
                "task_id", "title", "status", "reason", "detail", "files",
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
