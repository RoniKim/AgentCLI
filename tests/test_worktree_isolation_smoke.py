import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_runner.gitops import create_worktree, remove_worktree


def _run(cmd, cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)


class TestWorktreeIsolationSmoke(unittest.TestCase):
    def test_create_and_remove_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _run(["git", "init"], repo)
            _run(["git", "config", "user.email", "test@example.com"], repo)
            _run(["git", "config", "user.name", "Tester"], repo)
            (repo / "a.txt").write_text("base\n", encoding="utf-8")
            _run(["git", "add", "a.txt"], repo)
            _run(["git", "commit", "-m", "init"], repo)

            worktree = Path(tmp) / "worktree"
            create_worktree(repo, worktree)
            self.assertTrue(worktree.exists())
            remove_worktree(repo, worktree)
            self.assertFalse(worktree.exists())


if __name__ == "__main__":
    unittest.main()
