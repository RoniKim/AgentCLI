from __future__ import annotations

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
    def __init__(self, status: dict[str, object]) -> None:
        self._status = dict(status)
        run_dir = self._status.get("run_dir")
        self.run_dir = Path(str(run_dir)).expanduser().resolve() if run_dir else None

    def status(self) -> dict[str, object]:
        return dict(self._status)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


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
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "tasks").mkdir(parents=True, exist_ok=True)
    (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)

    backlog_task_status = "done" if status == "success" else ("failed" if status == "failed" else "in_progress")
    secondary_task_id = "T-021"
    task_files = ["agent_runner/web.py", "web_console/app.js"]
    if status == "failed":
        task_files.append("tests/test_web_console_readonly.py")
    _write(
        run_dir / "BACKLOG.json",
        json.dumps(
            {
                "generated_at": "2026-04-26T12:00:00",
                "tasks": [
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
                        "id": secondary_task_id,
                        "title": "Backlog follows lifecycle records",
                        "prompt": "Keep the backlog view aligned with lifecycle artifacts.",
                        "files": ["web_console/app.js", "web_console/styles.css"],
                        "done_when": "Dependency, attempt, file scope, and failure information render in the browser.",
                        "skills": ["ui"],
                        "skills_rationale": "Keep the backlog panel readable.",
                        "depends_on": [task_id],
                        "status": "pending",
                    },
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    state_payload: dict[str, object] = {"done": [], "failed": [], "warnings": []}
    if status == "success":
        state_payload["done"] = [task_id]
    elif status == "failed":
        state_payload["failed"] = [
            {"task": task_id, "reason": final_reason, "detail": "Dev attempt 2 failed during the build step.", "attempt": 2, "cycle": 1, "step": 0, "rc": final_rc}
        ]
    _write(run_dir / "STATE.json", json.dumps(state_payload, ensure_ascii=False, indent=2) + "\n")

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
        {"ts": "2026-04-26T12:08:00", "seq": 11, "level": "info", "event": "cycle_end", "stage": "Dev", "cycle": cycle, "rc": 0 if status == "success" else final_rc, "done": 1 if status == "success" else 0, "total": 2, "failed": 1 if status == "failed" else 0, "duration_seconds": 480, "message": "cycle end"}
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
                "total_tasks": 2,
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
    _write(run_dir / "cycle_summary.log", f"2026-04-26T12:08:00 cycle=1 done={1 if status == 'success' else 0}/2 failed={1 if status == 'failed' else 0} dt=480.0s\n")
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
          throw new Error('Unknown fixture kind: ' + fixture.kind);
        });
        process.stdout.write(JSON.stringify(results));
        """
    ).replace("__SOURCE_PATH__", json.dumps(str(WEB_CONSOLE / "app.js"))).replace(
        "__FIXTURES__", json.dumps(fixtures, ensure_ascii=False)
    )
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


class WebConsoleReadonlyTests(unittest.TestCase):
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
        self.empty_repo = self._tmp / "empty-repo"
        self.empty_repo.mkdir(parents=True, exist_ok=True)

        self.home = self._tmp / "home"
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

        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        self.app = create_app(self.repo, web_dir=WEB_CONSOLE)
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
        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        controller = FakeRunnerController(controller_status) if controller_status is not None else None
        with patch.object(web_module, "_build_runner_controller", return_value=controller):
            client = TestClient(create_app(repo, web_dir=WEB_CONSOLE))
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
        self.assertEqual("success", payload["progress"]["run_status"])
        self.assertEqual("success", payload["active_run"]["status"])
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

    def test_empty_latest_timestamp_run_dir_does_not_mask_real_run(self) -> None:
        empty_run = self.repo / ".AgentCLI" / "agent_runs" / "20260426-130000"
        empty_run.mkdir(parents=True, exist_ok=True)

        from agent_runner.web import build_snapshot

        payload = build_snapshot(self.repo, runner_controller_auto_build=False)

        self.assertEqual("20260426-120000", Path(payload["latest_run_dir"]).name)
        self.assertEqual("20260426-120000", payload["active_run"]["id"])
        self.assertEqual("success", payload["progress"]["run_status"])

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
        self.assertEqual({"window": "5h", "used": None, "available": False}, no_run["active_run"]["quota"])
        self.assertIsNone(no_run["active_run"]["budgetUsed"])

        live_run_dir = self._make_live_run_dir("20260426-110000")
        later_run_dir = self._make_live_run_dir("20260426-140000")
        _write_run_bundle(later_run_dir, status="success", final_rc=0, final_reason="project_complete", branch="main")
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
            ),
        )
        self.assertEqual(live_run_dir.resolve().as_posix(), live["latest_run_dir"])
        self.assertIn("20260426-140000", {item["id"] for item in live["history"]["items"]})
        self.assertEqual("running", live["progress"]["run_status"])
        self.assertEqual("running", live["active_run"]["status"])
        self.assertEqual("feature/live-run", live["active_run"]["branch"])
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
        self.assertEqual({"window": "5h", "used": None, "available": False}, live["active_run"]["quota"])
        self.assertIsNone(live["active_run"]["budgetUsed"])

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
                quota={"window": "5h", "used": 0.33},
                budget_used=0.33,
            ),
        )
        self.assertEqual(success_run_dir.resolve().as_posix(), success["latest_run_dir"])
        self.assertEqual("success", success["progress"]["run_status"])
        self.assertEqual("success", success["active_run"]["status"])
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
        self.assertEqual({"window": "5h", "used": 0.33, "available": True}, success["active_run"]["quota"])
        self.assertEqual(0.33, success["active_run"]["budgetUsed"])
        self.assertEqual(0.33, success["active_run"]["quota"]["used"])

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
        self.assertEqual({"window": "5h", "used": None, "available": False}, stopped["active_run"]["quota"])
        self.assertIsNone(stopped["active_run"]["budgetUsed"])

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
        self.assertEqual({"window": "5h", "used": None, "available": False}, failed["active_run"]["quota"])
        self.assertIsNone(failed["active_run"]["budgetUsed"])
        self.assertEqual(3, len(failed["stages"]))
        self.assertEqual("failed", failed["stages"][2]["status"])
        self.assertEqual("failed", failed["stages"][1]["status"])
        self.assertEqual("T-020", failed["backlog"]["items"][0]["id"])
        self.assertEqual("failed", failed["backlog"]["items"][0]["status"])
        self.assertEqual("build_failed", failed["backlog"]["items"][0]["failure_reason"])
        self.assertEqual("agent_runner/web.py, web_console/app.js, tests/test_web_console_readonly.py", failed["backlog"]["items"][0]["file_scope"])
        self.assertEqual(["T-020"], failed["backlog"]["items"][1]["depends_on"])

    def test_section_endpoints_return_stable_shapes(self) -> None:
        progress = self.client.get("/api/progress").json()
        for key in ("active_run", "stages", "backlog", "goals", "logs", "config", "prompts", "history", "metrics", "notifications", "worktree", "state"):
            self.assertIn(key, progress)
        self.assertEqual(1, progress["tasks_done"])
        self.assertEqual(2, progress["tasks_total"])
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

        config = self.client.get("/api/config").json()
        self.assertIn("data", config)
        self.assertIn("resolved_prompts_dir", config)

        prompts = self.client.get("/api/prompts").json()
        self.assertIn("items", prompts)
        self.assertGreaterEqual(len(prompts["items"]), 3)

        history = self.client.get("/api/history").json()
        self.assertIn("items", history)
        self.assertGreaterEqual(len(history["items"]), 1)

        worktree = self.client.get("/api/worktree").json()
        self.assertEqual("pending review", worktree["status"])
        self.assertTrue(worktree["changedFiles"])

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
        self.assertEqual("p0", payload["completion_level"])
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
        self.assertEqual("p0", payload["completion_level"])
        self.assertEqual(0, payload["summary"]["total"])
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
            self.assertEqual({"window": "5h", "used": None, "available": False}, no_run["activeRun"]["quota"])
            self.assertIsNone(no_run["activeRun"]["budgetUsed"])

        with self.subTest("partial-run"):
            self.assertEqual("partial", partial["sectionState"]["stages"]["status"])
            self.assertEqual("Only some lifecycle records were published.", partial["sectionState"]["stages"]["message"])
            self.assertEqual(2, len(partial["stages"]))
            self.assertEqual("T-020", partial["stages"][0]["taskId"])
            self.assertEqual(2, partial["backlog"][0]["attempt"])
            self.assertEqual("agent_runner/web.py, web_console/app.js", partial["backlog"][0]["fileScope"])
            self.assertEqual(120, len(partial["logs"]))
            self.assertEqual("running", partial["activeRun"]["status"])

        with self.subTest("normal-run"):
            self.assertEqual("api", normal["sourceMode"])
            self.assertEqual("ready", normal["sectionState"]["stages"]["status"])
            self.assertEqual("ready", normal["sectionState"]["backlog"]["status"])
            self.assertEqual("ready", normal["sectionState"]["worktree"]["status"])
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
            self.assertEqual("pending", normal["worktreeMerge"]["status"])

        with self.subTest("fallback-fixture"):
            self.assertEqual("fallback", fallback["sourceMode"])
            self.assertEqual("Fallback data", fallback["snapshotLabel"])
            self.assertEqual("no-run", fallback["activeRun"]["id"])
            self.assertEqual("idle", fallback["activeRun"]["status"])
            self.assertEqual([], fallback["stages"])
            self.assertEqual([], fallback["backlog"])
            self.assertEqual("empty", fallback["sectionState"]["activeRun"]["status"])
            self.assertEqual("empty", fallback["sectionState"]["stages"]["status"])
            self.assertEqual("empty", fallback["sectionState"]["backlog"]["status"])

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

    def test_adapter_normalizes_string_config_values_for_schema_fields(self) -> None:
        snapshot = _make_no_run_snapshot()
        snapshot["config"]["data"] = {
            "roles": "PM,Dev,QA",
            "telegram": {"enabled": "false"},
            "budget": {"max_iters": "7"},
        }

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": snapshot}])[0]

        self.assertEqual(["PM", "Dev", "QA"], normalized["config"]["roles"])
        self.assertFalse(normalized["config"]["telegram"]["enabled"])
        self.assertEqual(7, normalized["config"]["budget"]["max_iters"])


if __name__ == "__main__":
    unittest.main()
