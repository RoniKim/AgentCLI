from __future__ import annotations

import shutil
import sys
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.gitops import create_worktree, export_worktree_patch, remove_worktree
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


if __name__ == "__main__":
    unittest.main()
