import unittest

from agent_runner.task_status import (
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_REGRESSION_FAILED,
    TASK_STATUS_REVIEW_REQUIRED,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
    classify_task_failure,
    is_auto_merge_allowed,
    is_auto_retry_allowed,
)


class TaskStatusClassificationTests(unittest.TestCase):
    def test_completed_is_only_auto_merge_status(self) -> None:
        self.assertEqual(TASK_STATUS_COMPLETED, classify_task_failure("completed"))
        self.assertTrue(is_auto_merge_allowed(TASK_STATUS_COMPLETED))
        self.assertFalse(is_auto_merge_allowed(TASK_STATUS_REVIEW_REQUIRED))

    def test_environment_failures_do_not_auto_retry(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[
                {
                    "kind": "test",
                    "summary": "ModuleNotFoundError: No module named 'playwright'",
                }
            ],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)
        self.assertFalse(is_auto_retry_allowed(status))

    def test_ui_contract_failures_require_contract_review(self) -> None:
        status = classify_task_failure(
            "fast_regression_failed",
            validations=[
                {
                    "gate": "fast_web_worktree_regression",
                    "failure_summary": "Playwright locator expected button to_be_visible by accessible name",
                }
            ],
        )
        self.assertEqual(TASK_STATUS_TEST_CONTRACT_CHANGED, status)
        self.assertFalse(is_auto_retry_allowed(status))

    def test_broad_regression_defaults_to_manual_review(self) -> None:
        status = classify_task_failure(
            "fast_regression_failed",
            validations=[{"summary": "FAILED tests/test_integration.py::test_slow_path"}],
        )
        self.assertEqual(TASK_STATUS_REVIEW_REQUIRED, status)

    def test_compiler_errors_are_regression_failures(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "Program.cs(10,5): error CS1002: ; expected"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)
        self.assertTrue(is_auto_retry_allowed(status))


if __name__ == "__main__":
    unittest.main()
