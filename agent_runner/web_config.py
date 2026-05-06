from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import shutil
from pathlib import Path
from typing import Any

from .cli import DEFAULTS as CLI_DEFAULTS
from .config import (
    builtin_roles,
    load_config,
    normalize_config_list_value,
    normalize_config_value,
    validate_roles_value,
)
from .runtime_contract import (
    CODEX_MODEL_FIELD_SPECS,
    ENTERPRISE_BUDGET_FLOORS,
    PIPELINE_ROLE_FIELD_SPEC,
    enterprise_role_string,
)
from .shared import coerce_roles_arg
from .utils import atomic_write_json


SENSITIVE_CONFIG_TOKENS = {
    "api",
    "apikey",
    "api_key",
    "auth",
    "bearer",
    "bot",
    "chat",
    "client_secret",
    "credential",
    "key",
    "password",
    "secret",
    "session",
    "token",
    "webhook",
}

REDACTED_VALUE = "[redacted]"

CONFIG_CONTRACT_GROUPS: list[dict[str, Any]] = [
    {
        "id": "project",
        "title": "Project",
        "paths": [
            "repo",
            "profile",
            "execution_backend",
            "roles",
            "policy.enabled",
            "policy_scan_scope",
            "security.enabled",
            "security_scan_scope",
        ],
    },
    {
        "id": "runner",
        "title": "Runner",
        "paths": [
            "autopilot",
            "continuous",
            "iterations",
            "max_turns_per_task",
            "loop",
            "loop_sleep_seconds",
            "loop_max_cycles",
            "loop_idle_exit_after",
            "idle_exit_cycles",
            "max_consecutive_failed_cycles",
            "run_tests",
            "budget_reset_per_cycle",
        ],
    },
    {
        "id": "mcp",
        "title": "MCP",
        "paths": ["mcp_mode", "mcp_timeout_seconds", "codex_package"],
    },
    {
        "id": "plugins",
        "title": "Plugin Stages",
        "paths": ["plugins_enabled", "plugins_allowlist", "plugins_strict"],
    },
    {
        "id": "quota",
        "title": "Quota",
        "paths": [
            "quota_check_enabled",
            "quota_five_hour_max_utilization",
            "quota_seven_day_max_utilization",
            "quota_wait_for_reset",
        ],
    },
    {
        "id": "worktree",
        "title": "Worktree",
        "paths": [
            "worktree_isolation",
            "isolate_task",
            "gitops.worktree_merge_mode",
            "gitops.untracked_exclude_globs",
        ],
    },
    {
        "id": "prompts",
        "title": "Prompt Paths",
        "paths": ["prompts_dir"],
    },
    {
        "id": "codex_models",
        "title": "Codex Models",
        "paths": [
            "pm_model",
            "dev_model",
            "dev_model_tier1",
            "dev_model_tier2",
            "qa_model",
            "reporter_model",
        ],
    },
    {
        "id": "claude",
        "title": "Claude Backend",
        "paths": [
            "claudecode_model",
            "claudecode_permission_mode",
            "claudecode_max_turns",
            "claudecode_setting_sources",
            "claudecode_user",
            "claudecode_include_partial_messages",
            "claudecode_fork_session",
            "claudecode_max_thinking_tokens",
            "claudecode_pm_model",
            "claudecode_dev_model",
            "claudecode_dev_model_tier1",
            "claudecode_dev_model_tier2",
            "claudecode_qa_model",
            "claudecode_reporter_model",
            "claudecode_pm_allowed_tools",
            "claudecode_pm_disallowed_tools",
            "claudecode_dev_allowed_tools",
            "claudecode_dev_disallowed_tools",
            "claudecode_qa_allowed_tools",
            "claudecode_qa_disallowed_tools",
            "claudecode_mcp_tools_enabled",
            "claudecode_hooks_enabled",
            "claudecode_can_use_tool_enabled",
            "claudecode_can_use_tool_strict_isolation",
            "claudecode_subagents_enabled",
            "claudecode_subagent_reviewer_enabled",
            "claudecode_subagent_runner_enabled",
            "claudecode_subagent_auditor_enabled",
            "claudecode_subagent_reviewer_model",
            "claudecode_subagent_runner_model",
            "claudecode_subagent_auditor_model",
        ],
    },
    {
        "id": "pm_refresh",
        "title": "PM Refresh",
        "paths": ["pm_refresh_backlog", "pm_refresh_every_cycles", "pm_include_working_tree"],
    },
    {
        "id": "budget",
        "title": "Budget",
        "paths": [
            "budgets.max_pm_structured_retries",
            "budgets.max_dev_escalations_per_task",
            "budgets.max_dev_continuations_per_task",
            "budgets.max_total_escalations_per_run",
            "budgets.max_total_continuations_per_run",
            "budgets.max_total_repair_attempts_per_run",
        ],
    },
    {
        "id": "telegram",
        "title": "Telegram",
        "paths": [
            "telegram.enabled",
            "telegram.runner_mode",
            "telegram.poll_timeout_seconds",
            "telegram.allowed_chat_ids",
            "telegram.bot_token",
            "telegram.pairing_code",
            "telegram.instance_name",
            "telegram.notify_events",
            "telegram.send_cycle_summary",
            "telegram.notify_poll_interval_seconds",
            "telegram.stalled_seconds",
            "telegram.tail_lines_default",
        ],
    },
    {
        "id": "goals",
        "title": "Goals",
        "paths": [
            "goals_enabled",
            "goals_auto_generate",
            "goals_auto_check",
            "goals_auto_refresh",
            "goals_refresh_max_per_run",
            "goals_completion_level",
        ],
    },
    {
        "id": "retention",
        "title": "Retention",
        "paths": [
            "retention.enabled",
            "retention.max_days",
            "retention.max_run_dirs",
            "retention.keep_failed_runs",
            "retention.keep_pending_worktree_runs",
            "retention.prune_logs_over_mb",
            "retention.include_pm_cache",
            "retention.include_logs",
            "retention.include_diagnostics",
            "retention.include_backups",
        ],
    },
]

