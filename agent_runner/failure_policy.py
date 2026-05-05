from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

from .task_status import (
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_REGRESSION_FAILED,
    TASK_STATUS_REVIEW_REQUIRED,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
    classify_task_failure,
    is_auto_merge_allowed,
    is_auto_retry_allowed,
    is_manual_review_required,
)


ACTION_AUTO_MERGE = "auto_merge"
ACTION_RETRY = "retry"
ACTION_PRESERVE_FOR_REVIEW = "preserve_for_review"
ACTION_ABANDON_BRANCH = "abandon_branch"
ACTION_RESTORE_CHECKPOINT = "restore_checkpoint"
ACTION_STOP_RUN = "stop_run"
STATUS_GROUP_COMPLETED = "completed"
STATUS_GROUP_BLOCKED_ENV = "blocked_env"
STATUS_GROUP_REVIEW = "review"
STATUS_GROUP_REGRESSION = "regression"
STATUS_GROUP_OTHER = "other"

PRESERVE_FOR_REVIEW_STATUSES = {
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
    TASK_STATUS_REVIEW_REQUIRED,
}
REVIEW_TASK_STATUSES = {
    TASK_STATUS_REVIEW_REQUIRED,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
}
REGRESSION_TASK_STATUSES = {
    TASK_STATUS_REGRESSION_FAILED,
    "failed",
}
STOP_RUN_REASONS = {
    "abandon_failed",
    "rollback_blocked",
    "rollback_failed",
    "budget_exceeded",
}
FORCE_TERMINAL_DISPOSITION_REASONS = {"exhausted_attempts"}


@dataclass(frozen=True)
class FailureDisposition:
    task_status: str
    action: str
    review_required: bool
    auto_merge_allowed: bool
    retry_eligible: bool
    retry_allowed_now: bool
    auto_retry_allowed: bool
    retry_budget_consumed: bool
    reason: str
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class FailureOutcome:
    reason: str
    task_status: str
    detail: str = ""
    validation_artifact: str = ""
    attempt: int = 0
    max_attempts: int = 1

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_task_status(
    reason: str,
    *,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
) -> str:
    status = str(task_status or "").strip().lower()
    if status:
        return status
    return classify_task_failure(reason, validations=validations or [], detail=detail)


def normalize_reason(reason: str) -> str:
    return str(reason or "").strip().lower() or "unknown"


def normalize_reason_set(reasons: set[str] | Sequence[str] | None) -> set[str]:
    return {
        normalize_reason(str(reason))
        for reason in (reasons or [])
        if str(reason or "").strip()
    }


def should_preserve_for_review(task_status: str) -> bool:
    return str(task_status or "").strip().lower() in PRESERVE_FOR_REVIEW_STATUSES


def is_blocked_env_status(task_status: str) -> bool:
    return str(task_status or "").strip().lower() == TASK_STATUS_BLOCKED_ENV


def task_status_group(task_status: str) -> str:
    status = str(task_status or "").strip().lower()
    if status == TASK_STATUS_COMPLETED or status == "done":
        return STATUS_GROUP_COMPLETED
    if status == TASK_STATUS_BLOCKED_ENV:
        return STATUS_GROUP_BLOCKED_ENV
    if status in REVIEW_TASK_STATUSES:
        return STATUS_GROUP_REVIEW
    if status in REGRESSION_TASK_STATUSES:
        return STATUS_GROUP_REGRESSION
    return STATUS_GROUP_OTHER


def count_task_status_groups(statuses: Sequence[str]) -> dict[str, int]:
    counts = {
        STATUS_GROUP_BLOCKED_ENV: 0,
        STATUS_GROUP_REVIEW: 0,
        STATUS_GROUP_REGRESSION: 0,
        STATUS_GROUP_OTHER: 0,
    }
    for status in statuses:
        group = task_status_group(status)
        if group == STATUS_GROUP_COMPLETED:
            continue
        counts[group] = counts.get(group, 0) + 1
    return counts


def should_count_cycle_failure_for_stop(
    *,
    reason: str,
    task_statuses: Sequence[str],
    rc: int,
) -> bool:
    if reason == "budget_exceeded":
        return True
    if rc == 0:
        return False
    statuses = [str(status or "").strip().lower() for status in task_statuses if str(status or "").strip()]
    if statuses and all(status == TASK_STATUS_BLOCKED_ENV for status in statuses):
        return False
    return True


def has_retry_budget(*, attempt: int, max_attempts: int) -> bool:
    return (int(attempt) + 1) < max(int(max_attempts), 0)


def build_failure_outcome(
    reason: str,
    *,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    validation_artifact: str = "",
    attempt: int = 0,
    max_attempts: int = 1,
) -> FailureOutcome:
    normalized_reason = normalize_reason(reason)
    status = normalize_task_status(
        normalized_reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
    )
    return FailureOutcome(
        reason=normalized_reason,
        task_status=status,
        detail=str(detail or ""),
        validation_artifact=str(validation_artifact or ""),
        attempt=int(attempt),
        max_attempts=int(max_attempts),
    )


def terminal_failure_action(*, has_task_branch: bool, has_checkpoint: bool) -> str:
    if has_task_branch:
        return ACTION_ABANDON_BRANCH
    if has_checkpoint:
        return ACTION_RESTORE_CHECKPOINT
    return ACTION_STOP_RUN


