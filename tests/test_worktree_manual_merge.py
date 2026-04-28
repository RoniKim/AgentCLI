from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.gitops import (
    WORKTREE_MERGE_PENDING,
    apply_pending_worktree_merge,
    create_worktree,
    discard_pending_worktree_merge,
    export_worktree_patch,
    remove_worktree,
    summarize_worktree_diff,
    WorktreeCleanupError,
    WorktreeSafetyError,
    sha256_text,
)
from agent_runner.utils import run_cmd


def _rmtree_best_effort(path: Path) -> None:
    def _clear_readonly(func, path_text, _exc_info):
        try:
            os.chmod(path_text, stat.S_IWRITE)
            func(path_text)
        except Exception:
            pass

    try:
        shutil.rmtree(path, onerror=_clear_readonly)
    except Exception:
        shutil.rmtree(path, ignore_errors=True)


class WorktreeManualMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = Path.home() / ".codex" / "memories" / "agentcli-wmm-tests"
        self.fixture_base.mkdir(parents=True, exist_ok=True)
        self.fixture_root = self.fixture_base / f"t-{uuid.uuid4().hex[:12]}"
        self.fixture_root.mkdir()
        self.repo = self.fixture_root / "repo"
        self.worktree = self.fixture_root / "worktree"
        self.patch_path = self.fixture_root / "worktree.patch"
        self.pending_path = self.fixture_root / WORKTREE_MERGE_PENDING
        self.addCleanup(lambda: _rmtree_best_effort(self.fixture_root))

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        code, out = run_cmd(["git", *args], cwd=cwd or self.repo, timeout_sec=60)
        self.assertEqual(code, 0, out)
        return out

    def _init_repo(self, *, source_text: str = "base\n") -> str:
        self.repo.mkdir(parents=True, exist_ok=True)
        self._git("init")
        self._git("config", "user.email", "agentcli@example.invalid")
        self._git("config", "user.name", "AgentCLI Test")
        (self.repo / "README.md").write_text(source_text, encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "base")
        return self._git("rev-parse", "HEAD").strip()

    def _source_branch(self) -> str:
        return self._git("rev-parse", "--abbrev-ref", "HEAD").strip()

    def _cleanup_error(self, cleanup_path: Path, *, attempts: list[dict[str, object]] | None = None) -> WorktreeCleanupError:
        permission_error = PermissionError(13, "Permission denied", cleanup_path.as_posix())
        cleanup_attempts = attempts or [
            {
                "attempt": 1,
                "operation": "shutil.rmtree",
                "path": cleanup_path.as_posix(),
                "worktree_dir": self.worktree.resolve().as_posix(),
                "error_type": "PermissionError",
                "message": str(permission_error),
                "errno": 13,
            }
        ]
        details = {
            "path": cleanup_path.as_posix(),
            "worktree_dir": self.worktree.resolve().as_posix(),
            "attempts": cleanup_attempts,
            "operation": "shutil.rmtree",
            "error_type": "PermissionError",
            "message": str(permission_error),
        }
        return WorktreeCleanupError(
            str(permission_error),
            cleanup_path=cleanup_path.as_posix(),
            details=details,
            attempts=cleanup_attempts,
        )

    def _write_pending_payload(
        self,
        *,
        patch_text: str,
        base_ref: str,
        expected_head: str,
        branch: str,
        source_repo_state: str = "clean",
        worktree_state: str = "dirty",
        run_id: str | None = None,
        head_ref: str | None = None,
    ) -> dict[str, object]:
        self.patch_path.write_text(patch_text, encoding="utf-8")
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": "pending",
            "created_at": "2026-04-26T12:02:00",
            "run_id": run_id or self.fixture_root.name,
            "run_dir": self.fixture_root.resolve().as_posix(),
            "source_repo": self.repo.resolve().as_posix(),
            "source_repo_root": self.repo.resolve().as_posix(),
            "branch": branch,
            "expected_head": expected_head,
            "source_repo_state": source_repo_state,
            "worktree_state": worktree_state,
            "worktree_dir": self.worktree.resolve().as_posix(),
            "patch_path": self.patch_path.resolve().as_posix(),
            "patch_hash": sha256_text(patch_text),
            "base_ref": base_ref,
            "head_ref": head_ref or expected_head,
            "last_rc": 0,
        }
        payload["sourceRepoRoot"] = payload["source_repo_root"]
        payload["sourceRepoState"] = payload["source_repo_state"]
        payload["worktreeState"] = payload["worktree_state"]
        payload["patchHash"] = payload["patch_hash"]
        payload["runId"] = payload["run_id"]
        payload["runDir"] = payload["run_dir"]
        self.pending_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def _load_pending(self) -> dict[str, object]:
        return json.loads(self.pending_path.read_text(encoding="utf-8"))

    def test_export_worktree_patch_includes_committed_worktree_changes(self) -> None:
        base = self._init_repo()

        create_worktree(self.repo, self.worktree, run_dir=self.fixture_root)
        (self.worktree / "feature.txt").write_text("from worktree\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)

        export_worktree_patch(self.worktree, self.patch_path, base_ref=base)

        patch_text = self.patch_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("feature.txt", patch_text)
        self.assertIn("from worktree", patch_text)
        remove_worktree(self.repo, self.worktree)

    def test_summarize_worktree_diff_handles_binary_deleted_renamed_and_large_files(self) -> None:
        patch_path = self.fixture_root / "states.patch"
        patch_path.write_text(
            "\n".join(
                [
                    "diff --git a/bin/data.bin b/bin/data.bin",
                    "Binary files a/bin/data.bin and b/bin/data.bin differ",
                    "",
                    "diff --git a/docs/old.md b/docs/old.md",
                    "deleted file mode 100644",
                    "--- a/docs/old.md",
                    "+++ /dev/null",
                    "@@ -1 +0,0 @@",
                    "-old",
                    "",
                    "diff --git a/docs/old-name.md b/docs/new-name.md",
                    "similarity index 100%",
                    "rename from docs/old-name.md",
                    "rename to docs/new-name.md",
                    "",
                    "diff --git a/src/large.txt b/src/large.txt",
                    "--- a/src/large.txt",
                    "+++ b/src/large.txt",
                    "@@ -1,8 +1,18 @@",
                    "-old line 1",
                    "-old line 2",
                    "-old line 3",
                    "-old line 4",
                    "-old line 5",
                    "-old line 6",
                    "-old line 7",
                    "-old line 8",
                    "+new line 1",
                    "+new line 2",
                    "+new line 3",
                    "+new line 4",
                    "+new line 5",
                    "+new line 6",
                    "+new line 7",
                    "+new line 8",
                    "+new line 9",
                    "+new line 10",
                    "+new line 11",
                    "+new line 12",
                    "",
                ]
            ),
            encoding="utf-8",
        )

        changed_files = summarize_worktree_diff(patch_path)
        by_path = {item["path"]: item for item in changed_files}

        self.assertEqual("binary", by_path["bin/data.bin"]["kind"])
        self.assertTrue(by_path["bin/data.bin"]["binary"])
        self.assertEqual("deleted", by_path["docs/old.md"]["kind"])
        self.assertTrue(by_path["docs/old.md"]["deleted"])
        self.assertEqual("renamed", by_path["docs/new-name.md"]["kind"])
        self.assertTrue(by_path["docs/new-name.md"]["renamed"])
        self.assertEqual("docs/old-name.md", by_path["docs/new-name.md"]["oldPath"])
        self.assertEqual("docs/new-name.md", by_path["docs/new-name.md"]["newPath"])
        self.assertTrue(by_path["src/large.txt"]["large"])
        self.assertTrue(by_path["src/large.txt"]["truncated"])
        self.assertGreaterEqual(by_path["src/large.txt"]["lineCount"], 12)
        self.assertTrue(by_path["src/large.txt"]["hunks"])

    def test_remove_worktree_retries_locked_path_before_succeeding(self) -> None:
        self._init_repo()
        retry_worktree = self.fixture_root / ".agentcli_worktrees" / "repo" / "retry"
        locked_file = retry_worktree / "nested" / "locked.txt"
        locked_file.parent.mkdir(parents=True, exist_ok=True)
        locked_file.write_text("locked\n", encoding="utf-8")
        retry_worktree.mkdir(parents=True, exist_ok=True)

        rmtree_calls: list[Path] = []

        def fake_run_cmd(cmd, cwd, timeout_sec=600):
            if cmd[:3] == ["git", "worktree", "remove"]:
                return 1, f"permission denied: {locked_file.as_posix()}"
            if cmd[:3] == ["git", "worktree", "prune"]:
                return 0, ""
            self.fail(f"Unexpected command: {cmd}")

        def fake_rmtree(path):
            rmtree_calls.append(Path(path))
            if len(rmtree_calls) < 3:
                raise PermissionError(13, "Permission denied", locked_file.as_posix())
            target = Path(path)
            for candidate in sorted(target.rglob("*"), reverse=True):
                if candidate.is_file() or candidate.is_symlink():
                    candidate.unlink()
            for candidate in sorted(target.rglob("*"), reverse=True):
                if candidate.is_dir():
                    candidate.rmdir()
            target.rmdir()

        with (
            patch("agent_runner.gitops.run_cmd", side_effect=fake_run_cmd),
            patch("agent_runner.gitops.shutil.rmtree", side_effect=fake_rmtree),
            patch("agent_runner.gitops.time.sleep") as sleep_mock,
        ):
            remove_worktree(self.repo, retry_worktree)

        self.assertEqual(3, len(rmtree_calls))
        self.assertEqual(2, sleep_mock.call_count)
        self.assertFalse(retry_worktree.exists())

    def test_remove_worktree_raises_structured_cleanup_error_after_retries_are_exhausted(self) -> None:
        self._init_repo()
        retry_worktree = self.fixture_root / ".agentcli_worktrees" / "repo" / "retry-fail"
        locked_file = retry_worktree / "nested" / "locked.txt"
        locked_file.parent.mkdir(parents=True, exist_ok=True)
        locked_file.write_text("locked\n", encoding="utf-8")
        retry_worktree.mkdir(parents=True, exist_ok=True)

        def fake_run_cmd(cmd, cwd, timeout_sec=600):
            if cmd[:3] == ["git", "worktree", "remove"]:
                return 1, f"permission denied: {locked_file.as_posix()}"
            if cmd[:3] == ["git", "worktree", "prune"]:
                return 0, ""
            self.fail(f"Unexpected command: {cmd}")

        def fake_rmtree(path):
            raise PermissionError(13, "Permission denied", locked_file.as_posix())

        with (
            patch("agent_runner.gitops.run_cmd", side_effect=fake_run_cmd),
            patch("agent_runner.gitops.shutil.rmtree", side_effect=fake_rmtree),
            patch("agent_runner.gitops.time.sleep") as sleep_mock,
        ):
            with self.assertRaises(WorktreeCleanupError) as ctx:
                remove_worktree(self.repo, retry_worktree)

        error = ctx.exception
        self.assertEqual("worktree_cleanup_failed", error.code)
        self.assertEqual(locked_file.as_posix(), error.cleanup_path)
        self.assertIn(locked_file.as_posix(), error.cleanup_message)
        self.assertEqual(locked_file.as_posix(), error.details["path"])
        self.assertEqual(locked_file.as_posix(), error.details["locking_path"])
        self.assertEqual(retry_worktree.as_posix(), error.details["affected_artifact"])
        self.assertEqual(locked_file.as_posix(), error.details["attempts"][0]["path"])
        self.assertEqual(locked_file.as_posix(), error.details["attempts"][0]["locking_path"])
        self.assertEqual(retry_worktree.as_posix(), error.details["attempts"][0]["affected_artifact"])
        self.assertEqual(4, len(error.details["attempts"]))
        self.assertEqual([0.05, 0.1, 0.2], error.details["retry_schedule_seconds"])
        self.assertEqual(3, sleep_mock.call_count)
        if os.name == "nt":
            self.assertTrue(error.details["reboot_required"])
            self.assertIn("reboot", str(error.details["reboot_guidance"]).lower())
        self.assertTrue(retry_worktree.exists())

    def test_apply_pending_worktree_merge_keeps_patch_applied_when_cleanup_fails(self) -> None:
        base_ref = self._init_repo()
        branch = self._source_branch()

        payload = self._write_pending_payload(
            patch_text="""diff --git a/feature.txt b/feature.txt
new file mode 100644
index 0000000..7c890e8
--- /dev/null
+++ b/feature.txt
@@ -0,0 +1 @@
+from patch
""",
            base_ref=base_ref,
            expected_head=base_ref,
            branch=branch,
        )
        self.worktree.mkdir()
        locked_file = self.worktree / "nested" / "locked.txt"
        locked_file.parent.mkdir(parents=True, exist_ok=True)
        locked_file.write_text("locked\n", encoding="utf-8")

        cleanup_error = self._cleanup_error(locked_file)
        with patch("agent_runner.gitops.remove_worktree", side_effect=cleanup_error):
            result = apply_pending_worktree_merge(self.pending_path)

        self.assertEqual("applied_cleanup_failed", result["status"])
        self.assertEqual(locked_file.as_posix(), result["cleanup_path"])
        self.assertEqual(str(cleanup_error), result["cleanup_message"])
        self.assertIn(locked_file.as_posix(), str(result["cleanup_error"]))
        self.assertEqual(locked_file.as_posix(), result["cleanup_details"]["path"])
        self.assertEqual(locked_file.as_posix(), result["cleanup_details"]["attempts"][0]["path"])
        self.assertEqual("generated_worktree_remove", result["resolution_actions"][0]["kind"])
        self.assertEqual("failed", result["resolution_actions"][0]["status"])
        self.assertEqual("stale_marker_prune", result["resolution_actions"][1]["kind"])
        self.assertEqual("done", result["resolution_actions"][1]["status"])
        self.assertEqual("cleanup_failed_reconcile", result["resolution_actions"][2]["kind"])
        self.assertEqual("required", result["resolution_actions"][2]["status"])
        self.assertEqual("from patch\n", (self.repo / "feature.txt").read_text(encoding="utf-8"))
        self.assertFalse(self.pending_path.exists())
        cleanup_artifact = self.fixture_root / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json"
        self.assertTrue(cleanup_artifact.exists())
        cleanup_payload = json.loads(cleanup_artifact.read_text(encoding="utf-8"))
        self.assertEqual(locked_file.as_posix(), cleanup_payload["cleanup_path"])
        self.assertEqual(str(cleanup_error), cleanup_payload["cleanup_message"])
        self.assertEqual("required", cleanup_payload["resolution_actions"][2]["status"])

    def test_apply_pending_worktree_merge_fast_forwards_commits_before_dirty_patch(self) -> None:
        base_ref = self._init_repo(source_text="base\n")
        branch = self._source_branch()
        create_worktree(self.repo, self.worktree, run_dir=self.fixture_root)

        (self.worktree / "README.md").write_text("committed\n", encoding="utf-8")
        self._git("add", "README.md", cwd=self.worktree)
        self._git("commit", "-m", "committed worktree change", cwd=self.worktree)
        head_ref = self._git("rev-parse", "HEAD", cwd=self.worktree).strip()
        (self.worktree / "dirty.txt").write_text("dirty worktree change\n", encoding="utf-8")

        stale_full_patch = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-not the source content
+committed
"""
        self._write_pending_payload(
            patch_text=stale_full_patch,
            base_ref=base_ref,
            expected_head=base_ref,
            branch=branch,
            head_ref=head_ref,
        )

        result = apply_pending_worktree_merge(self.pending_path)

        self.assertEqual("applied", result["status"])
        self.assertEqual("generated_worktree_remove", result["resolution_actions"][0]["kind"])
        self.assertEqual("done", result["resolution_actions"][0]["status"])
        self.assertEqual("stale_marker_prune", result["resolution_actions"][1]["kind"])
        self.assertEqual("done", result["resolution_actions"][1]["status"])
        self.assertEqual("fast_forward_then_patch", result["merge_mode"])
        self.assertEqual(head_ref, result["fast_forward_ref"])
        self.assertEqual(head_ref, result["fastForwardRef"])
        dirty_patch_path = (self.fixture_root / "worktree_dirty_uncommitted.patch").resolve()
        self.assertEqual(dirty_patch_path.as_posix(), result["dirty_patch_path"])
        self.assertEqual(dirty_patch_path.as_posix(), result["dirtyPatchPath"])
        self.assertEqual(sha256_text(dirty_patch_path.read_text(encoding="utf-8", errors="replace")), result["dirty_patch_hash"])
        self.assertEqual(result["dirty_patch_hash"], result["dirtyPatchHash"])
        self.assertTrue(result["dirty_patch_applied"])
        self.assertTrue(result["dirtyPatchApplied"])
        self.assertIsInstance(result["dirty_patch_check"], dict)
        self.assertEqual("ok", result["dirty_patch_check"]["status"])
        self.assertTrue(result["dirty_patch_check"]["ok"])
        self.assertEqual(result["dirty_patch_check"], result["dirtyPatchCheck"])
        self.assertEqual(head_ref, self._git("rev-parse", "HEAD").strip())
        self.assertEqual("committed\n", (self.repo / "README.md").read_text(encoding="utf-8"))
        self.assertEqual("dirty worktree change\n", (self.repo / "dirty.txt").read_text(encoding="utf-8"))
        self.assertTrue((self.fixture_root / "worktree_dirty_uncommitted.patch").exists())
        self.assertFalse(self.pending_path.exists())
        applied_artifact = self.fixture_root / "WORKTREE_MERGE_APPLIED.json"
        self.assertTrue(applied_artifact.exists())
        applied_payload = json.loads(applied_artifact.read_text(encoding="utf-8"))
        self.assertEqual(head_ref, applied_payload["fast_forward_ref"])
        self.assertEqual(head_ref, applied_payload["fastForwardRef"])
        self.assertEqual(dirty_patch_path.as_posix(), applied_payload["dirty_patch_path"])
        self.assertEqual(dirty_patch_path.as_posix(), applied_payload["dirtyPatchPath"])
        self.assertEqual(result["dirty_patch_hash"], applied_payload["dirty_patch_hash"])
        self.assertEqual(result["dirty_patch_hash"], applied_payload["dirtyPatchHash"])
        self.assertTrue(applied_payload["dirty_patch_applied"])
        self.assertTrue(applied_payload["dirtyPatchApplied"])
        self.assertEqual(result["dirty_patch_check"], applied_payload["dirty_patch_check"])
        self.assertEqual(result["dirty_patch_check"], applied_payload["dirtyPatchCheck"])
        self.assertFalse(self.worktree.exists())

    def test_discard_pending_worktree_merge_records_cleanup_failure_without_raising(self) -> None:
        self._init_repo()
        self.worktree.mkdir()
        locked_file = self.worktree / "nested" / "locked.txt"
        locked_file.parent.mkdir(parents=True, exist_ok=True)
        locked_file.write_text("locked\n", encoding="utf-8")
        self._write_pending_payload(
            patch_text=(
                "diff --git a/feature.txt b/feature.txt\n"
                "--- a/feature.txt\n"
                "+++ b/feature.txt\n"
                "@@ -0,0 +1 @@\n"
                "+from patch\n"
            ),
            base_ref=self._git("rev-parse", "HEAD").strip(),
            expected_head=self._git("rev-parse", "HEAD").strip(),
            branch=self._source_branch(),
        )

        cleanup_error = self._cleanup_error(locked_file)
        with patch("agent_runner.gitops.remove_worktree", side_effect=cleanup_error):
            result = discard_pending_worktree_merge(self.pending_path)

        self.assertEqual("discard_cleanup_failed", result["status"])
        self.assertEqual(locked_file.as_posix(), result["cleanup_path"])
        self.assertEqual(str(cleanup_error), result["cleanup_message"])
        self.assertIn(locked_file.as_posix(), str(result["cleanup_error"]))
        self.assertEqual(locked_file.as_posix(), result["cleanup_details"]["path"])
        self.assertEqual("source_safe_discard", result["resolution_actions"][0]["kind"])
        self.assertEqual("done", result["resolution_actions"][0]["status"])
        self.assertEqual("generated_worktree_remove", result["resolution_actions"][1]["kind"])
        self.assertEqual("failed", result["resolution_actions"][1]["status"])
        self.assertEqual("cleanup_failed_reconcile", result["resolution_actions"][3]["kind"])
        self.assertEqual("required", result["resolution_actions"][3]["status"])
        self.assertFalse(self.pending_path.exists())
        cleanup_artifact = self.fixture_root / "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json"
        self.assertTrue(cleanup_artifact.exists())
        cleanup_payload = json.loads(cleanup_artifact.read_text(encoding="utf-8"))
        self.assertEqual(locked_file.as_posix(), cleanup_payload["cleanup_path"])
        self.assertEqual(str(cleanup_error), cleanup_payload["cleanup_message"])

    def test_apply_pending_worktree_merge_rejects_dirty_source_repo(self) -> None:
        base_ref = self._init_repo()
        branch = self._source_branch()
        self._write_pending_payload(
            patch_text=(
                "diff --git a/feature.txt b/feature.txt\n"
                "--- a/feature.txt\n"
                "+++ b/feature.txt\n"
                "@@ -0,0 +1 @@\n"
                "+from patch\n"
            ),
            base_ref=base_ref,
            expected_head=base_ref,
            branch=branch,
        )
        (self.repo / "README.md").write_text("base\nsource dirtied\n", encoding="utf-8")

        with self.assertRaises(WorktreeSafetyError) as ctx:
            apply_pending_worktree_merge(self.pending_path)

        self.assertEqual("worktree_source_repo_dirty", ctx.exception.code)
        self.assertTrue(self.pending_path.exists())
        self.assertFalse((self.repo / "feature.txt").exists())

    def test_apply_pending_worktree_merge_rejects_head_mismatch(self) -> None:
        base_ref = self._init_repo()
        branch = self._source_branch()
        self._write_pending_payload(
            patch_text=(
                "diff --git a/feature.txt b/feature.txt\n"
                "--- a/feature.txt\n"
                "+++ b/feature.txt\n"
                "@@ -0,0 +1 @@\n"
                "+from patch\n"
            ),
            base_ref=base_ref,
            expected_head=base_ref,
            branch=branch,
        )
        (self.repo / "feature.txt").write_text("source moved\n", encoding="utf-8")
        self._git("add", "feature.txt")
        self._git("commit", "-m", "advance")

        with self.assertRaises(WorktreeSafetyError) as ctx:
            apply_pending_worktree_merge(self.pending_path)

        self.assertEqual("worktree_base_ref_mismatch", ctx.exception.code)
        self.assertTrue(self.pending_path.exists())
        self.assertFalse((self.repo / "feature.txt").read_text(encoding="utf-8").startswith("from patch"))

    def test_apply_pending_worktree_merge_rejects_patch_hash_mismatch(self) -> None:
        base_ref = self._init_repo()
        branch = self._source_branch()
        self._write_pending_payload(
            patch_text=(
                "diff --git a/feature.txt b/feature.txt\n"
                "--- a/feature.txt\n"
                "+++ b/feature.txt\n"
                "@@ -0,0 +1 @@\n"
                "+from patch\n"
            ),
            base_ref=base_ref,
            expected_head=base_ref,
            branch=branch,
        )
        pending = self._load_pending()
        pending["patch_hash"] = "0" * 64
        pending["patchHash"] = "0" * 64
        self.pending_path.write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(WorktreeSafetyError) as ctx:
            apply_pending_worktree_merge(self.pending_path)

        self.assertEqual("worktree_patch_hash_mismatch", ctx.exception.code)
        self.assertTrue(self.pending_path.exists())
        self.assertFalse((self.repo / "feature.txt").exists())

    def test_apply_pending_worktree_merge_rejects_git_apply_check_failure(self) -> None:
        base_ref = self._init_repo()
        branch = self._source_branch()
        invalid_patch = (
            "diff --git a/feature.txt b/feature.txt\n"
            "--- a/feature.txt\n"
            "+++ b/feature.txt\n"
            "@@ -1 +1 @@\n"
            "-does not match\n"
            "+still does not match\n"
        )
        self._write_pending_payload(
            patch_text=invalid_patch,
            base_ref=base_ref,
            expected_head=base_ref,
            branch=branch,
        )

        with self.assertRaises(WorktreeSafetyError) as ctx:
            apply_pending_worktree_merge(self.pending_path)

        self.assertEqual("worktree_patch_check_failed", ctx.exception.code)
        self.assertEqual("feature.txt", ctx.exception.details["failed_files"][0]["path"])
        self.assertEqual("feature.txt", ctx.exception.details["apply_check"]["failed_files"][0]["path"])
        self.assertEqual("@@ -1 +1 @@", ctx.exception.details["failed_hunks"][0]["header"])
        self.assertEqual("@@ -1 +1 @@", ctx.exception.details["apply_check"]["failed_hunks"][0]["header"])
        self.assertTrue(self.pending_path.exists())
        self.assertFalse((self.repo / "feature.txt").exists())

    def test_apply_pending_worktree_merge_reports_patch_apply_failure_details_and_keeps_pending_state(self) -> None:
        base_ref = self._init_repo()
        branch = self._source_branch()
        patch_text = (
            "diff --git a/feature.txt b/feature.txt\n"
            "new file mode 100644\n"
            "index 0000000..7c890e8\n"
            "--- /dev/null\n"
            "+++ b/feature.txt\n"
            "@@ -0,0 +1 @@\n"
            "+from patch\n"
        )
        self._write_pending_payload(
            patch_text=patch_text,
            base_ref=base_ref,
            expected_head=base_ref,
            branch=branch,
        )

        def fake_run_cmd(cmd, cwd, timeout_sec=600):
            if cmd[:5] == ["git", "apply", "--check", "--binary", "--whitespace=nowarn"]:
                return 0, ""
            if cmd[:4] == ["git", "apply", "--binary", "--whitespace=nowarn"]:
                return 1, "error: patch failed: feature.txt:1\nerror: feature.txt: patch does not apply\n"
            return run_cmd(cmd, cwd=cwd, timeout_sec=timeout_sec)

        with patch("agent_runner.gitops.run_cmd", side_effect=fake_run_cmd):
            with self.assertRaises(WorktreeSafetyError) as ctx:
                apply_pending_worktree_merge(self.pending_path)

        self.assertEqual("worktree_patch_apply_failed", ctx.exception.code)
        self.assertEqual("feature.txt", ctx.exception.details["failed_files"][0]["path"])
        self.assertEqual("feature.txt", ctx.exception.details["failed_hunks"][0]["path"])
        self.assertEqual("@@ -0,0 +1 @@", ctx.exception.details["failed_hunks"][0]["header"])
        self.assertTrue(self.pending_path.exists())
        self.assertFalse((self.repo / "feature.txt").exists())


if __name__ == "__main__":
    unittest.main()
