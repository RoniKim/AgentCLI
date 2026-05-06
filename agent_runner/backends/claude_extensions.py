"""Claude Agent SDK Extensions — MCP Tools, Hooks, can_use_tool, Subagents.

All features are **opt-in** (disabled by default) and controlled via
`ClaudeCodeConfig` fields set from CLI flags / config JSON.

Usage inside claudecode.py:
    from .claude_extensions import ClaudeExtensionContext, apply_extensions
    ext_ctx = ClaudeExtensionContext(...)
    apply_extensions(ext_ctx, cfg, kwargs, stage)
"""
from __future__ import annotations

import argparse
import inspect
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Runtime context — passed around as a single object
# ---------------------------------------------------------------------------

@dataclass
class ClaudeExtensionContext:
    """Mutable context shared across all extension factories."""
    repo: Path
    run_dir: Path
    stop_path: Path
    logger: Any               # StructuredLogger
    metrics: Any              # MetricsLogger
    args: argparse.Namespace
    debug: bool
    policy_rules: list        # load_policy_rules() result

    # Mutable per-stage state (updated before each query)
    current_stage: str = ""
    current_task_id: str = ""
    current_task_files: list[str] = field(default_factory=list)

    # Cached extension objects (built once, reused across queries)
    _cached_mcp_server: Any = field(default=None, repr=False)
    _cached_hooks: Any = field(default=None, repr=False)
    _cached_can_use_tool: Any = field(default=None, repr=False)


# ---------------------------------------------------------------------------
# MCP tool response helper
# ---------------------------------------------------------------------------

def _text_response(text: str) -> dict:
    """Format a text string as an MCP tool response."""
    return {"content": [{"type": "text", "text": text}]}


# ---------------------------------------------------------------------------
# Phase 2: Custom MCP Tools
# ---------------------------------------------------------------------------

def build_mcp_server(ctx: ClaudeExtensionContext) -> Any:
    """Build an SDK MCP server exposing AgentCLI pipeline tools.

    Tools: check_state, load_backlog, run_build, run_tests, git_status, query_events

    SDK API reference:
        @tool(name, description, params_dict)  — params_dict is {param_name: type}
        Return value: {"content": [{"type": "text", "text": "..."}]}
    """
    try:
        from claude_agent_sdk import tool, create_sdk_mcp_server
    except ImportError:
        return None

    # -- Tool implementations --

    @tool("check_state", "Read STATE.json (done/failed task tracking)", {})
    async def check_state(args: dict) -> dict:
        from ..state import load_state
        state_path = ctx.run_dir / "STATE.json"
        if not state_path.exists():
            return _text_response(json.dumps({"done": [], "failed": []}))
        state = load_state(state_path)
        return _text_response(json.dumps(state, ensure_ascii=False, indent=2))

    @tool("load_backlog", "Read BACKLOG.json task list", {})
    async def load_backlog(args: dict) -> dict:
        from ..state import load_backlog_json
        bl_path = ctx.run_dir / "BACKLOG.json"
        if not bl_path.exists():
            return _text_response(json.dumps({"tasks": []}))
        tasks = load_backlog_json(bl_path)
        return _text_response(json.dumps(
            [{"id": t.id, "title": t.title, "files": t.files, "done_when": t.done_when} for t in tasks],
            ensure_ascii=False, indent=2,
        ))

    @tool("run_build", "Execute the build gate and return pass/fail", {})
    async def run_build(args_dict: dict) -> dict:
        from ..gates import run_build_gate_async
        log_path = ctx.run_dir / "mcp_build.txt"
        ok = await run_build_gate_async(
            repo=ctx.repo,
            build_cmd=getattr(ctx.args, "build_cmd", []),
            build_timeout_sec=int(getattr(ctx.args, "build_timeout_seconds", 1800)),
            legacy_build_target=str(getattr(ctx.args, "dotnet_build_target", "") or ""),
            log_path=log_path,
            stop_path=ctx.stop_path,
        )
        output = ""
        if log_path.exists():
            output = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        return _text_response(json.dumps({"passed": ok, "output_tail": output}))

    @tool("run_tests", "Execute the test gate and return pass/fail", {})
    async def run_tests(args_dict: dict) -> dict:
        from ..gates import run_test_gate_async
        log_path = ctx.run_dir / "mcp_test.txt"
        ok = await run_test_gate_async(
            repo=ctx.repo,
            test_cmd=getattr(ctx.args, "test_cmd", []),
            test_timeout_sec=int(getattr(ctx.args, "test_timeout_seconds", 3600)),
            legacy_test_target=str(getattr(ctx.args, "dotnet_test_target", "") or ""),
            legacy_test_filter=str(getattr(ctx.args, "dotnet_test_filter", "") or ""),
            log_path=log_path,
            stop_path=ctx.stop_path,
        )
        output = ""
        if log_path.exists():
            output = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
        return _text_response(json.dumps({"passed": ok, "output_tail": output}))

    @tool("git_status", "Get git HEAD, porcelain status, and changed files", {})
    async def git_status(args_dict: dict) -> dict:
        from ..gitops import git_head, git_porcelain, git_untracked_files
        return _text_response(json.dumps({
            "head": git_head(ctx.repo),
            "porcelain": git_porcelain(ctx.repo),
            "untracked": git_untracked_files(ctx.repo),
        }, ensure_ascii=False, indent=2))

    @tool("query_events", "Query metrics.jsonl events (last N lines)", {"last_n": int})
    async def query_events(args_dict: dict) -> dict:
        n = int(args_dict.get("last_n", 20) or 20)
        metrics_path = ctx.run_dir / "metrics.jsonl"
        if not metrics_path.exists():
            return _text_response(json.dumps([]))
        lines = metrics_path.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        tail = lines[-n:] if len(lines) > n else lines
        events = []
        for line in tail:
            try:
                events.append(json.loads(line))
            except Exception:
                events.append({"raw": line})
        return _text_response(json.dumps(events, ensure_ascii=False, indent=2))

    server = create_sdk_mcp_server(
        name="agentcli",
        version="1.0.0",
        tools=[check_state, load_backlog, run_build, run_tests, git_status, query_events],
    )
    return server


