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
from .goals import goals_path, parse_goals_completion, read_goals
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
        "stages": "No lifecycle records were published yet.",
        "backlog": "No backlog artifacts were published yet.",
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


RUN_STATUS_ALIASES = {
    "complete": "success",
    "completed": "success",
    "done": "success",
    "finished": "stopped",
    "halted": "stopped",
    "stopping": "stopped",
    "stopped": "stopped",
    "stop_requested": "stopped",
    "cancelled": "stopped",
    "canceled": "stopped",
    "aborted": "stopped",
    "error": "failed",
    "ok": "success",
    "prepared_only": "success",
}
RUN_STATUS_VALUES = {"idle", "running", "stopped", "failed", "success"}


def _coerce_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return int(float(raw))
    except Exception:
        return None


def _coerce_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _coerce_optional_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return int(float(value))
        except Exception:
            return None
    raw = str(value).strip()
    if not raw:
        return None
    numeric = _coerce_optional_int(raw)
    if numeric is not None:
        return numeric
    ms = _iso_to_ms(raw)
    return ms or None


def _pick_text(*values: Any) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _pick_value(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _normalize_run_status(
    raw_status: Any,
    *,
    running: bool = False,
    exit_code: Any = None,
    final_reason: str = "",
    stop_file_exists: bool = False,
    has_run_dir: bool = False,
) -> str:
    status = _pick_text(raw_status).lower()
    status = RUN_STATUS_ALIASES.get(status, status)
    if status in RUN_STATUS_VALUES:
        return status
    if status == "no-run":
        return "idle"
    if running:
        return "running"

    reason = _pick_text(final_reason).lower()
    if reason in {"project_complete", "all_tasks_done", "completed", "success"}:
        return "success"
    if reason in {"stop_file", "stop_requested", "stopped", "user_stop", "manual_stop"} or stop_file_exists:
        return "stopped"

    rc = _coerce_optional_int(exit_code)
    if rc is not None:
        if rc == 0 and reason in {"", "ok", "prepared_only"} and has_run_dir:
            return "success"
        if rc != 0:
            return "failed"

    if reason in {"failed", "error", "exception", "abandoned", "abandon_failed", "build_failed", "test_failed", "policy_violation", "exhausted_attempts"}:
        return "failed"
    return "running" if has_run_dir else "idle"


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


def _controller_status_payload(controller: RunnerController | None) -> dict[str, Any]:
    if controller is None:
        return {}
    try:
        payload = controller.status()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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


def _parse_goal_items_and_warnings(goals_text: str | None) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {"p0": [], "p1": []}
    warnings: list[dict[str, Any]] = []
    if not goals_text or not goals_text.strip():
        return items, warnings

    checkbox_re = re.compile(r"^\s*-\s*\[(x| )\]\s*(.*)$", re.IGNORECASE)
    list_re = re.compile(r"^\s*[-*+]\s+")
    current_bucket: str | None = None
    ignore_outside_list_items = False

    for line_number, line in enumerate(goals_text.splitlines(), start=1):
        stripped = line.strip()
        lower = stripped.lower()
        if not stripped:
            continue
        heading = re.match(r"^(#+)\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip().lower()
            if level == 2 and re.match(r"p0\b", title):
                current_bucket = "p0"
                ignore_outside_list_items = False
                continue
            if level == 2 and re.match(r"p1\b", title):
                current_bucket = "p1"
                ignore_outside_list_items = False
                continue
            if level == 2:
                current_bucket = None
                ignore_outside_list_items = title.startswith("completion criteria")
                continue
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue

        match = checkbox_re.match(line)
        if match:
            done = match.group(1).strip().lower() == "x"
            if current_bucket in ("p0", "p1"):
                item_text = match.group(2).strip()
                items[current_bucket].append(
                    {
                        "done": done,
                        "checked": done,
                        "checkbox": "[x]" if done else "[ ]",
                        "text": item_text,
                        "note": "",
                        "line_number": line_number,
                        "line": line_number,
                    }
                )
            else:
                warnings.append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "reason": "checkbox_outside_goal_section",
                        "message": "Checkbox item outside P0/P1 was ignored.",
                    }
                )
            continue

        if current_bucket in ("p0", "p1"):
            warnings.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "unsupported_goal_line",
                    "message": "Non-checkbox content inside a GOALS section was ignored.",
                }
            )
            continue

        if list_re.match(line) and not ignore_outside_list_items:
            warnings.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "unsupported_list_item",
                    "message": "List item outside P0/P1 was ignored.",
                }
            )

    return items, warnings


def _build_goals_payload(repo: Path, *, completion_level: str = "all") -> dict[str, Any]:
    goal_path = goals_path(repo)
    exists = False
    mtime = None
    size = None
    try:
        exists = goal_path.exists() and goal_path.is_file()
    except OSError:
        exists = False
    if exists:
        try:
            stat = goal_path.stat()
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError:
            mtime = None
            size = None

    _path, raw_text = read_goals(repo)
    raw_text = raw_text or ""
    items, warnings = _parse_goal_items_and_warnings(raw_text)
    completion = parse_goals_completion(raw_text, completion_level=completion_level)
    p0_total = len(items["p0"])
    p1_total = len(items["p1"])
    p0_done = len([item for item in items["p0"] if item.get("done")])
    p1_done = len([item for item in items["p1"] if item.get("done")])
    total = p0_total + p1_total
    done = p0_done + p1_done
    summary = {
        "has_goals": bool(completion.get("has_goals")),
        "project_complete": bool(completion.get("project_complete")),
        "p0_total": p0_total,
        "p0_done": p0_done,
        "p1_total": p1_total,
        "p1_done": p1_done,
        "all_total": int(completion.get("all_total") or total),
        "all_done": int(completion.get("all_done") or done),
        "total": total,
        "done": done,
        "unchecked": max(0, total - done),
        "warnings": len(warnings),
    }
    return {
        "path": goal_path.as_posix(),
        "exists": bool(exists),
        "mtime": mtime,
        "size": size,
        "raw_text": raw_text,
        "items": items,
        "completion": completion,
        "summary": summary,
        "warnings": warnings,
        "completion_level": completion_level,
    }


def _goal_items(goals_text: str | None) -> dict[str, list[dict[str, Any]]]:
    return _parse_goal_items_and_warnings(goals_text)[0]


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


