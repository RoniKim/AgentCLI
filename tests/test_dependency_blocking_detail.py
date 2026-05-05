import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.backlog_utils import normalize_backlog_tasks
from agent_runner.pipeline.shared_runtime import select_next_task_with_dependency_checks
from agent_runner.reporting import build_cycle_change_summary
from agent_runner.state import TaskItem, load_backlog_json
from agent_runner.web import _load_backlog_payload


class _Logger:
    def __init__(self) -> None:
        self.skips: list[tuple[str, str]] = []

    def skip_event(self, task_id: str, reason: str) -> None:
        self.skips.append((task_id, reason))


class _Metrics:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def event(self, name: str, **kwargs: object) -> None:
        self.events.append((name, kwargs))


class DependencyBlockingDetailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_home = os.environ.get("AGENTCLI_HOME")
        self.root = Path.cwd() / f".tmp-dependency-blocking-{uuid.uuid4().hex}"
        os.environ["AGENTCLI_HOME"] = str(self.root / "home")
        self.run_dir = self.root / ".AgentCLI" / "agent_runs" / "20260430-100000"
        self.run_dir.mkdir(parents=True)
        self.state_path = self.run_dir / "STATE.json"

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def _tasks(self) -> list[TaskItem]:
        return [
            TaskItem(
                id="T1",
                title="Full validation runs on demand",
                prompt="Implement validation.",
                files=[],
                done_when="Validation command exists.",
                skills=[],
                skills_rationale=None,
                depends_on=[],
            ),
            TaskItem(
                id="T2",
                title="Web PR Queue shows blocking reasons",
                prompt="Render blocking reasons.",
                files=[],
                done_when="Web shows blocker detail.",
                skills=[],
                skills_rationale=None,
                depends_on=["T1"],
            ),
        ]

    def _select(
        self,
        tasks: list[TaskItem],
        *,
        state: dict[str, object] | None = None,
        skipped_set: set[str] | None = None,
        step_idx: int = 0,
        total_iterations: int = 4,
        remaining_window_budget: int | None = None,
        metrics: _Metrics | None = None,
    ) -> tuple[TaskItem | None, dict[str, object], list[dict[str, object]], _Metrics]:
        state_obj: dict[str, object] = dict(state or {"done": [], "failed": [], "warnings": []})
        state_obj.setdefault("done", [])
        state_obj.setdefault("failed", [])
        state_obj.setdefault("warnings", [])
        results: list[dict[str, object]] = []
        metrics_obj = metrics or _Metrics()
        next_task = select_next_task_with_dependency_checks(
            tasks=tasks,
            done_set=set(),
            skipped_set=set(skipped_set or set()),
            state=state_obj,
            state_path=self.state_path,
            cycle_idx=0,
            max_consecutive_failures=3,
            task_history_enabled=False,
            count_consecutive_title_failures_fn=lambda _title: 0,
            save_state_fn=lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
            record_history_fn=lambda *args, **kwargs: None,
            logger=_Logger(),
            metrics=metrics_obj,
            eprint_fn=lambda _msg: None,
            task_results=results,
            step_idx=step_idx,
            total_iterations=total_iterations,
            remaining_window_budget=remaining_window_budget,
        )
        return next_task, state_obj, results, metrics_obj

    def test_dependency_failed_records_blocking_task_detail(self) -> None:
        tasks = self._tasks()
        state = {
            "done": [],
            "failed": [
                {
                    "task": "T1",
                    "reason": "fast_regression_failed",
                    "task_status": "regression_failed",
                    "detail": "test_web_console_safety.py failed on stale lock reuse.",
                    "validation_summary": "1 failing safety test",
                }
            ],
            "warnings": [],
        }
        task_results: list[dict[str, object]] = []

        next_task = select_next_task_with_dependency_checks(
            tasks=tasks,
            done_set=set(),
            skipped_set={"T1"},
            state=state,
            state_path=self.state_path,
            cycle_idx=0,
            max_consecutive_failures=3,
            task_history_enabled=False,
            count_consecutive_title_failures_fn=lambda _title: 0,
            save_state_fn=lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
            record_history_fn=lambda *args, **kwargs: None,
            logger=_Logger(),
            metrics=_Metrics(),
            eprint_fn=lambda _msg: None,
            task_results=task_results,
        )

        self.assertIsNone(next_task)
        blocked = state["failed"][-1]
        self.assertEqual("T2", blocked["task"])
        self.assertEqual("dependency_failed", blocked["reason"])
        self.assertEqual("review_required", blocked["task_status"])
        self.assertIn("T1: Full validation runs on demand", blocked["detail"])
        self.assertIn("validation=1 failing safety test", blocked["detail"])
        blockers = blocked["blocked_dependencies"]
        self.assertEqual("T1", blockers[0]["task_id"])
        self.assertEqual("regression_failed", blockers[0]["status"])
        self.assertEqual("fast_regression_failed", blockers[0]["reason"])
        self.assertIn("upstream task T1", blockers[0]["next_action"])
        self.assertEqual("T2", task_results[-1]["id"])
        self.assertEqual(blockers, task_results[-1]["blocked_dependencies"])

    def test_explicit_metadata_survives_load_normalize_and_backlog_payload(self) -> None:
        raw_tasks = [
            {
                "id": "T10",
                "title": "Ship the selector polish",
                "prompt": "Implement the selector polish in the runner and web console.",
                "files": ["agent_runner/cycle.py"],
                "done_when": "Selector polish is shipped.",
                "skills": [],
                "skills_rationale": None,
                "depends_on": ["T1"],
                "effort": "S",
                "priority": "P0",
                "touched_file_globs": ["agent_runner/*.py", "web_console/**/*"],
            }
        ]
        backlog_path = self.run_dir / "BACKLOG.json"
        backlog_path.write_text(json.dumps({"tasks": raw_tasks}, ensure_ascii=False, indent=2), encoding="utf-8")

        loaded = load_backlog_json(backlog_path)
        normalized = normalize_backlog_tasks(raw_tasks, self.run_dir)
        payload = _load_backlog_payload(self.run_dir, {"done": [], "failed": [], "warnings": []})

        self.assertEqual("S", loaded[0].effort)
        self.assertEqual("P0", loaded[0].priority)
        self.assertEqual(["agent_runner/*.py", "web_console/**/*"], loaded[0].touched_file_globs)
        self.assertEqual(["T1"], loaded[0].depends_on)
        self.assertEqual("S", normalized[0]["effort"])
        self.assertEqual("P0", normalized[0]["priority"])
        self.assertEqual(["agent_runner/*.py", "web_console/**/*"], normalized[0]["touched_file_globs"])
        self.assertEqual(["T1"], normalized[0]["depends_on"])
        self.assertEqual("S", payload["items"][0]["effort"])
        self.assertEqual("P0", payload["items"][0]["priority"])
        self.assertEqual(["agent_runner/*.py", "web_console/**/*"], payload["items"][0]["touched_file_globs"])

    def test_inferred_metadata_is_recorded_before_selection(self) -> None:
        backlog_path = self.run_dir / "BACKLOG.json"
        backlog_path.write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "T20",
                            "title": "Fix backlog badge copy",
                            "prompt": "Tighten a single backlog badge label in the web console.",
                            "files": ["web_console/app.js"],
                            "done_when": "The label copy is updated.",
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        tasks = load_backlog_json(backlog_path)
        next_task, _state, _results, metrics = self._select(tasks, total_iterations=3, remaining_window_budget=1)

        self.assertIsNotNone(next_task)
        self.assertEqual("P0", next_task.priority)
        self.assertEqual("M", next_task.effort)
        self.assertEqual(["web_console/app.js"], next_task.touched_file_globs)
        selection_event = next(event for event in metrics.events if event[0] == "task_selection")
        payload = selection_event[1]
        self.assertEqual("T20", payload["selected_task_id"])
        self.assertEqual(1, payload["remaining_window_budget"])
        candidate = payload["ready_candidates"][0]
        self.assertEqual("P0", candidate["priority"])
        self.assertEqual("M", candidate["effort"])
        self.assertEqual(["web_console/app.js"], candidate["touched_file_globs"])

    def test_dependency_blocked_large_task_is_skipped_for_unblocked_small_task(self) -> None:
        tasks = [
            TaskItem(
                id="T0",
                title="Upstream blocker",
                prompt="Finish the missing upstream work.",
                files=["agent_runner/pipeline/shared_runtime.py"],
                done_when="Upstream work exists.",
                skills=[],
                skills_rationale=None,
                depends_on=[],
                effort="L",
                priority="P0",
                touched_file_globs=["agent_runner/pipeline/shared_runtime.py"],
            ),
            TaskItem(
                id="T1",
                title="Large risky downstream task",
                prompt="Consume the upstream work across the runtime and backlog views.",
                files=["agent_runner/cycle.py", "agent_runner/web.py"],
                done_when="Downstream runtime work is complete.",
                skills=[],
                skills_rationale=None,
                depends_on=["T0"],
                effort="L",
                priority="P0",
                touched_file_globs=["agent_runner/cycle.py", "agent_runner/web.py"],
            ),
            TaskItem(
                id="T2",
                title="Small backlog text fix",
                prompt="Adjust one backlog label in the web console.",
                files=["web_console/app.js"],
                done_when="The label is adjusted.",
                skills=[],
                skills_rationale=None,
                depends_on=[],
                effort="S",
                priority="P0",
                touched_file_globs=["web_console/app.js"],
            ),
        ]
        state = {
            "done": [],
            "failed": [
                {
                    "task": "T0",
                    "reason": "fast_regression_failed",
                    "task_status": "regression_failed",
                    "detail": "Blocked upstream validator.",
                    "validation_summary": "1 failing safety test",
                }
            ],
            "warnings": [],
        }

        next_task, state_obj, results, _metrics = self._select(
            tasks,
            state=state,
            skipped_set={"T0"},
            total_iterations=2,
            remaining_window_budget=1,
        )

        self.assertIsNotNone(next_task)
        self.assertEqual("T2", next_task.id)
        blocked = state_obj["failed"][-1]
        self.assertEqual("T1", blocked["task"])
        self.assertEqual("dependency_failed", blocked["reason"])
        self.assertEqual("T1", results[-1]["id"])
        self.assertEqual("dependency_failed", results[-1]["reason"])

    def test_large_tasks_are_deferred_only_when_smaller_unblocked_task_exists(self) -> None:
        large = TaskItem(
            id="T30",
            title="Large risky runtime refactor",
            prompt="Refactor the scheduler across cycle, state, and shared runtime.",
            files=["agent_runner/cycle.py", "agent_runner/state.py", "agent_runner/pipeline/shared_runtime.py"],
            done_when="The scheduler refactor is complete.",
            skills=[],
            skills_rationale=None,
            depends_on=[],
            effort="L",
            priority="P0",
            touched_file_globs=["agent_runner/**/*.py"],
        )
        small = TaskItem(
            id="T31",
            title="Small unblocked selector test",
            prompt="Add a focused selector test.",
            files=["tests/test_dependency_blocking_detail.py"],
            done_when="The focused test exists.",
            skills=[],
            skills_rationale=None,
            depends_on=[],
            effort="S",
            priority="P0",
            touched_file_globs=["tests/test_dependency_blocking_detail.py"],
        )
        blocked_small = TaskItem(
            id="T32",
            title="Blocked small selector test",
            prompt="Add a focused selector test after the upstream helper lands.",
            files=["tests/test_dependency_blocking_detail.py"],
            done_when="The focused test exists.",
            skills=[],
            skills_rationale=None,
            depends_on=["T99"],
            effort="S",
            priority="P0",
            touched_file_globs=["tests/test_dependency_blocking_detail.py"],
        )

        selected_with_small, _state_a, _results_a, _metrics_a = self._select(
            [large, small],
            total_iterations=2,
            remaining_window_budget=1,
        )
        selected_without_eligible_small, _state_b, _results_b, _metrics_b = self._select(
            [large, blocked_small],
            total_iterations=2,
            remaining_window_budget=1,
        )

        self.assertIsNotNone(selected_with_small)
        self.assertEqual("T31", selected_with_small.id)
        self.assertIsNotNone(selected_without_eligible_small)
        self.assertEqual("T30", selected_without_eligible_small.id)

    def test_reports_and_web_payload_preserve_dependency_blockers(self) -> None:
        tasks = self._tasks()
        (self.run_dir / "BACKLOG.json").write_text(
            json.dumps({"tasks": [task.__dict__ for task in tasks]}),
            encoding="utf-8",
        )
        blockers = [
            {
                "task_id": "T1",
                "title": "Full validation runs on demand",
                "status": "regression_failed",
                "reason": "fast_regression_failed",
                "validation_summary": "1 failing safety test",
                "next_action": "Resolve or review upstream task T1, then retry the dependent task.",
            }
        ]
        state = {
            "done": [],
            "failed": [
                {
                    "task": "T2",
                    "reason": "dependency_failed",
                    "task_status": "review_required",
                    "detail": "Blocked by unresolved task dependencies.",
                    "blocked_dependencies": blockers,
                    "next_action": "Resolve blocking upstream tasks before retrying this task.",
                }
            ],
            "warnings": [],
        }
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

        summary = build_cycle_change_summary(
            repo=self.root,
            run_dir=self.run_dir,
            cycle_idx=0,
            start_head="",
            end_head="",
            task_results=[],
        )
        payload = _load_backlog_payload(self.run_dir, state)

        failed_item = summary["failed_tasks"]["items"][0]
        self.assertEqual(blockers, failed_item["blocked_dependencies"])
        t2 = next(item for item in payload["items"] if item["id"] == "T2")
        self.assertEqual(blockers, t2["failure"]["blocked_dependencies"])
        self.assertEqual("Resolve blocking upstream tasks before retrying this task.", t2["failure"]["next_action"])


if __name__ == "__main__":
    unittest.main()
