from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.pipeline import PipelineManager
from agent_runner.pipeline.session import PipelineSession
from agent_runner.pipeline.stages.base import (
    STAGE_EFFECT_BACKLOG_WRITTEN,
    STAGE_EFFECTS_BACKLOG_MUTATION,
    STAGE_EFFECT_TASKS_RELOAD_REQUIRED,
    Stage,
    StageOutcome,
)
from agent_runner.pipeline.stages.backlog_refiner_stage import BacklogRefinerStage
from agent_runner.state import load_backlog_json, write_backlog_files


def _snapshot_task_ids(tasks: list[object]) -> list[str]:
    out: list[str] = []
    for task in tasks:
        if isinstance(task, str):
            out.append(task)
            continue
        task_id = getattr(task, "id", None)
        out.append(str(task_id).strip() if str(task_id).strip() else str(task))
    return out


def _task(task_id: str, title: str) -> dict[str, object]:
    return {
        "id": task_id,
        "title": title,
        "prompt": f"Implement {task_id}: {title}",
        "files": [],
        "done_when": "done",
        "skills": [],
        "depends_on": [],
    }


def _oversized_task() -> dict[str, object]:
    goal_trace = [
        {"goal_ref": "P0-L10", "goal_text": "Expose PR queue data", "matched_fields": ["title"]},
        {"goal_ref": "P0-L11", "goal_text": "Render PR queue UI", "matched_fields": ["prompt"]},
    ]
    return {
        "id": "T2",
        "title": "Web PR Queue shows diff, QA notes, validation logs, merge preflight, and blocking reasons",
        "prompt": "Implement API, serializer, UI, redaction, tests, and Playwright coverage for PR Queue details.",
        "files": [
            "agent_runner/web.py",
            "web_console/app.js",
            "web_console/styles.css",
            "tests/test_web_console_readonly.py",
            "tests/test_web_console_static.py",
            "tests/web_console_playwright_smoke.py",
        ],
        "done_when": "API returns bounded data; UI renders list/detail; QA notes show; validation logs show; Playwright smoke passes.",
        "skills": [],
        "depends_on": ["T1"],
        "goal_trace": goal_trace,
    }


async def _noop_phase(cycle_idx: int) -> StageOutcome:
    return StageOutcome.ok("noop")


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
        self.seen_tasks.append(_snapshot_task_ids(list(getattr(session, "tasks", []))))
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
        self.seen_tasks.append(_snapshot_task_ids(list(getattr(session, "tasks", []))))
        session.backlog_tasks = list(self.updated_tasks)
        return self.outcome


class _SessionBacklogWritingStage(Stage):
    name = "PL"

    def __init__(self, updated_tasks: list[dict[str, object]], outcome: StageOutcome | None = None) -> None:
        self.updated_tasks = [dict(task) for task in updated_tasks]
        self.outcome = outcome or StageOutcome.ok("backlog_refined")
        self.calls = 0
        self.seen_tasks: list[list[str]] = []
        self.audit_path: Path | None = None

    async def run(self, session, cycle_idx):  # type: ignore[override]
        self.calls += 1
        self.seen_tasks.append(_snapshot_task_ids(list(getattr(session, "tasks", []))))
        self.audit_path = session.write_backlog_tasks(self.updated_tasks, source_stage=self.name, cycle_idx=cycle_idx)
        return self.outcome