def _load_backlog_payload(
    run_dir: Path | None,
    state: dict[str, Any],
    *,
    current_task_id: str = "",
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    tasks = _load_tasks(run_dir)
    done_ids = set(str(item) for item in (state.get("done") or []) if str(item).strip())
    failed_items = state.get("failed") if isinstance(state.get("failed"), list) else []
    failed_lookup: dict[str, dict[str, Any]] = {}
    for item in failed_items:
        if not isinstance(item, dict):
            continue
        task_id = _pick_text(item.get("task"), item.get("task_id"))
        if not task_id:
            continue
        failed_lookup[task_id] = {
            "reason": _pick_text(item.get("reason"), item.get("status")),
            "detail": _pick_text(item.get("detail"), item.get("message")),
            "attempt": _coerce_optional_int(item.get("attempt")),
            "cycle": _coerce_optional_int(item.get("cycle")),
            "step": _coerce_optional_int(item.get("step")),
            "rc": _coerce_optional_int(item.get("rc")),
        }

    runtime_index = _task_runtime_index(events or [])

    backlog: list[dict[str, Any]] = []
    selected_id = ""
    selected_started_at = -1
    for index, task in enumerate(tasks):
        status = "pending"
        if task.id in done_ids:
            status = "done"
        elif task.id in failed_lookup:
            status = "failed"

        runtime = runtime_index.get(task.id, {})
        started_at = _coerce_optional_int(runtime.get("startedAt"))
        ended_at = _coerce_optional_int(runtime.get("endedAt"))
        runtime_attempt = _coerce_optional_int(runtime.get("attempt"))
        runtime_cycle = _coerce_optional_int(runtime.get("cycle"))
        runtime_step = _coerce_optional_int(runtime.get("step"))
        runtime_reason = _pick_text(runtime.get("reason"))
        runtime_message = _pick_text(runtime.get("lastMessage"), runtime.get("lastEvent"))
        runtime_status = _normalize_lifecycle_status(
            runtime.get("status"),
            rc=runtime.get("rc"),
            reason=runtime_reason,
            running=bool(started_at is not None and ended_at is None and status not in {"done", "failed"}),
            has_activity=started_at is not None or ended_at is not None or bool(runtime_reason or runtime_message),
            default=status,
        )
        if task.id == current_task_id:
            status = "in_progress"
        elif task.id in done_ids:
            status = "done"
        elif task.id in failed_lookup:
            status = "failed"
        elif runtime_status == "running" or runtime_status == "in_progress":
            status = "in_progress"
        elif runtime_status == "done":
            status = "done"
        elif runtime_status == "failed":
            status = "failed"

        if status == "in_progress":
            if task.id == current_task_id or (started_at is not None and started_at > selected_started_at):
                selected_id = task.id
                selected_started_at = started_at or selected_started_at
            if task.id == current_task_id and selected_started_at < 0:
                selected_started_at = started_at or 0

        failure = failed_lookup.get(task.id, {})
        failure_reason = _pick_text(failure.get("reason"))
        failure_detail = _pick_text(failure.get("detail"))
        if status == "failed":
            if not failure_reason:
                failure_reason = runtime_reason
            if not failure_detail:
                failure_detail = runtime_message
        attempt = _coerce_optional_int(_pick_value(failure.get("attempt"), runtime_attempt))
        file_scope = _task_file_scope(task.files)
        recent_output = _task_output_excerpt(
            run_dir,
            stage_name="Dev",
            cycle=runtime_cycle,
            step=runtime_step,
            task_id=task.id,
            attempt=attempt,
            reason=failure_reason,
            fallback_text=failure_detail or runtime_message,
        )

        backlog.append(
            {
                "id": task.id,
                "title": task.title,
                "prompt": task.prompt,
                "files": task.files,
                "file_scope": file_scope,
                "done_when": task.done_when,
                "skills": task.skills,
                "skills_rationale": task.skills_rationale,
                "depends_on": task.depends_on,
                "status": status,
                "priority": _task_priority(task, index),
                "tags": _task_tags(task),
                "estimate": _task_estimate(task),
                "skill": task.skills[0] if task.skills else None,
                "attempt": attempt,
                "failure": {
                    "reason": failure_reason,
                    "detail": failure_detail,
                    "cycle": failure.get("cycle"),
                    "step": failure.get("step"),
                    "rc": failure.get("rc"),
                },
                "failure_reason": failure_reason,
                "failure_detail": failure_detail,
                "recent_output": recent_output,
                "cycle": runtime_cycle,
                "step": runtime_step,
                "task_title": runtime.get("taskTitle") or task.title,
                "model": runtime.get("model") or "",
                "started_at": started_at,
                "ended_at": ended_at,
            }
        )

    if current_task_id and any(item["id"] == current_task_id for item in backlog):
        selected_id = current_task_id

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


def _normalize_stage_name(value: Any) -> str:
    raw = _pick_text(value).strip().lower()
    if not raw:
        return ""
    if raw.startswith("pm") or raw in {"planner", "planning", "pm_stage"}:
        return "PM"
    if raw.startswith("dev") or raw.startswith("task") or raw.startswith("build") or raw.startswith("test") or raw in {"implementation"}:
        return "Dev"
    if raw.startswith("qa") or raw in {"verification", "qa_stage"}:
        return "QA"
    return ""


def _normalize_lifecycle_status(
    raw_status: Any,
    *,
    rc: Any = None,
    reason: str = "",
    running: bool = False,
    has_activity: bool = False,
    default: str = "pending",
) -> str:
    normalized = _pick_text(raw_status).strip().lower()
    aliases = {
        "complete": "done",
        "completed": "done",
        "done": "done",
        "ok": "done",
        "success": "done",
        "skip": "skipped",
        "skipped": "skipped",
        "stop": "stopped",
        "stopped": "stopped",
        "halted": "stopped",
        "cancelled": "stopped",
        "canceled": "stopped",
        "fail": "failed",
        "failed": "failed",
        "error": "failed",
        "running": "running",
        "active": "running",
        "in_progress": "running",
        "pending": "pending",
        "idle": "pending",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized in {"done", "running", "pending", "failed", "stopped", "skipped"}:
        if running and normalized in {"pending", "skipped"}:
            return "running"
        return normalized

    if running:
        return "running"

    rc_value = _coerce_optional_int(rc)
    if rc_value is not None:
        if rc_value == 0:
            reason_value = _pick_text(reason).strip().lower()
            if reason_value in {"stop_file", "stop_requested", "stopped", "manual_stop"}:
                return "stopped"
            return "done"
        return "failed"

    reason_value = _pick_text(reason).strip().lower()
    if reason_value in {"project_complete", "all_tasks_done", "completed", "success", "ok", "done"}:
        return "done"
    if reason_value in {"stop_file", "stop_requested", "stopped", "manual_stop", "quota_exhausted"}:
        return "stopped"
    if reason_value in {"failed", "error", "exception", "abandoned", "abandon_failed", "build_failed", "test_failed", "policy_violation", "exhausted_attempts", "needs_dependency", "blocked_dependency", "no_diff"}:
        return "failed"
    return "running" if has_activity else default


def _task_file_scope(files: list[str], *, limit: int = 3) -> str:
    cleaned: list[str] = []
    for path in files:
        text = str(path).replace("\\", "/").strip()
        if text and text not in cleaned:
            cleaned.append(text)
    if not cleaned:
        return ""
    if len(cleaned) <= max(1, int(limit)):
        return ", ".join(cleaned)
    limit_i = max(1, int(limit))
    return ", ".join(cleaned[:limit_i]) + f" (+{len(cleaned) - limit_i} more)"


def _task_runtime_index(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        task_id = _pick_text(event.get("task_id"), event.get("task"))
        if not task_id:
            continue

        entry = index.setdefault(
            task_id,
            {
                "taskId": task_id,
                "taskTitle": "",
                "cycle": None,
                "step": None,
                "attempt": None,
                "model": "",
                "startedAt": None,
                "endedAt": None,
                "rc": None,
                "reason": "",
                "lastMessage": "",
                "lastEvent": "",
            },
        )

        event_type = str(event.get("event") or event.get("type") or "").strip().lower()
        ts = _iso_to_ms(event.get("ts"))
        cycle = _coerce_optional_int(event.get("cycle"))
        step = _coerce_optional_int(event.get("step"))
        attempt = _coerce_optional_int(event.get("attempt"))
        model = _pick_text(event.get("model"))
        task_title = _pick_text(event.get("task_title"), event.get("title"))
        reason = _pick_text(event.get("reason"))
        message = _event_message(event)

        if cycle is not None:
            current_cycle = _coerce_optional_int(entry.get("cycle"))
            entry["cycle"] = cycle if current_cycle is None else max(current_cycle, cycle)
        if step is not None and entry.get("step") is None:
            entry["step"] = step
        if attempt is not None:
            current_attempt = _coerce_optional_int(entry.get("attempt"))
            entry["attempt"] = attempt if current_attempt is None else max(current_attempt, attempt)
        if model and not entry.get("model"):
            entry["model"] = model
        if task_title and not entry.get("taskTitle"):
            entry["taskTitle"] = task_title
        if reason and not entry.get("reason"):
            entry["reason"] = reason
        if reason or message:
            entry["lastMessage"] = reason or message
            entry["lastEvent"] = message or reason

        if event_type in {"task_start", "dev_attempt_start"}:
            if ts and (entry.get("startedAt") is None or ts < int(entry.get("startedAt") or 0)):
                entry["startedAt"] = ts
            if event_type == "dev_attempt_start" and model:
                entry["model"] = model
            if event_type == "dev_attempt_start" and attempt is not None:
                current_attempt = _coerce_optional_int(entry.get("attempt"))
                entry["attempt"] = attempt if current_attempt is None else max(current_attempt, attempt)
        elif event_type in {"task_end", "build_end", "test_end"}:
            if ts and (entry.get("endedAt") is None or ts > int(entry.get("endedAt") or 0)):
                entry["endedAt"] = ts
            rc = _coerce_optional_int(event.get("rc"))
            if rc is not None:
                entry["rc"] = rc
            if reason:
                entry["reason"] = reason
        elif event_type == "dev_attempt_retry":
            if ts and (entry.get("endedAt") is None or ts > int(entry.get("endedAt") or 0)):
                entry["endedAt"] = ts
            if reason:
                entry["reason"] = reason
            if attempt is not None:
                current_attempt = _coerce_optional_int(entry.get("attempt"))
                entry["attempt"] = (attempt + 1) if current_attempt is None else max(current_attempt, attempt + 1)

    for entry in index.values():
        started_at = _coerce_optional_int(entry.get("startedAt"))
        ended_at = _coerce_optional_int(entry.get("endedAt"))
        if started_at is not None and ended_at is not None and ended_at >= started_at:
            entry["durationSec"] = round((ended_at - started_at) / 1000.0, 3)
        else:
            entry["durationSec"] = None
    return index


def _task_output_excerpt(
    run_dir: Path | None,
    *,
    stage_name: str,
    cycle: int | None = None,
    step: int | None = None,
    task_id: str = "",
    attempt: int | None = None,
    reason: str = "",
    fallback_text: str = "",
) -> str:
    if run_dir is None:
        return _pick_text(fallback_text)

    candidates: list[Path] = []
    stage_key = _normalize_stage_name(stage_name)
    cycle_i = _coerce_optional_int(cycle)
    step_i = _coerce_optional_int(step)
    attempt_i = _coerce_optional_int(attempt)

    if stage_key == "PM" and cycle_i is not None:
        candidates.extend(
            [
                run_dir / f"pm_final_output_cycle_{cycle_i:03d}.txt",
                run_dir / "NOTES_PM.md",
                run_dir / "cycle_summary.log",
                run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
            ]
        )
    elif stage_key == "QA" and cycle_i is not None:
        candidates.extend(
            [
                run_dir / f"qa_followups_cycle_{cycle_i:03d}.json",
                run_dir / "cycle_summary.log",
                run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
            ]
        )
    elif task_id and cycle_i is not None and step_i is not None and attempt_i is not None:
        task_dir = run_dir / "tasks" / f"c{cycle_i:03d}_s{step_i:03d}_{task_id}" / f"attempt_{attempt_i:02d}"
        reason_text = _pick_text(reason).lower()
        if "build" in reason_text:
            candidates.append(task_dir / "build.txt")
        if "test" in reason_text:
            candidates.append(task_dir / "test.txt")
        candidates.extend(
            [
                task_dir / "dev_output.txt",
                task_dir / "NOTES.md",
                task_dir / "DEPENDENCY_REQUIRED.md",
                run_dir / "dev_logs" / f"c{cycle_i:03d}_s{step_i:03d}_{task_id}_a{attempt_i:02d}.txt",
            ]
        )
    elif task_id and cycle_i is not None:
        candidates.extend(
            [
                run_dir / "cycle_summary.log",
                run_dir / f"run_summary_cycle_{cycle_i:03d}.json",
            ]
        )

    for candidate in candidates:
        text = _tail_text(candidate, 12)
        if text:
            return text
    return _pick_text(fallback_text)


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


def _normalize_log_tail_level(value: Any) -> str:
    level = str(value or "").strip().lower()
    if level == "warning":
        return "warn"
    if level == "error":
        return "err"
    return level


def _log_tail_source_candidates(run_dir: Path | None) -> list[Path]:
    if run_dir is None:
        return []
    return [
        run_dir / "metrics.jsonl",
        run_dir / "logs" / "events.jsonl",
        run_dir / "logs" / "run.log",
    ]


def _resolve_log_tail_source(run_dir: Path | None) -> Path | None:
    candidates = _log_tail_source_candidates(run_dir)
    if not candidates:
        return None
    for candidate in candidates:
        try:
            if candidate.exists() and candidate.is_file():
                return candidate
        except OSError:
            continue
    return candidates[0]


def _log_tail_entry_search_text(entry: dict[str, Any]) -> str:
    parts = [
        entry.get("t"),
        entry.get("ts"),
        entry.get("lvl"),
        entry.get("level"),
        entry.get("stage"),
        entry.get("task_id"),
        entry.get("taskId"),
        entry.get("task_title"),
        entry.get("taskTitle"),
        entry.get("event"),
        entry.get("type"),
        entry.get("msg"),
        entry.get("message"),
        entry.get("text"),
        entry.get("reason"),
        entry.get("raw"),
    ]
    return " ".join(str(part).lower() for part in parts if part is not None and str(part).strip())


def _normalize_structured_log_tail_entry(payload: dict[str, Any], *, raw_line: str, line_number: int) -> dict[str, Any]:
    ts = _pick_text(payload.get("ts"), payload.get("timestamp"), payload.get("time"))
    level = _normalize_log_tail_level(_pick_text(payload.get("lvl"), payload.get("level"), _event_level(payload)))
    stage = _pick_text(payload.get("stage"), payload.get("component"), payload.get("scope")) or _event_stage(payload)
    message = _pick_text(payload.get("msg"), payload.get("message"), payload.get("text"), _event_message(payload), raw_line)
    task_id = _pick_text(payload.get("task_id"), payload.get("taskId"))
    task_title = _pick_text(payload.get("task_title"), payload.get("taskTitle"))
    event = _pick_text(payload.get("event"), payload.get("type"))
    return {
        "cursor": line_number,
        "line_number": line_number,
        "raw": raw_line,
        "ts": ts,
        "t": _fmt_clock(ts) if ts else "",
        "lvl": level or "info",
        "level": level or "info",
        "stage": stage or "boot",
        "msg": message,
        "message": message,
        "task_id": task_id,
        "taskId": task_id,
        "task_title": task_title,
        "taskTitle": task_title,
        "event": event,
        "type": event,
        "cycle": _coerce_optional_int(payload.get("cycle")),
        "step": _coerce_optional_int(payload.get("step")),
        "attempt": _coerce_optional_int(payload.get("attempt")),
        "reason": _pick_text(payload.get("reason")),
        "rc": _coerce_optional_int(payload.get("rc")),
    }


def _normalize_plain_log_tail_entry(raw_line: str, *, line_number: int) -> dict[str, Any]:
    pattern = re.compile(r"^(?:(?P<date>\d{4}-\d{2}-\d{2})\s+)?(?P<time>\d{2}:\d{2}:\d{2})\s+\[(?P<level>[A-Z]+)\]\s*(?P<msg>.*)$")
    match = pattern.match(raw_line)
    if match:
        level = _normalize_log_tail_level(match.group("level"))
        msg = match.group("msg").strip()
        ts = " ".join(part for part in (match.group("date"), match.group("time")) if part).strip()
        return {
            "cursor": line_number,
            "line_number": line_number,
            "raw": raw_line,
            "ts": ts,
            "t": match.group("time"),
            "lvl": level or "info",
            "level": level or "info",
            "stage": "boot",
            "msg": msg,
            "message": msg,
            "task_id": "",
            "taskId": "",
            "task_title": "",
            "taskTitle": "",
            "event": "",
            "type": "",
            "cycle": None,
            "step": None,
            "attempt": None,
            "reason": "",
            "rc": None,
        }
    msg = raw_line.strip()
    return {
        "cursor": line_number,
        "line_number": line_number,
        "raw": raw_line,
        "ts": "",
        "t": "",
        "lvl": "info",
        "level": "info",
        "stage": "boot",
        "msg": msg,
        "message": msg,
        "task_id": "",
        "taskId": "",
        "task_title": "",
        "taskTitle": "",
        "event": "",
        "type": "",
        "cycle": None,
        "step": None,
        "attempt": None,
        "reason": "",
        "rc": None,
    }


def _parse_log_tail_entry(raw_line: str, *, line_number: int, source_path: Path) -> tuple[dict[str, Any] | None, bool]:
    raw = raw_line.rstrip("\n")
    if not raw.strip():
        return None, False
    if source_path.suffix.lower() == ".jsonl":
        try:
            payload = json.loads(raw)
        except Exception:
            return None, True
        if not isinstance(payload, dict):
            return None, True
        return _normalize_structured_log_tail_entry(payload, raw_line=raw, line_number=line_number), False
    return _normalize_plain_log_tail_entry(raw, line_number=line_number), False


def _log_tail_entry_matches(
    entry: dict[str, Any],
    *,
    level: str = "",
    stage: str = "",
    task_id: str = "",
    search: str = "",
) -> bool:
    level_filter = _normalize_log_tail_level(level)
    if level_filter and level_filter not in {"all", "any", "*"}:
        entry_level = _normalize_log_tail_level(entry.get("lvl") or entry.get("level"))
        if entry_level != level_filter:
            return False

    stage_filter = _pick_text(stage).lower()
    if stage_filter and stage_filter not in {"all", "any", "*"}:
        entry_stage = _pick_text(entry.get("stage")).lower()
        if entry_stage != stage_filter:
            return False

    task_filter = _pick_text(task_id).lower()
    if task_filter:
        entry_task = _pick_text(entry.get("task_id"), entry.get("taskId")).lower()
        if entry_task != task_filter:
            return False

    search_filter = _pick_text(search).lower()
    if search_filter and search_filter not in _log_tail_entry_search_text(entry):
        return False

    return True


def _build_log_tail_payload(
    source_path: Path,
    *,
    cursor: int | None,
    max_lines: int,
    level: str = "",
    stage: str = "",
    task_id: str = "",
    search: str = "",
    live: bool = False,
) -> dict[str, Any]:
    max_lines = max(1, int(max_lines))
    source_file = source_path.expanduser().resolve()
    entries: list[dict[str, Any]] = []
    malformed_count = 0
    total_lines = 0
    next_cursor = 0
    cursor_mode = cursor is not None
    start_cursor = max(0, int(cursor or 0))

    try:
        with source_file.open("r", encoding="utf-8", errors="replace") as handle:
            if cursor_mode:
                for line_number, raw_line in enumerate(handle, start=1):
                    total_lines = line_number
                    if line_number <= start_cursor:
                        continue
                    next_cursor = line_number
                    entry, malformed = _parse_log_tail_entry(raw_line, line_number=line_number, source_path=source_file)
                    if malformed:
                        malformed_count += 1
                        continue
                    if entry is None or not _log_tail_entry_matches(entry, level=level, stage=stage, task_id=task_id, search=search):
                        continue
                    entries.append(entry)
                    if len(entries) >= max_lines:
                        break
            else:
                matched: deque[dict[str, Any]] = deque(maxlen=max_lines)
                for line_number, raw_line in enumerate(handle, start=1):
                    total_lines = line_number
                    next_cursor = line_number
                    entry, malformed = _parse_log_tail_entry(raw_line, line_number=line_number, source_path=source_file)
                    if malformed:
                        malformed_count += 1
                        continue
                    if entry is None or not _log_tail_entry_matches(entry, level=level, stage=stage, task_id=task_id, search=search):
                        continue
                    matched.append(entry)
                entries = list(matched)
    except FileNotFoundError:
        return {
            "ok": False,
            "state": "missing_file",
            "entries": [],
            "next_cursor": 0,
            "source_file": source_file.as_posix(),
            "source_path": source_file.as_posix(),
            "source": {
                "path": source_file.as_posix(),
                "name": source_file.name,
                "exists": False,
            },
            "malformed_lines": 0,
        }
    except Exception as ex:
        return {
            "ok": False,
            "state": "read_error",
            "entries": [],
            "next_cursor": start_cursor,
            "source_file": source_file.as_posix(),
            "source_path": source_file.as_posix(),
            "source": {
                "path": source_file.as_posix(),
                "name": source_file.name,
                "exists": source_file.exists(),
            },
            "error": str(ex).strip() or ex.__class__.__name__,
            "malformed_lines": malformed_count,
        }

    if cursor_mode and next_cursor == 0:
        next_cursor = total_lines

    state = "loading" if (entries or live or (cursor_mode and total_lines > start_cursor)) else "empty"
    if malformed_count:
        state = "malformed_line"

    return {
        "ok": True,
        "state": state,
        "entries": entries,
        "next_cursor": next_cursor if cursor_mode else total_lines,
        "source_file": source_file.as_posix(),
        "source_path": source_file.as_posix(),
        "source": {
            "path": source_file.as_posix(),
            "name": source_file.name,
            "exists": source_file.exists(),
        },
        "cursor": start_cursor if cursor_mode else None,
        "max_lines": max_lines,
        "malformed_lines": malformed_count,
    }


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
    controller_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    repo_path = repo.as_posix()
    run_id = _pick_text(controller_data.get("run_id"), controller_data.get("runId"), run_dir.name if run_dir else "", "no-run")
    run_id = run_id or "no-run"
    run_dir_text = _pick_text(controller_data.get("run_dir"), run_dir.as_posix() if run_dir else "")
    run_dir_exists = False
    if run_dir is not None:
        try:
            run_dir_exists = run_dir.exists() and run_dir.is_dir()
        except OSError:
            run_dir_exists = False
    started_at_ms = _coerce_optional_ms(_pick_value(controller_data.get("startedAt"), controller_data.get("started_at")))
    uptime_seconds = _coerce_optional_int(_pick_value(controller_data.get("uptime_seconds"), controller_data.get("uptimeSeconds")))
    if started_at_ms is None and run_dir_exists:
        try:
            started_at_ms = _epoch_ms(run_dir.stat().st_ctime)
        except OSError:
            started_at_ms = None
    if started_at_ms is None and uptime_seconds is not None:
        started_at_ms = max(0, _epoch_ms(time.time()) - (uptime_seconds * 1000))
    started_at_ms = started_at_ms or 0
    ended_at_ms = 0
    if run_dir_exists:
        try:
            ended_at_ms = _epoch_ms(run_dir.stat().st_mtime)
        except OSError:
            ended_at_ms = 0
    elapsed_sec = _coerce_optional_int(_pick_value(controller_data.get("elapsedSec"), controller_data.get("elapsed_seconds"), controller_data.get("elapsedSeconds")))
    if elapsed_sec is None and uptime_seconds is not None:
        elapsed_sec = max(0, uptime_seconds)
    if elapsed_sec is None and started_at_ms and ended_at_ms:
        elapsed_sec = max(0, int((ended_at_ms - started_at_ms) / 1000))
    if elapsed_sec is None:
        elapsed_sec = 0

    cycles = run_summary.get("cycles") if isinstance(run_summary.get("cycles"), list) else []
    final_reason_text = _pick_text(
        controller_data.get("final_reason"),
        controller_data.get("reason"),
        progress.get("final_reason"),
        last_run_summary.get("stop_reason"),
        run_summary.get("final").get("reason") if isinstance(run_summary.get("final"), dict) else "",
    )

    tasks_total = _coerce_optional_int(progress.get("tasks_total"))
    if tasks_total is None:
        tasks_total = len(backlog)
    tasks_done = int(progress.get("tasks_done") or 0)
    tasks_failed = int(progress.get("tasks_failed") or 0)
    progress_available = bool(progress.get("progress_available"))
    progress_value = _coerce_optional_float(_pick_value(progress.get("progress"), progress.get("progress_value"), progress.get("progressValue")))
    if not progress_available:
        progress_value = None

    run_status = _normalize_run_status(
        controller_data.get("run_status") or controller_data.get("status") or progress.get("run_status") or progress.get("status"),
        running=bool(controller_data.get("running")),
        exit_code=controller_data.get("exit_code"),
        final_reason=final_reason_text,
        stop_file_exists=bool(controller_data.get("stop_file_exists") or (run_dir_exists and (run_dir / "STOP").exists())),
        has_run_dir=bool(run_dir),
    )

    last_event_stage = _pick_text(controller_data.get("stage"), controller_data.get("current_stage"), metrics.get("last_stage"), progress.get("last_stage"))
    if not last_event_stage:
        if run_status == "success":
            last_event_stage = "QA"
        elif run_status == "idle":
            last_event_stage = "idle"
        else:
            last_event_stage = "Dev"

    task_id = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    )

    task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
        progress.get("current_task_title"),
    )
    if not task_title and task_id:
        task_title = next((item["title"] for item in backlog if item.get("id") == task_id), "")

    attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            progress.get("attempt"),
            progress.get("current_attempt"),
            run_summary.get("attempt"),
            last_run_summary.get("attempt"),
        )
    )

    worktree_mode = _pick_text(
        controller_data.get("worktree_mode"),
        controller_data.get("worktreeMode"),
        progress.get("worktree_mode"),
        progress.get("worktreeMode"),
    )
    if not worktree_mode:
        worktree_mode = "manual"
    iteration_value = _pick_value(_coerce_optional_int(progress.get("iterations")), len(cycles) or None)

    tokens = metrics.get("tokens") if isinstance(metrics.get("tokens"), dict) else {}
    tokens_available = bool(metrics.get("tokens_available") or metrics.get("tokensAvailable"))
    token_in = _coerce_optional_int(tokens.get("in"))
    token_out = _coerce_optional_int(tokens.get("out"))
    if not tokens_available:
        token_in = None
        token_out = None

    quota_available = bool(metrics.get("quota_available") or metrics.get("quotaAvailable"))
    quota_used = _coerce_optional_float(_pick_value(metrics.get("quota_used"), metrics.get("quotaUsed")))
    if not quota_available:
        quota_used = None

    budget_available = bool(metrics.get("budget_available") or metrics.get("budgetAvailable"))
    budget_used = _coerce_optional_float(_pick_value(metrics.get("budget_used"), metrics.get("budgetUsed")))
    if not budget_available:
        budget_used = None

    branch_value = _pick_text(controller_data.get("branch"), branch, run_summary.get("branch"), last_run_summary.get("branch"))

    return {
        "id": run_id,
        "repo": repo_path,
        "repoLabel": repo.name or repo_path.rsplit("/", 1)[-1],
        "branch": branch_value or "HEAD",
        "backend": str(config.get("execution_backend") or "codex"),
        "startedAt": started_at_ms,
        "stage": last_event_stage,
        "stageIndex": STAGE_ORDER.get(last_event_stage.lower(), 0),
        "iteration": 0 if run_status == "idle" else int(iteration_value if iteration_value is not None else 1),
        "maxIterations": int(config.get("iterations") or 5),
        "runDir": run_dir_text,
        "attempt": attempt,
        "worktreeMode": worktree_mode,
        "finalReason": final_reason_text,
        "progressAvailable": progress_available,
        "progress": round(progress_value, 3) if progress_value is not None else None,
        "budgetAvailable": budget_available,
        "budgetUsed": round(budget_used, 3) if budget_used is not None else None,
        "tokensAvailable": tokens_available,
        "tokens": {
            "in": token_in,
            "out": token_out,
            "available": tokens_available,
        },
        "quotaAvailable": quota_available,
        "quota": {
            "window": _pick_text(controller_data.get("quota_window"), controller_data.get("quotaWindow"), controller_data.get("quota", {}).get("window") if isinstance(controller_data.get("quota"), dict) else "", "5h"),
            "used": round(quota_used, 3) if quota_used is not None else None,
            "available": quota_available,
        },
        "elapsedSec": elapsed_sec,
        "status": run_status,
        "task": task_id or "",
        "taskTitle": task_title or "",
    }


