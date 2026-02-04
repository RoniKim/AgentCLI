from __future__ import annotations

from .base import Stage, StageOutcome


class DevStage(Stage):
    name = "Dev"

    async def run(self, session: object, cycle_idx: int) -> StageOutcome:
        fn = getattr(session, "dev_phase", None)
        if not callable(fn):
            return StageOutcome.fail("dev_not_supported", rc=2)
        return await fn(cycle_idx)
