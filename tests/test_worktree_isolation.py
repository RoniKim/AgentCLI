from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import sys
import threading
import unittest
import uuid
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.gitops import (
    WORKTREE_MERGE_PENDING,
    WorktreeSafetyError,
    _cleanup_pytest_cache_tempdirs,
    abandon_task_branch,
    create_task_branch,
    git_head,
    git_repo_state,
    _git_data_lines,
    create_worktree,
    default_worktree_dir,
    remove_worktree,
    scan_worktree_diagnostics,
)
from agent_runner.pr_queue import (
    load_branch_index,
    pr_branch_index_path,
    pr_packet_path,
    queue_review_packet,
    validate_review_packet,
)
from agent_runner.preflight import check_runner_start_readiness
from agent_runner.remote.controller import read_runner_control_event
from agent_runner.shell import RunnerShell
from agent_runner.stop_progress import read_stop_progress, write_stop_progress
from agent_runner.utils import run_cmd


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class WorktreeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = Path.home() / ".codex" / "memories" / "agentcli-worktree-isolation-tests"
        self.fixture_base.mkdir(parents=True, exist_ok=True)
        self.fixture_root = self.fixture_base / f"t-{uuid.uuid4().hex[:12]}"
        self.fixture_root.mkdir()
        self.repo = self.fixture_root / "repo"
        self.repo.mkdir()
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260425-211701"
        self.run_dir.mkdir(parents=True)
        self.worktree = self.fixture_root / "worktree"
        self.patch_path = self.run_dir / "worktree.patch"
        self.contract_path = self.run_dir / "WORKTREE_REUSE_CONTRACT.json"
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        code, out = run_cmd(["git", *args], cwd=cwd or self.repo, timeout_sec=60)
        self.assertEqual(code, 0, out)
        return out

    def _init_repo(self) -> str:
        self._git("init")
        self._git("config", "user.email", "agentcli@example.invalid")
        self._git("config", "user.name", "AgentCLI Test")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "base")
        return self._git("rev-parse", "HEAD").strip()

    def _source_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def _ensure_source_venv(self) -> Path:
        python_rel = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        python_path = self.repo / ".venv" / python_rel
        python_path.parent.mkdir(parents=True, exist_ok=True)
        python_path.write_text("", encoding="utf-8")
        return python_path

    def test_pytest_cache_tempdir_cleanup_retries_transient_directory_lock(self) -> None:
        temp_dir = self.repo / "pytest-cache-files-retry"
        temp_dir.mkdir()
        calls = {"count": 0}
        real_rmtree = shutil.rmtree

        def fake_rmtree(path: Path | str, *args: object, **kwargs: object) -> object:
            if Path(path).resolve() == temp_dir.resolve() and calls["count"] == 0:
                calls["count"] += 1
                raise PermissionError(13, "Permission denied", str(temp_dir))
            return real_rmtree(path, *args, **kwargs)

        with (
            patch("agent_runner.gitops.shutil.rmtree", side_effect=fake_rmtree),
            patch("agent_runner.gitops.time.sleep", return_value=None),
        ):
            result = _cleanup_pytest_cache_tempdirs(self.repo, max_attempts=2, initial_backoff_seconds=0)

        self.assertFalse(temp_dir.exists())
        self.assertEqual([temp_dir.as_posix()], result["removed"])
        self.assertEqual([], result["locked"])

    def test_pytest_cache_tempdir_cleanup_reports_persistent_directory_lock(self) -> None:
        temp_dir = self.repo / "pytest-cache-files-locked"
        temp_dir.mkdir()

        def fake_rmtree(path: Path | str, *args: object, **kwargs: object) -> object:
            if Path(path).resolve() == temp_dir.resolve():
                raise PermissionError(13, "Permission denied", str(temp_dir))
            return shutil.rmtree(path, *args, **kwargs)

        with (
            patch("agent_runner.gitops.shutil.rmtree", side_effect=fake_rmtree),
            patch("agent_runner.gitops.time.sleep", return_value=None),
        ):
            result = _cleanup_pytest_cache_tempdirs(self.repo, max_attempts=2, initial_backoff_seconds=0)

        self.assertTrue(temp_dir.exists())
        self.assertEqual([], result["removed"])
        self.assertEqual(temp_dir.as_posix(), result["locked"][0]["path"])
        self.assertEqual(2, len(result["locked"][0]["attempts"]))

    def _load_contract(self) -> dict[str, object]:
        return json.loads(self.contract_path.read_text(encoding="utf-8"))

    def _write_contract(self, **updates: object) -> None:
        payload = self._load_contract()
        payload.update(updates)
        self.contract_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _write_residual_cleanup_artifact(self, *, generated_worktree: Path | None = None) -> tuple[Path, str]:
        worktree_dir = generated_worktree or (self.fixture_root / ".agentcli_worktrees" / self.repo.name / "residual")
        worktree_dir.mkdir(parents=True, exist_ok=True)
        self.patch_path.write_text("diff --git a/a b/a\n", encoding="utf-8")
        locked_path = (worktree_dir / "nested" / "locked.txt").as_posix()
        locked_message = str(PermissionError(13, "Permission denied", locked_path))
        payload = {
            "schema_version": 1,
            "status": "applied_cleanup_failed",
            "created_at": "2026-04-27T12:01:00",
            "source_repo": self.repo.resolve().as_posix(),
            "run_dir": self.run_dir.resolve().as_posix(),
            "worktree_dir": worktree_dir.resolve().as_posix(),
            "patch_path": self.patch_path.resolve().as_posix(),
            "cleanup_path": locked_path,
            "cleanup_message": locked_message,
            "cleanup_details": {
                "path": locked_path,
                "locking_path": locked_path,
                "affected_artifact": worktree_dir.resolve().as_posix(),
                "worktree_dir": worktree_dir.resolve().as_posix(),
                "operation": "shutil.rmtree",
                "permission_detail": locked_message,
                "reboot_required": True,
                "reboot_guidance": "Close the locking process or reboot Windows before retrying cleanup.",
                "admin_guidance": "Ask an administrator to remove the residual directory if the ACL lock persists.",
                "git_worktree_registration": {
                    "repo": self.repo.resolve().as_posix(),
                    "worktree_dir": worktree_dir.resolve().as_posix(),
                    "registered": False,
                    "registered_path": "",
                    "rc": 0,
                    "output": "",
                },
                "residual_directory": True,
            },
            "cleanup_attempts": [
                {
                    "attempt": 1,
                    "operation": "shutil.rmtree",
                    "path": locked_path,
                    "locking_path": locked_path,
                    "affected_artifact": worktree_dir.resolve().as_posix(),
                    "worktree_dir": worktree_dir.resolve().as_posix(),
                    "error_type": "PermissionError",
                    "message": locked_message,
                    "errno": 13,
                }
            ],
            "cleanup_reconciliation": {
                "artifact_status": "applied_cleanup_failed",
                "final_status": "applied",
                "worktree_dir": worktree_dir.resolve().as_posix(),
                "worktree_exists": True,
                "cleanup_path": locked_path,
                "cleanup_path_exists": False,
                "pending_marker_paths": [],
                "existing_pending_markers": [],
                "marker_state": "reconciled",
                "worktree_state": "present",
                "blocking_paths": [worktree_dir.resolve().as_posix()],
                "reconciled": False,
                "reconciled_from": "",
                "git_worktree_registration": {
                    "repo": self.repo.resolve().as_posix(),
                    "worktree_dir": worktree_dir.resolve().as_posix(),
                    "registered": False,
                    "registered_path": "",
                    "rc": 0,
                    "output": "",
                },
                "residual_directory": True,
            },
            "base_ref": "main",
            "head_ref": "abc12345",
            "last_rc": 0,
        }
        self.run_dir.joinpath("WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return worktree_dir, locked_path

    def _prepare_validation_packet(self) -> dict[str, object]:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        goal_trace = [
            {
                "goal_ref": "GOAL-1",
                "goal_text": "Keep validation work in an isolated temporary worktree.",
            }
        ]
        tb = create_task_branch(
            self.worktree,
            "T1",
            task_title="Validate isolated PR packet",
            goal_trace=goal_trace,
        )
        (self.worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)
        branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        abandon_task_branch(self.worktree, tb)
        remove_worktree(self.repo, self.worktree)

        (self.run_dir / "last_run_summary.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "build_enabled": True,
                    "run_tests": True,
                    "build_cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
                    "test_cmd": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
                    "build_timeout_seconds": 60,
                    "test_timeout_seconds": 60,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

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
            "goal_trace": goal_trace,
            "source_head_before": source_head_before,
        }

    def test_default_worktree_dir_is_outside_source_repo(self) -> None:
        worktree_dir = default_worktree_dir(self.repo, self.run_dir)

        self.assertFalse(_is_relative_to(worktree_dir, self.repo))
        self.assertIn(".agentcli_worktrees", {part.lower() for part in worktree_dir.parts})
        self.assertEqual(worktree_dir.name, self.run_dir.name)

    def test_default_worktree_dir_respects_external_env_override(self) -> None:
        custom_root = self.fixture_root / "custom-worktrees"

        with patch.dict(os.environ, {"AGENTCLI_WORKTREE_HOME": str(custom_root)}, clear=False):
            worktree_dir = default_worktree_dir(self.repo, self.run_dir)

        self.assertEqual(worktree_dir, (custom_root / self.repo.name / self.run_dir.name).resolve())
        self.assertFalse(_is_relative_to(worktree_dir, self.repo))

    def test_create_worktree_rejects_nested_source_repo_path(self) -> None:
        nested_worktree = self.repo / ".AgentCLI" / "agent_runs" / "nested" / "worktree"

        with self.assertRaisesRegex(RuntimeError, "inside the source repository"):
            create_worktree(self.repo, nested_worktree)

    def test_create_worktree_writes_and_reuses_matching_contract(self) -> None:
        expected_head = self._init_repo()

        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)
        self.assertTrue(self.contract_path.exists())
        contract = self._load_contract()
        self.assertEqual(self.run_dir.name, contract["run_id"])
        self.assertEqual(self.repo.resolve().as_posix(), contract["source_repo"])
        self.assertEqual(expected_head, contract["expected_head"])
        self.assertEqual("clean", contract["source_repo_state"])
        self.assertEqual("clean", contract["worktree_state"])

        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)
        self.assertTrue(self.worktree.exists())
        self.assertEqual(expected_head, self._git("rev-parse", "HEAD").strip())

    def test_create_worktree_rejects_reuse_when_run_id_mismatches(self) -> None:
        self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)
        self._write_contract(run_id="20260425-000000")

        with self.assertRaises(WorktreeSafetyError) as ctx:
            create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        self.assertEqual("worktree_reuse_run_id_mismatch", ctx.exception.code)

    def test_create_worktree_rejects_reuse_when_expected_head_mismatches(self) -> None:
        self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)
        self._write_contract(expected_head="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")

        with self.assertRaises(WorktreeSafetyError) as ctx:
            create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        self.assertEqual("worktree_reuse_expected_head_mismatch", ctx.exception.code)

    def test_create_worktree_rejects_reuse_when_branch_mismatches(self) -> None:
        self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)
        self._write_contract(branch="feature/reuse-test")

        with self.assertRaises(WorktreeSafetyError) as ctx:
            create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        self.assertEqual("worktree_reuse_branch_mismatch", ctx.exception.code)

    def test_create_worktree_rejects_reuse_when_state_mismatches(self) -> None:
        self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)
        (self.worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        with self.assertRaises(WorktreeSafetyError) as ctx:
            create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        self.assertEqual("worktree_reuse_state_mismatch", ctx.exception.code)

    def test_create_worktree_rejects_reuse_when_source_repo_ownership_mismatches(self) -> None:
        expected_head = self._init_repo()
        self._git("worktree", "add", "--detach", self.worktree.as_posix(), "HEAD")
        worktree_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        worktree_branch = self._git("rev-parse", "--abbrev-ref", "HEAD", cwd=self.worktree).strip()
        contract = {
            "schema_version": 1,
            "run_id": self.run_dir.name,
            "created_at": "2026-04-26T12:02:00",
            "source_repo": self.repo.resolve().as_posix(),
            "source_repo_root": self.repo.resolve().as_posix(),
            "branch": self._source_branch(),
            "expected_head": expected_head,
            "base_ref": expected_head,
            "head_ref": worktree_head,
            "source_repo_state": "clean",
            "worktree_state": "clean",
            "worktree_branch": worktree_branch,
            "worktree_dir": self.worktree.resolve().as_posix(),
        }
        self.contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._write_contract(
            source_repo=(self.fixture_root / "other-repo").as_posix(),
            source_repo_root=(self.fixture_root / "other-repo").as_posix(),
        )

        with self.assertRaises(WorktreeSafetyError) as ctx:
            create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        self.assertEqual("worktree_reuse_source_repo_mismatch", ctx.exception.code)

    def test_git_data_lines_drop_warnings_without_changing_porcelain_status(self) -> None:
        out = (
            " M agent_runner/gitops.py\n"
            "warning: unable to access global ignore\n"
            "?? tests/test_worktree_isolation.py\n"
        )

        self.assertEqual(
            _git_data_lines(out),
            [" M agent_runner/gitops.py", "?? tests/test_worktree_isolation.py"],
        )

    def test_scan_worktree_diagnostics_returns_ok_when_no_artifacts_exist(self) -> None:
        diagnostics = scan_worktree_diagnostics(self.repo)

        self.assertEqual("ok", diagnostics["status"])
        self.assertTrue(diagnostics["summary"]["healthy"])
        self.assertEqual(0, diagnostics["summary"]["issue_count"])
        self.assertEqual([], diagnostics["issues"])
        self.assertEqual([], diagnostics["pending_markers"])
        self.assertEqual([], diagnostics["cleanup_failed"])
        self.assertEqual([], diagnostics["generated_worktrees"])

    def test_scan_worktree_diagnostics_reports_stale_marker_and_missing_patch(self) -> None:
        self.worktree.mkdir(parents=True, exist_ok=True)
        marker = {
            "schema_version": 1,
            "status": "pending",
            "created_at": "2026-04-27T12:00:00",
            "source_repo": self.repo.resolve().as_posix(),
            "run_dir": self.run_dir.resolve().as_posix(),
            "worktree_dir": self.worktree.resolve().as_posix(),
            "patch_path": self.patch_path.resolve().as_posix(),
            "base_ref": "main",
            "head_ref": "abc12345",
            "last_rc": 0,
        }
        self.patch_path.write_text("diff --git a/a b/a\n", encoding="utf-8")
        self.run_dir.joinpath(WORKTREE_MERGE_PENDING).write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.patch_path.unlink()

        diagnostics = scan_worktree_diagnostics(self.repo)
        issue_kinds = {str(issue["kind"]) for issue in diagnostics["issues"]}

        self.assertEqual("warning", diagnostics["status"])
        self.assertIn("stale_pending_marker", issue_kinds)
        self.assertIn("missing_patch", issue_kinds)
        self.assertTrue(diagnostics["pending_markers"][0]["stale"])
        self.assertIn("patch", diagnostics["pending_markers"][0]["reason"])
        self.assertEqual("stale_marker_prune", diagnostics["pending_markers"][0]["resolution_actions"][0]["kind"])
        self.assertEqual("required", diagnostics["pending_markers"][0]["resolution_actions"][0]["status"])

    def test_scan_worktree_diagnostics_recovers_after_stale_marker_repair(self) -> None:
        self.worktree.mkdir(parents=True, exist_ok=True)
        marker = {
            "schema_version": 1,
            "status": "pending",
            "created_at": "2026-04-27T12:00:00",
            "source_repo": self.repo.resolve().as_posix(),
            "run_dir": self.run_dir.resolve().as_posix(),
            "worktree_dir": self.worktree.resolve().as_posix(),
            "patch_path": self.patch_path.resolve().as_posix(),
            "base_ref": "main",
            "head_ref": "abc12345",
            "last_rc": 0,
        }
        self.patch_path.write_text("diff --git a/a b/a\n", encoding="utf-8")
        central_pending = self.repo / ".AgentCLI" / WORKTREE_MERGE_PENDING
        central_pending.parent.mkdir(parents=True, exist_ok=True)
        central_pending.write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        stale = scan_worktree_diagnostics(self.repo)
        self.assertEqual("warning", stale["status"])
        self.assertTrue(stale["pending_markers"][0]["stale"])

        self.run_dir.joinpath(WORKTREE_MERGE_PENDING).write_text(json.dumps(marker, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        repaired = scan_worktree_diagnostics(self.repo)
        self.assertEqual("ok", repaired["status"])
        self.assertFalse(repaired["pending_markers"][0]["stale"])
        self.assertEqual([], repaired["issues"])

    def test_scan_worktree_diagnostics_reports_cleanup_failed_artifact(self) -> None:
        generated_worktree = self.fixture_root / ".agentcli_worktrees" / self.repo.name / "cleanup-failed"
        generated_worktree.mkdir(parents=True, exist_ok=True)
        (generated_worktree / ".git").write_text("gitdir: ../.git/worktrees/cleanup-failed\n", encoding="utf-8")
        self.patch_path.write_text("diff --git a/a b/a\n", encoding="utf-8")
        payload = {
            "schema_version": 1,
            "status": "applied_cleanup_failed",
            "created_at": "2026-04-27T12:01:00",
            "source_repo": self.repo.resolve().as_posix(),
            "run_dir": self.run_dir.resolve().as_posix(),
            "worktree_dir": generated_worktree.resolve().as_posix(),
            "patch_path": self.patch_path.resolve().as_posix(),
            "cleanup_path": generated_worktree.resolve().as_posix(),
            "cleanup_message": "cleanup failed",
            "base_ref": "main",
            "head_ref": "abc12345",
            "last_rc": 0,
        }
        self.run_dir.joinpath("WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        diagnostics = scan_worktree_diagnostics(self.repo)
        issue_kinds = {str(issue["kind"]) for issue in diagnostics["issues"]}

        self.assertEqual("error", diagnostics["status"])
        self.assertIn("cleanup_failed", issue_kinds)
        self.assertTrue(diagnostics["cleanup_failed"])
        self.assertFalse(diagnostics["generated_worktrees"][0]["orphaned"])
        self.assertFalse(diagnostics["cleanup_failed"][0]["reconciliation"]["reconciled"])
        self.assertEqual(generated_worktree.resolve().as_posix(), diagnostics["cleanup_failed"][0]["reconciliation"]["blocking_paths"][0])
        self.assertEqual("generated_worktree_remove", diagnostics["cleanup_failed"][0]["resolution_actions"][0]["kind"])
        self.assertEqual("failed", diagnostics["cleanup_failed"][0]["resolution_actions"][0]["status"])
        self.assertEqual("cleanup_failed_reconcile", diagnostics["cleanup_failed"][0]["resolution_actions"][2]["kind"])

    def test_scan_worktree_diagnostics_reports_residual_cleanup_directory_as_warning(self) -> None:
        residual_worktree, locked_path = self._write_residual_cleanup_artifact()

        diagnostics = scan_worktree_diagnostics(self.repo)
        issue_kinds = {str(issue["kind"]) for issue in diagnostics["issues"]}
        cleanup_failed = diagnostics["cleanup_failed"][0]

        self.assertEqual("warning", diagnostics["status"])
        self.assertIn("cleanup_failed", issue_kinds)
        self.assertTrue(cleanup_failed["residual_directory"])
        self.assertFalse(cleanup_failed["reconciliation"]["git_worktree_registration"]["registered"])
        self.assertEqual("warn", next(issue["severity"] for issue in diagnostics["issues"] if issue["kind"] == "cleanup_failed"))
        self.assertEqual(residual_worktree.resolve().as_posix(), next(issue["path"] for issue in diagnostics["issues"] if issue["kind"] == "cleanup_failed"))
        self.assertEqual("shutil.rmtree", cleanup_failed["cleanup_operation"])
        self.assertIn("Permission denied", cleanup_failed["permission_detail"])
        self.assertIn("reboot", cleanup_failed["reboot_guidance"].lower())
        self.assertIn("administrator", cleanup_failed["admin_guidance"].lower())
        self.assertIn("residual directory", cleanup_failed["resolution_actions"][-1]["detail"].lower())
        self.assertEqual(residual_worktree.resolve().as_posix(), cleanup_failed["reconciliation"]["blocking_paths"][0])
        self.assertEqual(locked_path, cleanup_failed["cleanup_path"])

    def test_scan_worktree_diagnostics_reconciles_cleanup_failed_artifact_after_cleanup_finishes(self) -> None:
        self.patch_path.write_text("diff --git a/a b/a\n", encoding="utf-8")
        payload = {
            "schema_version": 1,
            "status": "applied_cleanup_failed",
            "created_at": "2026-04-27T12:01:00",
            "source_repo": self.repo.resolve().as_posix(),
            "run_dir": self.run_dir.resolve().as_posix(),
            "worktree_dir": self.worktree.resolve().as_posix(),
            "patch_path": self.patch_path.resolve().as_posix(),
            "cleanup_path": self.worktree.resolve().as_posix(),
            "cleanup_message": "cleanup failed",
            "base_ref": "main",
            "head_ref": "abc12345",
            "last_rc": 0,
        }
        artifact_path = self.run_dir / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json"
        artifact_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        diagnostics = scan_worktree_diagnostics(self.repo)

        self.assertEqual("ok", diagnostics["status"])
        self.assertEqual([], diagnostics["cleanup_failed"])
        self.assertFalse(artifact_path.exists())
        reconciled_artifact = self.run_dir / "WORKTREE_MERGE_APPLIED.json"
        self.assertTrue(reconciled_artifact.exists())
        reconciled_payload = json.loads(reconciled_artifact.read_text(encoding="utf-8"))
        self.assertEqual("applied", reconciled_payload["status"])
        self.assertEqual("applied_cleanup_failed", reconciled_payload["cleanup_reconciled_from"])
        self.assertTrue(reconciled_payload["cleanup_reconciliation"]["reconciled"])
        self.assertEqual("cleanup_failed_reconcile", reconciled_payload["resolution_actions"][-1]["kind"])
        self.assertEqual("done", reconciled_payload["resolution_actions"][-1]["status"])

    def test_runner_start_readiness_is_not_blocked_by_residual_cleanup_warning(self) -> None:
        self._init_repo()
        self._ensure_source_venv()
        self._write_residual_cleanup_artifact()

        diagnostics = scan_worktree_diagnostics(self.repo)
        readiness = check_runner_start_readiness(self.repo, self.run_dir)

        self.assertEqual("warning", diagnostics["status"])
        self.assertTrue(readiness["ok"])
        self.assertEqual([], readiness["blockers"])

    def test_scan_worktree_diagnostics_reports_orphaned_generated_worktree(self) -> None:
        generated_worktree = self.fixture_root / ".agentcli_worktrees" / self.repo.name / "orphaned"
        generated_worktree.mkdir(parents=True, exist_ok=True)
        (generated_worktree / ".git").write_text("gitdir: ../.git/worktrees/orphaned\n", encoding="utf-8")

        diagnostics = scan_worktree_diagnostics(self.repo)
        issue_kinds = {str(issue["kind"]) for issue in diagnostics["issues"]}

        self.assertEqual("warning", diagnostics["status"])
        self.assertIn("orphaned_worktree", issue_kinds)
        self.assertTrue(any(item["orphaned"] for item in diagnostics["generated_worktrees"]))
        orphaned_entry = next(item for item in diagnostics["generated_worktrees"] if item["orphaned"])
        self.assertEqual("generated_worktree_remove", orphaned_entry["resolution_actions"][0]["kind"])
        self.assertEqual("required", orphaned_entry["resolution_actions"][0]["status"])

    def test_scan_worktree_diagnostics_filters_are_read_only_and_category_scoped(self) -> None:
        active_worktree = self.fixture_root / ".agentcli_worktrees" / self.repo.name / "active"
        cleanup_worktree = self.fixture_root / ".agentcli_worktrees" / self.repo.name / "cleanup"
        orphaned_worktree = self.fixture_root / ".agentcli_worktrees" / self.repo.name / "orphaned"
        stale_worktree = self.fixture_root / "stale-worktree"
        for worktree in (active_worktree, cleanup_worktree, orphaned_worktree):
            worktree.mkdir(parents=True, exist_ok=True)
            (worktree / ".git").write_text(f"gitdir: ../.git/worktrees/{worktree.name}\n", encoding="utf-8")

        active_patch = self.patch_path
        active_patch.write_text("diff --git a/active.txt b/active.txt\n", encoding="utf-8")
        active_marker = {
            "schema_version": 1,
            "status": "pending",
            "created_at": "2026-04-27T12:00:00",
            "source_repo": self.repo.resolve().as_posix(),
            "run_dir": self.run_dir.resolve().as_posix(),
            "worktree_dir": self.worktree.resolve().as_posix(),
            "patch_path": active_patch.resolve().as_posix(),
            "base_ref": "main",
            "head_ref": "abc12345",
            "last_rc": 0,
        }
        self.run_dir.joinpath(WORKTREE_MERGE_PENDING).write_text(
            json.dumps(active_marker, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        central_patch = self.run_dir / "stale.patch"
        central_patch.write_text("diff --git a/stale.txt b/stale.txt\n", encoding="utf-8")
        self.repo.joinpath(".AgentCLI", WORKTREE_MERGE_PENDING).write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "created_at": "2026-04-27T12:01:00",
                    "source_repo": self.repo.resolve().as_posix(),
                    "run_dir": self.run_dir.resolve().as_posix(),
                    "worktree_dir": stale_worktree.resolve().as_posix(),
                    "patch_path": central_patch.resolve().as_posix(),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        central_patch.unlink()

        cleanup_patch = self.run_dir / "cleanup.patch"
        cleanup_patch.write_text("diff --git a/cleanup.txt b/cleanup.txt\n", encoding="utf-8")
        self.run_dir.joinpath("WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "applied_cleanup_failed",
                    "created_at": "2026-04-27T12:02:00",
                    "source_repo": self.repo.resolve().as_posix(),
                    "run_dir": self.run_dir.resolve().as_posix(),
                    "worktree_dir": cleanup_worktree.resolve().as_posix(),
                    "patch_path": cleanup_patch.resolve().as_posix(),
                    "cleanup_path": cleanup_worktree.resolve().as_posix(),
                    "cleanup_message": "cleanup failed",
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        before = {
            "active_marker": self.run_dir.joinpath(WORKTREE_MERGE_PENDING).read_text(encoding="utf-8"),
            "central_marker": self.repo.joinpath(".AgentCLI", WORKTREE_MERGE_PENDING).read_text(encoding="utf-8"),
            "cleanup_artifact": self.run_dir.joinpath("WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").read_text(encoding="utf-8"),
            "active_patch": active_patch.read_text(encoding="utf-8"),
            "cleanup_patch": cleanup_patch.read_text(encoding="utf-8"),
            "central_patch_exists": central_patch.exists(),
            "generated_root_entries": sorted(child.name for child in (self.fixture_root / ".agentcli_worktrees" / self.repo.name).iterdir()),
        }

        for category in ("active", "pending", "stale", "orphaned", "cleanup_failed", "missing_patch"):
            diagnostics = scan_worktree_diagnostics(self.repo, categories=[category])
            selected_entries = [
                *diagnostics["pending_markers"],
                *diagnostics["cleanup_failed"],
                *diagnostics["generated_worktrees"],
                *diagnostics["issues"],
            ]

            self.assertEqual([category], diagnostics["filters"]["categories"])
            self.assertEqual(
                ["active", "pending", "stale", "orphaned", "cleanup_failed", "missing_patch"],
                diagnostics["filters"]["availableCategories"],
            )
            self.assertTrue(selected_entries)
            for entry in selected_entries:
                self.assertIn(category, entry["categories"])

        after = {
            "active_marker": self.run_dir.joinpath(WORKTREE_MERGE_PENDING).read_text(encoding="utf-8"),
            "central_marker": self.repo.joinpath(".AgentCLI", WORKTREE_MERGE_PENDING).read_text(encoding="utf-8"),
            "cleanup_artifact": self.run_dir.joinpath("WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").read_text(encoding="utf-8"),
            "active_patch": active_patch.read_text(encoding="utf-8"),
            "cleanup_patch": cleanup_patch.read_text(encoding="utf-8"),
            "central_patch_exists": central_patch.exists(),
            "generated_root_entries": sorted(child.name for child in (self.fixture_root / ".agentcli_worktrees" / self.repo.name).iterdir()),
        }

        self.assertEqual(before, after)

    def test_shell_worktree_command_prints_diagnostics_without_mutation(self) -> None:
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())

        before = {
            "pending": self.run_dir.joinpath(WORKTREE_MERGE_PENDING).exists(),
            "patch": self.patch_path.exists(),
            "worktrees": list((self.fixture_root / ".agentcli_worktrees").glob("**/*")),
        }
        stream = io.StringIO()
        with redirect_stdout(stream):
            shell.worktree([])
        output = stream.getvalue()
        after = {
            "pending": self.run_dir.joinpath(WORKTREE_MERGE_PENDING).exists(),
            "patch": self.patch_path.exists(),
            "worktrees": list((self.fixture_root / ".agentcli_worktrees").glob("**/*")),
        }

        self.assertIn("Worktree Diagnostics", output)
        self.assertIn("issues: none", output)
        self.assertEqual(before, after)

    def test_shell_worktree_command_prints_residual_cleanup_guidance(self) -> None:
        residual_worktree, _ = self._write_residual_cleanup_artifact()
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        stream = io.StringIO()

        with redirect_stdout(stream):
            shell.worktree([])

        output = stream.getvalue()
        self.assertIn("Worktree Diagnostics", output)
        self.assertIn("cleanup-failed", output)
        self.assertIn("residual", output.lower())
        self.assertIn(residual_worktree.resolve().as_posix(), output)
        self.assertIn("shutil.rmtree", output)
        self.assertIn("Permission denied", output)
        self.assertIn("reboot", output.lower())
        self.assertIn("administrator", output.lower())

    def test_runner_start_readiness_reports_missing_source_venv_and_records_shell_event(self) -> None:
        self._init_repo()
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        stream = io.StringIO()

        with (
            redirect_stdout(stream),
            patch("agent_runner.shell.run_runner", side_effect=AssertionError("runner should not start")),
        ):
            shell.start(["--run-dir", self.run_dir.as_posix()])
            if shell._runner_thread is not None:
                shell._runner_thread.join(timeout=2)

        output = stream.getvalue()
        self.assertIn("Runner start blocked by readiness checks.", output)
        self.assertIn("missing_source_venv", output)
        event = read_runner_control_event(self.run_dir)
        self.assertEqual("error", event["status"])
        self.assertEqual("readiness", event["phase"])
        readiness = event["result"]["readiness"]
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("missing_source_venv", blocker_codes)

    def test_runner_start_readiness_reports_git_safe_directory_blocker_when_git_status_is_dubious(self) -> None:
        self._init_repo()
        self._ensure_source_venv()
        dubious_output = (
            "fatal: detected dubious ownership in repository at 'C:/temp/repo'\n"
            "To add an exception for this directory, call:\n\n"
            "\tgit config --global --add safe.directory C:/temp/repo\n"
        )

        with patch("agent_runner.preflight.run_cmd", return_value=(128, dubious_output)):
            readiness = check_runner_start_readiness(self.repo, self.run_dir)

        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertFalse(readiness["ok"])
        self.assertIn("git_safe_directory_required", blocker_codes)
        blocker = next(item for item in readiness["blockers"] if item["code"] == "git_safe_directory_required")
        self.assertEqual("C:/temp/repo", blocker["details"]["safe_directory_hint"])

    def test_runner_start_readiness_reports_stale_stop_and_runner_wait_artifacts(self) -> None:
        self._init_repo()
        self._ensure_source_venv()
        (self.run_dir / "STOP").write_text("stop_file\n", encoding="utf-8")
        write_stop_progress(
            self.run_dir,
            phase="runner_wait",
            message="Waiting for runner shutdown and final artifacts.",
            requested_at_monotonic=0.0,
            running=True,
            runner_alive=True,
        )

        readiness = check_runner_start_readiness(self.repo, self.run_dir)

        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertFalse(readiness["ok"])
        self.assertIn("stale_stop_artifact", blocker_codes)
        self.assertIn("stale_runner_wait_artifact", blocker_codes)

    def test_runner_start_readiness_warns_when_generated_worktree_is_already_merged(self) -> None:
        self._init_repo()
        self._ensure_source_venv()
        generated_worktree = default_worktree_dir(self.repo, self.run_dir)
        create_worktree(self.repo, generated_worktree, run_dir=self.run_dir)
        (generated_worktree / "merged.txt").write_text("from worktree\n", encoding="utf-8")
        self._git("add", "merged.txt", cwd=generated_worktree)
        self._git("commit", "-m", "worktree change", cwd=generated_worktree)
        worktree_head = self._git("rev-parse", "HEAD", cwd=generated_worktree).strip()
        self._git("merge", "--ff-only", worktree_head)

        readiness = check_runner_start_readiness(self.repo, self.run_dir)

        warning_codes = {item["code"] for item in readiness["warnings"]}
        self.assertTrue(readiness["ok"])
        self.assertIn("generated_worktree_already_merged", warning_codes)

    def test_runner_start_readiness_reports_clean_ready_state(self) -> None:
        self._init_repo()
        self._ensure_source_venv()

        readiness = check_runner_start_readiness(self.repo, self.run_dir)

        self.assertTrue(readiness["ok"])
        self.assertEqual([], readiness["blockers"])
        self.assertEqual([], readiness["warnings"])

    def test_shell_start_creates_fresh_run_dir_by_default_and_resumes_latest_explicitly(self) -> None:
        self._init_repo()
        self._ensure_source_venv()
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        captured_run_dirs: list[Path] = []

        def fake_run_runner(args: object) -> int:
            captured_run_dirs.append(Path(str(getattr(args, "run_dir", ""))).expanduser().resolve())
            return 0

        with (
            patch("agent_runner.shell.init_process_guard", return_value=None),
            patch("agent_runner.shell.run_runner", side_effect=fake_run_runner),
        ):
            shell.start([])
            if shell._runner_thread is not None:
                shell._runner_thread.join(timeout=2)
            first_run_dir = shell.run_dir
            self.assertIsNotNone(first_run_dir)

            shell.start([])
            if shell._runner_thread is not None:
                shell._runner_thread.join(timeout=2)
            second_run_dir = shell.run_dir
            self.assertIsNotNone(second_run_dir)

            shell.start(["--resume-latest"])
            if shell._runner_thread is not None:
                shell._runner_thread.join(timeout=2)
            resumed_run_dir = shell.run_dir

        self.assertNotEqual(first_run_dir, second_run_dir)
        self.assertEqual(second_run_dir, resumed_run_dir)
        self.assertTrue(first_run_dir.exists())
        self.assertTrue(second_run_dir.exists())
        self.assertTrue(resumed_run_dir.exists())
        self.assertEqual([first_run_dir.resolve(), second_run_dir.resolve(), second_run_dir.resolve()], captured_run_dirs)

    def test_shell_stop_writes_stop_to_suffixed_actual_run_dir_when_start_state_is_split(self) -> None:
        shell = RunnerShell()
        shell.set_repo(self.repo.as_posix())
        shell.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260428-092144"
        actual_run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260428-092144-0001"
        shell.run_dir.mkdir(parents=True, exist_ok=True)
        actual_run_dir.mkdir(parents=True, exist_ok=True)
        (actual_run_dir / "BACKLOG.json").write_text('{"tasks":[]}\n', encoding="utf-8")

        release = threading.Event()
        shell._runner_thread = threading.Thread(target=lambda: release.wait(5), daemon=True)
        shell._runner_thread.start()

        try:
            with patch("agent_runner.shell.terminate_all_children", return_value=None):
                shell.stop(wait=False)
        finally:
            release.set()
            shell._runner_thread.join(timeout=2)

        self.assertTrue((shell.run_dir / "STOP").exists())
        self.assertTrue((actual_run_dir / "STOP").exists())
        progress = read_stop_progress(shell.run_dir)
        self.assertEqual((shell.run_dir / "STOP").as_posix(), progress["stop_file_paths"]["stop_file_path"])
        self.assertEqual((actual_run_dir / "STOP").as_posix(), progress["stop_file_paths"]["stop_file_path_2"])

    def test_shell_save_drops_transient_run_dir_intent(self) -> None:
        home = self.fixture_root / "home"
        home.mkdir(parents=True, exist_ok=True)
        explicit_run_dir = self.repo / ".AgentCLI" / "agent_runs" / "explicit-run"

        with patch.dict(os.environ, {"AGENTCLI_HOME": str(home)}, clear=False):
            shell = RunnerShell()
            shell.set_repo(self.repo.as_posix())
            shell.overrides["run_dir"] = explicit_run_dir.as_posix()
            shell.overrides["resume_latest"] = True
            shell.save()

        self.assertIsNotNone(shell.config_path)
        saved = json.loads(shell.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("run_dir", saved)
        self.assertNotIn("resume_latest", saved)

    def test_fast_web_worktree_regression_runs_exact_suite_and_persists_summary(self) -> None:
        from agent_runner.gates import (
            repo_has_web_worktree_markers,
            run_fast_web_worktree_regression_async,
            should_run_fast_web_worktree_regression,
        )

        (self.repo / "agent_runner").mkdir(parents=True, exist_ok=True)
        (self.repo / "web_console").mkdir(parents=True, exist_ok=True)
        (self.repo / ".doc").mkdir(parents=True, exist_ok=True)
        (self.repo / ".doc" / "GOALS.md").write_text(
            "# Project Goals\n\n## P0\n- [ ] Keep the regression gate fast.\n",
            encoding="utf-8",
        )

        self.assertTrue(repo_has_web_worktree_markers(self.repo))
        self.assertTrue(should_run_fast_web_worktree_regression(self.repo, ["agent_runner/web.py"]))
        self.assertTrue(
            should_run_fast_web_worktree_regression(
                self.repo,
                ["docs/notes.md"],
                ["web_console/app.js"],
            )
        )

        calls: list[dict[str, object]] = []

        async def fake_run_cmd_async(cmd, cwd, log_path, *, timeout_sec=600, stop_path=None, max_output_bytes=10_000_000):
            calls.append({
                "cmd": list(cmd),
                "cwd": cwd,
                "log_path": log_path,
                "timeout_sec": timeout_sec,
                "stop_path": stop_path,
                "max_output_bytes": max_output_bytes,
            })
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text("ok\n", encoding="utf-8")
            return 0, "ok"

        summary_path = self.run_dir / "fast_web_worktree_regression.json"
        with patch("agent_runner.gates.run_cmd_async", new=fake_run_cmd_async):
            result = asyncio.run(run_fast_web_worktree_regression_async(self.repo, summary_path))

        self.assertTrue(result["ok"])
        self.assertEqual(6, len(result["commands"]))
        self.assertEqual(6, len(calls))
        self.assertTrue(summary_path.exists())

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        expected_files = [
            "tests/test_web_console_readonly.py",
            "tests/test_web_console_safety.py",
            "tests/test_web_console_static.py",
            "tests/test_web_console_worktree.py",
            "tests/test_worktree_isolation.py",
            "tests/test_worktree_manual_merge.py",
        ]
        self.assertTrue(summary["ok"])
        self.assertEqual(expected_files, [item["test_file"] for item in summary["commands"]])
        self.assertEqual(expected_files, [item["test_file"] for item in result["commands"]])
        self.assertEqual(summary_path.as_posix(), summary["artifact_path"])
        self.assertEqual(summary_path.as_posix(), summary["artifactPath"])
        self.assertEqual(expected_files, summary["suite_files"])
        self.assertEqual([], summary["trigger_files"])
        self.assertEqual("", summary["failure_summary"])

        for call, expected_file in zip(calls, expected_files, strict=True):
            self.assertEqual(Path(expected_file).name, call["cmd"][-1])
            self.assertEqual(self.repo, call["cwd"])
            self.assertTrue(str(call["log_path"]).endswith(".txt"))

        log_dir = self.run_dir / "fast_web_worktree_regression"
        self.assertTrue((log_dir / "01_test_web_console_readonly.txt").exists())
        self.assertTrue((log_dir / "06_test_worktree_manual_merge.txt").exists())
        self.assertEqual("01_test_web_console_readonly.txt", Path(summary["commands"][0]["artifact_path"]).name)
        self.assertEqual("", summary["commands"][0]["failure_summary"])

    def test_fast_web_worktree_regression_stops_on_first_failure_and_records_summary(self) -> None:
        from agent_runner.gates import run_fast_web_worktree_regression_async

        (self.repo / "agent_runner").mkdir(parents=True, exist_ok=True)
        (self.repo / "web_console").mkdir(parents=True, exist_ok=True)
        (self.repo / ".doc").mkdir(parents=True, exist_ok=True)
        (self.repo / ".doc" / "GOALS.md").write_text(
            "# Project Goals\n\n## P0\n- [ ] Keep the regression gate fast.\n",
            encoding="utf-8",
        )

        calls: list[dict[str, object]] = []

        async def fake_run_cmd_async(cmd, cwd, log_path, *, timeout_sec=600, stop_path=None, max_output_bytes=10_000_000):
            test_file = str(cmd[-1])
            rc = 1 if test_file == "test_web_console_safety.py" else 0
            calls.append({
                "cmd": list(cmd),
                "cwd": cwd,
                "log_path": log_path,
                "rc": rc,
            })
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"rc={rc}\n", encoding="utf-8")
            return rc, "boom" if rc else "ok"

        summary_path = self.run_dir / "fast_web_worktree_regression.json"
        with patch("agent_runner.gates.run_cmd_async", new=fake_run_cmd_async):
            result = asyncio.run(run_fast_web_worktree_regression_async(self.repo, summary_path))

        self.assertFalse(result["ok"])
        self.assertEqual(2, len(calls))
        self.assertIsNotNone(result["failed_command"])
        self.assertEqual("test_web_console_safety", result["failed_command"]["name"])
        self.assertTrue(summary_path.exists())

        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertFalse(summary["ok"])
        self.assertEqual("test_web_console_safety", summary["failed_command"]["name"])
        self.assertEqual(2, len(summary["commands"]))
        self.assertEqual(summary_path.as_posix(), summary["artifact_path"])
        self.assertEqual(summary_path.as_posix(), summary["artifactPath"])
        self.assertIn("fast_web_worktree_regression failed:", summary["failure_summary"])
        self.assertEqual("boom", summary["failed_command"]["failure_summary"])
        self.assertEqual(
            (self.run_dir / "fast_web_worktree_regression" / "02_test_web_console_safety.txt").as_posix(),
            summary["commands"][1]["artifact_path"],
        )
        self.assertEqual("boom", summary["commands"][1]["failure_summary"])
        self.assertTrue((self.run_dir / "fast_web_worktree_regression" / "02_test_web_console_safety.txt").exists())

    def test_gate_commands_resolve_source_repo_venv_when_running_in_worktree(self) -> None:
        from agent_runner.gates import normalize_gate_command

        self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)
        expected_python = (
            self.repo / ".venv" / "Scripts" / "python.exe"
            if os.name == "nt"
            else self.repo / ".venv" / "bin" / "python"
        ).resolve()

        direct = normalize_gate_command(
            [".venv/Scripts/python.exe", "-B", "-m", "py_compile", "agent_runner/web.py"],
            repo=self.worktree,
            command_repo=self.repo,
        )
        self.assertEqual(str(expected_python), direct[0])

        powershell = normalize_gate_command(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "& .\\.venv\\Scripts\\python.exe -B -m unittest discover -s tests -p 'test_web_console*.py'",
            ],
            repo=self.worktree,
            command_repo=self.repo,
        )
        self.assertIn(str(expected_python), powershell[-1])
        self.assertNotIn(".\\.venv\\Scripts\\python.exe", powershell[-1])

    def test_fast_web_worktree_regression_failure_summary_and_retry_policy(self) -> None:
        from agent_runner.gates import (
            should_retry_fast_web_worktree_regression_failure,
            summarize_fast_web_worktree_regression_failure,
        )

        command_log = self.run_dir / "failed_static.txt"
        command_log.write_text("AssertionError: missing status-chip--reconnecting\n", encoding="utf-8")
        result = {
            "ok": False,
            "failed_command": {
                "name": "test_web_console_static",
                "test_file": "tests/test_web_console_static.py",
                "cmd": ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_web_console_static.py"],
                "rc": 1,
                "summary": "FAILED tests/test_web_console_static.py",
                "log_path": str(command_log),
            },
        }

        summary = summarize_fast_web_worktree_regression_failure(result, self.run_dir / "summary.json")

        self.assertIn("test_web_console_static", summary)
        self.assertIn("tests/test_web_console_static.py", summary)
        self.assertIn("status-chip--reconnecting", summary)
        self.assertTrue(
            should_retry_fast_web_worktree_regression_failure(
                True,
                attempt=0,
                max_attempts=3,
                dev_escalate_on={"test_failed"},
            )
        )
        self.assertFalse(
            should_retry_fast_web_worktree_regression_failure(
                True,
                attempt=2,
                max_attempts=3,
                dev_escalate_on={"test_failed"},
            )
        )

    def test_generated_worktree_cleanup_preserves_pr_queue_and_source_head(self) -> None:
        source_head_before = self._init_repo()
        create_worktree(self.repo, self.worktree, run_dir=self.run_dir)

        tb = create_task_branch(self.worktree, "T-review", task_title="Queue preserved review")
        (self.worktree / "feature.txt").write_text("reviewable\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "reviewable", cwd=self.worktree)
        branch_head = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        abandon_task_branch(self.worktree, tb)

        result = queue_review_packet(
            self.repo,
            run_id=self.run_dir.name,
            task_ids=["T-review"],
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

        remove_worktree(self.repo, self.worktree)

        packet_path = pr_packet_path(self.repo, result["packet_id"])
        index_path = pr_branch_index_path(self.repo)
        packet = json.loads(packet_path.read_text(encoding="utf-8"))
        index = load_branch_index(self.repo)

        self.assertEqual(source_head_before, git_head(self.repo))
        self.assertFalse(self.worktree.exists())
        self.assertTrue(packet_path.exists())
        self.assertTrue(index_path.exists())
        self.assertEqual("pr_queued", packet["status"])
        self.assertFalse(packet["source_main_mutated"])
        self.assertEqual(branch_head, packet["head_ref"])
        self.assertEqual(tb.branch_name, packet["branch"])
        self.assertEqual(1, len(index["entries"]))
        self.assertEqual(result["packet_id"], index["entries"][0]["id"])
        self.assertEqual(tb.branch_name, index["entries"][0]["branch"])

    def test_pr_queue_validation_keeps_source_repo_clean_and_uses_external_worktree(self) -> None:
        packet = self._prepare_validation_packet()
        source_head_before = git_head(self.repo)
        source_state_before = git_repo_state(self.repo)
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

        after_head = git_head(self.repo)
        after_state = git_repo_state(self.repo)
        worktree_dir = Path(result["worktree_dir"]).resolve()

        self.assertEqual(source_head_before, after_head)
        self.assertEqual(source_state_before, after_state)
        self.assertEqual("clean", source_state_before)
        self.assertEqual("clean", after_state)
        self.assertFalse(result["source_main_mutated"])
        self.assertTrue(result["worktree_created"])
        self.assertTrue(result["worktree_removed"])
        self.assertFalse(_is_relative_to(worktree_dir, self.repo))
        self.assertEqual(worktree_dir, calls[0]["repo"])
        self.assertEqual(worktree_dir, calls[1]["repo"])
        self.assertEqual(self.repo.resolve(), calls[0]["command_repo"])
        self.assertEqual(self.repo.resolve(), calls[1]["command_repo"])
        self.assertEqual(packet["goal_trace"], result["validation_records"][0]["goal_trace"])
        self.assertEqual("validation_passed", result["status"])


if __name__ == "__main__":
    unittest.main()
