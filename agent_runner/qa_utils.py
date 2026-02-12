"""QA followup utilities shared by Codex and Claude Code backends.

All functions are pure — no closure dependencies.
"""
from __future__ import annotations

import re
from typing import Any

from .utils import hash_prompt


def normalize_followup_prompt(text: str) -> str:
    """Trim and cap a QA follow-up prompt to a safe length."""
    s = str(text or "").strip()
    if len(s) > 1000:
        s = s[:1000].rstrip()
    return s


def extract_qa_followups(text: str, *, max_items: int) -> list[dict[str, Any]]:
    """Parse free-form QA output text into structured followup tasks."""
    items: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[-*•]\s+", s) or re.match(r"^\d+[\.\)]\s+", s):
            s = re.sub(r"^[-*•]\s+", "", s)
            s = re.sub(r"^\d+[\.\)]\s+", "", s)
            if len(s) >= 10:
                items.append(s)
        if len(items) >= max_items:
            break
    tasks: list[dict[str, Any]] = []
    for s in items:
        prompt = normalize_followup_prompt(s)
        if not prompt:
            continue
        tid = f"QA-FU-{hash_prompt(prompt)}"
        title = f"QA Follow-up: {s[:60]}".strip()
        tasks.append({
            "id": tid,
            "title": title,
            "prompt": prompt,
            "files": [],
            "done_when": "QA follow-up addressed and relevant tests/builds pass.",
            "skills": [],
            "skills_rationale": None,
            "depends_on": [],
        })
    return tasks


def followups_from_structured(model: Any, *, max_items: int) -> list[dict[str, Any]]:
    """Extract followup tasks from a structured (Pydantic) QA model."""
    tasks: list[dict[str, Any]] = []
    if not model or not getattr(model, "followups", None):
        return tasks
    for item in list(model.followups)[:max_items]:
        prompt = normalize_followup_prompt(getattr(item, "prompt", ""))
        if not prompt:
            continue
        tid = f"QA-FU-{hash_prompt(prompt)}"
        title = str(getattr(item, "title", "") or f"QA Follow-up: {prompt[:60]}").strip()
        severity = str(getattr(item, "severity", "") or "").strip()
        if severity:
            title = f"[{severity}] {title}"
        files = list(getattr(item, "files", []) or [])
        tasks.append({
            "id": tid,
            "title": title,
            "prompt": prompt,
            "files": files,
            "done_when": "QA follow-up addressed and relevant tests/builds pass.",
            "skills": [],
            "skills_rationale": None,
            "depends_on": [],
        })
    return tasks


def merge_qa_followups(
    base_tasks: list[dict[str, Any]],
    followups: list[dict[str, Any]],
    done_ids: set[str],
) -> list[dict[str, Any]]:
    """Merge QA followup tasks into an existing task list, deduplicating by ID."""
    existing_ids = {str(t.get("id") or "") for t in base_tasks if str(t.get("id") or "")}
    merged = list(base_tasks)
    for t in followups:
        tid = str(t.get("id") or "")
        if not tid or tid in existing_ids or tid in done_ids:
            continue
        merged.append(t)
    return merged
