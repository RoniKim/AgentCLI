from __future__ import annotations

import re
from typing import Any, Sequence


TASK_STATUS_COMPLETED = "completed"
TASK_STATUS_REVIEW_REQUIRED = "review_required"
TASK_STATUS_BLOCKED_ENV = "blocked_env"
TASK_STATUS_TEST_CONTRACT_CHANGED = "test_contract_changed"
TASK_STATUS_REGRESSION_FAILED = "regression_failed"

TASK_STATUS_VALUES = {
    TASK_STATUS_COMPLETED,
    TASK_STATUS_REVIEW_REQUIRED,
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
    TASK_STATUS_REGRESSION_FAILED,
}

MANUAL_REVIEW_TASK_STATUSES = {
    TASK_STATUS_REVIEW_REQUIRED,
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
    TASK_STATUS_REGRESSION_FAILED,
}

AUTO_MERGE_TASK_STATUSES = {TASK_STATUS_COMPLETED}
AUTO_RETRY_TASK_STATUSES = {TASK_STATUS_REGRESSION_FAILED}

_ENV_PATTERNS = (
    r"\bmodule\s+not\s+found\b",
    r"\bno\s+module\s+named\b",
    r"\bcommand\s+not\s+found\b",
    r"\bcommandnotfoundexception\b",
    r"\bnot\s+recognized\s+as\s+(?:an?\s+)?(?:internal|external|cmdlet|function|script|program)",
    r"\bexecutable\s+does(?:\s+not|n't)\s+exist\b",
    r"\bbrowser(?:s)?\s+(?:has|have)\s+not\s+been\s+installed\b",
    r"\bplease\s+run\b.*\binstall\b",
    r"\bpermission\s+denied\b",
    r"\baccess\s+is\s+denied\b",
    r"\bwinerror\s+5\b",
    r"\bwinerror\s+10048\b",
    r"\baddress\s+already\s+in\s+use\b",
    r"\bno\s+such\s+file\s+or\s+directory\b",
    r"\bfile\s+not\s+found\b",
    r"\bpath\s+too\s+long\b",
    r"\bmax_path\b",
    r"\bfatal:\s*['\"]?\$git_dir['\"]?\s+too\s+big\b",
)

_TEST_CONTRACT_PATTERNS = (
    r"\bplaywright\b",
    r"\bselenium\b",
    r"\bpuppeteer\b",
    r"\blocator\b",
    r"\bto_(?:be|have|contain|match|equal)\b",
    r"\bstrict\s+mode\s+violation\b",
    r"\baccessible\s+name\b",
    r"\baria[-_\s]?label\b",
    r"\bsnapshot\s+(?:mismatch|failed|does\s+not\s+match)\b",
    r"\bgolden\s+(?:file|snapshot|image)\b",
    r"\bvisual\s+regression\b",
)

_STRONG_REGRESSION_PATTERNS = (
    r"\bsyntaxerror\b",
    r"\bindentationerror\b",
    r"\btypeerror\b",
    r"\bnameerror\b",
    r"\breferenceerror\b",
    r"\battributeerror\b",
    r"\bnullreferenceexception\b",
    r"\bobjectdisposedexception\b",
    r"\bcs\d{4}\b",
    r"\bcompilation\s+failed\b",
    r"\btraceback\s+\(most\s+recent\s+call\s+last\)",
)


def _matches_any(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL) for pattern in patterns)


def _validation_text(validations: Sequence[dict[str, Any]] | None, detail: str = "") -> str:
    parts: list[str] = [str(detail or "")]
    for validation in validations or []:
        if not isinstance(validation, dict):
            continue
        for key in (
            "name",
            "kind",
            "gate",
            "cmd",
            "summary",
            "failure_summary",
            "failureSummary",
            "log_path",
            "artifact_path",
        ):
            value = validation.get(key)
            if value:
                parts.append(str(value))
        failed_command = validation.get("failed_command")
        if isinstance(failed_command, dict):
            parts.extend(str(value) for value in failed_command.values() if value)
    return "\n".join(parts).lower()


def classify_task_failure(
    reason: str,
    *,
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
) -> str:
    """Classify a non-completed task into an operator-facing status.

    The classifier is intentionally toolchain-agnostic. It looks at the gate
    reason plus command/log text, so Python, C#, browser, and shell-based
    projects can share the same operator behavior.
    """
    normalized_reason = str(reason or "").strip().lower()
    if normalized_reason in {"", "ok", "done", "passed", "success", TASK_STATUS_COMPLETED}:
        return TASK_STATUS_COMPLETED

    text = _validation_text(validations, detail)
    if _matches_any(text, _ENV_PATTERNS):
        return TASK_STATUS_BLOCKED_ENV

    if normalized_reason == "build_failed":
        return TASK_STATUS_REGRESSION_FAILED

    if normalized_reason in {"policy_violation", "no_commits", "no_diff"}:
        return TASK_STATUS_REGRESSION_FAILED

    if normalized_reason in {"test_failed", "fast_regression_failed"}:
        if _matches_any(text, _TEST_CONTRACT_PATTERNS):
            return TASK_STATUS_TEST_CONTRACT_CHANGED
        if _matches_any(text, _STRONG_REGRESSION_PATTERNS):
            return TASK_STATUS_REGRESSION_FAILED
        if normalized_reason == "fast_regression_failed":
            return TASK_STATUS_REVIEW_REQUIRED
        return TASK_STATUS_REGRESSION_FAILED

    if normalized_reason in {"exhausted_attempts", "abandon_failed", "rollback_failed", "rollback_blocked"}:
        return TASK_STATUS_REVIEW_REQUIRED

    if _matches_any(text, _STRONG_REGRESSION_PATTERNS):
        return TASK_STATUS_REGRESSION_FAILED
    return TASK_STATUS_REVIEW_REQUIRED


def is_auto_merge_allowed(task_status: str) -> bool:
    return str(task_status or "").strip().lower() in AUTO_MERGE_TASK_STATUSES


def is_auto_retry_allowed(task_status: str) -> bool:
    return str(task_status or "").strip().lower() in AUTO_RETRY_TASK_STATUSES


def is_manual_review_required(task_status: str) -> bool:
    return str(task_status or "").strip().lower() in MANUAL_REVIEW_TASK_STATUSES
