from __future__ import annotations

import json
import ntpath
import posixpath
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from os import PathLike
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .failure_policy import FailureOutcome, build_failure_outcome, should_preserve_for_review


PIPELINE_STAGE_ORDER: tuple[str, ...] = ("PM", "PL", "Security", "Dev", "QA", "Reporter")
BUILTIN_ROLE_SPECS: tuple[str, ...] = tuple(stage for stage in PIPELINE_STAGE_ORDER if stage != "Reporter")
DEFAULT_ROLE_SPECS: tuple[str, ...] = ("PM", "Dev", "QA")
ENTERPRISE_ROLE_SPECS: tuple[str, ...] = ("PM", "Security", "Dev", "QA")
ENTERPRISE_BUDGET_FLOORS: dict[str, int] = {
    "max_total_escalations_per_run": 5,
    "max_total_continuations_per_run": 5,
    "max_total_repair_attempts_per_run": 3,
}
ATTEMPT_STARTED_MARKER = "STARTED"
ATTEMPT_FINISHED_MARKER = "FINISHED"

ROLE_SPEC_CANONICALS: dict[str, str] = {
    "pm": "PM",
    "pl": "PL",
    "backlog_refiner": "PL",
    "backlogrefiner": "PL",
    "security": "Security",
    "dev": "Dev",
    "qa": "QA",
}

PIPELINE_ROLE_HINT = "Built-in order: PM, PL, Security, Dev, QA. Plugin specs like pkg.mod:Class are preserved."

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

_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:([\\/]|$)")


class RuntimeContextValidationError(ValueError):
    """Raised when a runtime context is missing required inputs."""

    def __init__(self, context_name: str, missing_fields: list[str]) -> None:
        self.context_name = str(context_name or "runtime_context")
        self.missing_fields = [str(field).strip() for field in missing_fields if str(field).strip()]
        joined = ", ".join(self.missing_fields) or "unknown"
        super().__init__(f"{self.context_name} missing required fields: {joined}")


def _is_windows_style_path(value: str) -> bool:
    text = str(value or "").strip()
    return bool(text) and (
        "\\" in text
        or bool(_WINDOWS_DRIVE_RE.match(text))
        or text.startswith("//")
    )