def _stage_payload(
    repo: Path,
    active_run: dict[str, Any],
    progress: dict[str, Any],
    config: dict[str, Any],
    *,
    run_dir: Path | None = None,
    run_summary: dict[str, Any] | None = None,
    last_run_summary: dict[str, Any] | None = None,
    controller_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    run_summary = run_summary if isinstance(run_summary, dict) else {}
    last_run_summary = last_run_summary if isinstance(last_run_summary, dict) else {}
    events = list(events) if isinstance(events, list) else (_cycle_events(run_dir) if run_dir is not None else [])
    task_runtime = _task_runtime_index(events)

    stage_titles = {
        "PM": "Backlog planning",
        "Dev": "Implementation",
        "QA": "Verification",
    }
    stage_model_defaults = {
        "PM": str(config.get("pm_model") or "gpt-5.5"),
        "Dev": str(config.get("dev_model") or "gpt-5.4-mini"),
        "QA": str(config.get("qa_model") or "gpt-5.4-mini"),
    }

    active_status = str(active_run.get("status") or progress.get("run_status") or "idle").strip().lower()
    current_stage = _normalize_stage_name(
        _pick_text(controller_data.get("stage"), controller_data.get("current_stage"), active_run.get("stage"), progress.get("current_stage"))
    )
    if not current_stage and active_status == "running" and _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        active_run.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    ):
        current_stage = "Dev"
    current_task_id = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        active_run.get("task"),
        progress.get("current_task_id"),
        progress.get("selected_task_id"),
        progress.get("task"),
    )
    current_task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
        active_run.get("taskTitle"),
        progress.get("current_task_title"),
    )
    current_attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            active_run.get("attempt"),
            progress.get("attempt"),
            progress.get("current_attempt"),
            last_run_summary.get("attempt"),
            run_summary.get("attempt"),
        )
    )
    cycles = run_summary.get("cycles") if isinstance(run_summary.get("cycles"), list) else []
    cycle_entries = [entry for entry in cycles if isinstance(entry, dict)]
    latest_summary_cycle: int | None = None
    for entry in cycle_entries:
        cycle_value = _coerce_optional_int(entry.get("cycle"))
        if cycle_value is not None and (latest_summary_cycle is None or cycle_value > latest_summary_cycle):
            latest_summary_cycle = cycle_value

    current_cycle = _coerce_optional_int(
        _pick_value(
            controller_data.get("cycle"),
            controller_data.get("current_cycle"),
            active_run.get("iteration"),
            progress.get("iterations"),
            last_run_summary.get("cycle"),
            latest_summary_cycle,
        )
    )
    event_cycles = [cycle_value for cycle_value in (_coerce_optional_int(event.get("cycle")) for event in events) if cycle_value is not None]
    latest_event_cycle = max(event_cycles) if event_cycles else None
    if active_status == "running":
        target_cycle = current_cycle or latest_event_cycle or latest_summary_cycle
    else:
        target_cycle = latest_summary_cycle or latest_event_cycle or current_cycle
    target_cycle = _coerce_optional_int(target_cycle)

    target_cycle_entry: dict[str, Any] = {}
    if target_cycle is not None:
        for entry in reversed(cycle_entries):
            if _coerce_optional_int(entry.get("cycle")) == target_cycle:
                target_cycle_entry = entry
                break

    stage_summary_map: dict[str, dict[str, Any]] = {}
    for raw_stage in target_cycle_entry.get("stages") if isinstance(target_cycle_entry.get("stages"), list) else []:
        if not isinstance(raw_stage, dict):
            continue
        stage_name = _normalize_stage_name(raw_stage.get("name"))
        if stage_name in stage_titles:
            stage_summary_map[stage_name] = raw_stage

    running_stage_name = current_stage if active_status == "running" else ""
    relevant_events = [event for event in events if target_cycle is None or _coerce_optional_int(event.get("cycle")) in {None, target_cycle}]

    stage_event_map: dict[str, dict[str, Any]] = {}
    for event in relevant_events:
        event_type = str(event.get("event") or event.get("type") or "").strip().lower()
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        stage_name = _normalize_stage_name(_pick_value(event.get("stage"), payload.get("stage")))
        if not stage_name and (event_type.startswith("pm_") or event_type.startswith("pm_stage_")):
            stage_name = "PM"
        elif not stage_name and (event_type.startswith("qa_") or event_type.startswith("qa_stage_")):
            stage_name = "QA"
        if stage_name not in {"PM", "QA"}:
            continue
        if event_type not in {"pm_start", "pm_end", "pm_stage_start", "pm_stage_end", "qa_start", "qa_end", "qa_stage_start", "qa_stage_end", "stage_event"}:
            continue
        entry = stage_event_map.setdefault(
            stage_name,
            {
                "cycle": None,
                "status": "",
                "startedAt": None,
                "endedAt": None,
                "rc": None,
                "reason": "",
                "model": "",
                "taskId": "",
                "taskTitle": "",
                "attempt": None,
                "step": None,
                "lastMessage": "",
                "lastEvent": "",
            },
        )
        ts = _iso_to_ms(event.get("ts"))
        if ts is not None:
            started_at = _coerce_optional_int(entry.get("startedAt"))
            ended_at = _coerce_optional_int(entry.get("endedAt"))
            if event_type.endswith("start") and (started_at is None or ts < started_at):
                entry["startedAt"] = ts
            if event_type.endswith("end") and (ended_at is None or ts > ended_at):
                entry["endedAt"] = ts
        cycle_value = _coerce_optional_int(_pick_value(event.get("cycle"), payload.get("cycle")))
        if cycle_value is not None and entry["cycle"] is None:
            entry["cycle"] = cycle_value
        reason = _pick_text(event.get("reason"), payload.get("reason"), payload.get("detail"))
        inner_event = _pick_text(payload.get("event")) if event_type == "stage_event" else ""
        if event_type == "stage_event":
            reason = _pick_text(reason, inner_event)
        if reason:
            entry["reason"] = reason
        model = _pick_text(event.get("model"), payload.get("model"))
        if model and not entry["model"]:
            entry["model"] = model
        task_id = _pick_text(event.get("task_id"), event.get("task"), payload.get("task_id"), payload.get("task"))
        if task_id and not entry["taskId"]:
            entry["taskId"] = task_id
        task_title = _pick_text(event.get("task_title"), event.get("taskTitle"), event.get("title"), payload.get("task_title"), payload.get("taskTitle"), payload.get("title"))
        if task_title and not entry["taskTitle"]:
            entry["taskTitle"] = task_title
        attempt = _coerce_optional_int(_pick_value(event.get("attempt"), payload.get("attempt")))
        if attempt is not None:
            entry["attempt"] = attempt
        step = _coerce_optional_int(_pick_value(event.get("step"), payload.get("step")))
        if step is not None and entry["step"] is None:
            entry["step"] = step
        if event_type.endswith("start"):
            entry["status"] = "running"
        elif event_type.endswith("end"):
            rc = _coerce_optional_int(_pick_value(event.get("rc"), payload.get("rc")))
            if rc is not None:
                entry["rc"] = rc
                entry["status"] = "done" if rc == 0 else "failed"
            elif reason:
                entry["status"] = _normalize_lifecycle_status(reason, reason=reason, default="done")
            elif not entry["status"]:
                entry["status"] = "done"
        elif event_type == "stage_event":
            if inner_event in {"error", "quota_exhausted"} or reason in {"error", "quota_exhausted"}:
                entry["status"] = "failed"
            elif not entry["status"]:
                entry["status"] = "running"
        message = _pick_text(payload.get("detail"), payload.get("reason"), reason, inner_event, _event_message(event))
        if message:
            entry["lastMessage"] = message
        event_label = inner_event or _event_message(event)
        if event_label:
            entry["lastEvent"] = event_label

    def _runtime_key(entry: dict[str, Any]) -> tuple[int, int, int, int, int]:
        return (
            _coerce_optional_int(entry.get("cycle")) or -1,
            _coerce_optional_int(entry.get("attempt")) or -1,
            _coerce_optional_int(entry.get("step")) or -1,
            _coerce_optional_int(entry.get("startedAt")) or -1,
            _coerce_optional_int(entry.get("endedAt")) or -1,
        )

    cycle_task_runtimes = [entry for entry in task_runtime.values() if target_cycle is None or _coerce_optional_int(entry.get("cycle")) in {None, target_cycle}]
    latest_task_runtime: dict[str, Any] = {}
    if cycle_task_runtimes:
        if current_task_id and current_task_id in task_runtime and (target_cycle is None or _coerce_optional_int(task_runtime[current_task_id].get("cycle")) in {None, target_cycle}):
            latest_task_runtime = task_runtime[current_task_id]
        else:
            latest_task_runtime = max(cycle_task_runtimes, key=_runtime_key)

    records: list[dict[str, Any]] = []
    for stage_name in ("PM", "Dev", "QA"):
        summary = stage_summary_map.get(stage_name, {})
        stage_runtime = latest_task_runtime if stage_name == "Dev" else stage_event_map.get(stage_name, {})

        summary_status = _pick_text(summary.get("status"))
        summary_reason = _pick_text(summary.get("reason"), summary.get("message"))
        summary_cycle = _coerce_optional_int(summary.get("cycle"))
        summary_step = _coerce_optional_int(summary.get("step"))
        runtime_cycle = _coerce_optional_int(stage_runtime.get("cycle"))
        cycle_value = summary_cycle if summary_cycle is not None else runtime_cycle
        if cycle_value is None:
            cycle_value = current_cycle if stage_name == running_stage_name and active_status == "running" else target_cycle

        started_at = _coerce_optional_ms(
            _pick_value(
                summary.get("startedAt"),
                summary.get("started_at"),
                stage_runtime.get("startedAt"),
                stage_runtime.get("started_at"),
            )
        )
        ended_at = _coerce_optional_ms(
            _pick_value(
                summary.get("endedAt"),
                summary.get("ended_at"),
                stage_runtime.get("endedAt"),
                stage_runtime.get("ended_at"),
            )
        )
        rc = _coerce_optional_int(_pick_value(summary.get("rc"), stage_runtime.get("rc")))
        reason = _pick_text(summary_reason, stage_runtime.get("reason"))
        task_id = _pick_text(summary.get("taskId"), summary.get("task_id"), stage_runtime.get("taskId"))
        task_title = _pick_text(summary.get("taskTitle"), summary.get("task_title"), stage_runtime.get("taskTitle"))
        step = _coerce_optional_int(_pick_value(summary_step, stage_runtime.get("step")))
        attempt = _coerce_optional_int(
            _pick_value(
                summary.get("attempt"),
                summary.get("currentAttempt"),
                stage_runtime.get("attempt"),
                current_attempt if stage_name in {"Dev", "PM", "QA"} else None,
            )
        )
        model = _pick_text(
            summary.get("model"),
            stage_runtime.get("model"),
            stage_model_defaults.get(stage_name, ""),
        )

        if stage_name == "Dev":
            if not task_id:
                task_id = current_task_id or _pick_text(stage_runtime.get("taskId"))
            if not task_title:
                task_title = current_task_title or _pick_text(stage_runtime.get("taskTitle"))
        elif stage_name == running_stage_name and active_status == "running":
            if not task_id:
                task_id = current_task_id
            if not task_title:
                task_title = current_task_title

        running = stage_name == running_stage_name and active_status == "running"
        if running:
            started_at = started_at or _coerce_optional_int(active_run.get("startedAt")) or _coerce_optional_int(controller_data.get("startedAt"))
            ended_at = None
            if attempt is None:
                attempt = current_attempt
            if not task_id:
                task_id = current_task_id or _pick_text(active_run.get("task"))
            if not task_title:
                task_title = current_task_title or _pick_text(active_run.get("taskTitle"))

        duration_sec = _coerce_optional_float(
            _pick_value(
                summary.get("durationSec"),
                summary.get("duration_seconds"),
                stage_runtime.get("durationSec"),
                stage_runtime.get("duration_seconds"),
            )
        )
        if duration_sec is None and started_at is not None and ended_at is not None and ended_at >= started_at:
            duration_sec = round((ended_at - started_at) / 1000.0, 3)
        if duration_sec is None and running:
            duration_sec = _coerce_optional_float(_pick_value(active_run.get("elapsedSec"), controller_data.get("elapsedSec"), controller_data.get("elapsed_seconds")))

        output_reason = reason or summary_reason or _pick_text(stage_runtime.get("reason"))
        recent_output = _pick_text(summary.get("recentOutput"), summary.get("recent_output"))
        if not recent_output:
            recent_output = _task_output_excerpt(
                run_dir,
                stage_name=stage_name,
                cycle=cycle_value,
                step=step,
                task_id=task_id or current_task_id,
                attempt=attempt,
                reason=output_reason,
                fallback_text=_pick_text(stage_runtime.get("lastMessage"), stage_runtime.get("lastEvent"), output_reason),
            )

        status = _normalize_lifecycle_status(
            summary_status or stage_runtime.get("status"),
            rc=rc,
            reason=reason,
            running=running,
            has_activity=bool(summary or stage_runtime or task_id or task_title or recent_output),
            default="pending",
        )

        if not summary and not stage_runtime and not running and not recent_output and not task_id and not task_title and not reason and status == "pending":
            continue

        if running:
            status = "running"

        records.append(
            {
                "id": stage_name,
                "label": stage_name,
                "title": task_title or stage_titles.get(stage_name, stage_name),
                "status": status,
                "cycle": cycle_value,
                "startedAt": started_at,
                "endedAt": ended_at,
                "durationSec": duration_sec,
                "model": model,
                "taskId": task_id,
                "taskTitle": task_title,
                "attempt": attempt,
                "step": step,
                "recentOutput": recent_output,
                "reason": reason,
                "rc": rc,
            }
        )

    return records


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
    reason = _pick_text(final.get("reason"), last_summary.get("stop_reason"))
    rc = _coerce_optional_int(final.get("rc"))
    if rc is None:
        rc = _coerce_optional_int(last_summary.get("rc"))
    stop_exists = (run_dir / "STOP").exists()
    if not reason and stop_exists:
        reason = "stop_file"
    status = _normalize_run_status(
        "",
        running=False,
        exit_code=rc,
        final_reason=reason,
        stop_file_exists=stop_exists,
        has_run_dir=True,
    )

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


