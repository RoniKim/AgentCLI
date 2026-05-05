from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterable

_VALID_STATUSES = frozenset({"ok", "skip", "stop", "fail"})
STAGE_EFFECT_BACKLOG_WRITTEN = "backlog_written"
STAGE_EFFECT_TASKS_RELOAD_REQUIRED = "tasks_reload_required"
STAGE_EFFECTS_BACKLOG_MUTATION = frozenset({STAGE_EFFECT_BACKLOG_WRITTEN, STAGE_EFFECT_TASKS_RELOAD_REQUIRED})
_VALID_EFFECTS = STAGE_EFFECTS_BACKLOG_MUTATION


def _normalize_effects(effects: Iterable[str] | None) -> frozenset[str]:
    if effects is None:
        return frozenset()
    if isinstance(effects, str):
        normalized = frozenset({effects} if effects else ())
    else:
        normalized = frozenset(str(effect).strip() for effect in effects if str(effect).strip())
    invalid = normalized - _VALID_EFFECTS
    if invalid:
        raise ValueError(f"Invalid StageOutcome effects: {sorted(invalid)!r}. Must be chosen from {sorted(_VALID_EFFECTS)!r}")
    return normalized


@dataclass
class StageOutcome:
    """Outcome from a stage execution."""

    status: str  # ok | skip | stop | fail
    rc: int = 0
    reason: str = ""
    detail: str = ""
    effects: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if self.status not in _VALID_STATUSES:
            raise ValueError(f"Invalid StageOutcome status: {self.status!r}. Must be one of {_VALID_STATUSES}")
        self.effects = _normalize_effects(self.effects)

    @staticmethod
    def ok(reason: str = "", detail: str = "", effects: Iterable[str] | None = None) -> "StageOutcome":
        return StageOutcome(status="ok", rc=0, reason=reason, detail=detail, effects=effects or ())

    @staticmethod
    def skip(reason: str = "", detail: str = "", effects: Iterable[str] | None = None) -> "StageOutcome":
        return StageOutcome(status="skip", rc=0, reason=reason, detail=detail, effects=effects or ())

    @staticmethod
    def stop(reason: str = "stop_requested", rc: int = 0, detail: str = "", effects: Iterable[str] | None = None) -> "StageOutcome":
        return StageOutcome(status="stop", rc=rc, reason=reason, detail=detail, effects=effects or ())

    @staticmethod
    def fail(reason: str = "failed", rc: int = 1, detail: str = "", effects: Iterable[str] | None = None) -> "StageOutcome":
        return StageOutcome(status="fail", rc=rc, reason=reason, detail=detail, effects=effects or ())

    def has_effect(self, effect: str) -> bool:
        if effect not in _VALID_EFFECTS:
            raise ValueError(f"Invalid stage effect query: {effect!r}. Must be one of {sorted(_VALID_EFFECTS)!r}")
        return effect in self.effects


class Stage(ABC):
    """Base class for pipeline stages."""

    name: str = "Stage"

    @abstractmethod
    async def run(self, session: Any, cycle_idx: int) -> StageOutcome:  # session: PipelineSession (avoid circular import)
        raise NotImplementedError