# ---------------------------------------------------------------------------
# Phase 3: Hooks (PreToolUse / PostToolUse)
#
# SDK hook signature (PreToolUse):
#   async def hook(input_data: HookInput, tool_use_id: str | None, context: HookContext) -> HookJSONOutput
#   input_data = {"tool_name": str, "tool_input": dict}
#   Return {} to allow, or {"hookSpecificOutput": {"hookEventName": "PreToolUse",
#       "permissionDecision": "deny", "permissionDecisionReason": "..."}} to block.
#
# SDK hook signature (PostToolUse):
#   Same signature. input_data also has "tool_output" key.
#   Return {} for no action, or inject system message via hookSpecificOutput.
# ---------------------------------------------------------------------------

_DANGEROUS_BASH_PATTERNS = [
    re.compile(r"\brm\s+(-[rf]+\s+)*/([\s;|&]|$)"),          # rm -rf /
    re.compile(r"\bgit\s+push\s+--force\b"),                   # git push --force
    re.compile(r"\bgit\s+push\s+-f\b"),                         # git push -f
    re.compile(r"\bgit\s+reset\s+--hard\b"),                    # git reset --hard
    re.compile(r"\bgit\s+clean\s+-[fdx]+\b"),                   # git clean -fd
    re.compile(r"\bformat\s+[A-Za-z]:\b"),                      # format C:
    re.compile(r"\b(del|rd|rmdir)\s+/s\s+/q\b", re.IGNORECASE),  # Windows del /s /q
]


