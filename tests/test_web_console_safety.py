from __future__ import annotations

import json
import os
import subprocess
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


from agent_runner.gitops import WorktreeCleanupError


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


def _runner_base_args(config_path: Path) -> SimpleNamespace:
    return SimpleNamespace(config_path=config_path.as_posix(), config=config_path.as_posix())


def _entry_text(entry: object) -> str:
    if isinstance(entry, dict):
        for key in ("msg", "message", "raw", "text", "reason"):
            value = entry.get(key)
            if value not in (None, "", False):
                return str(value)
    return ""


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
        self._start_seq = 0
        self.start_calls = 0
        self.stop_calls: list[bool] = []
        self.status_calls = 0
        self.start_overrides: list[dict[str, object]] = []

    def _next_run_dir(self) -> Path:
        self._start_seq += 1
        return self.repo / ".AgentCLI" / "agent_runs" / f"run_20260426_{self._start_seq:06d}"

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
        overrides = dict(overrides or {})
        self.start_overrides.append(overrides)
        if self.start_error:
            return {"ok": False, "message": self.start_error}
        if self._running:
            return {"ok": False, "message": "Runner is already running."}
        explicit_run_dir = str(overrides.get("run_dir") or "").strip()
        if explicit_run_dir:
            run_dir = Path(explicit_run_dir).expanduser().resolve()
        elif bool(overrides.get("resume_latest")) and self.run_dir is not None:
            run_dir = self.run_dir
        else:
            run_dir = self._next_run_dir()
        self._running = True
        self.run_dir = run_dir
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
        self.assertEqual("completed", control_payload["run_status"])
        self.assertEqual(self.config_path.as_posix(), control_payload["status"]["config_path"])
        start_options = control_payload["runner_control"]["startOptions"]
        self.assertEqual(self.config_path.as_posix(), start_options["path"])
        self.assertEqual(self.config_path.as_posix(), start_options["values"]["config_path"])
        self.assertTrue(start_options["defaults_path"])
        self.assertEqual(["one-shot", "continuous", "loop"], start_options["choices"]["run_mode"])
        self.assertEqual(["personal", "enterprise"], start_options["choices"]["profile"])
        self.assertEqual(["codex", "claudecode"], start_options["choices"]["execution_backend"])
        self.assertEqual([True, False], start_options["choices"]["autopilot"])
        self.assertEqual([True, False], start_options["choices"]["continuous"])
        self.assertEqual([True, False], start_options["choices"]["loop"])
        self.assertEqual([True, False], start_options["choices"]["one_shot"])
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

    def test_lan_snapshot_redacts_sensitive_surfaces(self) -> None:
        prompts_dir = self.home / "prompts" / "agentcli"
        _write(
            prompts_dir / "pm_instructions.md",
            "# LAN fixture\n\nProfile: {profile}\nRepo: {repo}\nSECRET: super-secret\n",
        )
        _write(
            self.run_dir / "BACKLOG.json",
            json.dumps(
                {
                    "generated_at": "2026-04-26T12:00:00",
                    "tasks": [
                        {
                            "id": "T-LAN-1",
                            "title": "Redaction boundary",
                            "prompt": "Implement the LAN redaction boundary.",
                            "description": "Task description with an excerpt that must stay hidden.",
                            "files": ["agent_runner/web.py"],
                            "done_when": "Status snapshot stays redacted.",
                            "skills": [],
                            "skills_rationale": "This explains why the excerpt exists.",
                            "depends_on": [],
                            "status": "failed",
                            "recent_output": "Task output excerpt should not be exposed.",
                            "failure_detail": "token=abc123 from task output",
                            "failure": {
                                "reason": "build_failed",
                                "detail": "Traceback with secret token",
                                "message": "Task failed with a secret token",
                            },
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
            json.dumps(
                {
                    "done": [],
                    "failed": [
                        {
                            "task_id": "T-LAN-1",
                            "reason": "build_failed",
                            "detail": "state failure token=abc123",
                            "attempt": 2,
                            "cycle": 3,
                            "step": 4,
                            "rc": 1,
                        }
                    ],
                    "warnings": [],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        task_output_dir = self.run_dir / "tasks" / "c003_s004_T-LAN-1" / "attempt_02"
        _write(task_output_dir / "build.txt", "Task build output with secret token=abc123\nSecond line\n")
        _write(
            self.run_dir / "metrics.jsonl",
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:00",
                            "seq": 1,
                            "level": "info",
                            "event": "task_failed",
                            "stage": "Dev",
                            "task_id": "T-LAN-1",
                            "taskTitle": "Redaction boundary",
                            "message": "logs token=abc123",
                            "reason": "build_failed",
                        },
                        ensure_ascii=False,
                    ),
                    "",
                ]
            ),
        )
        _write(self.run_dir / "cycle_summary.log", "2026-04-26 12:00:00 [INFO] cycle summary token=abc123\n")
        controller = FakeRunnerController(repo=self.repo, base_args=_runner_base_args(self.config_path))
        client, _ = _create_client(
            self.repo,
            enable_runner_controls=False,
            config_path=self.config_path,
            host="0.0.0.0",
            runner_controller=controller,
        )

        status = client.get("/api/status")
        self.assertEqual(200, status.status_code)
        status_payload = status.json()
        self.assertTrue(status_payload["redaction"]["active"])
        self.assertEqual("lan", status_payload["redaction"]["scope"])
        self.assertEqual(self.repo.name, status_payload["repo"]["name"])
        self.assertEqual("[redacted]", status_payload["logs"]["tail"])
        self.assertEqual("[redacted]", status_payload["logs"]["files"]["cycle_summary"])
        self.assertEqual("[redacted]", status_payload["logs"]["files"]["run_log"])
        self.assertEqual("[redacted]", status_payload["logs"]["files"]["metrics"])
        self.assertIn("files.cycle_summary", status_payload["logs"]["redaction"]["fields"])
        self.assertEqual("[redacted]", status_payload["goals"]["raw_text"])
        self.assertEqual("[redacted]", status_payload["config"]["path"])
        self.assertEqual("[redacted]", status_payload["config"]["resolved_prompts_dir"])
        self.assertEqual("[redacted]", status_payload["config"]["data"]["telegram"]["bot_token"])
        self.assertEqual("[redacted]", status_payload["config"]["data"]["telegram"]["pairing_code"])
        self.assertEqual("personal", status_payload["config"]["data"]["profile"])
        self.assertEqual("[redacted]", status_payload["config_contract"]["values"]["telegram"]["bot_token"])
        status_prompt = next(item for item in status_payload["prompts"]["items"] if item["file"] == "pm_instructions.md")
        self.assertEqual("[redacted]", status_prompt["preview"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["status"]["config_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["defaults_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["defaults"]["config_path"])

        progress = client.get("/api/progress")
        self.assertEqual(200, progress.status_code)
        progress_payload = progress.json()
        self.assertEqual("[redacted]", progress_payload["logs"]["tail"])
        self.assertEqual("[redacted]", progress_payload["logs"]["files"]["cycle_summary"])
        self.assertEqual("[redacted]", progress_payload["logs"]["files"]["run_log"])
        self.assertEqual("[redacted]", progress_payload["logs"]["files"]["metrics"])
        self.assertEqual("[redacted]", progress_payload["goals"]["raw_text"])
        self.assertEqual("[redacted]", progress_payload["config"]["path"])
        progress_prompt = next(item for item in progress_payload["prompts"]["items"] if item["file"] == "pm_instructions.md")
        self.assertEqual("[redacted]", progress_prompt["preview"])
        self.assertEqual("[redacted]", progress_payload["runner_control"]["status"]["config_path"])
        backlog_item = progress_payload["backlog"]["items"][0]
        self.assertEqual("Redaction boundary", backlog_item["title"])
        self.assertEqual("[redacted]", backlog_item.get("prompt"))
        self.assertIsNone(backlog_item.get("description"))
        self.assertEqual("[redacted]", backlog_item.get("skills_rationale"))
        self.assertEqual("[redacted]", backlog_item.get("recent_output", backlog_item.get("recentOutput")))
        self.assertEqual("[redacted]", backlog_item.get("failure_detail", backlog_item.get("failureDetail")))
        self.assertEqual("[redacted]", backlog_item["failure"]["detail"])
        self.assertNotIn("message", backlog_item["failure"])

        logs_payload = client.get("/api/logs").json()
        self.assertEqual("[redacted]", logs_payload["tail"])
        self.assertEqual("[redacted]", logs_payload["files"]["cycle_summary"])
        self.assertEqual("[redacted]", logs_payload["files"]["run_log"])
        self.assertEqual("[redacted]", logs_payload["files"]["metrics"])
        self.assertGreater(len(logs_payload["entries"]), 0)
        self.assertEqual("[redacted]", _entry_text(logs_payload["entries"][0]))
        logs_tail = client.get("/api/logs/tail").json()
        self.assertNotIn("tail", logs_tail)
        self.assertEqual("[redacted]", logs_tail["source"]["path"])
        self.assertGreater(len(logs_tail["entries"]), 0)
        self.assertEqual("[redacted]", _entry_text(logs_tail["entries"][0]))

        goals_payload = client.get("/api/goals").json()
        self.assertEqual("[redacted]", goals_payload["raw_text"])
        self.assertTrue(goals_payload["summary"]["has_goals"])
        self.assertGreaterEqual(goals_payload["summary"]["total"], 1)

        config_payload = client.get("/api/config").json()
        self.assertEqual("[redacted]", config_payload["path"])
        self.assertEqual("[redacted]", config_payload["meta"]["path"])
        self.assertEqual("[redacted]", config_payload["meta"]["resolved_prompts_dir"])
        self.assertEqual("[redacted]", config_payload["values"]["telegram"]["bot_token"])
        self.assertEqual("personal", config_payload["values"]["profile"])

        prompts_payload = client.get("/api/prompts").json()
        prompt_item = next(item for item in prompts_payload["items"] if item["file"] == "pm_instructions.md")
        self.assertEqual("[redacted]", prompt_item["preview"])
        self.assertNotIn("content", prompt_item)
        self.assertEqual("[redacted]", prompts_payload["dir"])

        prompt_read = client.get("/api/prompts/read", params={"id": "pm_instructions", "file": "pm_instructions.md"})
        self.assertEqual(200, prompt_read.status_code)
        prompt_read_payload = prompt_read.json()
        self.assertIn("SECRET: super-secret", prompt_read_payload["content"])
        self.assertIn("{repo}", prompt_read_payload["content"])

        runner_status = client.get("/api/runner/status").json()
        self.assertEqual("[redacted]", runner_status["status"]["config_path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertFalse(runner_status["enabled"])

    def test_lan_runner_control_responses_redact_runner_args(self) -> None:
        controller = FakeRunnerController(repo=self.repo, base_args=_runner_base_args(self.config_path))
        client, _ = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            host="0.0.0.0",
            trusted_network=True,
            runner_controller=controller,
        )

        response = client.post("/api/runner/start", json={"phrase": "START RUNNER"})
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("started", payload["status"])
        self.assertTrue(payload["runner_control"]["enabled"])
        self.assertEqual("[redacted]", payload["runner_control"]["status"]["config_path"])
        self.assertEqual("[redacted]", payload["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", payload["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertEqual("[redacted]", payload["result"]["config_path"])
        self.assertEqual(self.repo.as_posix(), payload["result"]["repo"])
        self.assertEqual("[redacted]", payload["snapshot"]["logs"]["files"]["cycle_summary"])
        self.assertEqual("[redacted]", payload["snapshot"]["runner_control"]["status"]["config_path"])
        self.assertTrue(payload["snapshot"]["redaction"]["active"])
        self.assertEqual("lan", payload["snapshot"]["redaction"]["scope"])

        runner_status = client.get("/api/runner/status").json()
        self.assertEqual("[redacted]", runner_status["status"]["config_path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["values"]["config_path"])

    def test_web_app_initializes_process_guard_and_shutdown_stops_runner(self) -> None:
        from fastapi.testclient import TestClient
        import agent_runner.web as web_module

        controller = FakeRunnerController(repo=self.repo, base_args=type("Args", (), {"config_path": str(self.config_path)})())
        controller.start({"repo": self.repo.as_posix(), "config_path": self.config_path.as_posix()})

        with (
            patch.object(web_module, "_build_runner_controller", return_value=controller),
            patch.object(web_module, "init_process_guard") as init_guard,
            patch.object(web_module, "terminate_all_children") as terminate_children,
        ):
            app = web_module.create_app(
                self.repo,
                web_dir=WEB_CONSOLE,
                enable_runner_controls=True,
                config_path=str(self.config_path),
            )
            init_guard.assert_called_once()
            with TestClient(app) as client:
                self.assertTrue(client.get("/api/runner/status").json()["status"]["running"])

        self.assertIn(True, controller.stop_calls)
        terminate_children.assert_called()

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
        self.assertEqual(["one-shot", "continuous", "loop"], control_status["runner_control"]["startOptions"]["choices"]["run_mode"])
        self.assertEqual(["personal", "enterprise"], control_status["runner_control"]["startOptions"]["choices"]["profile"])
        self.assertEqual(["codex", "claudecode"], control_status["runner_control"]["startOptions"]["choices"]["execution_backend"])

        start_config_path = (self.home / "configs" / "runner-start.json").resolve()
        reload_config_path = (self.home / "configs" / "runner-reload.json").resolve()
        restart_config_path = (self.home / "configs" / "runner-restart.json").resolve()

        start_response = client.post(
            "/api/runner/start",
            json={
                "phrase": "START RUNNER",
                "start_options": {
                    "autopilot": True,
                    "run_mode": "loop",
                    "loop_max_cycles": "4",
                    "profile": "enterprise",
                    "execution_backend": "claudecode",
                    "config_path": start_config_path.as_posix(),
                },
            },
        )
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
        self.assertEqual(start_config_path.as_posix(), controller.start_overrides[0]["config_path"])
        self.assertEqual(start_config_path.as_posix(), controller.start_overrides[0]["config"])
        self.assertTrue(controller.start_overrides[0]["autopilot"])
        self.assertEqual(True, controller.start_overrides[0]["continuous"])
        self.assertEqual(True, controller.start_overrides[0]["loop"])
        self.assertEqual(4, controller.start_overrides[0]["loop_max_cycles"])
        self.assertEqual("enterprise", controller.start_overrides[0]["profile"])
        self.assertEqual("claudecode", controller.start_overrides[0]["execution_backend"])
        self.assertNotIn("run_dir", controller.start_overrides[0])
        self.assertNotIn("resume_latest", controller.start_overrides[0])
        self.assertNotIn("run_mode", controller.start_overrides[0])
        start_run_dir = Path(start_body["result"]["run_dir"])

        reload_response = client.post(
            "/api/runner/reload",
            json={
                "token": "RELOAD RUNNER",
                "runnerOptions": {
                    "autopilot": False,
                    "run_mode": "continuous",
                    "loop_max_cycles": "7",
                    "profile": "personal",
                    "execution_backend": "codex",
                    "config_path": reload_config_path.as_posix(),
                },
            },
        )
        self.assertEqual(200, reload_response.status_code)
        reload_body = reload_response.json()
        self.assertTrue(reload_body["ok"])
        self.assertEqual("reload", reload_body["action"])
        self.assertEqual("reloaded", reload_body["status"])
        self.assertEqual(1, controller.stop_calls.count(False))
        self.assertEqual(2, controller.start_calls)
        self.assertEqual(self.repo.as_posix(), controller.start_overrides[1]["repo"])
        self.assertEqual(reload_config_path.as_posix(), controller.start_overrides[1]["config_path"])
        self.assertEqual(reload_config_path.as_posix(), controller.start_overrides[1]["config"])
        self.assertFalse(controller.start_overrides[1]["autopilot"])
        self.assertEqual(True, controller.start_overrides[1]["continuous"])
        self.assertEqual(False, controller.start_overrides[1]["loop"])
        self.assertEqual(7, controller.start_overrides[1]["loop_max_cycles"])
        self.assertEqual("personal", controller.start_overrides[1]["profile"])
        self.assertEqual("codex", controller.start_overrides[1]["execution_backend"])
        self.assertNotIn("run_dir", controller.start_overrides[1])
        self.assertNotIn("resume_latest", controller.start_overrides[1])
        reload_run_dir = Path(reload_body["result"]["start"]["run_dir"])
        self.assertNotEqual(start_run_dir, reload_run_dir)
        self.assertTrue(reload_body["snapshot"]["runner_control"]["status"]["running"])
        self.assertEqual(self.config_path.as_posix(), reload_body["snapshot"]["runner_control"]["status"]["config_path"])

        restart_response = client.post(
            "/api/runner/restart",
            json={
                "confirm": "RESTART RUNNER",
                "options": {
                    "autopilot": True,
                    "runMode": "one-shot",
                    "maxCycles": "0",
                    "profile": "enterprise",
                    "backend": "claudecode",
                    "configPath": restart_config_path.as_posix(),
                },
            },
        )
        self.assertEqual(200, restart_response.status_code)
        restart_body = restart_response.json()
        self.assertTrue(restart_body["ok"])
        self.assertEqual("restart", restart_body["action"])
        self.assertEqual("restarted", restart_body["status"])
        self.assertEqual(2, controller.stop_calls.count(False))
        self.assertEqual(3, controller.start_calls)
        self.assertEqual(self.repo.as_posix(), controller.start_overrides[2]["repo"])
        self.assertEqual(restart_config_path.as_posix(), controller.start_overrides[2]["config_path"])
        self.assertEqual(restart_config_path.as_posix(), controller.start_overrides[2]["config"])
        self.assertTrue(controller.start_overrides[2]["autopilot"])
        self.assertEqual(False, controller.start_overrides[2]["continuous"])
        self.assertEqual(False, controller.start_overrides[2]["loop"])
        self.assertEqual(0, controller.start_overrides[2]["loop_max_cycles"])
        self.assertEqual("enterprise", controller.start_overrides[2]["profile"])
        self.assertEqual("claudecode", controller.start_overrides[2]["execution_backend"])
        self.assertNotIn("run_dir", controller.start_overrides[2])
        self.assertNotIn("resume_latest", controller.start_overrides[2])
        restart_run_dir = Path(restart_body["result"]["start"]["run_dir"])
        self.assertNotEqual(reload_run_dir, restart_run_dir)
        self.assertNotEqual(start_run_dir, restart_run_dir)
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

    def test_invalid_runner_start_options_return_structured_400(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        invalid_cases = [
            (
                "/api/runner/start",
                {"confirmation": "START RUNNER", "start_options": {"execution_backend": "bogus", "config_path": self.config_path.as_posix()}},
                "execution_backend",
                "invalid_choice",
            ),
            (
                "/api/runner/restart",
                {"confirm": "RESTART RUNNER", "options": {"run_mode": "loop", "continuous": False, "config_path": self.config_path.as_posix()}},
                "continuous",
                "invalid_combination",
            ),
        ]

        for path, payload, field, code in invalid_cases:
            with self.subTest(path=path, field=field, code=code):
                response = client.post(path, json=payload)
                self.assertEqual(400, response.status_code)
                body = response.json()
                self.assertFalse(body["ok"])
                self.assertEqual("runner_start_options_invalid", body["error"]["code"])
                self.assertEqual("Runner start options are invalid.", body["message"])
                errors = body["error"]["details"]["errors"]
                self.assertTrue(any(item["field"] == field and item["code"] == code for item in errors))
                self.assertEqual(0, app.state.runner_controller.start_calls)
                self.assertEqual([], app.state.runner_controller.stop_calls)

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
        self.assertEqual("[redacted]", trusted_status["runner_control"]["status"]["config_path"])

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
        locked_file = self.worktree_dir / "nested" / "locked.txt"
        locked_file.parent.mkdir(parents=True, exist_ok=True)
        locked_file.write_text("locked\n", encoding="utf-8")
        permission_error = PermissionError(13, "Permission denied", locked_file.as_posix())
        cleanup_error = WorktreeCleanupError(
            str(permission_error),
            cleanup_path=locked_file.as_posix(),
            details={
                "path": locked_file.as_posix(),
                "worktree_dir": self.worktree_dir.as_posix(),
                "attempts": [
                    {
                        "attempt": 1,
                        "operation": "shutil.rmtree",
                        "path": locked_file.as_posix(),
                        "worktree_dir": self.worktree_dir.as_posix(),
                        "error_type": "PermissionError",
                        "message": str(permission_error),
                        "errno": 13,
                    }
                ],
                "operation": "shutil.rmtree",
                "error_type": "PermissionError",
                "message": str(permission_error),
            },
            attempts=[
                {
                    "attempt": 1,
                    "operation": "shutil.rmtree",
                    "path": locked_file.as_posix(),
                    "worktree_dir": self.worktree_dir.as_posix(),
                    "error_type": "PermissionError",
                    "message": str(permission_error),
                    "errno": 13,
                }
            ],
        )

        with patch("agent_runner.gitops.remove_worktree", side_effect=cleanup_error):
            response = client.post("/api/worktree/discard", json=body)

        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("discard_cleanup_failed", payload["status"])
        self.assertEqual("failed", payload["worktree"]["cleanupState"])
        self.assertEqual(locked_file.as_posix(), payload["worktree"]["cleanupPath"])
        self.assertEqual(str(permission_error), payload["worktree"]["cleanupMessage"])
        self.assertEqual(locked_file.as_posix(), payload["worktree"]["cleanupDetails"]["path"])
        self.assertEqual(locked_file.as_posix(), payload["worktree"]["cleanupDetails"]["attempts"][0]["path"])
        self.assertEqual(locked_file.as_posix(), payload["result"]["cleanup_path"])
        self.assertEqual(str(permission_error), payload["result"]["cleanup_message"])
        self.assertEqual(locked_file.as_posix(), payload["result"]["cleanup_details"]["path"])
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

    def _prepare_worktree_merge_client(self):
        from agent_runner.web import build_snapshot

        fixture = self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        body = self._worktree_action_payload(snapshot["worktree"], confirmation="MERGE WORKTREE")
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        return fixture, snapshot, body, client

    def test_worktree_merge_rejects_dirty_source_repo(self) -> None:
        fixture, snapshot, body, client = self._prepare_worktree_merge_client()
        source_file = Path(str(fixture["source_file"]))
        source_text = str(fixture["source_text"])
        source_file.write_text(source_text + "dirty\n", encoding="utf-8")

        response = client.post("/api/worktree/merge", json=body)
        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("worktree_source_repo_dirty", payload["error"]["code"])
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(snapshot["worktree"]["sourceRepo"], payload["worktree"]["sourceRepo"])

    def test_worktree_merge_rejects_head_mismatch(self) -> None:
        fixture, snapshot, body, client = self._prepare_worktree_merge_client()
        _write(self.repo / "advance.txt", "advance\n")
        self._git("add", "advance.txt")
        self._git("commit", "-m", "advance")

        response = client.post("/api/worktree/merge", json=body)
        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("worktree_base_ref_mismatch", payload["error"]["code"])
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(snapshot["worktree"]["sourceRepo"], payload["worktree"]["sourceRepo"])

    def test_worktree_merge_rejects_patch_hash_mismatch(self) -> None:
        fixture, snapshot, body, client = self._prepare_worktree_merge_client()
        pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
        pending["patch_hash"] = "0" * 64
        pending["patchHash"] = "0" * 64
        pending_text = json.dumps(pending, ensure_ascii=False, indent=2) + "\n"
        self.pending_path.write_text(pending_text, encoding="utf-8")
        (self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json").write_text(pending_text, encoding="utf-8")

        response = client.post("/api/worktree/merge", json=body)
        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("worktree_patch_hash_mismatch", payload["error"]["code"])
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(snapshot["worktree"]["sourceRepo"], payload["worktree"]["sourceRepo"])

    def test_worktree_merge_rejects_git_apply_check_failure(self) -> None:
        from agent_runner.gitops import sha256_text

        fixture, snapshot, body, client = self._prepare_worktree_merge_client()
        invalid_patch = (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n"
            "+++ b/src/app.py\n"
            "@@ -1 +1 @@\n"
            "-this context does not exist\n"
            "+still invalid\n"
        )
        self.patch_path.write_text(invalid_patch, encoding="utf-8")
        pending = json.loads(self.pending_path.read_text(encoding="utf-8"))
        patch_hash = sha256_text(invalid_patch)
        pending["patch_hash"] = patch_hash
        pending["patchHash"] = patch_hash
        pending_text = json.dumps(pending, ensure_ascii=False, indent=2) + "\n"
        self.pending_path.write_text(pending_text, encoding="utf-8")
        (self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json").write_text(pending_text, encoding="utf-8")

        response = client.post("/api/worktree/merge", json=body)
        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("worktree_patch_check_failed", payload["error"]["code"])
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(snapshot["worktree"]["sourceRepo"], payload["worktree"]["sourceRepo"])

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
            ("typo-p0", "# Project Goals\n\n## P 0\n- [ ] Keep read-only progress views\n\n## P1\n- [ ] Surface the safety banner\n"),
            ("typo-p1", "# Project Goals\n\n## P0\n- [ ] Keep read-only progress views\n\n## P 1\n- [ ] Surface the safety banner\n"),
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

    def test_shell_status_and_doctor_use_configured_goals_completion_level(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        from agent_runner.shell import RunnerShell

        config_path = self.home / "configs" / "shell-goals-p1.json"
        _write_config(config_path, self.repo, goals_completion_level="p1")

        shell = RunnerShell(initial_argv=["--repo", self.repo.as_posix(), "--config", config_path.as_posix()])
        self.assertEqual("p1", shell.effective()["goals_completion_level"])

        status_buffer = StringIO()
        with redirect_stdout(status_buffer):
            shell.status()
        status_output = status_buffer.getvalue()
        self.assertIn("goals_completion_level: p1", status_output)

        doctor_buffer = StringIO()
        with redirect_stdout(doctor_buffer):
            shell.doctor()
        doctor_output = doctor_buffer.getvalue()
        self.assertIn("goals_completion_level: p1", doctor_output)
        self.assertIn("project_complete: False", doctor_output)

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
