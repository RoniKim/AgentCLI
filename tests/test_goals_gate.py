from __future__ import annotations

from contextlib import redirect_stderr
from io import StringIO
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import agent_runner.goals as goals_module
from agent_runner.goals import (
    GOALS_INCOMPLETE_STATUS,
    classify_goals_completion_status,
    gate_pm_tasks_against_goals,
    parse_goals_completion,
    resolve_completion_final_reason,
    update_goals_checkboxes,
    write_completion_status,
)
from agent_runner.backlog_utils import normalize_backlog_tasks, postprocess_pm_output_tasks
from agent_runner.gitops import create_task_branch, format_task_commit_message
from agent_runner.metrics import MetricsLogger
from agent_runner.state import TaskItem, load_backlog_json, write_backlog_files
from agent_runner.utils import atomic_write_text as real_atomic_write_text


GOALS_MD = """# Project Goals

## P0 (Must-Have)
- [ ] Ship web console gate
- [ ] Add goal trace metadata

## P1 (Should-Have)
- [ ] Improve docs
"""


class GoalsGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = ROOT / ".tmp-goals-gate-tests"
        self.fixture_base.mkdir(exist_ok=True)
        self.fixture_root = self.fixture_base / f"{self._testMethodName}-{uuid.uuid4().hex}"
        self.fixture_root.mkdir()
        self.repo = self.fixture_root / "repo"
        self.run_dir = self.fixture_root / "run"
        self.repo.mkdir()
        (self.repo / ".doc").mkdir()
        self.goals_path = self.repo / ".doc" / "GOALS.md"
        self.goals_path.write_text(GOALS_MD, encoding="utf-8")
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))

    def _pm_tasks(self) -> list[dict[str, object]]:
        return [
            {
                "id": "T1",
                "title": "Refine layout",
                "prompt": "Tighten spacing on the backlog view.",
                "done_when": "The backlog view has a calmer visual hierarchy.",
            },
            {
                "id": "T2",
                "title": "Ship web console gate",
                "prompt": "Implement Ship web console gate exactly as written in GOALS.md.",
                "done_when": "The Ship web console gate goal is satisfied and documented.",
            },
        ]

    def test_no_goals_completion_reason_blocks_ok_terminal_reason(self) -> None:
        status = parse_goals_completion("", completion_level="all")
        completion = classify_goals_completion_status(status)

        self.assertFalse(status["has_goals"])
        self.assertFalse(completion["project_complete"])
        self.assertEqual("no_goals", completion["completion_status"])
        self.assertEqual(GOALS_INCOMPLETE_STATUS, completion["completion_reason"])

        write_completion_status(self.run_dir, status, stop_reason="cycle_end")
        self.assertEqual(GOALS_INCOMPLETE_STATUS, resolve_completion_final_reason(self.run_dir, "ok", last_rc=0))
        self.assertEqual("ok", resolve_completion_final_reason(self.run_dir, "ok", last_rc=1))

    def test_goals_auto_check_uses_atomic_write_for_matching_tasks(self) -> None:
        with patch("agent_runner.goals.atomic_write_text", wraps=real_atomic_write_text) as atomic_write:
            result = update_goals_checkboxes(
                self.repo,
                ["Ship web console gate"],
                ["GOALS: Ship web console gate"],
                completion_level="p0",
            )

        self.assertTrue(result["updated"])
        self.assertEqual("updated", result["status"])
        self.assertFalse(result["conflict"])
        self.assertEqual(["Ship web console gate"], result["checked_items"])
        atomic_write.assert_called_once()
        self.assertEqual(self.goals_path, Path(atomic_write.call_args.args[0]))

        updated_text = self.goals_path.read_text(encoding="utf-8")
        self.assertIn("- [x] Ship web console gate", updated_text)
        self.assertIn("- [ ] Add goal trace metadata", updated_text)

    def test_goals_auto_check_preserves_concurrent_edit_and_returns_conflict(self) -> None:
        conflict_text = GOALS_MD.replace("- [ ] Add goal trace metadata", "- [x] Add goal trace metadata", 1)
        original_read_snapshot = goals_module._read_goals_snapshot
        read_count = 0

        def conflicting_read_snapshot(path: Path) -> dict[str, object] | None:
            nonlocal read_count
            read_count += 1
            if read_count == 2:
                path.write_text(conflict_text, encoding="utf-8")
            return original_read_snapshot(path)

        stderr_buffer = StringIO()
        with (
            redirect_stderr(stderr_buffer),
            patch("agent_runner.goals._read_goals_snapshot", side_effect=conflicting_read_snapshot),
        ):
            result = update_goals_checkboxes(
                self.repo,
                ["Ship web console gate"],
                ["GOALS: Ship web console gate"],
                completion_level="p0",
            )

        self.assertFalse(result["updated"])
        self.assertEqual("conflict", result["status"])
        self.assertTrue(result["conflict"])
        self.assertTrue(result["skipped"])
        self.assertEqual([], result["checked_items"])
        self.assertEqual(["Ship web console gate"], result["matched_items"])
        self.assertEqual(conflict_text, self.goals_path.read_text(encoding="utf-8"))
        self.assertEqual(1, result["new_status"]["p0_done"])
        self.assertFalse(result["new_status"]["project_complete"])
        self.assertIn("concurrent edit conflict", stderr_buffer.getvalue())

    def test_goals_auto_check_no_match_is_non_mutating(self) -> None:
        original_text = self.goals_path.read_text(encoding="utf-8")
        with patch("agent_runner.goals.atomic_write_text", side_effect=AssertionError("unexpected write")):
            result = update_goals_checkboxes(
                self.repo,
                ["Unrelated task"],
                ["Unrelated prompt"],
                completion_level="p0",
            )

        self.assertFalse(result["updated"])
        self.assertEqual("no_match", result["status"])
        self.assertTrue(result["skipped"])
        self.assertFalse(result["conflict"])
        self.assertEqual(original_text, self.goals_path.read_text(encoding="utf-8"))

    def test_goals_auto_check_missing_file_is_non_mutating(self) -> None:
        self.goals_path.unlink()
        with patch("agent_runner.goals.atomic_write_text", side_effect=AssertionError("unexpected write")):
            result = update_goals_checkboxes(
                self.repo,
                ["Ship web console gate"],
                ["GOALS: Ship web console gate"],
                completion_level="p0",
            )

        self.assertFalse(result["updated"])
        self.assertEqual("missing_file", result["status"])
        self.assertTrue(result["skipped"])
        self.assertFalse(result["conflict"])
        self.assertFalse(self.goals_path.exists())

    def test_gate_rejects_unrelated_tasks_while_p0_remain(self) -> None:
        gate = gate_pm_tasks_against_goals(self.repo, self._pm_tasks(), completion_level="p0")

        self.assertTrue(gate["gate_required"])
        self.assertEqual("partial", gate["status"])
        self.assertEqual(self.repo / ".doc" / "GOALS.md", Path(gate["goal_path"]))
        self.assertEqual(1, len(gate["accepted_tasks"]))
        self.assertEqual(1, len(gate["rejected_tasks"]))
        self.assertEqual("pm_goal_gate_partial", gate["error"]["code"])

        accepted = gate["accepted_tasks"][0]
        self.assertEqual("T2", accepted["id"])
        self.assertTrue(accepted["goal_trace"])
        self.assertEqual("P0-L4", accepted["goal_trace"][0]["goal_ref"])
        self.assertEqual("Ship web console gate", accepted["goal_trace"][0]["goal_text"])
        self.assertIn("prompt", accepted["goal_trace"][0]["matched_fields"])

        rejected = gate["rejected_tasks"][0]
        self.assertEqual("T1", rejected["id"])
        self.assertEqual("missing_unchecked_p0_reference", rejected["reason"])
        self.assertEqual(["P0-L4", "P0-L5"], rejected["required_goal_refs"])

    def test_gate_rejects_all_unrelated_tasks_when_p0_remain(self) -> None:
        gate = gate_pm_tasks_against_goals(
            self.repo,
            [
                {
                    "id": "T9",
                    "title": "Refine layout",
                    "prompt": "Tighten spacing on the backlog view.",
                    "done_when": "The backlog view has a calmer visual hierarchy.",
                }
            ],
            completion_level="p0",
        )

        self.assertTrue(gate["gate_required"])
        self.assertEqual("rejected", gate["status"])
        self.assertEqual(0, len(gate["accepted_tasks"]))
        self.assertEqual(1, len(gate["rejected_tasks"]))
        self.assertEqual("pm_goal_gate_rejected", gate["error"]["code"])
        self.assertEqual("T9", gate["rejected_tasks"][0]["id"])

    def test_backlog_json_round_trips_goal_trace_metadata(self) -> None:
        gate = gate_pm_tasks_against_goals(self.repo, self._pm_tasks(), completion_level="p0")
        accepted_tasks = gate["accepted_tasks"]

        write_backlog_files(self.run_dir, accepted_tasks)
        backlog_path = self.run_dir / "BACKLOG.json"
        payload = json.loads(backlog_path.read_text(encoding="utf-8"))

        self.assertEqual("P0-L4", payload["tasks"][0]["goal_trace"][0]["goal_ref"])
        self.assertEqual("Ship web console gate", payload["tasks"][0]["goal_trace"][0]["goal_text"])

        loaded_tasks = load_backlog_json(backlog_path)
        self.assertEqual(1, len(loaded_tasks))
        self.assertEqual("P0-L4", loaded_tasks[0].goal_trace[0]["goal_ref"])
        self.assertEqual("Ship web console gate", loaded_tasks[0].goal_trace[0]["goal_text"])

    def test_goal_gate_splits_oversized_goal_bundles(self) -> None:
        goals_text = """# Project Goals

## P0
- [ ] Goal alpha
- [ ] Goal beta
- [ ] Goal gamma
- [ ] Goal delta
- [ ] Goal epsilon

## P1
- [ ] Later
"""
        (self.repo / ".doc" / "GOALS.md").write_text(goals_text, encoding="utf-8")
        task = {
            "id": "T1",
            "title": "Goal alpha Goal beta Goal gamma Goal delta Goal epsilon",
            "prompt": "Implement Goal alpha, Goal beta, Goal gamma, Goal delta, and Goal epsilon.",
            "done_when": "All five goals are satisfied.",
        }

        gate = gate_pm_tasks_against_goals(self.repo, [task], completion_level="p0")

        self.assertEqual("accepted", gate["status"])
        self.assertEqual(3, len(gate["accepted_tasks"]))
        self.assertEqual(1, len(gate["split_tasks"]))
        self.assertEqual(2, gate["max_goal_traces_per_task"])
        self.assertEqual([2, 2, 1], [len(t["goal_trace"]) for t in gate["accepted_tasks"]])
        self.assertTrue(all(t.get("split_reason") == "oversized_goal_bundle" for t in gate["accepted_tasks"]))
        self.assertIn("Implement ONLY the GOALS scope", gate["accepted_tasks"][0]["prompt"])

        normalized = normalize_backlog_tasks(gate["accepted_tasks"], self.run_dir)
        self.assertEqual(["T1", "T2", "T3"], [task["id"] for task in normalized])
        self.assertEqual([2, 2, 1], [len(t.get("goal_trace") or []) for t in normalized])
        self.assertEqual("P0-L4", normalized[0]["goal_trace"][0]["goal_ref"])
        self.assertEqual("P0-L8", normalized[2]["goal_trace"][0]["goal_ref"])

    def test_postprocess_preserves_valid_existing_goal_trace(self) -> None:
        existing_tasks = [
            TaskItem(
                id="T7",
                title="Follow-through work",
                prompt="Finish the already-traced work.",
                files=[],
                done_when="The gate work is complete.",
                skills=[],
                skills_rationale=None,
                depends_on=[],
                goal_trace=[
                    {
                        "goal_ref": "P0-L4",
                        "goal_text": "Ship web console gate",
                        "matched_fields": ["prompt"],
                    }
                ],
            )
        ]

        processed = postprocess_pm_output_tasks(
            repo=self.repo,
            run_dir=self.run_dir,
            cycle_idx=1,
            kind="incremental",
            raw_pm_output_path=self.run_dir / "pm_raw.txt",
            pm_output_model_dump={"kind": "incremental", "summary": "Keep the traced task.", "tasks": []},
            existing_tasks=existing_tasks,
            done_ids=set(),
            failed_ids=set(),
            completion_level="p0",
        )

        self.assertEqual(["T7"], [task["id"] for task in processed["backlog_tasks"]])
        self.assertEqual("P0-L4", processed["backlog_tasks"][0]["goal_trace"][0]["goal_ref"])
        self.assertEqual("preserved_goal_trace", processed["backlog_tasks"][0]["goal_trace"][0]["match_mode"])

    def test_postprocess_splits_oversized_tasks_and_keeps_dependency_trace(self) -> None:
        goals_text = """# Project Goals

## P0
- [ ] Goal alpha
- [ ] Goal beta
- [ ] Goal gamma
"""
        (self.repo / ".doc" / "GOALS.md").write_text(goals_text, encoding="utf-8")
        processed = postprocess_pm_output_tasks(
            repo=self.repo,
            run_dir=self.run_dir,
            cycle_idx=2,
            kind="bootstrap",
            raw_pm_output_path=self.run_dir / "pm_raw.txt",
            pm_output_model_dump={
                "kind": "bootstrap",
                "summary": "Split the bundled goals and keep dependencies.",
                "tasks": [
                    {
                        "id": "T1",
                        "title": "Goal alpha Goal beta Goal gamma",
                        "prompt": "Implement Goal alpha, Goal beta, and Goal gamma.",
                        "done_when": "All listed goals are complete.",
                    },
                    {
                        "id": "T9",
                        "title": "Goal gamma verification",
                        "prompt": "After Goal gamma is implemented, verify the Goal gamma flow end-to-end.",
                        "done_when": "Goal gamma validation passes.",
                        "depends_on": ["T1"],
                    },
                ],
            },
            existing_tasks=[],
            done_ids=set(),
            failed_ids=set(),
            completion_level="p0",
        )

        backlog = processed["backlog_tasks"]
        self.assertEqual(["T1", "T2", "T9"], [task["id"] for task in backlog])
        self.assertEqual([2, 1, 1], [len(task.get("goal_trace") or []) for task in backlog])
        self.assertEqual(["T1", "T2"], backlog[2]["depends_on"])
        self.assertEqual("P0-L4", backlog[0]["goal_trace"][0]["goal_ref"])
        self.assertEqual("P0-L6", backlog[2]["goal_trace"][0]["goal_ref"])

    def test_postprocess_rejects_irrelevant_existing_backlog_tasks_while_p0_unmet(self) -> None:
        existing_tasks = [
            TaskItem(
                id="T4",
                title="Refine layout",
                prompt="Tighten spacing on the backlog view.",
                files=[],
                done_when="The backlog view has a calmer visual hierarchy.",
                skills=[],
                skills_rationale=None,
                depends_on=[],
                goal_trace=[],
            )
        ]

        processed = postprocess_pm_output_tasks(
            repo=self.repo,
            run_dir=self.run_dir,
            cycle_idx=3,
            kind="incremental",
            raw_pm_output_path=self.run_dir / "pm_raw.txt",
            pm_output_model_dump={
                "kind": "incremental",
                "summary": "Keep only unmet P0 work in the backlog.",
                "tasks": [
                    {
                        "id": "T2",
                        "title": "Ship web console gate",
                        "prompt": "Implement Ship web console gate exactly as written in GOALS.md.",
                        "done_when": "The Ship web console gate goal is satisfied and documented.",
                    }
                ],
            },
            existing_tasks=existing_tasks,
            done_ids=set(),
            failed_ids=set(),
            completion_level="p0",
        )

        self.assertEqual(["T2"], [task["id"] for task in processed["backlog_tasks"]])
        self.assertEqual(1, len(processed["rejected_backlog_tasks"]))
        self.assertEqual("T4", processed["rejected_backlog_tasks"][0]["id"])
        self.assertEqual("missing_unchecked_p0_reference", processed["rejected_backlog_tasks"][0]["reason"])
        self.assertEqual(1, processed["pm_output_model_dump"]["goals_gate"]["backlog_rejected_count"])

    def test_task_branch_and_commit_message_include_goal_trace(self) -> None:
        gate = gate_pm_tasks_against_goals(self.repo, self._pm_tasks(), completion_level="p0")
        goal_trace = gate["accepted_tasks"][0]["goal_trace"]
        commands: list[list[str]] = []

        def fake_run_cmd(cmd: list[str], *, cwd: Path, timeout_sec: int) -> tuple[int, str]:
            commands.append(list(cmd))
            if cmd[:3] == ["git", "rev-parse", "--abbrev-ref"]:
                return 0, "main\n"
            if cmd[:3] == ["git", "checkout", "-b"]:
                return 0, ""
            raise AssertionError(f"unexpected git command: {cmd}")

        with (
            patch("agent_runner.gitops.check_and_remove_stale_git_lock", return_value=False),
            patch("agent_runner.gitops.git_head", return_value="abc123def4567890"),
            patch("agent_runner.gitops.git_porcelain", return_value=""),
            patch("agent_runner.gitops.run_cmd", side_effect=fake_run_cmd),
            patch("agent_runner.gitops._safe_ts", return_value="20260428-162802-000001Z"),
            patch("agent_runner.gitops.now_iso", return_value="2026-04-28T16:28:02+09:00"),
        ):
            tb = create_task_branch(
                self.repo,
                "T2",
                task_title="Ship web console gate",
                goal_trace=goal_trace,
            )

        self.assertEqual("task/T2__goal-P0-L4_20260428-162802-000001Z", tb.branch_name)
        self.assertEqual("P0-L4", tb.goal_trace[0]["goal_ref"])
        self.assertEqual("Ship web console gate", tb.goal_trace[0]["goal_text"])
        self.assertTrue(any(cmd[:3] == ["git", "checkout", "-b"] for cmd in commands))

        subject, body = format_task_commit_message(tb, action="build passed")
        self.assertIn("[GOAL P0-L4]", subject)
        self.assertIn("(build passed)", subject)
        self.assertIn("GOAL: P0-L4 Ship web console gate", body)

    def test_metrics_event_keeps_goal_trace_fields(self) -> None:
        metrics = MetricsLogger(self.run_dir / "metrics.jsonl")
        goal_trace = [
            {
                "goal_path": (self.repo / ".doc" / "GOALS.md").as_posix(),
                "goal_ref": "P0-L4",
                "goal_text": "Ship web console gate",
                "matched_fields": ["prompt"],
            }
        ]

        metrics.event(
            "task_start",
            cycle=1,
            step=1,
            task_id="T2",
            goal_trace=goal_trace,
            goal_ref="P0-L4",
            goal_text="Ship web console gate",
        )

        record = json.loads((self.run_dir / "metrics.jsonl").read_text(encoding="utf-8").strip())
        self.assertEqual("P0-L4", record["goal_ref"])
        self.assertEqual("Ship web console gate", record["goal_text"])
        self.assertEqual("P0-L4", record["goal_trace"][0]["goal_ref"])
        self.assertEqual("prompt", record["goal_trace"][0]["matched_fields"][0])


if __name__ == "__main__":
    unittest.main()
