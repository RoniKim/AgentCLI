import unittest
import inspect
from pathlib import Path

import agent_runner.cycle as cycle_module
import agent_runner.backends.claudecode as claudecode_module
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

    def test_claude_backend_uses_shared_completion_report_and_pr_queue_helpers(self) -> None:
        claude_source = inspect.getsource(claudecode_module)
        codex_source = inspect.getsource(cycle_module)

        for source in (claude_source, codex_source):
            self.assertIn("resolve_completion_final_reason(", source)
            self.assertIn('parse_goals_completion(_gt_eval or ""', source)

        self.assertIn("write_run_report_artifacts(", claude_source)
        self.assertIn("queue_review_packet(", claude_source)
        self.assertIn("read_pending_worktree_merge(", claude_source)
        self.assertIn("worktree_review_packet_failed", claude_source)

    def test_claude_backend_parity_tests_cover_shared_helpers_and_advanced_modes(self) -> None:
        claude_source = inspect.getsource(claudecode_module)
        codex_source = inspect.getsource(cycle_module)
        parity_markers = (
            "classify_task_validation_status(",
            "write_task_validation_artifacts(",
            "build_failure_outcome(",
            "should_preserve_for_review(",
            "record_task_failure_state(",
            "record_task_failure_result(",
            "queue_review_packet(",
            "write_run_report_artifacts(",
        )
        for marker in parity_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, codex_source)
                self.assertIn(marker, claude_source)

        self.assertIn("apply_extensions(ext_ctx, cfg, kwargs, stage)", claude_source)
        self.assertIn("cfg.mcp_tools_enabled", claude_source)
        self.assertIn("cfg.hooks_enabled", claude_source)
        self.assertIn("cfg.can_use_tool_enabled", claude_source)
        self.assertIn("cfg.subagents_enabled", claude_source)

        tests_root = Path(__file__).resolve().parent
        failure_tests = (tests_root / "test_claude_failure_schema.py").read_text(encoding="utf-8")
        advanced_tests = (tests_root / "test_claude_advanced_controls.py").read_text(encoding="utf-8")
        self.assertIn("_assert_enriched_failure_shape", failure_tests)
        self.assertIn("build_task_failure_result", failure_tests)
        self.assertIn("validation_artifact", failure_tests)
        self.assertIn("test_build_options_applies_mcp_hooks_dynamic_permission_and_subagents", advanced_tests)
        self.assertIn("test_disabled_advanced_controls_do_not_mutate_claude_options", advanced_tests)
        for token in ("mcp_servers", "hooks", "can_use_tool", "agents", "strict_isolation"):
            self.assertIn(token, advanced_tests)

    def test_claude_no_diff_success_paths_continue_to_validation_gates(self) -> None:
        claude_source = inspect.getsource(claudecode_module)
        start = claude_source.index("# Check if agent determined the task was already implemented")
        end = claude_source.index("if build_enabled:", start)
        no_diff_block = claude_source[start:end]

        self.assertIn("dev_no_diff_validation_required", no_diff_block)
        already_done_block = no_diff_block[
            no_diff_block.index("if task_already_done:") : no_diff_block.index("# Detect phantom edits")
        ]
        self.assertIn("continuing to validation gates", already_done_block)
        self.assertNotIn("task_completed = True", already_done_block)
        self.assertNotIn("task_results.append", already_done_block)
        self.assertNotIn("break", already_done_block)

        phantom_success_block = no_diff_block[
            no_diff_block.index("if changed:") : no_diff_block.index("# If still no diff after retry")
        ]
        self.assertIn("phantom_retry_success", phantom_success_block)
        self.assertNotIn("task_completed = True", phantom_success_block)
        self.assertNotIn("task_results.append", phantom_success_block)
        self.assertNotIn("break", phantom_success_block)

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
