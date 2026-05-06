from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import unittest
import uuid

from agent_runner.cli import DEFAULTS
from agent_runner.local_retention import (
    LOCAL_RETENTION_DRY_RUN,
    LocalRetentionConfig,
    build_local_retention_dry_run,
    local_retention_config_from_args,
)


class LocalRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-scratch" / f"local_retention_{uuid.uuid4().hex}"
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 5, 6, 0, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, relative_path: str, text: str = "artifact\n", *, stale_days: int = 60) -> Path:
        path = self.repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        ts = (self.now - timedelta(days=stale_days)).timestamp()
        os.utime(path, (ts, ts))
        return path

    def _mkdir(self, relative_path: str, *, stale_days: int = 60) -> Path:
        path = self.repo / relative_path
        path.mkdir(parents=True, exist_ok=True)
        ts = (self.now - timedelta(days=stale_days)).timestamp()
        os.utime(path, (ts, ts))
        return path

    def _touch_tree_mtime(self, path: Path, *, stale_days: int = 60) -> None:
        ts = (self.now - timedelta(days=stale_days)).timestamp()
        for item in sorted(path.rglob("*"), reverse=True):
            os.utime(item, (ts, ts))
        os.utime(path, (ts, ts))

    def test_dry_run_reports_all_local_retention_categories_and_preserves_pending_review(self) -> None:
        protected_run = self._mkdir(".AgentCLI/agent_runs/20260401-000000")
        stale_run = self._mkdir(".AgentCLI/agent_runs/20260402-000000")
        active_run = self._mkdir(".AgentCLI/agent_runs/20260506-000000", stale_days=0)

        self._write(
            ".AgentCLI/agent_runs/20260401-000000/STATE.json",
            json.dumps(
                {
                    "pending_review": [
                        {
                            "task": "T-review",
                            "task_status": "review_required",
                            "branch": "agent/T-review",
                            "validation_artifact": (protected_run / "logs" / "run.log").as_posix(),
                        }
                    ]
                }
            ),
        )
        self._write(".AgentCLI/agent_runs/20260401-000000/WORKTREE_MERGE_PENDING.json", "{}\n")
        self._write(".AgentCLI/agent_runs/20260401-000000/logs/run.log", "pending review log\n")
        self._write(".AgentCLI/agent_runs/20260401-000000/diagnostics/windows_diagnostics.json", "{}\n")
        self._write(".AgentCLI/agent_runs/20260402-000000/logs/run.log", "stale log\n")
        self._write(".AgentCLI/PM_CACHE/project_analysis.json", "{}\n")
        self._write(".AgentCLI/logs/backend.log", "old backend log\n")
        self._write(".AgentCLI/diagnostics/doctor.json", "{}\n")
        self._write(".doc/GOALS.20260401.bak.md", "backup\n")
        self._write(
            ".AgentCLI/pr_queue/pr-review.json",
            json.dumps({"id": "pr-review", "run_id": protected_run.name, "status": "pr_queued"}) + "\n",
        )
        self._touch_tree_mtime(protected_run)
        self._touch_tree_mtime(stale_run)

        payload = build_local_retention_dry_run(
            self.repo,
            cfg=LocalRetentionConfig(max_days=30, max_run_dirs=1),
            run_dir=active_run,
            active_run_dirs=[active_run],
            now=self.now,
            write_artifact=True,
        )

        artifact = active_run / LOCAL_RETENTION_DRY_RUN
        self.assertTrue(artifact.exists())
        self.assertTrue(payload["dry_run"])
        self.assertEqual("ready", payload["status"])
        self.assertEqual(
            {"agent_runs", "backups", "diagnostics", "logs", "pm_cache"},
            set(payload["summary"]["categories"]),
        )

        by_relative = {item["relative_path"]: item for item in payload["candidates"]}
        protected_entry = by_relative[".AgentCLI/agent_runs/20260401-000000"]
        stale_entry = by_relative[".AgentCLI/agent_runs/20260402-000000"]
        active_entry = by_relative[".AgentCLI/agent_runs/20260506-000000"]
        protected_log = by_relative[".AgentCLI/agent_runs/20260401-000000/logs/run.log"]

        self.assertEqual("preserve_pending_review_evidence", protected_entry["action"])
        self.assertTrue(protected_entry["protected"])
        self.assertEqual("delete_candidate", stale_entry["action"])
        self.assertEqual("preserve_active_run", active_entry["action"])
        self.assertEqual("preserve_pending_review_evidence", protected_log["action"])
        evidence_kinds = {item["kind"] for item in protected_entry["pending_review_evidence"]}
        self.assertIn("pending_review_state", evidence_kinds)
        self.assertIn("pending_worktree_review", evidence_kinds)
        self.assertIn("review_packet", evidence_kinds)

    def test_retention_defaults_are_available_through_config(self) -> None:
        self.assertIn("retention", DEFAULTS)
        cfg = local_retention_config_from_args({"retention": DEFAULTS["retention"]})
        self.assertTrue(cfg.enabled)
        self.assertEqual(30, cfg.max_days)
        self.assertEqual(50, cfg.max_run_dirs)
        self.assertTrue(cfg.keep_pending_worktree_runs)


if __name__ == "__main__":
    unittest.main()