def _controller_run_dir_is_current_or_terminal(controller_data: dict[str, Any]) -> bool:
    if bool(controller_data.get("running")):
        return True
    status = RUN_STATUS_ALIASES.get(_pick_text(controller_data.get("run_status"), controller_data.get("status")).lower(), "")
    if status in {"success", "stopped", "failed"}:
        return True
    reason = _pick_text(controller_data.get("final_reason"), controller_data.get("reason")).lower()
    terminal_reasons = {
        "project_complete",
        "all_tasks_done",
        "completed",
        "success",
        "ok",
        "prepared_only",
        "stop_file",
        "stop_requested",
        "stopped",
        "user_stop",
        "manual_stop",
        "failed",
        "error",
        "exception",
        "abandoned",
        "abandon_failed",
        "build_failed",
        "test_failed",
        "policy_violation",
        "exhausted_attempts",
    }
    if reason in terminal_reasons:
        return True
    return _coerce_optional_int(controller_data.get("exit_code")) is not None or bool(controller_data.get("stop_file_exists"))


def _resolve_latest_run_dir(
    repo: Path,
    controller_status: dict[str, Any] | None,
    controller: RunnerController | None,
) -> Path | None:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    controller_run_dir_text = _pick_text(controller_data.get("run_dir"), getattr(controller, "run_dir", None))
    if controller_run_dir_text:
        try:
            candidate = Path(controller_run_dir_text).expanduser().resolve()
            if candidate.exists() and candidate.is_dir():
                if _controller_run_dir_is_current_or_terminal(controller_data) or _run_dir_has_observable_artifacts(candidate):
                    return candidate
        except OSError:
            pass
    return _latest_observable_run_dir(repo)


