from __future__ import annotations

from .base import Stage, StageOutcome


class PMStage(Stage):
    name = "PM"

    async def run(self, session: object, cycle_idx: int) -> StageOutcome:
        fn = getattr(session, "pm_phase", None)
        if not callable(fn):
            return StageOutcome.fail("pm_not_supported", rc=2)
        return await fn(cycle_idx)
