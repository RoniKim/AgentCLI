"""GOALS.md management for project completion tracking.

GOALS.md defines what "done" means for a project.
- P0 (Must-Have): All must be checked for project completion.
- P1 (Should-Have): Nice-to-have; not required for completion.

When GOALS.md is absent, PM auto-generates a draft on the first cycle.
The user reviews/edits, and subsequent cycles converge toward those goals.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .utils import eprint, now_iso


def goals_path(repo: Path) -> Path:
    """Canonical location for project goals."""
    return repo / ".doc" / "GOALS.md"


def read_goals(repo: Path, max_chars: int = 12000) -> Tuple[Optional[Path], Optional[str]]:
    """Read GOALS.md. Returns (path, text) or (None, None) if missing."""
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


def format_goals_block(goals_path_: Optional[Path], goals_text: Optional[str]) -> str:
    """Format goals for PM prompt injection."""
    if not goals_path_ or not goals_text:
        return "(none — GOALS.md가 없습니다. 첫 Cycle에서 자동 생성합니다.)"
    lines = goals_text.strip().splitlines()
    head = lines[:150]
    return f"# GOALS SOURCE: {goals_path_.as_posix()}\n" + "\n".join(head)


def parse_goals_completion(goals_text: Optional[str]) -> Dict[str, Any]:
    """Parse GOALS.md checkboxes and evaluate completion status.

    Returns dict with:
      has_goals, p0_total, p0_done, p1_total, p1_done,
      all_total, all_done, p0_complete, project_complete,
      unmet_p0 (list of unchecked P0 items)
    """
    if not goals_text or not goals_text.strip():
        return {"has_goals": False, "project_complete": False}

    result: Dict[str, Any] = {
        "has_goals": True,
        "p0_total": 0, "p0_done": 0,
        "p1_total": 0, "p1_done": 0,
        "all_total": 0, "all_done": 0,
        "unmet_p0": [],
        "unmet_p1": [],
    }

    current_priority: Optional[str] = None

    for line in goals_text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()

        # Detect priority section headers
        if re.match(r'^##\s+p0\b', lower):
            current_priority = "p0"
            continue
        elif re.match(r'^##\s+p1\b', lower):
            current_priority = "p1"
            continue
        elif re.match(r'^##\s+(p2|p3|completion|criteria|note)', lower):
            current_priority = None
            continue
        elif lower.startswith('## '):
            current_priority = None
            continue

        # Parse checkboxes
        checkbox_done = re.match(r'^\s*-\s*\[x\]', line, re.IGNORECASE)
        checkbox_open = re.match(r'^\s*-\s*\[\s\]', line)

        if checkbox_done or checkbox_open:
            is_done = bool(checkbox_done)
            # Extract item text
            item_text = re.sub(r'^\s*-\s*\[[x ]\]\s*', '', line, flags=re.IGNORECASE).strip()

            result["all_total"] += 1
            if is_done:
                result["all_done"] += 1

            if current_priority == "p0":
                result["p0_total"] += 1
                if is_done:
                    result["p0_done"] += 1
                else:
                    result["unmet_p0"].append(item_text)
            elif current_priority == "p1":
                result["p1_total"] += 1
                if is_done:
                    result["p1_done"] += 1
                else:
                    result["unmet_p1"].append(item_text)

    # P0 complete: either all P0 items are checked, or no P0 items exist (nothing to do)
    result["p0_complete"] = (result["p0_total"] == 0) or (result["p0_done"] >= result["p0_total"])
    result["p1_complete"] = (result["p1_total"] == 0) or (result["p1_done"] >= result["p1_total"])
    # Project is complete when all P0 goals are met (including vacuously true when p0_total=0)
    # However, if there are NO goals at all, we require at least some items to exist
    result["project_complete"] = result["p0_complete"] and result["all_total"] > 0

    return result


def write_completion_status(run_dir: Path, status: Dict[str, Any], *,
                            failed_unresolved: int = 0,
                            stop_reason: str = "") -> Path:
    """Write COMPLETION_STATUS.json to run_dir."""
    import json
    payload = {
        "generated_at": now_iso(),
        "stop_reason": stop_reason,
        "goals": status,
        "failed_tasks_unresolved": failed_unresolved,
        "project_complete": status.get("project_complete", False) and failed_unresolved == 0,
    }
    out = run_dir / "COMPLETION_STATUS.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8", errors="replace")
    return out


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
    "Project Goals가 존재합니다. 백로그 생성 시 다음을 따르세요:\n"
    "1. GOALS.md의 미완료 P0 항목을 우선적으로 태스크로 변환하세요.\n"
    "2. P0 항목이 모두 달성되었으면 P1 항목을 태스크로 변환하세요.\n"
    "3. 새로운 P0/P1 이슈를 발견하면 GOALS.md에 추가하는 지시를 태스크에 포함하세요.\n"
    "4. GOALS.md 체크박스는 시스템이 자동 업데이트합니다. 직접 수정하지 마세요.\n"
)


# ---------------------------------------------------------------------------
# GOALS.md checkbox auto-update
# ---------------------------------------------------------------------------

def update_goals_checkboxes(repo: Path, done_task_titles: list[str],
                            done_task_prompts: list[str] | None = None) -> Dict[str, Any]:
    """Auto-check GOALS.md items that match completed task titles/prompts.

    Matching strategy (fuzzy keyword):
      For each unchecked goal item, check if any done task title or prompt
      contains significant keywords from the goal text.

    Returns dict with:
      updated: bool, checked_items: list[str], new_status: completion dict
    """
    gp = goals_path(repo)
    if not gp.exists():
        return {"updated": False, "checked_items": [], "new_status": {}}

    try:
        original = gp.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return {"updated": False, "checked_items": [], "new_status": {}}

    # Build search corpus from done tasks
    corpus_parts: list[str] = []
    for t in done_task_titles:
        corpus_parts.append(t.lower().strip())
    for p in (done_task_prompts or []):
        corpus_parts.append(p.lower().strip())
    corpus = " ||| ".join(corpus_parts)

    if not corpus.strip():
        return {"updated": False, "checked_items": [], "new_status": {}}

    lines = original.splitlines()
    new_lines: list[str] = []
    checked_items: list[str] = []

    for line in lines:
        # Only process unchecked checkboxes
        m = re.match(r'^(\s*-\s*)\[\s\]\s*(.+)$', line)
        if m:
            prefix = m.group(1)
            item_text = m.group(2).strip()
            if _goal_matches_corpus(item_text, corpus):
                new_lines.append(f"{prefix}[x] {item_text}")
                checked_items.append(item_text)
                continue
        new_lines.append(line)

    if not checked_items:
        return {"updated": False, "checked_items": [], "new_status": parse_goals_completion(original)}

    updated_text = "\n".join(new_lines)
    # Preserve trailing newline if original had one
    if original.endswith("\n") and not updated_text.endswith("\n"):
        updated_text += "\n"

    try:
        gp.write_text(updated_text, encoding="utf-8", errors="replace")
    except Exception as exc:
        eprint(f"[WARN] Failed to update GOALS.md checkboxes: {exc}")
        return {"updated": False, "checked_items": checked_items, "new_status": {}}

    new_status = parse_goals_completion(updated_text)
    return {"updated": True, "checked_items": checked_items, "new_status": new_status}


def _goal_matches_corpus(goal_item: str, corpus: str) -> bool:
    """Check if a goal item is semantically matched by done task corpus.

    Strategy: extract significant keywords (3+ chars) from the goal item
    and check if a threshold of them appear in the corpus.
    """
    # Strip common generic words
    noise = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has",
        "been", "are", "was", "were", "will", "can", "not", "all", "but",
        "없음", "있음", "동작", "기능", "정상", "성공", "완료", "추가",
    }
    # Extract keywords from goal item
    words = re.findall(r'[\w가-힣]+', goal_item.lower())
    keywords = [w for w in words if len(w) >= 3 and w not in noise]

    if not keywords:
        return False

    # Require at least 60% of keywords to match, minimum 2
    match_count = sum(1 for kw in keywords if kw in corpus)
    threshold = max(2, int(len(keywords) * 0.6))

    return match_count >= threshold
