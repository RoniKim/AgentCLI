from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agent_runner.runner_entry as runner_entry
from agent_runner.gitops import scan_worktree_diagnostics
from agent_runner.goals import should_attempt_goals_refresh
from agent_runner.pipeline.manager import PipelineManager
from agent_runner.pipeline.stages.base import Stage, StageOutcome
from agent_runner.pr_queue import pr_branch_index_path, pr_packet_path, pr_queue_root, reconcile_review_queue
from agent_runner.runtime_contract import ATTEMPT_STARTED_MARKER
from agent_runner.stop_progress import stop_aware_sleep
from agent_runner.utils import (
    STOP_REASON_ALL_TASKS_ATTEMPTED,
    STOP_REASON_NO_TASKS,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_QUOTA,
    STOP_REASON_QUOTA_UTILIZATION,
    STOP_REASON_STOP_FILE,
)


class _FakeRunner:
    def __init__(self, name: str, calls: list[str], terminal_reason: str) -> None:
        self.name = name
        self._calls = calls
        self._terminal_reason = terminal_reason

    async def run(self, args: argparse.Namespace, repo: Path) -> int:
        self._calls.append(self.name)
        run_dir = Path(args.run_dir)
        run_dir.mkdir(parents=True, exist_ok=True)
        if self._terminal_reason:
            (run_dir / "STOP").write_text(f"{self._terminal_reason}\n", encoding="utf-8")
            (run_dir / "run_summary.json").write_text(
                json.dumps({"final": {"rc": 0, "reason": self._terminal_reason}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        else:
            if (run_dir / "STOP").exists():
                raise AssertionError("failover did not clear STOP before starting the next backend")
            (run_dir / "run_summary.json").write_text(
                json.dumps({"final": {"rc": 0, "reason": STOP_REASON_PROJECT_COMPLETE}}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        return 0


class _FakeSession:
    def __init__(self, *, tasks_available: bool = True) -> None:
        self.done_delta = 0
        self.tasks_available = tasks_available

    def has_stop(self) -> bool:
        return False

    def ensure_backlog(self) -> bool:
        return True

    def ensure_tasks_loaded(self) -> bool:
        return self.tasks_available

    def consume_stage_effects(self) -> frozenset[str]:
        return frozenset()


class _OutcomeStage(Stage):
    name = "Dev"

    def __init__(self, outcome: StageOutcome) -> None:
        self._outcome = outcome

    async def run(self, session: _FakeSession, cycle_idx: int) -> StageOutcome:
        return self._outcome


class CriticalPathSmokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        scratch_root = Path.home() / ".codex" / "memories" / "agentcli-critical-paths"
        scratch_root.mkdir(parents=True, exist_ok=True)
        self.tmp = scratch_root / f"t-{uuid.uuid4().hex[:12]}"
        self.tmp.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.repo = self.tmp / "repo"
        self.repo.mkdir(parents=True)

    async def test_backend_failover_dispatches_quota_reasons_and_preserves_run_context(self) -> None:
        for reason in (STOP_REASON_QUOTA, STOP_REASON_QUOTA_UTILIZATION):
            with self.subTest(reason=reason):
                calls: list[str] = []
                run_dir = self.tmp / f"run-{reason}"
                args = argparse.Namespace(
                    repo=str(self.repo),
                    execution_backend="codex",
                    failover_enabled=True,
                    failover_backends=["codex", "claudecode"],
                    failover_on=[reason],
                    failover_max_switches=1,
                    run_dir=str(run_dir),
                    resume_latest=False,
                    stop_file="STOP",
                )

                def fake_get_runner(backend: str) -> _FakeRunner:
                    return _FakeRunner(backend, calls, reason if backend == "codex" else "")

                preflight = [
                    SimpleNamespace(backend="codex", ok=True, issues=[]),
                    SimpleNamespace(backend="claudecode", ok=True, issues=[]),
                ]
                with patch.object(runner_entry, "run_preflight", return_value=preflight), patch.object(
                    runner_entry,
                    "get_runner",
                    side_effect=fake_get_runner,
                ):
                    rc = await runner_entry._main_async_dispatch(args)

                self.assertEqual(0, rc)
                self.assertEqual(["codex", "claudecode"], calls)
                self.assertEqual(str(run_dir.resolve()), str(Path(args.run_dir).resolve()))
                self.assertFalse((run_dir / "STOP").exists())
                metrics = [
                    json.loads(line)
                    for line in (run_dir / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                failover_events = [item for item in metrics if item.get("event") == "backend_failover"]
                self.assertEqual(1, len(failover_events))
                self.assertEqual("codex", failover_events[0]["from_backend"])
                self.assertEqual("claudecode", failover_events[0]["to_backend"])
                self.assertEqual(reason, failover_events[0]["reason"])

    async def test_quota_wait_stop_aware_sleep_exits_on_stop_and_refreshes_heartbeat(self) -> None:
        run_dir = self.tmp / "run-wait"
        run_dir.mkdir()
        stop_path = run_dir / "STOP"
        wait_task = asyncio.create_task(
            stop_aware_sleep(
                30,
                run_dir=run_dir,
                stop_paths=[stop_path],
                poll_seconds=0.01,
                heartbeat_interval_seconds=0.01,
            )
        )
        await asyncio.sleep(0.03)
        stop_path.write_text(f"{STOP_REASON_STOP_FILE}\n", encoding="utf-8")

        result = await asyncio.wait_for(wait_task, timeout=1)

        self.assertTrue(result.stopped)
        self.assertEqual(STOP_REASON_STOP_FILE, result.stop_reason)
        self.assertTrue((run_dir / "HEARTBEAT").exists())

    async def test_outer_loop_reason_handling_keeps_rescue_reasons_explicit(self) -> None:
        goals_path = self.repo / ".doc" / "GOALS.md"
        goals_path.parent.mkdir(parents=True)
        goals_path.write_text("# Goals\n\n## P0\n\n- [x] required complete\n\n## P1\n\n- [x] followup complete\n", encoding="utf-8")

        project_result = await PipelineManager([_OutcomeStage(StageOutcome.ok(reason=STOP_REASON_PROJECT_COMPLETE))]).run_cycle(
            _FakeSession(),
            1,
            continuous=True,
        )
        attempted_result = await PipelineManager([_OutcomeStage(StageOutcome.ok(reason=STOP_REASON_ALL_TASKS_ATTEMPTED))]).run_cycle(
            _FakeSession(),
            1,
            continuous=True,
        )
        no_tasks_result = await PipelineManager([_OutcomeStage(StageOutcome.ok())]).run_cycle(
            _FakeSession(tasks_available=False),
            1,
            continuous=True,
        )

        self.assertEqual(STOP_REASON_PROJECT_COMPLETE, project_result.reason)
        self.assertEqual(STOP_REASON_ALL_TASKS_ATTEMPTED, attempted_result.reason)
        self.assertEqual(STOP_REASON_NO_TASKS, no_tasks_result.reason)
        self.assertEqual(
            (True, "ok"),
            should_attempt_goals_refresh(self.repo, STOP_REASON_PROJECT_COMPLETE, 0, 1, True),
        )
        self.assertEqual(
            (True, "ok"),
            should_attempt_goals_refresh(self.repo, STOP_REASON_NO_TASKS, 0, 1, True),
        )
        self.assertEqual(
            (False, "not_rescuable"),
            should_attempt_goals_refresh(self.repo, STOP_REASON_ALL_TASKS_ATTEMPTED, 0, 1, True),
        )

    def test_interrupted_attempt_detection_reports_started_without_finished(self) -> None:
        attempt_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260506-010203" / "tasks" / "c1_s1_T-001" / "attempt_1"
        attempt_dir.mkdir(parents=True)
        (attempt_dir / ATTEMPT_STARTED_MARKER).write_text(
            json.dumps({"timestamp": "2026-05-06T01:02:03"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        diagnostics = scan_worktree_diagnostics(self.repo)

        self.assertEqual(1, diagnostics["summary"]["interrupted_attempts"])
        interrupted = diagnostics["interrupted_attempts"][0]
        self.assertEqual("T-001", interrupted["task_id"])
        self.assertEqual(1, interrupted["attempt"])
        self.assertEqual("interrupted", interrupted["status"])

    def test_pr_queue_reconcile_reports_missing_packet_and_index_without_deleting_evidence(self) -> None:
        queue_root = pr_queue_root(self.repo)
        queue_root.mkdir(parents=True)
        packet_path = pr_packet_path(self.repo, "packet-present")
        packet_path.write_text(
            json.dumps(
                {
                    "id": "packet-present",
                    "status": "pr_queued",
                    "branch_index_status": "written",
                    "task_ids": ["T-001"],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        missing_packet_path = pr_packet_path(self.repo, "packet-missing")
        index_path = pr_branch_index_path(self.repo)
        index_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "updated_at": "2026-05-06T01:02:03",
                    "entries": [
                        {
                            "id": "packet-missing",
                            "packet_path": missing_packet_path.as_posix(),
                            "status": "pr_queued",
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        packet_before = packet_path.read_text(encoding="utf-8")
        index_before = index_path.read_text(encoding="utf-8")

        result = reconcile_review_queue(self.repo)

        self.assertFalse(result["ok"])
        self.assertTrue(result["dry_run"])
        self.assertEqual("issues_found", result["state"])
        self.assertEqual(1, result["summary"]["missing_packets"])
        self.assertEqual(1, result["summary"]["missing_branch_index_entries"])
        items = {str(item["id"]): item for item in result["items"]}
        self.assertEqual("missing", items["packet-present"]["branch_index_state"])
        self.assertEqual("orphan_index_entry", items["packet-missing"]["kind"])
        self.assertFalse(missing_packet_path.exists())
        self.assertEqual(packet_before, packet_path.read_text(encoding="utf-8"))
        self.assertEqual(index_before, index_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
