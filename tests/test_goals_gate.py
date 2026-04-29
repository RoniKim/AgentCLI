from __future__ import annotations

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


from agent_runner.goals import gate_pm_tasks_against_goals
from agent_runner.backlog_utils import normalize_backlog_tasks
from agent_runner.gitops import create_task_branch, format_task_commit_message
from agent_runner.metrics import MetricsLogger
from agent_runner.state import load_backlog_json, write_backlog_files


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
        (self.repo / ".doc" / "GOALS.md").write_text(GOALS_MD, encoding="utf-8")
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
