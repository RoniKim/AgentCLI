from __future__ import annotations

import asyncio
import unittest

from agent_runner.pipeline import PipelineManager
from agent_runner.pipeline.stages.base import (
    STAGE_EFFECT_BACKLOG_WRITTEN,
    STAGE_EFFECT_TASKS_RELOAD_REQUIRED,
    Stage,
    StageOutcome,
)


class _FakeSession:
    def __init__(self, tasks: list[str]) -> None:
        self.backlog_tasks = list(tasks)
        self.tasks: list[str] = []
        self.done_delta = 0
        self.ensure_backlog_calls = 0
        self.ensure_tasks_loaded_calls = 0
        self.reload_tasks_calls = 0
        self.load_tasks_calls = 0
        self.invalidate_tasks_calls = 0

    def has_stop(self) -> bool:
        return False

    def ensure_backlog(self) -> bool:
        self.ensure_backlog_calls += 1
        return True

    def load_tasks(self) -> list[str]:
        self.load_tasks_calls += 1
        return list(self.backlog_tasks)

    def ensure_tasks_loaded(self) -> bool:
        self.ensure_tasks_loaded_calls += 1
        if self.tasks:
            return True
        if not self.ensure_backlog():
            return False
        self.tasks = self.load_tasks() or []
        return bool(self.tasks)

    def invalidate_tasks(self) -> None:
        self.invalidate_tasks_calls += 1
        self.tasks = []

    def reload_tasks(self) -> bool:
        self.reload_tasks_calls += 1
        self.invalidate_tasks()
        if not self.ensure_backlog():
            return False
        self.tasks = self.load_tasks() or []
        return bool(self.tasks)


class _StaticStage(Stage):
    def __init__(self, name: str, outcome: StageOutcome) -> None:
        self.name = name
        self.outcome = outcome
        self.calls = 0
        self.seen_tasks: list[list[str]] = []

    async def run(self, session, cycle_idx):  # type: ignore[override]
        self.calls += 1
        self.seen_tasks.append(list(getattr(session, "tasks", [])))
        return self.outcome


class _BacklogWritingStage(_StaticStage):
    def __init__(self, updated_tasks: list[str], outcome: StageOutcome | None = None) -> None:
        super().__init__(
            "PL",
            outcome or StageOutcome.ok(
                "backlog_refined",
                effects={STAGE_EFFECT_BACKLOG_WRITTEN, STAGE_EFFECT_TASKS_RELOAD_REQUIRED},
            ),
        )
        self.updated_tasks = list(updated_tasks)

    async def run(self, session, cycle_idx):  # type: ignore[override]
        self.calls += 1
        self.seen_tasks.append(list(getattr(session, "tasks", [])))
        session.backlog_tasks = list(self.updated_tasks)
        return self.outcome


class PipelineStageEffectsTests(unittest.TestCase):
    def test_stage_outcome_validates_declared_effects(self) -> None:
        outcome = StageOutcome.ok(effects=STAGE_EFFECT_BACKLOG_WRITTEN)

        self.assertEqual(frozenset({STAGE_EFFECT_BACKLOG_WRITTEN}), outcome.effects)
        self.assertTrue(outcome.has_effect(STAGE_EFFECT_BACKLOG_WRITTEN))

        with self.assertRaises(ValueError):
            StageOutcome.ok(effects={"not_a_real_effect"})

    def test_backlog_writing_stage_reloads_tasks_before_dev(self) -> None:
        session = _FakeSession(["pm-task"])
        pm = _StaticStage("PM", StageOutcome.ok("pm_ok"))
        refiner = _BacklogWritingStage(["refined-task"])
        dev = _StaticStage("Dev", StageOutcome.ok("dev_ok"))

        result = asyncio.run(PipelineManager([pm, refiner, dev]).run_cycle(session, 0, continuous=True))

        self.assertEqual(0, result.rc)
        self.assertEqual([["pm-task"]], refiner.seen_tasks)
        self.assertEqual([["refined-task"]], dev.seen_tasks)
        self.assertEqual(1, session.ensure_tasks_loaded_calls)
        self.assertEqual(1, session.reload_tasks_calls)
        self.assertEqual(2, session.load_tasks_calls)

    def test_non_mutating_stage_does_not_reload_tasks(self) -> None:
        session = _FakeSession(["pm-task"])
        pm = _StaticStage("PM", StageOutcome.ok("pm_ok"))
        pl = _StaticStage("PL", StageOutcome.ok("pl_ok"))
        dev = _StaticStage("Dev", StageOutcome.ok("dev_ok"))

        result = asyncio.run(PipelineManager([pm, pl, dev]).run_cycle(session, 0, continuous=True))

        self.assertEqual(0, result.rc)
        self.assertEqual([["pm-task"]], pl.seen_tasks)
        self.assertEqual([["pm-task"]], dev.seen_tasks)
        self.assertEqual(1, session.ensure_tasks_loaded_calls)
        self.assertEqual(0, session.reload_tasks_calls)
        self.assertEqual(1, session.load_tasks_calls)

    def test_stop_outcome_short_circuits_after_declared_backlog_mutation(self) -> None:
        session = _FakeSession(["pm-task"])
        stop_outcome = StageOutcome.stop(
            "stop_requested",
            effects={STAGE_EFFECT_BACKLOG_WRITTEN, STAGE_EFFECT_TASKS_RELOAD_REQUIRED},
        )
        pm = _StaticStage("PM", StageOutcome.ok("pm_ok"))
        pl = _BacklogWritingStage(["refined-task"], outcome=stop_outcome)
        dev = _StaticStage("Dev", StageOutcome.ok("dev_ok"))

        result = asyncio.run(PipelineManager([pm, pl, dev]).run_cycle(session, 0, continuous=True))

        self.assertEqual(0, result.rc)
        self.assertEqual("stop_requested", result.reason)
        self.assertEqual(0, dev.calls)
        self.assertEqual(0, session.reload_tasks_calls)

    def test_fail_outcome_short_circuits_after_declared_backlog_mutation(self) -> None:
        session = _FakeSession(["pm-task"])
        fail_outcome = StageOutcome.fail(
            "pl_failed",
            rc=7,
            effects={STAGE_EFFECT_BACKLOG_WRITTEN, STAGE_EFFECT_TASKS_RELOAD_REQUIRED},
        )
        pm = _StaticStage("PM", StageOutcome.ok("pm_ok"))
        pl = _BacklogWritingStage(["refined-task"], outcome=fail_outcome)
        dev = _StaticStage("Dev", StageOutcome.ok("dev_ok"))

        result = asyncio.run(PipelineManager([pm, pl, dev]).run_cycle(session, 0, continuous=True))

        self.assertEqual(7, result.rc)
        self.assertEqual("pl_failed", result.reason)
        self.assertEqual(0, dev.calls)
        self.assertEqual(0, session.reload_tasks_calls)


if __name__ == "__main__":
    unittest.main()
