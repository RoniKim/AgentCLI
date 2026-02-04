from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .config import resolve_prompts_dir
from .prompts import ensure_default_prompt_files


def _ask_str(prompt: str, default: str) -> str:
    s = input(f"{prompt} [{default}]: ").strip()
    return s if s else default


def _ask_choice(prompt: str, default: str, choices: list[str]) -> str:
    cs = "/".join(choices)
    while True:
        s = input(f"{prompt} ({cs}) [{default}]: ").strip().lower()
        if not s:
            return default
        if s in choices:
            return s
        print(f"  (invalid choice: {s}; expected one of {cs})")


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

    execution_backend = _ask_choice(
        "Execution backend (runner)",
        str(defaults.get("execution_backend", "codex")).lower(),
        ["codex", "claudecode"],
    )

    # Claude Code backend settings (only asked when selected)
    claudecode_cfg: Dict[str, Any] = {}
    if execution_backend == "claudecode":
        print("\n--- Claude Code backend options ---")
        claudecode_cfg = {
            "claudecode_model": _ask_str("Claude model", str(defaults.get("claudecode_model", "sonnet"))),
            "claudecode_permission_mode": _ask_choice(
                "Permission mode",
                str(defaults.get("claudecode_permission_mode", "acceptEdits")),
                ["default", "acceptEdits", "bypassPermissions", "plan"],
            ),
            "claudecode_max_turns": _ask_int("Max turns per query", int(defaults.get("claudecode_max_turns", 32))),
            "claudecode_setting_sources": _ask_str(
                "Setting sources (comma-separated: user,project,local)",
                str(defaults.get("claudecode_setting_sources", "project")),
            ),
            "claudecode_system_prompt_append": _ask_str(
                "System prompt append (optional)",
                str(defaults.get("claudecode_system_prompt_append", "")),
            ),
            "claudecode_user": _ask_str("User identifier (optional)", str(defaults.get("claudecode_user", ""))),
            "claudecode_include_partial_messages": _ask_bool(
                "Include partial/streaming messages",
                bool(defaults.get("claudecode_include_partial_messages", False)),
            ),
            "claudecode_fork_session": _ask_bool("Fork session when resuming", bool(defaults.get("claudecode_fork_session", False))),
            "claudecode_max_thinking_tokens": _ask_int(
                "Max thinking tokens (0 = SDK default)",
                int(defaults.get("claudecode_max_thinking_tokens", 0)),
            ),
            "claudecode_continue_conversation": _ask_bool(
                "Continue conversation (keep session)",
                bool(defaults.get("claudecode_continue_conversation", False)),
            ),
            "claudecode_enable_file_checkpointing": _ask_bool(
                "Enable file checkpointing",
                bool(defaults.get("claudecode_enable_file_checkpointing", False)),
            ),
        }

    autopilot = _ask_bool("Enable autopilot", bool(defaults.get("autopilot", False)))
    continuous = _ask_bool("Enable continuous (execute tasks after backlog)", bool(defaults.get("continuous", False)))
    iterations = _ask_int("Iterations per run", int(defaults.get("iterations", 30)))
    max_turns_per_task = _ask_int("Max turns per task", int(defaults.get("max_turns_per_task", 12)))

    # When an agent hits MaxTurnsExceeded, we can safely re-run with a short CONTINUE prompt.
    # This prevents long overnight runs from aborting due to turn caps.
    dev_max_turns_continuations = _ask_int(
        "Dev continuations on max-turns (0=disable)", int(defaults.get("dev_max_turns_continuations", 2))
    )
    pm_max_turns_continuations = _ask_int(
        "PM continuations on max-turns (0=disable)", int(defaults.get("pm_max_turns_continuations", 1))
    )

    loop = _ask_bool("Enable loop (unattended cycles)", bool(defaults.get("loop", False)))
    loop_sleep_seconds = _ask_int("Loop sleep seconds", int(defaults.get("loop_sleep_seconds", 60)))
    loop_max_cycles = _ask_int("Loop max cycles (0 = unlimited)", int(defaults.get("loop_max_cycles", 0)))
    loop_idle_exit_after = _ask_int("Loop idle exit after seconds (0 = disabled)", int(defaults.get("loop_idle_exit_after", 0)))

    no_build = _ask_bool("Disable build gate (no_build)", bool(defaults.get("no_build", False)))
    run_tests = _ask_bool("Enable tests gate (run_tests)", bool(defaults.get("run_tests", False)))

    # Optional: generic build/test commands (preferred). If empty, legacy dotnet auto-detect may run.
    use_custom_gates = _ask_bool("Use custom build/test commands (build_cmd/test_cmd)", False)
    build_cmd: list[str] = []
    test_cmd: list[str] = []
    if use_custom_gates:
        build_raw = _ask_str("build_cmd (comma-separated argv; empty=none)", "").strip()
        test_raw = _ask_str("test_cmd  (comma-separated argv; empty=none)", "").strip()
        build_cmd = [p.strip() for p in build_raw.split(",") if p.strip()] if build_raw else []
        test_cmd = [p.strip() for p in test_raw.split(",") if p.strip()] if test_raw else []

    build_timeout_seconds = _ask_int("Build timeout seconds", int(defaults.get("build_timeout_seconds", 1800)))
    test_timeout_seconds = _ask_int("Test timeout seconds", int(defaults.get("test_timeout_seconds", 3600)))

    # Models (cost saver defaults)
    pm_model = _ask_str("PM model", str(defaults.get("pm_model", "gpt-5-mini")))
    dev_model = _ask_str("Dev model (base)", str(defaults.get("dev_model", "gpt-5.1-codex-mini")))
    dev_model_tier1 = _ask_str("Dev model tier1 (escalation)", str(defaults.get("dev_model_tier1", "gpt-5.1-codex")))
    dev_model_tier2 = _ask_str("Dev model tier2 (escalation)", str(defaults.get("dev_model_tier2", "gpt-5.2-codex")))
    dev_auto_escalate = _ask_bool("Dev auto-escalate on failures", bool(defaults.get("dev_auto_escalate", True)))
    dev_max_escalations = _ask_int("Dev max escalations per task", int(defaults.get("dev_max_escalations", 2)))
    reporter_model = _ask_str("Reporter model (shutdown report)", str(defaults.get("reporter_model", "gpt-5-nano")))
    report_max_turns = _ask_int("Reporter max turns", int(defaults.get("report_max_turns", 8)))
    qa_model = _ask_str("QA model", str(defaults.get("qa_model", "gpt-5-mini")))

    # prompts_dir is python-side; allow empty
    prompts_dir = _ask_str(
        "Prompts directory (absolute or relative to AgentCLI home; empty=default)",
        str(defaults.get("prompts_dir", "")) or "",
    ).strip()

    cfg: Dict[str, Any] = {
        "config_version": 2,
        "execution_backend": execution_backend,
        "autopilot": autopilot,
        "continuous": continuous,
        "iterations": iterations,
        "max_turns_per_task": max_turns_per_task,
        "dev_max_turns_continuations": dev_max_turns_continuations,
        "pm_max_turns_continuations": pm_max_turns_continuations,
        "loop": loop,
        "loop_sleep_seconds": loop_sleep_seconds,
        "loop_max_cycles": loop_max_cycles,
        "loop_idle_exit_after": loop_idle_exit_after,
        "no_build": no_build,
        "run_tests": run_tests,
        "build_cmd": build_cmd,
        "test_cmd": test_cmd,
        "build_timeout_seconds": build_timeout_seconds,
        "test_timeout_seconds": test_timeout_seconds,
        "pm_model": pm_model,
        "dev_model": dev_model,
        "dev_model_tier1": dev_model_tier1,
        "dev_model_tier2": dev_model_tier2,
        "dev_auto_escalate": dev_auto_escalate,
        "dev_max_escalations": dev_max_escalations,
        "dev_escalate_on": ["no_diff", "build_failed", "test_failed"],
        "reporter_model": reporter_model,
        "report_max_turns": report_max_turns,
        "qa_model": qa_model,
        "prompts_dir": prompts_dir,
    }

    # Merge backend-specific options
    cfg.update(claudecode_cfg)

    # Ensure prompts exist (never overwrites)
    pd = resolve_prompts_dir(repo, prompts_dir)
    ensure_default_prompt_files(pd)

    print("")
    print("Wizard completed.")
    print(f"- prompts_dir resolved to: {pd}")
    print("")
    return cfg
