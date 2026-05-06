"""GOALS.md management for project completion tracking.

GOALS.md defines what "done" means for a project.
- P0 (Must-Have): Critical items.
- P1 (Should-Have): Important but secondary.

Completion level is configurable via `goals_completion_level`:
  "p0"  — P0 all checked → project_complete  (legacy default)
  "p1"  — P0 + P1 all checked → project_complete
  "all" — Every checkbox in the file checked → project_complete

When GOALS.md is absent, PM auto-generates a draft on the first cycle.
The user reviews/edits, and subsequent cycles converge toward those goals.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .utils import (
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_NO_TASKS,
    STOP_REASON_PM_REFRESH_NO_BACKLOG,
    atomic_write_text,
)

from .utils import eprint, now_iso


GOALS_INCOMPLETE_STATUS = "goals_incomplete"
MAX_GOAL_TRACES_PER_TASK = 2


def goals_path(repo: Path) -> Path:
    """Canonical location for project goals."""
    return repo / ".doc" / "GOALS.md"


def read_goals(repo: Path, max_chars: int = 0) -> Tuple[Optional[Path], Optional[str]]:
    """Read GOALS.md. Returns (path, text) or (None, None) if missing.

    Args:
        max_chars: Truncate after this many chars. 0 = no limit (default).
    """
    p = goals_path(repo)
    if not p.exists():
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


def format_goals_block(goals_path_: Optional[Path], goals_text: Optional[str],
                       max_lines: int = 800) -> str:
    """Format goals for PM prompt injection.

    Args:
        max_lines: Maximum lines to include. 0 = no limit. Default 800
                   (enough for ~400 goal items with section headers).
    """
    if not goals_path_ or not goals_text:
        return "(none — GOALS.md가 없습니다. 첫 Cycle에서 자동 생성합니다.)"
    lines = goals_text.strip().splitlines()
    if max_lines and len(lines) > max_lines:
        head = lines[:max_lines]
        return (f"# GOALS SOURCE: {goals_path_.as_posix()}\n"
                + "\n".join(head)
                + f"\n\n...(truncated — {len(lines) - max_lines} lines omitted)")
    return f"# GOALS SOURCE: {goals_path_.as_posix()}\n" + "\n".join(lines)


def _goals_completion_required_sections(completion_level: str) -> list[str]:
    level = completion_level.lower().strip() if completion_level else "all"
    if level == "p0":
        return ["p0"]
    return ["p0", "p1"]


def resolve_goals_completion_level(value: Any = None, *, default: str = "all") -> str:
    """Normalize the configured goals completion level."""
    fallback = str(default or "all").strip().lower() or "all"
    if fallback not in {"p0", "p1", "all"}:
        fallback = "all"
    level = str(value if value is not None else fallback).strip().lower()
    if level in {"p0", "p1", "all"}:
        return level
    return fallback


def _normalize_goal_match_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w가-힣]+", " ", str(text or "").lower())).strip()


def _goal_ref_for_item(section: str, line_number: int) -> str:
    section_label = str(section or "").strip().upper() or "P?"
    return f"{section_label}-L{int(line_number)}"


def _build_goal_item(
    *,
    section: str,
    section_index: int,
    line_number: int,
    text: str,
    checked: bool,
    raw_line: str,
) -> Dict[str, Any]:
    goal_ref = _goal_ref_for_item(section, line_number)
    section_label = str(section or "").strip().upper() or "P?"
    return {
        "goal_ref": goal_ref,
        "goal_id": goal_ref,
        "goal_section": section,
        "goal_section_label": section_label,
        "goal_index": int(section_index),
        "goal_line_number": int(line_number),
        "goal_text": text,
        "text": text,
        "checked": bool(checked),
        "done": bool(checked),
        "line_number": int(line_number),
        "raw_line": raw_line,
    }


def _analyze_goals_markdown(
    goals_text: Optional[str],
    *,
    completion_level: str = "all",
) -> Dict[str, Any]:
    """Parse GOALS.md checkboxes and evaluate completion status."""
    level = resolve_goals_completion_level(completion_level)
    required_sections = _goals_completion_required_sections(level)

    if not goals_text or not goals_text.strip():
        return {
            "has_goals": False,
            "valid": False,
            "missing_sections": list(required_sections),
            "warnings": [],
            "p0_total": 0, "p0_done": 0,
            "p1_total": 0, "p1_done": 0,
            "all_total": 0, "all_done": 0,
            "p0_complete": False,
            "p1_complete": False,
            "project_complete": False,
            "unmet_p0": [],
            "unmet_p1": [],
            "items": {"p0": [], "p1": []},
            "goal_items": [],
            "unmet_p0_items": [],
            "unmet_p1_items": [],
            "unmet_p0_goal_refs": [],
            "unmet_p0_goal_texts": [],
            "unmet_p1_goal_refs": [],
            "unmet_p1_goal_texts": [],
        }

    result: Dict[str, Any] = {
        "has_goals": True,
        "valid": False,
        "missing_sections": [],
        "warnings": [],
        "p0_total": 0, "p0_done": 0,
        "p1_total": 0, "p1_done": 0,
        "all_total": 0, "all_done": 0,
        "unmet_p0": [],
        "unmet_p1": [],
        "items": {"p0": [], "p1": []},
        "goal_items": [],
        "unmet_p0_items": [],
        "unmet_p1_items": [],
        "unmet_p0_goal_refs": [],
        "unmet_p0_goal_texts": [],
        "unmet_p1_goal_refs": [],
        "unmet_p1_goal_texts": [],
    }

    current_priority: Optional[str] = None
    section_has_items: dict[str, bool] = {"p0": False, "p1": False}
    section_items: dict[str, list[Dict[str, Any]]] = {"p0": [], "p1": []}
    all_items: list[Dict[str, Any]] = []

    for line_number, line in enumerate(goals_text.splitlines(), start=1):
        stripped = line.strip()

        if not stripped:
            continue

        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue

        heading = re.match(r"^(#+)\s+(.+)$", stripped)
        if heading:
            level_no = len(heading.group(1))
            title = heading.group(2).strip().lower()
            if level_no == 2 and re.match(r"^p0\b", title):
                current_priority = "p0"
                continue
            if level_no == 2 and re.match(r"^p1\b", title):
                current_priority = "p1"
                continue
            if level_no == 2 and re.match(r"^p[\s_-]*[01]\b", title):
                result["warnings"].append(
                    {
                        "line_number": line_number,
                        "line": stripped,
                        "reason": "malformed_priority_section_heading",
                        "message": "Priority section headings must use ## P0 or ## P1.",
                    }
                )
            if level_no > 2:
                continue
            current_priority = None
            continue

        checkbox_done = re.match(r'^\s*-\s*\[x\]', line, re.IGNORECASE)
        checkbox_open = re.match(r'^\s*-\s*\[\s\]', line)
        if checkbox_done or checkbox_open:
            is_done = bool(checkbox_done)
            item_text = re.sub(r'^\s*-\s*\[[x ]\]\s*', '', line, flags=re.IGNORECASE).strip()

            result["all_total"] += 1
            if is_done:
                result["all_done"] += 1

            if current_priority == "p0":
                section_has_items["p0"] = True
                result["p0_total"] += 1
                if is_done:
                    result["p0_done"] += 1
                else:
                    result["unmet_p0"].append(item_text)
                item = _build_goal_item(
                    section="p0",
                    section_index=len(section_items["p0"]) + 1,
                    line_number=line_number,
                    text=item_text,
                    checked=is_done,
                    raw_line=line,
                )
                section_items["p0"].append(item)
                all_items.append(item)
            elif current_priority == "p1":
                section_has_items["p1"] = True
                result["p1_total"] += 1
                if is_done:
                    result["p1_done"] += 1
                else:
                    result["unmet_p1"].append(item_text)
                item = _build_goal_item(
                    section="p1",
                    section_index=len(section_items["p1"]) + 1,
                    line_number=line_number,
                    text=item_text,
                    checked=is_done,
                    raw_line=line,
                )
                section_items["p1"].append(item)
                all_items.append(item)
            else:
                result["warnings"].append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "reason": "checkbox_outside_priority_section",
                        "message": "Checkbox item outside P0/P1 was ignored for P0/P1 completion.",
                    }
                )
            continue

    missing_sections = [section for section in required_sections if not section_has_items[section]]
    has_malformed_required_heading = any(
        warning.get("reason") == "malformed_priority_section_heading"
        for warning in result["warnings"]
    )
    result["missing_sections"] = missing_sections
    result["valid"] = not missing_sections and not has_malformed_required_heading

    result["p0_complete"] = section_has_items["p0"] and (result["p0_done"] >= result["p0_total"])
    result["p1_complete"] = section_has_items["p0"] and section_has_items["p1"] and (result["p0_done"] >= result["p0_total"]) and (result["p1_done"] >= result["p1_total"])

    if not result["valid"]:
        result["project_complete"] = False
    elif level == "p0":
        result["project_complete"] = result["p0_complete"]
    elif level == "p1":
        result["project_complete"] = result["p1_complete"]
    else:
        result["project_complete"] = result["all_total"] > 0 and result["all_done"] >= result["all_total"]

    result["items"] = {
        "p0": [dict(item) for item in section_items["p0"]],
        "p1": [dict(item) for item in section_items["p1"]],
    }
    result["goal_items"] = [dict(item) for item in all_items]
    result["unmet_p0_items"] = [dict(item) for item in section_items["p0"] if not item["checked"]]
    result["unmet_p1_items"] = [dict(item) for item in section_items["p1"] if not item["checked"]]
    result["unmet_p0_goal_refs"] = [item["goal_ref"] for item in result["unmet_p0_items"]]
    result["unmet_p0_goal_texts"] = [item["goal_text"] for item in result["unmet_p0_items"]]
    result["unmet_p1_goal_refs"] = [item["goal_ref"] for item in result["unmet_p1_items"]]
    result["unmet_p1_goal_texts"] = [item["goal_text"] for item in result["unmet_p1_items"]]

    return result


def parse_goals_completion(goals_text: Optional[str], *,
                           completion_level: str = "all") -> Dict[str, Any]:
    return _analyze_goals_markdown(goals_text, completion_level=completion_level)


def classify_goals_completion_status(
    status: Dict[str, Any],
    *,
    failed_unresolved: int = 0,
) -> Dict[str, Any]:
    """Classify a goals evaluation into an explicit terminal completion status."""
    has_goals = bool(status.get("has_goals", False))
    project_complete = bool(status.get("project_complete", False)) and failed_unresolved == 0
    if not has_goals:
        completion_status = "no_goals"
        completion_reason = GOALS_INCOMPLETE_STATUS
    elif project_complete:
        completion_status = STOP_REASON_PROJECT_COMPLETE
        completion_reason = STOP_REASON_PROJECT_COMPLETE
    else:
        completion_status = GOALS_INCOMPLETE_STATUS
        completion_reason = GOALS_INCOMPLETE_STATUS
    return {
        "has_goals": has_goals,
        "project_complete": project_complete,
        "completion_status": completion_status,
        "completionStatus": completion_status,
        "completion_reason": completion_reason,
        "completionReason": completion_reason,
    }


def write_completion_status(run_dir: Path, status: Dict[str, Any], *,
                            failed_unresolved: int = 0,
                            stop_reason: str = "") -> Path:
    """Write COMPLETION_STATUS.json to run_dir."""
    import json
    completion = classify_goals_completion_status(status, failed_unresolved=failed_unresolved)
    payload = {
        "generated_at": now_iso(),
        "stop_reason": stop_reason,
        "goals": status,
        "failed_tasks_unresolved": failed_unresolved,
        "project_complete": completion["project_complete"],
        "completion_status": completion["completion_status"],
        "completionStatus": completion["completionStatus"],
        "completion_reason": completion["completion_reason"],
        "completionReason": completion["completionReason"],
        "has_goals": completion["has_goals"],
        "hasGoals": completion["has_goals"],
    }
    out = run_dir / "COMPLETION_STATUS.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", errors="replace")
    return out


def resolve_completion_final_reason(run_dir: Path, final_reason: str, *, last_rc: int = 0) -> str:
    """Promote explicit GOALS completion artifacts into terminal run reasons.

    Missing or invalid GOALS remain incomplete: they may keep the diagnostic
    completion_status value `no_goals`, but their completion_reason must still
    prevent a successful `ok` terminal reason.
    """
    reason = str(final_reason or "").strip()
    if int(last_rc or 0) != 0 or reason not in {"", "ok"}:
        return reason
    try:
        payload = json.loads((Path(run_dir) / "COMPLETION_STATUS.json").read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return reason
    if not isinstance(payload, dict):
        return reason
    completion_status = str(
        payload.get("completion_status")
        or payload.get("completionStatus")
        or ""
    ).strip().lower()
    completion_reason = str(
        payload.get("completion_reason")
        or payload.get("completionReason")
        or ""
    ).strip().lower()
    if completion_reason in {GOALS_INCOMPLETE_STATUS, STOP_REASON_PROJECT_COMPLETE}:
        return completion_reason
    if completion_status in {GOALS_INCOMPLETE_STATUS, STOP_REASON_PROJECT_COMPLETE}:
        return completion_status
    if completion_status == "no_goals":
        return GOALS_INCOMPLETE_STATUS
    return reason


def _goal_gate_task_text(task: Dict[str, Any]) -> Dict[str, str]:
    return {
        "title": _normalize_goal_match_text(task.get("title") or ""),
        "prompt": _normalize_goal_match_text(task.get("prompt") or ""),
        "done_when": _normalize_goal_match_text(task.get("done_when") or ""),
    }


def _goal_gate_preserved_traces(
    task: Dict[str, Any],
    goal_items: list[Dict[str, Any]],
    *,
    goal_path: Optional[Path],
) -> list[Dict[str, Any]]:
    goal_items_by_ref: dict[str, Dict[str, Any]] = {}
    goal_items_by_text: dict[str, Dict[str, Any]] = {}
    for item in goal_items:
        goal_ref = str(item.get("goal_ref") or item.get("goal_id") or "").strip()
        goal_text = str(item.get("goal_text") or item.get("text") or "").strip()
        goal_text_norm = _normalize_goal_match_text(goal_text)
        if goal_ref:
            goal_items_by_ref[goal_ref] = item
        if goal_text_norm and goal_text_norm not in goal_items_by_text:
            goal_items_by_text[goal_text_norm] = item

    existing = task.get("goal_trace")
    if isinstance(existing, dict):
        existing = [existing]
    if not isinstance(existing, list):
        return []

    preserved: list[Dict[str, Any]] = []
    seen_refs: set[str] = set()
    for raw_trace in existing:
        if not isinstance(raw_trace, dict):
            continue
        goal_ref = str(raw_trace.get("goal_ref") or raw_trace.get("goal_id") or "").strip()
        goal_text = str(raw_trace.get("goal_text") or raw_trace.get("text") or "").strip()
        goal_text_norm = _normalize_goal_match_text(goal_text)
        item = goal_items_by_ref.get(goal_ref) if goal_ref else None
        if item is None and goal_text_norm:
            item = goal_items_by_text.get(goal_text_norm)
        if not isinstance(item, dict):
            continue

        resolved_ref = str(item.get("goal_ref") or item.get("goal_id") or "").strip()
        if not resolved_ref or resolved_ref in seen_refs:
            continue

        matched_fields_val = raw_trace.get("matched_fields")
        matched_fields = (
            [str(field).strip() for field in matched_fields_val if str(field).strip()]
            if isinstance(matched_fields_val, list)
            else []
        )
        preserved.append(
            {
                "goal_path": goal_path.as_posix() if goal_path else "",
                "goal_ref": resolved_ref,
                "goal_id": str(item.get("goal_id") or resolved_ref),
                "goal_section": str(item.get("goal_section") or ""),
                "goal_section_label": str(item.get("goal_section_label") or item.get("goal_section") or "").strip().upper(),
                "goal_line_number": int(item.get("goal_line_number") or item.get("line_number") or 0),
                "goal_index": int(item.get("goal_index") or item.get("index") or 0),
                "goal_text": str(item.get("goal_text") or item.get("text") or "").strip(),
                "goal_checked": bool(item.get("checked") if "checked" in item else item.get("goal_checked")),
                "matched_fields": matched_fields,
                "match_mode": "preserved_goal_trace",
            }
        )
        seen_refs.add(resolved_ref)

    return preserved


def _goal_gate_trace_for_task(
    task: Dict[str, Any],
    goal_items: list[Dict[str, Any]],
    *,
    goal_path: Optional[Path],
) -> list[Dict[str, Any]]:
    """Return deterministic goal trace candidates for a task."""
    if not goal_items:
        return []

    task_text = _goal_gate_task_text(task)
    traces = _goal_gate_preserved_traces(task, goal_items, goal_path=goal_path)
    if traces:
        return traces
    seen_refs = {
        str(trace.get("goal_ref") or trace.get("goal_id") or "").strip()
        for trace in traces
        if isinstance(trace, dict)
    }

    for item in goal_items:
        goal_ref = str(item.get("goal_ref") or item.get("goal_id") or "").strip()
        if not goal_ref or goal_ref in seen_refs:
            continue
        goal_text = str(item.get("goal_text") or item.get("text") or "").strip()
        goal_text_norm = _normalize_goal_match_text(goal_text)
        section_label = str(item.get("goal_section_label") or item.get("goal_section") or "").strip().upper()
        line_number = int(item.get("goal_line_number") or item.get("line_number") or 0)
        ref_pattern = None
        if section_label and line_number > 0:
            ref_pattern = re.compile(rf"\b{re.escape(section_label)}\W*L\W*{line_number}\b", re.IGNORECASE)

        matched_fields: list[str] = []
        matched_ref = False
        matched_text = False
        for field_name, field_text in task_text.items():
            if not field_text:
                continue
            if ref_pattern and ref_pattern.search(field_text):
                matched_fields.append(field_name)
                matched_ref = True
                continue
            if goal_text_norm and goal_text_norm in field_text:
                matched_fields.append(field_name)
                matched_text = True

        if not matched_fields:
            continue

        seen_refs.add(goal_ref)
        traces.append(
            {
                "goal_path": goal_path.as_posix() if goal_path else "",
                "goal_ref": goal_ref,
                "goal_id": str(item.get("goal_id") or goal_ref),
                "goal_section": str(item.get("goal_section") or ""),
                "goal_section_label": section_label or str(item.get("goal_section") or "").strip().upper(),
                "goal_line_number": line_number,
                "goal_index": int(item.get("goal_index") or item.get("index") or 0),
                "goal_text": goal_text,
                "goal_checked": bool(item.get("checked") if "checked" in item else item.get("goal_checked")),
                "matched_fields": list(dict.fromkeys(matched_fields)),
                "match_mode": "goal_ref+text" if matched_ref and matched_text else "goal_ref" if matched_ref else "goal_text",
            }
        )

    return traces


def _short_task_text(text: Any, *, max_len: int = 120) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(normalized) <= max_len:
        return normalized
    return normalized[: max(0, max_len - 3)].rstrip() + "..."


def _goal_scope_lines(traces: list[Dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for trace in traces:
        ref = str(trace.get("goal_ref") or trace.get("goal_id") or "").strip()
        text = _short_task_text(trace.get("goal_text") or trace.get("text") or "", max_len=180)
        if ref and text:
            lines.append(f"- {ref}: {text}")
        elif ref:
            lines.append(f"- {ref}")
        elif text:
            lines.append(f"- {text}")
    return lines


def _split_oversized_goal_task(
    task: Dict[str, Any],
    matches: list[Dict[str, Any]],
    *,
    max_goal_traces_per_task: int = MAX_GOAL_TRACES_PER_TASK,
) -> list[Dict[str, Any]]:
    """Split a PM task that bundled too many GOALS into reviewable chunks."""
    max_goals = max(1, int(max_goal_traces_per_task or MAX_GOAL_TRACES_PER_TASK))
    if len(matches) <= max_goals:
        next_task = dict(task)
        next_task["goal_trace"] = [dict(trace) for trace in matches]
        return [next_task]

    original_id = str(task.get("id") or "T").strip() or "T"
    original_title = _short_task_text(task.get("title") or original_id, max_len=160)
    original_prompt = str(task.get("prompt") or "").strip()
    original_done_when = str(task.get("done_when") or "").strip()
    chunks = [matches[index : index + max_goals] for index in range(0, len(matches), max_goals)]
    split_tasks: list[Dict[str, Any]] = []

    for index, chunk in enumerate(chunks, start=1):
        primary = chunk[0] if chunk else {}
        primary_text = _short_task_text(primary.get("goal_text") or primary.get("text") or original_title, max_len=110)
        task_id = original_id if index == 1 else f"{original_id}-{index}"
        scope_lines = "\n".join(_goal_scope_lines([dict(trace) for trace in chunk]))
        next_task = dict(task)
        next_task["id"] = task_id
        next_task["title"] = primary_text or original_title
        next_task["prompt"] = (
            "AgentCLI split this PM task because the original task matched too many unchecked GOALS items.\n"
            "Implement ONLY the GOALS scope below in this task; leave the remaining original scope for the sibling tasks.\n\n"
            f"GOALS scope:\n{scope_lines}\n\n"
            f"Original task title: {original_title}\n\n"
            f"Original prompt:\n{original_prompt}"
        ).strip()
        next_task["done_when"] = (
            "Only the listed GOALS scope is implemented and validated; unrelated sibling GOALS remain untouched."
            if not original_done_when
            else (
                "Only the listed GOALS scope is implemented and validated. "
                f"Original done_when context: {original_done_when}"
            )
        )
        next_task["goal_trace"] = [dict(trace) for trace in chunk]
        next_task["split_from_task_id"] = original_id
        next_task["split_reason"] = "oversized_goal_bundle"
        next_task["split_index"] = index
        next_task["split_count"] = len(chunks)
        split_tasks.append(next_task)

    return split_tasks


def gate_pm_tasks_against_goals(
    repo: Path,
    tasks: list[Dict[str, Any]],
    *,
    completion_level: str = "all",
) -> Dict[str, Any]:
    """Gate PM tasks against GOALS.md and attach trace metadata.

    When unchecked P0 items exist, each PM task must reference at least one
    unchecked P0 goal by exact text or a deterministic GOALS id. Tasks that do
    not trace to an unchecked P0 are rejected rather than being written to
    BACKLOG.json.
    """
    goal_path, goals_text = read_goals(repo)
    goal_status = parse_goals_completion(goals_text, completion_level=completion_level)
    goal_items = list(goal_status.get("goal_items") or [])
    unmet_p0_items = list(goal_status.get("unmet_p0_items") or [])
    gate_required = bool(goal_path and goals_text and goals_text.strip() and unmet_p0_items)
    candidate_items = unmet_p0_items if gate_required else goal_items

    accepted_tasks: list[Dict[str, Any]] = []
    rejected_tasks: list[Dict[str, Any]] = []
    split_tasks: list[Dict[str, Any]] = []
    emitted_ids_by_source: dict[str, list[str]] = {}
    goal_path_str = goal_path.as_posix() if goal_path else ""

    for raw_task in tasks:
        if not isinstance(raw_task, dict):
            continue
        task = dict(raw_task)
        all_matches = _goal_gate_trace_for_task(task, goal_items, goal_path=goal_path)
        accepted_matches = _goal_gate_trace_for_task(task, candidate_items, goal_path=goal_path)

        if gate_required:
            if not accepted_matches:
                rejected_tasks.append(
                    {
                        "id": str(task.get("id") or "").strip(),
                        "title": str(task.get("title") or "").strip(),
                        "reason": "missing_unchecked_p0_reference",
                        "message": "Task does not reference an unchecked P0 goal by exact text or GOALS id.",
                        "matched_goal_refs": [trace["goal_ref"] for trace in all_matches],
                        "required_goal_refs": [item["goal_ref"] for item in unmet_p0_items],
                    }
                )
                continue
            emitted = _split_oversized_goal_task(task, accepted_matches)
        else:
            emitted = _split_oversized_goal_task(task, all_matches) if all_matches else [task]

        original_id = str(task.get("id") or "").strip()
        if original_id:
            emitted_ids_by_source[original_id] = [
                str(emitted_task.get("id") or "").strip()
                for emitted_task in emitted
                if isinstance(emitted_task, dict) and str(emitted_task.get("id") or "").strip()
            ]

        if len(emitted) > 1:
            split_tasks.append(
                {
                    "id": str(task.get("id") or "").strip(),
                    "title": str(task.get("title") or "").strip(),
                    "matched_goal_refs": [trace["goal_ref"] for trace in (accepted_matches or all_matches)],
                    "emitted_task_count": len(emitted),
                    "max_goal_traces_per_task": MAX_GOAL_TRACES_PER_TASK,
                    "reason": "oversized_goal_bundle",
                }
            )
        accepted_tasks.extend(emitted)

    if accepted_tasks and emitted_ids_by_source:
        remapped_accepted: list[Dict[str, Any]] = []
        for accepted_task in accepted_tasks:
            depends_on_val = accepted_task.get("depends_on")
            if not isinstance(depends_on_val, list) or not depends_on_val:
                remapped_accepted.append(accepted_task)
                continue

            expanded: list[str] = []
            for dep in depends_on_val:
                dep_id = str(dep or "").strip()
                if not dep_id:
                    continue
                remapped = emitted_ids_by_source.get(dep_id)
                if remapped:
                    expanded.extend(remapped)
                else:
                    expanded.append(dep_id)
            next_task = dict(accepted_task)
            next_task["depends_on"] = list(dict.fromkeys(expanded))
            remapped_accepted.append(next_task)
        accepted_tasks = remapped_accepted

    if gate_required:
        if rejected_tasks and not accepted_tasks:
            status = "rejected"
            error_code = "pm_goal_gate_rejected"
            message = "PM tasks were rejected because GOALS has unchecked P0 items and no task referenced them."
        elif rejected_tasks:
            status = "partial"
            error_code = "pm_goal_gate_partial"
            message = "Some PM tasks were rejected because they did not reference an unchecked P0 goal."
        else:
            status = "accepted"
            error_code = ""
            message = "All PM tasks referenced an unchecked P0 goal."
    else:
        status = "accepted"
        error_code = ""
        message = "GOALS gate was not required."

    error: Optional[Dict[str, Any]] = None
    if error_code:
        error = {
            "code": error_code,
            "message": message,
            "details": {
                "goal_path": goal_path_str,
                "gate_required": gate_required,
                "accepted_count": len(accepted_tasks),
                "rejected_count": len(rejected_tasks),
                "split_count": len(split_tasks),
                "unmet_p0_goal_refs": [item["goal_ref"] for item in unmet_p0_items],
                "unmet_p0_goal_texts": [item["goal_text"] for item in unmet_p0_items],
            },
        }

    return {
        "goal_path": goal_path_str,
        "goals": goal_status,
        "gate_required": gate_required,
        "status": status,
        "message": message,
        "accepted_tasks": accepted_tasks,
        "rejected_tasks": rejected_tasks,
        "split_tasks": split_tasks,
        "max_goal_traces_per_task": MAX_GOAL_TRACES_PER_TASK,
        "error": error,
    }


# -- PM Goals generation prompt fragment --

GOALS_GENERATION_INSTRUCTION = (
    "GOALS.md가 존재하지 않습니다.\n"
    "백로그 생성 전에, 먼저 .doc/GOALS.md 파일을 생성하세요.\n"
    "레포의 README, 코드 구조, 기존 기능을 분석하여 아래 형식으로 작성:\n\n"
    "```markdown\n"
    "# Project Goals\n\n"
    "> Auto-generated by AgentCLI PM.\n"
    "> 사용자 검토 후 수정하세요. 이후 Cycle은 이 파일을 기준으로 완성도를 평가합니다.\n\n"
    "## P0 (Must-Have)\n"
    "- [ ] (핵심 기능 1 — 없으면 프로젝트가 동작하지 않음)\n"
    "- [ ] (핵심 기능 2)\n"
    "- [ ] 빌드 성공\n"
    "- [ ] 런타임 크래시 없음\n\n"
    "## P1 (Should-Have)\n"
    "- [ ] (있으면 좋은 기능 1)\n"
    "- [ ] (있으면 좋은 기능 2)\n\n"
    "## Completion Criteria\n"
    "- 모든 P0 항목 [x] 완료\n"
    "- 빌드 게이트 통과\n"
    "- 실패 후 미처리 태스크 0개\n"
    "```\n\n"
    "P0에는 프로젝트가 동작하는 데 필수적인 기능만 포함하세요.\n"
    "P1에는 품질/UX 개선 항목을 포함하세요.\n"
    "GOALS.md 생성 후, 해당 목표를 기반으로 백로그를 생성하세요.\n"
)

GOALS_EVALUATION_INSTRUCTION = (
    "**GOALS.md는 최우선 지시사항입니다. 반드시 아래 규칙을 따르세요:**\n\n"
    "1. **미완료 P0 항목이 최고 우선순위입니다.** 대부분의 태스크는 미완료 P0 항목을 직접 구현해야 합니다.\n"
    "2. **안정화 태스크 허용.** GOALS 구현 과정에서 발견된 빌드 에러, 테스트 실패, 컴파일 경고 수정은\n"
    "   GOALS 항목과 같은 태스크에 포함하거나 별도 안정화 태스크로 생성할 수 있습니다.\n"
    "   단, GOALS와 무관한 순수 리팩토링이나 코드 스타일 개선은 허용하지 않습니다.\n"
    "3. **P0 전부 완료 시에만 P1로 이동.** P0가 남아 있으면 P1 태스크를 생성하지 마세요.\n"
    "4. **태스크 제목에 GOALS 항목 원문을 반드시 포함하세요.**\n"
    "   예: title=\"Dashboard 데이터 최신성 표시 — 각 카드별 N분 전 갱신 타임스탬프\"\n"
    "   이유: 시스템이 키워드 매칭으로 GOALS 체크박스를 자동 업데이트합니다.\n"
    "5. **태스크 prompt 첫 줄에 GOALS 항목을 인용하세요.**\n"
    "   예: prompt=\"GOALS: Dashboard 데이터 최신성 표시 — 각 카드별 N분 전 갱신 타임스탬프\\n\\n구현: ...\"\n"
    "6. GOALS.md 체크박스는 시스템이 자동 업데이트합니다. 직접 수정하지 마세요.\n"
    "7. 새로운 P0/P1 이슈를 발견하면 open_questions에 기재하세요 (태스크로 만들지 마세요).\n"
    "8. **Backend gap 처리.** RPC/뷰 계약이 누락된 GOALS 항목은 가짜 구현 대신 warnings에 기록하세요.\n"
)


# ---------------------------------------------------------------------------
# GOALS.md auto-refresh — rescuable reasons + decision function
# ---------------------------------------------------------------------------

GOALS_REFRESH_RESCUABLE_REASONS: frozenset[str] = frozenset({
    STOP_REASON_PROJECT_COMPLETE,       # Dev→QA 후 GOALS 전체 완료
    STOP_REASON_NO_TASKS,               # PipelineManager: 백로그 없음/빈 태스크
    STOP_REASON_PM_REFRESH_NO_BACKLOG,  # run_dev_loop: PM refresh 후 백로그 없음
})


def should_attempt_goals_refresh(
    repo: Path,
    reason: str,
    goals_refresh_count: int,
    goals_refresh_max: int,
    goals_auto_refresh: bool,
    completion_level: str = "all",
) -> Tuple[bool, str]:
    """Determine whether a goals auto-refresh should be attempted.

    Returns (should_attempt, why) where *why* is a short tag:
      "ok"              — attempt is warranted
      "disabled"        — feature flag off
      "not_rescuable"   — reason not in GOALS_REFRESH_RESCUABLE_REASONS
      "max_reached"     — refresh count exhausted
      "no_goals"        — GOALS.md absent or empty
      "goals_incomplete" — goals exist but not all complete yet
    """
    if not goals_auto_refresh:
        return (False, "disabled")
    if reason not in GOALS_REFRESH_RESCUABLE_REASONS:
        return (False, "not_rescuable")
    if goals_refresh_count >= goals_refresh_max:
        return (False, "max_reached")

    _path, goals_text = read_goals(repo)
    status = parse_goals_completion(goals_text, completion_level=completion_level)
    if not status.get("has_goals"):
        return (False, "no_goals")
    if not status.get("project_complete"):
        return (False, "goals_incomplete")

    return (True, "ok")


# ---------------------------------------------------------------------------
# GOALS.md auto-refresh prompt + logic
# ---------------------------------------------------------------------------

GOALS_REFRESH_PROMPT = (
    "당신은 프로젝트 분석 전문가입니다.\n"
    "아래에 현재 GOALS.md 내용이 제공됩니다. 모든 항목이 완료(체크) 상태입니다.\n\n"
    "프로젝트 코드베이스를 분석하여 **다음 단계로 수행할 새로운 개선/기능 항목**을 식별하세요.\n\n"
    "규칙:\n"
    "1. 이미 완료된 항목을 다시 생성하지 마세요.\n"
    "2. 3~10개의 새 항목을 P0(필수)와 P1(개선)으로 구분하여 출력하세요.\n"
    "3. 출력 형식은 반드시 아래 마크다운 체크박스 형식을 사용하세요:\n\n"
    "```\n"
    "## P0\n"
    "- [ ] 항목 설명\n"
    "- [ ] 항목 설명\n\n"
    "## P1\n"
    "- [ ] 항목 설명\n"
    "```\n\n"
    "4. 각 항목은 구체적이고 실행 가능해야 합니다.\n"
    "5. 프로젝트의 현재 상태를 파악하여 실질적으로 가치 있는 작업만 제안하세요.\n"
    "6. 기존 기능 강화, 성능 개선, 코드 품질, 테스트 커버리지, 문서화 등을 고려하세요.\n"
)


def build_goals_refresh_prompt(goals_text: str) -> str:
    """Combine current GOALS.md text with the refresh prompt for LLM."""
    header = "=== 현재 GOALS.md (모든 항목 완료됨) ===\n"
    if goals_text.strip():
        header += goals_text.strip() + "\n"
    else:
        header += "(비어 있음)\n"
    header += "\n=== 지시사항 ===\n"
    return header + GOALS_REFRESH_PROMPT


def parse_and_append_refreshed_goals(repo: Path, llm_output: str) -> Dict[str, Any]:
    """Parse LLM output for new goal items and append them to GOALS.md.

    Extracts ``- [ ] ...`` lines, categorises by P0/P1 headers, appends to
    GOALS.md with an auto-refresh separator comment.

    Returns:
        {"appended": bool, "p0_count": int, "p1_count": int}
    """
    try:
        return _parse_and_append_refreshed_goals_inner(repo, llm_output)
    except Exception:
        return {"appended": False, "p0_count": 0, "p1_count": 0}


def _parse_and_append_refreshed_goals_inner(repo: Path, llm_output: str) -> Dict[str, Any]:
    """Inner implementation (may raise)."""
    if not llm_output or not llm_output.strip():
        return {"appended": False, "p0_count": 0, "p1_count": 0}

    lines = llm_output.splitlines()
    p0_items: list[str] = []
    p1_items: list[str] = []
    current_section: Optional[str] = None

    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()

        # Detect section headers
        if re.match(r'^#{1,3}\s+p0\b', lower):
            current_section = "p0"
            continue
        elif re.match(r'^#{1,3}\s+p1\b', lower):
            current_section = "p1"
            continue
        elif stripped.startswith('#'):
            # Other headers reset section (could be noise)
            continue

        # Extract unchecked checkbox items
        m = re.match(r'^\s*-\s*\[\s\]\s+(.+)$', stripped)
        if m:
            item_text = m.group(1).strip()
            if not item_text:
                continue
            if current_section == "p1":
                p1_items.append(item_text)
            else:
                # Default to P0 if no section header seen yet
                p0_items.append(item_text)

    total = len(p0_items) + len(p1_items)
    if total == 0:
        return {"appended": False, "p0_count": 0, "p1_count": 0}

    # Build the append block
    timestamp = now_iso()
    # Determine refresh number by counting existing auto-refresh markers
    gp = goals_path(repo)
    existing_text = ""
    if gp.exists():
        try:
            existing_text = gp.read_text(encoding="utf-8-sig", errors="replace")
        except Exception:
            try:
                existing_text = gp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass

    refresh_n = len(re.findall(r'<!-- Auto-Refresh #\d+', existing_text)) + 1

    block_lines: list[str] = [
        "",
        f"<!-- Auto-Refresh #{refresh_n} ({timestamp}) -->",
        "",
    ]
    if p0_items:
        block_lines.append("## P0")
        for item in p0_items:
            block_lines.append(f"- [ ] {item}")
        block_lines.append("")
    if p1_items:
        block_lines.append("## P1")
        for item in p1_items:
            block_lines.append(f"- [ ] {item}")
        block_lines.append("")

    append_text = "\n".join(block_lines)

    # Append to GOALS.md
    gp.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(gp, "a", encoding="utf-8", errors="replace") as f:
            f.write(append_text)
    except Exception:
        return {"appended": False, "p0_count": len(p0_items), "p1_count": len(p1_items)}

    return {"appended": True, "p0_count": len(p0_items), "p1_count": len(p1_items)}


# ---------------------------------------------------------------------------
# GOALS.md checkbox auto-update
# ---------------------------------------------------------------------------


def _read_goals_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return None
    try:
        stat_result = path.stat()
    except Exception:
        return None
    return {
        "text": text,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "mtime_ns": int(getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1_000_000_000))),
        "size": int(stat_result.st_size),
    }


def _goals_snapshot_matches(expected: Dict[str, Any], current: Dict[str, Any]) -> bool:
    return (
        str(expected.get("sha256") or "") == str(current.get("sha256") or "")
        and int(expected.get("mtime_ns") or -1) == int(current.get("mtime_ns") or -1)
        and int(expected.get("size") or -1) == int(current.get("size") or -1)
    )


def update_goals_checkboxes(
    repo: Path,
    done_task_titles: list[str],
    done_task_prompts: list[str] | None = None,
    completion_level: str = "all",
) -> Dict[str, Any]:
    """Auto-check GOALS.md items that match completed task titles/prompts.

    Matching strategy (fuzzy keyword):
      For each unchecked goal item, check if any done task title or prompt
      contains significant keywords from the goal text.

    Returns dict with:
      updated: bool, checked_items: list[str], new_status: completion dict,
      status: explicit write result
    """
    gp = goals_path(repo)
    if not gp.exists():
        return {
            "updated": False,
            "checked_items": [],
            "matched_items": [],
            "new_status": {},
            "status": "missing_file",
            "conflict": False,
            "skipped": True,
            "message": "GOALS.md was missing.",
        }

    original_snapshot = _read_goals_snapshot(gp)
    if original_snapshot is None:
        return {
            "updated": False,
            "checked_items": [],
            "matched_items": [],
            "new_status": {},
            "status": "read_failed",
            "conflict": False,
            "skipped": True,
            "message": "Failed to read GOALS.md.",
        }
    original = str(original_snapshot.get("text") or "")

    # Build search corpus from done tasks
    corpus_parts: list[str] = []
    for t in done_task_titles:
        corpus_parts.append(t.lower().strip())
    for p in (done_task_prompts or []):
        corpus_parts.append(p.lower().strip())
    corpus = " ||| ".join(corpus_parts)

    if not corpus.strip():
        return {
            "updated": False,
            "checked_items": [],
            "matched_items": [],
            "new_status": parse_goals_completion(original, completion_level=completion_level),
            "status": "no_match",
            "conflict": False,
            "skipped": True,
            "message": "No completed task text was available for GOALS auto-check.",
        }

    lines = original.splitlines()
    new_lines: list[str] = []
    checked_items: list[str] = []

    checked_strategies: list[tuple[str, str]] = []  # (item_text, strategy)

    for line in lines:
        # Only process unchecked checkboxes
        m = re.match(r'^(\s*-\s*)\[\s\]\s*(.+)$', line)
        if m:
            prefix = m.group(1)
            item_text = m.group(2).strip()
            matched, strategy = _goal_match_detail(item_text, corpus)
            if matched:
                new_lines.append(f"{prefix}[x] {item_text}")
                checked_items.append(item_text)
                checked_strategies.append((item_text, strategy))
                continue
        new_lines.append(line)

    if not checked_items:
        return {
            "updated": False,
            "checked_items": [],
            "matched_items": [],
            "new_status": parse_goals_completion(original, completion_level=completion_level),
            "status": "no_match",
            "conflict": False,
            "skipped": True,
            "message": "No GOALS checkbox matched the completed task text.",
        }

    updated_text = "\n".join(new_lines)
    # Preserve trailing newline if original had one
    if original.endswith("\n") and not updated_text.endswith("\n"):
        updated_text += "\n"

    current_snapshot = _read_goals_snapshot(gp)
    if current_snapshot is None or not _goals_snapshot_matches(original_snapshot, current_snapshot):
        current_text = str((current_snapshot or {}).get("text") or "")
        eprint(
            "[WARN] GOALS auto-check skipped due to concurrent edit conflict; "
            "preserving the operator/Web version."
        )
        return {
            "updated": False,
            "checked_items": [],
            "matched_items": list(checked_items),
            "new_status": parse_goals_completion(current_text or original, completion_level=completion_level),
            "status": "conflict",
            "conflict": True,
            "skipped": True,
            "message": "GOALS.md changed after auto-check read; the atomic update was skipped.",
        }

    try:
        atomic_write_text(gp, updated_text)
    except Exception as exc:
        eprint(f"[WARN] Failed to update GOALS.md checkboxes: {exc}")
        return {
            "updated": False,
            "checked_items": [],
            "matched_items": list(checked_items),
            "new_status": parse_goals_completion(original, completion_level=completion_level),
            "status": "write_failed",
            "conflict": False,
            "skipped": True,
            "message": f"Failed to write GOALS.md checkboxes: {exc}",
        }

    detail_parts = [f"{t}({s})" for t, s in checked_strategies]
    eprint(f"[GOALS] Auto-checked {len(checked_items)} item(s): {', '.join(detail_parts)}")

    new_status = parse_goals_completion(updated_text, completion_level=completion_level)
    return {
        "updated": True,
        "checked_items": checked_items,
        "matched_items": list(checked_items),
        "new_status": new_status,
        "status": "updated",
        "conflict": False,
        "skipped": False,
        "message": f"Auto-checked {len(checked_items)} GOALS item(s).",
    }


def _goal_match_detail(goal_item: str, corpus: str) -> tuple[bool, str]:
    """Check if a goal item is semantically matched by done task corpus.

    Returns (matched, strategy) where strategy is one of:
      "exact", "korean_phrase", "keyword", "none"

    Strategy:
    1. Check for GOALS: prefix exact match (highest confidence)
    2. Check Korean substring matches (phrase-level, >=80% threshold, min 3 phrases)
    3. Fuzzy keyword matching (word-level, 80% threshold, min 4 keywords)
    """
    goal_lower = goal_item.lower().strip()
    corpus_lower = corpus.lower()

    # --- Strategy 1: exact GOALS: prefix match ---
    # PM is instructed to include "GOALS: {item text}" in task prompts
    if goal_lower in corpus_lower:
        return True, "exact"

    # --- Strategy 2: Korean phrase substring matching ---
    # Extract Korean phrases (2+ chars) and check substring presence
    # Raised threshold to 80% to reduce false positives (was 60%)
    ko_phrases = re.findall(r'[가-힣]{2,}', goal_item)
    if ko_phrases:
        ko_match = sum(1 for p in ko_phrases if p in corpus)
        if len(ko_phrases) >= 3 and ko_match >= max(3, math.ceil(len(ko_phrases) * 0.8)):
            return True, "korean_phrase"

    # --- Strategy 3: mixed keyword matching (strict threshold) ---
    # Raised threshold to 80% with minimum 4 keywords to prevent false auto-check.
    # Common Korean verbs/nouns that appear across many GOALS items are noise.
    noise = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has",
        "been", "are", "was", "were", "will", "can", "not", "all", "but",
        "없음", "있음", "동작", "기능", "정상", "성공", "완료", "추가",
        "항목", "필요", "처리", "사용", "적용", "구현", "수정", "표시",
        "확인", "설정", "변경", "제거", "호출", "검증", "방지",
    }
    words = re.findall(r'[\w가-힣]+', goal_lower)
    keywords = [w for w in words if len(w) >= 2 and w not in noise]

    if not keywords:
        return False, "none"

    match_count = sum(1 for kw in keywords if kw in corpus_lower)
    # Strict: 80% threshold, minimum 4 (was 60%/3)
    threshold = max(4, math.ceil(len(keywords) * 0.8))

    if match_count >= threshold:
        return True, "keyword"
    return False, "none"