CONFIG_CONTRACT_FIELDS: list[dict[str, Any]] = [
    {"path": "repo", "group": "project", "kind": "text", "label": "Repository", "restart": True, "allow_empty": False, "desc": "Repository root the runner targets.", "hint": "Set automatically from the repo the server serves."},
    {"path": "profile", "group": "project", "kind": "enum", "label": "Profile", "restart": True, "options": ["personal", "enterprise"], "allow_empty": False, "desc": "Default safety profile used to derive runner limits.", "hint": "Enterprise raises several guardrails."},
    {"path": "execution_backend", "group": "project", "kind": "enum", "label": "Execution backend", "restart": True, "options": ["codex", "claudecode"], "allow_empty": False, "desc": "Backend used for Dev and QA stages.", "hint": "codex = OpenAI Codex CLI, claudecode = Claude Code."},
    PIPELINE_ROLE_FIELD_SPEC,
    {"path": "policy.enabled", "group": "project", "kind": "bool", "label": "Policy scan", "allow_empty": True, "desc": "Enable policy scanning before task execution.", "hint": "Enterprise profile enables policy scanning."},
    {"path": "policy_scan_scope", "group": "project", "kind": "enum", "label": "Policy scan scope", "options": ["", "quick", "staged", "full"], "allow_empty": True, "desc": "Override scan scope for policy checks.", "hint": "Empty inherits scan_scope."},
    {"path": "security.enabled", "group": "project", "kind": "bool", "label": "Security enabled", "allow_empty": True, "desc": "Enable the Security stage in the pipeline.", "hint": "Security stage requires Security in roles."},
    {"path": "security_scan_scope", "group": "project", "kind": "enum", "label": "Security scan scope", "options": ["", "quick", "staged", "full"], "allow_empty": True, "desc": "Override scan scope for Security stage checks.", "hint": "Enterprise profile enables the Security stage and security scan."},
    {"path": "autopilot", "group": "runner", "kind": "bool", "label": "Autopilot", "allow_empty": True, "desc": "Skip interactive confirmation prompts.", "hint": "When off, the runner pauses between stages."},
    {"path": "continuous", "group": "runner", "kind": "bool", "label": "Continuous", "allow_empty": True, "desc": "Keep chaining cycles without manual stopping.", "hint": "Best paired with autopilot for unattended runs."},
    {"path": "iterations", "group": "runner", "kind": "number", "label": "Iterations", "min": 1, "allow_empty": False, "desc": "Maximum run iterations.", "hint": "One iteration equals one PM -> PL -> Dev -> QA cycle."},
    {"path": "max_turns_per_task", "group": "runner", "kind": "number", "label": "Max turns per task", "min": 1, "allow_empty": False, "desc": "Upper bound for per-task model turns.", "hint": "Keeps a single task from spinning forever."},
    {"path": "stop_wait_timeout_seconds", "group": "runner", "kind": "number", "label": "Stop wait timeout seconds", "min": 1, "allow_empty": False, "desc": "How long /stop --wait and web stop wait for runner finalization before reporting timeout.", "hint": "The runner still honors STOP; this only controls the operator wait window."},
    {"path": "loop", "group": "runner", "kind": "bool", "label": "Loop", "allow_empty": True, "desc": "Keep the runner cycling after a run completes.", "hint": "Pair with loop_sleep_seconds to avoid busy looping."},
    {"path": "loop_sleep_seconds", "group": "runner", "kind": "number", "label": "Loop sleep seconds", "min": 1, "allow_empty": False, "desc": "Delay between looped runs.", "hint": "Longer sleeps reduce churn when no work is queued."},
    {"path": "loop_max_cycles", "group": "runner", "kind": "number", "label": "Loop max cycles", "min": 0, "allow_empty": False, "desc": "Hard cap on loop cycles.", "hint": "Zero means no extra cap beyond the rest of the runner."},
    {"path": "loop_idle_exit_after", "group": "runner", "kind": "number", "label": "Loop idle exit after", "min": 0, "allow_empty": False, "desc": "Exit after this many idle loop passes.", "hint": "Zero keeps the loop running until a different stop condition fires."},
    {"path": "idle_exit_cycles", "group": "runner", "kind": "number", "label": "Idle exit cycles", "min": 0, "allow_empty": False, "desc": "How many idle cycles trigger shutdown.", "hint": "Zero keeps idle loop mode alive until a different stop condition fires."},
    {"path": "max_consecutive_failed_cycles", "group": "runner", "kind": "number", "label": "Max consecutive failed cycles", "min": 0, "allow_empty": False, "desc": "Stop after this many failed cycles in a row.", "hint": "Prevents the runner from grinding through repeated failures."},
    {"path": "run_tests", "group": "runner", "kind": "bool", "label": "Run tests", "allow_empty": True, "desc": "Run the test suite during QA.", "hint": "Keeps verification inside the task loop."},
    {"path": "budget_reset_per_cycle", "group": "runner", "kind": "bool", "label": "Budget reset per cycle", "allow_empty": True, "desc": "Reset cycle-level budget tracking every cycle.", "hint": "Useful when cycle-level guardrails matter more than the full run."},
    {"path": "mcp_mode", "group": "mcp", "kind": "enum", "label": "MCP mode", "restart": True, "options": ["npx", "codex", "disabled"], "allow_empty": False, "desc": "Launcher mode for Codex MCP integration.", "hint": "Diagnostics stay non-blocking when the selected launcher is unavailable."},
    {"path": "mcp_timeout_seconds", "group": "mcp", "kind": "number", "label": "MCP timeout seconds", "restart": True, "min": 0, "allow_empty": False, "desc": "Timeout applied to MCP launcher operations.", "hint": "Zero disables waiting beyond immediate command completion."},
    {"path": "codex_package", "group": "mcp", "kind": "text", "label": "Codex package", "restart": True, "allow_empty": False, "desc": "Package passed to npx when MCP mode uses npx.", "hint": "Default is @openai/codex@latest."},
    {"path": "plugins_enabled", "group": "plugins", "kind": "bool", "label": "Plugin stages", "restart": True, "allow_empty": True, "desc": "Enable external plugin stage loading from role specs.", "hint": "Plugin specs must still pass the allowlist."},
    {"path": "plugins_allowlist", "group": "plugins", "kind": "list", "label": "Plugin allowlist", "restart": True, "item_kind": "text", "allow_empty": True, "desc": "Allowed plugin module or module:class patterns.", "hint": "Examples: my_pkg.stages, my_pkg.* or my_pkg.stage:StageClass."},
    {"path": "plugins_strict", "group": "plugins", "kind": "bool", "label": "Plugin strict mode", "restart": True, "allow_empty": True, "desc": "Fail the run when plugin stages are disabled, blocked, missing, or fail to load.", "hint": "When off, affected plugin stages are skipped with diagnostics."},
    {"path": "quota_check_enabled", "group": "quota", "kind": "bool", "label": "Quota checks", "allow_empty": True, "desc": "Enable quota utilization checks.", "hint": "Disabling this removes the quota guardrails from the runner."},
    {"path": "quota_five_hour_max_utilization", "group": "quota", "kind": "number", "label": "5h max utilization", "min": 0, "max": 100, "allow_empty": False, "desc": "Five-hour quota utilization ceiling.", "hint": "Percent used before the runner stops or pauses."},
    {"path": "quota_seven_day_max_utilization", "group": "quota", "kind": "number", "label": "7d max utilization", "min": 0, "max": 100, "allow_empty": False, "desc": "Seven-day quota utilization ceiling.", "hint": "Percent used before the runner stops or pauses."},
    {"path": "quota_wait_for_reset", "group": "quota", "kind": "bool", "label": "Wait for reset", "allow_empty": True, "desc": "Pause until quota resets instead of failing fast.", "hint": "Keeps the runner from hammering an exhausted quota window."},
    {"path": "worktree_isolation", "group": "worktree", "kind": "bool", "label": "Worktree isolation", "restart": True, "allow_empty": True, "desc": "Run tasks in an isolated git worktree.", "hint": "Recommended for shared machines and safety-sensitive changes."},
    {"path": "isolate_task", "group": "worktree", "kind": "bool", "label": "Isolate task", "allow_empty": True, "desc": "Give each task an isolated workspace.", "hint": "Helps keep per-task edits clean when the runner fans out."},
    {"path": "gitops.worktree_merge_mode", "group": "worktree", "kind": "enum", "label": "Merge mode", "restart": True, "options": ["manual", "auto"], "allow_empty": False, "desc": "How worktree patches are merged.", "hint": "Manual mode keeps review in the loop."},
    {"path": "gitops.untracked_exclude_globs", "group": "worktree", "kind": "list", "label": "Untracked exclude globs", "item_kind": "text", "allow_empty": True, "desc": "Comma-separated globs ignored by worktree review.", "hint": "Keep generated files out of merge noise."},
    {"path": "prompts_dir", "group": "prompts", "kind": "text", "label": "Prompts directory", "restart": True, "allow_empty": True, "desc": "Directory that stores repo-specific prompt templates.", "hint": "Empty means the repo-specific default prompts directory."},
    *CODEX_MODEL_FIELD_SPECS,
    {"path": "claudecode_model", "group": "claude", "kind": "text", "label": "Claude model", "restart": True, "allow_empty": False, "desc": "Default Claude model for Claude Code backend stages.", "hint": "Role-specific Claude model fields can override this."},
    {"path": "claudecode_permission_mode", "group": "claude", "kind": "enum", "label": "Permission mode", "restart": True, "options": ["default", "acceptEdits", "bypassPermissions", "plan"], "allow_empty": False, "desc": "Claude Agent SDK permission mode.", "hint": "acceptEdits is the default local-editing mode."},
    {"path": "claudecode_max_turns", "group": "claude", "kind": "number", "label": "Max turns", "restart": True, "min": 1, "allow_empty": False, "desc": "Max turns per Claude query.", "hint": "Use a finite limit to avoid long-running SDK conversations."},
    {"path": "claudecode_setting_sources", "group": "claude", "kind": "multienum", "label": "Setting sources", "restart": True, "options": ["user", "project", "local"], "allow_empty": False, "desc": "Claude settings sources passed to the SDK.", "hint": "Project-only keeps behavior scoped to this repo."},
    {"path": "claudecode_user", "group": "claude", "kind": "text", "label": "Claude user", "restart": True, "allow_empty": True, "desc": "Optional user identifier passed to Claude SDK.", "hint": "Leave blank for SDK default."},
    {"path": "claudecode_include_partial_messages", "group": "claude", "kind": "bool", "label": "Partial messages", "restart": True, "allow_empty": True, "desc": "Include partial streaming message events from Claude SDK.", "hint": "Useful for diagnostics, noisier in logs."},
    {"path": "claudecode_fork_session", "group": "claude", "kind": "bool", "label": "Fork session", "restart": True, "allow_empty": True, "desc": "Fork when resuming a Claude session.", "hint": "Only applies with a resume session id."},
    {"path": "claudecode_max_thinking_tokens", "group": "claude", "kind": "number", "label": "Max thinking tokens", "restart": True, "min": 0, "allow_empty": False, "desc": "Optional Claude thinking-token cap; zero uses SDK default.", "hint": "Use only with SDK/model support."},
    {"path": "claudecode_pm_model", "group": "claude", "kind": "text", "label": "PM Claude model", "restart": True, "allow_empty": True, "desc": "Claude model override for PM stage.", "hint": "Empty falls back to Claude model."},
    {"path": "claudecode_dev_model", "group": "claude", "kind": "text", "label": "Dev Claude model", "restart": True, "allow_empty": True, "desc": "Claude model override for Dev stage.", "hint": "Empty falls back to Claude model."},
    {"path": "claudecode_dev_model_tier1", "group": "claude", "kind": "text", "label": "Dev tier1 Claude model", "restart": True, "allow_empty": True, "desc": "Claude Dev escalation tier1 model.", "hint": "Empty disables this Claude-specific tier."},
    {"path": "claudecode_dev_model_tier2", "group": "claude", "kind": "text", "label": "Dev tier2 Claude model", "restart": True, "allow_empty": True, "desc": "Claude Dev escalation tier2 model.", "hint": "Empty disables this Claude-specific tier."},
    {"path": "claudecode_qa_model", "group": "claude", "kind": "text", "label": "QA Claude model", "restart": True, "allow_empty": True, "desc": "Claude model override for QA stage.", "hint": "Empty falls back to Claude model."},
    {"path": "claudecode_reporter_model", "group": "claude", "kind": "text", "label": "Reporter Claude model", "restart": True, "allow_empty": True, "desc": "Claude model override for Reporter stage.", "hint": "Empty falls back to Claude model."},
    {"path": "claudecode_pm_allowed_tools", "group": "claude", "kind": "list", "label": "PM allowed tools", "restart": True, "item_kind": "text", "allow_empty": True, "desc": "Claude PM stage allowed tools.", "hint": "Comma-separated or array values are accepted."},
    {"path": "claudecode_pm_disallowed_tools", "group": "claude", "kind": "list", "label": "PM disallowed tools", "restart": True, "item_kind": "text", "allow_empty": True, "desc": "Claude PM stage disallowed tools.", "hint": "Use to block specific SDK tools."},
    {"path": "claudecode_dev_allowed_tools", "group": "claude", "kind": "list", "label": "Dev allowed tools", "restart": True, "item_kind": "text", "allow_empty": True, "desc": "Claude Dev stage allowed tools.", "hint": "MCP and Task tools are appended when corresponding advanced controls are active."},
    {"path": "claudecode_dev_disallowed_tools", "group": "claude", "kind": "list", "label": "Dev disallowed tools", "restart": True, "item_kind": "text", "allow_empty": True, "desc": "Claude Dev stage disallowed tools.", "hint": "Use cautiously; blocking Write/Edit can prevent implementation."},
    {"path": "claudecode_qa_allowed_tools", "group": "claude", "kind": "list", "label": "QA allowed tools", "restart": True, "item_kind": "text", "allow_empty": True, "desc": "Claude QA stage allowed tools.", "hint": "QA remains read-only when dynamic permission control is enabled."},
    {"path": "claudecode_qa_disallowed_tools", "group": "claude", "kind": "list", "label": "QA disallowed tools", "restart": True, "item_kind": "text", "allow_empty": True, "desc": "Claude QA stage disallowed tools.", "hint": "Use to further restrict QA."},
    {"path": "claudecode_mcp_tools_enabled", "group": "claude", "kind": "bool", "label": "MCP tools", "restart": True, "allow_empty": True, "desc": "Enable AgentCLI MCP tools for Claude Dev/QA/buildfix stages.", "hint": "Requires SDK support for mcp_servers."},
    {"path": "claudecode_hooks_enabled", "group": "claude", "kind": "bool", "label": "Hooks", "restart": True, "allow_empty": True, "desc": "Enable Claude SDK PreToolUse/PostToolUse hooks.", "hint": "Requires SDK support for hooks."},
    {"path": "claudecode_can_use_tool_enabled", "group": "claude", "kind": "bool", "label": "Dynamic permission", "restart": True, "allow_empty": True, "desc": "Enable Claude SDK can_use_tool permission callback.", "hint": "Required for QA read-only enforcement and strict task isolation."},
    {"path": "claudecode_can_use_tool_strict_isolation", "group": "claude", "kind": "bool", "label": "Strict isolation", "restart": True, "allow_empty": True, "desc": "Warn on Dev edits outside the selected task file set.", "hint": "Only enforced when dynamic permission is enabled."},
    {"path": "claudecode_subagents_enabled", "group": "claude", "kind": "bool", "label": "Subagents", "restart": True, "allow_empty": True, "desc": "Enable Claude SDK subagents for Dev stage.", "hint": "Requires SDK support for agents and Task tool."},
    {"path": "claudecode_subagent_reviewer_enabled", "group": "claude", "kind": "bool", "label": "Reviewer subagent", "restart": True, "allow_empty": True, "desc": "Enable code-reviewer subagent.", "hint": "Read-only reviewer."},
    {"path": "claudecode_subagent_runner_enabled", "group": "claude", "kind": "bool", "label": "Runner subagent", "restart": True, "allow_empty": True, "desc": "Enable test-runner subagent.", "hint": "Can run Bash for verification."},
    {"path": "claudecode_subagent_auditor_enabled", "group": "claude", "kind": "bool", "label": "Auditor subagent", "restart": True, "allow_empty": True, "desc": "Enable security-auditor subagent.", "hint": "Read-only security review."},
    {"path": "claudecode_subagent_reviewer_model", "group": "claude", "kind": "text", "label": "Reviewer model", "restart": True, "allow_empty": True, "desc": "Claude model override for code-reviewer subagent.", "hint": "Empty falls back to QA/default Claude model."},
    {"path": "claudecode_subagent_runner_model", "group": "claude", "kind": "text", "label": "Runner model", "restart": True, "allow_empty": True, "desc": "Claude model override for test-runner subagent.", "hint": "Empty falls back to Dev/default Claude model."},
    {"path": "claudecode_subagent_auditor_model", "group": "claude", "kind": "text", "label": "Auditor model", "restart": True, "allow_empty": True, "desc": "Claude model override for security-auditor subagent.", "hint": "Empty falls back to QA/default Claude model."},
    {"path": "pm_refresh_backlog", "group": "pm_refresh", "kind": "bool", "label": "Refresh backlog", "allow_empty": True, "desc": "Let PM refresh the backlog from live context.", "hint": "Useful when the backlog should absorb new work after a run."},
    {"path": "pm_refresh_every_cycles", "group": "pm_refresh", "kind": "number", "label": "Refresh every cycles", "min": 0, "allow_empty": False, "desc": "Refresh cadence for PM backlog updates.", "hint": "Zero disables periodic refreshes."},
    {"path": "pm_include_working_tree", "group": "pm_refresh", "kind": "bool", "label": "Include working tree", "allow_empty": True, "desc": "Let PM inspect the working tree during refresh.", "hint": "Helps PM pick up local edits while refreshing the backlog."},
    {"path": "budgets.max_pm_structured_retries", "group": "budget", "kind": "number", "label": "PM structured retries", "min": 0, "allow_empty": False, "desc": "Retry cap for structured PM output.", "hint": "Prevents retry loops when PM output keeps failing schema checks."},
    {"path": "budgets.max_dev_escalations_per_task", "group": "budget", "kind": "number", "label": "Dev escalations per task", "min": 0, "allow_empty": False, "desc": "Escalation budget for a single Dev task.", "hint": "Used to cap repeated model escalations."},
    {"path": "budgets.max_dev_continuations_per_task", "group": "budget", "kind": "number", "label": "Dev continuations per task", "min": 0, "allow_empty": False, "desc": "Continuation budget for a single Dev task.", "hint": "Keeps partial response continuations bounded."},
    {"path": "budgets.max_total_escalations_per_run", "group": "budget", "kind": "number", "label": "Total escalations per run", "min": 0, "allow_empty": False, "desc": "Escalation budget for the full run.", "hint": "Set to zero to disable the cap."},
    {"path": "budgets.max_total_continuations_per_run", "group": "budget", "kind": "number", "label": "Total continuations per run", "min": 0, "allow_empty": False, "desc": "Continuation budget for the full run.", "hint": "Set to zero to disable the cap."},
    {"path": "budgets.max_total_repair_attempts_per_run", "group": "budget", "kind": "number", "label": "Total repair attempts", "min": 0, "allow_empty": False, "desc": "Repair budget for the full run.", "hint": "Limits repeated repair loops across stages."},
    {"path": "telegram.enabled", "group": "telegram", "kind": "bool", "label": "Enabled", "restart": True, "allow_empty": True, "desc": "Mirror run events to Telegram.", "hint": "Local notification bridge only."},
    {"path": "telegram.runner_mode", "group": "telegram", "kind": "enum", "label": "Runner mode", "restart": True, "options": ["thread", "subprocess"], "allow_empty": False, "desc": "How the Telegram runner is hosted.", "hint": "Thread mode stays in-process. Subprocess mode isolates the service."},
    {"path": "telegram.poll_timeout_seconds", "group": "telegram", "kind": "number", "label": "Poll timeout seconds", "min": 1, "allow_empty": False, "desc": "Long-poll timeout for Telegram control-plane requests.", "hint": "Longer timeouts reduce polling chatter."},
    {"path": "telegram.allowed_chat_ids", "group": "telegram", "kind": "list", "label": "Allowed chat IDs", "item_kind": "int", "allow_empty": True, "desc": "Comma-separated allowlisted Telegram chat IDs.", "hint": "Empty means any chat id is currently allowed by policy."},
    {"path": "telegram.bot_token", "group": "telegram", "kind": "text", "label": "Bot token", "restart": True, "redacted": True, "allow_empty": True, "desc": "Telegram bot token used for remote control.", "hint": "Shown as redacted in the browser."},
    {"path": "telegram.pairing_code", "group": "telegram", "kind": "text", "label": "Pairing code", "restart": True, "redacted": True, "allow_empty": True, "desc": "One-time pairing code for Telegram control.", "hint": "Shown as redacted in the browser."},
    {"path": "telegram.instance_name", "group": "telegram", "kind": "text", "label": "Instance name", "allow_empty": True, "desc": "Friendly label surfaced in Telegram messages.", "hint": "Useful when multiple runners share one chat."},
    {"path": "telegram.notify_events", "group": "telegram", "kind": "list", "label": "Notify events", "item_kind": "text", "allow_empty": True, "desc": "Comma-separated push events for Telegram notifications.", "hint": "Examples: run_start, task_done, quota."},
    {"path": "telegram.send_cycle_summary", "group": "telegram", "kind": "bool", "label": "Send cycle summary", "allow_empty": True, "desc": "Push new cycle summary lines to Telegram.", "hint": "Helpful when the runner is unattended."},
    {"path": "telegram.notify_poll_interval_seconds", "group": "telegram", "kind": "number", "label": "Notify poll interval", "min": 2, "allow_empty": False, "desc": "Polling interval used by Telegram notification refresh.", "hint": "Longer intervals reduce background polling."},
    {"path": "telegram.stalled_seconds", "group": "telegram", "kind": "number", "label": "Stalled seconds", "min": 60, "allow_empty": False, "desc": "Threshold before a run is considered stalled.", "hint": "Helps identify slow or hung runs."},
    {"path": "telegram.tail_lines_default", "group": "telegram", "kind": "number", "label": "Tail lines default", "min": 1, "allow_empty": False, "desc": "Default number of log lines included in Telegram pushes.", "hint": "Keeps notifications compact."},
    {"path": "goals_enabled", "group": "goals", "kind": "bool", "label": "Goals enabled", "allow_empty": True, "desc": "Enable GOALS.md tracking.", "hint": "Disabling this turns off the goals completion gate."},
    {"path": "goals_auto_generate", "group": "goals", "kind": "bool", "label": "Auto-generate goals", "allow_empty": True, "desc": "Auto-generate goals content from PM context.", "hint": "Useful when goals are derived from the current task set."},
    {"path": "goals_auto_check", "group": "goals", "kind": "bool", "label": "Auto-check goals", "allow_empty": True, "desc": "Re-check goals completion automatically.", "hint": "Keeps completion status in sync with the latest snapshot."},
    {"path": "goals_auto_refresh", "group": "goals", "kind": "bool", "label": "Auto-refresh goals", "allow_empty": True, "desc": "Refresh GOALS.md after project completion.", "hint": "Useful for the next run once the current project is complete."},
    {"path": "goals_refresh_max_per_run", "group": "goals", "kind": "number", "label": "Goals refresh max per run", "min": 0, "allow_empty": False, "desc": "Hard cap on goals refresh attempts per run.", "hint": "Zero disables refresh retries."},
    {"path": "goals_completion_level", "group": "goals", "kind": "enum", "label": "Goals completion level", "options": ["p0", "p1", "all"], "allow_empty": False, "desc": "Which goals must be satisfied to treat the project as complete.", "hint": "p0 is legacy, p1 includes P1, all requires every checkbox."},
    {"path": "retention.enabled", "group": "retention", "kind": "bool", "label": "Retention enabled", "allow_empty": True, "desc": "Enable repo-local dry-run retention reporting.", "hint": "Dry-run reports only; no files are deleted by this setting."},
    {"path": "retention.max_days", "group": "retention", "kind": "number", "label": "Max artifact age days", "min": 0, "allow_empty": False, "desc": "Age threshold for run, cache, log, diagnostic, and backup prune candidates.", "hint": "Zero disables age-based candidates."},
    {"path": "retention.max_run_dirs", "group": "retention", "kind": "number", "label": "Max run directories", "min": 0, "allow_empty": False, "desc": "Keep the newest run directories before older runs become prune candidates.", "hint": "Protected pending review runs are always preserved."},
    {"path": "retention.keep_failed_runs", "group": "retention", "kind": "bool", "label": "Keep failed runs", "allow_empty": True, "desc": "Preserve failed or blocked run directories from prune candidates.", "hint": "Failure artifacts are often needed for follow-up."},
    {"path": "retention.keep_pending_worktree_runs", "group": "retention", "kind": "bool", "label": "Keep pending worktree runs", "allow_empty": True, "desc": "Preserve pending worktree review, cleanup-failed, and queued PR packet evidence.", "hint": "This prevents retention from deleting operator review state."},
    {"path": "retention.prune_logs_over_mb", "group": "retention", "kind": "number", "label": "Prune logs over MB", "min": 0, "allow_empty": False, "desc": "Large log files above this size become dry-run prune candidates.", "hint": "Zero disables size-based log candidates."},
    {"path": "retention.include_pm_cache", "group": "retention", "kind": "bool", "label": "Include PM cache", "allow_empty": True, "desc": "Include .AgentCLI/PM_CACHE files in dry-run retention reports.", "hint": "Cache candidates are reported, not deleted."},
    {"path": "retention.include_logs", "group": "retention", "kind": "bool", "label": "Include logs", "allow_empty": True, "desc": "Include runtime logs and dev logs in dry-run retention reports.", "hint": "Logs under protected runs inherit the run protection."},
    {"path": "retention.include_diagnostics", "group": "retention", "kind": "bool", "label": "Include diagnostics", "allow_empty": True, "desc": "Include diagnostic artifacts in dry-run retention reports.", "hint": "Diagnostics under protected runs inherit the run protection."},
    {"path": "retention.include_backups", "group": "retention", "kind": "bool", "label": "Include backups", "allow_empty": True, "desc": "Include local backup files in dry-run retention reports.", "hint": "Config, prompt, and GOALS backups are reported when stale."},
]


