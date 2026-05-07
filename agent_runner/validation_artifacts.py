from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .task_status import (
    TASK_STATUS_COMPLETED,
    classify_task_failure,
    is_manual_review_required,
)
from .utils import safe_write_text


def _object_list(values: Sequence[object] | None) -> list[object]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)):
        text = str(values).strip()
        return [text] if text else []
    return list(values)


def _string_list(values: Sequence[object] | None) -> list[str]:
    out: list[str] = []
    for item in _object_list(values):
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def write_task_validation_artifacts(
    *,
    attempt_dir: Path,
    task_id: str,
    task_title: str,
    task_files: Sequence[object] | None,
    cycle: int,
    step: int,
    attempt: int,
    validations: Sequence[dict[str, Any]] | None,
    status: str,
    reason: str,
    detail: str = "",
    task_status: str = "",
    goal_ref: str = "",
    goal_text: str = "",
    goal_trace: Sequence[object] | None = None,
    active_goal_context: dict[str, Any] | None = None,
) -> Path:
    """Persist per-attempt validation artifacts shared across execution backends."""

    artifact_path = attempt_dir / "validation.json"
    records = [record for record in (validations or []) if isinstance(record, dict)]
    validation_status = str(status or "").strip() or "unknown"
    validation_reason = str(reason or "").strip()
    validation_detail = str(detail or "").strip()
    outcome_status = str(task_status or "").strip()
    if not outcome_status:
        outcome_status = (
            TASK_STATUS_COMPLETED
            if validation_status in {"passed", "validation_passed", TASK_STATUS_COMPLETED}
            else classify_task_failure(
                validation_reason,
                validations=records,
                detail=validation_detail,
            )
        )

    fast_validation = next(
        (record for record in records if str(record.get("gate") or "") == "fast_web_worktree_regression"),
        {},
    )
    selected_fast_suite = list(fast_validation.get("suite_files") or fast_validation.get("suiteFiles") or [])
    trigger_files = list(fast_validation.get("trigger_files") or fast_validation.get("triggerFiles") or [])
    compile_validation = next((record for record in records if str(record.get("kind") or "") == "compile"), {})
    test_validation = next((record for record in records if str(record.get("kind") or "") == "test"), {})
    normalized_goal_trace = _object_list(goal_trace)
    normalized_task_files = _string_list(task_files)
    active_goal_payload = dict(active_goal_context or {})

    payload = {
        "schema_version": 1,
        "artifact_path": artifact_path.as_posix(),
        "task_id": str(task_id or ""),
        "task_title": str(task_title or ""),
        "task_files": normalized_task_files,
        "goal_ref": str(goal_ref or ""),
        "goal_text": str(goal_text or ""),
        "goal_trace": normalized_goal_trace,
        "active_goal_context": active_goal_payload,
        "activeGoalContext": active_goal_payload,
        "cycle": int(cycle),
        "step": int(step),
        "attempt": int(attempt),
        "status": validation_status,
        "task_status": outcome_status,
        "taskStatus": outcome_status,
        "outcome_status": outcome_status,
        "outcomeStatus": outcome_status,
        "review_required": is_manual_review_required(outcome_status),
        "reviewRequired": is_manual_review_required(outcome_status),
        "auto_merge_allowed": outcome_status == TASK_STATUS_COMPLETED,
        "autoMergeAllowed": outcome_status == TASK_STATUS_COMPLETED,
        "reason": validation_reason,
        "detail": validation_detail,
        "validation_status": validation_status,
        "validationStatus": validation_status,
        "validation_reason": validation_reason,
        "validationReason": validation_reason,
        "validation_detail": validation_detail,
        "validationDetail": validation_detail,
        "compile_validation": compile_validation,
        "compileValidation": compile_validation,
        "test_validation": test_validation,
        "testValidation": test_validation,
        "fast_regression_validation": fast_validation,
        "fastRegressionValidation": fast_validation,
        "selected_fast_regression_suite": selected_fast_suite,
        "selectedFastRegressionSuite": selected_fast_suite,
        "trigger_files": trigger_files,
        "triggerFiles": trigger_files,
        "validations": records,
        "failure_summary": validation_detail if validation_status != "passed" else "",
        "failureSummary": validation_detail if validation_status != "passed" else "",
    }
    try:
        safe_write_text(artifact_path, json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    except Exception:
        pass

    summary_lines = [
        f"validation_status={validation_status}",
        f"task_status={outcome_status}",
        f"reason={validation_reason or 'ok'}",
    ]
    if selected_fast_suite:
        summary_lines.append("fast_suite=" + ", ".join(selected_fast_suite))
    if trigger_files:
        summary_lines.append("trigger_files=" + ", ".join(trigger_files))
    for record in records:
        record_name = str(record.get("name") or record.get("gate") or record.get("kind") or "validation")
        record_rc = record.get("rc")
        record_artifact = str(
            record.get("artifact_path")
            or record.get("artifactPath")
            or record.get("log_path")
            or record.get("logPath")
            or ""
        )
        summary_lines.append(f"{record_name}: rc={record_rc} artifact={record_artifact}")
        record_failure = str(record.get("failure_summary") or record.get("failureSummary") or "").strip()
        if record_failure and record_failure != validation_detail:
            summary_lines.append(f"{record_name}_failure={record_failure}")
    if validation_detail:
        summary_lines.append(f"failure_summary={validation_detail}")
    try:
        safe_write_text(attempt_dir / "validation.txt", "\n".join(summary_lines).rstrip() + "\n")
    except Exception:
        pass
    return artifact_path