def _build_metrics_payload(
    run_dir: Path | None,
    progress: dict[str, Any],
    controller_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    events = _cycle_events(run_dir)
    cycle_end_events = [event for event in events if str(event.get("event") or event.get("type") or "").strip().lower() == "cycle_end"]
    tokens24h: list[int] = []
    success24h: list[int] = []
    budget: list[float] = []
    last_tokens = {"in": None, "out": None}
    last_stage = ""
    quota_used: float | None = None
    budget_used: float | None = None
    tokens_available = False
    budget_available = False
    quota_available = False

    for event in cycle_end_events:
        tokens = event.get("tokens") if isinstance(event.get("tokens"), dict) else {}
        total = tokens.get("_total") if isinstance(tokens.get("_total"), dict) else {}
        total_tokens = _coerce_optional_int(total.get("total"))
        if total_tokens is not None:
            tokens24h.append(total_tokens)
            tokens_available = True
        success24h.append(1 if int(event.get("rc") or 0) == 0 else 0)
        event_budget = _coerce_optional_float(
            _pick_value(event.get("budget"), event.get("budget_used"), event.get("budgetUsed"))
        )
        if event_budget is not None:
            budget.append(round(max(0.0, min(1.0, event_budget)), 3))
            budget_available = True
            budget_used = max(budget_used or 0.0, event_budget)
        input_tokens = _coerce_optional_int(total.get("input"))
        output_tokens = _coerce_optional_int(total.get("output"))
        if input_tokens is not None or output_tokens is not None:
            last_tokens = {
                "in": input_tokens,
                "out": output_tokens,
            }
            tokens_available = True
        last_stage = str(event.get("stage") or event.get("name") or "").strip() or last_stage
        quota = event.get("quota") if isinstance(event.get("quota"), dict) else {}
        quota_value = _coerce_optional_float(quota.get("used"))
        if quota_value is not None:
            quota_used = max(quota_used or 0.0, quota_value)
            quota_available = True
    controller_tokens = controller_data.get("tokens") if isinstance(controller_data.get("tokens"), dict) else {}
    input_tokens = _coerce_optional_int(controller_tokens.get("in"))
    output_tokens = _coerce_optional_int(controller_tokens.get("out"))
    if input_tokens is not None or output_tokens is not None:
        last_tokens = {
            "in": input_tokens,
            "out": output_tokens,
        }
        tokens_available = True

    controller_quota = controller_data.get("quota") if isinstance(controller_data.get("quota"), dict) else {}
    controller_quota_value = _coerce_optional_float(controller_quota.get("used"))
    if controller_quota_value is not None:
        quota_used = controller_quota_value
        quota_available = True

    controller_budget = _coerce_optional_float(_pick_value(controller_data.get("budget_used"), controller_data.get("budgetUsed")))
    if controller_budget is not None:
        budget_used = controller_budget
        budget_available = True
        if not budget:
            budget.append(round(max(0.0, min(1.0, controller_budget)), 3))
    last_stage = _pick_text(controller_data.get("last_stage"), controller_data.get("stage"), last_stage)

    return {
        "tokens24h": tokens24h,
        "success24h": success24h,
        "budget": budget,
        "tokens": last_tokens,
        "tokens_available": tokens_available,
        "budget_available": budget_available,
        "quota_available": quota_available,
        "last_stage": last_stage,
        "quota_used": quota_used,
        "budget_used": budget_used,
    }


def _build_progress_payload(
    *,
    repo: Path,
    run_dir: Path | None,
    config: dict[str, Any],
    branch: str,
    controller_status: dict[str, Any] | None = None,
    events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    controller_data = controller_status if isinstance(controller_status, dict) else {}
    state = load_state(run_dir / "STATE.json") if run_dir else {"done": [], "failed": [], "warnings": []}
    backlog = _load_backlog_payload(run_dir, state, current_task_id=_pick_text(controller_data.get("current_task_id"), controller_data.get("task_id"), controller_data.get("task")), events=events)
    completion_level = str(config.get("goals_completion_level") or "all").strip() or "all"
    goals = _build_goals_payload(repo, completion_level=completion_level)
    backlog_items = backlog["items"]
    done_ids = set(str(item) for item in (state.get("done") or []) if str(item).strip())
    failed_items = state.get("failed") if isinstance(state.get("failed"), list) else []
    failed_ids = {
        str(item.get("task") or "").strip()
        for item in failed_items
        if isinstance(item, dict) and str(item.get("task") or "").strip()
    }
    tasks_total = len(backlog_items)
    tasks_done = _coerce_optional_int(controller_data.get("done"))
    if tasks_done is None:
        tasks_done = len([item for item in backlog_items if item["id"] in done_ids])
    tasks_failed = _coerce_optional_int(controller_data.get("failed"))
    if tasks_failed is None:
        tasks_failed = len([item for item in backlog_items if item["id"] in failed_ids])
    run_summary = _safe_json(run_dir / "run_summary.json", {}) if run_dir else {}
    last_run_summary = _safe_json(run_dir / "last_run_summary.json", {}) if run_dir else {}
    final = run_summary.get("final") if isinstance(run_summary.get("final"), dict) else {}
    final_reason = _pick_text(
        controller_data.get("final_reason"),
        controller_data.get("reason"),
        final.get("reason") if isinstance(final, dict) else "",
        last_run_summary.get("stop_reason"),
    )
    final_rc = _coerce_optional_int(controller_data.get("exit_code"))
    if final_rc is None:
        final_rc = _coerce_optional_int(final.get("rc") if isinstance(final, dict) else None)
    if final_rc is None:
        final_rc = _coerce_optional_int(last_run_summary.get("rc") if isinstance(last_run_summary, dict) else None)
    stop_file_exists = bool(run_dir and (run_dir / "STOP").exists())
    run_status = _normalize_run_status(
        _pick_text(
            controller_data.get("run_status"),
            controller_data.get("status"),
            final.get("status") if isinstance(final, dict) else "",
            last_run_summary.get("status"),
        ),
        running=bool(controller_data.get("running")),
        exit_code=final_rc,
        final_reason=final_reason,
        stop_file_exists=stop_file_exists,
        has_run_dir=bool(run_dir),
    )
    current_task = _pick_text(
        controller_data.get("current_task_id"),
        controller_data.get("task_id"),
        controller_data.get("task"),
        backlog["selected_id"],
    )
    if not current_task:
        current_task = backlog["selected_id"] if backlog["selected_id"] else ""
    current_task_title = _pick_text(
        controller_data.get("current_task_title"),
        controller_data.get("task_title"),
        controller_data.get("taskTitle"),
    )
    if not current_task_title and current_task:
        current_task_title = next((item["title"] for item in backlog_items if item["id"] == current_task), "")
    progress_value = _coerce_optional_float(
        _pick_value(
            controller_data.get("progress"),
            controller_data.get("progress_ratio"),
            controller_data.get("progressValue"),
            run_summary.get("progress"),
        )
    )
    progress_available = progress_value is not None
    attempt = _coerce_optional_int(
        _pick_value(
            controller_data.get("attempt"),
            controller_data.get("current_attempt"),
            controller_data.get("attempt_index"),
            run_summary.get("attempt"),
            last_run_summary.get("attempt"),
        )
    )
    worktree_mode = _pick_text(controller_data.get("worktree_mode"), controller_data.get("worktreeMode"))

    return {
        "latest_run_dir": run_dir.as_posix() if run_dir else None,
        "run_status": run_status,
        "tasks_done": tasks_done,
        "tasks_total": tasks_total,
        "tasks_failed": tasks_failed,
        "progress": round(progress_value, 3) if progress_value is not None else None,
        "progress_available": progress_available,
        "current_task_id": current_task,
        "current_task_title": current_task_title,
        "attempt": attempt,
        "worktree_mode": worktree_mode,
        "iterations": len(run_summary.get("cycles") or []),
        "goals": goals,
        "backlog": backlog,
        "state": state,
        "final_reason": final_reason,
        "final_rc": final_rc,
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
    cfg_path, cfg, cfg_source = _load_config_payload(repo_root, config_path)
    prompts_dir = resolve_prompts_dir(repo_root, str(cfg.get("prompts_dir") or ""))
    if not prompts_dir:
        prompts_dir = default_prompts_dir(repo_root)
    goals_completion_level = str(cfg.get("goals_completion_level") or "all").strip() or "all"
    goals = _build_goals_payload(repo_root, completion_level=goals_completion_level)
    prompt_items = _load_prompt_items(repo_root, prompts_dir)
    branch = _branch_name(repo_root)
    controller = runner_controller
    if controller is None and runner_controller_auto_build:
        controller = _build_runner_controller(repo_root, cfg, cfg_path)
    controller_status = _controller_status_payload(controller)
    latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
    logs_events = _cycle_events(latest_run_dir)
    progress = _build_progress_payload(
        repo=repo_root,
        run_dir=latest_run_dir,
        config=cfg,
        branch=branch,
        controller_status=controller_status,
        events=logs_events,
    )
    state = progress["state"] if isinstance(progress.get("state"), dict) else {"done": [], "failed": [], "warnings": []}
    backlog = progress["backlog"] if isinstance(progress.get("backlog"), dict) else {"items": [], "counts": {}, "selected_id": ""}
    run_summary = _safe_json(latest_run_dir / "run_summary.json", {}) if latest_run_dir else {}
    last_run_summary = _safe_json(latest_run_dir / "last_run_summary.json", {}) if latest_run_dir else {}
    log_entries = _load_log_entries(latest_run_dir)
    metrics = _build_metrics_payload(latest_run_dir, progress, controller_status=controller_status)
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
        controller_status=controller_status,
    )
    stages = _stage_payload(repo_root, active_run, progress, cfg, run_dir=latest_run_dir, run_summary=run_summary, last_run_summary=last_run_summary, controller_status=controller_status, events=logs_events)
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
    goals_summary = goals.get("summary") if isinstance(goals.get("summary"), dict) else {}
    goals_total = int(goals_summary.get("total") or 0)
    if not goals_total:
        goals_items = goals.get("items") if isinstance(goals.get("items"), dict) else {}
        goals_total = sum(len(items) for items in goals_items.values()) if isinstance(goals_items, dict) else 0
    has_metrics = bool(
        metrics.get("tokens24h")
        or metrics.get("success24h")
        or metrics.get("budget")
        or metrics.get("last_stage")
        or metrics.get("quota_used") is not None
        or metrics.get("budget_used") is not None
        or metrics.get("tokens_available")
        or metrics.get("budget_available")
        or metrics.get("quota_available")
    )
    active_run_section_state = buildSectionState("activeRun", "empty" if active_run_empty else "ready", fallbackSectionMessage("activeRun") if active_run_empty else "")
    stages_section_state = buildSectionState(
        "stages",
        "ready" if len(stages) >= 3 else ("partial" if stages else "empty"),
        "" if len(stages) >= 3 else ("Only some lifecycle records were published." if stages else fallbackSectionMessage("stages")),
    )
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
        "goals": goals,
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
            "progress": progress.get("progress"),
            "progress_available": progress.get("progress_available", False),
            "current_task_id": progress.get("current_task_id", ""),
            "current_task_title": progress.get("current_task_title", ""),
            "attempt": progress.get("attempt"),
            "worktree_mode": progress.get("worktree_mode", ""),
            "goals": progress.get("goals", {}),
            "backlog": backlog,
            "final_reason": progress.get("final_reason", ""),
            "final_rc": progress.get("final_rc"),
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

    def _goals() -> dict[str, Any]:
        completion_level = str(cfg.get("goals_completion_level") or "all").strip() or "all"
        return _build_goals_payload(repo_root, completion_level=completion_level)

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

    @app.get("/api/logs/tail")
    @app.get("/api/logs/live")
    def api_logs_tail(request: Request) -> Any:
        query = request.query_params
        cursor_raw = query.get("cursor")
        max_lines_raw = query.get("max_lines") or query.get("lines")
        level = str(query.get("level") or "").strip()
        stage = str(query.get("stage") or "").strip()
        task_id = str(query.get("task_id") or query.get("taskId") or "").strip()
        search = str(query.get("search") or query.get("q") or "").strip()
        cursor: int | None = None
        if cursor_raw is not None and str(cursor_raw).strip():
            cursor = _coerce_optional_int(cursor_raw)
            if cursor is None:
                cursor = 0
        max_lines = _coerce_optional_int(max_lines_raw) if max_lines_raw is not None else 50
        if max_lines is None or max_lines <= 0:
            max_lines = 50
        max_lines = min(max_lines, 500)

        try:
            controller_status = _controller_status_payload(controller)
            latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
            source_path = _resolve_log_tail_source(latest_run_dir)
            if source_path is None:
                source_path = (latest_run_dir / "metrics.jsonl") if latest_run_dir is not None else None
            if source_path is None:
                return {
                    "ok": False,
                    "state": "missing_file",
                    "entries": [],
                    "next_cursor": 0,
                    "source_file": "",
                    "source_path": "",
                    "source": {
                        "path": "",
                        "name": "",
                        "exists": False,
                    },
                    "malformed_lines": 0,
                }
            live = bool(controller_status.get("running"))
            return _build_log_tail_payload(
                source_path,
                cursor=cursor,
                max_lines=max_lines,
                level=level,
                stage=stage,
                task_id=task_id,
                search=search,
                live=live,
            )
        except Exception as ex:
            source_text = ""
            try:
                controller_status = _controller_status_payload(controller)
                latest_run_dir = _resolve_latest_run_dir(repo_root, controller_status, controller)
                source_path = _resolve_log_tail_source(latest_run_dir)
                if source_path is not None:
                    source_text = source_path.as_posix()
            except Exception:
                source_text = ""
            return {
                "ok": False,
                "state": "read_error",
                "entries": [],
                "next_cursor": 0,
                "source_file": source_text,
                "source_path": source_text,
                "source": {
                    "path": source_text,
                    "name": Path(source_text).name if source_text else "",
                    "exists": bool(source_text and Path(source_text).exists()),
                },
                "error": str(ex).strip() or ex.__class__.__name__,
                "malformed_lines": 0,
            }

    @app.get("/api/config")
    def api_config() -> dict[str, Any]:
        return _section("config")

    @app.get("/api/prompts")
    def api_prompts() -> dict[str, Any]:
        return _section("prompts")

    @app.get("/api/goals")
    def api_goals() -> dict[str, Any]:
        return _goals()

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