@dataclass
class ConfigMutationError:
    status_code: int
    code: str
    message: str
    details: dict[str, Any]


@dataclass
class ConfigSaveResult:
    updated_raw: dict[str, Any]
    backup_path: Path
    changed_paths: list[str]
    reload_required_paths: list[str]


@dataclass
class ConfigRestoreResult:
    restored_raw: dict[str, Any]
    backup_path: Path
    restored_from: Path


def _is_sensitive_config_key(key: Any) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key or "").strip().lower()).strip("_")
    if not normalized:
        return False
    parts = {part for part in normalized.split("_") if part}
    if "pairing" in parts and "code" in parts:
        return True
    return normalized in SENSITIVE_CONFIG_TOKENS or bool(parts & SENSITIVE_CONFIG_TOKENS)


def _redact_config(value: Any, *, key: str = "") -> Any:
    if _is_sensitive_config_key(key):
        if isinstance(value, bool) or value in (None, ""):
            return value
        return REDACTED_VALUE
    if isinstance(value, dict):
        return {str(item_key): _redact_config(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_redact_config(item) for item in value]
    return value


def _config_path_get(tree: Any, path: str) -> Any:
    current = tree
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        if part not in current:
            return None
        current = current[part]
    return current


def _config_has_path(tree: Any, path: str) -> bool:
    current = tree
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _config_path_set(tree: dict[str, Any], path: str, value: Any) -> None:
    current = tree
    parts = path.split(".")
    for part in parts[:-1]:
        next_value = current.get(part)
        if not isinstance(next_value, dict):
            next_value = {}
            current[part] = next_value
        current = next_value
    current[parts[-1]] = value


def _merge_config_tree(base: Any, overlay: Any) -> Any:
    if not isinstance(base, dict):
        return deepcopy(overlay)
    result = deepcopy(base)
    if not isinstance(overlay, dict):
        return result
    for key, value in overlay.items():
        if isinstance(result.get(key), dict) and isinstance(value, dict):
            result[key] = _merge_config_tree(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _normalize_config_list(value: Any, *, item_kind: str = "text") -> list[Any]:
    return normalize_config_list_value(value, item_kind=item_kind)


def _normalize_config_contract_value(value: Any, spec: dict[str, Any]) -> Any:
    return normalize_config_value(value, spec, str(spec.get("path") or ""))


def _apply_enterprise_profile_defaults(normalized: dict[str, Any], raw_cfg: dict[str, Any]) -> dict[str, Any]:
    profile = str(normalized.get("profile") or "personal").strip().lower()
    if profile != "enterprise":
        normalized["profile"] = "personal"
        return normalized

    if not _config_has_path(raw_cfg, "roles"):
        normalized["roles"] = enterprise_role_string()
    policy = normalized.get("policy") if isinstance(normalized.get("policy"), dict) else {}
    policy["enabled"] = True
    normalized["policy"] = policy
    normalized["no_policy_scan"] = False

    security = normalized.get("security") if isinstance(normalized.get("security"), dict) else {}
    security["enabled"] = True
    normalized["security"] = security

    budgets = normalized.get("budgets") if isinstance(normalized.get("budgets"), dict) else {}
    for key, floor in ENTERPRISE_BUDGET_FLOORS.items():
        try:
            current = int(budgets.get(key) or 0)
        except Exception:
            current = 0
        budgets[key] = max(current, int(floor))
    normalized["budgets"] = budgets
    normalized["profile"] = "enterprise"
    return normalized


def _build_profile_effective_payload(raw_cfg: dict[str, Any], normalized_launch: dict[str, Any]) -> dict[str, Any]:
    profile = str(normalized_launch.get("profile") or "personal").strip().lower() or "personal"
    roles_value = normalize_config_list_value(normalized_launch.get("roles"), item_kind="text")
    policy = normalized_launch.get("policy") if isinstance(normalized_launch.get("policy"), dict) else {}
    security = normalized_launch.get("security") if isinstance(normalized_launch.get("security"), dict) else {}
    budgets = normalized_launch.get("budgets") if isinstance(normalized_launch.get("budgets"), dict) else {}
    budget_floors = {key: int(ENTERPRISE_BUDGET_FLOORS[key]) for key in ENTERPRISE_BUDGET_FLOORS}
    budget_values = {key: int(budgets.get(key) or 0) for key in budget_floors}
    enterprise = profile == "enterprise"
    payload = {
        "profile": profile,
        "enterprise": enterprise,
        "roles": roles_value,
        "security_stage_inserted": bool(enterprise and not _config_has_path(raw_cfg, "roles") and "Security" in roles_value),
        "securityStageInserted": bool(enterprise and not _config_has_path(raw_cfg, "roles") and "Security" in roles_value),
        "policy_enabled": bool(policy.get("enabled", False)),
        "policyEnabled": bool(policy.get("enabled", False)),
        "security_enabled": bool(security.get("enabled", False)),
        "securityEnabled": bool(security.get("enabled", False)),
        "budget_floors": budget_floors,
        "budgetFloors": budget_floors,
        "budget_values": budget_values,
        "budgetValues": budget_values,
        "budget_floor_enforced": bool(enterprise and all(budget_values[key] >= floor for key, floor in budget_floors.items())),
        "budgetFloorEnforced": bool(enterprise and all(budget_values[key] >= floor for key, floor in budget_floors.items())),
    }
    return payload


def _normalize_config_for_launch(cfg: dict[str, Any]) -> dict[str, Any]:
    normalized = _merge_config_tree(CLI_DEFAULTS, cfg)
    if not isinstance(normalized, dict):
        normalized = {}
    for spec in CONFIG_CONTRACT_FIELDS:
        path = str(spec.get("path") or "")
        if not path:
            continue
        if path == "policy.enabled" and not _config_has_path(cfg, path):
            _config_path_set(normalized, path, not bool(normalized.get("no_policy_scan", False)))
        if path == "policy.enabled":
            normalized["no_policy_scan"] = not bool(_config_path_get(normalized, path))
        _config_path_set(normalized, path, normalize_config_value(_config_path_get(normalized, path), spec, path))
    for path in (
        "gitops.untracked_exclude_globs",
        "plugins_allowlist",
        "policy.ignore_paths",
        "policy.allow_patterns",
        "scan_ignore_globs",
        "scan_ignore_paths",
        "failover_backends",
        "failover_on",
        "dev_escalate_on",
    ):
        current = _config_path_get(normalized, path)
        if current is None:
            continue
        _config_path_set(normalized, path, normalize_config_list_value(current, item_kind="text"))
    normalized["roles"] = coerce_roles_arg(normalized.get("roles"))
    normalized = _apply_enterprise_profile_defaults(normalized, cfg if isinstance(cfg, dict) else {})
    return normalized


def _fmt_mtime(value: float) -> str:
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "template"


def _build_config_contract(
    repo_root: Path,
    cfg: dict[str, Any],
    cfg_path: Path,
    cfg_source: str,
    prompts_dir: Path,
    *,
    save_enabled: bool = False,
    save_endpoint: str = "/api/config/save",
    save_requires_opt_in: bool = True,
    restore_enabled: bool | None = None,
    restore_endpoint: str = "/api/config/restore",
    restore_requires_opt_in: bool = True,
) -> dict[str, Any]:
    effective_cfg = _merge_config_tree(CLI_DEFAULTS, cfg)
    effective_cfg["repo"] = repo_root.as_posix()
    default_cfg = _merge_config_tree(CLI_DEFAULTS, {})
    default_cfg["repo"] = ""

    values: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    schema: dict[str, Any] = {}
    restart_required_paths: list[str] = []
    redacted_paths: list[str] = []

    for spec in CONFIG_CONTRACT_FIELDS:
        path = str(spec["path"])
        field_schema = {
            "path": path,
            "kind": spec.get("kind", "text"),
            "label": spec.get("label", path.rsplit(".", 1)[-1].replace("_", " ").title()),
            "group": spec.get("group", ""),
            "desc": spec.get("desc", ""),
            "hint": spec.get("hint", ""),
            "restart": bool(spec.get("restart", False)),
            "editable": bool(spec.get("editable", True)),
            "redacted": bool(spec.get("redacted", False)),
            "allow_empty": bool(spec.get("allow_empty", False)),
        }
        if spec.get("options") is not None:
            field_schema["options"] = list(spec["options"])
        if spec.get("min") is not None:
            field_schema["min"] = spec["min"]
        if spec.get("max") is not None:
            field_schema["max"] = spec["max"]
        if spec.get("step") is not None:
            field_schema["step"] = spec["step"]
        if spec.get("item_kind") is not None:
            field_schema["item_kind"] = spec["item_kind"]

        raw_value = _config_path_get(effective_cfg, path)
        raw_default = _config_path_get(default_cfg, path)
        if path == "policy.enabled":
            if not _config_has_path(cfg, path):
                raw_value = not bool(effective_cfg.get("no_policy_scan", False))
            raw_default = not bool(default_cfg.get("no_policy_scan", False))
        normalized_value = _normalize_config_contract_value(raw_value, spec)
        normalized_default = _normalize_config_contract_value(raw_default, spec)
        _config_path_set(values, path, normalized_value)
        _config_path_set(defaults, path, normalized_default)
        schema[path] = field_schema
        if field_schema["restart"]:
            restart_required_paths.append(path)
        if field_schema["redacted"] or _is_sensitive_config_key(path):
            redacted_paths.append(path)

    redaction = {
        "placeholder": REDACTED_VALUE,
        "paths": list(dict.fromkeys(redacted_paths)),
        "tokens": sorted(SENSITIVE_CONFIG_TOKENS),
    }
    restart_required_paths = list(dict.fromkeys(restart_required_paths))
    redacted_values = _redact_config(values)
    redacted_defaults = _redact_config(defaults)
    backups = _config_backup_candidates(cfg_path)
    for path in redaction["paths"]:
        if _config_path_get(redacted_values, path) not in (None, "", False):
            _config_path_set(redacted_values, path, REDACTED_VALUE)
        if _config_path_get(redacted_defaults, path) not in (None, "", False):
            _config_path_set(redacted_defaults, path, REDACTED_VALUE)
    profile_effective = _build_profile_effective_payload(cfg if isinstance(cfg, dict) else {}, _normalize_config_for_launch(cfg))

    return {
        "path": cfg_path.as_posix(),
        "source": cfg_source,
        "resolved_prompts_dir": prompts_dir.as_posix(),
        "values": redacted_values,
        "defaults": redacted_defaults,
        "schema": schema,
        "groups": CONFIG_CONTRACT_GROUPS,
        "redaction": redaction,
        "restart_required_paths": restart_required_paths,
        "backups": backups,
        "profile_effective": profile_effective,
        "profileEffective": profile_effective,
        "meta": {
            "path": cfg_path.as_posix(),
            "source": cfg_source,
            "resolved_prompts_dir": prompts_dir.as_posix(),
            "save_enabled": bool(save_enabled),
            "save_endpoint": str(save_endpoint or "/api/config/save"),
            "save_requires_opt_in": bool(save_requires_opt_in),
            "restore_enabled": bool(save_enabled if restore_enabled is None else restore_enabled),
            "restore_endpoint": str(restore_endpoint or "/api/config/restore"),
            "restore_requires_opt_in": bool(restore_requires_opt_in),
        },
    }


def _config_save_backup_path(cfg_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return cfg_path.with_name(f"{cfg_path.stem}.{stamp}.bak{cfg_path.suffix}")


def _config_backup_candidates(cfg_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    pattern = f"{cfg_path.stem}.*.bak{cfg_path.suffix}"
    try:
        parent = cfg_path.parent
        if parent.exists() and parent.is_dir():
            for candidate in sorted(
                [path for path in parent.glob(pattern) if path.is_file()],
                key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
                reverse=True,
            )[: max(0, int(limit)) or 0]:
                try:
                    stats = candidate.stat()
                except Exception:
                    continue
                candidates.append(
                    {
                        "path": candidate.as_posix(),
                        "name": candidate.name,
                        "updated": _fmt_mtime(stats.st_mtime),
                        "size": stats.st_size,
                        "summary": f"{_fmt_mtime(stats.st_mtime)} | {stats.st_size} bytes",
                    }
                )
    except Exception:
        return []
    return candidates


def _config_resolve_backup_selection(
    cfg_path: Path,
    requested_backup_path: Any,
    *,
    backups: list[dict[str, Any]] | None = None,
) -> tuple[Path | None, ConfigMutationError | None]:
    requested = str(requested_backup_path or "").strip()
    if not requested:
        return None, ConfigMutationError(400, "config_backup_path_required", "A backup path is required.", {"field": "backup_path"})

    candidate = Path(requested.replace("\\", "/")).expanduser()
    resolved = candidate.resolve() if candidate.is_absolute() else (cfg_path.parent / candidate).resolve()

    try:
        resolved.relative_to(cfg_path.parent)
    except Exception:
        return (
            None,
            ConfigMutationError(
                400,
                "config_backup_path_outside_config_dir",
                "Config backup must stay within the config directory.",
                {"path": resolved.as_posix(), "config_path": cfg_path.as_posix()},
            ),
        )

    expected_pattern = f"{cfg_path.stem}.*.bak{cfg_path.suffix}"
    if not resolved.name.startswith(f"{cfg_path.stem}.") or not resolved.name.endswith(f".bak{cfg_path.suffix}"):
        return (
            None,
            ConfigMutationError(
                400,
                "config_backup_not_found",
                "The selected backup file is not available for this config path.",
                {"path": resolved.as_posix(), "config_path": cfg_path.as_posix(), "expected_pattern": expected_pattern},
            ),
        )

    discovered = backups if backups is not None else _config_backup_candidates(cfg_path)
    discovered_paths = {str(item.get("path") or "") for item in discovered if isinstance(item, dict)}
    if resolved.as_posix() not in discovered_paths:
        return (
            None,
            ConfigMutationError(
                404,
                "config_backup_not_found",
                "The selected backup file is not available for this config path.",
                {"path": resolved.as_posix(), "config_path": cfg_path.as_posix(), "expected_pattern": expected_pattern},
            ),
        )

    if not resolved.is_file():
        return (
            None,
            ConfigMutationError(
                404,
                "config_backup_not_found",
                "The selected backup file was not found.",
                {"path": resolved.as_posix(), "config_path": cfg_path.as_posix()},
            ),
        )

    return resolved, None


def _config_save_validate_change(path: str, raw_value: Any, schema: dict[str, Any], current_value: Any) -> tuple[Any, str, dict[str, Any]]:
    kind = str(schema.get("kind") or "text")
    allow_empty = bool(schema.get("allow_empty", False))

    if path == "repo" and raw_value != current_value:
        return raw_value, "config_path_unsafe", {"path": path, "reason": "Repository root is managed by the server."}

    if bool(schema.get("redacted")) and raw_value == REDACTED_VALUE and raw_value != current_value:
        return raw_value, "config_redacted_placeholder", {"path": path, "placeholder": REDACTED_VALUE}

    if kind == "bool":
        if isinstance(raw_value, bool):
            return raw_value, "", {}
        return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "boolean"}

    if kind == "number":
        if raw_value in (None, ""):
            if allow_empty:
                return "", "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        if isinstance(raw_value, bool):
            return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "number"}
        if isinstance(raw_value, (int, float)):
            number: int | float = raw_value
        elif isinstance(raw_value, str):
            text = raw_value.strip()
            if not text:
                return raw_value, "config_value_required", {"path": path, "kind": kind}
            try:
                number = int(text)
            except Exception:
                try:
                    number = float(text)
                except Exception:
                    return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "number"}
        else:
            return raw_value, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": "number"}
        if isinstance(number, float) and number.is_integer():
            number = int(number)
        min_value = schema.get("min")
        max_value = schema.get("max")
        if min_value is not None and number < min_value:
            return number, "config_value_out_of_range", {"path": path, "kind": kind, "min": min_value}
        if max_value is not None and number > max_value:
            return number, "config_value_out_of_range", {"path": path, "kind": kind, "max": max_value}
        return number, "", {}

    if kind == "text":
        if raw_value in (None, ""):
            if allow_empty:
                return "", "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        if not isinstance(raw_value, str):
            raw_value = str(raw_value)
        if not raw_value.strip() and not allow_empty:
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        return raw_value, "", {}

    if kind == "enum":
        options = [str(option) for option in schema.get("options") or []]
        if raw_value in (None, ""):
            if allow_empty:
                return "", "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        if not isinstance(raw_value, str):
            raw_value = str(raw_value)
        if raw_value not in options:
            return raw_value, "config_value_invalid_choice", {"path": path, "kind": kind, "options": options}
        return raw_value, "", {}

    if kind == "multienum":
        if path == "roles":
            items, invalid = validate_roles_value(raw_value)
            if not items:
                if allow_empty:
                    return [], "", {}
                return raw_value, "config_value_required", {"path": path, "kind": kind}
            if invalid:
                return items, "config_value_invalid_choice", {
                    "path": path,
                    "kind": kind,
                    "invalid": invalid,
                    "options": builtin_roles(),
                }
            return items, "", {}
        options = [str(option) for option in schema.get("options") or []]
        items = raw_value if isinstance(raw_value, list) else _normalize_config_list(raw_value, item_kind="text")
        items = [str(item) for item in items if str(item).strip()]
        if not items:
            if allow_empty:
                return [], "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        invalid = [item for item in items if item not in options]
        if invalid:
            return items, "config_value_invalid_choice", {"path": path, "kind": kind, "invalid": invalid, "options": options}
        return items, "", {}

    if kind == "list":
        item_kind = str(schema.get("item_kind") or "text")
        items = raw_value if isinstance(raw_value, list) else _normalize_config_list(raw_value, item_kind=item_kind)
        if not items:
            if allow_empty:
                return [], "", {}
            return raw_value, "config_value_required", {"path": path, "kind": kind}
        normalized_items: list[Any] = []
        if item_kind in {"int", "number"}:
            for item in items:
                if isinstance(item, bool):
                    return items, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": item_kind}
                if isinstance(item, (int, float)):
                    number = int(item) if float(item).is_integer() else item
                elif isinstance(item, str):
                    text = item.strip()
                    if not text:
                        return items, "config_value_required", {"path": path, "kind": kind}
                    try:
                        number = int(text)
                    except Exception:
                        try:
                            number = float(text)
                        except Exception:
                            return items, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": item_kind}
                else:
                    return items, "config_value_type_mismatch", {"path": path, "kind": kind, "expected": item_kind}
                if isinstance(number, float) and number.is_integer():
                    number = int(number)
                normalized_items.append(number)
            return normalized_items, "", {}
        normalized_items = [str(item) for item in items]
        return normalized_items, "", {}

    return raw_value, "", {}


def _config_validation_error(validation_errors: list[dict[str, Any]]) -> ConfigMutationError:
    validation_payload = {
        "error_count": len(validation_errors),
        "errors": validation_errors,
    }
    if len(validation_errors) == 1:
        first_error = validation_errors[0]
        details = dict(first_error.get("details") or {})
        details["field"] = first_error.get("field")
        details["validation"] = validation_payload
        return ConfigMutationError(
            400,
            str(first_error.get("code") or "config_validation_failed"),
            str(first_error.get("message") or "Config save payload is not valid for this field."),
            details,
        )
    return ConfigMutationError(
        400,
        "config_validation_failed",
        "One or more config changes were rejected.",
        {"validation": validation_payload},
    )


def _config_save_changes(
    cfg_path: Path,
    raw_changes: Any,
    *,
    schema: dict[str, Any],
    restart_required_paths: set[str] | list[str],
) -> tuple[ConfigSaveResult | None, ConfigMutationError | None]:
    if isinstance(raw_changes, dict):
        raw_changes = [{"path": key, "value": value} for key, value in raw_changes.items()]
    if not isinstance(raw_changes, list):
        return (
            None,
            ConfigMutationError(
                400,
                "config_changes_required",
                "Config save request must include a changes array.",
                {"field": "changes"},
            ),
        )

    try:
        current_raw = load_config(cfg_path)
    except Exception as ex:
        return (
            None,
            ConfigMutationError(
                400,
                "config_read_error",
                "Existing config file could not be read.",
                {"path": cfg_path.as_posix(), "error": str(ex).strip() or ex.__class__.__name__},
            ),
        )
    if not isinstance(current_raw, dict):
        return None, ConfigMutationError(400, "config_read_error", "Existing config file could not be read.", {"path": cfg_path.as_posix()})

    updated_raw = deepcopy(current_raw)
    changed_paths: list[str] = []
    reload_required: list[str] = []
    validation_errors: list[dict[str, Any]] = []
    restart_required_lookup = {str(path) for path in restart_required_paths if str(path).strip()}

    for index, entry in enumerate(raw_changes):
        if not isinstance(entry, dict):
            validation_errors.append(
                {
                    "field": "changes",
                    "code": "config_change_invalid",
                    "message": "Each config change must be an object.",
                    "details": {"index": index},
                }
            )
            continue
        path = str(entry.get("path") or entry.get("field") or entry.get("name") or "").strip()
        if not path:
            validation_errors.append(
                {
                    "field": "changes",
                    "code": "config_path_required",
                    "message": "Each config change must include a path.",
                    "details": {"index": index},
                }
            )
            continue
        field_schema = schema.get(path)
        if not isinstance(field_schema, dict):
            validation_errors.append(
                {
                    "field": path,
                    "code": "config_unknown_path",
                    "message": "Config field is not part of the save schema.",
                    "details": {"path": path},
                }
            )
            continue
        if not bool(field_schema.get("editable", True)):
            validation_errors.append(
                {
                    "field": path,
                    "code": "config_field_not_editable",
                    "message": "Config field cannot be edited.",
                    "details": {"path": path},
                }
            )
            continue

        raw_value = entry.get("value")
        if "value" not in entry and "to" in entry:
            raw_value = entry.get("to")
        if "value" not in entry and "to" not in entry and "next" in entry:
            raw_value = entry.get("next")

        current_value = _config_path_get(current_raw, path)
        normalized_value, error_code, error_details = _config_save_validate_change(path, raw_value, field_schema, current_value)
        if error_code:
            validation_errors.append(
                {
                    "field": path,
                    "code": error_code,
                    "message": "Config save payload is not valid for this field.",
                    "details": error_details,
                }
            )
            continue
        if normalized_value == current_value:
            continue
        _config_path_set(updated_raw, path, normalized_value)
        changed_paths.append(path)
        if path in restart_required_lookup or bool(field_schema.get("restart", False)):
            reload_required.append(path)

    if validation_errors:
        return None, _config_validation_error(validation_errors)

    changed_paths = list(dict.fromkeys(changed_paths))
    reload_required = list(dict.fromkeys(reload_required))
    if not changed_paths:
        return None, ConfigMutationError(400, "config_no_changes", "No config changes were supplied.", {})

    # Session-only run selection intent should never persist in config.
    updated_raw.pop("run_dir", None)
    updated_raw.pop("resume_latest", None)

    backup_path = _config_save_backup_path(cfg_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        shutil.copy2(cfg_path, backup_path)
    else:
        atomic_write_json(backup_path, current_raw)

    atomic_write_json(cfg_path, updated_raw)
    return ConfigSaveResult(updated_raw, backup_path, changed_paths, reload_required), None


def _config_restore_backup(
    cfg_path: Path,
    requested_backup_path: Any,
    confirmation: str,
    *,
    confirmation_phrase: str,
) -> tuple[ConfigRestoreResult | None, ConfigMutationError | None]:
    if not str(requested_backup_path or "").strip():
        return None, ConfigMutationError(400, "config_backup_path_required", "A backup path is required.", {"field": "backup_path"})
    if not confirmation:
        return (
            None,
            ConfigMutationError(
                400,
                "config_restore_confirmation_required",
                "A restore confirmation phrase is required.",
                {"field": "confirm", "confirmation_phrase": confirmation_phrase},
            ),
        )
    if confirmation != confirmation_phrase:
        return (
            None,
            ConfigMutationError(
                400,
                "config_restore_confirmation_mismatch",
                "The restore confirmation phrase did not match.",
                {"field": "confirm", "confirmation_phrase": confirmation_phrase},
            ),
        )

    backups = _config_backup_candidates(cfg_path)
    restored_from, selection_error = _config_resolve_backup_selection(cfg_path, requested_backup_path, backups=backups)
    if selection_error is not None or restored_from is None:
        return None, selection_error or ConfigMutationError(404, "config_backup_not_found", "The selected backup file is not available for this config path.", {})

    try:
        current_raw = load_config(cfg_path)
    except Exception as ex:
        return (
            None,
            ConfigMutationError(
                400,
                "config_read_error",
                "Existing config file could not be read.",
                {"path": cfg_path.as_posix(), "error": str(ex).strip() or ex.__class__.__name__},
            ),
        )
    if not isinstance(current_raw, dict):
        return None, ConfigMutationError(400, "config_read_error", "Existing config file could not be read.", {"path": cfg_path.as_posix()})

    restored_from_path = restored_from.as_posix()
    try:
        restored_raw = load_config(restored_from)
    except Exception as ex:
        return (
            None,
            ConfigMutationError(
                400,
                "config_backup_invalid_json",
                "The selected backup file could not be parsed as JSON.",
                {"path": restored_from_path, "error": str(ex).strip() or ex.__class__.__name__},
            ),
        )
    if not isinstance(restored_raw, dict):
        return None, ConfigMutationError(400, "config_backup_invalid_json", "The selected backup file could not be parsed as JSON.", {"path": restored_from_path})

    backup_path = _config_save_backup_path(cfg_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg_path.exists():
        shutil.copy2(cfg_path, backup_path)
    else:
        atomic_write_json(backup_path, current_raw)

    atomic_write_json(cfg_path, restored_raw)
    post_restore_raw = load_config(cfg_path)
    if not isinstance(post_restore_raw, dict) or post_restore_raw != restored_raw:
        return (
            None,
            ConfigMutationError(
                500,
                "config_restore_validation_failed",
                "Config restore could not be validated after writing.",
                {
                    "path": cfg_path.as_posix(),
                    "restored_from_path": restored_from_path,
                    "backup_path": backup_path.as_posix(),
                },
            ),
        )

    return ConfigRestoreResult(restored_raw, backup_path, restored_from), None
