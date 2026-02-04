from __future__ import annotations

from .base import Stage, StageOutcome


class QAStage(Stage):
    name = "QA"

    async def run(self, session: object, cycle_idx: int) -> StageOutcome:
        fn = getattr(session, "qa_phase", None)
        if not callable(fn):
            return StageOutcome.fail("qa_not_supported", rc=2)
        return await fn(cycle_idx)
