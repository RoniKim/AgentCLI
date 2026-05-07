from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import shutil
import time
import unittest
import uuid

from agent_runner.experience import upsert_lessons
from agent_runner.main import main as runner_main
from agent_runner.backlog_utils import postprocess_pm_output_tasks, record_history
from agent_runner.pipeline.shared_runtime import SharedCycleDeps, run_shared_cycle_once
from agent_runner.preflight import check_active_goal_autonomy_readiness
from agent_runner.prompts import append_active_goal_context, append_pm_essential_context
from agent_runner.pr_queue import pr_packet_path, queue_review_packet
from agent_runner.reporting import collect_shutdown_context, build_local_shutdown_report, write_run_report_artifacts
from agent_runner.shell import RunnerShell
from agent_runner.state import write_backlog_files
from agent_runner.task_history import query_history
from agent_runner.validation_artifacts import write_task_validation_artifacts
from agent_runner.active_goal import (
    ActiveGoalConflict,
    ActiveGoalError,
    active_goal_events_path,
    active_goal_export_path,
    active_goal_goals_proposal_path,
    active_goal_path,
    active_goal_role_context,
    active_goal_role_context_from_task_snapshot,
    active_goal_mode_policy,
    active_goal_task_metadata,
    build_active_goal_analytics,
    build_active_goal_status,
    build_active_goal_timeline,
    cancel_active_goal,
    clear_active_goal,
    complete_active_goal_checkpoint,
    complete_active_goal,
    create_active_goal,
    export_active_goal_state,
    format_active_goal_block,
    increment_active_goal_usage,
    import_active_goal_state,
    list_active_goal_autonomy_presets,
    list_active_goal_templates,
    propose_goals_from_active_goal,
    recommend_next_active_goals,
    set_active_goal_checkpoints,
    update_active_goal,
    write_active_goal_export,
)
from agent_runner.web_redaction import _redact_web_active_goal_payload, _redact_web_history_payload


class ActiveGoalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-scratch" / f"active_goal_{uuid.uuid4().hex}"
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_create_writes_repo_local_runtime_artifact_and_status(self) -> None:
        status = create_active_goal(
            self.repo,
            "Implement active goal runtime",
            mode="exploratory",
            token_budget=123,
            cycle_budget=3,
            source={"kind": "operator", "surface": "shell"},
        )

        path = active_goal_path(self.repo)
        self.assertTrue(path.exists())
        self.assertEqual(path.as_posix(), status["path"])
        self.assertTrue(status["active"])
        self.assertEqual("active", status["state"])
        self.assertEqual("Implement active goal runtime", status["goal"]["objective"])
        self.assertEqual("exploratory", status["goal"]["mode"])
        self.assertEqual(123, status["goal"]["budgets"]["token_budget"])
        self.assertEqual(3, status["goal"]["budgets"]["cycle_budget"])
        self.assertEqual(0, status["progress"]["cycle"]["used"])
        self.assertEqual(3, status["progress"]["cycle"]["remaining"])
        self.assertIn("cycles=0/3", status["progress"]["summary"])
        self.assertTrue(status["activeGoalProgress"]["subordinateToGoalsMd"])
        self.assertTrue(status["pmInjection"]["doesNotOverrideGoals"])
        self.assertEqual("goals_first", status["pmInjection"]["priorityPolicy"])
        self.assertTrue(status["etag"])

    def test_create_rejects_replacing_active_goal_without_intent(self) -> None:
        first = create_active_goal(self.repo, "First goal")

        with self.assertRaises(ActiveGoalConflict):
            create_active_goal(self.repo, "Second goal")

        current = build_active_goal_status(self.repo)
        self.assertEqual(first["goal"]["id"], current["goal"]["id"])
        self.assertEqual("First goal", current["goal"]["objective"])

    def test_update_uses_stale_write_etag_and_increments_revision(self) -> None:
        status = create_active_goal(self.repo, "Original")
        etag = status["etag"]

        updated = update_active_goal(self.repo, objective="Updated", expected_etag=etag)

        self.assertEqual("Updated", updated["goal"]["objective"])
        self.assertEqual(2, updated["goal"]["revision"])
        with self.assertRaises(ActiveGoalConflict):
            update_active_goal(self.repo, objective="Stale", expected_etag=etag)

    def test_complete_cancel_and_clear_are_distinct_states(self) -> None:
        created = create_active_goal(self.repo, "Finish active goal core")
        with self.assertRaises(ActiveGoalError):
            complete_active_goal(self.repo, evidence="   ", expected_etag=created["etag"])

        completed = complete_active_goal(
            self.repo,
            evidence="Unit tests passed",
            expected_etag=created["etag"],
        )

        self.assertFalse(completed["active"])
        self.assertEqual("completed", completed["state"])
        self.assertEqual("completed", completed["goal"]["status"])
        self.assertEqual("Unit tests passed", completed["goal"]["completion_evidence"][0]["text"])
        self.assertEqual("operator_confirmation", completed["goal"]["completion_evidence"][0]["source"])
        self.assertTrue(completed["completionPolicy"]["doesNotMarkGoalsComplete"])
        self.assertTrue(completed["completionPolicy"]["doesNotApprovePrMerge"])

        canceled = create_active_goal(self.repo, "Temporary goal", replace=True)
        canceled = cancel_active_goal(self.repo, reason="No longer needed", expected_etag=canceled["etag"])
        self.assertEqual("canceled", canceled["state"])
        self.assertFalse(canceled["active"])
        self.assertEqual("cancel_reason", canceled["goal"]["completion_evidence"][0]["kind"])

        cleared = clear_active_goal(self.repo, expected_etag=canceled["etag"])
        self.assertEqual("missing", cleared["state"])
        self.assertFalse(active_goal_path(self.repo).exists())

    def test_complete_accepts_task_and_validation_evidence_sources(self) -> None:
        created = create_active_goal(self.repo, "Complete with structured evidence")

        completed = complete_active_goal(
            self.repo,
            evidence=[
                {"kind": "task_outcome", "text": "T1 done", "ref": "STATE.json"},
                {"kind": "validation_artifact", "text": "pytest passed", "ref": "tasks/T1/attempt_01/validation.json"},
            ],
            expected_etag=created["etag"],
        )

        sources = {item["source"] for item in completed["goal"]["completion_evidence"]}
        self.assertEqual({"task_outcome", "validation_artifact"}, sources)
        context = active_goal_role_context(completed, role="FinalReport")
        self.assertTrue(context["completionPolicy"]["doesNotCountAsMergeReadiness"])
        self.assertTrue(context["subordinate_to_goals_md"])

    def test_namespaced_status_reports_terminal_reasons_without_stop_priority_changes(self) -> None:
        missing = build_active_goal_status(self.repo)
        self.assertEqual("active_goal", missing["active_goal_status"]["namespace"])
        self.assertEqual("active_goal_missing_objective", missing["terminal_reason"])
        self.assertTrue(missing["active_goal_status"]["stop_priority_unchanged"])

        token_status = create_active_goal(self.repo, "Budgeted goal", token_budget=5, cycle_budget=2)
        token_status = update_active_goal(
            self.repo,
            usage_delta={"tokens_used": 5},
            expected_etag=token_status["etag"],
        )
        self.assertEqual("active_goal_token_budget_exhausted", token_status["terminal_reason"])
        self.assertTrue(token_status["active_goal_status"]["budget_status"]["token_budget_exhausted"])

        cycle_status = create_active_goal(self.repo, "Cycle budgeted goal", cycle_budget=1, replace=True)
        cycle_status = update_active_goal(
            self.repo,
            usage_delta={"cycles_used": 1},
            expected_etag=cycle_status["etag"],
        )
        self.assertEqual("active_goal_cycle_budget_exhausted", cycle_status["terminal_reason"])
        self.assertTrue(cycle_status["active_goal_status"]["budget_status"]["cycle_budget_exhausted"])

        time_status = create_active_goal(self.repo, "Timed goal", time_budget_seconds=10, replace=True)
        time_status = update_active_goal(
            self.repo,
            usage_delta={"time_used_seconds": 10},
            expected_etag=time_status["etag"],
        )
        self.assertEqual("active_goal_time_budget_expired", time_status["terminal_reason"])
        self.assertTrue(time_status["active_goal_status"]["budget_status"]["time_budget_expired"])
        self.assertTrue(time_status["active_goal_status"]["budget_status"]["budget_exhausted"])

        completed = complete_active_goal(self.repo, evidence="Done", expected_etag=time_status["etag"])
        self.assertEqual("active_goal_completed", completed["terminal_reason"])

        canceled = create_active_goal(self.repo, "Canceled goal", replace=True)
        canceled = cancel_active_goal(self.repo, reason="Stopped", expected_etag=canceled["etag"])
        self.assertEqual("active_goal_canceled", canceled["terminal_reason"])

    def test_usage_counters_persist_across_reads_and_keep_audit_trail(self) -> None:
        created = create_active_goal(self.repo, "Persist usage counters", token_budget=100, cycle_budget=5)

        increment_active_goal_usage(self.repo, tokens_used=12, time_used_seconds=3, cycles_used=1)
        after_restart_read = build_active_goal_status(self.repo)

        self.assertEqual(created["goal"]["id"], after_restart_read["goal"]["id"])
        self.assertEqual(12, after_restart_read["goal"]["usage"]["tokens_used"])
        self.assertEqual(3, after_restart_read["goal"]["usage"]["time_used_seconds"])
        self.assertEqual(1, after_restart_read["goal"]["usage"]["cycles_used"])
        self.assertEqual(4, after_restart_read["progress"]["cycle"]["remaining"])
        self.assertEqual(88, after_restart_read["progress"]["token"]["remaining"])
        self.assertIn("cycles=1/5", after_restart_read["progress"]["summary"])
        self.assertTrue(active_goal_events_path(self.repo).exists())
        events_text = active_goal_events_path(self.repo).read_text(encoding="utf-8")
        self.assertIn('"action": "create"', events_text)
        self.assertIn('"action": "update"', events_text)

    def test_prompt_block_is_subordinate_to_project_goals(self) -> None:
        status = create_active_goal(self.repo, "Refine PM planning", mode="adaptive")

        block = format_active_goal_block(status)

        self.assertIn("ACTIVE GOAL SOURCE", block)
        self.assertIn("Runtime operator intent only", block)
        self.assertIn("do not override GOALS.md", block)
        self.assertIn("mode_policy", block)
        self.assertIn("Mode never bypasses GOALS.md", block)
        self.assertIn("Refine PM planning", block)

        prompt = append_pm_essential_context("Plan.", active_goal_block=block)
        self.assertIn("<pm_active_goal>", prompt)
        self.assertIn("subordinate to GOALS.md", prompt)

    def test_execution_modes_expose_policy_without_gate_bypass(self) -> None:
        strict_policy = active_goal_mode_policy("strict")
        exploratory_policy = active_goal_mode_policy("exploratory")

        self.assertFalse(strict_policy["allow_bounded_discovery"])
        self.assertTrue(exploratory_policy["allow_bounded_discovery"])
        self.assertTrue(exploratory_policy["allowBroaderDiscovery"])
        self.assertFalse(exploratory_policy["can_bypass_gates"])
        self.assertIn("proposal-only GOALS updates", exploratory_policy["planning_guidance"])

        status = create_active_goal(self.repo, "Explore safer planning", mode="exploratory")
        block = format_active_goal_block(status)
        self.assertIn("bounded discovery", block)
        self.assertIn("Mode never bypasses GOALS.md", block)
        self.assertTrue(status["active_goal_status"]["mode_policy"]["allowBroaderDiscovery"])

        context = active_goal_role_context(status, role="PM")
        self.assertEqual("exploratory", context["mode_policy"]["mode"])
        self.assertFalse(context["mode_policy"]["can_bypass_gates"])

    def test_active_goal_context_helper_covers_role_prompts_and_artifacts(self) -> None:
        status = create_active_goal(self.repo, "Coordinate role-specific goal context", mode="strict")
        block = format_active_goal_block(status)

        for role, marker in (
            ("Dev", "<dev_active_goal>"),
            ("QA", "<qa_active_goal>"),
            ("Reporter", "<reporter_active_goal>"),
            ("PL", "<pl_active_goal>"),
            ("Analyzer", "<analyzer_active_goal>"),
        ):
            prompt = append_active_goal_context("Work.", active_goal_block=block, role=role)
            self.assertIn(marker, prompt)
            self.assertIn("subordinate to GOALS.md", prompt)
            self.assertIn("do not override GOALS.md", prompt)

            context = active_goal_role_context(status, role=role)
            self.assertTrue(context["active"])
            self.assertEqual(status["goal"]["id"], context["active_goal_id"])
            self.assertTrue(context["subordinate_to_goals_md"])
            self.assertEqual("goals_first", context["priority_policy"])

    def test_codex_and_claude_prompt_paths_cover_active_goal_roles(self) -> None:
        codex_source = (Path.cwd() / "agent_runner" / "cycle.py").read_text(encoding="utf-8", errors="replace")
        claude_source = (
            Path.cwd() / "agent_runner" / "backends" / "claudecode.py"
        ).read_text(encoding="utf-8", errors="replace")

        for source in (codex_source, claude_source):
            self.assertIn("append_active_goal_context", source)
            self.assertIn('role="Dev"', source)
            self.assertIn('role="QA"', source)
            self.assertIn('role="Reporter"', source)

    def test_runner_and_telegram_paths_preserve_active_goal_state(self) -> None:
        codex_source = (Path.cwd() / "agent_runner" / "cycle.py").read_text(encoding="utf-8", errors="replace")
        claude_source = (
            Path.cwd() / "agent_runner" / "backends" / "claudecode.py"
        ).read_text(encoding="utf-8", errors="replace")
        telegram_source = (
            Path.cwd() / "agent_runner" / "remote" / "telegram_service.py"
        ).read_text(encoding="utf-8", errors="replace")

        for source in (codex_source, claude_source):
            self.assertIn("increment_active_goal_usage", source)
            self.assertIn("tokens_used=_inp + _out", source)
            self.assertIn("cycles_used=1", source)
            self.assertIn("active_goal_repo=source_repo if worktree_dir is not None else repo", source)
        self.assertIn('CommandHandler("goal", self.cmd_goal)', telegram_source)
        self.assertIn("create_active_goal", telegram_source)
        self.assertIn("complete_active_goal", telegram_source)
        self.assertIn("_active_goal_summary_line", telegram_source)
        self.assertIn("progress:", telegram_source)
        shell_source = (Path.cwd() / "agent_runner" / "shell.py").read_text(encoding="utf-8", errors="replace")
        self.assertIn("active_goal_readiness", shell_source)
        self.assertIn("check_active_goal_autonomy_readiness", shell_source)

    def test_active_goal_readiness_warns_on_autonomy_conflicts(self) -> None:
        create_active_goal(self.repo, "Explore without autonomy bounds", mode="exploratory")

        readiness = check_active_goal_autonomy_readiness(
            self.repo,
            {
                "unattended": True,
                "loop": False,
                "goals_completion_level": "p0",
                "gitops": {"worktree_merge_mode": "auto"},
            },
        )

        codes = {item["code"] for item in readiness["warnings"]}
        self.assertEqual("warning", readiness["status"])
        self.assertIn("active_goal_exploratory_unbounded", codes)
        self.assertIn("active_goal_completion_level_narrows_unattended", codes)
        self.assertIn("active_goal_unattended_without_loop", codes)
        self.assertIn("active_goal_loop_unbounded", codes)
        self.assertIn("active_goal_auto_merge_conflict", codes)

    def test_shared_cycle_stops_on_active_goal_budget_for_backend_parity(self) -> None:
        create_active_goal(self.repo, "Stop when active goal budget is exhausted", token_budget=1)
        increment_active_goal_usage(self.repo, tokens_used=1)
        events: list[dict[str, object]] = []

        class Metrics:
            def event(self, event: str, **kwargs: object) -> None:
                events.append({"event": event, **kwargs})

        async def pm_stub(*_args: object, **_kwargs: object) -> bool:
            raise AssertionError("PM should not run after active-goal budget stop")

        async def dev_stub(*_args: object, **_kwargs: object) -> tuple[int, str, int, bool]:
            raise AssertionError("Dev should not run after active-goal budget stop")

        async def qa_stub(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise AssertionError("QA should not run after active-goal budget stop")

        deps = SharedCycleDeps(
            args=argparse.Namespace(pm_include_working_tree=False),
            repo=self.repo,
            active_goal_repo=self.repo,
            run_dir=self.root / "run-shared-cycle",
            stop_path=self.root / "run-shared-cycle" / "STOP",
            metrics=Metrics(),
            pipeline_mgr=object(),
            continuous=False,
            ensure_backlog=lambda: True,
            load_tasks=lambda: [],
            run_pm_if_needed=pm_stub,
            run_dev_loop=dev_stub,
            run_qa_if_needed=qa_stub,
            pm_stop_reason={},
            detect_stop_reason=lambda _paths: None,
            budget_state={},
            run_summary={},
            write_run_summary=lambda: None,
            snapshot_json=self.root / "run-shared-cycle" / "snapshot.json",
            get_prev_head=lambda: "",
            set_prev_head=lambda _value: None,
            get_policy_scan_summary=lambda: None,
            set_policy_scan_summary=lambda _value: None,
            get_security_scan_summary=lambda: None,
            set_security_scan_summary=lambda _value: None,
            policy_scan_enabled=False,
            policy_scan_scope="changed",
            security_enabled=False,
            security_scan_scope="changed",
            security_rules=[],
            security_fail_severity="high",
            security_end_include_totals=False,
            scan_ignore_paths=[],
            collect_scan=lambda _scope: ([], {}),
            security_scan_files_fn=lambda *_args, **_kwargs: {},
            severity_at_or_above_fn=lambda *_args: False,
            git_head_fn=lambda _repo: "HEAD",
            git_changed_files_fn=lambda *_args: [],
            git_worktree_changed_files_fn=lambda _repo: [],
            repo_fingerprint_fn=lambda _repo: "fp",
            eprint_fn=lambda _msg: None,
            stop_reason_quota="quota_exhausted",
            stop_reason_stop_file="stop_file",
            stop_reason_project_complete="project_complete",
            stop_reason_all_tasks_done="all_tasks_done",
            stop_reason_no_tasks="no_tasks",
        )

        result = asyncio.run(run_shared_cycle_once(0, deps))

        self.assertEqual(0, result.rc)
        self.assertEqual("active_goal_token_budget_exhausted", result.reason)
        self.assertEqual("active_goal_budget_stop", events[0]["event"])

    def test_pm_postprocess_attaches_active_goal_metadata_to_backlog_tasks(self) -> None:
        (self.repo / ".doc").mkdir()
        (self.repo / ".doc" / "GOALS.md").write_text(
            "# Project Goals\n\n## P0\n- [ ] Implement active goal runtime\n\n## P1\n- [ ] Later\n",
            encoding="utf-8",
        )
        status = create_active_goal(self.repo, "Operator wants active goal runtime", mode="adaptive")

        processed = postprocess_pm_output_tasks(
            repo=self.repo,
            run_dir=self.root / "run",
            cycle_idx=1,
            kind="bootstrap",
            raw_pm_output_path=self.root / "pm.txt",
            pm_output_model_dump={
                "kind": "bootstrap",
                "summary": "Plan",
                "tasks": [
                    {
                        "id": "T1",
                        "title": "Implement active goal runtime",
                        "prompt": "GOALS: Implement active goal runtime\n\nBuild the core runtime.",
                        "done_when": "The runtime exists.",
                    }
                ],
            },
            existing_tasks=[],
            done_ids=set(),
            failed_ids=set(),
            completion_level="p0",
            active_goal_status=status,
        )

        task = processed["backlog_tasks"][0]
        self.assertEqual(status["goal"]["id"], task["active_goal_id"])
        self.assertEqual(status["goal"]["id"], task["activeGoalId"])
        self.assertEqual("Operator wants active goal runtime", task["active_goal"]["objective"])
        self.assertEqual("Operator wants active goal runtime", task["activeGoal"]["objective"])

    def test_pm_postprocess_keeps_active_goal_decomposition_proposal_only_without_goals_checkbox(self) -> None:
        (self.repo / ".doc").mkdir()
        (self.repo / ".doc" / "GOALS.md").write_text(
            "# Project Goals\n\n## P0\n- [ ] Preserve GOALS-first project safety\n",
            encoding="utf-8",
        )
        status = create_active_goal(self.repo, "Investigate telemetry retention drift", mode="exploratory")

        processed = postprocess_pm_output_tasks(
            repo=self.repo,
            run_dir=self.root / "run-active-admission",
            cycle_idx=1,
            kind="bootstrap",
            raw_pm_output_path=self.root / "pm-active.txt",
            pm_output_model_dump={
                "kind": "bootstrap",
                "summary": "Plan active goal",
                "tasks": [
                    {
                        "id": "T-active",
                        "title": "Investigate telemetry retention drift",
                        "prompt": "Active goal: Investigate telemetry retention drift. Produce a bounded diagnosis.",
                        "done_when": "A small diagnosis is written; no GOALS checkbox is changed.",
                    }
                ],
            },
            existing_tasks=[],
            done_ids=set(),
            failed_ids=set(),
            completion_level="p0",
            active_goal_status=status,
        )

        self.assertEqual("rejected", processed["pm_gate"]["status"])
        self.assertEqual([], processed["active_goal_admitted_tasks"])
        self.assertEqual([], processed["backlog_tasks"])
        self.assertEqual(1, len(processed["active_goal_proposed_tasks"]))
        proposal = processed["active_goal_proposed_tasks"][0]
        self.assertEqual(status["goal"]["id"], proposal["active_goal_id"])
        self.assertEqual("active_goal_proposal", proposal["active_goal_proposal"]["admission"])
        self.assertTrue(proposal["active_goal_proposal"]["does_not_enter_backlog"])
        self.assertTrue(proposal["active_goal_proposal"]["does_not_mark_goals_complete"])
        self.assertIn("proposal-only", processed["pm_gate"]["message"])

    def test_pm_postprocess_rejects_unrelated_task_despite_active_goal(self) -> None:
        (self.repo / ".doc").mkdir()
        (self.repo / ".doc" / "GOALS.md").write_text(
            "# Project Goals\n\n## P0\n- [ ] Preserve GOALS-first project safety\n",
            encoding="utf-8",
        )
        status = create_active_goal(self.repo, "Investigate telemetry retention drift", mode="exploratory")

        processed = postprocess_pm_output_tasks(
            repo=self.repo,
            run_dir=self.root / "run-active-reject",
            cycle_idx=1,
            kind="bootstrap",
            raw_pm_output_path=self.root / "pm-reject.txt",
            pm_output_model_dump={
                "kind": "bootstrap",
                "summary": "Bad plan",
                "tasks": [
                    {
                        "id": "T-unrelated",
                        "title": "Rewrite unrelated styling",
                        "prompt": "Change colors across the app.",
                        "done_when": "Colors changed.",
                    }
                ],
            },
            existing_tasks=[],
            done_ids=set(),
            failed_ids=set(),
            completion_level="p0",
            active_goal_status=status,
        )

        self.assertEqual([], processed["backlog_tasks"])
        self.assertEqual(1, len(processed["rejected_pm_tasks"]))
        self.assertEqual("missing_unchecked_p0_reference", processed["rejected_pm_tasks"][0]["reason"])

    def test_active_goal_goals_bridge_writes_proposal_without_mutating_goals(self) -> None:
        (self.repo / ".doc").mkdir()
        goals_path = self.repo / ".doc" / "GOALS.md"
        original_goals = "# Project Goals\n\n## P0\n- [ ] Existing goal\n"
        goals_path.write_text(original_goals, encoding="utf-8")
        status = create_active_goal(self.repo, "Add telemetry retention goal", mode="adaptive")

        proposal = propose_goals_from_active_goal(self.repo, level="P1")

        self.assertEqual(original_goals, goals_path.read_text(encoding="utf-8"))
        self.assertTrue(active_goal_goals_proposal_path(self.repo).exists())
        self.assertEqual(status["goal"]["id"], proposal["active_goal_id"])
        self.assertTrue(proposal["does_not_mutate_goals_md"])
        self.assertTrue(proposal["requires_operator_confirmation"])
        self.assertEqual("P1", proposal["proposals"][0]["level"])
        self.assertEqual("Add telemetry retention goal", proposal["proposals"][0]["text"])
        self.assertIn("add", proposal["proposals"][0]["forbidden_without_confirmation"])

    def test_templates_presets_and_checkpoints_shape_active_goal_runtime(self) -> None:
        templates = list_active_goal_templates()
        presets = list_active_goal_autonomy_presets()

        self.assertEqual(
            {
                "bug_fix",
                "feature_build",
                "refactor",
                "test_hardening",
                "documentation",
                "release_prep",
                "exploratory_improvement",
            },
            {item["key"] for item in templates["templates"]},
        )
        self.assertEqual({"one_shot", "overnight", "exploratory"}, {item["key"] for item in presets["presets"]})

        status = create_active_goal(
            self.repo,
            "Fix active-goal checkpoint UI regression",
            template_key="bug_fix",
            autonomy_preset_key="one_shot",
        )

        goal = status["goal"]
        self.assertEqual("strict", goal["mode"])
        self.assertEqual("bug_fix", goal["template"]["key"])
        self.assertEqual("one_shot", goal["autonomy_preset"]["key"])
        self.assertEqual(2, goal["budgets"]["cycle_budget"])
        self.assertEqual(3, len(goal["checkpoints"]))
        self.assertIn("checkpoints=0/3", status["progress"]["summary"])

        first_checkpoint = goal["checkpoints"][0]["id"]
        updated = complete_active_goal_checkpoint(
            self.repo,
            first_checkpoint,
            evidence={"kind": "task_outcome", "text": "Regression isolated", "ref": "STATE.json"},
            resume_point={"task_id": "T-checkpoint", "note": "Continue at patch step"},
            expected_etag=status["etag"],
        )

        checkpoints = updated["goal"]["checkpoints"]
        self.assertEqual("completed", checkpoints[0]["status"])
        self.assertEqual("active", checkpoints[1]["status"])
        self.assertEqual("task_outcome", checkpoints[0]["evidence"][0]["source"])
        self.assertEqual("T-checkpoint", checkpoints[0]["resume_point"]["task_id"])
        self.assertEqual(1, updated["progress"]["checkpointProgress"]["completed"])

        replacement = set_active_goal_checkpoints(
            self.repo,
            [{"title": "Manual checkpoint"}, {"title": "Final validation"}],
            expected_etag=updated["etag"],
        )
        self.assertEqual(2, replacement["progress"]["checkpointProgress"]["total"])

    def test_recommendations_use_goals_experience_pr_validation_and_stale_todo_signals(self) -> None:
        (self.repo / ".doc").mkdir()
        (self.repo / ".doc" / "GOALS.md").write_text(
            "# Project Goals\n\n## P0\n- [ ] Repair failing validation\n\n## P1\n- [ ] Improve operator ergonomics\n",
            encoding="utf-8",
        )
        pr_path = self.repo / ".AgentCLI" / "pr_queue" / "pr-blocked.json"
        pr_path.parent.mkdir(parents=True, exist_ok=True)
        pr_path.write_text(
            json.dumps(
                {
                    "id": "pr-blocked",
                    "status": "pr_queued",
                    "validation_status": "validation_failed",
                    "task_ids": ["T-pr"],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        validation_path = self.repo / ".AgentCLI" / "agent_runs" / "run-001" / "tasks" / "T-val" / "attempt_01" / "validation.json"
        validation_path.parent.mkdir(parents=True, exist_ok=True)
        validation_path.write_text(
            json.dumps({"task_id": "T-val", "status": "validation_failed", "reason": "pytest failed"}, ensure_ascii=False),
            encoding="utf-8",
        )
        todo_path = self.repo / ".AgentCLI" / "todo" / "Today.md"
        todo_path.parent.mkdir(parents=True, exist_ok=True)
        todo_path.write_text("# TODO\n\n- [ ] Reconcile stale operator priority\n", encoding="utf-8")
        (todo_path.parent / "LAST_TODO.txt").write_text(".AgentCLI/todo/Today.md\n", encoding="utf-8")
        stale_ts = time.time() - (3 * 24 * 60 * 60)
        os.utime(todo_path, (stale_ts, stale_ts))
        upsert_lessons(
            self.repo,
            [
                {
                    "kind": "active_goal_budget",
                    "normalized_trigger": "active_goal_budget_test",
                    "lesson": "Shrink active-goal decomposition after budget exhaustion.",
                    "confidence": 0.9,
                    "evidence_pointers": [".AgentCLI/goals/ACTIVE_GOAL.json"],
                }
            ],
        )

        recommendations = recommend_next_active_goals(self.repo, limit=10)

        sources = {item["sourceKind"] for item in recommendations["items"]}
        self.assertIn("unmet_p0_goals", sources)
        self.assertIn("pr_queue_blocker", sources)
        self.assertIn("failing_validation", sources)
        self.assertIn("experience_db_lesson", sources)
        self.assertIn("stale_todo_priority", sources)
        self.assertTrue(all(item["requiresOperatorConfirmation"] for item in recommendations["items"]))
        self.assertTrue(recommendations["subordinateToGoalsMd"])

    def test_timeline_export_import_and_analytics_are_redacted_runtime_evidence(self) -> None:
        secret = "api_key=SECRET-ACTIVE-GOAL-EXPORT"
        status = create_active_goal(
            self.repo,
            f"Ship analytics without leaking {secret}",
            template_key="test_hardening",
            autonomy_preset_key="one_shot",
        )
        increment_active_goal_usage(self.repo, cycles_used=1, tokens_used=42)
        record_history(
            self.repo,
            self.repo / ".AgentCLI" / "agent_runs" / "run-export",
            "codex",
            task_id="T-export",
            title="Export active goal state",
            status="done",
            files=["agent_runner/active_goal.py"],
        )
        context = active_goal_role_context(build_active_goal_status(self.repo), role="Validation")
        validation_path = write_task_validation_artifacts(
            attempt_dir=self.repo / ".AgentCLI" / "agent_runs" / "run-export" / "tasks" / "T-export" / "attempt_01",
            task_id="T-export",
            task_title="Export active goal state",
            task_files=["agent_runner/active_goal.py"],
            cycle=1,
            step=1,
            attempt=1,
            validations=[],
            status="passed",
            reason="completed",
            active_goal_context=context,
        )
        queue_review_packet(
            self.repo,
            run_id="run-export",
            task_ids=["T-export"],
            base_ref="base",
            head_ref="head",
            branch="feature/export",
            validation_status="validation_passed",
            validation_artifacts=[validation_path.as_posix()],
            changed_files=["agent_runner/active_goal.py"],
        )
        completed = complete_active_goal(
            self.repo,
            evidence={"kind": "validation_artifact", "text": f"validated {secret}", "ref": validation_path.as_posix()},
            expected_etag=build_active_goal_status(self.repo)["etag"],
        )

        timeline = build_active_goal_timeline(self.repo)
        kinds = {item["kind"] for item in timeline["items"]}
        self.assertIn("goal_event", kinds)
        self.assertIn("task_decomposition", kinds)
        self.assertIn("validation_evidence", kinds)
        self.assertIn("pr_packet", kinds)
        self.assertEqual(completed["goal"]["id"], timeline["goalId"])

        analytics = build_active_goal_analytics(self.repo)
        self.assertEqual(1, analytics["completed"])
        self.assertEqual(1, analytics["manualInterventionCount"])
        self.assertEqual(1, analytics["medianCyclesToCompletion"])

        export_payload = write_active_goal_export(self.repo)
        self.assertTrue(active_goal_export_path(self.repo).exists())
        serialized_export = json.dumps(export_payload, ensure_ascii=False)
        self.assertNotIn(secret, serialized_export)
        self.assertTrue(export_payload["redaction_policy"]["rawPromptsExcluded"])
        self.assertTrue(export_payload["subordinateToGoalsMd"])

        imported = import_active_goal_state(self.repo, export_payload, replace=True)
        self.assertEqual("import", imported["goal"]["source"]["kind"])
        self.assertEqual(completed["goal"]["id"], imported["goal"]["id"])
        self.assertTrue(export_active_goal_state(self.repo)["redaction_policy"]["secretPatternsRedacted"])

    def test_active_goal_metadata_reaches_task_history(self) -> None:
        status = create_active_goal(
            self.repo,
            "Record task history active goal metadata",
            token_budget=321,
            cycle_budget=4,
        )

        record_history(
            self.repo,
            self.root / "run-001",
            "codex",
            task_id="T-history",
            title="Persist history metadata",
            status="done",
            files=["agent_runner/task_history.py"],
        )

        rows = query_history(self.repo, max_items=1)
        self.assertEqual("T-history", rows[0]["task_id"])
        self.assertEqual(status["goal"]["id"], rows[0]["active_goal_id"])
        self.assertEqual("Record task history active goal metadata", rows[0]["active_goal"]["objective"])
        self.assertEqual(321, rows[0]["active_goal"]["budgets"]["token_budget"])

    def test_task_bound_active_goal_snapshot_prevents_history_context_drift(self) -> None:
        original = create_active_goal(self.repo, "Original task-bound active goal", cycle_budget=2)
        original_snapshot = active_goal_task_metadata(original)["active_goal"]
        create_active_goal(self.repo, "New live active goal", replace=True)

        context = active_goal_role_context_from_task_snapshot(original_snapshot, role="Validation")
        record_history(
            self.repo,
            self.root / "run-drift",
            "codex",
            task_id="T-drift",
            title="Persist task-bound metadata",
            status="done",
            active_goal=original_snapshot,
        )

        rows = query_history(self.repo, max_items=1)
        self.assertEqual(original["goal"]["id"], context["active_goal_id"])
        self.assertEqual("Original task-bound active goal", context["active_goal"]["objective"])
        self.assertEqual(original["goal"]["id"], rows[0]["active_goal_id"])
        self.assertEqual("Original task-bound active goal", rows[0]["active_goal"]["objective"])

    def test_active_goal_metadata_reaches_pr_validation_and_shutdown_reports(self) -> None:
        status = create_active_goal(self.repo, "Carry active goal through artifacts", cycle_budget=2)
        context = active_goal_role_context(status, role="Validation")
        run_dir = self.root / "run-artifacts"
        attempt_dir = run_dir / "tasks" / "T-artifacts" / "attempt_01"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        write_backlog_files(
            run_dir,
            [
                {
                    "id": "T-artifacts",
                    "title": "Artifact task",
                    "prompt": "Write active goal metadata.",
                    "files": ["agent_runner/active_goal.py"],
                    "done_when": "Metadata exists.",
                }
            ],
        )
        (run_dir / "STATE.json").write_text(
            json.dumps({"done": ["T-artifacts"], "failed": [], "warnings": []}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        validation_path = write_task_validation_artifacts(
            attempt_dir=attempt_dir,
            task_id="T-artifacts",
            task_title="Artifact task",
            task_files=["agent_runner/active_goal.py"],
            cycle=1,
            step=1,
            attempt=1,
            validations=[],
            status="passed",
            reason="completed",
            active_goal_context=context,
        )
        validation_payload = json.loads(validation_path.read_text(encoding="utf-8"))
        self.assertEqual(status["goal"]["id"], validation_payload["active_goal_context"]["active_goal_id"])
        self.assertEqual(2, validation_payload["active_goal_context"]["active_goal"]["budgets"]["cycle_budget"])

        packet_result = queue_review_packet(
            self.repo,
            run_id=run_dir.name,
            task_ids=["T-artifacts"],
            base_ref="base",
            head_ref="head",
            branch="feature/artifacts",
            validation_status="validation_passed",
            validation_artifacts=[validation_path.as_posix()],
            changed_files=["agent_runner/active_goal.py"],
        )
        packet = json.loads(pr_packet_path(self.repo, str(packet_result["packet_id"])).read_text(encoding="utf-8"))
        self.assertEqual(status["goal"]["id"], packet["active_goal_context"]["active_goal_id"])
        self.assertTrue(packet["active_goal_context"]["does_not_approve_pr_merge"])
        self.assertTrue(packet["active_goal_context"]["does_not_mark_goals_complete"])

        shutdown_context = collect_shutdown_context(self.repo, run_dir)
        self.assertEqual(status["goal"]["id"], shutdown_context["active_goal_context"]["active_goal_id"])
        shutdown_report = build_local_shutdown_report(
            repo=self.repo,
            run_dir=run_dir,
            reason="test_complete",
            last_task_id="T-artifacts",
        )
        self.assertIn(f"active_goal_id: {status['goal']['id']}", shutdown_report)

        reports = write_run_report_artifacts(repo=self.repo, run_dir=run_dir, stop_reason="test_complete")
        self.assertEqual(
            status["goal"]["id"],
            reports["qa_validation_report"]["active_goal_context"]["active_goal_id"],
        )
        self.assertEqual(
            status["goal"]["id"],
            reports["final_run_report"]["active_goal_context"]["active_goal_id"],
        )
        self.assertEqual("state=active", reports["final_run_report"]["active_goal_progress"]["summary"].split()[0])
        final_md = (run_dir / "FINAL_RUN_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("## Active Goal", final_md)
        self.assertIn("subordinate_to_goals_md: True", final_md)

    def test_shell_goal_command_creates_and_reports_status(self) -> None:
        shell = RunnerShell(initial_argv=["--repo", str(self.repo)])

        buffer = StringIO()
        with redirect_stdout(buffer):
            shell.goal(["create", "Ship", "active", "goal", "core", "--mode", "strict"])

        output = buffer.getvalue()
        self.assertIn("[OK] Active goal created", output)
        self.assertIn("active_goal:", output)
        self.assertIn("active_goal_progress:", output)
        self.assertIn("active_goal_objective: Ship active goal core", output)
        self.assertEqual("strict", build_active_goal_status(self.repo)["goal"]["mode"])

    def test_non_interactive_active_goal_create_and_status_exit_without_running(self) -> None:
        buffer = StringIO()
        with redirect_stdout(buffer):
            rc = runner_main(
                [
                    "--repo",
                    str(self.repo),
                    "--active-goal-objective",
                    "Scripted active goal",
                    "--active-goal-mode",
                    "adaptive",
                    "--active-goal-cycle-budget",
                    "2",
                ]
            )

        self.assertEqual(0, rc)
        payload = json.loads(buffer.getvalue())
        self.assertEqual("Scripted active goal", payload["goal"]["objective"])
        self.assertEqual(2, payload["goal"]["budgets"]["cycle_budget"])
        self.assertEqual(2, payload["progress"]["cycle"]["remaining"])

        buffer = StringIO()
        with redirect_stdout(buffer):
            rc = runner_main(["--repo", str(self.repo), "--active-goal-status"])

        self.assertEqual(0, rc)
        payload = json.loads(buffer.getvalue())
        self.assertTrue(payload["active"])
        self.assertEqual("Scripted active goal", payload["goal"]["objective"])
        self.assertIn("cycles=0/2", payload["progress"]["summary"])

        buffer = StringIO()
        with redirect_stdout(buffer):
            rc = runner_main(
                [
                    "--repo",
                    str(self.repo),
                    "--active-goal-update",
                    "--active-goal-objective",
                    "Scripted active goal updated",
                    "--active-goal-mode",
                    "strict",
                    "--active-goal-notes",
                    "operator note",
                    "--active-goal-etag",
                    payload["etag"],
                ]
            )

        self.assertEqual(0, rc)
        updated = json.loads(buffer.getvalue())
        self.assertEqual("Scripted active goal updated", updated["goal"]["objective"])
        self.assertEqual("strict", updated["goal"]["mode"])
        self.assertEqual("operator note", updated["goal"]["notes"])

    def test_web_active_goal_redaction_hides_operator_text(self) -> None:
        secret = "SECRET-ACTIVE-GOAL"
        status = create_active_goal(self.repo, f"Ship {secret}", source={"kind": "operator", "actor": secret})
        status = complete_active_goal(self.repo, evidence=f"Validated {secret}", expected_etag=status["etag"])

        redacted = _redact_web_active_goal_payload(status)

        serialized = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        self.assertEqual("[redacted]", redacted["path"])
        self.assertEqual("[redacted]", redacted["goal"]["objective"])
        self.assertEqual("[redacted]", redacted["goal"]["completion_evidence"][0]["text"])

    def test_web_active_goal_redaction_covers_history_and_intelligence_shapes(self) -> None:
        secret = "SECRET-ACTIVE-GOAL-INTEL"
        context = {
            "active_goal": {"id": "goal-1", "objective": f"Ship {secret}", "notes": f"note {secret}"},
            "activeGoal": {"id": "goal-1", "objective": f"Ship {secret}", "notes": f"note {secret}"},
        }
        history = {
            "items": [
                {
                    "finalRunReport": {"active_goal_context": context},
                    "qaValidationReport": {"activeGoalContext": context},
                    "activeGoalContext": context,
                }
            ]
        }
        intelligence = {
            "recommendations": [
                {
                    "objective": f"Fix {secret}",
                    "reason": f"Because {secret}",
                    "evidence": [{"ref": f".doc/{secret}.md", "text": f"evidence {secret}"}],
                }
            ],
            "items": [{"objective": f"Timeline {secret}", "title": f"Task {secret}", "artifact": f"log-{secret}.json"}],
            "analytics": {
                "validation_failure_reasons": {f"failed {secret}": 2},
                "validationFailureReasons": {f"failed {secret}": 2},
            },
        }

        redacted_history = _redact_web_history_payload(history)
        redacted_intelligence = _redact_web_active_goal_payload(intelligence)

        self.assertNotIn(secret, json.dumps(redacted_history, ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(redacted_intelligence, ensure_ascii=False))
        self.assertEqual(
            "[redacted]",
            redacted_history["items"][0]["finalRunReport"]["active_goal_context"]["active_goal"]["objective"],
        )
        self.assertEqual("[redacted]", redacted_intelligence["recommendations"][0]["objective"])


if __name__ == "__main__":
    unittest.main()
