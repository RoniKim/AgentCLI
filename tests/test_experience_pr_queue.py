from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.experience import experience_db_path, query_pr_queue_signals
from agent_runner.gitops import abandon_task_branch, create_task_branch, create_worktree, git_head, remove_worktree
from agent_runner.pr_queue import queue_review_packet, record_review_packet_decision
from agent_runner.utils import run_cmd


class PRQueueExperienceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = Path.home() / ".codex" / "memories" / "agentcli-pr-queue-experience-tests"
        self.fixture_base.mkdir(parents=True, exist_ok=True)
        self.fixture_root = self.fixture_base / f"t-{uuid.uuid4().hex[:12]}"
        self.fixture_root.mkdir()
        self.repo = self.fixture_root / "repo"
        self.worktree = self.fixture_root / "worktree"
        self.run_dir = self.fixture_root / ".AgentCLI" / "agent_runs" / "20260503-125328"
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

    def _prepare_packet(self) -> dict[str, object]:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        goal_trace = [
            {
                "goal_ref": "GOAL-PR-10",
                "goal_text": "Tie review decisions back to the packet and GOALS trace.",
            }
        ]
        tb = create_task_branch(self.worktree, "T10", task_title="Record PR queue experience", goal_trace=goal_trace)
        (self.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)
        branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        abandon_task_branch(self.worktree, tb)
        remove_worktree(self.repo, self.worktree)

        result = queue_review_packet(
            self.repo,
            run_id=self.run_dir.name,
            task_ids=["T10"],
            base_ref=tb.base_commit,
            head_ref=branch_head,
            branch=tb.branch_name,
            created_at=tb.created_at,
            source_head_before=source_head_before,
            source_head_after=git_head(self.repo),
            worktree_dir=self.worktree.as_posix(),
            validation_status="validation_pending",
            validation_artifacts=[],
            qa_notes=["ready for review"],
            goal_trace=goal_trace,
            changed_files=["feature.txt"],
            status="pr_queued",
        )
        return {
            "packet_id": str(result["packet_id"]),
            "packet_path": Path(str(result["packet_path"])),
            "goal_trace": goal_trace,
            "branch": tb.branch_name,
            "base_ref": tb.base_commit,
            "head_ref": branch_head,
        }

    def test_record_review_packet_decision_records_discard_reason(self) -> None:
        packet = self._prepare_packet()
        discard_reason_path = self.repo / "discard_reason.json"
        discard_reason_path.write_text('{"reason":"duplicate_scope"}\n', encoding="utf-8")

        rows = record_review_packet_decision(
            self.repo,
            str(packet["packet_id"]),
            action="discard",
            decision_status="discarded",
            reason="duplicate_scope",
            evidence=[
                {
                    "kind": "decision_reason",
                    "path": discard_reason_path.as_posix(),
                }
            ],
            metadata={
                "selected_reason": "duplicate_scope",
                "source": "unit_test",
            },
        )
        signals = query_pr_queue_signals(
            self.repo,
            packet_id=str(packet["packet_id"]),
            signal_kind="discard",
        )

        self.assertTrue(experience_db_path(self.repo).exists())
        self.assertEqual(1, len(rows))
        self.assertEqual(1, len(signals))
        self.assertEqual("discard", signals[0]["signal_kind"])
        self.assertEqual("discarded", signals[0]["decision_status"])
        self.assertEqual("duplicate_scope", signals[0]["reason"])
        self.assertEqual("T10", signals[0]["task_id"])
        self.assertEqual(packet["goal_trace"], signals[0]["goal_trace"])
        self.assertEqual(packet["branch"], signals[0]["branch"])
        self.assertEqual(packet["base_ref"], signals[0]["base_ref"])
        self.assertEqual(packet["head_ref"], signals[0]["head_ref"])
        self.assertEqual("duplicate_scope", signals[0]["metadata"]["selected_reason"])
        self.assertEqual(
            discard_reason_path.as_posix(),
            signals[0]["evidence"][1]["path"],
        )

    def test_record_review_packet_decision_records_rebase_blocked_and_applied_payloads(self) -> None:
        packet = self._prepare_packet()
        blocked_path = self.repo / "rebase_blocked.json"
        applied_path = self.repo / "rebase_applied.json"
        blocked_path.write_text('{"status":"blocked"}\n', encoding="utf-8")
        applied_path.write_text('{"status":"applied"}\n', encoding="utf-8")

        record_review_packet_decision(
            self.repo,
            str(packet["packet_id"]),
            action="rebase",
            decision_status="blocked",
            reason="conflict_detected",
            evidence=[{"kind": "conflict_summary", "path": blocked_path.as_posix()}],
            metadata={
                "blocked_by": "main",
                "conflict_count": 2,
            },
        )
        record_review_packet_decision(
            self.repo,
            str(packet["packet_id"]),
            action="rebase",
            decision_status="applied",
            reason="rebase_applied",
            evidence=[{"kind": "rebase_summary", "path": applied_path.as_posix()}],
            metadata={
                "rebased_onto": "main",
                "conflict_count": 0,
            },
        )

        signals = query_pr_queue_signals(
            self.repo,
            packet_id=str(packet["packet_id"]),
            signal_kind="rebase",
            max_items=10,
        )

        self.assertEqual(2, len(signals))
        self.assertEqual(["applied", "blocked"], [signal["decision_status"] for signal in signals])
        self.assertEqual(["rebase_applied", "conflict_detected"], [signal["reason"] for signal in signals])
        self.assertEqual(packet["goal_trace"], signals[0]["goal_trace"])
        self.assertEqual("main", signals[0]["metadata"]["rebased_onto"])
        self.assertEqual(0, signals[0]["metadata"]["conflict_count"])
        self.assertEqual(applied_path.as_posix(), signals[0]["evidence"][1]["path"])
        self.assertEqual("main", signals[1]["metadata"]["blocked_by"])
        self.assertEqual(2, signals[1]["metadata"]["conflict_count"])
        self.assertEqual(blocked_path.as_posix(), signals[1]["evidence"][1]["path"])


if __name__ == "__main__":
    unittest.main()
