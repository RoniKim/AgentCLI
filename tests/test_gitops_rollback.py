import tempfile
import unittest
from pathlib import Path

from agent_runner.gitops import create_checkpoint, restore_checkpoint
from agent_runner.utils import run_cmd


def _init_repo(path: Path) -> None:
    run_cmd(["git", "init"], cwd=path, timeout_sec=30)
    run_cmd(["git", "config", "user.email", "test@example.com"], cwd=path, timeout_sec=30)
    run_cmd(["git", "config", "user.name", "Test User"], cwd=path, timeout_sec=30)
    (path / "file.txt").write_text("base\n", encoding="utf-8")
    run_cmd(["git", "add", "file.txt"], cwd=path, timeout_sec=30)
    run_cmd(["git", "commit", "-m", "init"], cwd=path, timeout_sec=30)


class TestGitopsRollback(unittest.TestCase):
    def test_restore_checkpoint_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            _init_repo(repo)

            (repo / "file.txt").write_text("change\n", encoding="utf-8")
            cp = create_checkpoint(repo, repo / ".checkpoint")

            with self.assertRaises(RuntimeError):
                restore_checkpoint(repo, cp, dangerous=False, run_dir=repo, stop_path=None)

            blocked = repo / "ROLLBACK_BLOCKED.md"
            self.assertTrue(blocked.exists())

    def test_restore_checkpoint_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            _init_repo(repo)

            (repo / "file.txt").write_text("checkpoint\n", encoding="utf-8")
            cp = create_checkpoint(repo, repo / ".checkpoint")

            (repo / "file.txt").write_text("dirty\n", encoding="utf-8")
            restore_checkpoint(repo, cp, dangerous=True, run_dir=repo, stop_path=None)

            self.assertEqual((repo / "file.txt").read_text(encoding="utf-8"), "checkpoint\n")

    def test_restore_checkpoint_failure_report(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "repo"
            repo.mkdir()
            _init_repo(repo)

            (repo / "file.txt").write_text("checkpoint\n", encoding="utf-8")
            cp = create_checkpoint(repo, repo / ".checkpoint")
            cp.patch_path.write_text("not a patch\n", encoding="utf-8")

            with self.assertRaises(RuntimeError):
                restore_checkpoint(repo, cp, dangerous=True, run_dir=repo, stop_path=None)

            failure = repo / "ROLLBACK_FAILURE.md"
            self.assertTrue(failure.exists())

            rescue_dirs = list((cp.patch_path.parent.parent).glob(f"{cp.patch_path.parent.name}_rescue_*"))
            self.assertTrue(rescue_dirs)


if __name__ == "__main__":
    unittest.main()
