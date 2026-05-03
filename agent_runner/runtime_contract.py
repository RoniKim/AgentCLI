from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _normalize_context_root(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _normalize_leaf_name(filename: str) -> str:
    text = str(filename or "").strip()
    if not text:
        raise ValueError("Artifact filename must not be empty.")
    if Path(text).name != text or text in {".", ".."}:
        raise ValueError(f"Artifact filename must be a leaf name: {filename!r}")
    return text


def _normalize_child_path(parent: Path, child: str | Path) -> Path:
    root = _normalize_context_root(parent)
    candidate = (root / Path(child)).resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Context path {child!r} escapes root {root}.") from exc
    return candidate


@dataclass(frozen=True)
class RunnerContext:
    run_dir: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_dir", _normalize_context_root(self.run_dir))

    @property
    def tasks_dir(self) -> Path:
        return self.run_dir / "tasks"

    def artifact_path(self, filename: str) -> Path:
        return _normalize_child_path(self.run_dir, _normalize_leaf_name(filename))

    def task_context(self, *, cycle: int, step: int, task_id: str) -> TaskContext:
        return TaskContext(runner=self, cycle=cycle, step=step, task_id=task_id)

    @property
    def dependency_required_path(self) -> Path:
        return self.artifact_path("DEPENDENCY_REQUIRED.md")

    @property
    def notes_path(self) -> Path:
        return self.artifact_path("NOTES.md")


@dataclass(frozen=True)
class TaskContext:
    runner: RunnerContext
    cycle: int
    step: int
    task_id: str

    @property
    def task_dir_name(self) -> str:
        return f"c{self.cycle:03d}_s{self.step:03d}_{self.task_id}"

    @property
    def task_dir(self) -> Path:
        return _normalize_child_path(self.runner.tasks_dir, self.task_dir_name)

    def attempt_context(self, attempt: int) -> AttemptContext:
        return AttemptContext(task=self, attempt=attempt)


@dataclass(frozen=True)
class AttemptContext:
    task: TaskContext
    attempt: int

    @property
    def run_dir(self) -> Path:
        return self.task.runner.run_dir

    @property
    def task_dir(self) -> Path:
        return self.task.task_dir

    @property
    def attempt_dir_name(self) -> str:
        return f"attempt_{self.attempt:02d}"

    @property
    def attempt_dir(self) -> Path:
        return _normalize_child_path(self.task_dir, self.attempt_dir_name)

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


def default_role_string() -> str:
    return ",".join(DEFAULT_ROLE_SPECS)


def enterprise_role_string() -> str:
    return ",".join(ENTERPRISE_ROLE_SPECS)
