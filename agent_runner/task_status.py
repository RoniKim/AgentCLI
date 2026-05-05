from __future__ import annotations

import re
from typing import Any, Pattern, Sequence


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
    r"\bblocked_dependency\b",
    r"\bneeds_dependency\b",
    r"\bmissing\s+(?:dependency|tool|sdk|runtime|package)\b",
    r"\bdependency\s+(?:is\s+)?(?:missing|unavailable|not\s+installed)\b",
    r"\bmodule\s+not\s+found\b",
    r"\bno\s+module\s+named\b",
    r"\bimporterror\b",
    r"\bmodulenotfounderror\b",
    r"\bcommand\s+not\s+found\b",
    r"\bcommandnotfoundexception\b",
    r"\bnot\s+recognized\s+as\s+(?:an?\s+)?(?:internal|external|cmdlet|function|script|program)",
    r"\bexecutable\s+does(?:\s+not|n't)\s+exist\b",
    r"\bnode:\s+command\s+not\s+found\b",
    r"\bnpm\s+err!\s+missing\s+script\b",
    r"\bpnpm:\s+command\s+not\s+found\b",
    r"\byarn:\s+command\s+not\s+found\b",
    r"\bgo:\s+command\s+not\s+found\b",
    r"\bcargo:\s+command\s+not\s+found\b",
    r"\brustc:\s+command\s+not\s+found\b",
    r"\bjavac:\s+command\s+not\s+found\b",
    r"\bmvn:\s+command\s+not\s+found\b",
    r"\bgradle:\s+command\s+not\s+found\b",
    r"\bcmake:\s+command\s+not\s+found\b",
    r"\bmsbuild(?:\.exe)?:\s+command\s+not\s+found\b",
    r"\bdotnet:\s+command\s+not\s+found\b",
    r"\bnuget:\s+command\s+not\s+found\b",
    r"\bswift:\s+command\s+not\s+found\b",
    r"\bkotlinc:\s+command\s+not\s+found\b",
    r"\b(?:go|cargo|mvn|gradle|dotnet|nuget|cmake)\s+.*(?:not\s+found|is\s+not\s+recognized)\b",
    r"\b\[error\]\s+could\s+not\s+resolve\s+dependencies\b",
    r"\bcould\s+not\s+resolve\s+dependencies\s+for\b",
    r"\bcould\s+not\s+resolve\s+dependency\b",
    r"\bcould\s+not\s+resolve\s+all\s+(?:files|task\s+dependencies)\s+for\s+configuration\b",
    r"\bfailed\s+to\s+read\s+artifact\s+descriptor\b",
    r"\bfailed\s+to\s+collect\s+dependencies\b",
    r"\bcould\s+not\s+find\s+\S+:\S+:\S+",
    r"\bcould\s+not\s+download\s+\S+",
    r"\bnu1101\b",
    r"\bunable\s+to\s+find\s+package\b",
    r"\bpackage\s+\S+\s+is\s+not\s+found\s+in\s+the\s+following\s+(?:primary\s+)?source",
    r"\bfailed\s+to\s+download\s+\S+",
    r"\bdownload\s+of\s+\S+\s+failed\b",
    r"\bfailed\s+to\s+get\s+[`'\"]?\S+[`'\"]?\s+as\s+a\s+dependency\s+of\s+package\b",
    r"\bfailed\s+to\s+resolve\s+crate\b",
    r"\bcannot\s+find\s+module\b",
    r"\bgo:\s+module\s+not\s+found\b",
    r"\bgo:\s+\S+:\s+(?:no\s+such\s+host|no\s+matching\s+versions)\b",
    r"\bcould\s+not\s+find\s+cmake_(?:c|cxx)_compiler\b",
    r"\bno\s+cmake_(?:c|cxx)_compiler\s+could\s+be\s+found\b",
    r"\bcmake_make_program\s+is\s+not\s+set\b",
    r"\bthe\s+(?:c|cxx|c\+\+)\s+compiler\s+identification\s+is\s+unknown\b",
    r"\bpull\s+access\s+denied\b",
    r"\bmanifest\s+unknown\b",
    r"\brepository\s+does\s+not\s+exist\b",
    r"\bbrowser(?:s)?\s+(?:has|have)\s+not\s+been\s+installed\b",
    r"\bexecutable\s+doesn't\s+exist\s+at\b",
    r"\bplease\s+run\b.*\binstall\b",
    r"\binstall\s+playwright\b",
    r"\bplaywright\s+install\b",
    r"\bpermission\s+denied\b",
    r"\baccess\s+is\s+denied\b",
    r"\boperation\s+not\s+permitted\b",
    r"\beacces\b",
    r"\beperm\b",
    r"\bebusy\b",
    r"\bwinerror\s+5\b",
    r"\bwinerror\s+10048\b",
    r"\baddress\s+already\s+in\s+use\b",
    r"\beaddrinuse\b",
    r"\bno\s+such\s+file\s+or\s+directory\b",
    r"\bfile\s+not\s+found\b",
    r"\bthe\s+system\s+cannot\s+find\s+the\s+(?:file|path)\s+specified\b",
    r"\bpath\s+too\s+long\b",
    r"\bmax_path\b",
    r"\bfatal:\s*['\"]?\$git_dir['\"]?\s+too\s+big\b",
)

