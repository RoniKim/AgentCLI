from __future__ import annotations

import io
import json
import shutil
import sys
import threading
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from prompt_toolkit.completion.base import CompleteEvent
from prompt_toolkit.document import Document

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import agent_runner.pr_queue as pr_queue_module
from agent_runner.gitops import (
    abandon_task_branch,
    create_task_branch,
    create_worktree,
    git_head,
    git_rev_parse_ref,
    has_new_commits,
    ref_has_new_commits,
    remove_worktree,
)
from agent_runner.experience import query_pr_queue_signals
from agent_runner.pr_queue import (
    PrQueueMergeError,
    discard_review_packet,
    load_branch_index,
    list_review_packets,
    pr_branch_index_path,
    pr_packet_path,
    rebase_review_packet,
    reconcile_review_queue,
    merge_review_packet,
    pr_queue_merge_confirmation_phrase,
    queue_review_packet,
    validate_review_packet,
)
from agent_runner.shell import RunnerShell, _build_completer, _dispatch
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

    def _write_pr_queue_validation_config(self, *, build_enabled: bool, run_tests: bool) -> None:
        run_dir = self.repo / ".AgentCLI" / "agent_runs" / self.run_dir.name
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "build_enabled": build_enabled,
            "run_tests": run_tests,
            "build_cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
            "test_cmd": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
            "build_timeout_seconds": 60,
            "test_timeout_seconds": 60,
        }
        (run_dir / "last_run_summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _prepare_validation_packet(self) -> dict[str, object]:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        goal_trace = [
            {
                "goal_ref": "GOAL-1",
                "goal_text": "Keep the PR queue validation isolated from the source repo.",
            }
        ]
        tb = create_task_branch(self.worktree, "T1", task_title="Validate PR queue packet", goal_trace=goal_trace)
        (self.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)
        branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        abandon_task_branch(self.worktree, tb)
        remove_worktree(self.repo, self.worktree)

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
            validation_status="validation_pending",
            validation_artifacts=[],
            qa_notes=["ready for validation"],
            goal_trace=goal_trace,
            changed_files=["feature.txt"],
            status="pr_queued",
        )
        return {
            "packet_id": result["packet_id"],
            "packet_path": Path(result["packet_path"]),
            "goal_trace": goal_trace,
            "source_head_before": source_head_before,
            "branch_head": branch_head,
            "task_branch": tb,
        }

    def _prepare_branch_queue_packet(
        self,
        *,
        packet_branch: str | None = None,
        packet_base_ref: str | None = None,
        packet_head_ref: str | None = None,
        worktree_dir: str = "",
        remove_worktree_after: bool = True,
    ) -> dict[str, object]:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        tb = create_task_branch(self.worktree, "T1", task_title="Reconcile PR queue packet")
        (self.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)
        branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        abandon_task_branch(self.worktree, tb)

        result = queue_review_packet(
            self.repo,
            run_id=self.run_dir.name,
            task_ids=["T1"],
            base_ref=packet_base_ref if packet_base_ref is not None else tb.base_commit,
            head_ref=packet_head_ref if packet_head_ref is not None else branch_head,
            branch=packet_branch if packet_branch is not None else tb.branch_name,
            created_at=tb.created_at,
            source_head_before=source_head_before,
            source_head_after=git_head(self.repo),
            worktree_dir=worktree_dir,
            validation_status="validation_pending",
            validation_artifacts=[],
            qa_notes=["queued for reconciliation"],
            goal_trace=tb.goal_trace,
            changed_files=["feature.txt"],
            status="pr_queued",
        )
        if remove_worktree_after:
            remove_worktree(self.repo, self.worktree)
        return {
            "packet_id": result["packet_id"],
            "packet_path": Path(result["packet_path"]),
            "source_head_before": source_head_before,
            "branch_head": branch_head,
            "task_branch": tb,
        }

    def _update_packet_file(self, packet_path: Path, **updates: object) -> dict[str, object]:
        payload = json.loads(packet_path.read_text(encoding="utf-8"))
        payload.update(updates)
        packet_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def _set_packet_validation_status(self, packet: dict[str, object], validation_status: str) -> None:
        packet_path = Path(packet["packet_path"])
        packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
        packet_data["status"] = validation_status
        packet_data["validation_status"] = validation_status
        packet_data["validationStatus"] = validation_status
        packet_data["updated_at"] = packet_data.get("updated_at") or packet_data.get("updatedAt") or ""
        packet_path.write_text(json.dumps(packet_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _prepare_validated_packet(self) -> dict[str, object]:
        packet = self._prepare_validation_packet()
        self._write_pr_queue_validation_config(build_enabled=True, run_tests=True)

        async def fake_run_build_validation_async(
            repo: Path,
            build_cmd: object,
            build_timeout_sec: int,
            legacy_build_target: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("build ok\n", encoding="utf-8")
            return {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
                "rc": 0,
                "ok": True,
                "status": "passed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "build ok",
                "failure_summary": "",
                "failureSummary": "",
            }

        async def fake_run_test_validation_async(
            repo: Path,
            test_cmd: object,
            test_timeout_sec: int,
            legacy_test_target: str,
            legacy_test_filter: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("test ok\n", encoding="utf-8")
            return {
                "name": "test",
                "kind": "test",
                "gate": "test",
                "cmd": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
                "rc": 0,
                "ok": True,
                "status": "passed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "test ok",
                "failure_summary": "",
                "failureSummary": "",
            }

        with (
            patch("agent_runner.pr_queue.run_build_validation_async", new=fake_run_build_validation_async),
            patch("agent_runner.pr_queue.run_test_validation_async", new=fake_run_test_validation_async),
        ):
            validate_review_packet(self.repo, str(packet["packet_id"]))
        return packet

    def test_shell_help_and_completer_include_pr_queue_commands(self) -> None:
        shell = RunnerShell()
        stream = io.StringIO()

        with redirect_stdout(stream):
            shell.help()

        output = stream.getvalue()
        self.assertIn("/prs", output)
        self.assertIn("/pr <id>", output)
        self.assertIn("/validate-pr <id> [--full]", output)
        self.assertIn("/merge-pr <id>", output)
        self.assertIn("/discard-pr <id>", output)
        self.assertIn("/rebase-pr <id>", output)

        completer = _build_completer()
        self.assertIsNotNone(completer)
        completions = list(
            completer.get_completions(
                Document("/", cursor_position=1),
                CompleteEvent(completion_requested=True),
            )
        )
        completion_texts = {completion.text for completion in completions}
        self.assertIn("/prs", completion_texts)
        self.assertIn("/pr", completion_texts)
        self.assertIn("/validate-pr", completion_texts)
        self.assertIn("/merge-pr", completion_texts)
        self.assertIn("/discard-pr", completion_texts)
        self.assertIn("/rebase-pr", completion_texts)

    def test_reconcile_review_queue_reports_missing_patch_in_dry_run(self) -> None:
        packet = self._prepare_branch_queue_packet(
            packet_branch="main",
            worktree_dir=self.worktree.as_posix(),
            remove_worktree_after=False,
        )
        packet_path = Path(packet["packet_path"])
        index_path = pr_branch_index_path(self.repo)
        missing_patch = self.fixture_root / "missing.patch"
        self._update_packet_file(packet_path, diff_artifacts=[missing_patch.as_posix()])
        packet_before = packet_path.read_text(encoding="utf-8")
        index_before = index_path.read_text(encoding="utf-8")

        result = reconcile_review_queue(self.repo)

        self.assertFalse(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual("issues_found", result["state"])
        self.assertEqual(1, result["summary"]["missing_patch_artifacts"])
        item = result["items"][0]
        self.assertEqual("patch", item["mode"])
        self.assertEqual("missing", item["patch_artifact_state"])
        self.assertEqual("present", item["worktree_state"])
        self.assertEqual(packet_before, packet_path.read_text(encoding="utf-8"))
        self.assertEqual(index_before, index_path.read_text(encoding="utf-8"))
        self.assertNotIn("queue_reconciliation", json.loads(packet_path.read_text(encoding="utf-8")))

    def test_reconcile_review_queue_reports_deleted_generated_worktree(self) -> None:
        packet = self._prepare_branch_queue_packet(
            packet_branch="main",
            worktree_dir=self.worktree.as_posix(),
            remove_worktree_after=True,
        )
        packet_path = Path(packet["packet_path"])
        patch_path = self.fixture_root / "worktree.patch"
        patch_path.write_text("diff --git a/feature.txt b/feature.txt\n", encoding="utf-8")
        self._update_packet_file(packet_path, diff_artifacts=[patch_path.as_posix()])

        result = reconcile_review_queue(self.repo)

        self.assertFalse(result["ok"])
        self.assertEqual(1, result["summary"]["deleted_worktrees"])
        item = result["items"][0]
        self.assertEqual("patch", item["mode"])
        self.assertEqual("deleted", item["worktree_state"])
        self.assertEqual("present", item["patch_artifact_state"])
        self.assertFalse(any(issue["kind"] == "branch_ref" for issue in item["issues"]))

    def test_reconcile_review_queue_apply_repairs_stale_branch_index_metadata_without_recreating_branch(self) -> None:
        packet = self._prepare_branch_queue_packet(worktree_dir="", remove_worktree_after=True)
        packet_path = Path(packet["packet_path"])
        branch_name = packet["task_branch"].branch_name
        self._git("branch", "-D", branch_name)

        index_path = pr_branch_index_path(self.repo)
        index_payload = load_branch_index(self.repo)
        index_payload["entries"][0]["branch"] = "task/stale"
        index_payload["entries"][0]["head_ref"] = "deadbeef"
        index_payload["entries"][0]["packet_path"] = (self.fixture_root / "wrong-packet.json").as_posix()
        index_path.write_text(json.dumps(index_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        dry_run = reconcile_review_queue(self.repo)
        item = dry_run["items"][0]
        self.assertEqual("missing", item["branch_state"])
        self.assertEqual("stale", item["branch_index_state"])

        applied = reconcile_review_queue(self.repo, apply=True)

        packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
        index_data = load_branch_index(self.repo)
        self.assertEqual("", git_rev_parse_ref(self.repo, branch_name))
        self.assertTrue(packet_path.exists())
        self.assertEqual(1, applied["summary"]["applied_updates"])
        self.assertEqual("written", packet_data["branch_index_status"])
        self.assertEqual("issues_found", packet_data["queue_reconciliation"]["status"])
        self.assertIn("branch_ref:missing", packet_data["queue_reconciliation"]["issue_keys"])
        self.assertIn("branch_index:stale", packet_data["queue_reconciliation"]["issue_keys"])
        self.assertEqual(branch_name, index_data["entries"][0]["branch"])
        self.assertEqual(packet["branch_head"], index_data["entries"][0]["head_ref"])
        self.assertEqual(packet_path.resolve().as_posix(), Path(index_data["entries"][0]["packet_path"]).resolve().as_posix())
        self.assertEqual(packet_data["queue_reconciliation"], index_data["entries"][0]["queue_reconciliation"])

    def test_reconcile_review_queue_valid_packet_is_noop(self) -> None:
        packet = self._prepare_branch_queue_packet(worktree_dir="", remove_worktree_after=True)
        packet_path = Path(packet["packet_path"])
        index_path = pr_branch_index_path(self.repo)
        packet_before = packet_path.read_text(encoding="utf-8")
        index_before = index_path.read_text(encoding="utf-8")

        result = reconcile_review_queue(self.repo, apply=True)

        self.assertTrue(result["ok"])
        self.assertEqual("ok", result["state"])
        self.assertEqual(0, result["summary"]["issue_count"])
        self.assertEqual(0, result["summary"]["applied_updates"])
        self.assertEqual(packet_before, packet_path.read_text(encoding="utf-8"))
        self.assertEqual(index_before, index_path.read_text(encoding="utf-8"))

    def test_shell_prs_reports_empty_queue(self) -> None:
        self.repo.mkdir(parents=True, exist_ok=True)
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        stream = io.StringIO()

        with redirect_stdout(stream):
            _dispatch(shell, "/prs")

        output = stream.getvalue()
        self.assertIn("PR Queue", output)
        self.assertIn("No queued PR packets.", output)

        payload = list_review_packets(self.repo)
        self.assertEqual("empty", payload["state"])
        self.assertEqual([], payload["items"])

    def test_shell_prs_reports_populated_queue(self) -> None:
        packet = self._prepare_validation_packet()
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        stream = io.StringIO()

        with redirect_stdout(stream):
            _dispatch(shell, "/prs")

        output = stream.getvalue()
        self.assertIn("PR Queue", output)
        self.assertIn(str(packet["packet_id"]), output)
        self.assertIn("validation_pending", output)
        self.assertIn("T1", output)
        self.assertIn(str(packet["task_branch"].branch_name), output)

    def test_shell_pr_reports_detail_output(self) -> None:
        packet = self._prepare_validation_packet()
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        stream = io.StringIO()

        with redirect_stdout(stream):
            _dispatch(shell, f"/pr {packet['packet_id']}")

        output = stream.getvalue()
        self.assertIn("PR Packet", output)
        self.assertIn(str(packet["packet_id"]), output)
        self.assertIn("feature.txt", output)
        self.assertIn("ready for validation", output)
        self.assertIn(str(packet["task_branch"].branch_name), output)

    def test_shell_validate_pr_command_persists_validation_status(self) -> None:
        packet = self._prepare_validation_packet()
        self._write_pr_queue_validation_config(build_enabled=True, run_tests=True)
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        stream = io.StringIO()

        async def fake_run_build_validation_async(
            repo: Path,
            build_cmd: object,
            build_timeout_sec: int,
            legacy_build_target: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("build ok\n", encoding="utf-8")
            return {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
                "rc": 0,
                "ok": True,
                "status": "passed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "build ok",
                "failure_summary": "",
                "failureSummary": "",
            }

        async def fake_run_test_validation_async(
            repo: Path,
            test_cmd: object,
            test_timeout_sec: int,
            legacy_test_target: str,
            legacy_test_filter: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("test ok\n", encoding="utf-8")
            return {
                "name": "test",
                "kind": "test",
                "gate": "test",
                "cmd": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
                "rc": 0,
                "ok": True,
                "status": "passed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "test ok",
                "failure_summary": "",
                "failureSummary": "",
            }

        with (
            redirect_stdout(stream),
            patch("agent_runner.pr_queue.run_build_validation_async", new=fake_run_build_validation_async),
            patch("agent_runner.pr_queue.run_test_validation_async", new=fake_run_test_validation_async),
        ):
            _dispatch(shell, f"/validate-pr {packet['packet_id']} --full")

        packet_data = json.loads(Path(packet["packet_path"]).read_text(encoding="utf-8"))
        output = stream.getvalue()
        self.assertIn("validation_passed", output)
        self.assertEqual("validation_passed", packet_data["validation_status"])
        self.assertTrue(str(packet_data["validation_artifact_path"]).endswith("validation.json"))

    def test_shell_merge_pr_command_rejects_bad_confirmation(self) -> None:
        packet = self._prepare_validated_packet()
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        stream = io.StringIO()

        with redirect_stdout(stream), patch("builtins.input", return_value="WRONG"):
            _dispatch(shell, f"/merge-pr {packet['packet_id']}")

        output = stream.getvalue()
        packet_data = json.loads(Path(packet["packet_path"]).read_text(encoding="utf-8"))
        self.assertIn("approval_mismatch", output)
        self.assertEqual("validation_passed", packet_data["validation_status"])
        self.assertNotEqual("approved", packet_data["status"])

    def test_discard_review_packet_records_decision_without_corrupting_branch_index(self) -> None:
        packet = self._prepare_validation_packet()
        before = load_branch_index(self.repo)
        before_entry = dict(before["entries"][0])

        result = discard_review_packet(self.repo, str(packet["packet_id"]), reason="duplicate_scope")
        packet_data = json.loads(Path(result["packet_path"]).read_text(encoding="utf-8"))
        after = load_branch_index(self.repo)
        after_entry = dict(after["entries"][0])
        signal_rows = query_pr_queue_signals(
            self.repo,
            packet_id=str(packet["packet_id"]),
            signal_kind="discard",
            decision_status="discarded",
        )

        self.assertTrue(result["ok"])
        self.assertEqual("discarded", packet_data["status"])
        self.assertEqual("duplicate_scope", packet_data["discard_reason"])
        self.assertEqual(before_entry["id"], after_entry["id"])
        self.assertEqual(before_entry["branch"], after_entry["branch"])
        self.assertEqual(before_entry["base_ref"], after_entry["base_ref"])
        self.assertEqual(before_entry["head_ref"], after_entry["head_ref"])
        self.assertEqual("discarded", after_entry["status"])
        self.assertEqual(1, len(signal_rows))
        self.assertEqual("duplicate_scope", signal_rows[0]["reason"])

    def test_rebase_review_packet_records_decision_without_corrupting_branch_index(self) -> None:
        packet = self._prepare_validation_packet()
        before = load_branch_index(self.repo)
        before_entry = dict(before["entries"][0])

        result = rebase_review_packet(self.repo, str(packet["packet_id"]), reason="source_head_advanced")
        packet_data = json.loads(Path(result["packet_path"]).read_text(encoding="utf-8"))
        after = load_branch_index(self.repo)
        after_entry = dict(after["entries"][0])
        signal_rows = query_pr_queue_signals(
            self.repo,
            packet_id=str(packet["packet_id"]),
            signal_kind="rebase",
            decision_status="requested",
        )

        self.assertTrue(result["ok"])
        self.assertEqual("review_required", packet_data["status"])
        self.assertEqual("validation_pending", packet_data["validation_status"])
        self.assertEqual("requested", packet_data["rebase_status"])
        self.assertEqual("source_head_advanced", packet_data["rebase_reason"])
        self.assertEqual(before_entry["id"], after_entry["id"])
        self.assertEqual(before_entry["branch"], after_entry["branch"])
        self.assertEqual(before_entry["base_ref"], after_entry["base_ref"])
        self.assertEqual(before_entry["head_ref"], after_entry["head_ref"])
        self.assertEqual("review_required", after_entry["status"])
        self.assertEqual("validation_pending", after_entry["validation_status"])
        self.assertEqual(1, len(signal_rows))
        self.assertEqual("source_head_advanced", signal_rows[0]["reason"])

    def test_concurrent_pr_queue_mutations_serialize_packet_and_index_updates(self) -> None:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        def queue_packet(task_id: str, filename: str) -> dict[str, object]:
            tb = create_task_branch(self.worktree, task_id, task_title=f"Queue {task_id}")
            (self.worktree / filename).write_text(f"{task_id}\n", encoding="utf-8")
            self._git("add", filename, cwd=self.worktree)
            self._git("commit", "-m", f"{task_id.lower()} feature", cwd=self.worktree)
            branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
            abandon_task_branch(self.worktree, tb)
            result = queue_review_packet(
                self.repo,
                run_id=self.run_dir.name,
                task_ids=[task_id],
                base_ref=tb.base_commit,
                head_ref=branch_head,
                branch=tb.branch_name,
                created_at=tb.created_at,
                source_head_before=source_head_before,
                source_head_after=git_head(self.repo),
                worktree_dir=self.worktree.as_posix(),
                validation_status="validation_pending",
                validation_artifacts=[],
                qa_notes=[f"{task_id} ready"],
                goal_trace=tb.goal_trace,
                changed_files=[filename],
                status="pr_queued",
            )
            return {
                "packet_id": str(result["packet_id"]),
                "packet_path": Path(result["packet_path"]),
            }

        first = queue_packet("T1", "first.txt")
        second = queue_packet("T2", "second.txt")
        remove_worktree(self.repo, self.worktree)

        first_write_started = threading.Event()
        release_first_write = threading.Event()
        second_write_seen = threading.Event()
        block_once = {"value": False}
        original_atomic_write_json = pr_queue_module.atomic_write_json

        def wrapped_atomic_write_json(path: Path, payload: object) -> None:
            path_obj = Path(path).resolve()
            if (
                not block_once["value"]
                and path_obj == Path(first["packet_path"]).resolve()
                and isinstance(payload, dict)
                and str(payload.get("status") or "") == "discarded"
                and str(payload.get("branch_index_status") or "") == "pending"
            ):
                block_once["value"] = True
                first_write_started.set()
                self.assertTrue(release_first_write.wait(timeout=5))
            if path_obj == Path(second["packet_path"]).resolve():
                second_write_seen.set()
            original_atomic_write_json(path, payload)

        results: dict[str, dict[str, object]] = {}
        errors: list[BaseException] = []

        def run_discard() -> None:
            try:
                results["discard"] = discard_review_packet(self.repo, str(first["packet_id"]), reason="duplicate_scope")
            except BaseException as ex:
                errors.append(ex)

        def run_rebase() -> None:
            try:
                results["rebase"] = rebase_review_packet(self.repo, str(second["packet_id"]), reason="source_head_advanced")
            except BaseException as ex:
                errors.append(ex)

        with patch("agent_runner.pr_queue.atomic_write_json", side_effect=wrapped_atomic_write_json):
            discard_thread = threading.Thread(target=run_discard)
            rebase_thread = threading.Thread(target=run_rebase)
            discard_thread.start()
            self.assertTrue(first_write_started.wait(timeout=5))
            rebase_thread.start()
            self.assertFalse(second_write_seen.wait(timeout=0.2))
            release_first_write.set()
            discard_thread.join(timeout=5)
            rebase_thread.join(timeout=5)

        self.assertFalse(errors, errors)
        self.assertIn("discard", results)
        self.assertIn("rebase", results)
        self.assertTrue(second_write_seen.is_set())

        index = load_branch_index(self.repo)
        entries = {str(entry["id"]): dict(entry) for entry in index["entries"]}
        first_packet = json.loads(Path(first["packet_path"]).read_text(encoding="utf-8"))
        second_packet = json.loads(Path(second["packet_path"]).read_text(encoding="utf-8"))

        self.assertEqual("discarded", first_packet["status"])
        self.assertEqual("written", first_packet["branch_index_status"])
        self.assertEqual("review_required", second_packet["status"])
        self.assertEqual("written", second_packet["branch_index_status"])
        self.assertEqual("discarded", entries[str(first["packet_id"])]["status"])
        self.assertEqual("review_required", entries[str(second["packet_id"])]["status"])
        self.assertEqual("requested", entries[str(second["packet_id"])]["rebase_status"])

    def test_packet_write_failure_leaves_prior_branch_index_usable(self) -> None:
        packet = self._prepare_validation_packet()
        packet_path = Path(packet["packet_path"])
        before = load_branch_index(self.repo)
        original_atomic_write_json = pr_queue_module.atomic_write_json
        fail_once = {"value": False}

        def wrapped_atomic_write_json(path: Path, payload: object) -> None:
            if (
                not fail_once["value"]
                and Path(path).resolve() == packet_path.resolve()
                and isinstance(payload, dict)
                and str(payload.get("status") or "") == "discarded"
                and str(payload.get("branch_index_status") or "") == "pending"
            ):
                fail_once["value"] = True
                raise OSError("simulated packet write failure")
            original_atomic_write_json(path, payload)

        with patch("agent_runner.pr_queue.atomic_write_json", side_effect=wrapped_atomic_write_json):
            with self.assertRaises(OSError):
                discard_review_packet(self.repo, str(packet["packet_id"]), reason="duplicate_scope")

        after = load_branch_index(self.repo)
        packet_data = json.loads(packet_path.read_text(encoding="utf-8"))

        self.assertEqual(before, after)
        self.assertEqual("pr_queued", packet_data["status"])
        self.assertEqual("written", packet_data["branch_index_status"])

    def test_reconcile_repairs_interrupted_packet_index_update_without_losing_packet_metadata(self) -> None:
        packet = self._prepare_branch_queue_packet(worktree_dir="", remove_worktree_after=True)
        packet_path = Path(packet["packet_path"])

        self._update_packet_file(
            packet_path,
            status="review_required",
            approval_status="rebase_requested",
            approvalStatus="rebase_requested",
            validation_status="validation_pending",
            validationStatus="validation_pending",
            validation_reason="rebase_requested",
            validationReason="rebase_requested",
            validation_detail="Rebase requested; rerun validation after updating the branch.",
            validationDetail="Rebase requested; rerun validation after updating the branch.",
            rebase_status="requested",
            rebaseStatus="requested",
            rebase_reason="source_head_advanced",
            rebaseReason="source_head_advanced",
            branch_index_status="pending",
        )

        dry_run = reconcile_review_queue(self.repo)
        item = next(item for item in dry_run["items"] if str(item.get("id") or "") == str(packet["packet_id"]))

        self.assertEqual("stale", item["branch_index_state"])

        applied = reconcile_review_queue(self.repo, apply=True)
        packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
        index_data = load_branch_index(self.repo)
        index_entry = next(entry for entry in index_data["entries"] if str(entry["id"]) == str(packet["packet_id"]))

        self.assertEqual(1, applied["summary"]["applied_updates"])
        self.assertEqual("requested", packet_data["rebase_status"])
        self.assertEqual("source_head_advanced", packet_data["rebase_reason"])
        self.assertEqual("written", packet_data["branch_index_status"])
        self.assertFalse(packet_data["source_main_mutated"])
        self.assertEqual("review_required", index_entry["status"])
        self.assertEqual("rebase_requested", index_entry["approval_status"])
        self.assertEqual("validation_pending", index_entry["validation_status"])
        self.assertEqual("requested", index_entry["rebase_status"])
        self.assertEqual("source_head_advanced", index_entry["rebase_reason"])

    def test_validate_review_packet_uses_isolated_worktree_and_persists_artifacts(self) -> None:
        packet = self._prepare_validation_packet()
        self._write_pr_queue_validation_config(build_enabled=True, run_tests=True)
        validation_run_dir = self.repo / ".AgentCLI" / "agent_runs" / self.run_dir.name

        calls: list[dict[str, object]] = []

        async def fake_run_build_validation_async(
            repo: Path,
            build_cmd: object,
            build_timeout_sec: int,
            legacy_build_target: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            calls.append(
                {
                    "gate": "build",
                    "repo": Path(repo).resolve(),
                    "command_repo": Path(command_repo).resolve() if command_repo is not None else None,
                    "log_path": Path(log_path).resolve(),
                    "cmd": list(build_cmd) if isinstance(build_cmd, list) else build_cmd,
                }
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("build ok\n", encoding="utf-8")
            return {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
                "rc": 0,
                "ok": True,
                "status": "passed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "build ok",
                "failure_summary": "",
                "failureSummary": "",
            }

        async def fake_run_test_validation_async(
            repo: Path,
            test_cmd: object,
            test_timeout_sec: int,
            legacy_test_target: str,
            legacy_test_filter: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            calls.append(
                {
                    "gate": "test",
                    "repo": Path(repo).resolve(),
                    "command_repo": Path(command_repo).resolve() if command_repo is not None else None,
                    "log_path": Path(log_path).resolve(),
                    "cmd": list(test_cmd) if isinstance(test_cmd, list) else test_cmd,
                }
            )
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("test ok\n", encoding="utf-8")
            return {
                "name": "test",
                "kind": "test",
                "gate": "test",
                "cmd": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
                "rc": 0,
                "ok": True,
                "status": "passed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "test ok",
                "failure_summary": "",
                "failureSummary": "",
            }

        with (
            patch("agent_runner.pr_queue.run_build_validation_async", new=fake_run_build_validation_async),
            patch("agent_runner.pr_queue.run_test_validation_async", new=fake_run_test_validation_async),
        ):
            result = validate_review_packet(self.repo, str(packet["packet_id"]))

        packet_path = Path(result["packet_path"])
        summary_path = Path(result["summary_path"])
        packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
        summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
        signal_rows = query_pr_queue_signals(self.repo, packet_id=str(packet["packet_id"]), signal_kind="validate")
        expected_summary_path = validation_run_dir / "pr_queue_validation" / str(packet["packet_id"]) / "attempt_01" / "validation.json"

        self.assertTrue(result["ok"])
        self.assertEqual("validation_passed", result["status"])
        self.assertEqual(expected_summary_path.resolve(), summary_path.resolve())
        self.assertTrue(packet_path.exists())
        self.assertTrue(summary_path.exists())
        self.assertEqual("validation_passed", packet_data["validation_status"])
        self.assertEqual(summary_path.as_posix(), packet_data["validation_artifact_path"])
        self.assertEqual(
            [
                (validation_run_dir / "pr_queue_validation" / str(packet["packet_id"]) / "attempt_01" / "build.txt").as_posix(),
                (validation_run_dir / "pr_queue_validation" / str(packet["packet_id"]) / "attempt_01" / "test.txt").as_posix(),
                (validation_run_dir / "pr_queue_validation" / str(packet["packet_id"]) / "attempt_01" / "fast_web_worktree_regression.json").as_posix(),
            ],
            packet_data["validation_artifacts"],
        )
        self.assertEqual(packet_data["validation_artifacts"], result["validation_artifacts"])
        self.assertEqual(self.run_dir.name, packet_data["run_id"])
        self.assertEqual(packet["goal_trace"], packet_data["goal_trace"])
        self.assertEqual(packet["goal_trace"], summary_data["goal_trace"])
        self.assertTrue(all(record["goal_trace"] == packet["goal_trace"] for record in result["validation_records"]))
        self.assertEqual(3, len(result["validation_records"]))
        self.assertTrue(result["validation_plan"]["build_enabled"])
        self.assertTrue(result["validation_plan"]["run_tests"])
        self.assertFalse(result["validation_plan"]["fast_regression_applicable"])
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0]["repo"], calls[1]["repo"])
        self.assertEqual(calls[0]["repo"], Path(result["worktree_dir"]).resolve())
        self.assertFalse(str(calls[0]["repo"]).startswith(str(self.repo.resolve())))
        self.assertEqual(self.repo.resolve(), calls[0]["command_repo"])
        self.assertEqual(self.repo.resolve(), calls[1]["command_repo"])
        self.assertTrue(result["worktree_created"])
        self.assertTrue(result["worktree_removed"])
        self.assertFalse(result["source_main_mutated"])
        self.assertEqual(packet["source_head_before"], result["source_head_before"])
        self.assertEqual(packet["source_head_before"], result["source_head_after"])
        self.assertEqual("validation_passed", summary_data["status"])
        self.assertEqual(3, summary_data["validation_summary"]["records_total"])
        self.assertEqual(3, summary_data["validation_summary"]["records_passed"])
        self.assertEqual(1, len(signal_rows))
        self.assertEqual("validate", signal_rows[0]["signal_kind"])
        self.assertEqual("validation_passed", signal_rows[0]["decision_status"])
        self.assertEqual("T1", signal_rows[0]["task_id"])
        self.assertEqual(packet["goal_trace"], signal_rows[0]["goal_trace"])
        self.assertEqual(packet["task_branch"].branch_name, signal_rows[0]["branch"])
        self.assertEqual(summary_path.as_posix(), signal_rows[0]["metadata"]["summary_path"])
        build_record, test_record, fast_record = result["validation_records"]
        self.assertTrue(build_record["required"])
        self.assertTrue(build_record["applicable"])
        self.assertTrue(test_record["required"])
        self.assertTrue(test_record["applicable"])
        self.assertFalse(fast_record["required"])
        self.assertFalse(fast_record["applicable"])

    def test_validate_review_packet_failed_validation_marks_validation_failed(self) -> None:
        packet = self._prepare_validation_packet()
        self._write_pr_queue_validation_config(build_enabled=True, run_tests=True)
        validation_run_dir = self.repo / ".AgentCLI" / "agent_runs" / self.run_dir.name

        async def fake_run_build_validation_async(
            repo: Path,
            build_cmd: object,
            build_timeout_sec: int,
            legacy_build_target: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("AssertionError: expected build output\n", encoding="utf-8")
            return {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
                "rc": 1,
                "ok": False,
                "status": "failed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "AssertionError: expected build output",
                "failure_summary": "AssertionError: expected build output",
                "failureSummary": "AssertionError: expected build output",
            }

        with (
            patch("agent_runner.pr_queue.run_build_validation_async", new=fake_run_build_validation_async),
            patch("agent_runner.pr_queue.run_test_validation_async", side_effect=AssertionError("test validation should not run")),
            patch("agent_runner.pr_queue.run_fast_web_worktree_regression_async", side_effect=AssertionError("fast regression should not run")),
        ):
            result = validate_review_packet(self.repo, str(packet["packet_id"]))

        packet_data = json.loads(Path(result["packet_path"]).read_text(encoding="utf-8"))
        signal_rows = query_pr_queue_signals(
            self.repo,
            packet_id=str(packet["packet_id"]),
            signal_kind="validate",
            decision_status="validation_failed",
        )

        self.assertFalse(result["ok"])
        self.assertEqual("validation_failed", result["status"])
        self.assertEqual("validation_failed", packet_data["validation_status"])
        self.assertEqual(3, len(result["validation_records"]))
        self.assertEqual(1, result["validation_summary"]["records_failed"])
        self.assertEqual(2, result["validation_summary"]["records_pending"])
        self.assertFalse(result["source_main_mutated"])
        self.assertTrue(result["worktree_created"])
        self.assertTrue(result["worktree_removed"])
        self.assertEqual(
            (validation_run_dir / "pr_queue_validation" / str(packet["packet_id"]) / "attempt_01" / "validation.json").resolve(),
            Path(result["summary_path"]).resolve(),
        )
        self.assertEqual("build_failed", packet_data["validation_reason"])
        self.assertIn("expected build output", packet_data["validation_detail"])
        self.assertTrue(all(record["goal_trace"] == packet["goal_trace"] for record in result["validation_records"]))
        self.assertEqual(1, len(signal_rows))
        self.assertEqual("build_failed", signal_rows[0]["reason"])
        self.assertEqual(packet["goal_trace"], signal_rows[0]["goal_trace"])

    def test_validate_review_packet_blocked_environment_marks_blocked_env(self) -> None:
        packet = self._prepare_validation_packet()
        self._write_pr_queue_validation_config(build_enabled=True, run_tests=True)
        validation_run_dir = self.repo / ".AgentCLI" / "agent_runs" / self.run_dir.name

        async def fake_run_build_validation_async(
            repo: Path,
            build_cmd: object,
            build_timeout_sec: int,
            legacy_build_target: str,
            log_path: Path,
            *,
            stop_path: Path | None = None,
            max_output_bytes: int = 10_000_000,
            command_repo: Path | None = None,
        ) -> dict[str, object]:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("python: command not found\n", encoding="utf-8")
            return {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
                "rc": 127,
                "ok": False,
                "status": "failed",
                "artifact_path": log_path.as_posix(),
                "artifactPath": log_path.as_posix(),
                "log_path": log_path.as_posix(),
                "logPath": log_path.as_posix(),
                "summary": "python: command not found",
                "failure_summary": "python: command not found",
                "failureSummary": "python: command not found",
            }

        with (
            patch("agent_runner.pr_queue.run_build_validation_async", new=fake_run_build_validation_async),
            patch("agent_runner.pr_queue.run_test_validation_async", side_effect=AssertionError("test validation should not run")),
            patch("agent_runner.pr_queue.run_fast_web_worktree_regression_async", side_effect=AssertionError("fast regression should not run")),
        ):
            result = validate_review_packet(self.repo, str(packet["packet_id"]))

        packet_data = json.loads(Path(result["packet_path"]).read_text(encoding="utf-8"))

        self.assertFalse(result["ok"])
        self.assertEqual("blocked_env", result["status"])
        self.assertEqual("blocked_env", packet_data["validation_status"])
        self.assertEqual(1, result["validation_summary"]["records_blocked_env"])
        self.assertEqual(2, result["validation_summary"]["records_pending"])
        self.assertTrue(result["worktree_created"])
        self.assertTrue(result["worktree_removed"])
        self.assertEqual(
            (validation_run_dir / "pr_queue_validation" / str(packet["packet_id"]) / "attempt_01" / "validation.json").resolve(),
            Path(result["summary_path"]).resolve(),
        )
        self.assertEqual("build_failed", packet_data["validation_reason"])
        self.assertIn("command not found", packet_data["validation_detail"])
        self.assertTrue(all(record["goal_trace"] == packet["goal_trace"] for record in result["validation_records"]))

    def test_merge_review_packet_records_approval_after_validation_passed(self) -> None:
        packet = self._prepare_validated_packet()
        expected_phrase = pr_queue_merge_confirmation_phrase(str(packet["packet_id"]))
        source_head_before = git_head(self.repo)

        result = merge_review_packet(self.repo, str(packet["packet_id"]), approval_phrase=expected_phrase)
        packet_data = json.loads(Path(result["packet_path"]).read_text(encoding="utf-8"))
        signal_rows = query_pr_queue_signals(
            self.repo,
            packet_id=str(packet["packet_id"]),
            signal_kind="merge",
            decision_status="approved",
        )

        self.assertTrue(result["ok"])
        self.assertEqual("merge", result["action"])
        self.assertEqual("approved", result["status"])
        self.assertEqual("approved", result["approval_status"])
        self.assertEqual(expected_phrase, result["approval"]["required_phrase"])
        self.assertEqual("approved", result["merge_outcome"]["status"])
        self.assertFalse(result["merge_outcome"]["committed"])
        self.assertFalse(result["source_main_mutated"])
        self.assertEqual(source_head_before, git_head(self.repo))
        self.assertEqual("approved", packet_data["status"])
        self.assertEqual("approved", packet_data["approval_status"])
        self.assertEqual("approved", packet_data["merge_status"])
        self.assertEqual("validation_passed", packet_data["validation_status"])
        self.assertEqual(expected_phrase, packet_data["approval"]["required_phrase"])
        self.assertEqual(1, len(signal_rows))
        self.assertEqual("approval_confirmed", signal_rows[0]["reason"])
        self.assertEqual(packet["goal_trace"], signal_rows[0]["goal_trace"])
        self.assertEqual(packet_data["branch"], signal_rows[0]["branch"])

    def test_merge_review_packet_rejects_missing_packet(self) -> None:
        with self.assertRaises(PrQueueMergeError) as ctx:
            merge_review_packet(
                self.repo,
                "pr-missing",
                approval_phrase=pr_queue_merge_confirmation_phrase("pr-missing"),
            )
        self.assertEqual("packet_missing", ctx.exception.code)

    def test_merge_review_packet_rejects_validation_statuses_that_cannot_merge(self) -> None:
        packet = self._prepare_validation_packet()
        for validation_status in ("validation_pending", "tests_skipped", "no_tests_found", "validation_failed", "blocked_env"):
            with self.subTest(validation_status=validation_status):
                self._set_packet_validation_status(packet, validation_status)
                with self.assertRaises(PrQueueMergeError) as ctx:
                    merge_review_packet(
                        self.repo,
                        str(packet["packet_id"]),
                        approval_phrase=pr_queue_merge_confirmation_phrase(str(packet["packet_id"])),
                    )
                self.assertEqual(validation_status, ctx.exception.code)

    def test_merge_review_packet_rejects_source_dirty(self) -> None:
        packet = self._prepare_validated_packet()
        dirty_path = self.repo / "dirty.txt"
        dirty_path.write_text("dirty\n", encoding="utf-8")

        with self.assertRaises(PrQueueMergeError) as ctx:
            merge_review_packet(
                self.repo,
                str(packet["packet_id"]),
                approval_phrase=pr_queue_merge_confirmation_phrase(str(packet["packet_id"])),
            )
        self.assertEqual("source_dirty", ctx.exception.code)

    def test_merge_review_packet_rejects_stale_packet_after_source_head_changes(self) -> None:
        packet = self._prepare_validated_packet()
        (self.repo / "advance.txt").write_text("advance\n", encoding="utf-8")
        self._git("add", "advance.txt")
        self._git("commit", "-m", "advance")

        with self.assertRaises(PrQueueMergeError) as ctx:
            merge_review_packet(
                self.repo,
                str(packet["packet_id"]),
                approval_phrase=pr_queue_merge_confirmation_phrase(str(packet["packet_id"])),
            )
        self.assertEqual("packet_stale", ctx.exception.code)

    def test_merge_review_packet_rejects_approval_mismatch(self) -> None:
        packet = self._prepare_validated_packet()

        with self.assertRaises(PrQueueMergeError) as ctx:
            merge_review_packet(self.repo, str(packet["packet_id"]), approval_phrase="WRONG")
        self.assertEqual("approval_mismatch", ctx.exception.code)
        signal_rows = query_pr_queue_signals(
            self.repo,
            packet_id=str(packet["packet_id"]),
            signal_kind="merge",
            decision_status="rejected",
        )
        self.assertEqual(1, len(signal_rows))
        self.assertEqual("approval_mismatch", signal_rows[0]["reason"])
        self.assertEqual(packet["goal_trace"], signal_rows[0]["goal_trace"])


if __name__ == "__main__":
    unittest.main()
