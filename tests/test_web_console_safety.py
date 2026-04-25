from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


class FakeRunnerController:
    def __init__(self, *, repo: Path, base_args, runner_mode: str = "thread") -> None:
        self.repo = repo.expanduser().resolve()
        self.base_args = base_args
        self.runner_mode = runner_mode
        self._running = False
        self.run_dir: Path | None = None
        self.start_calls = 0
        self.stop_calls: list[bool] = []
        self.status_calls = 0

    def _status_payload(self) -> dict[str, object]:
        run_dir = self.run_dir or (self.repo / ".AgentCLI" / "agent_runs" / "run_20260426_010000")
        return {
            "running": self._running,
            "runner_mode": self.runner_mode,
            "repo": str(self.repo),
            "run_dir": str(run_dir if self.run_dir else ""),
            "uptime_seconds": 18 if self._running else 0,
            "exit_code": None,
            "stop_file": "STOP",
            "stop_file_exists": bool(self.run_dir and not self._running),
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "reason": "",
            "last_event": "2026-04-26T01:00:00 cycle_end" if self._running else "",
        }

    def status(self) -> dict[str, object]:
        self.status_calls += 1
        return self._status_payload()

    def start(self, overrides=None) -> dict[str, object]:
        self.start_calls += 1
        if self._running:
            return {"ok": False, "message": "Runner is already running."}
        self._running = True
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "run_20260426_010000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        return {
            "ok": True,
            "message": "Runner started.",
            "runner_mode": self.runner_mode,
            "run_dir": str(self.run_dir),
        }

    def stop(self, *, wait: bool = False) -> dict[str, object]:
        self.stop_calls.append(bool(wait))
        if self.run_dir is None:
            self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "run_20260426_010000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._running = False
        return {
            "ok": True,
            "message": "Runner stopped.",
            "running": False,
            "run_dir": str(self.run_dir),
        }


def _create_client(repo: Path, *, enable_runner_controls: bool | None):
    from fastapi.testclient import TestClient
    import agent_runner.web as web_module

    with patch.object(web_module, "RunnerController", FakeRunnerController):
        app = web_module.create_app(repo, web_dir=WEB_CONSOLE, enable_runner_controls=enable_runner_controls)
    return TestClient(app), app


class WebConsoleSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            import fastapi  # noqa: F401
        except Exception as exc:
            raise unittest.SkipTest(f"FastAPI is unavailable: {exc}") from exc

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)

        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self._old_home = os.environ.get("AGENTCLI_HOME")
        os.environ["AGENTCLI_HOME"] = str(self.home)
        self.addCleanup(self._restore_home)

        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260426-120000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "logs").mkdir(parents=True, exist_ok=True)

        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

