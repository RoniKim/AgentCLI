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


def _ask_choice(prompt: str, default: str, choices: list[str]) -> str:
    """Simple choice prompt (case-insensitive)."""
    ch = "/".join(choices)
    s = input(f"{prompt} [{default}] ({ch}): ").strip()
    if not s:
        return default
    s2 = s.strip().lower()
    for c in choices:
        if s2 == c.lower():
            return c
    print("  (invalid choice, using default)")
    return default


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

    # Start from a full defaults snapshot so the saved config contains *all* keys.
    # This makes /config and future migrations predictable.
    cfg: Dict[str, Any] = dict(defaults)
    cfg["config_version"] = 2

    autopilot = _ask_bool("Enable autopilot", bool(cfg.get("autopilot", False)))
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

    loop = _ask_bool("Enable loop (unattended cycles)", bool(cfg.get("loop", False)))
    loop_sleep_seconds = _ask_int("Loop sleep seconds", int(defaults.get("loop_sleep_seconds", 60)))
    loop_max_cycles = _ask_int("Loop max cycles (0 = unlimited)", int(defaults.get("loop_max_cycles", 0)))
    loop_idle_exit_after = _ask_int("Loop idle exit after seconds (0 = disabled)", int(defaults.get("loop_idle_exit_after", 0)))

    no_build = _ask_bool("Disable build gate (no_build)", bool(cfg.get("no_build", False)))
    require_build = _ask_bool("Require build even if no_build is true (require_build)", bool(cfg.get("require_build", False)))
    run_tests = _ask_bool("Enable tests gate (run_tests)", bool(cfg.get("run_tests", False)))

    # Optional: generic build/test commands (preferred). If empty, legacy dotnet auto-detect may run.
    use_custom_gates = _ask_bool("Use custom build/test commands (build_cmd/test_cmd)", False)
    build_cmd: list[str] = []
    test_cmd: list[str] = []
    if use_custom_gates:
        build_raw = _ask_str("build_cmd (comma-separated argv; empty=none)", "").strip()
        test_raw = _ask_str("test_cmd  (comma-separated argv; empty=none)", "").strip()
        build_cmd = [p.strip() for p in build_raw.split(",") if p.strip()] if build_raw else []
        test_cmd = [p.strip() for p in test_raw.split(",") if p.strip()] if test_raw else []

    build_timeout_seconds = _ask_int("Build timeout seconds", int(cfg.get("build_timeout_seconds", 1800)))
    test_timeout_seconds = _ask_int("Test timeout seconds", int(cfg.get("test_timeout_seconds", 3600)))

    # Models (cost saver defaults)
    pm_model = _ask_str("PM model", str(cfg.get("pm_model", "gpt-5-mini")))
    dev_model = _ask_str("Dev model (base)", str(cfg.get("dev_model", "gpt-5.1-codex-mini")))
    dev_model_tier1 = _ask_str("Dev model tier1 (escalation)", str(cfg.get("dev_model_tier1", "gpt-5.1-codex")))
    dev_model_tier2 = _ask_str("Dev model tier2 (escalation)", str(cfg.get("dev_model_tier2", "gpt-5.2-codex")))
    dev_auto_escalate = _ask_bool("Dev auto-escalate on failures", bool(cfg.get("dev_auto_escalate", True)))
    dev_max_escalations = _ask_int("Dev max escalations per task", int(cfg.get("dev_max_escalations", 2)))
    dev_escalate_on_raw = _ask_str(
        "Dev escalate conditions (comma-separated)",
        ",".join(list(cfg.get("dev_escalate_on") or ["no_diff", "build_failed", "test_failed"])),
    ).strip()
    dev_escalate_on = [p.strip() for p in dev_escalate_on_raw.split(",") if p.strip()]

    reporter_model = _ask_str("Reporter model (shutdown report)", str(cfg.get("reporter_model", "gpt-5-nano")))
    report_max_turns = _ask_int("Reporter max turns", int(cfg.get("report_max_turns", 8)))
    qa_model = _ask_str("QA model", str(cfg.get("qa_model", "gpt-5-mini")))
    qa_always = _ask_bool("Always run QA even if no diff (qa_always)", bool(cfg.get("qa_always", False)))

    # prompts_dir is python-side; allow empty
    prompts_dir = _ask_str(
        "Prompts directory (absolute or relative to AgentCLI home; empty=default)",
        str(cfg.get("prompts_dir", "")) or "",
    ).strip()

    # Docs / policy / env
    docs_read_mode = _ask_choice("Docs read mode", str(cfg.get("docs_read_mode", "digest")), ["digest", "full", "none"])
    docs_dir = _ask_str("Docs directory (relative to repo)", str(cfg.get("docs_dir", ".doc/Docs")))
    docs_digest_file = _ask_str("Docs digest output file (relative to repo)", str(cfg.get("docs_digest_file", ".doc/DOCS_DIGEST.md")))
    generate_digest = _ask_bool("Generate docs digest on start (generate_digest)", bool(cfg.get("generate_digest", False)))

    no_policy_scan = _ask_bool("Disable policy scan (no_policy_scan)", bool(cfg.get("no_policy_scan", False)))
    policy_rules_file = _ask_str("Policy rules file (optional)", str(cfg.get("policy_rules_file", "")) or "").strip()
    policy_rule_raw = _ask_str("Inline policy_rule list (comma-separated; empty=none)", ",".join(list(cfg.get("policy_rule") or []))).strip()
    policy_rule = [p.strip() for p in policy_rule_raw.split(",") if p.strip()] if policy_rule_raw else []

    env_file = _ask_str(".env file path (absolute or relative to AgentCLI home; empty=auto)", str(cfg.get("env_file", "")) or "").strip()
    stop_file = _ask_str("Stop file name", str(cfg.get("stop_file", "STOP")) or "STOP").strip() or "STOP"

    # Tool backend (extensible)
    tool_backend = _ask_choice("Tool backend preset", str(cfg.get("tool_backend", "auto")), ["auto", "codex", "claude", "disabled"])
    tool_name = _ask_str("Tool display name", str(cfg.get("tool_name", "Codex_CLI")) or "Codex_CLI").strip() or "Codex_CLI"
    tool_command = _ask_str("Override tool command (optional)", str(cfg.get("tool_command", "")) or "").strip()
    tool_args_raw = _ask_str("Override tool args (comma-separated; optional)", ",".join(list(cfg.get("tool_args") or []))).strip()
    tool_args = [p.strip() for p in tool_args_raw.split(",") if p.strip()] if tool_args_raw else []

    # Apply wizard answers into the full config snapshot
    cfg.update(
        {
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
            "require_build": require_build,
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
            "dev_escalate_on": dev_escalate_on,
            "reporter_model": reporter_model,
            "report_max_turns": report_max_turns,
            "qa_model": qa_model,
            "qa_always": qa_always,
            "prompts_dir": prompts_dir,
            "docs_read_mode": docs_read_mode,
            "docs_dir": docs_dir,
            "docs_digest_file": docs_digest_file,
            "generate_digest": generate_digest,
            "no_policy_scan": no_policy_scan,
            "policy_rules_file": policy_rules_file,
            "policy_rule": policy_rule,
            "env_file": env_file,
            "stop_file": stop_file,
            "tool_backend": tool_backend,
            "tool_name": tool_name,
            "tool_command": tool_command,
            "tool_args": tool_args,
        }
    )

    # Ensure prompts exist (never overwrites)
    pd = resolve_prompts_dir(repo, prompts_dir)
    ensure_default_prompt_files(pd)

    print("")
    print("Wizard completed.")
    print(f"- prompts_dir resolved to: {pd}")
    print("")
    return cfg