def _normalize_path(value: str | PathLike[str] | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if _is_windows_style_path(text):
        normalized = ntpath.normpath(text.replace("/", "\\"))
        return PureWindowsPath(normalized).as_posix()
    normalized = posixpath.normpath(text.replace("\\", "/"))
    return PurePosixPath(normalized).as_posix()


def _join_path(base: str, *parts: object) -> str:
    base_text = _normalize_path(base)
    if not base_text:
        return ""
    if _is_windows_style_path(base_text):
        path = PureWindowsPath(base_text)
        for part in parts:
            path /= str(part)
        return path.as_posix()
    path = PurePosixPath(base_text)
    for part in parts:
        path /= str(part)
    return path.as_posix()


def _path_name(value: str) -> str:
    text = _normalize_path(value)
    if not text:
        return ""
    if _is_windows_style_path(text):
        return PureWindowsPath(text).name
    return PurePosixPath(text).name


def _normalize_context_root(path: str | PathLike[str]) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _normalize_leaf_name(filename: str) -> str:
    text = str(filename or "").strip()
    if not text:
        raise ValueError("Artifact filename must not be empty.")
    if Path(text).name != text or text in {".", ".."}:
        raise ValueError(f"Artifact filename must be a leaf name: {filename!r}")
    return text


def _normalize_child_path(parent: str | PathLike[str], child: str | PathLike[str]) -> Path:
    root = _normalize_context_root(parent)
    candidate = (root / Path(child)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Context path {child!r} escapes root {root}.") from exc
    return candidate


def _missing_required_fields(required_fields: dict[str, str]) -> list[str]:
    return [name for name, value in required_fields.items() if not str(value or "").strip()]


def _raise_for_missing_fields(context_name: str, missing_fields: list[str]) -> None:
    if missing_fields:
        raise RuntimeContextValidationError(context_name, missing_fields)


@dataclass(frozen=True, slots=True)
class TaskBranchState:
    """Serializable branch metadata carried by task and attempt contexts."""

    branch_name: str = ""
    base_branch: str = ""
    base_commit: str = ""
    head_ref: str = ""
    created_at: str = ""
    task_id: str = ""
    task_title: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "branch_name", str(self.branch_name or "").strip())
        object.__setattr__(self, "base_branch", str(self.base_branch or "").strip())
        object.__setattr__(self, "base_commit", str(self.base_commit or "").strip())
        object.__setattr__(self, "head_ref", str(self.head_ref or "").strip())
        object.__setattr__(self, "created_at", str(self.created_at or "").strip())
        object.__setattr__(self, "task_id", str(self.task_id or "").strip())
        object.__setattr__(self, "task_title", str(self.task_title or "").strip())

    @property
    def active(self) -> bool:
        return bool(self.branch_name)

    @property
    def base_ref(self) -> str:
        if self.base_branch and self.base_branch != "HEAD":
            return self.base_branch
        return self.base_commit

    @classmethod
    def from_task_branch(cls, task_branch: object | None, *, head_ref: str = "") -> TaskBranchState | None:
        if task_branch is None:
            return None
        return cls(
            branch_name=str(getattr(task_branch, "branch_name", "") or "").strip(),
            base_branch=str(getattr(task_branch, "base_branch", "") or "").strip(),
            base_commit=str(getattr(task_branch, "base_commit", "") or "").strip(),
            head_ref=str(head_ref or "").strip(),
            created_at=str(getattr(task_branch, "created_at", "") or "").strip(),
            task_id=str(getattr(task_branch, "task_id", "") or "").strip(),
            task_title=str(getattr(task_branch, "task_title", "") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "branch_name": self.branch_name,
            "base_branch": self.base_branch,
            "base_commit": self.base_commit,
            "base_ref": self.base_ref,
            "head_ref": self.head_ref,
            "created_at": self.created_at,
            "task_id": self.task_id,
            "task_title": self.task_title,
        }


@dataclass(frozen=True, slots=True)
class RunnerContext:
    """Normalized runtime paths for a single runner session."""

    source_repo: str | PathLike[str]
    run_dir: str | PathLike[str] | None = None
    execution_worktree: str | PathLike[str] | None = ""
    execution_repo: str = field(init=False)
    worktree_isolated: bool = field(init=False)
    run_id: str = field(init=False)
    tasks_dir: Any = field(init=False)
    missing_fields: tuple[str, ...] = field(init=False)
    valid: bool = field(init=False)
    _path_mode: bool = field(init=False, repr=False)

    def __post_init__(self) -> None:
        run_dir_missing = self.run_dir is None or not str(self.run_dir or "").strip()
        path_mode = run_dir_missing and bool(str(self.source_repo or "").strip())
        source_repo_input: str | PathLike[str] | None = "" if path_mode else self.source_repo
        run_dir_input: str | PathLike[str] | None = self.source_repo if path_mode else self.run_dir
        execution_worktree_input: str | PathLike[str] | None = "" if path_mode else self.execution_worktree

        source_repo = _normalize_path(source_repo_input)
        run_dir = _normalize_path(run_dir_input)
        execution_worktree = _normalize_path(execution_worktree_input)
        execution_repo = execution_worktree or source_repo
        required_fields = {"run_dir": run_dir} if path_mode else {"source_repo": source_repo, "run_dir": run_dir}
        missing_fields = tuple(_missing_required_fields(required_fields))
        run_dir_value: Any = _normalize_context_root(run_dir) if path_mode and run_dir else run_dir
        tasks_dir: Any = (run_dir_value / "tasks") if path_mode and run_dir else _join_path(run_dir, "tasks")
        object.__setattr__(self, "source_repo", source_repo)
        object.__setattr__(self, "run_dir", run_dir_value)
        object.__setattr__(self, "execution_worktree", execution_worktree)
        object.__setattr__(self, "execution_repo", execution_repo)
        object.__setattr__(self, "worktree_isolated", bool(execution_worktree and execution_repo != source_repo))
        object.__setattr__(self, "run_id", _path_name(run_dir))
        object.__setattr__(self, "tasks_dir", tasks_dir)
        object.__setattr__(self, "missing_fields", missing_fields)
        object.__setattr__(self, "valid", not missing_fields)
        object.__setattr__(self, "_path_mode", path_mode)

    @classmethod
    def from_paths(
        cls,
        *,
        source_repo: str | PathLike[str] | None,
        run_dir: str | PathLike[str] | None,
        execution_worktree: str | PathLike[str] | None = None,
        strict: bool = True,
    ) -> RunnerContext:
        context = cls(
            source_repo=str(source_repo or ""),
            run_dir=str(run_dir or ""),
            execution_worktree=str(execution_worktree or ""),
        )
        if strict:
            _raise_for_missing_fields("RunnerContext", list(context.missing_fields))
        return context

    def task(
        self,
        *,
        cycle: int,
        step: int,
        task_id: str,
        task_title: str = "",
        task_branch: TaskBranchState | None = None,
        strict: bool = True,
    ) -> TaskContext:
        return TaskContext.from_runner(
            self,
            cycle=cycle,
            step=step,
            task_id=task_id,
            task_title=task_title,
            task_branch=task_branch,
            strict=strict,
        )

    def task_context(self, *, cycle: int, step: int, task_id: str) -> TaskContext:
        return self.task(cycle=cycle, step=step, task_id=task_id, strict=False)

    def artifact_path(self, filename: str) -> Path:
        return _normalize_child_path(self.run_dir, _normalize_leaf_name(filename))

    @property
    def dependency_required_path(self) -> Path:
        return self.artifact_path("DEPENDENCY_REQUIRED.md")

    @property
    def notes_path(self) -> Path:
        return self.artifact_path("NOTES.md")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_repo": self.source_repo,
            "execution_repo": self.execution_repo,
            "execution_worktree": self.execution_worktree,
            "worktree_isolated": self.worktree_isolated,
            "run_dir": self.run_dir.as_posix() if isinstance(self.run_dir, Path) else self.run_dir,
            "run_id": self.run_id,
            "tasks_dir": self.tasks_dir.as_posix() if isinstance(self.tasks_dir, Path) else self.tasks_dir,
            "missing_fields": list(self.missing_fields),
            "valid": self.valid,
        }


@dataclass(frozen=True, slots=True)
class TaskContext:
    """Derived task-level artifact context for a runner session."""

    runner: RunnerContext
    cycle: int
    step: int
    task_id: str
    task_title: str = ""
    task_branch: TaskBranchState | None = None
    task_key: str = field(init=False)
    task_dir: Any = field(init=False)
    missing_fields: tuple[str, ...] = field(init=False)
    valid: bool = field(init=False)

    def __post_init__(self) -> None:
        task_id = str(self.task_id or "").strip()
        task_key = f"c{int(self.cycle):03d}_s{int(self.step):03d}_{task_id}" if task_id else ""
        if getattr(self.runner, "_path_mode", False):
            task_dir = _normalize_child_path(self.runner.tasks_dir, task_key) if task_key else _normalize_context_root(self.runner.tasks_dir)
        else:
            task_dir = _join_path(str(self.runner.tasks_dir), task_key)
        missing_fields = tuple(
            list(self.runner.missing_fields)
            + _missing_required_fields({"task_id": task_id})
        )
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "task_title", str(self.task_title or "").strip())
        object.__setattr__(self, "task_key", task_key)
        object.__setattr__(self, "task_dir", task_dir)
        object.__setattr__(self, "missing_fields", missing_fields)
        object.__setattr__(self, "valid", not missing_fields)

    @classmethod
    def from_runner(
        cls,
        runner: RunnerContext,
        *,
        cycle: int,
        step: int,
        task_id: str,
        task_title: str = "",
        task_branch: TaskBranchState | None = None,
        strict: bool = True,
    ) -> TaskContext:
        context = cls(
            runner=runner,
            cycle=int(cycle),
            step=int(step),
            task_id=str(task_id or ""),
            task_title=str(task_title or ""),
            task_branch=task_branch,
        )
        if strict:
            _raise_for_missing_fields("TaskContext", list(context.missing_fields))
        return context

    def attempt(self, attempt: int, *, strict: bool = True) -> AttemptContext:
        return AttemptContext.from_task(self, attempt=attempt, strict=strict)

    @property
    def task_dir_name(self) -> str:
        return self.task_key

    def attempt_context(self, attempt: int) -> AttemptContext:
        return self.attempt(attempt, strict=False)

    def to_dict(self) -> dict[str, Any]:
        payload = self.runner.to_dict()
        payload.update(
            {
                "cycle": self.cycle,
                "step": self.step,
                "task_id": self.task_id,
                "task_title": self.task_title,
                "task_key": self.task_key,
                "task_dir": self.task_dir.as_posix() if isinstance(self.task_dir, Path) else self.task_dir,
                "task_branch": self.task_branch.to_dict() if self.task_branch is not None else {},
                "missing_fields": list(self.missing_fields),
                "valid": self.valid,
            }
        )
        return payload


