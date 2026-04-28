from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from ..cli import DEFAULTS
from ..config import AGENT_WORK_DIR, default_config_path, resolve_config_path, resolve_prompts_dir
from ..logger import close_all_loggers
from ..process_guard import register_pid, terminate_all_children, terminate_process_tree, tracked_pid_details, unregister_pid_if_exited
from ..run_dir import find_latest_run_dir, make_run_dir
from ..runner_entry import run as run_runner
from ..stop_progress import (
    STOP_PROGRESS_FILE,
    STOP_PROGRESS_LOG_FILE,
    clear_stop_progress,
    file_write_signal,
    normalize_stop_progress_payload,
    read_stop_progress,
    write_stop_progress,
)
from ..state import count_state_task_ids, load_backlog_task_ids, load_state
from ..utils import STOP_REASON_STOP_FILE, detect_stop_reason, rotate_log_file


RUNNER_START_MODE_CHOICES = ("one-shot", "continuous", "loop")
RUNNER_START_PROFILE_CHOICES = ("personal", "enterprise")
RUNNER_START_BACKEND_CHOICES = ("codex", "claudecode")
RUNNER_START_BOOL_CHOICES = (True, False)
RUNNER_START_TRUTHY = {"1", "true", "yes", "on", "enabled"}
RUNNER_START_FALSY = {"0", "false", "no", "off", "disabled"}
REDACTED_VALUE = "[redacted]"


