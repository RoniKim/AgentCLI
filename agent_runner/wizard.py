from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .config import resolve_prompts_dir
from .prompts import ensure_default_prompt_files


def _ask_str(prompt: str, default: str) -> str:
    s = input(f"{prompt} [{default}]: ").strip()
    return s if s else default


def _ask_int(prompt: str, default: int) -> int:
    s = input(f"{prompt} [{default}]: ").strip()
    if not s:
        return default
    try:
        return int(s)
    except ValueError:
        print("  (invalid int, using default)")
        return default


def _ask_bool(prompt: str, default: bool) -> bool:
    d = "Y/n" if default else "y/N"
    s = input(f"{prompt} ({d}): ").strip().lower()
    if not s:
        return default
    return s in ("y", "yes", "1", "true", "t")


def run_wizard(repo: Path, defaults: Dict[str, Any]) -> Dict[str, Any]:
    """
    python-side 저장 정책을 따르는 wizard:
    - prompts_dir: empty면 python-side 기본(AgentCLI/prompts/<repo-slug>/)
    - config 저장은 cli.py에서 python-side로 처리
    """
    print("")
    print("=== AgentCLI Wizard (python-side config/prompts) ===")
    print(f"repo: {repo}")
    print("")

    autopilot = _ask_bool("Enable autopilot", bool(defaults.get("autopilot", False)))
    continuous = _ask_bool("Enable continuous (execute tasks after backlog)", bool(defaults.get("continuous", False)))
    iterations = _ask_int("Iterations per run", int(defaults.get("iterations", 30)))
    max_turns_per_task = _ask_int("Max turns per task", int(defaults.get("max_turns_per_task", 12)))

    loop = _ask_bool("Enable loop (unattended cycles)", bool(defaults.get("loop", False)))
    loop_sleep_seconds = _ask_int("Loop sleep seconds", int(defaults.get("loop_sleep_seconds", 60)))
    loop_max_cycles = _ask_int("Loop max cycles (0 = unlimited)", int(defaults.get("loop_max_cycles", 0)))
    loop_idle_exit_after = _ask_int("Loop idle exit after seconds (0 = disabled)", int(defaults.get("loop_idle_exit_after", 0)))

    no_build = _ask_bool("Disable dotnet build gate (no_build)", bool(defaults.get("no_build", False)))
    run_tests = _ask_bool("Enable tests gate (run_tests)", bool(defaults.get("run_tests", False)))

    # prompts_dir is python-side; allow empty
    prompts_dir = _ask_str(
        "Prompts directory (absolute or relative to AgentCLI home; empty=default)",
        str(defaults.get("prompts_dir", "")) or "",
    ).strip()

    cfg: Dict[str, Any] = {
        "autopilot": autopilot,
        "continuous": continuous,
        "iterations": iterations,
        "max_turns_per_task": max_turns_per_task,
        "loop": loop,
        "loop_sleep_seconds": loop_sleep_seconds,
        "loop_max_cycles": loop_max_cycles,
        "loop_idle_exit_after": loop_idle_exit_after,
        "no_build": no_build,
        "run_tests": run_tests,
        "prompts_dir": prompts_dir,
    }

    # Ensure prompts exist (never overwrites)
    pd = resolve_prompts_dir(repo, prompts_dir)
    ensure_default_prompt_files(pd)

    print("")
    print("Wizard completed.")
    print(f"- prompts_dir resolved to: {pd}")
    print("")
    return cfg
