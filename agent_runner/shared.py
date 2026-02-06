"""Shared utility functions used by both Codex and Claude Code backends."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .utils import read_text_robust
from .skills.parser import parse_skill_text


def load_json_if_exists(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return default
    return default


def inline_skills_for(role: str, inline_mode: str) -> bool:
    mode = str(inline_mode or "").strip().lower()
    if mode in ("none", ""):
        return False
    if mode == "both":
        return True
    return mode == role.lower()


def format_skill_selection(skill_ids: list[str], skills_by_id: dict[str, Any]) -> str:
    if not skill_ids:
        return "(none)"
    lines: list[str] = []
    missing: list[str] = []
    for sid in skill_ids:
        rec = skills_by_id.get(sid)
        if rec is not None:
            try:
                resolved_path = rec.skill_path.resolve()
            except Exception:
                resolved_path = rec.skill_path
            lines.append(f"- {rec.name} ({sid})")
            lines.append(f"  - root: {rec.source_root}")
            lines.append(f"  - relative_path: {rec.relative_path}")
            lines.append(f"  - resolved_path: {resolved_path}")
        else:
            lines.append(f"- {sid} (missing)")
            missing.append(sid)
    if missing:
        lines.append("Missing skills: " + ", ".join(missing))
    return "\n".join(lines)
