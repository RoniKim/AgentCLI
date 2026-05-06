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


def _write_basic_config(path: Path, repo: Path) -> None:
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
        _write_basic_config(self.config_path, self.repo)

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

    def test_status_dashboard_scope_excludes_heavy_snapshot_sections(self) -> None:
        goal_marker = "GOALS-RAW-TEXT-MARKER"
        history_marker = "HISTORY-RAW-MARKER"
        old_log_marker = "OLD-LOG-LINE-MARKER"

        _write(
            self.repo / ".doc" / "GOALS.md",
            f"""# Project Goals

{goal_marker}

## P0
- [ ] Compact dashboard polling

## P1
- [x] Keep full snapshot routes
""",
        )
        _write(self.repo / "prompts" / "agentcli" / "pm_prompt.md", "PM prompt inventory marker\n")
        _write(self.repo / "prompts" / "agentcli" / "dev_prompt.md", "Dev prompt inventory marker\n")

        run_dir = self._make_run_dir("20260504-120000")
        self._write_backlog(
            run_dir,
            [
                {
                    "id": "T16",
                    "title": "Compact dashboard polling",
                    "status": "in_progress",
                    "priority": "P0",
                    "estimate": "M",
                }
            ],
        )
        self._write_state(run_dir, {"done": [], "failed": [], "warnings": []})
        _write(
            run_dir / "run_summary.json",
            json.dumps(
                {
                    "branch": "main",
                    "cycles": [{"summary": history_marker}],
                    "final": {"rc": 0, "reason": ""},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            run_dir / "last_run_summary.json",
            json.dumps(
                {
                    "status": "running",
                    "total_tasks": 1,
                    "skipped": 0,
                    "duration_seconds": 45,
                    "reason": history_marker,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        log_lines = [f"2026-05-04 12:00:{index:02d} [INFO] line {index}" for index in range(1, 16)]
        log_lines.insert(0, f"2026-05-04 12:00:00 [INFO] {old_log_marker}")
        _write(run_dir / "logs" / "run.log", "\n".join(log_lines) + "\n")

        client = self._create_client(
            controller_status={
                "run_dir": run_dir.as_posix(),
                "running": True,
                "stage": "Dev",
                "current_task_id": "T16",
                "current_task_title": "Compact dashboard polling",
                "runner_mode": "thread",
            }
        )

        full_payload = client.get("/api/status").json()
        dashboard_payload = client.get("/api/status", params={"scope": "dashboard"}).json()

        self.assertIn(goal_marker, json.dumps(full_payload["goals"], ensure_ascii=False))
        self.assertNotIn("raw_text", dashboard_payload["goals"])
        self.assertNotIn("rawText", dashboard_payload["goals"])
        self.assertNotIn(goal_marker, json.dumps(dashboard_payload["goals"], ensure_ascii=False))

        self.assertTrue(full_payload["history"]["items"])
        self.assertEqual([], dashboard_payload["history"]["items"])
        self.assertNotIn(history_marker, json.dumps(dashboard_payload["history"], ensure_ascii=False))

        self.assertTrue(full_payload["prompts"]["items"])
        self.assertEqual([], dashboard_payload["prompts"]["items"])
        self.assertTrue(full_payload["config_contract"]["schema"])
        self.assertEqual({}, dashboard_payload["config_contract"]["schema"])
        self.assertEqual({}, dashboard_payload["config_contract"]["values"])
        self.assertEqual({}, dashboard_payload["config_contract"]["defaults"])

        self.assertGreater(len(full_payload["logs"]["entries"]), 12)
        self.assertLessEqual(len(dashboard_payload["logs"]["entries"]), 12)
        self.assertNotIn(old_log_marker, json.dumps(dashboard_payload["logs"], ensure_ascii=False))
        self.assertNotIn("tail", dashboard_payload["logs"])
        self.assertNotIn("files", dashboard_payload["logs"])

        for key in ("active_run", "progress", "runner_control", "liveRun", "sectionState", "snapshotRefresh"):
            self.assertIn(key, dashboard_payload)
        self.assertEqual(full_payload["active_run"]["id"], dashboard_payload["active_run"]["id"])
        self.assertEqual(full_payload["progress"]["run_status"], dashboard_payload["progress"]["run_status"])
        self.assertEqual(full_payload["runner_control"]["enabled"], dashboard_payload["runner_control"]["enabled"])
        self.assertEqual(full_payload["snapshotRefresh"]["status"], dashboard_payload["snapshotRefresh"]["status"])

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

    def test_progress_contract_splits_failure_groups(self) -> None:
        self._write_goals()
        run_dir = self._make_run_dir("20260503-010204")
        self._write_backlog(
            run_dir,
            [
                {"id": "T1", "title": "Install dependency", "prompt": "", "files": [], "done_when": "", "skills": [], "skills_rationale": "", "depends_on": []},
                {"id": "T2", "title": "Review selector contract", "prompt": "", "files": [], "done_when": "", "skills": [], "skills_rationale": "", "depends_on": []},
                {"id": "T3", "title": "Fix regression", "prompt": "", "files": [], "done_when": "", "skills": [], "skills_rationale": "", "depends_on": []},
            ],
        )
        self._write_state(
            run_dir,
            {
                "done": [],
                "failed": [
                    {"task": "T1", "reason": "build_failed", "task_status": "blocked_env"},
                    {"task": "T2", "reason": "fast_regression_failed", "task_status": "test_contract_changed"},
                    {"task": "T3", "reason": "build_failed", "task_status": "regression_failed"},
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

        self.assertEqual(3, payload["backlog"]["counts"]["failed"])
        self.assertEqual(1, payload["backlog"]["counts"]["blocked_env"])
        self.assertEqual(1, payload["backlog"]["counts"]["review"])
        self.assertEqual(1, payload["backlog"]["counts"]["regressed"])
        self.assertEqual(1, payload["backlog"]["failureGroupCounts"]["blocked_env"])
        self.assertEqual(1, payload["backlog"]["failureGroupCounts"]["review"])
        self.assertEqual(1, payload["backlog"]["failureGroupCounts"]["regression"])

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

    def test_existing_status_progress_worktree_and_runner_contracts_stay_stable_for_failed_run(self) -> None:
        self._write_goals()
        run_dir = self._make_run_dir("20260503-030405")
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
                    "skills_rationale": "Keep extracted payload builders stable.",
                    "depends_on": [],
                },
                {
                    "id": "T2",
                    "title": "Failed task",
                    "prompt": "failed",
                    "files": ["failed.py"],
                    "done_when": "failed",
                    "skills": ["observability"],
                    "skills_rationale": "Keep extracted payload builders stable.",
                    "depends_on": ["T1"],
                },
                {
                    "id": "T3",
                    "title": "Pending task",
                    "prompt": "pending",
                    "files": ["pending.py"],
                    "done_when": "pending",
                    "skills": ["observability"],
                    "skills_rationale": "Keep extracted payload builders stable.",
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
                "done": 1,
                "failed": 1,
                "warnings": 0,
                "runner_mode": "thread",
            }
        )

        status_payload = client.get("/api/status").json()
        progress_payload = client.get("/api/progress").json()
        worktree_payload = client.get("/api/worktree").json()
        runner_payload = client.get("/api/runner/status").json()
        projection = {
            "status": {
                "active_run": {
                    key: status_payload["active_run"][key]
                    for key in (
                        "id",
                        "stage",
                        "stageIndex",
                        "runDir",
                        "progressAvailable",
                        "progress",
                        "executionStatus",
                        "completionStatus",
                        "completionReason",
                        "goalsComplete",
                        "backlogComplete",
                        "projectComplete",
                        "projectStatus",
                        "status",
                        "task",
                        "taskTitle",
                        "finalReason",
                    )
                },
                "progress": {
                    key: status_payload["progress"][key]
                    for key in (
                        "run_status",
                        "tasks_done",
                        "tasks_total",
                        "tasks_failed",
                        "progress",
                        "progress_available",
                        "current_task_id",
                        "current_task_title",
                        "executionStatus",
                        "completionStatus",
                        "completionReason",
                        "projectComplete",
                        "projectStatus",
                        "goalsComplete",
                        "backlogComplete",
                        "final_reason",
                        "final_rc",
                    )
                },
                "worktree": {
                    key: status_payload["worktree"][key]
                    for key in ("status", "summary", "risk", "runDir", "runnerRc", "lastRc")
                },
                "runner_control": {
                    key: status_payload["runner_control"][key]
                    for key in (
                        "enabled",
                        "controller_available",
                        "busy",
                        "run_status",
                        "execution_status",
                        "project_complete",
                        "project_status",
                        "goals_complete",
                        "backlog_complete",
                        "message",
                    )
                },
            },
            "progress": {
                "top": {
                    key: progress_payload[key]
                    for key in (
                        "run_status",
                        "tasks_done",
                        "tasks_total",
                        "tasks_failed",
                        "current_task_id",
                        "current_task_title",
                        "executionStatus",
                        "completionStatus",
                        "completionReason",
                        "projectComplete",
                        "projectStatus",
                        "goalsComplete",
                        "backlogComplete",
                        "final_reason",
                    )
                },
                "nested": {
                    key: progress_payload["progress"][key]
                    for key in (
                        "run_status",
                        "tasks_done",
                        "tasks_total",
                        "tasks_failed",
                        "progress",
                        "progress_available",
                        "current_task_id",
                        "current_task_title",
                        "executionStatus",
                        "completionStatus",
                        "completionReason",
                        "projectComplete",
                        "projectStatus",
                        "goalsComplete",
                        "backlogComplete",
                        "final_reason",
                        "final_rc",
                    )
                },
            },
            "worktree": {
                key: worktree_payload[key]
                for key in ("status", "summary", "risk", "runDir", "runnerRc", "lastRc")
            },
            "runner": {
                "top": {
                    key: runner_payload[key]
                    for key in (
                        "enabled",
                        "controller_available",
                        "busy",
                        "run_status",
                        "executionStatus",
                        "projectComplete",
                        "projectStatus",
                        "goalsComplete",
                        "backlogComplete",
                        "message",
                    )
                },
                "status": {
                    key: runner_payload["status"][key]
                    for key in (
                        "running",
                        "runner_mode",
                        "run_dir",
                        "exit_code",
                        "done",
                        "failed",
                        "warnings",
                        "state_counts",
                        "reason",
                        "last_event",
                        "event_count",
                    )
                },
            },
        }

        expected_progress = {
            "run_status": "failed",
            "tasks_done": 1,
            "tasks_total": 3,
            "tasks_failed": 1,
            "progress": None,
            "progress_available": False,
            "current_task_id": "T2",
            "current_task_title": "Failed task",
            "executionStatus": "failed",
            "completionStatus": "",
            "completionReason": "",
            "projectComplete": False,
            "projectStatus": "incomplete",
            "goalsComplete": False,
            "backlogComplete": False,
            "final_reason": "build_failed",
            "final_rc": 1,
        }
        expected_worktree = {
            "status": "none",
            "summary": "No pending worktree merge.",
            "risk": "No isolated worktree patch is pending review.",
            "runDir": run_dir.as_posix(),
            "runnerRc": 0,
            "lastRc": 0,
        }

        self.assertEqual(
            {
                "status": {
                    "active_run": {
                        "id": "20260503-030405",
                        "stage": "Dev",
                        "stageIndex": 3,
                        "runDir": run_dir.as_posix(),
                        "progressAvailable": False,
                        "progress": None,
                        "executionStatus": "failed",
                        "completionStatus": "",
                        "completionReason": "",
                        "goalsComplete": False,
                        "backlogComplete": False,
                        "projectComplete": False,
                        "projectStatus": "incomplete",
                        "status": "failed",
                        "task": "T2",
                        "taskTitle": "Failed task",
                        "finalReason": "build_failed",
                    },
                    "progress": expected_progress,
                    "worktree": expected_worktree,
                    "runner_control": {
                        "enabled": False,
                        "controller_available": True,
                        "busy": False,
                        "run_status": "failed",
                        "execution_status": "failed",
                        "project_complete": False,
                        "project_status": "incomplete",
                        "goals_complete": False,
                        "backlog_complete": False,
                        "message": "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
                },
            },
            "progress": {
                "top": {
                    "run_status": "failed",
                    "tasks_done": 1,
                    "tasks_total": 3,
                    "tasks_failed": 1,
                    "current_task_id": "T2",
                    "current_task_title": "Failed task",
                    "executionStatus": "failed",
                    "completionStatus": "",
                    "completionReason": "",
                    "projectComplete": False,
                    "projectStatus": "incomplete",
                    "goalsComplete": False,
                    "backlogComplete": False,
                    "final_reason": "build_failed",
                },
                "nested": expected_progress,
            },
            "worktree": expected_worktree,
            "runner": {
                "top": {
                    "enabled": False,
                        "controller_available": True,
                        "busy": False,
                        "run_status": "failed",
                        "executionStatus": "failed",
                        "projectComplete": False,
                        "projectStatus": "incomplete",
                        "goalsComplete": False,
                        "backlogComplete": False,
                        "message": "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls.",
                    },
                    "status": {
                        "running": False,
                        "runner_mode": "thread",
                        "run_dir": run_dir.as_posix(),
                        "exit_code": 1,
                        "done": 1,
                        "failed": 1,
                        "warnings": 0,
                        "state_counts": {"done": 1, "failed": 1, "warnings": 0},
                        "reason": "build_failed",
                        "last_event": "",
                        "event_count": 0,
                    },
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


import json
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from agent_runner.web import create_app


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _write_json(path: Path, payload: dict[str, object]) -> None:
    _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_config(path: Path, repo: Path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "repo": repo.as_posix(),
        "profile": "enterprise",
        "execution_backend": "codex",
        "roles": ["PM", "Dev", "QA"],
        "iterations": 3,
        "prompts_dir": "prompts/agentcli",
        "goals_completion_level": "all",
        "telegram": {
            "enabled": True,
            "bot_token": "secret-token",
            "pairing_code": "PAIR-1234",
        },
    }
    for key, value in overrides.items():
        if key == "telegram" and isinstance(value, dict):
            payload.setdefault("telegram", {})
            assert isinstance(payload["telegram"], dict)
            payload["telegram"].update(value)
        else:
            payload[key] = value
    _write_json(path, payload)


def _relative_to(path: str | Path, root: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def _normalize_prompt_source(source: str, home: Path) -> str:
    source_path = Path(source)
    if source_path.is_absolute():
        return _relative_to(source_path, home)
    return source.replace("\\", "/")


@dataclass
class EndpointFixture:
    root: Path
    repo: Path
    home: Path
    config_path: Path
    prompts_dir: Path
    goals_path: Path
    run_dir: Path

    def create_client(self) -> TestClient:
        app = create_app(self.repo, web_dir=WEB_CONSOLE, config_path=str(self.config_path))
        return TestClient(app)


@pytest.fixture()
def endpoint_fixture(monkeypatch: pytest.MonkeyPatch) -> EndpointFixture:
    tmp_root = ROOT / ".test-scratch"
    tmp_root.mkdir(parents=True, exist_ok=True)
    root = tmp_root / f"web_endpoint_golden_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    repo = root / "repo"
    home = root / "home"
    repo.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("AGENTCLI_HOME", str(home))

    config_path = home / "configs" / "agentcli.json"
    prompts_dir = home / "prompts" / "agentcli"
    goals_path = repo / ".doc" / "GOALS.md"
    run_dir = repo / ".AgentCLI" / "agent_runs" / "20260426-120000"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)

    _write_config(config_path, repo)
    fixture = EndpointFixture(
        root=root,
        repo=repo,
        home=home,
        config_path=config_path,
        prompts_dir=prompts_dir,
        goals_path=goals_path,
        run_dir=run_dir,
    )
    try:
        yield fixture
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_api_config_contract_exposes_metadata_redaction_backups_and_mutation_routes(endpoint_fixture: EndpointFixture) -> None:
    backup_path = endpoint_fixture.config_path.with_name("agentcli.20260426-120000.bak.json")
    _write(
        backup_path,
        '{\n  "repo": "/tmp/backup",\n  "iterations": 2\n}\n',
    )
    os.utime(backup_path, (1_714_145_200, 1_714_145_200))

    with endpoint_fixture.create_client() as client:
        payload = client.get("/api/config").json()

    backup = payload["backups"][0]
    normalized = {
        "path": _relative_to(payload["path"], endpoint_fixture.home),
        "source": payload["source"],
        "resolved_prompts_dir": _relative_to(payload["resolved_prompts_dir"], endpoint_fixture.home),
        "repo_value": _relative_to(payload["values"]["repo"], endpoint_fixture.root),
        "profile": payload["values"]["profile"],
        "telegram": {
            "bot_token": payload["values"]["telegram"]["bot_token"],
            "pairing_code": payload["values"]["telegram"]["pairing_code"],
        },
        "repo_schema": {
            "kind": payload["schema"]["repo"]["kind"],
            "label": payload["schema"]["repo"]["label"],
            "editable": payload["schema"]["repo"]["editable"],
            "restart": payload["schema"]["repo"]["restart"],
        },
        "bot_token_schema": {
            "kind": payload["schema"]["telegram.bot_token"]["kind"],
            "editable": payload["schema"]["telegram.bot_token"]["editable"],
            "redacted": payload["schema"]["telegram.bot_token"]["redacted"],
        },
        "redaction": {
            "placeholder": payload["redaction"]["placeholder"],
            "has_bot_token": "telegram.bot_token" in payload["redaction"]["paths"],
            "has_pairing_code": "telegram.pairing_code" in payload["redaction"]["paths"],
        },
        "backup": {
            "path": _relative_to(backup["path"], endpoint_fixture.home),
            "name": backup["name"],
            "size": backup["size"],
            "has_updated": bool(backup["updated"]),
            "summary_has_size": backup["summary"].endswith(f"{backup['size']} bytes"),
        },
        "meta": {
            "path": _relative_to(payload["meta"]["path"], endpoint_fixture.home),
            "resolved_prompts_dir": _relative_to(payload["meta"]["resolved_prompts_dir"], endpoint_fixture.home),
            "save_enabled": payload["meta"]["save_enabled"],
            "save_endpoint": payload["meta"]["save_endpoint"],
            "save_requires_opt_in": payload["meta"]["save_requires_opt_in"],
            "restore_enabled": payload["meta"]["restore_enabled"],
            "restore_endpoint": payload["meta"]["restore_endpoint"],
            "restore_requires_opt_in": payload["meta"]["restore_requires_opt_in"],
        },
    }

    assert normalized == {
        "path": "configs/agentcli.json",
        "source": "explicit",
        "resolved_prompts_dir": "prompts/agentcli",
        "repo_value": "repo",
        "profile": "enterprise",
        "telegram": {
            "bot_token": "[redacted]",
            "pairing_code": "[redacted]",
        },
        "repo_schema": {
            "kind": "text",
            "label": "Repository",
            "editable": True,
            "restart": True,
        },
        "bot_token_schema": {
            "kind": "text",
            "editable": True,
            "redacted": True,
        },
        "redaction": {
            "placeholder": "[redacted]",
            "has_bot_token": True,
            "has_pairing_code": True,
        },
        "backup": {
            "path": "configs/agentcli.20260426-120000.bak.json",
            "name": "agentcli.20260426-120000.bak.json",
            "size": 51,
            "has_updated": True,
            "summary_has_size": True,
        },
        "meta": {
            "path": "configs/agentcli.json",
            "resolved_prompts_dir": "prompts/agentcli",
            "save_enabled": False,
            "save_endpoint": "/api/config/save",
            "save_requires_opt_in": True,
            "restore_enabled": False,
            "restore_endpoint": "/api/config/restore",
            "restore_requires_opt_in": True,
        },
    }


def test_api_goals_contract_returns_raw_text_parsed_items_and_checkbox_state(endpoint_fixture: EndpointFixture) -> None:
    goals_text = """# Project Goals

## P0
- [x] Lock config payload contract
- [ ] Preserve prompt preview redaction

## P1
- [ ] Distinguish missing and malformed logs
"""
    _write(endpoint_fixture.goals_path, goals_text)

    with endpoint_fixture.create_client() as client:
        payload = client.get("/api/goals").json()

    normalized = {
        "path": _relative_to(payload["path"], endpoint_fixture.repo),
        "exists": payload["exists"],
        "completion_level": payload["completion_level"],
        "raw_text": payload["raw_text"],
        "items": {
            "p0": [
                {
                    "text": item["text"],
                    "checked": item["checked"],
                    "checkbox": item["checkbox"],
                    "line_number": item["line_number"],
                }
                for item in payload["items"]["p0"]
            ],
            "p1": [
                {
                    "text": item["text"],
                    "checked": item["checked"],
                    "checkbox": item["checkbox"],
                    "line_number": item["line_number"],
                }
                for item in payload["items"]["p1"]
            ],
        },
        "summary": {
            "p0_total": payload["summary"]["p0_total"],
            "p0_done": payload["summary"]["p0_done"],
            "p1_total": payload["summary"]["p1_total"],
            "p1_done": payload["summary"]["p1_done"],
            "total": payload["summary"]["total"],
            "done": payload["summary"]["done"],
            "unchecked": payload["summary"]["unchecked"],
            "warnings": payload["summary"]["warnings"],
        },
        "completion": {
            "has_goals": payload["completion"]["has_goals"],
            "project_complete": payload["completion"]["project_complete"],
            "valid": payload["completion"]["valid"],
            "missing_sections": payload["completion"]["missing_sections"],
        },
    }

    assert normalized == {
        "path": ".doc/GOALS.md",
        "exists": True,
        "completion_level": "all",
        "raw_text": goals_text,
        "items": {
            "p0": [
                {
                    "text": "Lock config payload contract",
                    "checked": True,
                    "checkbox": "[x]",
                    "line_number": 4,
                },
                {
                    "text": "Preserve prompt preview redaction",
                    "checked": False,
                    "checkbox": "[ ]",
                    "line_number": 5,
                },
            ],
            "p1": [
                {
                    "text": "Distinguish missing and malformed logs",
                    "checked": False,
                    "checkbox": "[ ]",
                    "line_number": 8,
                },
            ],
        },
        "summary": {
            "p0_total": 2,
            "p0_done": 1,
            "p1_total": 1,
            "p1_done": 0,
            "total": 3,
            "done": 1,
            "unchecked": 2,
            "warnings": 0,
        },
        "completion": {
            "has_goals": True,
            "project_complete": False,
            "valid": True,
            "missing_sections": [],
        },
    }


def test_api_prompts_contract_returns_profile_aware_inventory_with_redacted_previews(endpoint_fixture: EndpointFixture) -> None:
    prompt_body = """# Local PM Instructions

Profile: {profile}
Repo: {repo}
"""
    _write(endpoint_fixture.prompts_dir / "pm_instructions.md", prompt_body)

    with endpoint_fixture.create_client() as client:
        payload = client.get("/api/prompts").json()

    items = {item["id"]: item for item in payload["items"]}
    override = items["pm_instructions"]
    template = items["pm_bootstrap"]

    normalized = {
        "dir": _relative_to(payload["dir"], endpoint_fixture.home),
        "profiles": sorted({item["profile"] for item in payload["items"]}),
        "items_without_content": all("content" not in item for item in payload["items"]),
        "override": {
            "file": override["file"],
            "path": _relative_to(override["path"], endpoint_fixture.home),
            "source": _normalize_prompt_source(str(override["source"]), endpoint_fixture.home),
            "scope": override["scope"],
            "profile": override["profile"],
            "mode": override["mode"],
            "preview": override["preview"],
            "content_length": override["content_length"],
        },
        "template": {
            "file": template["file"],
            "path": _relative_to(template["path"], endpoint_fixture.home),
            "source": _normalize_prompt_source(str(template["source"]), endpoint_fixture.home),
            "scope": template["scope"],
            "profile": template["profile"],
            "mode": template["mode"],
            "preview": template["preview"],
            "has_content_length": template["content_length"] > 0,
        },
    }

    assert normalized == {
        "dir": "prompts/agentcli",
        "profiles": ["enterprise"],
        "items_without_content": True,
        "override": {
            "file": "pm_instructions.md",
            "path": "prompts/agentcli/pm_instructions.md",
            "source": "prompts/agentcli",
            "scope": "PM",
            "profile": "enterprise",
            "mode": "override",
            "preview": "[redacted]",
            "content_length": len(prompt_body),
        },
        "template": {
            "file": "pm_bootstrap_prompt.md",
            "path": "prompts/agentcli/pm_bootstrap_prompt.md",
            "source": "templates/agent_prompts",
            "scope": "PM",
            "profile": "enterprise",
            "mode": "template",
            "preview": "[redacted]",
            "has_content_length": True,
        },
    }


def test_api_logs_tail_contract_distinguishes_missing_empty_and_malformed_sources(endpoint_fixture: EndpointFixture) -> None:
    _write(endpoint_fixture.run_dir / "logs" / "error.log", "")
    _write(
        endpoint_fixture.run_dir / "logs" / "events.jsonl",
        "\n".join(
            [
                "not json at all",
                json.dumps(
                    {
                        "ts": "2026-04-26T12:00:20",
                        "seq": 3,
                        "level": "info",
                        "event": "task_end",
                        "stage": "Dev",
                        "task_id": "T-023",
                        "message": "task end",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
    )

    with endpoint_fixture.create_client() as client:
        missing = client.get("/api/logs/tail", params={"source": "run_log"}).json()
        empty = client.get("/api/logs/tail", params={"source": "error_log"}).json()
        malformed = client.get("/api/logs/tail", params={"source": "events_jsonl"}).json()

    def _shape(payload: dict[str, object]) -> dict[str, object]:
        source = payload["source"]
        assert isinstance(source, dict)
        return {
            "ok": payload["ok"],
            "state": payload["state"],
            "source_id": payload["source_id"],
            "selected_source_id": payload["selected_source_id"],
            "source_name": source["name"],
            "source_available": source["available"],
            "next_cursor": payload["next_cursor"],
            "malformed_lines": payload["malformed_lines"],
            "entry_lines": [entry["line_number"] for entry in payload["entries"]],
            "entry_messages": [entry["msg"] for entry in payload["entries"]],
        }

    assert _shape(missing) == {
        "ok": False,
        "state": "missing_file",
        "source_id": "run_log",
        "selected_source_id": "run_log",
        "source_name": "run.log",
        "source_available": False,
        "next_cursor": 0,
        "malformed_lines": 0,
        "entry_lines": [],
        "entry_messages": [],
    }
    assert _shape(empty) == {
        "ok": True,
        "state": "empty",
        "source_id": "error_log",
        "selected_source_id": "error_log",
        "source_name": "error.log",
        "source_available": True,
        "next_cursor": 0,
        "malformed_lines": 0,
        "entry_lines": [],
        "entry_messages": [],
    }
    assert _shape(malformed) == {
        "ok": True,
        "state": "malformed_line",
        "source_id": "events_jsonl",
        "selected_source_id": "events_jsonl",
        "source_name": "events.jsonl",
        "source_available": True,
        "next_cursor": 2,
        "malformed_lines": 1,
        "entry_lines": [2],
        "entry_messages": ["task end"],
    }


def test_api_logs_contract_protects_default_logs_payload(endpoint_fixture: EndpointFixture) -> None:
    _write(
        endpoint_fixture.run_dir / "logs" / "run.log",
        "2026-04-26T12:00:10Z [INFO] PM T-023 run started\n",
    )

    with endpoint_fixture.create_client() as client:
        payload = client.get("/api/logs").json()

    source = payload["source"]
    assert isinstance(source, dict)
    assert {
        "source_id": payload["source_id"],
        "selected_source_id": payload["selected_source_id"],
        "entries_source_id": payload["entries_source_id"],
        "entries_source_kind": payload["entries_source_kind"],
        "source_name": source["name"],
        "source_available": source["available"],
        "sources": [item["id"] for item in payload["sources"]],
        "entry_messages": [entry["msg"] for entry in payload["entries"]],
        "last_line_message": payload["last_line"]["msg"],
        "eof": payload["eof"],
        "redaction": payload["redaction"],
    } == {
        "source_id": "run_log",
        "selected_source_id": "run_log",
        "entries_source_id": "run_log",
        "entries_source_kind": "log",
        "source_name": "run.log",
        "source_available": True,
        "sources": ["run_log", "error_log", "events_jsonl", "cycle_summary", "backend_transcript"],
        "entry_messages": ["2026-04-26T12:00:10Z [INFO] PM T-023 run started"],
        "last_line_message": "2026-04-26T12:00:10Z [INFO] PM T-023 run started",
        "eof": False,
        "redaction": {},
    }