_TEST_CONTRACT_PATTERNS = (
    r"\b(?:playwright|selenium|puppeteer|cypress|webdriver)\b.*\b(?:locator|accessible\s+name|aria[-_\s]?label|snapshot|golden|visual\s+regression)\b",
    r"\blocator\b",
    r"\bdata-testid\b",
    r"\bto_(?:be|have|contain|match|equal)\b",
    r"\bexpect(?:ed)?\s+locator\b",
    r"\bexpected\s+.*\bto\s+(?:be|have|contain|match)\b",
    r"\bstrict\s+mode\s+violation\b",
    r"\baccessible\s+name\b",
    r"\baria[-_\s]?label\b",
    r"\brole\s+.*(?:not\s+found|mismatch)\b",
    r"\bsnapshot\s+(?:mismatch|failed|does\s+not\s+match)\b",
    r"\bgolden\s+(?:file|snapshot|image)\b",
    r"\bvisual\s+regression\b",
    r"\bscreenshot\s+(?:mismatch|diff|does\s+not\s+match)\b",
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
    r"\bpanic:\b",
    r"\bfatal\s+error:\b",
    r"\bsegmentation\s+fault\b",
    r"\bundefined:\s+\w+",
    r"\bcannot\s+find\s+symbol\b",
    r"\bcompilation\s+failure\b",
    r"\bbuild\s+failed\s+with\s+an\s+exception\b",
    r"\berror:\s+could\s+not\s+compile\b",
    r"\berror\s+e\d{4}\b",
    r"\berror\s+c\d{4}\b",
    r"\bclang:\s+error\b",
    r"\bg\+\+:\s+error\b",
    r"\bnullpointerexception\b",
    r"\bclasscastexception\b",
    r"\billegalstateexception\b",
    r"\bkotlinnullpointerexception\b",
    r"\bpackage\s+\S+\s+does\s+not\s+exist\b",
    r"\b';'\s+expected\b",
    r"\bruntime\s+error:\s+",
    r"\bgoroutine\s+\d+\s+\[",
    r"\bcannot\s+use\s+.*\s+as\s+.*\s+in\s+(?:argument|assignment|return)",
    r"\bmismatched\s+types\b",
    r"\bpanicked\s+at\s+",
    r"\b>\s*task\s+\S+\s+failed\b",
    r"\bfatal\s+exception\b",
    r"\bunhandled\s*promise\s*rejection\b",
    r"\bunhandledpromiserejection\b",
    r"\berror:\s+cannot\s+find\s+'?[A-Za-z_][\w.]*'?",
    r"\berror:\s+no\s+matching\s+function\b",
    r"\berror:\s+expected\s+';'",
    r"\berror:\s+expected\s+expression\b",
    r"\berror:\s+redefinition\s+of\b",
    r"\bundefined\s+reference\s+to\b",
    r"\blnk\d{4}\b",
    r"\bundefined\s+symbols?\s+for\s+architecture\b",
)

