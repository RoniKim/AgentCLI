from __future__ import annotations

import unittest
from pathlib import Path

from agent_runner.gates import looks_like_no_tests_found
from agent_runner.runtime_contract import RunnerContext


class GatesValidationTests(unittest.TestCase):
    def test_no_tests_found_detects_empty_unittest_run(self) -> None:
        self.assertTrue(
            looks_like_no_tests_found(
                """
                ----------------------------------------------------------------------
                Ran 0 tests in 0.001s

                OK
                """
            )
        )

    def test_no_tests_found_ignores_empty_tail_after_successful_suites(self) -> None:
        log_text = """
        ................................................................
        ----------------------------------------------------------------------
        Ran 156 tests in 244.264s

        OK
        .................................................
        ----------------------------------------------------------------------
        Ran 49 tests in 45.571s

        OK
        s
        ----------------------------------------------------------------------
        Ran 0 tests in 0.001s

        OK (skipped=1)
        """

        self.assertFalse(looks_like_no_tests_found(log_text))

    def test_no_tests_found_ignores_zero_passed_when_tests_ran(self) -> None:
        self.assertFalse(looks_like_no_tests_found("Ran 3 tests in 0.5s\nFAILED (failures=3)\n0 tests passed"))

    def test_attempt_context_keeps_validation_artifact_filenames_stable(self) -> None:
        attempt_context = RunnerContext((Path.cwd() / ".tmp_runner_context").resolve()).task_context(
            cycle=1,
            step=1,
            task_id="T31",
        ).attempt_context(1)

        self.assertEqual("build.txt", attempt_context.build_log_path.name)
        self.assertEqual("test.txt", attempt_context.test_log_path.name)
        self.assertEqual("fast_web_worktree_regression.json", attempt_context.fast_web_worktree_regression_path.name)
        self.assertEqual("validation.json", attempt_context.validation_json_path.name)
        self.assertEqual("validation.txt", attempt_context.validation_txt_path.name)


if __name__ == "__main__":
    unittest.main()
