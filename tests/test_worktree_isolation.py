from __future__ import annotations

import json
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


from agent_runner.gitops import WorktreeSafetyError, _git_data_lines, create_worktree, default_worktree_dir
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

    def _load_contract(self) -> dict[str, object]:
        return json.loads(self.contract_path.read_text(encoding="utf-8"))

    def _write_contract(self, **updates: object) -> None:
        payload = self._load_contract()
        payload.update(updates)
        self.contract_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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


if __name__ == "__main__":
    unittest.main()
