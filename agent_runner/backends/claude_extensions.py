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
    repo_str = str(ctx.repo).replace("\\", "/").rstrip("/")

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
                try:
                    abs_path = str(Path(file_path).resolve()).replace("\\", "/")
                except (ValueError, OSError):
                    abs_path = ""
                if abs_path and not abs_path.startswith(repo_str):
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
                        task_paths = {str(Path(f).resolve()).replace("\\", "/").lower() for f in ctx.current_task_files}
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
