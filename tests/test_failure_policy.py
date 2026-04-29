import unittest

from agent_runner.failure_policy import (
    ACTION_PRESERVE_FOR_REVIEW,
    ACTION_RETRY,
    build_failure_entry,
    count_task_status_groups,
    decide_failure_disposition,
    should_count_cycle_failure_for_stop,
)
from agent_runner.task_status import TASK_STATUS_BLOCKED_ENV, TASK_STATUS_REGRESSION_FAILED


class FailurePolicyTests(unittest.TestCase):
    def test_blocked_environment_is_preserved_for_review(self) -> None:
        disposition = decide_failure_disposition(
            "test_failed",
            task_status=TASK_STATUS_BLOCKED_ENV,
            attempt=0,
            max_attempts=3,
            dev_auto_escalate=True,
            dev_escalate_on={"test_failed"},
        )

        self.assertEqual(ACTION_PRESERVE_FOR_REVIEW, disposition.action)
        self.assertTrue(disposition.review_required)
        self.assertFalse(disposition.auto_merge_allowed)
        self.assertFalse(disposition.retry_budget_consumed)

    def test_regression_can_retry_when_budget_and_reason_allow_it(self) -> None:
        disposition = decide_failure_disposition(
            "build_failed",
            task_status=TASK_STATUS_REGRESSION_FAILED,
            attempt=0,
            max_attempts=2,
            dev_auto_escalate=True,
            dev_escalate_on={"build_failed"},
        )

        self.assertEqual(ACTION_RETRY, disposition.action)
        self.assertTrue(disposition.retry_budget_consumed)

    def test_failure_entry_has_legacy_and_new_status_fields(self) -> None:
        entry = build_failure_entry(
            task_id="T1",
            reason="needs_dependency",
            detail="mvn: command not found",
        )

        self.assertEqual("T1", entry["task"])
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, entry["status"])
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, entry["task_status"])
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, entry["taskStatus"])
        self.assertTrue(entry["review_required"])
        self.assertFalse(entry["auto_merge_allowed"])

    def test_blocked_env_cycle_failure_does_not_count_toward_stop(self) -> None:
        self.assertFalse(
            should_count_cycle_failure_for_stop(
                reason="build_failed",
                task_statuses=["blocked_env"],
                rc=1,
            )
        )
        self.assertTrue(
            should_count_cycle_failure_for_stop(
                reason="build_failed",
                task_statuses=["blocked_env", "regression_failed"],
                rc=1,
            )
        )
        self.assertTrue(
            should_count_cycle_failure_for_stop(
                reason="budget_exceeded",
                task_statuses=["blocked_env"],
                rc=0,
            )
        )

    def test_failure_status_groups_split_operational_buckets(self) -> None:
        counts = count_task_status_groups(
            ["blocked_env", "review_required", "test_contract_changed", "regression_failed", "failed", "completed"]
        )

        self.assertEqual(1, counts["blocked_env"])
        self.assertEqual(2, counts["review"])
        self.assertEqual(2, counts["regression"])


if __name__ == "__main__":
    unittest.main()
