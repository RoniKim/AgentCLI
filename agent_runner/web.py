from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import (
    default_config_path,
    default_prompts_dir,
    legacy_default_config_path,
    load_config,
    resolve_config_path,
    resolve_prompts_dir,
)
from .goals import parse_goals_completion, read_goals
from .gitops import find_pending_worktree_merge, git_head, read_pending_worktree_merge
from .prompts import (
    DEV_INSTRUCTIONS_DEFAULT,
    DEV_TASK_TEMPLATE_DEFAULT,
    PM_BOOTSTRAP_TEMPLATE_DEFAULT,
    PM_INCREMENTAL_TEMPLATE_DEFAULT,
    PM_INSTRUCTIONS_DEFAULT,
    PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT,
    PromptStore,
    QA_INSTRUCTIONS_DEFAULT,
    QA_TEMPLATE_DEFAULT,
    REPORTER_INSTRUCTIONS_DEFAULT,
)
from .run_dir import find_latest_run_dir
from .remote.controller import RunnerController
from .state import TaskItem, load_backlog_json, load_state, parse_backlog_md
from .utils import now_iso, run_cmd

try:  # Optional dependency: the app must still import when FastAPI is absent.
    from fastapi import Body, FastAPI, HTTPException, Request
    from fastapi.responses import FileResponse, JSONResponse
except Exception:  # pragma: no cover - exercised in dependency-missing environments
    Body = None  # type: ignore[assignment]
    FastAPI = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    FileResponse = None  # type: ignore[assignment]
    JSONResponse = None  # type: ignore[assignment]

try:  # Optional dependency for the run helper.
    import uvicorn
except Exception:  # pragma: no cover - exercised in dependency-missing environments
    uvicorn = None  # type: ignore[assignment]


PROMPT_SPECS: list[dict[str, str]] = [
    {
        "id": "pm_instructions",
        "file": "pm_instructions.md",
        "scope": "PM",
        "default": PM_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "dev_instructions",
        "file": "dev_instructions.md",
        "scope": "Dev",
        "default": DEV_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "qa_instructions",
        "file": "qa_instructions.md",
        "scope": "QA",
        "default": QA_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "pm_bootstrap",
        "file": "pm_bootstrap_prompt.md",
        "scope": "PM",
        "default": PM_BOOTSTRAP_TEMPLATE_DEFAULT,
    },
    {
        "id": "pm_incremental",
        "file": "pm_incremental_prompt.md",
        "scope": "PM",
        "default": PM_INCREMENTAL_TEMPLATE_DEFAULT,
    },
    {
        "id": "dev_task",
        "file": "dev_task_prompt.md",
        "scope": "Dev",
        "default": DEV_TASK_TEMPLATE_DEFAULT,
    },
    {
        "id": "qa_prompt",
        "file": "qa_prompt.md",
        "scope": "QA",
        "default": QA_TEMPLATE_DEFAULT,
    },
    {
        "id": "reporter_instructions",
        "file": "reporter_instructions.md",
        "scope": "Reporter",
        "default": REPORTER_INSTRUCTIONS_DEFAULT,
    },
    {
        "id": "pm_shutdown_report",
        "file": "pm_shutdown_report_prompt.md",
        "scope": "Reporter",
        "default": PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT,
    },
]

STAGE_ORDER = {"pm": 0, "dev": 1, "qa": 2, "security": 3, "reporter": 4}
RUNNER_CONTROL_CONFIRMATIONS = {
    "start": "START RUNNER",
    "stop": "STOP RUNNER",
    "reload": "RELOAD RUNNER",
    "restart": "RESTART RUNNER",
}
RUN_DIR_ARTIFACT_NAMES = {
    "BACKLOG.json",
    "BACKLOG.md",
    "STATE.json",
    "STOP",
    "cycle_summary.log",
    "last_run_summary.json",
    "metrics.jsonl",
    "run_summary.json",
    "WORKTREE_APPLY_FAILURE.md",
    "WORKTREE_MERGE_APPLIED.json",
    "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
    "WORKTREE_MERGE_DISCARDED.json",
    "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json",
    "WORKTREE_MERGE_PENDING.json",
    "WORKTREE_NOT_APPLIED.md",
    "worktree.patch",
}
RUNNER_CONTROL_TRUTHY = {"1", "true", "yes", "on", "enabled"}
RUNNER_CONTROL_FALSY = {"0", "false", "no", "off", "disabled"}
SENSITIVE_CONFIG_TOKENS = {
    "api",
    "apikey",
    "api_key",
    "auth",
    "bearer",
    "bot",
    "chat",
    "client_secret",
    "credential",
    "key",
    "password",
    "secret",
    "session",
    "token",
    "webhook",
}
REDACTED_VALUE = "[redacted]"


def _repo_root(repo: Path | str | None) -> Path:
    if repo is None:
        return Path(__file__).resolve().parents[1]
    return Path(repo).expanduser().resolve()


def buildSectionState(kind: str, rawStatus: str, message: str, source: str = "api") -> dict[str, str]:
    status = str(rawStatus or "ready").strip() or "ready"
    return {
        "kind": str(kind),
        "status": status,
        "message": str(message or ""),
        "source": str(source or "api"),
    }


def fallbackSectionMessage(kind: str) -> str:
    messages = {
        "activeRun": "No active run is published yet.",
        "stages": "No pipeline stages are available yet.",
        "backlog": "Backlog has not been emitted yet.",
        "goals": "No goals were found in GOALS.md.",
        "config": "Config snapshot is incomplete.",
        "prompts": "Prompt inventory is empty.",
        "logs": "No log entries are available yet.",
        "notifications": "No notifications have been recorded yet.",
        "metrics": "No metrics snapshot is available yet.",
        "history": "Run history is empty.",
        "worktree": "No pending worktree merge is available.",
        "runnerControl": "Runner controls are unavailable in fallback mode.",
    }
    return messages.get(kind, "No data available yet.")


def _run_dir_has_observable_artifacts(run_dir: Path | None) -> bool:
    if run_dir is None:
        return False
    try:
        if not run_dir.exists() or not run_dir.is_dir():
            return False
        for name in RUN_DIR_ARTIFACT_NAMES:
            if (run_dir / name).exists():
                return True
        logs_dir = run_dir / "logs"
        if logs_dir.exists() and logs_dir.is_dir():
            return any(item.is_file() for item in logs_dir.iterdir())
    except OSError:
        return False
    return False


def _safe_json(path: Path, fallback: Any) -> Any:
    try:
        if not path.exists():
            return fallback
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return fallback
        payload = json.loads(raw)
        return payload if payload is not None else fallback
    except Exception:
        return fallback


