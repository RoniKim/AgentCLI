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
ACTION_REGRESSION_FAILED = "regression_failed"
ACTION_STOP = "stop"
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


@dataclass(frozen=True)
class FailureDisposition:
    task_status: str
    action: str
    review_required: bool
    auto_merge_allowed: bool
    auto_retry_allowed: bool
    retry_budget_consumed: bool
    reason: str
    message: str = ""

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


def decide_failure_disposition(
    reason: str,
    *,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    attempt: int = 0,
    max_attempts: int = 1,
    dev_auto_escalate: bool = False,
    dev_escalate_on: set[str] | Sequence[str] | None = None,
) -> FailureDisposition:
    status = normalize_task_status(
        reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
    )
    normalized_reason = str(reason or "").strip() or "unknown"
    can_retry = (
        is_auto_retry_allowed(status)
        and bool(dev_auto_escalate)
        and (attempt + 1) < max_attempts
        and normalized_reason in set(dev_escalate_on or [])
    )
    if status == TASK_STATUS_COMPLETED:
        action = ACTION_AUTO_MERGE
        message = "Task completed; strict gates may auto-merge."
    elif can_retry:
        action = ACTION_RETRY
        message = "Regression failure is eligible for an automated retry."
    elif should_preserve_for_review(status):
        action = ACTION_PRESERVE_FOR_REVIEW
        message = "Work is preserved for manual review instead of auto-merge."
    elif status == TASK_STATUS_REGRESSION_FAILED:
        action = ACTION_REGRESSION_FAILED
        message = "Likely product regression; auto-merge is blocked."
    else:
        action = ACTION_STOP
        message = "Failure needs operator review."

    return FailureDisposition(
        task_status=status,
        action=action,
        review_required=is_manual_review_required(status),
        auto_merge_allowed=is_auto_merge_allowed(status),
        auto_retry_allowed=is_auto_retry_allowed(status),
        retry_budget_consumed=can_retry,
        reason=normalized_reason,
        message=message,
    )


def build_failure_entry(
    *,
    task_id: str,
    reason: str,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    **extra: Any,
) -> dict[str, Any]:
    status = normalize_task_status(
        reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
    )
    disposition = decide_failure_disposition(
        reason,
        task_status=status,
        validations=validations,
        detail=detail,
    )
    entry: dict[str, Any] = {
        "task": task_id,
        "reason": str(reason or "unknown"),
        "status": status,
        "task_status": status,
        "taskStatus": status,
        "outcome_status": status,
        "outcomeStatus": status,
        "review_required": disposition.review_required,
        "reviewRequired": disposition.review_required,
        "auto_merge_allowed": disposition.auto_merge_allowed,
        "autoMergeAllowed": disposition.auto_merge_allowed,
        "auto_retry_allowed": disposition.auto_retry_allowed,
        "autoRetryAllowed": disposition.auto_retry_allowed,
        "disposition": disposition.action,
        "disposition_message": disposition.message,
        "dispositionMessage": disposition.message,
    }
    if detail:
        entry["detail"] = detail
    entry.update(extra)
    return entry
