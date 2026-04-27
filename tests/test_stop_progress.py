from __future__ import annotations

import argparse
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent_runner.remote.controller import RunnerController
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
            phase="waiting_runner",
            message="Waiting for runner shutdown.",
            requested_at_monotonic=requested_at,
            running=True,
        )

        self.assertEqual("waiting_runner", progress["phase"])
        self.assertTrue(stop_progress_is_active(progress))
        self.assertEqual("waiting_runner", read_stop_progress(run_dir)["phase"])
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
        self.assertIn("requested", phases)
        self.assertIn("stop_file_written", phases)
        self.assertEqual("finalized", read_stop_progress(run_dir)["phase"])

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
            with patch("agent_runner.remote.controller.close_all_loggers") as close_loggers:
                result = controller.stop(wait=True)
        finally:
            release.set()
            thread.join(timeout=2)

        self.assertFalse(result["ok"])
        self.assertTrue(result["running"])
        close_loggers.assert_not_called()
        progress = read_stop_progress(run_dir)
        self.assertEqual("timeout", progress["phase"])
        self.assertIn("1s stop wait timeout", progress["message"])

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


if __name__ == "__main__":
    unittest.main()
