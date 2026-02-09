from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .session import PipelineSession
from .stages.base import Stage, StageOutcome


@dataclass
class CycleResult:
    rc: int
    reason: str
    done_delta: int
    stages: list[dict[str, str | int]]


class PipelineManager:
    """Runs configured stages in order.

    This manager is intentionally small and backend-agnostic.
    It only coordinates stage ordering, and performs generic checks
    for backlog/tasks and "prepared-only" mode.
    """

    def __init__(self, stages: Iterable[Stage]):
        self.stages: List[Stage] = list(stages)

    def _has_stage(self, name: str) -> bool:
        low = name.strip().lower()
        return any((getattr(s, "name", "") or "").strip().lower() == low for s in self.stages)

    async def run_cycle(self, session: PipelineSession, cycle_idx: int, *, continuous: bool) -> CycleResult:
        if session.has_stop():
            return CycleResult(rc=0, reason="stop_file", done_delta=0, stages=[])

        # If PM is disabled, require an existing backlog before any non-PM stage.
        if not self._has_stage("PM"):
            try:
                if not session.ensure_backlog():
                    return CycleResult(rc=1, reason="pm_disabled_no_backlog", done_delta=0, stages=[])
            except Exception as exc:
                return CycleResult(rc=1, reason=f"ensure_backlog_exception: {exc}", done_delta=0, stages=[])

        tasks_checked = False

        stage_results: list[dict[str, str | int]] = []

        for stage in self.stages:
            if session.has_stop():
                return CycleResult(rc=0, reason="stop_file", done_delta=session.done_delta, stages=stage_results)

            stage_name = (getattr(stage, "name", "") or "").strip().lower()

            # Before running any non-PM stage, make sure tasks are available.
            if stage_name != "pm" and not tasks_checked:
                try:
                    if not session.ensure_tasks_loaded():
                        # missing backlog or cannot parse
                        return CycleResult(rc=1, reason="no_tasks", done_delta=0, stages=stage_results)
                except Exception as exc:
                    return CycleResult(rc=1, reason=f"ensure_tasks_exception: {exc}", done_delta=0, stages=stage_results)
                tasks_checked = True

                if not continuous:
                    # behave like legacy: prepare artifacts and stop before executing tasks
                    return CycleResult(rc=0, reason="prepared_only", done_delta=0, stages=stage_results)

            try:
                out: StageOutcome = await stage.run(session, cycle_idx)
            except Exception as exc:
                stage_results.append({"name": getattr(stage, "name", "") or stage.__class__.__name__, "status": "fail", "rc": 1, "reason": f"stage_exception: {exc}"})
                return CycleResult(rc=1, reason="stage_exception", done_delta=getattr(session, 'done_delta', 0), stages=stage_results)
            stage_results.append(
                {
                    "name": getattr(stage, "name", "") or stage.__class__.__name__,
                    "status": out.status,
                    "rc": out.rc,
                    "reason": out.reason or "",
                }
            )

            if out.status == "skip":
                continue
            if out.status == "stop":
                return CycleResult(rc=out.rc, reason=out.reason or "stop", done_delta=session.done_delta, stages=stage_results)
            if out.status == "fail":
                return CycleResult(rc=out.rc, reason=out.reason or "failed", done_delta=session.done_delta, stages=stage_results)

        # PM-only (or stages that never needed tasks): still validate/load backlog so the user can inspect it.
        if not tasks_checked:
            try:
                if not session.ensure_tasks_loaded():
                    return CycleResult(rc=1, reason="no_tasks", done_delta=0, stages=stage_results)
            except Exception as exc:
                return CycleResult(rc=1, reason=f"ensure_tasks_exception: {exc}", done_delta=0, stages=stage_results)
            tasks_checked = True

            if not continuous:
                return CycleResult(rc=0, reason="prepared_only", done_delta=0, stages=stage_results)

        return CycleResult(rc=0, reason="ok", done_delta=session.done_delta, stages=stage_results)