def disposition_message(
    action: str,
    *,
    task_status: str,
    reason: str,
) -> str:
    if action == ACTION_AUTO_MERGE:
        return "Task completed; strict gates may auto-merge."
    if action == ACTION_RETRY:
        return "Regression failure is eligible for an automated retry."
    if action == ACTION_PRESERVE_FOR_REVIEW:
        return "Work is preserved for manual review instead of auto-merge."
    if action == ACTION_ABANDON_BRANCH:
        if reason == "exhausted_attempts":
            return "Retry budget is exhausted; abandon the task branch and queue review."
        return "Failure is not retryable now; abandon the task branch and queue review."
    if action == ACTION_RESTORE_CHECKPOINT:
        if reason == "exhausted_attempts":
            return "Retry budget is exhausted; restore the last checkpoint and queue review."
        return "Failure is not retryable now; restore the last checkpoint and queue review."
    if reason in STOP_RUN_REASONS:
        return "Recovery flow failed; stop the run for operator review."
    if task_status == TASK_STATUS_COMPLETED:
        return "Failure disposition is not required for completed work."
    return "Failure needs operator review."


def decide_failure_disposition(
    reason: str,
    *,
    failure_outcome: FailureOutcome | None = None,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    attempt: int = 0,
    max_attempts: int = 1,
    dev_auto_escalate: bool = False,
    dev_escalate_on: set[str] | Sequence[str] | None = None,
    has_task_branch: bool = False,
    has_checkpoint: bool = False,
) -> FailureDisposition:
    outcome = failure_outcome or build_failure_outcome(
        reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    status = outcome.task_status
    normalized_reason = outcome.reason
    retry_eligible = is_auto_retry_allowed(status)
    can_retry = (
        retry_eligible
        and bool(dev_auto_escalate)
        and has_retry_budget(attempt=outcome.attempt, max_attempts=outcome.max_attempts)
        and normalized_reason in normalize_reason_set(dev_escalate_on)
    )
    if status == TASK_STATUS_COMPLETED:
        action = ACTION_AUTO_MERGE
    elif normalized_reason in STOP_RUN_REASONS:
        action = ACTION_STOP_RUN
    elif can_retry:
        action = ACTION_RETRY
    elif normalized_reason in FORCE_TERMINAL_DISPOSITION_REASONS:
        action = terminal_failure_action(
            has_task_branch=has_task_branch,
            has_checkpoint=has_checkpoint,
        )
    elif should_preserve_for_review(status):
        action = ACTION_PRESERVE_FOR_REVIEW
    else:
        action = terminal_failure_action(
            has_task_branch=has_task_branch,
            has_checkpoint=has_checkpoint,
        )
    message = disposition_message(
        action,
        task_status=status,
        reason=normalized_reason,
    )

    return FailureDisposition(
        task_status=status,
        action=action,
        review_required=is_manual_review_required(status),
        auto_merge_allowed=is_auto_merge_allowed(status),
        retry_eligible=retry_eligible,
        retry_allowed_now=can_retry,
        auto_retry_allowed=retry_eligible,
        retry_budget_consumed=can_retry,
        reason=normalized_reason,
        message=message,
    )


def build_failure_entry(
    *,
    task_id: str,
    reason: str,
    failure_outcome: FailureOutcome | None = None,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    attempt: int = 0,
    max_attempts: int = 1,
    dev_auto_escalate: bool = False,
    dev_escalate_on: set[str] | Sequence[str] | None = None,
    has_task_branch: bool = False,
    has_checkpoint: bool = False,
    **extra: Any,
) -> dict[str, Any]:
    outcome = failure_outcome or build_failure_outcome(
        reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
        attempt=attempt,
        max_attempts=max_attempts,
    )
    status = outcome.task_status
    disposition = decide_failure_disposition(
        outcome.reason,
        failure_outcome=outcome,
        dev_auto_escalate=dev_auto_escalate,
        dev_escalate_on=dev_escalate_on,
        has_task_branch=has_task_branch,
        has_checkpoint=has_checkpoint,
    )
    entry: dict[str, Any] = {
        "task": task_id,
        "reason": outcome.reason,
        "status": status,
        "task_status": status,
        "taskStatus": status,
        "outcome_status": status,
        "outcomeStatus": status,
        "review_required": disposition.review_required,
        "reviewRequired": disposition.review_required,
        "auto_merge_allowed": disposition.auto_merge_allowed,
        "autoMergeAllowed": disposition.auto_merge_allowed,
        "retry_eligible": disposition.retry_eligible,
        "retryEligible": disposition.retry_eligible,
        "retry_allowed_now": disposition.retry_allowed_now,
        "retryAllowedNow": disposition.retry_allowed_now,
        "auto_retry_allowed": disposition.auto_retry_allowed,
        "autoRetryAllowed": disposition.auto_retry_allowed,
        "retry_budget_consumed": disposition.retry_budget_consumed,
        "retryBudgetConsumed": disposition.retry_budget_consumed,
        "disposition": disposition.action,
        "disposition_message": disposition.message,
        "dispositionMessage": disposition.message,
    }
    if outcome.detail:
        entry["detail"] = outcome.detail
    entry.update(extra)
    return entry
