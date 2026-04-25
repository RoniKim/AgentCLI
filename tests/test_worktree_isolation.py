from __future__ import annotations

import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.gitops import _git_data_lines, create_worktree, default_worktree_dir


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


class WorktreeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = ROOT / ".tmp-worktree-isolation-tests"
        self.fixture_base.mkdir(exist_ok=True)
        self.fixture_root = self.fixture_base / f"{self._testMethodName}-{uuid.uuid4().hex}"
        self.fixture_root.mkdir()
        self.repo = self.fixture_root / "repo"
        self.repo.mkdir()
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260425-211701"
        self.run_dir.mkdir(parents=True)
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))

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


if __name__ == "__main__":
    unittest.main()
