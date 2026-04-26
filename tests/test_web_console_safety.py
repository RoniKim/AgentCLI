from __future__ import annotations

import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _write_config(path: Path, repo: Path, **overrides: object) -> None:
    payload = {
        "repo": repo.as_posix(),
        "profile": "personal",
        "execution_backend": "codex",
        "roles": ["PM", "Dev", "QA"],
        "iterations": 2,
        "prompts_dir": "prompts/agentcli",
        "goals_completion_level": "all",
        "telegram": {
            "enabled": True,
            "bot_token": "secret-token",
            "pairing_code": "pairing-code",
        },
    }
    for key, value in overrides.items():
        if key == "telegram" and isinstance(value, dict):
            payload.setdefault("telegram", {}).update(value)
        else:
            payload[key] = value
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


GOALS_SAVE_CONFIRMATION_PHRASE = "DELETE OR DOWNGRADE UNMET P0 GOALS"


class FakeRunnerController:
    def __init__(
        self,
        *,
        repo: Path,
        base_args,
        runner_mode: str = "thread",
        status_error: str | None = None,
        start_error: str | None = None,
        stop_error: str | None = None,
    ) -> None:
        self.repo = repo.expanduser().resolve()
        self.base_args = base_args
        self.runner_mode = runner_mode
        self.status_error = status_error
        self.start_error = start_error
        self.stop_error = stop_error
        self._running = False
        self.run_dir: Path | None = None
        self.start_calls = 0
        self.stop_calls: list[bool] = []
        self.status_calls = 0
        self.start_overrides: list[dict[str, object]] = []

    def _status_payload(self) -> dict[str, object]:
        run_dir = self.run_dir or (self.repo / ".AgentCLI" / "agent_runs" / "run_20260426_010000")
        return {
            "running": self._running,
            "runner_mode": self.runner_mode,
            "repo": str(self.repo),
            "config_path": str(getattr(self.base_args, "config_path", getattr(self.base_args, "config", "")) or ""),
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
        if self.status_error:
            raise RuntimeError(self.status_error)
        return self._status_payload()

    def start(self, overrides=None) -> dict[str, object]:
        self.start_calls += 1
        self.start_overrides.append(dict(overrides or {}))
        if self.start_error:
            return {"ok": False, "message": self.start_error}
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
            "repo": str(self.repo),
            "config_path": str(getattr(self.base_args, "config_path", getattr(self.base_args, "config", "")) or ""),
        }

    def stop(self, *, wait: bool = False) -> dict[str, object]:
        self.stop_calls.append(bool(wait))
        if self.stop_error:
            return {"ok": False, "message": self.stop_error}
        if self.run_dir is None:
            self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "run_20260426_010000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._running = False
        return {
            "ok": True,
            "message": "Runner stopped.",
            "running": False,
            "run_dir": str(self.run_dir),
            "repo": str(self.repo),
            "config_path": str(getattr(self.base_args, "config_path", getattr(self.base_args, "config", "")) or ""),
        }


