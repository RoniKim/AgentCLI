from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from .indexer import SkillRecord


@dataclass(frozen=True)
class SkillMatch:
    skill_id: str
    name: str
    score: float


def _ratio(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def suggest_skills(missing_skill_id: str, records: Iterable[SkillRecord], *, max_results: int = 3) -> list[SkillMatch]:
    target = (missing_skill_id or "").strip().lower()
    scored: list[SkillMatch] = []
    for record in records:
        candidates = [
            _ratio(target, record.skill_id.lower()),
            _ratio(target, record.name.lower()),
            _ratio(target, record.relative_path.lower()),
        ]
        score = max(candidates)
        scored.append(SkillMatch(skill_id=record.skill_id, name=record.name, score=score))
    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:max_results]
