from __future__ import annotations

import unittest
from pathlib import Path

from agent_runner.runtime_contract import RunnerContext


class RunnerContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.run_dir = (Path.cwd() / ".tmp_runner_context").resolve()

    def test_build_failure_paths_match_existing_attempt_layout(self) -> None:
        runner_context = RunnerContext(self.run_dir)
        task_context = runner_context.task_context(cycle=1, step=2, task_id="T31")
        attempt_context = task_context.attempt_context(0)

        self.assertEqual(self.run_dir / "tasks" / "c001_s002_T31", task_context.task_dir)
        self.assertEqual(task_context.task_dir / "attempt_00", attempt_context.attempt_dir)
        self.assertEqual(attempt_context.attempt_dir / "build.txt", attempt_context.build_log_path)
        self.assertEqual(attempt_context.attempt_dir / "validation.json", attempt_context.validation_json_path)

    def test_test_failure_paths_keep_validation_text_and_attempt_notes_names(self) -> None:
        runner_context = RunnerContext(self.run_dir)
        attempt_context = runner_context.task_context(cycle=3, step=4, task_id="T31").attempt_context(2)

        self.assertEqual(attempt_context.attempt_dir / "test.txt", attempt_context.test_log_path)
        self.assertEqual(attempt_context.attempt_dir / "validation.txt", attempt_context.validation_txt_path)
        self.assertEqual(attempt_context.attempt_dir / "NOTES.md", attempt_context.notes_path)
        self.assertEqual(attempt_context.attempt_dir / "DEPENDENCY_REQUIRED.md", attempt_context.dependency_required_path)

    def test_fast_regression_paths_preserve_existing_filenames(self) -> None:
        runner_context = RunnerContext(self.run_dir)
        attempt_context = runner_context.task_context(cycle=5, step=6, task_id="T31").attempt_context(1)

        self.assertEqual(
            attempt_context.attempt_dir / "fast_web_worktree_regression.json",
            attempt_context.fast_web_worktree_regression_path,
        )
        self.assertEqual(attempt_context.attempt_dir / "dev_output.txt", attempt_context.dev_output_path)
        self.assertEqual(self.run_dir / "DEPENDENCY_REQUIRED.md", runner_context.dependency_required_path)


if __name__ == "__main__":
    unittest.main()
