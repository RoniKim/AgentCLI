from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, List, Optional

from ..state import write_backlog_files
from ..utils import atomic_write_json, atomic_write_text
from .stages.base import STAGE_EFFECTS_BACKLOG_MUTATION, StageOutcome


EnsureBacklogFn = Callable[[], bool]
LoadTasksFn = Callable[[], List[Any]]
AsyncStageFn = Callable[[int], Awaitable[StageOutcome]]
_STAGE_NAME_SANITIZER = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass
class PipelineSession:
    """Per-cycle session passed into stages.

    This is deliberately backend-agnostic: stages call `pm_phase/dev_phase/qa_phase/security_phase`
    that the backend/session wires to concrete implementations.
    """

    args: argparse.Namespace
    repo: Path
    run_dir: Path
    stop_path: Path

    ensure_backlog: EnsureBacklogFn
    load_tasks: LoadTasksFn

    pm_phase: AsyncStageFn
    dev_phase: AsyncStageFn
    qa_phase: AsyncStageFn
    security_phase: Optional[AsyncStageFn] = None

    # shared mutable data
    data: dict[str, Any] = field(default_factory=dict)
    _pending_effects: set[str] = field(default_factory=set, init=False, repr=False)

    # populated by manager
    tasks: List[Any] = field(default_factory=list)
    ran_tasks: bool = False
    done_delta: int = 0

    def has_stop(self) -> bool:
        try:
            return self.stop_path.exists()
        except OSError:
            return False

    def ensure_tasks_loaded(self) -> bool:
        if self.tasks:
            return True
        if not self.ensure_backlog():
            return False
        self.tasks = self.load_tasks() or []
        return bool(self.tasks)

    def invalidate_tasks(self) -> None:
        self.tasks = []

    def reload_tasks(self) -> bool:
        self.invalidate_tasks()
        if not self.ensure_backlog():
            return False
        self.tasks = self.load_tasks() or []
        return bool(self.tasks)

    def _resolve_run_path(self, relative_path: str) -> Path:
        raw = str(relative_path or "").strip()
        if not raw:
            raise ValueError("Run artifact path must not be empty")
        candidate = Path(raw)
        run_root = self.run_dir.resolve(strict=False)
        resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (run_root / candidate).resolve(strict=False)
        try:
            resolved.relative_to(run_root)
        except ValueError as ex:
            raise ValueError(f"Path escapes run_dir: {relative_path}") from ex
        if resolved == run_root:
            raise ValueError(f"Run artifact path must point to a file: {relative_path}")
        return resolved

    def record_stage_effects(self, effects: Iterable[str]) -> frozenset[str]:
        normalized = StageOutcome.ok(effects=effects).effects
        self._pending_effects.update(normalized)
        return normalized

    def consume_stage_effects(self) -> frozenset[str]:
        effects = frozenset(self._pending_effects)
        self._pending_effects.clear()
        return effects

    def pending_stage_effects(self) -> frozenset[str]:
        return frozenset(self._pending_effects)

    def write_stage_artifact(self, relative_path: str, payload: dict[str, Any] | str) -> Path:
        artifact_path = self._resolve_run_path(relative_path)
        if isinstance(payload, str):
            atomic_write_text(artifact_path, payload)
        else:
            atomic_write_json(artifact_path, payload)
        return artifact_path

    def write_backlog_tasks(self, tasks: List[dict[str, Any]], *, source_stage: str, cycle_idx: int) -> Path:
        if not tasks:
            raise ValueError("Backlog write rejected empty task list; return StageOutcome.stop('no_tasks_after_refinement') instead.")
        if any(not isinstance(task, dict) for task in tasks):
            raise TypeError("Backlog tasks must be dictionaries")
        backlog_json_path, backlog_md_path = write_backlog_files(self.run_dir, tasks)
        self.invalidate_tasks()
        effects = self.record_stage_effects(STAGE_EFFECTS_BACKLOG_MUTATION)
        stage_name = _STAGE_NAME_SANITIZER.sub("_", str(source_stage or "").strip()) or "stage"
        artifact_rel = Path("stage_artifacts") / stage_name / f"backlog_write_cycle_{cycle_idx:03d}.json"
        return self.write_stage_artifact(
            artifact_rel.as_posix(),
            {
                "stage": source_stage,
                "cycle": cycle_idx,
                "task_count": len(tasks),
                "backlog_json": backlog_json_path.relative_to(self.run_dir).as_posix(),
                "backlog_md": backlog_md_path.relative_to(self.run_dir).as_posix(),
                "effects": sorted(effects),
            },
        )

    async def security(self, cycle_idx: int) -> StageOutcome:
        if callable(self.security_phase):
            return await self.security_phase(cycle_idx)
        return StageOutcome.skip("security_not_supported")
