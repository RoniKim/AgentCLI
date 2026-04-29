from __future__ import annotations

import argparse
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent_runner.remote.controller import RunnerController, normalize_runner_start_options
from agent_runner.stop_progress import (
    STOP_PROGRESS_FILE,
    STOP_PROGRESS_LOG_FILE,
    read_stop_progress,
    stop_progress_is_active,
    write_stop_progress,
)


def _scratch_dir(name: str) -> Path:
    root = Path(__file__).resolve().parents[1] / ".test-scratch" / "unit-temp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


class StopProgressTests(unittest.TestCase):
    def test_write_and_read_stop_progress(self) -> None:
        run_dir = _scratch_dir("stop_progress")
        requested_at = time.monotonic()

        progress = write_stop_progress(
            run_dir,
            phase="requested",
            message="Stop requested.",
            requested_at_monotonic=requested_at,
            running=True,
            runner_alive=True,
            tracked_child_pids=[321, 654],
            tracked_child_processes=[
                {
                    "pid": 321,
                    "alive": True,
                    "session_file": "C:/temp/session_321.json",
                }
            ],
            stop_file_paths={
                "stop_file_path": "C:/temp/STOP",
                "stop_progress_path": "C:/temp/STOP_PROGRESS.json",
                "stop_progress_log_path": "C:/temp/stop_progress.log",
            },
            last_artifact_signal={
                "path": "C:/temp/run_summary.json",
                "updated_at": "2026-04-28T00:00:00",
            },
            last_log_signal={
                "path": "C:/temp/cycle_summary.log",
                "updated_at": "2026-04-28T00:00:00",
            },
            timeout_guidance={
                "summary": "Retry stop after checking the runner.",
                "recoverable": True,
                "steps": ["Retry stop after checking the runner."],
                "manual_cleanup_hints": ["Close the runner."],
                "locked_file_paths": ["C:/temp/locked.txt"],
            },
        )

        self.assertEqual("request", progress["phase"])
        self.assertTrue(stop_progress_is_active(progress))
        read_progress = read_stop_progress(run_dir)
        self.assertEqual("request", read_progress["phase"])
        self.assertEqual("request", read_progress["current_phase"]["phase"])
        self.assertEqual(["request"], [entry["phase"] for entry in read_progress["history"]])
        self.assertTrue(read_progress["runner_alive"])
        self.assertEqual([321, 654], read_progress["tracked_child_pids"])
        self.assertEqual("C:/temp/STOP", read_progress["stop_file_paths"]["stop_file_path"])
        self.assertTrue(read_progress["timeout_guidance"]["can_retry"])
        self.assertEqual(["Close the runner."], read_progress["manual_cleanup_hints"])
        self.assertEqual(["C:/temp/locked.txt"], read_progress["locked_file_paths"])
        self.assertEqual(321, read_progress["tracked_child_processes"][0]["pid"])
        self.assertEqual("C:/temp/session_321.json", read_progress["tracked_child_processes"][0]["session_file"])
        self.assertTrue((run_dir / STOP_PROGRESS_FILE).exists())
        self.assertTrue((run_dir / STOP_PROGRESS_LOG_FILE).exists())

    def test_controller_stop_emits_progress_payloads(self) -> None:
        repo = _scratch_dir("controller_stop") / "repo"
        run_dir = repo / ".AgentCLI" / "agent_runs" / "run"
        run_dir.mkdir(parents=True)
        controller = RunnerController(
            repo=repo,
            base_args=argparse.Namespace(stop_file="STOP", config_path="", run_dir=str(run_dir)),
            runner_mode="thread",
        )
        controller.run_dir = run_dir
        events: list[dict[str, object]] = []

        result = controller.stop(wait=True, progress_callback=events.append)

        self.assertTrue(result["ok"])
        self.assertFalse(result["running"])
        phases = [str(event.get("phase")) for event in events]
        self.assertEqual(["request", "stop_file_write", "child_termination", "final_artifact_collection", "finalized"], phases)
        progress = read_stop_progress(run_dir)
        self.assertEqual("finalized", progress["phase"])
        self.assertEqual("finalized", progress["current_phase"]["phase"])
        self.assertEqual(
            ["request", "stop_file_write", "child_termination", "final_artifact_collection", "finalized"],
            [entry["phase"] for entry in progress["history"]],
        )
        self.assertFalse(progress["timeout_guidance"]["can_retry"])

    def test_controller_stop_wait_timeout_is_configurable(self) -> None:
        repo = _scratch_dir("controller_stop_timeout") / "repo"
        run_dir = repo / ".AgentCLI" / "agent_runs" / "run"
        run_dir.mkdir(parents=True)
        release = threading.Event()
        controller = RunnerController(
            repo=repo,
            base_args=argparse.Namespace(
                stop_file="STOP",
                config_path="",
                run_dir=str(run_dir),
                stop_wait_timeout_seconds=1,
            ),
            runner_mode="thread",
        )
        controller.run_dir = run_dir
        thread = threading.Thread(target=lambda: release.wait(5), daemon=True)
        controller._runner_thread = thread
        thread.start()

        try:
            with patch("agent_runner.remote.controller.close_all_loggers") as close_loggers, patch(
                "agent_runner.remote.controller.terminate_all_children"
            ) as terminate_children:
                result = controller.stop(wait=True)
        finally:
            release.set()
            thread.join(timeout=2)

        self.assertFalse(result["ok"])
        self.assertTrue(result["running"])
        self.assertEqual("timeout", result["stop_progress"]["phase"])
        self.assertTrue(result["timeoutGuidance"]["canRetry"])
        close_loggers.assert_not_called()
        terminate_children.assert_called()
        progress = read_stop_progress(run_dir)
        self.assertEqual("timeout", progress["phase"])
        self.assertEqual("timeout", progress["current_phase"]["phase"])
        self.assertIn("1s stop wait timeout", progress["message"])
        self.assertIn("runner_wait", [entry["phase"] for entry in progress["history"]])
        self.assertTrue(progress["runner_alive"])
        self.assertTrue(progress["timeout_guidance"]["recoverable"])

    def test_controller_start_creates_fresh_run_dir_by_default_and_reuses_only_when_explicit(self) -> None:
        repo = _scratch_dir("controller_start") / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        controller = RunnerController(
            repo=repo,
            base_args=argparse.Namespace(
                stop_file="STOP",
                config_path="",
                run_dir="",
                resume_latest=False,
            ),
            runner_mode="thread",
        )

        with patch("agent_runner.remote.controller.run_runner", return_value=0):
            first = controller.start()
            if controller._runner_thread is not None:
                controller._runner_thread.join(timeout=2)
            first_run_dir = Path(str(first.get("run_dir") or "")).resolve()
            self.assertTrue(first_run_dir.exists())

            second = controller.start()
            if controller._runner_thread is not None:
                controller._runner_thread.join(timeout=2)
            second_run_dir = Path(str(second.get("run_dir") or "")).resolve()
            self.assertTrue(second_run_dir.exists())

            resumed = controller.start({"resume_latest": True})
            if controller._runner_thread is not None:
                controller._runner_thread.join(timeout=2)
            resumed_run_dir = Path(str(resumed.get("run_dir") or "")).resolve()

            explicit_run_dir = repo / ".AgentCLI" / "agent_runs" / "explicit-run"
            explicit_1 = controller.start({"run_dir": explicit_run_dir.as_posix()})
            if controller._runner_thread is not None:
                controller._runner_thread.join(timeout=2)
            explicit_1_run_dir = Path(str(explicit_1.get("run_dir") or "")).resolve()

            explicit_2 = controller.start({"run_dir": explicit_run_dir.as_posix()})
            if controller._runner_thread is not None:
                controller._runner_thread.join(timeout=2)
            explicit_2_run_dir = Path(str(explicit_2.get("run_dir") or "")).resolve()

        self.assertNotEqual(first_run_dir, second_run_dir)
        self.assertEqual(second_run_dir, resumed_run_dir)
        self.assertEqual(explicit_run_dir.resolve(), explicit_1_run_dir)
        self.assertEqual(explicit_1_run_dir, explicit_2_run_dir)
        self.assertEqual(first_run_dir.as_posix(), str(first.get("run_dir") or ""))
        self.assertEqual(second_run_dir.as_posix(), str(second.get("run_dir") or ""))
        self.assertEqual(resumed_run_dir.as_posix(), str(resumed.get("run_dir") or ""))
        self.assertEqual(explicit_1_run_dir.as_posix(), str(explicit_1.get("run_dir") or ""))
        self.assertEqual(explicit_2_run_dir.as_posix(), str(explicit_2.get("run_dir") or ""))

    def test_runner_start_option_path_policy_reports_run_and_config_root_errors(self) -> None:
        scratch = _scratch_dir("runner_start_path_policy")
        repo = scratch / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        run_root = repo / ".AgentCLI" / "agent_runs"
        config_root = scratch / "home" / "configs"
        config_root.mkdir(parents=True, exist_ok=True)
        approved_config = config_root / "agentcli.json"
        outside_config = scratch / "outside" / "agentcli.json"
        outside_run_dir = scratch / "runs" / "outside"

        _, error = normalize_runner_start_options(
            repo,
            {
                "config_path": outside_config.as_posix(),
                "run_dir": outside_run_dir.as_posix(),
            },
            base_args=argparse.Namespace(config_path=approved_config.as_posix(), config=approved_config.as_posix()),
            approved_run_root=run_root,
            approved_config_roots=[config_root],
        )

        self.assertIsNotNone(error)
        details = error["details"] if error is not None else {}
        errors = details["errors"]
        self.assertTrue(any(item["field"] == "run_dir" and item["code"] == "outside_run_root" for item in errors))
        self.assertTrue(any(item["field"] == "config_path" and item["code"] == "outside_config_root" for item in errors))


if __name__ == "__main__":
    unittest.main()
