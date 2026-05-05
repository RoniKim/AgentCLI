import json
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

from agent_runner.backlog_utils import build_failed_tasks_block
from agent_runner.config import default_database_path
from agent_runner.task_history import count_consecutive_title_failures, query_history, record_task


class TaskHistoryStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_home = os.environ.get("AGENTCLI_HOME")
        self.root = Path.cwd() / f".tmp-task-history-status-{uuid.uuid4().hex}"
        self.root.mkdir()
        os.environ["AGENTCLI_HOME"] = str(self.root / "home")
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260505-000000"
        self.run_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def test_task_status_is_retained_for_migrated_and_new_rows(self) -> None:
        db_path = default_database_path(self.repo)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE task_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id      TEXT NOT NULL,
                    title        TEXT NOT NULL,
                    status       TEXT NOT NULL,
                    reason       TEXT DEFAULT '',
                    detail       TEXT DEFAULT '',
                    files        TEXT DEFAULT '[]',
                    cycle_idx    INTEGER DEFAULT 0,
                    run_id       TEXT DEFAULT '',
                    backend      TEXT DEFAULT '',
                    recorded_at  TEXT NOT NULL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO task_history
                (task_id, title, status, reason, detail, files, cycle_idx, run_id, backend, recorded_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "T0",
                    "Legacy history row",
                    "failed",
                    "build_failed",
                    "legacy row before task_status migration",
                    "[]",
                    1,
                    "legacy-run",
                    "codex",
                    "2026-05-05T00:00:00+00:00",
                ),
            )
            conn.commit()

        record_task(
            self.repo,
            task_id="T1",
            title="New history row",
            status="failed",
            task_status="blocked_env",
            reason="needs_dependency",
        )

        rows = query_history(self.repo, max_items=2)
        self.assertEqual("blocked_env", rows[0]["task_status"])
        self.assertEqual("needs_dependency", rows[0]["reason"])
        self.assertEqual("", rows[1]["task_status"])
        self.assertEqual("build_failed", rows[1]["reason"])

    def test_failed_tasks_block_uses_history_task_status_and_reason_without_raw_logs(self) -> None:
        task_id = "T2"
        title = "Repair PM failure context"
        raw_detail = "Assistant: raw backend transcript\nshell_command: pytest tests/test_web.py"

        (self.run_dir / "BACKLOG.json").write_text(
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": task_id,
                            "title": title,
                            "prompt": "Implement the PM failure context fix.",
                            "files": ["agent_runner/task_history.py"],
                            "done_when": "PM prompt shows classification metadata only.",
                            "skills": [],
                            "skills_rationale": None,
                            "depends_on": [],
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        state_path = self.run_dir / "STATE.json"
        state_path.write_text(
            json.dumps({"done": [], "failed": [{"task": task_id, "detail": raw_detail}], "warnings": []}, ensure_ascii=False),
            encoding="utf-8",
        )

        record_task(
            self.repo,
            task_id=task_id,
            title=title,
            status="failed",
            task_status="blocked_env",
            reason="needs_dependency",
            detail=raw_detail,
            attempt=2,
            max_attempts=3,
        )

        block = build_failed_tasks_block(state_path, self.run_dir)
        self.assertIn('"task_status": "blocked_env"', block)
        self.assertIn('"reason": "needs_dependency"', block)
        self.assertIn('"title": "Repair PM failure context"', block)
        self.assertNotIn('"detail"', block)
        self.assertNotIn("Assistant: raw backend transcript", block)
        self.assertNotIn("shell_command: pytest", block)

    def test_consecutive_failures_ignore_env_and_contract_statuses_but_count_regressions(self) -> None:
        title = "Install web smoke coverage"
        record_task(
            self.repo,
            task_id="T1",
            title=title,
            status="failed",
            task_status="regression_failed",
            reason="test_failed",
        )
        record_task(
            self.repo,
            task_id="T1",
            title=title,
            status="failed",
            task_status="blocked_env",
            reason="needs_dependency",
        )
        record_task(
            self.repo,
            task_id="T1",
            title=title,
            status="failed",
            task_status="test_contract_changed",
            reason="test_failed",
        )
        record_task(
            self.repo,
            task_id="T1",
            title=title,
            status="failed",
            task_status="regression_failed",
            reason="test_failed",
        )

        self.assertEqual(2, count_consecutive_title_failures(self.repo, title))

        record_task(
            self.repo,
            task_id="T1",
            title=title,
            status="done",
            task_status="completed",
        )
        self.assertEqual(0, count_consecutive_title_failures(self.repo, title))


if __name__ == "__main__":
    unittest.main()
