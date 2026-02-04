import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_runner.gitops import create_checkpoint, restore_checkpoint


def _run(cmd, cwd: Path) -> str:
    r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())
    return r.stdout.strip()


def _init_repo(repo: Path) -> None:
    _run(["git", "init"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Tester"], repo)
    (repo / "a.txt").write_text("base\n", encoding="utf-8")
    _run(["git", "add", "a.txt"], repo)
    _run(["git", "commit", "-m", "init"], repo)


class TestGitOpsRollback(unittest.TestCase):
    def test_restore_blocked_when_not_dangerous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            (repo / "a.txt").write_text("change\n", encoding="utf-8")
            (repo / "b.txt").write_text("untracked\n", encoding="utf-8")

            cp = create_checkpoint(repo, repo / "checkpoint")
            run_dir = Path(tmp) / "run"
            run_dir.mkdir()

            before = _run(["git", "status", "--porcelain"], repo)
            with self.assertRaises(RuntimeError):
                restore_checkpoint(repo, cp, dangerous=False, run_dir=run_dir, stop_path=None)
            after = _run(["git", "status", "--porcelain"], repo)

            self.assertEqual(before, after)
            self.assertTrue((run_dir / "ROLLBACK_BLOCKED.md").exists())

    def test_restore_success_when_dangerous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            (repo / "a.txt").write_text("checkpoint\n", encoding="utf-8")
            cp = create_checkpoint(repo, repo / "checkpoint")
            _run(["git", "reset", "--hard"], repo)

            restore_checkpoint(repo, cp, dangerous=True, run_dir=repo, stop_path=None)

            self.assertEqual((repo / "a.txt").read_text(encoding="utf-8"), "checkpoint\n")

    def test_restore_failure_creates_report_and_rescue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            (repo / "a.txt").write_text("checkpoint\n", encoding="utf-8")
            cp = create_checkpoint(repo, repo / "checkpoint")
            cp.patch_path.write_text("\n", encoding="utf-8")

            run_dir = Path(tmp) / "run"
            run_dir.mkdir()

            with self.assertRaises(RuntimeError):
                restore_checkpoint(repo, cp, dangerous=True, run_dir=run_dir, stop_path=None)

            self.assertTrue((run_dir / "ROLLBACK_FAILURE.md").exists())
            rescue_dirs = list(run_dir.glob("checkpoint_rescue_*"))
            self.assertTrue(rescue_dirs)


if __name__ == "__main__":
    unittest.main()
