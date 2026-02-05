from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ..utils import read_text_robust
from .indexer import SkillRecord
from .parser import parse_skill_text

DEFAULT_TOTAL_CHAR_CAP = 8000


def _strip_frontmatter(lines: list[str]) -> list[str]:
    if not lines:
        return []
    if lines[0].strip() != "---":
        return lines
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[idx + 1 :]
    return lines


def _excerpt_lines(text: str, max_lines: int) -> list[str]:
    lines = text.splitlines()
    body = _strip_frontmatter(lines)
    return body[:max_lines]


def build_skills_context(
    records: Iterable[SkillRecord],
    *,
    max_excerpt_lines: int,
    total_char_cap: int = DEFAULT_TOTAL_CHAR_CAP,
    include_excerpts: bool,
) -> str:
    if not records:
        return "(no skills selected)"

    chunks: list[str] = []
    used_chars = 0
    for record in records:
        header = f"- {record.name} ({record.skill_id})"
        desc = f"  - {record.description}" if record.description else ""
        block_lines = [header]
        if desc:
            block_lines.append(desc)

        if include_excerpts and max_excerpt_lines > 0:
            text, status = read_text_robust(record.skill_path)
            if status == "ok":
                meta = parse_skill_text(text, fallback_name=record.name)
                excerpt = _excerpt_lines("\n".join(meta.body_lines), max_excerpt_lines)
                if excerpt:
                    block_lines.append("  - excerpt:")
                    block_lines.extend([f"    {line}" for line in excerpt])

        block = "\n".join(block_lines)
        if used_chars + len(block) > total_char_cap:
            chunks.append("- (skill excerpts truncated due to cap)")
            break
        chunks.append(block)
        used_chars += len(block)

    return "\n".join(chunks)
