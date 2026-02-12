"""Backlog utilities shared by Codex and Claude Code backends.

Functions that were previously nested closures are parameterized here so both
backends can import and call them identically.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Optional

from .state import load_backlog_json, parse_backlog_md, load_state, TaskItem
from .task_history import record_task as _record_task_history
from .utils import eprint


# ---------------------------------------------------------------------------
# normalize_backlog_tasks
# ---------------------------------------------------------------------------

def normalize_backlog_tasks(
    raw_tasks: list[dict[str, Any]],
    run_dir: Path,
) -> list[dict[str, Any]]:
    """Normalize/defend backlog tasks produced by PM.

    Goals:
    - Prevent PM from delegating PM-only work to Dev
    - Keep task IDs stable and unique (T1/T2 allowed)
    - Keep token usage predictable by keeping tasks atomic and concrete

    Parameters
    ----------
    raw_tasks : list[dict]
        Raw task dicts from PM output.
    run_dir : Path
        Run directory (used to write NOTES_PM.md for removed PM-only tasks).
    """

    def _looks_like_pm_work(t: dict[str, Any]) -> bool:
        txt = f"{t.get('title','')}\n{t.get('prompt','')}".lower()
        forbidden = (
            "create backlog", "generate backlog", "backlog.json", "backlog.md",
            "backlog", "triage", "prioritize", "roadmap", "plan", "planning",
            "analysis", "review", "audit", "repo_inventory", "repo inventory",
            "inventory", "prompt engineering", "update prompts", "pm instructions",
            "status report", "progress report", "shutdown report", "postmortem",
            "project_analysis.md", "project analysis", "pm_cache", "pm cache",
            "agent_runs", "run_dir", "state.json", "notes_pm.md", "requirements.md",
            "agent_tasks.md", "notes.md",
            "\ubc31\ub85c\uadf8", "\ubd84\uc11d", "\uac80\ud1a0", "\ub9ac\ud3ec\ud2b8", "\ubcf4\uace0\uc11c", "\uc778\ubca4\ud1a0\ub9ac", "\ud504\ub86c\ud504\ud2b8", "\uacc4\ud68d", "\uc815\ub9ac",
        )
        if any(k in txt for k in forbidden):
            positive = ("implement", "fix", "build", "test", "ui", "screen", "page", "component", "refactor")
            if any(p in txt for p in positive):
                return False
            return True
        files = t.get("files") or []
        if isinstance(files, list) and files:
            fl = [str(x).replace("\\", "/").lower().strip() for x in files if str(x).strip()]
            if all((p.startswith(".doc/") or "/.doc/" in p) for p in fl):
                return True
            if any("agent_runs" in p or "pm_cache" in p or "project_analysis" in p or "repo_inventory" in p for p in fl):
                return True
        return False

    filtered: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for t in raw_tasks:
        if not isinstance(t, dict):
            continue
        if _looks_like_pm_work(t):
            removed.append(t)
        else:
            filtered.append(t)

    if removed:
        try:
            notes_path = run_dir / "NOTES_PM.md"
            existing = ""
            if notes_path.exists():
                existing = notes_path.read_text(encoding="utf-8-sig", errors="replace")
            extra = ["\n\n## Removed PM-only tasks (auto-filter)", "(These were removed to avoid PM delegating planning artifacts to Dev.)", ""]
            for t in removed[:20]:
                extra.append(f"- {t.get('id','(no id)')} {t.get('title','')}")
            notes_path.write_text((existing.rstrip() + "\n" + "\n".join(extra)).strip() + "\n", encoding="utf-8", errors="replace")
        except Exception:
            pass

    used: set[str] = set()
    next_num = 1
    out: list[dict[str, Any]] = []
    for t in filtered:
        tid = str(t.get("id") or "").strip()
        m = re.match(r"^T(\d+)$", tid)
        n = int(m.group(1)) if m else 0

        if n >= 1 and tid and tid not in used:
            fixed_id = tid
        else:
            while True:
                cand = f"T{next_num}"
                next_num += 1
                if cand not in used:
                    fixed_id = cand
                    break

        used.add(fixed_id)
        skills_val = t.get("skills") or []
        if isinstance(skills_val, list):
            skills = [str(s).strip() for s in skills_val if str(s).strip()]
        elif isinstance(skills_val, str):
            skills = [s.strip() for s in skills_val.split(",") if s.strip()]
        else:
            skills = []
        depends_on_val = t.get("depends_on") or []
        if isinstance(depends_on_val, list):
            depends_on = [str(d).strip() for d in depends_on_val if str(d).strip()]
        else:
            depends_on = []
        out.append({
            "id": fixed_id,
            "title": str(t.get("title") or fixed_id).strip() or fixed_id,
            "prompt": str(t.get("prompt") or "").strip() or f"Implement {fixed_id}.",
            "files": t.get("files") if isinstance(t.get("files"), list) else [],
            "done_when": str(t.get("done_when") or "Git diff exists and build passes.").strip(),
            "skills": skills,
            "skills_rationale": None if t.get("skills_rationale") is None else str(t.get("skills_rationale")),
            "depends_on": depends_on,
        })
    return out


# ---------------------------------------------------------------------------
# validate_skill_ids
# ---------------------------------------------------------------------------

def validate_skill_ids(
    tasks: list[dict[str, Any]],
    *,
    skills_enabled: bool,
    skills_by_id: dict[str, Any],
    skills_records: list,
    skills_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Validate and optionally auto-fix skill IDs in task list."""
    if not skills_enabled or not tasks:
        return tasks

    from .skills.match import suggest_skills

    autofix = bool(skills_cfg.get("skill_match_autofix", False))
    threshold = float(skills_cfg.get("skill_match_autofix_threshold") or 0)
    updated: list[dict[str, Any]] = []
    for task in tasks:
        skills_list = [str(s).strip() for s in (task.get("skills") or []) if str(s).strip()]
        new_skills: list[str] = []
        for sid in skills_list:
            if sid in skills_by_id:
                new_skills.append(sid)
                continue
            suggestions = suggest_skills(sid, skills_records, max_results=3)
            if suggestions:
                top = suggestions[0]
                suggestion_msg = ", ".join([f"{s.skill_id}({s.name}, {s.score:.2f})" for s in suggestions])
                eprint(f"[SKILLS] Unknown skill_id '{sid}'. Suggestions: {suggestion_msg}")
                if autofix and top.score >= threshold:
                    eprint(f"[SKILLS] Auto-fix: '{sid}' -> '{top.skill_id}' (score {top.score:.2f})")
                    new_skills.append(top.skill_id)
                else:
                    new_skills.append(sid)
            else:
                eprint(f"[SKILLS] Unknown skill_id '{sid}' (no suggestions)")
                new_skills.append(sid)
        new_task = dict(task)
        new_task["skills"] = new_skills
        updated.append(new_task)
    return updated


