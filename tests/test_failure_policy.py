import unittest

from agent_runner.failure_policy import (
    ACTION_ABANDON_BRANCH,
    ACTION_PRESERVE_FOR_REVIEW,
    ACTION_RESTORE_CHECKPOINT,
    ACTION_RETRY,
    ACTION_STOP_RUN,
    build_failure_outcome,
    build_failure_entry,
    count_task_status_groups,
    decide_failure_disposition,
    should_count_cycle_failure_for_stop,
)
from agent_runner.task_failures import build_task_failure_result, record_task_failure_state
from agent_runner.task_status import (
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_REGRESSION_FAILED,
    TASK_STATUS_REVIEW_REQUIRED,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
)


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
        self.assertFalse(disposition.retry_eligible)
        self.assertFalse(disposition.retry_allowed_now)
        self.assertFalse(disposition.retry_budget_consumed)

    def test_typed_failure_outcome_preserves_test_contract_review_status(self) -> None:
        outcome = build_failure_outcome(
            "test_failed",
            task_status=TASK_STATUS_TEST_CONTRACT_CHANGED,
            detail="locator drift",
            validation_artifact="C:/tmp/validation.json",
            attempt=0,
            max_attempts=3,
        )

        disposition = decide_failure_disposition(
            outcome.reason,
            failure_outcome=outcome,
            dev_auto_escalate=True,
            dev_escalate_on={"test_failed"},
            has_checkpoint=True,
        )

        self.assertEqual(TASK_STATUS_TEST_CONTRACT_CHANGED, disposition.task_status)
        self.assertEqual(ACTION_PRESERVE_FOR_REVIEW, disposition.action)
        self.assertFalse(disposition.retry_eligible)
        self.assertFalse(disposition.retry_allowed_now)

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
        self.assertTrue(disposition.retry_eligible)
        self.assertTrue(disposition.retry_allowed_now)
        self.assertTrue(disposition.retry_budget_consumed)

    def test_exhausted_attempts_abandons_branch_when_one_exists(self) -> None:
        disposition = decide_failure_disposition(
            "exhausted_attempts",
            task_status=TASK_STATUS_REVIEW_REQUIRED,
            attempt=2,
            max_attempts=3,
            has_task_branch=True,
        )

        self.assertEqual(ACTION_ABANDON_BRANCH, disposition.action)
        self.assertFalse(disposition.retry_allowed_now)

    def test_exhausted_attempts_restores_checkpoint_without_branch(self) -> None:
        disposition = decide_failure_disposition(
            "exhausted_attempts",
            task_status=TASK_STATUS_REVIEW_REQUIRED,
            attempt=2,
            max_attempts=3,
            has_checkpoint=True,
        )

        self.assertEqual(ACTION_RESTORE_CHECKPOINT, disposition.action)
        self.assertFalse(disposition.retry_allowed_now)

    def test_rollback_failure_stops_run(self) -> None:
        disposition = decide_failure_disposition(
            "rollback_failed",
            task_status=TASK_STATUS_REVIEW_REQUIRED,
            attempt=0,
            max_attempts=3,
            has_task_branch=True,
            has_checkpoint=True,
        )

        self.assertEqual(ACTION_STOP_RUN, disposition.action)
        self.assertFalse(disposition.retry_allowed_now)

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
        self.assertFalse(entry["retry_eligible"])
        self.assertFalse(entry["retry_allowed_now"])
        self.assertFalse(entry["retry_budget_consumed"])
        self.assertEqual(ACTION_PRESERVE_FOR_REVIEW, entry["disposition"])

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

    def test_regression_failure_record_keeps_failed_bucket_and_skips_pending_review(self) -> None:
        state = {"failed": []}

        failed_entry = record_task_failure_state(
            state,
            task_id="T2",
            reason="fast_regression_failed",
            task_status=TASK_STATUS_REGRESSION_FAILED,
            detail="playwright smoke failed",
        )
        pending_entry = record_task_failure_state(
            state,
            bucket="pending_review",
            task_id="T2",
            reason="fast_regression_failed",
            task_status=TASK_STATUS_REGRESSION_FAILED,
            detail="playwright smoke failed",
        )

        self.assertIsNotNone(failed_entry)
        self.assertEqual(1, len(state["failed"]))
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, failed_entry["status"])
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, failed_entry["task_status"])
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, failed_entry["taskStatus"])
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, failed_entry["outcome_status"])
        self.assertTrue(failed_entry["review_required"])
        self.assertIsNone(pending_entry)
        self.assertNotIn("pending_review", state)

    def test_blocked_env_pending_review_record_is_preserved(self) -> None:
        state: dict[str, object] = {}

        entry = record_task_failure_state(
            state,
            bucket="pending_review",
            task_id="T3",
            reason="needs_dependency",
            detail="mvn: command not found",
            extra={
                "title": "Install dependency",
                "cycle": 2,
                "step": 4,
                "attempt": 1,
                "max_attempts": 3,
                "validation_artifact": "C:/tmp/validation.json",
            },
        )

        self.assertIsNotNone(entry)
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, entry["task_status"])
        self.assertTrue(entry["review_required"])
        self.assertEqual("Install dependency", entry["title"])
        self.assertEqual("C:/tmp/validation.json", entry["validation_artifact"])
        self.assertEqual(1, len(state["pending_review"]))

    def test_failure_result_preserves_validation_artifact_and_detail_metadata(self) -> None:
        result = build_task_failure_result(
            task_id="T4",
            task_title="Validate browser console",
            reason="build_failed",
            task_status=TASK_STATUS_REGRESSION_FAILED,
            detail="BrowserTests failed with stale lock reuse.",
            duration=3.5,
            attempt=2,
            max_attempts=3,
            validation_artifact="C:/tmp/tasks/T4/attempt_02/validation.json",
            validation_status="failed",
            extra={"goal_ref": "G4"},
        )

        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, result["status"])
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, result["task_status"])
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, result["taskStatus"])
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, result["outcome_status"])
        self.assertTrue(result["review_required"])
        self.assertEqual("BrowserTests failed with stale lock reuse.", result["detail"])
        self.assertEqual(2, result["attempt"])
        self.assertEqual(3, result["max_attempts"])
        self.assertEqual("C:/tmp/tasks/T4/attempt_02/validation.json", result["validation_artifact"])
        self.assertEqual("C:/tmp/tasks/T4/attempt_02/validation.json", result["validationArtifact"])
        self.assertEqual("failed", result["validation_status"])
        self.assertEqual("failed", result["validationStatus"])
        self.assertEqual("G4", result["goal_ref"])


if __name__ == "__main__":
    unittest.main()
