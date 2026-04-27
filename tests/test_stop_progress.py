from __future__ import annotations

import argparse
import time
import unittest
import uuid
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
