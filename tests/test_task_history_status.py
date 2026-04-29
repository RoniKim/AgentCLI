import os
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.task_history import count_consecutive_title_failures, query_history, record_task


class TaskHistoryStatusTests(unittest.TestCase):
    def test_task_status_is_persisted_and_blocks_env_from_consecutive_failures(self) -> None:
        old_home = os.environ.get("AGENTCLI_HOME")
        root = Path.cwd() / f".tmp-task-history-status-{uuid.uuid4().hex}"
        try:
            root.mkdir()
            os.environ["AGENTCLI_HOME"] = str(root / "home")
            repo = root / "repo"
            repo.mkdir()

            record_task(
                repo,
                task_id="T1",
                title="Install web smoke coverage",
                status="failed",
                task_status="regression_failed",
                reason="test_failed",
            )
            record_task(
                repo,
                task_id="T1",
                title="Install web smoke coverage",
                status="failed",
                task_status="blocked_env",
                reason="needs_dependency",
            )

            rows = query_history(repo, max_items=2)
            self.assertEqual("blocked_env", rows[0]["task_status"])
            self.assertEqual(1, count_consecutive_title_failures(repo, "Install web smoke coverage"))

            record_task(
                repo,
                task_id="T1",
                title="Install web smoke coverage",
                status="done",
                task_status="completed",
            )
            self.assertEqual(0, count_consecutive_title_failures(repo, "Install web smoke coverage"))
        finally:
            if old_home is None:
                os.environ.pop("AGENTCLI_HOME", None)
            else:
                os.environ["AGENTCLI_HOME"] = old_home
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
