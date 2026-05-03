import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent_runner.reporting import write_analyzer_summary_artifacts


class AnalyzerSummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / f".tmp-analyzer-summary-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.run_dir = self.root / ".AgentCLI" / "agent_runs" / "20260503-125328"
        self.run_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_json(self, path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _write_jsonl(self, path: Path, rows: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
            encoding="utf-8",
        )

    def _write_backlog_and_state(
        self,
        *,
        tasks: list[dict[str, object]],
        done: list[str] | None = None,
        failed: list[dict[str, object]] | None = None,
    ) -> None:
        self._write_json(self.run_dir / "BACKLOG.json", {"tasks": tasks})
        self._write_json(
            self.run_dir / "STATE.json",
            {
                "done": list(done or []),
                "failed": list(failed or []),
                "warnings": [],
            },
        )

    def _write_task_validation(
        self,
        task_id: str,
        task_title: str,
        status: str,
        summary: str,
    ) -> None:
        self._write_json(
            self.run_dir / "tasks" / task_id / "attempt_01" / "validation.json",
            {
                "schema_version": 1,
                "kind": "qa_validation_attempt",
                "task_id": task_id,
                "task_title": task_title,
                "status": status,
                "validation_status": status,
                "reason": status,
                "summary": summary,
                "detail": summary,
            },
        )

    def _write_pr_validation(
        self,
        packet_id: str,
        *,
        status: str,
        detail: str,
        task_ids: list[str] | None = None,
    ) -> None:
        self._write_json(
            self.run_dir / "pr_queue_validation" / packet_id / "attempt_01" / "validation.json",
            {
                "schema_version": 1,
                "kind": "pr_queue_validation_attempt",
                "packet_id": packet_id,
                "task_ids": list(task_ids or []),
                "status": status,
                "validation_status": status,
                "reason": status,
                "detail": detail,
                "summary": detail,
            },
        )

    def _write_pr_packet(
        self,
        packet_id: str,
        *,
        validation_status: str,
        merge_status: str = "",
        approval_status: str = "",
        detail: str = "",
        preflight_status: str = "",
    ) -> None:
        payload: dict[str, object] = {
            "packet_id": packet_id,
            "validation_status": validation_status,
        }
        if merge_status:
            payload["merge_status"] = merge_status
            payload["merge_outcome"] = {
                "status": merge_status,
                "detail": detail,
            }
            if preflight_status:
                payload["merge_outcome"]["preflight_status"] = preflight_status
        if approval_status:
            payload["approval_status"] = approval_status
        if detail and "merge_outcome" not in payload:
            payload["detail"] = detail
        self._write_json(self.run_dir / "pr_queue" / f"{packet_id}.json", payload)

    def _write_summary(self) -> dict[str, object]:
        with (
            patch("agent_runner.utils.run_cmd", side_effect=AssertionError("run_cmd should not be used")),
            patch("agent_runner.utils._CodexAppServerClient._rpc", side_effect=AssertionError("backend RPC should not be used")),
        ):
            summary = write_analyzer_summary_artifacts(self.run_dir)
        on_disk = json.loads((self.run_dir / "ANALYZER_SUMMARY.json").read_text(encoding="utf-8"))
        self.assertEqual(summary, on_disk)
        return summary

    def test_successful_run_writes_summary_from_local_artifacts(self) -> None:
        self._write_backlog_and_state(
            tasks=[{"id": "T1", "title": "Ship analyzer", "prompt": "", "files": []}],
            done=["T1"],
        )
        self._write_task_validation("T1", "Ship analyzer", "validation_passed", "All checks passed.")
        self._write_jsonl(
            self.run_dir / "EXPERIENCE_UPDATES.jsonl",
            [{"kind": "pm_hint", "message": "Keep validated tasks small enough for quick review."}],
        )

        summary = self._write_summary()

        self.assertEqual("20260503-125328", summary["run_id"])
        self.assertEqual("1 completed task(s); validation passed.", summary["summary"])
        self.assertEqual([], summary["task_lessons"])
        self.assertEqual([], summary["validation_lessons"])
        self.assertEqual([], summary["merge_hints"])
        self.assertIn("Completed tasks with passing validation are good candidates for merge review.", summary["pm_hints"])
        self.assertIn("Keep validated tasks small enough for quick review.", summary["pm_hints"])
        self.assertEqual([], summary["operator_actions"])

    def test_validation_failed_creates_regression_lessons(self) -> None:
        self._write_backlog_and_state(
            tasks=[{"id": "T2", "title": "Fix regression", "prompt": "", "files": []}],
            failed=[
                {
                    "task": "T2",
                    "reason": "test_failed",
                    "task_status": "regression_failed",
                    "detail": "Smoke test regressed.",
                }
            ],
        )
        self._write_task_validation("T2", "Fix regression", "validation_failed", "1 smoke test failed.")

        summary = self._write_summary()

        self.assertEqual("0 completed task(s); 1 review/failed task(s); validation failed.", summary["summary"])
        self.assertEqual("regression", summary["task_lessons"][0]["kind"])
        self.assertIn("T2 (Fix regression)", summary["task_lessons"][0]["lesson"])
        self.assertEqual("validation_failed", summary["validation_lessons"][0]["kind"])
        self.assertIn("failed validation", summary["validation_lessons"][0]["lesson"])
        self.assertEqual(
            ["Keep regression fixes isolated from new feature work until validation passes."],
            summary["pm_hints"],
        )
        self.assertIn("Inspect the failing validation for T2 (Fix regression) and rerun the affected gate.", summary["operator_actions"])
        self.assertIn(
            "Inspect the failing validation artifact for T2 (Fix regression) and rerun the affected gate.",
            summary["operator_actions"],
        )

    def test_blocked_env_creates_environment_lessons(self) -> None:
        self._write_backlog_and_state(
            tasks=[{"id": "T3", "title": "Restore toolchain", "prompt": "", "files": []}],
            failed=[
                {
                    "task": "T3",
                    "reason": "build_failed",
                    "task_status": "blocked_env",
                    "detail": "python: command not found",
                }
            ],
        )
        self._write_json(
            self.run_dir / "failed_tasks.json",
            {
                "items": [
                    {
                        "task_id": "T3",
                        "title": "Restore toolchain",
                        "task_status": "blocked_env",
                        "reason": "build_failed",
                        "next_action": "Install python and rerun the blocked task.",
                    }
                ]
            },
        )
        self._write_task_validation("T3", "Restore toolchain", "blocked_env", "python: command not found")

        summary = self._write_summary()

        self.assertEqual("0 completed task(s); 1 review/failed task(s); validation blocked environment.", summary["summary"])
        self.assertEqual("env", summary["task_lessons"][0]["kind"])
        self.assertEqual("blocked_env", summary["validation_lessons"][0]["kind"])
        self.assertEqual(
            ["Queue environment and setup fixes separately from feature work until blocked tasks are cleared."],
            summary["pm_hints"],
        )
        self.assertIn("Install python and rerun the blocked task.", summary["operator_actions"])

    def test_no_tests_found_is_preserved_for_review(self) -> None:
        self._write_backlog_and_state(
            tasks=[{"id": "T4", "title": "Build-only cleanup", "prompt": "", "files": []}],
            done=["T4"],
        )
        self._write_task_validation("T4", "Build-only cleanup", "no_tests_found", "No tests were found.")

        summary = self._write_summary()

        self.assertEqual("1 completed task(s); validation no tests found.", summary["summary"])
        self.assertEqual([], summary["task_lessons"])
        self.assertEqual("no_tests_found", summary["validation_lessons"][0]["kind"])
        self.assertEqual(
            ["When a task has no tests, preserve the work for review and schedule explicit coverage."],
            summary["pm_hints"],
        )
        self.assertEqual(
            ["Decide whether T4 (Build-only cleanup) needs new tests or an explicit build-only review before merge."],
            summary["operator_actions"],
        )

    def test_pr_merge_blockers_surface_merge_hints(self) -> None:
        self._write_backlog_and_state(
            tasks=[{"id": "T5", "title": "Queue packet", "prompt": "", "files": []}],
            done=["T5"],
        )
        self._write_pr_validation("pr-001", status="validation_passed", detail="Packet validation passed.", task_ids=["T5"])
        self._write_pr_packet(
            "pr-001",
            validation_status="validation_passed",
            merge_status="conflict",
            detail="Base branch moved and the patch no longer applies cleanly.",
            preflight_status="failed",
        )

        summary = self._write_summary()

        self.assertEqual("1 completed task(s); validation passed; 1 PR merge blocker(s).", summary["summary"])
        self.assertEqual(["PR packet pr-001 is blocked by merge status conflict."], summary["merge_hints"])
        self.assertEqual(
            ["Review PR packet pr-001 and resolve the merge blocker: Base branch moved and the patch no longer applies cleanly."],
            summary["operator_actions"],
        )


if __name__ == "__main__":
    unittest.main()
