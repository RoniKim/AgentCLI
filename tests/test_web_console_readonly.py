from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import textwrap
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


class FakeRunnerController:
    def __init__(self, status: dict[str, object], *, status_error: str | None = None) -> None:
        self._status = dict(status)
        self.status_error = status_error
        run_dir = self._status.get("run_dir")
        self.run_dir = Path(str(run_dir)).expanduser().resolve() if run_dir else None

    def status(self) -> dict[str, object]:
        if self.status_error:
            raise RuntimeError(self.status_error)
        return dict(self._status)


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


def _make_log_entries(count):
    levels = ["debug", "info", "warn", "err"]
    stages = ["PM", "Dev", "QA"]
    entries = []
    for index in range(count):
        entries.append(
            {
                "ts": f"2026-04-26T12:{index % 60:02d}:00",
                "lvl": levels[index % len(levels)],
                "stage": stages[index % len(stages)],
                "message": f"log entry {index:03d}",
            }
        )
    return entries


def _write_run_bundle(
    run_dir: Path,
    *,
    task_id: str = "T-020",
    task_title: str = "API-backed observation path",
    branch: str = "main",
    status: str = "success",
    final_rc: int = 0,
    final_reason: str = "project_complete",
    stop_file: bool = False,
    backlog_tasks: list[dict[str, object]] | None = None,
    state_payload: dict[str, object] | None = None,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)

    task_files = ["agent_runner/web.py", "web_console/app.js"]
    if status == "failed":
        task_files.append("tests/test_web_console_readonly.py")
    backlog_task_status = "done" if status == "success" else ("failed" if status == "failed" else "in_progress")
    default_backlog_tasks = [
        {
            "id": task_id,
            "title": task_title,
            "prompt": "Read-only web console snapshot.",
            "files": task_files,
            "done_when": "Snapshot is stable.",
            "skills": ["observability"],
            "skills_rationale": "Surface the lifecycle contract in the browser.",
            "depends_on": [],
            "status": backlog_task_status,
        },
        {
            "id": "T-021",
            "title": "Backlog follows lifecycle records",
            "prompt": "Keep the backlog view aligned with lifecycle artifacts.",
            "files": ["web_console/app.js", "web_console/styles.css"],
            "done_when": "Dependency, attempt, file scope, and failure information render in the browser.",
            "skills": ["ui"],
            "skills_rationale": "Keep the backlog panel readable.",
            "depends_on": [task_id],
            "status": "pending",
        },
    ]
    backlog_tasks_payload = default_backlog_tasks if backlog_tasks is None else [dict(task) for task in backlog_tasks]
    if backlog_tasks_payload:
        task_id = str(backlog_tasks_payload[0].get("id") or task_id)
        task_title = str(backlog_tasks_payload[0].get("title") or task_title)
    secondary_task_id = str(backlog_tasks_payload[1].get("id") or "T-021") if len(backlog_tasks_payload) > 1 else "T-021"
    _write(
        run_dir / "BACKLOG.json",
        json.dumps(
            {
                "generated_at": "2026-04-26T12:00:00",
                "tasks": backlog_tasks_payload,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    default_state_payload: dict[str, object] = {"done": [], "failed": [], "warnings": []}
    if status == "success":
        default_state_payload["done"] = [task_id]
    elif status == "failed":
        default_state_payload["failed"] = [
            {"task": task_id, "reason": final_reason, "detail": "Dev attempt 2 failed during the build step.", "attempt": 2, "cycle": 1, "step": 0, "rc": final_rc}
        ]
    _write(run_dir / "STATE.json", json.dumps(state_payload if state_payload is not None else default_state_payload, ensure_ascii=False, indent=2) + "\n")
    task_count = len(backlog_tasks_payload)

    cycle = 1
    step = 0
    pm_started = "2026-04-26T12:00:00"
    pm_ended = "2026-04-26T12:01:00"
    dev_first_started = "2026-04-26T12:02:00"
    dev_first_ended = "2026-04-26T12:03:00"
    dev_second_started = "2026-04-26T12:04:00"
    dev_second_ended = "2026-04-26T12:05:00"
    qa_started = "2026-04-26T12:06:00"
    qa_ended = "2026-04-26T12:07:00"

    pm_output = (
        "Backlog planning complete.\n"
        f"Primary task: {task_id}\n"
        f"Dependency chain: {secondary_task_id} depends on {task_id}\n"
    )
    dev_success_output = (
        "Dev attempt 2 completed successfully.\n"
        f"Changed files: agent_runner/web.py, web_console/app.js\n"
    )
    dev_failure_output = (
        "Build failed during attempt 2.\n"
        f"Reason: {final_reason}\n"
    )
    attempt_one_output = "Attempt 1 stopped after the build step reported an error.\n"
    qa_output = (
        "{\"cycle\": 1, \"status\": \"verified\", \"tasks\": ["
        f"{{\"id\": {json.dumps(task_id)}, \"status\": \"verified\"}}"
        "]}\n"
    )

    attempt_root = run_dir / "tasks" / f"c{cycle:03d}_s{step:03d}_{task_id}"
    attempt_01 = attempt_root / "attempt_01"
    attempt_02 = attempt_root / "attempt_02"
    attempt_01.mkdir(parents=True, exist_ok=True)
    attempt_02.mkdir(parents=True, exist_ok=True)

    _write(attempt_01 / "build.txt", attempt_one_output)
    _write(attempt_02 / "dev_output.txt", dev_success_output if status == "success" else dev_failure_output)
    _write(attempt_02 / "build.txt", dev_success_output if status == "success" else dev_failure_output)
    _write(attempt_02 / "NOTES.md", f"Attempt 2 for {task_id}.\n")
    _write(run_dir / "dev_logs" / f"c{cycle:03d}_s{step:03d}_{task_id}_a02.txt", dev_success_output if status == "success" else dev_failure_output)
    _write(run_dir / "pm_final_output_cycle_001.txt", pm_output)
    _write(run_dir / "NOTES_PM.md", pm_output)
    _write(run_dir / "qa_followups_cycle_001.json", qa_output)

    metrics_entries = [
        {"ts": pm_started, "seq": 1, "level": "info", "event": "cycle_start", "stage": "PM", "cycle": cycle, "message": "cycle start"},
        {"ts": pm_started, "seq": 2, "level": "info", "event": "pm_start", "stage": "PM", "cycle": cycle, "message": "pm stage start"},
        {"ts": pm_ended, "seq": 3, "level": "info", "event": "pm_end", "stage": "PM", "cycle": cycle, "rc": 0, "reason": "pm_ready", "message": "pm stage end"},
        {"ts": dev_first_started, "seq": 4, "level": "info", "event": "task_start", "stage": "Dev", "cycle": cycle, "step": step, "task_id": task_id, "task_title": task_title, "message": "task start"},
        {"ts": dev_first_started, "seq": 5, "level": "info", "event": "dev_attempt_start", "stage": "Dev", "cycle": cycle, "step": step, "task_id": task_id, "task_title": task_title, "attempt": 1, "model": "gpt-5.4-mini", "message": "dev attempt 1 start"},
        {"ts": dev_first_ended, "seq": 6, "level": "warn", "event": "dev_attempt_retry", "stage": "Dev", "cycle": cycle, "step": step, "task_id": task_id, "task_title": task_title, "attempt": 1, "reason": "build_failed", "message": "retry after build failure"},
        {"ts": dev_second_started, "seq": 7, "level": "info", "event": "dev_attempt_start", "stage": "Dev", "cycle": cycle, "step": step, "task_id": task_id, "task_title": task_title, "attempt": 2, "model": "gpt-5.4-mini", "message": "dev attempt 2 start"},
        {"ts": dev_second_ended, "seq": 8, "level": "info", "event": "task_end", "stage": "Dev", "cycle": cycle, "step": step, "task_id": task_id, "task_title": task_title, "attempt": 2, "rc": 0 if status == "success" else final_rc, "reason": "completed" if status == "success" else final_reason, "message": "task end"},
    ]
    metrics_entries.extend(
        [
            {"ts": qa_started, "seq": 9, "level": "info", "event": "qa_start", "stage": "QA", "cycle": cycle, "task_id": task_id, "task_title": task_title, "message": "qa start"},
            {"ts": qa_ended, "seq": 10, "level": "info", "event": "qa_end", "stage": "QA", "cycle": cycle, "rc": 0 if status == "success" else final_rc, "reason": "qa_verified" if status == "success" else final_reason, "message": "qa end"},
        ]
    )
    metrics_entries.append(
        {"ts": "2026-04-26T12:08:00", "seq": 11, "level": "info", "event": "cycle_end", "stage": "Dev", "cycle": cycle, "rc": 0 if status == "success" else final_rc, "done": 1 if status == "success" else 0, "total": task_count, "failed": 1 if status == "failed" else 0, "duration_seconds": 480, "message": "cycle end"}
    )
    _write(run_dir / "metrics.jsonl", "\n".join(json.dumps(item, ensure_ascii=False) for item in metrics_entries) + "\n")

    run_summary_stages = [
        {"name": "PM", "status": "ok", "rc": 0, "reason": "pm_ready", "cycle": cycle, "startedAt": pm_started, "endedAt": pm_ended, "durationSec": 60, "model": "gpt-5.5", "taskId": task_id, "taskTitle": task_title, "attempt": 1},
        {"name": "Dev", "status": "ok" if status == "success" else "fail", "rc": 0 if status == "success" else final_rc, "reason": "completed" if status == "success" else final_reason, "cycle": cycle, "startedAt": dev_second_started, "endedAt": dev_second_ended, "durationSec": 60, "model": "gpt-5.4-mini", "taskId": task_id, "taskTitle": task_title, "attempt": 2, "step": step},
        {"name": "QA", "status": "ok" if status == "success" else "fail", "rc": 0 if status == "success" else final_rc, "reason": "qa_verified" if status == "success" else final_reason, "cycle": cycle, "startedAt": qa_started, "endedAt": qa_ended, "durationSec": 60, "model": "gpt-5.4-mini", "taskId": task_id, "taskTitle": task_title, "attempt": 2},
    ]
    _write(
        run_dir / "run_summary.json",
        json.dumps(
            {
                "run_id": run_dir.name,
                "repo": str(run_dir.parents[2]),
                "branch": branch,
                "cycles": [{"cycle": cycle, "stages": run_summary_stages}],
                "final": {"rc": final_rc, "reason": final_reason},
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
                "ts": "2026-04-26T12:08:00",
                "cycle": cycle,
                "run_dir": str(run_dir),
                "done": 1 if status == "success" else 0,
                "skipped": 0,
                "total_tasks": task_count,
                "failed_count": 1 if status == "failed" else 0,
                "duration_seconds": 480,
                "status": status if status in {"success", "failed", "stopped"} else "running",
                "rc": final_rc,
                "stop_reason": final_reason if status == "stopped" else "",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    _write(run_dir / "cycle_summary.log", f"2026-04-26T12:08:00 cycle=1 done={1 if status == 'success' else 0}/{task_count} failed={1 if status == 'failed' else 0} dt=480.0s\n")
    if stop_file:
        _write(run_dir / "STOP", "")


def _make_no_run_snapshot():
    return {
        "ok": True,
        "latest_run_dir": "",
        "repo": {
            "path": "",
            "name": "agentcli",
            "head": "",
            "branch": "HEAD",
        },
        "active_run": {},
        "stages": [],
        "backlog": {
            "items": [],
            "counts": {},
            "selected_id": "",
        },
        "goals": {
            "items": {"p0": [], "p1": []},
            "path": ".doc/GOALS.md",
            "completion": {},
        },
        "config": {
            "path": "config/agentcli.json",
            "source": "default",
            "data": {},
            "resolved_prompts_dir": "",
        },
        "prompts": {
            "items": [],
            "dir": "",
            "exists": False,
        },
        "logs": {
            "entries": [],
            "tail": "",
            "files": {},
        },
        "notifications": [],
        "history": {
            "items": [],
        },
        "metrics": {},
        "worktree": {
            "status": "none",
        },
        "progress": {
            "latest_run_dir": None,
            "run_status": "idle",
            "tasks_done": 0,
            "tasks_total": 0,
            "tasks_failed": 0,
            "progress": 0,
            "current_task_id": "",
            "current_task_title": "",
            "goals": {"p0": [], "p1": []},
            "backlog": {"items": [], "counts": {}, "selected_id": ""},
            "final_reason": "",
            "state": {"done": [], "failed": [], "warnings": []},
        },
    }


def _make_partial_snapshot():
    repo_path = "C:/Dev/AgentCLI"
    return {
        "ok": True,
        "latest_run_dir": ".AgentCLI/agent_runs/20260426-121500",
        "repo": {
            "path": repo_path,
            "name": "AgentCLI",
            "head": "abc12345",
            "branch": "main",
        },
        "active_run": {
            "id": "run_20260426_121500",
            "repo": repo_path,
            "repoLabel": "AgentCLI",
            "branch": "main",
            "backend": "codex",
            "startedAt": 1714133700000,
            "stage": "Dev",
            "stageIndex": 1,
            "iteration": 2,
            "maxIterations": 4,
            "progress": 0.5,
            "budgetUsed": 0.4,
            "tokens": {"in": 1200, "out": 450},
            "quota": {"window": "5h", "used": 0.4},
            "elapsedSec": 1200,
            "status": "running",
            "task": "T-020",
            "taskTitle": "API-backed observation path",
        },
        "stages": [
            {
                "id": "PM",
                "label": "PM",
                "title": "Backlog planning",
                "status": "done",
                "durationSec": 300,
                "model": "gpt-5.1-codex-mini",
                "cycle": 1,
                "startedAt": 1714133400000,
                "endedAt": 1714133700000,
                "taskId": "T-020",
                "taskTitle": "API-backed observation path",
                "attempt": 1,
                "recentOutput": "PM planning complete.",
                "reason": "pm_ready",
                "rc": 0,
            },
            {
                "id": "Dev",
                "label": "Dev",
                "title": "API-backed observation path",
                "status": "running",
                "durationSec": 960,
                "model": "gpt-5.1-codex-mini",
                "cycle": 1,
                "startedAt": 1714133700000,
                "endedAt": None,
                "taskId": "T-020",
                "taskTitle": "API-backed observation path",
                "attempt": 2,
                "recentOutput": "Dev attempt 2 is still running.",
                "reason": "",
                "rc": None,
            },
        ],
        "backlog": {
            "items": [
                {
                    "id": "T-020",
                    "title": "API-backed observation path",
                    "status": "in_progress",
                    "priority": "P0",
                    "tags": ["web", "api"],
                    "estimate": "M",
                    "skill": "observability",
                    "description": "Wire the browser console to read-only status endpoints.",
                    "prompt": "Read-only web console snapshot.",
                    "files": ["agent_runner/web.py", "web_console/app.js"],
                    "depends_on": [],
                    "file_scope": "agent_runner/web.py, web_console/app.js",
                    "attempt": 2,
                    "failure": {"reason": "", "detail": "", "cycle": None, "step": None, "rc": None},
                    "failure_reason": "",
                    "failure_detail": "",
                    "recent_output": "Dev attempt 2 is still running.",
                    "cycle": 1,
                    "step": 0,
                    "task_title": "API-backed observation path",
                    "model": "gpt-5.1-codex-mini",
                    "started_at": 1714133700000,
                    "ended_at": None,
                }
            ],
            "counts": {"pending": 0, "in_progress": 1, "done": 0, "failed": 0},
            "selected_id": "T-020",
        },
        "goals": {
            "items": {
                "p0": [
                    {
                        "done": False,
                        "text": "Observe the current run in a browser without CLI shell access",
                        "note": "",
                    }
                ],
                "p1": [
                    {
                        "done": False,
                        "text": "Keep the browser useful when no run exists",
                        "note": "",
                    }
                ],
            },
            "path": ".doc/GOALS.md",
            "completion": {"project_complete": False},
        },
        "config": {
            "path": "config/agentcli.json",
            "source": "read-only",
            "data": {
                "repo": repo_path,
                "execution_backend": "codex",
                "iterations": 4,
                "prompts_dir": "prompts/agentcli",
            },
            "resolved_prompts_dir": "prompts/agentcli",
        },
        "prompts": {
            "items": [
                {
                    "id": "bootstrap",
                    "file": "bootstrap_prompt.md",
                    "scope": "PM",
                    "source": "repo",
                    "mode": "template",
                    "updated": "2026-04-26T12:00:00",
                    "summary": "Bootstrap the read-only console view.",
                    "preview": "Open the status dashboard first.",
                }
            ],
            "dir": "prompts/agentcli",
            "exists": True,
        },
        "logs": {
            "entries": _make_log_entries(130),
            "tail": "partial snapshot tail",
            "files": {
                "cycle_summary": ".AgentCLI/agent_runs/20260426-121500/cycle_summary.log",
            },
        },
        "notifications": [
            {
                "ts": 1714133760000,
                "kind": "task_done",
                "text": "T-019 | QA verification completed",
                "run": "run_20260426_121000",
            }
        ],
        "history": {
            "items": [
                {
                    "id": "run_20260426_121000",
                    "startedAt": 1714133400000,
                    "status": "success",
                    "tasksDone": 2,
                    "tasksTotal": 2,
                    "branch": "main",
                    "durationSec": 780,
                    "stopReason": "",
                    "runDir": ".AgentCLI/agent_runs/20260426-121000",
                    "lastCycle": "cycle=1 done=2/2",
                }
            ],
        },
        "metrics": {
            "tokens24h": [10, 20, 30],
            "success24h": [1, 1, 0],
            "budget": [0.1, 0.2, 0.3],
            "tokens": {"in": 1200, "out": 450},
            "last_stage": "Dev",
            "quota": {"window": "5h", "used": 0.4},
            "quota_window": "5h",
            "quota_used": 0.4,
        },
        "worktree": {
            "status": "pending",
            "mode": "manual",
            "branch": "main",
            "worktree": "C:/Dev/AgentCLI.worktree",
            "patch": ".AgentCLI/agent_runs/20260426-121500/worktree.patch",
            "pendingFile": ".AgentCLI/agent_runs/20260426-121500/WORKTREE_MERGE_PENDING.json",
            "summary": "Pending merge review.",
            "risk": "Review before merge.",
            "changedFiles": [
                {
                    "path": "web_console/app.js",
                    "kind": "modified",
                    "note": "adapter wiring",
                }
            ],
            "checklist": ["Inspect patch hunks", "Verify no secret leakage"],
            "runDir": ".AgentCLI/agent_runs/20260426-121500",
            "headRef": "abc12345",
            "lastRc": 0,
        },
        "progress": {
            "latest_run_dir": ".AgentCLI/agent_runs/20260426-121500",
            "run_status": "running",
            "tasks_done": 1,
            "tasks_total": 2,
            "tasks_failed": 0,
            "progress": 0.5,
            "current_task_id": "T-020",
            "current_task_title": "API-backed observation path",
            "goals": {"p0": [], "p1": []},
            "backlog": {"items": [], "counts": {}, "selected_id": "T-020"},
            "final_reason": "",
            "state": {"done": [], "failed": [], "warnings": []},
        },
    }


def _make_normal_snapshot():
    repo_path = "C:/Dev/AgentCLI"
    return {
        "ok": True,
        "latest_run_dir": ".AgentCLI/agent_runs/20260426-120000",
        "repo": {
            "path": repo_path,
            "name": "AgentCLI",
            "head": "fedcba98",
            "branch": "main",
        },
        "active_run": {
            "id": "run_20260426_120000",
            "repo": repo_path,
            "repoLabel": "AgentCLI",
            "branch": "main",
            "backend": "codex",
            "startedAt": 1714132800000,
            "stage": "Dev",
            "stageIndex": 1,
            "iteration": 3,
            "maxIterations": 5,
            "progress": 0.72,
            "budgetUsed": 0.56,
            "tokens": {"in": 18420, "out": 6421},
            "quota": {"window": "5h", "used": 0.41},
            "elapsedSec": 1680,
            "status": "running",
            "task": "T-020",
            "taskTitle": "API-backed observation path",
        },
        "stages": [
            {
                "id": "PM",
                "label": "PM",
                "title": "Backlog planning",
                "status": "done",
                "durationSec": 300,
                "model": "gpt-5.1-codex-mini",
                "cycle": 1,
                "startedAt": 1714133400000,
                "endedAt": 1714133700000,
                "taskId": "T-020",
                "taskTitle": "API-backed observation path",
                "attempt": 1,
                "recentOutput": "PM planning complete.",
                "reason": "pm_ready",
                "rc": 0,
            },
            {
                "id": "Dev",
                "label": "Dev",
                "title": "API-backed observation path",
                "status": "running",
                "durationSec": 960,
                "model": "gpt-5.1-codex-mini",
                "cycle": 1,
                "startedAt": 1714133700000,
                "endedAt": None,
                "taskId": "T-020",
                "taskTitle": "API-backed observation path",
                "attempt": 2,
                "recentOutput": "Dev attempt 2 is still running.",
                "reason": "",
                "rc": None,
            },
            {
                "id": "QA",
                "label": "QA",
                "title": "Verification",
                "status": "pending",
                "durationSec": 0,
                "model": "gpt-5.1-codex-mini",
                "cycle": 1,
                "startedAt": None,
                "endedAt": None,
                "taskId": "T-020",
                "taskTitle": "API-backed observation path",
                "attempt": 2,
                "recentOutput": "QA has not started yet.",
                "reason": "",
                "rc": None,
            },
        ],
        "backlog": {
            "items": [
                {
                    "id": "T-020",
                    "title": "API-backed observation path",
                    "status": "in_progress",
                    "priority": "P0",
                    "tags": ["web", "api"],
                    "estimate": "M",
                    "skill": "observability",
                    "description": "Wire the browser console to read-only status endpoints.",
                    "prompt": "Read-only web console snapshot.",
                    "files": ["agent_runner/web.py", "web_console/app.js"],
                    "depends_on": [],
                    "file_scope": "agent_runner/web.py, web_console/app.js",
                    "attempt": 2,
                    "failure": {"reason": "", "detail": "", "cycle": None, "step": None, "rc": None},
                    "failure_reason": "",
                    "failure_detail": "",
                    "recent_output": "Dev attempt 2 is still running.",
                    "cycle": 1,
                    "step": 0,
                    "task_title": "API-backed observation path",
                    "model": "gpt-5.1-codex-mini",
                    "started_at": 1714133700000,
                    "ended_at": None,
                },
                {
                    "id": "T-021",
                    "title": "Bounded log tail",
                    "status": "pending",
                    "priority": "P1",
                    "tags": ["logs"],
                    "estimate": "S",
                    "skill": "",
                    "description": "Keep the log DOM bounded during refresh.",
                    "prompt": "Read-only web console snapshot.",
                    "files": ["web_console/app.js", "web_console/styles.css"],
                    "depends_on": ["T-020"],
                    "file_scope": "web_console/app.js, web_console/styles.css",
                    "attempt": None,
                    "failure": {"reason": "", "detail": "", "cycle": None, "step": None, "rc": None},
                    "failure_reason": "",
                    "failure_detail": "",
                    "recent_output": "",
                    "cycle": None,
                    "step": None,
                    "task_title": "",
                    "model": "",
                    "started_at": None,
                    "ended_at": None,
                },
            ],
            "counts": {"pending": 1, "in_progress": 1, "done": 0, "failed": 0},
            "selected_id": "T-020",
        },
        "goals": {
            "items": {
                "p0": [
                    {
                        "done": False,
                        "text": "Observe the current run in a browser without CLI shell access",
                        "note": "",
                    }
                ],
                "p1": [
                    {
                        "done": True,
                        "text": "Keep the browser useful when no run exists",
                        "note": "",
                    }
                ],
            },
            "path": ".doc/GOALS.md",
            "completion": {"project_complete": False},
        },
        "config": {
            "path": "config/agentcli.json",
            "source": "read-only",
            "data": {
                "repo": repo_path,
                "execution_backend": "codex",
                "iterations": 5,
                "prompts_dir": "prompts/agentcli",
                "telegram": {
                    "enabled": True,
                    "instance_name": "home-pc-main",
                },
            },
            "resolved_prompts_dir": "prompts/agentcli",
        },
        "prompts": {
            "items": [
                {
                    "id": "bootstrap",
                    "file": "bootstrap_prompt.md",
                    "scope": "PM",
                    "source": "repo",
                    "mode": "template",
                    "updated": "2026-04-26T12:00:00",
                    "summary": "Bootstrap the read-only console view.",
                    "preview": "Open the status dashboard first.",
                },
                {
                    "id": "dev",
                    "file": "dev_prompt.md",
                    "scope": "Dev",
                    "source": "repo",
                    "mode": "override",
                    "updated": "2026-04-26T12:05:00",
                    "summary": "Use browser data adapters for read-only observation.",
                    "preview": "Prefer adapter outputs over hardcoded shell data.",
                },
            ],
            "dir": "prompts/agentcli",
            "exists": True,
        },
        "logs": {
            "entries": [
                {
                    "ts": "2026-04-26T12:00:00",
                    "lvl": "info",
                    "stage": "boot",
                    "message": "AgentCLI web console started.",
                },
                {
                    "ts": "2026-04-26T12:01:00",
                    "lvl": "info",
                    "stage": "PM",
                    "message": "Backlog emitted from read-only API.",
                },
                {
                    "ts": "2026-04-26T12:02:00",
                    "lvl": "warn",
                    "stage": "Dev",
                    "message": "Browser view should stay bounded on refresh.",
                },
            ],
            "tail": "normal snapshot tail",
            "files": {
                "cycle_summary": ".AgentCLI/agent_runs/20260426-120000/cycle_summary.log",
                "run_log": ".AgentCLI/agent_runs/20260426-120000/logs/run.log",
            },
        },
        "notifications": [
            {
                "ts": 1714132860000,
                "kind": "run_start",
                "text": "Run started | main",
                "run": "run_20260426_120000",
            },
            {
                "ts": 1714132920000,
                "kind": "task_done",
                "text": "T-019 | verification completed",
                "run": "run_20260426_120000",
            },
        ],
        "history": {
            "items": [
                {
                    "id": "run_20260426_120000",
                    "startedAt": 1714132800000,
                    "status": "running",
                    "tasksDone": 1,
                    "tasksTotal": 2,
                    "branch": "main",
                    "durationSec": 1680,
                    "stopReason": "",
                    "runDir": ".AgentCLI/agent_runs/20260426-120000",
                    "lastCycle": "cycle=3 done=1/2",
                }
            ],
        },
        "metrics": {
            "tokens24h": [120, 240, 360, 480],
            "success24h": [1, 1, 1, 0],
            "budget": [0.1, 0.2, 0.35, 0.56],
            "tokens": {"in": 18420, "out": 6421},
            "last_stage": "Dev",
            "quota": {"window": "5h", "used": 0.41},
            "quota_window": "5h",
            "quota_used": 0.41,
        },
        "worktree": {
            "status": "pending",
            "mode": "manual",
            "branch": "main",
            "worktree": "C:/Dev/AgentCLI.worktree",
            "patch": ".AgentCLI/agent_runs/20260426-120000/worktree.patch",
            "pendingFile": ".AgentCLI/agent_runs/20260426-120000/WORKTREE_MERGE_PENDING.json",
            "summary": "Pending merge review.",
            "risk": "Review before merge.",
            "changedFiles": [
                {
                    "path": "web_console/app.js",
                    "kind": "modified",
                    "note": "adapter wiring",
                },
                {
                    "path": "web_console/styles.css",
                    "kind": "modified",
                    "note": "section banners",
                },
            ],
            "checklist": [
                "Inspect patch hunks",
                "Verify no secret leakage",
                "Approve merge only after review",
            ],
            "runDir": ".AgentCLI/agent_runs/20260426-120000",
            "headRef": "fedcba98",
            "lastRc": 0,
        },
        "progress": {
            "latest_run_dir": ".AgentCLI/agent_runs/20260426-120000",
            "run_status": "running",
            "tasks_done": 1,
            "tasks_total": 2,
            "tasks_failed": 0,
            "progress": 0.5,
            "current_task_id": "T-020",
            "current_task_title": "API-backed observation path",
            "goals": {
                "p0": ["Observe the current run in a browser without CLI shell access"],
                "p1": ["Keep the browser useful when no run exists"],
            },
            "backlog": {"items": [], "counts": {}, "selected_id": "T-020"},
            "final_reason": "",
            "state": {"done": ["T-019"], "failed": [], "warnings": []},
        },
    }


def _run_adapter_harness(fixtures):
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    script = textwrap.dedent(
        """
        const fs = require('fs');
        const vm = require('vm');
        const sourcePath = __SOURCE_PATH__;
        const source = fs.readFileSync(sourcePath, 'utf8');
        const root = { innerHTML: '' };
        const document = {
          title: '',
          body: {
            appendChild() {},
            removeChild() {},
          },
          createElement(tag) {
            return {
              tagName: String(tag).toUpperCase(),
              style: {},
              value: '',
              setAttribute() {},
              select() {},
              focus() {},
              setSelectionRange() {},
            };
          },
          execCommand() { return true; },
          getElementById() { return root; },
          addEventListener() {},
          querySelector() { return null; },
        };
        const context = {
          console,
          JSON,
          Date,
          Math,
          Number,
          String,
          Boolean,
          Array,
          Object,
          RegExp,
          Error,
          Promise,
          setTimeout() { return 1; },
          clearTimeout() {},
          setInterval() { return 1; },
          clearInterval() {},
          fetch() { throw new Error('fetch should not run during adapter import'); },
          navigator: { clipboard: { writeText() { return Promise.resolve(); } } },
          history: { replaceState() {} },
          location: { hash: '' },
          localStorage: {
            _data: Object.create(null),
            getItem(key) {
              return Object.prototype.hasOwnProperty.call(this._data, key) ? this._data[key] : null;
            },
            setItem(key, value) {
              this._data[key] = String(value);
            },
            removeItem(key) {
              delete this._data[key];
            },
          },
          document,
          addEventListener() {},
          removeEventListener() {},
        };
        context.window = context;
        context.globalThis = context;
        context.__AGENTCLI_SKIP_BOOTSTRAP__ = true;
        vm.runInNewContext(source, context, { filename: sourcePath });
        const adapters = context.__AGENTCLI_ADAPTERS__;
        if (!adapters) {
          throw new Error('Missing __AGENTCLI_ADAPTERS__ export');
        }
        const fixtures = __FIXTURES__;
        const results = fixtures.map((fixture) => {
          if (fixture.kind === 'snapshot') {
            return adapters.normalizeSnapshot(fixture.data);
          }
          if (fixture.kind === 'fallback') {
            return adapters.createFallbackFixture();
          }
          if (fixture.kind === 'call') {
            const name = fixture.name || fixture.fn;
            const fn = adapters[name];
            if (typeof fn !== 'function') {
              throw new Error('Unknown adapter function: ' + name);
            }
            const args = Array.isArray(fixture.args) ? fixture.args : [];
            return fn(...args);
          }
          throw new Error('Unknown fixture kind: ' + fixture.kind);
        });
        process.stdout.write(JSON.stringify(results));
        """
    ).replace("__SOURCE_PATH__", json.dumps(str(WEB_CONSOLE / "app.js"))).replace(
        "__FIXTURES__", json.dumps(fixtures, ensure_ascii=False)
    )
    completed = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return json.loads(completed.stdout)


def _run_log_tail_harness(ops):
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    script = textwrap.dedent(
        """
        const fs = require('fs');
        const vm = require('vm');
        const sourcePath = __SOURCE_PATH__;
        const source = fs.readFileSync(sourcePath, 'utf8');
        const root = { innerHTML: '' };
        const document = {
          title: '',
          body: {
            appendChild() {},
            removeChild() {},
          },
          createElement(tag) {
            return {
              tagName: String(tag).toUpperCase(),
              style: {},
              value: '',
              setAttribute() {},
              select() {},
              focus() {},
              setSelectionRange() {},
            };
          },
          execCommand() { return true; },
          getElementById() { return root; },
          addEventListener() {},
          querySelector() { return null; },
        };
        const context = {
          console,
          JSON,
          Date,
          Math,
          Number,
          String,
          Boolean,
          Array,
          Object,
          RegExp,
          Error,
          Promise,
          setTimeout() { return 1; },
          clearTimeout() {},
          setInterval() { return 1; },
          clearInterval() {},
          fetch() { throw new Error('fetch should not run during adapter import'); },
          navigator: { clipboard: { writeText() { return Promise.resolve(); } } },
          history: { replaceState() {} },
          location: { hash: '' },
          localStorage: {
            _data: Object.create(null),
            getItem(key) {
              return Object.prototype.hasOwnProperty.call(this._data, key) ? this._data[key] : null;
            },
            setItem(key, value) {
              this._data[key] = String(value);
            },
            removeItem(key) {
              delete this._data[key];
            },
          },
          document,
          addEventListener() {},
          removeEventListener() {},
        };
        context.window = context;
        context.globalThis = context;
        context.__AGENTCLI_SKIP_BOOTSTRAP__ = true;
        vm.runInNewContext(source, context, { filename: sourcePath });
        const adapters = context.__AGENTCLI_ADAPTERS__;
        if (!adapters) {
          throw new Error('Missing __AGENTCLI_ADAPTERS__ export');
        }
        const ops = __OPS__;
        const results = ops.map((op) => {
          if (op.kind === 'state') {
            return adapters.createBlankLogTailState();
          }
          if (op.kind === 'query') {
            return {
              query: adapters.buildLogTailQuery(op.filters || {}, op.options || {}),
              url: adapters.buildLogTailRequestUrl(op.filters || {}, op.options || {}),
            };
          }
          if (op.kind === 'apply') {
            return adapters.applyLogTailPayload(op.previous, op.payload, op.options || {});
          }
          if (op.kind === 'describe') {
            return adapters.describeLogTailState(op.tail || {});
          }
          if (op.kind === 'banner') {
            return {
              description: adapters.describeLogTailState(op.tail || {}),
              banner: adapters.renderLogTailBanner(op.tail || {}),
              filters: adapters.renderLogTailFilters(op.tail || {}),
            };
          }
          if (op.kind === 'clipboard') {
            return adapters.buildLogTailClipboardText(op.entries || [], op.selected || []);
          }
          if (op.kind === 'download') {
            return adapters.buildLogTailDownloadArtifact(op.tail || {}, op.context || {});
          }
          if (op.kind === 'format') {
            return adapters.formatLogTailLine(op.entry || {});
          }
          throw new Error('Unknown op kind: ' + op.kind);
        });
        process.stdout.write(JSON.stringify(results));
        """
    ).replace("__SOURCE_PATH__", json.dumps(str(WEB_CONSOLE / "app.js"))).replace(
        "__OPS__", json.dumps(ops, ensure_ascii=False)
    )
    completed = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return json.loads(completed.stdout)


def _run_log_tail_session_harness(steps, fetch_responses=None):
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    script = "(async () => {\n" + textwrap.dedent(
        """
        const fs = require('fs');
        const vm = require('vm');

        const sourcePath = __SOURCE_PATH__;
        const source = fs.readFileSync(sourcePath, 'utf8');
        const fetchResponses = __FETCH_RESPONSES__;
        const steps = __STEPS__;
        const clipboard = [];
        const downloads = [];
        const fetchCalls = [];
        const intervals = [];
        const cleared = [];
        const roots = {
          app: { innerHTML: '' },
          topbar: { innerHTML: '' },
          sidebar: { innerHTML: '' },
          main: {
            innerHTML: '',
            dataset: {},
            scrollTop: 0,
            querySelector() {
              return { scrollTop: 0, scrollHeight: 0 };
            },
          },
          'overlay-root': { innerHTML: '' },
        };

        class TestBlob {
          constructor(parts, options) {
            this.parts = Array.isArray(parts) ? parts : [parts];
            this.options = options || {};
            this.textValue = this.parts
              .map((part) => (typeof part === 'string' ? part : String(part)))
              .join('');
          }

          async text() {
            return this.textValue;
          }
        }

        const document = {
          title: '',
          body: {
            appendChild() {},
            removeChild() {},
          },
          createElement(tag) {
            const name = String(tag).toLowerCase();
            if (name === 'a') {
              return {
                href: '',
                download: '',
                rel: '',
                style: {},
                click() {
                  downloads.push({
                    kind: 'click',
                    download: this.download,
                    href: this.href,
                  });
                },
                setAttribute() {},
              };
            }
            return {
              tagName: String(tag).toUpperCase(),
              style: {},
              value: '',
              setAttribute() {},
              select() {},
              focus() {},
              setSelectionRange() {},
              click() {},
            };
          },
          execCommand() {
            return true;
          },
          getElementById(id) {
            return roots[id] || null;
          },
          addEventListener() {},
          querySelector() {
            return null;
          },
        };

        let intervalSeq = 0;
        const defaultResponse = {
          ok: true,
          state: 'loading',
          entries: [],
          next_cursor: 0,
          cursor: 0,
          source: {
            path: '',
            name: '',
            exists: true,
          },
        };

        const context = {
          console,
          JSON,
          Date,
          Math,
          Number,
          String,
          Boolean,
          Array,
          Object,
          RegExp,
          Error,
          Promise,
          Blob: TestBlob,
          setTimeout() {
            return 1;
          },
          clearTimeout() {},
          setInterval(_, delay) {
            const id = ++intervalSeq;
            intervals.push({ id, delay });
            return id;
          },
          clearInterval(id) {
            cleared.push(id);
          },
          fetch(url) {
            fetchCalls.push(url);
            const response = fetchResponses.length ? fetchResponses.shift() : defaultResponse;
            if (typeof response === 'function') {
              return Promise.resolve(response(url));
            }
            return Promise.resolve({
              ok: response.ok !== false,
              status: response.status || (response.ok === false ? 500 : 200),
              json() {
                return Promise.resolve(response.body || response);
              },
            });
          },
          navigator: {
            clipboard: {
              writeText(text) {
                clipboard.push(text);
                return Promise.resolve();
              },
            },
          },
          URL: {
            createObjectURL(blob) {
              downloads.push({
                kind: 'blob',
                type: blob && blob.options ? blob.options.type : '',
                text: blob && typeof blob.textValue === 'string'
                  ? blob.textValue
                  : Array.isArray(blob && blob.parts)
                    ? blob.parts.join('')
                    : '',
              });
              return `blob:${downloads.length}`;
            },
            revokeObjectURL(url) {
              downloads.push({ kind: 'revoke', url });
            },
          },
          history: { replaceState() {} },
          location: { hash: '' },
          localStorage: {
            _data: Object.create(null),
            getItem(key) {
              return Object.prototype.hasOwnProperty.call(this._data, key) ? this._data[key] : null;
            },
            setItem(key, value) {
              this._data[key] = String(value);
            },
            removeItem(key) {
              delete this._data[key];
            },
          },
          document,
          addEventListener() {},
          removeEventListener() {},
        };

        context.window = context;
        context.globalThis = context;
        context.__AGENTCLI_SKIP_BOOTSTRAP__ = true;

        vm.runInNewContext(source, context, { filename: sourcePath });

        const adapters = context.__AGENTCLI_ADAPTERS__;
        if (!adapters) {
          throw new Error('Missing __AGENTCLI_ADAPTERS__ export');
        }

        const results = [];
        for (const step of steps) {
          if (step.kind !== 'call') {
            throw new Error('Unknown step kind: ' + step.kind);
          }
          const fn = adapters[step.name];
          if (typeof fn !== 'function') {
            throw new Error('Missing adapter: ' + step.name);
          }
          let value = fn(...(step.args || []));
          if (value && typeof value.then === 'function') {
            value = await value;
          }
          results.push(value);
          await Promise.resolve();
          await Promise.resolve();
        }

        process.stdout.write(JSON.stringify({ results, clipboard, downloads, fetchCalls, intervals, cleared }));
        """
    ) + "\n})().catch((error) => {\n  console.error(error);\n  process.exit(1);\n});\n"
    script = script.replace("__SOURCE_PATH__", json.dumps(str(WEB_CONSOLE / "app.js"))).replace(
        "__FETCH_RESPONSES__", json.dumps(fetch_responses or [], ensure_ascii=False)
    ).replace("__STEPS__", json.dumps(steps, ensure_ascii=False))
    completed = subprocess.run([node, "-"], input=script, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return json.loads(completed.stdout)


class WebConsoleRedactionHelperTests(unittest.TestCase):
    def test_redact_web_log_payload_redacts_files_metadata(self) -> None:
        from agent_runner.web import _redact_web_log_payload

        payload = {
            "entries": [
                {
                    "msg": "Task build output with secret token=abc123",
                    "raw": "Task build output with secret token=abc123",
                }
            ],
            "tail": "cycle summary token=abc123",
            "files": {
                "cycle_summary": "D:/runs/latest/cycle_summary.log",
                "run_log": "D:/runs/latest/logs/run.log",
                "metrics": "D:/runs/latest/metrics.jsonl",
            },
            "source": {
                "path": "D:/runs/latest/metrics.jsonl",
                "name": "metrics.jsonl",
                "exists": True,
            },
        }

        redacted = _redact_web_log_payload(payload)
        self.assertEqual("[redacted]", redacted["entries"][0]["msg"])
        self.assertEqual("[redacted]", redacted["entries"][0]["raw"])
        self.assertEqual("[redacted]", redacted["tail"])
        self.assertEqual("[redacted]", redacted["files"]["cycle_summary"])
        self.assertEqual("[redacted]", redacted["files"]["run_log"])
        self.assertEqual("[redacted]", redacted["files"]["metrics"])
        self.assertEqual("[redacted]", redacted["source"]["path"])
        self.assertEqual("[redacted]", redacted["source"]["name"])
        self.assertIn("files.cycle_summary", redacted["redaction"]["fields"])


class WebConsoleReadonlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            import fastapi  # noqa: F401
        except Exception as exc:
            raise unittest.SkipTest(f"FastAPI is unavailable: {exc}") from exc

    def _create_app(self, repo: Path, *, config_path: Path | None = None):
        from agent_runner.web import create_app

        kwargs = {"web_dir": WEB_CONSOLE}
        if config_path is not None:
            kwargs["config_path"] = str(config_path)
        elif repo == self.repo:
            kwargs["config_path"] = str(self.config_path)
        return create_app(repo, **kwargs)

    def _write_worktree_artifact(self, relative: str, text: str) -> Path:
        path = self.run_dir / relative
        _write(path, text)
        return path

    def _clear_worktree_artifacts(self) -> None:
        for relative in [
            "WORKTREE_MERGE_PENDING.json",
            "WORKTREE_MERGE_PENDING.md",
            "WORKTREE_MERGE_APPLIED.json",
            "WORKTREE_MERGE_DISCARDED.json",
            "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
            "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json",
            "WORKTREE_APPLY_FAILURE.md",
            "WORKTREE_PATCH_NOT_APPLIED.md",
            "WORKTREE_NOT_APPLIED.md",
            "worktree.patch",
        ]:
            path = self.run_dir / relative
            if path.exists():
                path.unlink()
        central_dir = self.repo / ".AgentCLI"
        for relative in [
            "WORKTREE_MERGE_PENDING.json",
            "WORKTREE_MERGE_PENDING.md",
        ]:
            path = central_dir / relative
            if path.exists():
                path.unlink()

    def setUp(self) -> None:
        self._tmp_root = ROOT / ".test-scratch"
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        self._tmp = self._tmp_root / f"{self._testMethodName}_{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.empty_repo = self._tmp / "empty-repo"
        self.empty_repo.mkdir(parents=True, exist_ok=True)

        self.home = self._tmp / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self._old_home = os.environ.get("AGENTCLI_HOME")
        os.environ["AGENTCLI_HOME"] = str(self.home)
        self.addCleanup(self._restore_home)
        self.config_path = self.home / "configs" / "agentcli.json"
        self.prompts_dir = self.home / "prompts" / "agentcli"

        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260426-120000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "logs").mkdir(parents=True, exist_ok=True)
        self.goals_path = self.repo / ".doc" / "GOALS.md"

        _write(
            self.goals_path,
            """# Project Goals

## P0
- [x] Expose read-only progress views
- [ ] Add FastAPI web console

## P1
- [ ] Polish status panels
""",
        )

        backlog = {
            "generated_at": "2026-04-26T12:00:00",
            "tasks": [
                {
                    "id": "T1",
                    "title": "Expose read-only progress views",
                    "prompt": "Implement the read-only web status views.",
                    "files": ["agent_runner/web.py", "web_console/app.js", "web_console/styles.css"],
                    "done_when": "Endpoint returns current run progress and lifecycle records.",
                    "skills": ["observability"],
                    "skills_rationale": "Surface the lifecycle contract in the browser.",
                    "depends_on": [],
                },
                {
                    "id": "T2",
                    "title": "Add FastAPI web console",
                    "prompt": "Serve the production web console.",
                    "files": ["web_console/app.js", "web_console/styles.css"],
                    "done_when": "Static assets and JSON endpoints respond.",
                    "skills": ["ui"],
                    "skills_rationale": "Keep the backlog panel readable.",
                    "depends_on": ["T1"],
                },
            ],
        }
        _write(self.run_dir / "BACKLOG.json", json.dumps(backlog, ensure_ascii=False, indent=2) + "\n")
        _write(self.run_dir / "STATE.json", json.dumps({"done": ["T1"], "failed": [], "warnings": []}, ensure_ascii=False, indent=2) + "\n")
        _write(self.run_dir / "pm_final_output_cycle_001.txt", "PM planning complete for cycle 1.\n")
        _write(self.run_dir / "dev_output.txt", "Implemented read-only lifecycle adapters from run artifacts.\n")
        _write(self.run_dir / "qa_final_output_cycle_001.txt", "QA verification passed for the read-only console.\n")
        _write(
            self.run_dir / "metrics.jsonl",
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:00",
                            "seq": 1,
                            "level": "info",
                            "event": "pm_start",
                            "stage": "PM",
                            "cycle": 1,
                            "message": "cycle start",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:30",
                            "seq": 2,
                            "level": "info",
                            "event": "pm_end",
                            "stage": "PM",
                            "cycle": 1,
                            "rc": 0,
                            "reason": "pm_ready",
                            "message": "pm stage end",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:45",
                            "seq": 3,
                            "level": "info",
                            "event": "task_start",
                            "stage": "Dev",
                            "cycle": 1,
                            "step": 1,
                            "task_id": "T1",
                            "task_title": "Expose read-only progress views",
                            "message": "task start",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:46",
                            "seq": 4,
                            "level": "info",
                            "event": "dev_attempt_start",
                            "stage": "Dev",
                            "cycle": 1,
                            "step": 1,
                            "task_id": "T1",
                            "task_title": "Expose read-only progress views",
                            "attempt": 1,
                            "model": "gpt-5.4-mini",
                            "message": "dev attempt 1 start",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:01:10",
                            "seq": 5,
                            "level": "warn",
                            "event": "dev_attempt_retry",
                            "stage": "Dev",
                            "cycle": 1,
                            "step": 1,
                            "task_id": "T1",
                            "task_title": "Expose read-only progress views",
                            "attempt": 1,
                            "reason": "build_failed",
                            "message": "retry after build failure",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:01:11",
                            "seq": 6,
                            "level": "info",
                            "event": "dev_attempt_start",
                            "stage": "Dev",
                            "cycle": 1,
                            "step": 1,
                            "task_id": "T1",
                            "task_title": "Expose read-only progress views",
                            "attempt": 2,
                            "model": "gpt-5.4-mini",
                            "message": "dev attempt 2 start",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:01:21",
                            "seq": 7,
                            "level": "info",
                            "event": "task_end",
                            "stage": "Dev",
                            "cycle": 1,
                            "step": 1,
                            "task_id": "T1",
                            "task_title": "Expose read-only progress views",
                            "attempt": 2,
                            "rc": 0,
                            "reason": "completed",
                            "message": "task end",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:01:30",
                            "seq": 8,
                            "level": "info",
                            "event": "qa_start",
                            "stage": "QA",
                            "cycle": 1,
                            "task_id": "T1",
                            "task_title": "Expose read-only progress views",
                            "message": "qa start",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:01:50",
                            "seq": 9,
                            "level": "info",
                            "event": "qa_end",
                            "stage": "QA",
                            "cycle": 1,
                            "rc": 0,
                            "reason": "qa_verified",
                            "message": "qa end",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:02:00",
                            "seq": 10,
                            "level": "info",
                            "event": "cycle_end",
                            "stage": "Dev",
                            "cycle": 1,
                            "message": "cycle end",
                            "done": 1,
                            "total": 2,
                            "rc": 0,
                            "tokens": {"_total": {"input": 11, "output": 22, "total": 33}},
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
                    "profile": "personal",
                    "cycles": [
                        {
                            "cycle": 1,
                            "stages": [
                                {
                                    "name": "PM",
                                    "status": "ok",
                                    "rc": 0,
                                    "reason": "pm_ready",
                                    "cycle": 1,
                                    "startedAt": "2026-04-26T12:00:00",
                                    "endedAt": "2026-04-26T12:00:30",
                                    "durationSec": 30,
                                    "model": "gpt-5.5",
                                    "taskId": "pm_bootstrap",
                                    "taskTitle": "Backlog planning",
                                    "attempt": 1,
                                    "recentOutput": "PM planning complete for cycle 1.",
                                },
                                {
                                    "name": "Dev",
                                    "status": "ok",
                                    "rc": 0,
                                    "reason": "completed",
                                    "cycle": 1,
                                    "startedAt": "2026-04-26T12:00:45",
                                    "endedAt": "2026-04-26T12:01:21",
                                    "durationSec": 36,
                                    "model": "gpt-5.4-mini",
                                    "taskId": "T1",
                                    "taskTitle": "Expose read-only progress views",
                                    "attempt": 2,
                                    "step": 1,
                                    "recentOutput": "Implemented read-only lifecycle adapters from run artifacts.",
                                },
                                {
                                    "name": "QA",
                                    "status": "ok",
                                    "rc": 0,
                                    "reason": "qa_verified",
                                    "cycle": 1,
                                    "startedAt": "2026-04-26T12:01:30",
                                    "endedAt": "2026-04-26T12:01:50",
                                    "durationSec": 20,
                                    "model": "gpt-5.4-mini",
                                    "taskId": "T1",
                                    "taskTitle": "Expose read-only progress views",
                                    "attempt": 2,
                                    "recentOutput": "QA verification passed for the read-only console.",
                                },
                            ],
                        }
                    ],
                    "final": {"rc": 0, "reason": "project_complete"},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            self.run_dir / "last_run_summary.json",
            json.dumps(
                {
                    "ts": "2026-04-26T12:02:00",
                    "cycle": 1,
                    "run_dir": str(self.run_dir),
                    "done": 1,
                    "skipped": 0,
                    "total_tasks": 2,
                    "failed_count": 0,
                    "duration_seconds": 120,
                    "build_enabled": True,
                    "run_tests": True,
                    "policy_scan_enabled": False,
                    "status": "success",
                    "rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            self.run_dir / "cycle_summary.log",
            "2026-04-26T12:02:00 cycle=1 done=1/2 failed=0 dt=120.0s\n",
        )
        _write(
            self.run_dir / "logs" / "run.log",
            "2026-04-26 12:00:00 [INFO] cycle started\n2026-04-26 12:01:00 [INFO] cycle finished\n",
        )
        _write(
            self.run_dir / "WORKTREE_MERGE_PENDING.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "created_at": "2026-04-26T12:02:00",
                    "source_repo": str(self.repo),
                    "run_dir": str(self.run_dir),
                    "worktree_dir": str(self.repo / "worktree"),
                    "patch_path": str(self.run_dir / "worktree.patch"),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            self.run_dir / "worktree.patch",
            """diff --git a/agent_runner/web.py b/agent_runner/web.py
--- a/agent_runner/web.py
+++ b/agent_runner/web.py
@@ -1,1 +1,1 @@
-old
+new
""",
        )

        _write(
            self.config_path,
            json.dumps(
                {
                    "repo": self.repo.as_posix(),
                    "profile": "personal",
                    "execution_backend": "codex",
                    "prompts_dir": "prompts/agentcli",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            self.prompts_dir / "pm_instructions.md",
            textwrap.dedent(
                """\
                # Local PM Instructions

                Profile: {profile}
                Repo: {repo}

                Keep inventory previews redacted and use the explicit read path for the editor.
                """
            ),
        )

        from fastapi.testclient import TestClient

        self.app = self._create_app(self.repo)
        self.client = TestClient(self.app)

    def _make_live_run_dir(self, name: str) -> Path:
        run_dir = self.repo / ".AgentCLI" / "agent_runs" / name
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir

    def _controller_status(self, run_dir: Path, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "running": True,
            "runner_mode": "thread",
            "repo": str(self.repo),
            "config_path": self.config_path.as_posix(),
            "run_dir": str(run_dir),
            "uptime_seconds": 600,
            "exit_code": None,
            "stop_file": "STOP",
            "stop_file_exists": False,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "reason": "",
            "last_event": "",
            "startedAt": 1714132800000,
            "elapsedSec": 600,
            "stage": "Dev",
            "current_task_id": "T-020",
            "current_task_title": "API-backed observation path",
            "branch": "main",
            "attempt": 1,
            "worktree_mode": "manual",
        }
        payload.update(overrides)
        return payload

    def _api_status(self, repo: Path, controller_status: dict[str, object] | None) -> dict[str, object]:
        from agent_runner import web as web_module
        from fastapi.testclient import TestClient

        controller = FakeRunnerController(controller_status) if controller_status is not None else None
        with patch.object(web_module, "_build_runner_controller", return_value=controller):
            client = TestClient(self._create_app(repo))
        response = client.get("/api/status")
        self.assertEqual(200, response.status_code)
        return response.json()

    def _api_log_tail(self, **params: object) -> dict[str, object]:
        response = self.client.get("/api/logs/tail", params=params)
        self.assertEqual(200, response.status_code)
        return response.json()

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self._old_home

    def test_health_and_status_expose_read_only_snapshot(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(200, health.status_code)
        health_payload = health.json()
        self.assertTrue(health_payload["ok"])
        self.assertTrue(health_payload["latest_run_dir"].endswith("20260426-120000"))

        status = self.client.get("/api/status")
        self.assertEqual(200, status.status_code)
        payload = status.json()
        for key in ("active_run", "stages", "backlog", "goals", "logs", "config", "prompts", "history", "metrics", "notifications", "worktree", "progress"):
            self.assertIn(key, payload)
        self.assertEqual("20260426-120000", payload["active_run"]["id"])
        self.assertEqual(3, len(payload["stages"]))
        self.assertEqual(2, len(payload["backlog"]["items"]))
        self.assertEqual({"pending": 1, "in_progress": 0, "done": 1, "failed": 0}, payload["backlog"]["counts"])
        pm_stage, dev_stage, qa_stage = payload["stages"]
        self.assertEqual("PM", pm_stage["id"])
        self.assertEqual("done", pm_stage["status"])
        self.assertEqual("pm_bootstrap", pm_stage["taskId"])
        self.assertEqual(1, pm_stage["attempt"])
        self.assertEqual(30, pm_stage["durationSec"])
        self.assertLess(pm_stage["startedAt"], pm_stage["endedAt"])
        self.assertTrue(pm_stage["recentOutput"])
        self.assertEqual("Dev", dev_stage["id"])
        self.assertEqual("done", dev_stage["status"])
        self.assertEqual("T1", dev_stage["taskId"])
        self.assertEqual(2, dev_stage["attempt"])
        self.assertEqual(36, dev_stage["durationSec"])
        self.assertLess(dev_stage["startedAt"], dev_stage["endedAt"])
        self.assertTrue(dev_stage["recentOutput"])
        self.assertIsNotNone(dev_stage["endedAt"])
        self.assertEqual("QA", qa_stage["id"])
        self.assertEqual("done", qa_stage["status"])
        self.assertEqual("T1", qa_stage["taskId"])
        self.assertEqual(2, qa_stage["attempt"])
        self.assertEqual(20, qa_stage["durationSec"])
        self.assertLess(qa_stage["startedAt"], qa_stage["endedAt"])
        self.assertTrue(qa_stage["recentOutput"])
        primary_backlog, secondary_backlog = payload["backlog"]["items"]
        self.assertEqual("done", primary_backlog["status"])
        self.assertEqual("agent_runner/web.py, web_console/app.js, web_console/styles.css", primary_backlog["file_scope"])
        self.assertEqual(2, primary_backlog["attempt"])
        self.assertEqual([], primary_backlog["depends_on"])
        self.assertEqual("", primary_backlog["failure_reason"])
        self.assertTrue(primary_backlog["recent_output"])
        self.assertEqual(["T1"], secondary_backlog["depends_on"])
        self.assertEqual("pending", secondary_backlog["status"])
        self.assertEqual("web_console/app.js, web_console/styles.css", secondary_backlog["file_scope"])
        self.assertEqual(1, payload["progress"]["tasks_done"])
        self.assertEqual("completed", payload["progress"]["execution_status"])
        self.assertEqual("completed", payload["progress"]["run_status"])
        self.assertFalse(payload["progress"]["project_complete"])
        self.assertEqual("incomplete", payload["progress"]["project_status"])
        self.assertEqual("completed", payload["active_run"]["executionStatus"])
        self.assertEqual("completed", payload["active_run"]["status"])
        self.assertFalse(payload["active_run"]["projectComplete"])
        self.assertEqual("incomplete", payload["active_run"]["projectStatus"])
        self.assertIsNone(payload["progress"]["progress"])
        self.assertIsNone(payload["active_run"]["progress"])
        self.assertTrue(payload["active_run"]["tokensAvailable"])
        self.assertTrue(payload["active_run"]["tokens"]["available"])
        self.assertEqual({"in": 11, "out": 22, "available": True}, payload["active_run"]["tokens"])
        self.assertFalse(payload["active_run"]["budgetAvailable"])
        self.assertFalse(payload["active_run"]["quotaAvailable"])
        self.assertIsNone(payload["active_run"]["budgetUsed"])
        self.assertIsNone(payload["active_run"]["quota"]["used"])
        self.assertFalse(payload["active_run"]["quota"]["available"])
        self.assertFalse(payload["active_run"]["progressAvailable"])
        self.assertTrue(payload["goals"]["exists"])
        self.assertEqual(3, payload["goals"]["summary"]["total"])
        self.assertEqual(1, payload["goals"]["summary"]["done"])
        self.assertEqual(4, payload["goals"]["items"]["p0"][0]["line_number"])
        self.assertEqual(8, payload["goals"]["items"]["p1"][0]["line_number"])
        self.assertEqual([], payload["goals"]["warnings"])
        self.assertTrue(payload["metrics"]["tokens_available"])
        self.assertEqual({"in": 11, "out": 22}, payload["metrics"]["tokens"])
        self.assertFalse(payload["metrics"]["budget_available"])
        self.assertFalse(payload["metrics"]["quota_available"])
        self.assertIsNone(payload["metrics"]["budget_used"])
        self.assertIsNone(payload["metrics"]["quota_used"])

        history_response = self.client.get("/api/history")
        self.assertEqual(200, history_response.status_code)
        history_payload = history_response.json()
        self.assertIn("items", history_payload)
        self.assertIn("summary", history_payload)
        self.assertGreaterEqual(len(history_payload["items"]), 1)
        history_item = next(item for item in history_payload["items"] if item["id"] == self.run_dir.name)
        self.assertEqual("project_complete", history_item["finalReason"])
        self.assertEqual("project_complete", history_item["shutdownReason"])
        self.assertEqual("project_complete", history_item["stopReason"])
        self.assertEqual("completed", history_item["executionStatus"])
        self.assertEqual("completed", history_item["status"])
        self.assertFalse(history_item["projectComplete"])
        self.assertEqual("incomplete", history_item["projectStatus"])
        self.assertEqual(1, history_item["tasksDone"])
        self.assertEqual(2, history_item["tasksTotal"])
        self.assertEqual(0, history_item["tasksFailed"])
        self.assertEqual(0, history_item["tasksSkipped"])
        self.assertEqual({"done": 1, "failed": 0, "skipped": 0, "total": 2, "cycles": 1}, history_item["taskCounts"])
        self.assertEqual(120, history_item["durationSec"])
        self.assertEqual(payload["repo"]["branch"], history_item["branch"])
        self.assertEqual(self.run_dir.as_posix(), history_item["runDir"])
        self.assertEqual("pending", history_item["worktreeOutcome"])
        self.assertIn("runSummary", history_item)
        self.assertIn("lastRunSummary", history_item)

        self.assertGreaterEqual(len(payload["notifications"]), 1)
        kinds = {item["kind"] for item in payload["notifications"]}
        self.assertIn("run_start", kinds)
        self.assertIn("task_done", kinds)

    def test_empty_latest_timestamp_run_dir_does_not_mask_real_run(self) -> None:
        empty_run = self.repo / ".AgentCLI" / "agent_runs" / "20260426-130000"
        empty_run.mkdir(parents=True, exist_ok=True)

        from agent_runner.web import build_snapshot

        payload = build_snapshot(self.repo, runner_controller_auto_build=False)

        self.assertEqual("20260426-120000", Path(payload["latest_run_dir"]).name)
        self.assertEqual("20260426-120000", payload["active_run"]["id"])
        self.assertEqual("completed", payload["progress"]["execution_status"])
        self.assertEqual("completed", payload["progress"]["run_status"])
        self.assertFalse(payload["progress"]["project_complete"])
        self.assertEqual("incomplete", payload["progress"]["project_status"])

    def test_api_status_covers_no_run_and_live_running_controller_snapshot(self) -> None:
        no_run = self._api_status(self.empty_repo, None)
        self.assertEqual("no-run", no_run["active_run"]["id"])
        self.assertEqual("idle", no_run["progress"]["run_status"])
        self.assertEqual("idle", no_run["active_run"]["status"])
        self.assertIsNone(no_run["latest_run_dir"])
        self.assertEqual("", no_run["active_run"]["runDir"])
        self.assertIsNone(no_run["active_run"]["progress"])
        self.assertIsNone(no_run["progress"]["progress"])
        self.assertFalse(no_run["active_run"]["tokensAvailable"])
        self.assertFalse(no_run["active_run"]["budgetAvailable"])
        self.assertFalse(no_run["active_run"]["quotaAvailable"])
        self.assertFalse(no_run["active_run"]["progressAvailable"])
        self.assertEqual({"in": None, "out": None, "available": False}, no_run["active_run"]["tokens"])
        self.assertEqual({"window": "", "used": None, "available": False}, no_run["active_run"]["quota"])
        self.assertIsNone(no_run["active_run"]["budgetUsed"])
        self.assertEqual({"window": "", "used": None, "available": False}, no_run["metrics"]["quota"])
        self.assertFalse(no_run["metrics"]["quotaAvailable"])
        self.assertFalse(no_run["metrics"]["quota_available"])
        self.assertEqual("", no_run["metrics"]["quotaWindow"])
        self.assertEqual("", no_run["metrics"]["quota_window"])
        self.assertIsNone(no_run["metrics"]["quotaUsed"])
        self.assertIsNone(no_run["metrics"]["quota_used"])
        self.assertIn("live_state", no_run["runner_control"])
        self.assertFalse(no_run["runner_control"]["live_state"]["available"])
        self.assertEqual("unavailable", no_run["runner_control"]["live_state"]["runner_process"]["status"])
        self.assertEqual("Unavailable", no_run["runner_control"]["live_state"]["runner_process"]["statusLabel"])
        self.assertEqual("unavailable", no_run["runner_control"]["live_state"]["task_backend"]["status"])
        self.assertEqual("Unavailable", no_run["runner_control"]["live_state"]["task_backend"]["statusLabel"])
        self.assertEqual("unavailable", no_run["runner_control"]["live_state"]["tracked_children"]["status"])
        self.assertEqual("Unavailable", no_run["runner_control"]["live_state"]["tracked_children"]["statusLabel"])
        self.assertEqual("unavailable", no_run["runner_control"]["live_state"]["artifact_writer"]["status"])
        self.assertEqual("Unavailable", no_run["runner_control"]["live_state"]["artifact_writer"]["statusLabel"])
        self.assertFalse(no_run["liveRun"]["liveState"]["available"])
        self.assertEqual("unavailable", no_run["liveRun"]["liveState"]["runnerProcess"]["status"])
        self.assertEqual("unavailable", no_run["liveRun"]["liveState"]["taskBackend"]["status"])

        live_run_dir = self._make_live_run_dir("20260426-110000")
        later_run_dir = self._make_live_run_dir("20260426-140000")
        _write_run_bundle(later_run_dir, status="success", final_rc=0, final_reason="project_complete", branch="main")
        stop_progress = {
            "phase": "timeout",
            "message": "Runner is still alive after 1s stop wait timeout.",
            "elapsed_seconds": 12,
            "updated_at": "2026-04-28T00:00:12",
            "requested_at": "2026-04-28T00:00:00",
            "history": [
                {
                    "phase": "request",
                    "message": "Stop requested.",
                    "elapsed_seconds": 0,
                    "updated_at": "2026-04-28T00:00:00",
                },
                {
                    "phase": "runner_wait",
                    "message": "Waiting for runner shutdown and final artifacts.",
                    "elapsed_seconds": 8,
                    "updated_at": "2026-04-28T00:00:08",
                    "tracked_child_pids": [321, 654],
                    "tracked_child_processes": [
                        {
                            "pid": 321,
                            "alive": True,
                            "session_file": "C:/temp/session_321.json",
                            "session_exists": True,
                        },
                        {
                            "pid": 654,
                            "alive": False,
                            "session_file": "C:/temp/session_654.json",
                            "session_exists": False,
                        },
                    ],
                    "last_artifact_signal": {
                        "path": "C:/temp/run_summary.json",
                        "updated_at": "2026-04-28T00:00:10",
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
                {
                    "phase": "timeout",
                    "message": "Runner is still alive after 1s stop wait timeout.",
                    "elapsed_seconds": 12,
                    "updated_at": "2026-04-28T00:00:12",
                },
            ],
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
                    },
                    {
                        "pid": 654,
                        "alive": False,
                        "session_file": "C:/temp/session_654.json",
                        "session_exists": False,
                    },
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
            "runner_alive": True,
            "tracked_child_pids": [321, 654],
            "tracked_child_processes": [
                {
                    "pid": 321,
                    "alive": True,
                    "session_file": "C:/temp/session_321.json",
                    "session_exists": True,
                },
                {
                    "pid": 654,
                    "alive": False,
                    "session_file": "C:/temp/session_654.json",
                    "session_exists": False,
                },
            ],
            "last_artifact_signal": {
                "path": "C:/temp/run_summary.json",
                "updated_at": "2026-04-28T00:00:10",
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
        }
        live = self._api_status(
            self.repo,
            self._controller_status(
                live_run_dir,
                running=True,
                reason="",
                current_task_id="T-LIVE-1",
                current_task_title="Live running task",
                branch="feature/live-run",
                attempt=7,
                worktree_mode="isolation",
                stage="Dev",
                startedAt=1714136400000,
                elapsedSec=900,
                stop_progress=stop_progress,
            ),
        )
        self.assertEqual(live_run_dir.resolve().as_posix(), live["latest_run_dir"])
        self.assertIn("20260426-140000", {item["id"] for item in live["history"]["items"]})
        self.assertEqual("running", live["progress"]["run_status"])
        self.assertEqual("running", live["active_run"]["status"])
        self.assertEqual("feature/live-run", live["active_run"]["branch"])
        self.assertEqual(self.config_path.as_posix(), live["runner_control"]["status"]["config_path"])
        self.assertEqual(self.config_path.as_posix(), live["runner_control"]["startOptions"]["values"]["config_path"])
        self.assertEqual(["one-shot", "continuous", "loop"], live["runner_control"]["startOptions"]["choices"]["run_mode"])
        self.assertEqual(["personal", "enterprise"], live["runner_control"]["startOptions"]["choices"]["profile"])
        self.assertEqual(["codex", "claudecode"], live["runner_control"]["startOptions"]["choices"]["execution_backend"])
        self.assertEqual("T-LIVE-1", live["active_run"]["task"])
        self.assertEqual("Live running task", live["active_run"]["taskTitle"])
        self.assertEqual(7, live["active_run"]["attempt"])
        self.assertEqual("isolation", live["active_run"]["worktreeMode"])
        self.assertEqual("", live["active_run"]["finalReason"])
        self.assertEqual("T-LIVE-1", live["progress"]["current_task_id"])
        self.assertEqual("Live running task", live["progress"]["current_task_title"])
        self.assertEqual("isolation", live["progress"]["worktree_mode"])
        self.assertEqual("", live["progress"]["final_reason"])
        self.assertIsNone(live["active_run"]["progress"])
        self.assertIsNone(live["progress"]["progress"])
        self.assertFalse(live["active_run"]["tokensAvailable"])
        self.assertFalse(live["active_run"]["budgetAvailable"])
        self.assertFalse(live["active_run"]["quotaAvailable"])
        self.assertFalse(live["active_run"]["progressAvailable"])
        self.assertEqual({"in": None, "out": None, "available": False}, live["active_run"]["tokens"])
        self.assertEqual({"window": "", "used": None, "available": False}, live["active_run"]["quota"])
        self.assertIsNone(live["active_run"]["budgetUsed"])
        self.assertEqual({"window": "", "used": None, "available": False}, live["metrics"]["quota"])
        self.assertFalse(live["metrics"]["quotaAvailable"])
        self.assertFalse(live["metrics"]["quota_available"])
        self.assertEqual("", live["metrics"]["quotaWindow"])
        self.assertEqual("", live["metrics"]["quota_window"])
        self.assertIsNone(live["metrics"]["quotaUsed"])
        self.assertIsNone(live["metrics"]["quota_used"])
        self.assertIn("live_state", live["runner_control"])
        self.assertTrue(live["runner_control"]["live_state"]["available"])
        self.assertEqual("alive", live["runner_control"]["live_state"]["runner_process"]["status"])
        self.assertEqual("Alive", live["runner_control"]["live_state"]["runner_process"]["statusLabel"])
        self.assertEqual("alive", live["runner_control"]["live_state"]["task_backend"]["status"])
        self.assertEqual("Alive", live["runner_control"]["live_state"]["task_backend"]["statusLabel"])
        self.assertEqual("alive", live["runner_control"]["live_state"]["tracked_children"]["status"])
        self.assertEqual("Alive", live["runner_control"]["live_state"]["tracked_children"]["statusLabel"])
        self.assertEqual(2, live["runner_control"]["live_state"]["tracked_children"]["count"])
        self.assertEqual(1, live["runner_control"]["live_state"]["tracked_children"]["aliveCount"])
        self.assertEqual("flushing", live["runner_control"]["live_state"]["artifact_writer"]["status"])
        self.assertEqual("Flushing", live["runner_control"]["live_state"]["artifact_writer"]["statusLabel"])
        self.assertTrue(live["runner_control"]["live_state"]["artifact_writer"]["flushing"])
        self.assertEqual("flushing", live["liveRun"]["liveState"]["artifactWriter"]["status"])
        self.assertTrue(live["liveRun"]["process"]["liveState"]["artifact_writer"]["flushing"])
        live_state_html = _run_adapter_harness(
            [
                {
                    "kind": "call",
                    "name": "normalizeLiveState",
                    "args": [live["runner_control"]["live_state"]],
                },
                {
                    "kind": "call",
                    "name": "runnerControlLiveStateChips",
                    "args": [live["runner_control"]["live_state"]],
                },
                {
                    "kind": "call",
                    "name": "runnerControlLiveStateChips",
                    "args": [no_run["runner_control"]["live_state"]],
                },
            ]
        )
        normalized_live_state = live_state_html[0]
        self.assertEqual("alive", normalized_live_state["runnerProcess"]["status"])
        self.assertEqual("alive", normalized_live_state["taskBackend"]["status"])
        self.assertEqual("alive", normalized_live_state["trackedChildren"]["status"])
        self.assertEqual(2, normalized_live_state["trackedChildren"]["count"])
        self.assertEqual(1, normalized_live_state["trackedChildren"]["aliveCount"])
        self.assertEqual("flushing", normalized_live_state["artifactWriter"]["status"])
        self.assertTrue(normalized_live_state["artifactWriter"]["flushing"])
        self.assertIn("Live states", live_state_html[1])
        self.assertIn("Runner process", live_state_html[1])
        self.assertIn("Task backend", live_state_html[1])
        self.assertIn("Tracked children", live_state_html[1])
        self.assertIn("Artifact writer", live_state_html[1])
        self.assertIn("Alive", live_state_html[1])
        self.assertIn("Flushing", live_state_html[1])
        self.assertIn("unavailable", live_state_html[2])

    def test_api_status_live_state_contract_keeps_runner_backend_children_and_artifact_states_separate(self) -> None:
        from agent_runner.web import _build_live_state_payload

        base_controller_status = {
            "runner_mode": "thread",
            "repo": self.repo.as_posix(),
            "config_path": self.config_path.as_posix(),
            "run_dir": self.run_dir.as_posix(),
            "stage": "Dev",
            "branch": "main",
        }

        no_run = _build_live_state_payload(None, controller_available=False)
        backend_only = _build_live_state_payload(
            {**base_controller_status, "running": False},
            progress={"run_status": "running"},
            active_run={"status": "running"},
            controller_available=True,
        )
        child_only = _build_live_state_payload(
            {
                **base_controller_status,
                "running": False,
                "stop_progress": {
                    "phase": "runner_wait",
                    "tracked_child_pids": [321],
                    "tracked_child_processes": [
                        {
                            "pid": 321,
                            "alive": True,
                            "session_file": "C:/temp/session_321.json",
                            "session_exists": True,
                        }
                    ],
                },
            },
            progress={"run_status": "idle"},
            active_run={"status": "idle"},
            controller_available=True,
        )
        artifact_flushing = _build_live_state_payload(
            {
                **base_controller_status,
                "running": True,
                "stop_progress": {
                    "phase": "final_artifact_collection",
                    "last_artifact_signal": {
                        "path": "C:/temp/run_summary.json",
                        "updated_at": "2026-04-28T00:00:10",
                    },
                },
            },
            progress={"run_status": "running"},
            active_run={"status": "running"},
            controller_available=True,
        )

        self.assertEqual("unavailable", no_run["runnerProcess"]["status"])
        self.assertEqual("unavailable", no_run["taskBackend"]["status"])
        self.assertEqual("unavailable", no_run["trackedChildren"]["status"])
        self.assertEqual("unavailable", no_run["artifactWriter"]["status"])

        self.assertEqual("stopped", backend_only["runnerProcess"]["status"])
        self.assertEqual("alive", backend_only["taskBackend"]["status"])
        self.assertEqual("unavailable", backend_only["trackedChildren"]["status"])
        self.assertEqual("unavailable", backend_only["artifactWriter"]["status"])

        self.assertEqual("stopped", child_only["runnerProcess"]["status"])
        self.assertEqual("idle", child_only["taskBackend"]["status"])
        self.assertEqual("alive", child_only["trackedChildren"]["status"])
        self.assertEqual("idle", child_only["artifactWriter"]["status"])

        self.assertEqual("alive", artifact_flushing["runnerProcess"]["status"])
        self.assertEqual("alive", artifact_flushing["taskBackend"]["status"])
        self.assertEqual("stopped", artifact_flushing["trackedChildren"]["status"])
        self.assertEqual("flushing", artifact_flushing["artifactWriter"]["status"])

        raw_legacy_live_state = {
            "available": True,
            "source": "api",
            "runnerProcess": {
                "kind": "runnerProcess",
                "available": True,
                "alive": True,
            },
            "taskBackend": {
                "kind": "taskBackend",
                "available": True,
                "alive": True,
            },
            "trackedChildren": {
                "kind": "trackedChildren",
                "available": True,
                "alive": True,
            },
            "artifactWriter": {
                "kind": "artifactWriter",
                "available": True,
                "flushing": True,
            },
            "items": [
                {
                    "kind": "runnerProcess",
                    "available": True,
                    "alive": True,
                },
                {
                    "kind": "taskBackend",
                    "available": True,
                    "alive": True,
                },
                {
                    "kind": "trackedChildren",
                    "available": True,
                    "alive": True,
                },
                {
                    "kind": "artifactWriter",
                    "available": True,
                    "flushing": True,
                },
            ],
        }

        def make_control(live_state: dict[str, object], *, running: bool, run_status: str, controller_available: bool = True) -> dict[str, object]:
            return {
                "enabled": True,
                "source": "api",
                "controllerAvailable": controller_available,
                "busy": False,
                "message": "Runner snapshot.",
                "lastAction": "",
                "lastMessage": "",
                "lastError": "",
                "runStatus": run_status,
                "status": {
                    "running": running,
                    "runnerMode": "thread",
                    "repo": self.repo.as_posix(),
                    "configPath": self.config_path.as_posix(),
                    "runDir": self.run_dir.as_posix(),
                    "stopProgress": {},
                    "reason": "",
                    "lastEvent": "2026-04-26T12:08:00 cycle_end",
                },
                "liveState": live_state,
            }

        results = _run_adapter_harness(
            [
                {"kind": "call", "name": "normalizeLiveState", "args": [raw_legacy_live_state]},
                {"kind": "call", "name": "runnerControlDetailRows", "args": [make_control(no_run, running=False, run_status="idle", controller_available=False), {"chipTone": "paused", "label": "Unavailable"}]},
                {"kind": "call", "name": "runnerControlDetailRows", "args": [make_control(backend_only, running=False, run_status="running"), {"chipTone": "running", "label": "Running"}]},
                {"kind": "call", "name": "runnerControlDetailRows", "args": [make_control(child_only, running=False, run_status="idle"), {"chipTone": "idle", "label": "Idle"}]},
                {"kind": "call", "name": "runnerControlDetailRows", "args": [make_control(artifact_flushing, running=True, run_status="running"), {"chipTone": "loading", "label": "Flushing"}]},
            ]
        )

        normalized_legacy = results[0]
        self.assertEqual("unavailable", normalized_legacy["runnerProcess"]["status"])
        self.assertEqual("unavailable", normalized_legacy["runnerProcess"]["statusLabel"])
        self.assertEqual("unavailable", normalized_legacy["taskBackend"]["status"])
        self.assertEqual("unavailable", normalized_legacy["taskBackend"]["statusLabel"])
        self.assertEqual("unavailable", normalized_legacy["trackedChildren"]["status"])
        self.assertEqual("unavailable", normalized_legacy["trackedChildren"]["statusLabel"])
        self.assertEqual("unavailable", normalized_legacy["artifactWriter"]["status"])
        self.assertEqual("unavailable", normalized_legacy["artifactWriter"]["statusLabel"])

        no_run_rows = {row["label"]: row["value"] for row in results[1]}
        self.assertEqual("unavailable", no_run_rows["Runner process"])
        self.assertEqual("unavailable", no_run_rows["Task backend"])
        self.assertEqual("unavailable", no_run_rows["Tracked children"])
        self.assertEqual("unavailable", no_run_rows["Artifact writer"])

        backend_rows = {row["label"]: row["value"] for row in results[2]}
        self.assertEqual("Stopped", backend_rows["Runner process"])
        self.assertEqual("Alive", backend_rows["Task backend"])
        self.assertEqual("unavailable", backend_rows["Tracked children"])
        self.assertEqual("unavailable", backend_rows["Artifact writer"])

        child_rows = {row["label"]: row["value"] for row in results[3]}
        self.assertEqual("Stopped", child_rows["Runner process"])
        self.assertEqual("Idle", child_rows["Task backend"])
        self.assertEqual("Alive", child_rows["Tracked children"])
        self.assertEqual("Idle", child_rows["Artifact writer"])

        artifact_rows = {row["label"]: row["value"] for row in results[4]}
        self.assertEqual("Alive", artifact_rows["Runner process"])
        self.assertEqual("Alive", artifact_rows["Task backend"])
        self.assertEqual("Stopped", artifact_rows["Tracked children"])
        self.assertEqual("Flushing", artifact_rows["Artifact writer"])

    def test_api_status_prefers_active_run_quota_over_metrics_when_both_are_real(self) -> None:
        from agent_runner import web as web_module
        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        run_dir = self._make_live_run_dir("20260426-115500")
        _write_run_bundle(run_dir, status="success", final_rc=0, final_reason="project_complete", branch="main")

        controller = FakeRunnerController(
            self._controller_status(
                run_dir,
                running=False,
                reason="project_complete",
                exit_code=0,
                current_task_id="T-SUCC-2",
                current_task_title="Controller quota wins",
                branch="main",
                attempt=4,
                worktree_mode="manual",
                stage="QA",
                startedAt=1714137600000,
                elapsedSec=1800,
                progress=1.0,
                tokens={"in": 1024, "out": 256},
                quota={"window": "5h", "used": 0.41},
                budget_used=0.41,
            )
        )

        original_build_metrics = web_module._build_metrics_payload

        def fake_build_metrics_payload(*args: object, **kwargs: object) -> dict[str, object]:
            metrics = dict(original_build_metrics(*args, **kwargs))
            metrics["quota"] = {"window": "7d", "used": 0.33, "available": True}
            metrics["quota_available"] = True
            metrics["quotaAvailable"] = True
            metrics["quota_window"] = "7d"
            metrics["quotaWindow"] = "7d"
            metrics["quota_used"] = 0.33
            metrics["quotaUsed"] = 0.33
            return metrics

        with patch.object(web_module, "_build_runner_controller", return_value=controller), patch.object(
            web_module,
            "_build_metrics_payload",
            side_effect=fake_build_metrics_payload,
        ):
            client = TestClient(self._create_app(self.repo))
            payload = client.get("/api/status").json()

        expected_quota = {"window": "5h", "used": 0.41, "available": True}
        self.assertEqual(expected_quota, payload["active_run"]["quota"])
        self.assertEqual(expected_quota, payload["metrics"]["quota"])
        self.assertEqual("5h", payload["active_run"]["quotaWindow"])
        self.assertEqual("5h", payload["metrics"]["quotaWindow"])
        self.assertEqual(0.41, payload["active_run"]["quotaUsed"])
        self.assertEqual(0.41, payload["metrics"]["quotaUsed"])
        self.assertTrue(payload["active_run"]["quotaAvailable"])
        self.assertTrue(payload["metrics"]["quotaAvailable"])

    def test_api_status_normalizes_terminal_snapshots(self) -> None:
        success_run_dir = self._make_live_run_dir("20260426-111000")
        success = self._api_status(
            self.repo,
            self._controller_status(
                success_run_dir,
                running=False,
                reason="project_complete",
                exit_code=0,
                current_task_id="T-SUCC-1",
                current_task_title="Completed success task",
                branch="release/success",
                attempt=3,
                worktree_mode="manual",
                stage="QA",
                startedAt=1714137600000,
                elapsedSec=1800,
                progress=1.0,
                tokens={"in": 4096, "out": 1024},
                quota={"window": "7d", "used": 0.33},
                budget_used=0.33,
            ),
        )
        self.assertEqual(success_run_dir.resolve().as_posix(), success["latest_run_dir"])
        self.assertEqual("completed", success["progress"]["execution_status"])
        self.assertEqual("completed", success["progress"]["run_status"])
        self.assertFalse(success["progress"]["project_complete"])
        self.assertEqual("incomplete", success["progress"]["project_status"])
        self.assertEqual("completed", success["active_run"]["executionStatus"])
        self.assertEqual("completed", success["active_run"]["status"])
        self.assertFalse(success["active_run"]["projectComplete"])
        self.assertEqual("incomplete", success["active_run"]["projectStatus"])
        self.assertEqual("project_complete", success["progress"]["final_reason"])
        self.assertEqual("project_complete", success["active_run"]["finalReason"])
        self.assertTrue(success["active_run"]["runDir"].endswith("20260426-111000"))
        self.assertEqual("release/success", success["active_run"]["branch"])
        self.assertEqual("T-SUCC-1", success["active_run"]["task"])
        self.assertEqual("Completed success task", success["active_run"]["taskTitle"])
        self.assertEqual(3, success["active_run"]["attempt"])
        self.assertEqual(1.0, success["active_run"]["progress"])
        self.assertTrue(success["active_run"]["progressAvailable"])
        self.assertTrue(success["active_run"]["tokensAvailable"])
        self.assertTrue(success["active_run"]["budgetAvailable"])
        self.assertTrue(success["active_run"]["quotaAvailable"])
        self.assertEqual({"in": 4096, "out": 1024, "available": True}, success["active_run"]["tokens"])
        self.assertEqual({"window": "7d", "used": 0.33, "available": True}, success["active_run"]["quota"])
        self.assertEqual(0.33, success["active_run"]["budgetUsed"])
        self.assertEqual(0.33, success["active_run"]["quota"]["used"])
        self.assertTrue(success["metrics"]["quotaAvailable"])
        self.assertTrue(success["metrics"]["quota_available"])
        self.assertEqual({"window": "7d", "used": 0.33, "available": True}, success["metrics"]["quota"])
        self.assertEqual("7d", success["metrics"]["quotaWindow"])
        self.assertEqual("7d", success["metrics"]["quota_window"])
        self.assertEqual(0.33, success["metrics"]["quotaUsed"])
        self.assertEqual(0.33, success["metrics"]["quota_used"])

        ok_run_dir = self._make_live_run_dir("20260426-130000")
        _write_run_bundle(ok_run_dir, status="success", final_rc=0, final_reason="ok", branch="main")

        from agent_runner import web as web_module
        from fastapi.testclient import TestClient

        controller = FakeRunnerController(
            self._controller_status(
                ok_run_dir,
                running=False,
                reason="ok",
                exit_code=0,
                stage="QA",
                current_task_id="T1",
                current_task_title="Expose read-only progress views",
                branch="main",
                attempt=2,
                worktree_mode="manual",
                startedAt=1714132800000,
                elapsedSec=120,
                progress=1.0,
            )
        )
        with patch.object(web_module, "_build_runner_controller", return_value=controller):
            client = TestClient(self._create_app(self.repo))

        status_response = client.get("/api/status")
        self.assertEqual(200, status_response.status_code)
        status_payload = status_response.json()

        progress_response = client.get("/api/progress")
        self.assertEqual(200, progress_response.status_code)
        progress_payload = progress_response.json()

        runner_status_response = client.get("/api/runner/status")
        self.assertEqual(200, runner_status_response.status_code)
        runner_status_payload = runner_status_response.json()

        history_response = client.get("/api/history")
        self.assertEqual(200, history_response.status_code)
        history_payload = history_response.json()
        history_item = next(item for item in history_payload["items"] if item["id"] == ok_run_dir.name)

        self.assertEqual(2, status_payload["goals"]["summary"]["unchecked"])
        self.assertEqual({"pending": 1, "in_progress": 0, "done": 1, "failed": 0}, status_payload["backlog"]["counts"])
        self.assertEqual("completed", status_payload["progress"]["execution_status"])
        self.assertEqual("completed", status_payload["progress"]["run_status"])
        self.assertNotEqual("success", status_payload["progress"]["run_status"])
        self.assertFalse(status_payload["progress"]["project_complete"])
        self.assertEqual("incomplete", status_payload["progress"]["project_status"])
        self.assertEqual("completed", status_payload["active_run"]["executionStatus"])
        self.assertEqual("completed", status_payload["active_run"]["status"])
        self.assertNotEqual("success", status_payload["active_run"]["status"])
        self.assertFalse(status_payload["active_run"]["projectComplete"])
        self.assertEqual("incomplete", status_payload["active_run"]["projectStatus"])
        self.assertEqual("ok", status_payload["progress"]["final_reason"])
        self.assertEqual("ok", status_payload["active_run"]["finalReason"])

        self.assertEqual("completed", progress_payload["execution_status"])
        self.assertEqual("completed", progress_payload["run_status"])
        self.assertNotEqual("success", progress_payload["run_status"])
        self.assertFalse(progress_payload["project_complete"])
        self.assertEqual("incomplete", progress_payload["project_status"])
        self.assertEqual(status_payload["liveRun"]["identity"]["id"], progress_payload["liveRun"]["identity"]["id"])
        self.assertEqual(status_payload["liveRun"]["identity"]["id"], runner_status_payload["liveRun"]["identity"]["id"])
        self.assertEqual(status_payload["liveRun"]["status"]["runStatus"], progress_payload["liveRun"]["status"]["runStatus"])
        self.assertEqual(status_payload["liveRun"]["status"]["executionStatus"], progress_payload["liveRun"]["status"]["executionStatus"])
        self.assertEqual(status_payload["liveRun"]["currentTask"]["id"], progress_payload["liveRun"]["currentTask"]["id"])
        self.assertEqual(status_payload["liveRun"]["log"]["cursor"], progress_payload["liveRun"]["log"]["cursor"])
        self.assertEqual(status_payload["liveRun"]["runnerControl"]["status"]["running"], runner_status_payload["liveRun"]["runnerControl"]["status"]["running"])

        self.assertEqual("completed", runner_status_payload["executionStatus"])
        self.assertEqual("completed", runner_status_payload["run_status"])
        self.assertFalse(runner_status_payload["project_complete"])
        self.assertEqual("incomplete", runner_status_payload["projectStatus"])
        self.assertEqual("ok", runner_status_payload["status"]["reason"])

        self.assertEqual("ok", history_item["finalReason"])
        self.assertEqual("ok", history_item["shutdownReason"])
        self.assertEqual("completed", history_item["executionStatus"])
        self.assertEqual("completed", history_item["status"])
        self.assertNotEqual("success", history_item["status"])
        self.assertFalse(history_item["projectComplete"])
        self.assertEqual("incomplete", history_item["projectStatus"])

        stopped_run_dir = self._make_live_run_dir("20260426-112000")
        stopped = self._api_status(
            self.repo,
            self._controller_status(
                stopped_run_dir,
                running=False,
                reason="stop_file",
                exit_code=0,
                stop_file_exists=True,
                current_task_id="T-STOP-1",
                current_task_title="Stopped task",
                branch="main",
                attempt=5,
                worktree_mode="manual",
                stage="Dev",
                startedAt=1714138800000,
                elapsedSec=2100,
            ),
        )
        self.assertEqual(stopped_run_dir.resolve().as_posix(), stopped["latest_run_dir"])
        self.assertEqual("stopped", stopped["progress"]["run_status"])
        self.assertEqual("stopped", stopped["active_run"]["status"])
        self.assertEqual("stop_file", stopped["progress"]["final_reason"])
        self.assertEqual("stop_file", stopped["active_run"]["finalReason"])
        self.assertTrue(stopped["active_run"]["runDir"].endswith("20260426-112000"))
        self.assertEqual("T-STOP-1", stopped["active_run"]["task"])
        self.assertEqual("Stopped task", stopped["active_run"]["taskTitle"])
        self.assertIsNone(stopped["active_run"]["progress"])
        self.assertIsNone(stopped["progress"]["progress"])
        self.assertFalse(stopped["active_run"]["tokensAvailable"])
        self.assertFalse(stopped["active_run"]["budgetAvailable"])
        self.assertFalse(stopped["active_run"]["quotaAvailable"])
        self.assertFalse(stopped["active_run"]["progressAvailable"])
        self.assertEqual({"in": None, "out": None, "available": False}, stopped["active_run"]["tokens"])
        self.assertEqual({"window": "", "used": None, "available": False}, stopped["active_run"]["quota"])
        self.assertIsNone(stopped["active_run"]["budgetUsed"])
        self.assertEqual({"window": "", "used": None, "available": False}, stopped["metrics"]["quota"])
        self.assertFalse(stopped["metrics"]["quotaAvailable"])
        self.assertFalse(stopped["metrics"]["quota_available"])

        failed_run_dir = self._make_live_run_dir("20260426-113000")
        _write_run_bundle(failed_run_dir, status="failed", final_rc=3, final_reason="build_failed", branch="main")
        failed = self._api_status(
            self.repo,
            self._controller_status(
                failed_run_dir,
                running=False,
                reason="build_failed",
                exit_code=3,
                failed=1,
                current_task_id="T-FAIL-1",
                current_task_title="Failed task",
                branch="main",
                attempt=6,
                worktree_mode="manual",
                stage="Dev",
                startedAt=1714140000000,
                elapsedSec=2400,
            ),
        )
        self.assertEqual(failed_run_dir.resolve().as_posix(), failed["latest_run_dir"])
        self.assertEqual("failed", failed["progress"]["run_status"])
        self.assertEqual("failed", failed["active_run"]["status"])
        self.assertEqual("build_failed", failed["progress"]["final_reason"])
        self.assertEqual("build_failed", failed["active_run"]["finalReason"])
        self.assertTrue(failed["active_run"]["runDir"].endswith("20260426-113000"))
        self.assertEqual("T-FAIL-1", failed["active_run"]["task"])
        self.assertEqual("Failed task", failed["active_run"]["taskTitle"])
        self.assertIsNone(failed["active_run"]["progress"])
        self.assertIsNone(failed["progress"]["progress"])
        self.assertFalse(failed["active_run"]["tokensAvailable"])
        self.assertFalse(failed["active_run"]["budgetAvailable"])
        self.assertFalse(failed["active_run"]["quotaAvailable"])
        self.assertFalse(failed["active_run"]["progressAvailable"])
        self.assertEqual({"in": None, "out": None, "available": False}, failed["active_run"]["tokens"])
        self.assertEqual({"window": "", "used": None, "available": False}, failed["active_run"]["quota"])
        self.assertIsNone(failed["active_run"]["budgetUsed"])
        self.assertEqual({"window": "", "used": None, "available": False}, failed["metrics"]["quota"])
        self.assertFalse(failed["metrics"]["quotaAvailable"])
        self.assertFalse(failed["metrics"]["quota_available"])
        self.assertEqual(3, len(failed["stages"]))
        self.assertEqual("failed", failed["stages"][2]["status"])
        self.assertEqual("failed", failed["stages"][1]["status"])
        self.assertEqual("T-020", failed["backlog"]["items"][0]["id"])
        self.assertEqual("failed", failed["backlog"]["items"][0]["status"])
        self.assertEqual("build_failed", failed["backlog"]["items"][0]["failure_reason"])
        self.assertEqual("agent_runner/web.py, web_console/app.js, tests/test_web_console_readonly.py", failed["backlog"]["items"][0]["file_scope"])
        self.assertEqual(["T-020"], failed["backlog"]["items"][1]["depends_on"])

    def test_stale_state_ids_are_filtered_to_current_backlog_generation(self) -> None:
        stale_repo = self._tmp / "stale-repo"
        stale_repo.mkdir(parents=True, exist_ok=True)
        stale_config_path = stale_repo / "config" / "agentcli.json"
        _write_config(stale_config_path, stale_repo)

        run_dir = stale_repo / ".AgentCLI" / "agent_runs" / "20260426-150000"
        _write_run_bundle(
            run_dir,
            status="failed",
            final_rc=3,
            final_reason="build_failed",
            branch="main",
            backlog_tasks=[
                {
                    "id": "T-020",
                    "title": "Current backlog task",
                    "prompt": "Read-only web console snapshot.",
                    "files": ["agent_runner/web.py", "web_console/app.js"],
                    "done_when": "Snapshot is stable.",
                    "skills": ["observability"],
                    "skills_rationale": "Surface the lifecycle contract in the browser.",
                    "depends_on": [],
                    "status": "failed",
                }
            ],
            state_payload={
                "done": ["T-OLD-DONE"],
                "failed": [
                    {
                        "task": "T-020",
                        "reason": "build_failed",
                        "detail": "Current backlog task failed during the build step.",
                        "attempt": 2,
                        "cycle": 1,
                        "step": 0,
                        "rc": 3,
                    },
                    {
                        "task": "T-OLD-FAIL",
                        "reason": "stale_failure",
                        "detail": "Stale failure from a previous backlog generation.",
                        "attempt": 1,
                        "cycle": 0,
                        "step": 0,
                        "rc": 1,
                    },
                ],
                "warnings": [
                    {
                        "task": "T-OLD-WARN",
                        "reason": "stale_warning",
                        "detail": "Stale warning from a previous backlog generation.",
                        "attempt": 1,
                        "cycle": 0,
                        "step": 0,
                    }
                ],
            },
        )

        from agent_runner import web as web_module
        from agent_runner.remote.controller import RunnerController
        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        controller = RunnerController(
            repo=stale_repo,
            base_args=argparse.Namespace(config_path=stale_config_path.as_posix(), run_dir=run_dir.as_posix()),
        )
        controller_status = controller.status()
        self.assertEqual({"done": 0, "failed": 1, "warnings": 0}, controller_status["state_counts"])
        self.assertEqual(0, controller_status["done"])
        self.assertEqual(1, controller_status["failed"])
        self.assertEqual(0, controller_status["warnings"])

        with patch.object(web_module, "_build_runner_controller", return_value=FakeRunnerController(controller_status)):
            client = TestClient(create_app(stale_repo, web_dir=WEB_CONSOLE, config_path=stale_config_path.as_posix()))
            payload = client.get("/api/status").json()

        self.assertEqual({"done": 0, "failed": 1, "warnings": 0}, payload["runner_control"]["status"]["state_counts"])
        self.assertEqual(0, payload["runner_control"]["status"]["done"])
        self.assertEqual(1, payload["runner_control"]["status"]["failed"])
        self.assertEqual(0, payload["runner_control"]["status"]["warnings"])
        self.assertEqual(1, payload["progress"]["tasks_total"])
        self.assertEqual(0, payload["progress"]["tasks_done"])
        self.assertEqual(1, payload["progress"]["tasks_failed"])
        self.assertEqual({"done": 0, "failed": 1, "warnings": 0}, payload["progress"]["state_counts"])
        self.assertEqual(1, payload["history"]["items"][0]["taskCounts"]["total"])
        self.assertEqual(0, payload["history"]["items"][0]["taskCounts"]["done"])
        self.assertEqual(1, payload["history"]["items"][0]["taskCounts"]["failed"])
        self.assertEqual({"done": 0, "failed": 1, "warnings": 0}, payload["history"]["items"][0]["state_counts"])
        self.assertEqual(1, payload["history"]["summary"]["tasksTotal"])
        self.assertEqual(0, payload["history"]["summary"]["tasksDone"])
        self.assertEqual(1, payload["history"]["summary"]["tasksFailed"])

        browser_payload = json.loads(json.dumps(payload))
        browser_payload["history"]["items"][0]["tasksDone"] = 19
        browser_payload["history"]["items"][0]["tasksFailed"] = 21
        browser_payload["history"]["items"][0]["taskCounts"]["done"] = 19
        browser_payload["history"]["items"][0]["taskCounts"]["failed"] = 21
        browser_payload["history"]["summary"]["tasksDone"] = 19
        browser_payload["history"]["summary"]["tasksFailed"] = 21
        adapted = _run_adapter_harness([{"kind": "snapshot", "data": browser_payload}])[0]

        adapted_history_item = adapted["history"][0]
        self.assertEqual(0, adapted_history_item["tasksDone"])
        self.assertEqual(1, adapted_history_item["tasksFailed"])
        self.assertEqual({"done": 0, "failed": 1, "warnings": 0}, adapted_history_item["stateCounts"])
        self.assertEqual(1, adapted_history_item["tasksTotal"])
        self.assertEqual(1, adapted_history_item["taskCounts"]["total"])
        self.assertEqual(0, adapted["historySummary"]["tasksDone"])
        self.assertEqual(1, adapted["historySummary"]["tasksFailed"])
        self.assertEqual(1, adapted["historySummary"]["tasksTotal"])
        self.assertEqual(0, adapted["runnerControl"]["status"]["done"])
        self.assertEqual(1, adapted["runnerControl"]["status"]["failed"])
        self.assertEqual(0, adapted["runnerControl"]["status"]["warnings"])

    def test_api_status_surfaces_runner_control_controller_errors(self) -> None:
        from agent_runner import web as web_module
        from fastapi.testclient import TestClient

        controller = FakeRunnerController(
            self._controller_status(
                self.run_dir,
                running=False,
                reason="",
                exit_code=0,
                current_task_id="T-ERROR-1",
                current_task_title="Broken controller task",
                branch="main",
                attempt=1,
                worktree_mode="manual",
                stage="Dev",
                startedAt=1714137600000,
                elapsedSec=120,
            ),
            status_error="controller unavailable",
        )

        with patch.object(web_module, "_build_runner_controller", return_value=controller):
            client = TestClient(self._create_app(self.repo))

        payload = client.get("/api/status").json()
        self.assertEqual("error", payload["sectionState"]["runnerControl"]["state"])
        self.assertEqual("status_error: controller unavailable", payload["runner_control"]["message"])
        self.assertFalse(payload["runner_control"]["actions"]["start"]["enabled"])
        self.assertFalse(payload["runner_control"]["actions"]["restart"]["enabled"])
        self.assertEqual("status_error: controller unavailable", payload["runner_control"]["status"]["reason"])
        self.assertEqual(self.config_path.as_posix(), payload["runner_control"]["status"]["config_path"])

    def test_api_status_hydrates_runner_control_from_artifact_when_controller_status_fails(self) -> None:
        from agent_runner.remote.controller import write_runner_control_event
        from agent_runner import web as web_module
        from fastapi.testclient import TestClient

        write_runner_control_event(
            self.run_dir,
            action="restart",
            status="controller_error",
            message="",
            error="Runner control failed: controller offline",
            ok=False,
            source="controller",
            repo=self.repo.as_posix(),
            config_path=self.config_path.as_posix(),
            running=False,
            runner_mode="thread",
        )

        controller = FakeRunnerController(
            self._controller_status(
                self.run_dir,
                running=False,
                reason="",
                exit_code=0,
                current_task_id="T-ERROR-2",
                current_task_title="Disconnected controller",
                branch="main",
                attempt=1,
                worktree_mode="manual",
                stage="Dev",
                startedAt=1714137600000,
                elapsedSec=120,
            ),
            status_error="controller offline",
        )

        with patch.object(web_module, "_build_runner_controller", return_value=controller):
            client = TestClient(self._create_app(self.repo))

        payload = client.get("/api/status").json()
        self.assertEqual("restart", payload["runner_control"]["status"]["last_action"])
        self.assertEqual("", payload["runner_control"]["status"]["last_message"])
        self.assertEqual("Runner control failed: controller offline", payload["runner_control"]["status"]["last_error"])
        self.assertEqual("Runner control failed: controller offline", payload["runner_control"]["message"])
        self.assertEqual("status_error: controller offline", payload["runner_control"]["status"]["reason"])
        self.assertEqual(self.config_path.as_posix(), payload["runner_control"]["status"]["config_path"])

    def test_section_endpoints_return_stable_shapes(self) -> None:
        progress = self.client.get("/api/progress").json()
        for key in ("active_run", "stages", "backlog", "goals", "logs", "config", "prompts", "history", "metrics", "notifications", "worktree", "state"):
            self.assertIn(key, progress)
        self.assertEqual(1, progress["tasks_done"])
        self.assertEqual(2, progress["tasks_total"])
        self.assertEqual("completed", progress["execution_status"])
        self.assertEqual("completed", progress["run_status"])
        self.assertFalse(progress["project_complete"])
        self.assertEqual("incomplete", progress["project_status"])
        self.assertIn("goals", progress)

        logs = self.client.get("/api/logs").json()
        self.assertIn("entries", logs)
        self.assertGreaterEqual(len(logs["entries"]), 1)

        goals = self.client.get("/api/goals").json()
        for key in ("path", "exists", "mtime", "size", "raw_text", "items", "completion", "completion_level", "summary", "warnings"):
            self.assertIn(key, goals)
        self.assertEqual(3, goals["summary"]["total"])
        self.assertEqual(1, goals["summary"]["done"])
        self.assertEqual(4, goals["items"]["p0"][0]["line_number"])

    def test_adapter_normalizes_live_run_contract_from_api_snapshot(self) -> None:
        status_payload = self.client.get("/api/status").json()
        adapted = _run_adapter_harness(
            [
                {
                    "kind": "call",
                    "name": "adaptLiveRun",
                    "args": [
                        status_payload["liveRun"],
                        {
                            "repo": status_payload["repo"],
                            "progress": status_payload["progress"],
                            "metrics": status_payload["metrics"],
                            "config": status_payload["config"],
                            "branch": status_payload["repo"]["branch"],
                        },
                    ],
                }
            ]
        )[0]

        self.assertEqual(status_payload["liveRun"]["identity"]["id"], adapted["identity"]["id"])
        self.assertEqual(status_payload["liveRun"]["identity"]["runId"], adapted["identity"]["runId"])
        self.assertEqual(status_payload["liveRun"]["status"]["runStatus"], adapted["status"]["runStatus"])
        self.assertEqual(status_payload["liveRun"]["status"]["executionStatus"], adapted["status"]["executionStatus"])
        self.assertEqual(status_payload["liveRun"]["currentTask"]["id"], adapted["currentTask"]["id"])
        self.assertEqual(status_payload["liveRun"]["currentTask"]["title"], adapted["currentTask"]["title"])
        self.assertEqual(status_payload["liveRun"]["log"]["cursor"], adapted["log"]["cursor"])
        self.assertEqual(status_payload["liveRun"]["log"]["state"], adapted["log"]["state"])
        self.assertEqual(status_payload["liveRun"]["notifications"]["count"], adapted["notifications"]["count"])
        self.assertEqual(status_payload["liveRun"]["runnerControl"]["status"]["running"], adapted["runnerControl"]["status"]["running"])
        self.assertEqual(status_payload["liveRun"]["process"]["running"], adapted["process"]["running"])
        self.assertEqual(len(status_payload["liveRun"]["stageSummaries"]), len(adapted["stageSummaries"]))
        self.assertIn("liveState", status_payload["liveRun"])
        self.assertIn("liveState", status_payload["liveRun"]["process"])
        self.assertIn("liveState", adapted)
        self.assertIn("liveState", adapted["process"])
        self.assertEqual(status_payload["liveRun"]["liveState"]["runner_process"]["status"], adapted["liveState"]["runnerProcess"]["status"])
        self.assertEqual(status_payload["liveRun"]["process"]["liveState"]["task_backend"]["status"], adapted["process"]["liveState"]["taskBackend"]["status"])
        self.assertEqual(status_payload["liveRun"]["process"]["liveState"]["tracked_children"]["status"], adapted["process"]["liveState"]["trackedChildren"]["status"])

        config = self.client.get("/api/config").json()
        self.assertIn("values", config)
        self.assertIn("defaults", config)
        self.assertIn("schema", config)
        self.assertIn("groups", config)
        self.assertIn("redaction", config)
        self.assertIn("restart_required_paths", config)
        self.assertIn("meta", config)
        self.assertIn("resolved_prompts_dir", config)
        self.assertEqual(config["path"], config["meta"]["path"])
        self.assertEqual(config["source"], config["meta"]["source"])
        self.assertEqual(config["resolved_prompts_dir"], config["meta"]["resolved_prompts_dir"])
        self.assertFalse(config["meta"]["save_enabled"])
        self.assertEqual("/api/config/save", config["meta"]["save_endpoint"])
        self.assertTrue(config["meta"]["save_requires_opt_in"])
        self.assertEqual("Repository", config["schema"]["repo"]["label"])
        self.assertTrue(config["schema"]["repo"]["restart"])
        self.assertEqual(self.repo.as_posix(), config["values"]["repo"])
        self.assertIn("pm_model", config["values"])
        self.assertIn("pm_model", config["schema"])
        self.assertIn("prompts_dir", config["defaults"])
        group_titles = {group["title"] for group in config["groups"]}
        self.assertTrue({"Project", "Runner", "Quota", "Worktree", "Prompt Paths", "Codex Models", "PM Refresh", "Budget", "Telegram", "Goals"}.issubset(group_titles))
        self.assertTrue(config["schema"]["telegram.bot_token"]["redacted"])
        self.assertTrue(config["schema"]["prompts_dir"]["restart"])
        self.assertEqual("[redacted]", config["redaction"]["placeholder"])
        self.assertIn("telegram.bot_token", config["redaction"]["paths"])
        self.assertIn("telegram.pairing_code", config["redaction"]["paths"])
        self.assertIn("prompts_dir", config["restart_required_paths"])

        prompts = self.client.get("/api/prompts").json()
        self.assertIn("items", prompts)
        self.assertGreaterEqual(len(prompts["items"]), 3)

        history = self.client.get("/api/history").json()
        self.assertIn("items", history)
        self.assertGreaterEqual(len(history["items"]), 1)
        history_item = next(item for item in history["items"] if item["id"] == self.run_dir.name)
        self.assertEqual("completed", history_item["executionStatus"])
        self.assertEqual("completed", history_item["status"])
        self.assertFalse(history_item["projectComplete"])
        self.assertEqual("incomplete", history_item["projectStatus"])

        worktree = self.client.get("/api/worktree").json()
        self.assertEqual("pending review", worktree["status"])
        self.assertTrue(worktree["reviewRequired"])
        self.assertEqual(self.repo.resolve(), Path(worktree["sourceRepo"]).resolve())
        self.assertEqual("main", worktree["sourceBranch"])
        self.assertEqual("main", worktree["baseRef"])
        self.assertEqual("abc12345", worktree["headRef"])
        self.assertEqual(self.run_dir.resolve(), Path(worktree["runDir"]).resolve())
        self.assertEqual(self.run_dir / "WORKTREE_MERGE_PENDING.json", Path(worktree["statusFile"]))
        self.assertEqual(self.run_dir / "WORKTREE_MERGE_PENDING.json", Path(worktree["pendingFile"]))
        self.assertEqual(self.repo / "worktree", Path(worktree["cleanupPath"]))
        self.assertEqual("pending", worktree["cleanupState"])
        self.assertEqual("Cleanup has not run yet.", worktree["cleanupMessage"])
        self.assertIn("Review patch from main to abc12345", worktree["risk"])
        self.assertTrue(worktree["changedFiles"])
        self.assertEqual("agent_runner/web.py", worktree["changedFiles"][0]["path"])
        self.assertIn("merge-worktree", worktree["reviewRequiredMessage"])
        self.assertIn("discard-worktree", worktree["reviewRequiredMessage"])

    def test_api_worktree_normalizes_empty_malformed_applied_and_failed_states(self) -> None:
        patch_text = "\n".join(
            [
                "diff --git a/agent_runner/web.py b/agent_runner/web.py",
                "--- a/agent_runner/web.py",
                "+++ b/agent_runner/web.py",
                "@@ -1 +1 @@",
                "-old",
                "+new",
                "",
            ]
        )

        def write_status_artifact(name: str, payload: dict[str, object]) -> None:
            _write(self.run_dir / name, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

        def write_patch() -> Path:
            return self._write_worktree_artifact("worktree.patch", patch_text)

        with self.subTest("no-pending"):
            self._clear_worktree_artifacts()
            worktree = self.client.get("/api/worktree").json()
            self.assertEqual("none", worktree["status"])
            self.assertFalse(worktree["reviewRequired"])
            self.assertEqual("", worktree["statusFile"])
            self.assertEqual("none", worktree["cleanupState"])
            self.assertEqual("No pending worktree merge.", worktree["reviewRequiredMessage"])
            self.assertEqual([], worktree["changedFiles"])

        with self.subTest("malformed"):
            self._clear_worktree_artifacts()
            self._write_worktree_artifact("WORKTREE_MERGE_PENDING.json", "{ not-json }\n")
            worktree = self.client.get("/api/worktree").json()
            self.assertEqual("error", worktree["status"])
            self.assertTrue(worktree["reviewRequired"])
            self.assertEqual(self.run_dir / "WORKTREE_MERGE_PENDING.json", Path(worktree["statusFile"]))
            self.assertEqual("none", worktree["cleanupState"])
            self.assertIn("malformed", worktree["reviewRequiredMessage"].lower())
            self.assertEqual([], worktree["changedFiles"])

        for status_name, artifact_name, expected_cleanup_state in [
            ("applied", "WORKTREE_MERGE_APPLIED.json", "done"),
            ("discarded", "WORKTREE_MERGE_DISCARDED.json", "done"),
        ]:
            with self.subTest(status_name):
                self._clear_worktree_artifacts()
                write_patch()
                write_status_artifact(
                    artifact_name,
                    {
                        "schema_version": 1,
                        "status": status_name,
                        "created_at": "2026-04-26T12:03:00",
                        "source_repo": self.repo.as_posix(),
                        "run_dir": self.run_dir.as_posix(),
                        "worktree_dir": (self.repo / "worktree").as_posix(),
                        "patch_path": (self.run_dir / "worktree.patch").as_posix(),
                        "base_ref": "main",
                        "head_ref": "abc12345",
                        "last_rc": 0,
                    },
                )
                worktree = self.client.get("/api/worktree").json()
                self.assertEqual(status_name, worktree["status"])
                self.assertFalse(worktree["reviewRequired"])
                self.assertEqual(self.run_dir / artifact_name, Path(worktree["statusFile"]))
                self.assertEqual(expected_cleanup_state, worktree["cleanupState"])
                self.assertEqual(self.repo / "worktree", Path(worktree["cleanupPath"]))
                self.assertTrue(worktree["changedFiles"])
                self.assertEqual("agent_runner/web.py", worktree["changedFiles"][0]["path"])
                self.assertIn(status_name.split("_")[0], worktree["summary"].lower())

        for status_name, artifact_name in [
            ("applied_cleanup_failed", "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json"),
            ("discard_cleanup_failed", "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json"),
        ]:
            with self.subTest(status_name):
                self._clear_worktree_artifacts()
                write_patch()
                write_status_artifact(
                    artifact_name,
                    {
                        "schema_version": 1,
                        "status": status_name,
                        "created_at": "2026-04-26T12:04:00",
                        "source_repo": self.repo.as_posix(),
                        "run_dir": self.run_dir.as_posix(),
                        "worktree_dir": (self.repo / "worktree").as_posix(),
                        "patch_path": (self.run_dir / "worktree.patch").as_posix(),
                        "base_ref": "main",
                        "head_ref": "abc12345",
                        "last_rc": 0,
                        "cleanup_message": f"{status_name} cleanup failed",
                    },
                )
                worktree = self.client.get("/api/worktree").json()
                self.assertEqual(status_name, worktree["status"])
                self.assertTrue(worktree["reviewRequired"])
                self.assertEqual(self.run_dir / artifact_name, Path(worktree["statusFile"]))
                self.assertEqual("failed", worktree["cleanupState"])
                self.assertIn("cleanup failed", worktree["cleanupMessage"].lower())
                self.assertTrue(worktree["changedFiles"])

        with self.subTest("stale-central-marker"):
            self._clear_worktree_artifacts()
            _write(
                self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "pending",
                        "created_at": "2026-04-26T12:05:00",
                        "source_repo": self.repo.as_posix(),
                        "run_dir": self.run_dir.as_posix(),
                        "worktree_dir": (self.repo / "worktree").as_posix(),
                        "patch_path": (self.run_dir / "worktree.patch").as_posix(),
                        "base_ref": "main",
                        "head_ref": "abc12345",
                        "last_rc": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
            write_patch()
            worktree = self.client.get("/api/worktree").json()
            self.assertEqual("error", worktree["status"])
            self.assertTrue(worktree["reviewRequired"])
            self.assertIn("stale", worktree["reviewRequiredMessage"].lower())
            self.assertTrue(worktree["statusFile"].endswith("WORKTREE_MERGE_PENDING.json"))
            self.assertEqual([], worktree["changedFiles"])

    def test_prompt_inventory_is_redacted_and_profile_aware(self) -> None:
        prompts = self.client.get("/api/prompts").json()
        items = {item["id"]: item for item in prompts["items"]}

        self.assertIn("pm_instructions", items)
        self.assertIn("pm_bootstrap", items)

        override = items["pm_instructions"]
        self.assertEqual("personal", override["profile"])
        self.assertEqual("override", override["mode"])
        self.assertEqual("[redacted]", override["preview"])
        self.assertTrue(override["path"].endswith("pm_instructions.md"))
        self.assertTrue(override["source"].endswith("prompts/agentcli"))

        template = items["pm_bootstrap"]
        self.assertEqual("personal", template["profile"])
        self.assertEqual("template", template["mode"])
        self.assertEqual("[redacted]", template["preview"])
        self.assertTrue(template["path"].endswith("pm_bootstrap_prompt.md"))
        self.assertEqual("templates/agent_prompts", template["source"])

    def test_prompt_read_returns_full_content_for_override_and_template_prompts(self) -> None:
        override = self.client.get(
            "/api/prompts/read",
            params={"id": "pm_instructions", "file": "pm_instructions.md"},
        )
        self.assertEqual(200, override.status_code)
        override_payload = override.json()
        self.assertTrue(override_payload["ok"])
        self.assertEqual("pm_instructions", override_payload["id"])
        self.assertEqual("personal", override_payload["profile"])
        self.assertEqual("override", override_payload["mode"])
        self.assertTrue(override_payload["source"].endswith("prompts/agentcli"))
        self.assertEqual(
            textwrap.dedent(
                """\
                # Local PM Instructions

                Profile: {profile}
                Repo: {repo}

                Keep inventory previews redacted and use the explicit read path for the editor.
                """
            ),
            override_payload["content"],
        )
        self.assertEqual(["profile", "repo"], override_payload["template_variables"])

        template = self.client.get(
            "/api/prompts/read",
            params={"id": "pm_bootstrap", "file": "pm_bootstrap_prompt.md"},
        )
        self.assertEqual(200, template.status_code)
        template_payload = template.json()
        self.assertTrue(template_payload["ok"])
        self.assertEqual("pm_bootstrap", template_payload["id"])
        self.assertEqual("personal", template_payload["profile"])
        self.assertEqual("template", template_payload["mode"])
        self.assertEqual("templates/agent_prompts", template_payload["source"])
        self.assertIn("{analysis_md}", template_payload["content"])
        self.assertIn("{repo}", template_payload["content"])
        self.assertIn("PROJECT_ANALYSIS.md", template_payload["content"])
        self.assertIn("analysis_md", template_payload["template_variables"])

    def test_prompt_read_validates_request_shape_and_rejects_traversal(self) -> None:
        missing_id = self.client.get("/api/prompts/read", params={"file": "pm_instructions.md"})
        self.assertEqual(400, missing_id.status_code)
        self.assertEqual("prompt_id_required", missing_id.json()["error"]["code"])

        missing_file = self.client.get("/api/prompts/read", params={"id": "pm_instructions"})
        self.assertEqual(400, missing_file.status_code)
        self.assertEqual("prompt_file_required", missing_file.json()["error"]["code"])

        mismatch = self.client.get(
            "/api/prompts/read",
            params={"id": "pm_instructions", "file": "pm_bootstrap_prompt.md"},
        )
        self.assertEqual(400, mismatch.status_code)
        self.assertEqual("prompt_file_mismatch", mismatch.json()["error"]["code"])

        traversal = self.client.get(
            "/api/prompts/read",
            params={"id": "pm_instructions", "file": "../secrets.md"},
        )
        self.assertEqual(400, traversal.status_code)
        self.assertEqual("prompt_path_outside_prompts_dir", traversal.json()["error"]["code"])

    def test_prompt_editor_validation_panel_and_dirty_reset(self) -> None:
        backup_path = (self.home / "prompts" / "agentcli" / "pm_bootstrap_prompt.20260426-120000.bak.md").as_posix()
        prompt = {
            "id": "pm_bootstrap",
            "file": "pm_bootstrap_prompt.md",
            "path": (self.home / "prompts" / "agentcli" / "pm_bootstrap_prompt.md").as_posix(),
            "scope": "PM",
            "profile": "personal",
            "source": "templates/agent_prompts",
            "mode": "template",
            "updated": "template",
            "summary": "PM bootstrap prompt",
            "preview": "[redacted]",
        }
        backup = {
            "path": backup_path,
            "name": Path(backup_path).name,
            "updated": "2026-04-26 12:00",
            "size": 42,
            "summary": "2026-04-26 12:00 | 42 bytes",
        }
        invalid_content = "Valid prompt {repo}\n"
        valid_content = "Valid prompt {repo} {analysis_md}\n"
        invalid_payload = {
            **prompt,
            "content": invalid_content,
            "template_variables": ["repo"],
            "required_template_variables": ["repo", "analysis_md"],
            "backups": [backup],
        }
        valid_payload = {
            **prompt,
            "content": valid_content,
            "template_variables": ["repo", "analysis_md"],
            "required_template_variables": ["repo", "analysis_md"],
            "backups": [backup],
        }

        results = _run_adapter_harness(
            [
                {"kind": "call", "name": "applyPromptEditorPayload", "args": [prompt, invalid_payload]},
                {"kind": "call", "name": "promptEditorValidation", "args": []},
                {"kind": "call", "name": "renderPromptEditorValidation", "args": []},
                {"kind": "call", "name": "inspectPromptEditorState", "args": []},
                {
                    "kind": "call",
                    "name": "applyPromptEditorPayload",
                    "args": [prompt, valid_payload, {"backupSelection": backup_path, "restoreConfirmation": "RESTORE BACKUP"}],
                },
                {"kind": "call", "name": "renderPromptEditorMutationPanel", "args": []},
                {"kind": "call", "name": "inspectPromptEditorState", "args": []},
                {"kind": "call", "name": "updatePromptEditorDraft", "args": ["draftContent", valid_content + "Changed in browser.\n"]},
                {"kind": "call", "name": "inspectPromptEditorState", "args": []},
                {
                    "kind": "call",
                    "name": "applyPromptEditorPayload",
                    "args": [prompt, valid_payload, {"backupSelection": backup_path}],
                },
                {"kind": "call", "name": "inspectPromptEditorState", "args": []},
            ]
        )

        validation = results[1]
        self.assertIn("Missing template variables", validation["templateError"])
        self.assertIn("{analysis_md}", validation["templateError"])
        self.assertEqual(["repo", "analysis_md"], validation["requiredVariables"])

        validation_html = results[2]
        self.assertIn("Template-variable validation", validation_html)
        self.assertIn("field-error", validation_html)

        initial_state = results[3]
        self.assertFalse(initial_state["dirty"])
        self.assertEqual("pm_bootstrap", initial_state["promptId"])
        self.assertEqual([backup_path], [item["path"] for item in initial_state["backups"]])

        mutation_panel = results[5]
        self.assertIn("data-prompt-backup-select", mutation_panel)
        self.assertIn("data-prompt-restore-confirmation", mutation_panel)
        self.assertIn("Save Prompt", mutation_panel)
        self.assertIn("Restore Backup", mutation_panel)
        self.assertIn("Selected backup", mutation_panel)
        self.assertIn("Available backups", mutation_panel)
        self.assertIn("Prompt mutations are locked", mutation_panel)
        self.assertTrue(
            "Loading runner control status..." in mutation_panel
            or "Prompt saves and restores are disabled until runner controls are enabled." in mutation_panel
        )

        ready_state = results[6]
        self.assertFalse(ready_state["dirty"])
        self.assertEqual(backup_path, ready_state["backupSelection"])
        self.assertEqual("RESTORE BACKUP", ready_state["restoreConfirmation"])
        self.assertEqual("idle", ready_state["saveState"]["status"])
        self.assertEqual("idle", ready_state["restoreState"]["status"])

        dirty_state = results[8]
        self.assertTrue(dirty_state["dirty"])
        self.assertEqual("idle", dirty_state["saveState"]["status"])
        self.assertEqual("idle", dirty_state["restoreState"]["status"])

        reset_state = results[10]
        self.assertFalse(reset_state["dirty"])
        self.assertEqual(backup_path, reset_state["backupSelection"])
        self.assertEqual("", reset_state["restoreConfirmation"])
        self.assertEqual("idle", reset_state["saveState"]["status"])
        self.assertEqual("idle", reset_state["restoreState"]["status"])

    def test_prompt_editor_banner_surfaces_save_and_restore_outcomes(self) -> None:
        backup_path = (self.home / "prompts" / "agentcli" / "pm_bootstrap_prompt.20260426-120000.bak.md").as_posix()
        prompt = {
            "id": "pm_bootstrap",
            "file": "pm_bootstrap_prompt.md",
            "path": (self.home / "prompts" / "agentcli" / "pm_bootstrap_prompt.md").as_posix(),
            "scope": "PM",
            "profile": "personal",
            "source": "templates/agent_prompts",
            "mode": "template",
            "updated": "template",
            "summary": "PM bootstrap prompt",
            "preview": "[redacted]",
        }
        backup = {
            "path": backup_path,
            "name": Path(backup_path).name,
            "updated": "2026-04-26 12:00",
            "size": 42,
            "summary": "2026-04-26 12:00 | 42 bytes",
        }
        payload = {
            **prompt,
            "content": "Valid prompt {repo} {analysis_md}\n",
            "template_variables": ["repo", "analysis_md"],
            "required_template_variables": ["repo", "analysis_md"],
            "backups": [backup],
        }
        results = _run_adapter_harness(
            [
                {
                    "kind": "call",
                    "name": "applyPromptEditorPayload",
                    "args": [
                        prompt,
                        payload,
                        {
                            "saveState": {
                                "status": "success",
                                "message": "Prompt saved.",
                                "errorCode": "",
                                "backupPath": backup_path,
                                "savedPath": prompt["path"],
                                "savedAt": 1,
                                "requestPath": "/api/prompts/save",
                            },
                        },
                    ],
                },
                {"kind": "call", "name": "renderPromptEditorState", "args": []},
                {"kind": "call", "name": "renderPromptEditorMutationPanel", "args": []},
                {"kind": "call", "name": "renderPromptEditorBanner", "args": []},
                {
                    "kind": "call",
                    "name": "applyPromptEditorPayload",
                    "args": [
                        prompt,
                        payload,
                        {
                            "saveState": {
                                "status": "error",
                                "message": "Prompt save failed.",
                                "errorCode": "prompt_save_failed",
                                "backupPath": backup_path,
                                "savedPath": prompt["path"],
                                "savedAt": 2,
                                "requestPath": "/api/prompts/save",
                            },
                        },
                    ],
                },
                {"kind": "call", "name": "renderPromptEditorState", "args": []},
                {"kind": "call", "name": "renderPromptEditorBanner", "args": []},
                {
                    "kind": "call",
                    "name": "applyPromptEditorPayload",
                    "args": [
                        prompt,
                        payload,
                        {
                            "restoreState": {
                                "status": "success",
                                "message": "Prompt restored.",
                                "errorCode": "",
                                "backupPath": backup_path,
                                "restoredFromPath": backup_path,
                                "restoredAt": 3,
                                "requestPath": "/api/prompts/restore",
                            },
                        },
                    ],
                },
                {"kind": "call", "name": "renderPromptEditorState", "args": []},
                {"kind": "call", "name": "renderPromptEditorMutationPanel", "args": []},
                {"kind": "call", "name": "renderPromptEditorBanner", "args": []},
            ]
        )

        save_state = results[1]
        self.assertIn("SAVED", save_state)
        self.assertIn("BACKUP", save_state)
        save_panel = results[2]
        self.assertIn("Backup path", save_panel)
        save_banner = results[3]
        self.assertIn("Prompt saved", save_banner)

        save_error_state = results[5]
        self.assertIn("SAVE ERROR", save_error_state)
        save_error_banner = results[6]
        self.assertIn("Prompt save failed", save_error_banner)

        restore_state = results[8]
        self.assertIn("RESTORED", restore_state)
        self.assertIn("BACKUP", restore_state)
        restore_panel = results[9]
        self.assertIn("Restored from", restore_panel)
        self.assertIn("Restore backup path", restore_panel)
        restore_banner = results[10]
        self.assertIn("Prompt restored", restore_banner)

    def test_prompt_read_surfaces_validation_failures_for_empty_and_partial_templates(self) -> None:
        _write_config(self.config_path, self.repo)
        prompts_dir = self.home / "prompts" / "agentcli"
        prompt_path = prompts_dir / "pm_bootstrap_prompt.md"
        from fastapi.testclient import TestClient

        client = TestClient(self._create_app(self.repo))

        _write(prompt_path, "")
        empty_response = client.get("/api/prompts/read", params={"id": "pm_bootstrap", "file": "pm_bootstrap_prompt.md"})
        self.assertEqual(200, empty_response.status_code)
        empty_payload = empty_response.json()
        self.assertTrue(empty_payload["ok"])
        self.assertFalse(empty_payload["validation"]["ok"])
        self.assertEqual("Prompt content cannot be empty.", empty_payload["validation"]["content_error"])
        self.assertEqual("prompt_content_required", empty_payload["validation"]["content_error_code"])
        self.assertIn("prompt_content_required", [error["code"] for error in empty_payload["validation"]["errors"]])
        self.assertIn("prompt_template_variables_missing", [error["code"] for error in empty_payload["validation"]["errors"]])

        _write(prompt_path, "Repo: {repo}\n")
        partial_response = client.get("/api/prompts/read", params={"id": "pm_bootstrap", "file": "pm_bootstrap_prompt.md"})
        self.assertEqual(200, partial_response.status_code)
        partial_payload = partial_response.json()
        self.assertTrue(partial_payload["ok"])
        self.assertFalse(partial_payload["validation"]["ok"])
        self.assertEqual("", partial_payload["validation"]["content_error"])
        self.assertEqual("", partial_payload["validation"]["content_error_code"])
        self.assertGreater(len(partial_payload["validation"]["missing_variables"]), 0)
        self.assertIn("analysis_md", partial_payload["validation"]["missing_variables"])
        self.assertEqual("prompt_template_variables_missing", partial_payload["validation"]["template_error_code"])

    def test_api_logs_tail_supports_incremental_cursor_reads_filters_and_malformed_state(self) -> None:
        _write(
            self.run_dir / "metrics.jsonl",
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:00",
                            "seq": 1,
                            "level": "info",
                            "event": "pm_start",
                            "stage": "PM",
                            "cycle": 1,
                            "message": "pm start",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:10",
                            "seq": 2,
                            "level": "warn",
                            "event": "dev_retry",
                            "stage": "Dev",
                            "cycle": 1,
                            "step": 2,
                            "task_id": "T-020",
                            "task_title": "API-backed observation path",
                            "attempt": 1,
                            "reason": "build_failed",
                            "message": "retry after build failure",
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:20",
                            "seq": 3,
                            "level": "info",
                            "event": "task_end",
                            "stage": "Dev",
                            "cycle": 1,
                            "step": 2,
                            "task_id": "T-020",
                            "task_title": "API-backed observation path",
                            "attempt": 2,
                            "rc": 0,
                            "reason": "completed",
                            "message": "task end",
                        },
                        ensure_ascii=False,
                    ),
                    "not json at all",
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:30",
                            "seq": 4,
                            "level": "info",
                            "event": "qa_end",
                            "stage": "QA",
                            "cycle": 1,
                            "task_id": "T-021",
                            "task_title": "Polish logs",
                            "message": "qa verified",
                        },
                        ensure_ascii=False,
                    ),
                ]
            )
            + "\n",
        )

        tail = self._api_log_tail(max_lines=2)
        self.assertEqual("metrics.jsonl", Path(str(tail["source_file"])).name)
        self.assertEqual("malformed_line", tail["state"])
        self.assertEqual(5, tail["next_cursor"])
        self.assertEqual([3, 5], [entry["line_number"] for entry in tail["entries"]])

        chunk_1 = self._api_log_tail(cursor=0, max_lines=2)
        self.assertEqual("loading", chunk_1["state"])
        self.assertEqual(2, chunk_1["next_cursor"])
        self.assertEqual([1, 2], [entry["line_number"] for entry in chunk_1["entries"]])
        self.assertEqual(["info", "warn"], [entry["lvl"] for entry in chunk_1["entries"]])

        chunk_2 = self._api_log_tail(cursor=2, max_lines=2)
        self.assertEqual("malformed_line", chunk_2["state"])
        self.assertEqual(5, chunk_2["next_cursor"])
        self.assertEqual([3, 5], [entry["line_number"] for entry in chunk_2["entries"]])
        self.assertEqual(["task end", "qa verified"], [entry["msg"] for entry in chunk_2["entries"]])

        filtered = self._api_log_tail(
            cursor=1,
            max_lines=1,
            level="warn",
            stage="Dev",
            task_id="T-020",
            search="build failure",
        )
        self.assertEqual("loading", filtered["state"])
        self.assertEqual(2, filtered["next_cursor"])
        self.assertEqual(1, len(filtered["entries"]))
        self.assertEqual(2, filtered["entries"][0]["line_number"])
        self.assertEqual("warn", filtered["entries"][0]["lvl"])
        self.assertEqual("Dev", filtered["entries"][0]["stage"])
        self.assertEqual("T-020", filtered["entries"][0]["task_id"])
        self.assertIn("build failure", filtered["entries"][0]["msg"])

        no_match = self._api_log_tail(cursor=4, max_lines=1, level="trace")
        self.assertEqual("loading", no_match["state"])
        self.assertEqual(5, no_match["next_cursor"])
        self.assertEqual([], no_match["entries"])

    def test_api_logs_tail_handles_missing_log_file(self) -> None:
        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app(self.empty_repo, web_dir=WEB_CONSOLE))
        payload = client.get("/api/logs/tail").json()

        self.assertFalse(payload["ok"])
        self.assertEqual("missing_file", payload["state"])
        self.assertEqual([], payload["entries"])
        self.assertEqual(0, payload["next_cursor"])
        self.assertEqual("", payload["source_file"])

    def test_api_logs_tail_reports_empty_state_for_empty_log_file(self) -> None:
        _write(self.run_dir / "metrics.jsonl", "")

        payload = self._api_log_tail(max_lines=5)

        self.assertTrue(payload["ok"])
        self.assertEqual("empty", payload["state"])
        self.assertEqual([], payload["entries"])
        self.assertEqual(0, payload["next_cursor"])
        self.assertEqual("metrics.jsonl", Path(str(payload["source_file"])).name)

    def test_api_logs_tail_reports_read_errors(self) -> None:
        with patch.object(Path, "open", side_effect=OSError("simulated read failure")):
            payload = self._api_log_tail(max_lines=1)

        self.assertFalse(payload["ok"])
        self.assertEqual("read_error", payload["state"])
        self.assertEqual([], payload["entries"])
        self.assertEqual(0, payload["next_cursor"])
        self.assertTrue(payload["source_file"].endswith("metrics.jsonl"))

    def test_config_redaction_masks_sensitive_values(self) -> None:
        from agent_runner.web import _redact_config

        redacted = _redact_config(
            {
                "repo": "C:/Dev/AgentCLI",
                "openai_api_key": "example-api-key",
                "telegram": {
                    "enabled": True,
                    "bot_token": "123:abc",
                    "chat_id": "999",
                },
                "nested": [{"password": "secret"}, {"name": "safe"}],
            }
        )

        self.assertEqual("C:/Dev/AgentCLI", redacted["repo"])
        self.assertEqual("[redacted]", redacted["openai_api_key"])
        self.assertTrue(redacted["telegram"]["enabled"])
        self.assertEqual("[redacted]", redacted["telegram"]["bot_token"])
        self.assertEqual("[redacted]", redacted["telegram"]["chat_id"])
        self.assertEqual("[redacted]", redacted["nested"][0]["password"])
        self.assertEqual("safe", redacted["nested"][1]["name"])

    def test_static_console_assets_are_served(self) -> None:
        root = self.client.get("/")
        self.assertEqual(200, root.status_code)
        self.assertIn("text/html", root.headers.get("content-type", ""))

        app_js = self.client.get("/app.js")
        self.assertEqual(200, app_js.status_code)
        self.assertIn("application/javascript", app_js.headers.get("content-type", ""))

        styles = self.client.get("/styles.css")
        self.assertEqual(200, styles.status_code)
        self.assertIn("text/css", styles.headers.get("content-type", ""))

    def test_unknown_api_paths_return_not_found(self) -> None:
        response = self.client.get("/api/unknown")
        self.assertEqual(404, response.status_code)

    def test_api_goals_returns_metadata_items_and_warnings(self) -> None:
        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

Intro line that should be ignored.

## P0
- [x] Expose read-only progress views
- [ ] Add FastAPI goals endpoint
This line should warn.

## P1
- [X] Keep browser-local edits
- [ ] Show file metadata
- [ ] Preserve line numbers
Another unsupported line.

## Notes
- [ ] Ignore outside sections
""",
        )

        payload = self.client.get("/api/goals").json()

        self.assertTrue(payload["exists"])
        self.assertTrue(payload["path"].endswith(".doc/GOALS.md"))
        self.assertIn("Expose read-only progress views", payload["raw_text"])
        self.assertGreater(payload["size"], 0)
        self.assertIsNotNone(payload["mtime"])
        self.assertEqual("all", payload["completion_level"])
        self.assertEqual(2, len(payload["items"]["p0"]))
        self.assertEqual(3, len(payload["items"]["p1"]))
        self.assertEqual(6, payload["items"]["p0"][0]["line_number"])
        self.assertTrue(payload["items"]["p0"][0]["checked"])
        self.assertEqual(7, payload["items"]["p0"][1]["line_number"])
        self.assertFalse(payload["items"]["p0"][1]["checked"])
        self.assertEqual(11, payload["items"]["p1"][0]["line_number"])
        self.assertTrue(payload["items"]["p1"][0]["checked"])
        self.assertEqual(12, payload["items"]["p1"][1]["line_number"])
        self.assertFalse(payload["items"]["p1"][1]["checked"])
        self.assertEqual(13, payload["items"]["p1"][2]["line_number"])
        self.assertFalse(payload["items"]["p1"][2]["checked"])
        self.assertEqual(5, payload["summary"]["total"])
        self.assertEqual(2, payload["summary"]["done"])
        self.assertFalse(payload["completion"]["project_complete"])
        self.assertEqual(3, payload["summary"]["warnings"])
        warning_lines = [warning["line_number"] for warning in payload["warnings"]]
        self.assertEqual([8, 14, 17], warning_lines)
        self.assertIn("unsupported_goal_line", {warning["reason"] for warning in payload["warnings"]})
        self.assertIn("checkbox_outside_goal_section", {warning["reason"] for warning in payload["warnings"]})

    def test_api_goals_parses_nested_p0_p1_sections_without_completion_noise(self) -> None:
        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

## P0 (Must-Have)

### P0-A. Foundation

- [x] Serve the web console

### P0-B. Runtime

- [ ] Show live status

## P1 (Should-Have)

### P1-A. Polish

- [ ] Add optional polish

## Completion Criteria

- This non-goal checklist criterion should not warn.
""",
        )

        payload = self.client.get("/api/goals").json()

        self.assertEqual(2, len(payload["items"]["p0"]))
        self.assertEqual(1, len(payload["items"]["p1"]))
        self.assertEqual(3, payload["summary"]["total"])
        self.assertEqual(1, payload["summary"]["done"])
        self.assertEqual(0, payload["summary"]["warnings"])
        self.assertEqual("Serve the web console", payload["items"]["p0"][0]["text"])
        self.assertEqual(7, payload["items"]["p0"][0]["line_number"])
        self.assertEqual("Show live status", payload["items"]["p0"][1]["text"])
        self.assertEqual("Add optional polish", payload["items"]["p1"][0]["text"])

    def test_api_goals_handles_missing_goals_file(self) -> None:
        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        client = TestClient(create_app(self.empty_repo, web_dir=WEB_CONSOLE))
        payload = client.get("/api/goals").json()

        self.assertFalse(payload["exists"])
        self.assertTrue(payload["path"].endswith(".doc/GOALS.md"))
        self.assertEqual("", payload["raw_text"])
        self.assertEqual({"p0": [], "p1": []}, payload["items"])
        self.assertFalse(payload["completion"]["has_goals"])
        self.assertFalse(payload["completion"]["project_complete"])
        self.assertEqual("all", payload["completion_level"])
        self.assertEqual(0, payload["summary"]["total"])
        self.assertEqual(0, payload["summary"]["warnings"])
        self.assertEqual([], payload["warnings"])

    def test_api_goals_handles_empty_goals_file(self) -> None:
        _write(self.repo / ".doc" / "GOALS.md", "")

        payload = self.client.get("/api/goals").json()

        self.assertTrue(payload["exists"])
        self.assertTrue(payload["path"].endswith(".doc/GOALS.md"))
        self.assertEqual("", payload["raw_text"])
        self.assertEqual({"p0": [], "p1": []}, payload["items"])
        self.assertFalse(payload["completion"]["has_goals"])
        self.assertFalse(payload["completion"]["project_complete"])
        self.assertEqual("all", payload["completion_level"])
        self.assertEqual(0, payload["summary"]["total"])
        self.assertEqual(0, payload["summary"]["done"])
        self.assertEqual(0, payload["summary"]["unchecked"])
        self.assertEqual(0, payload["summary"]["warnings"])
        self.assertEqual([], payload["warnings"])

    def test_adapter_response_normalization_covers_no_run_partial_and_normal_fixtures(self) -> None:
        no_run, partial, normal, fallback = _run_adapter_harness(
            [
                {"kind": "snapshot", "data": _make_no_run_snapshot()},
                {"kind": "snapshot", "data": _make_partial_snapshot()},
                {"kind": "snapshot", "data": _make_normal_snapshot()},
                {"kind": "fallback"},
            ]
        )

        with self.subTest("no-run"):
            self.assertEqual("api", no_run["sourceMode"])
            self.assertEqual("no-run", no_run["activeRun"]["id"])
            self.assertEqual("", no_run["activeRun"]["task"])
            self.assertEqual("", no_run["activeRun"]["taskTitle"])
            self.assertIsNone(no_run["activeRun"]["attempt"])
            self.assertEqual("", no_run["activeRun"]["worktreeMode"])
            self.assertEqual("", no_run["activeRun"]["runDir"])
            self.assertEqual("", no_run["activeRun"]["finalReason"])
            self.assertEqual("empty", no_run["sectionState"]["activeRun"]["status"])
            self.assertEqual("empty", no_run["sectionState"]["stages"]["status"])
            self.assertEqual("No lifecycle records were published yet.", no_run["sectionState"]["stages"]["message"])
            self.assertEqual(0, len(no_run["stages"]))
            self.assertEqual("", no_run["backlogSelectedId"])
            self.assertEqual({"pending": 0, "in_progress": 0, "done": 0, "failed": 0}, no_run["backlogCounts"])
            self.assertFalse(no_run["activeRun"]["tokensAvailable"])
            self.assertFalse(no_run["activeRun"]["budgetAvailable"])
            self.assertFalse(no_run["activeRun"]["quotaAvailable"])
            self.assertFalse(no_run["activeRun"]["progressAvailable"])
            self.assertEqual({"in": None, "out": None, "available": False}, no_run["activeRun"]["tokens"])
            self.assertEqual({"window": "", "used": None, "available": False}, no_run["activeRun"]["quota"])
            self.assertIsNone(no_run["activeRun"]["budgetUsed"])
            self.assertEqual({"window": "", "used": None, "available": False}, no_run["metrics"]["quota"])
            self.assertEqual([], no_run["history"])
            self.assertEqual([], no_run["notifications"])
            self.assertEqual("empty", no_run["sectionState"]["history"]["status"])
            self.assertEqual("Run history is empty.", no_run["sectionState"]["history"]["message"])
            self.assertEqual("empty", no_run["sectionState"]["notifications"]["status"])
            self.assertEqual("No notifications have been recorded yet.", no_run["sectionState"]["notifications"]["message"])

        with self.subTest("partial-run"):
            self.assertEqual("partial", partial["sectionState"]["stages"]["status"])
            self.assertEqual("Only some lifecycle records were published.", partial["sectionState"]["stages"]["message"])
            self.assertEqual(2, len(partial["stages"]))
            self.assertEqual("T-020", partial["stages"][0]["taskId"])
            self.assertEqual(2, partial["backlog"][0]["attempt"])
            self.assertEqual("agent_runner/web.py, web_console/app.js", partial["backlog"][0]["fileScope"])
            self.assertEqual(120, len(partial["logs"]))
            self.assertEqual("running", partial["activeRun"]["status"])
            self.assertEqual({"window": "5h", "used": 0.4, "available": True}, partial["activeRun"]["quota"])
            self.assertEqual("5h", partial["activeRun"]["quotaWindow"])
            self.assertEqual(0.4, partial["activeRun"]["quotaUsed"])

        with self.subTest("normal-run"):
            self.assertEqual("api", normal["sourceMode"])
            self.assertEqual("ready", normal["sectionState"]["stages"]["status"])
            self.assertEqual("ready", normal["sectionState"]["backlog"]["status"])
            self.assertEqual("partial", normal["sectionState"]["worktree"]["status"])
            self.assertEqual(3, len(normal["stages"]))
            self.assertEqual(2, len(normal["backlog"]))
            self.assertEqual(["T-020"], normal["backlog"][1]["dependsOn"])
            self.assertEqual("web_console/app.js, web_console/styles.css", normal["backlog"][1]["fileScope"])
            self.assertEqual("running", normal["stages"][1]["status"])
            self.assertEqual("QA", normal["stages"][2]["id"])
            self.assertEqual("run_20260426_120000", normal["activeRun"]["id"])
            self.assertEqual("T-020", normal["activeRun"]["task"])
            self.assertEqual("API-backed observation path", normal["activeRun"]["taskTitle"])
            self.assertEqual("main", normal["activeRun"]["branch"])
            self.assertEqual(".AgentCLI/agent_runs/20260426-120000", normal["activeRun"]["runDir"])
            self.assertEqual("", normal["activeRun"]["finalReason"])
            self.assertEqual("T-020", normal["progress"]["current_task_id"])
            self.assertEqual("API-backed observation path", normal["progress"]["current_task_title"])

            self.assertEqual("", normal["progress"]["final_reason"])
            self.assertTrue(normal["activeRun"]["tokensAvailable"])
            self.assertTrue(normal["activeRun"]["budgetAvailable"])
            self.assertTrue(normal["activeRun"]["quotaAvailable"])
            self.assertEqual({"window": "5h", "used": 0.41, "available": True}, normal["activeRun"]["quota"])
            self.assertEqual("5h", normal["activeRun"]["quotaWindow"])
            self.assertEqual(0.41, normal["activeRun"]["quotaUsed"])
            self.assertEqual("pending", normal["worktreeMerge"]["status"])

        with self.subTest("fallback-fixture"):
            self.assertEqual("fallback", fallback["sourceMode"])
            self.assertEqual("Fallback data", fallback["snapshotLabel"])
            self.assertEqual("no-run", fallback["activeRun"]["id"])
            self.assertEqual("idle", fallback["activeRun"]["status"])
            self.assertEqual({"window": "", "used": None, "available": False}, fallback["activeRun"]["quota"])
            self.assertEqual({"window": "", "used": None, "available": False}, fallback["metrics"]["quota"])
            self.assertEqual([], fallback["stages"])
            self.assertEqual([], fallback["backlog"])
            self.assertEqual("empty", fallback["sectionState"]["activeRun"]["status"])
            self.assertEqual("empty", fallback["sectionState"]["stages"]["status"])
            self.assertEqual("empty", fallback["sectionState"]["backlog"]["status"])

    def test_log_tail_helpers_build_queries_and_cursor_updates(self) -> None:
        blank = _run_log_tail_harness([{"kind": "state"}])[0]
        query_result = _run_log_tail_harness(
            [
                {
                    "kind": "query",
                    "filters": {"level": "WARN", "stage": " Dev ", "taskId": "T-020", "search": " error path "},
                    "options": {"cursor": 12, "maxLines": 25},
                }
            ]
        )[0]

        self.assertEqual(
            {"max_lines": 25, "cursor": 12, "level": "warn", "stage": "Dev", "task_id": "T-020", "search": "error path"},
            query_result["query"],
        )
        self.assertEqual(
            "/api/logs/tail?max_lines=25&cursor=12&level=warn&stage=Dev&task_id=T-020&search=error%20path",
            query_result["url"],
        )

        previous = {
            **blank,
            "status": "loading",
            "loading": False,
            "paused": False,
            "entries": [
                {
                    "cursor": 4,
                    "line_number": 4,
                    "t": "12:04:00",
                    "stage": "Dev",
                    "lvl": "warn",
                    "msg": "existing warn line",
                }
            ],
            "cursor": 4,
            "nextCursor": 5,
            "selected": [4],
            "filters": {"level": "warn", "stage": "Dev", "taskId": "T-020", "search": "error path"},
            "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True},
        }
        first_payload = {
            "ok": True,
            "state": "loading",
            "entries": [
                {
                    "cursor": 5,
                    "line_number": 5,
                    "t": "12:05:00",
                    "stage": "Dev",
                    "lvl": "info",
                    "msg": "fresh info line",
                }
            ],
            "next_cursor": 6,
            "cursor": 5,
            "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True},
            "malformed_lines": 0,
        }
        applied_first = _run_log_tail_harness(
            [{"kind": "apply", "previous": previous, "payload": first_payload, "options": {"reset": False}}]
        )[0]
        second_payload = {
            "ok": True,
            "state": "loading",
            "entries": [
                {
                    "cursor": 6,
                    "line_number": 6,
                    "t": "12:06:00",
                    "stage": "QA",
                    "lvl": "warn",
                    "msg": "fresh warning line",
                }
            ],
            "next_cursor": 7,
            "cursor": 6,
            "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True},
            "malformed_lines": 0,
        }
        applied_second = _run_log_tail_harness(
            [{"kind": "apply", "previous": applied_first, "payload": second_payload, "options": {"reset": False}}]
        )[0]

        self.assertEqual(5, applied_first["cursor"])
        self.assertEqual(6, applied_first["nextCursor"])
        self.assertEqual([4], applied_first["selected"])
        self.assertEqual(2, len(applied_first["entries"]))
        self.assertEqual(6, applied_second["cursor"])
        self.assertEqual(7, applied_second["nextCursor"])
        self.assertEqual([4], applied_second["selected"])
        self.assertEqual(3, len(applied_second["entries"]))

        clipboard = _run_log_tail_harness(
            [
                {
                    "kind": "clipboard",
                    "entries": applied_second["entries"],
                    "selected": applied_second["selected"],
                }
            ]
        )[0]
        download = _run_log_tail_harness(
            [
                {
                    "kind": "download",
                    "tail": applied_second,
                    "context": {
                        "runId": "run_20260426_120000",
                        "latestRunDir": ".AgentCLI/agent_runs/20260426-120000",
                    },
                }
            ]
        )[0]

        self.assertIn("#4 12:04:00 [Dev] warn existing warn line", clipboard)
        self.assertEqual("agentcli-run_20260426_120000-logs.txt", download["filename"])
        self.assertIn("# Filters: level=warn | stage=Dev | task_id=T-020 | search=error path", download["text"])
        self.assertIn("fresh warning line", download["text"])

    def test_log_tail_live_polling_advances_cursor_and_stops_when_paused(self) -> None:
        session = _run_log_tail_session_harness(
            [
                {
                    "kind": "call",
                    "name": "seedLogTailState",
                    "args": [
                        {
                            "activeView": "logs",
                            "sourceMode": "api",
                            "runId": self.run_dir.name,
                            "latestRunDir": self.run_dir.as_posix(),
                            "logTail": {
                                "status": "loading",
                                "loading": False,
                                "paused": False,
                                "entries": [
                                    {
                                        "cursor": 5,
                                        "line_number": 5,
                                        "t": "12:05:00",
                                        "stage": "Dev",
                                        "lvl": "warn",
                                        "msg": "existing warn line",
                                    }
                                ],
                                "cursor": 5,
                                "nextCursor": 6,
                                "selected": [5],
                                "filters": {
                                    "level": "warn",
                                    "stage": "Dev",
                                    "taskId": "T-020",
                                    "search": "build failure",
                                },
                                "source": {
                                    "path": "C:/runs/run.log",
                                    "name": "run.log",
                                    "exists": True,
                                },
                            },
                        }
                    ],
                },
                {
                    "kind": "call",
                    "name": "startServerLogTail",
                    "args": [{"silent": True}],
                },
                {
                    "kind": "call",
                    "name": "inspectLogTailState",
                    "args": [],
                },
                {
                    "kind": "call",
                    "name": "setLiveTailPaused",
                    "args": [True],
                },
                {
                    "kind": "call",
                    "name": "syncLogTailStreaming",
                    "args": [],
                },
                {
                    "kind": "call",
                    "name": "inspectLogTailState",
                    "args": [],
                },
                {
                    "kind": "call",
                    "name": "setLiveTailPaused",
                    "args": [False],
                },
                {
                    "kind": "call",
                    "name": "syncLogTailStreaming",
                    "args": [],
                },
                {
                    "kind": "call",
                    "name": "inspectLogTailState",
                    "args": [],
                },
            ],
            fetch_responses=[
                {
                    "ok": True,
                    "body": {
                        "ok": True,
                        "state": "loading",
                        "entries": [
                            {
                                "cursor": 6,
                                "line_number": 6,
                                "t": "12:06:00",
                                "stage": "QA",
                                "lvl": "info",
                                "msg": "fresh info line",
                            }
                        ],
                        "next_cursor": 7,
                        "cursor": 6,
                        "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True},
                        "malformed_lines": 0,
                    },
                },
                {
                    "ok": True,
                    "body": {
                        "ok": True,
                        "state": "loading",
                        "entries": [
                            {
                                "cursor": 7,
                                "line_number": 7,
                                "t": "12:07:00",
                                "stage": "PM",
                                "lvl": "debug",
                                "msg": "fresh resume line",
                            }
                        ],
                        "next_cursor": 8,
                        "cursor": 7,
                        "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True},
                        "malformed_lines": 0,
                    },
                },
            ],
        )
        started = session["results"][2]
        paused = session["results"][5]
        resumed = session["results"][8]

        self.assertEqual(
            "/api/logs/tail?max_lines=120&cursor=6&level=warn&stage=Dev&task_id=T-020&search=build%20failure",
            session["fetchCalls"][0],
        )
        self.assertEqual(
            "/api/logs/tail?max_lines=120&cursor=7&level=warn&stage=Dev&task_id=T-020&search=build%20failure",
            session["fetchCalls"][1],
        )
        self.assertEqual([{"id": 1, "delay": 2400}, {"id": 2, "delay": 2400}], session["intervals"])
        self.assertEqual([1], session["cleared"])
        self.assertTrue(started["timerActive"])
        self.assertFalse(started["paused"])
        self.assertEqual("loading", started["status"])
        self.assertEqual(6, started["cursor"])
        self.assertEqual(7, started["nextCursor"])
        self.assertEqual(2, len(started["entries"]))
        self.assertFalse(started["loading"])
        self.assertEqual([5], started["selected"])

        self.assertTrue(paused["paused"])
        self.assertFalse(paused["loading"])
        self.assertFalse(paused["timerActive"])
        self.assertEqual(6, paused["cursor"])
        self.assertEqual(7, paused["nextCursor"])
        self.assertEqual(2, len(paused["entries"]))
        self.assertEqual([5], paused["selected"])
        self.assertEqual({"level": "warn", "stage": "Dev", "taskId": "T-020", "search": "build failure"}, paused["filters"])
        self.assertGreater(paused["requestSeq"], started["requestSeq"])

        self.assertFalse(resumed["paused"])
        self.assertTrue(resumed["timerActive"])
        self.assertEqual(7, resumed["cursor"])
        self.assertEqual(8, resumed["nextCursor"])
        self.assertEqual(3, len(resumed["entries"]))
        self.assertEqual([5], resumed["selected"])
        self.assertEqual({"level": "warn", "stage": "Dev", "taskId": "T-020", "search": "build failure"}, resumed["filters"])
        self.assertGreater(resumed["requestSeq"], paused["requestSeq"])

    def test_log_tail_state_banners_and_toolbar_rendering(self) -> None:
        blank = _run_log_tail_harness([{"kind": "state"}])[0]
        active_tail = {
            **blank,
            "loading": False,
            "paused": False,
            "status": "loading",
            "entries": [
                {
                    "cursor": 4,
                    "line_number": 4,
                    "t": "12:04:00",
                    "stage": "Dev",
                    "lvl": "warn",
                    "msg": "existing warn line",
                }
            ],
            "cursor": 4,
            "nextCursor": 5,
            "selected": [4],
            "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True},
        }
        paused_tail = {**active_tail, "paused": True, "selected": [4, 6]}
        missing_tail = {**blank, "status": "missing_file", "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": False}}
        empty_tail = {**blank, "status": "empty", "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True}}
        read_error_tail = {
            **blank,
            "status": "read_error",
            "error": "permission denied",
            "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": False},
        }

        describe_results = _run_log_tail_harness(
            [
                {"kind": "describe", "tail": active_tail},
                {"kind": "describe", "tail": paused_tail},
                {"kind": "describe", "tail": empty_tail},
                {"kind": "describe", "tail": missing_tail},
                {"kind": "describe", "tail": read_error_tail},
            ]
        )

        self.assertEqual("Live tail active", describe_results[0]["title"])
        self.assertEqual("live", describe_results[0]["state"])
        self.assertEqual("Live tail paused", describe_results[1]["title"])
        self.assertEqual("paused", describe_results[1]["state"])
        self.assertEqual("No matching log lines", describe_results[2]["title"])
        self.assertEqual("empty", describe_results[2]["state"])
        self.assertEqual("Log file missing", describe_results[3]["title"])
        self.assertEqual("missing_file", describe_results[3]["state"])
        self.assertEqual("Log read error", describe_results[4]["title"])
        self.assertEqual("read_error", describe_results[4]["state"])
        self.assertIn("permission denied", describe_results[4]["copy"])
        self.assertIn("Resume live tail", describe_results[1]["copy"])
        self.assertIn("cursor 5", describe_results[1]["copy"])

        loading_tail = {**blank, "status": "loading", "loading": True, "paused": False, "source": {"path": "C:/runs/run.log", "name": "run.log", "exists": True}}
        loading_banner = _run_log_tail_harness([{"kind": "banner", "tail": loading_tail}])[0]
        self.assertIn("Loading active run log", loading_banner["banner"])
        self.assertIn("Pause live tail", loading_banner["filters"])
        self.assertIn('aria-busy="true"', loading_banner["filters"])
        self.assertIn("button--loading", loading_banner["filters"])
        self.assertIn("status-chip--loading", loading_banner["filters"])

        banner_result = _run_log_tail_harness([{"kind": "banner", "tail": paused_tail}])[0]
        self.assertIn("Live tail paused", banner_result["banner"])
        self.assertIn("Resume live tail", banner_result["filters"])
        self.assertIn("Copy selected lines (2)", banner_result["filters"])
        self.assertIn("Download filtered logs", banner_result["filters"])
        self.assertIn("Clear selection", banner_result["filters"])
        self.assertIn('data-log-filter-field="stage"', banner_result["filters"])
        self.assertIn('data-log-filter-field="taskId"', banner_result["filters"])
        self.assertIn('data-log-filter-field="search"', banner_result["filters"])
        self.assertIn('data-log-level="warn"', banner_result["filters"])
        self.assertIn("status-chip--paused", banner_result["filters"])
        self.assertIn("button--paused", banner_result["filters"])
        self.assertIn('aria-pressed="true"', banner_result["filters"])

        live_banner = _run_log_tail_harness([{"kind": "banner", "tail": active_tail}])[0]
        self.assertIn("Live tail active", live_banner["banner"])
        self.assertIn("Pause live tail", live_banner["filters"])
        self.assertIn("Copy selected lines", live_banner["filters"])
        self.assertIn("status-chip--running", live_banner["filters"])
        self.assertIn("button--quiet", live_banner["filters"])
        self.assertIn('aria-pressed="false"', live_banner["filters"])

    def test_adapter_normalizes_history_and_notification_contracts(self) -> None:
        populated_snapshot = self.client.get("/api/status").json()
        populated, empty = _run_adapter_harness(
            [
                {"kind": "snapshot", "data": populated_snapshot},
                {"kind": "snapshot", "data": _make_no_run_snapshot()},
            ]
        )

        self.assertEqual("ready", populated["sectionState"]["history"]["status"])
        self.assertEqual("ready", populated["sectionState"]["notifications"]["status"])
        self.assertGreaterEqual(len(populated["history"]), 1)
        history_item = next(item for item in populated["history"] if item["id"] == self.run_dir.name)
        self.assertEqual("project_complete", history_item["finalReason"])
        self.assertEqual("project_complete", history_item["shutdownReason"])
        self.assertEqual("project_complete", history_item["stopReason"])
        self.assertEqual({"done": 1, "failed": 0, "skipped": 0, "total": 2, "cycles": 1}, history_item["taskCounts"])
        self.assertEqual(populated["repo"]["branch"], history_item["branch"])
        self.assertEqual(120, history_item["durationSec"])
        self.assertEqual("pending", history_item["worktreeOutcome"])
        self.assertIn("runSummary", history_item)
        self.assertIn("lastRunSummary", history_item)
        self.assertEqual(1, populated["historySummary"]["runs"])
        self.assertEqual(1, populated["historySummary"]["tasksDone"])
        self.assertEqual(0, populated["historySummary"]["tasksFailed"])
        self.assertGreaterEqual(len(populated["notifications"]), 1)
        notification_kinds = {item["kind"] for item in populated["notifications"]}
        self.assertIn("run_start", notification_kinds)
        self.assertIn("task_done", notification_kinds)

        self.assertEqual([], empty["history"])
        self.assertEqual(0, empty["historySummary"]["runs"])
        self.assertEqual([], empty["notifications"])
        self.assertEqual("empty", empty["sectionState"]["history"]["status"])
        self.assertEqual("Run history is empty.", empty["sectionState"]["history"]["message"])
        self.assertEqual("empty", empty["sectionState"]["notifications"]["status"])
        self.assertEqual("No notifications have been recorded yet.", empty["sectionState"]["notifications"]["message"])

    def test_adapter_rejects_partial_quota_sources_without_mixing_fields(self) -> None:
        snapshot = _make_partial_snapshot()
        snapshot["active_run"]["quota"] = {"used": 0.4}
        snapshot["active_run"]["quota_used"] = 0.4
        snapshot["active_run"]["quotaUsed"] = 0.4
        snapshot["active_run"]["quota_window"] = ""
        snapshot["active_run"]["quotaWindow"] = ""
        snapshot["active_run"]["quotaAvailable"] = False
        snapshot["active_run"]["quota_available"] = False
        snapshot["metrics"]["quota"] = {"window": "5h"}
        snapshot["metrics"]["quota_window"] = "5h"
        snapshot["metrics"]["quotaWindow"] = "5h"
        snapshot["metrics"]["quota_used"] = None
        snapshot["metrics"]["quotaUsed"] = None
        snapshot["metrics"]["quotaAvailable"] = False
        snapshot["metrics"]["quota_available"] = False

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": snapshot}])[0]

        self.assertEqual("running", normalized["activeRun"]["status"])
        self.assertFalse(normalized["activeRun"]["quotaAvailable"])
        self.assertFalse(normalized["activeRun"]["quota_available"])
        self.assertEqual({"window": "", "used": None, "available": False}, normalized["activeRun"]["quota"])
        self.assertEqual("", normalized["activeRun"]["quotaWindow"])
        self.assertEqual("", normalized["activeRun"]["quota_window"])
        self.assertIsNone(normalized["activeRun"]["quotaUsed"])
        self.assertIsNone(normalized["activeRun"]["quota_used"])
        self.assertFalse(normalized["metrics"]["quotaAvailable"])
        self.assertFalse(normalized["metrics"]["quota_available"])
        self.assertEqual({"window": "", "used": None, "available": False}, normalized["metrics"]["quota"])
        self.assertEqual("", normalized["metrics"]["quotaWindow"])
        self.assertEqual("", normalized["metrics"]["quota_window"])
        self.assertIsNone(normalized["metrics"]["quotaUsed"])
        self.assertIsNone(normalized["metrics"]["quota_used"])

    def test_adapter_prefers_active_run_quota_when_metrics_disagree(self) -> None:
        snapshot = _make_partial_snapshot()
        snapshot["active_run"]["quota"] = {"window": "5h", "used": 0.41}
        snapshot["active_run"]["quota_window"] = "5h"
        snapshot["active_run"]["quotaWindow"] = "5h"
        snapshot["active_run"]["quota_used"] = 0.41
        snapshot["active_run"]["quotaUsed"] = 0.41
        snapshot["active_run"]["quotaAvailable"] = True
        snapshot["active_run"]["quota_available"] = True
        snapshot["metrics"]["quota"] = {"window": "7d", "used": 0.33}
        snapshot["metrics"]["quota_window"] = "7d"
        snapshot["metrics"]["quotaWindow"] = "7d"
        snapshot["metrics"]["quota_used"] = 0.33
        snapshot["metrics"]["quotaUsed"] = 0.33
        snapshot["metrics"]["quotaAvailable"] = True
        snapshot["metrics"]["quota_available"] = True

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": snapshot}])[0]

        expected_quota = {"window": "5h", "used": 0.41, "available": True}
        self.assertEqual(expected_quota, normalized["activeRun"]["quota"])
        self.assertEqual(expected_quota, normalized["metrics"]["quota"])
        self.assertEqual("5h", normalized["activeRun"]["quotaWindow"])
        self.assertEqual("5h", normalized["metrics"]["quotaWindow"])
        self.assertEqual(0.41, normalized["activeRun"]["quotaUsed"])
        self.assertEqual(0.41, normalized["metrics"]["quotaUsed"])
        self.assertTrue(normalized["activeRun"]["quotaAvailable"])
        self.assertTrue(normalized["metrics"]["quotaAvailable"])

    def test_adapter_preserves_goals_metadata_and_browser_local_items(self) -> None:
        snapshot = _make_no_run_snapshot()
        snapshot["goals"] = {
            "path": ".doc/GOALS.md",
            "exists": True,
            "mtime": 1714132800.0,
            "size": 512,
            "raw_text": "# Project Goals\n\n## P0\n- [x] Expose read-only progress views\n\n## P1\n- [ ] Add metadata-aware goal rendering\n",
            "completion_level": "all",
            "items": {
                "p0": [
                    {
                        "done": True,
                        "checked": True,
                        "checkbox": "[x]",
                        "text": "Expose read-only progress views",
                        "note": "",
                        "line_number": 4,
                        "line": 4,
                    }
                ],
                "p1": [
                    {
                        "done": False,
                        "checked": False,
                        "checkbox": "[ ]",
                        "text": "Add metadata-aware goal rendering",
                        "note": "",
                        "line_number": 7,
                        "line": 7,
                    }
                ],
            },
            "completion": {
                "has_goals": True,
                "p0_total": 1,
                "p0_done": 1,
                "p1_total": 1,
                "p1_done": 0,
                "all_total": 2,
                "all_done": 1,
                "project_complete": False,
            },
            "summary": {
                "has_goals": True,
                "project_complete": False,
                "p0_total": 1,
                "p0_done": 1,
                "p1_total": 1,
                "p1_done": 0,
                "all_total": 2,
                "all_done": 1,
                "total": 2,
                "done": 1,
                "unchecked": 1,
                "warnings": 1,
            },
            "warnings": [
                {
                    "line_number": 8,
                    "line": "This line should warn.",
                    "reason": "unsupported_goal_line",
                    "message": "Non-checkbox content inside a GOALS section was ignored.",
                }
            ],
        }

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": snapshot}])[0]

        self.assertTrue(normalized["goalsSnapshot"]["exists"])
        self.assertEqual(".doc/GOALS.md", normalized["goalsSnapshot"]["path"])
        self.assertEqual(4, normalized["goals"]["p0"][0]["lineNumber"])
        self.assertEqual(4, normalized["goals"]["p0"][0]["line_number"])
        self.assertTrue(normalized["goals"]["p0"][0]["checked"])
        self.assertEqual(7, normalized["goals"]["p1"][0]["lineNumber"])
        self.assertFalse(normalized["goals"]["p1"][0]["checked"])
        self.assertEqual(2, normalized["goalsMeta"]["total"])
        self.assertEqual(1, normalized["goalsMeta"]["done"])
        self.assertEqual("all", normalized["goalsSnapshot"]["completion_level"])
        self.assertEqual(1, len(normalized["goalsSnapshot"]["warnings"]))
        self.assertEqual("unsupported_goal_line", normalized["goalsSnapshot"]["warnings"][0]["reason"])
        self.assertEqual(8, normalized["goalsSnapshot"]["warnings"][0]["line_number"])
        self.assertEqual("ready", normalized["sectionState"]["goals"]["status"])
        self.assertEqual(
            "Read-only GOALS.md snapshot with stable P0/P1 grouping and exact checkbox state.",
            normalized["sectionState"]["goals"]["message"],
        )

        with self.subTest("missing-file"):
            missing = _make_no_run_snapshot()
            missing["goals"] = {
                "path": ".doc/GOALS.md",
                "exists": False,
                "mtime": None,
                "size": None,
                "raw_text": "",
                "completion_level": "all",
                "items": {"p0": [], "p1": []},
                "completion": {
                    "has_goals": False,
                    "project_complete": False,
                    "p0_total": 0,
                    "p0_done": 0,
                    "p1_total": 0,
                    "p1_done": 0,
                    "all_total": 0,
                    "all_done": 0,
                },
                "summary": {
                    "has_goals": False,
                    "project_complete": False,
                    "p0_total": 0,
                    "p0_done": 0,
                    "p1_total": 0,
                    "p1_done": 0,
                    "all_total": 0,
                    "all_done": 0,
                    "total": 0,
                    "done": 0,
                    "unchecked": 0,
                    "warnings": 0,
                },
                "warnings": [],
            }

            normalized_missing = _run_adapter_harness([{"kind": "snapshot", "data": missing}])[0]

            self.assertFalse(normalized_missing["goalsSnapshot"]["exists"])
            self.assertEqual("", normalized_missing["goalsSnapshot"]["raw_text"])
            self.assertEqual({"p0": [], "p1": []}, normalized_missing["goals"])
            self.assertEqual(0, normalized_missing["goalsMeta"]["total"])
            self.assertEqual(0, normalized_missing["goalsMeta"]["done"])
            self.assertEqual("GOALS.md is missing.", normalized_missing["sectionState"]["goals"]["message"])

        with self.subTest("empty-file"):
            empty = _make_no_run_snapshot()
            empty["goals"] = {
                "path": ".doc/GOALS.md",
                "exists": True,
                "mtime": 1714132800.0,
                "size": 0,
                "raw_text": "",
                "completion_level": "all",
                "items": {"p0": [], "p1": []},
                "completion": {
                    "has_goals": False,
                    "project_complete": False,
                    "p0_total": 0,
                    "p0_done": 0,
                    "p1_total": 0,
                    "p1_done": 0,
                    "all_total": 0,
                    "all_done": 0,
                },
                "summary": {
                    "has_goals": False,
                    "project_complete": False,
                    "p0_total": 0,
                    "p0_done": 0,
                    "p1_total": 0,
                    "p1_done": 0,
                    "all_total": 0,
                    "all_done": 0,
                    "total": 0,
                    "done": 0,
                    "unchecked": 0,
                    "warnings": 0,
                },
                "warnings": [],
            }

            normalized_empty = _run_adapter_harness([{"kind": "snapshot", "data": empty}])[0]

            self.assertTrue(normalized_empty["goalsSnapshot"]["exists"])
            self.assertEqual("", normalized_empty["goalsSnapshot"]["raw_text"])
            self.assertEqual({"p0": [], "p1": []}, normalized_empty["goals"])
            self.assertEqual(0, normalized_empty["goalsMeta"]["total"])
            self.assertEqual(0, normalized_empty["goalsMeta"]["done"])
            self.assertEqual("GOALS.md is empty.", normalized_empty["sectionState"]["goals"]["message"])

    def test_adapter_builds_goal_draft_diff_without_losing_metadata(self) -> None:
        snapshot = _make_no_run_snapshot()
        snapshot["goals"] = {
            "path": ".doc/GOALS.md",
            "exists": True,
            "mtime": 1714132800.0,
            "size": 256,
            "raw_text": "# Project Goals\n\n## P0\n- [x] Expose read-only progress views\n- [ ] Add FastAPI goals endpoint\n\n## P1\n- [ ] Keep browser-local edits\n",
            "completion_level": "all",
            "items": {
                "p0": [
                    {
                        "done": True,
                        "checked": True,
                        "checkbox": "[x]",
                        "text": "Expose read-only progress views",
                        "note": "",
                        "line_number": 4,
                        "lineNumber": 4,
                        "line": 4,
                    },
                    {
                        "done": False,
                        "checked": False,
                        "checkbox": "[ ]",
                        "text": "Add FastAPI goals endpoint",
                        "note": "",
                        "line_number": 5,
                        "lineNumber": 5,
                        "line": 5,
                    },
                ],
                "p1": [
                    {
                        "done": False,
                        "checked": False,
                        "checkbox": "[ ]",
                        "text": "Keep browser-local edits",
                        "note": "",
                        "line_number": 8,
                        "lineNumber": 8,
                        "line": 8,
                    }
                ],
            },
            "completion": {
                "has_goals": True,
                "project_complete": False,
                "p0_total": 2,
                "p0_done": 1,
                "p1_total": 1,
                "p1_done": 0,
                "all_total": 3,
                "all_done": 1,
            },
            "summary": {
                "has_goals": True,
                "project_complete": False,
                "p0_total": 2,
                "p0_done": 1,
                "p1_total": 1,
                "p1_done": 0,
                "all_total": 3,
                "all_done": 1,
                "total": 3,
                "done": 1,
                "unchecked": 2,
                "warnings": 0,
            },
            "warnings": [],
        }
        draft = {
            "p0": [
                {**snapshot["goals"]["items"]["p0"][1]},
                {**snapshot["goals"]["items"]["p0"][0]},
                {
                    "done": False,
                    "checked": False,
                    "checkbox": "[ ]",
                    "text": "Add browser-local goal draft",
                    "note": "",
                    "line_number": 0,
                    "lineNumber": 0,
                    "line": 0,
                },
            ],
            "p1": [
                {**snapshot["goals"]["items"]["p1"][0], "note": "Editable drafts preserve line metadata"},
            ],
        }

        normalized, draft_summary = _run_adapter_harness(
            [
                {"kind": "snapshot", "data": snapshot},
                {"kind": "call", "name": "buildGoalDraftSummary", "args": [snapshot["goals"]["items"], draft]},
            ]
        )

        self.assertEqual(4, normalized["goals"]["p0"][0]["lineNumber"])
        self.assertTrue(normalized["goals"]["p0"][0]["checked"])
        self.assertEqual(5, normalized["goals"]["p0"][1]["lineNumber"])
        self.assertFalse(normalized["goals"]["p0"][1]["checked"])
        self.assertEqual(8, normalized["goals"]["p1"][0]["lineNumber"])
        self.assertFalse(normalized["goals"]["p1"][0]["checked"])

        self.assertTrue(draft_summary["dirty"])
        self.assertEqual(1, draft_summary["added"])
        self.assertEqual(1, draft_summary["edited"])
        self.assertEqual(2, draft_summary["moved"])
        self.assertEqual(0, draft_summary["removed"])

        moved = next(row for row in draft_summary["rows"] if row["kind"] == "moved" and row["base"]["lineNumber"] == 4)
        self.assertEqual(4, moved["item"]["lineNumber"])
        self.assertEqual("[x]", moved["item"]["checkbox"])
        self.assertEqual("Expose read-only progress views", moved["item"]["text"])

        edited = next(row for row in draft_summary["rows"] if row["kind"] == "edited")
        self.assertEqual(8, edited["base"]["lineNumber"])
        self.assertEqual("[ ]", edited["base"]["checkbox"])
        self.assertEqual("Editable drafts preserve line metadata", edited["item"]["note"])
        self.assertEqual(8, edited["item"]["lineNumber"])
        self.assertEqual("[ ]", edited["item"]["checkbox"])

        added = next(row for row in draft_summary["rows"] if row["kind"] == "added")
        self.assertEqual(0, added["item"]["lineNumber"])
        self.assertEqual("[ ]", added["item"]["checkbox"])
        self.assertEqual("Add browser-local goal draft", added["item"]["text"])

    def test_adapter_builds_goal_save_risk_summary_and_normalizes_save_response(self) -> None:
        snapshot_goals = {
            "p0": [
                {
                    "done": False,
                    "checked": False,
                    "checkbox": "[ ]",
                    "text": "Keep the launch blocker",
                    "note": "",
                    "lineNumber": 4,
                    "line_number": 4,
                    "line": 4,
                },
                {
                    "done": False,
                    "checked": False,
                    "checkbox": "[ ]",
                    "text": "Preserve backup safety",
                    "note": "",
                    "lineNumber": 5,
                    "line_number": 5,
                    "line": 5,
                },
                {
                    "done": True,
                    "checked": True,
                    "checkbox": "[x]",
                    "text": "Expose read-only progress views",
                    "note": "",
                    "lineNumber": 6,
                    "line_number": 6,
                    "line": 6,
                },
            ],
            "p1": [
                {
                    "done": False,
                    "checked": False,
                    "checkbox": "[ ]",
                    "text": "Keep browser-local edits",
                    "note": "",
                    "lineNumber": 9,
                    "line_number": 9,
                    "line": 9,
                }
            ],
        }
        draft_goals = {
            "p0": [dict(snapshot_goals["p0"][2])],
            "p1": [
                dict(snapshot_goals["p1"][0]),
                dict(snapshot_goals["p0"][1]),
            ],
        }
        backup_path = ".doc/GOALS.20260426-120000-000000Z.bak.md"
        responses = _run_adapter_harness(
            [
                {"kind": "call", "name": "buildGoalSaveRiskSummary", "args": [snapshot_goals, draft_goals]},
                {
                    "kind": "call",
                    "name": "normalizeGoalSaveResponse",
                    "args": [
                        {
                            "ok": False,
                            "action": "goals-save",
                            "status": "error",
                            "message": "Deleting or downgrading unmet P0 goals requires the exact confirmation phrase.",
                            "error": {
                                "code": "goals_confirmation_required",
                                "message": "Deleting or downgrading unmet P0 goals requires the exact confirmation phrase.",
                                "details": {
                                    "backup_path": backup_path,
                                    "confirmation_phrase": "DELETE OR DOWNGRADE UNMET P0 GOALS",
                                    "risk": {
                                        "requires_confirmation": True,
                                        "confirmation_phrase": "DELETE OR DOWNGRADE UNMET P0 GOALS",
                                        "deleted_unchecked_p0": [snapshot_goals["p0"][0]],
                                        "downgraded_unchecked_p0": [snapshot_goals["p0"][1]],
                                        "risk_count": 2,
                                    },
                                },
                            },
                        }
                    ],
                },
                {"kind": "call", "name": "createBlankGoalSaveState", "args": []},
                {"kind": "call", "name": "goalSaveRequestPath", "args": []},
            ]
        )

        risk, normalized, blank_state, request_path = responses
        self.assertTrue(risk["requiresConfirmation"])
        self.assertEqual(2, risk["riskCount"])
        self.assertEqual(1, len(risk["deletedUncheckedP0"]))
        self.assertEqual(1, len(risk["downgradedUncheckedP0"]))
        self.assertEqual("Keep the launch blocker", risk["deletedUncheckedP0"][0]["text"])
        self.assertEqual("Preserve backup safety", risk["downgradedUncheckedP0"][0]["text"])
        self.assertTrue(normalized["error"]["code"].startswith("goals_confirmation"))
        self.assertEqual(backup_path, normalized["backupPath"])
        self.assertEqual(2, normalized["risk"]["riskCount"])
        self.assertEqual("DELETE OR DOWNGRADE UNMET P0 GOALS", normalized["risk"]["confirmationPhrase"])
        self.assertEqual("/api/goals/save", request_path)
        self.assertEqual("/api/goals/save", blank_state["requestPath"])
        self.assertEqual("DELETE OR DOWNGRADE UNMET P0 GOALS", blank_state["risk"]["confirmationPhrase"])

    def test_adapter_normalizes_string_config_values_for_schema_fields(self) -> None:
        snapshot = _make_no_run_snapshot()
        snapshot["config"]["data"] = {
            "roles": "PM,Dev,QA",
            "telegram": {"enabled": "false"},
            "budgets": {"max_dev_continuations_per_task": "7"},
        }

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": snapshot}])[0]

        self.assertEqual(["PM", "Dev", "QA"], normalized["config"]["roles"])
        self.assertFalse(normalized["config"]["telegram"]["enabled"])
        self.assertEqual(7, normalized["config"]["budgets"]["max_dev_continuations_per_task"])

    def test_goal_save_helpers_round_trip_notes_and_required_sections(self) -> None:
        from agent_runner.web import _goal_save_has_required_sections, _goal_save_serialize_draft, _parse_goal_items_and_warnings

        raw_text = """# Project Goals

## P0
- [ ] Keep the release checklist stable
<!-- goal-note: "Escalate if this slips" -->

## P1
- [x] Draft the follow-up note
"""

        items, warnings = _parse_goal_items_and_warnings(raw_text)
        self.assertTrue(_goal_save_has_required_sections(raw_text))
        self.assertEqual([], warnings)
        self.assertEqual("Escalate if this slips", items["p0"][0]["note"])

        serialized = _goal_save_serialize_draft(items)
        self.assertIn('<!-- goal-note: "Escalate if this slips" -->', serialized)
        round_trip_items, round_trip_warnings = _parse_goal_items_and_warnings(serialized)
        self.assertEqual("Keep the release checklist stable", round_trip_items["p0"][0]["text"])
        self.assertEqual("Escalate if this slips", round_trip_items["p0"][0]["note"])
        self.assertEqual([], round_trip_warnings)
        self.assertFalse(_goal_save_has_required_sections("# Project Goals\n\n## P0\n- [ ] Missing P1\n"))
        self.assertFalse(_goal_save_has_required_sections("# Project Goals\n\n## P1\n- [ ] Missing P0\n"))

    def test_parse_goals_completion_requires_priority_sections_and_reports_warnings(self) -> None:
        from agent_runner.goals import parse_goals_completion

        def check(
            *,
            label: str,
            level: str,
            text: str,
            valid: bool,
            project_complete: bool,
            missing_sections: list[str],
            p0_complete: bool,
            p1_complete: bool,
            p0_total: int,
            p1_total: int,
            all_total: int,
            warning_reason: str | None = None,
            warning_count: int = 0,
        ) -> None:
            status = parse_goals_completion(text, completion_level=level)
            self.assertTrue(status["has_goals"], label)
            self.assertEqual(valid, status["valid"], label)
            self.assertEqual(project_complete, status["project_complete"], label)
            self.assertEqual(missing_sections, status["missing_sections"], label)
            self.assertEqual(p0_complete, status["p0_complete"], label)
            self.assertEqual(p1_complete, status["p1_complete"], label)
            self.assertEqual(p0_total, status["p0_total"], label)
            self.assertEqual(p1_total, status["p1_total"], label)
            self.assertEqual(all_total, status["all_total"], label)
            self.assertEqual(warning_count, len(status["warnings"]), label)
            if warning_reason is not None:
                self.assertIn(warning_reason, [warning["reason"] for warning in status["warnings"]], label)

        missing_p0 = """# Project Goals

## P1
- [x] Follow through
"""
        check(
            label="missing-p0 / p0",
            level="p0",
            text=missing_p0,
            valid=False,
            project_complete=False,
            missing_sections=["p0"],
            p0_complete=False,
            p1_complete=False,
            p0_total=0,
            p1_total=1,
            all_total=1,
        )
        check(
            label="missing-p0 / p1",
            level="p1",
            text=missing_p0,
            valid=False,
            project_complete=False,
            missing_sections=["p0"],
            p0_complete=False,
            p1_complete=False,
            p0_total=0,
            p1_total=1,
            all_total=1,
        )
        check(
            label="missing-p0 / all",
            level="all",
            text=missing_p0,
            valid=False,
            project_complete=False,
            missing_sections=["p0"],
            p0_complete=False,
            p1_complete=False,
            p0_total=0,
            p1_total=1,
            all_total=1,
        )

        missing_p1 = """# Project Goals

## P0
- [x] Keep launch moving
"""
        check(
            label="missing-p1 / p0",
            level="p0",
            text=missing_p1,
            valid=True,
            project_complete=True,
            missing_sections=[],
            p0_complete=True,
            p1_complete=False,
            p0_total=1,
            p1_total=0,
            all_total=1,
        )
        check(
            label="missing-p1 / p1",
            level="p1",
            text=missing_p1,
            valid=False,
            project_complete=False,
            missing_sections=["p1"],
            p0_complete=True,
            p1_complete=False,
            p0_total=1,
            p1_total=0,
            all_total=1,
        )
        check(
            label="missing-p1 / all",
            level="all",
            text=missing_p1,
            valid=False,
            project_complete=False,
            missing_sections=["p1"],
            p0_complete=True,
            p1_complete=False,
            p0_total=1,
            p1_total=0,
            all_total=1,
        )

        malformed_heading = """# Project Goals

## P 0
- [x] Keep launch moving

## P1
- [x] Follow through
"""
        check(
            label="malformed-heading / all",
            level="all",
            text=malformed_heading,
            valid=False,
            project_complete=False,
            missing_sections=["p0"],
            p0_complete=False,
            p1_complete=False,
            p0_total=0,
            p1_total=1,
            all_total=2,
            warning_reason="malformed_priority_section_heading",
            warning_count=2,
        )

        malformed_but_complete = """# Project Goals

## P 0
- [x] Keep launch moving

## P0
- [x] Keep launch moving

## P1
- [x] Follow through
"""
        check(
            label="malformed-complete / p0",
            level="p0",
            text=malformed_but_complete,
            valid=False,
            project_complete=False,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=3,
            warning_reason="malformed_priority_section_heading",
            warning_count=2,
        )
        check(
            label="malformed-complete / p1",
            level="p1",
            text=malformed_but_complete,
            valid=False,
            project_complete=False,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=3,
            warning_reason="malformed_priority_section_heading",
            warning_count=2,
        )
        check(
            label="malformed-complete / all",
            level="all",
            text=malformed_but_complete,
            valid=False,
            project_complete=False,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=3,
            warning_reason="malformed_priority_section_heading",
            warning_count=2,
        )

        outside_items = """# Project Goals

- [x] Outside item

## P0
- [x] Keep launch moving

## P1
- [x] Follow through
"""
        check(
            label="outside-items / p0",
            level="p0",
            text=outside_items,
            valid=True,
            project_complete=True,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=3,
            warning_reason="checkbox_outside_priority_section",
            warning_count=1,
        )
        check(
            label="outside-items / p1",
            level="p1",
            text=outside_items,
            valid=True,
            project_complete=True,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=3,
            warning_reason="checkbox_outside_priority_section",
            warning_count=1,
        )
        check(
            label="outside-items / all",
            level="all",
            text=outside_items,
            valid=True,
            project_complete=True,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=3,
            warning_reason="checkbox_outside_priority_section",
            warning_count=1,
        )

        valid_text = """# Project Goals

## P0
- [x] Keep launch moving

## P1
- [x] Follow through
"""
        check(
            label="valid / p0",
            level="p0",
            text=valid_text,
            valid=True,
            project_complete=True,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=2,
        )
        check(
            label="valid / p1",
            level="p1",
            text=valid_text,
            valid=True,
            project_complete=True,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=2,
        )
        check(
            label="valid / all",
            level="all",
            text=valid_text,
            valid=True,
            project_complete=True,
            missing_sections=[],
            p0_complete=True,
            p1_complete=True,
            p0_total=1,
            p1_total=1,
            all_total=2,
        )

    def test_goals_payload_surfaces_missing_sections_and_invalid_state(self) -> None:
        from agent_runner.web import _build_goals_payload

        repo = self._tmp / "goals-payload-repo"
        repo.mkdir(parents=True, exist_ok=True)
        _write(
            repo / ".doc" / "GOALS.md",
            """# Project Goals

## P 0
- [x] Keep launch moving

## P1
- [x] Follow through
""",
        )

        payload = _build_goals_payload(repo, completion_level="all")
        self.assertFalse(payload["completion"]["valid"])
        self.assertEqual(["p0"], payload["completion"]["missing_sections"])
        self.assertEqual(2, len(payload["completion"]["warnings"]))
        self.assertFalse(payload["summary"]["valid"])
        self.assertEqual(["p0"], payload["summary"]["missing_sections"])
        self.assertGreaterEqual(len(payload["warnings"]), 1)

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": {**_make_no_run_snapshot(), "goals": payload}}])[0]
        self.assertEqual("partial", normalized["sectionState"]["goals"]["status"])
        self.assertEqual(["p0"], normalized["goalsCompletion"]["missing_sections"])
        self.assertFalse(normalized["goalsCompletion"]["valid"])

        _write(
            repo / ".doc" / "GOALS.md",
            """# Project Goals

## P 0
- [x] Keep launch moving

## P0
- [x] Keep launch moving

## P1
- [x] Follow through
""",
        )

        malformed_complete = _build_goals_payload(repo, completion_level="all")
        self.assertFalse(malformed_complete["completion"]["valid"])
        self.assertEqual([], malformed_complete["completion"]["missing_sections"])
        self.assertEqual(2, len(malformed_complete["completion"]["warnings"]))
        self.assertFalse(malformed_complete["summary"]["valid"])
        self.assertEqual([], malformed_complete["summary"]["missing_sections"])

        normalized_complete = _run_adapter_harness(
            [{"kind": "snapshot", "data": {**_make_no_run_snapshot(), "goals": malformed_complete}}]
        )[0]
        self.assertEqual("partial", normalized_complete["sectionState"]["goals"]["status"])
        self.assertEqual([], normalized_complete["goalsCompletion"]["missing_sections"])
        self.assertFalse(normalized_complete["goalsCompletion"]["valid"])

    def test_goals_completion_level_is_shared_across_shell_runner_and_web(self) -> None:
        from contextlib import redirect_stdout
        from io import StringIO

        from agent_runner.goals import (
            parse_goals_completion,
            resolve_goals_completion_level,
            should_attempt_goals_refresh,
            update_goals_checkboxes,
        )
        from agent_runner.shell import RunnerShell
        from agent_runner.utils import STOP_REASON_PROJECT_COMPLETE

        raw_text = """# Project Goals

## P0
- [x] Keep read-only progress views

## P1
- [ ] Add FastAPI web console
"""
        _write(self.goals_path, raw_text)

        self.assertEqual("all", resolve_goals_completion_level("bogus"))

        expected_by_level = {
            "p0": True,
            "p1": False,
            "all": False,
        }
        for level, expected in expected_by_level.items():
            with self.subTest(level=level):
                config_path = self.home / "configs" / f"goals-{level}.json"
                _write_config(config_path, self.repo, goals_completion_level=level)

                parsed = parse_goals_completion(raw_text, completion_level=level)
                self.assertEqual(expected, parsed["project_complete"])
                self.assertEqual(level, resolve_goals_completion_level(level))

                should_refresh, why = should_attempt_goals_refresh(
                    self.repo,
                    STOP_REASON_PROJECT_COMPLETE,
                    0,
                    3,
                    True,
                    completion_level=level,
                )
                self.assertEqual(expected, should_refresh)
                self.assertEqual("ok" if expected else "goals_incomplete", why)

                update_result = update_goals_checkboxes(
                    self.repo,
                    ["unrelated task"],
                    ["unrelated prompt"],
                    completion_level=level,
                )
                self.assertFalse(update_result["updated"])
                self.assertEqual(expected, update_result["new_status"]["project_complete"])

                shell = RunnerShell(initial_argv=["--repo", self.repo.as_posix(), "--config", config_path.as_posix()])
                self.assertEqual(level, shell.effective()["goals_completion_level"])

                status_buffer = StringIO()
                with redirect_stdout(status_buffer):
                    shell.status()
                status_output = status_buffer.getvalue()
                self.assertIn(f"goals_completion_level: {level}", status_output)

                doctor_buffer = StringIO()
                with redirect_stdout(doctor_buffer):
                    shell.doctor()
                doctor_output = doctor_buffer.getvalue()
                self.assertIn(f"goals_completion_level: {level}", doctor_output)
                self.assertIn(f"project_complete: {expected}", doctor_output)

                from fastapi.testclient import TestClient

                client = TestClient(self._create_app(self.repo, config_path=config_path))
                status_payload = client.get("/api/status").json()
                self.assertEqual(expected, status_payload["goals"]["completion"]["project_complete"])
                self.assertEqual(expected, status_payload["progress"]["goals_complete"])
                goals_payload = client.get("/api/goals").json()
                self.assertEqual(expected, goals_payload["completion"]["project_complete"])

    def test_adapter_normalizes_config_contract_shape_and_redaction(self) -> None:
        snapshot = _make_no_run_snapshot()
        snapshot["config_contract"] = {
            "path": "config/agentcli.json",
            "source": "api",
            "resolved_prompts_dir": "prompts/agentcli",
            "values": {
                "repo": "C:/Dev/AgentCLI",
                "profile": "personal",
                "execution_backend": "codex",
                "roles": ["PM", "Dev", "QA"],
                "autopilot": True,
                "continuous": True,
                "iterations": 4,
                "prompts_dir": "prompts/agentcli",
                "pm_model": "gpt-5.5",
                "dev_model": "gpt-5.4",
                "dev_model_tier1": "gpt-5.4-mini",
                "dev_model_tier2": "gpt-5.1",
                "qa_model": "gpt-5.4-mini",
                "reporter_model": "gpt-5.4-mini",
                "quota_five_hour_max_utilization": 80,
                "quota_seven_day_max_utilization": 90,
                "telegram": {
                    "bot_token": "[redacted]",
                    "instance_name": "agentcli",
                },
                "goals_completion_level": "all",
            },
            "defaults": {
                "repo": "",
                "profile": "personal",
                "execution_backend": "codex",
                "roles": ["PM", "Dev", "QA"],
                "autopilot": True,
                "continuous": True,
                "iterations": 1,
                "prompts_dir": "prompts/agentcli",
                "pm_model": "gpt-5.5",
                "dev_model": "gpt-5.4",
                "dev_model_tier1": "gpt-5.4-mini",
                "dev_model_tier2": "gpt-5.1",
                "qa_model": "gpt-5.4-mini",
                "reporter_model": "gpt-5.4-mini",
                "telegram": {
                    "bot_token": "[redacted]",
                    "instance_name": "home-pc-main",
                },
                "goals_completion_level": "all",
            },
            "schema": {
                "repo": {
                    "path": "repo",
                    "kind": "text",
                    "label": "Repository",
                    "group": "project",
                    "desc": "Repository root the runner targets.",
                    "hint": "Set automatically from the repo the server serves.",
                    "restart": True,
                    "editable": True,
                    "redacted": False,
                    "allow_empty": False,
                },
                "prompts_dir": {
                    "path": "prompts_dir",
                    "kind": "text",
                    "label": "Prompts directory",
                    "group": "prompts",
                    "restart": True,
                    "editable": True,
                    "redacted": False,
                    "allow_empty": True,
                },
                "telegram.bot_token": {
                    "path": "telegram.bot_token",
                    "kind": "text",
                    "label": "Bot token",
                    "group": "telegram",
                    "restart": True,
                    "editable": True,
                    "redacted": True,
                    "allow_empty": True,
                },
            },
            "groups": [
                {"id": "prompts", "title": "Prompt Paths", "paths": ["prompts_dir"]},
                {"id": "telegram", "title": "Telegram", "paths": ["telegram.bot_token"]},
            ],
            "redaction": {
                "placeholder": "[redacted]",
                "paths": ["telegram.bot_token"],
                "tokens": ["token"],
            },
            "restart_required_paths": ["repo", "prompts_dir", "telegram.bot_token"],
            "meta": {
                "path": "config/agentcli.json",
                "source": "api",
                "resolved_prompts_dir": "prompts/agentcli",
            },
        }

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": snapshot}])[0]

        self.assertEqual("config/agentcli.json", normalized["configMeta"]["path"])
        self.assertEqual("prompts/agentcli", normalized["configMeta"]["resolved_prompts_dir"])
        self.assertEqual("C:/Dev/AgentCLI", normalized["config"]["repo"])
        self.assertEqual("gpt-5.5", normalized["config"]["pm_model"])
        self.assertEqual("[redacted]", normalized["config"]["telegram"]["bot_token"])
        self.assertEqual("[redacted]", normalized["configDefault"]["telegram"]["bot_token"])
        self.assertEqual("Prompt Paths", normalized["configContract"]["groups"][0]["title"])
        self.assertEqual(["prompts_dir"], normalized["configContract"]["groups"][0]["paths"])
        self.assertIn("telegram.bot_token", normalized["configContract"]["redaction"]["paths"])
        self.assertIn("telegram.bot_token", normalized["configContract"]["restart_required_paths"])

    def test_adapter_preserves_plugin_role_specs_as_editable_controls(self) -> None:
        options = ["PM", "Dev", "QA", "Security"]
        normalized_from_string, normalized_from_array, plugin_kind, malformed_kind, rendered_from_string, rendered_from_array = _run_adapter_harness(
            [
                {"kind": "call", "name": "normalizeRoleSpecs", "args": ["PM, pkg.mod:Class, QA", options]},
                {"kind": "call", "name": "normalizeRoleSpecs", "args": [["PM", "pkg.mod:Class", "QA"], options]},
                {"kind": "call", "name": "classifyRoleSpec", "args": ["pkg.mod:Class", options]},
                {"kind": "call", "name": "classifyRoleSpec", "args": ["bad role", options]},
                {
                    "kind": "call",
                    "name": "renderConfigRolesControl",
                    "args": [
                        {
                            "path": "roles",
                            "options": options,
                            "value": "PM, pkg.mod:Class, QA",
                        }
                    ],
                },
                {
                    "kind": "call",
                    "name": "renderConfigRolesControl",
                    "args": [
                        {
                            "path": "roles",
                            "options": options,
                            "value": ["PM", "pkg.mod:Class", "QA"],
                        }
                    ],
                },
            ]
        )

        self.assertEqual(["PM", "pkg.mod:Class", "QA"], normalized_from_string)
        self.assertEqual(normalized_from_string, normalized_from_array)
        self.assertEqual("plugin", plugin_kind)
        self.assertEqual("invalid", malformed_kind)
        self.assertEqual(rendered_from_string, rendered_from_array)
        self.assertIn('data-config-field="roles"', rendered_from_string)
        self.assertIn('value="PM, pkg.mod:Class, QA"', rendered_from_string)
        self.assertIn('chip--info', rendered_from_string)
        self.assertIn('pkg.mod:Class', rendered_from_string)

    def test_runner_control_start_option_validation_and_preview_are_exposed_to_js(self) -> None:
        from agent_runner.remote.controller import build_runner_start_options_contract

        base_run_dir = self.home / "runs" / "explicit"
        base_args = argparse.Namespace(
            config_path=self.config_path.as_posix(),
            config=self.config_path.as_posix(),
            autopilot=True,
            continuous=True,
            loop=True,
            loop_max_cycles=7,
            profile="enterprise",
            execution_backend="claudecode",
            run_dir=base_run_dir.as_posix(),
            resume_latest=True,
        )
        contract = build_runner_start_options_contract(self.repo, base_args)
        invalid_draft = {
            "autopilot": True,
            "run_mode": "loop",
            "continuous": False,
            "loop": False,
            "one_shot": True,
            "loop_max_cycles": "-1",
            "profile": "bogus",
            "execution_backend": "bogus",
            "config_path": "",
            "run_dir": base_run_dir.as_posix(),
            "resume_latest": True,
        }
        valid_draft = {
            "autopilot": True,
            "run_mode": "loop",
            "continuous": True,
            "loop": True,
            "one_shot": False,
            "loop_max_cycles": "7",
            "profile": "enterprise",
            "execution_backend": "claudecode",
            "config_path": self.config_path.as_posix(),
            "run_dir": base_run_dir.as_posix(),
            "resume_latest": True,
        }

        results = _run_adapter_harness(
            [
                {"kind": "call", "name": "runnerControlStartOptionsValidation", "args": [{"startOptions": contract}, invalid_draft]},
                {"kind": "call", "name": "runnerControlStartOptionsArgvPreview", "args": [{"startOptions": contract}, valid_draft]},
                {
                    "kind": "call",
                    "name": "runnerControlStartOptionCard",
                    "args": [
                        {
                            "path": "run_mode",
                            "label": "Run mode",
                            "currentValue": "loop",
                            "defaultValue": "one-shot",
                            "hint": "Pair with autopilot.",
                            "controlHTML": "<button type=\"button\">loop</button>",
                            "disabled": False,
                            "errors": ["Loop does not match the selected run mode."],
                        }
                    ],
                },
            ]
        )

        validation = results[0]
        self.assertFalse(validation["valid"])
        self.assertEqual(8, validation["errorCount"])
        self.assertIn("Fix the highlighted start options before continuing.", validation["message"])
        self.assertIn("continuous", validation["fieldErrors"])
        self.assertIn("loop", validation["fieldErrors"])
        self.assertIn("one_shot", validation["fieldErrors"])
        self.assertIn("loop_max_cycles", validation["fieldErrors"])
        self.assertIn("profile", validation["fieldErrors"])
        self.assertIn("execution_backend", validation["fieldErrors"])
        self.assertIn("config_path", validation["fieldErrors"])
        self.assertIn("resume_latest", validation["fieldErrors"])

        preview = results[1]
        self.assertEqual(
            [
                "--repo",
                self.repo.as_posix(),
                "--config",
                self.config_path.as_posix(),
                "--autopilot",
                "--continuous",
                "--loop",
                "--resume-latest",
                "--loop-max-cycles",
                "7",
                "--profile",
                "enterprise",
                "--execution-backend",
                "claudecode",
                "--run-dir",
                base_run_dir.as_posix(),
            ],
            preview,
        )

        card_html = results[2]
        self.assertIn("runner-control__option--invalid", card_html)
        self.assertIn("field-error", card_html)

    def test_runner_control_timeout_stop_progress_renders_history_and_retry_state(self) -> None:
        timeout_progress = {
            "phase": "timeout",
            "message": "Runner is still alive after 1s stop wait timeout.",
            "elapsed_seconds": 12,
            "updated_at": "2026-04-28T00:00:12",
            "requested_at": "2026-04-28T00:00:00",
            "history": [
                {
                    "phase": "request",
                    "message": "Stop requested.",
                    "elapsed_seconds": 0,
                    "updated_at": "2026-04-28T00:00:00",
                },
                {
                    "phase": "stop_file_write",
                    "message": "Stop file written: C:/temp/STOP",
                    "elapsed_seconds": 1,
                    "updated_at": "2026-04-28T00:00:01",
                },
                {
                    "phase": "child_termination",
                    "message": "Terminating tracked child processes.",
                    "elapsed_seconds": 2,
                    "updated_at": "2026-04-28T00:00:02",
                },
                {
                    "phase": "runner_wait",
                    "message": "Waiting for runner shutdown and final artifacts.",
                    "elapsed_seconds": 6,
                    "updated_at": "2026-04-28T00:00:06",
                },
                {
                    "phase": "timeout",
                    "message": "Runner is still alive after 1s stop wait timeout.",
                    "elapsed_seconds": 12,
                    "updated_at": "2026-04-28T00:00:12",
                },
            ],
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
        }
        finalized_progress = {
            "phase": "finalized",
            "message": "Runner stop sequence finished.",
            "elapsed_seconds": 16,
            "updated_at": "2026-04-28T00:00:16",
            "requested_at": "2026-04-28T00:00:00",
            "history": [
                {"phase": "request", "message": "Stop requested.", "elapsed_seconds": 0, "updated_at": "2026-04-28T00:00:00"},
                {"phase": "stop_file_write", "message": "Stop file written: C:/temp/STOP", "elapsed_seconds": 1, "updated_at": "2026-04-28T00:00:01"},
                {"phase": "child_termination", "message": "Terminating tracked child processes.", "elapsed_seconds": 2, "updated_at": "2026-04-28T00:00:02"},
                {"phase": "final_artifact_collection", "message": "Collecting final artifacts and logs.", "elapsed_seconds": 15, "updated_at": "2026-04-28T00:00:15"},
                {"phase": "finalized", "message": "Runner stop sequence finished.", "elapsed_seconds": 16, "updated_at": "2026-04-28T00:00:16"},
            ],
            "current_phase": {
                "phase": "finalized",
                "message": "Runner stop sequence finished.",
                "elapsed_seconds": 16,
                "updated_at": "2026-04-28T00:00:16",
                "runner_alive": False,
                "tracked_child_pids": [],
                "tracked_child_processes": [],
                "stop_file_paths": {
                    "stop_file_path": "C:/temp/STOP",
                    "stop_progress_path": "C:/temp/STOP_PROGRESS.json",
                    "stop_progress_log_path": "C:/temp/stop_progress.log",
                },
                "timeout_guidance": {
                    "summary": "Stop sequence finalized.",
                    "recoverable": False,
                    "steps": [],
                    "manual_cleanup_hints": [],
                    "locked_file_paths": [],
                },
            },
            "runner_alive": False,
            "tracked_child_pids": [],
            "tracked_child_processes": [],
            "stop_file_paths": {
                "stop_file_path": "C:/temp/STOP",
                "stop_progress_path": "C:/temp/STOP_PROGRESS.json",
                "stop_progress_log_path": "C:/temp/stop_progress.log",
            },
            "timeout_guidance": {
                "summary": "Stop sequence finalized.",
                "recoverable": False,
                "steps": [],
                "manual_cleanup_hints": [],
                "locked_file_paths": [],
            },
        }

        control = {
            "enabled": True,
            "controllerAvailable": True,
            "busy": False,
            "message": "Runner is stopping.",
            "lastAction": "stop",
            "lastMessage": "",
            "lastError": "",
            "status": {
                "running": True,
                "runnerMode": "thread",
                "repo": self.repo.as_posix(),
                "configPath": self.config_path.as_posix(),
                "runDir": self.run_dir.as_posix(),
                "stopProgress": timeout_progress,
                "reason": "",
                "lastEvent": "2026-04-26T12:08:00 cycle_end",
            },
        }
        finalized_control = {
            **control,
            "message": "Runner stop sequence finished.",
            "status": {
                **control["status"],
                "running": False,
                "stopProgress": finalized_progress,
                "lastEvent": "2026-04-26T12:08:00 cycle_end",
            },
        }

        results = _run_adapter_harness(
            [
                {"kind": "call", "name": "normalizeStopProgress", "args": [timeout_progress]},
                {"kind": "call", "name": "runnerControlStateInfo", "args": [control]},
                {"kind": "call", "name": "runnerControlDetailRows", "args": [control, {"chipTone": "warn", "label": "Stop timed out"}]},
                {"kind": "call", "name": "renderStopProgressSection", "args": [timeout_progress]},
                {"kind": "call", "name": "normalizeStopProgress", "args": [finalized_progress]},
                {"kind": "call", "name": "runnerControlStateInfo", "args": [finalized_control]},
            ]
        )

        normalized_timeout = results[0]
        timeout_display = results[1]
        timeout_rows = results[2]
        timeout_html = results[3]
        normalized_finalized = results[4]
        finalized_display = results[5]

        self.assertEqual("timeout", normalized_timeout["phase"])
        self.assertEqual(["request", "stop_file_write", "child_termination", "runner_wait", "timeout"], [entry["phase"] for entry in normalized_timeout["history"]])
        self.assertTrue(normalized_timeout["runnerAlive"])
        self.assertEqual([321, 654], normalized_timeout["trackedChildPids"])
        self.assertTrue(normalized_timeout["timeoutGuidance"]["canRetry"])
        self.assertEqual(["Close the runner."], normalized_timeout["manualCleanupHints"])
        self.assertEqual(["C:/temp/locked.txt"], normalized_timeout["lockedFilePaths"])
        self.assertEqual("warn", timeout_display["chipTone"])
        self.assertEqual("Stop timed out", timeout_display["label"])
        self.assertEqual("Stop timed out", timeout_display["title"])
        self.assertIn("Retry stop", timeout_display["copy"])
        self.assertIn("stop-progress__history-item--current", timeout_html)
        self.assertIn("Phase history", timeout_html)
        self.assertIn("Remaining tracked PIDs", timeout_html)
        self.assertIn("Manual cleanup hints", timeout_html)
        self.assertIn("Locked file paths", timeout_html)

        timeout_labels = {row["label"] for row in timeout_rows}
        self.assertIn("Current phase", timeout_labels)
        self.assertIn("Phase history", timeout_labels)
        self.assertIn("Runner process", timeout_labels)
        self.assertIn("Remaining tracked PIDs", timeout_labels)
        self.assertIn("Stop file paths", timeout_labels)
        self.assertIn("Last artifact write", timeout_labels)
        self.assertIn("Last log write", timeout_labels)
        self.assertIn("Timeout guidance", timeout_labels)
        self.assertIn("Manual cleanup hints", timeout_labels)
        self.assertIn("Locked file paths", timeout_labels)

        self.assertEqual("finalized", normalized_finalized["phase"])
        self.assertEqual(["request", "stop_file_write", "child_termination", "final_artifact_collection", "finalized"], [entry["phase"] for entry in normalized_finalized["history"]])
        self.assertFalse(normalized_finalized["timeoutGuidance"]["canRetry"])
        self.assertEqual("success", finalized_display["chipTone"])
        self.assertEqual("Stopped", finalized_display["label"])
        self.assertEqual("Action complete", finalized_display["title"])
        self.assertIn("Runner stop sequence finished.", finalized_display["copy"])

    def test_fast_web_worktree_regression_scope_detection_requires_repo_markers(self) -> None:
        from agent_runner.gates import repo_has_web_worktree_markers, should_run_fast_web_worktree_regression

        self.assertFalse(repo_has_web_worktree_markers(self.repo))
        self.assertFalse(should_run_fast_web_worktree_regression(self.repo, ["web_console/app.js"]))

        (self.repo / "agent_runner").mkdir(parents=True, exist_ok=True)
        (self.repo / "web_console").mkdir(parents=True, exist_ok=True)
        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

## P0
- [ ] Keep self-development runs on the fast regression suite.
""",
        )

        self.assertTrue(repo_has_web_worktree_markers(self.repo))
        self.assertTrue(should_run_fast_web_worktree_regression(self.repo, ["web_console/app.js"]))
        self.assertTrue(should_run_fast_web_worktree_regression(self.repo, ["tests/test_web_console_readonly.py"]))
        self.assertTrue(
            should_run_fast_web_worktree_regression(
                self.repo,
                [(self.repo / "tests" / "test_worktree_manual_merge.py").as_posix()],
            )
        )
        self.assertFalse(should_run_fast_web_worktree_regression(self.repo, ["docs/notes.md"]))


if __name__ == "__main__":
    unittest.main()
