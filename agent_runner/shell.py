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
from .run_dir import find_latest_run_dir
from .todo import ensure_todo_file, read_current_todo, set_current_todo, open_path

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


def _src_label(src: str) -> str:
    """Short Korean label for a value source."""
    if src == "override":
        return "세션"
    if src == "config":
        return "설정"
    if src == "shell":
        return "쉘"
    return "기본"


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

        # config persistence state
        self._dirty: bool = False

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

        # Loading a config should reset dirty flag (we are now in sync with disk).
        self._dirty = False

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
        """Return the effective settings (defaults <- config <- overrides).

        NOTE:
          - Config may contain keys beyond DEFAULTS (future-proof). We keep them
            in the effective dict for printing, but only DEFAULTS keys are
            passed into the runner.
          - Overrides are session-only (e.g., /start --flags or /override).
        """
        eff: Dict[str, Any] = dict(DEFAULTS)
        if self.config:
            # Apply all config keys (including unknown) for visibility.
            eff.update(self.config)
        if self.overrides:
            eff.update(self.overrides)
        if self.repo:
            eff["repo"] = str(self.repo)
        return eff

    def effective_with_sources(self) -> Dict[str, tuple[Any, str]]:
        """Return mapping of key -> (value, source).

        source is one of: default | config | override | shell
        """
        out: Dict[str, tuple[Any, str]] = {}
        for k, dv in DEFAULTS.items():
            out[k] = (dv, "default")
        for k, v in (self.config or {}).items():
            # unknown keys are treated as config
            out[k] = (v, "config")
        for k, v in (self.overrides or {}).items():
            out[k] = (v, "override")
        if self.repo:
            out["repo"] = (str(self.repo), "shell")
        return out

    def print_config(self) -> None:
        eff = self.effective()
        repo = self.repo
        cfgp = self.config_path or (default_config_path(repo) if repo else None)

        # Resolve some commonly confusing relative paths for display.
        prompts_dir_resolved = None
        try:
            if repo:
                prompts_dir_resolved = resolve_prompts_dir(repo, str(eff.get("prompts_dir") or ""))
        except Exception:
            prompts_dir_resolved = None

        def _resolve_repo_rel(p: Any) -> str:
            s = str(p or "").strip()
            if not s:
                return ""
            try:
                pp = Path(s).expanduser()
                if pp.is_absolute() or not repo:
                    return str(pp)
                return str((repo / pp).resolve())
            except Exception:
                return s

        docs_dir_resolved = _resolve_repo_rel(eff.get("docs_dir"))
        docs_digest_resolved = _resolve_repo_rel(eff.get("docs_digest_file"))
        policy_rules_file_resolved = _resolve_repo_rel(eff.get("policy_rules_file"))

        # Ensure .env is loaded so env sanity reflects reality even in shell mode.
        try:
            _ = load_dotenv_best_effort(repo or Path.cwd(), explicit_env_file=str(eff.get("env_file") or ""), override=True)
        except Exception:
            pass

        print("\n=== AgentCLI Shell: 현재 설정(/config) ===")
        print(f"repo:        {_shorten(repo)}")
        print(f"config 파일:  {_shorten(cfgp)}")
        print(f"run_dir:     {eff.get('run_dir') or '(자동)'}")
        print(f"env_file:    {eff.get('env_file') or '(자동)'}")
        print(f"prompts_dir: {str(prompts_dir_resolved) if prompts_dir_resolved else (eff.get('prompts_dir') or '(자동)')}")
        print(
            f"docs:        mode={eff.get('docs_read_mode')} dir={docs_dir_resolved or eff.get('docs_dir')} digest={docs_digest_resolved or eff.get('docs_digest_file')}"
        )
        if policy_rules_file_resolved or eff.get("policy_rules_file"):
            print(f"policy_rules_file: {policy_rules_file_resolved or eff.get('policy_rules_file')}")
        print(f"autopilot:   {bool(eff.get('autopilot'))}")
        print(f"loop:        {bool(eff.get('loop'))} (sleep={eff.get('loop_sleep_seconds')}s, max_cycles={eff.get('loop_max_cycles')}, idle_exit={eff.get('loop_idle_exit_after')}s)")
        print(f"continuous:  {bool(eff.get('continuous'))} (iterations={eff.get('iterations')}, max_turns_per_task={eff.get('max_turns_per_task')})")
        print(f"gates:       no_build={bool(eff.get('no_build'))}, require_build={bool(eff.get('require_build'))}, run_tests={bool(eff.get('run_tests'))}")
        print(f"models:      PM={eff.get('pm_model')} / Dev={eff.get('dev_model')} / QA={eff.get('qa_model')} / Reporter={eff.get('reporter_model')}")
        print(f"dev_escalate: enabled={bool(eff.get('dev_auto_escalate'))} max={eff.get('dev_max_escalations')} on={eff.get('dev_escalate_on')}")
        print(f"tool:        backend={eff.get('tool_backend')} name={eff.get('tool_name')} cmd={eff.get('tool_command') or '(preset)'} args={eff.get('tool_args')}")
        # Show the actually resolved tool invocation (preset -> command/args) for transparency.
        try:
            from types import SimpleNamespace
            from .pipeline.tooling import build_tool_spec

            ns = SimpleNamespace(**{k: eff.get(k) for k in DEFAULTS.keys()})
            spec = build_tool_spec(ns)
            if spec is None:
                print("tool_resolved: (disabled)")
            else:
                argv = " ".join([spec.command, *spec.args])
                print(f"tool_resolved: {argv}")
        except Exception:
            pass
        print(f"mcp:         mode={eff.get('mcp_mode')} package={eff.get('codex_package')} timeout={eff.get('mcp_timeout_seconds')}s")
        print(f"debug:       {bool(eff.get('debug', False))}")
        print(f"OPENAI_API_KEY 설정됨: {_yesno(bool(os.getenv('OPENAI_API_KEY', '').strip()))}")
        if self._dirty:
            print("[주의] 설정 변경이 저장되지 않았습니다. 저장: /save")
        print("========================================\n")

        # 상세 설정 (전체)
        src = self.effective_with_sources()
        print("--- 상세 설정(전체 키) ---")
        # Stable order: known keys first, then unknown (config extras)
        known = list(DEFAULTS.keys())
        extras = sorted([k for k in src.keys() if k not in DEFAULTS])

        def _fmt_value(v: Any) -> str:
            if isinstance(v, list):
                return "[" + ", ".join(str(x) for x in v) + "]"
            return str(v)

        for k in known + extras:
            v, s = src[k]
            print(f"- {k}: {_fmt_value(v)}  ({_src_label(s)})")
        print("")

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
        # For unattended overnight ops, prefer resuming the latest run_dir
        # (prevents backlog/state duplication when the shell restarts).
        prefer_resume = bool(eff.get("loop") or eff.get("continuous") or eff.get("autopilot"))
        latest = find_latest_run_dir(self.repo) if prefer_resume else None
        self.run_dir = latest if latest is not None else make_run_dir(self.repo)
        self.overrides["run_dir"] = str(self.run_dir)
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
            self.config[key] = _coerce_value(key, raw_value, DEFAULTS[key])
            self._dirty = True
            print(f"[OK] (config) {key} = {self.config[key]}  -> 저장 필요: /save")
        except Exception as ex:
            print(f"[ERR] {ex}")

    def cmd_add(self, key: str, raw_value: str) -> None:
        key = key.replace("-", "_").strip()
        if key not in DEFAULTS or not isinstance(DEFAULTS[key], list):
            print(f"[ERR] {key} is not a list option")
            return
        cur = self.config.get(key)
        if cur is None:
            cur = list(DEFAULTS.get(key) or [])
        if not isinstance(cur, list):
            cur = [str(cur)]
        cur.append(raw_value)
        self.config[key] = cur
        self._dirty = True
        print(f"[OK] (config) {key} += {raw_value}  -> 저장 필요: /save")

    def cmd_override(self, key: str, raw_value: str) -> None:
        """Session-only override (does not touch config)."""
        key = key.replace("-", "_").strip()
        if key not in DEFAULTS:
            print(f"[ERR] Unknown key: {key}")
            return
        try:
            self.overrides[key] = _coerce_value(key, raw_value, DEFAULTS[key])
            print(f"[OK] (세션) {key} = {self.overrides[key]}")
        except Exception as ex:
            print(f"[ERR] {ex}")

    def save(self, path_str: str | None = None) -> None:
        if not self.repo and not path_str:
            print("[ERR] repo is not set; provide a path: /save <path>")
            return
        if path_str:
            # Align with cli.py: relative paths resolve from AgentCLI home.
            out_path = resolve_config_path(self.repo, path_str) if self.repo else Path(path_str).expanduser().resolve()
        else:
            out_path = default_config_path(self.repo)  # type: ignore[arg-type]

        # Save config-centric values (NOT session overrides).
        # Keep unknown keys for forward compatibility.
        cfg_out: Dict[str, Any] = dict(self.config or {})
        for k, dv in DEFAULTS.items():
            cfg_out[k] = cfg_out.get(k, dv)

        try:
            save_config(out_path, cfg_out)
            self.config_path = out_path
            self.config = cfg_out
            self._dirty = False
            print(f"[OK] Saved config: {out_path}")
        except Exception as ex:
            print(f"[ERR] Failed to save config: {ex}")

    def load(self, path_str: str | None = None) -> None:
        if not self.repo and not path_str:
            print("[ERR] repo is not set; provide a path: /load <path>")
            return
        if path_str:
            p = resolve_config_path(self.repo, path_str) if self.repo else Path(path_str).expanduser().resolve()
        else:
            p = default_config_path(self.repo)  # type: ignore[arg-type]
        if not p.exists():
            print(f"[ERR] Config not found: {p}")
            return
        try:
            cfg = load_config(p)
            self.config = cfg
            self.config_path = p
            self._dirty = False
            print(f"[OK] Loaded config: {p}")
        except Exception as ex:
            print(f"[ERR] Failed to load config: {ex}")

    def help(self, cmd: str | None = None) -> None:
        """Print help in Korean.

        - /help           : 전체 명령 요약
        - /help /start    : 특정 명령 상세
        """
        help_map: Dict[str, list[str]] = {
            "/help": [
                "사용법: /help [명령어]",
                "설명: 명령어 도움말을 출력합니다.",
                "예시: /help /start",
            ],
            "/repo": [
                "사용법: /repo <경로>",
                "설명: 작업할 레포지토리 루트 경로를 설정합니다.",
                "예시: /repo C:/Dev/BudgetBook",
            ],
            "/config": [
                "사용법: /config",
                "설명: 현재 적용 중인 설정(기본값/Config/세션 오버라이드 포함)을 모두 출력합니다.",
                "팁: 설정을 바꾼 후 저장하지 않았으면 '저장 필요' 경고가 표시됩니다.",
            ],
            "/set": [
                "사용법: /set <키> <값>",
                "설명: 설정을 'config'에 반영합니다(영구 반영). 저장하려면 /save를 실행하세요.",
                "예시: /set iterations 20",
                "예시: /set run_tests true",
            ],
            "/override": [
                "사용법: /override <키> <값>",
                "설명: 현재 세션에만 임시로 적용합니다(저장되지 않음).",
                "예시: /override debug true",
            ],
            "/add": [
                "사용법: /add <키> <값>",
                "설명: 리스트 타입 설정에 값을 추가합니다(config에 반영). 저장하려면 /save를 실행하세요.",
                "예시: /add policy_rule \"deny: secrets\"",
            ],
            "/load": [
                "사용법: /load [경로]",
                "설명: 설정 JSON을 로드합니다. 경로 생략 시 기본 config 경로를 사용합니다.",
            ],
            "/save": [
                "사용법: /save [경로]",
                "설명: 현재 config를 JSON으로 저장합니다. (세션 오버라이드는 저장하지 않습니다)",
            ],
            "/start": [
                "사용법: /start [--플래그 ...]",
                "설명: 백그라운드에서 러너를 시작합니다. 추가 플래그는 세션 오버라이드로만 적용됩니다.",
                "예시: /start --autopilot --loop",
                "예시: /start --iterations 10 --max-turns-per-task 8",
            ],
            "/stop": [
                "사용법: /stop [--wait]",
                "설명: run_dir에 STOP 파일을 생성하여 중지를 요청합니다.",
                "옵션: --wait 를 주면 최대 60초 기다린 후 /status를 출력합니다.",
            ],
            "/status": [
                "사용법: /status",
                "설명: 러너 실행 상태(실행중/종료코드/uptime)를 출력합니다.",
            ],
            "/todo": [
                "사용법: /todo [--save | --load <path|latest>]",
                "설명: 레포 로컬 TODO 파일(.doc/todo)을 생성/선택/미리보기/열기 합니다.",
                "예시: /todo --save",
                "예시: /todo --load latest",
            ],
            "/exit": [
                "사용법: /exit",
                "설명: 쉘을 종료합니다. 저장되지 않은 설정이 있으면 경고합니다.",
            ],
        }

        if cmd:
            key = cmd.strip()
            if not key.startswith("/"):
                key = "/" + key
            lines = help_map.get(key)
            if not lines:
                print(f"[ERR] 알 수 없는 명령: {cmd}")
                print("사용 가능한 명령: " + ", ".join(sorted(help_map.keys())))
                return
            print("\n".join([f"[{key}]", *lines, ""]))
            return

        # Summary view
        print("\n명령어 도움말 (/help [명령어])")
        print("- /help [명령어]            도움말(명령어별 상세 지원)")
        print("- /repo <경로>              레포 루트 설정")
        print("- /config                  현재 적용 설정 전체 출력(기본/설정/세션)")
        print("- /set <키> <값>            설정을 config에 반영(영구). 저장: /save")
        print("- /override <키> <값>       현재 세션에만 임시 적용(저장 안 됨)")
        print("- /add <키> <값>            리스트 설정에 항목 추가(config). 저장: /save")
        print("- /load [경로]              config 로드")
        print("- /save [경로]              config 저장")
        print("- /start [--flags...]       러너 시작(백그라운드)")
        print("- /stop [--wait]            중지 요청(STOP 파일 생성)")
        print("- /status                  상태 확인")
        print("- /todo ...                TODO 파일 관리")
        print("- /exit                    종료")
        print("\n예시: /help /start\n")


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
            "/override": set_keys,
            "/add": set_keys,
            "/load": None,
            "/save": None,
            "/start": start_flags,
            "/stop": WordCompleter(["--wait"], ignore_case=True),
            "/status": None,
            "/todo": WordCompleter(["--save", "--load", "latest"], ignore_case=True),
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
        if sh._dirty:
            print("[WARN] 저장되지 않은 설정이 있습니다. 저장: /save")
        return True
    if cmd == "/help":
        sh.help(args[0] if args else None)
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
    if cmd == "/override":
        if len(args) < 2:
            print("[ERR] Usage: /override <key> <value>")
            return False
        sh.cmd_override(args[0], " ".join(args[1:]))
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

    if cmd == "/todo":
        sh.todo(args)
        return False

    print("[ERR] Unknown command. Type /help.")
    return False
