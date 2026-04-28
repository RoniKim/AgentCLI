from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import threading
import time
import json
from pathlib import Path
from typing import Any, Dict, Optional

from .cli import DEFAULTS
from .config import (
    app_home,
    load_config,
    save_config,
    resolve_config_path,
    default_config_path,
    legacy_default_config_path,
    legacy_config_path,
    resolve_prompts_dir,
)
from .goals import resolve_goals_completion_level
from .runner_entry import run as run_runner
from .run_dir import make_run_dir
from .run_dir import find_latest_run_dir
from .logger import close_all_loggers
from .todo import ensure_todo_file, read_current_todo, set_current_todo, open_path
from .preflight import run_preflight
from .process_guard import init_process_guard, terminate_all_children
from .stop_progress import clear_stop_progress, read_stop_progress, write_stop_progress
from .utils import STOP_REASON_STOP_FILE
from .remote.controller import (
    RUNNER_CONTROL_EVENT_FILE,
    read_runner_control_event,
    write_runner_control_event,
)
from .gitops import (
    apply_pending_worktree_merge,
    discard_pending_worktree_merge,
    find_pending_worktree_merge,
    read_pending_worktree_merge,
    scan_worktree_diagnostics,
)

# prompt_toolkit is an optional dependency at import time (for nicer UX).
# If it's missing, we fall back to basic input().
try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import NestedCompleter, WordCompleter
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:  # pragma: no cover
    PromptSession = None  # type: ignore
    AutoSuggestFromHistory = None  # type: ignore
    NestedCompleter = None  # type: ignore
    WordCompleter = None  # type: ignore
    FileHistory = None  # type: ignore
    patch_stdout = None  # type: ignore


BOOL_TRUE = {"1", "true", "t", "yes", "y", "on"}
BOOL_FALSE = {"0", "false", "f", "no", "n", "off"}
SHELL_STOP_ARTIFACT_NAMES = {
    "BACKLOG.json",
    "BACKLOG.md",
    "STATE.json",
    "cycle_summary.log",
    RUNNER_CONTROL_EVENT_FILE,
    "last_run_summary.json",
    "metrics.jsonl",
    "run_summary.json",
}


def _coerce_value(key: str, raw: str, default: Any) -> Any:
    """Coerce user input to the expected type based on DEFAULTS."""
    if isinstance(default, bool):
        v = raw.strip().lower()
        if v in BOOL_TRUE:
            return True
        if v in BOOL_FALSE:
            return False
        raise ValueError(f"Expected boolean for {key} (use true/false).")
    if isinstance(default, int):
        return int(raw.strip())
    if isinstance(default, list):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        return parts
    if isinstance(default, dict):
        try:
            import json
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass
        return default  # ignore invalid input for dict keys
    return raw


def _yesno(v: bool) -> str:
    return "yes" if v else "no"


def _shorten(path: Optional[Path]) -> str:
    return str(path) if path else "(not set)"