# ---------------------------------------------------------------------------
# load_backlog_context_for_pm
# ---------------------------------------------------------------------------

def load_backlog_context_for_pm(
    backlog_json_path: Path,
    backlog_md_path: Path,
    state_path: Path,
) -> tuple[str, list[TaskItem], set[str]]:
    """Load backlog + state to provide PM with stable context for incremental planning."""
    tasks: list[TaskItem] = []
    if backlog_json_path.exists():
        try:
            tasks = load_backlog_json(backlog_json_path)
        except Exception:
            pass
    if not tasks and backlog_md_path.exists():
        try:
            tasks = parse_backlog_md(backlog_md_path)
        except Exception:
            pass

    try:
        state_obj = load_state(state_path)
    except Exception:
        state_obj = {"done": [], "failed": []}

    done_ids = set(state_obj.get("done", []) or [])
    failed_list = state_obj.get("failed", []) or []
    failed_ids = {(f.get("task", "") if isinstance(f, dict) else f) for f in failed_list}

    lines: list[str] = []
    for t in tasks:
        if t.id in done_ids:
            mark = "x"
        elif t.id in failed_ids:
            mark = "F"
        else:
            mark = " "
        lines.append(f"- [{mark}] {t.id} {t.title}")

    block = "\n".join(lines) if lines else "(no backlog found)"
    return block, tasks, done_ids


# ---------------------------------------------------------------------------
# find_latest_dev_log_for_task
# ---------------------------------------------------------------------------

def find_latest_dev_log_for_task(run_dir: Path, task_id: str) -> list[str]:
    """Find the latest dev log for a given task ID and return tail lines."""
    dev_logs_dir = run_dir / "dev_logs"
    if not dev_logs_dir.exists():
        return []
    matches = sorted(dev_logs_dir.glob(f"*_{task_id}_*.txt"), key=lambda x: x.stat().st_mtime)
    if not matches:
        matches = sorted(run_dir.glob(f"tasks/*_{task_id}/attempt_*/dev_log.txt"), key=lambda x: x.stat().st_mtime)
    if not matches:
        return []
    try:
        text = matches[-1].read_text(encoding="utf-8", errors="replace")
        lines = text.strip().splitlines()
        return lines[-15:]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# build_failed_tasks_block
# ---------------------------------------------------------------------------

def build_failed_tasks_block(state_path: Path, run_dir: Path) -> str:
    """Build a summary of failed tasks with reasons for PM context."""
    try:
        state_obj = load_state(state_path)
    except Exception:
        state_obj = {"failed": []}
    failed_list = state_obj.get("failed", []) or []
    if not failed_list:
        return "(none)"
    lines: list[str] = []
    for f in failed_list:
        if isinstance(f, dict):
            tid = f.get("task", "?")
            reason = f.get("reason", "unknown")
            detail = f.get("detail", "")
            lines.append(f"- {tid}: {reason}")
            if detail:
                lines.append(f"  Detail: {detail}")
            dev_log = find_latest_dev_log_for_task(run_dir, tid)
            if dev_log:
                lines.append("  Last dev log (tail):")
                for dl in dev_log[-8:]:
                    lines.append(f"    {dl}")
        else:
            lines.append(f"- {f}: unknown")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# record_history
# ---------------------------------------------------------------------------

def record_history(
    repo: Path,
    run_dir: Path,
    backend: str,
    *,
    task_id: str,
    title: str,
    status: str,
    reason: str = "",
    detail: str = "",
    files: list[str] | None = None,
    cycle: int = 0,
    attempt: int = 0,
    max_attempts: int = 1,
    task_history_enabled: bool = True,
) -> None:
    """Record a task result to the cross-run history database."""
    if not task_history_enabled:
        return
    _record_task_history(
        repo, task_id=task_id, title=title, status=status,
        reason=reason, detail=detail, files=files,
        cycle_idx=cycle, attempt=attempt, max_attempts=max_attempts,
        run_id=run_dir.name, backend=backend,
    )
