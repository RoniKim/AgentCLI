from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import unittest
import uuid

from agent_runner.cli import DEFAULTS
from agent_runner.experience import (
    DEFAULT_EXPERIENCE_RETENTION_DAYS,
    ExperienceRetentionConfig,
    experience_retention_config_from_args,
    prune_experience_payload,
)


class ExperienceRetentionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-scratch" / f"experience_retention_{uuid.uuid4().hex}"
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.now = datetime(2026, 5, 3, 0, 0, 0, tzinfo=timezone.utc)
        self.cfg = ExperienceRetentionConfig(retention_days=30)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_path(self, relative_path: str, *, content: str = "artifact\n", stale_days: int | None = None, is_dir: bool = False) -> Path:
        path = self.repo / relative_path
        if is_dir:
            path.mkdir(parents=True, exist_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        if stale_days is not None:
            ts = (self.now - timedelta(days=stale_days)).timestamp()
            os.utime(path, (ts, ts))
        return path

    def _lesson(self, lesson_id: str, *, days_old: int, evidence: list[str], task_status: str = "failed") -> dict[str, object]:
        timestamp = (self.now - timedelta(days=days_old)).isoformat()
        return {
            "id": lesson_id,
            "lesson": f"Lesson {lesson_id}",
            "task_status": task_status,
            "created_at": timestamp,
            "last_seen_at": timestamp,
            "evidence": evidence,
        }

    def test_stale_lessons_are_removed_after_retention_window(self) -> None:
        artifact = self._write_path(
            ".AgentCLI/agent_runs/20260301-100000/tasks/T1/attempt_01/test.txt",
            stale_days=45,
        )
        payload = {
            "lessons": [
                self._lesson(
                    "L-stale",
                    days_old=45,
                    evidence=[artifact.relative_to(self.repo).as_posix()],
                )
            ]
        }

        result = prune_experience_payload(
            payload,
            repo=self.repo,
            cfg=self.cfg,
            dry_run=False,
            now=self.now,
        )

        self.assertEqual(0, result.lessons_after)
        self.assertFalse(artifact.exists())
        self.assertEqual(["L-stale"], [item["lesson_id"] for item in result.pruned_lessons])
        self.assertIn(artifact.as_posix(), result.deleted_paths)
        self.assertEqual([], result.updated_payload["lessons"])

    def test_pending_pr_and_active_run_evidence_are_preserved(self) -> None:
        active_run_dir = self._write_path(".AgentCLI/agent_runs/20260503-120000", is_dir=True, stale_days=60)
        active_run_artifact = self._write_path(
            ".AgentCLI/agent_runs/20260503-120000/logs/run.log",
            stale_days=60,
        )
        pending_pr_packet = self._write_path(
            ".AgentCLI/pr_queue/pr-demo.json",
            content='{"status":"pr_queued"}\n',
            stale_days=60,
        )
        payload = {
            "lessons": [
                self._lesson(
                    "L-review",
                    days_old=60,
                    task_status="review_required",
                    evidence=[active_run_artifact.relative_to(self.repo).as_posix()],
                ),
                self._lesson(
                    "L-pending-pr",
                    days_old=60,
                    evidence=[pending_pr_packet.relative_to(self.repo).as_posix()],
                ),
            ]
        }

        result = prune_experience_payload(
            payload,
            repo=self.repo,
            cfg=self.cfg,
            dry_run=False,
            now=self.now,
            active_run_dirs=[active_run_dir],
        )

        self.assertTrue(active_run_dir.exists())
        self.assertTrue(active_run_artifact.exists())
        self.assertTrue(pending_pr_packet.exists())
        self.assertEqual(["L-review"], [item["id"] for item in result.updated_payload["lessons"]])
        self.assertEqual(["L-pending-pr"], [item["lesson_id"] for item in result.pruned_lessons])
        preserved_reasons = {item["reason"] for item in result.preserved_evidence}
        self.assertIn("active_run_artifact", preserved_reasons)
        self.assertIn("pending_pr_queue", preserved_reasons)

    def test_malformed_or_missing_evidence_pointers_do_not_delete_unrelated_files(self) -> None:
        unrelated = self._write_path("notes.txt", content="keep me\n", stale_days=120)
        payload = {
            "lessons": [
                self._lesson(
                    "L-malformed",
                    days_old=120,
                    evidence=[
                        "notes.txt",
                        ".AgentCLI/agent_runs/ghost/tasks/T1/test.txt",
                        "bad\x00pointer",
                    ],
                )
            ]
        }

        result = prune_experience_payload(
            payload,
            repo=self.repo,
            cfg=self.cfg,
            dry_run=False,
            now=self.now,
        )

        self.assertTrue(unrelated.exists())
        self.assertEqual(0, result.lessons_after)
        self.assertNotIn(unrelated.as_posix(), result.deleted_paths)
        self.assertIn("unmanaged_pointer", {item["reason"] for item in result.skipped_evidence})
        self.assertIn("missing_pointer", {item["reason"] for item in result.pruned_evidence})
        self.assertIn("malformed_pointer", {item["reason"] for item in result.pruned_evidence})

    def test_retention_defaults_are_available_through_config(self) -> None:
        self.assertEqual(DEFAULT_EXPERIENCE_RETENTION_DAYS, DEFAULTS["experience"]["experience_retention_days"])
        cfg = experience_retention_config_from_args({"experience": DEFAULTS["experience"]})
        self.assertEqual(DEFAULT_EXPERIENCE_RETENTION_DAYS, cfg.retention_days)
        self.assertTrue(cfg.enabled)


if __name__ == "__main__":
    unittest.main()
