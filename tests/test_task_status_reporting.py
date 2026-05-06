import json
import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import agent_runner.web as web_module
from agent_runner.reporting import build_cycle_change_summary, build_local_shutdown_report, build_qa_validation_report, write_run_report_artifacts
from agent_runner.task_failures import record_task_failure_state
from agent_runner.web_payloads import build_history_item
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

    def _write_state(self, state: dict[str, object]) -> None:
        self.state = state
        (self.run_dir / "STATE.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _write_reports(self, *, stop_reason: str = "ok") -> dict[str, object]:
        with (
            patch("agent_runner.reporting.write_analyzer_summary_artifacts", return_value={}),
            patch("agent_runner.reporting.write_analyzer_artifacts", return_value={"summary": {}, "artifacts": {}}),
        ):
            return write_run_report_artifacts(
                repo=self.root,
                run_dir=self.run_dir,
                stop_reason=stop_reason,
            )

    def _read_operations_summary(self) -> dict[str, object]:
        return json.loads((self.run_dir / "OPERATIONS_SUMMARY.json").read_text(encoding="utf-8"))

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

    def test_shutdown_reports_split_failure_groups(self) -> None:
        artifacts = self._write_reports(stop_reason="build_failed")
        final_report = artifacts["final_run_report"]
        operations_summary = self._read_operations_summary()
        shutdown_report = build_local_shutdown_report(
            repo=self.root,
            run_dir=self.run_dir,
            reason="build_failed",
        )

        self.assertEqual(1, final_report["tasks"]["tasks_blocked_env"])
        self.assertEqual(1, final_report["tasks"]["tasks_review"])
        self.assertEqual(1, final_report["tasks"]["tasks_regressed"])
        self.assertEqual(1, operations_summary["counts"]["blocked_env"])
        self.assertEqual(1, operations_summary["counts"]["review_required"])
        self.assertEqual(1, operations_summary["counts"]["regression"])
        self.assertIn("- blocked_env_count: 1", shutdown_report)
        self.assertIn("- review_needed_count: 1", shutdown_report)
        self.assertIn("- regression_count: 1", shutdown_report)

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

    def test_write_run_report_artifacts_writes_successful_operations_summary(self) -> None:
        self._write_state({"done": ["T1", "T4"], "failed": [], "warnings": []})

        artifacts = self._write_reports()
        summary = self._read_operations_summary()
        summary_md = (self.run_dir / "OPERATIONS_SUMMARY.md").read_text(encoding="utf-8")
        work_summary_md = (self.run_dir / "WORK_SUMMARY.md").read_text(encoding="utf-8")

        self.assertEqual("queued", summary["status"])
        self.assertEqual(2, summary["counts"]["completed"])
        self.assertEqual(2, summary["counts"]["queued"])
        self.assertEqual(0, summary["counts"]["review_required"])
        self.assertEqual(0, summary["counts"]["blocked_env"])
        self.assertEqual(0, summary["stale_cleanup"]["warning_count"])
        self.assertEqual(0, summary["handle_process_warnings"]["warning_count"])
        self.assertIn("completed: 2", summary_md)
        self.assertIn("queued: 2", summary_md)
        self.assertEqual((self.run_dir / "WORK_SUMMARY.md").as_posix(), artifacts["artifacts"]["work_summary_markdown"])
        self.assertIn("# Work Summary", work_summary_md)
        self.assertIn("done: 2/4", work_summary_md)
        self.assertIn("validation:", work_summary_md)
        self.assertNotIn("raw log", work_summary_md.lower())
        self.assertNotIn("diff --git", work_summary_md)

    def test_operations_summary_counts_review_required_and_blocked_env_and_exposes_history_artifacts(self) -> None:
        state = {"done": ["T4"], "failed": [], "warnings": []}
        record_task_failure_state(state, task_id="T1", reason="build_failed", task_status="blocked_env")
        record_task_failure_state(state, bucket="pending_review", task_id="T2", reason="fast_regression_failed", task_status="test_contract_changed")
        self._write_state(state)

        self._write_reports()
        summary = self._read_operations_summary()
        history_item = build_history_item(web_module, self.root, self.run_dir, branch="main")

        self.assertEqual("needs_attention", summary["status"])
        self.assertEqual(1, summary["counts"]["completed"])
        self.assertEqual(1, summary["counts"]["queued"])
        self.assertEqual(1, summary["counts"]["review_required"])
        self.assertEqual(1, summary["counts"]["blocked_env"])
        self.assertIn("operationsSummary", history_item)
        self.assertEqual(1, history_item["operationsSummary"]["counts"]["review_required"])
        self.assertEqual((self.run_dir / "OPERATIONS_SUMMARY.json").as_posix(), history_item["reportArtifacts"]["operationsSummaryJson"])
        self.assertEqual((self.run_dir / "OPERATIONS_SUMMARY.md").as_posix(), history_item["reportArtifacts"]["operationsSummaryMarkdown"])
        self.assertEqual((self.run_dir / "WORK_SUMMARY.md").as_posix(), history_item["reportArtifacts"]["workSummaryMarkdown"])

    def test_operations_summary_includes_stale_cleanup_warnings(self) -> None:
        self._write_state({"done": ["T4"], "failed": [], "warnings": []})
        cleanup_artifact = {
            "schema_version": 1,
            "status": "applied_cleanup_failed",
            "run_dir": self.run_dir.as_posix(),
            "worktree_dir": (self.root / "generated-worktree").as_posix(),
            "cleanup_path": (self.root / "generated-worktree").as_posix(),
            "cleanup_message": "cleanup failed for generated worktree",
            "cleanup_reconciliation": {
                "artifact_status": "applied_cleanup_failed",
                "blocking_paths": [(self.root / "generated-worktree").as_posix()],
                "residual_directory": True,
                "reconciled": False,
            },
        }
        (self.run_dir / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").write_text(
            json.dumps(cleanup_artifact, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self._write_reports()
        summary = self._read_operations_summary()

        self.assertEqual(1, summary["stale_cleanup"]["warning_count"])
        self.assertEqual("worktree_cleanup_failed", summary["stale_cleanup"]["items"][0]["kind"])
        self.assertIn("cleanup failed", summary["stale_cleanup"]["items"][0]["message"])
        self.assertTrue(
            any("cleanup" in action.lower() for action in summary["next_operator_actions"]),
            summary["next_operator_actions"],
        )

    def test_operations_summary_includes_handle_process_warnings(self) -> None:
        self._write_state({"done": ["T4"], "failed": [], "warnings": []})
        diagnostics_dir = self.run_dir / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        diagnostics_payload = {
            "status": "ok",
            "artifact_path": (diagnostics_dir / "WINDOWS_HANDLE_DIAGNOSTICS.json").as_posix(),
            "source_path": (self.root / "source-diagnostics.jsonl").as_posix(),
            "summary": {
                "warning_count": 1,
                "warning_kinds": ["handle_growth"],
                "latest_handle_growth": 150,
            },
            "warnings": [
                {
                    "kind": "handle_growth",
                    "message": "handle_growth 150 >= 100",
                    "sample": 1,
                    "ts": "2026-05-05T00:01:00Z",
                    "value": 150,
                    "threshold": 100,
                }
            ],
        }
        (diagnostics_dir / "WINDOWS_HANDLE_DIAGNOSTICS.json").write_text(
            json.dumps(diagnostics_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        self._write_reports()
        summary = self._read_operations_summary()

        self.assertEqual(1, summary["handle_process_warnings"]["warning_count"])
        self.assertEqual(["handle_growth"], summary["handle_process_warnings"]["warning_kinds"])
        self.assertEqual("handle_growth", summary["handle_process_warnings"]["items"][0]["kind"])
        self.assertTrue(
            any("windows_handle_diagnostics" in action.lower() for action in summary["next_operator_actions"]),
            summary["next_operator_actions"],
        )


if __name__ == "__main__":
    unittest.main()
