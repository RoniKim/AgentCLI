from __future__ import annotations

from .base import Stage, StageOutcome


class SecurityStage(Stage):
    """Optional stage.

    Backends can implement `security_phase` on the session.
    If not provided, this stage will be skipped.
    """

    name = "Security"

    async def run(self, session: object, cycle_idx: int) -> StageOutcome:
        fn = getattr(session, "security_phase", None)
        if not callable(fn):
            return StageOutcome.skip("security_not_supported")
        return await fn(cycle_idx)
