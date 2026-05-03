from __future__ import annotations

import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _GoldenRunnerController:
    def __init__(self, status: dict[str, object] | None = None) -> None:
        self._status = dict(status or {})
        run_dir = self._status.get("run_dir")
        self.run_dir = Path(str(run_dir)).expanduser().resolve() if run_dir else None

    def status(self, *args: Any, **kwargs: Any) -> dict[str, object]:
        _ = args, kwargs
        return dict(self._status)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _write_config(path: Path, repo: Path) -> None:
    payload = {
        "repo": repo.as_posix(),
        "profile": "personal",
        "execution_backend": "codex",
        "iterations": 2,
        "prompts_dir": "prompts/agentcli",
        "goals_completion_level": "all",
    }
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


class WebEndpointGoldenTests(unittest.TestCase):
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
        _write_config(self.config_path, self.repo)

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
            return
        os.environ["AGENTCLI_HOME"] = self._old_home

    def _create_client(self, *, controller_status: dict[str, object] | None = None):
        from agent_runner import web as web_module
        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        controller = _GoldenRunnerController(controller_status)
        with patch.object(web_module, "_build_runner_controller", return_value=controller):
            app = create_app(
                self.repo,
                web_dir=WEB_CONSOLE,
                config_path=self.config_path.as_posix(),
            )
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def _write_goals(self) -> None:
        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

## P0
- [x] Keep endpoint contracts stable

