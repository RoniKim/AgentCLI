import os
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.config import default_database_path
from agent_runner.experience import (
    query_completed_task_experiences,
    record_completed_task_experience,
)


class CompletedTaskExperienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_home = os.environ.get("AGENTCLI_HOME")
        self.root = Path.cwd() / f".tmp-experience-completed-{uuid.uuid4().hex}"
        self.root.mkdir()
        os.environ["AGENTCLI_HOME"] = str(self.root / "home")
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.run_id = "20260503-125328"
        self.task_id = "T7"
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / self.run_id
        self.validation_dir = self.run_dir / "tasks" / self.task_id / "attempt_01"
        self.validation_dir.mkdir(parents=True, exist_ok=True)
        self.validation_path = self.validation_dir / "validation.json"
        self.build_path = self.validation_dir / "build.txt"
        self.patch_path = self.validation_dir / "changes.patch"
        self.validation_path.write_text('{"status":"validation_passed"}\n', encoding="utf-8")
        self.build_path.write_text("build ok\n", encoding="utf-8")
        self.patch_path.write_text("diff --git a/file b/file\n", encoding="utf-8")
        self.addCleanup(self._cleanup)

    def _cleanup(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def test_record_completed_task_experience_persists_pr_packet_metadata(self) -> None:
        record_completed_task_experience(
            self.repo,
            run_id=self.run_id,
            task_id=self.task_id,
            title="Link completed task experience metadata",
            status="done",
            task_status="completed",
            validation_status="validation_passed",
            goal_trace=[
                {"goal_ref": "P0-U", "goal_text": "Link completed tasks to structured experience metadata."},
            ],
            changed_files=["agent_runner/experience.py", "agent_runner/cycle.py"],
            branch_ref="task/T7__goal-P0-U_20260503",
            head_ref="abc123def456",
            base_ref="main",
            validation_artifacts=[
                self.validation_path.as_posix(),
                self.build_path.as_posix(),
                self.patch_path.as_posix(),
            ],
            validation_records=[
                {
                    "artifact_path": self.validation_path.as_posix(),
                    "summary": "validation passed",
                }
            ],
            pr_packet_ids=["pr-20260503-t7"],
        )

        rows = query_completed_task_experiences(self.repo, run_id=self.run_id, task_id=self.task_id)
        self.assertEqual(1, len(rows))
        record = rows[0]

        self.assertEqual(self.run_id, record["run_id"])
        self.assertEqual(self.task_id, record["task_id"])
        self.assertEqual("done", record["status"])
        self.assertEqual("completed", record["task_status"])
        self.assertEqual("validation_passed", record["validation_status"])
        self.assertEqual(["P0-U"], record["goal_refs"])
        self.assertEqual(["agent_runner/experience.py", "agent_runner/cycle.py"], record["changed_files"])
        self.assertEqual("task/T7__goal-P0-U_20260503", record["branch_ref"])
        self.assertEqual("abc123def456", record["head_ref"])
        self.assertEqual("main", record["base_ref"])
        self.assertEqual(
            [
                ".AgentCLI/agent_runs/20260503-125328/tasks/T7/attempt_01/validation.json",
                ".AgentCLI/agent_runs/20260503-125328/tasks/T7/attempt_01/build.txt",
            ],
            record["validation_artifacts"],
        )
        self.assertEqual(["pr-20260503-t7"], record["pr_packet_ids"])

    def test_record_completed_task_experience_uses_empty_packet_ids_when_no_pr_packet_exists(self) -> None:
        record_completed_task_experience(
            self.repo,
            run_id=self.run_id,
            task_id=self.task_id,
            title="Persist completed experience without review packet",
            status="done",
            task_status="completed",
            validation_status="tests_skipped",
            goal_trace=[{"goal_ref": "P0-U"}],
            changed_files=["agent_runner/cycle.py"],
            validation_artifacts=[self.validation_path.as_posix()],
            pr_packet_ids=[],
        )

        rows = query_completed_task_experiences(self.repo, run_id=self.run_id, task_id=self.task_id)
        self.assertEqual(1, len(rows))
        self.assertEqual([], rows[0]["pr_packet_ids"])
        self.assertEqual(
            [".AgentCLI/agent_runs/20260503-125328/tasks/T7/attempt_01/validation.json"],
            rows[0]["validation_artifacts"],
        )

    def test_record_completed_task_experience_redacts_raw_prompts_logs_and_diffs(self) -> None:
        raw_prompt = "RAW_PROMPT_SHOULD_NOT_PERSIST"
        raw_log = "RAW_LOG_SHOULD_NOT_PERSIST"
        raw_diff = "RAW_DIFF_SHOULD_NOT_PERSIST"
        raw_goal_text = "RAW_GOAL_TEXT_SHOULD_NOT_PERSIST"

        record_completed_task_experience(
            self.repo,
            run_id=self.run_id,
            task_id=self.task_id,
            title="Redact untrusted experience payloads",
            status="done",
            task_status="completed",
            validation_status="validation_failed",
            goal_trace=[{"goal_ref": "P0-U", "goal_text": raw_goal_text}],
            changed_files=[
                {"path": "agent_runner/experience.py", "diff": raw_diff},
                {"file": "agent_runner/cycle.py", "patch": raw_diff},
            ],
            branch_ref="task/T7-redaction",
            head_ref="def456abc123",
            base_ref="main",
            validation_artifacts=[self.patch_path.as_posix()],
            validation_records=[
                {
                    "artifact_path": self.validation_path.as_posix(),
                    "summary": raw_log,
                    "detail": raw_log,
                    "prompt": raw_prompt,
                    "diff": raw_diff,
                }
            ],
            pr_packet_ids=["pr-redaction"],
        )

        rows = query_completed_task_experiences(self.repo, run_id=self.run_id, task_id=self.task_id)
        self.assertEqual(1, len(rows))
        record = rows[0]
        serialized = str(record)

        self.assertEqual(["P0-U"], record["goal_refs"])
        self.assertEqual(["agent_runner/experience.py", "agent_runner/cycle.py"], record["changed_files"])
        self.assertEqual(
            [".AgentCLI/agent_runs/20260503-125328/tasks/T7/attempt_01/validation.json"],
            record["validation_artifacts"],
        )
        self.assertNotIn(raw_prompt, serialized)
        self.assertNotIn(raw_log, serialized)
        self.assertNotIn(raw_diff, serialized)
        self.assertNotIn(raw_goal_text, serialized)

        db_path = default_database_path(self.repo)
        db_blobs = [db_path.read_bytes()]
        wal_path = Path(str(db_path) + "-wal")
        if wal_path.exists():
            db_blobs.append(wal_path.read_bytes())
        combined = b"".join(db_blobs)

        self.assertNotIn(raw_prompt.encode("utf-8"), combined)
        self.assertNotIn(raw_log.encode("utf-8"), combined)
        self.assertNotIn(raw_diff.encode("utf-8"), combined)
        self.assertNotIn(raw_goal_text.encode("utf-8"), combined)


if __name__ == "__main__":
    unittest.main()
