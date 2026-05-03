from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .failure_policy import should_preserve_for_review


PIPELINE_STAGE_ORDER: tuple[str, ...] = ("PM", "Security", "Dev", "QA", "Reporter")
BUILTIN_ROLE_SPECS: tuple[str, ...] = tuple(stage for stage in PIPELINE_STAGE_ORDER if stage != "Reporter")
DEFAULT_ROLE_SPECS: tuple[str, ...] = ("PM", "Dev", "QA")
ENTERPRISE_ROLE_SPECS: tuple[str, ...] = ("PM", "Security", "Dev", "QA")

ROLE_SPEC_CANONICALS: dict[str, str] = {
    "pm": "PM",
    "security": "Security",
    "dev": "Dev",
    "qa": "QA",
}

PIPELINE_ROLE_HINT = "Built-in order: PM, Security, Dev, QA. Plugin specs like pkg.mod:Class are preserved."

CODEX_DEV_MODEL_LADDER: tuple[str, str, str] = ("gpt-5.4-mini", "gpt-5.4", "gpt-5.5")
CODEX_MODEL_DEFAULTS: dict[str, str] = {
    "pm_model": "gpt-5.5",
    "dev_model": CODEX_DEV_MODEL_LADDER[0],
    "dev_model_tier1": CODEX_DEV_MODEL_LADDER[1],
    "dev_model_tier2": CODEX_DEV_MODEL_LADDER[2],
    "qa_model": "gpt-5.5",
    "reporter_model": "gpt-5.4-mini",
}

PIPELINE_ROLE_FIELD_SPEC: dict[str, Any] = {
    "path": "roles",
    "group": "project",
    "kind": "multienum",
    "label": "Pipeline roles",
    "options": BUILTIN_ROLE_SPECS,
    "allow_empty": False,
    "desc": "Stages enabled in the pipeline.",
    "hint": PIPELINE_ROLE_HINT,
}

CODEX_MODEL_FIELD_SPECS: tuple[dict[str, Any], ...] = (
    {
        "path": "pm_model",
        "group": "codex_models",
        "kind": "text",
        "label": "PM model",
        "allow_empty": False,
        "desc": "Model used for PM planning and backlog generation.",
        "hint": "Approved Codex default: gpt-5.5.",
    },
    {
        "path": "dev_model",
        "group": "codex_models",
        "kind": "text",
        "label": "Dev fallback model",
        "allow_empty": False,
        "desc": "First model in the Dev fallback ladder.",
        "hint": f"Approved ladder: {' -> '.join(CODEX_DEV_MODEL_LADDER)}.",
    },
    {
        "path": "dev_model_tier1",
        "group": "codex_models",
        "kind": "text",
        "label": "Dev fallback tier 1",
        "allow_empty": False,
        "desc": "Second model in the Dev fallback ladder.",
        "hint": "Escalates to gpt-5.4 when the base model is not enough.",
    },
    {
        "path": "dev_model_tier2",
        "group": "codex_models",
        "kind": "text",
        "label": "Dev fallback tier 2",
        "allow_empty": False,
        "desc": "Final model in the Dev fallback ladder.",
        "hint": "Escalates to gpt-5.5 as the last approved Codex tier.",
    },
    {
        "path": "qa_model",
        "group": "codex_models",
        "kind": "text",
        "label": "QA model",
        "allow_empty": False,
        "desc": "Model used for QA verification.",
        "hint": "Approved Codex default: gpt-5.5.",
    },
    {
        "path": "reporter_model",
        "group": "codex_models",
        "kind": "text",
        "label": "Reporter model",
        "allow_empty": False,
        "desc": "Model used for close-out reporting.",
        "hint": "Approved Codex default: gpt-5.4-mini.",
    },
)

TASK_BRANCH_ACTION_ABANDON = "abandon_branch"
TASK_BRANCH_ACTION_RECORD_PENDING = "record_pending_review"
TASK_BRANCH_ACTION_ROLLBACK = "restore_checkpoint"


@dataclass(frozen=True)
class TaskBranchDispositionDecision:
    outcome_status: str
    preserve_for_review: bool
    action: str
    event_name: str = ""


@dataclass(frozen=True)
class TaskBranchDispositionDispatchResult:
    ok: bool
    stop_reason: str
    disposition: TaskBranchDispositionDecision


@dataclass(frozen=True)
class WorktreeCleanupDispatchResult:
    attempted: bool
    ok: bool
    final_reason: str
    artifact_path: str = ""
    detail: str = ""


def decide_task_branch_disposition(
    reason: str,
    *,
    task_status: str = "",
    detail: str = "",
    has_task_branch: bool = False,
    has_checkpoint: bool = False,
    task_status_resolver: Callable[[str, str], str],
) -> TaskBranchDispositionDecision:
    outcome_status = str(task_status or "").strip() or task_status_resolver(reason, detail)
    preserve = should_preserve_for_review(outcome_status)
    if has_task_branch:
        return TaskBranchDispositionDecision(
            outcome_status=outcome_status,
            preserve_for_review=preserve,
            action=TASK_BRANCH_ACTION_ABANDON,
            event_name="task_branch_preserved" if preserve else "task_branch_abandoned",
        )
    if has_checkpoint:
        return TaskBranchDispositionDecision(
            outcome_status=outcome_status,
            preserve_for_review=preserve,
            action=TASK_BRANCH_ACTION_ROLLBACK,
        )
    return TaskBranchDispositionDecision(
        outcome_status=outcome_status,
        preserve_for_review=preserve,
        action=TASK_BRANCH_ACTION_RECORD_PENDING,
    )


