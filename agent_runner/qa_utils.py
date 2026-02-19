"""QA followup utilities shared by Codex and Claude Code backends.

All functions are pure — no closure dependencies.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .utils import hash_prompt


MIN_FOLLOWUP_PROMPT_LENGTH = 80  # Lowered from 150; Korean text is shorter than English for equivalent detail


def normalize_followup_prompt(text: str) -> str:
    """Trim and cap a QA follow-up prompt to a safe length."""
    s = str(text or "").strip()
    if len(s) > 1000:
        s = s[:1000].rstrip()
    return s


def _parse_bullet_items(text: str, *, max_items: int = 50) -> list[str]:
    """Extract actionable items from free-form text.

    Supports: bullet lists (-, *, •), numbered lists (1. / 1)),
    markdown checklists (- [ ] / - [x]), and heading lines (### ...).
    """
    items: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            continue
        # Markdown checklist: - [ ] item / - [x] item
        m_check = re.match(r"^-\s*\[[ xX]\]\s+(.+)", s)
        if m_check:
            item = m_check.group(1).strip()
            if len(item) >= 10:
                items.append(item)
            if len(items) >= max_items:
                break
            continue
        # Bullet / numbered list
        if re.match(r"^[-*•]\s+", s) or re.match(r"^\d+[\.\)]\s+", s):
            s = re.sub(r"^[-*•]\s+", "", s)
            s = re.sub(r"^\d+[\.\)]\s+", "", s)
            if len(s) >= 10:
                items.append(s)
            if len(items) >= max_items:
                break
            continue
        # Heading line: ### Title
        m_heading = re.match(r"^#{1,4}\s+(.+)", s)
        if m_heading:
            item = m_heading.group(1).strip()
            if len(item) >= 10:
                items.append(item)
            if len(items) >= max_items:
                break
            continue
    return items


def extract_qa_followups(
    text: str, *, max_items: int, run_dir: Path | None = None,
) -> list[dict[str, Any]]:
    """Parse free-form QA output text into structured followup tasks.

    Falls back to reading TEST_PLAN.md if text parsing yields nothing.
    """
    items = _parse_bullet_items(text, max_items=max_items)

    # Fallback: read TEST_PLAN.md if available and text parsing found nothing
    if not items and run_dir:
        test_plan = run_dir / "qa" / "TEST_PLAN.md"
        if test_plan.exists():
            try:
                plan_text = test_plan.read_text(encoding="utf-8", errors="replace")
                items = _parse_bullet_items(plan_text, max_items=max_items)
            except Exception:
                pass

    tasks: list[dict[str, Any]] = []
    skipped = 0
    for s in items[:max_items]:
        prompt = normalize_followup_prompt(s)
        if not prompt:
            continue
        if len(prompt) < MIN_FOLLOWUP_PROMPT_LENGTH:
            skipped += 1
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
            "type": "code_fix",
        })
    if skipped:
        import sys
        print(f"[WARN] Skipped {skipped} QA followup(s): prompt < {MIN_FOLLOWUP_PROMPT_LENGTH} chars", file=sys.stderr)
    return tasks


def followups_from_structured(model: Any, *, max_items: int) -> list[dict[str, Any]]:
    """Extract followup tasks from a structured (Pydantic) QA model."""
    tasks: list[dict[str, Any]] = []
    if not model or not getattr(model, "followups", None):
        return tasks
    skipped = 0
    for item in list(model.followups)[:max_items]:
        prompt = normalize_followup_prompt(getattr(item, "prompt", ""))
        if not prompt:
            continue
        if len(prompt) < MIN_FOLLOWUP_PROMPT_LENGTH:
            skipped += 1
            continue
        tid = f"QA-FU-{hash_prompt(prompt)}"
        title = str(getattr(item, "title", "") or f"QA Follow-up: {prompt[:60]}").strip()
        severity = str(getattr(item, "severity", "") or "").strip()
        if severity:
            title = f"[{severity}] {title}"
        files = list(getattr(item, "files", []) or [])
        followup_type = str(getattr(item, "type", "") or "").strip() or "code_fix"
        tasks.append({
            "id": tid,
            "title": title,
            "prompt": prompt,
            "files": files,
            "done_when": "QA follow-up addressed and relevant tests/builds pass.",
            "skills": [],
            "skills_rationale": None,
            "depends_on": [],
            "type": followup_type,
        })
    if skipped:
        import sys
        print(f"[WARN] Skipped {skipped} structured QA followup(s): prompt < {MIN_FOLLOWUP_PROMPT_LENGTH} chars", file=sys.stderr)
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


def split_followups_by_type(
    followups: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split followups into (code_fix, manual_test) lists."""
    code_fix: list[dict[str, Any]] = []
    manual_test: list[dict[str, Any]] = []
    for f in followups:
        if f.get("type", "code_fix") == "manual_test":
            manual_test.append(f)
        else:
            code_fix.append(f)
    return code_fix, manual_test


def write_manual_checks(
    run_dir: Path,
    cycle_idx: int,
    checks: list[dict[str, Any]],
) -> None:
    """Append manual test items to QA_MANUAL_CHECKS.md."""
    if not checks:
        return
    lines: list[str] = []
    lines.append(f"\n## Cycle {cycle_idx} — Manual Verification Items\n")
    for item in checks:
        severity = ""
        title = str(item.get("title") or "Untitled")
        # Extract severity from title if present (e.g. "[HIGH] title")
        if title.startswith("["):
            end = title.find("]")
            if end > 0:
                severity = title[1:end].strip().upper()
                title = title[end + 1:].strip()  # strip prefix to avoid duplication
        if not severity:
            severity = str(item.get("severity") or "").strip().upper()
        prefix = f"[{severity}] " if severity else ""
        lines.append(f"- [ ] {prefix}{title}")
        files = item.get("files") or []
        if files:
            lines.append(f"  - Files: {', '.join(str(f) for f in files)}")
        prompt = str(item.get("prompt") or "").strip()
        if prompt:
            short = prompt[:200].replace("\n", " ")
            lines.append(f"  - {short}")
    lines.append("")
    md_path = run_dir / "QA_MANUAL_CHECKS.md"
    existing = ""
    if md_path.exists():
        try:
            existing = md_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if not existing.strip():
        existing = "# QA Manual Checks\n\nItems below require human verification (no code change needed).\n"
    content = existing.rstrip() + "\n" + "\n".join(lines)
    md_path.write_text(content, encoding="utf-8", errors="replace")
