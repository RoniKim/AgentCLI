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

    def _build_blocks(include_excerpt: bool) -> list[str]:
        blocks: list[str] = []
        for record in records:
            header = f"- {record.name} ({record.skill_id})"
            desc = f"  - {record.description}" if record.description else ""
            block_lines = [header]
            if desc:
                block_lines.append(desc)

            if include_excerpt and max_excerpt_lines > 0:
                text, status = read_text_robust(record.skill_path)
                if status == "ok":
                    meta = parse_skill_text(text, fallback_name=record.name)
                    excerpt = _excerpt_lines("\n".join(meta.body_lines), max_excerpt_lines)
                    if excerpt:
                        block_lines.append("  - excerpt:")
                        block_lines.extend([f"    {line}" for line in excerpt])
            blocks.append("\n".join(block_lines))
        return blocks

    def _join_with_cap(blocks: list[str], cap: int) -> tuple[str, bool]:
        if cap <= 0:
            return "\n".join(blocks), False
        chunks: list[str] = []
        used_chars = 0
        for block in blocks:
            if used_chars + len(block) > cap:
                chunks.append("- (skill context truncated due to cap)")
                return "\n".join(chunks), True
            chunks.append(block)
            used_chars += len(block)
        return "\n".join(chunks), False

    primary_blocks = _build_blocks(include_excerpts)
    joined, truncated = _join_with_cap(primary_blocks, total_char_cap)
    if not truncated:
        return joined

    if include_excerpts:
        compact_blocks = _build_blocks(False)
        compact_joined, compact_truncated = _join_with_cap(compact_blocks, total_char_cap)
        if not compact_truncated:
            return compact_joined + "\n- (switched to compact mode due to cap)"
        return compact_joined

    return joined