## P0
- [x] Expose read-only progress views
- [ ] Add FastAPI web console
""",
        )
        _write(
            self.run_dir / "BACKLOG.json",
            json.dumps(
                {
                    "generated_at": "2026-04-26T12:00:00",
                    "tasks": [
                        {
                            "id": "T1",
                            "title": "Expose read-only progress views",
                            "prompt": "Implement the read-only web status views.",
                            "files": ["agent_runner/web.py"],
                            "done_when": "Endpoint returns current run progress.",
                            "skills": [],
                            "skills_rationale": None,
                            "depends_on": [],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            self.run_dir / "STATE.json",
            json.dumps({"done": ["T1"], "failed": [], "warnings": []}, ensure_ascii=False, indent=2) + "\n",
        )
        _write(
            self.run_dir / "metrics.jsonl",
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:00",
                            "seq": 1,
                            "level": "info",
                            "event": "cycle_start",
                            "stage": "PM",
                            "message": "cycle start",
                        },
                        ensure_ascii=False,
                    ),
                    "",
                ]
            ),
        )
        _write(
            self.run_dir / "run_summary.json",
            json.dumps(
                {
                    "run_id": self.run_dir.name,
                    "repo": str(self.repo),
                    "final": {"rc": 0, "reason": "project_complete"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(self.run_dir / "cycle_summary.log", "2026-04-26T12:00:00 cycle=1 done=1/1 failed=0 dt=60.0s\n")
        _write(self.run_dir / "logs" / "run.log", "2026-04-26 12:00:00 [INFO] cycle started\n")

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self._old_home

    def test_runner_controls_are_disabled_until_opt_in(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=False)

        status = client.get("/api/status")
        self.assertEqual(200, status.status_code)
        status_payload = status.json()
        self.assertIn("runner_control", status_payload)
        self.assertFalse(status_payload["runner_control"]["enabled"])
        self.assertFalse(status_payload["runner_control"]["actions"]["start"]["enabled"])

        control_status = client.get("/api/runner/status")
        self.assertEqual(200, control_status.status_code)
        control_payload = control_status.json()
        self.assertFalse(control_payload["enabled"])
        self.assertTrue(control_payload["controller_available"])
        self.assertEqual("cli", control_payload["source"])
        self.assertFalse(control_payload["busy"])
        self.assertEqual("success", control_payload["run_status"])
        self.assertFalse(control_payload["runner_control"]["actions"]["reload"]["enabled"])
        self.assertFalse(control_payload["runner_control"]["actions"]["restart"]["enabled"])
        self.assertEqual("RESTART RUNNER", control_payload["confirmation"]["restart"])

        response = client.post("/api/runner/start", json={"phrase": "START RUNNER"})
        self.assertEqual(403, response.status_code)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual("runner_controls_disabled", body["error"]["code"])
        self.assertFalse(body["runner_control"]["enabled"])
        self.assertFalse(app.state.runner_controller.start_calls)

    def test_confirmation_is_required_when_controls_are_enabled(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=True)

        response = client.post("/api/runner/start", json={})
        self.assertEqual(400, response.status_code)
        body = response.json()
        self.assertFalse(body["ok"])
        self.assertEqual("confirmation_required", body["error"]["code"])
        self.assertEqual("START RUNNER", body["error"]["details"]["expected"])
        self.assertEqual(0, app.state.runner_controller.start_calls)

        mismatch = client.post("/api/runner/start", json={"token": "WRONG"})
        self.assertEqual(400, mismatch.status_code)
        mismatch_body = mismatch.json()
        self.assertFalse(mismatch_body["ok"])
        self.assertEqual("confirmation_mismatch", mismatch_body["error"]["code"])
        self.assertEqual(0, app.state.runner_controller.start_calls)

    def test_start_reload_restart_and_stop_round_trip_through_controller(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=True)
        controller = app.state.runner_controller

        control_status = client.get("/api/runner/status").json()
        self.assertTrue(control_status["enabled"])
        self.assertTrue(control_status["runner_control"]["actions"]["restart"]["enabled"])

        start_response = client.post("/api/runner/start", json={"phrase": "START RUNNER"})
        self.assertEqual(200, start_response.status_code)
        start_body = start_response.json()
        self.assertTrue(start_body["ok"])
        self.assertEqual("start", start_body["action"])
        self.assertTrue(start_body["snapshot"]["runner_control"]["status"]["running"])
        self.assertEqual(1, controller.start_calls)
        self.assertEqual([], controller.stop_calls)

        reload_response = client.post("/api/runner/reload", json={"token": "RELOAD RUNNER"})
        self.assertEqual(200, reload_response.status_code)
        reload_body = reload_response.json()
        self.assertTrue(reload_body["ok"])
        self.assertEqual("reload", reload_body["action"])
        self.assertEqual(1, controller.stop_calls.count(False))
        self.assertEqual(2, controller.start_calls)
        self.assertTrue(reload_body["snapshot"]["runner_control"]["status"]["running"])

        restart_response = client.post("/api/runner/restart", json={"confirm": "RESTART RUNNER"})
        self.assertEqual(200, restart_response.status_code)
        restart_body = restart_response.json()
        self.assertTrue(restart_body["ok"])
        self.assertEqual("restart", restart_body["action"])
        self.assertEqual(2, controller.stop_calls.count(False))
        self.assertEqual(3, controller.start_calls)
        self.assertTrue(restart_body["snapshot"]["runner_control"]["status"]["running"])

        stop_response = client.post("/api/runner/stop", json={"confirm": "STOP RUNNER"})
        self.assertEqual(200, stop_response.status_code)
        stop_body = stop_response.json()
        self.assertTrue(stop_body["ok"])
        self.assertEqual("stop", stop_body["action"])
        self.assertEqual([False, False, True], controller.stop_calls)
        self.assertFalse(stop_body["snapshot"]["runner_control"]["status"]["running"])


if __name__ == "__main__":
    unittest.main()