_MAX_VALIDATION_TEXT = 64_000
_COMPILED_ENV_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in _ENV_PATTERNS)
_COMPILED_TEST_CONTRACT_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in _TEST_CONTRACT_PATTERNS)
_COMPILED_STRONG_REGRESSION_PATTERNS = tuple(re.compile(pattern, re.IGNORECASE | re.DOTALL) for pattern in _STRONG_REGRESSION_PATTERNS)

def _matches_any(text: str, patterns: Sequence[Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def _validation_text(validations: Sequence[dict[str, Any]] | None, detail: str = "") -> str:
    parts: list[str] = []
    remaining = _MAX_VALIDATION_TEXT

    def add_part(value: Any) -> None:
        nonlocal remaining
        if remaining <= 0 or not value:
            return
        text = str(value)
        if not text:
            return
        if len(text) > remaining:
            text = text[:remaining]
        parts.append(text)
        remaining -= len(text) + 1

    add_part(detail)
    for validation in validations or []:
        if remaining <= 0:
            break
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
            add_part(value)
        failed_command = validation.get("failed_command")
        if isinstance(failed_command, dict):
            for value in failed_command.values():
                add_part(value)
    text = "\n".join(parts).lower()
    return text


def classify_task_failure(
    reason: str,
    *,
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    treat_empty_as_completed: bool = False,
) -> str:
    """Classify a non-completed task into an operator-facing status.

    The classifier is intentionally toolchain-agnostic. It looks at the gate
    reason plus command/log text, so Python, C#, browser, and shell-based
    projects can share the same operator behavior.
    """
    normalized_reason = str(reason or "").strip().lower()
    if treat_empty_as_completed and not normalized_reason:
        return TASK_STATUS_COMPLETED
    if normalized_reason in {"ok", "done", "passed", "success", TASK_STATUS_COMPLETED}:
        return TASK_STATUS_COMPLETED
    if not normalized_reason:
        return TASK_STATUS_REVIEW_REQUIRED

    if normalized_reason in {"needs_dependency", "blocked_dependency", "missing_dependency"}:
        return TASK_STATUS_BLOCKED_ENV

    text = _validation_text(validations, detail)
    if _matches_any(text, _COMPILED_ENV_PATTERNS):
        return TASK_STATUS_BLOCKED_ENV

    if normalized_reason == "build_failed":
        if _matches_any(text, _COMPILED_ENV_PATTERNS):
            return TASK_STATUS_BLOCKED_ENV
        return TASK_STATUS_REGRESSION_FAILED

    if normalized_reason in {"policy_violation", "no_commits", "no_diff"}:
        return TASK_STATUS_REGRESSION_FAILED

    if normalized_reason in {"test_failed", "fast_regression_failed"}:
        if _matches_any(text, _COMPILED_TEST_CONTRACT_PATTERNS):
            return TASK_STATUS_TEST_CONTRACT_CHANGED
        if _matches_any(text, _COMPILED_STRONG_REGRESSION_PATTERNS):
            return TASK_STATUS_REGRESSION_FAILED
        if normalized_reason == "fast_regression_failed":
            return TASK_STATUS_REVIEW_REQUIRED
        return TASK_STATUS_REGRESSION_FAILED

    if normalized_reason in {"exhausted_attempts", "abandon_failed", "rollback_failed", "rollback_blocked"}:
        return TASK_STATUS_REVIEW_REQUIRED

    if _matches_any(text, _COMPILED_STRONG_REGRESSION_PATTERNS):
        return TASK_STATUS_REGRESSION_FAILED
    return TASK_STATUS_REVIEW_REQUIRED


def is_auto_merge_allowed(task_status: str) -> bool:
    return str(task_status or "").strip().lower() in AUTO_MERGE_TASK_STATUSES


def is_auto_retry_allowed(task_status: str) -> bool:
    return str(task_status or "").strip().lower() in AUTO_RETRY_TASK_STATUSES


def is_manual_review_required(task_status: str) -> bool:
    return str(task_status or "").strip().lower() in MANUAL_REVIEW_TASK_STATUSES
