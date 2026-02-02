from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from .config import default_prompts_dir
from .prompts import ensure_default_prompt_files


def _yn(prompt: str, default: bool) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        ans = input(f"{prompt} ({d}) ").strip().lower()
        if not ans:
            return default
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("Please enter y or n.")


def _ask_str(prompt: str, default: str) -> str:
    ans = input(f"{prompt} [{default}] ").strip()
    return ans or default


def _ask_int(prompt: str, default: int, min_v: int | None = None) -> int:
    while True:
        ans = input(f"{prompt} [{default}] ").strip()
        if not ans:
            return default
        try:
            v = int(ans)
            if min_v is not None and v < min_v:
                print(f"Must be >= {min_v}")
                continue
            return v
        except ValueError:
            print("Please enter an integer.")


def run_wizard(repo: Path) -> Dict[str, Any]:
    print("")
    print("== Setup Wizard (CLI-first) ==")
    print("This will create a config file (no secrets) and optional prompt templates you can edit.")
    print("")

    # Core paths
    run_dir = _ask_str("Fixed run_dir to reuse across sessions (relative to repo recommended)",
                       ".doc/agent_runs/night_run")
    prompts_dir = _ask_str("Prompts directory (relative to repo recommended)",
                           ".doc/agent_prompts")

    # Unattended loop
    loop = _yn("Enable --loop unattended cycles", default=True)
    loop_sleep_seconds = _ask_int("Loop sleep seconds", 60, min_v=0)
    loop_max_cycles = _ask_int("Loop max cycles (0=unlimited)", 0, min_v=0)
    loop_idle_exit_after = _ask_int("Idle exit after seconds (0=disabled)", 3600, min_v=0)
    stop_file = _ask_str("Stop file name inside run_dir", "STOP")

    # Token-saving / safety gates
    autopilot = _yn("Autopilot (approval-policy=never)", default=loop)
    isolate_task = _yn("Isolate each task with local rollback (recommended)", default=True)
    run_tests = _yn("Run dotnet test after build", default=False)
    no_build = _yn("Skip dotnet build (NOT recommended)", default=False)
    no_policy_scan = _yn("Disable policy scan (NOT recommended)", default=False)

    # PM drift guards (token tradeoff)
    pm_include_working_tree = _yn("PM include working-tree changes (may increase tokens)", default=False)
    pm_refresh_backlog = _yn("PM allow refreshing backlog even if no repo diffs (useful in --loop)", default=loop)
    pm_refresh_every_cycles = 0
    if pm_refresh_backlog:
        pm_refresh_every_cycles = _ask_int("PM refresh backlog every N cycles (0=only when idle)", 0, min_v=0)

    # Docs
    docs_read_mode = _ask_str("Docs read mode (digest/full/none)", "digest")
    docs_dir = _ask_str("Docs directory", ".doc/Docs")
    docs_digest_file = _ask_str("Docs digest file", ".doc/Docs/00_DOCS_DIGEST.md")
    generate_digest = _yn("Generate/update docs digest automatically when missing", default=(docs_read_mode == "digest"))

    # Iterations / budgets
    continuous = _yn("Continuous mode (PM then Dev loop)", default=True)
    iterations = _ask_int("Max Dev iterations per cycle", 30, min_v=1)
    max_turns_per_task = _ask_int("Max turns per task", 8, min_v=1)

    cfg: Dict[str, Any] = {
        # paths
        "run_dir": run_dir,
        "prompts_dir": prompts_dir,
        # docs
        "docs_dir": docs_dir,
        "docs_read_mode": docs_read_mode,
        "docs_digest_file": docs_digest_file,
        "generate_digest": bool(generate_digest),
        # operation
        "autopilot": bool(autopilot),
        "loop": bool(loop),
        "loop_sleep_seconds": int(loop_sleep_seconds),
        "loop_max_cycles": int(loop_max_cycles),
        "loop_idle_exit_after": int(loop_idle_exit_after),
        "stop_file": stop_file,
        "continuous": bool(continuous),
        "iterations": int(iterations),
        "max_turns_per_task": int(max_turns_per_task),
        # gates
        "no_build": bool(no_build),
        "run_tests": bool(run_tests),
        "isolate_task": bool(isolate_task),
        "no_policy_scan": bool(no_policy_scan),
        # pm drift guards
        "pm_include_working_tree": bool(pm_include_working_tree),
        "pm_refresh_backlog": bool(pm_refresh_backlog),
        "pm_refresh_every_cycles": int(pm_refresh_every_cycles),
        # models (keep defaults)
        "pm_model": "gpt-5-mini",
        "dev_model": "gpt-5.2-codex",
        "qa_model": "gpt-5-mini",
        # mcp
        "mcp_mode": "npx",
        "codex_package": "@openai/codex@latest",
        "mcp_timeout_seconds": 360000,
        # qa
        "qa_always": False,
        # additional safe defaults
        "allow_no_diff": False,
    }

    # Ensure prompt templates exist (never overwrites)
    pd = (repo / prompts_dir).resolve() if not Path(prompts_dir).is_absolute() else Path(prompts_dir).resolve()
    ensure_default_prompt_files(pd)

    print("")
    print("Wizard completed.")
    print(f"- Config will point to prompts dir: {prompts_dir}")
    print("")
    return cfg