def _runner_start_path_text(value: Path | str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().resolve().as_posix()
    except Exception:
        return raw.replace("\\", "/")


def _runner_start_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in RUNNER_START_TRUTHY:
        return True
    if raw in RUNNER_START_FALSY:
        return False
    return None


def _runner_start_int(value: Any) -> int | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        number = int(float(raw))
    except Exception:
        return None
    return number


def _runner_start_mode_from_flags(continuous: bool, loop: bool) -> str:
    if loop:
        return "loop"
    if continuous:
        return "continuous"
    return "one-shot"


def _runner_start_mode_from_contract(values: dict[str, Any]) -> str:
    if bool(values.get("loop")):
        return "loop"
    if bool(values.get("continuous")):
        return "continuous"
    return "one-shot"


def _runner_start_profile(value: Any, default: str = "personal") -> str:
    raw = str(value or "").strip().lower()
    if raw in RUNNER_START_PROFILE_CHOICES:
        return raw
    return default if default in RUNNER_START_PROFILE_CHOICES else "personal"


def _runner_start_backend(value: Any, default: str = "codex") -> str:
    raw = str(value or "").strip().lower()
    if raw in RUNNER_START_BACKEND_CHOICES:
        return raw
    return default if default in RUNNER_START_BACKEND_CHOICES else "codex"


def _runner_start_validation_payload(errors: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_errors = [dict(error) for error in errors]
    field_errors: dict[str, list[str]] = {}
    for error in normalized_errors:
        field = str(error.get("field") or "").strip()
        message = str(error.get("message") or "").strip()
        if field and message:
            field_errors.setdefault(field, []).append(message)
    return {
        "valid": not normalized_errors,
        "message": "Runner start options are valid." if not normalized_errors else "Runner start options are invalid.",
        "error_count": len(normalized_errors),
        "errors": normalized_errors,
        "field_errors": field_errors,
    }


def _runner_start_argv_preview(
    *,
    repo: str,
    config_path: str,
    autopilot: bool,
    run_mode: str,
    loop_max_cycles: Any,
    profile: str,
    execution_backend: str,
    run_dir: str = "",
    resume_latest: bool = False,
    redact_paths: bool = False,
) -> list[str]:
    def _path_token(value: str) -> str:
        text = _runner_start_path_text(value)
        if not text:
            return ""
        return REDACTED_VALUE if redact_paths else text

    mode = run_mode if run_mode in RUNNER_START_MODE_CHOICES else _runner_start_mode_from_flags(False, False)
    argv = [
        "--repo",
        _path_token(repo),
        "--config",
        _path_token(config_path),
        "--autopilot" if autopilot else "--no-autopilot",
    ]
    if mode == "loop":
        argv.extend(["--continuous", "--loop"])
    elif mode == "continuous":
        argv.extend(["--continuous", "--no-loop"])
    else:
        argv.extend(["--no-continuous", "--no-loop"])
    argv.extend(["--resume-latest" if resume_latest else "--no-resume-latest"])
    argv.extend(["--loop-max-cycles", str(max(0, int(_runner_start_int(loop_max_cycles) or 0)))])
    argv.extend(["--profile", _runner_start_profile(profile, "personal")])
    argv.extend(["--execution-backend", _runner_start_backend(execution_backend, "codex")])
    run_dir_token = _path_token(run_dir)
    if run_dir_token:
        argv.extend(["--run-dir", run_dir_token])
    return argv


def build_runner_start_options_contract(
    repo: Path,
    base_args: argparse.Namespace | None = None,
    *,
    redact_paths: bool = False,
) -> dict[str, Any]:
    repo_root = repo.expanduser().resolve()
    args = base_args or argparse.Namespace()
    default_cfg_path = default_config_path(repo_root)
    repo_text = repo_root.as_posix()

    current_continuous = bool(getattr(args, "continuous", DEFAULTS.get("continuous", False)))
    current_loop = bool(getattr(args, "loop", DEFAULTS.get("loop", False)))
    default_continuous = bool(DEFAULTS.get("continuous", False))
    default_loop = bool(DEFAULTS.get("loop", False))
    current_run_dir = _runner_start_path_text(getattr(args, "run_dir", getattr(args, "runDir", "")))
    default_run_dir = _runner_start_path_text(DEFAULTS.get("run_dir", ""))
    current_resume_latest = bool(getattr(args, "resume_latest", getattr(args, "resumeLatest", DEFAULTS.get("resume_latest", False))))
    default_resume_latest = bool(DEFAULTS.get("resume_latest", False))

    current_config_path = _runner_start_path_text(
        getattr(args, "config_path", getattr(args, "config", "")) or default_cfg_path
    )
    default_config_path_text = _runner_start_path_text(default_cfg_path)
    public_current_config_path = REDACTED_VALUE if redact_paths and current_config_path else current_config_path
    public_default_config_path_text = REDACTED_VALUE if redact_paths and default_config_path_text else default_config_path_text
    public_repo_text = REDACTED_VALUE if redact_paths and repo_text else repo_text
    public_current_run_dir = REDACTED_VALUE if redact_paths and current_run_dir else current_run_dir
    public_default_run_dir = REDACTED_VALUE if redact_paths and default_run_dir else default_run_dir

    values = {
        "autopilot": bool(getattr(args, "autopilot", DEFAULTS.get("autopilot", False))),
        "run_mode": _runner_start_mode_from_flags(current_continuous, current_loop),
        "continuous": current_continuous,
        "loop": current_loop,
        "one_shot": not current_continuous and not current_loop,
        "loop_max_cycles": max(0, int(_runner_start_int(getattr(args, "loop_max_cycles", DEFAULTS.get("loop_max_cycles", 0))) or 0)),
        "profile": _runner_start_profile(getattr(args, "profile", DEFAULTS.get("profile", "personal")), str(DEFAULTS.get("profile", "personal") or "personal")),
        "execution_backend": _runner_start_backend(
            getattr(args, "execution_backend", DEFAULTS.get("execution_backend", "codex")),
            str(DEFAULTS.get("execution_backend", "codex") or "codex"),
        ),
        "config_path": public_current_config_path,
        "run_dir": public_current_run_dir,
        "resume_latest": current_resume_latest,
    }
    validation_values = dict(values)
    validation_values["config_path"] = current_config_path
    validation_values["run_dir"] = current_run_dir
    defaults = {
        "autopilot": bool(DEFAULTS.get("autopilot", False)),
        "run_mode": _runner_start_mode_from_flags(default_continuous, default_loop),
        "continuous": default_continuous,
        "loop": default_loop,
        "one_shot": not default_continuous and not default_loop,
        "loop_max_cycles": max(0, int(_runner_start_int(DEFAULTS.get("loop_max_cycles", 0)) or 0)),
        "profile": _runner_start_profile(DEFAULTS.get("profile", "personal"), "personal"),
        "execution_backend": _runner_start_backend(DEFAULTS.get("execution_backend", "codex"), "codex"),
        "config_path": public_default_config_path_text,
        "run_dir": public_default_run_dir,
        "resume_latest": default_resume_latest,
    }
    schema = {
        "autopilot": {
            "kind": "bool",
            "label": "Autopilot",
            "choices": list(RUNNER_START_BOOL_CHOICES),
            "desc": "Skip interactive confirmation prompts.",
            "hint": "When off, the runner pauses between stages.",
            "group": "mode",
        },
        "run_mode": {
            "kind": "enum",
            "label": "Run mode",
            "options": list(RUNNER_START_MODE_CHOICES),
            "choices": list(RUNNER_START_MODE_CHOICES),
            "desc": "Practical shell-equivalent run mode.",
            "hint": "One-shot runs once; continuous chains cycles; loop repeats runs.",
            "group": "mode",
        },
        "continuous": {
            "kind": "bool",
            "label": "Continuous",
            "choices": list(RUNNER_START_BOOL_CHOICES),
            "desc": "Keep chaining cycles without manual stopping.",
            "hint": "Pair with autopilot for unattended runs.",
            "group": "mode",
            "derived": True,
        },
        "loop": {
            "kind": "bool",
            "label": "Loop",
            "choices": list(RUNNER_START_BOOL_CHOICES),
            "desc": "Keep the runner cycling after a run completes.",
            "hint": "Pair with loop max cycles to avoid infinite loops.",
            "group": "mode",
            "derived": True,
        },
        "one_shot": {
            "kind": "bool",
            "label": "One-shot",
            "choices": list(RUNNER_START_BOOL_CHOICES),
            "desc": "Run once and stop.",
            "hint": "One-shot means both continuous and loop are off.",
            "group": "mode",
            "derived": True,
        },
        "loop_max_cycles": {
            "kind": "number",
            "label": "Max cycles",
            "min": 0,
            "allow_empty": False,
            "desc": "Hard cap on loop cycles.",
            "hint": "Zero means no extra cap beyond the rest of the runner.",
            "group": "mode",
        },
        "profile": {
            "kind": "enum",
            "label": "Profile",
            "options": list(RUNNER_START_PROFILE_CHOICES),
            "choices": list(RUNNER_START_PROFILE_CHOICES),
            "desc": "Default safety profile used to derive runner limits.",
            "hint": "Enterprise raises several guardrails.",
            "group": "project",
        },
        "execution_backend": {
            "kind": "enum",
            "label": "Backend",
            "options": list(RUNNER_START_BACKEND_CHOICES),
            "choices": list(RUNNER_START_BACKEND_CHOICES),
            "desc": "Backend used for Dev and QA stages.",
            "hint": "codex = OpenAI Codex CLI, claudecode = Claude Code.",
            "group": "project",
        },
        "config_path": {
            "kind": "text",
            "label": "Config path",
            "allow_empty": False,
            "desc": "Config JSON path to load before starting.",
            "hint": "Leave blank to keep the current config path.",
            "group": "project",
        },
        "run_dir": {
            "kind": "text",
            "label": "Run dir",
            "allow_empty": True,
            "desc": "Explicit run directory to reuse before starting.",
            "hint": "Leave blank to create a new run directory.",
            "group": "session",
        },
        "resume_latest": {
            "kind": "bool",
            "label": "Resume latest",
            "choices": list(RUNNER_START_BOOL_CHOICES),
            "desc": "Reuse the latest run directory before starting.",
            "hint": "Use with explicit run-dir intent only when you mean to resume the latest run.",
            "group": "session",
        },
    }
    contract = {
        "repo": public_repo_text,
        "path": current_config_path,
        "defaults_path": default_config_path_text,
        "values": values,
        "defaults": defaults,
        "schema": schema,
        "choices": {
            "run_mode": list(RUNNER_START_MODE_CHOICES),
            "profile": list(RUNNER_START_PROFILE_CHOICES),
            "execution_backend": list(RUNNER_START_BACKEND_CHOICES),
            "autopilot": list(RUNNER_START_BOOL_CHOICES),
            "continuous": list(RUNNER_START_BOOL_CHOICES),
            "loop": list(RUNNER_START_BOOL_CHOICES),
            "one_shot": list(RUNNER_START_BOOL_CHOICES),
            "resume_latest": list(RUNNER_START_BOOL_CHOICES),
        },
        "redaction": {
            "active": bool(redact_paths),
            "placeholder": REDACTED_VALUE,
            "paths": (
                [
                    "repo",
                    "path",
                    "defaults_path",
                    "values.config_path",
                    "defaults.config_path",
                    "values.run_dir",
                    "defaults.run_dir",
                    "argv_preview",
                ]
                if redact_paths
                else []
            ),
        },
    }
    _, validation_error = normalize_runner_start_options(
        repo_root,
        validation_values,
        base_args=args,
        contract=contract,
    )
    validation_errors = list((validation_error or {}).get("details", {}).get("errors") or [])
    contract["validation"] = _runner_start_validation_payload(validation_errors)
    contract["argv_preview"] = _runner_start_argv_preview(
        repo=repo_text,
        config_path=current_config_path,
        autopilot=bool(values.get("autopilot")),
        run_mode=str(values.get("run_mode") or "one-shot"),
        loop_max_cycles=values.get("loop_max_cycles"),
        profile=str(values.get("profile") or "personal"),
        execution_backend=str(values.get("execution_backend") or "codex"),
        run_dir=current_run_dir,
        resume_latest=current_resume_latest,
        redact_paths=redact_paths,
    )
    return contract


def normalize_runner_start_options(
    repo: Path,
    raw_options: dict[str, Any] | None,
    *,
    base_args: argparse.Namespace | None = None,
    contract: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    repo_root = repo.expanduser().resolve()
    contract = contract or build_runner_start_options_contract(repo_root, base_args)
    current_values = dict(contract.get("values") or {})
    raw = dict(raw_options or {})
    overrides: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []

    def _error(field: str, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        error: dict[str, Any] = {"field": field, "code": code, "message": message}
        if details:
            error["details"] = details
        errors.append(error)

    def _raw_value(*names: str) -> tuple[bool, Any]:
        for name in names:
            if name in raw:
                return True, raw[name]
        return False, None

    def _parse_bool(field: str, value: Any) -> bool | None:
        parsed = _runner_start_bool(value)
        if parsed is None:
            _error(field, "invalid_choice", f"{field} must be true or false.", {"choices": list(RUNNER_START_BOOL_CHOICES)})
        return parsed

    autopilot_present, autopilot_raw = _raw_value("autopilot", "autoPilot")
    if autopilot_present:
        autopilot = _parse_bool("autopilot", autopilot_raw)
        if autopilot is not None:
            overrides["autopilot"] = autopilot

    profile_present, profile_raw = _raw_value("profile")
    if profile_present:
        profile = str(profile_raw or "").strip().lower()
        if profile not in RUNNER_START_PROFILE_CHOICES:
            _error("profile", "invalid_choice", "Profile must be personal or enterprise.", {"choices": list(RUNNER_START_PROFILE_CHOICES)})
        else:
            overrides["profile"] = profile

    backend_present, backend_raw = _raw_value("execution_backend", "executionBackend", "backend")
    if backend_present:
        backend = str(backend_raw or "").strip().lower()
        if backend not in RUNNER_START_BACKEND_CHOICES:
            _error("execution_backend", "invalid_choice", "Backend must be codex or claudecode.", {"choices": list(RUNNER_START_BACKEND_CHOICES)})
        else:
            overrides["execution_backend"] = backend

    config_path_present, config_path_raw = _raw_value("config_path", "configPath", "config")
    if config_path_present:
        config_path_text = _runner_start_path_text(config_path_raw)
        if not config_path_text:
            _error("config_path", "required", "Config path cannot be empty.")
        elif config_path_text == REDACTED_VALUE:
            _error("config_path", "invalid_value", "Config path cannot be the redacted placeholder.")
        else:
            try:
                resolved_config_path = resolve_config_path(repo_root, config_path_text)
                config_path_text = resolved_config_path.expanduser().resolve().as_posix()
            except Exception:
                config_path_text = _runner_start_path_text(config_path_text)
            overrides["config_path"] = config_path_text
            overrides["config"] = config_path_text

    loop_max_cycles_present, loop_max_cycles_raw = _raw_value("loop_max_cycles", "loopMaxCycles", "max_cycles", "maxCycles")
    if loop_max_cycles_present:
        loop_max_cycles = _runner_start_int(loop_max_cycles_raw)
        if loop_max_cycles is None or loop_max_cycles < 0:
            _error("loop_max_cycles", "invalid_value", "Max cycles must be an integer greater than or equal to 0.")
        else:
            overrides["loop_max_cycles"] = loop_max_cycles

    run_dir_present, run_dir_raw = _raw_value("run_dir", "runDir")
    run_dir_text = ""
    if run_dir_present:
        run_dir_text = _runner_start_path_text(run_dir_raw)
        if run_dir_text:
            if run_dir_text == REDACTED_VALUE:
                _error("run_dir", "invalid_value", "Run dir cannot be the redacted placeholder.")
            else:
                overrides["run_dir"] = run_dir_text
        if run_dir_text == REDACTED_VALUE:
            run_dir_text = ""

    resume_latest_present, resume_latest_raw = _raw_value("resume_latest", "resumeLatest", "resume-latest")
    if resume_latest_present:
        resume_latest = _parse_bool("resume_latest", resume_latest_raw)
        if resume_latest is not None:
            overrides["resume_latest"] = resume_latest
            if resume_latest and run_dir_text:
                _error("resume_latest", "invalid_combination", "Resume latest cannot be combined with an explicit run dir.")

    run_mode_present, run_mode_raw = _raw_value("run_mode", "runMode", "mode")
    continuous_present, continuous_raw = _raw_value("continuous")
    loop_present, loop_raw = _raw_value("loop")
    one_shot_present, one_shot_raw = _raw_value("one_shot", "one-shot", "oneShot")

    explicit_mode_fields = run_mode_present or continuous_present or loop_present or one_shot_present
    selected_mode = current_values.get("run_mode") or _runner_start_mode_from_contract(current_values)

    if run_mode_present:
        run_mode = str(run_mode_raw or "").strip().lower().replace("_", "-")
        if run_mode not in RUNNER_START_MODE_CHOICES:
            _error("run_mode", "invalid_choice", "Run mode must be one-shot, continuous, or loop.", {"choices": list(RUNNER_START_MODE_CHOICES)})
        else:
            selected_mode = run_mode
            if continuous_present:
                continuous = _parse_bool("continuous", continuous_raw)
                if continuous is not None and continuous != (run_mode in {"continuous", "loop"}):
                    _error("continuous", "invalid_combination", "Continuous does not match the selected run mode.")
            if loop_present:
                loop = _parse_bool("loop", loop_raw)
                if loop is not None and loop != (run_mode == "loop"):
                    _error("loop", "invalid_combination", "Loop does not match the selected run mode.")
            if one_shot_present:
                one_shot = _parse_bool("one_shot", one_shot_raw)
                if one_shot is not None and one_shot != (run_mode == "one-shot"):
                    _error("one_shot", "invalid_combination", "One-shot does not match the selected run mode.")
            if run_mode == "one-shot":
                overrides["continuous"] = False
                overrides["loop"] = False
            elif run_mode == "continuous":
                overrides["continuous"] = True
                overrides["loop"] = False
            else:
                overrides["continuous"] = True
                overrides["loop"] = True
    elif explicit_mode_fields:
        continuous_value = current_values.get("continuous")
        loop_value = current_values.get("loop")
        selected_mode = _runner_start_mode_from_flags(bool(continuous_value), bool(loop_value))

        if continuous_present:
            continuous = _parse_bool("continuous", continuous_raw)
            if continuous is not None:
                continuous_value = continuous
        if loop_present:
            loop = _parse_bool("loop", loop_raw)
            if loop is not None:
                loop_value = loop
        if one_shot_present:
            one_shot = _parse_bool("one_shot", one_shot_raw)
        else:
            one_shot = None

        if loop_present and bool(loop_value):
            if continuous_present and continuous is False:
                _error("loop", "invalid_combination", "Loop requires continuous to be true.")
            continuous_value = True
            selected_mode = "loop"
        elif continuous_present and bool(continuous_value):
            selected_mode = "continuous"
            if loop_present and loop_value:
                selected_mode = "loop"
        elif one_shot_present and bool(one_shot):
            selected_mode = "one-shot"
            continuous_value = False
            loop_value = False
        elif one_shot_present or continuous_present or loop_present:
            selected_mode = "one-shot"
            continuous_value = False
            loop_value = False

        if one_shot_present and bool(one_shot):
            if continuous_present and bool(continuous_raw):
                _error("one_shot", "invalid_combination", "One-shot cannot be combined with continuous.")
            if loop_present and bool(loop_raw):
                _error("one_shot", "invalid_combination", "One-shot cannot be combined with loop.")
            continuous_value = False
            loop_value = False

        overrides["continuous"] = bool(continuous_value)
        overrides["loop"] = bool(loop_value)
    # No mode fields present: keep the base runner mode.

    if errors:
        return {}, {
            "code": "runner_start_options_invalid",
            "message": "Runner start options are invalid.",
            "details": _runner_start_validation_payload(errors),
        }

    if "config_path" not in overrides:
        overrides["config_path"] = current_values.get("config_path") or _runner_start_path_text(
            getattr(base_args or argparse.Namespace(), "config_path", getattr(base_args or argparse.Namespace(), "config", "")) or default_config_path(repo_root)
        )
    if "config" not in overrides and "config_path" in overrides:
        overrides["config"] = overrides["config_path"]
    if "autopilot" not in overrides and autopilot_present is False:
        overrides["autopilot"] = bool(current_values.get("autopilot", False))
    if "loop_max_cycles" not in overrides and loop_max_cycles_present is False:
        maybe_loop_max_cycles = _runner_start_int(current_values.get("loop_max_cycles"))
        if maybe_loop_max_cycles is not None:
            overrides["loop_max_cycles"] = max(0, maybe_loop_max_cycles)
    if "run_dir" not in overrides and run_dir_present is False:
        maybe_run_dir = _runner_start_path_text(current_values.get("run_dir"))
        if maybe_run_dir:
            overrides["run_dir"] = maybe_run_dir
    if "resume_latest" not in overrides and resume_latest_present is False:
        overrides["resume_latest"] = bool(current_values.get("resume_latest", False))
    return overrides, None


class RunnerController:
    ALLOWED_TAIL_FILES = frozenset(
        {
            "cycle_summary.log",
            "metrics.jsonl",
            "run_summary.json",
            "last_run_summary.json",
            "STATE.json",
            "BACKLOG.md",
            "telegram_runner_subprocess.log",
        }
    )

    def __init__(self, *, repo: Path, base_args: argparse.Namespace, runner_mode: str = "thread") -> None:
        self.repo = repo.expanduser().resolve()
        self.base_args = base_args
        self.runner_mode = runner_mode if runner_mode in {"thread", "subprocess"} else "thread"

        self._runner_thread: Optional[threading.Thread] = None
        self._runner_process: Optional[subprocess.Popen[bytes]] = None
        self._runner_log_handle: Optional[Any] = None
        self._runner_watch_thread: Optional[threading.Thread] = None
        self._runner_exit_code: Optional[int] = None
        self._runner_started_at: Optional[float] = None
        self._start_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self.run_dir: Optional[Path] = None
        self._on_done_callbacks: list[Callable[[int], None]] = []

        configured_run_dir = str(getattr(base_args, "run_dir", "") or "").strip()
        if configured_run_dir:
            self.run_dir = Path(configured_run_dir).expanduser().resolve()

    def register_on_done(self, callback: Callable[[int], None]) -> None:
        self._on_done_callbacks.append(callback)

    def _fire_on_done(self, rc: int) -> None:
        for cb in self._on_done_callbacks:
            try:
                cb(rc)
            except Exception:
                pass

    def start_options_contract(self, *, redact_paths: bool = False) -> dict[str, Any]:
        return build_runner_start_options_contract(self.repo, self.base_args, redact_paths=redact_paths)

    def _stop_file_name(self) -> str:
        raw = str(getattr(self.base_args, "stop_file", "STOP") or "STOP").strip()
        return raw or "STOP"

    def _config_path_name(self) -> str:
        raw = str(getattr(self.base_args, "config_path", getattr(self.base_args, "config", "")) or "").strip()
        if not raw:
            return ""
        try:
            return Path(raw).expanduser().as_posix()
        except Exception:
            return raw.replace("\\", "/")

    def _stop_wait_timeout_seconds(self) -> int:
        try:
            value = int(getattr(self.base_args, "stop_wait_timeout_seconds", 180) or 180)
        except Exception:
            value = 180
        return max(1, value)

    def _runner_process_pid(self) -> int | None:
        proc = self._runner_process
        if proc is None or not proc.pid:
            return None
        return int(proc.pid)

    def _stop_file_paths(self, run_dir: Path) -> dict[str, str]:
        stop_file_name = self._stop_file_name()
        paths = {
            "stop_file_path": (run_dir / stop_file_name).as_posix(),
            "stop_progress_path": (run_dir / STOP_PROGRESS_FILE).as_posix(),
            "stop_progress_log_path": (run_dir / STOP_PROGRESS_LOG_FILE).as_posix(),
        }
        runner_pid = self._runner_process_pid()
        if runner_pid is not None:
            paths["runner_process_log_path"] = (run_dir / "telegram_runner_subprocess.log").as_posix()
        return paths

    def _stop_artifact_candidates(self, run_dir: Path) -> list[Path]:
        return [
            run_dir / "run_summary.json",
            run_dir / "last_run_summary.json",
            run_dir / "cycle_summary.log",
            run_dir / "STATE.json",
            run_dir / "metrics.jsonl",
            run_dir / "BACKLOG.json",
            run_dir / "NOTES.md",
        ]

    def _stop_log_candidates(self, run_dir: Path) -> list[Path]:
        return [
            run_dir / "logs" / "run.log",
            run_dir / "telegram_runner_subprocess.log",
            run_dir / "cycle_summary.log",
            run_dir / STOP_PROGRESS_LOG_FILE,
        ]

    def _latest_signal(self, paths: list[Path], *, kind: str) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        latest_epoch = -1.0
        for path in paths:
            signal = file_write_signal(path, kind=kind)
            if not signal:
                continue
            try:
                epoch = float(signal.get("updated_at_epoch") or 0.0)
            except Exception:
                epoch = 0.0
            if latest is None or epoch >= latest_epoch:
                latest = signal
                latest_epoch = epoch
        return latest

    def _stop_timeout_guidance(
        self,
        *,
        phase: str,
        runner_alive: bool,
        tracked_child_pids: list[int],
        tracked_child_processes: list[dict[str, Any]] | None,
        stop_file_paths: dict[str, str],
        runner_pid: int | None,
        last_artifact_signal: dict[str, Any] | None,
        last_log_signal: dict[str, Any] | None,
        manual_cleanup_hints: list[str] | None = None,
        locked_file_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        active_phase = str(phase or "").strip().lower()
        retryable = bool(runner_alive or active_phase in {"runner_wait", "timeout"})
        next_steps: list[str] = []
        if runner_alive:
            next_steps.append("Keep the panel open and retry stop after the runner settles.")
        elif active_phase == "timeout":
            next_steps.append("The stop wait timed out after the runner finished or was still settling.")
        if tracked_child_pids:
            joined = ", ".join(str(pid) for pid in tracked_child_pids)
            next_steps.append(f"Tracked child PIDs: {joined}.")
        if tracked_child_processes:
            details: list[str] = []
            for record in tracked_child_processes:
                pid = record.get("pid")
                if pid in (None, "", False):
                    continue
                piece = f"PID {pid}"
                session_file = str(record.get("session_file") or record.get("sessionFile") or "").strip()
                if session_file:
                    piece = f"{piece} ({session_file})"
                details.append(piece)
            if details:
                next_steps.append(f"Tracked child records: {'; '.join(details)}.")
        if runner_pid is not None:
            next_steps.append(f"Runner PID: {runner_pid}.")
        if last_artifact_signal and last_artifact_signal.get("path"):
            next_steps.append(f"Latest artifact write: {last_artifact_signal.get('path')}.")
        if last_log_signal and last_log_signal.get("path"):
            next_steps.append(f"Latest log write: {last_log_signal.get('path')}.")
        for path_key, label in (
            ("stop_file_path", "Stop file path"),
            ("stop_progress_path", "Stop progress file"),
            ("stop_progress_log_path", "Stop progress log"),
            ("runner_process_log_path", "Runner process log"),
        ):
            path_value = stop_file_paths.get(path_key)
            if path_value:
                next_steps.append(f"{label}: {path_value}.")
        if manual_cleanup_hints:
            next_steps.extend([str(item).strip() for item in manual_cleanup_hints if str(item).strip()])
        if active_phase == "timeout":
            summary = "Stop wait timed out. Retry the stop action or inspect the tracked child processes."
        elif retryable:
            summary = "Runner is still shutting down. Retry the stop action or inspect the tracked child processes."
        else:
            summary = "Stop sequence finalized."
        guidance = {
            "recoverable": retryable,
            "can_retry": retryable,
            "summary": summary,
            "message": summary,
            "steps": next_steps,
            "manual_cleanup_hints": list(dict.fromkeys([str(item).strip() for item in (manual_cleanup_hints or []) if str(item).strip()])),
            "locked_file_paths": list(dict.fromkeys([str(item).strip() for item in (locked_file_paths or []) if str(item).strip()])),
        }
        return guidance

    def _stop_progress_context(self, run_dir: Path, *, phase: str, manual_cleanup_hints: list[str] | None = None, locked_file_paths: list[str] | None = None) -> dict[str, Any]:
        runner_alive = self._runner_is_alive()
        tracked_child_processes = tracked_pid_details()
        tracked_child_pids = [int(record.get("pid")) for record in tracked_child_processes if record.get("pid") not in (None, "", False)]
        runner_pid = self._runner_process_pid()
        stop_file_paths = self._stop_file_paths(run_dir)
        last_artifact_signal = self._latest_signal(self._stop_artifact_candidates(run_dir), kind="artifact")
        last_log_signal = self._latest_signal(self._stop_log_candidates(run_dir), kind="log")
        timeout_guidance = self._stop_timeout_guidance(
            phase=phase,
            runner_alive=runner_alive,
            tracked_child_pids=tracked_child_pids,
            tracked_child_processes=tracked_child_processes,
            stop_file_paths=stop_file_paths,
            runner_pid=runner_pid,
            last_artifact_signal=last_artifact_signal,
            last_log_signal=last_log_signal,
            manual_cleanup_hints=manual_cleanup_hints,
            locked_file_paths=locked_file_paths,
        )
        return {
            "runner_alive": runner_alive,
            "runnerAlive": runner_alive,
            "running": runner_alive,
            "runner_pid": runner_pid,
            "runnerPid": runner_pid,
            "tracked_child_pids": tracked_child_pids,
            "trackedChildPids": tracked_child_pids,
            "tracked_child_processes": tracked_child_processes,
            "trackedChildProcesses": tracked_child_processes,
            "stop_file_paths": stop_file_paths,
            "stopFilePaths": stop_file_paths,
            "last_artifact_signal": last_artifact_signal,
            "lastArtifactSignal": last_artifact_signal,
            "last_log_signal": last_log_signal,
            "lastLogSignal": last_log_signal,
            "timeout_guidance": timeout_guidance,
            "timeoutGuidance": timeout_guidance,
            "manual_cleanup_hints": list(timeout_guidance.get("manual_cleanup_hints") or []),
            "manualCleanupHints": list(timeout_guidance.get("manual_cleanup_hints") or []),
            "locked_file_paths": list(timeout_guidance.get("locked_file_paths") or []),
            "lockedFilePaths": list(timeout_guidance.get("locked_file_paths") or []),
        }

    def _stop_progress_snapshot(self, run_dir: Path | None, *, phase: str | None = None) -> dict[str, Any]:
        if run_dir is None:
            return {}
        snapshot = normalize_stop_progress_payload(read_stop_progress(run_dir))
        if not snapshot:
            return {}
        current_phase = snapshot.get("current_phase") if isinstance(snapshot.get("current_phase"), dict) else {}
        phase_name = str(phase or snapshot.get("phase") or current_phase.get("phase") or "").strip()
        context = self._stop_progress_context(
            run_dir,
            phase=phase_name,
            manual_cleanup_hints=list(snapshot.get("manual_cleanup_hints") or snapshot.get("manualCleanupHints") or []),
            locked_file_paths=list(snapshot.get("locked_file_paths") or snapshot.get("lockedFilePaths") or []),
        )
        snapshot.update(context)
        if not snapshot.get("stop_file_paths"):
            snapshot["stop_file_paths"] = self._stop_file_paths(run_dir)
            snapshot["stopFilePaths"] = dict(snapshot["stop_file_paths"])
        if not snapshot.get("timeout_guidance"):
            snapshot["timeout_guidance"] = self._stop_timeout_guidance(
                phase=phase_name,
                runner_alive=bool(snapshot.get("runner_alive")),
                tracked_child_pids=list(snapshot.get("tracked_child_pids") or []),
                tracked_child_processes=list(snapshot.get("tracked_child_processes") or []),
                stop_file_paths=dict(snapshot.get("stop_file_paths") or {}),
                runner_pid=snapshot.get("runner_pid") if isinstance(snapshot.get("runner_pid"), int) else None,
                last_artifact_signal=snapshot.get("last_artifact_signal") if isinstance(snapshot.get("last_artifact_signal"), dict) else None,
                last_log_signal=snapshot.get("last_log_signal") if isinstance(snapshot.get("last_log_signal"), dict) else None,
                manual_cleanup_hints=list(snapshot.get("manual_cleanup_hints") or []),
                locked_file_paths=list(snapshot.get("locked_file_paths") or []),
            )
            snapshot["timeoutGuidance"] = dict(snapshot["timeout_guidance"])
        return snapshot

    def _effective_dict(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        eff: dict[str, Any] = {}
        for key, default_value in DEFAULTS.items():
            eff[key] = getattr(self.base_args, key, default_value)
        if overrides:
            eff.update(overrides)
        eff["repo"] = self.repo.as_posix()
        try:
            eff["prompts_dir"] = resolve_prompts_dir(self.repo, str(eff.get("prompts_dir") or "")).as_posix()
        except Exception:
            pass
        return eff

    def _ensure_run_dir(self, eff: dict[str, Any]) -> Path:
        explicit = str(eff.get("run_dir") or "").strip()
        if explicit:
            return Path(explicit).expanduser().resolve()
        # Fresh runs are the default. Only an explicit resume_latest request may
        # reuse the latest observed run directory.
        latest = find_latest_run_dir(self.repo) if bool(eff.get("resume_latest")) else None
        return latest if latest is not None else make_run_dir(self.repo)

    def _refresh_process_state(self) -> None:
        proc = self._runner_process
        if proc is None:
            return
        rc = proc.poll()
        if rc is None:
            return
        self._finalize_runner_process(proc, int(rc))

    def _finalize_runner_process(self, proc: subprocess.Popen[bytes], rc: int) -> None:
        with self._state_lock:
            if self._runner_process is not proc:
                return
            self._runner_exit_code = int(rc)
            try:
                if proc.pid:
                    terminate_process_tree(int(proc.pid), include_root=False, wait=True)
                    unregister_pid_if_exited(int(proc.pid))
            except Exception:
                pass
            self._runner_process = None
            if self._runner_log_handle is not None:
                try:
                    self._runner_log_handle.close()
                except Exception:
                    pass
                self._runner_log_handle = None

    def _start_subprocess_watch(self, proc: subprocess.Popen[bytes]) -> None:
        def _watch() -> None:
            try:
                rc = proc.wait()
            except Exception:
                rc = 1
            self._finalize_runner_process(proc, int(rc))
            self._fire_on_done(int(rc))

        self._runner_watch_thread = threading.Thread(
            target=_watch,
            name="agentcli-runner-subprocess-watch",
            daemon=True,
        )
        self._runner_watch_thread.start()

    def _runner_is_alive(self) -> bool:
        if self.runner_mode == "subprocess":
            self._refresh_process_state()
            return bool(self._runner_process and self._runner_process.poll() is None)
        return bool(self._runner_thread and self._runner_thread.is_alive())

    def _start_thread(self, args: argparse.Namespace) -> None:
        def _target() -> None:
            self._runner_exit_code = None
            try:
                rc = run_runner(args)
            except Exception:
                rc = 1
            self._runner_exit_code = int(rc)
            self._fire_on_done(int(rc))

        self._runner_thread = threading.Thread(target=_target, name="agentcli-runner", daemon=True)
        self._runner_thread.start()

    def _start_subprocess(self, args: argparse.Namespace, run_dir: Path) -> None:
        payload_path = run_dir / ".telegram_runner_args.json"
        payload_path.write_text(
            json.dumps(vars(args), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            errors="replace",
        )
        log_path = run_dir / "telegram_runner_subprocess.log"
        try:
            rotate_log_file(log_path, max_bytes=20_000_000, backup_count=5, max_age_days=14)
            self._runner_log_handle = log_path.open("ab")
            cmd = [
                sys.executable,
                "-m",
                "agent_runner.remote.subprocess_runner",
                "--args-json",
                str(payload_path),
            ]
            self._runner_process = subprocess.Popen(
                cmd,
                cwd=str(self.repo),
                stdin=subprocess.DEVNULL,
                stdout=self._runner_log_handle,
                stderr=subprocess.STDOUT,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
            )
            if self._runner_process.pid:
                try:
                    register_pid(int(self._runner_process.pid))
                except Exception:
                    pass
            self._start_subprocess_watch(self._runner_process)
        except Exception:
            if self._runner_log_handle is not None:
                try:
                    self._runner_log_handle.close()
                except Exception:
                    pass
            self._runner_log_handle = None
            self._runner_process = None
            raise

    def start(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_overrides, validation_error = normalize_runner_start_options(
            self.repo,
            overrides,
            base_args=self.base_args,
            contract=self.start_options_contract(),
        )
        context = {
            "repo": self.repo.as_posix(),
            "config_path": str(
                normalized_overrides.get("config_path")
                or self._config_path_name()
            ),
        }
        if validation_error:
            return {"ok": False, "message": str(validation_error.get("message") or "Runner start options are invalid."), "error": validation_error, **context}
        if not self._start_lock.acquire(blocking=False):
            return {"ok": False, "message": "\ub7ec\ub108 \uc2dc\uc791\uc774 \uc774\ubbf8 \uc9c4\ud589 \uc911\uc785\ub2c8\ub2e4.", **context}

        try:
            if self._runner_is_alive():
                return {"ok": False, "message": "\ub7ec\ub108\uac00 \uc774\ubbf8 \uc2e4\ud589 \uc911\uc785\ub2c8\ub2e4.", **context}

            base_run_dir_intent = str(getattr(self.base_args, "run_dir", "") or "").strip()
            base_resume_latest_intent = bool(getattr(self.base_args, "resume_latest", False))
            eff = self._effective_dict(normalized_overrides)
            run_dir = self._ensure_run_dir(eff)
            run_dir.mkdir(parents=True, exist_ok=True)
            self.run_dir = run_dir
            eff["run_dir"] = run_dir.as_posix()

            stop_paths = {run_dir / self._stop_file_name(), run_dir / "STOP"}
            for stop_path in stop_paths:
                try:
                    if stop_path.exists():
                        stop_path.unlink()
                except Exception:
                    pass
            clear_stop_progress(run_dir)

            args = argparse.Namespace(**eff)
            self._runner_exit_code = None
            self._runner_started_at = time.time()

            if self.runner_mode == "subprocess":
                self._start_subprocess(args, run_dir)
            else:
                self._start_thread(args)

            updated_base = dict(eff)
            updated_base["run_dir"] = str(normalized_overrides.get("run_dir") or base_run_dir_intent)
            updated_base["resume_latest"] = base_resume_latest_intent
            self.base_args = argparse.Namespace(**updated_base)

            return {
                "ok": True,
                "message": "\ub7ec\ub108\uac00 \uc2dc\uc791\ub418\uc5c8\uc2b5\ub2c8\ub2e4.",
                "runner_mode": self.runner_mode,
                "run_dir": run_dir.as_posix(),
                **context,
            }
        finally:
            self._start_lock.release()

    def _emit_stop_progress(
        self,
        run_dir: Path,
        *,
        phase: str,
        message: str,
        requested_at_monotonic: float,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        extra_fields = dict(fields)
        manual_cleanup_hints = extra_fields.pop("manual_cleanup_hints", extra_fields.pop("manualCleanupHints", None))
        locked_file_paths = extra_fields.pop("locked_file_paths", extra_fields.pop("lockedFilePaths", None))
        context_fields = self._stop_progress_context(
            run_dir,
            phase=phase,
            manual_cleanup_hints=list(manual_cleanup_hints or []),
            locked_file_paths=list(locked_file_paths or []),
        )
        context_fields.update(extra_fields)
        runner_alive = bool(context_fields.pop("runner_alive", False))
        context_fields.pop("runnerAlive", None)
        context_fields.pop("running", None)
        progress = write_stop_progress(
            run_dir,
            phase=phase,
            message=message,
            requested_at_monotonic=requested_at_monotonic,
            running=runner_alive,
            runner_alive=runner_alive,
            runner_mode=self.runner_mode,
            last_event=self._latest_event(run_dir),
            **context_fields,
        )
        if progress_callback is not None:
            try:
                progress_callback(progress)
            except Exception:
                pass
        return progress

    def stop(
        self,
        *,
        wait: bool = False,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        context = {"repo": self.repo.as_posix(), "config_path": self._config_path_name()}
        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return {"ok": False, "message": "\uc2e4\ud589 \ub514\ub809\ud1a0\ub9ac\ub97c \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.", **context}
        self.run_dir = run_dir
        requested_at = time.monotonic()
        self._emit_stop_progress(
            run_dir,
            phase="request",
            message="Stop requested.",
            requested_at_monotonic=requested_at,
            progress_callback=progress_callback,
        )

        stop_path = run_dir / self._stop_file_name()
        try:
            stop_path.write_text(STOP_REASON_STOP_FILE + "\n", encoding="utf-8", errors="replace")
        except Exception as ex:
            self._emit_stop_progress(
                run_dir,
                phase="failed",
                message=f"Failed to create stop file: {ex}",
                requested_at_monotonic=requested_at,
                progress_callback=progress_callback,
            )
            return {"ok": False, "message": f"\uc815\uc9c0 \ud30c\uc77c \uc0dd\uc131 \uc2e4\ud328: {ex}", **context}

        self._emit_stop_progress(
            run_dir,
            phase="stop_file_write",
            message=f"Stop file written: {stop_path.as_posix()}",
            requested_at_monotonic=requested_at,
            progress_callback=progress_callback,
        )

        if self.runner_mode == "thread":
            try:
                self._emit_stop_progress(
                    run_dir,
                    phase="child_termination",
                    message="Terminating tracked child processes.",
                    requested_at_monotonic=requested_at,
                    progress_callback=progress_callback,
                )
                terminate_all_children()
            except Exception:
                pass

            if wait and self._runner_thread and self._runner_thread.is_alive():
                wait_timeout = self._stop_wait_timeout_seconds()
                deadline = time.monotonic() + wait_timeout
                while self._runner_thread.is_alive() and time.monotonic() < deadline:
                    self._emit_stop_progress(
                        run_dir,
                        phase="runner_wait",
                        message="Waiting for runner shutdown and final artifacts.",
                        requested_at_monotonic=requested_at,
                        progress_callback=progress_callback,
                    )
                    self._runner_thread.join(timeout=1.0)
        else:
            self._refresh_process_state()
            if wait and self._runner_process and self._runner_process.poll() is None:
                grace_deadline = time.monotonic() + 12
                while self._runner_process and self._runner_process.poll() is None and time.monotonic() < grace_deadline:
                    self._emit_stop_progress(
                        run_dir,
                        phase="runner_wait",
                        message="Waiting for runner subprocess to exit.",
                        requested_at_monotonic=requested_at,
                        progress_callback=progress_callback,
                        pid=int(self._runner_process.pid) if self._runner_process.pid else None,
                    )
                    time.sleep(1.0)
                self._refresh_process_state()
                if self._runner_process and self._runner_process.poll() is None:
                    try:
                        self._emit_stop_progress(
                            run_dir,
                            phase="child_termination",
                            message="Grace period expired; terminating runner process tree.",
                            requested_at_monotonic=requested_at,
                            progress_callback=progress_callback,
                            pid=int(self._runner_process.pid) if self._runner_process.pid else None,
                        )
                        terminate_process_tree(int(self._runner_process.pid), include_root=True)
                        force_deadline = time.monotonic() + 20
                        while self._runner_process and self._runner_process.poll() is None and time.monotonic() < force_deadline:
                            self._emit_stop_progress(
                                run_dir,
                                phase="runner_wait",
                                message="Waiting after forced process-tree termination.",
                                requested_at_monotonic=requested_at,
                                progress_callback=progress_callback,
                                pid=int(self._runner_process.pid) if self._runner_process.pid else None,
                            )
                            time.sleep(1.0)
                    except Exception:
                        try:
                            self._runner_process.kill()
                        except Exception:
                            pass
                self._refresh_process_state()

        running = self._runner_is_alive()
        if not running:
            close_all_loggers()
            self._emit_stop_progress(
                run_dir,
                phase="final_artifact_collection",
                message="Collecting final artifacts and logs.",
                requested_at_monotonic=requested_at,
                progress_callback=progress_callback,
                exit_code=self._runner_exit_code,
            )
            final_phase = "finalized"
            final_message = "Runner stop sequence finished."
            self._emit_stop_progress(
                run_dir,
                phase=final_phase,
                message=final_message,
                requested_at_monotonic=requested_at,
                progress_callback=progress_callback,
                exit_code=self._runner_exit_code,
            )
        elif wait:
            final_phase = "timeout"
            final_message = f"Runner is still alive after {self._stop_wait_timeout_seconds()}s stop wait timeout."
            self._emit_stop_progress(
                run_dir,
                phase=final_phase,
                message=final_message,
                requested_at_monotonic=requested_at,
                progress_callback=progress_callback,
                exit_code=self._runner_exit_code,
            )
        else:
            final_phase = "runner_wait"
            final_message = "Stop requested; runner is still shutting down."
            self._emit_stop_progress(
                run_dir,
                phase=final_phase,
                message=final_message,
                requested_at_monotonic=requested_at,
                progress_callback=progress_callback,
                exit_code=self._runner_exit_code,
            )

        final_progress = read_stop_progress(run_dir)

        return {
            "ok": not (wait and running),
            "message": final_message if wait and running else f"\uc911\uc9c0 \uc694\uccad\ub428: {stop_path.as_posix()}",
            "running": running,
            "runner_alive": running,
            "runnerAlive": running,
            "run_dir": run_dir.as_posix(),
            "tracked_child_pids": list(final_progress.get("tracked_child_pids") or []),
            "trackedChildPids": list(final_progress.get("tracked_child_pids") or []),
            "tracked_child_processes": list(final_progress.get("tracked_child_processes") or []),
            "trackedChildProcesses": list(final_progress.get("tracked_child_processes") or []),
            "stop_file_paths": dict(final_progress.get("stop_file_paths") or {}),
            "stopFilePaths": dict(final_progress.get("stop_file_paths") or {}),
            "last_artifact_signal": final_progress.get("last_artifact_signal"),
            "lastArtifactSignal": final_progress.get("last_artifact_signal"),
            "last_log_signal": final_progress.get("last_log_signal"),
            "lastLogSignal": final_progress.get("last_log_signal"),
            "timeout_guidance": dict(final_progress.get("timeout_guidance") or {}),
            "timeoutGuidance": dict(final_progress.get("timeout_guidance") or {}),
            "manual_cleanup_hints": list(final_progress.get("manual_cleanup_hints") or []),
            "manualCleanupHints": list(final_progress.get("manual_cleanup_hints") or []),
            "locked_file_paths": list(final_progress.get("locked_file_paths") or []),
            "lockedFilePaths": list(final_progress.get("locked_file_paths") or []),
            "stop_progress": final_progress,
            **context,
        }

    def _load_json(self, path: Path, fallback: Any) -> Any:
        try:
            if not path.exists():
                return fallback
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
            if not raw:
                return fallback
            return json.loads(raw)
        except Exception:
            return fallback

    def _tail_lines(self, path: Path, lines: int) -> str:
        if not path.exists() or not path.is_file():
            return ""
        max_lines = max(1, int(lines))
        dq: deque[str] = deque(maxlen=max_lines)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    dq.append(line.rstrip("\n"))
        except Exception:
            return ""
        return "\n".join(dq).strip()

    def _latest_event(self, run_dir: Path) -> str:
        metrics_path = run_dir / "metrics.jsonl"
        tail = self._tail_lines(metrics_path, 5)
        if not tail:
            return ""
        events: list[dict[str, Any]] = []
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        if not events:
            return ""
        latest = events[-1]
        fields: list[str] = []
        ts = str(latest.get("ts") or "").strip()
        event_type = str(latest.get("type") or "").strip()
        if ts:
            fields.append(ts)
        if event_type:
            fields.append(event_type)
        task_id = str(latest.get("task_id") or "").strip()
        if task_id:
            fields.append(f"task={task_id}")
        reason = str(latest.get("reason") or "").strip()
        if reason:
            fields.append(f"reason={reason}")
        rc = latest.get("rc")
        if isinstance(rc, int):
            fields.append(f"rc={rc}")
        return " ".join(fields).strip()

    def _state_counts(self, run_dir: Path) -> dict[str, int]:
        state = load_state(run_dir / "STATE.json")
        backlog_task_ids = load_backlog_task_ids(run_dir / "BACKLOG.json")
        return count_state_task_ids(state, backlog_task_ids)

    def _stop_reason(self, run_dir: Path) -> str:
        stop_paths = [run_dir / self._stop_file_name(), run_dir / "STOP"]
        detected = detect_stop_reason(stop_paths)
        if detected:
            return detected
        summary_payload = self._load_json(run_dir / "run_summary.json", {})
        if isinstance(summary_payload, dict):
            final = summary_payload.get("final")
            if isinstance(final, dict):
                return str(final.get("reason") or "").strip()
        return ""

    def _runs_root(self) -> Path:
        root = self.repo / AGENT_WORK_DIR / "agent_runs"
        if root.exists():
            return root
        legacy = self.repo / ".doc" / "agent_runs"
        if legacy.exists():
            return legacy
        return root

    def _collect_run_info(self, run_dir: Path) -> dict[str, Any]:
        counts = self._state_counts(run_dir)
        cycle_tail = self._tail_lines(run_dir / "cycle_summary.log", 1)
        return {
            "run_id": run_dir.name,
            "run_dir": run_dir.as_posix(),
            "done": counts["done"],
            "failed": counts["failed"],
            "warnings": counts["warnings"],
            "state_counts": dict(counts),
            "reason": self._stop_reason(run_dir),
            "last_cycle": cycle_tail,
        }

    def list_runs(self, n: int = 10) -> list[dict[str, Any]]:
        root = self._runs_root()
        if not root.exists() or not root.is_dir():
            return []
        dirs = [p for p in root.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.name, reverse=True)
        out: list[dict[str, Any]] = []
        for run_dir in dirs[: max(1, int(n))]:
            out.append(self._collect_run_info(run_dir))
        return out

    def tail(self, *, name: str = "cycle_summary.log", lines: int = 50) -> str:
        file_name = str(name or "cycle_summary.log").strip()
        if file_name not in self.ALLOWED_TAIL_FILES:
            allowed = ", ".join(sorted(self.ALLOWED_TAIL_FILES))
            raise ValueError(f"\uc9c0\uc6d0\ub418\uc9c0 \uc54a\ub294 \ud30c\uc77c. \ud5c8\uc6a9: {allowed}")

        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return ""
        self.run_dir = run_dir
        return self._tail_lines(run_dir / file_name, max(1, int(lines)))

    def filter_metrics(self, *, event_type: str = "", errors_only: bool = False, limit: int = 50) -> str:
        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return ""
        self.run_dir = run_dir
        metrics_path = run_dir / "metrics.jsonl"
        if not metrics_path.exists() or not metrics_path.is_file():
            return ""

        event_key = str(event_type or "").strip().lower()
        max_lines = max(1, int(limit))
        dq: deque[str] = deque(maxlen=max_lines)

        try:
            with metrics_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    raw = line.rstrip("\n")
                    if not raw.strip():
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        payload = {}

                    if not isinstance(payload, dict):
                        continue

                    ev = str(payload.get("event") or payload.get("type") or "").strip().lower()
                    level = str(payload.get("level") or "").strip().lower()
                    reason = str(payload.get("reason") or payload.get("message") or "").strip().lower()
                    rc = payload.get("rc")

                    if event_key and ev != event_key:
                        continue

                    if errors_only:
                        is_error = False
                        if level == "error":
                            is_error = True
                        if isinstance(rc, int) and rc != 0:
                            is_error = True
                        if any(tok in ev for tok in ("error", "fail", "exception", "violation", "exhausted")):
                            is_error = True
                        if any(tok in reason for tok in ("error", "fail", "exception", "violation", "exhausted")):
                            is_error = True
                        if not is_error:
                            continue

                    dq.append(raw)
        except Exception:
            return ""
        return "\n".join(dq).strip()

    def grep(self, *, pattern: str, name: str = "metrics.jsonl", lines: int = 50, ignore_case: bool = True) -> str:
        file_name = str(name or "metrics.jsonl").strip()
        if file_name not in self.ALLOWED_TAIL_FILES:
            allowed = ", ".join(sorted(self.ALLOWED_TAIL_FILES))
            raise ValueError(f"\uc9c0\uc6d0\ub418\uc9c0 \uc54a\ub294 \ud30c\uc77c. \ud5c8\uc6a9: {allowed}")

        needle = str(pattern or "").strip()
        if not needle:
            raise ValueError("\ud328\ud134\uc774 \ube44\uc5b4 \uc788\uc2b5\ub2c8\ub2e4")

        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return ""
        self.run_dir = run_dir
        path = run_dir / file_name
        if not path.exists() or not path.is_file():
            return ""

        flags = re.IGNORECASE if ignore_case else 0
        try:
            rx = re.compile(needle, flags)
        except Exception as ex:
            raise ValueError(f"\uc798\ubabb\ub41c \uc815\uaddc\uc2dd: {ex}") from ex

        max_lines = max(1, int(lines))
        dq: deque[str] = deque(maxlen=max_lines)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    raw = line.rstrip("\n")
                    if not raw:
                        continue
                    if rx.search(raw):
                        dq.append(raw)
        except Exception:
            return ""
        return "\n".join(dq).strip()

    def status(self, *, redact_paths: bool = False) -> dict[str, Any]:
        running = self._runner_is_alive()
        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        config_path = self._config_path_name()
        start_options = self.start_options_contract(redact_paths=redact_paths)
        if run_dir is not None:
            self.run_dir = run_dir

        uptime_seconds = 0
        if self._runner_started_at:
            uptime_seconds = max(0, int(time.time() - self._runner_started_at))

        status: dict[str, Any] = {
            "running": running,
            "runner_mode": self.runner_mode,
            "repo": self.repo.as_posix(),
            "config_path": config_path,
            "run_dir": run_dir.as_posix() if run_dir else "",
            "uptime_seconds": uptime_seconds,
            "exit_code": self._runner_exit_code,
            "stop_file": self._stop_file_name(),
            "stop_file_exists": False,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "state_counts": {"done": 0, "failed": 0, "warnings": 0},
            "reason": "",
            "last_event": "",
            "stop_progress": {},
            "start_options": start_options,
            "startOptions": start_options,
        }
        if run_dir is None:
            return status

        stop_path = run_dir / self._stop_file_name()
        status["stop_file_exists"] = bool(stop_path.exists())
        counts = self._state_counts(run_dir)
        status["done"] = counts["done"]
        status["failed"] = counts["failed"]
        status["warnings"] = counts["warnings"]
        status["state_counts"] = dict(counts)
        status["reason"] = self._stop_reason(run_dir)
        status["last_event"] = self._latest_event(run_dir)
        status["stop_progress"] = read_stop_progress(run_dir)
        status["start_options"] = start_options
        status["startOptions"] = start_options
        return status
