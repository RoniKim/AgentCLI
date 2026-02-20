from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

from .config import (
    app_home,
    legacy_config_path,
    legacy_default_config_path,
    load_config,
    resolve_config_path,
    resolve_prompts_dir,
    save_config,
)
from .wizard import run_wizard

from .prompts import ensure_default_prompt_files

import re


def _normalize_execution_backend(v: Any) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return "codex"
    if s in {"codex", "openai", "openai-agents", "agents"}:
        return "codex"
    if s in {"claude", "claude-code", "claude_code", "claudecode", "anthropic"}:
        return "claudecode"
    s = re.sub(r"[^a-z0-9_-]+", "", s)
    return s or "codex"


def _normalize_codex_reasoning_effort(v: Any) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return ""
    alias = {
        "none": "",
        "off": "",
        "default": "",
        "auto": "",
        "max": "xhigh",
        "highest": "xhigh",
        "x-high": "xhigh",
        "very-high": "xhigh",
        "very_high": "xhigh",
    }
    s = alias.get(s, s)
    allowed = {"minimal", "low", "medium", "high", "xhigh", ""}
    return s if s in allowed else ""



# ---- Defaults shown in /config ----
# NOTE: cycle.py references many args attributes directly (args.foo). Those MUST exist to avoid AttributeError,
# especially when starting from the interactive shell (/start) which constructs args from DEFAULTS.
DEFAULTS: Dict[str, Any] = {
    # Core / paths
    "repo": "",
    "config": "",
    "config_version": 2,
    "run_dir": "",            # empty => auto
    "resume_latest": False,
    # Execution backend engine (default: codex)
    "execution_backend": "codex",

    # Claude Code backend (claude-agent-sdk) options
    "claudecode_model": "sonnet",
    "claudecode_permission_mode": "acceptEdits",
    "claudecode_max_turns": 32,
    "claudecode_setting_sources": "project",
    "claudecode_system_prompt_append": "",
    "claudecode_continue_conversation": False,
    "claudecode_resume": "",
    "claudecode_enable_file_checkpointing": False,

    # Claude Agent SDK advanced toggles (best-effort; ignored if SDK doesn't support)
    "claudecode_user": "",
    "claudecode_include_partial_messages": False,
    "claudecode_fork_session": False,
    # 0 => SDK default (None)
    "claudecode_max_thinking_tokens": 0,

    # Tool allow/deny lists are comma-separated strings (or JSON arrays when saved via wizard).
    # PM may need to update .AgentCLI/PM_CACHE/PROJECT_ANALYSIS.md to keep parity with Codex backend.
    "claudecode_pm_allowed_tools": "Read,Grep,Glob,Write,Edit",
    "claudecode_pm_disallowed_tools": "",
    "claudecode_dev_allowed_tools": "Read,Write,Edit,Grep,Glob,Bash",
    "claudecode_dev_disallowed_tools": "",
    "claudecode_qa_allowed_tools": "Read,Grep,Glob,Bash",
    "claudecode_qa_disallowed_tools": "",

    # Claude Code role-specific model overrides (empty => claudecode_model fallback)
    "claudecode_pm_model": "",
    "claudecode_dev_model": "",
    "claudecode_dev_model_tier1": "",
    "claudecode_dev_model_tier2": "",
    "claudecode_qa_model": "",
    "claudecode_reporter_model": "",

    # Claude Agent SDK extensions (opt-in, all disabled by default)
    "claudecode_mcp_tools_enabled": False,
    "claudecode_hooks_enabled": False,
    "claudecode_can_use_tool_enabled": False,
    "claudecode_can_use_tool_strict_isolation": False,
    "claudecode_subagents_enabled": False,
    "claudecode_subagent_reviewer_enabled": True,
    "claudecode_subagent_runner_enabled": True,
    "claudecode_subagent_auditor_enabled": True,
    "claudecode_subagent_reviewer_model": "",
    "claudecode_subagent_runner_model": "",
    "claudecode_subagent_auditor_model": "",

    # Pipeline roles (comma-separated). Default keeps legacy order.
    # Example: "PM,Dev,QA" or "PM,Dev".
    "roles": "PM,Dev,QA",

    # Profile
    "profile": "personal",

    # Runner behavior
    "autopilot": False,
    "loop": False,
    "loop_sleep_seconds": 60,
    "loop_max_cycles": 0,
    "loop_idle_exit_after": 0,
    "idle_exit_cycles": 3,
    # Goals completion level: "p0" (P0 only), "p1" (P0+P1), "all" (every checkbox)
    "goals_completion_level": "all",
    "max_consecutive_failed_cycles": 3,
    "budget_reset_per_cycle": True,
    "quota_check_enabled": True,
    "quota_five_hour_max_utilization": 95,
    "quota_seven_day_max_utilization": 95,
    "quota_wait_for_reset": True,
    "continuous": False,
    "iterations": 30,
    "max_turns_per_task": 12,
    "isolate_task": False,
    "worktree_isolation": False,

    # Gitops defaults
    "gitops": {
        "untracked_exclude_globs": [".doc/**", ".doc", ".AgentCLI/**", ".AgentCLI", ".agent_runs/**", ".agent_runs", "*.log"],
    },

    # Safety / gates
    "no_policy_scan": False,
    "policy_rules_file": "",
    "policy_rule": [],
    "scan_scope": "quick",
    "policy_scan_scope": "",
    "security_scan_scope": "",
    "scan_max_files": 500,
    "scan_max_bytes_per_file": 200_000,
    "scan_max_total_bytes": 20_000_000,
    "scan_timeout_seconds": 60,
    "scan_ignore_globs": [".doc/**", ".doc", ".AgentCLI/**", ".AgentCLI", ".agent_runs/**", ".agent_runs", "worktree/**", "**/*.log"],
    "scan_ignore_paths": [],
    "scan_include_untracked_in_full": False,

    # Policy config (new)
    "policy": {
        "enabled": None,
        "fail_severity": "high",
        "rules": [],
        "ignore_paths": [],
        "allow_patterns": [],
    },

    # Security stage config
    "security": {
        "enabled": False,
        "fail_severity": "high",
        "rules_path": "",
    },

    "no_build": False,
    "require_build": False,
    "run_tests": False,

    # dotnet gates (used by cycle.py)
    "dotnet_build_target": "",
    "dotnet_test_target": "",
    "dotnet_test_filter": "",

    # generic gates (preferred)
    # If provided, these override dotnet_* targets/filters and are used as-is.
    # Format: ["cmd", "arg1", "arg2", ...]
    "build_cmd": [],
    "test_cmd": [],
    "build_timeout_seconds": 1800,

    # Models (all Codex; single billing)
    "pm_model": "gpt-5.1-codex-mini",
    "dev_model": "gpt-5.1-codex-mini",
    "qa_model": "gpt-5.1-codex-mini",
    # Optional codex exec override: -c model_reasoning_effort="<value>"
    # Empty = use Codex CLI/global config default.
    "codex_reasoning_effort": "",
    "qa_always": True,
    "qa_to_backlog": False,
    "max_qa_followups": 5,

    # Reporter / shutdown report
    "reporter_model": "gpt-5.1-codex-mini",
    "report_max_turns": 8,

    # Dev cost controls
    "dev_auto_escalate": True,
    "dev_max_escalations": 2,
    "dev_model_tier1": "gpt-5.1-codex",
    "dev_model_tier2": "gpt-5.2-codex",
    "dev_escalate_on": ["no_diff", "build_failed", "test_failed", "no_commits"],

    # Timeouts (seconds) - referenced by cycle.py
    "pm_timeout_seconds": 900,
    "dev_timeout_seconds": 900,
    "mcp_timeout_seconds": 120,
    "test_timeout_seconds": 3600,

    # PM tuning knobs (referenced by cycle.py)
    "pm_structured_retries": 2,
    "pm_max_turns_continuations": 1,
    "pm_bootstrap_max_turns": 28,
    "pm_incremental_max_turns": 18,
    "pm_refresh_backlog": False,
    "pm_refresh_every_cycles": 0,
    "pm_include_working_tree": False,

    # Dev tuning knobs
    "dev_max_turns_continuations": 2,

    # Budget guardrails
    "budgets": {
        "max_pm_structured_retries": 2,
        "max_dev_escalations_per_task": 2,
        "max_dev_continuations_per_task": 2,
        "max_total_escalations_per_run": 10,
        "max_total_continuations_per_run": 10,
        "max_total_repair_attempts_per_run": 5,
    },

    # MCP
    "mcp_mode": "npx",
    "codex_package": "@openai/codex@latest",

    # Docs
    "docs_read_mode": "digest",
    "docs_dir": ".doc/Docs",
    "docs_digest_file": ".doc/DOCS_DIGEST.md",
    "generate_digest": False,

    # Prompts (python-side default when empty)
    "prompts_dir": "",

    # Skills system
    "skills": {
        "enabled": False,
        "roots": [
            str(Path.home() / ".codex" / "skills"),
            str(Path.home() / ".agents" / "skills"),
            str(Path.home() / ".claude" / "skills"),
        ],
        "snapshot_dir": "",
        "inline_mode": "qa",
        "max_excerpt_lines": 12,
        "pm_summary_max_items": 120,
        "pm_summary_max_chars": 8000,
        "qa_max_total_chars": 8000,
        "skill_match_autofix": False,
        "skill_match_autofix_threshold": 0.9,
    },

    # Misc / debug
    "debug": False,
    "stop_file": "STOP",
    "allow_no_diff": False,
    "stop_if_no_diff": False,

    # Git rollback safety
    "dangerous_git_rollback": False,

    # Failover (backend chain)
    "failover_enabled": False,
    "failover_backends": ["codex", "claudecode"],
    "failover_on": ["quota_exhausted", "quota_utilization"],
    "failover_max_switches": 0,  # 0 means unlimited switches

    # Plugin stages
    "plugins_enabled": False,
    "plugins_allowlist": [],
    "plugins_strict": True,

    # Task history (cross-run SQLite)
    "task_history_enabled": True,
    "task_history_max_items": 15,
    "max_consecutive_task_failures": 3,

    # Project goals / completion tracking
    "goals_enabled": True,
    "goals_auto_generate": True,
    "goals_auto_check": True,
    "goals_auto_refresh": False,           # Auto-refresh GOALS after project_complete
    "goals_refresh_max_per_run": 3,        # Max refresh attempts per run (loop guard)

    # Remote control plane (Telegram)
    "telegram": {
        "enabled": False,
        "bot_token": "",
        "allowed_chat_ids": [],
        "pairing_code": "",
        "instance_name": "",
        "notify_events": [
            "run_start",
            "run_stop",
            "task_done",
            "task_failed",
            "quota",
            "error",
            "stalled",
            "project_complete",
            "backend_failover",
        ],
        "send_cycle_summary": True,
        "notify_poll_interval_seconds": 8,
        "stalled_seconds": 600,
        "tail_lines_default": 50,
        "runner_mode": "thread",  # thread | subprocess
        "poll_timeout_seconds": 30,
    },
}


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=True)

    # Core
    p.add_argument("--repo", required=True, help="Repo root path")

    # Config / UX
    p.add_argument(
        "--config",
        default="",
        help="Config file path (default: ~/.agentcli/configs/<repo-slug>.json). Relative paths resolve from AgentCLI home.",
    )
    p.add_argument("--run-now", action="store_true", help="Run immediately (skip interactive shell)")
    p.add_argument("--wizard", action="store_true", help="Run wizard to create/update config")
    p.add_argument("--non-interactive", action="store_true", help="Disable interactive prompts")
    p.add_argument("--init-prompts", action="store_true", help="Create prompt templates in prompts_dir and exit")
    p.add_argument("--telegram", dest="telegram_service", action="store_true", default=None, help="Run hybrid mode (local shell + Telegram control-plane)")
    p.add_argument("--telegram-runner-mode", default=None, choices=["thread", "subprocess"], help="Runner execution mode for Telegram hybrid mode")
    p.add_argument("--telegram-poll-timeout", type=int, default=None, help="Telegram long-poll timeout seconds")
    p.add_argument("--telegram-allowed-chat-id", action="append", default=None, help="Allowlisted chat_id (repeatable)")
    p.add_argument("--telegram-bot-token", default=None, help="Telegram bot token override")
    p.add_argument("--telegram-pairing-code", default=None, help="One-time pairing code for /pair command")
    p.add_argument("--telegram-instance-name", default=None, help="Instance label shown in Telegram notifications")
    p.add_argument("--telegram-notify-events", default=None, help="Comma-separated push events (run_start,run_stop,task_done,task_failed,quota,error,stalled)")
    p.add_argument("--telegram-send-cycle-summary", action=argparse.BooleanOptionalAction, default=None, help="Push new cycle_summary.log lines")
    p.add_argument("--telegram-notify-interval", type=int, default=None, help="Telegram push polling interval seconds")
    p.add_argument("--telegram-stalled-seconds", type=int, default=None, help="Stall detection threshold seconds (default: 600)")

    # Paths
    p.add_argument("--run-dir", default=None, help="Fixed run_dir to reuse. Empty/None = auto")
    p.add_argument("--resume-latest", action=argparse.BooleanOptionalAction, default=None, help="Resume latest run_dir")

    # Execution backend
    p.add_argument(
        "--execution-backend",
        dest="execution_backend",
        default=None,
        choices=["codex", "claudecode"],
        help="Execution backend engine (codex|claudecode). Default is codex.",
    )

    p.add_argument(
        "--roles",
        default=None,
        help=(
            "Comma-separated pipeline roles/stages to run. "
            "Examples: PM,Dev,QA (default), or PM,Dev (skip QA). "
            "Forward-compatible with plugin stages."
        ),
    )
    p.add_argument("--profile", default=None, choices=["personal", "enterprise"])

    # Behavior
    p.add_argument("--autopilot", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--loop", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--loop-sleep-seconds", type=int, default=None)
    p.add_argument("--loop-max-cycles", type=int, default=None)
    p.add_argument("--loop-idle-exit-after", type=int, default=None)

    p.add_argument("--continuous", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--iterations", type=int, default=None)
    p.add_argument("--max-turns-per-task", type=int, default=None)
    p.add_argument("--isolate-task", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--worktree-isolation", action=argparse.BooleanOptionalAction, default=None, help="Run in a git worktree")

    # Safety / gates
    # NOTE: argparse.BooleanOptionalAction cannot be used with option strings starting with "--no-" (Python 3.14+).
    # Keep backward-compatible flags while supporting an explicit enable flag.
    p.add_argument("--no-policy-scan", dest="no_policy_scan", action="store_true", default=None, help="Disable policy scan")
    p.add_argument("--policy-scan", dest="no_policy_scan", action="store_false", default=None, help="Enable policy scan")
    p.add_argument("--policy-enabled", dest="policy_enabled", action="store_true", default=None)
    p.add_argument("--policy-disabled", dest="policy_enabled", action="store_false", default=None)
    p.add_argument("--policy-fail-severity", default=None)
    p.add_argument("--policy-ignore-path", action="append", default=None)
    p.add_argument("--policy-allow-pattern", action="append", default=None)
    p.add_argument("--scan-scope", default=None, choices=["quick", "staged", "full"])
    p.add_argument("--policy-scan-scope", default=None, choices=["quick", "staged", "full"])
    p.add_argument("--security-scan-scope", default=None, choices=["quick", "staged", "full"])
    p.add_argument("--scan-max-files", type=int, default=None)
    p.add_argument("--scan-max-bytes-per-file", type=int, default=None)
    p.add_argument("--scan-max-total-bytes", type=int, default=None)
    p.add_argument("--scan-timeout-seconds", type=int, default=None)
    p.add_argument("--scan-ignore-glob", action="append", default=None)
    p.add_argument("--scan-ignore-path", action="append", default=None)
    p.add_argument("--scan-include-untracked-in-full", action=argparse.BooleanOptionalAction, default=None)

    p.add_argument("--policy-rules-file", default=None, help="Path to policy rules file")
    p.add_argument("--policy-rule", action="append", default=None, help="Inline policy rule (repeatable)")

    p.add_argument("--no-build", dest="no_build", action="store_true", default=None, help="Disable build gate")
    p.add_argument("--build", dest="no_build", action="store_false", default=None, help="Enable build gate")
    p.add_argument("--require-build", action=argparse.BooleanOptionalAction, default=None, help="Force build gate even if no_build is true")
    p.add_argument("--run-tests", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--dangerous-git-rollback", action=argparse.BooleanOptionalAction, default=None, help="Allow destructive git rollback")

    # Security stage
    p.add_argument("--security-enabled", dest="security_enabled", action="store_true", default=None)
    p.add_argument("--security-disabled", dest="security_enabled", action="store_false", default=None)
    p.add_argument("--security-fail-severity", default=None)
    p.add_argument("--security-rules-path", default=None)

    # Failover
    p.add_argument("--failover", dest="failover_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable backend failover")
    p.add_argument("--failover-backends", default=None, help="Backend chain for failover (comma-separated)")
    p.add_argument("--failover-on", action="append", default=None, help="Failover triggers (repeatable)")
    p.add_argument("--failover-max-switches", type=int, default=None, help="Max backend switches per run (0 = unlimited)")

    # Plugin stages
    p.add_argument("--plugins-enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable plugin stage loading")
    p.add_argument("--plugins-allowlist", default=None, help="Allowlist patterns for plugin stages (comma-separated)")
    p.add_argument("--plugins-strict", action=argparse.BooleanOptionalAction, default=None, help="Fail if plugin load is blocked or fails")

    p.add_argument("--dotnet-build-target", default=None, help="dotnet build target (e.g., path to .sln or project)")
    p.add_argument("--dotnet-test-target", default=None, help="dotnet test target (e.g., path to .sln or project)")
    p.add_argument("--dotnet-test-filter", default=None, help="dotnet test filter (passed to --filter)")

    # Generic gates (preferred): comma-separated argv list.
    # Examples:
    #   --build-cmd "dotnet,build,My.sln"
    #   --test-cmd  "dotnet,test,--filter,FullyQualifiedName~MyTest"
    p.add_argument("--build-cmd", default=None, help="Build command (comma-separated argv list)")
    p.add_argument("--test-cmd", default=None, help="Test command (comma-separated argv list)")
    p.add_argument("--build-timeout-seconds", type=int, default=None)

    # Models / MCP
    p.add_argument("--pm-model", default=None)
    p.add_argument("--dev-model", default=None)
    p.add_argument("--qa-model", default=None)
    p.add_argument(
        "--codex-reasoning-effort",
        default=None,
        choices=["minimal", "low", "medium", "high", "xhigh"],
        help="Codex reasoning effort override (maps to model_reasoning_effort).",
    )
    p.add_argument("--qa-to-backlog", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--max-qa-followups", type=int, default=None)
    p.add_argument("--reporter-model", default=None)
    p.add_argument("--report-max-turns", type=int, default=None)

    # Claude Code backend
    p.add_argument("--claudecode-model", default=None, help="Claude model (e.g., sonnet/opus/haiku)")
    p.add_argument("--claudecode-permission-mode", default=None, help="Claude tool permission mode (default/acceptEdits/bypassPermissions/plan)")
    p.add_argument("--claudecode-max-turns", type=int, default=None, help="Max turns per Claude query")
    p.add_argument("--claudecode-setting-sources", default=None, help="Settings sources: comma-separated user,project,local")
    p.add_argument("--claudecode-system-prompt-append", default=None, help="Append instructions to Claude Code system prompt preset")
    p.add_argument("--claudecode-continue-conversation", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--claudecode-resume", default=None, help="Resume session id")
    p.add_argument("--claudecode-enable-file-checkpointing", action=argparse.BooleanOptionalAction, default=None)

    # Claude Agent SDK advanced toggles (best-effort)
    p.add_argument("--claudecode-user", default=None, help="User identifier (optional)")
    p.add_argument("--claudecode-include-partial-messages", action=argparse.BooleanOptionalAction, default=None, help="Enable partial/streaming message events")
    p.add_argument("--claudecode-fork-session", action=argparse.BooleanOptionalAction, default=None, help="When resuming, fork into a new session id")
    p.add_argument("--claudecode-max-thinking-tokens", type=int, default=None, help="Max thinking tokens (0 or omitted = SDK default)")

    p.add_argument("--claudecode-pm-allowed-tools", default=None, help="PM stage allowed tools (comma-separated)")
    p.add_argument("--claudecode-pm-disallowed-tools", default=None, help="PM stage disallowed tools (comma-separated)")
    p.add_argument("--claudecode-dev-allowed-tools", default=None, help="Dev stage allowed tools (comma-separated)")
    p.add_argument("--claudecode-dev-disallowed-tools", default=None, help="Dev stage disallowed tools (comma-separated)")
    p.add_argument("--claudecode-qa-allowed-tools", default=None, help="QA stage allowed tools (comma-separated)")
    p.add_argument("--claudecode-qa-disallowed-tools", default=None, help="QA stage disallowed tools (comma-separated)")

    # Claude Code role-specific model overrides
    p.add_argument("--claudecode-pm-model", default=None, help="Claude model for PM stage (empty => claudecode_model)")
    p.add_argument("--claudecode-dev-model", default=None, help="Claude model for Dev stage (empty => claudecode_model)")
    p.add_argument("--claudecode-dev-model-tier1", default=None, help="Claude model for Dev escalation tier1 (empty => no escalation)")
    p.add_argument("--claudecode-dev-model-tier2", default=None, help="Claude model for Dev escalation tier2 (empty => no escalation)")
    p.add_argument("--claudecode-qa-model", default=None, help="Claude model for QA stage (empty => claudecode_model)")
    p.add_argument("--claudecode-reporter-model", default=None, help="Claude model for Reporter stage (empty => claudecode_model)")

    # Claude Agent SDK extensions (opt-in)
    p.add_argument("--claudecode-mcp-tools", dest="claudecode_mcp_tools_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable custom MCP tools for Claude (check_state, run_build, etc.)")
    p.add_argument("--claudecode-hooks", dest="claudecode_hooks_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable PreToolUse/PostToolUse safety hooks")
    p.add_argument("--claudecode-can-use-tool", dest="claudecode_can_use_tool_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable dynamic tool permission control")
    p.add_argument("--claudecode-can-use-tool-strict-isolation", dest="claudecode_can_use_tool_strict_isolation", action=argparse.BooleanOptionalAction, default=None, help="Strict task file isolation in Dev stage")
    p.add_argument("--claudecode-subagents", dest="claudecode_subagents_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable subagents (code-reviewer, test-runner, security-auditor)")
    p.add_argument("--claudecode-subagent-reviewer", dest="claudecode_subagent_reviewer_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable code-reviewer subagent")
    p.add_argument("--claudecode-subagent-runner", dest="claudecode_subagent_runner_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable test-runner subagent")
    p.add_argument("--claudecode-subagent-auditor", dest="claudecode_subagent_auditor_enabled", action=argparse.BooleanOptionalAction, default=None, help="Enable security-auditor subagent")
    p.add_argument("--claudecode-subagent-reviewer-model", default=None, help="Model for code-reviewer subagent")
    p.add_argument("--claudecode-subagent-runner-model", default=None, help="Model for test-runner subagent")
    p.add_argument("--claudecode-subagent-auditor-model", default=None, help="Model for security-auditor subagent")

    p.add_argument("--dev-auto-escalate", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--dev-max-escalations", type=int, default=None)
    p.add_argument("--dev-model-tier1", default=None)
    p.add_argument("--dev-model-tier2", default=None)
    p.add_argument("--dev-escalate-on", action="append", default=None, help="Escalate conditions (repeatable): no_diff, build_failed, test_failed")
    p.add_argument("--qa-always", action=argparse.BooleanOptionalAction, default=None)

    p.add_argument("--mcp-mode", default=None, choices=["npx", "codex", "disabled"])
    p.add_argument("--codex-package", default=None)
    p.add_argument("--mcp-timeout-seconds", type=int, default=None)

    # Docs / prompts
    p.add_argument("--docs-read-mode", default=None, choices=["digest", "full", "none"])
    p.add_argument("--docs-dir", default=None)
    p.add_argument("--docs-digest-file", default=None, help="Digest output file path (relative to repo by default)")
    p.add_argument("--generate-digest", action=argparse.BooleanOptionalAction, default=None, help="Force regenerate docs digest")

    p.add_argument(
        "--prompts-dir",
        default=None,
        help="Prompt templates directory. Absolute path or relative to AgentCLI home. Empty uses default ~/.agentcli/prompts/<repo-slug>/",
    )

    # Diagnostics
    p.add_argument("--debug", action=argparse.BooleanOptionalAction, default=None)

    # Misc
    p.add_argument("--stop-file", default=None)
    p.add_argument("--allow-no-diff", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--stop-if-no-diff", action=argparse.BooleanOptionalAction, default=None)  # compat only
    p.add_argument("--test-timeout-seconds", type=int, default=None)

    # Task history
    p.add_argument("--task-history", dest="task_history_enabled",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="Enable/disable cross-run task history (SQLite)")
    p.add_argument("--task-history-max-items", type=int, default=None,
                   help="Max history items to inject into PM prompt")

    # PM tuning knobs
    p.add_argument("--pm-timeout-seconds", type=int, default=None)
    p.add_argument("--dev-timeout-seconds", type=int, default=None)
    p.add_argument("--pm-bootstrap-max-turns", type=int, default=None)
    p.add_argument("--pm-incremental-max-turns", type=int, default=None)
    p.add_argument("--pm-refresh-backlog", action=argparse.BooleanOptionalAction, default=None)
    p.add_argument("--pm-refresh-every-cycles", type=int, default=None)
    p.add_argument("--pm-include-working-tree", action=argparse.BooleanOptionalAction, default=None)

    # Structured output knobs
    p.add_argument("--pm-structured-retries", type=int, default=None)
    p.add_argument("--pm-max-turns-continuations", type=int, default=None)
    p.add_argument("--dev-max-turns-continuations", type=int, default=None)

    # Budget guardrails
    p.add_argument("--budget-max-pm-structured-retries", type=int, default=None)
    p.add_argument("--budget-max-dev-escalations-per-task", type=int, default=None)
    p.add_argument("--budget-max-dev-continuations-per-task", type=int, default=None)
    p.add_argument("--budget-max-total-escalations-per-run", type=int, default=None)
    p.add_argument("--budget-max-total-continuations-per-run", type=int, default=None)
    p.add_argument("--budget-max-total-repair-attempts-per-run", type=int, default=None)

    return p


def _merge_effective(defaults: Dict[str, Any], cfg: Dict[str, Any], args_ns: argparse.Namespace) -> Dict[str, Any]:
    eff = dict(defaults)
    # config overlay
    for k, v in cfg.items():
        eff[k] = v

    # cli args overlay (only if not None)
    for k, v in vars(args_ns).items():
        if v is None:
            continue
        eff[k] = v

    explicit_args = {k for k, v in vars(args_ns).items() if v is not None}


    # ---- MIGRATIONS (best-effort, for older saved configs) ----
    # Older configs may pin some tuning knobs to 0 (meaning "no continuations") simply because
    # they were created before these defaults were introduced. If the config has no explicit
    # version marker, treat 0 as "legacy default" and lift it to current defaults.
    cfg_ver = cfg.get("config_version", 0)
    try:
        cfg_ver_i = int(cfg_ver) if cfg_ver is not None else 0
    except Exception:
        cfg_ver_i = 0

    # Only apply when user did NOT pass CLI flags for these and config looks legacy.
    if cfg_ver_i < 2:
        if getattr(args_ns, "dev_max_turns_continuations", None) is None:
            v = eff.get("dev_max_turns_continuations", None)
            if v in (0, None, ""):
                eff["dev_max_turns_continuations"] = int(defaults.get("dev_max_turns_continuations", 2))
        if getattr(args_ns, "pm_max_turns_continuations", None) is None:
            v = eff.get("pm_max_turns_continuations", None)
            if v in (0, None, ""):
                eff["pm_max_turns_continuations"] = int(defaults.get("pm_max_turns_continuations", 1))

    # Always stamp current config_version in effective args (save happens via /wizard).
    eff["config_version"] = int(defaults.get("config_version", 2))

    # compat: stop_if_no_diff -> allow_no_diff inverse-ish (keep simple)
    if eff.get("stop_if_no_diff") is True and eff.get("allow_no_diff") is None:
        eff["allow_no_diff"] = False

    # normalize policy_rule
    pr = eff.get("policy_rule")
    if pr is None:
        eff["policy_rule"] = []
    elif isinstance(pr, str):
        eff["policy_rule"] = [pr]

    # ---- normalize policy/security config ----
    def _normalize_policy(raw: Any) -> dict[str, Any]:
        defaults_policy = defaults.get("policy", {}) if isinstance(defaults.get("policy", {}), dict) else {}
        out: dict[str, Any] = dict(defaults_policy)
        if isinstance(raw, dict):
            out.update(raw)

        if "enabled" in out and out["enabled"] is not None:
            out["enabled"] = bool(out["enabled"])
        else:
            out["enabled"] = None

        out["fail_severity"] = str(out.get("fail_severity") or defaults_policy.get("fail_severity") or "high")

        rules = out.get("rules") or []
        if isinstance(rules, str):
            rules = [rules]
        out["rules"] = rules if isinstance(rules, list) else []

        ignore_paths = out.get("ignore_paths") or []
        if isinstance(ignore_paths, str):
            ignore_paths = [p.strip() for p in ignore_paths.split(",") if p.strip()]
        out["ignore_paths"] = [str(p).strip() for p in ignore_paths if str(p).strip()]

        allow_patterns = out.get("allow_patterns") or []
        if isinstance(allow_patterns, str):
            allow_patterns = [p.strip() for p in allow_patterns.split(",") if p.strip()]
        out["allow_patterns"] = [str(p).strip() for p in allow_patterns if str(p).strip()]
        return out

    def _normalize_security(raw: Any) -> dict[str, Any]:
        defaults_sec = defaults.get("security", {}) if isinstance(defaults.get("security", {}), dict) else {}
        out: dict[str, Any] = dict(defaults_sec)
        if isinstance(raw, dict):
            out.update(raw)
        out["enabled"] = bool(out.get("enabled", False))
        out["fail_severity"] = str(out.get("fail_severity") or defaults_sec.get("fail_severity") or "high")
        out["rules_path"] = str(out.get("rules_path") or "")
        return out

    eff["policy"] = _normalize_policy(eff.get("policy"))
    eff["security"] = _normalize_security(eff.get("security"))

    def _normalize_telegram(raw: Any) -> dict[str, Any]:
        defaults_tg = defaults.get("telegram", {}) if isinstance(defaults.get("telegram", {}), dict) else {}
        out: dict[str, Any] = dict(defaults_tg)
        if isinstance(raw, dict):
            out.update(raw)

        out["enabled"] = bool(out.get("enabled", False))
        out["bot_token"] = str(out.get("bot_token") or "")
        out["pairing_code"] = str(out.get("pairing_code") or "")
        out["instance_name"] = str(out.get("instance_name") or "").strip()

        allowed_raw = out.get("allowed_chat_ids") or []
        if isinstance(allowed_raw, str):
            allowed_values = [p.strip() for p in allowed_raw.split(",") if p.strip()]
        elif isinstance(allowed_raw, list):
            allowed_values = [str(v).strip() for v in allowed_raw if str(v).strip()]
        else:
            allowed_values = []
        allowed_ids: list[int] = []
        seen: set[int] = set()
        for value in allowed_values:
            try:
                chat_id = int(str(value).strip())
            except Exception:
                continue
            if chat_id in seen:
                continue
            seen.add(chat_id)
            allowed_ids.append(chat_id)
        out["allowed_chat_ids"] = allowed_ids

        notify_raw = out.get("notify_events") or []
        if isinstance(notify_raw, str):
            notify_events = [p.strip() for p in notify_raw.split(",") if p.strip()]
        elif isinstance(notify_raw, list):
            notify_events = [str(v).strip() for v in notify_raw if str(v).strip()]
        else:
            notify_events = []
        normalized_events: list[str] = []
        seen_events: set[str] = set()
        for value in notify_events:
            key = str(value).strip().lower()
            if not key or key in seen_events:
                continue
            seen_events.add(key)
            normalized_events.append(key)
        out["notify_events"] = normalized_events

        out["send_cycle_summary"] = bool(out.get("send_cycle_summary", True))
        try:
            out["notify_poll_interval_seconds"] = max(2, int(out.get("notify_poll_interval_seconds") or 8))
        except Exception:
            out["notify_poll_interval_seconds"] = int(defaults_tg.get("notify_poll_interval_seconds") or 8)
        try:
            out["stalled_seconds"] = max(60, int(out.get("stalled_seconds") or 600))
        except Exception:
            out["stalled_seconds"] = int(defaults_tg.get("stalled_seconds") or 600)
        try:
            out["tail_lines_default"] = max(1, int(out.get("tail_lines_default") or 50))
        except Exception:
            out["tail_lines_default"] = int(defaults_tg.get("tail_lines_default") or 50)

        runner_mode = str(out.get("runner_mode") or "thread").strip().lower()
        if runner_mode not in {"thread", "subprocess"}:
            runner_mode = "thread"
        out["runner_mode"] = runner_mode
        try:
            out["poll_timeout_seconds"] = max(1, int(out.get("poll_timeout_seconds") or 30))
        except Exception:
            out["poll_timeout_seconds"] = int(defaults_tg.get("poll_timeout_seconds") or 30)

        return out

    eff["telegram"] = _normalize_telegram(eff.get("telegram"))

    def _normalize_scan() -> None:
        eff["scan_scope"] = str(eff.get("scan_scope") or defaults.get("scan_scope") or "quick").strip().lower()
        if eff["scan_scope"] not in {"quick", "staged", "full"}:
            eff["scan_scope"] = "quick"
        eff["policy_scan_scope"] = str(eff.get("policy_scan_scope") or "").strip().lower()
        if eff["policy_scan_scope"] and eff["policy_scan_scope"] not in {"quick", "staged", "full"}:
            eff["policy_scan_scope"] = ""
        eff["security_scan_scope"] = str(eff.get("security_scan_scope") or "").strip().lower()
        if eff["security_scan_scope"] and eff["security_scan_scope"] not in {"quick", "staged", "full"}:
            eff["security_scan_scope"] = ""

        def _norm_int(key: str, fallback: int) -> int:
            try:
                return int(eff.get(key) or fallback)
            except Exception:
                return int(fallback)

        eff["scan_max_files"] = _norm_int("scan_max_files", int(defaults.get("scan_max_files") or 500))
        eff["scan_max_bytes_per_file"] = _norm_int("scan_max_bytes_per_file", int(defaults.get("scan_max_bytes_per_file") or 200_000))
        eff["scan_max_total_bytes"] = _norm_int("scan_max_total_bytes", int(defaults.get("scan_max_total_bytes") or 20_000_000))
        eff["scan_timeout_seconds"] = _norm_int("scan_timeout_seconds", int(defaults.get("scan_timeout_seconds") or 60))
        eff["scan_include_untracked_in_full"] = bool(eff.get("scan_include_untracked_in_full", False))

        ignore_globs = eff.get("scan_ignore_globs") or []
        if isinstance(ignore_globs, str):
            ignore_globs = [p.strip() for p in ignore_globs.split(",") if p.strip()]
        eff["scan_ignore_globs"] = [str(p).strip() for p in ignore_globs if str(p).strip()]

        ignore_paths = eff.get("scan_ignore_paths") or []
        if isinstance(ignore_paths, str):
            ignore_paths = [p.strip() for p in ignore_paths.split(",") if p.strip()]
        eff["scan_ignore_paths"] = [str(p).strip() for p in ignore_paths if str(p).strip()]

    _normalize_scan()

    if "policy_enabled" in explicit_args:
        eff["policy"]["enabled"] = bool(eff.get("policy_enabled"))
    if "policy_fail_severity" in explicit_args:
        eff["policy"]["fail_severity"] = str(eff.get("policy_fail_severity") or eff["policy"]["fail_severity"])
    if "policy_ignore_path" in explicit_args and eff.get("policy_ignore_path") is not None:
        eff["policy"]["ignore_paths"] = [str(p).strip() for p in (eff.get("policy_ignore_path") or []) if str(p).strip()]
    if "policy_allow_pattern" in explicit_args and eff.get("policy_allow_pattern") is not None:
        eff["policy"]["allow_patterns"] = [str(p).strip() for p in (eff.get("policy_allow_pattern") or []) if str(p).strip()]

    if "scan_scope" in explicit_args:
        eff["scan_scope"] = str(eff.get("scan_scope") or eff["scan_scope"]).strip().lower()
    if "policy_scan_scope" in explicit_args:
        eff["policy_scan_scope"] = str(eff.get("policy_scan_scope") or "").strip().lower()
    if "security_scan_scope" in explicit_args:
        eff["security_scan_scope"] = str(eff.get("security_scan_scope") or "").strip().lower()
    if "scan_max_files" in explicit_args:
        _v = eff.get("scan_max_files")
        eff["scan_max_files"] = int(_v) if _v is not None else int(eff["scan_max_files"])
    if "scan_max_bytes_per_file" in explicit_args:
        _v = eff.get("scan_max_bytes_per_file")
        eff["scan_max_bytes_per_file"] = int(_v) if _v is not None else int(eff["scan_max_bytes_per_file"])
    if "scan_max_total_bytes" in explicit_args:
        _v = eff.get("scan_max_total_bytes")
        eff["scan_max_total_bytes"] = int(_v) if _v is not None else int(eff["scan_max_total_bytes"])
    if "scan_timeout_seconds" in explicit_args:
        _v = eff.get("scan_timeout_seconds")
        eff["scan_timeout_seconds"] = int(_v) if _v is not None else int(eff["scan_timeout_seconds"])
    if "scan_ignore_glob" in explicit_args and eff.get("scan_ignore_glob") is not None:
        eff["scan_ignore_globs"] = [str(p).strip() for p in (eff.get("scan_ignore_glob") or []) if str(p).strip()]
    if "scan_ignore_path" in explicit_args and eff.get("scan_ignore_path") is not None:
        eff["scan_ignore_paths"] = [str(p).strip() for p in (eff.get("scan_ignore_path") or []) if str(p).strip()]
    if "scan_include_untracked_in_full" in explicit_args:
        eff["scan_include_untracked_in_full"] = bool(eff.get("scan_include_untracked_in_full"))

    if "security_enabled" in explicit_args:
        eff["security"]["enabled"] = bool(eff.get("security_enabled"))
    if "security_fail_severity" in explicit_args:
        eff["security"]["fail_severity"] = str(eff.get("security_fail_severity") or eff["security"]["fail_severity"])
    if "security_rules_path" in explicit_args:
        eff["security"]["rules_path"] = str(eff.get("security_rules_path") or "")

    if "telegram_service" in explicit_args:
        eff["telegram"]["enabled"] = bool(eff.get("telegram_service"))
    if "telegram_runner_mode" in explicit_args:
        mode = str(eff.get("telegram_runner_mode") or "").strip().lower()
        if mode in {"thread", "subprocess"}:
            eff["telegram"]["runner_mode"] = mode
    if "telegram_poll_timeout" in explicit_args:
        try:
            eff["telegram"]["poll_timeout_seconds"] = max(1, int(eff.get("telegram_poll_timeout") or 30))
        except Exception:
            pass
    if "telegram_allowed_chat_id" in explicit_args:
        raw_ids = eff.get("telegram_allowed_chat_id") or []
        normalized: list[int] = []
        seen_ids: set[int] = set()
        for value in raw_ids:
            try:
                chat_id = int(str(value).strip())
            except Exception:
                continue
            if chat_id in seen_ids:
                continue
            seen_ids.add(chat_id)
            normalized.append(chat_id)
        eff["telegram"]["allowed_chat_ids"] = normalized
    if "telegram_bot_token" in explicit_args:
        eff["telegram"]["bot_token"] = str(eff.get("telegram_bot_token") or "")
    if "telegram_pairing_code" in explicit_args:
        eff["telegram"]["pairing_code"] = str(eff.get("telegram_pairing_code") or "")
    if "telegram_instance_name" in explicit_args:
        eff["telegram"]["instance_name"] = str(eff.get("telegram_instance_name") or "").strip()
    if "telegram_notify_events" in explicit_args:
        raw_events = str(eff.get("telegram_notify_events") or "").strip()
        if raw_events:
            eff["telegram"]["notify_events"] = [p.strip() for p in raw_events.split(",") if p.strip()]
        else:
            eff["telegram"]["notify_events"] = []
    if "telegram_send_cycle_summary" in explicit_args:
        eff["telegram"]["send_cycle_summary"] = bool(eff.get("telegram_send_cycle_summary"))
    if "telegram_notify_interval" in explicit_args:
        try:
            eff["telegram"]["notify_poll_interval_seconds"] = max(2, int(eff.get("telegram_notify_interval") or 8))
        except Exception:
            pass
    if "telegram_stalled_seconds" in explicit_args:
        try:
            eff["telegram"]["stalled_seconds"] = max(60, int(eff.get("telegram_stalled_seconds") or 600))
        except Exception:
            pass

    if eff["policy"].get("enabled") is None:
        eff["policy"]["enabled"] = not bool(eff.get("no_policy_scan", False))
    eff["no_policy_scan"] = not bool(eff["policy"].get("enabled", True))

    # ---- normalize budgets ----
    def _normalize_budgets(raw: Any) -> dict[str, Any]:
        defaults_budgets = defaults.get("budgets", {}) if isinstance(defaults.get("budgets", {}), dict) else {}
        out: dict[str, Any] = dict(defaults_budgets)
        if isinstance(raw, dict):
            out.update(raw)
        for key in list(out.keys()):
            try:
                out[key] = int(out.get(key))
            except Exception:
                out[key] = int(defaults_budgets.get(key) or 0)
        return out

    eff["budgets"] = _normalize_budgets(eff.get("budgets"))

    if "budget_max_pm_structured_retries" in explicit_args:
        eff["budgets"]["max_pm_structured_retries"] = int(eff.get("budget_max_pm_structured_retries") or 0)
    if "budget_max_dev_escalations_per_task" in explicit_args:
        eff["budgets"]["max_dev_escalations_per_task"] = int(eff.get("budget_max_dev_escalations_per_task") or 0)
    if "budget_max_dev_continuations_per_task" in explicit_args:
        eff["budgets"]["max_dev_continuations_per_task"] = int(eff.get("budget_max_dev_continuations_per_task") or 0)
    if "budget_max_total_escalations_per_run" in explicit_args:
        eff["budgets"]["max_total_escalations_per_run"] = int(eff.get("budget_max_total_escalations_per_run") or 0)
    if "budget_max_total_continuations_per_run" in explicit_args:
        eff["budgets"]["max_total_continuations_per_run"] = int(eff.get("budget_max_total_continuations_per_run") or 0)
    if "budget_max_total_repair_attempts_per_run" in explicit_args:
        eff["budgets"]["max_total_repair_attempts_per_run"] = int(eff.get("budget_max_total_repair_attempts_per_run") or 0)

    # ---- apply profile defaults ----
    def _apply_profile() -> None:
        profile = str(eff.get("profile") or "personal").strip().lower()
        if profile != "enterprise":
            eff["profile"] = "personal"
            return

        if "roles" not in explicit_args:
            eff["roles"] = "PM,Security,Dev,QA"
        if "qa_always" not in explicit_args:
            eff["qa_always"] = True
        if "policy_enabled" not in explicit_args and "no_policy_scan" not in explicit_args:
            eff["policy"]["enabled"] = True
        if "security_enabled" not in explicit_args:
            eff["security"]["enabled"] = True

        budgets = eff.get("budgets", {})
        if "budget_max_total_escalations_per_run" not in explicit_args:
            budgets["max_total_escalations_per_run"] = max(int(budgets.get("max_total_escalations_per_run") or 0), 5)
        if "budget_max_total_continuations_per_run" not in explicit_args:
            budgets["max_total_continuations_per_run"] = max(int(budgets.get("max_total_continuations_per_run") or 0), 5)
        if "budget_max_total_repair_attempts_per_run" not in explicit_args:
            budgets["max_total_repair_attempts_per_run"] = max(int(budgets.get("max_total_repair_attempts_per_run") or 0), 3)
        eff["budgets"] = budgets
        eff["profile"] = "enterprise"

    _apply_profile()

    eff["no_policy_scan"] = not bool(eff["policy"].get("enabled", True))

    # normalize execution_backend
    eff["execution_backend"] = _normalize_execution_backend(eff.get("execution_backend", defaults.get("execution_backend", "codex")))
    eff["codex_reasoning_effort"] = _normalize_codex_reasoning_effort(
        eff.get("codex_reasoning_effort", defaults.get("codex_reasoning_effort", ""))
    )

    # normalize failover lists
    fb = eff.get("failover_backends")
    if fb is None:
        eff["failover_backends"] = list(defaults.get("failover_backends", ["codex"]))
    elif isinstance(fb, str):
        eff["failover_backends"] = [p.strip() for p in fb.split(",") if p.strip()]

    fo = eff.get("failover_on")
    if fo is None:
        eff["failover_on"] = list(defaults.get("failover_on", ["quota_exhausted"]))
    elif isinstance(fo, str):
        eff["failover_on"] = [fo]

    raw_failover_max_switches = eff.get("failover_max_switches", None)
    if raw_failover_max_switches is None:
        raw_failover_max_switches = defaults.get("failover_max_switches", 0)
    try:
        eff["failover_max_switches"] = int(raw_failover_max_switches)
    except Exception:
        eff["failover_max_switches"] = int(defaults.get("failover_max_switches", 0))


    # normalize dev_escalate_on (repeatable CLI flag)
    de = eff.get("dev_escalate_on")
    if de is None:
        eff["dev_escalate_on"] = list(defaults.get("dev_escalate_on", []))
    elif isinstance(de, str):
        eff["dev_escalate_on"] = [de]

    # ---- normalize generic gate commands ----
    def _norm_cmd(v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            out = []
            for it in v:
                s = str(it).strip()
                if s:
                    out.append(s)
            return out
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            if "," in s:
                return [p.strip() for p in s.split(",") if p.strip()]
            # fallback: whitespace split
            return [p for p in s.split() if p]
        return []

    eff["build_cmd"] = _norm_cmd(eff.get("build_cmd"))
    eff["test_cmd"] = _norm_cmd(eff.get("test_cmd"))

    # ---- normalize skills config ----
    def _normalize_skills(raw: Any) -> dict[str, Any]:
        defaults_skills = defaults.get("skills", {}) if isinstance(defaults.get("skills", {}), dict) else {}
        out: dict[str, Any] = dict(defaults_skills)
        if isinstance(raw, dict):
            out.update(raw)
        out["enabled"] = bool(out.get("enabled", False))

        roots = out.get("roots") or []
        if isinstance(roots, str):
            roots_list = [p.strip() for p in roots.split(",") if p.strip()]
        elif isinstance(roots, list):
            roots_list = [str(p).strip() for p in roots if str(p).strip()]
        else:
            roots_list = []
        out["roots"] = roots_list

        inline_mode = str(out.get("inline_mode", "qa") or "").strip().lower()
        if inline_mode == "off":
            inline_mode = "none"
        if inline_mode not in {"qa", "pm", "both", "none"}:
            inline_mode = "qa"
        out["inline_mode"] = inline_mode

        try:
            out["max_excerpt_lines"] = max(0, int(out.get("max_excerpt_lines") or 0))
        except Exception:
            out["max_excerpt_lines"] = int(defaults_skills.get("max_excerpt_lines", 12))

        try:
            out["pm_summary_max_items"] = max(0, int(out.get("pm_summary_max_items") or 0))
        except Exception:
            out["pm_summary_max_items"] = int(defaults_skills.get("pm_summary_max_items", 120))

        try:
            out["pm_summary_max_chars"] = max(0, int(out.get("pm_summary_max_chars") or 0))
        except Exception:
            out["pm_summary_max_chars"] = int(defaults_skills.get("pm_summary_max_chars", 8000))

        try:
            out["qa_max_total_chars"] = max(0, int(out.get("qa_max_total_chars") or 0))
        except Exception:
            out["qa_max_total_chars"] = int(defaults_skills.get("qa_max_total_chars", 8000))

        out["skill_match_autofix"] = bool(out.get("skill_match_autofix", False))
        try:
            out["skill_match_autofix_threshold"] = float(out.get("skill_match_autofix_threshold") or 0)
        except Exception:
            out["skill_match_autofix_threshold"] = float(defaults_skills.get("skill_match_autofix_threshold", 0.9))

        out["snapshot_dir"] = str(out.get("snapshot_dir", "") or "")
        return out

    eff["skills"] = _normalize_skills(eff.get("skills"))

    # ---- normalize gitops config ----
    def _normalize_gitops(raw: Any) -> dict[str, Any]:
        defaults_gitops = defaults.get("gitops", {}) if isinstance(defaults.get("gitops", {}), dict) else {}
        out: dict[str, Any] = dict(defaults_gitops)
        if isinstance(raw, dict):
            out.update(raw)
        globs = out.get("untracked_exclude_globs") or []
        if isinstance(globs, str):
            globs_list = [p.strip() for p in globs.split(",") if p.strip()]
        elif isinstance(globs, list):
            globs_list = [str(p).strip() for p in globs if str(p).strip()]
        else:
            globs_list = []
        out["untracked_exclude_globs"] = globs_list
        return out

    eff["gitops"] = _normalize_gitops(eff.get("gitops"))

    # Migration: if generic commands are empty but legacy dotnet targets are set,
    # keep behavior by synthesizing build_cmd/test_cmd. (This does NOT delete dotnet_* keys.)
    if not eff["build_cmd"]:
        bt = str(eff.get("dotnet_build_target", "") or "").strip()
        if bt:
            eff["build_cmd"] = ["dotnet", "build", bt]
    if not eff["test_cmd"]:
        tt = str(eff.get("dotnet_test_target", "") or "").strip()
        tf = str(eff.get("dotnet_test_filter", "") or "").strip()
        if tt or tf:
            cmd = ["dotnet", "test"]
            if tt:
                cmd.append(tt)
            if tf:
                cmd.extend(["--filter", tf])
            eff["test_cmd"] = cmd

    # build_timeout_seconds default fallback
    try:
        eff["build_timeout_seconds"] = int(eff.get("build_timeout_seconds") or defaults.get("build_timeout_seconds") or 1800)
    except Exception:
        eff["build_timeout_seconds"] = int(defaults.get("build_timeout_seconds") or 1800)

    return eff


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    args.repo = str(repo)

    # Resolve config path (python-side default)
    cfg_path = resolve_config_path(repo, args.config)
    legacy_default_path = legacy_default_config_path(repo)
    legacy_path = legacy_config_path(repo)

    cfg: Dict[str, Any] = {}
    # Wizard mode: create/update config and exit
    if args.wizard:
        cfg = run_wizard(repo=repo, defaults=DEFAULTS)
        save_config(cfg_path, cfg)
        print(f"[OK] Wrote config: {cfg_path}")
        # ensure prompts exist in chosen prompts_dir
        pd = resolve_prompts_dir(repo, str(cfg.get("prompts_dir", "")))
        ensure_default_prompt_files(pd)
        raise SystemExit(0)

    # Normal mode: load config (prefer new location, fallback to legacy)
    read_path = cfg_path
    if not read_path.exists():
        if legacy_default_path is not None and legacy_default_path.exists():
            read_path = legacy_default_path
        else:
            read_path = legacy_path
    if read_path.exists():
        try:
            cfg = load_config(read_path)
            if read_path != cfg_path:
                print(f"[INFO] Loaded legacy config: {read_path}")
                print(f"[INFO] Next save/wizard writes to: {cfg_path}")
        except Exception as ex:
            print(f"[WARN] Failed to load config ({read_path}): {ex}")
            cfg = {}

    eff = _merge_effective(DEFAULTS, cfg, args)

    # Normalize config path into args.config (so /config prints the python-side path)
    eff["config"] = str(cfg_path)

    # Normalize prompts_dir to absolute python-side folder
    eff_prompts_dir = resolve_prompts_dir(repo, str(eff.get("prompts_dir", "")))
    eff["prompts_dir"] = str(eff_prompts_dir)

    # If init-prompts requested: create templates and exit
    if bool(eff.get("init_prompts", False)):
        ensure_default_prompt_files(eff_prompts_dir)
        print(f"[OK] Prompt templates ensured at: {eff_prompts_dir}")
        raise SystemExit(0)

    # Write back to args namespace
    out = argparse.Namespace()
    for k, v in eff.items():
        setattr(out, k, v)

    return out
