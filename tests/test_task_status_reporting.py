import json
import os
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.reporting import build_cycle_change_summary, build_qa_validation_report
from agent_runner.task_failures import record_task_failure_state
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
        self.state = {"done": ["T4"], "failed": [], "warnings": []}
        record_task_failure_state(self.state, task_id="T1", reason="build_failed", task_status="blocked_env")
        record_task_failure_state(self.state, task_id="T2", reason="fast_regression_failed", task_status="test_contract_changed")
        record_task_failure_state(self.state, task_id="T3", reason="build_failed", task_status="regression_failed")
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

    def test_validation_reports_preserve_skip_statuses(self) -> None:
        task_specs = [
            ("T1", "Deferred validation", "validation_pending", "Validation was intentionally deferred."),
            ("T2", "Policy skip", "tests_skipped", "Configured policy skipped tests."),
            ("T3", "No tests found", "no_tests_found", "No tests were found."),
        ]
        validation_paths: list[Path] = []
        task_results: list[dict[str, object]] = []
        for index, (task_id, task_title, status, detail) in enumerate(task_specs, start=1):
            attempt_dir = self.run_dir / "tasks" / task_id / "attempt_01"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            validation_path = attempt_dir / "validation.json"
            validation_paths.append(validation_path)
            validation_payload = {
                "schema_version": 1,
                "kind": "qa_validation_attempt",
                "task_id": task_id,
                "task_title": task_title,
                "cycle": 1,
                "step": index,
                "attempt": 1,
                "status": status,
                "validation_status": status,
                "reason": status,
                "detail": detail,
                "summary": detail,
                "artifact_path": validation_path.as_posix(),
            }
            validation_path.write_text(json.dumps(validation_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            task_results.append(
                {
                    "id": task_id,
                    "title": task_title,
                    "status": "done",
                    "validation_artifact": validation_path.as_posix(),
                    "validation_status": status,
                }
            )

        qa_report = build_qa_validation_report(Path.cwd(), self.run_dir)
        cycle_summary = build_cycle_change_summary(
            repo=Path.cwd(),
            run_dir=self.run_dir,
            cycle_idx=1,
            start_head="",
            end_head="",
            task_results=task_results,
        )

        self.assertEqual("no_tests_found", qa_report["status"])
        self.assertEqual([status for _, _, status, _ in task_specs], [item["status"] for item in qa_report["attempts"]])
        self.assertEqual([path.as_posix() for path in validation_paths], [item["artifact_path"] for item in qa_report["attempts"]])

        validation = cycle_summary["validation_summary"]
        self.assertEqual(3, validation["tasks_total"])
        self.assertEqual(3, validation["tasks_done"])
        self.assertEqual(0, validation["passed"])
        self.assertEqual(0, validation["failed"])
        self.assertEqual(3, validation["skipped"])
        self.assertEqual([status for _, _, status, _ in task_specs], [item["status"] for item in cycle_summary["validation_results"]])


if __name__ == "__main__":
    unittest.main()
