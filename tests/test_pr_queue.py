from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


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
from agent_runner.pr_queue import (
    load_branch_index,
    pr_branch_index_path,
    pr_packet_path,
    queue_review_packet,
    validate_review_packet,
)
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


if __name__ == "__main__":
    unittest.main()
