from __future__ import annotations

import ntpath
import posixpath
import re
from dataclasses import dataclass, field
from os import PathLike
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any


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

    source_repo: str
    run_dir: str
    execution_worktree: str = ""
    execution_repo: str = field(init=False)
    worktree_isolated: bool = field(init=False)
    run_id: str = field(init=False)
    tasks_dir: str = field(init=False)
    missing_fields: tuple[str, ...] = field(init=False)
    valid: bool = field(init=False)

    def __post_init__(self) -> None:
        source_repo = _normalize_path(self.source_repo)
        run_dir = _normalize_path(self.run_dir)
        execution_worktree = _normalize_path(self.execution_worktree)
        execution_repo = execution_worktree or source_repo
        missing_fields = tuple(
            _missing_required_fields(
                {
                    "source_repo": source_repo,
                    "run_dir": run_dir,
                }
            )
        )
        object.__setattr__(self, "source_repo", source_repo)
        object.__setattr__(self, "run_dir", run_dir)
        object.__setattr__(self, "execution_worktree", execution_worktree)
        object.__setattr__(self, "execution_repo", execution_repo)
        object.__setattr__(self, "worktree_isolated", bool(execution_worktree and execution_repo != source_repo))
        object.__setattr__(self, "run_id", _path_name(run_dir))
        object.__setattr__(self, "tasks_dir", _join_path(run_dir, "tasks"))
        object.__setattr__(self, "missing_fields", missing_fields)
        object.__setattr__(self, "valid", not missing_fields)

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_repo": self.source_repo,
            "execution_repo": self.execution_repo,
            "execution_worktree": self.execution_worktree,
            "worktree_isolated": self.worktree_isolated,
            "run_dir": self.run_dir,
            "run_id": self.run_id,
            "tasks_dir": self.tasks_dir,
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
    task_dir: str = field(init=False)
    missing_fields: tuple[str, ...] = field(init=False)
    valid: bool = field(init=False)

    def __post_init__(self) -> None:
        task_id = str(self.task_id or "").strip()
        task_key = f"c{int(self.cycle):03d}_s{int(self.step):03d}_{task_id}" if task_id else ""
        task_dir = _join_path(self.runner.tasks_dir, task_key)
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

    def to_dict(self) -> dict[str, Any]:
        payload = self.runner.to_dict()
        payload.update(
            {
                "cycle": self.cycle,
                "step": self.step,
                "task_id": self.task_id,
                "task_title": self.task_title,
                "task_key": self.task_key,
                "task_dir": self.task_dir,
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
    attempt_dir: str = field(init=False)
    missing_fields: tuple[str, ...] = field(init=False)
    valid: bool = field(init=False)

    def __post_init__(self) -> None:
        attempt_dir_name = f"attempt_{int(self.attempt):02d}"
        attempt_dir = _join_path(self.task.task_dir, attempt_dir_name)
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

    def to_dict(self) -> dict[str, Any]:
        payload = self.task.to_dict()
        payload.update(
            {
                "attempt": self.attempt,
                "attempt_dir_name": self.attempt_dir_name,
                "attempt_dir": self.attempt_dir,
                "missing_fields": list(self.missing_fields),
                "valid": self.valid,
            }
        )
        return payload


def default_role_string() -> str:
    return ",".join(DEFAULT_ROLE_SPECS)


def enterprise_role_string() -> str:
    return ",".join(ENTERPRISE_ROLE_SPECS)