def _create_client(
    repo: Path,
    *,
    enable_runner_controls: bool | None,
    config_path: Path | None = None,
    host: str = "127.0.0.1",
    trusted_network: bool | None = None,
    runner_controller: object | None = None,
):
    from fastapi.testclient import TestClient
    import agent_runner.web as web_module

    with patch.object(web_module, "RunnerController", FakeRunnerController):
        kwargs = {
            "web_dir": WEB_CONSOLE,
            "enable_runner_controls": enable_runner_controls,
            "bind_host": host,
            "trusted_network": trusted_network,
        }
        if config_path is not None:
            kwargs["config_path"] = str(config_path)
        if runner_controller is not None:
            with patch.object(web_module, "_build_runner_controller", return_value=runner_controller):
                app = web_module.create_app(repo, **kwargs)
        else:
            app = web_module.create_app(repo, **kwargs)
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
        self._tmp_root = ROOT / ".test-scratch"
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        self._tmp = self._tmp_root / f"{self._testMethodName}_{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)

        self.home = self._tmp / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self._old_home = os.environ.get("AGENTCLI_HOME")
        os.environ["AGENTCLI_HOME"] = str(self.home)
        self.addCleanup(self._restore_home)

        self.config_path = self.home / "configs" / "agentcli.json"
        _write(
            self.config_path,
            json.dumps(
                {
                    "repo": self.repo.as_posix(),
                    "profile": "personal",
                    "execution_backend": "codex",
                    "iterations": 4,
                    "prompts_dir": "prompts/agentcli",
                    "telegram": {
                        "enabled": False,
                        "runner_mode": "thread",
                        "bot_token": "initial-bot-token",
                        "pairing_code": "PAIR-0001",
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260426-120000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.worktree_dir = self._tmp / "worktree"
        self.patch_path = self.run_dir / "worktree.patch"
        self.pending_path = self.run_dir / "WORKTREE_MERGE_PENDING.json"
        (self.run_dir / "logs").mkdir(parents=True, exist_ok=True)

        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

## P0
- [x] Expose read-only progress views
- [ ] Add FastAPI web console
""",
        )
        self.goals_path = self.repo / ".doc" / "GOALS.md"
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

    def _goal_item(self, text: str, *, done: bool = False, note: str = "") -> dict[str, object]:
        return {
            "done": done,
            "checked": done,
            "checkbox": "[x]" if done else "[ ]",
            "text": text,
            "note": note,
        }

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
        return completed.stdout.strip()

    def _prepare_pending_worktree(self, *, source_text: str = 'print("before")\n', updated_text: str = 'print("after")\n') -> dict[str, object]:
        from agent_runner.gitops import handle_worktree_patch

        source_file = self.repo / "src" / "app.py"
        source_file.parent.mkdir(parents=True, exist_ok=True)
        _write(source_file, source_text)

        self._git("init")
        self._git("config", "user.email", "agentcli-tests@example.com")
        self._git("config", "user.name", "AgentCLI Tests")
        self._git("add", "src/app.py")
        self._git("commit", "-m", "base")
        self._git("worktree", "add", "--detach", "--force", self.worktree_dir.as_posix(), "HEAD")

        worktree_file = self.worktree_dir / "src" / "app.py"
        worktree_file.parent.mkdir(parents=True, exist_ok=True)
        _write(worktree_file, updated_text)

        handle_worktree_patch(self.worktree_dir, self.repo, self.run_dir, 0, auto_apply=False)

        return {
            "source_file": source_file,
            "source_text": source_text,
            "updated_text": updated_text,
            "commit": self._git("rev-parse", "HEAD"),
        }

    def _worktree_action_payload(self, worktree: dict[str, object], *, confirmation: str) -> dict[str, object]:
        return {
            "confirmation": confirmation,
            "sourceRepo": worktree["sourceRepo"],
            "runDir": worktree["runDir"],
            "worktreeDir": worktree["worktreeDir"],
            "patchPath": worktree["patchPath"],
            "pendingFile": worktree["pendingFile"],
            "statusFile": worktree["statusFile"],
            "baseRef": worktree["baseRef"],
            "headRef": worktree["headRef"],
            "cleanupPath": worktree["cleanupPath"],
        }

    def test_runner_controls_are_disabled_until_opt_in(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)

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
        self.assertEqual(self.config_path.as_posix(), control_payload["status"]["config_path"])
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
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        expected_confirmations = {
            "start": "START RUNNER",
            "stop": "STOP RUNNER",
            "reload": "RELOAD RUNNER",
            "restart": "RESTART RUNNER",
        }
        for action, expected in expected_confirmations.items():
            with self.subTest(action=action, case="required"):
                response = client.post(f"/api/runner/{action}", json={})
                self.assertEqual(400, response.status_code)
                body = response.json()
                self.assertFalse(body["ok"])
                self.assertEqual("confirmation_required", body["error"]["code"])
                self.assertEqual(expected, body["error"]["details"]["expected"])

            with self.subTest(action=action, case="mismatch"):
                mismatch = client.post(f"/api/runner/{action}", json={"confirmation": "WRONG"})
                self.assertEqual(400, mismatch.status_code)
                mismatch_body = mismatch.json()
                self.assertFalse(mismatch_body["ok"])
                self.assertEqual("confirmation_mismatch", mismatch_body["error"]["code"])
                self.assertEqual(expected, mismatch_body["error"]["details"]["expected"])

        self.assertEqual(0, app.state.runner_controller.start_calls)
        self.assertEqual([], app.state.runner_controller.stop_calls)

    def test_start_reload_restart_and_stop_round_trip_through_controller(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        controller = app.state.runner_controller

        control_status = client.get("/api/runner/status").json()
        self.assertTrue(control_status["enabled"])
        self.assertTrue(control_status["runner_control"]["actions"]["restart"]["enabled"])
        self.assertEqual(self.config_path.as_posix(), control_status["runner_control"]["status"]["config_path"])

        start_response = client.post("/api/runner/start", json={"phrase": "START RUNNER"})
        self.assertEqual(200, start_response.status_code)
        start_body = start_response.json()
        self.assertTrue(start_body["ok"])
        self.assertEqual("start", start_body["action"])
        self.assertEqual("started", start_body["status"])
        self.assertTrue(start_body["snapshot"]["runner_control"]["status"]["running"])
        self.assertEqual(self.config_path.as_posix(), start_body["snapshot"]["runner_control"]["status"]["config_path"])
        self.assertEqual(1, controller.start_calls)
        self.assertEqual([], controller.stop_calls)
        self.assertEqual(self.repo.as_posix(), controller.start_overrides[0]["repo"])
        self.assertEqual(self.config_path.as_posix(), controller.start_overrides[0]["config_path"])
        self.assertEqual(self.config_path.as_posix(), controller.start_overrides[0]["config"])

        reload_response = client.post("/api/runner/reload", json={"token": "RELOAD RUNNER"})
        self.assertEqual(200, reload_response.status_code)
        reload_body = reload_response.json()
        self.assertTrue(reload_body["ok"])
        self.assertEqual("reload", reload_body["action"])
        self.assertEqual("reloaded", reload_body["status"])
        self.assertEqual(1, controller.stop_calls.count(False))
        self.assertEqual(2, controller.start_calls)
        self.assertEqual(self.repo.as_posix(), controller.start_overrides[1]["repo"])
        self.assertEqual(self.config_path.as_posix(), controller.start_overrides[1]["config_path"])
        self.assertTrue(reload_body["snapshot"]["runner_control"]["status"]["running"])
        self.assertEqual(self.config_path.as_posix(), reload_body["snapshot"]["runner_control"]["status"]["config_path"])

        restart_response = client.post("/api/runner/restart", json={"confirm": "RESTART RUNNER"})
        self.assertEqual(200, restart_response.status_code)
        restart_body = restart_response.json()
        self.assertTrue(restart_body["ok"])
        self.assertEqual("restart", restart_body["action"])
        self.assertEqual("restarted", restart_body["status"])
        self.assertEqual(2, controller.stop_calls.count(False))
        self.assertEqual(3, controller.start_calls)
        self.assertEqual(self.repo.as_posix(), controller.start_overrides[2]["repo"])
        self.assertEqual(self.config_path.as_posix(), controller.start_overrides[2]["config_path"])
        self.assertTrue(restart_body["snapshot"]["runner_control"]["status"]["running"])
        self.assertEqual(self.config_path.as_posix(), restart_body["snapshot"]["runner_control"]["status"]["config_path"])

        stop_response = client.post("/api/runner/stop", json={"confirm": "STOP RUNNER"})
        self.assertEqual(200, stop_response.status_code)
        stop_body = stop_response.json()
        self.assertTrue(stop_body["ok"])
        self.assertEqual("stop", stop_body["action"])
        self.assertEqual("stopped", stop_body["status"])
        self.assertEqual([False, False, True], controller.stop_calls)
        self.assertFalse(stop_body["snapshot"]["runner_control"]["status"]["running"])
        self.assertEqual(self.config_path.as_posix(), stop_body["snapshot"]["runner_control"]["status"]["config_path"])
        self.assertEqual(3, len(controller.start_overrides))

    def test_runner_controls_require_trusted_network_on_non_loopback_bind(self) -> None:
        blocked_client, blocked_app = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            host="0.0.0.0",
        )

        blocked_status = blocked_client.get("/api/runner/status").json()
        self.assertFalse(blocked_status["enabled"])
        self.assertEqual("cli", blocked_status["source"])
        self.assertIn("0.0.0.0", blocked_status["message"])
        self.assertIn("trusted-network", blocked_status["message"])
        self.assertFalse(blocked_status["runner_control"]["actions"]["start"]["enabled"])
        self.assertFalse(blocked_status["runner_control"]["actions"]["restart"]["enabled"])

        blocked_response = blocked_client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
        self.assertEqual(403, blocked_response.status_code)
        blocked_body = blocked_response.json()
        self.assertFalse(blocked_body["ok"])
        self.assertEqual("runner_controls_disabled", blocked_body["error"]["code"])
        self.assertIn("trusted-network", blocked_body["error"]["details"]["reason"])
        self.assertFalse(blocked_app.state.runner_controller.start_calls)

        trusted_client, trusted_app = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            host="0.0.0.0",
            trusted_network=True,
        )

        trusted_status = trusted_client.get("/api/runner/status").json()
        self.assertTrue(trusted_status["enabled"])
        self.assertEqual("cli;cli:--trusted-network", trusted_status["source"])
        self.assertTrue(trusted_status["runner_control"]["actions"]["start"]["enabled"])
        self.assertEqual(self.config_path.as_posix(), trusted_status["runner_control"]["status"]["config_path"])

        trusted_response = trusted_client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
        self.assertEqual(200, trusted_response.status_code)
        trusted_body = trusted_response.json()
        self.assertTrue(trusted_body["ok"])
        self.assertEqual("started", trusted_body["status"])
        self.assertEqual(self.repo.as_posix(), trusted_app.state.runner_controller.start_overrides[0]["repo"])
        self.assertEqual(self.config_path.as_posix(), trusted_app.state.runner_controller.start_overrides[0]["config_path"])

    def test_runner_controller_errors_surface_distinct_api_failures(self) -> None:
        from types import SimpleNamespace

        status_controller = FakeRunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(),
            status_error="controller offline",
        )
        status_client, status_app = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            runner_controller=status_controller,
        )

        snapshot_payload = status_client.get("/api/status").json()
        self.assertEqual("error", snapshot_payload["sectionState"]["runnerControl"]["state"])
        self.assertEqual("status_error: controller offline", snapshot_payload["runner_control"]["message"])
        self.assertFalse(snapshot_payload["runner_control"]["actions"]["start"]["enabled"])
        self.assertEqual(self.config_path.as_posix(), snapshot_payload["runner_control"]["status"]["config_path"])

        status_payload = status_client.get("/api/runner/status").json()
        self.assertEqual("status_error: controller offline", status_payload["message"])
        self.assertFalse(status_payload["actions"]["start"]["enabled"])
        self.assertEqual(self.config_path.as_posix(), status_payload["status"]["config_path"])
        status_response = status_client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
        self.assertEqual(503, status_response.status_code)
        status_body = status_response.json()
        self.assertFalse(status_body["ok"])
        self.assertEqual("runner_controller_status_error", status_body["error"]["code"])
        self.assertEqual(0, status_app.state.runner_controller.start_calls)

        action_controller = FakeRunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(),
            start_error="start exploded",
            stop_error="stop exploded",
        )
        action_client, action_app = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            runner_controller=action_controller,
        )

        start_response = action_client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
        self.assertEqual(409, start_response.status_code)
        start_body = start_response.json()
        self.assertFalse(start_body["ok"])
        self.assertEqual("runner_start_failed", start_body["error"]["code"])
        self.assertIn("start exploded", start_body["message"])

        reload_response = action_client.post("/api/runner/reload", json={"confirmation": "RELOAD RUNNER"})
        self.assertEqual(409, reload_response.status_code)
        reload_body = reload_response.json()
        self.assertFalse(reload_body["ok"])
        self.assertEqual("runner_reload_failed", reload_body["error"]["code"])
        self.assertIn("start exploded", reload_body["message"])

        restart_response = action_client.post("/api/runner/restart", json={"confirmation": "RESTART RUNNER"})
        self.assertEqual(409, restart_response.status_code)
        restart_body = restart_response.json()
        self.assertFalse(restart_body["ok"])
        self.assertEqual("runner_restart_failed", restart_body["error"]["code"])
        self.assertIn("start exploded", restart_body["message"])

        stop_response = action_client.post("/api/runner/stop", json={"confirmation": "STOP RUNNER"})
        self.assertEqual(409, stop_response.status_code)
        stop_body = stop_response.json()
        self.assertFalse(stop_body["ok"])
        self.assertEqual("runner_stop_failed", stop_body["error"]["code"])
        self.assertIn("stop exploded", stop_body["message"])
        self.assertEqual(3, action_app.state.runner_controller.start_calls)
        self.assertEqual([True], action_app.state.runner_controller.stop_calls)

    def test_worktree_actions_require_opt_in_and_report_pending_state(self) -> None:
        from agent_runner.web import build_snapshot

        fixture = self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        worktree = snapshot["worktree"]
        self.assertEqual("pending review", worktree["status"])
        self.assertTrue(worktree["reviewRequired"])

        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)
        response = client.post("/api/worktree/merge", json=self._worktree_action_payload(worktree, confirmation="MERGE WORKTREE"))
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("worktree_actions_disabled", payload["error"]["code"])
        self.assertEqual(worktree["sourceRepo"], payload["worktree"]["sourceRepo"])
        self.assertEqual(fixture["source_text"], fixture["source_file"].read_text(encoding="utf-8"))
        self.assertTrue(self.pending_path.exists())
        self.assertEqual("pending", payload["worktree"]["cleanupState"])

    def test_worktree_actions_report_no_pending_state_when_marker_is_missing(self) -> None:
        from agent_runner.web import build_snapshot

        self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        worktree = snapshot["worktree"]

        if self.pending_path.exists():
            self.pending_path.unlink()
        central_pending = self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json"
        if central_pending.exists():
            central_pending.unlink()

        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        response = client.post("/api/worktree/merge", json=self._worktree_action_payload(worktree, confirmation="MERGE WORKTREE"))
        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("worktree_pending_not_found", payload["error"]["code"])
        self.assertEqual("No pending worktree merge is available.", payload["message"])

    def test_worktree_merge_applies_patch_without_committing(self) -> None:
        from agent_runner.web import build_snapshot

        fixture = self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        worktree = snapshot["worktree"]
        body = self._worktree_action_payload(worktree, confirmation="MERGE WORKTREE")
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        head_before = fixture["commit"]
        response = client.post("/api/worktree/merge", json=body)
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("merge", payload["action"])
        self.assertEqual("applied", payload["status"])
        self.assertEqual("applied", payload["result"]["status"])
        self.assertEqual(head_before, self._git("rev-parse", "HEAD"))
        self.assertEqual(fixture["updated_text"], fixture["source_file"].read_text(encoding="utf-8"))
        self.assertFalse(self.pending_path.exists())
        self.assertTrue((self.run_dir / "WORKTREE_MERGE_APPLIED.json").exists())
        self.assertFalse(self.worktree_dir.exists())

        retry = client.post("/api/worktree/merge", json=body)
        self.assertEqual(409, retry.status_code)
        self.assertEqual("worktree_pending_not_found", retry.json()["error"]["code"])

    def test_worktree_discard_removes_pending_state_without_touching_source_files(self) -> None:
        from agent_runner.web import build_snapshot

        fixture = self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        worktree = snapshot["worktree"]
        body = self._worktree_action_payload(worktree, confirmation="DISCARD WORKTREE")
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        head_before = fixture["commit"]
        response = client.post("/api/worktree/discard", json=body)
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("discard", payload["action"])
        self.assertEqual("discarded", payload["status"])
        self.assertEqual("discarded", payload["result"]["status"])
        self.assertEqual(head_before, self._git("rev-parse", "HEAD"))
        self.assertEqual(fixture["source_text"], fixture["source_file"].read_text(encoding="utf-8"))
        self.assertFalse(self.pending_path.exists())
        self.assertTrue((self.run_dir / "WORKTREE_MERGE_DISCARDED.json").exists())
        self.assertFalse(self.worktree_dir.exists())

        retry = client.post("/api/worktree/discard", json=body)
        self.assertEqual(409, retry.status_code)
        self.assertEqual("worktree_pending_not_found", retry.json()["error"]["code"])

    def test_worktree_cleanup_failure_stays_visible_after_discard(self) -> None:
        from agent_runner.web import build_snapshot

        fixture = self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        worktree = snapshot["worktree"]
        body = self._worktree_action_payload(worktree, confirmation="DISCARD WORKTREE")
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        with patch("agent_runner.gitops.remove_worktree", side_effect=RuntimeError("cleanup exploded")):
            response = client.post("/api/worktree/discard", json=body)

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("discard_cleanup_failed", payload["status"])
        self.assertEqual("failed", payload["worktree"]["cleanupState"])
        self.assertIn("cleanup exploded", payload["worktree"]["cleanupMessage"])
        self.assertIn("cleanup exploded", payload["result"]["cleanup_error"])
        self.assertEqual(fixture["source_text"], fixture["source_file"].read_text(encoding="utf-8"))
        self.assertEqual(fixture["commit"], self._git("rev-parse", "HEAD"))
        self.assertTrue(self.worktree_dir.exists())
        self.assertTrue((self.run_dir / "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json").exists())
        self.assertFalse(self.pending_path.exists())

    def test_worktree_actions_reject_bad_confirmation_and_containment_mismatches(self) -> None:
        from agent_runner.web import build_snapshot

        self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        worktree = snapshot["worktree"]
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        wrong_confirmation = client.post(
            "/api/worktree/merge",
            json=self._worktree_action_payload(worktree, confirmation="WRONG"),
        )
        self.assertEqual(400, wrong_confirmation.status_code)
        wrong_payload = wrong_confirmation.json()
        self.assertFalse(wrong_payload["ok"])
        self.assertEqual("confirmation_mismatch", wrong_payload["error"]["code"])
        self.assertEqual("MERGE WORKTREE", wrong_payload["error"]["details"]["expected"])

        mismatch_body = self._worktree_action_payload(worktree, confirmation="MERGE WORKTREE")
        mismatch_body["sourceRepo"] = (self.repo / "elsewhere").as_posix()
        source_mismatch = client.post("/api/worktree/merge", json=mismatch_body)
        self.assertEqual(400, source_mismatch.status_code)
        self.assertEqual("worktree_source_repo_mismatch", source_mismatch.json()["error"]["code"])

        pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
        pending["worktree_dir"] = self.repo.as_posix()
        pending_text = json.dumps(pending, ensure_ascii=False, indent=2) + "\n"
        self.pending_path.write_text(pending_text, encoding="utf-8")
        central_pending = self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json"
        central_pending.write_text(pending_text, encoding="utf-8")
        invalid_snapshot = build_snapshot(self.repo)
        invalid_body = self._worktree_action_payload(invalid_snapshot["worktree"], confirmation="MERGE WORKTREE")
        containment = client.post("/api/worktree/merge", json=invalid_body)
        self.assertEqual(400, containment.status_code)
        self.assertEqual("worktree_path_inside_source_repo", containment.json()["error"]["code"])

    def test_config_save_is_disabled_until_opt_in(self) -> None:
        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)

        before = self.config_path.read_text(encoding="utf-8")
        response = client.post("/api/config/save", json={"changes": [{"path": "iterations", "value": 4}]})
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("config_save_disabled", payload["error"]["code"])
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))
        backups = list(self.config_path.parent.glob(f"{self.config_path.stem}.*.bak{self.config_path.suffix}"))
        self.assertEqual([], backups)

    def test_config_save_rejects_invalid_values(self) -> None:
        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        before = self.config_path.read_text(encoding="utf-8")
        response = client.post("/api/config/save", json={"changes": [{"path": "iterations", "value": 0}]})
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("config_value_out_of_range", payload["error"]["code"])
        self.assertEqual("iterations", payload["error"]["details"]["path"])
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))
        backups = list(self.config_path.parent.glob(f"{self.config_path.stem}.*.bak{self.config_path.suffix}"))
        self.assertEqual([], backups)

    def test_config_save_creates_backup_and_updates_file(self) -> None:
        _write_config(self.config_path, self.repo, iterations=2, prompts_dir="prompts/agentcli")
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.config_path.read_text(encoding="utf-8")
        response = client.post(
            "/api/config/save",
            json={
                "changes": [
                    {"path": "iterations", "value": 4},
                    {"path": "prompts_dir", "value": "prompts/agentcli-updated"},
                    {"path": "telegram.runner_mode", "value": "subprocess"},
                ]
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(self.config_path.as_posix(), payload["config_path"])
        self.assertIn("backup_path", payload)
        self.assertIn("reload_required_paths", payload)
        self.assertIn("prompts_dir", payload["reload_required_paths"])
        self.assertIn("telegram.runner_mode", payload["reload_required_paths"])

        backup_path = Path(payload["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(original, backup_path.read_text(encoding="utf-8"))

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(4, saved["iterations"])
        self.assertEqual("prompts/agentcli-updated", saved["prompts_dir"])
        self.assertEqual("subprocess", saved["telegram"]["runner_mode"])

        controller = app.state.runner_controller
        self.assertEqual("subprocess", controller.runner_mode)
        self.assertEqual("subprocess", controller.base_args.telegram["runner_mode"])

    def test_config_save_rejects_unsafe_or_redacted_payloads(self) -> None:
        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        before = self.config_path.read_text(encoding="utf-8")
        unsafe = client.post("/api/config/save", json={"changes": [{"path": "repo", "value": (self.repo / "other").as_posix()}]})
        self.assertEqual(400, unsafe.status_code)
        unsafe_payload = unsafe.json()
        self.assertFalse(unsafe_payload["ok"])
        self.assertEqual("config_path_unsafe", unsafe_payload["error"]["code"])
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))

        redacted = client.post("/api/config/save", json={"changes": [{"path": "telegram.bot_token", "value": "[redacted]"}]})
        self.assertEqual(400, redacted.status_code)
        redacted_payload = redacted.json()
        self.assertFalse(redacted_payload["ok"])
        self.assertEqual("config_redacted_placeholder", redacted_payload["error"]["code"])
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))
        backups = list(self.config_path.parent.glob(f"{self.config_path.stem}.*.bak{self.config_path.suffix}"))
        self.assertEqual([], backups)

    def test_goals_save_is_disabled_until_opt_in(self) -> None:
        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)

        before = self.goals_path.read_text(encoding="utf-8")
        response = client.post(
            "/api/goals/save",
            json={
                "draft": {
                    "p0": [self._goal_item("Keep read-only progress views", done=False)],
                    "p1": [],
                }
            },
        )
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("goals_save_disabled", payload["error"]["code"])
        self.assertEqual(before, self.goals_path.read_text(encoding="utf-8"))
        backups = list(self.goals_path.parent.glob(f"{self.goals_path.stem}.*.bak{self.goals_path.suffix}"))
        self.assertEqual([], backups)

    def test_goals_save_creates_backup_and_updates_file(self) -> None:
        from agent_runner.web import _goal_save_serialize_draft

        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.goals_path.read_text(encoding="utf-8")
        draft = {
            "p0": [
                self._goal_item("Expose read-only progress views", done=True),
                self._goal_item("Add FastAPI web console", done=False),
            ],
            "p1": [
                self._goal_item("Surface the safety banner", done=False),
            ],
        }

        response = client.post("/api/goals/save", json={"draft": draft})
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("goals-save", payload["action"])
        self.assertEqual("saved", payload["status"])
        self.assertFalse(payload["risk"]["requires_confirmation"])
        self.assertEqual(0, payload["risk"]["risk_count"])
        self.assertEqual(GOALS_SAVE_CONFIRMATION_PHRASE, payload["risk"]["confirmationPhrase"])
        self.assertEqual(self.goals_path.as_posix(), payload["saved_path"])
        backup_path = Path(payload["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(original, backup_path.read_text(encoding="utf-8"))
        self.assertEqual(self.goals_path.read_text(encoding="utf-8"), payload["snapshot"]["goals"]["raw_text"])
        self.assertEqual(_goal_save_serialize_draft(draft), self.goals_path.read_text(encoding="utf-8"))
        backups = list(self.goals_path.parent.glob(f"{self.goals_path.stem}.*.bak{self.goals_path.suffix}"))
        self.assertEqual(1, len(backups))

    def test_goals_save_blocks_unconfirmed_p0_delete(self) -> None:
        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.goals_path.read_text(encoding="utf-8")
        draft = {
            "p0": [
                self._goal_item("Expose read-only progress views", done=True),
            ],
            "p1": [
                self._goal_item("Surface the safety banner", done=False),
            ],
        }

        response = client.post("/api/goals/save", json={"draft": draft})
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("goals_confirmation_required", payload["error"]["code"])
        self.assertEqual(original, self.goals_path.read_text(encoding="utf-8"))
        backups = list(self.goals_path.parent.glob(f"{self.goals_path.stem}.*.bak{self.goals_path.suffix}"))
        self.assertEqual([], backups)
        details = payload["error"]["details"]
        self.assertEqual(1, details["risk"]["risk_count"])
        self.assertEqual(1, len(details["risk"]["deleted_unchecked_p0"]))
        self.assertEqual("DELETE OR DOWNGRADE UNMET P0 GOALS", details["confirmation_phrase"])

    def test_goals_save_blocks_unconfirmed_p0_downgrade(self) -> None:
        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.goals_path.read_text(encoding="utf-8")
        draft = {
            "p0": [
                self._goal_item("Expose read-only progress views", done=True),
            ],
            "p1": [
                self._goal_item("Add FastAPI web console", done=False),
                self._goal_item("Surface the safety banner", done=False),
            ],
        }

        response = client.post("/api/goals/save", json={"draft": draft})
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("goals_confirmation_required", payload["error"]["code"])
        self.assertEqual(original, self.goals_path.read_text(encoding="utf-8"))
        backups = list(self.goals_path.parent.glob(f"{self.goals_path.stem}.*.bak{self.goals_path.suffix}"))
        self.assertEqual([], backups)
        details = payload["error"]["details"]
        self.assertEqual(1, details["risk"]["risk_count"])
        self.assertEqual(1, len(details["risk"]["downgraded_unchecked_p0"]))
        self.assertEqual("DELETE OR DOWNGRADE UNMET P0 GOALS", details["confirmation_phrase"])

    def test_goals_save_allows_confirmed_p0_delete(self) -> None:
        from agent_runner.web import GOALS_SAVE_CONFIRMATION_PHRASE, _goal_save_serialize_draft

        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.goals_path.read_text(encoding="utf-8")
        draft = {
            "p0": [
                self._goal_item("Expose read-only progress views", done=True),
            ],
            "p1": [
                self._goal_item("Surface the safety banner", done=False),
            ],
        }

        response = client.post(
            "/api/goals/save",
            json={
                "draft": draft,
                "confirm": GOALS_SAVE_CONFIRMATION_PHRASE,
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("goals-save", payload["action"])
        self.assertEqual("saved", payload["status"])
        self.assertTrue(payload["risk"]["requires_confirmation"])
        self.assertEqual(1, payload["risk"]["risk_count"])
        backup_path = Path(payload["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(original, backup_path.read_text(encoding="utf-8"))
        self.assertEqual(_goal_save_serialize_draft(draft), self.goals_path.read_text(encoding="utf-8"))
        self.assertEqual(self.goals_path.as_posix(), payload["saved_path"])

    def test_goals_save_allows_confirmed_p0_downgrade(self) -> None:
        from agent_runner.web import GOALS_SAVE_CONFIRMATION_PHRASE, _goal_save_serialize_draft

        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.goals_path.read_text(encoding="utf-8")
        draft = {
            "p0": [
                self._goal_item("Expose read-only progress views", done=True),
            ],
            "p1": [
                self._goal_item("Add FastAPI web console", done=False),
                self._goal_item("Surface the safety banner", done=False),
            ],
        }

        response = client.post(
            "/api/goals/save",
            json={
                "draft": draft,
                "confirm": GOALS_SAVE_CONFIRMATION_PHRASE,
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("goals-save", payload["action"])
        self.assertEqual("saved", payload["status"])
        self.assertTrue(payload["risk"]["requires_confirmation"])
        self.assertEqual(1, payload["risk"]["risk_count"])
        backup_path = Path(payload["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(original, backup_path.read_text(encoding="utf-8"))
        self.assertEqual(_goal_save_serialize_draft(draft), self.goals_path.read_text(encoding="utf-8"))
        self.assertEqual(self.goals_path.as_posix(), payload["saved_path"])

    def test_goals_save_rejects_malformed_required_priority_sections(self) -> None:
        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.goals_path.read_text(encoding="utf-8")
        cases = (
            ("missing-p1", "# Project Goals\n\n## P0\n- [ ] Keep read-only progress views\n"),
            ("missing-p0", "# Project Goals\n\n## P1\n- [ ] Surface the safety banner\n"),
        )

        for label, raw_text in cases:
            with self.subTest(label=label):
                response = client.post("/api/goals/save", json={"raw_text": raw_text})
                self.assertEqual(400, response.status_code)
                payload = response.json()
                self.assertFalse(payload["ok"])
                self.assertEqual("goals_sections_required", payload["error"]["code"])
                self.assertEqual(original, self.goals_path.read_text(encoding="utf-8"))
                backups = list(self.goals_path.parent.glob(f"{self.goals_path.stem}.*.bak{self.goals_path.suffix}"))
                self.assertEqual([], backups)

    def test_prompt_read_rejects_traversal_paths(self) -> None:
        config_path = self.config_path
        prompts_dir = self.home / "prompts" / "agentcli"
        _write_config(config_path, self.repo)
        _write(
            prompts_dir / "pm_instructions.md",
            "# Safety fixture\n\nProfile: {profile}\nRepo: {repo}\n",
        )

        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=config_path)
        response = client.get("/api/prompts/read", params={"id": "pm_instructions", "file": "../secrets.md"})
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("prompt_path_outside_prompts_dir", payload["error"]["code"])

        absolute = client.get(
            "/api/prompts/read",
            params={"id": "pm_instructions", "file": (self.home / "secrets.md").as_posix()},
        )
        self.assertEqual(400, absolute.status_code)
        self.assertEqual("prompt_path_outside_prompts_dir", absolute.json()["error"]["code"])

    def test_prompt_save_rejects_invalid_filenames_and_validation_failures(self) -> None:
        _write_config(self.config_path, self.repo)
        prompts_dir = self.home / "prompts" / "agentcli"
        prompt_path = prompts_dir / "pm_bootstrap_prompt.md"
        template_text = (ROOT / "templates" / "agent_prompts" / "pm_bootstrap_prompt.md").read_text(encoding="utf-8")
        _write(prompt_path, template_text)

        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        before = prompt_path.read_text(encoding="utf-8")
        invalid_name = client.post(
            "/api/prompts/save",
            json={
                "id": "pm_bootstrap",
                "file": "sub/../pm_bootstrap_prompt.md",
                "content": template_text,
            },
        )
        self.assertEqual(400, invalid_name.status_code)
        invalid_payload = invalid_name.json()
        self.assertFalse(invalid_payload["ok"])
        self.assertEqual("prompt_file_invalid", invalid_payload["error"]["code"])
        self.assertEqual(before, prompt_path.read_text(encoding="utf-8"))
        self.assertEqual([], list(prompts_dir.glob("pm_bootstrap_prompt.*.bak.md")))

        empty = client.post(
            "/api/prompts/save",
            json={"id": "pm_bootstrap", "file": "pm_bootstrap_prompt.md", "content": ""},
        )
        self.assertEqual(400, empty.status_code)
        empty_payload = empty.json()
        self.assertFalse(empty_payload["ok"])
        self.assertEqual("prompt_content_required", empty_payload["error"]["code"])
        self.assertEqual(before, prompt_path.read_text(encoding="utf-8"))
        self.assertEqual([], list(prompts_dir.glob("pm_bootstrap_prompt.*.bak.md")))

        missing = client.post(
            "/api/prompts/save",
            json={"id": "pm_bootstrap", "file": "pm_bootstrap_prompt.md", "content": "Repo: {repo}\n"},
        )
        self.assertEqual(400, missing.status_code)
        missing_payload = missing.json()
        self.assertFalse(missing_payload["ok"])
        self.assertEqual("prompt_template_variables_missing", missing_payload["error"]["code"])
        self.assertIn("analysis_md", missing_payload["error"]["details"]["validation"]["missing_variables"])
        self.assertEqual(before, prompt_path.read_text(encoding="utf-8"))
        self.assertEqual([], list(prompts_dir.glob("pm_bootstrap_prompt.*.bak.md")))

    def test_prompt_save_creates_backup_and_updates_prompt_atomically(self) -> None:
        _write_config(self.config_path, self.repo)
        prompts_dir = self.home / "prompts" / "agentcli"
        prompt_path = prompts_dir / "pm_bootstrap_prompt.md"
        original = (ROOT / "templates" / "agent_prompts" / "pm_bootstrap_prompt.md").read_text(encoding="utf-8")
        updated = original + "\n# local update\n"
        _write(prompt_path, original)

        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        response = client.post(
            "/api/prompts/save",
            json={
                "id": "pm_bootstrap",
                "file": "pm_bootstrap_prompt.md",
                "content": updated,
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("saved", payload["status"])
        self.assertEqual(prompt_path.as_posix(), payload["saved_path"])
        self.assertIn("backup_path", payload)

        backup_path = Path(payload["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(original, backup_path.read_text(encoding="utf-8"))
        self.assertEqual(updated, prompt_path.read_text(encoding="utf-8"))

        prompt_body = payload["prompt"]
        self.assertTrue(prompt_body["validation"]["ok"])
        self.assertEqual(updated, prompt_body["content"])
        self.assertEqual(1, len(list(prompts_dir.glob("pm_bootstrap_prompt.*.bak.md"))))

    def test_prompt_restore_rejects_traversal_and_restores_selected_backup(self) -> None:
        _write_config(self.config_path, self.repo)
        prompts_dir = self.home / "prompts" / "agentcli"
        prompt_path = prompts_dir / "pm_bootstrap_prompt.md"
        original = (ROOT / "templates" / "agent_prompts" / "pm_bootstrap_prompt.md").read_text(encoding="utf-8")
        updated = original + "\n# local update\n"
        drifted = updated + "\n# unsaved drift\n"
        _write(prompt_path, original)

        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        save_response = client.post(
            "/api/prompts/save",
            json={
                "id": "pm_bootstrap",
                "file": "pm_bootstrap_prompt.md",
                "content": updated,
            },
        )
        self.assertEqual(200, save_response.status_code)
        save_payload = save_response.json()
        backup_path = Path(save_payload["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(original, backup_path.read_text(encoding="utf-8"))

        _write(prompt_path, drifted)

        traversal = client.post(
            "/api/prompts/restore",
            json={
                "id": "pm_bootstrap",
                "file": "../secrets.md",
                "backup_path": backup_path.as_posix(),
                "confirm": "RESTORE BACKUP",
            },
        )
        self.assertEqual(400, traversal.status_code)
        self.assertEqual("prompt_path_outside_prompts_dir", traversal.json()["error"]["code"])

        invalid_name = client.post(
            "/api/prompts/restore",
            json={
                "id": "pm_bootstrap",
                "file": "sub/../pm_bootstrap_prompt.md",
                "backup_path": backup_path.as_posix(),
                "confirm": "RESTORE BACKUP",
            },
        )
        self.assertEqual(400, invalid_name.status_code)
        self.assertEqual("prompt_file_invalid", invalid_name.json()["error"]["code"])

        restore_response = client.post(
            "/api/prompts/restore",
            json={
                "id": "pm_bootstrap",
                "file": "pm_bootstrap_prompt.md",
                "backup_path": backup_path.as_posix(),
                "confirm": "RESTORE BACKUP",
            },
        )
        self.assertEqual(200, restore_response.status_code)
        restore_payload = restore_response.json()
        self.assertTrue(restore_payload["ok"])
        self.assertEqual("restored", restore_payload["status"])
        self.assertEqual(backup_path.as_posix(), restore_payload["restored_from_path"])
        restore_backup_path = Path(restore_payload["backup_path"])
        self.assertTrue(restore_backup_path.exists())
        self.assertEqual(drifted, restore_backup_path.read_text(encoding="utf-8"))
        self.assertEqual(original, prompt_path.read_text(encoding="utf-8"))
        self.assertEqual(2, len(list(prompts_dir.glob("pm_bootstrap_prompt.*.bak.md"))))

    def test_prompt_mutations_require_opt_in(self) -> None:
        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)
        prompt_body = {
            "id": "pm_bootstrap",
            "file": "pm_bootstrap_prompt.md",
            "content": "Prompt for the repo: {repo} {analysis_md}\n",
        }

        save = client.post("/api/prompts/save", json=prompt_body)
        self.assertEqual(403, save.status_code)
        self.assertEqual("prompt_mutation_disabled", save.json()["error"]["code"])

        restore = client.post(
            "/api/prompts/restore",
            json={
                "id": "pm_bootstrap",
                "file": "pm_bootstrap_prompt.md",
                "backup_path": (self.home / "prompts" / "agentcli" / "pm_bootstrap_prompt.bak.md").as_posix(),
                "confirm": "RESTORE BACKUP",
            },
        )
        self.assertEqual(403, restore.status_code)
        self.assertEqual("prompt_mutation_disabled", restore.json()["error"]["code"])


if __name__ == "__main__":
    unittest.main()
