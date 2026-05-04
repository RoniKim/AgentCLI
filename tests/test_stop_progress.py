from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent_runner.remote.controller import RunnerController, normalize_runner_start_options
from agent_runner.shell import RunnerShell
from agent_runner.stop_progress import (
    STOP_PROGRESS_FILE,
    STOP_PROGRESS_LOG_FILE,
    STOP_RECONCILIATION_FILE,
    STOP_RECONCILIATION_LOG_FILE,
    STOP_SNAPSHOT_FILE,
    STOP_SNAPSHOT_LOG_FILE,
    read_stop_snapshot,
    read_stop_progress,
    reconcile_stale_stop_files,
    record_stop_progress,
    stop_progress_is_active,
    write_stop_progress,
    write_stop_snapshot,
)


def _scratch_dir(name: str) -> Path:
    root = Path(__file__).resolve().parents[1] / ".test-scratch" / "unit-temp"
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return completed.stdout.strip()


def _prepare_start_ready_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git(repo, "init")
    _git(repo, "config", "user.email", "agentcli-tests@example.com")
    _git(repo, "config", "user.name", "AgentCLI Tests")
    (repo / "README.md").write_text("ready\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "base")
    python_rel = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    python_path = repo / ".venv" / python_rel
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_text("", encoding="utf-8")


def _set_file_age(path: Path, age_seconds: int) -> None:
    old = time.time() - max(0, int(age_seconds))
    os.utime(path, (old, old))


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

    def test_record_stop_progress_repairs_partial_existing_payload(self) -> None:
        run_dir = _scratch_dir("stop_progress_partial")
        (run_dir / STOP_PROGRESS_FILE).write_text(
            json.dumps(
                {
                    "phase": "requested",
                    "currentPhase": {"message": "Stop requested.", "updatedAt": "2026-05-04T00:00:00"},
                    "history": [{"message": "Stop requested."}],
                    "runnerAlive": "true",
                    "stopFilePaths": {"stop_file_path": "C:\\temp\\STOP"},
                    "timeoutGuidance": {"manualCleanupHints": "Close the runner", "canRetry": "yes"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        record_stop_progress(
            run_dir,
            phase="waiting_runner",
            message="Waiting for runner shutdown and final artifacts.",
            requested_at_monotonic=max(0.0, time.monotonic() - 2.0),
            context_fields={
                "runner_alive": True,
                "stop_file_paths": {
                    "stop_file_path": "C:/temp/STOP",
                    "stop_progress_path": "C:/temp/STOP_PROGRESS.json",
                },
                "timeout_guidance": {
                    "summary": "Retry stop after checking the runner.",
                    "can_retry": True,
                    "manual_cleanup_hints": ["Close the runner"],
                },
            },
        )

        progress = read_stop_progress(run_dir)
        self.assertEqual("runner_wait", progress["phase"])
        self.assertEqual(["request", "runner_wait"], [entry["phase"] for entry in progress["history"]])
        self.assertTrue(progress["runner_alive"])
        self.assertEqual("C:/temp/STOP", progress["stop_file_paths"]["stop_file_path"])
        self.assertTrue(progress["timeout_guidance"]["can_retry"])
        self.assertEqual(["Close the runner"], progress["manual_cleanup_hints"])

    def test_shell_stop_emits_shared_progress_payloads(self) -> None:
        repo = _scratch_dir("shell_stop") / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        run_dir = repo / ".AgentCLI" / "agent_runs" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        release = threading.Event()
        shell = RunnerShell()
        shell.set_repo(repo.as_posix())
        shell.run_dir = run_dir
        shell._runner_thread = threading.Thread(target=lambda: release.wait(5), daemon=True)
        shell._runner_thread.start()

        try:
            with patch("agent_runner.shell.terminate_all_children") as terminate_children, patch("builtins.print"):
                shell.stop(wait=False)
        finally:
            release.set()
            if shell._runner_thread is not None:
                shell._runner_thread.join(timeout=2)

        progress = read_stop_progress(run_dir)
        self.assertEqual("child_termination", progress["phase"])
        self.assertEqual(
            ["request", "stop_file_write", "child_termination"],
            [entry["phase"] for entry in progress["history"]],
        )
        self.assertTrue((run_dir / "STOP").exists())
        self.assertEqual((run_dir / STOP_PROGRESS_FILE).as_posix(), progress["stop_file_paths"]["stop_progress_path"])
        terminate_children.assert_called()

    def test_write_stop_snapshot_preserves_backend_stop_schema(self) -> None:
        run_dir = _scratch_dir("stop_snapshot")
        (run_dir / "STOP").write_text("stop_file\n", encoding="utf-8")
        (run_dir / "progress.txt").write_text("done=1/3 skipped=0 last=T34\n", encoding="utf-8")
        (run_dir / "BACKLOG.json").write_text(
            json.dumps(
                [
                    {"id": "T34", "title": "Share stop progress"},
                    {"id": "T35", "title": "Next task"},
                ],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "STATE.json").write_text(
            json.dumps({"done": ["T34"], "failed": [{"task": "T35"}], "warnings": [{"task": "T35"}]}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

        snapshot = write_stop_snapshot(
            run_dir,
            stage="build_gate",
            cycle=3,
            step=1,
            task_id="T34",
            attempt=2,
            message="Stop requested; partial task artifacts and worktree state were preserved.",
        )

        self.assertEqual("stop_file", snapshot["reason"])
        self.assertEqual("build_gate", snapshot["stage"])
        self.assertEqual(3, snapshot["cycle"])
        self.assertEqual(1, snapshot["step"])
        self.assertEqual("T34", snapshot["task_id"])
        self.assertEqual(2, snapshot["attempt"])
        self.assertEqual("done=1/3 skipped=0 last=T34", snapshot["progress"])
        self.assertEqual({"done": 1, "failed": 1, "warnings": 1}, snapshot["state_counts"])
        self.assertTrue((run_dir / STOP_SNAPSHOT_FILE).exists())
        self.assertTrue((run_dir / STOP_SNAPSHOT_LOG_FILE).exists())
        self.assertEqual(snapshot, read_stop_snapshot(run_dir))

    def test_reconcile_stale_stop_with_old_heartbeat_deletes_and_audits(self) -> None:
        run_dir = _scratch_dir("stale_stop_reconcile")
        stop_path = run_dir / "STOP"
        heartbeat_path = run_dir / "HEARTBEAT"
        stop_path.write_text("stop_file\n", encoding="utf-8")
        heartbeat_path.write_text("2026-05-04T00:00:00\n", encoding="utf-8")
        write_stop_progress(
            run_dir,
            phase="runner_wait",
            message="Waiting for runner shutdown and final artifacts.",
            requested_at_monotonic=0.0,
            running=True,
            runner_alive=True,
        )
        _set_file_age(stop_path, 3600)
        _set_file_age(heartbeat_path, 3600)

        result = reconcile_stale_stop_files(
            run_dir,
            stop_stale_age_seconds=60,
            heartbeat_stale_age_seconds=60,
            source="test",
        )

        self.assertTrue(result["ok"])
        self.assertEqual("reconcile_stale_stop", result["decision"])
        self.assertEqual("deleted_stop_files", result["action_taken"])
        self.assertFalse(stop_path.exists())
        self.assertFalse((run_dir / STOP_PROGRESS_FILE).exists())
        audit = json.loads((run_dir / STOP_RECONCILIATION_FILE).read_text(encoding="utf-8"))
        self.assertEqual(stop_path.resolve().as_posix(), audit["stop_path"])
        self.assertEqual(heartbeat_path.resolve().as_posix(), audit["heartbeat_path"])
        self.assertEqual("stale", audit["heartbeat_state"])
        self.assertEqual("reconcile_stale_stop", audit["decision"])
        self.assertTrue((run_dir / STOP_RECONCILIATION_LOG_FILE).exists())

    def test_reconcile_fresh_stop_preserves_and_blocks_start(self) -> None:
        from agent_runner import runner_entry

        repo = _scratch_dir("fresh_stop_direct") / "repo"
        repo.mkdir(parents=True, exist_ok=True)
        run_dir = repo / ".AgentCLI" / "agent_runs" / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        stop_path = run_dir / "STOP"
        heartbeat_path = run_dir / "HEARTBEAT"
        stop_path.write_text("stop_file\n", encoding="utf-8")
        heartbeat_path.write_text("2026-05-04T00:00:00\n", encoding="utf-8")
        _set_file_age(heartbeat_path, 3600)

        helper_result = reconcile_stale_stop_files(
            run_dir,
            stop_stale_age_seconds=60,
            heartbeat_stale_age_seconds=60,
            source="test",
        )

        self.assertFalse(helper_result["ok"])
        self.assertEqual("block_fresh_stop", helper_result["decision"])
        self.assertEqual("preserved_stop_files", helper_result["action_taken"])
        self.assertTrue(stop_path.exists())

        args = argparse.Namespace(
            repo=repo.as_posix(),
            run_dir=run_dir.as_posix(),
            resume_latest=False,
            stop_file="STOP",
            execution_backend="codex",
            failover_enabled=False,
            stale_stop_reconcile_stop_age_seconds=60,
            stale_stop_reconcile_heartbeat_age_seconds=60,
            stale_stop_reconcile_allow_missing_heartbeat=False,
        )
        with patch.object(runner_entry, "_main_async_dispatch", side_effect=AssertionError("runner should not start")):
            rc = runner_entry.run(args)

        self.assertEqual(130, rc)
        self.assertTrue(stop_path.exists())
        audit = json.loads((run_dir / STOP_RECONCILIATION_FILE).read_text(encoding="utf-8"))
        self.assertEqual("block_fresh_stop", audit["decision"])

    def test_reconcile_missing_heartbeat_preserves_unless_explicit(self) -> None:
        run_dir = _scratch_dir("missing_heartbeat_reconcile")
        stop_path = run_dir / "STOP"
        stop_path.write_text("stop_file\n", encoding="utf-8")
        _set_file_age(stop_path, 3600)

        preserved = reconcile_stale_stop_files(
            run_dir,
            stop_stale_age_seconds=60,
            heartbeat_stale_age_seconds=60,
            source="test",
        )

        self.assertFalse(preserved["ok"])
        self.assertEqual("block_missing_heartbeat", preserved["decision"])
        self.assertEqual("preserved_stop_files", preserved["action_taken"])
        self.assertTrue(stop_path.exists())

        explicit = reconcile_stale_stop_files(
            run_dir,
            stop_stale_age_seconds=60,
            heartbeat_stale_age_seconds=60,
            allow_missing_heartbeat=True,
            source="test",
        )

        self.assertTrue(explicit["ok"])
        self.assertEqual("reconcile_missing_heartbeat_explicit", explicit["decision"])
        self.assertFalse(stop_path.exists())

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

    def test_controller_resume_reconciles_stale_stop_before_start(self) -> None:
        repo = _scratch_dir("controller_resume_stale_stop") / "repo"
        _prepare_start_ready_repo(repo)
        run_dir = repo / ".AgentCLI" / "agent_runs" / "20260504-000000"
        run_dir.mkdir(parents=True, exist_ok=True)
        stop_path = run_dir / "STOP"
        heartbeat_path = run_dir / "HEARTBEAT"
        stop_path.write_text("stop_file\n", encoding="utf-8")
        heartbeat_path.write_text("2026-05-04T00:00:00\n", encoding="utf-8")
        _set_file_age(stop_path, 3600)
        _set_file_age(heartbeat_path, 3600)
        controller = RunnerController(
            repo=repo,
            base_args=argparse.Namespace(
                stop_file="STOP",
                config_path="",
                run_dir="",
                resume_latest=False,
                stale_stop_reconcile_stop_age_seconds=60,
                stale_stop_reconcile_heartbeat_age_seconds=60,
                stale_stop_reconcile_allow_missing_heartbeat=False,
            ),
            runner_mode="thread",
        )

        with patch("agent_runner.remote.controller.run_runner", return_value=0):
            result = controller.start({"resume_latest": True})
            if controller._runner_thread is not None:
                controller._runner_thread.join(timeout=2)

        self.assertTrue(result["ok"])
        self.assertEqual(run_dir.resolve(), Path(str(result.get("run_dir") or "")).resolve())
        self.assertFalse(stop_path.exists())
        audit = json.loads((run_dir / STOP_RECONCILIATION_FILE).read_text(encoding="utf-8"))
        self.assertEqual("reconcile_stale_stop", audit["decision"])

    def test_controller_start_creates_fresh_run_dir_by_default_and_reuses_only_when_explicit(self) -> None:
        repo = _scratch_dir("controller_start") / "repo"
        _prepare_start_ready_repo(repo)
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
