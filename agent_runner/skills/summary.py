from __future__ import annotations

from typing import Iterable

from .indexer import SkillRecord


def summarize_skills_index_capped(
    records: Iterable[SkillRecord],
    *,
    max_items: int,
    max_chars: int,
) -> str:
    if max_items <= 0 or max_chars <= 0:
        return "(skills summary disabled)"

    lines: list[str] = []
    used_chars = 0
    count = 0
    for record in records:
        if count >= max_items:
            lines.append(f"- (summary truncated after {max_items} skills)")
            break
        desc = (record.description or "").replace("\n", " ").strip()
        line = f"- {record.skill_id} | {record.name} | {desc}" if desc else f"- {record.skill_id} | {record.name}"
        if used_chars + len(line) > max_chars:
            lines.append("- (summary truncated due to char cap)")
            break
        lines.append(line)
        used_chars += len(line)
        count += 1

    return "\n".join(lines) if lines else "(no skills indexed)"
