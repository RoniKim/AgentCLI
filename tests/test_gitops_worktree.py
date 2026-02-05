import subprocess
import tempfile
from pathlib import Path
import unittest

from agent_runner.gitops import apply_patch_to_repo, create_worktree, export_worktree_patch


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        text=True,
        capture_output=True,
    )
    return (result.stdout or "") + (result.stderr or "")


def _init_repo(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    _git(path, "add", "base.txt")
    _git(path, "commit", "-m", "init")


class TestGitopsWorktreePatch(unittest.TestCase):
    def test_export_includes_untracked_and_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            worktree_dir = Path(tmp) / "worktree"
            create_worktree(repo, worktree_dir)

            (worktree_dir / "new_file.txt").write_text("hello\n", encoding="utf-8")
            binary_bytes = b"\x89PNG\r\n\x1a\n\x00\x00\x00binary"
            (worktree_dir / "image.bin").write_bytes(binary_bytes)

            patch_path = Path(tmp) / "worktree.patch"
            export_worktree_patch(worktree_dir, patch_path)

            staged = _git(worktree_dir, "diff", "--cached", "--name-only").strip()
            self.assertEqual(staged, "")

            patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("new file mode", patch_text)
            self.assertIn("GIT binary patch", patch_text)

            apply_patch_to_repo(repo, patch_path)
            self.assertTrue((repo / "new_file.txt").exists())
            self.assertEqual((repo / "new_file.txt").read_text(encoding="utf-8"), "hello\n")
            self.assertEqual((repo / "image.bin").read_bytes(), binary_bytes)

    def test_export_respects_exclude_globs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            worktree_dir = Path(tmp) / "worktree"
            create_worktree(repo, worktree_dir)

            (worktree_dir / ".doc").mkdir(parents=True, exist_ok=True)
            (worktree_dir / ".doc" / "test.txt").write_text("ignore\n", encoding="utf-8")
            (worktree_dir / "keep.txt").write_text("keep\n", encoding="utf-8")

            patch_path = Path(tmp) / "worktree.patch"
            export_worktree_patch(worktree_dir, patch_path, exclude_globs=[".doc/**"])

            patch_text = patch_path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("keep.txt", patch_text)
            self.assertNotIn(".doc/test.txt", patch_text)


if __name__ == "__main__":
    unittest.main()