## P1
- [ ] Finish the remaining task
""",
        )

    def _make_run_dir(self, run_id: str) -> Path:
        run_dir = self.repo / ".AgentCLI" / "agent_runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _write_backlog(self, run_dir: Path, tasks: list[dict[str, object]]) -> None:
        _write(run_dir / "BACKLOG.json", json.dumps({"tasks": tasks}, ensure_ascii=False, indent=2) + "\n")

    def _write_state(self, run_dir: Path, state: dict[str, object]) -> None:
        _write(run_dir / "STATE.json", json.dumps(state, ensure_ascii=False, indent=2) + "\n")

    def test_status_no_run_contract_sections_are_normalized(self) -> None:
        client = self._create_client(controller_status={})

        payload = client.get("/api/status").json()
        projection = {
            "ok": payload["ok"],
            "latest_run_dir": payload["latest_run_dir"],
            "active_run": {
                key: payload["active_run"][key]
                for key in (
                    "id",
                    "backend",
                    "branch",
                    "stage",
                    "stageIndex",
                    "iteration",
                    "maxIterations",
                    "runDir",
                    "attempt",
                    "worktreeMode",
                    "finalReason",
                    "progressAvailable",
                    "progress",
                    "executionStatus",
                    "completionStatus",
                    "completionReason",
                    "goalsComplete",
                    "backlogComplete",
                    "projectComplete",
                    "projectStatus",
                    "budgetAvailable",
                    "budgetUsed",
                    "tokensAvailable",
                    "tokens",
                    "quotaAvailable",
                    "quotaWindow",
                    "quotaUsed",
                    "quota",
                    "elapsedSec",
                    "status",
                    "task",
                    "taskTitle",
                )
            },
            "progress": {
                key: payload["progress"][key]
                for key in (
                    "latest_run_dir",
                    "run_status",
                    "tasks_done",
                    "tasks_total",
                    "tasks_failed",
                    "stateCounts",
                    "progress",
                    "progress_available",
                    "current_task_id",
                    "current_task_title",
                    "attempt",
                    "worktree_mode",
                    "executionStatus",
                    "completionStatus",
                    "completionReason",
                    "projectComplete",
                    "projectStatus",
                    "goalsComplete",
                    "backlogComplete",
                    "final_reason",
                    "final_rc",
                    "state",
                )
            },
            "worktree": {
                key: payload["worktree"][key]
                for key in (
                    "status",
                    "mode",
                    "reviewRequired",
                    "reviewRequiredMessage",
                    "baseRef",
                    "headRef",
                    "worktreeDir",
                    "patchPath",
                    "pendingFile",
                    "statusFile",
                    "cleanupPath",
                    "cleanupMessage",
                    "cleanupDetails",
                    "cleanupAttempts",
                    "cleanupReconciliation",
                    "cleanup_reconciliation",
                    "cleanupState",
                    "resolutionActions",
                    "resolution_actions",
                    "summary",
                    "risk",
                    "changedFiles",
                    "changed_files",
                    "preflight",
                    "applyCheck",
                    "sourceRepoState",
                    "source_repo_state",
                    "sourceHead",
                    "source_head",
                    "expectedBaseRef",
                    "expected_base_ref",
                    "patchHash",
                    "patch_hash",
                    "pendingMarkerPath",
                    "pending_marker_path",
                    "checklist",
                    "runDir",
                    "runnerRc",
                    "lastRc",
                )
            },
            "pr_queue": {
                key: payload["pr_queue"][key]
                for key in ("ok", "state", "items", "detail", "selectedId", "selected_id", "summary", "message")
            },
            "section_state": {
                key: payload["sectionState"][key]["status"]
                for key in ("activeRun", "backlog", "goals", "prQueue", "worktree", "runnerControl")
            },
        }

        self.assertEqual(
            {
                "ok": True,
                "latest_run_dir": None,
                "active_run": {
                    "id": "no-run",
                    "backend": "codex",
                    "branch": "HEAD",
                    "stage": "idle",
                    "stageIndex": 0,
                    "iteration": 0,
                    "maxIterations": 2,
                    "runDir": "",
                    "attempt": None,
                    "worktreeMode": "manual",
                    "finalReason": "",
                    "progressAvailable": False,
                    "progress": None,
                    "executionStatus": "idle",
                    "completionStatus": "",
                    "completionReason": "",
                    "goalsComplete": False,
                    "backlogComplete": True,
                    "projectComplete": False,
                    "projectStatus": "incomplete",
                    "budgetAvailable": False,
                    "budgetUsed": None,
                    "tokensAvailable": False,
                    "tokens": {"in": None, "out": None, "available": False},
                    "quotaAvailable": False,
                    "quotaWindow": "",
                    "quotaUsed": None,
                    "quota": {"window": "", "used": None, "available": False},
                    "elapsedSec": 0,
                    "status": "idle",
                    "task": "",
                    "taskTitle": "",
                },
                "progress": {
                    "latest_run_dir": None,
                    "run_status": "idle",
                    "tasks_done": 0,
                    "tasks_total": 0,
                    "tasks_failed": 0,
                    "stateCounts": {"done": 0, "failed": 0, "warnings": 0},
                    "progress": None,
                    "progress_available": False,
                    "current_task_id": "",
                    "current_task_title": "",
                    "attempt": None,
                    "worktree_mode": "",
                    "executionStatus": "idle",
                    "completionStatus": "",
                    "completionReason": "",
                    "projectComplete": False,
                    "projectStatus": "incomplete",
                    "goalsComplete": False,
                    "backlogComplete": True,
                    "final_reason": "",
                    "final_rc": None,
                    "state": {"done": [], "failed": [], "warnings": []},
                },
                "worktree": {
                    "status": "none",
                    "mode": "manual",
                    "reviewRequired": False,
                    "reviewRequiredMessage": "No pending worktree merge.",
                    "baseRef": "",
                    "headRef": "",
                    "worktreeDir": "",
                    "patchPath": "",
                    "pendingFile": "",
                    "statusFile": "",
                    "cleanupPath": "",
                    "cleanupMessage": "No cleanup state is available.",
                    "cleanupDetails": {},
                    "cleanupAttempts": [],
                    "cleanupReconciliation": {},
                    "cleanup_reconciliation": {},
                    "cleanupState": "none",
                    "resolutionActions": [],
                    "resolution_actions": [],
                    "summary": "No pending worktree merge.",
                    "risk": "No isolated worktree patch is pending review.",
                    "changedFiles": [],
                    "changed_files": [],
                    "preflight": {},
                    "applyCheck": {},
                    "sourceRepoState": "",
                    "source_repo_state": "",
                    "sourceHead": "",
                    "source_head": "",
                    "expectedBaseRef": "",
                    "expected_base_ref": "",
                    "patchHash": "",
                    "patch_hash": "",
                    "pendingMarkerPath": "",
                    "pending_marker_path": "",
                    "checklist": [
                        "Inspect patch hunks",
                        "Verify no secret leakage",
                        "Approve merge only after review",
                        "Discard only after archival copy",
                    ],
                    "runDir": "",
                    "runnerRc": 0,
                    "lastRc": 0,
                },
                "pr_queue": {
                    "ok": True,
                    "state": "empty",
                    "items": [],
                    "detail": None,
                    "selectedId": "",
                    "selected_id": "",
                    "summary": {
                        "total": 0,
                        "blocked": 0,
                        "validationPassed": 0,
                        "validationPending": 0,
                        "validationFailed": 0,
                        "blockedEnv": 0,
                    },
                    "message": "No PR queue packets are available.",
                },
                "section_state": {
                    "activeRun": "empty",
                    "backlog": "empty",
                    "goals": "empty",
                    "prQueue": "empty",
                    "worktree": "empty",
                    "runnerControl": "disabled",
                },
            },
            projection,
        )

    def test_progress_contract_exposes_done_failed_and_pending_counters(self) -> None:
        self._write_goals()
        run_dir = self._make_run_dir("20260503-010203")
        self._write_backlog(
            run_dir,
            [
                {
                    "id": "T1",
                    "title": "Done task",
                    "prompt": "done",
                    "files": ["done.py"],
                    "done_when": "done",
                    "skills": ["observability"],
                    "skills_rationale": "Keep endpoint counters stable.",
                    "depends_on": [],
                },
                {
                    "id": "T2",
                    "title": "Failed task",
                    "prompt": "failed",
                    "files": ["failed.py"],
                    "done_when": "failed",
                    "skills": ["observability"],
                    "skills_rationale": "Keep endpoint counters stable.",
                    "depends_on": ["T1"],
                },
                {
                    "id": "T3",
                    "title": "Pending task",
                    "prompt": "pending",
                    "files": ["pending.py"],
                    "done_when": "pending",
                    "skills": ["observability"],
                    "skills_rationale": "Keep endpoint counters stable.",
                    "depends_on": ["T2"],
                },
            ],
        )
        self._write_state(
            run_dir,
            {
                "done": ["T1"],
                "failed": [
                    {
                        "task": "T2",
                        "reason": "build_failed",
                        "detail": "Failed build.",
                        "attempt": 2,
                        "cycle": 1,
                        "step": 0,
                        "rc": 1,
                    }
                ],
                "warnings": [],
            },
        )

        client = self._create_client(
            controller_status={
                "run_dir": run_dir.as_posix(),
                "running": False,
                "exit_code": 1,
                "reason": "build_failed",
                "stage": "Dev",
            }
        )

        payload = client.get("/api/progress").json()
        projection = {
            "run_id": Path(payload["latest_run_dir"]).name if payload["latest_run_dir"] else None,
            "summary": {
                key: payload[key]
                for key in (
                    "tasks_done",
                    "tasks_total",
                    "tasks_failed",
                    "run_status",
                    "final_reason",
                    "completion_status",
                    "completionStatus",
                    "completion_reason",
                    "completionReason",
                    "execution_status",
                    "executionStatus",
                    "project_complete",
                    "project_status",
                    "goals_complete",
                    "backlog_complete",
                )
            },
            "state": payload["state"],
            "backlog_counts": payload["backlog"]["counts"],
            "backlog_status_counts": payload["backlog"]["statusCounts"],
            "items": [
                {key: item[key] for key in ("id", "status", "failure_reason", "depends_on")}
                for item in payload["backlog"]["items"]
            ],
        }

        self.assertEqual(
            {
                "run_id": "20260503-010203",
                "summary": {
                    "tasks_done": 1,
                    "tasks_total": 3,
                    "tasks_failed": 1,
                    "run_status": "failed",
                    "final_reason": "build_failed",
                    "completion_status": "",
                    "completionStatus": "",
                    "completion_reason": "",
                    "completionReason": "",
                    "execution_status": "failed",
                    "executionStatus": "failed",
                    "project_complete": False,
                    "project_status": "incomplete",
                    "goals_complete": False,
                    "backlog_complete": False,
                },
                "state": {
                    "done": ["T1"],
                    "failed": [
                        {
                            "task": "T2",
                            "reason": "build_failed",
                            "detail": "Failed build.",
                            "attempt": 2,
                            "cycle": 1,
                            "step": 0,
                            "rc": 1,
                        }
                    ],
                    "warnings": [],
                },
                "backlog_counts": {
                    "pending": 1,
                    "in_progress": 0,
                    "done": 1,
                    "failed": 1,
                    "regressed": 1,
                    "review": 0,
                    "blocked_env": 0,
                    "tasks_regressed": 1,
                    "tasks_review": 0,
                    "tasks_blocked_env": 0,
                },
                "backlog_status_counts": {"done": 1, "failed": 1, "pending": 1},
                "items": [
                    {"id": "T1", "status": "done", "failure_reason": "", "depends_on": []},
                    {"id": "T2", "status": "failed", "failure_reason": "build_failed", "depends_on": ["T1"]},
                    {"id": "T3", "status": "pending", "failure_reason": "", "depends_on": ["T2"]},
                ],
            },
            projection,
        )

    def test_progress_contract_preserves_goals_incomplete_completion_state(self) -> None:
        self._write_goals()
        run_dir = self._make_run_dir("20260503-020304")
        self._write_backlog(
            run_dir,
            [
                {
                    "id": "T1",
                    "title": "Done task",
                    "prompt": "done",
                    "files": ["done.py"],
                    "done_when": "done",
                    "skills": ["observability"],
                    "skills_rationale": "Keep completion state stable.",
                    "depends_on": [],
                },
                {
                    "id": "T2",
                    "title": "Done task 2",
                    "prompt": "done",
                    "files": ["done_2.py"],
                    "done_when": "done",
                    "skills": ["observability"],
                    "skills_rationale": "Keep completion state stable.",
                    "depends_on": ["T1"],
                },
            ],
        )
        self._write_state(run_dir, {"done": ["T1", "T2"], "failed": [], "warnings": []})
        _write(
            run_dir / "COMPLETION_STATUS.json",
            json.dumps(
                {
                    "completion_status": "goals_incomplete",
                    "completion_reason": "goals_incomplete",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )

        client = self._create_client(
            controller_status={
                "run_dir": run_dir.as_posix(),
                "running": False,
                "exit_code": 0,
                "reason": "goals_incomplete",
                "stage": "QA",
            }
        )

        payload = client.get("/api/progress").json()
        projection = {
            "run_id": Path(payload["latest_run_dir"]).name if payload["latest_run_dir"] else None,
            "summary": {
                key: payload[key]
                for key in (
                    "tasks_done",
                    "tasks_total",
                    "tasks_failed",
                    "run_status",
                    "final_reason",
                    "completion_status",
                    "completionStatus",
                    "completion_reason",
                    "completionReason",
                    "execution_status",
                    "executionStatus",
                    "project_complete",
                    "project_status",
                    "goals_complete",
                    "backlog_complete",
                )
            },
            "backlog_counts": payload["backlog"]["counts"],
        }

        self.assertEqual(
            {
                "run_id": "20260503-020304",
                "summary": {
                    "tasks_done": 2,
                    "tasks_total": 2,
                    "tasks_failed": 0,
                    "run_status": "completed",
                    "final_reason": "goals_incomplete",
                    "completion_status": "goals_incomplete",
                    "completionStatus": "goals_incomplete",
                    "completion_reason": "goals_incomplete",
                    "completionReason": "goals_incomplete",
                    "execution_status": "completed",
                    "executionStatus": "completed",
                    "project_complete": False,
                    "project_status": "incomplete",
                    "goals_complete": False,
                    "backlog_complete": True,
                },
                "backlog_counts": {
                    "pending": 0,
                    "in_progress": 0,
                    "done": 2,
                    "failed": 0,
                    "regressed": 0,
                    "review": 0,
                    "blocked_env": 0,
                    "tasks_regressed": 0,
                    "tasks_review": 0,
                    "tasks_blocked_env": 0,
                },
            },
            projection,
        )

    def test_worktree_no_pending_contract_uses_diagnostics_safe_fields(self) -> None:
        client = self._create_client(controller_status={})

        payload = client.get("/api/worktree").json()
        projection = {
            key: payload[key]
            for key in (
                "status",
                "mode",
                "reviewRequired",
                "reviewRequiredMessage",
                "baseRef",
                "headRef",
                "worktreeDir",
                "patchPath",
                "pendingFile",
                "statusFile",
                "cleanupPath",
                "cleanupMessage",
                "cleanupDetails",
                "cleanupAttempts",
                "cleanupReconciliation",
                "cleanup_reconciliation",
                "cleanupState",
                "resolutionActions",
                "resolution_actions",
                "summary",
                "risk",
                "changedFiles",
                "changed_files",
                "preflight",
                "applyCheck",
                "sourceRepoState",
                "source_repo_state",
                "sourceHead",
                "source_head",
                "expectedBaseRef",
                "expected_base_ref",
                "patchHash",
                "patch_hash",
                "pendingMarkerPath",
                "pending_marker_path",
                "checklist",
                "runDir",
                "runnerRc",
                "lastRc",
            )
        }

        self.assertEqual(
            {
                "status": "none",
                "mode": "manual",
                "reviewRequired": False,
                "reviewRequiredMessage": "No pending worktree merge.",
                "baseRef": "",
                "headRef": "",
                "worktreeDir": "",
                "patchPath": "",
                "pendingFile": "",
                "statusFile": "",
                "cleanupPath": "",
                "cleanupMessage": "No cleanup state is available.",
                "cleanupDetails": {},
                "cleanupAttempts": [],
                "cleanupReconciliation": {},
                "cleanup_reconciliation": {},
                "cleanupState": "none",
                "resolutionActions": [],
                "resolution_actions": [],
                "summary": "No pending worktree merge.",
                "risk": "No isolated worktree patch is pending review.",
                "changedFiles": [],
                "changed_files": [],
                "preflight": {},
                "applyCheck": {},
                "sourceRepoState": "",
                "source_repo_state": "",
                "sourceHead": "",
                "source_head": "",
                "expectedBaseRef": "",
                "expected_base_ref": "",
                "patchHash": "",
                "patch_hash": "",
                "pendingMarkerPath": "",
                "pending_marker_path": "",
                "checklist": [
                    "Inspect patch hunks",
                    "Verify no secret leakage",
                    "Approve merge only after review",
                    "Discard only after archival copy",
                ],
                "runDir": "",
                "runnerRc": 0,
                "lastRc": 0,
            },
            projection,
        )

    def test_runner_status_disabled_contract_has_no_contradictory_liveness_flags(self) -> None:
        client = self._create_client(controller_status={})

        payload = client.get("/api/runner/status").json()
        projection = {
            "top": {
                key: payload[key]
                for key in (
                    "ok",
                    "latest_run_dir",
                    "source",
                    "enabled",
                    "controller_available",
                    "busy",
                    "run_status",
                    "execution_status",
                    "executionStatus",
                    "project_complete",
                    "project_status",
                    "projectStatus",
                    "goals_complete",
                    "goalsComplete",
                    "backlog_complete",
                    "backlogComplete",
                    "message",
                )
            },
            "status": {
                key: payload["status"][key]
                for key in (
                    "running",
                    "runner_mode",
                    "run_dir",
                    "uptime_seconds",
                    "exit_code",
                    "stop_file",
                    "stop_file_exists",
                    "done",
                    "failed",
                    "warnings",
                    "state_counts",
                    "reason",
                    "last_event",
                    "event_count",
                    "stop_progress",
                )
            },
            "actions": {name: payload["actions"][name]["enabled"] for name in ("start", "stop", "reload", "restart")},
            "nested_control": {
                "enabled": payload["runner_control"]["enabled"],
                "controller_available": payload["runner_control"]["controller_available"],
                "busy": payload["runner_control"]["busy"],
                "running": payload["runner_control"]["status"]["running"],
            },
            "live": {
                "runStatus": payload["liveRun"]["status"]["runStatus"],
                "executionStatus": payload["liveRun"]["status"]["executionStatus"],
                "runnerRunning": payload["liveRun"]["runnerControl"]["status"]["running"],
                "processRunning": payload["liveRun"]["process"]["running"],
                "runnerProcessStatus": payload["liveRun"]["process"]["liveState"]["runner_process"]["status"],
                "taskBackendStatus": payload["liveRun"]["process"]["liveState"]["task_backend"]["status"],
            },
        }

        self.assertEqual(
            {
                "top": {
                    "ok": True,
                    "latest_run_dir": None,
                    "source": "default",
                    "enabled": False,
                    "controller_available": True,
                    "busy": False,
                    "run_status": "idle",
                    "execution_status": "idle",
                    "executionStatus": "idle",
                    "project_complete": False,
                    "project_status": "incomplete",
                    "projectStatus": "incomplete",
                    "goals_complete": False,
                    "goalsComplete": False,
                    "backlog_complete": True,
                    "backlogComplete": True,
                    "message": "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
                },
                "status": {
                    "running": False,
                    "runner_mode": "thread",
                    "run_dir": "",
                    "uptime_seconds": 0,
                    "exit_code": None,
                    "stop_file": "STOP",
                    "stop_file_exists": False,
                    "done": 0,
                    "failed": 0,
                    "warnings": 0,
                    "state_counts": {"done": 0, "failed": 0, "warnings": 0},
                    "reason": "",
                    "last_event": "",
                    "event_count": 0,
                    "stop_progress": {},
                },
                "actions": {"start": False, "stop": False, "reload": False, "restart": False},
                "nested_control": {
                    "enabled": False,
                    "controller_available": True,
                    "busy": False,
                    "running": False,
                },
                "live": {
                    "runStatus": "idle",
                    "executionStatus": "idle",
                    "runnerRunning": False,
                    "processRunning": False,
                    "runnerProcessStatus": "unavailable",
                    "taskBackendStatus": "unavailable",
                },
            },
            projection,
        )


if __name__ == "__main__":
    unittest.main()