class PipelineStageEffectsTests(unittest.TestCase):
    def _make_temp_run_dir(self) -> Path:
        root = Path(__file__).resolve().parents[1] / ".tmp_pipeline_stage_effects"
        root.mkdir(parents=True, exist_ok=True)
        run_dir = root / f"case_{uuid.uuid4().hex[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(run_dir, ignore_errors=True))
        return run_dir

    def _make_real_session(self, run_dir: Path) -> PipelineSession:
        def ensure_backlog() -> bool:
            return (run_dir / "BACKLOG.json").exists()

        def load_tasks() -> list[object]:
            return load_backlog_json(run_dir / "BACKLOG.json")

        return PipelineSession(
            args=argparse.Namespace(),
            repo=run_dir,
            run_dir=run_dir,
            stop_path=run_dir / "STOP",
            ensure_backlog=ensure_backlog,
            load_tasks=load_tasks,
            pm_phase=_noop_phase,
            dev_phase=_noop_phase,
            qa_phase=_noop_phase,
        )

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

    def test_session_write_stage_artifact_writes_inside_run_dir(self) -> None:
        run_dir = self._make_temp_run_dir()
        session = self._make_real_session(run_dir)

        artifact_path = session.write_stage_artifact("artifacts/pl/result.json", {"ok": True, "cycle": 3})

        self.assertEqual(run_dir / "artifacts" / "pl" / "result.json", artifact_path)
        self.assertEqual({"ok": True, "cycle": 3}, json.loads(artifact_path.read_text(encoding="utf-8")))

    def test_session_write_stage_artifact_rejects_path_traversal(self) -> None:
        session = self._make_real_session(self._make_temp_run_dir())

        with self.assertRaisesRegex(ValueError, "Path escapes run_dir"):
            session.write_stage_artifact("../escape.json", {"ok": False})

    def test_session_write_backlog_tasks_updates_files_and_marks_reload_effects(self) -> None:
        run_dir = self._make_temp_run_dir()
        session = self._make_real_session(run_dir)
        session.tasks = ["stale-task"]

        audit_path = session.write_backlog_tasks([_task("T11", "Refined task")], source_stage="PL", cycle_idx=2)

        backlog_payload = json.loads((run_dir / "BACKLOG.json").read_text(encoding="utf-8"))
        self.assertEqual("T11", backlog_payload["tasks"][0]["id"])
        self.assertIn("- [ ] T11 Refined task", (run_dir / "BACKLOG.md").read_text(encoding="utf-8"))
        self.assertTrue(audit_path.exists())
        self.assertEqual(STAGE_EFFECTS_BACKLOG_MUTATION, session.pending_stage_effects())
        self.assertEqual([], session.tasks)

    def test_session_backlog_write_effects_trigger_manager_reload(self) -> None:
        run_dir = self._make_temp_run_dir()
        write_backlog_files(run_dir, [_task("T01", "PM task")])
        session = self._make_real_session(run_dir)
        pm = _StaticStage("PM", StageOutcome.ok("pm_ok"))
        pl = _SessionBacklogWritingStage([_task("T02", "Refined task")])
        dev = _StaticStage("Dev", StageOutcome.ok("dev_ok"))

        result = asyncio.run(PipelineManager([pm, pl, dev]).run_cycle(session, 0, continuous=True))

        self.assertEqual(0, result.rc)
        self.assertEqual([["T01"]], pl.seen_tasks)
        self.assertEqual([["T02"]], dev.seen_tasks)
        self.assertIsNotNone(pl.audit_path)
        self.assertEqual([], sorted(session.pending_stage_effects()))
        self.assertEqual(["T02"], [task.id for task in session.tasks])

    def test_backlog_refiner_noops_for_already_sized_backlog(self) -> None:
        run_dir = self._make_temp_run_dir()
        original = [_task("T1", "Small backend task"), _task("T2", "Small UI task")]
        write_backlog_files(run_dir, original)
        session = self._make_real_session(run_dir)

        outcome = asyncio.run(BacklogRefinerStage().run(session, 0))

        self.assertEqual("ok", outcome.status)
        self.assertEqual("backlog_refiner_noop", outcome.reason)
        self.assertEqual(frozenset(), outcome.effects)
        self.assertEqual(frozenset(), session.pending_stage_effects())
        self.assertEqual(["T1", "T2"], [task.id for task in load_backlog_json(run_dir / "BACKLOG.json")])
        self.assertFalse((run_dir / "PL_OUTPUT_cycle_000.json").exists())

    def test_backlog_refiner_splits_oversized_multi_goals_task_preserving_trace_and_dependencies(self) -> None:
        run_dir = self._make_temp_run_dir()
        oversized = _oversized_task()
        write_backlog_files(run_dir, [_task("T1", "Prepare queue data"), oversized, {**_task("T3", "Consume refined queue"), "depends_on": ["T2"]}])
        session = self._make_real_session(run_dir)

        outcome = asyncio.run(BacklogRefinerStage().run(session, 0))

        self.assertEqual("ok", outcome.status)
        self.assertEqual("backlog_refined", outcome.reason)
        self.assertEqual(STAGE_EFFECTS_BACKLOG_MUTATION, outcome.effects)
        payload = json.loads((run_dir / "BACKLOG.json").read_text(encoding="utf-8"))
        tasks = payload["tasks"]
        by_id = {task["id"]: task for task in tasks}
        self.assertEqual(["T1", "T2a", "T2b", "T2c", "T2d", "T3"], [task["id"] for task in tasks])
        for child_id in ["T2a", "T2b", "T2c", "T2d"]:
            self.assertEqual(oversized["goal_trace"], by_id[child_id]["goal_trace"])
            self.assertEqual("T2", by_id[child_id]["parent_task_id"])
            self.assertEqual("T2", by_id[child_id]["split_from_task_id"])
        self.assertEqual(["T1"], by_id["T2a"]["depends_on"])
        self.assertEqual(["T2a"], by_id["T2b"]["depends_on"])
        self.assertEqual(["T2b"], by_id["T2c"]["depends_on"])
        self.assertEqual(["T2c"], by_id["T2d"]["depends_on"])
        self.assertEqual(["T2d"], by_id["T3"]["depends_on"])
        self.assertTrue((run_dir / "PL_OUTPUT_cycle_000.json").exists())
        self.assertEqual(STAGE_EFFECTS_BACKLOG_MUTATION, session.pending_stage_effects())

    def test_dev_observes_reloaded_refined_task_list_after_pl_runs(self) -> None:
        run_dir = self._make_temp_run_dir()
        write_backlog_files(run_dir, [_task("T1", "Prepare queue data"), _oversized_task()])
        session = self._make_real_session(run_dir)
        pm = _StaticStage("PM", StageOutcome.ok("pm_ok"))
        pl = BacklogRefinerStage()
        dev = _StaticStage("Dev", StageOutcome.ok("dev_ok"))

        result = asyncio.run(PipelineManager([pm, pl, dev]).run_cycle(session, 0, continuous=True))

        self.assertEqual(0, result.rc)
        self.assertEqual([["T1", "T2a", "T2b", "T2c", "T2d"]], dev.seen_tasks)
        self.assertEqual(["T1", "T2a", "T2b", "T2c", "T2d"], [task.id for task in session.tasks])


if __name__ == "__main__":
    unittest.main()
