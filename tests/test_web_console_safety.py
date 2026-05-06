from __future__ import annotations

import json
import os
import subprocess
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import uuid


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


from agent_runner.gitops import WorktreeCleanupError
from agent_runner.stop_progress import write_stop_progress


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

    def test_lan_safety_helper_is_reexported_from_web(self) -> None:
        import agent_runner.web as web_module
        import agent_runner.web_redaction as web_redaction

        self.assertIs(web_module._lan_safety_blocks_mutations, web_redaction._lan_safety_blocks_mutations)

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

    def _web_instance_lock_path(self) -> Path:
        return self.repo / ".AgentCLI" / "web_console.lock.json"

    def _web_action_audit_path(self, run_dir: Path | None = None) -> Path:
        from agent_runner.web_action_audit import web_action_audit_path

        return web_action_audit_path(self.repo, run_dir)

    def _read_web_action_audit(self, run_dir: Path | None = None) -> list[dict[str, object]]:
        path = self._web_action_audit_path(run_dir)
        if not path.exists():
            return []
        records: list[dict[str, object]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records

    def _terminate_sleep_process(self, proc: subprocess.Popen[object]) -> None:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=5)
                except Exception:
                    pass
        else:
            try:
                proc.wait(timeout=1)
            except Exception:
                pass

    def _spawn_sleep_process(self) -> subprocess.Popen[object]:
        from agent_runner.process_guard import _pid_alive, _pid_create_time_ticks

        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            creationflags=creationflags,
        )
        self.addCleanup(self._terminate_sleep_process, proc)
        deadline = time.time() + 5.0
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            if _pid_create_time_ticks(proc.pid) is not None or _pid_alive(proc.pid):
                break
            time.sleep(0.05)
        return proc

    def _goal_item(self, text: str, *, done: bool = False, note: str = "") -> dict[str, object]:
        return {
            "done": done,
            "checked": done,
            "checkbox": "[x]" if done else "[ ]",
            "text": text,
            "note": note,
        }

    def _config_backup(
        self,
        stamp: str,
        *,
        data: dict[str, object],
        mtime: float | None = None,
        nested: str | None = None,
    ) -> Path:
        backup_dir = self.config_path.parent / nested if nested else self.config_path.parent
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{self.config_path.stem}.{stamp}.bak{self.config_path.suffix}"
        _write(backup_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        if mtime is not None:
            os.utime(backup_path, (mtime, mtime))
        return backup_path

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

    def _init_repo(self) -> str:
        self._git("init")
        self._git("config", "user.email", "agentcli-tests@example.com")
        self._git("config", "user.name", "AgentCLI Tests")
        _write(self.repo / "README.md", "base\n")
        self._git("add", "README.md")
        self._git("commit", "-m", "base")
        return self._git("rev-parse", "HEAD")

    def _ensure_source_venv(self) -> Path:
        python_rel = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
        python_path = self.repo / ".venv" / python_rel
        python_path.parent.mkdir(parents=True, exist_ok=True)
        python_path.write_text("", encoding="utf-8")
        return python_path

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

    def _prepare_pr_queue_merge_packet(self) -> dict[str, object]:
        from agent_runner.gitops import git_head
        from agent_runner.pr_queue import queue_review_packet

        source_head_before = self._init_repo()
        goal_trace = [
            {
                "goal_ref": "GOAL-1",
                "goal_text": "Validate the PR queue merge gate through the web console.",
            }
        ]
        feature_path = self.repo / "feature.txt"
        feature_path.write_text("feature\n", encoding="utf-8")
        self._git("add", "feature.txt")
        self._git("commit", "-m", "feature")
        branch_head = self._git("rev-parse", "HEAD")
        branch_name = self._git("branch", "--show-current") or "master"

        packet_result = queue_review_packet(
            self.repo,
            run_id=self.run_dir.name,
            task_ids=["T1"],
            base_ref=source_head_before,
            head_ref=branch_head,
            branch=branch_name,
            created_at="2026-04-26T12:00:00Z",
            source_head_before=source_head_before,
            source_head_after=git_head(self.repo),
            worktree_dir="",
            validation_status="validation_pending",
            validation_artifacts=[],
            qa_notes=["ready for validation"],
            goal_trace=goal_trace,
            changed_files=["feature.txt"],
            status="pr_queued",
        )

        run_dir = self.repo / ".AgentCLI" / "agent_runs" / self.run_dir.name
        run_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "build_enabled": True,
            "run_tests": True,
            "build_cmd": ["python", "-B", "-m", "py_compile", "agent_runner/pr_queue.py"],
            "test_cmd": ["python", "-B", "-m", "unittest", "discover", "-s", "tests"],
            "build_timeout_seconds": 60,
            "test_timeout_seconds": 60,
        }
        (run_dir / "last_run_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        validation_root = run_dir / "pr_queue_validation" / str(packet_result["packet_id"]) / "attempt_01"
        validation_root.mkdir(parents=True, exist_ok=True)
        validation_path = validation_root / "validation.json"
        validation_payload = {
            "schema_version": 1,
            "kind": "qa_validation_attempt",
            "task_id": "T1",
            "task_title": "Validate PR queue merge gate through the web console.",
            "cycle": 1,
            "step": 1,
            "attempt": 1,
            "status": "validation_passed",
            "validation_status": "validation_passed",
            "validation_reason": "validation_complete",
            "validation_detail": "Validation completed for the merge gate test.",
            "goal_trace": goal_trace,
            "qa_notes": ["ready for validation"],
            "summary": "Validation completed for the merge gate test.",
            "detail": "Validation completed for the merge gate test.",
            "failure_summary": "",
            "artifact_path": validation_path.as_posix(),
        }
        validation_path.write_text(json.dumps(validation_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        packet_path = Path(packet_result["packet_path"])
        packet_data = json.loads(packet_path.read_text(encoding="utf-8"))
        packet_data["status"] = "validation_passed"
        packet_data["validation_status"] = "validation_passed"
        packet_data["validationStatus"] = "validation_passed"
        packet_data["validation_artifact_path"] = validation_path.as_posix()
        packet_data["validationArtifactPath"] = validation_path.as_posix()
        packet_data["validation_artifacts"] = [validation_path.as_posix()]
        packet_data["validationArtifacts"] = [validation_path.as_posix()]
        packet_data["updated_at"] = packet_data.get("updated_at") or ""
        packet_path.write_text(json.dumps(packet_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        return {
            "packet_id": packet_result["packet_id"],
            "packet_path": packet_path,
            "source_head_before": source_head_before,
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
        self.assertTrue(start_options["validation"]["valid"])
        self.assertIsInstance(start_options["argv_preview"], list)
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

    def test_localhost_read_only_keeps_raw_prompt_reads_but_blocks_mutations(self) -> None:
        prompts_dir = self.home / "prompts" / "agentcli"
        _write(prompts_dir / "pm_instructions.md", "# Local prompt\nSECRET: local-only\nRepo: {repo}\n")
        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)

        status_payload = client.get("/api/status").json()
        self.assertFalse(status_payload["redaction"]["active"])
        self.assertEqual("local", status_payload["redaction"]["scope"])
        self.assertFalse(status_payload["runner_control"]["enabled"])
        self.assertFalse(status_payload["config_contract"]["meta"]["save_enabled"])

        prompt_read = client.get("/api/prompts/read", params={"id": "pm_instructions", "file": "pm_instructions.md"})
        self.assertEqual(200, prompt_read.status_code)
        prompt_payload = prompt_read.json()
        self.assertIn("SECRET: local-only", prompt_payload["content"])

        save_response = client.post("/api/config/save", json={"changes": [{"path": "iterations", "value": 5}]})
        self.assertEqual(403, save_response.status_code)
        self.assertEqual("config_save_disabled", save_response.json()["error"]["code"])

    def test_localhost_opt_in_enables_mutating_control_surfaces(self) -> None:
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        status_payload = client.get("/api/status").json()
        self.assertFalse(status_payload["redaction"]["active"])
        self.assertTrue(status_payload["runner_control"]["enabled"])
        self.assertTrue(status_payload["runner_control"]["actions"]["start"]["enabled"])
        self.assertTrue(status_payload["config_contract"]["meta"]["save_enabled"])
        self.assertTrue(status_payload["config_contract"]["meta"]["restore_enabled"])

    def test_runner_control_timeout_stop_response_is_retryable_and_surfaces_stop_progress(self) -> None:
        timeout_progress = {
            "phase": "timeout",
            "message": "Runner is still alive after 1s stop wait timeout.",
            "elapsed_seconds": 12,
            "updated_at": "2026-04-28T00:00:12",
            "requested_at": "2026-04-28T00:00:00",
            "history": [
                {"phase": "request", "message": "Stop requested.", "elapsed_seconds": 0, "updated_at": "2026-04-28T00:00:00"},
                {"phase": "stop_file_write", "message": "Stop file written: C:/temp/STOP", "elapsed_seconds": 1, "updated_at": "2026-04-28T00:00:01"},
                {"phase": "child_termination", "message": "Terminating tracked child processes.", "elapsed_seconds": 2, "updated_at": "2026-04-28T00:00:02"},
                {"phase": "runner_wait", "message": "Waiting for runner shutdown and final artifacts.", "elapsed_seconds": 6, "updated_at": "2026-04-28T00:00:06"},
                {"phase": "timeout", "message": "Runner is still alive after 1s stop wait timeout.", "elapsed_seconds": 12, "updated_at": "2026-04-28T00:00:12"},
            ],
            "runner_alive": True,
            "tracked_child_pids": [321, 654],
            "tracked_child_processes": [
                {
                    "pid": 321,
                    "alive": True,
                    "session_file": "C:/temp/session_321.json",
                    "session_exists": True,
                }
            ],
            "stop_file_paths": {
                "stop_file_path": "C:/temp/STOP",
                "stop_progress_path": "C:/temp/STOP_PROGRESS.json",
                "stop_progress_log_path": "C:/temp/stop_progress.log",
            },
            "last_artifact_signal": {
                "path": "C:/temp/run_summary.json",
                "updated_at": "2026-04-28T00:00:10",
            },
            "last_log_signal": {
                "path": "C:/temp/cycle_summary.log",
                "updated_at": "2026-04-28T00:00:11",
            },
            "timeout_guidance": {
                "summary": "Retry stop after checking the runner.",
                "recoverable": True,
                "steps": [
                    "Retry stop after checking the runner.",
                    "Inspect the tracked child PIDs.",
                ],
                "manual_cleanup_hints": ["Close the runner."],
                "locked_file_paths": ["C:/temp/locked.txt"],
            },
            "current_phase": {
                "phase": "timeout",
                "message": "Runner is still alive after 1s stop wait timeout.",
                "elapsed_seconds": 12,
                "updated_at": "2026-04-28T00:00:12",
                "runner_alive": True,
                "tracked_child_pids": [321, 654],
                "tracked_child_processes": [
                    {
                        "pid": 321,
                        "alive": True,
                        "session_file": "C:/temp/session_321.json",
                        "session_exists": True,
                    }
                ],
                "stop_file_paths": {
                    "stop_file_path": "C:/temp/STOP",
                    "stop_progress_path": "C:/temp/STOP_PROGRESS.json",
                    "stop_progress_log_path": "C:/temp/stop_progress.log",
                },
                "last_artifact_signal": {
                    "path": "C:/temp/run_summary.json",
                    "updated_at": "2026-04-28T00:00:10",
                },
                "last_log_signal": {
                    "path": "C:/temp/cycle_summary.log",
                    "updated_at": "2026-04-28T00:00:11",
                },
                "timeout_guidance": {
                    "summary": "Retry stop after checking the runner.",
                    "recoverable": True,
                    "steps": [
                        "Retry stop after checking the runner.",
                        "Inspect the tracked child PIDs.",
                    ],
                    "manual_cleanup_hints": ["Close the runner."],
                    "locked_file_paths": ["C:/temp/locked.txt"],
                },
            },
        }

        class TimeoutRunnerController:
            def __init__(self, *, repo: Path, base_args: SimpleNamespace, run_dir: Path, status_payload: dict[str, object], stop_result: dict[str, object]) -> None:
                self.repo = repo
                self.base_args = base_args
                self.run_dir = run_dir
                self._status = dict(status_payload)
                self._stop_result = dict(stop_result)
                self.start_calls = 0
                self.stop_calls: list[bool] = []

            def status(self) -> dict[str, object]:
                return dict(self._status)

            def stop(self, *, wait: bool = False) -> dict[str, object]:
                self.stop_calls.append(bool(wait))
                return dict(self._stop_result)

        status_payload = {
            "running": True,
            "runner_mode": "thread",
            "repo": self.repo.as_posix(),
            "config_path": self.config_path.as_posix(),
            "run_dir": self.run_dir.as_posix(),
            "uptime_seconds": 18,
            "exit_code": None,
            "stop_file": "STOP",
            "stop_file_exists": True,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "state_counts": {"done": 0, "failed": 0, "warnings": 0},
            "reason": "",
            "last_event": "2026-04-26T12:08:00 cycle_end",
            "stop_progress": timeout_progress,
            "start_options": {},
        }
        stop_result = {
            "ok": False,
            "status": "timeout",
            "message": "Runner is still alive after 1s stop wait timeout.",
            "running": True,
            "runner_alive": True,
            "runnerAlive": True,
            "run_dir": self.run_dir.as_posix(),
            "repo": self.repo.as_posix(),
            "config_path": self.config_path.as_posix(),
            "tracked_child_pids": [321, 654],
            "trackedChildPids": [321, 654],
            "tracked_child_processes": timeout_progress["tracked_child_processes"],
            "trackedChildProcesses": timeout_progress["tracked_child_processes"],
            "stop_file_paths": timeout_progress["stop_file_paths"],
            "stopFilePaths": timeout_progress["stop_file_paths"],
            "last_artifact_signal": timeout_progress["last_artifact_signal"],
            "lastArtifactSignal": timeout_progress["last_artifact_signal"],
            "last_log_signal": timeout_progress["last_log_signal"],
            "lastLogSignal": timeout_progress["last_log_signal"],
            "timeout_guidance": timeout_progress["timeout_guidance"],
            "timeoutGuidance": timeout_progress["timeout_guidance"],
            "manual_cleanup_hints": ["Close the runner."],
            "manualCleanupHints": ["Close the runner."],
            "locked_file_paths": ["C:/temp/locked.txt"],
            "lockedFilePaths": ["C:/temp/locked.txt"],
            "stop_progress": timeout_progress,
        }
        controller = TimeoutRunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(config_path=self.config_path.as_posix(), config=self.config_path.as_posix()),
            run_dir=self.run_dir,
            status_payload=status_payload,
            stop_result=stop_result,
        )
        client, _ = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            runner_controller=controller,
        )

        control_status = client.get("/api/runner/status")
        self.assertEqual(200, control_status.status_code)
        control_payload = control_status.json()
        self.assertTrue(control_payload["runner_control"]["actions"]["stop"]["enabled"])
        self.assertEqual("timeout", control_payload["status"]["stop_progress"]["phase"])
        self.assertEqual(["request", "stop_file_write", "child_termination", "runner_wait", "timeout"], [entry["phase"] for entry in control_payload["status"]["stop_progress"]["history"]])
        self.assertEqual("timeout", control_payload["runner_control"]["status"]["stopProgress"]["phase"])
        self.assertEqual([321, 654], control_payload["runner_control"]["status"]["stopProgress"]["trackedChildPids"])
        self.assertEqual(["Close the runner."], control_payload["runner_control"]["status"]["stopProgress"]["manualCleanupHints"])
        self.assertEqual(["C:/temp/locked.txt"], control_payload["runner_control"]["status"]["stopProgress"]["lockedFilePaths"])

        response = client.post("/api/runner/stop", json={"confirmation": "STOP RUNNER"})
        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("timeout", payload["status"])
        self.assertEqual("runner_stop_timeout", payload["error"]["code"])
        self.assertEqual("timeout", payload["result"]["stop_progress"]["phase"])
        self.assertEqual("timeout", payload["runner_control"]["status"]["stopProgress"]["phase"])
        self.assertEqual([321, 654], payload["runner_control"]["status"]["stopProgress"]["trackedChildPids"])
        self.assertTrue(controller.stop_calls and controller.stop_calls[-1])

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
        base_status_payload = controller._status_payload
        current_event = {
            "action": "start",
            "status": "started",
            "message": "Runner started.",
            "error": "",
            "ok": True,
            "source": "controller",
            "repo": self.repo.as_posix(),
            "config_path": self.config_path.as_posix(),
            "run_dir": self.run_dir.as_posix(),
        }
        history = [
            {
                "action": "start",
                "status": "request",
                "message": "Start requested.",
                "error": "",
                "ok": False,
                "source": "controller",
                "repo": self.repo.as_posix(),
                "config_path": self.config_path.as_posix(),
                "run_dir": self.run_dir.as_posix(),
            },
            current_event,
        ]

        def _status_payload_with_event_history() -> dict[str, object]:
            payload = base_status_payload()
            payload.update(
                {
                    "current_event": current_event,
                    "currentEvent": current_event,
                    "history": history,
                    "event_history": history,
                    "eventHistory": history,
                    "event_count": len(history),
                    "eventCount": len(history),
                }
            )
            return payload

        controller._status_payload = _status_payload_with_event_history  # type: ignore[method-assign]
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
        self.assertEqual("[redacted]", status_payload["runner_control"]["status"]["current_event"]["config_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["status"]["history"][0]["config_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["defaults_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["defaults"]["config_path"])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["argv_preview"][1])
        self.assertEqual("[redacted]", status_payload["runner_control"]["startOptions"]["argv_preview"][3])
        self.assertTrue(status_payload["runner_control"]["startOptions"]["validation"]["valid"])

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
        self.assertEqual("[redacted]", logs_payload["source"]["path"])
        self.assertGreater(len(logs_payload["entries"]), 0)
        self.assertEqual("[redacted]", _entry_text(logs_payload["entries"][0]))
        self.assertEqual("[redacted]", next(item["path"] for item in logs_payload["sources"] if item["id"] == "backend_transcript"))
        logs_tail = client.get("/api/logs/tail").json()
        self.assertNotIn("tail", logs_tail)
        self.assertEqual("[redacted]", logs_tail["source"]["path"])
        self.assertGreater(len(logs_tail["entries"]), 0)
        self.assertEqual("[redacted]", _entry_text(logs_tail["entries"][0]))
        self.assertEqual("[redacted]", next(item["name"] for item in logs_tail["sources"] if item["id"] == "backend_transcript"))

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
        self.assertEqual(403, prompt_read.status_code)
        prompt_read_payload = prompt_read.json()
        self.assertFalse(prompt_read_payload["ok"])
        self.assertEqual("lan_safety_prompt_read_blocked", prompt_read_payload["error"]["code"])
        self.assertIn("prompt inventory", prompt_read_payload["error"]["message"])
        self.assertNotIn("content", prompt_read_payload)

        runner_status = client.get("/api/runner/status").json()
        self.assertEqual("[redacted]", runner_status["status"]["config_path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["argv_preview"][1])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["argv_preview"][3])
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
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("lan_safety_mutation_blocked", payload["error"]["code"])
        self.assertEqual("lan_safety_blocked", payload["status"])
        self.assertFalse(payload["runner_control"]["enabled"])
        self.assertEqual("[redacted]", payload["runner_control"]["status"]["config_path"])
        self.assertEqual("[redacted]", payload["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", payload["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertEqual("[redacted]", payload["runner_control"]["startOptions"]["argv_preview"][1])
        self.assertEqual("[redacted]", payload["runner_control"]["startOptions"]["argv_preview"][3])
        self.assertTrue(payload["runner_control"]["startOptions"]["validation"]["valid"])
        self.assertEqual("[redacted]", payload["snapshot"]["logs"]["files"]["cycle_summary"])
        self.assertEqual("[redacted]", payload["snapshot"]["runner_control"]["status"]["config_path"])
        self.assertTrue(payload["snapshot"]["redaction"]["active"])
        self.assertEqual("lan", payload["snapshot"]["redaction"]["scope"])

        runner_status = client.get("/api/runner/status").json()
        self.assertEqual("[redacted]", runner_status["status"]["config_path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["argv_preview"][1])
        self.assertEqual("[redacted]", runner_status["runner_control"]["startOptions"]["argv_preview"][3])
        self.assertTrue(runner_status["runner_control"]["startOptions"]["validation"]["valid"])

    def test_runner_control_status_surfaces_valid_loop_preview(self) -> None:
        controller = FakeRunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(
                config_path=self.config_path.as_posix(),
                config=self.config_path.as_posix(),
                autopilot=True,
                continuous=True,
                loop=True,
                loop_max_cycles=7,
                profile="enterprise",
                execution_backend="claudecode",
                run_dir=(self.home / "runs" / "explicit").as_posix(),
                resume_latest=False,
            ),
        )
        client, _ = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            runner_controller=controller,
        )

        status_payload = client.get("/api/runner/status").json()
        start_options = status_payload["runner_control"]["startOptions"]
        self.assertTrue(start_options["validation"]["valid"])
        self.assertEqual(
            [
                "--repo",
                self.repo.as_posix(),
                "--config",
                self.config_path.as_posix(),
                "--autopilot",
                "--continuous",
                "--loop",
                "--no-resume-latest",
                "--loop-max-cycles",
                "7",
                "--profile",
                "enterprise",
                "--execution-backend",
                "claudecode",
                "--run-dir",
                (self.home / "runs" / "explicit").as_posix(),
            ],
            start_options["argv_preview"],
        )

    def test_runner_start_options_surface_all_field_errors_together(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        explicit_run_dir = (self.repo / ".AgentCLI" / "agent_runs" / "explicit").resolve()

        response = client.post(
            "/api/runner/start",
            json={
                "confirmation": "START RUNNER",
                "start_options": {
                    "autopilot": True,
                    "run_mode": "loop",
                    "continuous": False,
                    "loop": False,
                    "one_shot": True,
                    "loop_max_cycles": "-1",
                    "profile": "bogus",
                    "execution_backend": "bogus",
                    "config_path": "",
                    "run_dir": explicit_run_dir.as_posix(),
                    "resume_latest": True,
                },
            },
        )
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("runner_start_options_invalid", payload["error"]["code"])
        self.assertEqual("Runner start options are invalid.", payload["message"])
        details = payload["error"]["details"]
        self.assertFalse(details["valid"])
        self.assertEqual(8, details["error_count"])
        self.assertIn("continuous", details["field_errors"])
        self.assertIn("loop", details["field_errors"])
        self.assertIn("one_shot", details["field_errors"])
        self.assertIn("loop_max_cycles", details["field_errors"])
        self.assertIn("profile", details["field_errors"])
        self.assertIn("execution_backend", details["field_errors"])
        self.assertIn("config_path", details["field_errors"])
        self.assertIn("resume_latest", details["field_errors"])
        self.assertTrue(any(error["field"] == "continuous" and error["code"] == "invalid_combination" for error in details["errors"]))
        self.assertTrue(any(error["field"] == "loop" and error["code"] == "invalid_combination" for error in details["errors"]))
        self.assertTrue(any(error["field"] == "one_shot" and error["code"] == "invalid_combination" for error in details["errors"]))
        self.assertTrue(any(error["field"] == "profile" and error["code"] == "invalid_choice" for error in details["errors"]))
        self.assertTrue(any(error["field"] == "execution_backend" and error["code"] == "invalid_choice" for error in details["errors"]))
        self.assertTrue(any(error["field"] == "loop_max_cycles" and error["code"] == "invalid_value" for error in details["errors"]))
        self.assertTrue(any(error["field"] == "config_path" and error["code"] == "required" for error in details["errors"]))
        self.assertTrue(any(error["field"] == "resume_latest" and error["code"] == "invalid_combination" for error in details["errors"]))
        self.assertEqual(0, app.state.runner_controller.start_calls)
        self.assertEqual([], app.state.runner_controller.stop_calls)

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
        from agent_runner.remote.controller import read_runner_control_event

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

        app.state.runner_controller.run_dir = self.run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)
        mismatch = client.post("/api/runner/start", json={"confirmation": "WRONG"})
        self.assertEqual(400, mismatch.status_code)
        event = read_runner_control_event(self.run_dir)
        self.assertEqual("start", event["action"])
        self.assertEqual("confirmation_mismatch", event["status"])
        self.assertEqual("confirmation_mismatch", event["current_event"]["status"])
        self.assertGreaterEqual(int(event["event_count"]), 1)
        self.assertEqual("confirmation_mismatch", event["history"][-1]["status"])

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
        self.assertFalse(controller.start_overrides[0]["resume_latest"])
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
        self.assertFalse(controller.start_overrides[1]["resume_latest"])
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
        self.assertFalse(controller.start_overrides[2]["resume_latest"])
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

    def test_runner_actions_write_web_action_audit_records(self) -> None:
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        start = client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
        self.assertEqual(200, start.status_code)
        start_run_dir = Path(start.json()["result"]["run_dir"])

        reload = client.post("/api/runner/reload", json={"confirmation": "RELOAD RUNNER"})
        self.assertEqual(200, reload.status_code)
        reload_run_dir = Path(reload.json()["result"]["start"]["run_dir"])

        restart = client.post("/api/runner/restart", json={"confirmation": "RESTART RUNNER"})
        self.assertEqual(200, restart.status_code)
        restart_run_dir = Path(restart.json()["result"]["start"]["run_dir"])

        stop = client.post("/api/runner/stop", json={"confirmation": "STOP RUNNER"})
        self.assertEqual(200, stop.status_code)

        start_records = self._read_web_action_audit(start_run_dir)
        self.assertEqual("runner.start", start_records[-1]["action"])
        self.assertEqual("started", start_records[-1]["status"])
        self.assertTrue(start_records[-1]["ok"])
        self.assertEqual("/api/runner/start", start_records[-1]["route"])
        self.assertIn("timestamp", start_records[-1])
        self.assertIn("result", start_records[-1])

        reload_records = self._read_web_action_audit(reload_run_dir)
        self.assertEqual("runner.reload", reload_records[-1]["action"])
        self.assertEqual("reloaded", reload_records[-1]["status"])

        restart_records = self._read_web_action_audit(restart_run_dir)
        restart_actions = [record["action"] for record in restart_records]
        self.assertIn("runner.restart", restart_actions)
        self.assertEqual("runner.stop", restart_records[-1]["action"])
        self.assertEqual("stopped", restart_records[-1]["status"])
        self.assertTrue(restart_records[-1]["ok"])

    def test_unbound_failed_web_actions_write_repo_level_audit_not_historical_run(self) -> None:
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        bad_runner = client.post("/api/runner/start", json={"confirmation": "WRONG"})
        self.assertEqual(400, bad_runner.status_code)

        missing_worktree = client.post("/api/worktree/merge", json={"confirmation": "MERGE WORKTREE"})
        self.assertEqual(409, missing_worktree.status_code)

        repo_records = self._read_web_action_audit()
        actions = [record["action"] for record in repo_records]
        self.assertIn("runner.start", actions)
        self.assertIn("worktree.merge", actions)
        self.assertFalse(any(record["ok"] for record in repo_records))
        self.assertEqual("", repo_records[-1]["run_dir"])
        self.assertFalse((self.run_dir / "WEB_ACTION_AUDIT.jsonl").exists())

    def test_stopped_reload_restart_start_only_does_not_write_stop_artifacts(self) -> None:
        class StopArtifactController(FakeRunnerController):
            def stop(self, *, wait: bool = False) -> dict[str, object]:
                result = super().stop(wait=wait)
                if self.run_dir is not None:
                    _write(self.run_dir / "STOP", "stop requested\n")
                    _write(self.run_dir / "STOP_PROGRESS.json", "{}\n")
                    _write(self.run_dir / "stop_progress.log", "stop requested\n")
                return result

        for action, phrase, expected_status in [
            ("reload", "RELOAD RUNNER", "reload_started"),
            ("restart", "RESTART RUNNER", "restart_started"),
        ]:
            with self.subTest(action=action):
                historical_run_dir = self.repo / ".AgentCLI" / "agent_runs" / f"historical-{action}"
                historical_run_dir.mkdir(parents=True, exist_ok=True)
                _write(historical_run_dir / "run_summary.json", json.dumps({"final": {"rc": 0, "reason": "project_complete"}}, ensure_ascii=False) + "\n")

                controller = StopArtifactController(
                    repo=self.repo,
                    base_args=_runner_base_args(self.config_path),
                )
                controller.run_dir = historical_run_dir
                controller._running = False
                client, app = _create_client(
                    self.repo,
                    enable_runner_controls=True,
                    config_path=self.config_path,
                    runner_controller=controller,
                )

                response = client.post(f"/api/runner/{action}", json={"confirmation": phrase})
                self.assertEqual(200, response.status_code)
                body = response.json()
                self.assertTrue(body["ok"])
                self.assertEqual(expected_status, body["status"])
                self.assertTrue(body["result"]["stop"]["skipped"])
                self.assertEqual("runner_not_running", body["result"]["stop"]["reason"])
                self.assertEqual(1, app.state.runner_controller.start_calls)
                self.assertEqual([], app.state.runner_controller.stop_calls)
                self.assertNotEqual(historical_run_dir.resolve(), Path(body["result"]["start"]["run_dir"]).resolve())
                self.assertFalse((historical_run_dir / "STOP").exists())
                self.assertFalse((historical_run_dir / "STOP_PROGRESS.json").exists())
                self.assertFalse((historical_run_dir / "stop_progress.log").exists())

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

    def test_runner_start_rejects_launch_paths_outside_approved_roots(self) -> None:
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        outside_run_dir = (self.home / "runs" / "outside").resolve()
        response = client.post(
            "/api/runner/start",
            json={
                "confirmation": "START RUNNER",
                "start_options": {
                    "config_path": self.config_path.as_posix(),
                    "run_dir": outside_run_dir.as_posix(),
                },
            },
        )

        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("runner_start_options_invalid", payload["error"]["code"])
        errors = payload["error"]["details"]["errors"]
        self.assertTrue(any(item["field"] == "run_dir" and item["code"] == "outside_run_root" for item in errors))
        self.assertEqual(0, app.state.runner_controller.start_calls)
        self.assertEqual([], app.state.runner_controller.stop_calls)

        outside_config_path = (self._tmp / "outside-config" / "agentcli.json").resolve()
        _write_config(outside_config_path, self.repo)
        response = client.post(
            "/api/runner/start",
            json={
                "confirmation": "START RUNNER",
                "start_options": {
                    "config_path": outside_config_path.as_posix(),
                },
            },
        )

        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("runner_start_options_invalid", payload["error"]["code"])
        errors = payload["error"]["details"]["errors"]
        self.assertTrue(any(item["field"] == "config_path" and item["code"] == "outside_config_root" for item in errors))
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
        self.assertIn("LAN safety", blocked_status["message"])
        self.assertIn("trusted-operator", blocked_status["message"])
        self.assertFalse(blocked_status["runner_control"]["actions"]["start"]["enabled"])
        self.assertFalse(blocked_status["runner_control"]["actions"]["restart"]["enabled"])

        blocked_response = blocked_client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
        self.assertEqual(403, blocked_response.status_code)
        blocked_body = blocked_response.json()
        self.assertFalse(blocked_body["ok"])
        self.assertEqual("lan_safety_mutation_blocked", blocked_body["error"]["code"])
        self.assertEqual("lan", blocked_body["error"]["details"]["scope"])
        self.assertIn("LAN safety", blocked_body["error"]["details"]["reason"])
        self.assertFalse(blocked_app.state.runner_controller.start_calls)

        trusted_client, trusted_app = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            host="0.0.0.0",
            trusted_network=True,
        )

        trusted_status = trusted_client.get("/api/runner/status").json()
        self.assertFalse(trusted_status["enabled"])
        self.assertEqual("cli;cli:--trusted-network", trusted_status["source"])
        self.assertIn("LAN safety", trusted_status["message"])
        self.assertFalse(trusted_status["runner_control"]["actions"]["start"]["enabled"])
        self.assertEqual("[redacted]", trusted_status["runner_control"]["status"]["config_path"])

        trusted_response = trusted_client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
        self.assertEqual(403, trusted_response.status_code)
        trusted_body = trusted_response.json()
        self.assertFalse(trusted_body["ok"])
        self.assertEqual("lan_safety_mutation_blocked", trusted_body["error"]["code"])
        self.assertEqual("lan_safety_blocked", trusted_body["status"])
        self.assertEqual(0, trusted_app.state.runner_controller.start_calls)

    def test_artifact_open_helper_serves_only_safe_agentcli_artifacts(self) -> None:
        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)
        report = self.run_dir / "FINAL_RUN_REPORT.md"
        _write(report, "# Final report\n\nSafe artifact body.\n")

        relative_response = client.get("/api/artifacts/open", params={"path": report.relative_to(self.repo).as_posix()})
        self.assertEqual(200, relative_response.status_code)
        self.assertIn("Safe artifact body.", relative_response.text)
        self.assertEqual("nosniff", relative_response.headers.get("x-content-type-options"))
        self.assertEqual("no-store", relative_response.headers.get("cache-control"))
        self.assertIn("inline", relative_response.headers.get("content-disposition", ""))

        absolute_response = client.get("/api/artifacts/open", params={"path": report.as_posix(), "download": "true"})
        self.assertEqual(200, absolute_response.status_code)
        self.assertIn("attachment", absolute_response.headers.get("content-disposition", ""))

        outside = self.repo / "README.md"
        _write(outside, "outside\n")
        outside_response = client.get("/api/artifacts/open", params={"path": outside.as_posix()})
        self.assertEqual(403, outside_response.status_code)
        self.assertEqual("artifact_path_outside_agentcli_root", outside_response.json()["error"]["code"])

        unsupported = self.run_dir / "experience.db"
        _write(unsupported, "sqlite-ish\n")
        unsupported_response = client.get("/api/artifacts/open", params={"path": unsupported.as_posix()})
        self.assertEqual(415, unsupported_response.status_code)
        self.assertEqual("artifact_type_unsupported", unsupported_response.json()["error"]["code"])

        missing_response = client.get("/api/artifacts/open", params={"path": (self.run_dir / "missing.md").as_posix()})
        self.assertEqual(404, missing_response.status_code)
        self.assertEqual("artifact_not_found", missing_response.json()["error"]["code"])

        traversal_response = client.get("/api/artifacts/open", params={"path": ".AgentCLI/agent_runs/../web_console.lock.json"})
        self.assertEqual(400, traversal_response.status_code)
        self.assertEqual("artifact_path_traversal", traversal_response.json()["error"]["code"])

        directory_response = client.get("/api/artifacts/open", params={"path": self.run_dir.as_posix()})
        self.assertEqual(400, directory_response.status_code)
        self.assertEqual("artifact_not_file", directory_response.json()["error"]["code"])

        large_artifact = self.run_dir / "too-large.log"
        large_artifact.write_text("x" * (25 * 1024 * 1024 + 1), encoding="utf-8")
        large_response = client.get("/api/artifacts/open", params={"path": large_artifact.as_posix()})
        self.assertEqual(413, large_response.status_code)
        self.assertEqual("artifact_too_large", large_response.json()["error"]["code"])

    def test_artifact_open_helper_is_blocked_on_lan_binds(self) -> None:
        report = self.run_dir / "FINAL_RUN_REPORT.md"
        _write(report, "# Final report\n\nsecret-token should not stream on LAN.\n")
        client, _ = _create_client(
            self.repo,
            enable_runner_controls=False,
            config_path=self.config_path,
            host="0.0.0.0",
            trusted_network=True,
        )

        response = client.get("/api/artifacts/open", params={"path": report.as_posix()})
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertEqual("artifact_open_redaction_blocked", payload["error"]["code"])
        self.assertEqual("artifact-open", payload["error"]["details"]["blocked_action"])
        self.assertNotIn("secret-token", json.dumps(payload, ensure_ascii=False))

    def test_lan_mutating_actions_are_rejected_even_with_opt_in_and_trusted_network(self) -> None:
        controller = FakeRunnerController(repo=self.repo, base_args=_runner_base_args(self.config_path))
        client, app = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            host="0.0.0.0",
            trusted_network=True,
            runner_controller=controller,
        )

        mutation_requests = [
            ("/api/runner/start", {"confirmation": "START RUNNER"}),
            ("/api/runner/stop", {"confirmation": "STOP RUNNER"}),
            ("/api/runner/reload", {"confirmation": "RELOAD RUNNER"}),
            ("/api/runner/restart", {"confirmation": "RESTART RUNNER"}),
            ("/api/config/save", {"changes": [{"path": "iterations", "value": 6}]}),
            ("/api/config/restore", {"backup_path": "agentcli.20260426.bak.json", "confirm": "RESTORE CONFIG BACKUP"}),
            ("/api/prompts/save", {"id": "pm_bootstrap", "file": "pm_bootstrap_prompt.md", "content": "Repo: {repo} {analysis_md}\n"}),
            ("/api/prompts/restore", {"id": "pm_bootstrap", "file": "pm_bootstrap_prompt.md", "backup_path": "pm_bootstrap_prompt.bak.md", "confirm": "RESTORE BACKUP"}),
            ("/api/goals/save", {"draft": {"p0": [self._goal_item("Add FastAPI web console", done=False)], "p1": []}}),
            ("/api/worktree/merge", {"confirmation": "MERGE WORKTREE"}),
            ("/api/worktree/discard", {"confirmation": "DISCARD WORKTREE"}),
        ]

        for path, body in mutation_requests:
            with self.subTest(path=path):
                response = client.post(path, json=body)
                self.assertEqual(403, response.status_code)
                payload = response.json()
                self.assertFalse(payload["ok"])
                self.assertEqual("lan_safety_mutation_blocked", payload["error"]["code"])
                self.assertEqual("lan", payload["error"]["details"]["scope"])
                self.assertIn("LAN safety", payload["error"]["message"])

        self.assertEqual(0, app.state.runner_controller.start_calls)
        self.assertEqual([], app.state.runner_controller.stop_calls)

    def test_repo_web_instance_lock_allows_primary_owner_and_same_process_reuse(self) -> None:
        client, _ = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
        )
        payload = client.get("/api/status").json()
        self.assertEqual("primary", payload["web_instance"]["state"])
        self.assertEqual("read_write", payload["web_instance"]["mode"])
        self.assertTrue(payload["runner_control"]["enabled"])
        self.assertTrue(payload["runner_control"]["actions"]["start"]["enabled"])
        self.assertFalse(payload["web_instance"]["same_owner"])

        lock_path = Path(payload["web_instance"]["lock_path"])
        self.assertTrue(lock_path.exists())
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8", errors="replace"))
        self.assertEqual(self.repo.as_posix(), lock_payload["repo_root"])
        self.assertEqual(8000, lock_payload["port"])
        self.assertEqual("enabled", lock_payload["runner_control_state"])

        second_client, _ = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
        )
        second_payload = second_client.get("/api/status").json()
        self.assertEqual("primary", second_payload["web_instance"]["state"])
        self.assertEqual("read_write", second_payload["web_instance"]["mode"])
        self.assertTrue(second_payload["runner_control"]["enabled"])
        self.assertTrue(second_payload["runner_control"]["actions"]["start"]["enabled"])

    def test_live_duplicate_web_instance_is_read_only_and_refuses_mutations(self) -> None:
        proc = self._spawn_sleep_process()
        try:
            _write(
                self._web_instance_lock_path(),
                json.dumps(
                    {
                        "schema_version": 1,
                        "repo_root": self.repo.as_posix(),
                        "pid": proc.pid,
                        "created_at": "2026-04-29T00:00:00Z",
                        "host": "127.0.0.1",
                        "port": 8123,
                        "hostname": "duplicate-owner",
                        "state": "primary",
                        "mode": "read_write",
                        "runner_control_state": "enabled",
                        "runner_control_enabled": True,
                        "runner_control_requested": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )

            client, app = _create_client(
                self.repo,
                enable_runner_controls=True,
                config_path=self.config_path,
            )
            payload = client.get("/api/status").json()
            self.assertEqual("duplicate", payload["web_instance"]["state"])
            self.assertEqual("read_only", payload["web_instance"]["mode"])
            self.assertEqual("duplicate", payload["sectionState"]["runnerControl"]["state"])
            self.assertFalse(payload["runner_control"]["enabled"])
            self.assertTrue(payload["runner_control"]["read_only"])
            self.assertTrue(payload["runner_control"]["duplicate_instance"])
            self.assertFalse(payload["runner_control"]["actions"]["start"]["enabled"])
            self.assertFalse(payload["runner_control"]["actions"]["restart"]["enabled"])
            self.assertIn("read-only", payload["runner_control"]["message"])
            self.assertEqual(proc.pid, payload["web_instance"]["owner"]["pid"])

            response = client.post("/api/runner/start", json={"confirmation": "START RUNNER"})
            self.assertEqual(409, response.status_code)
            body = response.json()
            self.assertFalse(body["ok"])
            self.assertEqual("runner_controls_duplicate_instance", body["error"]["code"])
            self.assertEqual("duplicate", body["status"])
            self.assertFalse(app.state.runner_controller.start_calls)
        finally:
            self._terminate_sleep_process(proc)

    def test_stale_web_instance_lock_with_reused_pid_signature_is_reclaimed(self) -> None:
        from agent_runner.process_guard import _pid_create_time_ticks

        proc = self._spawn_sleep_process()
        try:
            actual_create_time = _pid_create_time_ticks(proc.pid)
            self.assertIsNotNone(actual_create_time)
            bogus_executable = (self._tmp / "bogus" / "web-console.exe").as_posix()
            _write(
                self._web_instance_lock_path(),
                json.dumps(
                    {
                        "schema_version": 1,
                        "repo_root": self.repo.as_posix(),
                        "pid": proc.pid,
                        "pid_create_time": int(actual_create_time) + 1,
                        "process_executable": bogus_executable,
                        "processExecutable": bogus_executable,
                        "created_at": "2026-04-29T00:00:00Z",
                        "host": "127.0.0.1",
                        "port": 8124,
                        "hostname": "stale-owner",
                        "state": "primary",
                        "mode": "read_write",
                        "runner_control_state": "enabled",
                        "runner_control_enabled": True,
                        "runner_control_requested": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )

            client, _ = _create_client(
                self.repo,
                enable_runner_controls=True,
                config_path=self.config_path,
            )
            payload = client.get("/api/status").json()
            self.assertEqual("primary", payload["web_instance"]["state"])
            self.assertEqual("read_write", payload["web_instance"]["mode"])
            self.assertTrue(payload["web_instance"]["stale_reclaimed"])
            self.assertTrue(payload["web_instance"]["liveness"]["deterministic"])
            self.assertIn(payload["web_instance"]["liveness"]["reason"], {"pid_reused", "process_executable_mismatch"})
            self.assertTrue(payload["runner_control"]["enabled"])
            self.assertTrue(payload["runner_control"]["actions"]["start"]["enabled"])

            lock_payload = json.loads(self._web_instance_lock_path().read_text(encoding="utf-8", errors="replace"))
            self.assertEqual(os.getpid(), lock_payload["pid"])
            self.assertEqual("enabled", lock_payload["runner_control_state"])
            self.assertNotEqual(proc.pid, lock_payload["pid"])
        finally:
            self._terminate_sleep_process(proc)

    def test_stale_web_instance_lock_with_reused_pid_executable_mismatch_is_reclaimed(self) -> None:
        from agent_runner.process_guard import _pid_create_time_ticks

        proc = self._spawn_sleep_process()
        try:
            actual_create_time = _pid_create_time_ticks(proc.pid)
            self.assertIsNotNone(actual_create_time)
            bogus_executable = (self._tmp / "bogus" / "web-console.exe").as_posix()
            _write(
                self._web_instance_lock_path(),
                json.dumps(
                    {
                        "schema_version": 1,
                        "repo_root": self.repo.as_posix(),
                        "pid": proc.pid,
                        "process_executable": bogus_executable,
                        "processExecutable": bogus_executable,
                        "created_at": "2026-04-29T00:00:00Z",
                        "host": "127.0.0.1",
                        "port": 8125,
                        "hostname": "stale-owner",
                        "state": "primary",
                        "mode": "read_write",
                        "runner_control_state": "enabled",
                        "runner_control_enabled": True,
                        "runner_control_requested": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )

            client, _ = _create_client(
                self.repo,
                enable_runner_controls=True,
                config_path=self.config_path,
            )
            payload = client.get("/api/status").json()
            self.assertEqual("primary", payload["web_instance"]["state"])
            self.assertEqual("read_write", payload["web_instance"]["mode"])
            self.assertTrue(payload["web_instance"]["stale_reclaimed"])
            self.assertTrue(payload["web_instance"]["liveness"]["deterministic"])
            self.assertEqual("process_executable_mismatch", payload["web_instance"]["liveness"]["reason"])
            self.assertTrue(payload["runner_control"]["enabled"])
            self.assertTrue(payload["runner_control"]["actions"]["start"]["enabled"])

            lock_payload = json.loads(self._web_instance_lock_path().read_text(encoding="utf-8", errors="replace"))
            self.assertEqual(os.getpid(), lock_payload["pid"])
            self.assertEqual("enabled", lock_payload["runner_control_state"])
            self.assertNotEqual(proc.pid, lock_payload["pid"])
        finally:
            self._terminate_sleep_process(proc)

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

    def test_runner_control_artifact_hydrates_controller_web_and_shell_views(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        from agent_runner.remote.controller import RunnerController, write_runner_control_event
        from agent_runner.shell import RunnerShell

        write_runner_control_event(
            self.run_dir,
            action="reload",
            status="reloaded",
            message="Runner reloaded.",
            error="",
            ok=True,
            source="controller",
            repo=self.repo.as_posix(),
            config_path=self.config_path.as_posix(),
            running=False,
            runner_mode="thread",
        )

        controller = RunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(
                config_path=self.config_path.as_posix(),
                config=self.config_path.as_posix(),
                run_dir=self.run_dir.as_posix(),
            ),
            runner_mode="thread",
        )
        controller_status = controller.status()
        self.assertEqual("reload", controller_status["last_action"])
        self.assertEqual("Runner reloaded.", controller_status["last_message"])
        self.assertEqual("", controller_status["last_error"])
        self.assertEqual("reload", controller_status["current_event"]["action"])
        self.assertEqual("reloaded", controller_status["current_event"]["status"])
        self.assertEqual(1, controller_status["event_count"])
        self.assertEqual(1, len(controller_status["history"]))
        self.assertEqual("reload", controller_status["history"][0]["action"])

        client, _ = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            host="0.0.0.0",
            trusted_network=True,
            runner_controller=controller,
        )
        response = client.get("/api/runner/status")
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertEqual("reload", payload["runner_control"]["status"]["last_action"])
        self.assertEqual("Runner reloaded.", payload["runner_control"]["status"]["last_message"])
        self.assertEqual("", payload["runner_control"]["status"]["last_error"])
        self.assertEqual("[redacted]", payload["runner_control"]["status"]["config_path"])
        self.assertEqual("reload", payload["runner_control"]["status"]["current_event"]["action"])
        self.assertEqual("reloaded", payload["runner_control"]["status"]["current_event"]["status"])
        self.assertEqual(1, payload["runner_control"]["status"]["event_count"])
        self.assertEqual("reload", payload["runner_control"]["status"]["history"][0]["action"])
        self.assertEqual("reload", payload["runner_control"]["last_action"])
        self.assertEqual("Runner reloaded.", payload["runner_control"]["last_message"])
        self.assertIn("LAN safety", payload["runner_control"]["message"])
        self.assertFalse(payload["runner_control"]["enabled"])

        shell = RunnerShell([], controller=None)
        shell.set_repo(self.repo.as_posix())
        shell.set_config_path(self.config_path.as_posix())
        shell.run_dir = self.run_dir
        stdout = StringIO()
        with redirect_stdout(stdout):
            shell.status()
        rendered = stdout.getvalue()
        self.assertIn("last_event: reloaded", rendered)
        self.assertIn("last_action: reload", rendered)
        self.assertIn("last_message: Runner reloaded.", rendered)

    def test_runner_controller_validation_error_writes_artifact_event(self) -> None:
        from agent_runner.remote.controller import RunnerController, read_runner_control_event

        controller = RunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(
                config_path=self.config_path.as_posix(),
                config=self.config_path.as_posix(),
                run_dir=self.run_dir.as_posix(),
            ),
        )
        controller.run_dir = self.run_dir
        self.run_dir.mkdir(parents=True, exist_ok=True)

        result = controller.start({"execution_backend": "bogus"}, control_action="start")
        self.assertFalse(result["ok"])

        event = read_runner_control_event(self.run_dir)
        self.assertEqual("start", event["action"])
        self.assertEqual("error", event["status"])
        self.assertEqual("validation", event["phase"])
        self.assertEqual(1, event["event_count"])
        self.assertEqual("start", event["current_event"]["action"])
        self.assertEqual("error", event["current_event"]["status"])
        self.assertEqual(1, len(event["history"]))
        self.assertEqual("error", event["history"][0]["status"])

    def test_runner_start_response_surfaces_exact_readiness_blockers(self) -> None:
        from fastapi.testclient import TestClient
        import agent_runner.web as web_module
        from agent_runner.remote.controller import RunnerController, read_runner_control_event

        self._init_repo()
        (self.run_dir / "STOP").write_text("stop_file\n", encoding="utf-8")
        write_stop_progress(
            self.run_dir,
            phase="runner_wait",
            message="Waiting for runner shutdown and final artifacts.",
            requested_at_monotonic=0.0,
            running=True,
            runner_alive=True,
        )
        controller = RunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(
                config_path=self.config_path.as_posix(),
                config=self.config_path.as_posix(),
                stop_file="STOP",
            ),
        )

        with patch.object(web_module, "_build_runner_controller", return_value=controller):
            app = web_module.create_app(
                self.repo,
                web_dir=WEB_CONSOLE,
                enable_runner_controls=True,
                config_path=str(self.config_path),
            )
            with TestClient(app) as client:
                response = client.post(
                    "/api/runner/start",
                    json={
                        "confirmation": "START RUNNER",
                        "start_options": {
                            "run_dir": self.run_dir.as_posix(),
                        },
                    },
                )

        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("runner_start_readiness_failed", payload["error"]["code"])
        readiness = payload["error"]["details"]["readiness"]
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("missing_source_venv", blocker_codes)
        self.assertIn("stale_stop_artifact", blocker_codes)
        self.assertIn("stale_runner_wait_artifact", blocker_codes)
        self.assertEqual(self.run_dir.as_posix(), payload["result"]["run_dir"])

        event = read_runner_control_event(self.run_dir)
        self.assertEqual("error", event["status"])
        self.assertEqual("readiness", event["phase"])
        self.assertEqual("runner_start_readiness_failed", event["result"]["error"]["code"])

    def test_runner_start_response_surfaces_stale_telegram_lock_blocker(self) -> None:
        import hashlib
        import warnings
        from fastapi.testclient import TestClient
        import agent_runner.web as web_module
        from agent_runner.remote.controller import RunnerController, read_runner_control_event

        self._init_repo()
        self._ensure_source_venv()
        temp_dir = self._tmp / "telegram-locks"
        temp_dir.mkdir(parents=True, exist_ok=True)
        token = "stale-telegram-web-token"
        token_fingerprint = hashlib.sha256(token.encode("utf-8")).hexdigest()[:10]
        lock_path = temp_dir / f"agentcli_tg_{token_fingerprint}.lock"
        old_time = time.time() - 900
        _write(
            lock_path,
            json.dumps(
                {
                    "pid": 999999,
                    "instance": "web-stale-telegram",
                    "repo": self.repo.as_posix(),
                    "started_unix": old_time,
                    "token_fingerprint": token_fingerprint,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        os.utime(lock_path, (old_time, old_time))
        controller = RunnerController(
            repo=self.repo,
            base_args=SimpleNamespace(
                config_path=self.config_path.as_posix(),
                config=self.config_path.as_posix(),
                stop_file="STOP",
            ),
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ResourceWarning)
            with (
                patch.object(web_module, "_build_runner_controller", return_value=controller),
                patch.object(web_module, "init_process_guard"),
                patch.object(web_module, "terminate_all_children"),
                patch("agent_runner.preflight.tempfile.gettempdir", return_value=temp_dir.as_posix()),
            ):
                app = web_module.create_app(
                    self.repo,
                    web_dir=WEB_CONSOLE,
                    enable_runner_controls=True,
                    config_path=str(self.config_path),
                )
                with TestClient(app) as client:
                    response = client.post(
                        "/api/runner/start",
                        json={
                            "confirmation": "START RUNNER",
                            "start_options": {
                                "run_dir": self.run_dir.as_posix(),
                            },
                        },
                    )

        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("runner_start_readiness_failed", payload["error"]["code"])
        readiness = payload["error"]["details"]["readiness"]
        blocker_codes = {item["code"] for item in readiness["blockers"]}
        self.assertIn("stale_telegram_token_lock", blocker_codes)
        blocker = next(item for item in readiness["blockers"] if item["code"] == "stale_telegram_token_lock")
        self.assertEqual(token_fingerprint, blocker["details"]["owner"]["token_fingerprint"])
        self.assertEqual("web-stale-telegram", blocker["details"]["owner"]["instance"])

        event = read_runner_control_event(self.run_dir)
        self.assertEqual("error", event["status"])
        self.assertEqual("readiness", event["phase"])
        self.assertEqual("runner_start_readiness_failed", event["result"]["error"]["code"])

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
        audit_records = self._read_web_action_audit(self.run_dir)
        self.assertEqual("worktree.merge", audit_records[-1]["action"])
        self.assertEqual("applied", audit_records[-1]["status"])
        self.assertTrue(audit_records[-1]["ok"])
        self.assertEqual("applied", audit_records[-1]["result"]["status"])
        self.assertIn("timestamp", audit_records[-1])

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
        audit_records = self._read_web_action_audit(self.run_dir)
        self.assertEqual("worktree.discard", audit_records[-1]["action"])
        self.assertEqual("discarded", audit_records[-1]["status"])
        self.assertTrue(audit_records[-1]["ok"])
        self.assertEqual("discarded", audit_records[-1]["result"]["status"])

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
        self.assertEqual("src/app.py", payload["error"]["details"]["failed_files"][0]["path"])
        self.assertEqual("src/app.py", payload["error"]["details"]["apply_check"]["failed_files"][0]["path"])
        self.assertEqual("@@ -1 +1 @@", payload["error"]["details"]["failed_hunks"][0]["header"])
        self.assertEqual("@@ -1 +1 @@", payload["error"]["details"]["apply_check"]["failed_hunks"][0]["header"])
        self.assertEqual("failed", payload["worktree"]["applyCheck"]["status"])
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(snapshot["worktree"]["sourceRepo"], payload["worktree"]["sourceRepo"])
        audit_records = self._read_web_action_audit(self.run_dir)
        audit = audit_records[-1]
        self.assertEqual("worktree.merge", audit["action"])
        self.assertFalse(audit["ok"])
        self.assertEqual("worktree_patch_check_failed", audit["error_code"])
        self.assertEqual("[redacted]", audit["details"]["output"])
        self.assertEqual("[redacted]", audit["details"]["apply_check"]["output"])
        audit_text = self._web_action_audit_path(self.run_dir).read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("this context does not exist", audit_text)
        self.assertNotIn("still invalid", audit_text)

    def test_worktree_merge_reports_patch_apply_failure_details_and_keeps_pending_state(self) -> None:
        from agent_runner.web import build_snapshot

        fixture = self._prepare_pending_worktree()
        snapshot = build_snapshot(self.repo)
        worktree = snapshot["worktree"]
        body = self._worktree_action_payload(worktree, confirmation="MERGE WORKTREE")
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        def fake_run_cmd(cmd, cwd, timeout_sec=600):
            if cmd[:5] == ["git", "apply", "--check", "--binary", "--whitespace=nowarn"]:
                return 0, ""
            if cmd[:4] == ["git", "apply", "--binary", "--whitespace=nowarn"]:
                return 1, "error: patch failed: src/app.py:1\nerror: src/app.py: patch does not apply\n"
            completed = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            return completed.returncode, (completed.stdout or "") + (completed.stderr or "")

        with patch("agent_runner.gitops.run_cmd", side_effect=fake_run_cmd):
            response = client.post("/api/worktree/merge", json=body)

        self.assertEqual(409, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("worktree_patch_apply_failed", payload["error"]["code"])
        self.assertEqual("src/app.py", payload["error"]["details"]["failed_files"][0]["path"])
        self.assertEqual("src/app.py", payload["error"]["details"]["failed_hunks"][0]["path"])
        self.assertEqual("@@ -1 +1 @@", payload["error"]["details"]["failed_hunks"][0]["header"])
        self.assertTrue(self.pending_path.exists())
        self.assertEqual(snapshot["worktree"]["sourceRepo"], payload["worktree"]["sourceRepo"])

    def test_pr_queue_merge_records_approval_without_committing(self) -> None:
        packet = self._prepare_pr_queue_merge_packet()
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        confirmation = f"MERGE PR {packet['packet_id']}"
        head_before = self._git("rev-parse", "HEAD")

        response = client.post(
            "/api/pr-queue/merge",
            json={"packetId": packet["packet_id"], "confirmation": confirmation},
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("merge", payload["action"])
        self.assertEqual("approved", payload["status"])
        self.assertEqual("approved", payload["result"]["status"])
        self.assertEqual(head_before, self._git("rev-parse", "HEAD"))

        packet_data = json.loads(packet["packet_path"].read_text(encoding="utf-8"))
        self.assertEqual("approved", packet_data["status"])
        self.assertEqual("validation_passed", packet_data["validation_status"])
        self.assertEqual(confirmation, packet_data["approval"]["required_phrase"])

    def test_pr_queue_merge_rejects_approval_mismatch(self) -> None:
        packet = self._prepare_pr_queue_merge_packet()
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        response = client.post(
            "/api/pr-queue/merge",
            json={"packetId": packet["packet_id"], "confirmation": "WRONG"},
        )
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("approval_mismatch", payload["error"]["code"])

    def test_pr_queue_merge_is_disabled_until_opt_in(self) -> None:
        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)

        response = client.post(
            "/api/pr-queue/merge",
            json={"packetId": "pr-queue-demo", "confirmation": "MERGE PR pr-queue-demo"},
        )
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("pr_queue_actions_disabled", payload["error"]["code"])

    def test_pr_queue_merge_is_blocked_on_lan_binds_without_trusted_network(self) -> None:
        client, _ = _create_client(
            self.repo,
            enable_runner_controls=True,
            config_path=self.config_path,
            host="0.0.0.0",
            trusted_network=False,
        )

        response = client.post(
            "/api/pr-queue/merge",
            json={"packetId": "pr-queue-demo", "confirmation": "MERGE PR pr-queue-demo"},
        )
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("lan_safety_mutation_blocked", payload["error"]["code"])

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
        from agent_runner.utils import atomic_write_json as real_atomic_write_json

        _write_config(self.config_path, self.repo, iterations=2, prompts_dir="prompts/agentcli")
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        original = self.config_path.read_text(encoding="utf-8")
        with patch("agent_runner.web_config.atomic_write_json", wraps=real_atomic_write_json) as atomic_write:
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
        self.assertIn(
            self.config_path.resolve(),
            [Path(call.args[0]).resolve() for call in atomic_write.call_args_list],
        )

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(4, saved["iterations"])
        self.assertEqual("prompts/agentcli-updated", saved["prompts_dir"])
        self.assertEqual("subprocess", saved["telegram"]["runner_mode"])

        controller = app.state.runner_controller
        self.assertEqual("subprocess", controller.runner_mode)
        self.assertEqual("subprocess", controller.base_args.telegram["runner_mode"])

    def test_config_prompt_and_goals_mutations_write_redacted_web_action_audit(self) -> None:
        _write_config(self.config_path, self.repo, iterations=2, prompts_dir="prompts/agentcli")
        prompts_dir = self.home / "prompts" / "agentcli"
        prompt_path = prompts_dir / "pm_bootstrap_prompt.md"
        original_prompt = (ROOT / "templates" / "agent_prompts" / "pm_bootstrap_prompt.md").read_text(encoding="utf-8")
        prompt_secret_marker = "DO_NOT_LOG_PROMPT_SECRET_20260506"
        goal_secret_marker = "DO_NOT_LOG_GOAL_TEXT_20260506"
        _write(prompt_path, original_prompt)

        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        config_response = client.post("/api/config/save", json={"changes": [{"path": "iterations", "value": 5}]})
        self.assertEqual(200, config_response.status_code)

        prompt_response = client.post(
            "/api/prompts/save",
            json={
                "id": "pm_bootstrap",
                "file": "pm_bootstrap_prompt.md",
                "content": original_prompt + f"\n# {prompt_secret_marker}\n",
            },
        )
        self.assertEqual(200, prompt_response.status_code)

        goals_response = client.post(
            "/api/goals/save",
            json={
                "draft": {
                    "p0": [
                        self._goal_item("Expose read-only progress views", done=True),
                        self._goal_item("Add FastAPI web console", done=False),
                    ],
                    "p1": [self._goal_item(goal_secret_marker, done=False)],
                }
            },
        )
        self.assertEqual(200, goals_response.status_code)

        records = self._read_web_action_audit()
        actions = [record["action"] for record in records]
        self.assertIn("config.save", actions)
        self.assertIn("prompt.save", actions)
        self.assertIn("goals.save", actions)

        config_record = next(record for record in records if record["action"] == "config.save")
        self.assertEqual("saved", config_record["status"])
        self.assertTrue(config_record["ok"])
        self.assertEqual(["iterations"], config_record["result"]["changed_paths"])
        self.assertIn("timestamp", config_record)

        prompt_record = next(record for record in records if record["action"] == "prompt.save")
        self.assertEqual("pm_bootstrap", prompt_record["result"]["prompt_id"])
        self.assertEqual(len(original_prompt + f"\n# {prompt_secret_marker}\n"), prompt_record["result"]["content_length"])

        goals_record = next(record for record in records if record["action"] == "goals.save")
        self.assertEqual({"p0": 2, "p1": 1}, goals_record["result"]["goal_counts"])
        self.assertEqual({"p0": 1, "p1": 0}, goals_record["result"]["checked_counts"])

        audit_text = self._web_action_audit_path().read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("initial-bot-token", audit_text)
        self.assertNotIn(prompt_secret_marker, audit_text)
        self.assertNotIn(goal_secret_marker, audit_text)

    def test_config_prompt_and_goals_failures_write_redacted_web_action_audit(self) -> None:
        _write_config(self.config_path, self.repo)
        prompts_dir = self.home / "prompts" / "agentcli"
        prompt_path = prompts_dir / "pm_bootstrap_prompt.md"
        original_prompt = (ROOT / "templates" / "agent_prompts" / "pm_bootstrap_prompt.md").read_text(encoding="utf-8")
        goal_secret_marker = "DO_NOT_LOG_FAILED_GOAL_TEXT_20260506"
        _write(prompt_path, original_prompt)

        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        config_response = client.post("/api/config/save", json={"changes": [{"path": "telegram.bot_token", "value": "[redacted]"}]})
        self.assertEqual(400, config_response.status_code)

        prompt_response = client.post(
            "/api/prompts/save",
            json={
                "id": "pm_bootstrap",
                "file": "pm_bootstrap_prompt.md",
                "content": "Repo only: {repo}\n",
            },
        )
        self.assertEqual(400, prompt_response.status_code)

        _write(
            self.goals_path,
            f"""# Project Goals

## P0
- [x] Expose read-only progress views
- [ ] {goal_secret_marker}

## P1
""",
        )
        goals_response = client.post(
            "/api/goals/save",
            json={
                "draft": {
                    "p0": [self._goal_item("Expose read-only progress views", done=True)],
                    "p1": [],
                }
            },
        )
        self.assertEqual(400, goals_response.status_code)

        records = self._read_web_action_audit()
        by_action = {record["action"]: record for record in records}
        self.assertEqual("invalid_request", by_action["config.save"]["status"])
        self.assertEqual("invalid_request", by_action["prompt.save"]["status"])
        self.assertEqual("goals_confirmation_required", by_action["goals.save"]["status"])
        self.assertFalse(by_action["config.save"]["ok"])
        self.assertFalse(by_action["prompt.save"]["ok"])
        self.assertFalse(by_action["goals.save"]["ok"])

        audit_text = self._web_action_audit_path().read_text(encoding="utf-8", errors="replace")
        self.assertNotIn("initial-bot-token", audit_text)
        self.assertNotIn("Repo only: {repo}", audit_text)
        self.assertNotIn(goal_secret_marker, audit_text)

    def test_config_save_normalizes_string_and_array_list_payloads_consistently(self) -> None:
        def _save_and_read(changes: list[dict[str, object]]) -> dict[str, object]:
            _write_config(
                self.config_path,
                self.repo,
                telegram={"allowed_chat_ids": [], "notify_events": []},
            )
            client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
            response = client.post("/api/config/save", json={"changes": changes})
            self.assertEqual(200, response.status_code)
            self.assertTrue(response.json()["ok"])
            return json.loads(self.config_path.read_text(encoding="utf-8"))

        saved_from_string = _save_and_read(
            [
                {"path": "telegram.allowed_chat_ids", "value": "101, 202"},
                {"path": "telegram.notify_events", "value": "run_start, task_done"},
            ]
        )
        saved_from_array = _save_and_read(
            [
                {"path": "telegram.allowed_chat_ids", "value": ["101", 202]},
                {"path": "telegram.notify_events", "value": ["run_start", "task_done"]},
            ]
        )

        self.assertEqual([101, 202], saved_from_string["telegram"]["allowed_chat_ids"])
        self.assertEqual(saved_from_string["telegram"]["allowed_chat_ids"], saved_from_array["telegram"]["allowed_chat_ids"])
        self.assertEqual(["run_start", "task_done"], saved_from_string["telegram"]["notify_events"])
        self.assertEqual(saved_from_string["telegram"]["notify_events"], saved_from_array["telegram"]["notify_events"])

    def test_config_save_preserves_plugin_roles_when_unrelated_fields_change(self) -> None:
        _write_config(
            self.config_path,
            self.repo,
            iterations=2,
            prompts_dir="prompts/agentcli",
            roles=["PM", "pkg.mod:Class", "QA"],
        )
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        response = client.post(
            "/api/config/save",
            json={
                "changes": [
                    {"path": "iterations", "value": 5},
                    {"path": "prompts_dir", "value": "prompts/agentcli-updated"},
                ]
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])

        saved = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], saved["roles"])
        self.assertEqual(5, saved["iterations"])
        self.assertEqual("prompts/agentcli-updated", saved["prompts_dir"])

        status_payload = client.get("/api/status").json()
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], status_payload["config"]["data"]["roles"])

    def test_config_save_normalizes_launch_fields_before_runner_start(self) -> None:
        _write_config(
            self.config_path,
            self.repo,
            roles=["PM", "Security", "Dev", "QA"],
            prompts_dir="prompts/agentcli",
            gitops={"untracked_exclude_globs": "*.log, .AgentCLI/**"},
            plugins_allowlist="plugin.alpha, plugin.beta",
            policy={"ignore_paths": "docs/**, tmp/**", "allow_patterns": "src/**, tests/**"},
            scan_ignore_paths="build/tmp, build/logs",
            failover_backends="codex, claudecode",
            failover_on="quota_exhausted, quota_utilization",
            dev_escalate_on="no_diff, build_failed",
            telegram={
                "enabled": True,
                "allowed_chat_ids": "101, 202",
                "notify_events": "run_start, task_done",
            },
        )
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        controller = app.state.runner_controller
        self.assertEqual("PM,Security,Dev,QA", controller.base_args.roles)
        self.assertEqual(["*.log", ".AgentCLI/**"], controller.base_args.gitops["untracked_exclude_globs"])
        self.assertEqual(["plugin.alpha", "plugin.beta"], controller.base_args.plugins_allowlist)
        self.assertEqual(["docs/**", "tmp/**"], controller.base_args.policy["ignore_paths"])
        self.assertEqual(["src/**", "tests/**"], controller.base_args.policy["allow_patterns"])
        self.assertEqual(["build/tmp", "build/logs"], controller.base_args.scan_ignore_paths)
        self.assertEqual(["codex", "claudecode"], controller.base_args.failover_backends)
        self.assertEqual(["quota_exhausted", "quota_utilization"], controller.base_args.failover_on)
        self.assertEqual(["no_diff", "build_failed"], controller.base_args.dev_escalate_on)
        self.assertEqual([101, 202], controller.base_args.telegram["allowed_chat_ids"])
        self.assertEqual(["run_start", "task_done"], controller.base_args.telegram["notify_events"])
        self.assertEqual((self.home / "prompts" / "agentcli").resolve().as_posix(), controller.base_args.prompts_dir)

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

    def test_config_save_reports_all_validation_errors(self) -> None:
        _write_config(self.config_path, self.repo)
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        before = self.config_path.read_text(encoding="utf-8")
        response = client.post(
            "/api/config/save",
            json={
                "changes": [
                    {"path": "iterations", "value": 0},
                    {"path": "telegram.bot_token", "value": "[redacted]"},
                ]
            },
        )
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("config_validation_failed", payload["error"]["code"])
        validation = payload["error"]["details"]["validation"]
        self.assertEqual(2, validation["error_count"])
        self.assertEqual(2, len(validation["errors"]))
        self.assertEqual({"iterations", "telegram.bot_token"}, {error["field"] for error in validation["errors"]})
        self.assertIn("config_value_out_of_range", {error["code"] for error in validation["errors"]})
        self.assertIn("config_redacted_placeholder", {error["code"] for error in validation["errors"]})
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))
        backups = list(self.config_path.parent.glob(f"{self.config_path.stem}.*.bak{self.config_path.suffix}"))
        self.assertEqual([], backups)

    def test_config_backup_listing_surfaces_recent_sibling_backups(self) -> None:
        _write_config(self.config_path, self.repo)
        config_dir = self.config_path.parent
        backup_data = {
            "repo": self.repo.as_posix(),
            "profile": "personal",
            "execution_backend": "codex",
            "iterations": 2,
            "prompts_dir": "prompts/agentcli",
        }
        older = self._config_backup("20260427-120000", data={**backup_data, "iterations": 3}, mtime=1_714_280_000.0)
        newer = self._config_backup("20260428-120000", data={**backup_data, "iterations": 4}, mtime=1_714_280_060.0)
        ignored = config_dir / f"{self.config_path.stem}.20260428-120000.txt"
        _write(ignored, "{}\n")

        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)
        payload = client.get("/api/config").json()

        self.assertEqual("/api/config/restore", payload["meta"]["restore_endpoint"])
        self.assertTrue(payload["meta"]["restore_enabled"])
        self.assertTrue(payload["meta"]["restore_requires_opt_in"])
        self.assertEqual([newer.as_posix(), older.as_posix()], [item["path"] for item in payload["backups"]])
        self.assertEqual(newer.name, payload["backups"][0]["name"])
        self.assertIn("bytes", payload["backups"][0]["summary"])
        self.assertNotIn(ignored.as_posix(), [item["path"] for item in payload["backups"]])

    def test_config_restore_creates_backup_and_reloads_selected_backup(self) -> None:
        _write_config(self.config_path, self.repo, iterations=2, prompts_dir="prompts/agentcli")
        original = self.config_path.read_text(encoding="utf-8")
        selected = self._config_backup(
            "20260428-121500",
            data={
                "repo": self.repo.as_posix(),
                "profile": "personal",
                "execution_backend": "codex",
                "iterations": 7,
                "prompts_dir": "prompts/agentcli",
                "telegram": {"enabled": True, "bot_token": "selected-token", "pairing_code": "PAIR-7777"},
            },
            mtime=1_714_280_500.0,
        )
        other = self._config_backup(
            "20260428-121000",
            data={
                "repo": self.repo.as_posix(),
                "profile": "personal",
                "execution_backend": "codex",
                "iterations": 3,
                "prompts_dir": "prompts/agentcli",
            },
            mtime=1_714_280_100.0,
        )
        client, app = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        response = client.post(
            "/api/config/restore",
            json={
                "backup_path": selected.as_posix(),
                "confirm": "RESTORE CONFIG BACKUP",
            },
        )
        self.assertEqual(200, response.status_code)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual("config-restore", payload["action"])
        self.assertEqual("restored", payload["status"])
        self.assertEqual(selected.as_posix(), payload["restored_from_path"])
        self.assertIn("backup_path", payload)
        self.assertIn("validation", payload)
        self.assertTrue(payload["validation"]["current"]["ok"])
        self.assertTrue(payload["validation"]["backup"]["ok"])
        self.assertTrue(payload["validation"]["restored"]["ok"])

        backup_path = Path(payload["backup_path"])
        self.assertTrue(backup_path.exists())
        self.assertEqual(json.loads(original), json.loads(backup_path.read_text(encoding="utf-8")))
        self.assertEqual(json.loads(selected.read_text(encoding="utf-8")), json.loads(self.config_path.read_text(encoding="utf-8")))
        self.assertEqual(7, json.loads(self.config_path.read_text(encoding="utf-8"))["iterations"])
        self.assertEqual(True, json.loads(self.config_path.read_text(encoding="utf-8"))["telegram"]["enabled"])
        self.assertEqual(self.config_path.as_posix(), payload["config_path"])
        self.assertEqual(7, payload["snapshot"]["config"]["data"]["iterations"])

        config_payload = client.get("/api/config").json()
        self.assertEqual(backup_path.as_posix(), config_payload["backups"][0]["path"])
        self.assertIn(other.as_posix(), [item["path"] for item in config_payload["backups"]])

        controller = app.state.runner_controller
        self.assertEqual(self.config_path.as_posix(), controller.base_args.config_path)

    def test_config_restore_rejects_confirmation_mismatch(self) -> None:
        _write_config(self.config_path, self.repo, iterations=2)
        backup = self._config_backup(
            "20260428-122000",
            data={
                "repo": self.repo.as_posix(),
                "profile": "personal",
                "execution_backend": "codex",
                "iterations": 5,
                "prompts_dir": "prompts/agentcli",
            },
            mtime=1_714_280_700.0,
        )
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        before = self.config_path.read_text(encoding="utf-8")
        response = client.post(
            "/api/config/restore",
            json={
                "backup_path": backup.as_posix(),
                "confirm": "RESTORE CONFIG",
            },
        )
        self.assertEqual(400, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("config_restore_confirmation_mismatch", payload["error"]["code"])
        self.assertEqual("RESTORE CONFIG BACKUP", payload["error"]["details"]["confirmation_phrase"])
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))
        self.assertEqual([backup.as_posix()], [item["path"] for item in client.get("/api/config").json()["backups"]])

    def test_config_restore_rejects_traversal_and_non_sibling_backups(self) -> None:
        _write_config(self.config_path, self.repo, iterations=2)
        backup = self._config_backup(
            "20260428-122500",
            data={
                "repo": self.repo.as_posix(),
                "profile": "personal",
                "execution_backend": "codex",
                "iterations": 6,
                "prompts_dir": "prompts/agentcli",
            },
            mtime=1_714_280_900.0,
        )
        nested_backup = self._config_backup(
            "20260428-122600",
            data={
                "repo": self.repo.as_posix(),
                "profile": "personal",
                "execution_backend": "codex",
                "iterations": 9,
                "prompts_dir": "prompts/agentcli",
            },
            mtime=1_714_280_960.0,
            nested="nested",
        )
        client, _ = _create_client(self.repo, enable_runner_controls=True, config_path=self.config_path)

        before = self.config_path.read_text(encoding="utf-8")
        traversal = client.post(
            "/api/config/restore",
            json={
                "backup_path": "../escape.json",
                "confirm": "RESTORE CONFIG BACKUP",
            },
        )
        self.assertEqual(400, traversal.status_code)
        traversal_payload = traversal.json()
        self.assertFalse(traversal_payload["ok"])
        self.assertEqual("config_backup_path_outside_config_dir", traversal_payload["error"]["code"])

        non_sibling = client.post(
            "/api/config/restore",
            json={
                "backup_path": nested_backup.as_posix(),
                "confirm": "RESTORE CONFIG BACKUP",
            },
        )
        self.assertEqual(404, non_sibling.status_code)
        non_sibling_payload = non_sibling.json()
        self.assertFalse(non_sibling_payload["ok"])
        self.assertEqual("config_backup_not_found", non_sibling_payload["error"]["code"])
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))
        self.assertEqual([backup.as_posix()], [item["path"] for item in client.get("/api/config").json()["backups"]])

    def test_config_restore_is_disabled_until_opt_in(self) -> None:
        _write_config(self.config_path, self.repo, iterations=2)
        backup = self._config_backup(
            "20260428-123000",
            data={
                "repo": self.repo.as_posix(),
                "profile": "personal",
                "execution_backend": "codex",
                "iterations": 8,
                "prompts_dir": "prompts/agentcli",
            },
            mtime=1_714_281_200.0,
        )
        client, _ = _create_client(self.repo, enable_runner_controls=False, config_path=self.config_path)

        before = self.config_path.read_text(encoding="utf-8")
        response = client.post(
            "/api/config/restore",
            json={
                "backup_path": backup.as_posix(),
                "confirm": "RESTORE CONFIG BACKUP",
            },
        )
        self.assertEqual(403, response.status_code)
        payload = response.json()
        self.assertFalse(payload["ok"])
        self.assertEqual("config_restore_disabled", payload["error"]["code"])
        self.assertEqual(before, self.config_path.read_text(encoding="utf-8"))
        self.assertEqual([backup.as_posix()], [item["path"] for item in client.get("/api/config").json()["backups"]])

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
        from agent_runner import web_goals
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

        expected_text = _goal_save_serialize_draft(draft)
        with patch("agent_runner.web_goals.atomic_write_text", wraps=web_goals.atomic_write_text) as atomic_write_mock:
            with patch("agent_runner.web_goals.shutil.copy2", wraps=web_goals.shutil.copy2) as copy2_mock:
                response = client.post("/api/goals/save", json={"draft": draft})
        atomic_write_calls = [(Path(call.args[0]), call.args[1]) for call in atomic_write_mock.call_args_list]
        backup_copy_calls = [(Path(call.args[0]), Path(call.args[1])) for call in copy2_mock.call_args_list]
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
        self.assertEqual(self.goals_path.parent, backup_path.parent)
        self.assertTrue(backup_path.name.startswith(f"{self.goals_path.stem}."))
        self.assertTrue(backup_path.name.endswith(f".bak{self.goals_path.suffix}"))
        self.assertEqual(original, backup_path.read_text(encoding="utf-8"))
        self.assertEqual(self.goals_path.read_text(encoding="utf-8"), payload["snapshot"]["goals"]["raw_text"])
        self.assertEqual(expected_text, self.goals_path.read_text(encoding="utf-8"))
        self.assertEqual([(self.goals_path, expected_text)], atomic_write_calls)
        self.assertEqual([(self.goals_path, backup_path)], backup_copy_calls)
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
        self.assertEqual(backup_path.as_posix(), prompt_body["backups"][0]["path"])
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
