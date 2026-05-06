from __future__ import annotations

from pathlib import Path
import shutil
import unittest
import uuid

from agent_runner.todo import (
    TODO_CONTENT_MAX_CHARS,
    build_todo_status,
    ensure_todo_file,
    format_todo_block,
    save_current_todo_text,
)


class TodoManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-scratch" / f"todo_management_{uuid.uuid4().hex}"
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_status_reports_active_path_freshness_and_goals_first_injection_policy(self) -> None:
        todo_path = ensure_todo_file(self.repo)
        todo_path.write_text("# TODO\n\n## Priorities\n- [ ] Ship only unmet GOALS work\n", encoding="utf-8")

        status = build_todo_status(self.repo, include_preview=True)

        self.assertEqual("ready", status["state"])
        self.assertEqual("fresh", status["freshness"])
        self.assertEqual(todo_path.as_posix(), status["activePath"])
        self.assertEqual(".AgentCLI/todo/" + todo_path.name, status["activeRelativePath"])
        self.assertTrue(status["pmInjection"]["enabled"])
        self.assertTrue(status["pmInjection"]["doesNotOverrideGoals"])
        self.assertEqual("goals_first", status["pmInjection"]["priorityPolicy"])
        self.assertIn("Ship only unmet GOALS work", status["preview"]["text"])

    def test_save_current_todo_text_is_repo_local_and_writes_backup(self) -> None:
        todo_path = ensure_todo_file(self.repo)
        todo_path.write_text("old todo\n", encoding="utf-8")

        result = save_current_todo_text(self.repo, "new todo\n")

        self.assertEqual(todo_path.as_posix(), result["activePath"])
        self.assertEqual("new todo\n", todo_path.read_text(encoding="utf-8"))
        self.assertTrue(result["backupPath"])
        self.assertTrue(Path(result["backupPath"]).exists())
        self.assertEqual("old todo\n", Path(result["backupPath"]).read_text(encoding="utf-8"))

    def test_todo_block_includes_goals_first_policy(self) -> None:
        block = format_todo_block(Path("todo.md"), "# TODO\n- [ ] A")

        self.assertIn("TODO SOURCE", block)
        self.assertIn("TODO POLICY", block)
        self.assertIn("do not override GOALS-first gating", block)

    def test_save_rejects_oversized_content(self) -> None:
        ensure_todo_file(self.repo)
        with self.assertRaises(ValueError):
            save_current_todo_text(self.repo, "x" * (TODO_CONTENT_MAX_CHARS + 1))


if __name__ == "__main__":
    unittest.main()
