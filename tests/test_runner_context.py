from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.runtime_contract import (
    AttemptContext,
    RunnerContext,
    RuntimeContextValidationError,
    TaskBranchState,
    TaskContext,
)


class RunnerContextTests(unittest.TestCase):
    def test_source_only_run_uses_source_repo_as_execution_repo(self) -> None:
        context = RunnerContext.from_paths(
            source_repo="/repo/source",
            run_dir="/repo/source/.AgentCLI/agent_runs/20260503-125328",
        )

        self.assertTrue(context.valid)
        self.assertEqual("/repo/source", context.source_repo)
        self.assertEqual("/repo/source", context.execution_repo)
        self.assertEqual("", context.execution_worktree)
        self.assertFalse(context.worktree_isolated)
        self.assertEqual("/repo/source/.AgentCLI/agent_runs/20260503-125328/tasks", context.tasks_dir)

    def test_worktree_isolated_run_exposes_distinct_execution_worktree(self) -> None:
        context = RunnerContext.from_paths(
            source_repo="/repo/source",
            run_dir="/repo/source/.AgentCLI/agent_runs/20260503-125328",
            execution_worktree="/tmp/agentcli-worktrees/source/20260503-125328",
        )

        self.assertEqual("/repo/source", context.source_repo)
        self.assertEqual("/tmp/agentcli-worktrees/source/20260503-125328", context.execution_repo)
        self.assertEqual("/tmp/agentcli-worktrees/source/20260503-125328", context.execution_worktree)
        self.assertTrue(context.worktree_isolated)

    def test_attempt_context_derives_task_and_attempt_directories(self) -> None:
        runner = RunnerContext.from_paths(
            source_repo="/repo/source",
            run_dir="/repo/source/.AgentCLI/agent_runs/20260503-125328",
        )

        task = runner.task(
            cycle=3,
            step=12,
            task_id="T30",
            task_title="Runner context objects",
        )
        attempt = task.attempt(2)

        self.assertIsInstance(task, TaskContext)
        self.assertIsInstance(attempt, AttemptContext)
        self.assertEqual(
            "/repo/source/.AgentCLI/agent_runs/20260503-125328/tasks/c003_s012_T30",
            task.task_dir,
        )
        self.assertEqual("attempt_02", attempt.attempt_dir_name)
        self.assertEqual(
            "/repo/source/.AgentCLI/agent_runs/20260503-125328/tasks/c003_s012_T30/attempt_02",
            attempt.attempt_dir,
        )
        self.assertEqual("T30", attempt.to_dict()["task_id"])

    def test_task_branch_metadata_serializes_base_and_head_state(self) -> None:
        class StubTaskBranch:
            branch_name = "task/T30_2026-05-03T12-53-28"
            base_branch = "HEAD"
            base_commit = "abc123def456"
            created_at = "2026-05-03T12:53:28Z"
            task_id = "T30"
            task_title = "Runner context objects"

        branch_state = TaskBranchState.from_task_branch(StubTaskBranch(), head_ref="fedcba654321")
        self.assertIsNotNone(branch_state)

        runner = RunnerContext.from_paths(
            source_repo="/repo/source",
            run_dir="/repo/source/.AgentCLI/agent_runs/20260503-125328",
        )
        task = runner.task(cycle=0, step=2, task_id="T30", task_branch=branch_state)
        payload = task.to_dict()

        self.assertEqual("abc123def456", branch_state.base_ref if branch_state is not None else "")
        self.assertEqual("task/T30_2026-05-03T12-53-28", payload["task_branch"]["branch_name"])
        self.assertEqual("abc123def456", payload["task_branch"]["base_ref"])
        self.assertEqual("fedcba654321", payload["task_branch"]["head_ref"])
        self.assertTrue(payload["task_branch"]["active"])

    def test_windows_style_paths_are_normalized(self) -> None:
        runner = RunnerContext.from_paths(
            source_repo=r"C:\Dev\AgentCLI",
            run_dir=r"C:\Dev\AgentCLI\.AgentCLI\agent_runs\20260503-125328\\",
            execution_worktree=r"C:\tmp\agentcli-worktrees\source\..\source\20260503-125328",
        )
        attempt = runner.task(cycle=1, step=4, task_id="T30").attempt(0)

        self.assertEqual("C:/Dev/AgentCLI", runner.source_repo)
        self.assertEqual("C:/Dev/AgentCLI/.AgentCLI/agent_runs/20260503-125328", runner.run_dir)
        self.assertEqual("C:/tmp/agentcli-worktrees/source/20260503-125328", runner.execution_worktree)
        self.assertEqual(
            "C:/Dev/AgentCLI/.AgentCLI/agent_runs/20260503-125328/tasks/c001_s004_T30/attempt_00",
            attempt.attempt_dir,
        )

    def test_runner_context_flags_and_rejects_missing_required_paths(self) -> None:
        flagged = RunnerContext.from_paths(
            source_repo="",
            run_dir="",
            strict=False,
        )

        self.assertFalse(flagged.valid)
        self.assertEqual(("source_repo", "run_dir"), flagged.missing_fields)

        with self.assertRaises(RuntimeContextValidationError) as ctx:
            RunnerContext.from_paths(source_repo=None, run_dir=None, strict=True)

        self.assertEqual(["source_repo", "run_dir"], ctx.exception.missing_fields)

    def test_build_failure_paths_match_existing_attempt_layout(self) -> None:
        run_dir = (Path.cwd() / ".tmp_runner_context").resolve()
        runner_context = RunnerContext(run_dir)
        task_context = runner_context.task_context(cycle=1, step=2, task_id="T31")
        attempt_context = task_context.attempt_context(0)

        self.assertEqual(run_dir / "tasks" / "c001_s002_T31", task_context.task_dir)
        self.assertEqual(task_context.task_dir / "attempt_00", attempt_context.attempt_dir)
        self.assertEqual(attempt_context.attempt_dir / "build.txt", attempt_context.build_log_path)
        self.assertEqual(attempt_context.attempt_dir / "validation.json", attempt_context.validation_json_path)

    def test_test_failure_paths_keep_validation_text_and_attempt_notes_names(self) -> None:
        run_dir = (Path.cwd() / ".tmp_runner_context").resolve()
        attempt_context = RunnerContext(run_dir).task_context(cycle=3, step=4, task_id="T31").attempt_context(2)

        self.assertEqual(attempt_context.attempt_dir / "test.txt", attempt_context.test_log_path)
        self.assertEqual(attempt_context.attempt_dir / "validation.txt", attempt_context.validation_txt_path)
        self.assertEqual(attempt_context.attempt_dir / "NOTES.md", attempt_context.notes_path)
        self.assertEqual(attempt_context.attempt_dir / "DEPENDENCY_REQUIRED.md", attempt_context.dependency_required_path)

    def test_fast_regression_paths_preserve_existing_filenames(self) -> None:
        run_dir = (Path.cwd() / ".tmp_runner_context").resolve()
        runner_context = RunnerContext(run_dir)
        attempt_context = runner_context.task_context(cycle=5, step=6, task_id="T31").attempt_context(1)

        self.assertEqual(
            attempt_context.attempt_dir / "fast_web_worktree_regression.json",
            attempt_context.fast_web_worktree_regression_path,
        )
        self.assertEqual(attempt_context.attempt_dir / "dev_output.txt", attempt_context.dev_output_path)
        self.assertEqual(run_dir / "DEPENDENCY_REQUIRED.md", runner_context.dependency_required_path)


if __name__ == "__main__":
    unittest.main()
