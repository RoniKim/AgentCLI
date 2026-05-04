from __future__ import annotations

from collections.abc import Mapping, MutableMapping, MutableSequence, Sequence
from typing import Any

from .failure_policy import build_failure_entry, should_preserve_for_review
from .state import save_state


def build_task_failure_state_entry(
    *,
    task_id: str,
    reason: str,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return build_failure_entry(
        task_id=task_id,
        reason=reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
        **dict(extra or {}),
    )


def record_task_failure_state(
    state: MutableMapping[str, Any],
    *,
    state_path: Any = None,
    bucket: str = "failed",
    task_id: str,
    reason: str,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    entry = build_task_failure_state_entry(
        task_id=task_id,
        reason=reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
        extra=extra,
    )
    if bucket == "pending_review" and not should_preserve_for_review(str(entry.get("task_status") or entry.get("status") or "")):
        return None
    items = state.setdefault(bucket, [])
    if not isinstance(items, list):
        items = []
        state[bucket] = items
    items.append(entry)
    if state_path is not None:
        save_state(state_path, state)  # type: ignore[arg-type]
    return entry


def build_task_failure_result(
    *,
    task_id: str,
    task_title: str,
    reason: str,
    duration: float,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    attempt: int | None = None,
    max_attempts: int | None = None,
    validation_artifact: str = "",
    validation_status: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    failure_entry = build_task_failure_state_entry(
        task_id=task_id,
        reason=reason,
        task_status=task_status,
        validations=validations,
        detail=detail,
    )
    outcome_status = str(
        failure_entry.get("task_status")
        or failure_entry.get("taskStatus")
        or failure_entry.get("outcome_status")
        or failure_entry.get("status")
        or "failed"
    )
    result: dict[str, Any] = {
        "id": task_id,
        "title": task_title,
        "status": outcome_status,
        "reason": str(failure_entry.get("reason") or reason or "unknown"),
        "duration": duration,
        "task_status": outcome_status,
        "taskStatus": outcome_status,
        "outcome_status": outcome_status,
        "outcomeStatus": outcome_status,
        "review_required": bool(failure_entry.get("review_required", False)),
        "reviewRequired": bool(failure_entry.get("reviewRequired", False)),
    }
    if detail:
        result["detail"] = detail
    if attempt is not None:
        result["attempt"] = attempt
    if max_attempts is not None:
        result["max_attempts"] = max_attempts
    if validation_artifact:
        result["validation_artifact"] = validation_artifact
        result["validationArtifact"] = validation_artifact
    if validation_status:
        result["validation_status"] = validation_status
        result["validationStatus"] = validation_status
    result.update(dict(extra or {}))
    return result


def record_task_failure_result(
    task_results: MutableSequence[dict[str, Any]],
    *,
    task_id: str,
    task_title: str,
    reason: str,
    duration: float,
    task_status: str = "",
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    attempt: int | None = None,
    max_attempts: int | None = None,
    validation_artifact: str = "",
    validation_status: str = "",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = build_task_failure_result(
        task_id=task_id,
        task_title=task_title,
        reason=reason,
        duration=duration,
        task_status=task_status,
        validations=validations,
        detail=detail,
        attempt=attempt,
        max_attempts=max_attempts,
        validation_artifact=validation_artifact,
        validation_status=validation_status,
        extra=extra,
    )
    task_results.append(result)
    return result
