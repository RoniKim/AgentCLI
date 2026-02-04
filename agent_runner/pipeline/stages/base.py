from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StageOutcome:
    """Outcome from a stage execution."""

    status: str  # ok | skip | stop | fail
    rc: int = 0
    reason: str = ""
    detail: str = ""

    @staticmethod
    def ok(reason: str = "", detail: str = "") -> "StageOutcome":
        return StageOutcome(status="ok", rc=0, reason=reason, detail=detail)

    @staticmethod
    def skip(reason: str = "", detail: str = "") -> "StageOutcome":
        return StageOutcome(status="skip", rc=0, reason=reason, detail=detail)

    @staticmethod
    def stop(reason: str = "stop_requested", rc: int = 0, detail: str = "") -> "StageOutcome":
        return StageOutcome(status="stop", rc=rc, reason=reason, detail=detail)

    @staticmethod
    def fail(reason: str = "failed", rc: int = 1, detail: str = "") -> "StageOutcome":
        return StageOutcome(status="fail", rc=rc, reason=reason, detail=detail)


class Stage(ABC):
    """Base class for pipeline stages."""

    name: str = "Stage"

    @abstractmethod
    async def run(self, session: object, cycle_idx: int) -> StageOutcome:
        raise NotImplementedError
