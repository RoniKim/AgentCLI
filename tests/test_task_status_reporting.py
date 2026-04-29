import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.reporting import build_cycle_change_summary
from agent_runner.web import _load_backlog_payload


class TaskStatusReportingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_home = os.environ.get("AGENTCLI_HOME")
        self.root = Path.cwd() / f".tmp-task-status-reporting-{uuid.uuid4().hex}"
        self.root.mkdir()
        os.environ["AGENTCLI_HOME"] = str(self.root / "home")
        self.run_dir = self.root / ".AgentCLI" / "agent_runs" / "20260429-120000"
        self.run_dir.mkdir(parents=True)
        tasks = [
            {"id": "T1", "title": "Install dependency", "prompt": "", "files": []},
            {"id": "T2", "title": "Review selector contract", "prompt": "", "files": []},
            {"id": "T3", "title": "Fix build regression", "prompt": "", "files": []},
            {"id": "T4", "title": "Already done", "prompt": "", "files": []},
        ]
        (self.run_dir / "BACKLOG.json").write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
        self.state = {
            "done": ["T4"],
            "failed": [
                {"task": "T1", "reason": "build_failed", "task_status": "blocked_env"},
                {"task": "T2", "reason": "fast_regression_failed", "task_status": "test_contract_changed"},
                {"task": "T3", "reason": "build_failed", "task_status": "regression_failed"},
            ],
            "warnings": [],
        }
        (self.run_dir / "STATE.json").write_text(json.dumps(self.state), encoding="utf-8")

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def test_web_backlog_splits_failure_counts(self) -> None:
        payload = _load_backlog_payload(self.run_dir, self.state)

        self.assertEqual(3, payload["counts"]["failed"])
        self.assertEqual(1, payload["counts"]["blocked_env"])
        self.assertEqual(1, payload["counts"]["review"])
        self.assertEqual(1, payload["counts"]["regressed"])
        self.assertEqual(1, payload["failure_group_counts"]["blocked_env"])
        self.assertEqual(1, payload["failure_group_counts"]["review"])
        self.assertEqual(1, payload["failure_group_counts"]["regression"])

    def test_cycle_change_summary_splits_failure_counts(self) -> None:
        summary = build_cycle_change_summary(
            repo=Path.cwd(),
            run_dir=self.run_dir,
            cycle_idx=0,
            start_head="",
            end_head="",
            task_results=[],
        )
        validation = summary["validation_summary"]

        self.assertEqual(3, validation["tasks_failed"])
        self.assertEqual(1, validation["tasks_blocked_env"])
        self.assertEqual(1, validation["tasks_review"])
        self.assertEqual(1, validation["tasks_regressed"])
        self.assertEqual(1, validation["failure_group_counts"]["blocked_env"])
        self.assertEqual(1, validation["failure_group_counts"]["review"])
        self.assertEqual(1, validation["failure_group_counts"]["regression"])


if __name__ == "__main__":
    unittest.main()
