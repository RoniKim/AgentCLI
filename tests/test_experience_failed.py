import json
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

from agent_runner.config import default_database_path
from agent_runner.experience import query_task_experiences, record_task_experience
from agent_runner.pipeline.shared_runtime import select_next_task_with_dependency_checks
from agent_runner.state import TaskItem


class _Logger:
    def skip_event(self, _task_id: str, _reason: str) -> None:
        pass


class _Metrics:
    def event(self, _name: str, **_kwargs: object) -> None:
        pass


class ExperienceFailedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_home = os.environ.get("AGENTCLI_HOME")
        self.root = Path.cwd() / f".tmp-experience-failed-{uuid.uuid4().hex}"
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True)
        os.environ["AGENTCLI_HOME"] = str(self.root / "home")
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260503-125328"
        self.run_dir.mkdir(parents=True)
        self.state_path = self.run_dir / "STATE.json"

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def _raw_payload_text(self, *, task_id: str) -> str:
        conn = sqlite3.connect(str(default_database_path(self.repo)))
        try:
            row = conn.execute(
                "SELECT experience_payload FROM task_experiences WHERE task_id = ? ORDER BY id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            return str(row[0]) if row else ""
        finally:
            conn.close()

    def test_dependency_failed_experience_records_blockers_without_raw_logs(self) -> None:
        tasks = [
            TaskItem(
                id="T1",
                title="Upstream validator",
                prompt="Implement upstream validator.",
                files=[],
                done_when="Validator exists.",
                skills=[],
                skills_rationale=None,
                depends_on=[],
            ),
            TaskItem(
                id="T2",
                title="Downstream consumer",
                prompt="Implement downstream consumer.",
                files=[],
                done_when="Consumer exists.",
                skills=[],
                skills_rationale=None,
                depends_on=["T1"],
            ),
        ]
        state = {
            "done": [],
            "failed": [
                {
                    "task": "T1",
                    "reason": "fast_regression_failed",
                    "task_status": "regression_failed",
                    "detail": "Assistant: raw backend transcript\nshell_command: pytest tests/test_web.py",
                    "validation_summary": "1 failing safety test",
                }
            ],
            "warnings": [],
        }
        task_results: list[dict[str, object]] = []

        def _record_experience(**kwargs: object) -> None:
            record_task_experience(
                self.repo,
                run_id=self.run_dir.name,
                backend="codex",
                **kwargs,
            )

        next_task = select_next_task_with_dependency_checks(
            tasks=tasks,
            done_set=set(),
            skipped_set={"T1"},
            state=state,
            state_path=self.state_path,
            cycle_idx=3,
            max_consecutive_failures=3,
            task_history_enabled=False,
            count_consecutive_title_failures_fn=lambda _title: 0,
            save_state_fn=lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
            record_history_fn=lambda *args, **kwargs: None,
            logger=_Logger(),
            metrics=_Metrics(),
            eprint_fn=lambda _msg: None,
            task_results=task_results,
            step_idx=2,
            record_task_experience_fn=_record_experience,
        )

        self.assertIsNone(next_task)
        row = query_task_experiences(self.repo, task_id="T2", max_items=1)[0]
        self.assertEqual("dependency_failed", row["reason"])
        self.assertEqual("review_required", row["task_status"])
        self.assertEqual("not_run_dependency_blocked", row["outcome_action"])
        self.assertEqual(3, row["cycle_idx"])
        self.assertEqual(2, row["step_idx"])
        self.assertEqual(0, row["attempt"])
        blockers = row["payload"]["blocked_dependencies"]
        self.assertEqual("T1", blockers[0]["task_id"])
        self.assertEqual("1 failing safety test", blockers[0]["validation_summary"])
        raw_payload = self._raw_payload_text(task_id="T2")
        self.assertNotIn("Assistant:", raw_payload)
        self.assertNotIn("shell_command", raw_payload)

    def test_validation_failed_experience_records_artifact_pointers_without_log_text(self) -> None:
        validation_json = (self.run_dir / "tasks" / "c004_s002_T8" / "attempt_01" / "validation.json").as_posix()
        test_log = (self.run_dir / "tasks" / "c004_s002_T8" / "attempt_01" / "test.txt").as_posix()
        record_task_experience(
            self.repo,
            run_id=self.run_dir.name,
            backend="codex",
            task_id="T8",
            title="Validation failure capture",
            status="failed",
            reason="test_failed",
            task_status="regression_failed",
            cycle_idx=4,
            step_idx=2,
            attempt=2,
            max_attempts=3,
            validation_status="validation_failed",
            validation_summary="2 smoke tests failed",
            validations=[
                {
                    "gate": "test",
                    "status": "failed",
                    "rc": 1,
                    "summary": "2 smoke tests failed",
                    "artifact_path": test_log,
                    "cmd": ["python", "-m", "pytest"],
                }
            ],
            artifact_pointers=[validation_json],
            outcome_action="discarded",
            detail="Assistant: raw backend transcript\nstderr: AssertionError at line 41",
        )

        row = query_task_experiences(self.repo, task_id="T8", max_items=1)[0]
        payload = row["payload"]
        self.assertEqual("validation_failed", row["validation_status"])
        self.assertEqual("2 smoke tests failed", payload["validation"]["summary"])
        self.assertEqual([validation_json, test_log], payload["artifact_pointers"])
        self.assertEqual(2, row["attempt"])
        self.assertEqual(3, row["max_attempts"])
        self.assertEqual("discarded", payload["outcome"]["action"])
        raw_payload = self._raw_payload_text(task_id="T8")
        self.assertNotIn("Assistant:", raw_payload)
        self.assertNotIn("stderr:", raw_payload)
        self.assertNotIn("python", raw_payload)

    def test_review_required_experience_records_preserved_outcome_without_transcript_text(self) -> None:
        fast_regression_artifact = (
            self.run_dir / "tasks" / "c005_s001_T9" / "attempt_00" / "fast_web_worktree_regression.json"
        ).as_posix()
        record_task_experience(
            self.repo,
            run_id=self.run_dir.name,
            backend="claudecode",
            task_id="T9",
            title="Preserve review-required work",
            status="failed",
            reason="fast_regression_failed",
            task_status="review_required",
            cycle_idx=5,
            step_idx=1,
            attempt=1,
            max_attempts=1,
            validation_status="validation_failed",
            validation_summary="Accessibility diff needs human review",
            artifact_pointers=[fast_regression_artifact],
            outcome_action="preserved_for_review",
            outcome_note="Assistant: review transcript omitted",
            detail="backend transcript line one\nbackend transcript line two",
        )

        row = query_task_experiences(self.repo, task_id="T9", max_items=1)[0]
        payload = row["payload"]
        self.assertEqual("claudecode", row["backend"])
        self.assertEqual("review_required", row["task_status"])
        self.assertEqual("preserved_for_review", row["outcome_action"])
        self.assertEqual("Accessibility diff needs human review", payload["validation"]["summary"])
        self.assertEqual("preserved_for_review", payload["outcome"]["action"])
        self.assertEqual([fast_regression_artifact], payload["artifact_pointers"])
        raw_payload = self._raw_payload_text(task_id="T9")
        self.assertNotIn("Assistant:", raw_payload)
        self.assertNotIn("backend transcript", raw_payload)


if __name__ == "__main__":
    unittest.main()
