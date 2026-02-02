from __future__ import annotations

import argparse
import os
import shlex
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .cli import DEFAULTS
from .config import load_config, save_config, resolve_config_path, default_config_path, legacy_config_path, resolve_prompts_dir
from .docs import load_dotenv_best_effort
from .cycle import run as run_cycle
from .run_dir import make_run_dir

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
    return raw


def _yesno(v: bool) -> str:
    return "yes" if v else "no"


def _shorten(path: Optional[Path]) -> str:
    return str(path) if path else "(not set)"


def _parse_kv_tokens(tokens: list[str]) -> Dict[str, Any]:
    """
    Parse tokens like:
      --autopilot
      --iterations 30
    into overrides, based on DEFAULTS.
    """
    out: Dict[str, Any] = {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if not t.startswith("--"):
            i += 1
            continue
        key = t[2:].replace("-", "_").strip()
        if key not in DEFAULTS:
            # Unknown option: ignore (shell should be forgiving)
            i += 1
            continue
        default = DEFAULTS[key]
        if isinstance(default, bool):
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
    def __init__(self, initial_argv: list[str] | None = None) -> None:
        self.repo: Optional[Path] = None
        self.config_path: Optional[Path] = None
        self.config: Dict[str, Any] = {}
        self.overrides: Dict[str, Any] = {}
        self.run_dir: Optional[Path] = None

        self._runner_thread: Optional[threading.Thread] = None
        self._runner_exit_code: Optional[int] = None
        self._runner_started_at: Optional[float] = None

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

        if self.repo:
            self._ensure_config_loaded()

    def _ensure_config_loaded(self) -> None:
        if not self.repo:
            return
        self.config_path = self.config_path or default_config_path(self.repo)
        # Prefer AgentCLI-side config. If missing, fall back to legacy repo/.doc config for compatibility.
        load_path = self.config_path if self.config_path.exists() else legacy_config_path(self.repo)
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

    def effective(self) -> Dict[str, Any]:
        eff: Dict[str, Any] = dict(DEFAULTS)
        # Only keep known keys from config
        if self.config:
            for k in DEFAULTS.keys():
                if k in self.config:
                    eff[k] = self.config[k]
        if self.overrides:
            eff.update(self.overrides)
        if self.repo:
            eff["repo"] = str(self.repo)
        return eff

    def print_config(self) -> None:
        eff = self.effective()
        repo = self.repo
        cfgp = self.config_path or (default_config_path(repo) if repo else None)

        # Ensure .env is loaded so env sanity reflects reality even in shell mode.
        try:
            _ = load_dotenv_best_effort(repo or Path.cwd(), explicit_env_file=str(eff.get("env_file") or ""), override=True)
        except Exception:
            pass

        print("\n=== AgentCLI Shell: Current Settings ===")
        print(f"repo:       {_shorten(repo)}")
        print(f"config:     {_shorten(cfgp)}")
        print(f"run_dir:    {eff.get('run_dir') or '(auto)'}")
        print(f"env_file:   {eff.get('env_file') or '(auto)'}")
        print(f"autopilot:  {bool(eff.get('autopilot'))}")
        print(f"loop:       {bool(eff.get('loop'))} (sleep={eff.get('loop_sleep_seconds')}s, max_cycles={eff.get('loop_max_cycles')})")
        print(f"continuous: {bool(eff.get('continuous'))} (iterations={eff.get('iterations')}, max_turns_per_task={eff.get('max_turns_per_task')})")
        print(f"no_policy_scan: {bool(eff.get('no_policy_scan'))}")
        print(f"no_build:   {bool(eff.get('no_build'))} / run_tests: {bool(eff.get('run_tests'))}")
        print(f"pm_model:   {eff.get('pm_model')}")
        print(f"dev_model:  {eff.get('dev_model')}")
        print(f"qa_model:   {eff.get('qa_model')}")
        print(f"mcp_mode:   {eff.get('mcp_mode')} (package={eff.get('codex_package')})")
        print(f"docs_read_mode: {eff.get('docs_read_mode')} (docs_dir={eff.get('docs_dir')})")
        print(f"debug:      {bool(eff.get('debug', False))}")
        print(f"OPENAI_API_KEY set: {_yesno(bool(os.getenv('OPENAI_API_KEY', '').strip()))}")
        print("======================================\n")

    def _runner_is_alive(self) -> bool:
        return bool(self._runner_thread and self._runner_thread.is_alive())

    def _ensure_run_dir(self) -> Path:
        if not self.repo:
            raise ValueError("repo is not set. Use /repo <path> first.")
        eff = self.effective()
        rd = str(eff.get("run_dir") or "").strip()
        if rd:
            self.run_dir = Path(rd).expanduser().resolve()
            return self.run_dir
        self.run_dir = make_run_dir(self.repo)
        self.overrides["run_dir"] = str(self.run_dir)
        return self.run_dir

    def start(self, extra_tokens: list[str]) -> None:
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

        eff = self.effective()
        # NOTE: DEFAULTS includes "repo" so passing repo twice will crash.
        args_dict = {k: eff.get(k) for k in DEFAULTS.keys()}
        args_dict["repo"] = str(self.repo)

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
                rc = run_cycle(args)
            except Exception:
                rc = 1
            self._runner_exit_code = int(rc)

        self._runner_thread = threading.Thread(target=_target, name="agentcli-runner", daemon=True)
        self._runner_thread.start()

        print("[OK] Runner started in background.")
        print(f" - run_dir: {run_dir}")
        print(f" - stop:   /stop  (creates {stop_file})")
        print(" - status: /status\n")

    def stop(self, wait: bool = False) -> None:
        if not self._runner_thread:
            print("[INFO] Runner is not started yet.")
            return
        if not self.run_dir:
            print("[WARN] run_dir is unknown; cannot create stop file.")
            return

        stop_file = str(self.effective().get("stop_file") or "STOP")
        stop_path = self.run_dir / stop_file
        try:
            stop_path.write_text("stop requested\n", encoding="utf-8", errors="replace")
            print(f"[OK] Stop requested via: {stop_path}")
        except Exception as ex:
            print(f"[ERR] Failed to create stop file: {ex}")
            return

        if wait and self._runner_thread:
            print("[INFO] Waiting for runner to exit...")
            self._runner_thread.join(timeout=60)
            self.status()

    def status(self) -> None:
        alive = self._runner_is_alive()
        started = self._runner_started_at
        dur = "-" if not started else f"{int(time.time() - started)}s"

        print("\n=== Runner Status ===")
        print(f"running: {alive}")
        print(f"run_dir: {_shorten(self.run_dir)}")
        print(f"uptime:  {dur}")
        print(f"exit:    {self._runner_exit_code if (not alive) else '(running)'}")
        print("=====================\n")

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
        out_path = Path(path_str).expanduser() if path_str else default_config_path(self.repo)  # type: ignore[arg-type]
        if not out_path.is_absolute() and self.repo:
            out_path = (self.repo / out_path).resolve()

        eff = self.effective()
        cfg_out = {k: eff.get(k) for k in DEFAULTS.keys()}

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
        p = Path(path_str).expanduser() if path_str else default_config_path(self.repo)  # type: ignore[arg-type]
        if not p.is_absolute() and self.repo:
            p = (self.repo / p).resolve()
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
        print(
            "\n".join(
                [
                    "Commands:",
                    "  /help                     Show this help",
                    "  /repo <path>               Set repo root",
                    "  /config                    Show effective settings + env sanity",
                    "  /set <key> <value>         Override a setting (types inferred from defaults)",
                    "  /add <key> <value>         Append to a list setting (e.g., policy_rule)",
                    "  /load [path]               Load config JSON (default: AgentCLI-side configs/<repo-hash>.json)",
                    "  /save [path]               Save effective config JSON",
                    "  /start [--flags...]        Start runner in background (ex: /start --autopilot --loop)",
                    "  /stop [--wait]             Request graceful stop (creates run_dir/STOP)",
                    "  /status                    Show runner status",
                    "  /exit                      Quit",
                    "",
                    "Tips:",
                    "  - You can pass --repo/--config to prefill: python agent_cli.py --repo ...",
                    "  - Use /config before /start to confirm settings.",
                ]
            )
        )


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
                "--loop",
                "--continuous",
                "--debug",
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
            }
        ),
        ignore_case=True,
    )

    return NestedCompleter.from_nested_dict(
        {
            "/help": None,
            "/repo": None,
            "/config": None,
            "/set": set_keys,
            "/add": set_keys,
            "/load": None,
            "/save": None,
            "/start": start_flags,
            "/stop": WordCompleter(["--wait"], ignore_case=True),
            "/status": None,
            "/exit": None,
            "/quit": None,
        }
    )


def shell_main(argv: list[str] | None = None) -> int:
    sh = RunnerShell(initial_argv=argv or [])
    print("AgentCLI Shell (prompt_toolkit). Type /help.")
    if sh.repo:
        print(f"repo preset: {sh.repo}")

    # prompt_toolkit mode
    if PromptSession is not None:
        # Keep history under repo if possible, else in home.
        hist_path = None
        if sh.repo:
            hist_path = (sh.repo / ".doc" / "agent_cli_history.txt").resolve()
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
    parts = shlex.split(line)
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in ("/exit", "/quit"):
        return True
    if cmd == "/help":
        sh.help()
        return False
    if cmd == "/repo":
        if not args:
            print("[ERR] Usage: /repo <path>")
            return False
        sh.set_repo(args[0])
        print(f"[OK] repo = {sh.repo}")
        return False
    if cmd == "/config":
        sh.print_config()
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

    print("[ERR] Unknown command. Type /help.")
    return False
