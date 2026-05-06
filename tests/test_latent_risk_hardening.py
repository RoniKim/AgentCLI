from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import shutil
import sqlite3
import unittest
import uuid
from pathlib import Path

from agent_runner.analysis_cache import MAX_ANALYSIS_CACHE_BYTES, append_analysis_changelog
from agent_runner.config import default_database_path
from agent_runner.local_retention import LocalRetentionConfig, build_local_retention_dry_run
from agent_runner.task_history import record_task
from agent_runner.utils import rotate_log_file


class LatentRiskHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_home = os.environ.get("AGENTCLI_HOME")
        self.root = Path.home() / ".codex" / "memories" / "agentcli-latent-risk" / f"t-{uuid.uuid4().hex[:12]}"
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ["AGENTCLI_HOME"] = str(self.root / "home")
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.now = datetime(2026, 5, 6, 0, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        if self.old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self.old_home
        shutil.rmtree(self.root, ignore_errors=True)

    def _mkdir_run(self, name: str, *, age_days: int) -> Path:
        path = self.repo / ".AgentCLI" / "agent_runs" / name
        path.mkdir(parents=True)
        ts = (self.now - timedelta(days=age_days)).timestamp()
        os.utime(path, (ts, ts))
        return path

    def test_rotate_log_file_rotates_by_size_and_prunes_overflow_backups(self) -> None:
        log_path = self.root / "logs" / "events.log"
        log_path.parent.mkdir(parents=True)
        log_path.write_text("current\n" * 300, encoding="utf-8")
        Path(str(log_path) + ".1").write_text("previous-one\n", encoding="utf-8")
        Path(str(log_path) + ".2").write_text("previous-two\n", encoding="utf-8")
        Path(str(log_path) + ".3").write_text("overflow\n", encoding="utf-8")

        rotate_log_file(log_path, max_bytes=1024, backup_count=2, max_age_days=14)

        self.assertFalse(log_path.exists())
        self.assertIn("current", Path(str(log_path) + ".1").read_text(encoding="utf-8"))
        self.assertIn("previous-one", Path(str(log_path) + ".2").read_text(encoding="utf-8"))
        self.assertFalse(Path(str(log_path) + ".3").exists())

    def test_agent_run_retention_keeps_newest_and_marks_older_runs(self) -> None:
        self._mkdir_run("20260401-000000", age_days=35)
        self._mkdir_run("20260402-000000", age_days=34)
        newest = self._mkdir_run("20260506-000000", age_days=0)

        payload = build_local_retention_dry_run(
            self.repo,
            cfg=LocalRetentionConfig(max_days=0, max_run_dirs=1, keep_failed_runs=False),
            run_dir=newest,
            active_run_dirs=[newest],
            now=self.now,
        )

        run_candidates = [item for item in payload["candidates"] if item["category"] == "agent_runs"]
        actions = {item["relative_path"]: item["action"] for item in run_candidates}
        self.assertEqual("delete_candidate", actions[".AgentCLI/agent_runs/20260401-000000"])
        self.assertEqual("delete_candidate", actions[".AgentCLI/agent_runs/20260402-000000"])
        self.assertEqual("preserve_active_run", actions[".AgentCLI/agent_runs/20260506-000000"])

    def test_task_history_creates_query_indexes(self) -> None:
        record_task(
            self.repo,
            task_id="T-idx",
            title="Index task history",
            status="failed",
            task_status="regression_failed",
            reason="test_failed",
        )

        with sqlite3.connect(str(default_database_path(self.repo))) as conn:
            indexes = {row[1] for row in conn.execute("PRAGMA index_list('task_history')").fetchall()}

        self.assertIn("idx_task_history_status_id", indexes)
        self.assertIn("idx_task_history_title_id", indexes)
        self.assertIn("idx_task_history_task_id_id", indexes)
        self.assertIn("idx_task_history_task_status_id", indexes)

    def test_analysis_cache_append_is_size_capped(self) -> None:
        analysis_path = self.repo / ".AgentCLI" / "PM_CACHE" / "PROJECT_ANALYSIS.md"
        analysis_path.parent.mkdir(parents=True)
        analysis_path.write_text("# PROJECT ANALYSIS\n\n" + ("old analysis line\n" * 40_000), encoding="utf-8")

        append_analysis_changelog(analysis_path, "- new bounded entry")

        content = analysis_path.read_text(encoding="utf-8")
        self.assertLessEqual(analysis_path.stat().st_size, MAX_ANALYSIS_CACHE_BYTES)
        self.assertIn("truncated to analysis cache size cap", content)
        self.assertIn("new bounded entry", content)


if __name__ == "__main__":
    unittest.main()
