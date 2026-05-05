from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import STAGE_EFFECTS_BACKLOG_MUTATION, Stage, StageOutcome


class BacklogRefinerStage(Stage):
    """Built-in PL stage that makes oversized PM tasks Dev-ready."""

    name = "PL"

    async def run(self, session: object, cycle_idx: int) -> StageOutcome:
        has_stop = getattr(session, "has_stop", None)
        if callable(has_stop) and has_stop():
            return StageOutcome.stop("stop_file")

        ensure_tasks_loaded = getattr(session, "ensure_tasks_loaded", None)
        if callable(ensure_tasks_loaded) and not ensure_tasks_loaded():
            return StageOutcome.fail("no_tasks_for_refinement", rc=1)

        tasks = list(getattr(session, "tasks", []) or [])
        if not tasks:
            return StageOutcome.fail("no_tasks_for_refinement", rc=1)

        input_task_count = len(tasks)
        # Lazy imports keep agent_runner.pipeline.session -> stages.base acyclic.
        from ...backlog_utils import normalize_backlog_tasks
        from ..shared_runtime import refine_backlog_tasks_for_pl

        result = refine_backlog_tasks_for_pl(tasks)
        self._record_result(session, cycle_idx, result, input_task_count)
        if not result.mutated:
            return StageOutcome.ok("backlog_refiner_noop")

        run_dir = Path(getattr(session, "run_dir", "."))
        normalized_tasks = normalize_backlog_tasks(result.tasks, run_dir)
        if not normalized_tasks:
            return StageOutcome.stop("no_tasks_after_refinement")

        write_backlog_tasks = getattr(session, "write_backlog_tasks", None)
        if not callable(write_backlog_tasks):
            return StageOutcome.fail("backlog_write_not_supported", rc=2)
        audit_path = write_backlog_tasks(normalized_tasks, source_stage=self.name, cycle_idx=cycle_idx)
        self._write_mutation_artifacts(session, cycle_idx, result, normalized_tasks, audit_path, input_task_count)
        return StageOutcome.ok("backlog_refined", effects=STAGE_EFFECTS_BACKLOG_MUTATION)

    def _record_result(self, session: object, cycle_idx: int, result: Any, input_task_count: int) -> None:
        data = getattr(session, "data", None)
        if isinstance(data, dict):
            data["pl_refinement"] = {
                "cycle": cycle_idx,
                "mutated": result.mutated,
                "input_task_count": input_task_count,
                "output_task_count": len(result.tasks),
                "decisions": list(result.decisions),
            }

    def _write_mutation_artifacts(
        self,
        session: object,
        cycle_idx: int,
        result: Any,
        normalized_tasks: list[dict[str, Any]],
        audit_path: Path,
        input_task_count: int,
    ) -> None:
        write_stage_artifact = getattr(session, "write_stage_artifact", None)
        if not callable(write_stage_artifact):
            return

        payload = {
            "cycle": cycle_idx,
            "decision": "split",
            "input_task_count": input_task_count,
            "output_task_count": len(normalized_tasks),
            "items": list(result.decisions),
            "backlog_write_artifact": audit_path.name,
        }
        write_stage_artifact(f"PL_OUTPUT_cycle_{cycle_idx:03d}.json", payload)
        write_stage_artifact(f"BACKLOG_REFINEMENT_cycle_{cycle_idx:03d}.json", payload)

        lines = ["# PL Backlog Refinement", ""]
        for item in result.decisions:
            children = ", ".join(str(child) for child in item.get("children", []) if str(child).strip())
            lines.append(f"- {item.get('task_id', '')}: split into {children}")
        write_stage_artifact("NOTES_PL.md", "\n".join(lines).rstrip() + "\n")