def dispatch_task_branch_disposition(
    reason: str,
    *,
    task_status: str = "",
    detail: str = "",
    validation_artifact: str = "",
    has_task_branch: bool = False,
    has_checkpoint: bool = False,
    task_status_resolver: Callable[[str, str], str],
    abandon_branch: Callable[[], str] | None = None,
    restore_checkpoint: Callable[[], str | None] | None = None,
    record_pending_review: Callable[..., None],
    persist_state: Callable[[], None],
    on_branch_success: Callable[[TaskBranchDispositionDecision, str], None] | None = None,
    on_abandon_failed: Callable[[str], None] | None = None,
    on_rollback_success: Callable[[TaskBranchDispositionDecision, str], None] | None = None,
    on_rollback_failed: Callable[[str, str], None] | None = None,
) -> TaskBranchDispositionDispatchResult:
    disposition = decide_task_branch_disposition(
        reason,
        task_status=task_status,
        detail=detail,
        has_task_branch=has_task_branch,
        has_checkpoint=has_checkpoint,
        task_status_resolver=task_status_resolver,
    )
    if disposition.action == TASK_BRANCH_ACTION_ABANDON:
        if abandon_branch is None:
            raise ValueError("abandon_branch is required when a task branch is available.")
        try:
            branch_name = str(abandon_branch() or "")
            record_pending_review(
                reason,
                task_status=disposition.outcome_status,
                detail=detail,
                branch=branch_name,
                validation_artifact=validation_artifact,
            )
            persist_state()
            if on_branch_success is not None:
                on_branch_success(disposition, branch_name)
            return TaskBranchDispositionDispatchResult(
                ok=True,
                stop_reason="",
                disposition=disposition,
            )
        except Exception as ex:
            if on_abandon_failed is not None:
                on_abandon_failed(str(ex))
            return TaskBranchDispositionDispatchResult(
                ok=False,
                stop_reason="abandon_failed",
                disposition=disposition,
            )

    if disposition.action == TASK_BRANCH_ACTION_RECORD_PENDING:
        record_pending_review(
            reason,
            task_status=disposition.outcome_status,
            detail=detail,
            validation_artifact=validation_artifact,
        )
        persist_state()
        return TaskBranchDispositionDispatchResult(
            ok=True,
            stop_reason="",
            disposition=disposition,
        )

    if restore_checkpoint is None:
        raise ValueError("restore_checkpoint is required when a checkpoint is available.")
    try:
        rescue_branch = str(restore_checkpoint() or "")
        if on_rollback_success is not None:
            on_rollback_success(disposition, rescue_branch)
        record_pending_review(
            reason,
            task_status=disposition.outcome_status,
            detail=detail,
            rescue_branch=rescue_branch,
            validation_artifact=validation_artifact,
        )
        persist_state()
        return TaskBranchDispositionDispatchResult(
            ok=True,
            stop_reason="",
            disposition=disposition,
        )
    except Exception as ex:
        failure_detail = str(ex)
        fail_reason = "rollback_blocked" if "blocked" in failure_detail.lower() else "rollback_failed"
        if on_rollback_failed is not None:
            on_rollback_failed(fail_reason, failure_detail)
        return TaskBranchDispositionDispatchResult(
            ok=False,
            stop_reason=fail_reason,
            disposition=disposition,
        )


def dispatch_worktree_cleanup(
    *,
    source_repo: Path,
    worktree_dir: Path | None,
    run_dir: Path,
    should_remove: bool,
    remove_worktree_fn: Callable[[Path, Path], None],
    eprint_fn: Callable[[str], None] | None = None,
) -> WorktreeCleanupDispatchResult:
    if worktree_dir is None or not should_remove:
        return WorktreeCleanupDispatchResult(
            attempted=False,
            ok=True,
            final_reason="",
        )

    try:
        remove_worktree_fn(source_repo, worktree_dir)
        return WorktreeCleanupDispatchResult(
            attempted=True,
            ok=True,
            final_reason="",
        )
    except Exception as ex:
        detail = str(ex)
        if eprint_fn is not None:
            eprint_fn(f"[WARN] Failed to remove worktree: {detail}")
        artifact_path = run_dir / "WORKTREE_CLEANUP_FAILURE.md"
        try:
            artifact_path.write_text(
                "# Worktree cleanup failure\n\n"
                f"AgentCLI could not remove the isolated worktree:\n\n- `{worktree_dir}`\n\n"
                f"Error:\n\n```text\n{detail}\n```\n",
                encoding="utf-8",
            )
        except Exception:
            pass
        return WorktreeCleanupDispatchResult(
            attempted=True,
            ok=False,
            final_reason="worktree_cleanup_failed",
            artifact_path=artifact_path.as_posix(),
            detail=detail,
        )


def default_role_string() -> str:
    return ",".join(DEFAULT_ROLE_SPECS)


def enterprise_role_string() -> str:
    return ",".join(ENTERPRISE_ROLE_SPECS)
