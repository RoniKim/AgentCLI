"""Backlog utilities shared by Codex and Claude Code backends.

Functions that were previously nested closures are parameterized here so both
backends can import and call them identically.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .goals import gate_pm_tasks_against_goals
from .pipeline.shared_runtime import merge_pm_tasks_with_existing_pending
from .state import load_backlog_json, parse_backlog_md, load_state, TaskItem
from .task_history import (
    build_failed_tasks_artifact as _build_failed_tasks_artifact,
    record_task as _record_task_history,
    render_failed_tasks_block as _render_failed_tasks_block,
)
from .utils import eprint, now_iso, safe_write_text


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
            if all((p.startswith(".doc/") or "/.doc/" in p or p.startswith(".agentcli/") or "/.agentcli/" in p) for p in fl):
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
    id_remap: dict[str, str] = {}  # old_id -> new_id for dependency fixup

    def _clean_goal_trace(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, dict):
            value = [value]
        if not isinstance(value, list):
            return []
        traces: list[dict[str, Any]] = []
        for trace in value:
            if not isinstance(trace, dict):
                continue
            goal_ref = str(trace.get("goal_ref") or trace.get("goal_id") or "").strip()
            goal_text = str(trace.get("goal_text") or trace.get("text") or "").strip()
            if not goal_ref and not goal_text:
                continue
            traces.append(dict(trace))
        return traces

    for t in filtered:
        tid = str(t.get("id") or "").strip()
        m = re.match(r"^T0*([1-9]\d*)([A-Za-z][A-Za-z0-9_-]*)?$", tid)
        n = int(m.group(1)) if m else 0
        suffix = (m.group(2) or "") if m else ""

        # Canonicalize: T01 → T1, T002 → T2 (선행 0 제거로 ID 충돌 방지)
        canonical = f"T{n}{suffix}" if n >= 1 else ""
        if canonical and canonical not in used:
            fixed_id = canonical
        elif n >= 1 and tid == canonical and tid not in used:
            fixed_id = tid  # 이미 canonical form
        else:
            while True:
                cand = f"T{next_num}"
                next_num += 1
                if cand not in used:
                    fixed_id = cand
                    break

        if tid and tid != fixed_id:
            id_remap[tid] = fixed_id
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
        normalized_task = {
            "id": fixed_id,
            "title": str(t.get("title") or fixed_id).strip() or fixed_id,
            "prompt": str(t.get("prompt") or "").strip() or f"Implement {fixed_id}.",
            "files": t.get("files") if isinstance(t.get("files"), list) else [],
            "done_when": str(t.get("done_when") or "Git diff exists and build passes.").strip(),
            "skills": skills,
            "skills_rationale": None if t.get("skills_rationale") is None else str(t.get("skills_rationale")),
            "depends_on": depends_on,
        }
        goal_trace = _clean_goal_trace(t.get("goal_trace"))
        if goal_trace:
            normalized_task["goal_trace"] = goal_trace
        for key in ("parent_task_id", "split_from_task_id", "split_reason", "split_index", "split_count"):
            if key in t:
                normalized_task[key] = t[key]
        out.append(normalized_task)

    # Remap depends_on references when task IDs were normalized
    if id_remap:
        valid_ids = {t["id"] for t in out}
        for t in out:
            t["depends_on"] = [
                id_remap.get(d, d) for d in t["depends_on"]
                if id_remap.get(d, d) in valid_ids
            ]
        remapped_str = ", ".join(f"{k}->{v}" for k, v in id_remap.items())
        eprint(f"[BACKLOG] Remapped task IDs: {remapped_str}")

    # Detect and break circular dependencies
    _break_circular_deps(out)

    # Check test task quality and emit warnings
    quality_warnings = _check_test_task_quality(out)
    for w in quality_warnings:
        eprint(f"[WARN] {w}")

    return out


def _write_pm_goal_gate_error(
    *,
    run_dir: Path,
    cycle_idx: int,
    kind: str,
    raw_pm_output_path: Path,
    pm_gate: dict[str, Any],
    backlog_gate: dict[str, Any],
    pm_output_model_dump: dict[str, Any],
) -> None:
    payload = {
        "generated_at": now_iso(),
        "cycle": cycle_idx,
        "kind": kind,
        "status": pm_gate.get("status", ""),
        "message": pm_gate.get("message", ""),
        "goal_path": pm_gate.get("goal_path", ""),
        "goals": pm_gate.get("goals", {}),
        "raw_output_path": raw_pm_output_path.as_posix(),
        "accepted_count": len(pm_gate.get("accepted_tasks") or []),
        "rejected_count": len(pm_gate.get("rejected_tasks") or []),
        "rejected_tasks": pm_gate.get("rejected_tasks", []),
        "backlog_status": backlog_gate.get("status", ""),
        "backlog_accepted_count": len(backlog_gate.get("accepted_tasks") or []),
        "backlog_rejected_count": len(backlog_gate.get("rejected_tasks") or []),
        "backlog_rejected_tasks": backlog_gate.get("rejected_tasks", []),
        "error": pm_gate.get("error"),
        "backlog_error": backlog_gate.get("error"),
        "pm_output": {
            "kind": pm_output_model_dump.get("kind"),
            "summary": pm_output_model_dump.get("summary"),
            "notes_md": pm_output_model_dump.get("notes_md"),
            "warnings": pm_output_model_dump.get("warnings", []),
            "open_questions": pm_output_model_dump.get("open_questions", []),
            "tasks": pm_output_model_dump.get("tasks", []),
        },
    }
    safe_write_text(
        run_dir / f"PM_GOALS_GATE_ERROR_cycle_{cycle_idx:03d}.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def postprocess_pm_output_tasks(
    *,
    repo: Path,
    run_dir: Path,
    cycle_idx: int,
    kind: str,
    raw_pm_output_path: Path,
    pm_output_model_dump: dict[str, Any],
    existing_tasks: list[Any],
    done_ids: set[str],
    failed_ids: set[str],
    completion_level: str,
) -> dict[str, Any]:
    """Apply shared GOALS/backlog postprocessing to a PM output payload."""
    raw_tasks_val = pm_output_model_dump.get("tasks")
    raw_tasks = [dict(task) for task in raw_tasks_val if isinstance(task, dict)] if isinstance(raw_tasks_val, list) else []

    pm_gate = gate_pm_tasks_against_goals(
        repo,
        raw_tasks,
        completion_level=completion_level,
    )
    accepted_pm_tasks = [dict(task) for task in pm_gate.get("accepted_tasks") or []]
    rejected_pm_tasks = [dict(task) for task in pm_gate.get("rejected_tasks") or []]

    merged_tasks = merge_pm_tasks_with_existing_pending(
        pm_tasks=accepted_pm_tasks,
        existing_tasks=existing_tasks,
        done_ids=done_ids,
        failed_ids=failed_ids,
    )
    backlog_gate = gate_pm_tasks_against_goals(
        repo,
        merged_tasks,
        completion_level=completion_level,
    )
    accepted_backlog_tasks = [dict(task) for task in backlog_gate.get("accepted_tasks") or []]
    rejected_backlog_tasks = [dict(task) for task in backlog_gate.get("rejected_tasks") or []]
    normalized_backlog_tasks = normalize_backlog_tasks(accepted_backlog_tasks, run_dir) if accepted_backlog_tasks else []

    pm_dump = dict(pm_output_model_dump)
    pm_dump["tasks"] = accepted_pm_tasks
    pm_dump["goals_gate"] = {
        "goal_path": pm_gate.get("goal_path", ""),
        "gate_required": pm_gate.get("gate_required", False),
        "status": pm_gate.get("status", ""),
        "message": pm_gate.get("message", ""),
        "accepted_count": len(accepted_pm_tasks),
        "rejected_count": len(rejected_pm_tasks),
        "error": pm_gate.get("error"),
        "goals": pm_gate.get("goals", {}),
        "backlog_status": backlog_gate.get("status", ""),
        "backlog_accepted_count": len(accepted_backlog_tasks),
        "backlog_rejected_count": len(rejected_backlog_tasks),
        "backlog_error": backlog_gate.get("error"),
    }
    if rejected_pm_tasks:
        pm_dump["rejected_tasks"] = rejected_pm_tasks
    else:
        pm_dump.pop("rejected_tasks", None)
    if rejected_backlog_tasks:
        pm_dump["backlog_rejected_tasks"] = rejected_backlog_tasks
    else:
        pm_dump.pop("backlog_rejected_tasks", None)

    if pm_gate.get("error") or rejected_pm_tasks or backlog_gate.get("error") or rejected_backlog_tasks:
        _write_pm_goal_gate_error(
            run_dir=run_dir,
            cycle_idx=cycle_idx,
            kind=kind,
            raw_pm_output_path=raw_pm_output_path,
            pm_gate=pm_gate,
            backlog_gate=backlog_gate,
            pm_output_model_dump=pm_dump,
        )

    return {
        "pm_gate": pm_gate,
        "accepted_pm_tasks": accepted_pm_tasks,
        "rejected_pm_tasks": rejected_pm_tasks,
        "pm_output_model_dump": pm_dump,
        "backlog_gate": backlog_gate,
        "backlog_tasks": normalized_backlog_tasks,
        "rejected_backlog_tasks": rejected_backlog_tasks,
    }


def _break_circular_deps(tasks: list[dict[str, Any]]) -> None:
    """Detect and break circular dependencies in-place.

    Uses DFS cycle detection. When a cycle is found, the back-edge
    dependency is removed and a warning is emitted.
    """
    graph: dict[str, list[str]] = {}
    valid_ids = {t["id"] for t in tasks}
    for t in tasks:
        graph[t["id"]] = [d for d in t.get("depends_on", []) if d in valid_ids]

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {tid: WHITE for tid in graph}
    edges_to_remove: list[tuple[str, str]] = []

    def dfs(node: str, path: list[str]) -> None:
        color[node] = GRAY
        for dep in graph.get(node, []):
            if color.get(dep) == GRAY:
                # Back edge found → cycle
                edges_to_remove.append((node, dep))
            elif color.get(dep) == WHITE:
                dfs(dep, path + [node])
        color[node] = BLACK

    for tid in graph:
        if color[tid] == WHITE:
            dfs(tid, [])

    if edges_to_remove:
        for src, dep in edges_to_remove:
            for t in tasks:
                if t["id"] == src and dep in t.get("depends_on", []):
                    t["depends_on"].remove(dep)
                    eprint(f"[WARN] Circular dependency detected: {src} -> {dep}; removed {dep} from {src}.depends_on")


def _check_test_task_quality(tasks: list[dict[str, Any]]) -> list[str]:
    """Return warnings for potentially trivial test tasks."""
    warnings: list[str] = []
    for t in tasks:
        title = str(t.get("title", "")).lower()
        prompt = str(t.get("prompt", ""))
        if "test" not in title:
            continue
        if len(prompt) < 150:
            warnings.append(
                f"Task {t.get('id')}: test prompt may be underspecified "
                f"({len(prompt)} chars < 150 minimum)"
            )
        # Detect trivial default/null-check only patterns
        trivial_keywords = ["_defaults()", "assert.null", "assert.equal(default"]
        prompt_lower = prompt.lower()
        logic_keywords = ["if ", "switch", "throw", "catch", "loop", "boundary", "edge", "error", "invalid"]
        has_logic_test = any(k in prompt_lower for k in logic_keywords)
        has_trivial_only = any(k in prompt_lower for k in trivial_keywords) and not has_logic_test
        if has_trivial_only:
            warnings.append(
                f"Task {t.get('id')}: test appears to only check defaults/nulls — "
                f"consider testing actual logic instead"
            )
    return warnings


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
) -> tuple[str, list[TaskItem], set[str], set[str]]:
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
        dep_suffix = f"  (depends_on: {t.depends_on})" if t.depends_on else ""
        lines.append(f"- [{mark}] {t.id} {t.title}{dep_suffix}")

    block = "\n".join(lines) if lines else "(no backlog found)"
    return block, tasks, done_ids, failed_ids


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
    """Build a structured failed-tasks block for PM context."""
    try:
        _backlog_block, tasks, done_ids, _failed_ids = load_backlog_context_for_pm(
            run_dir / "BACKLOG.json",
            run_dir / "BACKLOG.md",
            state_path,
        )
        state_obj = load_state(state_path)
    except Exception:
        state_obj = {"failed": []}
        tasks = []
        done_ids = set()
    failed_list = state_obj.get("failed", []) or []
    if not failed_list:
        return "(none)"
    title_lookup = {task.id: task.title for task in tasks}
    try:
        repo = run_dir.parents[2]
    except Exception:
        repo = run_dir
    artifact = _build_failed_tasks_artifact(
        repo,
        run_dir,
        failed_items=[item for item in failed_list if isinstance(item, dict)],
        task_lookup=title_lookup,
        done_ids=done_ids,
        source="state",
    )
    return _render_failed_tasks_block(artifact)


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
    task_status: str = "",
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
        task_status=task_status, reason=reason, detail=detail, files=files,
        cycle_idx=cycle, attempt=attempt, max_attempts=max_attempts,
        run_id=run_dir.name, backend=backend,
    )