@dataclass(frozen=True, slots=True)
class AttemptContext:
    """Derived attempt-level artifact context for a task execution."""

    task: TaskContext
    attempt: int
    attempt_dir_name: str = field(init=False)
    attempt_dir: Any = field(init=False)
    missing_fields: tuple[str, ...] = field(init=False)
    valid: bool = field(init=False)

    def __post_init__(self) -> None:
        attempt_dir_name = f"attempt_{int(self.attempt):02d}"
        if getattr(self.task.runner, "_path_mode", False):
            attempt_dir = _normalize_child_path(self.task.task_dir, attempt_dir_name)
        else:
            attempt_dir = _join_path(str(self.task.task_dir), attempt_dir_name)
        object.__setattr__(self, "attempt_dir_name", attempt_dir_name)
        object.__setattr__(self, "attempt_dir", attempt_dir)
        object.__setattr__(self, "missing_fields", self.task.missing_fields)
        object.__setattr__(self, "valid", self.task.valid)

    @classmethod
    def from_task(
        cls,
        task: TaskContext,
        *,
        attempt: int,
        strict: bool = True,
    ) -> AttemptContext:
        context = cls(task=task, attempt=int(attempt))
        if strict:
            _raise_for_missing_fields("AttemptContext", list(context.missing_fields))
        return context

    @property
    def run_dir(self) -> Path:
        return _normalize_context_root(self.task.runner.run_dir)

    @property
    def task_dir(self) -> Path:
        return _normalize_context_root(self.task.task_dir)

    def artifact_path(self, filename: str) -> Path:
        return _normalize_child_path(self.attempt_dir, _normalize_leaf_name(filename))

    @property
    def build_log_path(self) -> Path:
        return self.artifact_path("build.txt")

    @property
    def test_log_path(self) -> Path:
        return self.artifact_path("test.txt")

    @property
    def fast_web_worktree_regression_path(self) -> Path:
        return self.artifact_path("fast_web_worktree_regression.json")

    @property
    def validation_json_path(self) -> Path:
        return self.artifact_path("validation.json")

    @property
    def validation_txt_path(self) -> Path:
        return self.artifact_path("validation.txt")

    @property
    def dev_output_path(self) -> Path:
        return self.artifact_path("dev_output.txt")

    @property
    def notes_path(self) -> Path:
        return self.artifact_path("NOTES.md")

    @property
    def dependency_required_path(self) -> Path:
        return self.artifact_path("DEPENDENCY_REQUIRED.md")

    @property
    def started_marker_path(self) -> Path:
        return self.artifact_path(ATTEMPT_STARTED_MARKER)

    @property
    def finished_marker_path(self) -> Path:
        return self.artifact_path(ATTEMPT_FINISHED_MARKER)

    def _write_marker(self, filename: str, payload: dict[str, Any]) -> Path:
        marker_path = self.artifact_path(filename)
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            errors="replace",
        )
        return marker_path

    def write_started_marker(
        self,
        *,
        started_at: str,
        run_context: dict[str, Any] | None = None,
    ) -> Path:
        try:
            self.finished_marker_path.unlink()
        except FileNotFoundError:
            pass
        except Exception:
            pass
        payload = {
            "marker": ATTEMPT_STARTED_MARKER,
            "task_id": self.task.task_id,
            "attempt": self.attempt,
            "timestamp": str(started_at or "").strip(),
            "run_context": dict(run_context) if isinstance(run_context, dict) else self.to_dict(),
        }
        return self._write_marker(ATTEMPT_STARTED_MARKER, payload)

    def write_finished_marker(
        self,
        *,
        finished_at: str,
        status: str,
        reason: str,
        detail: str = "",
        run_context: dict[str, Any] | None = None,
    ) -> Path:
        payload = {
            "marker": ATTEMPT_FINISHED_MARKER,
            "task_id": self.task.task_id,
            "attempt": self.attempt,
            "timestamp": str(finished_at or "").strip(),
            "status": str(status or "").strip(),
            "reason": str(reason or "").strip(),
            "detail": str(detail or "").strip(),
            "run_context": dict(run_context) if isinstance(run_context, dict) else self.to_dict(),
        }
        return self._write_marker(ATTEMPT_FINISHED_MARKER, payload)

    def to_dict(self) -> dict[str, Any]:
        payload = self.task.to_dict()
        payload.update(
            {
                "attempt": self.attempt,
                "attempt_dir_name": self.attempt_dir_name,
                "attempt_dir": self.attempt_dir.as_posix() if isinstance(self.attempt_dir, Path) else self.attempt_dir,
                "missing_fields": list(self.missing_fields),
                "valid": self.valid,
            }
        )
        return payload


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
    reason: str = "",
    *,
    failure_outcome: FailureOutcome | None = None,
    task_status: str = "",
    detail: str = "",
    has_task_branch: bool = False,
    has_checkpoint: bool = False,
    task_status_resolver: Callable[[str, str], str] | None = None,
) -> TaskBranchDispositionDecision:
    resolved_outcome = failure_outcome
    if resolved_outcome is None:
        outcome_status = str(task_status or "").strip()
        if not outcome_status:
            if task_status_resolver is None:
                raise ValueError("task_status_resolver is required when failure_outcome and task_status are empty.")
            outcome_status = task_status_resolver(reason, detail)
        resolved_outcome = build_failure_outcome(
            reason,
            task_status=outcome_status,
            detail=detail,
        )
    outcome_status = resolved_outcome.task_status
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
    reason: str = "",
    *,
    failure_outcome: FailureOutcome | None = None,
    task_status: str = "",
    detail: str = "",
    validation_artifact: str = "",
    has_task_branch: bool = False,
    has_checkpoint: bool = False,
    task_status_resolver: Callable[[str, str], str] | None = None,
    abandon_branch: Callable[[], str] | None = None,
    restore_checkpoint: Callable[[], str | None] | None = None,
    record_pending_review: Callable[..., None],
    persist_state: Callable[[], None],
    on_branch_success: Callable[[TaskBranchDispositionDecision, str], None] | None = None,
    on_abandon_failed: Callable[[str], None] | None = None,
    on_rollback_success: Callable[[TaskBranchDispositionDecision, str], None] | None = None,
    on_rollback_failed: Callable[[str, str], None] | None = None,
) -> TaskBranchDispositionDispatchResult:
    resolved_outcome = failure_outcome
    if resolved_outcome is None:
        outcome_status = str(task_status or "").strip()
        if not outcome_status:
            if task_status_resolver is None:
                raise ValueError("task_status_resolver is required when failure_outcome and task_status are empty.")
            outcome_status = task_status_resolver(reason, detail)
        resolved_outcome = build_failure_outcome(
            reason,
            task_status=outcome_status,
            detail=detail,
            validation_artifact=validation_artifact,
        )
    disposition = decide_task_branch_disposition(
        resolved_outcome.reason,
        failure_outcome=resolved_outcome,
        has_task_branch=has_task_branch,
        has_checkpoint=has_checkpoint,
    )
    if disposition.action == TASK_BRANCH_ACTION_ABANDON:
        if abandon_branch is None:
            raise ValueError("abandon_branch is required when a task branch is available.")
        try:
            branch_name = str(abandon_branch() or "")
            record_pending_review(
                resolved_outcome.reason,
                task_status=disposition.outcome_status,
                detail=resolved_outcome.detail,
                branch=branch_name,
                validation_artifact=resolved_outcome.validation_artifact,
            )
            persist_state()
            if on_branch_success is not None:
                on_branch_success(disposition, branch_name)
            return TaskBranchDispositionDispatchResult(ok=True, stop_reason="", disposition=disposition)
        except Exception as ex:
            if on_abandon_failed is not None:
                on_abandon_failed(str(ex))
            return TaskBranchDispositionDispatchResult(ok=False, stop_reason="abandon_failed", disposition=disposition)

    if disposition.action == TASK_BRANCH_ACTION_RECORD_PENDING:
        record_pending_review(
            resolved_outcome.reason,
            task_status=disposition.outcome_status,
            detail=resolved_outcome.detail,
            validation_artifact=resolved_outcome.validation_artifact,
        )
        persist_state()
        return TaskBranchDispositionDispatchResult(ok=True, stop_reason="", disposition=disposition)

    if restore_checkpoint is None:
        raise ValueError("restore_checkpoint is required when a checkpoint is available.")
    try:
        rescue_branch = str(restore_checkpoint() or "")
        if on_rollback_success is not None:
            on_rollback_success(disposition, rescue_branch)
        record_pending_review(
            resolved_outcome.reason,
            task_status=disposition.outcome_status,
            detail=resolved_outcome.detail,
            rescue_branch=rescue_branch,
            validation_artifact=resolved_outcome.validation_artifact,
        )
        persist_state()
        return TaskBranchDispositionDispatchResult(ok=True, stop_reason="", disposition=disposition)
    except Exception as ex:
        failure_detail = str(ex)
        fail_reason = "rollback_blocked" if "blocked" in failure_detail.lower() else "rollback_failed"
        if on_rollback_failed is not None:
            on_rollback_failed(fail_reason, failure_detail)
        return TaskBranchDispositionDispatchResult(ok=False, stop_reason=fail_reason, disposition=disposition)


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
        return WorktreeCleanupDispatchResult(attempted=False, ok=True, final_reason="")

    try:
        remove_worktree_fn(source_repo, worktree_dir)
        return WorktreeCleanupDispatchResult(attempted=True, ok=True, final_reason="")
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
