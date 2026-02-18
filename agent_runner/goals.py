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

import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .utils import eprint, now_iso


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


def parse_goals_completion(goals_text: Optional[str], *,
                           completion_level: str = "all") -> Dict[str, Any]:
    """Parse GOALS.md checkboxes and evaluate completion status.

    Args:
        goals_text: Raw GOALS.md content.
        completion_level: When to declare project_complete.
            "p0"  — P0 all checked (legacy).
            "p1"  — P0 + P1 all checked.
            "all" — Every checkbox checked (default).

    Returns dict with:
      has_goals, p0_total, p0_done, p1_total, p1_done,
      all_total, all_done, p0_complete, p1_complete, project_complete,
      unmet_p0, unmet_p1
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

    # Per-level completion flags
    result["p0_complete"] = (result["p0_total"] == 0) or (result["p0_done"] >= result["p0_total"])
    result["p1_complete"] = (result["p1_total"] == 0) or (result["p1_done"] >= result["p1_total"])

    # Project completion depends on configured level
    level = completion_level.lower().strip() if completion_level else "all"
    if result["all_total"] == 0:
        result["project_complete"] = False
    elif level == "p0":
        result["project_complete"] = result["p0_complete"]
    elif level == "p1":
        result["project_complete"] = result["p0_complete"] and result["p1_complete"]
    else:  # "all" (default)
        result["project_complete"] = result["all_done"] >= result["all_total"]

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
    "**GOALS.md는 최우선 지시사항입니다. 반드시 아래 규칙을 따르세요:**\n\n"
    "1. **미완료 P0 항목이 최고 우선순위입니다.** 모든 태스크는 미완료 P0 항목을 직접 구현해야 합니다.\n"
    "2. **GOALS 외 작업 금지.** GOALS.md에 없는 버그픽스, 리팩토링, 테스트는 생성하지 마세요.\n"
    "   예외: 빌드 실패를 유발하는 긴급 버그만 허용.\n"
    "3. **P0 전부 완료 시에만 P1로 이동.** P0가 남아 있으면 P1 태스크를 생성하지 마세요.\n"
    "4. **태스크 제목에 GOALS 항목 원문을 반드시 포함하세요.**\n"
    "   예: title=\"Dashboard 데이터 최신성 표시 — 각 카드별 N분 전 갱신 타임스탬프\"\n"
    "   이유: 시스템이 키워드 매칭으로 GOALS 체크박스를 자동 업데이트합니다.\n"
    "5. **태스크 prompt 첫 줄에 GOALS 항목을 인용하세요.**\n"
    "   예: prompt=\"GOALS: Dashboard 데이터 최신성 표시 — 각 카드별 N분 전 갱신 타임스탬프\\n\\n구현: ...\"\n"
    "6. GOALS.md 체크박스는 시스템이 자동 업데이트합니다. 직접 수정하지 마세요.\n"
    "7. 새로운 P0/P1 이슈를 발견하면 open_questions에 기재하세요 (태스크로 만들지 마세요).\n"
)


# ---------------------------------------------------------------------------
# GOALS.md auto-refresh — rescuable reasons + decision function
# ---------------------------------------------------------------------------

GOALS_REFRESH_RESCUABLE_REASONS: frozenset[str] = frozenset({
    "project_complete",        # Dev→QA 후 GOALS 전체 완료
    "no_tasks",                # PipelineManager: 백로그 없음/빈 태스크
    "pm_refresh_no_backlog",   # run_dev_loop: PM refresh 후 백로그 없음
})


def should_attempt_goals_refresh(
    repo: Path,
    reason: str,
    goals_refresh_count: int,
    goals_refresh_max: int,
    goals_auto_refresh: bool,
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
    status = parse_goals_completion(goals_text)
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

    Strategy:
    1. Check for GOALS: prefix exact match (highest confidence)
    2. Check Korean substring matches (phrase-level)
    3. Fuzzy keyword matching (word-level, lower threshold)
    """
    goal_lower = goal_item.lower().strip()
    corpus_lower = corpus.lower()

    # --- Strategy 1: exact GOALS: prefix match ---
    # PM is instructed to include "GOALS: {item text}" in task prompts
    if goal_lower in corpus_lower:
        return True

    # --- Strategy 2: Korean phrase substring matching ---
    # Extract Korean phrases (2+ chars) and check substring presence
    ko_phrases = re.findall(r'[가-힣]{2,}', goal_item)
    if ko_phrases:
        ko_match = sum(1 for p in ko_phrases if p in corpus)
        if len(ko_phrases) >= 2 and ko_match >= max(2, len(ko_phrases) // 2):
            return True
        if len(ko_phrases) == 1 and ko_match >= 1:
            return True

    # --- Strategy 3: mixed keyword matching (original, relaxed threshold) ---
    noise = {
        "the", "and", "for", "with", "from", "that", "this", "have", "has",
        "been", "are", "was", "were", "will", "can", "not", "all", "but",
        "없음", "있음", "동작", "기능", "정상", "성공", "완료", "추가",
        "항목", "필요", "처리", "사용", "적용", "구현",
    }
    words = re.findall(r'[\w가-힣]+', goal_lower)
    keywords = [w for w in words if len(w) >= 2 and w not in noise]

    if not keywords:
        return False

    match_count = sum(1 for kw in keywords if kw in corpus_lower)
    # Relaxed: 40% threshold, minimum 2
    threshold = max(2, int(len(keywords) * 0.4))

    return match_count >= threshold