def _safe_jsonl(path: Path, *, max_items: int = 400) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    rows: deque[dict[str, Any]] = deque(maxlen=max(1, int(max_items)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except Exception:
                    continue
                if isinstance(payload, dict):
                    rows.append(payload)
    except Exception:
        return []
    return list(rows)


def _is_sensitive_config_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if not normalized:
        return False
    parts = {part for part in normalized.split("_") if part}
    return normalized in SENSITIVE_CONFIG_TOKENS or bool(parts & SENSITIVE_CONFIG_TOKENS)


def _redact_config(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_config_key(key):
        if isinstance(value, bool) or value in (None, ""):
            return value
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {str(item_key): _redact_config(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _epoch_ms(value: Any) -> int:
    try:
        return int(float(value) * 1000)
    except Exception:
        return 0


def _iso_to_ms(value: Any) -> int:
    raw = str(value or "").strip()
    if not raw:
        return 0
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return 0


def _fmt_clock(value: Any) -> str:
    ms = _epoch_ms(value) or _iso_to_ms(value)
    if not ms:
        return ""
    dt = datetime.fromtimestamp(ms / 1000.0)
    return dt.strftime("%H:%M:%S")


def _fmt_mtime(value: float) -> str:
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "template"


def _tail_text(path: Path, lines: int = 50) -> str:
    if not path.exists() or not path.is_file():
        return ""
    dq: deque[str] = deque(maxlen=max(1, int(lines)))
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                dq.append(line.rstrip("\n"))
    except Exception:
        return ""
    return "\n".join(dq).strip()


def _resolve_runner_controls_enabled(explicit: bool | None = None) -> tuple[bool, str]:
    if explicit is not None:
        return bool(explicit), "cli"

    raw = (os.getenv("AGENTCLI_WEB_RUNNER_CONTROLS") or "").strip().lower()
    if raw in RUNNER_CONTROL_TRUTHY:
        return True, "env:AGENTCLI_WEB_RUNNER_CONTROLS"
    if raw in RUNNER_CONTROL_FALSY:
        return False, "env:AGENTCLI_WEB_RUNNER_CONTROLS"
    return False, "default"


def _runner_control_confirmation(action: str) -> str:
    key = str(action or "").strip().lower()
    return RUNNER_CONTROL_CONFIRMATIONS.get(key, "")


def _runner_control_message(*, enabled: bool, source: str, running: bool, controller_available: bool) -> str:
    if not controller_available:
        return "Runner controller is unavailable."
    if not enabled:
        return "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls."
    if running:
        return f"Runner controls enabled via {source}. Controller reports the runner is running."
    return f"Runner controls enabled via {source}. Controller reports the runner is stopped."


def _runner_control_status_payload(
    controller: RunnerController | None,
    *,
    repo: Path,
    current_run_dir: str = "",
) -> dict[str, Any]:
    if controller is None:
        return {
            "running": False,
            "runner_mode": "unknown",
            "repo": str(repo),
            "run_dir": str(current_run_dir or ""),
            "uptime_seconds": 0,
            "exit_code": None,
            "stop_file": "STOP",
            "stop_file_exists": False,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "reason": "",
            "last_event": "",
        }

    try:
        status = controller.status()
    except Exception as ex:
        return {
            "running": False,
            "runner_mode": "unknown",
            "repo": str(repo),
            "run_dir": str(current_run_dir or ""),
            "uptime_seconds": 0,
            "exit_code": None,
            "stop_file": "STOP",
            "stop_file_exists": False,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "reason": f"status_error: {ex}",
            "last_event": "",
        }

    return {
        "running": bool(status.get("running")),
        "runner_mode": str(status.get("runner_mode") or "thread").strip() or "thread",
        "repo": str(status.get("repo") or repo),
        "run_dir": str(status.get("run_dir") or current_run_dir or ""),
        "uptime_seconds": int(status.get("uptime_seconds") or 0),
        "exit_code": status.get("exit_code"),
        "stop_file": str(status.get("stop_file") or "STOP"),
        "stop_file_exists": bool(status.get("stop_file_exists")),
        "done": int(status.get("done") or 0),
        "failed": int(status.get("failed") or 0),
        "warnings": int(status.get("warnings") or 0),
        "reason": str(status.get("reason") or "").strip(),
        "last_event": str(status.get("last_event") or "").strip(),
    }


def _runner_control_actions(
    enabled: bool,
    status_payload: dict[str, Any],
    *,
    controller_available: bool,
    busy: bool = False,
) -> dict[str, dict[str, Any]]:
    running = bool(status_payload.get("running"))
    disabled_reason = "Runner controls are disabled until the server is started with AGENTCLI_WEB_RUNNER_CONTROLS=1 or --enable-runner-controls."
    controller_reason = "Runner controller is unavailable."
    busy_reason = "A runner control request is already in flight."

    def _action(enabled_flag: bool, reason: str) -> dict[str, Any]:
        return {
            "enabled": bool(enabled_flag and not busy),
            "disabled_reason": reason,
            "busy": bool(busy),
        }

    return {
        "start": _action(
            enabled and controller_available and not running,
            busy_reason if busy else (disabled_reason if not enabled else (controller_reason if not controller_available else ("Runner is already running." if running else ""))),
        ),
        "stop": _action(
            enabled and controller_available and running,
            busy_reason if busy else (disabled_reason if not enabled else (controller_reason if not controller_available else ("Runner is not running." if not running else ""))),
        ),
        "reload": _action(
            enabled and controller_available,
            busy_reason if busy else (disabled_reason if not enabled else (controller_reason if not controller_available else "")),
        ),
        "restart": _action(
            enabled and controller_available,
            busy_reason if busy else (disabled_reason if not enabled else (controller_reason if not controller_available else "")),
        ),
    }


def _runner_control_payload(
    controller: RunnerController | None,
    *,
    repo: Path,
    enabled: bool,
    source: str,
    current_run_dir: str = "",
    last_action: str = "",
    last_message: str = "",
    last_error: str = "",
    run_status: str = "",
    busy: bool = False,
) -> dict[str, Any]:
    status_payload = _runner_control_status_payload(controller, repo=repo, current_run_dir=current_run_dir)
    controller_available = controller is not None
    actions = _runner_control_actions(enabled, status_payload, controller_available=controller_available, busy=busy)
    message = _runner_control_message(
        enabled=enabled,
        source=source,
        running=bool(status_payload.get("running")),
        controller_available=controller_available,
    )
    status_reason = str(status_payload.get("reason") or "").strip()
    if last_error:
        message = last_error
    elif status_reason.startswith("status_error:"):
        message = status_reason
    return {
        "enabled": bool(enabled),
        "source": source,
        "controller_available": controller_available,
        "message": message,
        "status": status_payload,
        "run_status": str(run_status or "").strip(),
        "actions": actions,
        "confirmation": dict(RUNNER_CONTROL_CONFIRMATIONS),
        "last_action": last_action,
        "last_message": last_message,
        "last_error": last_error,
        "busy": bool(busy),
    }


def _runner_control_confirmation_value(payload: dict[str, Any] | None) -> str:
    data = payload if isinstance(payload, dict) else {}
    for key in ("confirmation", "confirm", "token", "phrase"):
        raw = data.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def _build_runner_base_args(repo: Path, cfg: dict[str, Any], cfg_path: Path) -> argparse.Namespace:
    payload = dict(cfg or {})
    payload["repo"] = str(repo)
    payload["config"] = str(cfg_path)
    payload["config_path"] = str(cfg_path)
    return argparse.Namespace(**payload)


def _runner_mode_from_config(cfg: dict[str, Any]) -> str:
    telegram_cfg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
    raw = cfg.get("runner_mode") or telegram_cfg.get("runner_mode") or "thread"
    mode = str(raw or "thread").strip().lower()
    return mode if mode in {"thread", "subprocess"} else "thread"


def _build_runner_controller(repo: Path, cfg: dict[str, Any], cfg_path: Path) -> RunnerController | None:
    if RunnerController is None:
        return None
    try:
        return RunnerController(
            repo=repo,
            base_args=_build_runner_base_args(repo, cfg, cfg_path),
            runner_mode=_runner_mode_from_config(cfg),
        )
    except Exception:
        return None


def _runner_control_response(
    *,
    action: str,
    ok: bool,
    message: str,
    snapshot: dict[str, Any],
    status_code: int = 200,
    error_code: str = "",
    details: dict[str, Any] | None = None,
) -> Any:
    if JSONResponse is None:
        raise RuntimeError("JSONResponse is unavailable.")
    payload: dict[str, Any] = {
        "ok": bool(ok),
        "action": action,
        "message": message,
        "runner_control": snapshot.get("runner_control", {}),
        "snapshot": snapshot,
    }
    if not ok:
        payload["error"] = {
            "code": error_code or "runner_control_error",
            "message": message,
            "details": details or {},
        }
    return JSONResponse(payload, status_code=status_code)


def _wait_for_runner_idle(controller: RunnerController, timeout_sec: float = 10.0) -> bool:
    deadline = time.time() + max(0.5, float(timeout_sec))
    while time.time() < deadline:
        try:
            status = controller.status()
        except Exception:
            status = {}
        if not bool(status.get("running")):
            return True
        time.sleep(0.25)
    try:
        return not bool(controller.status().get("running"))
    except Exception:
        return False


def _text_excerpt(text: str, *, max_lines: int = 8, max_chars: int = 280) -> str:
    clean = (text or "").strip()
    if not clean:
        return ""
    lines = clean.splitlines()
    excerpt = "\n".join(lines[:max_lines]).strip()
    if len(excerpt) > max_chars:
        excerpt = excerpt[: max_chars - 3].rstrip() + "..."
    return excerpt


def _branch_name(repo: Path) -> str:
    rc, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout_sec=10)
    if rc != 0:
        return ""
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    if not lines:
        return ""
    branch = lines[-1]
    return branch if branch != "HEAD" else ""


def _git_head_short(repo: Path) -> str:
    try:
        head = git_head(repo).strip()
        return head[:8] if head else ""
    except Exception:
        return ""


def _load_config_payload(repo: Path, explicit: str | None = None) -> tuple[Path, dict[str, Any], str]:
    cfg_path = resolve_config_path(repo, explicit)
    source = "explicit" if explicit and str(explicit).strip() else "default"
    if not cfg_path.exists():
        legacy = legacy_default_config_path(repo)
        if legacy is not None:
            cfg_path = legacy
            source = "legacy"
    payload: dict[str, Any] = {}
    try:
        payload = load_config(cfg_path)
    except Exception:
        payload = {}
    return cfg_path, payload, source


def _load_tasks(run_dir: Path | None) -> list[TaskItem]:
    if run_dir is None:
        return []
    backlog_json = run_dir / "BACKLOG.json"
    backlog_md = run_dir / "BACKLOG.md"
    tasks: list[TaskItem] = []
    if backlog_json.exists():
        try:
            tasks = load_backlog_json(backlog_json)
        except Exception:
            tasks = []
    if not tasks and backlog_md.exists():
        try:
            tasks = parse_backlog_md(backlog_md)
        except Exception:
            tasks = []
    return tasks


def _task_priority(task: TaskItem, index: int) -> str:
    text = f"{task.title} {task.prompt}".lower()
    if any(token in text for token in ("test", "qa", "verify", "regression")):
        return "P1"
    if any(token in text for token in ("bug", "fix", "crash", "block", "security")):
        return "P0"
    return "P0" if index < 3 else "P1"


def _task_estimate(task: TaskItem) -> str:
    score = len(task.files) + max(1, len(task.prompt.split()) // 45)
    if score <= 1:
        return "S"
    if score <= 3:
        return "M"
    return "L"


def _task_tags(task: TaskItem) -> list[str]:
    tags: list[str] = []
    for skill in task.skills:
        if skill and skill not in tags:
            tags.append(skill)
    for path in task.files:
        clean = str(path).replace("\\", "/").strip()
        if not clean:
            continue
        head = clean.split("/", 1)[0]
        if head and head not in tags and head not in {".", ".."}:
            tags.append(head)
    if not tags:
        text = f"{task.title} {task.prompt}".lower()
        if "test" in text and "test" not in tags:
            tags.append("test")
        if "ui" in text and "ui" not in tags:
            tags.append("ui")
    return tags[:4]


def _goal_items(goals_text: str | None) -> dict[str, list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {"p0": [], "p1": []}
    if not goals_text or not goals_text.strip():
        return items

    current_bucket: str | None = None
    for line in goals_text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if re.match(r"^##\s+p0\b", lower):
            current_bucket = "p0"
            continue
        if re.match(r"^##\s+p1\b", lower):
            current_bucket = "p1"
            continue
        if stripped.startswith("## "):
            current_bucket = None
            continue

        match = re.match(r"^\s*-\s*\[(x| )\]\s*(.+)$", line, re.IGNORECASE)
        if not match or current_bucket not in ("p0", "p1"):
            continue
        items[current_bucket].append(
            {
                "done": match.group(1).lower() == "x",
                "text": match.group(2).strip(),
                "note": "",
            }
        )
    return items


def _prompt_preview(text: str) -> str:
    preview = _text_excerpt(text, max_lines=6, max_chars=420)
    return preview or "(empty)"


def _prompt_summary(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return "(empty)"
    for line in lines:
        if not line.startswith("#"):
            return line[:180]
    return lines[0][:180]


def _load_prompt_items(repo: Path, prompts_dir: Path) -> list[dict[str, Any]]:
    store = PromptStore(prompts_dir=prompts_dir)
    items: list[dict[str, Any]] = []
    for spec in PROMPT_SPECS:
        file_name = spec["file"]
        prompt_path = prompts_dir / file_name
        exists = prompt_path.exists() and prompt_path.is_file()
        content = store.get(spec["id"], spec["default"])
        mode = "override" if exists else "template"
        source = prompts_dir.as_posix() if exists else "templates/agent_prompts"
        updated = _fmt_mtime(prompt_path.stat().st_mtime) if exists else "template"
        content_length = len(content or "")
        items.append(
            {
                "id": spec["id"],
                "file": file_name,
                "scope": spec["scope"],
                "source": source,
                "mode": mode,
                "updated": updated,
                "summary": f"{mode.title()} prompt available ({content_length} characters).",
                "preview": "[redacted: prompt content is hidden in the read-only web API]",
                "path": prompt_path.as_posix(),
                "content_length": content_length,
            }
        )
    return items


def _load_backlog_payload(run_dir: Path | None, state: dict[str, Any]) -> dict[str, Any]:
    tasks = _load_tasks(run_dir)
    done_ids = set(str(item) for item in (state.get("done") or []) if str(item).strip())
    failed_items = state.get("failed") if isinstance(state.get("failed"), list) else []
    failed_ids = {
        str(item.get("task") or "").strip()
        for item in failed_items
        if isinstance(item, dict) and str(item.get("task") or "").strip()
    }

    backlog: list[dict[str, Any]] = []
    for index, task in enumerate(tasks):
        status = "pending"
        if task.id in done_ids:
            status = "done"
        elif task.id in failed_ids:
            status = "failed"

        backlog.append(
            {
                "id": task.id,
                "title": task.title,
                "prompt": task.prompt,
                "files": task.files,
                "done_when": task.done_when,
                "skills": task.skills,
                "skills_rationale": task.skills_rationale,
                "depends_on": task.depends_on,
                "status": "in_progress" if status == "pending" and index == 0 and tasks else status,
                "priority": _task_priority(task, index),
                "tags": _task_tags(task),
                "estimate": _task_estimate(task),
                "skill": task.skills[0] if task.skills else None,
            }
        )

    selected_id = ""
    for item in backlog:
        if item["status"] == "in_progress":
            selected_id = item["id"]
            break
    if not selected_id and backlog:
        selected_id = backlog[0]["id"]

    counts = {
        "pending": len([item for item in backlog if item["status"] == "pending"]),
        "in_progress": len([item for item in backlog if item["status"] == "in_progress"]),
        "done": len([item for item in backlog if item["status"] == "done"]),
        "failed": len([item for item in backlog if item["status"] == "failed"]),
    }
    return {
        "items": backlog,
        "counts": counts,
        "selected_id": selected_id,
    }


def _cycle_events(run_dir: Path | None) -> list[dict[str, Any]]:
    if run_dir is None:
        return []
    return _safe_jsonl(run_dir / "metrics.jsonl", max_items=500)


def _event_stage(event: dict[str, Any]) -> str:
    stage = str(event.get("stage") or "").strip()
    if stage:
        return stage
    event_type = str(event.get("event") or event.get("type") or "").strip().lower()
    if event_type.startswith("pm_"):
        return "PM"
    if event_type.startswith("dev_") or event_type.startswith("task_"):
        return "Dev"
    if event_type.startswith("qa_"):
        return "QA"
    if event_type.startswith("security_"):
        return "Security"
    if event_type.startswith("reporter_"):
        return "Reporter"
    return "boot"


def _event_level(event: dict[str, Any]) -> str:
    level = str(event.get("level") or "").strip().lower()
    if level in {"debug", "info", "warning", "error"}:
        return {"warning": "warn", "error": "err"}.get(level, level)
    event_type = str(event.get("event") or event.get("type") or "").strip().lower()
    if "error" in event_type or "fail" in event_type or "exception" in event_type:
        return "err"
    if "warn" in event_type or "stop" in event_type or "retry" in event_type:
        return "warn"
    return "info"


def _event_message(event: dict[str, Any]) -> str:
    message = event.get("message")
    if message:
        return str(message)
    event_type = str(event.get("event") or event.get("type") or "").strip()
    if event_type:
        return event_type
    return ""


def _load_log_entries(run_dir: Path | None) -> list[dict[str, Any]]:
    events = _cycle_events(run_dir)
    if events:
        rows: list[dict[str, Any]] = []
        for event in events:
            rows.append(
                {
                    "t": _fmt_clock(event.get("ts")),
                    "lvl": _event_level(event),
                    "stage": _event_stage(event),
                    "msg": _event_message(event),
                }
            )
        return rows

    if run_dir is None:
        return []

    fallback = run_dir / "logs" / "run.log"
    raw = _tail_text(fallback, lines=200)
    if not raw:
        return []

    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r"^(?:(?P<date>\d{4}-\d{2}-\d{2})\s+)?(?P<time>\d{2}:\d{2}:\d{2})\s+\[(?P<level>[A-Z]+)\]\s*(?P<msg>.*)$"
    )
    for line in raw.splitlines():
        match = pattern.match(line)
        if match:
            level = match.group("level").lower()
            rows.append(
                {
                    "t": match.group("time"),
                    "lvl": {"warning": "warn", "error": "err"}.get(level, level),
                    "stage": "boot",
                    "msg": match.group("msg").strip(),
                }
            )
        else:
            rows.append({"t": "", "lvl": "info", "stage": "boot", "msg": line.strip()})
    return rows


def _build_notifications(
    *,
    run_id: str,
    started_at_ms: int,
    branch: str,
    active_status: str,
    state: dict[str, Any],
    backlog: list[dict[str, Any]],
    events: list[dict[str, Any]],
    final_reason: str,
) -> list[dict[str, Any]]:
    backlog_by_id = {item["id"]: item for item in backlog}
    notifications: list[dict[str, Any]] = []

    if started_at_ms:
        notifications.append(
            {
                "t": started_at_ms,
                "kind": "run_start",
                "text": f"Run started | {branch or run_id}",
                "run": run_id,
            }
        )

    done_ids = [str(item) for item in (state.get("done") or []) if str(item).strip()]
    for task_id in done_ids[-4:]:
        item = backlog_by_id.get(task_id)
        title = item["title"] if item else task_id
        notifications.append(
            {
                "t": started_at_ms or _epoch_ms(datetime.now(timezone.utc).timestamp()),
                "kind": "task_done",
                "text": f"{task_id} | {title}",
                "run": run_id,
            }
        )

    failed_list = state.get("failed") if isinstance(state.get("failed"), list) else []
    for raw in failed_list[-4:]:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task") or "").strip()
        if not task_id:
            continue
        item = backlog_by_id.get(task_id)
        title = item["title"] if item else task_id
        notifications.append(
            {
                "t": started_at_ms or _epoch_ms(datetime.now(timezone.utc).timestamp()),
                "kind": "task_failed",
                "text": f"{task_id} | {title}",
                "run": run_id,
            }
        )

    for event in events:
        event_type = str(event.get("event") or event.get("type") or "").strip().lower()
        task_id = str(event.get("task_id") or "").strip()
        message = _event_message(event)
        ts = _iso_to_ms(event.get("ts"))

        kind: str | None = None
        text = message or event_type
        if event_type in {"task_end", "task_done"}:
            if str(event.get("success", True)).lower() in {"false", "0"} or str(event.get("status", "")).lower() in {"failed", "fail"}:
                kind = "task_failed"
            else:
                kind = "task_done"
        elif "quota" in event_type:
            kind = "quota"
        elif "fail" in event_type or "error" in event_type or "exception" in event_type:
            kind = "error"
        elif "stalled" in event_type:
            kind = "stalled"
        elif event_type in {"cycle_start", "pm_start"}:
            kind = "run_start"
        elif event_type in {"cycle_end", "pm_end", "qa_end", "dev_end"} and active_status in {"stopped", "failed"}:
            kind = "run_stop"

        if kind:
            notifications.append(
                {
                    "t": ts or started_at_ms,
                    "kind": kind,
                    "text": text or event_type,
                    "run": run_id,
                }
            )

    if final_reason:
        final_kind = "task_done" if final_reason in {"project_complete", "all_tasks_done"} else "run_stop"
        final_text = f"Run finished | {final_reason}"
        if final_kind == "task_done" and final_reason == "project_complete":
            final_text = "Project complete"
        notifications.append(
            {
                "t": _epoch_ms(datetime.now(timezone.utc).timestamp()),
                "kind": final_kind,
                "text": final_text,
                "run": run_id,
            }
        )

    notifications = [item for item in notifications if item.get("kind")]
    notifications.sort(key=lambda item: int(item.get("t") or 0), reverse=True)
    return notifications[:20]


def _active_run_payload(
    *,
    repo: Path,
    run_dir: Path | None,
    config: dict[str, Any],
    run_summary: dict[str, Any],
    last_run_summary: dict[str, Any],
    state: dict[str, Any],
    backlog: list[dict[str, Any]],
    progress: dict[str, Any],
    metrics: dict[str, Any],
    branch: str,
) -> dict[str, Any]:
    repo_path = repo.as_posix()
    run_id = run_dir.name if run_dir else "no-run"
    started_at_ms = _epoch_ms(run_dir.stat().st_ctime) if run_dir else 0
    ended_at_ms = _epoch_ms(run_dir.stat().st_mtime) if run_dir else 0
    elapsed_sec = max(0, int((ended_at_ms - started_at_ms) / 1000)) if started_at_ms and ended_at_ms else 0
    tasks_total = int(progress.get("tasks_total") or len(backlog))
    tasks_done = int(progress.get("tasks_done") or 0)
    progress_ratio = float(progress.get("progress") or 0.0)
    if not progress_ratio and tasks_total > 0:
        progress_ratio = min(1.0, tasks_done / tasks_total)

    latest_cycle = {}
    cycles = run_summary.get("cycles") if isinstance(run_summary.get("cycles"), list) else []
    if cycles:
        latest_cycle = cycles[-1] if isinstance(cycles[-1], dict) else {}

    active_status = str(progress.get("run_status") or "idle").strip() or "idle"

    last_event_stage = str(metrics.get("last_stage") or "").strip() or "Dev"
    if active_status == "success":
        last_event_stage = "QA"
    if active_status == "idle":
        last_event_stage = "idle"

    task_id = progress.get("current_task_id") or progress.get("selected_task_id") or ""
    if not task_id and backlog:
        task_id = backlog[0]["id"]

    tokens = metrics.get("tokens") if isinstance(metrics.get("tokens"), dict) else {}
    quota_used = float(metrics.get("quota_used") or 0.0)
    if not quota_used and tasks_total > 0:
        quota_used = min(1.0, max(0.0, tasks_done / tasks_total))

    budget_used = float(metrics.get("budget_used") or 0.0)
    if not budget_used:
        budget_used = quota_used

    return {
        "id": run_id,
        "repo": repo_path,
        "repoLabel": repo.name or repo_path.rsplit("/", 1)[-1],
        "branch": branch or "HEAD",
        "backend": str(config.get("execution_backend") or "codex"),
        "startedAt": started_at_ms,
        "stage": last_event_stage,
        "stageIndex": STAGE_ORDER.get(last_event_stage.lower(), 0),
        "iteration": 0 if active_status == "idle" else int(progress.get("iterations") or len(cycles) or 1),
        "maxIterations": int(config.get("iterations") or 5),
        "progress": round(progress_ratio, 3),
        "budgetUsed": round(budget_used, 3),
        "tokens": {
            "in": int(tokens.get("in") or 0),
            "out": int(tokens.get("out") or 0),
        },
        "quota": {
            "window": "5h",
            "used": round(quota_used, 3),
        },
        "elapsedSec": elapsed_sec,
        "status": active_status,
        "task": task_id or "",
        "taskTitle": progress.get("current_task_title") or "",
    }


def _stage_payload(repo: Path, active_run: dict[str, Any], progress: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    status = str(progress.get("run_status") or active_run.get("status") or "idle").strip()
    elapsed = int(active_run.get("elapsedSec") or 0)
    pm_duration = max(0, int(elapsed * 0.18))
    dev_duration = max(0, int(elapsed * 0.62))
    qa_duration = max(0, int(elapsed * 0.20))

    stage_status = {
        "PM": "done" if status in {"running", "success", "stopped", "failed"} else "pending",
        "Dev": "running" if status == "running" else ("done" if status == "success" else "pending"),
        "QA": "pending" if status != "success" else "done",
    }
    if status == "success":
        stage_status = {"PM": "done", "Dev": "done", "QA": "done"}
    elif status == "failed":
        stage_status = {"PM": "done", "Dev": "done", "QA": "pending"}
    elif status == "stopped":
        stage_status = {"PM": "done", "Dev": "done", "QA": "pending"}
    elif status == "idle":
        stage_status = {"PM": "pending", "Dev": "pending", "QA": "pending"}

    return [
        {
            "id": "PM",
            "label": "PM",
            "title": "Backlog planning",
            "status": stage_status["PM"],
            "durationSec": pm_duration,
            "model": str(config.get("pm_model") or "gpt-5.5"),
        },
        {
            "id": "Dev",
            "label": "Dev",
            "title": "Implementation",
            "status": stage_status["Dev"],
            "durationSec": dev_duration,
            "model": str(config.get("dev_model") or "gpt-5.4-mini"),
        },
        {
            "id": "QA",
            "label": "QA",
            "title": "Verification",
            "status": stage_status["QA"],
            "durationSec": qa_duration,
            "model": str(config.get("qa_model") or "gpt-5.4-mini"),
        },
    ]


def _history_item(
    repo: Path,
    run_dir: Path,
    *,
    branch: str,
) -> dict[str, Any]:
    state = load_state(run_dir / "STATE.json")
    backlog = _load_tasks(run_dir)
    run_summary = _safe_json(run_dir / "run_summary.json", {})
    last_summary = _safe_json(run_dir / "last_run_summary.json", {})

    done_ids = set(str(item) for item in (state.get("done") or []) if str(item).strip())
    task_ids = [item.id for item in backlog]
    tasks_done = len([tid for tid in task_ids if tid in done_ids])
    tasks_total = len(backlog)

    final = run_summary.get("final") if isinstance(run_summary.get("final"), dict) else {}
    reason = str(final.get("reason") or last_summary.get("stop_reason") or "").strip()
    rc = int(final.get("rc") or last_summary.get("rc") or 0) if (final or last_summary) else 0
    stop_exists = (run_dir / "STOP").exists()
    if not reason and stop_exists:
        reason = "stop_file"

    if reason in {"project_complete", "all_tasks_done"} or (rc == 0 and reason in {"ok", "prepared_only"}):
        status = "success"
    elif reason in {"stop_file", "stop_requested", "stopped", "user_stop"}:
        status = "stopped"
    elif rc and rc != 0:
        status = "failed"
    elif stop_exists:
        status = "stopped"
    else:
        status = "running"

    started_at = _epoch_ms(run_dir.stat().st_ctime)
    ended_at = _epoch_ms(run_dir.stat().st_mtime)
    duration_sec = max(0, int((ended_at - started_at) / 1000)) if started_at and ended_at else 0
    if duration_sec == 0:
        duration_sec = max(0, len(run_summary.get("cycles") or []) * 60)

    last_cycle = _tail_text(run_dir / "cycle_summary.log", 1).strip()
    return {
        "id": run_dir.name,
        "startedAt": started_at,
        "status": status,
        "tasksDone": tasks_done,
        "tasksTotal": tasks_total,
        "branch": branch or "HEAD",
        "durationSec": duration_sec,
        "stopReason": reason,
        "runDir": run_dir.as_posix(),
        "lastCycle": last_cycle,
    }


def _history_payload(repo: Path, run_dirs: list[Path], *, branch: str) -> dict[str, Any]:
    items = [_history_item(repo, run_dir, branch=branch) for run_dir in run_dirs]
    items.sort(key=lambda item: int(item.get("startedAt") or 0), reverse=True)
    successes = len([item for item in items if item["status"] == "success"])
    failures = len([item for item in items if item["status"] == "failed"])
    stopped = len([item for item in items if item["status"] == "stopped"])
    total_tasks = sum(int(item.get("tasksTotal") or 0) for item in items)
    done_tasks = sum(int(item.get("tasksDone") or 0) for item in items)
    return {
        "items": items,
        "summary": {
            "runs": len(items),
            "successes": successes,
            "failures": failures,
            "stopped": stopped,
            "tasksDone": done_tasks,
            "tasksTotal": total_tasks,
        },
    }


def _run_dirs(repo: Path) -> list[Path]:
    roots = [repo / ".AgentCLI" / "agent_runs", repo / ".doc" / "agent_runs"]
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        for candidate in root.iterdir():
            if not candidate.is_dir():
                continue
            has_artifacts = _run_dir_has_observable_artifacts(candidate)
            if not (re.match(r"^\d{8}-\d{6}$", candidate.name) or has_artifacts):
                continue
            if not has_artifacts:
                continue
            key = str(candidate.resolve())
            if key in seen:
                continue
            seen.add(key)
            found.append(candidate)
    found.sort(key=lambda item: item.name, reverse=True)
    return found


def _latest_observable_run_dir(repo: Path) -> Path | None:
    latest = find_latest_run_dir(repo)
    if _run_dir_has_observable_artifacts(latest):
        return latest
    run_dirs = _run_dirs(repo)
    return run_dirs[0] if run_dirs else None


def _build_metrics_payload(run_dir: Path | None, progress: dict[str, Any]) -> dict[str, Any]:
    events = _cycle_events(run_dir)
    cycle_end_events = [event for event in events if str(event.get("event") or event.get("type") or "").strip().lower() == "cycle_end"]
    tokens24h: list[int] = []
    success24h: list[int] = []
    budget: list[float] = []
    last_tokens = {"in": 0, "out": 0}
    last_stage = ""
    quota_used = 0.0

    for event in cycle_end_events:
        tokens = event.get("tokens") if isinstance(event.get("tokens"), dict) else {}
        total = tokens.get("_total") if isinstance(tokens.get("_total"), dict) else {}
        total_tokens = int(total.get("total") or 0)
        tokens24h.append(total_tokens)
        success24h.append(1 if int(event.get("rc") or 0) == 0 else 0)
        done = int(event.get("done") or 0)
        total_tasks = max(1, int(event.get("total") or 0))
        budget.append(round(min(1.0, done / total_tasks), 3))
        last_tokens = {
            "in": int(total.get("input") or 0),
            "out": int(total.get("output") or 0),
        }
        last_stage = str(event.get("stage") or event.get("name") or "").strip() or last_stage
        quota = event.get("quota") if isinstance(event.get("quota"), dict) else {}
        quota_used = max(quota_used, float(quota.get("used") or 0.0))

    if not tokens24h and progress.get("tasks_total"):
        tokens24h = [int(progress.get("tasks_done") or 0) * 1000]
        success24h = [1 if progress.get("status") == "success" else 0]
        budget = [round(float(progress.get("progress") or 0.0), 3)]

    return {
        "tokens24h": tokens24h,
        "success24h": success24h,
        "budget": budget,
        "tokens": last_tokens,
        "last_stage": last_stage,
        "quota_used": quota_used,
    }


def _build_progress_payload(
    *,
    repo: Path,
    run_dir: Path | None,
    config: dict[str, Any],
    branch: str,
) -> dict[str, Any]:
    state = load_state(run_dir / "STATE.json") if run_dir else {"done": [], "failed": [], "warnings": []}
    backlog = _load_backlog_payload(run_dir, state)
    goals_path, goals_text = read_goals(repo)
    completion_level = str(config.get("goals_completion_level") or "all").strip() or "all"
    goals_completion = parse_goals_completion(goals_text, completion_level=completion_level)
    backlog_items = backlog["items"]
    done_ids = set(str(item) for item in (state.get("done") or []) if str(item).strip())
    failed_items = state.get("failed") if isinstance(state.get("failed"), list) else []
    failed_ids = {
        str(item.get("task") or "").strip()
        for item in failed_items
        if isinstance(item, dict) and str(item.get("task") or "").strip()
    }
    tasks_total = len(backlog_items)
    tasks_done = len([item for item in backlog_items if item["id"] in done_ids])
    tasks_failed = len([item for item in backlog_items if item["id"] in failed_ids])
    progress_ratio = round(tasks_done / tasks_total, 3) if tasks_total else 0.0
    current_task = backlog["selected_id"] if backlog["selected_id"] else ""
    run_summary = _safe_json(run_dir / "run_summary.json", {}) if run_dir else {}
    last_run_summary = _safe_json(run_dir / "last_run_summary.json", {}) if run_dir else {}
    final = run_summary.get("final") if isinstance(run_summary.get("final"), dict) else {}
    final_reason = str(final.get("reason") or last_run_summary.get("stop_reason") or "").strip()
    final_rc = final.get("rc") if isinstance(final, dict) else None
    if final_rc is None:
        final_rc = last_run_summary.get("rc") if isinstance(last_run_summary, dict) else None
    final_rc_text = "" if final_rc is None else str(final_rc)
    if not final_reason and run_dir and (run_dir / "STOP").exists():
        final_reason = "stop_file"

    if not run_dir or not _run_dir_has_observable_artifacts(run_dir):
        run_status = "idle"
    elif final_reason in {"project_complete", "all_tasks_done"} or (
        final_reason in {"ok", "prepared_only"} and final_rc_text in {"", "0"}
    ):
        run_status = "success"
    elif final_reason in {"stop_file", "stopped", "user_stop"}:
        run_status = "stopped"
    elif final_reason and final_rc_text == "0":
        run_status = "success"
    elif final_reason and final_reason not in {"ok", "prepared_only"}:
        run_status = "failed"
    elif run_dir:
        run_status = "running"
    else:
        run_status = "idle"

    return {
        "latest_run_dir": run_dir.as_posix() if run_dir else None,
        "run_status": run_status,
        "tasks_done": tasks_done,
        "tasks_total": tasks_total,
        "tasks_failed": tasks_failed,
        "progress": progress_ratio,
        "current_task_id": current_task,
        "current_task_title": next((item["title"] for item in backlog_items if item["id"] == current_task), ""),
        "iterations": len(run_summary.get("cycles") or []),
        "goals": {
            "path": goals_path.as_posix() if goals_path else None,
            "completion": goals_completion,
            "items": _goal_items(goals_text),
        },
        "backlog": backlog,
        "state": state,
        "final_reason": final_reason,
    }


def _build_worktree_payload(repo: Path, run_dir: Path | None, branch: str) -> dict[str, Any]:
    repo_root = _repo_root(repo)
    pending_path = find_pending_worktree_merge(repo_root, run_dir)
    source_repo = repo_root.as_posix()
    source_branch = branch or "HEAD"
    run_dir_value = run_dir.as_posix() if run_dir else ""
    checklist = [
        "Inspect patch hunks",
        "Verify no secret leakage",
        "Approve merge only after review",
        "Discard only after archival copy",
    ]

    if pending_path is None:
        return {
            "status": "none",
            "mode": "manual",
            "reviewRequired": False,
            "reviewRequiredMessage": "No pending worktree merge.",
            "sourceRepo": source_repo,
            "sourceBranch": source_branch,
            "branch": source_branch,
            "baseRef": "",
            "headRef": "",
            "worktreeDir": "",
            "worktree": "",
            "patchPath": "",
            "patch": "",
            "pendingFile": "",
            "summary": "No pending worktree merge.",
            "risk": "No isolated worktree patch is pending review.",
            "changedFiles": [],
            "checklist": checklist,
            "runDir": run_dir_value,
            "runnerRc": 0,
            "lastRc": 0,
        }

    payload: dict[str, Any] = {}
    read_error = ""
    try:
        raw_payload = read_pending_worktree_merge(pending_path)
        if not isinstance(raw_payload, dict):
            raise TypeError("Pending merge payload must be a JSON object.")
        payload = raw_payload
    except Exception as ex:
        read_error = str(ex).strip() or ex.__class__.__name__

    def _coerce_int(value: Any) -> int:
        raw = str(value or "").strip()
        if not raw:
            return 0
        try:
            return int(float(raw))
        except Exception:
            return 0

    if read_error:
        error_message = f"Pending worktree merge file is malformed: {read_error}"
        return {
            "status": "error",
            "mode": "manual",
            "reviewRequired": True,
            "reviewRequiredMessage": error_message,
            "sourceRepo": source_repo,
            "sourceBranch": source_branch,
            "branch": source_branch,
            "baseRef": str(payload.get("base_ref") or "").strip(),
            "headRef": str(payload.get("head_ref") or "").strip(),
            "worktreeDir": str(payload.get("worktree_dir") or "").strip(),
            "worktree": str(payload.get("worktree_dir") or "").strip(),
            "patchPath": str(payload.get("patch_path") or "").strip(),
            "patch": str(payload.get("patch_path") or "").strip(),
            "pendingFile": pending_path.as_posix(),
            "summary": "Pending worktree merge file is malformed.",
            "risk": "Fix or delete the pending merge file before applying any source-repo change.",
            "changedFiles": [],
            "checklist": [
                "Inspect the pending JSON payload",
                "Fix or delete the malformed merge file",
                "Discard only after archival copy",
            ],
            "runDir": str(payload.get("run_dir") or run_dir_value or "").strip(),
            "runnerRc": 0,
            "lastRc": 0,
        }

    required_fields = ("source_repo", "run_dir", "worktree_dir", "patch_path", "base_ref", "head_ref")
    missing_fields = [field for field in required_fields if not str(payload.get(field) or "").strip()]
    if missing_fields:
        error_message = f"Pending worktree merge file is malformed: missing required fields ({', '.join(missing_fields)})"
        return {
            "status": "error",
            "mode": "manual",
            "reviewRequired": True,
            "reviewRequiredMessage": error_message,
            "sourceRepo": source_repo,
            "sourceBranch": source_branch,
            "branch": source_branch,
            "baseRef": str(payload.get("base_ref") or "").strip(),
            "headRef": str(payload.get("head_ref") or "").strip(),
            "worktreeDir": str(payload.get("worktree_dir") or "").strip(),
            "worktree": str(payload.get("worktree_dir") or "").strip(),
            "patchPath": str(payload.get("patch_path") or "").strip(),
            "patch": str(payload.get("patch_path") or "").strip(),
            "pendingFile": pending_path.as_posix(),
            "summary": "Pending worktree merge file is malformed.",
            "risk": "Fix or delete the pending merge file before applying any source-repo change.",
            "changedFiles": [],
            "checklist": [
                "Inspect the pending JSON payload",
                "Fix or delete the malformed merge file",
                "Discard only after archival copy",
            ],
            "runDir": str(payload.get("run_dir") or run_dir_value or "").strip(),
            "runnerRc": _coerce_int(payload.get("last_rc")),
            "lastRc": _coerce_int(payload.get("last_rc")),
        }

    source_repo_value = str(payload.get("source_repo") or "").strip() or source_repo
    run_dir_value = str(payload.get("run_dir") or run_dir_value or "").strip()
    worktree_dir = str(payload.get("worktree_dir") or "").strip()
    patch_path = str(payload.get("patch_path") or "").strip()
    base_ref = str(payload.get("base_ref") or "").strip()
    head_ref = str(payload.get("head_ref") or "").strip()
    runner_rc = _coerce_int(payload.get("last_rc"))
    source_branch = base_ref or source_branch

    changed_files: list[dict[str, Any]] = []
    patch_file = Path(patch_path)
    if patch_file.exists():
        try:
            text = patch_file.read_text(encoding="utf-8", errors="replace")
            seen: set[str] = set()
            for line in text.splitlines():
                match = re.match(r"^\+\+\+\s+b/(.+)$", line)
                if not match:
                    match = re.match(r"^diff --git a/.+ b/(.+)$", line)
                if not match:
                    continue
                path = match.group(1).strip()
                if path in seen:
                    continue
                seen.add(path)
                changed_files.append({"path": path, "kind": "modified", "note": ""})
        except Exception:
            changed_files = []

    if not changed_files:
        changed_files = [{"path": patch_path or "(unknown)", "kind": "modified", "note": "patch export"}]

    summary = (
        "Worktree produced a patch that must be reviewed before merge."
        if runner_rc == 0
        else f"Patch export completed with runner rc={runner_rc}."
    )
    review_message = "Review required before applying the patch to the source repository. Use /merge-worktree or /discard-worktree from the CLI when ready."
    risk = "Manual review required before applying the patch to the source repository."
    if head_ref and base_ref:
        risk = f"Review patch from {base_ref} to {head_ref} before applying."

    return {
        "status": "pending review",
        "mode": "manual",
        "reviewRequired": True,
        "reviewRequiredMessage": review_message,
        "sourceRepo": source_repo_value,
        "sourceBranch": source_branch,
        "branch": source_branch,
        "baseRef": base_ref,
        "headRef": head_ref,
        "worktreeDir": worktree_dir,
        "worktree": worktree_dir,
        "patchPath": patch_path,
        "patch": patch_path,
        "pendingFile": pending_path.as_posix(),
        "summary": summary,
        "risk": risk,
        "changedFiles": changed_files,
        "checklist": checklist,
        "runDir": run_dir_value,
        "runnerRc": runner_rc,
        "lastRc": runner_rc,
    }


def build_snapshot(
    repo: Path | str | None = None,
    *,
    config_path: str | None = None,
    runner_controller: RunnerController | None = None,
    runner_controls_enabled: bool | None = None,
    runner_controls_source: str | None = None,
    runner_control_busy: bool = False,
    runner_control_last_action: str = "",
    runner_control_last_message: str = "",
    runner_control_last_error: str = "",
    runner_controller_auto_build: bool = True,
) -> dict[str, Any]:
    repo_root = _repo_root(repo)
    latest_run_dir = _latest_observable_run_dir(repo_root)
    cfg_path, cfg, cfg_source = _load_config_payload(repo_root, config_path)
    prompts_dir = resolve_prompts_dir(repo_root, str(cfg.get("prompts_dir") or ""))
    if not prompts_dir:
        prompts_dir = default_prompts_dir(repo_root)
    goals_path, goals_text = read_goals(repo_root)
    goals_completion = parse_goals_completion(goals_text, completion_level=str(cfg.get("goals_completion_level") or "all"))
    goals = _goal_items(goals_text)
    prompt_items = _load_prompt_items(repo_root, prompts_dir)
    progress = _build_progress_payload(repo=repo_root, run_dir=latest_run_dir, config=cfg, branch=_branch_name(repo_root))
    state = progress["state"] if isinstance(progress.get("state"), dict) else {"done": [], "failed": [], "warnings": []}
    backlog = progress["backlog"] if isinstance(progress.get("backlog"), dict) else {"items": [], "counts": {}, "selected_id": ""}
    run_summary = _safe_json(latest_run_dir / "run_summary.json", {}) if latest_run_dir else {}
    last_run_summary = _safe_json(latest_run_dir / "last_run_summary.json", {}) if latest_run_dir else {}
    branch = _branch_name(repo_root)
    logs_events = _cycle_events(latest_run_dir)
    log_entries = _load_log_entries(latest_run_dir)
    metrics = _build_metrics_payload(latest_run_dir, progress)
    active_run = _active_run_payload(
        repo=repo_root,
        run_dir=latest_run_dir,
        config=cfg,
        run_summary=run_summary,
        last_run_summary=last_run_summary,
        state=state,
        backlog=backlog.get("items", []),
        progress=progress,
        metrics=metrics,
        branch=branch,
    )
    stages = _stage_payload(repo_root, active_run, progress, cfg)
    history = _history_payload(repo_root, _run_dirs(repo_root), branch=branch)
    notifications = _build_notifications(
        run_id=active_run["id"],
        started_at_ms=int(active_run.get("startedAt") or 0),
        branch=active_run.get("branch") or branch,
        active_status=str(progress.get("run_status") or "idle"),
        state=state,
        backlog=backlog.get("items", []),
        events=logs_events,
        final_reason=str(progress.get("final_reason") or ""),
    )
    worktree = _build_worktree_payload(repo_root, latest_run_dir, branch=branch)
    control_enabled, resolved_source = _resolve_runner_controls_enabled(runner_controls_enabled)
    control_source = runner_controls_source or resolved_source
    controller = runner_controller
    if controller is None and runner_controller_auto_build:
        controller = _build_runner_controller(repo_root, cfg, cfg_path)
    runner_control = _runner_control_payload(
        controller,
        repo=repo_root,
        enabled=control_enabled,
        source=control_source,
        current_run_dir=latest_run_dir.as_posix() if latest_run_dir else "",
        last_action=runner_control_last_action,
        last_message=runner_control_last_message,
        last_error=runner_control_last_error,
        run_status=str(progress.get("run_status") or "idle"),
        busy=bool(runner_control_busy),
    )
    active_run_empty = active_run["status"] == "idle" and not active_run.get("task") and not active_run.get("startedAt")
    runner_control_state = "ready" if runner_control["controller_available"] and runner_control["enabled"] else ("disabled" if runner_control["controller_available"] else "error")
    goals_total = sum(len(items) for items in goals.values()) if isinstance(goals, dict) else 0
    has_metrics = bool(
        metrics.get("tokens24h")
        or metrics.get("success24h")
        or metrics.get("budget")
        or metrics.get("last_stage")
        or metrics.get("quota_used")
        or any(int(value or 0) for value in (metrics.get("tokens") or {}).values())
    )
    active_run_section_state = buildSectionState("activeRun", "empty" if active_run_empty else "ready", fallbackSectionMessage("activeRun") if active_run_empty else "")
    stages_section_state = buildSectionState("stages", "ready" if stages else "empty", "" if stages else fallbackSectionMessage("stages"))
    backlog_section_state = buildSectionState("backlog", "ready" if backlog.get("items") else "empty", "" if backlog.get("items") else fallbackSectionMessage("backlog"))
    goals_section_state = buildSectionState("goals", "ready" if goals_total else "empty", "" if goals_total else fallbackSectionMessage("goals"))
    config_section_state = buildSectionState("config", "ready" if cfg else "empty", "" if cfg else fallbackSectionMessage("config"))
    prompts_section_state = buildSectionState("prompts", "ready" if prompt_items else "empty", "" if prompt_items else fallbackSectionMessage("prompts"))
    logs_section_state = buildSectionState("logs", "ready" if log_entries else "empty", "" if log_entries else fallbackSectionMessage("logs"))
    notifications_section_state = buildSectionState("notifications", "ready" if notifications else "empty", "" if notifications else fallbackSectionMessage("notifications"))
    metrics_section_state = buildSectionState("metrics", "ready" if has_metrics else "empty", "" if has_metrics else fallbackSectionMessage("metrics"))
    history_section_state = buildSectionState("history", "ready" if history.get("items") else "empty", "" if history.get("items") else fallbackSectionMessage("history"))
    worktree_section_state = buildSectionState(
        "worktree",
        "error" if worktree.get("status") == "error" else ("ready" if worktree.get("status") and worktree.get("status") != "none" else "empty"),
        worktree.get("reviewRequiredMessage") or worktree.get("summary") or (fallbackSectionMessage("worktree") if worktree.get("status") == "none" else ""),
    )
    runner_control_section_state = buildSectionState(
        "runnerControl",
        runner_control_state,
        runner_control.get("message") or fallbackSectionMessage("runnerControl"),
    )

    return {
        "ok": True,
        "repo": {
            "path": repo_root.as_posix(),
            "name": repo_root.name or repo_root.as_posix().rsplit("/", 1)[-1],
            "head": _git_head_short(repo_root),
            "branch": branch or "HEAD",
        },
        "latest_run_dir": latest_run_dir.as_posix() if latest_run_dir else None,
        "active_run": active_run,
        "stages": stages,
        "backlog": backlog,
        "goals": {
            "path": goals_path.as_posix() if goals_path else None,
            "completion": goals_completion,
            "items": goals,
        },
        "logs": {
            "entries": log_entries,
            "tail": _tail_text((latest_run_dir / "cycle_summary.log") if latest_run_dir else Path(""), 80),
            "files": {
                "cycle_summary": (latest_run_dir / "cycle_summary.log").as_posix() if latest_run_dir else "",
                "run_log": (latest_run_dir / "logs" / "run.log").as_posix() if latest_run_dir else "",
                "metrics": (latest_run_dir / "metrics.jsonl").as_posix() if latest_run_dir else "",
            },
        },
        "config": {
            "path": cfg_path.as_posix(),
            "source": cfg_source,
            "data": _redact_config(cfg),
            "resolved_prompts_dir": prompts_dir.as_posix(),
        },
        "prompts": {
            "dir": prompts_dir.as_posix(),
            "exists": prompts_dir.exists(),
            "items": prompt_items,
        },
        "history": history,
        "metrics": metrics,
        "notifications": notifications,
        "worktree": worktree,
        "runner_control": runner_control,
        "progress": {
            "latest_run_dir": latest_run_dir.as_posix() if latest_run_dir else None,
            "run_status": progress.get("run_status"),
            "tasks_done": progress.get("tasks_done", 0),
            "tasks_total": progress.get("tasks_total", 0),
            "tasks_failed": progress.get("tasks_failed", 0),
            "progress": progress.get("progress", 0.0),
            "current_task_id": progress.get("current_task_id", ""),
            "current_task_title": progress.get("current_task_title", ""),
            "goals": progress.get("goals", {}),
            "backlog": backlog,
            "final_reason": progress.get("final_reason", ""),
            "state": state,
        },
        "sectionState": {
            "activeRun": active_run_section_state,
            "stages": stages_section_state,
            "backlog": backlog_section_state,
            "goals": goals_section_state,
            "config": config_section_state,
            "prompts": prompts_section_state,
            "logs": logs_section_state,
            "notifications": notifications_section_state,
            "metrics": metrics_section_state,
            "history": history_section_state,
            "worktree": worktree_section_state,
            "runnerControl": runner_control_section_state,
        },
        "run_summary": run_summary,
        "last_run_summary": last_run_summary,
    }


def build_health(repo: Path | str | None = None) -> dict[str, Any]:
    snapshot = build_snapshot(repo)
    progress = snapshot.get("progress", {}) if isinstance(snapshot.get("progress"), dict) else {}
    runner_control = snapshot.get("runner_control", {}) if isinstance(snapshot.get("runner_control"), dict) else {}
    return {
        "ok": bool(snapshot.get("ok", False)),
        "repo": snapshot.get("repo", {}),
        "latest_run_dir": snapshot.get("latest_run_dir"),
        "status": progress.get("run_status", "idle"),
        "runner_control": runner_control,
        "timestamp": now_iso(),
    }


def _ensure_fastapi() -> None:
    if FastAPI is None or FileResponse is None:
        raise RuntimeError("FastAPI is not installed. Add the declared dependencies before serving the web console.")


def _resolve_web_dir(web_dir: Path | str | None) -> Path:
    if web_dir is not None and str(web_dir).strip():
        return Path(web_dir).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "web_console"


def create_app(
    repo: Path | str | None = None,
    *,
    web_dir: Path | str | None = None,
    config_path: str | None = None,
    enable_runner_controls: bool | None = None,
) -> Any:
    _ensure_fastapi()
    repo_root = _repo_root(repo)
    static_root = _resolve_web_dir(web_dir)
    cfg_path, cfg, _ = _load_config_payload(repo_root, config_path)
    controller = _build_runner_controller(repo_root, cfg, cfg_path)

    controls_enabled, controls_source = _resolve_runner_controls_enabled(enable_runner_controls)
    control_state: dict[str, str] = {
        "last_action": "",
        "last_message": "",
        "last_error": "",
    }
    control_lock = threading.Lock()

    app = FastAPI(
        title="AgentCLI Web Console",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.repo = repo_root
    app.state.web_dir = static_root
    app.state.config_path = config_path
    app.state.runner_controller = controller
    app.state.runner_controls_enabled = controls_enabled
    app.state.runner_controls_source = controls_source
    app.state.runner_control_lock = control_lock

    def _snapshot(*, busy_override: bool | None = None) -> dict[str, Any]:
        return build_snapshot(
            repo_root,
            config_path=config_path,
            runner_controller=controller,
            runner_controller_auto_build=controller is not None,
            runner_controls_enabled=controls_enabled,
            runner_controls_source=controls_source,
            runner_control_busy=control_lock.locked() if busy_override is None else bool(busy_override),
            runner_control_last_action=control_state["last_action"],
            runner_control_last_message=control_state["last_message"],
            runner_control_last_error=control_state["last_error"],
        )

    def _section(name: str) -> Any:
        return _snapshot()[name]

    def _runner_control_snapshot() -> dict[str, Any]:
        snap = _snapshot()
        control = snap.get("runner_control", {})
        return {
            "ok": bool(snap.get("ok", False)),
            "repo": snap.get("repo", {}),
            "latest_run_dir": snap.get("latest_run_dir"),
            "progress": snap.get("progress", {}),
            "runner_control": control,
            "source": control.get("source", ""),
            "enabled": bool(control.get("enabled")),
            "controller_available": bool(control.get("controller_available")),
            "busy": bool(control.get("busy")),
            "run_status": control.get("run_status", ""),
            "status": control.get("status", {}),
            "message": control.get("message", ""),
            "actions": control.get("actions", {}),
            "confirmation": control.get("confirmation", {}),
            "last_action": control.get("last_action", ""),
            "last_message": control.get("last_message", ""),
            "last_error": control.get("last_error", ""),
        }

    def _runner_control_response(
        *,
        action: str,
        status_code: int,
        ok: bool,
        status: str,
        message: str,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        busy_override: bool | None = None,
    ) -> Any:
        snapshot = _snapshot(busy_override=busy_override)
        payload = {
            "ok": ok,
            "action": action,
            "status": status,
            "message": message,
            "runner_control": snapshot.get("runner_control", {}),
            "snapshot": snapshot,
            "repo": snapshot.get("repo", {}),
            "latest_run_dir": snapshot.get("latest_run_dir"),
            "progress": snapshot.get("progress", {}),
        }
        if error_code:
            payload["error"] = {
                "code": error_code,
                "message": message,
            }
            if details:
                payload["error"]["details"] = details
        if result is not None:
            payload["result"] = result
        return JSONResponse(status_code=status_code, content=payload)

    def _runner_control_disabled(action: str) -> Any:
        control = _runner_control_snapshot().get("runner_control", {})
        message = str(control.get("message") or "Runner controls are disabled.")
        return _runner_control_response(
            action=action,
            status_code=403,
            ok=False,
            status="disabled",
            message=message,
            error_code="runner_controls_disabled",
            details={
                "enabled": bool(control.get("enabled")),
                "source": control.get("source", ""),
            },
        )

    def _runner_control_unavailable(action: str) -> Any:
        return _runner_control_response(
            action=action,
            status_code=503,
            ok=False,
            status="error",
            message="Runner controller is unavailable.",
            error_code="runner_controller_unavailable",
        )

    async def _runner_control_body(request: Request) -> dict[str, Any] | None:
        try:
            payload = await request.json()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    async def _handle_runner_action(action: str, request: Request) -> Any:
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"start", "stop", "reload", "restart"}:
            return _runner_control_response(
                action=normalized_action or "unknown",
                status_code=400,
                ok=False,
                status="error",
                message="Unknown runner action.",
                error_code="runner_action_unknown",
            )

        if not controls_enabled:
            return _runner_control_disabled(normalized_action)

        if controller is None:
            return _runner_control_unavailable(normalized_action)

        if not control_lock.acquire(blocking=False):
            return _runner_control_response(
                action=normalized_action,
                status_code=409,
                ok=False,
                status="busy",
                message="A runner control request is already in flight.",
                error_code="runner_controls_busy",
            )

        try:
            body = await _runner_control_body(request)
            if body is None:
                return _runner_control_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="error",
                    message="Runner control request body must be JSON.",
                    error_code="invalid_json",
                    busy_override=False,
                )
            provided = _runner_control_confirmation_value(body)
            expected = _runner_control_confirmation(normalized_action)
            if not provided:
                return _runner_control_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="confirmation_required",
                    message=f'Type "{expected}" to confirm this action.',
                    error_code="confirmation_required",
                    details={"expected": expected},
                    busy_override=False,
                )
            if provided != expected:
                return _runner_control_response(
                    action=normalized_action,
                    status_code=400,
                    ok=False,
                    status="confirmation_mismatch",
                    message=f'Confirmation phrase must be "{expected}".',
                    error_code="confirmation_mismatch",
                    details={"expected": expected},
                    busy_override=False,
                )

            if normalized_action == "start":
                result = controller.start()
                if not bool(result.get("ok")):
                    message = str(result.get("message") or "Runner start failed.")
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code="runner_start_failed",
                        result=result,
                        busy_override=False,
                    )
                message = str(result.get("message") or "Runner started.")
                control_state["last_action"] = normalized_action
                control_state["last_message"] = message
                control_state["last_error"] = ""
                return _runner_control_response(
                    action=normalized_action,
                    status_code=200,
                    ok=True,
                    status="started",
                    message=message,
                    result=result,
                    busy_override=False,
                )

            if normalized_action == "stop":
                result = controller.stop(wait=True)
                if not bool(result.get("ok")):
                    message = str(result.get("message") or "Runner stop failed.")
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code="runner_stop_failed",
                        result=result,
                        busy_override=False,
                    )
                message = str(result.get("message") or "Runner stopped.")
                control_state["last_action"] = normalized_action
                control_state["last_message"] = message
                control_state["last_error"] = ""
                return _runner_control_response(
                    action=normalized_action,
                    status_code=200,
                    ok=True,
                    status="stopped",
                    message=message,
                    result=result,
                    busy_override=False,
                )

            flow_name = "restart" if normalized_action == "restart" else "reload"
            current_status = {}
            try:
                current_status = controller.status()
            except Exception:
                current_status = {}
            should_stop = bool(current_status.get("running")) or bool(str(current_status.get("run_dir") or "").strip())
            stop_result: dict[str, Any] = {}
            if should_stop:
                stop_result = controller.stop(wait=False)
                if not bool(stop_result.get("ok")):
                    message = str(stop_result.get("message") or f"Runner {flow_name} stop failed.")
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code=f"runner_{flow_name}_stop_failed",
                        result={"stop": stop_result},
                        busy_override=False,
                    )
                if not _wait_for_runner_idle(controller, timeout_sec=12.0):
                    message = f"Runner did not stop before {flow_name}."
                    control_state["last_action"] = normalized_action
                    control_state["last_message"] = ""
                    control_state["last_error"] = message
                    return _runner_control_response(
                        action=normalized_action,
                        status_code=409,
                        ok=False,
                        status="error",
                        message=message,
                        error_code=f"runner_{flow_name}_timeout",
                        result={"stop": stop_result},
                        busy_override=False,
                    )

            result = controller.start()
            if not bool(result.get("ok")):
                message = str(result.get("message") or f"Runner {flow_name} failed.")
                control_state["last_action"] = normalized_action
                control_state["last_message"] = ""
                control_state["last_error"] = message
                return _runner_control_response(
                    action=normalized_action,
                    status_code=409,
                    ok=False,
                    status="error",
                    message=message,
                    error_code=f"runner_{flow_name}_failed",
                    result={"stop": stop_result, "start": result},
                    busy_override=False,
                )

            success_message = "Runner restarted." if normalized_action == "restart" else "Runner reloaded."
            message = success_message
            control_state["last_action"] = normalized_action
            control_state["last_message"] = message
            control_state["last_error"] = ""
            return _runner_control_response(
                action=normalized_action,
                status_code=200,
                ok=True,
                status="restarted" if normalized_action == "restart" else "reloaded",
                message=message,
                result={"stop": stop_result, "start": result},
                busy_override=False,
            )
        except Exception as ex:
            message = f"Runner control failed: {ex}"
            control_state["last_action"] = normalized_action
            control_state["last_message"] = ""
            control_state["last_error"] = message
            return _runner_control_response(
                action=normalized_action,
                status_code=500,
                ok=False,
                status="error",
                message=message,
                error_code="runner_control_exception",
                busy_override=False,
            )
        finally:
            control_lock.release()

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        snap = _snapshot()
        progress = snap.get("progress", {}) if isinstance(snap.get("progress"), dict) else {}
        return {
            "ok": bool(snap.get("ok", False)),
            "repo": snap.get("repo", {}),
            "latest_run_dir": snap.get("latest_run_dir"),
            "status": progress.get("run_status", "idle"),
            "runner_control": snap.get("runner_control", {}),
            "timestamp": now_iso(),
        }

    @app.get("/api/status")
    def api_status() -> dict[str, Any]:
        return _snapshot()

    @app.get("/api/progress")
    def api_progress() -> dict[str, Any]:
        snap = _snapshot()
        progress = snap.get("progress", {}) if isinstance(snap.get("progress"), dict) else {}
        return {
            "ok": bool(snap.get("ok", False)),
            "repo": snap.get("repo", {}),
            "latest_run_dir": snap.get("latest_run_dir"),
            "active_run": snap.get("active_run", {}),
            "stages": snap.get("stages", []),
            "backlog": snap.get("backlog", {}),
            "goals": snap.get("goals", {}),
            "logs": snap.get("logs", {}),
            "config": snap.get("config", {}),
            "prompts": snap.get("prompts", {}),
            "history": snap.get("history", {}),
            "metrics": snap.get("metrics", {}),
            "notifications": snap.get("notifications", []),
            "worktree": snap.get("worktree", {}),
            "runner_control": snap.get("runner_control", {}),
            "progress": progress,
            "tasks_done": progress.get("tasks_done", 0),
            "tasks_total": progress.get("tasks_total", 0),
            "tasks_failed": progress.get("tasks_failed", 0),
            "current_task_id": progress.get("current_task_id", ""),
            "current_task_title": progress.get("current_task_title", ""),
            "run_status": progress.get("run_status", "idle"),
            "final_reason": progress.get("final_reason", ""),
            "state": progress.get("state", {}),
        }

    @app.get("/api/runner/status")
    def api_runner_status() -> Any:
        return _runner_control_snapshot()

    @app.post("/api/runner/start")
    async def api_runner_start(request: Request) -> Any:
        return await _handle_runner_action("start", request)

    @app.post("/api/runner/stop")
    async def api_runner_stop(request: Request) -> Any:
        return await _handle_runner_action("stop", request)

    @app.post("/api/runner/reload")
    async def api_runner_reload(request: Request) -> Any:
        return await _handle_runner_action("reload", request)

    @app.post("/api/runner/restart")
    async def api_runner_restart(request: Request) -> Any:
        return await _handle_runner_action("restart", request)

    @app.get("/api/logs")
    def api_logs() -> dict[str, Any]:
        return _section("logs")

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return _section("config")

    @app.get("/api/prompts")
    def api_prompts() -> dict[str, Any]:
        return _section("prompts")

    @app.get("/api/history")
    def api_history() -> dict[str, Any]:
        return _section("history")

    @app.get("/api/worktree")
    def api_worktree() -> dict[str, Any]:
        return _section("worktree")

    def _serve_static_file(request_path: str = "") -> Any:
        clean = request_path.strip().lstrip("/").replace("\\", "/")
        if not clean or clean == "index.html":
            target = static_root / "index.html"
        else:
            target = (static_root / clean).resolve()
            try:
                target.relative_to(static_root.resolve())
            except Exception:
                target = static_root / "index.html"
        if not target.exists() or not target.is_file():
            target = static_root / "index.html"
        return FileResponse(target)

    @app.get("/")
    def root() -> Any:
        return _serve_static_file("")

    @app.get("/index.html")
    def index_html() -> Any:
        return _serve_static_file("index.html")

    @app.get("/{request_path:path}")
    def static_assets(request_path: str) -> Any:
        if request_path.startswith("api/"):
            if HTTPException is None:
                raise RuntimeError("API routes should not reach the static catch-all.")
            raise HTTPException(status_code=404)
        return _serve_static_file(request_path)

    return app


def serve(
    repo: Path | str | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    web_dir: Path | str | None = None,
    config_path: str | None = None,
    enable_runner_controls: bool | None = None,
) -> None:
    if uvicorn is None:
        raise RuntimeError("uvicorn is not installed. Add the declared dependencies before serving the web console.")
    app = create_app(repo, web_dir=web_dir, config_path=config_path, enable_runner_controls=enable_runner_controls)
    uvicorn.run(app, host=host, port=int(port))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve the AgentCLI web console with FastAPI.")
    parser.add_argument("--repo", default="", help="Repo root (default: the repository containing agent_runner/web.py)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (use 0.0.0.0 for LAN access)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--web-dir", default="", help="Static web console directory override")
    parser.add_argument("--config-path", default="", help="Optional explicit config path")
    parser.add_argument(
        "--enable-runner-controls",
        action="store_true",
        default=None,
        help="Allow POST runner control APIs (start/stop/reload/restart) when confirmation phrases are supplied.",
    )
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve() if str(args.repo).strip() else None
    web_dir = Path(args.web_dir).expanduser().resolve() if str(args.web_dir).strip() else None
    config_path = str(args.config_path).strip() or None
    serve(
        repo=repo,
        host=args.host,
        port=args.port,
        web_dir=web_dir,
        config_path=config_path,
        enable_runner_controls=getattr(args, "enable_runner_controls", None),
    )
    return 0


if FastAPI is not None:
    try:
        app = create_app()
    except Exception:
        app = None
else:
    app = None


if __name__ == "__main__":
    raise SystemExit(main())
