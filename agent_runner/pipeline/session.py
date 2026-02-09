from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional

from .stages.base import StageOutcome


EnsureBacklogFn = Callable[[], bool]
LoadTasksFn = Callable[[], List[Any]]
AsyncStageFn = Callable[[int], Awaitable[StageOutcome]]


@dataclass
class PipelineSession:
    """Per-cycle session passed into stages.

    This is deliberately backend-agnostic: stages call `pm_phase/dev_phase/qa_phase/security_phase`
    that the backend/session wires to concrete implementations.
    """

    args: argparse.Namespace
    repo: Path
    run_dir: Path
    stop_path: Path

    ensure_backlog: EnsureBacklogFn
    load_tasks: LoadTasksFn

    pm_phase: AsyncStageFn
    dev_phase: AsyncStageFn
    qa_phase: AsyncStageFn
    security_phase: Optional[AsyncStageFn] = None

    # shared mutable data
    data: dict[str, Any] = field(default_factory=dict)

    # populated by manager
    tasks: List[Any] = field(default_factory=list)
    ran_tasks: bool = False
    done_delta: int = 0

    def has_stop(self) -> bool:
        try:
            return self.stop_path.exists()
        except OSError:
            return False

    def ensure_tasks_loaded(self) -> bool:
        if self.tasks:
            return True
        if not self.ensure_backlog():
            return False
        self.tasks = self.load_tasks() or []
        return bool(self.tasks)

    async def security(self, cycle_idx: int) -> StageOutcome:
        if callable(self.security_phase):
            return await self.security_phase(cycle_idx)
        return StageOutcome.skip("security_not_supported")
