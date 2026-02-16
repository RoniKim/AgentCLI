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
            return "(no history)", "(no failures)"

        done_lines: list[str] = []
        failed_lines: list[str] = []

        for r in reversed(rows):  # oldest first
            status = r["status"].upper()
            date_str = (r.get("recorded_at") or "")[:10]

            if status == "DONE":
                done_lines.append(f"- [DONE] {r['task_id']}: {r['title']} ({date_str})")
            else:
                reason = r.get("reason") or ""
                att = int(r.get("attempt") or 0)
                max_att = int(r.get("max_attempts") or 0)
                detail = (r.get("detail") or "").strip()

                tag = f"{status}/{reason}" if reason else status
                if att > 0 and max_att > 0:
                    tag += f" {att}/{max_att}"

                line = f"- [{tag}] {r['task_id']}: {r['title']} ({date_str})"
                if detail:
                    if len(detail) > 120:
                        detail = detail[:117] + "..."
                    line += f"\n  failure_detail: {detail}"
                failed_lines.append(line)

        done_block = "\n".join(done_lines) if done_lines else "(no completed tasks)"
        failed_block = "\n".join(failed_lines) if failed_lines else "(no failures)"

        return done_block, failed_block
    except Exception as exc:
        eprint(f"[WARN] task_history.format_split_history_blocks failed: {exc}")
        return "(history unavailable)", "(history unavailable)"


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
        rows = query_history(repo, max_items=max_items)
        done = done_ids or set()
        # Deduplicate by task_id: keep latest title per ID (rows are newest-first)
        failed_map: dict[str, str] = {}   # task_id → title (first=latest wins)
        done_entries: list[tuple[str, str]] = []    # (task_id, title)
        for r in rows:
            tid = r.get("task_id", "")
            title = r.get("title", "")
            if r["status"].upper() == "DONE":
                done_entries.append((tid, title))
            else:
                if tid not in failed_map:
                    failed_map[tid] = title

        done_id_set = {t[0] for t in done_entries}
        done_title_corpus = " ||| ".join(t[1].lower().strip() for t in done_entries)

        unresolved_count = 0
        for ftid, ftitle in failed_map.items():
            # 1. Exact task_id match
            if ftid in done_id_set or ftid in done:
                continue
            # 2. Title similarity: check if any DONE task has similar title keywords
            if ftitle and done_title_corpus and _title_resolved(ftitle, done_title_corpus):
                continue
            unresolved_count += 1

        return unresolved_count
    except Exception as exc:
        eprint(f"[WARN] task_history.count_unresolved_failures failed: {exc}")
        return 0


def count_consecutive_title_failures(
    repo: Path,
    title: str,
    *,
    max_lookback: int = 20,
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
            rows = conn.execute(
                "SELECT status FROM task_history "
                "WHERE title = ? ORDER BY id DESC LIMIT ?",
                (str(title), int(max_lookback)),
            ).fetchall()
            count = 0
            for (status,) in rows:
                if status.upper() in ("FAILED", "FAIL"):
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
