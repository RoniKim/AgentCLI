import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.pipeline.shared_runtime import select_next_task_with_dependency_checks
from agent_runner.reporting import build_cycle_change_summary
from agent_runner.state import TaskItem
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