def _parse_kv_tokens(tokens: list[str], defaults: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """
    Parse tokens like:
      --autopilot
      --no-autopilot
      --iterations 30
    into overrides, based on DEFAULTS (or supplied defaults dict).
    """
    defs = defaults if defaults is not None else DEFAULTS
    out: Dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if not token.startswith("--"):
            i += 1
            continue
        raw_key = token[2:]
        # Handle --no-<key> prefix for booleans
        if raw_key.startswith("no-") or raw_key.startswith("no_"):
            key = raw_key[3:].replace("-", "_").strip()
            if key in defs and isinstance(defs[key], bool):
                out[key] = False
                i += 1
                continue
        key = raw_key.replace("-", "_").strip()
        if key not in defs:
            # Unknown option: ignore (shell should be forgiving)
            i += 1
            continue
        default = defs[key]
        if isinstance(default, bool):
            # Check for --no-<key> prefix
            if token.startswith("--no-"):
                out[key] = False
            elif i + 1 < len(tokens) and tokens[i + 1].lower() in ("false", "no", "0", "off"):
                out[key] = False
                i += 1
            else:
                out[key] = True
            i += 1
            continue
        if i + 1 >= len(tokens):
            i += 1
            continue
        out[key] = _coerce_value(key, tokens[i + 1], default)
        i += 2
    return out


class RunnerShell:
    def __init__(self, initial_argv: list[str] | None = None, controller: Any | None = None) -> None:
        self.repo: Optional[Path] = None
        self.config_path: Optional[Path] = None
        self.config: Dict[str, Any] = {}
        self.overrides: Dict[str, Any] = {}
        self.run_dir: Optional[Path] = None
        self._controller = controller
        self.runner_mode = "thread"

        self._runner_thread: Optional[threading.Thread] = None
        self._runner_exit_code: Optional[int] = None
        self._runner_started_at: Optional[float] = None
        self._start_lock = threading.Lock()

        self._apply_initial_argv(initial_argv or [])

    def _apply_initial_argv(self, argv: list[str]) -> None:
        """Allow pre-seeding repo/config/autopilot from CLI args, without starting."""
        i = 0
        while i < len(argv):
            a = argv[i]
            if a == "--repo" and i + 1 < len(argv):
                self.set_repo(argv[i + 1])
                i += 2
                continue
            if a == "--config" and i + 1 < len(argv):
                self.set_config_path(argv[i + 1])
                i += 2
                continue
            # common convenience flags
            if a == "--autopilot":
                self.overrides["autopilot"] = True
                i += 1
                continue
            if a == "--loop":
                self.overrides["loop"] = True
                i += 1
                continue
            if a == "--debug":
                self.overrides["debug"] = True
                i += 1
                continue
            i += 1

        # Parse any remaining --key value tokens that match DEFAULTS
        extra = _parse_kv_tokens(argv, DEFAULTS)
        self.overrides.update(extra)

        if self.repo:
            self._ensure_config_loaded()

    def _ensure_config_loaded(self) -> None:
        if not self.repo:
            return
        self.config_path = self.config_path or default_config_path(self.repo)
        # Prefer AgentCLI-side config. If missing, fall back to legacy repo/.doc config for compatibility.
        load_path = self.config_path
        if not load_path.exists():
            fallback = legacy_default_config_path(self.repo)
            if fallback and fallback.exists():
                load_path = fallback
            else:
                load_path = legacy_config_path(self.repo)
        if load_path.exists():
            try:
                self.config = load_config(load_path)
                # If we loaded legacy config, we keep config_path pointing to the new default
                # so that a subsequent /save migrates out of the repo.
                if load_path != self.config_path:
                    print(f"[INFO] Loaded legacy config: {load_path}")
            except Exception as ex:
                print(f"[WARN] Failed to load config: {load_path} ({ex})")
                self.config = {}
        else:
            self.config = {}

    def set_repo(self, path_str: str) -> None:
        self.repo = Path(path_str).expanduser().resolve()
        self._ensure_config_loaded()

    def set_config_path(self, path_str: str) -> None:
        if not self.repo:
            # treat as absolute-ish, resolve from CWD
            p = Path(path_str).expanduser()
            self.config_path = p.resolve()
        else:
            self.config_path = resolve_config_path(self.repo, path_str)
        self._ensure_config_loaded()

    def _config_path_name(self) -> str:
        path = self.config_path
        if path is None and self.repo is not None:
            path = default_config_path(self.repo)
        if path is None:
            return ""
        try:
            return path.expanduser().resolve().as_posix()
        except Exception:
            return str(path).replace("\\", "/")

    def effective(self) -> Dict[str, Any]:
        eff: Dict[str, Any] = dict(DEFAULTS)
        # Apply known keys from config (forward-compat: keep unknown keys in self.config for /save)
        if self.config:
            for k in DEFAULTS.keys():
                if k in self.config and k not in {"run_dir", "resume_latest"}:
                    eff[k] = self.config[k]
        if self.overrides:
            eff.update(self.overrides)
        if self.repo:
            eff["repo"] = str(self.repo)
        eff["goals_completion_level"] = resolve_goals_completion_level(eff.get("goals_completion_level"))
        return eff

    def print_config(self, show_all: bool = False) -> None:
        eff = self.effective()
        repo = self.repo
        cfgp = self.config_path or (default_config_path(repo) if repo else None)

        print("\n=== AgentCLI Shell: Current Settings ===")
        print(f"repo:       {_shorten(repo)}")
        print(f"config:     {_shorten(cfgp)}")
        run_dir_display = eff.get("run_dir") or (self.run_dir.as_posix() if self.run_dir else "(auto, fresh)")
        print(f"run_dir:    {run_dir_display}")
        print(f"resume_latest: {bool(eff.get('resume_latest'))}")
        print(f"autopilot:  {bool(eff.get('autopilot'))}")
        print(f"loop:       {bool(eff.get('loop'))} (sleep={eff.get('loop_sleep_seconds')}s, max_cycles={eff.get('loop_max_cycles')})")
        print(f"continuous: {bool(eff.get('continuous'))} (iterations={eff.get('iterations')}, max_turns_per_task={eff.get('max_turns_per_task')})")
        print(f"isolate_task: {bool(eff.get('isolate_task'))} / worktree_isolation: {bool(eff.get('worktree_isolation'))}")
        print(f"no_policy_scan: {bool(eff.get('no_policy_scan'))}")
        print(
            "scan_scope: "
            f"{eff.get('scan_scope')} (policy={eff.get('policy_scan_scope') or 'default'}, "
            f"security={eff.get('security_scan_scope') or 'default'})"
        )
        print(f"no_build:   {bool(eff.get('no_build'))} / run_tests: {bool(eff.get('run_tests'))}")
        print(f"dangerous_git_rollback: {bool(eff.get('dangerous_git_rollback'))}")
        print(f"failover_enabled: {bool(eff.get('failover_enabled'))}")
        print(f"failover_backends: {eff.get('failover_backends')}")
        print(f"failover_on: {eff.get('failover_on')}")
        print(f"failover_max_switches: {eff.get('failover_max_switches')}")
        print(f"pm_model:   {eff.get('pm_model')}")
        print(f"dev_model:  {eff.get('dev_model')}")
        print(f"qa_model:   {eff.get('qa_model')}")
        print(f"reporter_model: {eff.get('reporter_model')}")
        print(f"dev_auto_escalate: {bool(eff.get('dev_auto_escalate'))} (max={eff.get('dev_max_escalations')}, on={eff.get('dev_escalate_on')})")
        print(f"mcp_mode:   {eff.get('mcp_mode')} (package={eff.get('codex_package')})")
        print(f"docs_read_mode: {eff.get('docs_read_mode')} (docs_dir={eff.get('docs_dir')})")
        print(f"prompts_dir: {eff.get('prompts_dir')}")
        print(f"execution_backend: {eff.get('execution_backend')}")
        if str(eff.get('execution_backend') or '') == 'claudecode':
            print(f"claudecode_model: {eff.get('claudecode_model')}")
            print(f"claudecode_permission_mode: {eff.get('claudecode_permission_mode')}")
            print(f"claudecode_max_turns: {eff.get('claudecode_max_turns')}")
            print(f"claudecode_user: {eff.get('claudecode_user')}")
            print(f"claudecode_include_partial_messages: {bool(eff.get('claudecode_include_partial_messages'))}")
            print(f"claudecode_fork_session: {bool(eff.get('claudecode_fork_session'))}")
            print(f"claudecode_max_thinking_tokens: {eff.get('claudecode_max_thinking_tokens')}")
            print(f"claudecode_setting_sources: {eff.get('claudecode_setting_sources')}")
            print(f"claudecode_pm_allowed_tools: {eff.get('claudecode_pm_allowed_tools')}")
            print(f"claudecode_dev_allowed_tools: {eff.get('claudecode_dev_allowed_tools')}")
            print(f"claudecode_qa_allowed_tools: {eff.get('claudecode_qa_allowed_tools')}")
        print(f"roles:      {eff.get('roles')}")
        print(f"plugins_enabled: {bool(eff.get('plugins_enabled'))} (allowlist={eff.get('plugins_allowlist')}, strict={bool(eff.get('plugins_strict'))})")
        tg = eff.get("telegram") if isinstance(eff.get("telegram"), dict) else {}
        notify_events = tg.get("notify_events") if isinstance(tg.get("notify_events"), list) else []
        print(
            "telegram: "
            f"enabled={bool(tg.get('enabled', False))}, "
            f"runner_mode={tg.get('runner_mode', 'thread')}, "
            f"allowed_chat_ids={tg.get('allowed_chat_ids', [])}, "
            f"instance_name={tg.get('instance_name') or self.repo.name if self.repo else ''}, "
            f"notify_events={notify_events}, "
            f"cycle_summary={bool(tg.get('send_cycle_summary', True))}, "
            f"notify_interval={int(tg.get('notify_poll_interval_seconds') or 8)}s, "
            f"stalled_after={int(tg.get('stalled_seconds') or 600)}s"
        )
        print(f"debug:      {bool(eff.get('debug', False))}")
        print(f"auth:       login-based (codex login / claude auth login)")
        print("======================================\n")

        if show_all:
            raw_cfg = self.config if isinstance(self.config, dict) else {}
            unknown_keys = sorted([k for k in raw_cfg.keys() if k not in DEFAULTS])
            print("--- Raw config (loaded) ---")
            print(json.dumps(raw_cfg, ensure_ascii=False, indent=2))
            print("\n--- Overrides (session) ---")
            print(json.dumps(self.overrides or {}, ensure_ascii=False, indent=2))
            print("\n--- Effective config (merged) ---")
            print(json.dumps(eff, ensure_ascii=False, indent=2))
            if unknown_keys:
                print("\n--- Unknown keys preserved (forward-compat) ---")
                print(", ".join(unknown_keys))
            print()

    def _runner_is_alive(self) -> bool:
        if self._controller is not None:
            try:
                return bool(self._controller.status().get("running", False))
            except Exception:
                return False
        return bool(self._runner_thread and self._runner_thread.is_alive())

    def _run_dir_has_shell_stop_artifacts(self, run_dir: Path | None) -> bool:
        if run_dir is None:
            return False
        try:
            if not run_dir.exists() or not run_dir.is_dir():
                return False
            if any((run_dir / name).exists() for name in SHELL_STOP_ARTIFACT_NAMES):
                return True
            logs_dir = run_dir / "logs"
            return bool(logs_dir.exists() and logs_dir.is_dir() and any(item.is_file() for item in logs_dir.iterdir()))
        except OSError:
            return False

    def _shell_stop_target_run_dirs(self) -> list[Path]:
        targets: list[Path] = []
        if self.run_dir is not None:
            try:
                targets.append(self.run_dir.expanduser().resolve())
            except Exception:
                targets.append(self.run_dir)
        if self.repo and self.run_dir is not None:
            try:
                latest = find_latest_run_dir(self.repo)
            except Exception:
                latest = None
            if latest is not None:
                try:
                    latest_resolved = latest.expanduser().resolve()
                except Exception:
                    latest_resolved = latest
                seen = {str(path) for path in targets}
                if str(latest_resolved) not in seen:
                    base_name = self.run_dir.name
                    if latest_resolved.name.startswith(f"{base_name}-") or self._run_dir_has_shell_stop_artifacts(latest_resolved):
                        targets.append(latest_resolved)
        return targets

    def _shell_stop_file_paths(self, run_dirs: list[Path], stop_file: str) -> dict[str, str]:
        paths: dict[str, str] = {}
        for idx, run_dir in enumerate(run_dirs):
            suffix = "" if idx == 0 else f"_{idx + 1}"
            paths[f"stop_file_path{suffix}"] = (run_dir / stop_file).as_posix()
            paths[f"stop_progress_path{suffix}"] = (run_dir / "STOP_PROGRESS.json").as_posix()
            paths[f"stop_progress_log_path{suffix}"] = (run_dir / "stop_progress.log").as_posix()
        return paths

    def _sync_controller_args(self) -> None:
        if self._controller is None:
            return
        eff = self.effective()
        for key in DEFAULTS.keys():
            try:
                setattr(self._controller.base_args, key, eff.get(key))
            except Exception:
                pass
        if self.repo:
            self._controller.repo = self.repo

    def _ensure_run_dir(self) -> Path:
        if not self.repo:
            raise ValueError("repo is not set. Use /repo <path> first.")
        eff = self.effective()
        rd = str(eff.get("run_dir") or "").strip()
        if rd:
            self.run_dir = Path(rd).expanduser().resolve()
            return self.run_dir
        # Fresh runs are the default. Only an explicit resume_latest request or
        # explicit run_dir should reuse an existing run directory.
        latest = find_latest_run_dir(self.repo) if bool(eff.get("resume_latest")) else None
        self.run_dir = latest if latest is not None else make_run_dir(self.repo)
        return self.run_dir

    def todo(self, args: list[str]) -> None:
        """/todo UX

        - /todo --save  : create today's todo if missing, select it, and open
        - /todo --load <path|latest> : select an existing todo and open
        """
        if not self.repo:
            print("[ERR] repo is not set. Use /repo <path>.")
            return

        if not args:
            p, txt = read_current_todo(self.repo)
            if not p:
                print("[INFO] No todo selected. Use: /todo --save or /todo --load <path|latest>")
                return
            print(f"[TODO] {p}")
            if txt:
                preview = "\n".join(txt.splitlines()[:40])
                print(preview)
            return

        if args[0] == "--save":
            p = ensure_todo_file(self.repo)
            print(f"[OK] Todo saved/selected: {p}")
            if not open_path(p):
                print("[WARN] Failed to auto-open. Open it manually.")
            return

        if args[0] == "--load":
            if len(args) < 2:
                print("[ERR] Usage: /todo --load <path|latest>")
                return
            target = args[1].strip()
            if target.lower() == "latest":
                p, _txt = read_current_todo(self.repo)
                if not p:
                    print("[ERR] No todo files found. Use /todo --save first.")
                    return
                set_current_todo(self.repo, p)
            else:
                pp = Path(target).expanduser()
                p = (self.repo / pp).resolve() if not pp.is_absolute() else pp.resolve()
                if not p.exists() or not p.is_file():
                    print(f"[ERR] Todo not found: {p}")
                    return
                set_current_todo(self.repo, p)
            print(f"[OK] Todo selected: {p}")
            if not open_path(p):
                print("[WARN] Failed to auto-open. Open it manually.")
            return

        print("[ERR] Usage: /todo --save | /todo --load <path|latest>")

    def start(self, extra_tokens: list[str]) -> None:
        if not self._start_lock.acquire(blocking=False):
            print("[INFO] Runner start already in progress.")
            return
        try:
            self._start_locked(extra_tokens)
        finally:
            self._start_lock.release()

    def _start_locked(self, extra_tokens: list[str]) -> None:
        if self._controller is not None:
            if not self.repo:
                print("[ERR] repo is not set. Use /repo <path>.")
                return
            if not self.repo.exists():
                print(f"[ERR] repo not found: {self.repo}")
                return
            if self._runner_is_alive():
                print("[INFO] Runner is already running. Use /status or /stop.")
                return

            self.overrides.update(_parse_kv_tokens(extra_tokens))
            self._sync_controller_args()
            result = self._controller.start()
            run_dir = str(result.get("run_dir") or "").strip()
            if run_dir:
                try:
                    self.run_dir = Path(run_dir).expanduser().resolve()
                except Exception:
                    pass
            if result.get("ok", False):
                print("[OK] Runner started in background.")
                print(f" - run_dir: {result.get('run_dir') or '(unknown)'}")
                print(f" - stop:   /stop  (creates {self.effective().get('stop_file') or 'STOP'})")
                print(" - status: /status\n")
            else:
                print(f"[ERR] {result.get('message') or 'Failed to start runner.'}")
            return

        if self._runner_is_alive():
            print("[INFO] Runner is already running. Use /status or /stop.")
            return
        if not self.repo:
            print("[ERR] repo is not set. Use /repo <path>.")
            return
        if not self.repo.exists():
            print(f"[ERR] repo not found: {self.repo}")
            return

        # Apply inline overrides (for this session)
        self.overrides.update(_parse_kv_tokens(extra_tokens))

        run_dir = self._ensure_run_dir()
        run_dir.mkdir(parents=True, exist_ok=True)

        stop_file = str(self.effective().get("stop_file") or "STOP")
        stop_path = run_dir / stop_file
        try:
            if stop_path.exists():
                stop_path.unlink()
        except Exception:
            pass
        clear_stop_progress(run_dir)
        try:
            init_process_guard(stop_path_func=lambda p=stop_path: p)
        except Exception:
            pass

        self._record_runner_control_event(
            run_dir,
            action="start",
            status="request",
            message="Start requested.",
            error="",
            ok=False,
            running=False,
            phase="request",
        )

        eff = self.effective()
        # NOTE: DEFAULTS includes "repo" so passing repo twice will crash.
        args_dict = {k: eff.get(k) for k in DEFAULTS.keys()}
        args_dict["repo"] = str(self.repo)
        args_dict["run_dir"] = str(run_dir)

        # Ensure prompts_dir is always a python-side absolute path (avoid empty => repo root)
        try:
            args_dict["prompts_dir"] = str(resolve_prompts_dir(self.repo, str(args_dict.get("prompts_dir") or "")))
        except Exception:
            pass

        args = argparse.Namespace(**args_dict)

        def _target() -> None:
            self._runner_exit_code = None
            self._runner_started_at = time.time()
            try:
                rc = run_runner(args)
            except Exception as ex:
                import traceback
                print(f"[RUNNER ERROR] {ex}", flush=True)
                traceback.print_exc()
                rc = 1
            self._runner_exit_code = int(rc)

        self._runner_thread = threading.Thread(target=_target, name="agentcli-runner", daemon=True)
        self._runner_thread.start()

        self._record_runner_control_event(
            run_dir,
            action="start",
            status="started",
            message="Runner started.",
            error="",
            ok=True,
            running=True,
            result={"ok": True, "runner_mode": self.runner_mode, "run_dir": run_dir.as_posix()},
        )

        print("[OK] Runner started in background.")
        print(f" - run_dir: {run_dir}")
        print(f" - stop:   /stop  (creates {stop_file})")
        print(" - status: /status\n")

    def _print_stop_progress(self, progress: dict[str, Any]) -> None:
        phase = str(progress.get("phase") or "unknown").strip()
        elapsed = int(progress.get("elapsed_seconds") or 0)
        running = progress.get("running")
        message = str(progress.get("message") or "").strip()
        last_event = str(progress.get("last_event") or "").strip()
        running_part = f" runner={'alive' if running else 'idle'}" if running is not None else ""
        event_part = f" last_event={last_event}" if last_event else ""
        print(f"[STOP] phase={phase} elapsed={elapsed}s{running_part}{event_part} {message}".rstrip(), flush=True)

    def _controller_stop(self, wait: bool) -> dict[str, Any]:
        if self._controller is None:
            return {}
        if wait:
            try:
                return self._controller.stop(wait=wait, progress_callback=self._print_stop_progress)
            except TypeError:
                return self._controller.stop(wait=wait)
        return self._controller.stop(wait=wait)

    def _record_runner_control_event(
        self,
        run_dir: Path | None,
        *,
        action: str,
        status: str,
        message: str = "",
        error: str = "",
        ok: bool | None = None,
        running: bool | None = None,
        phase: str = "",
        stop_progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        if run_dir is None or self.repo is None:
            return {}
        payload_fields = dict(fields)
        if running is None:
            running = self._runner_is_alive()
        if running is not None:
            payload_fields["running"] = bool(running)
        payload_fields["runner_mode"] = self.runner_mode
        if phase:
            payload_fields["phase"] = phase
        if stop_progress is not None:
            payload_fields["stop_progress"] = stop_progress
        if result is not None:
            payload_fields["result"] = result
        return write_runner_control_event(
            run_dir,
            action=action,
            status=status,
            message=message,
            error=error,
            ok=ok,
            source="shell",
            repo=self.repo.as_posix(),
            config_path=self._config_path_name(),
            **payload_fields,
        )

    def stop(self, wait: bool = False) -> None:
        if self._controller is not None:
            result = self._controller_stop(wait=wait)
            ok = bool(result.get("ok", False))
            prefix = "[OK]" if ok else "[ERR]"
            print(f"{prefix} {result.get('message') or 'stop failed'}")
            run_dir = str(result.get("run_dir") or "").strip()
            if run_dir:
                try:
                    self.run_dir = Path(run_dir).expanduser().resolve()
                except Exception:
                    pass
            if wait:
                self.status()
            return

        if not self._runner_thread:
            print("[INFO] Runner is not started yet.")
            return
        if not self.run_dir:
            latest_run_dir = find_latest_run_dir(self.repo) if self.repo else None
            if latest_run_dir is not None:
                self.run_dir = latest_run_dir
        if not self.run_dir:
            print("[WARN] run_dir is unknown; cannot create stop file.")
            return

        stop_file = str(self.effective().get("stop_file") or "STOP")
        target_run_dirs = self._shell_stop_target_run_dirs()
        if not target_run_dirs:
            print("[WARN] run_dir is unknown; cannot create stop file.")
            return
        stop_path = target_run_dirs[0] / stop_file
        stop_file_paths = self._shell_stop_file_paths(target_run_dirs, stop_file)
        requested_at = time.monotonic()
        request_progress = write_stop_progress(
            self.run_dir,
            phase="requested",
            message="Stop requested.",
            requested_at_monotonic=requested_at,
            running=self._runner_is_alive(),
            runner_alive=self._runner_is_alive(),
            stop_file_paths=stop_file_paths,
        )
        self._print_stop_progress(request_progress)
        self._record_runner_control_event(
            self.run_dir,
            action="stop",
            status="request",
            message="Stop requested.",
            error="",
            ok=False,
            running=self._runner_is_alive(),
            phase="request",
            stop_progress=request_progress,
        )
        try:
            written_stop_paths: list[Path] = []
            for target_run_dir in target_run_dirs:
                target_stop_path = target_run_dir / stop_file
                target_stop_path.parent.mkdir(parents=True, exist_ok=True)
                target_stop_path.write_text(STOP_REASON_STOP_FILE + "\n", encoding="utf-8", errors="replace")
                written_stop_paths.append(target_stop_path)
            print(f"[OK] Stop requested via: {stop_path}")
            for alternate_stop_path in written_stop_paths[1:]:
                print(f"[INFO] Also wrote stop file: {alternate_stop_path}")
            stop_file_progress = write_stop_progress(
                self.run_dir,
                phase="stop_file_written",
                message=f"Stop file written: {stop_path}",
                requested_at_monotonic=requested_at,
                running=self._runner_is_alive(),
                runner_alive=self._runner_is_alive(),
                stop_file_paths=stop_file_paths,
            )
            self._print_stop_progress(stop_file_progress)
            self._record_runner_control_event(
                self.run_dir,
                action="stop",
                status="stop_file_written",
                message=f"Stop file written: {stop_path}",
                error="",
                ok=False,
                running=self._runner_is_alive(),
                phase="stop_file_written",
                stop_progress=stop_file_progress,
            )
        except Exception as ex:
            failed_progress = write_stop_progress(
                self.run_dir,
                phase="failed",
                message=f"Failed to create stop file: {ex}",
                requested_at_monotonic=requested_at,
                running=self._runner_is_alive(),
                runner_alive=self._runner_is_alive(),
                stop_file_paths=stop_file_paths,
            )
            self._print_stop_progress(failed_progress)
            self._record_runner_control_event(
                self.run_dir,
                action="stop",
                status="error",
                message="",
                error=f"Failed to create stop file: {ex}",
                ok=False,
                running=self._runner_is_alive(),
                phase="failed",
                stop_progress=failed_progress,
            )
            print(f"[ERR] Failed to create stop file: {ex}")
            return

        # Kill any tracked child processes immediately
        try:
            child_progress = write_stop_progress(
                self.run_dir,
                phase="terminating_children",
                message="Terminating tracked child processes.",
                requested_at_monotonic=requested_at,
                running=self._runner_is_alive(),
                runner_alive=self._runner_is_alive(),
                stop_file_paths=stop_file_paths,
            )
            self._print_stop_progress(child_progress)
            self._record_runner_control_event(
                self.run_dir,
                action="stop",
                status="child_termination",
                message="Terminating tracked child processes.",
                error="",
                ok=False,
                running=self._runner_is_alive(),
                phase="child_termination",
                stop_progress=child_progress,
            )
            terminate_all_children()
        except Exception:
            pass

        if wait and self._runner_thread:
            try:
                wait_timeout = int(self.effective().get("stop_wait_timeout_seconds") or 180)
            except Exception:
                wait_timeout = 180
            wait_timeout = max(1, wait_timeout)
            deadline = time.monotonic() + wait_timeout
            wait_progress = write_stop_progress(
                self.run_dir,
                phase="waiting_runner",
                message="Waiting for runner shutdown and final artifacts.",
                requested_at_monotonic=requested_at,
                running=True,
                runner_alive=True,
                stop_file_paths=stop_file_paths,
            )
            self._record_runner_control_event(
                self.run_dir,
                action="stop",
                status="runner_wait",
                message="Waiting for runner shutdown and final artifacts.",
                error="",
                ok=False,
                running=True,
                phase="runner_wait",
                stop_progress=wait_progress,
            )
            while self._runner_thread.is_alive() and time.monotonic() < deadline:
                self._print_stop_progress(
                    write_stop_progress(
                        self.run_dir,
                        phase="waiting_runner",
                        message="Waiting for runner shutdown and final artifacts.",
                        requested_at_monotonic=requested_at,
                        running=True,
                        runner_alive=True,
                        stop_file_paths=stop_file_paths,
                    )
                )
                try:
                    self._runner_thread.join(timeout=1.0)
                except KeyboardInterrupt:
                    print("[STOP] Stop is already in progress; keep waiting for cleanup.", flush=True)
            alive = self._runner_thread.is_alive()
            if not alive:
                close_all_loggers()
            final_wait_progress = write_stop_progress(
                self.run_dir,
                phase="timeout" if alive else "finalized",
                message=f"Runner is still alive after {wait_timeout}s stop wait timeout." if alive else "Runner stop sequence finished.",
                requested_at_monotonic=requested_at,
                running=alive,
                runner_alive=alive,
                exit_code=self._runner_exit_code,
                stop_file_paths=stop_file_paths,
            )
            self._print_stop_progress(final_wait_progress)

        alive = bool(self._runner_thread and self._runner_thread.is_alive())
        final_status = "timeout" if (wait and alive) else ("stopping" if alive else "stopped")
        final_message = (
            f"Runner is still alive after {wait_timeout}s stop wait timeout."
            if (wait and alive)
            else ("Stop requested; runner is still shutting down." if alive else "Runner stop sequence finished.")
        )
        final_stop_progress = read_stop_progress(self.run_dir)
        self._record_runner_control_event(
            self.run_dir,
            action="stop",
            status=final_status,
            message="" if final_status == "timeout" else final_message,
            error=final_message if final_status == "timeout" else "",
            ok=final_status != "timeout",
            running=alive,
            stop_progress=final_stop_progress,
            result={
                "ok": final_status != "timeout",
                "message": final_message if final_status == "timeout" else (
                    f"중지 요청됨: {stop_path.as_posix()}" if self.run_dir else "Runner stop requested."
                ),
                "running": alive,
                "run_dir": self.run_dir.as_posix(),
            },
        )
        if wait:
            self.status()

    def status(self) -> None:
        eff = self.effective()
        if self._controller is not None:
            data = self._controller.status()
            run_dir = str(data.get("run_dir") or "").strip()
            if run_dir:
                try:
                    self.run_dir = Path(run_dir).expanduser().resolve()
                except Exception:
                    pass
            print("\n=== Runner Status ===")
            print(f"running: {bool(data.get('running'))}")
            print(f"mode:    {data.get('runner_mode') or 'thread'}")
            print(f"run_dir: {data.get('run_dir') or '(not set)'}")
            print(f"goals_completion_level: {eff.get('goals_completion_level')}")
            print(f"uptime:  {int(data.get('uptime_seconds') or 0)}s")
            print(f"exit:    {data.get('exit_code') if data.get('exit_code') is not None else '(running/unknown)'}")
            print(
                "progress: "
                f"done={int(data.get('done') or 0)} "
                f"failed={int(data.get('failed') or 0)} "
                f"warnings={int(data.get('warnings') or 0)}"
            )
            print(f"reason:  {data.get('reason') or '(none)'}")
            last_event = str(data.get("last_event") or "").strip()
            if last_event:
                print(f"last_event: {last_event}")
            last_action = str(data.get("last_action") or data.get("lastAction") or "").strip()
            last_message = str(data.get("last_message") or data.get("lastMessage") or "").strip()
            last_error = str(data.get("last_error") or data.get("lastError") or "").strip()
            if last_action:
                print(f"last_action: {last_action}")
            if last_message:
                print(f"last_message: {last_message}")
            if last_error:
                print(f"last_error: {last_error}")
            stop_progress = data.get("stop_progress") if isinstance(data.get("stop_progress"), dict) else {}
            if stop_progress:
                print(
                    "stop:    "
                    f"phase={stop_progress.get('phase') or 'unknown'} "
                    f"elapsed={int(stop_progress.get('elapsed_seconds') or 0)}s "
                    f"message={stop_progress.get('message') or ''}"
                )
            print("=====================\n")
            self._print_pending_worktree_merge_hint()
            return

        run_dir = self.run_dir or (find_latest_run_dir(self.repo) if self.repo else None)
        if run_dir is not None:
            self.run_dir = run_dir
        alive = self._runner_is_alive()
        started = self._runner_started_at
        dur = "-" if not started else f"{int(time.time() - started)}s"

        print("\n=== Runner Status ===")
        print(f"running: {alive}")
        print(f"run_dir: {_shorten(run_dir)}")
        print(f"goals_completion_level: {eff.get('goals_completion_level')}")
        print(f"uptime:  {dur}")
        print(f"exit:    {self._runner_exit_code if (not alive) else '(running)'}")
        control_event = read_runner_control_event(run_dir)
        last_event = str(
            control_event.get("last_event")
            or control_event.get("lastEvent")
            or control_event.get("phase")
            or control_event.get("status")
            or ""
        ).strip()
        last_action = str(control_event.get("last_action") or control_event.get("lastAction") or "").strip()
        last_message = str(control_event.get("last_message") or control_event.get("lastMessage") or "").strip()
        last_error = str(control_event.get("last_error") or control_event.get("lastError") or "").strip()
        if last_event:
            print(f"last_event: {last_event}")
        if last_action:
            print(f"last_action: {last_action}")
        if last_message:
            print(f"last_message: {last_message}")
        if last_error:
            print(f"last_error: {last_error}")
        stop_progress = read_stop_progress(run_dir)
        if stop_progress:
            print(
                "stop:    "
                f"phase={stop_progress.get('phase') or 'unknown'} "
                f"elapsed={int(stop_progress.get('elapsed_seconds') or 0)}s "
                f"message={stop_progress.get('message') or ''}"
            )
        print("=====================\n")
        self._print_pending_worktree_merge_hint()

    def _pending_worktree_merge_path(self) -> Optional[Path]:
        if not self.repo:
            return None
        return find_pending_worktree_merge(self.repo, self.run_dir)

    def _print_pending_worktree_merge_hint(self) -> None:
        pending = self._pending_worktree_merge_path()
        if pending is None:
            return
        try:
            payload = read_pending_worktree_merge(pending)
            print("[ACTION REQUIRED] Worktree merge pending.")
            print(f" - run_dir:  {payload.get('run_dir') or pending.parent}")
            print(f" - worktree: {payload.get('worktree_dir') or '(unknown)'}")
            print(f" - patch:    {payload.get('patch_path') or '(unknown)'}")
            print(" - apply:   /merge-worktree")
            print(" - discard: /discard-worktree\n")
        except Exception as ex:
            print(f"[WARN] Pending worktree merge exists but could not be read: {pending} ({ex})")

    def merge_worktree(self) -> None:
        if self._runner_is_alive():
            print("[ERR] Runner is still running. Wait for shutdown before merging the worktree result.")
            return
        pending = self._pending_worktree_merge_path()
        if pending is None:
            print("[INFO] No pending worktree merge.")
            return
        try:
            payload = read_pending_worktree_merge(pending)
        except Exception as ex:
            print(f"[ERR] Failed to read pending worktree merge: {ex}")
            return

        print("\n=== Pending Worktree Merge ===")
        print(f"source:   {payload.get('source_repo')}")
        print(f"worktree: {payload.get('worktree_dir')}")
        print(f"patch:    {payload.get('patch_path')}")
        print(f"base:     {payload.get('base_ref')}")
        print(f"head:     {payload.get('head_ref')}")
        print("==============================")
        answer = input("Apply this patch to the source repo working tree and remove the worktree? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("[INFO] Merge skipped. Pending worktree result was left in place.")
            return
        try:
            result = apply_pending_worktree_merge(pending)
            print("[OK] Worktree patch applied to the source repo working tree.")
            if result.get("cleanup_error"):
                print(f"[WARN] Patch applied, but worktree cleanup failed: {result.get('cleanup_error')}")
            print("[INFO] Review `git status` and commit manually when ready.")
        except Exception as ex:
            print(f"[ERR] Failed to apply pending worktree merge: {ex}")

    def discard_worktree(self) -> None:
        if self._runner_is_alive():
            print("[ERR] Runner is still running. Wait for shutdown before discarding the worktree result.")
            return
        pending = self._pending_worktree_merge_path()
        if pending is None:
            print("[INFO] No pending worktree merge.")
            return
        try:
            payload = read_pending_worktree_merge(pending)
        except Exception as ex:
            print(f"[ERR] Failed to read pending worktree merge: {ex}")
            return
        print("\n=== Discard Pending Worktree ===")
        print(f"worktree: {payload.get('worktree_dir')}")
        print(f"patch:    {payload.get('patch_path')}")
        print("================================")
        answer = input("Discard this worktree result without applying it? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("[INFO] Discard skipped. Pending worktree result was left in place.")
            return
        try:
            result = discard_pending_worktree_merge(pending)
            if result.get("cleanup_error"):
                print(f"[WARN] Discard recorded, but worktree cleanup failed: {result.get('cleanup_error')}")
            else:
                print("[OK] Pending worktree result discarded.")
        except Exception as ex:
            print(f"[ERR] Failed to discard pending worktree merge: {ex}")

    def cmd_set(self, key: str, raw_value: str) -> None:
        key = key.replace("-", "_").strip()
        if key not in DEFAULTS:
            print(f"[ERR] Unknown key: {key}")
            return
        try:
            self.overrides[key] = _coerce_value(key, raw_value, DEFAULTS[key])
            print(f"[OK] {key} = {self.overrides[key]}")
        except Exception as ex:
            print(f"[ERR] {ex}")

    def cmd_add(self, key: str, raw_value: str) -> None:
        key = key.replace("-", "_").strip()
        if key not in DEFAULTS or not isinstance(DEFAULTS[key], list):
            print(f"[ERR] {key} is not a list option")
            return
        cur = self.overrides.get(key)
        if cur is None:
            cur = list(self.config.get(key) or DEFAULTS[key] or [])
        if not isinstance(cur, list):
            cur = [str(cur)]
        cur.append(raw_value)
        self.overrides[key] = cur
        print(f"[OK] {key} += {raw_value}")

    def save(self, path_str: str | None = None) -> None:
        if not self.repo and not path_str:
            print("[ERR] repo is not set; provide a path: /save <path>")
            return
        if self.repo:
            out_path = resolve_config_path(self.repo, path_str)
        else:
            p = Path(path_str).expanduser()  # type: ignore[arg-type]
            out_path = p.resolve() if p.is_absolute() else (app_home() / p).resolve()

        eff = self.effective()
        # Preserve unknown keys from the currently loaded config (forward compatibility).
        cfg_out: Dict[str, Any] = dict(self.config or {})
        for k in DEFAULTS.keys():
            cfg_out[k] = eff.get(k)
        # run_dir/resume_latest are session-only start intent, not persisted config.
        cfg_out.pop("run_dir", None)
        cfg_out.pop("resume_latest", None)

        try:
            save_config(out_path, cfg_out)
            self.config_path = out_path
            self.config = cfg_out
            print(f"[OK] Saved config: {out_path}")
        except Exception as ex:
            print(f"[ERR] Failed to save config: {ex}")

    def load(self, path_str: str | None = None) -> None:
        if not self.repo and not path_str:
            print("[ERR] repo is not set; provide a path: /load <path>")
            return
        if self.repo:
            p = resolve_config_path(self.repo, path_str)
        else:
            pp = Path(path_str).expanduser()  # type: ignore[arg-type]
            p = pp.resolve() if pp.is_absolute() else (app_home() / pp).resolve()
        if not p.exists():
            print(f"[ERR] Config not found: {p}")
            return
        try:
            cfg = load_config(p)
            self.config = cfg
            self.config_path = p
            print(f"[OK] Loaded config: {p}")
        except Exception as ex:
            print(f"[ERR] Failed to load config: {ex}")

    def help(self) -> None:
        lines = [
            "Commands (명령어):",
            "  /help                     도움말 표시",
            "  /doctor                   환경/설정 진단 보고서 생성",
            "  /worktree                 읽기 전용 워크트리 진단 목록 출력",
            "  /repo <path>               레포지토리 루트 설정",
            "  /config [--all]            현재 적용 설정 요약 출력 (--all: 전체 JSON 출력)",
            "  /set <key> <value>         설정 값을 덮어쓰기(타입은 기본값 기준)",
            "    예) /set execution_backend codex|claudecode",
            "    예) /set roles PM,Dev,QA   (단계 선택)",
            "  /add <key> <value>         리스트 설정에 항목 추가 (예: policy_rule)",
            "  /load [path]               config JSON 로드",
            "  /save [path]               현재 설정을 config JSON으로 저장 (알 수 없는 키도 보존)",
            "  /start [--flags...]        러너 백그라운드 시작 (예: /start --autopilot --loop)",
            "  /stop [--wait]             중지 요청(STOP 파일 생성). --wait로 종료 대기",
            "  /status                    러너 상태 확인",
            "  /merge-worktree            pending worktree result를 Y/N 확인 후 원본 repo에 적용",
            "  /discard-worktree          pending worktree result를 Y/N 확인 후 버림",
            "  /todo [--save|--load ...]   TODO 파일 생성/선택(.AgentCLI/todo)",
            "  /exit                      종료",
            "",
            "Tips:",
            "  - 시작 전 /config로 설정을 확인하세요.",
            "  - backend=claudecode 사용 시 Claude Code 로그인(claude auth login)과 claude-agent-sdk가 필요합니다.",
        ]
        print("  - /start creates a new run_dir by default.")
        print("  - Use /start --resume-latest or /start --run-dir <path> to reuse an existing run.")
        print("\n".join(lines))

    def worktree(self, args: list[str]) -> None:
        if not self.repo:
            print("[ERR] repo is not set; use /repo <path>")
            return

        diagnostics = scan_worktree_diagnostics(self.repo)
        summary = diagnostics.get("summary") if isinstance(diagnostics.get("summary"), dict) else {}
        issues = diagnostics.get("issues") if isinstance(diagnostics.get("issues"), list) else []
        pending_markers = diagnostics.get("pending_markers") if isinstance(diagnostics.get("pending_markers"), list) else []
        cleanup_failed = diagnostics.get("cleanup_failed") if isinstance(diagnostics.get("cleanup_failed"), list) else []
        generated_worktrees = diagnostics.get("generated_worktrees") if isinstance(diagnostics.get("generated_worktrees"), list) else []

        print("\n=== Worktree Diagnostics ===")
        print(f"status:   {diagnostics.get('status') or 'unknown'}")
        print(f"source:   {diagnostics.get('source_repo_root') or self.repo.as_posix()}")
        print(f"scanned:  {diagnostics.get('scanned_at') or '(unknown)'}")
        print(f"home:     {diagnostics.get('generated_worktree_home') or '(unknown)'}")
        print(
            "summary:  "
            f"runs={int(summary.get('run_dirs_scanned') or 0)} "
            f"markers={int(summary.get('pending_markers') or 0)} "
            f"stale={int(summary.get('stale_pending_markers') or 0)} "
            f"missing_patches={int(summary.get('missing_patches') or 0)} "
            f"cleanup_failed={int(summary.get('cleanup_failed') or 0)} "
            f"worktrees={int(summary.get('generated_worktrees') or 0)} "
            f"orphaned={int(summary.get('orphaned_worktrees') or 0)} "
            f"issues={int(summary.get('issue_count') or 0)}"
        )

        if pending_markers:
            print("pending markers:")
            for marker in pending_markers:
                status = str(marker.get("status") or "pending").strip()
                reason = str(marker.get("reason") or "").strip()
                path = str(marker.get("path") or "").strip()
                worktree_dir = str(marker.get("worktree_dir") or "").strip()
                patch_path = str(marker.get("patch_path") or "").strip()
                bits = [status]
                if reason:
                    bits.append(reason)
                if worktree_dir:
                    bits.append(worktree_dir)
                if patch_path:
                    bits.append(f"patch={patch_path}")
                print(f"  - {path}: {' | '.join(bits)}")

        if cleanup_failed:
            print("cleanup-failed:")
            for item in cleanup_failed:
                path = str(item.get("path") or "").strip()
                worktree_dir = str(item.get("worktree_dir") or "").strip()
                cleanup_path = str(item.get("cleanup_path") or "").strip()
                message = str(item.get("cleanup_message") or "").strip()
                bits = [item.get("status") or "cleanup_failed"]
                if worktree_dir:
                    bits.append(worktree_dir)
                if cleanup_path:
                    bits.append(f"cleanup={cleanup_path}")
                if message:
                    bits.append(message)
                print(f"  - {path}: {' | '.join(str(bit) for bit in bits)}")

        if generated_worktrees:
            print("generated worktrees:")
            for item in generated_worktrees:
                path = str(item.get("path") or "").strip()
                contract_path = str(item.get("contract_path") or "").strip()
                reason = str(item.get("reason") or "").strip()
                state = "orphaned" if item.get("orphaned") else "tracked"
                bits = [state]
                if contract_path:
                    bits.append(f"contract={contract_path}")
                if reason:
                    bits.append(reason)
                print(f"  - {path}: {' | '.join(bits)}")

        if issues:
            print("issues:")
            for issue in issues:
                kind = str(issue.get("kind") or "issue").strip()
                severity = str(issue.get("severity") or "warn").strip()
                message = str(issue.get("message") or "").strip()
                path = str(issue.get("path") or "").strip()
                details = issue.get("details") if isinstance(issue.get("details"), dict) else {}
                detail_bits = []
                if isinstance(details, dict):
                    for key in ("reason", "run_dir", "worktree_dir", "patch_path", "cleanup_path"):
                        value = str(details.get(key) or "").strip()
                        if value:
                            detail_bits.append(f"{key}={value}")
                suffix = f" ({'; '.join(detail_bits)})" if detail_bits else ""
                print(f"  - [{severity}] {kind}: {message} {path}{suffix}".rstrip())
        else:
            print("issues: none")

        print("============================\n")

    def doctor(self) -> None:
        if not self.repo:
            print("[ERR] repo is not set; use /repo <path>")
            return

        eff = self.effective()
        run_dir = self.run_dir or make_run_dir(self.repo)
        run_dir.mkdir(parents=True, exist_ok=True)
        report_lines: list[str] = ["# Doctor report", ""]

        # Git checks
        try:
            r = subprocess.run(["git", "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
            report_lines.append(f"- git version: {r.stdout.strip() or r.stderr.strip()}")
        except Exception as ex:
            report_lines.append(f"- git version: ERROR ({ex})")
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(self.repo),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            report_lines.append(f"- repo is git: {r.stdout.strip() == 'true'}")
        except Exception as ex:
            report_lines.append(f"- repo is git: ERROR ({ex})")

        # Config load check
        cfg_path = self.config_path or default_config_path(self.repo)
        try:
            _ = load_config(cfg_path) if cfg_path.exists() else {}
            report_lines.append(f"- config load: OK ({cfg_path})")
        except Exception as ex:
            report_lines.append(f"- config load: ERROR ({cfg_path}) ({ex})")

        # Run dir write check
        try:
            test_path = run_dir / "DOCTOR_WRITE_TEST.tmp"
            test_path.write_text("ok\n", encoding="utf-8", errors="replace")
            test_path.unlink(missing_ok=True)
            report_lines.append(f"- run_dir writable: OK ({run_dir})")
        except Exception as ex:
            report_lines.append(f"- run_dir writable: ERROR ({run_dir}) ({ex})")

        # Auth (login-based — no API keys needed)
        import shutil
        report_lines.append(f"- codex CLI: {'found' if shutil.which('codex') else 'NOT found'}")
        report_lines.append(f"- claude CLI: {'found' if shutil.which('claude') else 'NOT found'}")
        report_lines.append(f"- profile: {eff.get('profile', 'personal')}")
        report_lines.append(f"- policy enabled: {bool((eff.get('policy') or {}).get('enabled', False))}")
        report_lines.append(f"- security enabled: {bool((eff.get('security') or {}).get('enabled', False))}")

        # Backend preflight
        backends = eff.get("failover_backends") or [eff.get("execution_backend") or "codex"]
        report_lines.append("- backend preflight:")
        for result in run_preflight(argparse.Namespace(**eff), backends):
            status = "OK" if result.ok else "FAIL"
            detail = f" ({'; '.join(result.issues)})" if result.issues else ""
            report_lines.append(f"  - {result.backend}: {status}{detail}")

        def _first_cmd(cmd_val: Any, fallback: str) -> str:
            if isinstance(cmd_val, list) and cmd_val:
                return str(cmd_val[0])
            if isinstance(cmd_val, str) and cmd_val.strip():
                return cmd_val.strip().split()[0]
            return fallback

        build_exe = _first_cmd(eff.get("build_cmd"), "dotnet")
        test_exe = _first_cmd(eff.get("test_cmd"), "dotnet")

        report_lines.append(f"- build command executable: {build_exe} -> {shutil.which(build_exe) is not None}")
        report_lines.append(f"- test command executable: {test_exe} -> {shutil.which(test_exe) is not None}")

        # ── Prompts directory ──
        prompts_dir = resolve_prompts_dir(self.repo, eff.get("prompts_dir", ""))
        if prompts_dir.exists():
            override_files = [f.name for f in prompts_dir.iterdir() if f.suffix == ".md"]
            report_lines.append(f"- prompts_dir: OK ({prompts_dir}, {len(override_files)} overrides)")
        else:
            report_lines.append(f"- prompts_dir: not found ({prompts_dir})")

        # ── Skills system ──
        skills_cfg = eff.get("skills") or {}
        skills_enabled = skills_cfg.get("enabled", False)
        report_lines.append(f"- skills.enabled: {skills_enabled}")
        if skills_enabled:
            try:
                from .skills import resolve_skills_roots, build_skills_index
                roots_raw = skills_cfg.get("roots") or []
                roots = resolve_skills_roots(self.repo, roots_raw)
                existing = [r for r in roots if r.exists()]
                idx = build_skills_index(roots)
                report_lines.append(f"  - roots configured: {len(roots)}, existing: {len(existing)}")
                report_lines.append(f"  - skills discovered: {len(idx)}")
                if not idx:
                    report_lines.append("  - WARNING: enabled but no SKILL.md files found")
            except Exception as ex:
                report_lines.append(f"  - ERROR: {ex}")

        # ── Task history ──
        task_hist_enabled = eff.get("task_history_enabled", True)
        report_lines.append(f"- task_history_enabled: {task_hist_enabled}")
        if task_hist_enabled:
            try:
                from .task_history import query_history
                rows = query_history(self.repo, max_items=1)
                # query_history never raises; empty list = no records or DB issue
                report_lines.append(f"  - db query: OK (history accessible)")
            except Exception as ex:
                report_lines.append(f"  - db query: ERROR ({ex})")

        # ── Goals system ──
        goals_enabled = eff.get("goals_enabled", True)
        report_lines.append(f"- goals_enabled: {goals_enabled}")
        if goals_enabled:
            try:
                from .goals import goals_path, read_goals, parse_goals_completion
                gp = goals_path(self.repo)
                completion_level = resolve_goals_completion_level(eff.get("goals_completion_level"))
                report_lines.append(f"- goals_completion_level: {completion_level}")
                if gp.exists():
                    _, txt = read_goals(self.repo)
                    comp = parse_goals_completion(txt, completion_level=completion_level)
                    report_lines.append(f"  - project_complete: {bool(comp.get('project_complete'))}")
                    p0_info = f"P0: {comp.get('p0_done', 0)}/{comp.get('p0_total', 0)}"
                    p1_info = f"P1: {comp.get('p1_done', 0)}/{comp.get('p1_total', 0)}"
                    report_lines.append(f"  - GOALS.md: found ({p0_info}, {p1_info})")
                else:
                    report_lines.append(f"  - GOALS.md: not found (will auto-generate on first cycle)")
            except Exception as ex:
                report_lines.append(f"  - GOALS.md: ERROR ({ex})")

        # ── TODO system ──
        try:
            from .todo import todo_dir as _todo_dir_fn
            _td = _todo_dir_fn(self.repo)
            if _td.exists():
                _todo_path, todo_text = read_current_todo(self.repo)
                status = "has content" if todo_text and todo_text.strip() else "empty"
                report_lines.append(f"- todo: OK ({status})")
            else:
                report_lines.append(f"- todo: dir not found ({_td.relative_to(self.repo).as_posix()})")
        except Exception as ex:
            report_lines.append(f"- todo: ERROR ({ex})")

        # ── Docs digest ──
        docs_read_mode = eff.get("docs_read_mode", "digest")
        docs_dir_raw = eff.get("docs_dir", ".doc/Docs")
        report_lines.append(f"- docs_read_mode: {docs_read_mode}")
        try:
            from .docs import resolve_docs_dir
            docs_dir_resolved = resolve_docs_dir(self.repo, docs_dir_raw)
            if docs_dir_resolved:
                md_count = len(list(docs_dir_resolved.glob("**/*.md")))
                report_lines.append(f"  - docs_dir: OK ({docs_dir_resolved}, {md_count} .md files)")
            else:
                report_lines.append(f"  - docs_dir: not found")
            if docs_read_mode == "digest":
                digest_file = eff.get("docs_digest_file", ".doc/DOCS_DIGEST.md")
                dp = Path(digest_file)
                dp = dp if dp.is_absolute() else (self.repo / dp)
                if dp.exists():
                    size = dp.stat().st_size
                    report_lines.append(f"  - digest file: OK ({dp.name}, {size} bytes)")
                else:
                    report_lines.append(f"  - digest file: not found ({dp})")
        except Exception as ex:
            report_lines.append(f"  - docs: ERROR ({ex})")

        # ── Process guard ──
        import sys as _sys
        if _sys.platform == "win32":
            try:
                from .process_guard import _job_handle, _initialized
                if _initialized:
                    job_status = "Job Object active" if _job_handle else "initialized (no Job Object)"
                    report_lines.append(f"- process_guard: {job_status}")
                else:
                    report_lines.append(f"- process_guard: not yet initialized")
            except Exception as ex:
                report_lines.append(f"- process_guard: ERROR ({ex})")
        else:
            report_lines.append(f"- process_guard: N/A (non-Windows)")

        # ── Claude SDK (if backend is claudecode) ──
        backend = eff.get("execution_backend") or "codex"
        failover = eff.get("failover_backends") or []
        if backend == "claudecode" or "claudecode" in failover:
            try:
                import claude_agent_sdk  # noqa: F401
                ver = getattr(claude_agent_sdk, "__version__", "unknown")
                report_lines.append(f"- claude_agent_sdk: OK (v{ver})")
            except ImportError:
                report_lines.append(f"- claude_agent_sdk: NOT INSTALLED")

        report = "\n".join(report_lines) + "\n"
        (run_dir / "DOCTOR.md").write_text(report, encoding="utf-8", errors="replace")
        print(report)

def _build_completer() -> Any:
    """
    Build a prompt_toolkit completer.

    We keep this minimal and robust:
    - Commands completion
    - /set key completion
    - /start commonly used flags completion
    """
    if NestedCompleter is None or WordCompleter is None:
        return None

    set_keys = WordCompleter(sorted(DEFAULTS.keys()), ignore_case=True)
    start_flags = WordCompleter(
        sorted(
            {
                "--autopilot",
                "--run-dir",
                "--loop",
                "--continuous",
                "--debug",
                "--resume-latest",
                "--no-build",
                "--run-tests",
                "--no-policy-scan",
                "--iterations",
                "--max-turns-per-task",
                "--docs-read-mode",
                "--docs-dir",
                "--pm-model",
                "--dev-model",
                "--qa-model",
                "--execution-backend",
                "--roles",
                "--failover",
                "--failover-backends",
                "--failover-on",
                "--failover-max-switches",
                "--dangerous-git-rollback",
                "--plugins-enabled",
                "--plugins-allowlist",
                "--plugins-strict",
                "--worktree-isolation",
                "--claudecode-model",
                "--claudecode-permission-mode",
                "--claudecode-setting-sources",
            }
        ),
        ignore_case=True,
    )

    return NestedCompleter.from_nested_dict(
        {
            "/help": None,
            "/doctor": None,
            "/worktree": None,
            "/repo": None,
            "/config": None,
            "/set": set_keys,
            "/add": set_keys,
            "/load": None,
            "/save": None,
            "/start": start_flags,
            "/stop": WordCompleter(["--wait"], ignore_case=True),
            "/status": None,
            "/merge-worktree": None,
            "/discard-worktree": None,
            "/todo": WordCompleter(["--save", "--load", "latest"], ignore_case=True),
            "/exit": None,
            "/quit": None,
        }
    )


def shell_main(argv: list[str] | None = None, shell_instance: RunnerShell | None = None) -> int:
    # Initialize process guard early (L1 Job Object, L2 atexit, L4 orphan cleanup)
    try:
        init_process_guard()
    except Exception:
        pass

    sh = shell_instance or RunnerShell(initial_argv=argv or [])
    print("AgentCLI Shell (prompt_toolkit). Type /help.")
    if sh.repo:
        print(f"repo preset: {sh.repo}")

    # prompt_toolkit mode
    if PromptSession is not None:
        # Keep history under repo if possible, else in home.
        hist_path = None
        if sh.repo:
            from .config import AGENT_WORK_DIR, ensure_work_dir
            ensure_work_dir(sh.repo)
            hist_path = (sh.repo / AGENT_WORK_DIR / "agent_cli_history.txt").resolve()
            hist_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            hist_path = (Path.home() / ".agent_cli_history.txt").resolve()

        completer = _build_completer()
        session = PromptSession(
            history=FileHistory(str(hist_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
            complete_while_typing=True,
        )

        # patch_stdout prevents background prints from breaking the prompt line.
        ctx = patch_stdout() if patch_stdout is not None else None
        if ctx is None:
            # should not happen when PromptSession exists, but be safe.
            class _Noop:
                def __enter__(self): return self
                def __exit__(self, exc_type, exc, tb): return False
            ctx = _Noop()

        with ctx:
            while True:
                try:
                    line = session.prompt("> ").strip()
                except (EOFError, KeyboardInterrupt):
                    if sh._runner_is_alive():
                        print("\n[INTERRUPT] Runner is active; requesting /stop --wait instead of exiting.")
                        sh.stop(wait=True)
                        continue
                    print("\n[EXIT]")
                    return 0

                if not line:
                    continue
                if not line.startswith("/"):
                    print("[INFO] Shell mode. Use commands like /start, /config, /stop. (/help)")
                    continue
                if _dispatch(sh, line):
                    return 0

    # Fallback basic input()
    while True:  # pragma: no cover
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            if sh._runner_is_alive():
                print("\n[INTERRUPT] Runner is active; requesting /stop --wait instead of exiting.")
                sh.stop(wait=True)
                continue
            print("\n[EXIT]")
            return 0
        if not line:
            continue
        if not line.startswith("/"):
            print("[INFO] Shell mode. Use commands like /start, /config, /stop. (/help)")
            continue
        if _dispatch(sh, line):
            return 0


def _dispatch(sh: RunnerShell, line: str) -> bool:
    try:
        parts = shlex.split(line)
    except ValueError as e:
        print(f"[ERR] Invalid input: {e}")
        return False
    if not parts:
        return False
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("/exit", "/quit"):
        return True
    if cmd == "/help":
        sh.help()
        return False
    if cmd == "/doctor":
        sh.doctor()
        return False
    if cmd in {"/worktree", "/worktree-list", "/worktree-doctor"}:
        sh.worktree(args)
        return False
    if cmd == "/repo":
        if not args:
            print("[ERR] Usage: /repo <path>")
            return False
        sh.set_repo(args[0])
        print(f"[OK] repo = {sh.repo}")
        return False
    if cmd == "/config":
        show_all = bool(args and args[0] == "--all")
        sh.print_config(show_all=show_all)
        return False
    if cmd == "/set":
        if len(args) < 2:
            print("[ERR] Usage: /set <key> <value>")
            return False
        sh.cmd_set(args[0], " ".join(args[1:]))
        return False
    if cmd == "/add":
        if len(args) < 2:
            print("[ERR] Usage: /add <key> <value>")
            return False
        sh.cmd_add(args[0], " ".join(args[1:]))
        return False
    if cmd == "/load":
        sh.load(args[0] if args else None)
        return False
    if cmd == "/save":
        sh.save(args[0] if args else None)
        return False
    if cmd == "/start":
        sh.start(args)
        return False
    if cmd == "/stop":
        wait = bool(args and args[0] == "--wait")
        sh.stop(wait=wait)
        return False
    if cmd == "/status":
        sh.status()
        return False
    if cmd == "/merge-worktree":
        sh.merge_worktree()
        return False
    if cmd == "/discard-worktree":
        sh.discard_worktree()
        return False

    if cmd == "/todo":
        sh.todo(args)
        return False

    print("[ERR] Unknown command. Type /help.")
    return False
