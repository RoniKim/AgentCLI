from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    tags: list[str]
    body_lines: list[str]


def _split_frontmatter(lines: list[str]) -> tuple[list[str], list[str]]:
    if not lines:
        return [], []
    if lines[0].strip() != "---":
        return [], lines
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            return lines[1:idx], lines[idx + 1 :]
    return [], lines


def _parse_tags_inline(value: str) -> list[str]:
    s = value.strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]
    return [p.strip().strip('"').strip("'") for p in s.split(",") if p.strip()]


def _parse_frontmatter(lines: Iterable[str]) -> dict[str, object]:
    out: dict[str, object] = {}
    current_key: Optional[str] = None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("-") and current_key:
            item = line[1:].strip()
            if item:
                prev = out.get(current_key, [])
                if not isinstance(prev, list):
                    prev = []
                prev.append(item)
                out[current_key] = prev
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip().lower()
            val = val.strip()
            current_key = key
            if key == "tags":
                if val:
                    out[key] = _parse_tags_inline(val)
                else:
                    out[key] = []
            else:
                out[key] = val
            continue
    return out


def _first_heading(body_lines: list[str]) -> str:
    for line in body_lines:
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()
    return ""


def _first_nonempty(body_lines: list[str]) -> str:
    for line in body_lines:
        s = line.strip()
        if s:
            return s
    return ""


def parse_skill_text(text: str, *, fallback_name: str) -> SkillMetadata:
    lines = (text or "").splitlines()
    fm_lines, body_lines = _split_frontmatter(lines)
    frontmatter = _parse_frontmatter(fm_lines)

    name = str(frontmatter.get("name") or frontmatter.get("title") or "").strip()
    if not name:
        name = fallback_name

    description = str(frontmatter.get("description") or frontmatter.get("desc") or "").strip()
    if not description:
        description = _first_heading(body_lines) or _first_nonempty(body_lines)

    tags_val = frontmatter.get("tags")
    tags: list[str] = []
    if isinstance(tags_val, list):
        tags = [str(t).strip() for t in tags_val if str(t).strip()]
    elif isinstance(tags_val, str):
        tags = _parse_tags_inline(tags_val)

    return SkillMetadata(
        name=name,
        description=description,
        tags=tags,
        body_lines=body_lines,
    )