def _deny_hook(reason: str) -> dict:
    """Build a PreToolUse deny response in SDK format."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def build_hooks(ctx: ClaudeExtensionContext) -> dict:
    """Build PreToolUse / PostToolUse hooks for safety guardrails."""
    try:
        from claude_agent_sdk import HookMatcher
    except ImportError:
        return {}

    # -- PreToolUse: validate bash commands --
    async def validate_bash_command(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        """Block dangerous shell commands."""
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        if tool_name != "Bash":
            return {}
        command = tool_input.get("command", "") or tool_input.get("cmd", "")
        if not command:
            return {}
        cmd_lower = command.lower().strip()
        for pat in _DANGEROUS_BASH_PATTERNS:
            if pat.search(cmd_lower):
                ctx.logger.error(
                    f"[HOOK] Blocked dangerous command: {command[:120]}",
                    context={"stage": ctx.current_stage, "task_id": ctx.current_task_id},
                )
                ctx.metrics.event(
                    "hook_blocked", stage=ctx.current_stage,
                    tool="Bash", reason="dangerous_command",
                    command_preview=command[:120],
                )
                return _deny_hook(f"Dangerous command blocked: {command[:80]}")
        return {}

    # -- PreToolUse: audit all tool calls --
    async def audit_tool_use(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        """Log every tool invocation to metrics."""
        tool_name = input_data.get("tool_name", "unknown")
        ctx.metrics.event(
            "tool_use", stage=ctx.current_stage,
            tool=tool_name, task_id=ctx.current_task_id,
        )
        return {}  # allow

    # -- PostToolUse: check edits against policy --
    async def post_edit_policy_check(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        """After file edit, scan for policy violations and inject warning."""
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})
        file_path = tool_input.get("file_path", "") or tool_input.get("path", "")
        if not file_path or not ctx.policy_rules:
            return {}
        try:
            from ..policy import policy_scan_files
            fp = Path(file_path)
            if fp.exists():
                content = fp.read_text(encoding="utf-8", errors="replace")
                result = policy_scan_files(
                    [(str(fp), content)],
                    ctx.policy_rules,
                )
                violations = result.get("violations", []) if isinstance(result, dict) else []
                if violations:
                    warning_lines = [f"[POLICY WARNING] Violations detected in {file_path}:"]
                    for v in violations[:5]:
                        desc = v.get("message", str(v)) if isinstance(v, dict) else str(v)
                        warning_lines.append(f"  - {desc}")
                    warning = "\n".join(warning_lines)
                    ctx.logger.info(warning, context={"stage": ctx.current_stage})
                    ctx.metrics.event(
                        "policy_violation", stage=ctx.current_stage,
                        tool=tool_name, file=file_path,
                        count=len(violations),
                    )
                    return {
                        "hookSpecificOutput": {
                            "hookEventName": "PostToolUse",
                            "message": warning,
                        }
                    }
        except Exception:
            pass
        return {}

    # -- PostToolUse: check bash errors --
    async def post_bash_error_check(input_data: dict, tool_use_id: str | None, context: Any) -> dict:
        """Detect critical bash errors and inject system message."""
        tool_output = input_data.get("tool_output", "")
        output_str = str(tool_output) if tool_output else ""
        fatal_patterns = [
            "FATAL ERROR", "Segmentation fault", "core dumped",
            "Permission denied", "ENOMEM", "out of memory",
            "disk full", "No space left on device",
        ]
        for pat in fatal_patterns:
            if pat.lower() in output_str.lower():
                msg = f"[HOOK WARNING] Critical error detected in Bash output: {pat}"
                ctx.metrics.event(
                    "bash_critical_error", stage=ctx.current_stage,
                    pattern=pat, task_id=ctx.current_task_id,
                )
                return {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "message": msg,
                    }
                }
        return {}

    hooks = {
        "PreToolUse": [
            HookMatcher(matcher="Bash", hooks=[validate_bash_command]),
            HookMatcher(hooks=[audit_tool_use]),
        ],
        "PostToolUse": [
            HookMatcher(matcher="Edit", hooks=[post_edit_policy_check]),
            HookMatcher(matcher="Write", hooks=[post_edit_policy_check]),
            HookMatcher(matcher="Bash", hooks=[post_bash_error_check]),
        ],
    }
    return hooks


# ---------------------------------------------------------------------------
# Phase 4: can_use_tool (dynamic permission control)
#
# SDK API reference:
#   async def callback(tool_name: str, input_data: dict, context: ToolPermissionContext)
#       -> PermissionResultAllow | PermissionResultDeny
#   PermissionResultAllow()  — allow (optionally with updated_input)
#   PermissionResultDeny(message="...")  — deny with reason
# ---------------------------------------------------------------------------

_SENSITIVE_FILE_PATTERNS = [
    ".env", "credentials", ".key", ".pem", ".p12", ".pfx",
    "secret", "token", "password",
]

_PROTECTED_STATE_FILES = {"STATE.json", "BACKLOG.json", "metrics.jsonl"}


def build_can_use_tool(ctx: ClaudeExtensionContext, cfg: Any) -> Any:
    """Build a can_use_tool callback for dynamic tool permission control.

    Rules:
    - QA stage: deny Write/Edit (read-only)
    - All stages: block path traversal (..)
    - All stages: block writes outside repo
    - All stages: protect sensitive files
    - Dev + strict_isolation: warn on edits outside task files
    """
    try:
        from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
    except ImportError:
        return None

    strict_isolation = getattr(cfg, "can_use_tool_strict_isolation", False)
    try:
        repo_str = str(ctx.repo.resolve()).replace("\\", "/").rstrip("/")
    except Exception:
        repo_str = str(ctx.repo).replace("\\", "/").rstrip("/")

    def _resolve_for_repo(path_value: str) -> str:
        try:
            candidate = Path(path_value)
            if not candidate.is_absolute():
                candidate = ctx.repo / candidate
            return str(candidate.resolve()).replace("\\", "/")
        except (ValueError, OSError):
            return ""

    def _is_under_repo(abs_path: str) -> bool:
        clean = abs_path.replace("\\", "/").rstrip("/")
        return bool(clean == repo_str or clean.startswith(repo_str + "/"))

    async def can_use_tool(tool_name: str, input_data: dict, context: Any) -> Any:
        """SDK can_use_tool callback — returns PermissionResultAllow or PermissionResultDeny.

        Wrapped in try/except to prevent malformed tool input (e.g. invalid paths)
        from crashing the entire pipeline. Fails open (allow) on unexpected errors.
        """
        try:
            stage_low = (ctx.current_stage or "").lower()
            file_path = input_data.get("file_path", "") or input_data.get("path", "")

            # --- QA read-only enforcement ---
            if stage_low == "qa" and tool_name in ("Write", "Edit"):
                return PermissionResultDeny(
                    message="QA stage is read-only. You cannot modify files during QA.",
                )

            # --- File-modifying tools only ---
            if tool_name in ("Write", "Edit") and file_path:
                norm_path = file_path.replace("\\", "/")

                # Block path traversal
                try:
                    parts = Path(norm_path).parts
                except (ValueError, OSError):
                    parts = ()
                if ".." in parts:
                    ctx.metrics.event("can_use_tool_denied", tool=tool_name, reason="path_traversal", path=file_path[:120])
                    return PermissionResultDeny(
                        message=f"Path traversal blocked: {file_path}",
                    )

                # Block writes outside repo
                abs_path = _resolve_for_repo(file_path)
                if abs_path and not _is_under_repo(abs_path):
                    ctx.metrics.event("can_use_tool_denied", tool=tool_name, reason="outside_repo", path=file_path[:120])
                    return PermissionResultDeny(
                        message=f"Cannot modify files outside repository: {file_path}",
                    )

                # Protect sensitive files
                try:
                    basename = Path(file_path).name.lower()
                except (ValueError, OSError):
                    basename = ""
                for pat in _SENSITIVE_FILE_PATTERNS:
                    if basename and pat in basename:
                        ctx.metrics.event("can_use_tool_denied", tool=tool_name, reason="sensitive_file", path=file_path[:120])
                        return PermissionResultDeny(
                            message=f"Cannot modify sensitive file: {basename}",
                        )

                # Protect state files
                if basename and basename in {f.lower() for f in _PROTECTED_STATE_FILES}:
                    ctx.metrics.event("can_use_tool_denied", tool=tool_name, reason="state_file", path=file_path[:120])
                    return PermissionResultDeny(
                        message=f"Cannot modify pipeline state file: {basename}",
                    )

                # Strict task isolation (Dev only, opt-in)
                if strict_isolation and stage_low == "dev" and ctx.current_task_files and abs_path:
                    try:
                        task_paths = set()
                        for f in ctx.current_task_files:
                            task_abs = _resolve_for_repo(f)
                            if task_abs:
                                task_paths.add(task_abs.lower())
                    except (ValueError, OSError):
                        task_paths = set()
                    if task_paths and abs_path.lower() not in task_paths:
                        ctx.metrics.event(
                            "can_use_tool_warn", tool=tool_name,
                            reason="outside_task_scope", path=file_path[:120],
                            task_id=ctx.current_task_id,
                        )
                        return PermissionResultAllow()

            return PermissionResultAllow()
        except Exception:
            # Fail-open: allow the tool call rather than crash the pipeline
            return PermissionResultAllow()

    return can_use_tool


# ---------------------------------------------------------------------------
# Phase 5: Subagents (AgentDefinition)
# ---------------------------------------------------------------------------

def build_subagents(ctx: ClaudeExtensionContext, cfg: Any) -> dict:
    """Build subagent definitions for Dev stage.

    Agents: code-reviewer, test-runner, security-auditor
    """
    try:
        from claude_agent_sdk import AgentDefinition
    except ImportError:
        return {}

    agents: dict[str, Any] = {}

    # --- code-reviewer (read-only) ---
    if getattr(cfg, "subagent_reviewer_enabled", True):
        reviewer_model = getattr(cfg, "subagent_reviewer_model", "") or getattr(cfg, "qa_model", "") or cfg.model
        agents["code-reviewer"] = AgentDefinition(
            description=(
                "Code review specialist. Reviews code changes for correctness, style, "
                "and potential bugs. Read-only — cannot modify files."
            ),
            prompt=(
                f"You are a code reviewer for the project at {ctx.repo}.\n"
                "Review the code changes and provide:\n"
                "1. Correctness issues (bugs, logic errors)\n"
                "2. Style/convention violations\n"
                "3. Potential performance issues\n"
                "4. Security concerns\n"
                "Use Read, Grep, Glob to inspect files. Do NOT modify any files.\n"
                "Be concise and actionable."
            ),
            tools=["Read", "Grep", "Glob"],
            model=reviewer_model,
        )

    # --- test-runner ---
    if getattr(cfg, "subagent_runner_enabled", True):
        runner_model = getattr(cfg, "subagent_runner_model", "") or getattr(cfg, "dev_model", "") or cfg.model
        agents["test-runner"] = AgentDefinition(
            description=(
                "Test execution specialist. Runs tests and reports results. "
                "Can execute commands to build and test code."
            ),
            prompt=(
                f"You are a test runner for the project at {ctx.repo}.\n"
                "Your job:\n"
                "1. Run the project's test suite using Bash\n"
                "2. Analyze test output for failures\n"
                "3. Report a summary of passing/failing tests\n"
                "4. Identify root cause of failures if possible\n"
                "Use Bash to run tests, Read/Grep to inspect test files."
            ),
            tools=["Bash", "Read", "Grep"],
            model=runner_model,
        )

    # --- security-auditor (read-only) ---
    if getattr(cfg, "subagent_auditor_enabled", True):
        auditor_model = getattr(cfg, "subagent_auditor_model", "") or getattr(cfg, "qa_model", "") or cfg.model
        agents["security-auditor"] = AgentDefinition(
            description=(
                "Security audit specialist. Scans code for common vulnerabilities "
                "(OWASP Top 10, hardcoded secrets, injection risks). Read-only."
            ),
            prompt=(
                f"You are a security auditor for the project at {ctx.repo}.\n"
                "Scan the codebase for:\n"
                "1. Hardcoded secrets, API keys, passwords\n"
                "2. SQL injection, XSS, command injection risks\n"
                "3. Insecure dependencies or configurations\n"
                "4. Path traversal vulnerabilities\n"
                "5. Improper error handling that leaks information\n"
                "Use Read, Grep, Glob to inspect files. Do NOT modify any files.\n"
                "Report findings with severity (Critical/High/Medium/Low)."
            ),
            tools=["Read", "Grep", "Glob"],
            model=auditor_model,
        )

    return agents


# ---------------------------------------------------------------------------
# Unified apply_extensions() — called from _build_options()
# ---------------------------------------------------------------------------

_MCP_TOOL_NAMES = [
    "mcp__agentcli__check_state",
    "mcp__agentcli__load_backlog",
    "mcp__agentcli__run_build",
    "mcp__agentcli__run_tests",
    "mcp__agentcli__git_status",
    "mcp__agentcli__query_events",
]

_MCP_TOOLS_DESCRIPTION = (
    "\n\nYou also have access to AgentCLI pipeline tools via mcp__agentcli__* tools:\n"
    "- check_state: Read pipeline STATE.json (done/failed tasks)\n"
    "- load_backlog: Read BACKLOG.json task list\n"
    "- run_build: Execute project build gate\n"
    "- run_tests: Execute project test gate\n"
    "- git_status: Get git HEAD, porcelain status, changed files\n"
    "- query_events: Query recent pipeline events from metrics\n"
    "Use these tools to understand pipeline state and verify your work.\n"
)


CLAUDE_PERMISSION_MODE_CHOICES = ("default", "acceptEdits", "bypassPermissions", "plan")
CLAUDE_SETTING_SOURCE_CHOICES = ("user", "project", "local")
CLAUDE_ADVANCED_FEATURE_ORDER = (
    "mcp_tools",
    "hooks",
    "can_use_tool",
    "strict_isolation",
    "subagents",
)
CLAUDE_ADVANCED_FEATURE_META: dict[str, dict[str, Any]] = {
    "mcp_tools": {
        "label": "MCP tools",
        "cfg_key": "mcp_tools_enabled",
        "option": "mcp_servers",
        "stages": ["dev", "qa", "buildfix"],
    },
    "hooks": {
        "label": "Hooks",
        "cfg_key": "hooks_enabled",
        "option": "hooks",
        "stages": ["pm", "dev", "qa", "buildfix", "reporter"],
    },
    "can_use_tool": {
        "label": "Dynamic permission",
        "cfg_key": "can_use_tool_enabled",
        "option": "can_use_tool",
        "stages": ["pm", "dev", "qa", "buildfix", "reporter"],
    },
    "strict_isolation": {
        "label": "Strict task isolation",
        "cfg_key": "can_use_tool_strict_isolation",
        "option": "can_use_tool",
        "stages": ["dev"],
    },
    "subagents": {
        "label": "Subagents",
        "cfg_key": "subagents_enabled",
        "option": "agents",
        "stages": ["dev"],
    },
}


def _cfg_value(source: Any, key: str, default: Any = None) -> Any:
    prefixed = f"claudecode_{key}"
    if isinstance(source, dict):
        if key in source:
            return source.get(key)
        return source.get(prefixed, default)
    if hasattr(source, key):
        return getattr(source, key)
    if hasattr(source, prefixed):
        return getattr(source, prefixed)
    return default


def _cfg_bool(source: Any, key: str, default: bool = False) -> bool:
    value = _cfg_value(source, key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "off", "disabled", ""}:
            return False
    return bool(value)


def _cfg_int(source: Any, key: str, default: int) -> int:
    value = _cfg_value(source, key, default)
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except Exception:
        return default


def _cfg_list(source: Any, key: str, default: Any = None) -> list[str]:
    value = _cfg_value(source, key, default if default is not None else [])
    if isinstance(value, (list, tuple)):
        raw = list(value)
    elif isinstance(value, str):
        raw = [part.strip() for part in re.split(r"[,\s]+", value) if part.strip()]
    elif value in (None, ""):
        raw = []
    else:
        raw = [value]
    return [str(item).strip() for item in raw if str(item).strip()]


def _claude_advanced_normalized_config(source: Any) -> dict[str, Any]:
    return {
        "permission_mode": str(_cfg_value(source, "permission_mode", "acceptEdits") or "acceptEdits"),
        "max_turns": _cfg_int(source, "max_turns", 32),
        "setting_sources": _cfg_list(source, "setting_sources", ["project"]),
        "pm_allowed_tools": _cfg_list(source, "pm_allowed_tools", ["Read", "Grep", "Glob", "Write", "Edit"]),
        "dev_allowed_tools": _cfg_list(source, "dev_allowed_tools", ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]),
        "qa_allowed_tools": _cfg_list(source, "qa_allowed_tools", ["Read", "Grep", "Glob", "Bash"]),
        "pm_disallowed_tools": _cfg_list(source, "pm_disallowed_tools"),
        "dev_disallowed_tools": _cfg_list(source, "dev_disallowed_tools"),
        "qa_disallowed_tools": _cfg_list(source, "qa_disallowed_tools"),
        "mcp_tools_enabled": _cfg_bool(source, "mcp_tools_enabled"),
        "hooks_enabled": _cfg_bool(source, "hooks_enabled"),
        "can_use_tool_enabled": _cfg_bool(source, "can_use_tool_enabled"),
        "can_use_tool_strict_isolation": _cfg_bool(source, "can_use_tool_strict_isolation"),
        "subagents_enabled": _cfg_bool(source, "subagents_enabled"),
        "subagent_reviewer_enabled": _cfg_bool(source, "subagent_reviewer_enabled", True),
        "subagent_runner_enabled": _cfg_bool(source, "subagent_runner_enabled", True),
        "subagent_auditor_enabled": _cfg_bool(source, "subagent_auditor_enabled", True),
        "subagent_reviewer_model": str(_cfg_value(source, "subagent_reviewer_model", "") or ""),
        "subagent_runner_model": str(_cfg_value(source, "subagent_runner_model", "") or ""),
        "subagent_auditor_model": str(_cfg_value(source, "subagent_auditor_model", "") or ""),
    }


def _issue(severity: str, code: str, message: str, field: str = "") -> dict[str, str]:
    payload = {"severity": severity, "code": code, "message": message}
    if field:
        payload["field"] = field
    return payload


def validate_claude_advanced_config(source: Any) -> dict[str, Any]:
    """Validate Claude Code advanced controls without importing the SDK."""
    cfg = _claude_advanced_normalized_config(source)
    issues: list[dict[str, str]] = []

    if cfg["permission_mode"] not in CLAUDE_PERMISSION_MODE_CHOICES:
        issues.append(
            _issue(
                "error",
                "claude_invalid_permission_mode",
                "Claude permission mode must be one of the supported SDK modes.",
                "claudecode_permission_mode",
            )
        )
    if int(cfg["max_turns"]) < 1:
        issues.append(
            _issue(
                "error",
                "claude_invalid_max_turns",
                "Claude max turns must be at least 1.",
                "claudecode_max_turns",
            )
        )

    invalid_sources = [item for item in cfg["setting_sources"] if item not in CLAUDE_SETTING_SOURCE_CHOICES]
    if invalid_sources:
        issues.append(
            _issue(
                "error",
                "claude_invalid_setting_sources",
                "Claude setting sources must use user, project, or local.",
                "claudecode_setting_sources",
            )
        )

    for stage in ("pm", "dev", "qa"):
        if not cfg[f"{stage}_allowed_tools"]:
            issues.append(
                _issue(
                    "warning",
                    f"claude_{stage}_allowed_tools_empty",
                    f"Claude {stage.upper()} allowed tool list is empty.",
                    f"claudecode_{stage}_allowed_tools",
                )
            )

    if cfg["can_use_tool_strict_isolation"] and not cfg["can_use_tool_enabled"]:
        issues.append(
            _issue(
                "warning",
                "claude_strict_isolation_not_enforced",
                "Strict isolation requires dynamic permission control to be enabled.",
                "claudecode_can_use_tool_enabled",
            )
        )

    if cfg["subagents_enabled"] and not (
        cfg["subagent_reviewer_enabled"]
        or cfg["subagent_runner_enabled"]
        or cfg["subagent_auditor_enabled"]
    ):
        issues.append(
            _issue(
                "warning",
                "claude_subagents_all_disabled",
                "Subagents are enabled, but every built-in subagent is disabled.",
                "claudecode_subagents_enabled",
            )
        )

    enabled_count = sum(1 for feature in CLAUDE_ADVANCED_FEATURE_ORDER if bool(cfg[CLAUDE_ADVANCED_FEATURE_META[feature]["cfg_key"]]))
    status = "error" if any(item["severity"] == "error" for item in issues) else "warning" if issues else "ok"
    return {
        "status": status,
        "valid": status != "error",
        "config": cfg,
        "enabled_count": enabled_count,
        "enabledCount": enabled_count,
        "issues": issues,
        "warnings": [item for item in issues if item["severity"] == "warning"],
        "errors": [item for item in issues if item["severity"] == "error"],
    }


def _sdk_option_support(options_cls: Any, option_name: str) -> bool | None:
    if options_cls is None:
        return None
    try:
        signature = inspect.signature(options_cls)
        params = signature.parameters
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
            return True
        return option_name in params
    except Exception:
        return None


def build_claude_advanced_diagnostics(source: Any, *, options_cls: Any = None) -> dict[str, Any]:
    validation = validate_claude_advanced_config(source)
    cfg = dict(validation["config"])
    sdk_available = True
    sdk_version = ""
    sdk_error = ""
    if options_cls is None:
        try:
            import claude_agent_sdk  # type: ignore
            options_cls = getattr(claude_agent_sdk, "ClaudeAgentOptions", None)
            sdk_version = str(getattr(claude_agent_sdk, "__version__", "") or "")
        except Exception as ex:
            options_cls = None
            sdk_available = False
            sdk_error = str(ex).strip() or ex.__class__.__name__

    option_support: dict[str, bool | None] = {}
    for meta in CLAUDE_ADVANCED_FEATURE_META.values():
        option_name = str(meta.get("option") or "")
        if option_name and option_name not in option_support:
            option_support[option_name] = _sdk_option_support(options_cls, option_name)

    issues = [dict(item) for item in validation["issues"]]
    features: dict[str, dict[str, Any]] = {}
    for feature_id in CLAUDE_ADVANCED_FEATURE_ORDER:
        meta = CLAUDE_ADVANCED_FEATURE_META[feature_id]
        cfg_key = str(meta["cfg_key"])
        option_name = str(meta["option"])
        enabled = bool(cfg.get(cfg_key))
        sdk_supported = option_support.get(option_name)
        feature_status = "disabled"
        if enabled:
            feature_status = "ok"
            if sdk_supported is False:
                feature_status = "warning"
                issues.append(
                    _issue(
                        "warning",
                        f"claude_{feature_id}_sdk_unsupported",
                        f"Installed Claude SDK does not expose the {option_name} option.",
                        f"claudecode_{cfg_key}",
                    )
                )
            elif sdk_supported is None and not sdk_available:
                feature_status = "warning"
                issues.append(
                    _issue(
                        "warning",
                        f"claude_{feature_id}_sdk_unavailable",
                        "Claude SDK is not installed, so this advanced feature cannot be verified.",
                        f"claudecode_{cfg_key}",
                    )
                )
        if feature_id == "strict_isolation" and enabled and not cfg["can_use_tool_enabled"]:
            feature_status = "warning"
        features[feature_id] = {
            "id": feature_id,
            "label": meta["label"],
            "enabled": enabled,
            "option": option_name,
            "sdk_supported": sdk_supported,
            "sdkSupported": sdk_supported,
            "stages": list(meta["stages"]),
            "status": feature_status,
            "enforced": bool(enabled and (feature_id != "strict_isolation" or cfg["can_use_tool_enabled"])),
        }

    status = "error" if any(item["severity"] == "error" for item in issues) else "warning" if issues else "ok"
    return {
        "status": status,
        "valid": not any(item["severity"] == "error" for item in issues),
        "sdk": {
            "available": bool(sdk_available),
            "version": sdk_version,
            "error": sdk_error,
            "option_support": option_support,
            "optionSupport": option_support,
        },
        "config": cfg,
        "features": features,
        "issues": issues,
        "warnings": [item for item in issues if item["severity"] == "warning"],
        "errors": [item for item in issues if item["severity"] == "error"],
        "mcp_tools": list(_MCP_TOOL_NAMES),
        "mcpTools": list(_MCP_TOOL_NAMES),
        "summary": {
            "enabled_count": sum(1 for item in features.values() if item["enabled"]),
            "enabledCount": sum(1 for item in features.values() if item["enabled"]),
            "issue_count": len(issues),
            "issueCount": len(issues),
            "warning_count": sum(1 for item in issues if item["severity"] == "warning"),
            "warningCount": sum(1 for item in issues if item["severity"] == "warning"),
            "error_count": sum(1 for item in issues if item["severity"] == "error"),
            "errorCount": sum(1 for item in issues if item["severity"] == "error"),
        },
    }


def format_claude_advanced_diagnostics_lines(diagnostics: dict[str, Any], *, indent: str = "") -> list[str]:
    diag = diagnostics if isinstance(diagnostics, dict) else {}
    sdk = diag.get("sdk") if isinstance(diag.get("sdk"), dict) else {}
    summary = diag.get("summary") if isinstance(diag.get("summary"), dict) else {}
    features = diag.get("features") if isinstance(diag.get("features"), dict) else {}
    lines = [
        (
            f"{indent}- status={diag.get('status') or 'unknown'} "
            f"valid={bool(diag.get('valid', False))} "
            f"enabled={int(summary.get('enabled_count') or 0)}/{len(CLAUDE_ADVANCED_FEATURE_ORDER)} "
            f"sdk_available={bool(sdk.get('available', False))}"
        )
    ]
    for feature_id in CLAUDE_ADVANCED_FEATURE_ORDER:
        item = features.get(feature_id) if isinstance(features.get(feature_id), dict) else {}
        lines.append(
            f"{indent}  - {feature_id}: "
            f"enabled={bool(item.get('enabled', False))} "
            f"status={item.get('status') or 'unknown'} "
            f"sdk_supported={item.get('sdk_supported')}"
        )
    for issue in list(diag.get("issues") or [])[:8]:
        if not isinstance(issue, dict):
            continue
        lines.append(
            f"{indent}  - {issue.get('severity') or 'issue'} "
            f"{issue.get('code') or 'unknown'}: {issue.get('message') or ''}"
        )
    return lines


def apply_extensions(
    ext_ctx: Optional[ClaudeExtensionContext],
    cfg: Any,
    kwargs: dict[str, Any],
    stage: str,
) -> None:
    """Apply enabled extensions to the kwargs dict that will be passed to ClaudeAgentOptions.

    Modifies kwargs in-place: may add 'mcp_servers', 'hooks', 'can_use_tool', 'agents' keys,
    and extend 'allowed_tools' and 'system_prompt'.

    Safe to call even when ext_ctx is None or features are disabled — does nothing.
    """
    if ext_ctx is None:
        return

    stage_low = (stage or "").strip().lower()

    # --- MCP Tools (Phase 2) ---
    # Only for Dev/QA/BuildFix stages (PM keeps structured output)
    if getattr(cfg, "mcp_tools_enabled", False) and stage_low in ("dev", "qa", "buildfix"):
        # Cache: build once, reuse across queries
        if ext_ctx._cached_mcp_server is None:
            ext_ctx._cached_mcp_server = build_mcp_server(ext_ctx)
        mcp_server = ext_ctx._cached_mcp_server
        if mcp_server is not None:
            kwargs["mcp_servers"] = {"agentcli": mcp_server}
            allowed = list(kwargs.get("allowed_tools", []))
            for name in _MCP_TOOL_NAMES:
                if name not in allowed:
                    allowed.append(name)
            kwargs["allowed_tools"] = allowed
            # Append MCP tool descriptions to system prompt
            sys_prompt = kwargs.get("system_prompt", "")
            if sys_prompt and _MCP_TOOLS_DESCRIPTION not in sys_prompt:
                kwargs["system_prompt"] = sys_prompt + _MCP_TOOLS_DESCRIPTION

    # --- Hooks (Phase 3) ---
    if getattr(cfg, "hooks_enabled", False):
        # Cache: build once, reuse across queries
        if ext_ctx._cached_hooks is None:
            ext_ctx._cached_hooks = build_hooks(ext_ctx)
        hooks = ext_ctx._cached_hooks
        if hooks:
            kwargs["hooks"] = hooks

    # --- can_use_tool (Phase 4) ---
    if getattr(cfg, "can_use_tool_enabled", False):
        # Cache: build once, reuse across queries
        if ext_ctx._cached_can_use_tool is None:
            ext_ctx._cached_can_use_tool = build_can_use_tool(ext_ctx, cfg)
        cb = ext_ctx._cached_can_use_tool
        if cb is not None:
            kwargs["can_use_tool"] = cb

    # --- Subagents (Phase 5) ---
    # Only for Dev stage (not cached — lightweight AgentDefinition objects)
    if getattr(cfg, "subagents_enabled", False) and stage_low == "dev":
        agents_dict = build_subagents(ext_ctx, cfg)
        if agents_dict:
            kwargs["agents"] = agents_dict
            allowed = list(kwargs.get("allowed_tools", []))
            if "Task" not in allowed:
                allowed.append("Task")
            kwargs["allowed_tools"] = allowed
