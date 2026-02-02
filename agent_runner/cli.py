from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .config import load_config, save_config, resolve_config_path, default_config_path, default_prompts_dir
from .wizard import run_wizard
from .prompts import ensure_default_prompt_files


DEFAULTS: Dict[str, Any] = {
    # core
    "run_dir": "",
    "env_file": "",
    # docs
    "docs_dir": ".doc/Docs",
    "docs_read_mode": "digest",
    "docs_digest_file": ".doc/Docs/00_DOCS_DIGEST.md",
    "generate_digest": False,
    # autopilot
    "autopilot": False,
    # loop
    "loop": False,
    "loop_sleep_seconds": 60,
    "loop_max_cycles": 0,
    "loop_idle_exit_after": 3600,
    "stop_file": "STOP",
    # PM turns
    "pm_bootstrap_max_turns": 120,
    "pm_incremental_max_turns": 30,
    "pm_backlog_max_turns": 30,
    "pm_structured_retries": 2,
    "pm_max_turns_continuations": 1,
    "pm_timeout_seconds": 0,
    "dev_max_turns_continuations": 2,
    "dev_timeout_seconds": 0,

    # PM drift guards
    "pm_include_working_tree": False,
    "pm_refresh_backlog": False,
    "pm_refresh_every_cycles": 0,
    # Dev
    "continuous": False,
    "iterations": 30,
    "max_turns_per_task": 12,
    "allow_no_diff": False,
    "stop_if_no_diff": False,  # deprecated
    # Gates
    "no_build": False,
    "require_build": False,  # deprecated
    "dotnet_build_target": "",
    "run_tests": False,
    "dotnet_test_target": "",
    "dotnet_test_filter": "",
    "test_timeout_seconds": 3600,
    "isolate_task": False,
    # Policy
    "no_policy_scan": False,
    "policy_rules_file": "",
    "policy_rule": [],
    # Models
    "pm_model": "gpt-5-mini",
    "dev_model": "gpt-5.2-codex",
    "qa_model": "gpt-5-mini",
    # MCP
    "mcp_mode": "npx",
    "codex_package": "@openai/codex@latest",
    "mcp_timeout_seconds": 360000,
    # QA
    "qa_always": False,
    # prompts
    "prompts_dir": ".doc/agent_prompts",
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="CLI-first PM→Dev→QA runner (token-optimized).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Config / wizard
    p.add_argument("--config", default="", help="Config file path (default: repo/.doc/agent_config.json)")
    p.add_argument("--wizard", action="store_true", help="Run interactive wizard to create/update config")
    p.add_argument("--non-interactive", action="store_true", help="Disable interactive prompts")
    p.add_argument("--init-prompts", action="store_true", help="Create prompt templates in prompts_dir and exit")
    p.add_argument("--prompts-dir", default=DEFAULTS["prompts_dir"], help="Prompt templates directory (relative to repo)")

    # Repo / run dir
    p.add_argument("--repo", required=True, help="Repo root path (e.g., C:\\Dev\\BudgetBook)")
    p.add_argument("--run-dir", default=DEFAULTS["run_dir"], help="Resume an existing run folder under .doc/agent_runs/...")
    p.add_argument("--env-file", default=DEFAULTS["env_file"], help="Optional: explicit .env file path (loaded before repo chdir)")

    # Docs
    p.add_argument("--docs-dir", default=DEFAULTS["docs_dir"], help="Docs dir relative to repo")
    p.add_argument("--docs-read-mode", choices=["digest", "full", "none"], default=DEFAULTS["docs_read_mode"],
                   help="digest=read headings digest only (recommended), full=allow opening docs, none=skip docs")
    p.add_argument("--docs-digest-file", default=DEFAULTS["docs_digest_file"], help="Digest file path rel to repo")
    p.add_argument("--generate-digest", action="store_true", default=DEFAULTS["generate_digest"],
                   help="Generate/update docs digest locally (no tokens)")

    p.add_argument("--autopilot", action="store_true", default=DEFAULTS["autopilot"],
                   help="approval-policy=never, sandbox=workspace-write")

    # Loop
    p.add_argument("--loop", action="store_true", default=DEFAULTS["loop"],
                   help="Run PM→Dev→QA cycles repeatedly (unattended). Reuses the SAME run_dir and accumulates logs/state.")
    p.add_argument("--loop-sleep-seconds", type=int, default=DEFAULTS["loop_sleep_seconds"],
                   help="Sleep between cycles in --loop mode")
    p.add_argument("--loop-max-cycles", type=int, default=DEFAULTS["loop_max_cycles"],
                   help="Max cycles in --loop mode. 0 = unlimited")
    p.add_argument("--loop-idle-exit-after", type=int, default=DEFAULTS["loop_idle_exit_after"],
                   help="Exit if no progress for this many seconds in --loop mode")
    p.add_argument("--stop-file", default=DEFAULTS["stop_file"],
                   help="Stop-file name under run_dir to gracefully stop")

    # PM budgets
    p.add_argument("--pm-bootstrap-max-turns", type=int, default=DEFAULTS["pm_bootstrap_max_turns"])
    p.add_argument("--pm-incremental-max-turns", type=int, default=DEFAULTS["pm_incremental_max_turns"])
    p.add_argument("--pm-backlog-max-turns", type=int, default=DEFAULTS["pm_backlog_max_turns"])

    # Structured output / resilience
    p.add_argument("--pm-structured-retries", type=int, default=DEFAULTS["pm_structured_retries"],
                   help="Retries to repair/validate PM JSON output schema")
    p.add_argument("--pm-max-turns-continuations", type=int, default=DEFAULTS["pm_max_turns_continuations"],
                   help="If PM hits max-turns, retry with a continuation prompt (best-effort)")
    p.add_argument("--pm-timeout-seconds", type=int, default=DEFAULTS["pm_timeout_seconds"],
                   help="Hard timeout for a PM run (0 disables)")
    p.add_argument("--dev-max-turns-continuations", type=int, default=DEFAULTS["dev_max_turns_continuations"],
                   help="If Dev hits max-turns, retry with a continuation prompt (best-effort)")
    p.add_argument("--dev-timeout-seconds", type=int, default=DEFAULTS["dev_timeout_seconds"],
                   help="Hard timeout for a Dev task run (0 disables)")

    # PM drift guards
    p.add_argument("--pm-include-working-tree", action="store_true", default=DEFAULTS["pm_include_working_tree"])
    p.add_argument("--pm-refresh-backlog", action="store_true", default=DEFAULTS["pm_refresh_backlog"])
    p.add_argument("--pm-refresh-every-cycles", type=int, default=DEFAULTS["pm_refresh_every_cycles"])

    # Dev
    p.add_argument("--continuous", action="store_true", default=DEFAULTS["continuous"])
    p.add_argument("--iterations", type=int, default=DEFAULTS["iterations"])
    p.add_argument("--max-turns-per-task", type=int, default=DEFAULTS["max_turns_per_task"])

    p.add_argument("--allow-no-diff", action="store_true", default=DEFAULTS["allow_no_diff"])
    p.add_argument("--stop-if-no-diff", action="store_true", default=DEFAULTS["stop_if_no_diff"],
                   help="[deprecated] no-diff is failure by default; keep for compatibility")

    # Build/Test gates
    p.add_argument("--no-build", action="store_true", default=DEFAULTS["no_build"],
                   help="Skip dotnet build (default is to build after each task)")
    p.add_argument("--require-build", action="store_true", default=DEFAULTS["require_build"],
                   help="[deprecated] build is ON by default unless --no-build")
    p.add_argument("--dotnet-build-target", default=DEFAULTS["dotnet_build_target"])

    p.add_argument("--run-tests", action="store_true", default=DEFAULTS["run_tests"])
    p.add_argument("--dotnet-test-target", default=DEFAULTS["dotnet_test_target"])
    p.add_argument("--dotnet-test-filter", default=DEFAULTS["dotnet_test_filter"])
    p.add_argument("--test-timeout-seconds", type=int, default=DEFAULTS["test_timeout_seconds"])

    # Isolation
    p.add_argument("--isolate-task", action="store_true", default=DEFAULTS["isolate_task"],
                   help="Checkpoint repo before each task and rollback on failure")

    # Policy scan
    p.add_argument("--no-policy-scan", action="store_true", default=DEFAULTS["no_policy_scan"])
    p.add_argument("--policy-rules-file", default=DEFAULTS["policy_rules_file"])
    p.add_argument("--policy-rule", action="append", default=list(DEFAULTS["policy_rule"]))

    # Models
    p.add_argument("--pm-model", default=DEFAULTS["pm_model"])
    p.add_argument("--dev-model", default=DEFAULTS["dev_model"])
    p.add_argument("--qa-model", default=DEFAULTS["qa_model"])

    # MCP server
    p.add_argument("--mcp-mode", choices=["npx", "codex"], default=DEFAULTS["mcp_mode"])
    p.add_argument("--codex-package", default=DEFAULTS["codex_package"])
    p.add_argument("--mcp-timeout-seconds", type=int, default=DEFAULTS["mcp_timeout_seconds"])

    # QA control
    p.add_argument("--qa-always", action="store_true", default=DEFAULTS["qa_always"])

    return p


def _interactive_choose() -> str:
    print("")
    print("No config file found.")
    print("1) Run setup wizard (recommended)")
    print("2) Continue with built-in defaults (no config)")
    print("3) Quit")
    while True:
        ans = input("Select [1/2/3]: ").strip()
        if ans in ("1", "2", "3"):
            return ans
        print("Please enter 1, 2, or 3.")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    argv = list(argv) if argv is not None else sys.argv[1:]

    # Build parser with internal defaults first
    parser = _build_parser()
    pre, _unknown = parser.parse_known_args(argv)

    repo = Path(pre.repo).expanduser().resolve()
    cfg_path = resolve_config_path(repo, pre.config)

    cfg: Dict[str, Any] = {}
    if pre.wizard:
        # Always run wizard when explicitly requested
        cfg = run_wizard(repo)
        save_config(cfg_path, cfg)
        print(f"[OK] Wrote config: {cfg_path}")
    else:
        if cfg_path.exists():
            try:
                cfg = load_config(cfg_path)
            except Exception as ex:
                print(f"[WARN] Failed to load config ({cfg_path}): {ex}")
                cfg = {}
        else:
            # Config missing - interactive wizard offer
            if (not pre.non_interactive) and sys.stdin.isatty():
                choice = _interactive_choose()
                if choice == "1":
                    cfg = run_wizard(repo)
                    save_config(cfg_path, cfg)
                    print(f"[OK] Wrote config: {cfg_path}")
                elif choice == "3":
                    raise SystemExit(1)
            else:
                # Non-interactive: proceed with defaults for compatibility
                cfg = {}

    # Apply config on top of internal defaults (argparse CLI overrides after parsing)
    if cfg:
        parser.set_defaults(**cfg)

    args = parser.parse_args(argv)

    # Init prompts and exit
    prompts_dir = (repo / args.prompts_dir).resolve() if not Path(args.prompts_dir).is_absolute() else Path(args.prompts_dir).resolve()
    if args.init_prompts:
        ensure_default_prompt_files(prompts_dir)
        print(f"[OK] Prompt templates ensured at: {prompts_dir}")
        raise SystemExit(0)

    return args
