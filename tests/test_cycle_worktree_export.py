import tempfile
from pathlib import Path
import subprocess
import unittest

from agent_runner.gitops import create_worktree, handle_worktree_patch


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "base.txt")
    _git(path, "commit", "-m", "init")


class TestWorktreeExportOnStop(unittest.TestCase):
    def test_patch_preserved_without_auto_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            worktree_dir = Path(tmp) / "worktree"
            create_worktree(repo, worktree_dir)

            (worktree_dir / "note.txt").write_text("stop\n", encoding="utf-8")

            run_dir = Path(tmp) / "run"
            run_dir.mkdir()

            rc = handle_worktree_patch(worktree_dir, repo, run_dir, last_rc=1)

            self.assertEqual(rc, 1)
            self.assertTrue((run_dir / "worktree.patch").exists())
            self.assertTrue((run_dir / "WORKTREE_PATCH_NOT_APPLIED.md").exists())
            self.assertFalse((repo / "note.txt").exists())


if __name__ == "__main__":
    unittest.main()
