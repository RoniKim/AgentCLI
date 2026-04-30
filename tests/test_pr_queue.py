from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.gitops import (
    abandon_task_branch,
    create_task_branch,
    create_worktree,
    git_head,
    has_new_commits,
    ref_has_new_commits,
    remove_worktree,
)
from agent_runner.pr_queue import load_branch_index, pr_branch_index_path, pr_packet_path, queue_review_packet
from agent_runner.utils import run_cmd


class PRQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = Path.home() / ".codex" / "memories" / "agentcli-pr-queue-tests"
        self.fixture_base.mkdir(parents=True, exist_ok=True)
        self.fixture_root = self.fixture_base / f"t-{uuid.uuid4().hex[:12]}"
        self.fixture_root.mkdir()
        self.repo = self.fixture_root / "repo"
        self.worktree = self.fixture_root / "worktree"
        self.run_dir = self.fixture_root / ".AgentCLI" / "agent_runs" / "20260429-150347"
        self.run_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        code, out = run_cmd(["git", *args], cwd=cwd or self.repo, timeout_sec=60)
        self.assertEqual(code, 0, out)
        return out

    def _init_repo(self) -> str:
        self.repo.mkdir(parents=True, exist_ok=True)
        self._git("init")
        self._git("config", "user.email", "agentcli@example.invalid")
        self._git("config", "user.name", "AgentCLI Test")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "base")
        self._git("branch", "-M", "main")
        return git_head(self.repo)

    def test_queue_review_packet_writes_durable_packet_and_index(self) -> None:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        tb = create_task_branch(self.worktree, "T1", task_title="Queue review packet")
        (self.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)
        branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        abandon_task_branch(self.worktree, tb)

        result = queue_review_packet(
            self.repo,
            run_id=self.run_dir.name,
            task_ids=["T1"],
            base_ref=tb.base_commit,
            head_ref=branch_head,
            branch=tb.branch_name,
            created_at=tb.created_at,
            source_head_before=source_head_before,
            source_head_after=git_head(self.repo),
            worktree_dir=self.worktree.as_posix(),
            validation_status="validation_passed",
            validation_artifacts=[(self.run_dir / "validation.log").as_posix()],
            qa_notes=["ready for review"],
            goal_trace=tb.goal_trace,
            changed_files=["feature.txt"],
            status="pr_queued",
        )

        # Re-run with the same identifiers to exercise atomic overwrite/upsert.
        queue_review_packet(
            self.repo,
            run_id=self.run_dir.name,
            task_ids=["T1"],
            base_ref=tb.base_commit,
            head_ref=branch_head,
            branch=tb.branch_name,
            created_at=tb.created_at,
            source_head_before=source_head_before,
            source_head_after=git_head(self.repo),
            worktree_dir=self.worktree.as_posix(),
            validation_status="validation_passed",
            validation_artifacts=[(self.run_dir / "validation.log").as_posix()],
            qa_notes=["updated"],
            goal_trace=tb.goal_trace,
            changed_files=["feature.txt"],
            status="pr_queued",
        )

        remove_worktree(self.repo, self.worktree)

        self.assertEqual(source_head_before, git_head(self.repo))
        self.assertFalse(self.worktree.exists())

        packet_path = pr_packet_path(self.repo, result["packet_id"])
        index_path = pr_branch_index_path(self.repo)
        self.assertTrue(packet_path.exists())
        self.assertTrue(index_path.exists())

        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        index = load_branch_index(self.repo)

        self.assertEqual("pr_queued", packet["status"])
        self.assertFalse(packet["source_main_mutated"])
        self.assertEqual(branch_head, packet["head_ref"])
        self.assertEqual(tb.branch_name, packet["branch"])
        self.assertEqual(self.run_dir.name, packet["run_id"])
        self.assertEqual(["T1"], packet["task_ids"])
        self.assertEqual(["updated"], packet["qa_notes"])
        self.assertEqual(1, len(index["entries"]))
        self.assertEqual(result["packet_id"], index["entries"][0]["id"])
        self.assertEqual(tb.branch_name, index["entries"][0]["branch"])
        self.assertEqual(source_head_before, git_head(self.repo))

    def test_preserved_task_branch_counts_commits_after_checkout_returns_to_base(self) -> None:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        tb = create_task_branch(self.worktree, "T1", task_title="Preserve branch")
        (self.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)
        abandon_task_branch(self.worktree, tb)

        self.assertFalse(has_new_commits(self.worktree, source_head_before))
        self.assertTrue(ref_has_new_commits(self.worktree, tb.branch_name, source_head_before))

    def test_queue_review_packet_reports_missing_branch_metadata_as_recoverable(self) -> None:
        source_head_before = self._init_repo()

        result = queue_review_packet(
            self.repo,
            run_id=self.run_dir.name,
            task_ids=["T1"],
            base_ref=source_head_before,
            head_ref="",
            branch="",
            source_head_before=source_head_before,
            source_head_after=source_head_before,
            validation_status="validation_pending",
            status="pr_queued",
        )

        self.assertFalse(result["ok"])
        self.assertTrue(result["recoverable"])
        self.assertEqual("branch_metadata_missing", result["status"])
        self.assertTrue(Path(result["packet_path"]).exists())
        self.assertFalse(Path(result["branch_index_path"]).exists())

        packet = json.loads(Path(result["packet_path"]).read_text(encoding="utf-8"))
        index = load_branch_index(self.repo)
        self.assertEqual("branch_metadata_missing", packet["status"])
        self.assertEqual("skipped", packet["branch_index_status"])
        self.assertEqual([], index["entries"])

    def test_queue_review_packet_populates_metadata_from_run_artifacts(self) -> None:
        source_head_before = self._init_repo()
        run_dir = self.repo / ".AgentCLI" / "agent_runs" / self.run_dir.name
        run_dir.mkdir(parents=True, exist_ok=True)
        create_worktree(self.repo, self.worktree, run_dir=run_dir)

        tb = create_task_branch(
            self.worktree,
            "T1",
            task_title="Queue review packet",
            goal_trace=[{"goal_ref": "GOAL-1", "goal_text": "trace packet metadata"}],
        )
        (self.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)
        branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()

        pending_payload = {
            "schema_version": 1,
            "status": "pending",
            "run_id": run_dir.name,
            "source_repo": self.repo.as_posix(),
            "branch": tb.branch_name,
            "source_branch": tb.branch_name,
            "base_ref": tb.base_commit,
            "head_ref": branch_head,
            "source_head_before": source_head_before,
            "source_head_after": source_head_before,
            "worktree_dir": self.worktree.as_posix(),
            "changed_files": ["feature.txt"],
            "preflight": {
                "sentinel": "from-pending",
                "base_ref": tb.base_commit,
                "head_ref": branch_head,
                "branch": tb.branch_name,
                "source_head_before": source_head_before,
                "source_head_after": source_head_before,
                "source_main_mutated": False,
            },
        }
        (run_dir / "WORKTREE_MERGE_PENDING.json").write_text(
            json.dumps(pending_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        validation_dir = run_dir / "tasks" / "T1" / "attempt_01"
        validation_dir.mkdir(parents=True, exist_ok=True)
        validation_path = validation_dir / "validation.json"
        validation_payload = {
            "schema_version": 1,
            "kind": "qa_validation_attempt",
            "task_id": "T1",
            "task_title": "Queue review packet",
            "cycle": 1,
            "step": 1,
            "attempt": 1,
            "status": "tests_skipped",
            "validation_status": "tests_skipped",
            "validation_reason": "scope_skip",
            "validation_detail": "Tests were intentionally deferred by policy.",
            "goal_trace": tb.goal_trace,
            "qa_notes": [
                "artifact note one",
                "artifact note two",
            ],
            "summary": "",
            "detail": "",
            "failure_summary": "",
            "artifact_path": validation_path.as_posix(),
        }
        validation_path.write_text(json.dumps(validation_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        result = queue_review_packet(
            self.repo,
            run_id=run_dir.name,
            task_ids=["T1"],
            validation_status="validation_passed",
            validation_artifacts=[
                (run_dir / "WORKTREE_MERGE_PENDING.json").as_posix(),
                (run_dir / "notes.patch").as_posix(),
            ],
            status="pr_queued",
        )

        packet = json.loads(Path(result["packet_path"]).read_text(encoding="utf-8"))

        self.assertTrue(result["ok"])
        self.assertFalse(result["recoverable"])
        self.assertEqual(source_head_before, packet["source_head_before"])
        self.assertEqual(source_head_before, packet["source_head_after"])
        self.assertEqual(tb.base_commit, packet["base_ref"])
        self.assertEqual(branch_head, packet["head_ref"])
        self.assertEqual(tb.branch_name, packet["branch"])
        self.assertEqual(["feature.txt"], packet["changed_files"])
        self.assertEqual(tb.goal_trace, packet["goal_trace"])
        self.assertEqual(["artifact note one", "artifact note two"], packet["qa_notes"])
        self.assertEqual("tests_skipped", packet["validation_status"])
        self.assertEqual([validation_path.as_posix()], packet["validation_artifacts"])
        self.assertEqual("from-pending", packet["merge_preflight"]["sentinel"])
        self.assertEqual(False, packet["merge_preflight"]["source_main_mutated"])
        self.assertGreaterEqual(len(packet["commits"]), 1)
        self.assertEqual("feature", packet["commits"][0]["subject"])
        self.assertEqual("pr_queued", packet["status"])

        abandoned_branch = abandon_task_branch(self.worktree, tb)
        self.assertEqual(tb.branch_name, abandoned_branch)
        remove_worktree(self.repo, self.worktree)


if __name__ == "__main__":
    unittest.main()
