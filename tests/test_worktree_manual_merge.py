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
    WORKTREE_MERGE_PENDING,
    apply_pending_worktree_merge,
    create_worktree,
    discard_pending_worktree_merge,
    export_worktree_patch,
    remove_worktree,
)
from agent_runner.utils import run_cmd


class WorktreeManualMergeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = ROOT / ".tmp-worktree-manual-merge-tests"
        self.fixture_base.mkdir(exist_ok=True)
        self.fixture_root = self.fixture_base / f"{self._testMethodName}-{uuid.uuid4().hex}"
        self.fixture_root.mkdir()
        self.repo = self.fixture_root / "repo"
        self.worktree = self.fixture_root / "worktree"
        self.patch_path = self.fixture_root / "worktree.patch"
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        code, out = run_cmd(["git", *args], cwd=cwd or self.repo, timeout_sec=60)
        self.assertEqual(code, 0, out)
        return out

    def test_export_worktree_patch_includes_committed_worktree_changes(self) -> None:
        self.repo.mkdir()
        self._git("init")
        self._git("config", "user.email", "agentcli@example.invalid")
        self._git("config", "user.name", "AgentCLI Test")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "base")
        base = self._git("rev-parse", "HEAD").strip()

        create_worktree(self.repo, self.worktree)
        (self.worktree / "feature.txt").write_text("from worktree\n", encoding="utf-8")
        self._git("add", "feature.txt", cwd=self.worktree)
        self._git("commit", "-m", "feature", cwd=self.worktree)

        export_worktree_patch(self.worktree, self.patch_path, base_ref=base)

        patch_text = self.patch_path.read_text(encoding="utf-8", errors="replace")
        self.assertIn("feature.txt", patch_text)
        self.assertIn("from worktree", patch_text)
        remove_worktree(self.repo, self.worktree)

    def test_apply_pending_worktree_merge_keeps_patch_applied_when_cleanup_fails(self) -> None:
        self.repo.mkdir()
        self._git("init")
        self._git("config", "user.email", "agentcli@example.invalid")
        self._git("config", "user.name", "AgentCLI Test")
        (self.repo / "README.md").write_text("base\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-m", "base")

        self.patch_path.write_text(
            """diff --git a/feature.txt b/feature.txt
new file mode 100644
index 0000000..7c890e8
--- /dev/null
+++ b/feature.txt
@@ -0,0 +1 @@
+from patch
""",
            encoding="utf-8",
        )
        self.worktree.mkdir()
        pending = self.fixture_root / WORKTREE_MERGE_PENDING
        pending.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "source_repo": str(self.repo),
                    "run_dir": str(self.fixture_root),
                    "worktree_dir": str(self.worktree),
                    "patch_path": str(self.patch_path),
                    "base_ref": "base",
                    "head_ref": "head",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        with patch("agent_runner.gitops.remove_worktree", side_effect=RuntimeError("locked worktree")):
            result = apply_pending_worktree_merge(pending)

        self.assertEqual("applied_cleanup_failed", result["status"])
        self.assertIn("locked worktree", str(result["cleanup_error"]))
        self.assertEqual("from patch\n", (self.repo / "feature.txt").read_text(encoding="utf-8"))
        self.assertFalse(pending.exists())
        self.assertTrue((self.fixture_root / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").exists())

    def test_discard_pending_worktree_merge_records_cleanup_failure_without_raising(self) -> None:
        self.repo.mkdir()
        self.worktree.mkdir()
        pending = self.fixture_root / WORKTREE_MERGE_PENDING
        pending.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "source_repo": str(self.repo),
                    "run_dir": str(self.fixture_root),
                    "worktree_dir": str(self.worktree),
                    "patch_path": str(self.patch_path),
                    "base_ref": "base",
                    "head_ref": "head",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        with patch("agent_runner.gitops.remove_worktree", side_effect=RuntimeError("locked worktree")):
            result = discard_pending_worktree_merge(pending)

        self.assertEqual("discard_cleanup_failed", result["status"])
        self.assertIn("locked worktree", str(result["cleanup_error"]))
        self.assertFalse(pending.exists())
        self.assertTrue((self.fixture_root / "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json").exists())


if __name__ == "__main__":
    unittest.main()
