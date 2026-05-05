import unittest

from agent_runner.backends.claudecode import (
    _build_claude_failure_outcome,
    _record_claude_failure_result,
    _record_claude_failure_state,
)
from agent_runner.task_failures import build_task_failure_result, build_task_failure_state_entry
from agent_runner.task_status import (
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_REGRESSION_FAILED,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
)


class ClaudeFailureSchemaTests(unittest.TestCase):
    def _assert_enriched_failure_shape(self, payload: dict[str, object]) -> None:
        for key in (
            "reason",
            "status",
            "task_status",
            "taskStatus",
            "outcome_status",
            "outcomeStatus",
            "review_required",
            "reviewRequired",
            "retry_eligible",
            "retryEligible",
            "retry_allowed_now",
            "retryAllowedNow",
            "auto_retry_allowed",
            "autoRetryAllowed",
            "retry_budget_consumed",
            "retryBudgetConsumed",
            "disposition",
            "disposition_message",
            "dispositionMessage",
            "attempt",
            "max_attempts",
            "detail",
            "validation_artifact",
            "validationArtifact",
            "validation_status",
            "validationStatus",
        ):
            self.assertIn(key, payload)

    def test_blocked_env_helper_matches_shared_helpers(self) -> None:
        validation_artifact = "C:/tmp/tasks/T1/attempt_01/validation.json"
        outcome = _build_claude_failure_outcome(
            "build_failed",
            detail="mvn: command not found while restoring packages",
            validation_artifact=validation_artifact,
            attempt_index=0,
            max_attempts=3,
        )

        self.assertEqual(TASK_STATUS_BLOCKED_ENV, outcome.task_status)

        state: dict[str, object] = {}
        failed_entry = _record_claude_failure_state(
            state,
            task_id="T1",
            reason="build_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        pending_entry = _record_claude_failure_state(
            state,
            bucket="pending_review",
            task_id="T1",
            reason="build_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            title="Restore toolchain",
            cycle=2,
            step=4,
            branch="review/T1",
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        task_results: list[dict[str, object]] = []
        result = _record_claude_failure_result(
            task_results,
            task_id="T1",
            task_title="Restore toolchain",
            reason="build_failed",
            duration=1.25,
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )

        expected_failed = build_task_failure_state_entry(
            task_id="T1",
            reason="build_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        expected_pending = build_task_failure_state_entry(
            task_id="T1",
            reason="build_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
            extra={
                "title": "Restore toolchain",
                "cycle": 2,
                "step": 4,
                "branch": "review/T1",
            },
        )
        expected_result = build_task_failure_result(
            task_id="T1",
            task_title="Restore toolchain",
            reason="build_failed",
            duration=1.25,
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )

        self.assertEqual(expected_failed, failed_entry)
        self.assertEqual(expected_pending, pending_entry)
        self.assertEqual(expected_result, result)
        self.assertEqual([failed_entry], state["failed"])
        self.assertEqual([pending_entry], state["pending_review"])
        self.assertEqual([result], task_results)
        self._assert_enriched_failure_shape(failed_entry)
        self._assert_enriched_failure_shape(pending_entry)
        self._assert_enriched_failure_shape(result)

    def test_test_contract_changed_helper_matches_shared_helpers(self) -> None:
        validation_artifact = "C:/tmp/tasks/T2/attempt_01/validation.json"
        outcome = _build_claude_failure_outcome(
            "test_failed",
            detail="locator getByRole(button) failed after accessible name drift",
            validation_artifact=validation_artifact,
            attempt_index=0,
            max_attempts=2,
        )

        self.assertEqual(TASK_STATUS_TEST_CONTRACT_CHANGED, outcome.task_status)

        state: dict[str, object] = {}
        failed_entry = _record_claude_failure_state(
            state,
            task_id="T2",
            reason="test_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=2,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        pending_entry = _record_claude_failure_state(
            state,
            bucket="pending_review",
            task_id="T2",
            reason="test_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=2,
            title="Repair selector contract",
            cycle=5,
            step=1,
            rescue_branch="rescue/T2",
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        task_results: list[dict[str, object]] = []
        result = _record_claude_failure_result(
            task_results,
            task_id="T2",
            task_title="Repair selector contract",
            reason="test_failed",
            duration=2.5,
            failure_outcome=outcome,
            attempt=1,
            max_attempts=2,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )

        expected_failed = build_task_failure_state_entry(
            task_id="T2",
            reason="test_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=2,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        expected_pending = build_task_failure_state_entry(
            task_id="T2",
            reason="test_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=2,
            validation_artifact=validation_artifact,
            validation_status="failed",
            extra={
                "title": "Repair selector contract",
                "cycle": 5,
                "step": 1,
                "rescue_branch": "rescue/T2",
            },
        )
        expected_result = build_task_failure_result(
            task_id="T2",
            task_title="Repair selector contract",
            reason="test_failed",
            duration=2.5,
            failure_outcome=outcome,
            attempt=1,
            max_attempts=2,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )

        self.assertEqual(expected_failed, failed_entry)
        self.assertEqual(expected_pending, pending_entry)
        self.assertEqual(expected_result, result)
        self._assert_enriched_failure_shape(failed_entry)
        self._assert_enriched_failure_shape(pending_entry)
        self._assert_enriched_failure_shape(result)

    def test_regression_failed_helper_skips_pending_review_but_keeps_enriched_failure_schema(self) -> None:
        validation_artifact = "C:/tmp/tasks/T3/attempt_01/validation.json"
        outcome = _build_claude_failure_outcome(
            "build_failed",
            detail="error CS1002: ; expected",
            validation_artifact=validation_artifact,
            attempt_index=0,
            max_attempts=3,
        )

        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, outcome.task_status)

        state: dict[str, object] = {}
        failed_entry = _record_claude_failure_state(
            state,
            task_id="T3",
            reason="build_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        pending_entry = _record_claude_failure_state(
            state,
            bucket="pending_review",
            task_id="T3",
            reason="build_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            title="Fix compile regression",
            cycle=3,
            step=2,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        task_results: list[dict[str, object]] = []
        result = _record_claude_failure_result(
            task_results,
            task_id="T3",
            task_title="Fix compile regression",
            reason="build_failed",
            duration=3.75,
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )

        expected_failed = build_task_failure_state_entry(
            task_id="T3",
            reason="build_failed",
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )
        expected_result = build_task_failure_result(
            task_id="T3",
            task_title="Fix compile regression",
            reason="build_failed",
            duration=3.75,
            failure_outcome=outcome,
            attempt=1,
            max_attempts=3,
            validation_artifact=validation_artifact,
            validation_status="failed",
        )

        self.assertEqual(expected_failed, failed_entry)
        self.assertIsNone(pending_entry)
        self.assertEqual(expected_result, result)
        self.assertEqual([failed_entry], state["failed"])
        self.assertNotIn("pending_review", state)
        self._assert_enriched_failure_shape(failed_entry)
        self._assert_enriched_failure_shape(result)


if __name__ == "__main__":
    unittest.main()
