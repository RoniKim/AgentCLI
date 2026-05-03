"""Claude Code backend - full feature parity with Codex backend (cycle.py).

This backend uses the Claude Agent SDK (claude_agent_sdk) as the execution engine
while providing the same artifacts, logging, and orchestration as the Codex backend.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Optional, Tuple
import inspect

from ..process_guard import register_pid, unregister_pid_if_exited
from ..analysis_cache import merge_dev_hints_to_global_changelog
from ..docs import resolve_docs_dir, generate_docs_digest
from ..experience import load_pm_experience_summary
from ..gates import (
    classify_task_validation_status,
    extract_build_warnings,
    run_build_validation_async,
    run_build_gate_async,
    run_fast_web_worktree_regression_async,
    run_test_validation_async,
    should_run_fast_web_worktree_regression,
    should_retry_fast_web_worktree_regression_failure,
    summarize_fast_web_worktree_regression_failure,
)
from ..gitops import (
    git_head,
    git_changed_files,
    git_worktree_changed_files,
    git_porcelain,
    git_untracked_files,
    has_working_tree_changes,
    has_new_commits,
    repo_fingerprint,
    create_checkpoint,
    restore_checkpoint,
    update_checkpoint,
    RepoCheckpoint,
    TaskBranch,
    create_task_branch,
    merge_task_branch,
    abandon_task_branch,
    reset_task_branch,
    default_worktree_dir,
    create_worktree,
    remove_worktree,
    handle_worktree_patch,
    check_and_remove_stale_git_lock,
    ensure_clean_working_tree,
)
from ..inventory import build_repo_inventory, write_repo_inventory_files
from ..todo import read_current_todo, format_todo_block
from ..metrics import MetricsLogger
from ..logger import create_logger
from ..policy import load_policy_rules, policy_scan_files
from ..security import load_security_rules, security_scan_files
from ..scan import DEFAULT_SCAN_IGNORE_GLOBS
from ..prompts import (
    PromptStore,
    ensure_pm_instructions_have_output_schema,
    append_pm_output_contract,
    append_pm_essential_context,
    PM_BOOTSTRAP_TEMPLATE_DEFAULT,
    PM_INCREMENTAL_TEMPLATE_DEFAULT,
    DEV_TASK_TEMPLATE_DEFAULT,
    QA_TEMPLATE_DEFAULT,
    QA_FOLLOWUPS_OUTPUT_CONTRACT,
    PM_INSTRUCTIONS_DEFAULT,
    DEV_INSTRUCTIONS_DEFAULT,
    QA_INSTRUCTIONS_DEFAULT,
    REPORTER_INSTRUCTIONS_DEFAULT,
    PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT,
    PM_TURN_BUDGET_WARNING,
)
from ..experience import (
    ExperienceRedactionSettings,
    render_pm_experience_summary_from_run,
)
from ..reporting import collect_shutdown_context, build_local_shutdown_report
from ..pipeline import PipelineManager, make_stages
from ..pipeline.shared_runtime import (
    SharedCycleDeps,
    append_cycle_summary_line,
    build_goals_prompt_context,
    build_qa_skills_context,
    compute_dev_model_tiers,
    collect_scan_with_config,
    detect_and_clear_recycled_ids,
    ensure_backlog_artifacts,
    load_backlog_tasks,
    merge_pm_tasks_with_existing_pending,
    maybe_refresh_tasks_after_pm,
    prepare_pm_inventory_markdown,
    process_qa_followups,
    run_shared_cycle_once,
    select_next_task_with_dependency_checks,
    write_pm_output_artifacts,
    write_run_summary_file,
)
from ..state import count_state_task_ids
from ..run_dir import make_run_dir, find_latest_run_dir
from ..stop_progress import write_stop_snapshot
from ..schemas import PMOutputV2, pm_output_json_schema
from ..state import (
    load_backlog_json,
    load_backlog_task_ids,
    parse_backlog_md,
    load_state,
    save_state,
    write_backlog_files,
    mark_backlog_done,
    TaskItem,
)
from ..structured import parse_pm_output_with_errors, dump_pretty, describe_parse_failure, parse_qa_followups
from ..skills import (
    build_skills_context,
    build_skills_index,
    resolve_skills_roots,
    resolve_snapshot_dir,
    summarize_skills_index_capped,
    write_skills_snapshot,
)
from ..shared import (
    load_json_if_exists as _load_json_if_exists,
    inline_skills_for as _inline_skills_for,
    format_skill_selection as _format_skill_selection,
    coerce_roles_arg as _coerce_roles_arg,
)
from ..validation_artifacts import write_task_validation_artifacts
from ..task_history import format_history_block as _format_history_block, format_split_history_blocks as _format_split_history_blocks, count_unresolved_failures as _count_unresolved_failures, count_consecutive_title_failures as _count_consecutive_title_failures
from ..task_status import (
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_COMPLETED,
    classify_task_failure,
    is_auto_retry_allowed,
)
from ..failure_policy import (
    count_task_status_groups,
    should_count_cycle_failure_for_stop,
    should_preserve_for_review,
)
from ..task_failures import record_task_failure_result, record_task_failure_state
from ..progress import print_cycle_report, TokenTracker, extract_claude_tokens
from ..utils import (
    force_utf8_stdio,
    eprint,
    now_iso,
    run_cmd,
    safe_write_text,
    rotate_log_file,
    has_quota_text,
    choose_stop_reason,
    detect_stop_reason,
    write_heartbeat,
    loop_cycle_indices,
    check_quota_utilization,
    seconds_until_reset,
    severity_at_or_above,
    budget_exceeded,
    is_unsafe_path,
    STOP_REASON_QUOTA,
    STOP_REASON_QUOTA_UTILIZATION,
    STOP_REASON_STOP_FILE,
    STOP_REASON_ALL_TASKS_DONE,
    STOP_REASON_ALL_TASKS_ATTEMPTED,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_NO_TASKS,
    STOP_REASON_PM_REFRESH_NO_BACKLOG,
)
from ..exceptions import BudgetExceeded, StopRequested
from ..exc_detect import (
    is_max_turns_exception,
    is_quota_exception,
    is_model_invalid_exception,
    is_transient_exception,
)
from ..experience import record_task_experience
from ..qa_utils import (
    extract_qa_followups,
    followups_from_structured,
    merge_qa_followups,
    split_followups_by_type,
    write_manual_checks,
)
from ..backlog_utils import (
    normalize_backlog_tasks,
    validate_skill_ids,
    load_backlog_context_for_pm,
    build_failed_tasks_block,
    record_history,
)
from ..goals import (
    read_goals,
    format_goals_block,
    parse_goals_completion,
    update_goals_checkboxes,
    write_completion_status,
    build_goals_refresh_prompt,
    parse_and_append_refreshed_goals,
    GOALS_GENERATION_INSTRUCTION,
    GOALS_EVALUATION_INSTRUCTION,
    GOALS_REFRESH_RESCUABLE_REASONS,
    should_attempt_goals_refresh,
    resolve_goals_completion_level,
)


def _patch_prompt_for_claude(prompt: str) -> str:
    """Replace ALL Codex/OpenAI-specific references in prompts with Claude Code equivalents."""
    # 1) Catch-all first: replace any "Codex MCP" mentions before inserting new text
    prompt = re.sub(r"Codex MCP", "Claude Code built-in tools", prompt)

    # 2) "When editing files, call Claude Code built-in tools with ..." - clean instruction
    prompt = re.sub(
        r"When editing files,\s*(?:call|use)\s+Claude Code built-in tools\s+with[^\n]*",
        "When editing files, use Claude Code built-in tools (Read, Write, Edit, Grep, Glob, Bash) directly.",
        prompt,
    )

    # 3) "use Codex skills system" - neutral phrasing
    prompt = re.sub(
        r"\(use Codex skills system;\s*do NOT inline skill text\)",
        "(apply the skills listed below; do NOT inline full skill text)",
        prompt,
    )

    # 4) "Prefer apply_patch for edits" - Claude Code Edit tool
    prompt = re.sub(
        r"Prefer apply_patch for (?:edits|modifications)[^.\n]*\.?",
        "Use the Edit tool for targeted modifications and the Write tool for new files.",
        prompt,
    )

    return prompt


# ---------------------------------------------------------------------------
# Claude SDK adapter helpers (SDK-specific, not shared with Codex)
# ---------------------------------------------------------------------------

def _as_str_list(v: object) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out: list[str] = []
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
        return [p for p in s.split() if p]
    return [str(v).strip()] if str(v).strip() else []


def _parse_setting_sources(v: object) -> list[str]:
    out: list[str] = []
    for s in _as_str_list(v):
        low = s.strip().lower()
        if not low:
            continue
        if low == "global":
            low = "user"
        if low in {"user", "project", "local"}:
            out.append(low)
    if not out:
        out = ["project"]
    return out


@dataclass(frozen=True)
class ClaudeCodeConfig:
    model: str
    permission_mode: str
    max_turns: int
    setting_sources: list[str]
    system_prompt_append: str
    continue_conversation: bool
    resume: str
    enable_file_checkpointing: bool

    user: str
    include_partial_messages: bool
    fork_session: bool
    max_thinking_tokens: Optional[int]

    # Role-specific model overrides (empty => use self.model fallback)
    pm_model: str
    dev_model: str
    dev_model_tier1: str
    dev_model_tier2: str
    qa_model: str
    reporter_model: str

    pm_allowed_tools: list[str]
    pm_disallowed_tools: list[str]
    dev_allowed_tools: list[str]
    dev_disallowed_tools: list[str]
    qa_allowed_tools: list[str]
    qa_disallowed_tools: list[str]

    # Extensions (opt-in)
    mcp_tools_enabled: bool
    hooks_enabled: bool
    can_use_tool_enabled: bool
    can_use_tool_strict_isolation: bool
    subagents_enabled: bool
    subagent_reviewer_enabled: bool
    subagent_runner_enabled: bool
    subagent_auditor_enabled: bool
    subagent_reviewer_model: str
    subagent_runner_model: str
    subagent_auditor_model: str


def _load_claudecode_cfg(args: argparse.Namespace) -> ClaudeCodeConfig:
    return ClaudeCodeConfig(
        model=str(getattr(args, "claudecode_model", "sonnet") or "sonnet"),
        permission_mode=str(getattr(args, "claudecode_permission_mode", "acceptEdits") or "acceptEdits"),
        max_turns=int(getattr(args, "claudecode_max_turns", 32) or 32),
        setting_sources=_parse_setting_sources(getattr(args, "claudecode_setting_sources", "project")),
        system_prompt_append=str(getattr(args, "claudecode_system_prompt_append", "") or ""),
        continue_conversation=bool(getattr(args, "claudecode_continue_conversation", False)),
        resume=str(getattr(args, "claudecode_resume", "") or ""),
        enable_file_checkpointing=bool(getattr(args, "claudecode_enable_file_checkpointing", False)),
        user=str(getattr(args, "claudecode_user", "") or ""),
        include_partial_messages=bool(getattr(args, "claudecode_include_partial_messages", False)),
        fork_session=bool(getattr(args, "claudecode_fork_session", False)),
        max_thinking_tokens=(
            int(getattr(args, "claudecode_max_thinking_tokens", 0) or 0)
            if int(getattr(args, "claudecode_max_thinking_tokens", 0) or 0) > 0
            else None
        ),
        pm_model=str(getattr(args, "claudecode_pm_model", "") or ""),
        dev_model=str(getattr(args, "claudecode_dev_model", "") or ""),
        dev_model_tier1=str(getattr(args, "claudecode_dev_model_tier1", "") or ""),
        dev_model_tier2=str(getattr(args, "claudecode_dev_model_tier2", "") or ""),
        qa_model=str(getattr(args, "claudecode_qa_model", "") or ""),
        reporter_model=str(getattr(args, "claudecode_reporter_model", "") or ""),
        pm_allowed_tools=_as_str_list(getattr(args, "claudecode_pm_allowed_tools", "Read,Grep,Glob,Write,Edit")),
        pm_disallowed_tools=_as_str_list(getattr(args, "claudecode_pm_disallowed_tools", "")),
        dev_allowed_tools=_as_str_list(getattr(args, "claudecode_dev_allowed_tools", "Read,Write,Edit,Grep,Glob,Bash")),
        dev_disallowed_tools=_as_str_list(getattr(args, "claudecode_dev_disallowed_tools", "")),
        qa_allowed_tools=_as_str_list(getattr(args, "claudecode_qa_allowed_tools", "Read,Grep,Glob,Bash")),
        qa_disallowed_tools=_as_str_list(getattr(args, "claudecode_qa_disallowed_tools", "")),
        # Extensions
        mcp_tools_enabled=bool(getattr(args, "claudecode_mcp_tools_enabled", False)),
        hooks_enabled=bool(getattr(args, "claudecode_hooks_enabled", False)),
        can_use_tool_enabled=bool(getattr(args, "claudecode_can_use_tool_enabled", False)),
        can_use_tool_strict_isolation=bool(getattr(args, "claudecode_can_use_tool_strict_isolation", False)),
        subagents_enabled=bool(getattr(args, "claudecode_subagents_enabled", False)),
        subagent_reviewer_enabled=bool(getattr(args, "claudecode_subagent_reviewer_enabled", True)),
        subagent_runner_enabled=bool(getattr(args, "claudecode_subagent_runner_enabled", True)),
        subagent_auditor_enabled=bool(getattr(args, "claudecode_subagent_auditor_enabled", True)),
        subagent_reviewer_model=str(getattr(args, "claudecode_subagent_reviewer_model", "") or ""),
        subagent_runner_model=str(getattr(args, "claudecode_subagent_runner_model", "") or ""),
        subagent_auditor_model=str(getattr(args, "claudecode_subagent_auditor_model", "") or ""),
    )


def _filter_kwargs_for_ctor(cls: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        sig = inspect.signature(cls)
        allowed = set(sig.parameters.keys())
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            return kwargs
        dropped = {k for k in kwargs if k not in allowed}
        if dropped:
            eprint(f"[DEBUG] _filter_kwargs_for_ctor: dropped params not in {cls.__name__}: {sorted(dropped)}")
        return {k: v for k, v in kwargs.items() if k in allowed}
    except Exception:
        return kwargs


def _build_options(cfg: ClaudeCodeConfig, *, repo: Path, stage: str, model_override: str = "", stage_instructions: str = "", max_turns_override: int = 0, ext_ctx: Any = None) -> Any:
    """Build Claude Agent SDK options for a stage."""
    try:
        from claude_agent_sdk import ClaudeAgentOptions
    except Exception as ex:
        raise RuntimeError(
            "claude_agent_sdk is not installed. Install it first "
            "(see: https://platform.claude.com/docs/ko/agent-sdk/python). "
            f"Original error: {ex}"
        )

    stage_low = (stage or "").strip().lower()
    if stage_low == "pm":
        allowed = list(cfg.pm_allowed_tools)
        for t in ("Read", "Write", "Edit", "Grep", "Glob", "Bash"):
            if t not in allowed:
                allowed.append(t)
        disallowed = cfg.pm_disallowed_tools
        output_format = {"type": "json_schema", "schema": pm_output_json_schema()}
    elif stage_low in ("dev", "buildfix", "reporter"):
        allowed = cfg.dev_allowed_tools
        disallowed = cfg.dev_disallowed_tools
        output_format = None
    else:
        allowed = cfg.qa_allowed_tools
        disallowed = cfg.qa_disallowed_tools
        output_format = None

    parts = [
        "You are running inside AgentCLI (Claude Code backend). Follow the stage instructions exactly.\n",
        f"Your working directory is: {repo}\n",
        f"IMPORTANT: All file paths in Read/Write/Edit/Glob/Grep tool calls MUST use absolute paths under {repo}.\n",
        "You have access to Claude Code built-in tools: Read, Write, Edit, Grep, Glob, Bash.\n",
        "Do NOT attempt to call Codex MCP or any external MCP tools. Use only the built-in tools.\n\n",
    ]
    if stage_low == "pm":
        parts.append(PM_TURN_BUDGET_WARNING + "\n")
    if stage_instructions.strip():
        parts.append(_patch_prompt_for_claude(stage_instructions.strip()) + "\n\n")
    if cfg.system_prompt_append.strip():
        parts.append(cfg.system_prompt_append.strip() + "\n")
    system_prompt = "".join(parts)

    model = model_override or cfg.model

    kwargs: dict[str, Any] = {
        "model": model,
        "permission_mode": cfg.permission_mode,
        "max_turns": int(max_turns_override) if max_turns_override > 0 else int(cfg.max_turns),
        "setting_sources": cfg.setting_sources,
        "allowed_tools": allowed,
        "disallowed_tools": disallowed,
        "output_format": output_format,
        "system_prompt": system_prompt,
        "cwd": str(repo),
        "continue_conversation": cfg.continue_conversation,
        "resume": cfg.resume or None,
        "enable_file_checkpointing": cfg.enable_file_checkpointing,
        "user": cfg.user or None,
        "include_partial_messages": bool(cfg.include_partial_messages),
        "fork_session": bool(cfg.fork_session),
        "max_thinking_tokens": cfg.max_thinking_tokens,
        "max_buffer_size": 10 * 1024 * 1024,  # 10MB - prevent buffer overflow on large responses
    }
    # Apply SDK extensions (MCP tools, hooks, can_use_tool, subagents)
    from .claude_extensions import apply_extensions, _MCP_TOOL_NAMES
    apply_extensions(ext_ctx, cfg, kwargs, stage)

    # Snapshot which extension keys were added before filtering
    _ext_keys_before = {k for k in ("mcp_servers", "hooks", "can_use_tool", "agents") if k in kwargs}

    kwargs = _filter_kwargs_for_ctor(ClaudeAgentOptions, kwargs)

    # Clean up orphaned tool names when _filter_kwargs_for_ctor drops extension keys
    # (SDK version doesn't support these params yet - tool names would reference nothing)
    _ext_keys_after = {k for k in ("mcp_servers", "hooks", "can_use_tool", "agents") if k in kwargs}
    dropped_ext = _ext_keys_before - _ext_keys_after
    if dropped_ext and "allowed_tools" in kwargs:
        orphaned: set[str] = set()
        if "mcp_servers" in dropped_ext:
            orphaned.update(_MCP_TOOL_NAMES)
        if "agents" in dropped_ext:
            orphaned.add("Task")
        if orphaned:
            kwargs["allowed_tools"] = [t for t in kwargs["allowed_tools"] if t not in orphaned]
            if ext_ctx and getattr(ext_ctx, "debug", False):
                eprint(f"[DEBUG] Removed orphaned tool names after SDK filtering: {sorted(orphaned)}")

    return ClaudeAgentOptions(**kwargs)


# ---------------------------------------------------------------------------
# Claude SDK message stream helpers
# ---------------------------------------------------------------------------

async def _collect_messages(stream: Any, *, stop_path: Path, debug: bool) -> Tuple[str, Optional[Any]]:
    text_parts: list[str] = []
    structured: Any = None

    if hasattr(stream, "__aiter__"):
        iterator = stream
    elif hasattr(stream, "__iter__"):
        import queue, threading
        _q: queue.Queue = queue.Queue()
        _SENTINEL = object()
        _stop_event = threading.Event()
        async def _threaded_sync_iter():
            loop = asyncio.get_running_loop()
            def _consume():
                try:
                    for msg in stream:
                        if _stop_event.is_set():
                            break
                        _q.put(msg)
                finally:
                    _q.put(_SENTINEL)
            fut = loop.run_in_executor(None, _consume)
            try:
                while True:
                    item = await loop.run_in_executor(None, _q.get)
                    if item is _SENTINEL:
                        break
                    yield item
            finally:
                _stop_event.set()
                await fut
        iterator = _threaded_sync_iter()
    else:
        raise RuntimeError("ClaudeSDKClient did not provide a message stream")

    tool_calls_made: list[str] = []

    async for msg in iterator:
        if stop_path.exists():
            raise StopRequested()

        msg_name = msg.__class__.__name__
        msg_type = getattr(msg, "type", None)

        # Log tool calls for debugging (helps diagnose no_diff issues)
        if msg_name in {"ToolUseMessage", "ToolCallMessage"} or msg_type in {"tool_use", "tool_call"}:
            tool_name = getattr(msg, "name", None) or getattr(msg, "tool_name", None) or "unknown"
            tool_calls_made.append(tool_name)
            if debug:
                eprint(f"  [TOOL] {tool_name}")
        if msg_name in {"ToolResultMessage", "ToolResponseMessage"} or msg_type in {"tool_result", "tool_response"}:
            if debug:
                tool_name = getattr(msg, "name", None) or getattr(msg, "tool_name", None) or ""
                is_error = getattr(msg, "is_error", None) or getattr(msg, "error", None)
                status = "ERROR" if is_error else "ok"
                eprint(f"  [TOOL_RESULT] {tool_name} - {status}")

        if msg_name in {"AssistantMessage", "TextMessage"} or msg_type == "assistant":
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for blk in content:
                    blk_type = getattr(blk, "type", None)
                    t = getattr(blk, "text", None)
                    if isinstance(t, str) and t.strip():
                        text_parts.append(t)
                    # Capture tool_use blocks within assistant content
                    if blk_type == "tool_use":
                        tool_name = getattr(blk, "name", None) or "unknown"
                        tool_calls_made.append(tool_name)
                        if debug:
                            eprint(f"  [TOOL] {tool_name}")

        if msg_name in {"ResultMessage", "ResponseMessage"} or msg_type == "result":
            result = getattr(msg, "result", None)
            if isinstance(result, dict):
                structured = result
            elif isinstance(result, str) and result.strip():
                text_parts.append(result)
            so = getattr(msg, "structured_output", None)
            if so is not None:
                structured = so
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for blk in content:
                    t = getattr(blk, "text", None)
                    if isinstance(t, str) and t.strip():
                        text_parts.append(t)

        # Capture error/system messages - Claude Code may surface quota or
        # API errors as ErrorMessage/SystemMessage without raising an exception.
        if msg_name in {"ErrorMessage", "SystemMessage"} or msg_type in {"error", "system"}:
            err_text = (
                getattr(msg, "error", None)
                or getattr(msg, "message", None)
                or getattr(msg, "content", None)
                or getattr(msg, "text", None)
                or ""
            )
            if isinstance(err_text, str) and err_text.strip():
                text_parts.append(f"[SDK_ERROR] {err_text}")
                eprint(f"  [SDK_ERROR] {err_text}")

    if tool_calls_made:
        eprint(f"  [TOOLS_SUMMARY] {len(tool_calls_made)} tool calls: {', '.join(tool_calls_made)}")

    return ("\n".join(text_parts).strip(), structured)


async def _start_query(client: Any, prompt: str) -> None:
    try:
        result = client.query(prompt)
    except TypeError as te:
        # Only retry with keyword arg if the error is about call signature,
        # not about an unrelated type error inside .query()
        msg = str(te).lower()
        if "argument" in msg or "positional" in msg or "unexpected" in msg or "required" in msg:
            result = client.query(prompt=prompt)
        else:
            raise
    if inspect.isawaitable(result):
        await result


async def _receive_messages(client: Any, *, stop_path: Path, debug: bool) -> Tuple[str, Optional[Any]]:
    if hasattr(client, "receive_response"):
        stream = client.receive_response()
        if inspect.isawaitable(stream):
            stream = await stream
        return await _collect_messages(stream, stop_path=stop_path, debug=debug)

    if hasattr(client, "receive_messages"):
        stream = client.receive_messages()
        if inspect.isawaitable(stream):
            stream = await stream
        return await _collect_messages(stream, stop_path=stop_path, debug=debug)

    def _coerce_stream(candidate: Any) -> Any | None:
        if hasattr(candidate, "__aiter__") or hasattr(candidate, "__iter__"):
            return candidate
        for attr_name in ("stream", "messages", "iter_messages"):
            if hasattr(candidate, attr_name):
                obj = getattr(candidate, attr_name)
                try:
                    s = obj() if callable(obj) else obj
                except TypeError as te:
                    eprint(f"[DEBUG] _coerce_stream: {attr_name}() raised TypeError: {te}")
                    continue
                if hasattr(s, "__aiter__") or hasattr(s, "__iter__"):
                    return s
        return None

    stream = _coerce_stream(client)
    if stream is not None:
        return await _collect_messages(stream, stop_path=stop_path, debug=debug)

    # Provide a more informative error with available attributes
    client_attrs = [a for a in dir(client) if not a.startswith("_")]
    raise RuntimeError(
        f"ClaudeSDKClient does not provide a message stream. "
        f"Available attributes: {', '.join(client_attrs[:15])}"
    )


def _extract_client_pid(client: object) -> Optional[int]:
    """Extract the child process PID from a ClaudeSDKClient instance.

    Traverses client._transport._process.pid defensively.
    Single attempt - by the time ``__aenter__`` returns, the subprocess
    should already be started.  Returns None if not available (L1 Job Object
    still protects in that case).
    """
    transport = getattr(client, "_transport", None)
    if transport is None:
        return None
    process = getattr(transport, "_process", None)
    if process is None:
        return None
    pid = getattr(process, "pid", None)
    return pid if isinstance(pid, int) else None


async def _run_claude_query(
    cfg: ClaudeCodeConfig,
    prompt: str,
    *,
    repo: Path,
    stage: str,
    stop_path: Path,
    debug: bool,
    model_override: str = "",
    stage_instructions: str = "",
    max_turns_override: int = 0,
    timeout_seconds: int = 0,
    max_retries: int = 3,
    initial_backoff: float = 5.0,
    ext_ctx: Any = None,
    heartbeat_callback: Callable[[], None] | None = None,
    heartbeat_interval_seconds: int = 120,
) -> Tuple[str, Optional[Any]]:
    """High-level helper: create client, send query, collect messages.

    Includes retry with exponential backoff for transient errors (429, 5xx, timeout).
    Quota/budget/stop exceptions are never retried.

    A safety timeout of 1 hour is applied when timeout_seconds is 0 (unlimited)
    to prevent indefinite hangs from stalled SDK streams.
    """
    from claude_agent_sdk import ClaudeSDKClient

    _DEFAULT_SAFETY_TIMEOUT = 3600  # 1 hour - prevents infinite hang
    effective_timeout = timeout_seconds if timeout_seconds > 0 else _DEFAULT_SAFETY_TIMEOUT

    # _build_options sets cwd=repo in ClaudeAgentOptions, so the SDK
    # subprocess will start in the correct directory without os.chdir().
    for attempt in range(max_retries + 1):
        try:
            options = _build_options(cfg, repo=repo, stage=stage, model_override=model_override, stage_instructions=stage_instructions, max_turns_override=max_turns_override, ext_ctx=ext_ctx)
            async with ClaudeSDKClient(options=options) as client:
                child_pid = _extract_client_pid(client)
                if child_pid is not None:
                    register_pid(child_pid)
                hb_task: asyncio.Task[None] | None = None
                try:
                    await asyncio.wait_for(_start_query(client, prompt), timeout=120)
                    # Start heartbeat after query begins
                    if heartbeat_callback:
                        async def _hb_loop() -> None:
                            while True:
                                await asyncio.sleep(heartbeat_interval_seconds)
                                try:
                                    heartbeat_callback()
                                except Exception:
                                    pass
                        hb_task = asyncio.create_task(_hb_loop())
                    coro = _receive_messages(client, stop_path=stop_path, debug=debug)
                    text, structured = await asyncio.wait_for(coro, timeout=effective_timeout)
                    # Post-return quota check: Claude Code subprocess may exit
                    # normally while embedding quota/limit messages in its output
                    # text instead of raising an exception.
                    if has_quota_text(text or ""):
                        raise RuntimeError(
                            f"Quota exhaustion detected in Claude output: "
                            f"{(text or '')[:200]}"
                        )
                    return text, structured
                finally:
                    if hb_task is not None:
                        hb_task.cancel()
                    if child_pid is not None:
                        unregister_pid_if_exited(child_pid)
        except (StopRequested, BudgetExceeded):
            raise
        except Exception as ex:
            if is_quota_exception(ex):
                raise
            if is_transient_exception(ex) and attempt < max_retries:
                wait = initial_backoff * (2 ** attempt)
                eprint(f"[RETRY] {stage} transient error (attempt {attempt + 1}/{max_retries}): {ex}; retrying in {wait:.0f}s")
                await asyncio.sleep(wait)
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


async def _run_with_continuations(
    cfg: "ClaudeCodeConfig",
    prompt: str,
    *,
    repo: Path,
    stage: str,
    stop_path: Path,
    debug: bool,
    model_override: str = "",
    stage_instructions: str = "",
    max_turns_override: int = 0,
    timeout_seconds: int = 0,
    label: str = "",
    max_continuations: int = 0,
    task_id: str = "",
    budget_state: dict | None = None,
    budgets_cfg: dict | None = None,
    metrics: Any = None,
    _budget_exceeded: Any = None,
    ext_ctx: Any = None,
) -> Tuple[str, Optional[Any]]:
    """Run a Claude query, optionally continuing if max-turns exception occurs.

    Mirrors cycle.py's _run_with_continuations pattern.
    """
    cont_left = int(max_continuations or 0)
    bs = budget_state or {}
    bc = budgets_cfg or {}
    per_task = bs.get("per_task_continuations", {})
    task_key = task_id or label or stage

    continuation_msg = (
        f"\n\n[AGENTCLI_CONTINUATION_MARKER]\n You hit a turn limit previously while running '{label or stage}'. "
        "Continue EXACTLY from where you left off.\n"
        "- Do NOT restate a plan.\n"
        "- Do NOT summarize.\n"
        "- Apply changes now (call tools / edit files).\n"
        "- End with only the required output."
    )

    while True:
        try:
            _hb_cb = (lambda: metrics.event("heartbeat", stage=label or stage, task_id=task_id)) if metrics else None
            return await _run_claude_query(
                cfg, prompt, repo=repo, stage=stage, stop_path=stop_path, debug=debug,
                model_override=model_override, stage_instructions=stage_instructions,
                max_turns_override=max_turns_override, timeout_seconds=timeout_seconds,
                ext_ctx=ext_ctx,
                heartbeat_callback=_hb_cb,
            )
        except (StopRequested, BudgetExceeded):
            raise
        except Exception as ex:
            if is_quota_exception(ex):
                raise
            if cont_left > 0 and is_max_turns_exception(ex):
                # Budget checks
                if _budget_exceeded and callable(_budget_exceeded):
                    if budget_exceeded("total_continuations", bs.get("total_continuations", 0),
                                        int(bc.get("max_total_continuations_per_run") or 0)):
                        if metrics:
                            metrics.event("budget_exceeded", cycle=-1, reason="total_continuations")
                        raise BudgetExceeded("total_continuations")
                    per_task.setdefault(task_key, 0)
                    if budget_exceeded("dev_continuations_per_task", per_task[task_key],
                                        int(bc.get("max_dev_continuations_per_task") or 0)):
                        if metrics:
                            metrics.event("budget_exceeded", cycle=-1, reason="dev_continuations_per_task", task_id=task_id)
                        raise BudgetExceeded("dev_continuations_per_task")

                bs["total_continuations"] = bs.get("total_continuations", 0) + 1
                per_task.setdefault(task_key, 0)
                per_task[task_key] += 1
                if metrics:
                    metrics.event("continuation_attempt", stage=label or stage, task_id=task_id, count=bs["total_continuations"])
                cont_left -= 1
                eprint(f"[CONTINUE] Max turns exceeded for {task_key}; continuing ({cont_left} left)...")

                if "\n[AGENTCLI_CONTINUATION_MARKER]\n" in prompt:
                    prompt = prompt.split("\n[AGENTCLI_CONTINUATION_MARKER]\n")[0] + continuation_msg
                else:
                    prompt = prompt + continuation_msg
                continue
            raise


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main_async_claudecode(args: argparse.Namespace, repo: Path) -> int:
    """Claude Code backend main - full parity with Codex backend (cycle.py)."""

    force_utf8_stdio()

    repo = repo.expanduser().resolve()
    if not repo.exists():
        eprint(f"Repo not found: {repo}")
        return 2

    cfg = _load_claudecode_cfg(args)

    # Run dir
    if getattr(args, "run_dir", ""):
        run_dir = Path(args.run_dir).expanduser().resolve()
    elif bool(getattr(args, "resume_latest", False)):
        latest = find_latest_run_dir(repo)
        run_dir = latest.expanduser().resolve() if latest is not None else make_run_dir(repo)
    else:
        run_dir = make_run_dir(repo)
    run_dir.mkdir(parents=True, exist_ok=True)

    _MAX_SUMMARY_CYCLES = 50  # Keep only the last N cycles in-memory to prevent OOM

    run_summary: dict[str, Any] = {
        "run_id": run_dir.name,
        "repo": str(repo),
        "profile": str(getattr(args, "profile", "personal") or "personal"),
        "cycles": [],
    }

    def _write_run_summary() -> None:
        write_run_summary_file(
            run_summary=run_summary,
            run_dir=run_dir,
            max_summary_cycles=_MAX_SUMMARY_CYCLES,
            warn=eprint,
        )

    def _fail_validation(name: str, value: str) -> None:
        msg = (
            "# Validation failure\n\n"
            f"Blocked unsafe path for `{name}`: `{value}`\n\n"
            "Path traversal patterns like `..` are not allowed. Use an absolute path or a safe relative path.\n"
        )
        safe_write_text(run_dir / "VALIDATION_FAILURE.md", msg)

    for _name, _value in (("prompts_dir", getattr(args, "prompts_dir", "") or ""),):
        if str(_value).strip() and is_unsafe_path(str(_value)):
            _fail_validation(_name, str(_value))
            eprint(f"[STOP] Validation failure for {_name}: {_value}")
            return 2

    source_repo = repo
    source_base_ref = git_head(source_repo)
    worktree_dir: Optional[Path] = None
    if bool(getattr(args, "worktree_isolation", False)):
        worktree_dir = default_worktree_dir(source_repo, run_dir)
        try:
            create_worktree(source_repo, worktree_dir, run_dir=run_dir)
        except Exception as ex:
            eprint(f"[STOP] Failed to create worktree: {ex}")
            return 2
        eprint(f"[INFO] Isolated worktree: {worktree_dir}")
        repo = worktree_dir

    # NOTE: Do NOT call os.chdir(repo) here - it is process-global and
    # thread-unsafe when the runner executes in shell mode's background thread.
    # The SDK receives 'cwd' via ClaudeAgentOptions instead.

    # Observability
    metrics = MetricsLogger(run_dir / "metrics.jsonl")
    logger = create_logger(run_dir, debug=bool(getattr(args, "debug", False)))
    # trace_ctx reserved for future tracing support
    stop_path = run_dir / str(getattr(args, "stop_file", "STOP"))
    cycle_summary_path = run_dir / "cycle_summary.log"
    last_run_summary_path = run_dir / "last_run_summary.json"

    def record_stop_checkpoint(
        *,
        stage: str,
        cycle: int,
        step: int = -1,
        task_id: str = "",
        attempt: int | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        payload = write_stop_snapshot(
            run_dir,
            stage=stage,
            cycle=cycle,
            step=step,
            task_id=task_id,
            attempt=attempt,
            message=message,
            stop_paths=[stop_path],
        )
        try:
            metrics.event(
                "stop_checkpoint",
                cycle=cycle,
                step=step,
                task_id=task_id,
                attempt=attempt if attempt is not None else -1,
                stage=stage,
                reason=str(payload.get("reason") or STOP_REASON_STOP_FILE),
            )
        except Exception:
            pass
        return payload

    # Global PM cache
    from ..config import AGENT_WORK_DIR
    pm_cache_dir = repo / AGENT_WORK_DIR / "PM_CACHE"
    pm_cache_dir.mkdir(parents=True, exist_ok=True)
    analysis_md = pm_cache_dir / "PROJECT_ANALYSIS.md"

    # Docs
    docs_dir = resolve_docs_dir(repo, str(getattr(args, "docs_dir", "") or ""))
    digest_path = (repo / Path(str(getattr(args, "docs_digest_file", ".doc/DOCS_DIGEST.md") or ".doc/DOCS_DIGEST.md"))).resolve()
    digest_rel = digest_path.relative_to(repo).as_posix() if repo in digest_path.parents else digest_path.as_posix()

    docs_read_mode = str(getattr(args, "docs_read_mode", "digest") or "digest")
    if docs_read_mode == "digest":
        if bool(getattr(args, "generate_digest", False)) and docs_dir:
            generate_docs_digest(repo, docs_dir, digest_path)
        elif not digest_path.exists() and docs_dir:
            generate_docs_digest(repo, docs_dir, digest_path)

    # Skills
    skills_cfg = getattr(args, "skills", {}) if isinstance(getattr(args, "skills", {}), dict) else {}
    skills_enabled = bool(skills_cfg.get("enabled", False))
    skills_records: list = []
    skills_index_summary = "(skills disabled)"
    skills_by_id: dict[str, Any] = {}
    if skills_enabled:
        roots = resolve_skills_roots(repo, skills_cfg.get("roots", []))
        skills_records = build_skills_index(roots)
        skills_by_id = {r.skill_id: r for r in skills_records}
        snapshot_dir = resolve_snapshot_dir(run_dir, skills_cfg.get("snapshot_dir", ""))
        write_skills_snapshot(skills_records, snapshot_dir)
        skills_index_summary = summarize_skills_index_capped(
            skills_records,
            max_items=int(skills_cfg.get("pm_summary_max_items", 0) or 0),
            max_chars=int(skills_cfg.get("pm_summary_max_chars", 0) or 0),
        )

    # autopilot flag not needed for Claude backend (no codex_call_hint dependency)

    # Prompt store (same as Codex)
    prompts_dir_raw = str(getattr(args, "prompts_dir", "") or "")
    prompts_dir = (repo / prompts_dir_raw).resolve() if prompts_dir_raw and not Path(prompts_dir_raw).is_absolute() else Path(prompts_dir_raw).resolve() if prompts_dir_raw else (repo / "prompts").resolve()
    store = PromptStore(prompts_dir=prompts_dir)

    pm_instructions = ensure_pm_instructions_have_output_schema(store.get("pm_instructions", PM_INSTRUCTIONS_DEFAULT))
    dev_instructions = store.get("dev_instructions", DEV_INSTRUCTIONS_DEFAULT)
    qa_instructions = store.get("qa_instructions", QA_INSTRUCTIONS_DEFAULT)

    # Gates
    build_enabled = (not bool(getattr(args, "no_build", False))) or bool(getattr(args, "require_build", False))
    stop_on_no_diff = (not bool(getattr(args, "allow_no_diff", False))) or bool(getattr(args, "stop_if_no_diff", False))
    run_tests = bool(getattr(args, "run_tests", False))

    # Policy
    policy_cfg = getattr(args, "policy", {}) if isinstance(getattr(args, "policy", {}), dict) else {}
    _policy_enabled_raw = policy_cfg.get("enabled")
    if _policy_enabled_raw is None:
        policy_scan_enabled = not bool(getattr(args, "no_policy_scan", False))
    else:
        policy_scan_enabled = bool(_policy_enabled_raw)
    policy_fail_severity = str(policy_cfg.get("fail_severity") or "high")
    policy_rules = load_policy_rules(getattr(args, "policy_rules_file", ""), list(getattr(args, "policy_rule", []) or []))
    policy_rules.extend(list(policy_cfg.get("rules", []) or []))
    policy_ignore_paths = list(policy_cfg.get("ignore_paths", []) or [])
    policy_allow_patterns = list(policy_cfg.get("allow_patterns", []) or [])

    # Claude Agent SDK extension context
    from .claude_extensions import ClaudeExtensionContext
    ext_ctx: Optional[ClaudeExtensionContext] = None
    if cfg.mcp_tools_enabled or cfg.hooks_enabled or cfg.can_use_tool_enabled or cfg.subagents_enabled:
        ext_ctx = ClaudeExtensionContext(
            repo=repo,
            run_dir=run_dir,
            stop_path=stop_path,
            logger=logger,
            metrics=metrics,
            args=args,
            debug=bool(getattr(args, "debug", False)),
            policy_rules=policy_rules,
        )

    # Security
    security_cfg = getattr(args, "security", {}) if isinstance(getattr(args, "security", {}), dict) else {}
    security_enabled = bool(security_cfg.get("enabled", False))
    security_fail_severity = str(security_cfg.get("fail_severity") or "high")
    security_rules = load_security_rules(str(security_cfg.get("rules_path") or ""))

    # Scan
    scan_scope = str(getattr(args, "scan_scope", "quick") or "quick").strip().lower()
    policy_scan_scope = str(getattr(args, "policy_scan_scope", "") or "").strip().lower() or scan_scope
    security_scan_scope = str(getattr(args, "security_scan_scope", "") or "").strip().lower() or scan_scope
    scan_max_files = int(getattr(args, "scan_max_files", 500) or 500)
    scan_max_bytes_per_file = int(getattr(args, "scan_max_bytes_per_file", 200_000) or 200_000)
    scan_max_total_bytes = int(getattr(args, "scan_max_total_bytes", 20_000_000) or 20_000_000)
    scan_timeout_seconds = int(getattr(args, "scan_timeout_seconds", 60) or 60)
    scan_ignore_globs = list(getattr(args, "scan_ignore_globs", []) or [])
    if not scan_ignore_globs:
        scan_ignore_globs = list(DEFAULT_SCAN_IGNORE_GLOBS)
    scan_ignore_paths = list(getattr(args, "scan_ignore_paths", []) or [])
    scan_include_untracked_in_full = bool(getattr(args, "scan_include_untracked_in_full", False))

    # Budgets
    budgets_cfg = getattr(args, "budgets", {}) if isinstance(getattr(args, "budgets", {}), dict) else {}
    budget_state: dict[str, Any] = {
        "total_escalations": 0,
        "total_continuations": 0,
        "total_repairs": 0,
        "per_task_escalations": {},
        "per_task_continuations": {},
    }

    # PM state
    pm_fp_path = pm_cache_dir / "PM_LAST_FINGERPRINT.json"
    pm_fp_obj = _load_json_if_exists(pm_fp_path, default={"fingerprint": "", "updated_at": ""})
    last_pm_fp = str(pm_fp_obj.get("fingerprint") or "")

    pm_stop_reason: dict[str, str] = {}

    snapshot_json = pm_cache_dir / "REPO_SNAPSHOT.json"
    snapshot = _load_json_if_exists(snapshot_json, default={"head": "", "updated_at": ""})
    prev_head = (snapshot.get("head") or "").strip()

    dev_hints_dir = run_dir / "analysis_hints"
    dev_hints_dir.mkdir(parents=True, exist_ok=True)
    experience_redaction = ExperienceRedactionSettings.from_source(args)

    continuous = bool(getattr(args, "continuous", False) or getattr(args, "loop", False))

    roles_raw = _coerce_roles_arg(getattr(args, "roles", None))
    plugins_allowlist = getattr(args, "plugins_allowlist", []) or []
    if isinstance(plugins_allowlist, str):
        plugins_allowlist = [p.strip() for p in plugins_allowlist.split(",") if p.strip()]

    stages: list = []
    plugin_failure: Optional[Exception] = None
    try:
        stages = make_stages(
            roles_raw,
            plugins_enabled=bool(getattr(args, "plugins_enabled", False)),
            plugins_allowlist=list(plugins_allowlist),
            plugins_strict=bool(getattr(args, "plugins_strict", True)),
        )
    except Exception as ex:
        plugin_failure = ex
        safe_write_text(run_dir / "PLUGIN_LOAD_FAILURE.md", f"# Plugin load failure\n\n{ex}\n")
        eprint(f"[STOP] Plugin load failure: {ex}")

    if plugin_failure is not None:
        if worktree_dir is not None:
            try:
                remove_worktree(source_repo, worktree_dir)
            except Exception as ex:
                eprint(f"[WARN] Failed to remove worktree: {ex}")
        return 1

    pipeline_mgr = PipelineManager(stages)
    pm_stage_enabled = any((getattr(s, 'name', '') or '').strip().lower() == 'pm' for s in stages)

    def append_cycle_summary(line: str) -> None:
        append_cycle_summary_line(
            cycle_summary_path=cycle_summary_path,
            line=line,
            rotate_log_file_fn=rotate_log_file,
        )

    # Backlog state
    backlog_json_path = run_dir / "BACKLOG.json"
    backlog_md_path = run_dir / "BACKLOG.md"
    state_path = run_dir / "STATE.json"

    # ---------------------------------------------------------------------------
    # Shared helpers (same as Codex)
    # ---------------------------------------------------------------------------

    def _collect_scan(scope: str, *, ignore_paths: Optional[list[str]] = None) -> tuple[list[tuple[str, str]], dict[str, Any]]:
        return collect_scan_with_config(
            repo=repo,
            scope=scope,
            scan_ignore_paths=scan_ignore_paths,
            scan_ignore_globs=scan_ignore_globs,
            scan_max_files=scan_max_files,
            scan_max_bytes_per_file=scan_max_bytes_per_file,
            scan_max_total_bytes=scan_max_total_bytes,
            scan_timeout_seconds=scan_timeout_seconds,
            scan_include_untracked_in_full=scan_include_untracked_in_full,
            ignore_paths=ignore_paths,
        )

    _extract_qa_followups = extract_qa_followups
    _followups_from_structured = followups_from_structured
    _merge_qa_followups = merge_qa_followups

    def _normalize_backlog_tasks(raw_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return normalize_backlog_tasks(raw_tasks, run_dir)

    def _validate_skill_ids(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return validate_skill_ids(
            tasks,
            skills_enabled=skills_enabled,
            skills_by_id=skills_by_id,
            skills_records=skills_records,
            skills_cfg=skills_cfg,
        )

    def ensure_backlog() -> bool:
        return ensure_backlog_artifacts(
            run_dir=run_dir,
            stop_path=stop_path,
            metrics=metrics,
            warn=eprint,
        )

    def load_tasks() -> list[TaskItem]:
        return load_backlog_tasks(
            run_dir=run_dir,
            load_backlog_json_fn=load_backlog_json,
            parse_backlog_md_fn=parse_backlog_md,
            warn=eprint,
        )

    def _load_backlog_context_for_pm() -> tuple[str, list[TaskItem], set[str], set[str]]:
        return load_backlog_context_for_pm(backlog_json_path, backlog_md_path, state_path)

    def _build_failed_tasks_block() -> str:
        return build_failed_tasks_block(state_path, run_dir)

    def _record_history(task_id: str, title: str, status: str, reason: str = "",
                        detail: str = "", files: list[str] | None = None, cycle: int = 0,
                        attempt: int = 0, max_attempts: int = 1, task_status: str = "") -> None:
        record_history(
            repo, run_dir, "claudecode",
            task_id=task_id, title=title, status=status,
            task_status=task_status, reason=reason, detail=detail, files=files,
            cycle=cycle, attempt=attempt, max_attempts=max_attempts,
            task_history_enabled=bool(getattr(args, "task_history_enabled", True)),
        )

    def _record_task_experience_event(
        *,
        task_id: str,
        title: str,
        status: str,
        reason: str = "",
        task_status: str = "",
        cycle_idx: int = 0,
        step_idx: int = 0,
        attempt: int = 0,
        max_attempts: int = 0,
        validation_status: str = "",
        validation_summary: str = "",
        validations: list[dict[str, Any]] | None = None,
        blocked_dependencies: list[dict[str, Any]] | None = None,
        artifact_pointers: list[str] | None = None,
        outcome_action: str = "",
        outcome_note: str = "",
        detail: str = "",
    ) -> None:
        record_task_experience(
            repo,
            run_id=run_dir.name,
            backend="claudecode",
            task_id=task_id,
            title=title,
            status=status,
            reason=reason,
            task_status=task_status,
            cycle_idx=cycle_idx,
            step_idx=step_idx,
            attempt=attempt,
            max_attempts=max_attempts,
            validation_status=validation_status,
            validation_summary=validation_summary,
            validations=validations,
            blocked_dependencies=blocked_dependencies,
            artifact_pointers=artifact_pointers,
            outcome_action=outcome_action,
            outcome_note=outcome_note,
            detail=detail,
        )

    # ---------------------------------------------------------------------------
    # PM phase (structured output with repair - same as Codex)
    # ---------------------------------------------------------------------------

    async def _run_pm_structured(pm_prompt: str, *, max_turns: int, cycle_idx: int, kind: str, output_path: Path) -> PMOutputV2 | None:
        retries = int(getattr(args, "pm_structured_retries", 2))
        max_budget_retries = int(budgets_cfg.get("max_pm_structured_retries") or retries)
        retries = min(retries, max_budget_retries) if max_budget_retries > 0 else retries
        last_raw = ""
        repair_prompt = ""
        for attempt in range(retries + 1):
            prompt = pm_prompt if attempt == 0 else repair_prompt
            try:
                if ext_ctx:
                    ext_ctx.current_stage = "PM"
                pm_max_conts = int(getattr(args, "pm_max_turns_continuations", 1) or 0)
                text, structured = await _run_with_continuations(
                    cfg, prompt, repo=repo, stage="PM",
                    stop_path=stop_path, debug=bool(getattr(args, "debug", False)),
                    model_override=cfg.pm_model,
                    stage_instructions=pm_instructions,
                    max_turns_override=max_turns,
                    timeout_seconds=int(getattr(args, "pm_timeout_seconds", 900) or 900),
                    label="pm", max_continuations=pm_max_conts,
                    task_id=f"pm_{kind}",
                    budget_state=budget_state, budgets_cfg=budgets_cfg,
                    metrics=metrics, _budget_exceeded=budget_exceeded,
                    ext_ctx=ext_ctx,
                )
            except StopRequested:
                raise
            except BudgetExceeded as ex:
                metrics.event("budget_exceeded", cycle=cycle_idx, reason=str(ex))
                return None
            except Exception as ex:
                if is_quota_exception(ex):
                    raise
                logger.stage_event("pm", "error", cycle=cycle_idx, detail=str(ex))
                return None

            if structured is not None:
                try:
                    last_raw = json.dumps(structured, ensure_ascii=False)
                except Exception:
                    last_raw = str(structured)
            else:
                last_raw = text

            try:
                output_path.write_text(last_raw + "\n", encoding="utf-8", errors="replace")
            except Exception:
                pass

            parsed, missing, type_errors = parse_pm_output_with_errors(last_raw, kind_hint=kind)
            if parsed is not None:
                metrics.event("pm_structured_parse", cycle=cycle_idx, attempt=attempt, ok=True)
                return parsed
            metrics.event("pm_structured_parse", cycle=cycle_idx, attempt=attempt, ok=False)

            # Early exit: quota or repetitive garbage - no point retrying
            if "<quota_detected>" in missing:
                logger.stage_event("pm", "quota_detected", cycle=cycle_idx)
                metrics.event("pm_garbage_detected", cycle=cycle_idx, kind="quota")
                raise Exception("quota exceeded - detected in PM output")
            if "<repetitive_output>" in missing:
                eprint("[PM] Repetitive/garbage output detected - aborting PM structured retries.")
                metrics.event("pm_garbage_detected", cycle=cycle_idx, kind="repetitive")
                break

            if attempt < retries:
                repair_limit = int(budgets_cfg.get("max_total_repair_attempts_per_run") or 0)
                if budget_exceeded("total_repairs", budget_state["total_repairs"], repair_limit):
                    metrics.event("budget_exceeded", cycle=cycle_idx, reason="total_repairs")
                    break
                budget_state["total_repairs"] += 1

            repair_prompt = (
                "Your previous response was invalid or did not match the required JSON schema. "
                "Return ONLY a single JSON object with keys: kind, summary, tasks, notes_md, warnings, open_questions, analysis_updated, analysis_path. "
                "No markdown, no prose outside JSON.\n\n"
                f"Validation errors:\n- Missing fields: {', '.join(missing) if missing else '(none)'}\n"
                f"- Type errors: {', '.join(type_errors) if type_errors else '(none)'}\n\n"
                "Previous response (for repair):\n" + last_raw[:8000]
            )

        if last_raw:
            describe_parse_failure(f"pm_{kind}", last_raw)

        # Fallback: file-based artifacts
        try:
            bj = run_dir / "BACKLOG.json"
            if bj.exists():
                fb_tasks = load_backlog_json(bj)
                if fb_tasks:
                    notes_md = None
                    notes_p = run_dir / "NOTES.md"
                    if notes_p.exists():
                        try:
                            notes_md = notes_p.read_text(encoding="utf-8-sig", errors="replace")
                        except Exception:
                            notes_md = notes_p.read_text(encoding="utf-8", errors="replace")
                    return PMOutputV2(
                        kind=kind,
                        summary="PM output JSON did not validate; loaded tasks from run_dir/BACKLOG.json.",
                        tasks=[{"id": t.id, "title": t.title, "prompt": t.prompt, "files": t.files, "done_when": t.done_when or "Git diff exists and build passes."} for t in fb_tasks],
                        notes_md=notes_md,
                        warnings=[], open_questions=[],
                        analysis_updated=False, analysis_path=str(analysis_md),
                    )
        except Exception:
            pass
        return None

    async def run_pm_if_needed(cycle_idx: int, curr_head: str, changed_files: list[str], repo_fp: str, force_refresh_backlog: bool = False) -> bool:
        nonlocal last_pm_fp, prev_head

        need_bootstrap = not analysis_md.exists()
        need_incremental = False
        force_refresh = bool(force_refresh_backlog)

        if not need_bootstrap:
            if changed_files:
                need_incremental = True
            elif bool(getattr(args, "pm_include_working_tree", False)) and repo_fp and repo_fp != last_pm_fp:
                need_incremental = True
            if bool(getattr(args, "pm_refresh_backlog", False)):
                pm_refresh_every = int(getattr(args, "pm_refresh_every_cycles", 0) or 0)
                if pm_refresh_every and pm_refresh_every > 0:
                    if (cycle_idx % pm_refresh_every) == 0:
                        force_refresh = True

        if stop_path.exists():
            return True

        pm_output_path = run_dir / f"pm_final_output_cycle_{cycle_idx:03d}.txt"

        inv_md = prepare_pm_inventory_markdown(
            repo=repo,
            run_dir=run_dir,
            pm_cache_dir=pm_cache_dir,
            cycle_idx=cycle_idx,
            metrics=metrics,
            build_repo_inventory_fn=build_repo_inventory,
            write_repo_inventory_files_fn=write_repo_inventory_files,
        )

        todo_path, todo_text = read_current_todo(repo)
        todo_block = format_todo_block(todo_path, todo_text)

        # Goals context
        goals_block, goals_instruction = build_goals_prompt_context(
            repo=repo,
            goals_enabled=bool(getattr(args, "goals_enabled", True)),
            goals_auto_generate=bool(getattr(args, "goals_auto_generate", True)),
            read_goals_fn=read_goals,
            format_goals_block_fn=format_goals_block,
            goals_evaluation_instruction=GOALS_EVALUATION_INSTRUCTION,
            goals_generation_instruction=GOALS_GENERATION_INSTRUCTION,
        )
        experience_repo = source_repo if worktree_dir is not None else repo
        pm_experience_summary = load_pm_experience_summary(experience_repo, run_dir, args=args)

        try:
            if need_bootstrap:
                metrics.event("pm_start", cycle=cycle_idx, kind="bootstrap")
                experience_summary_block = render_pm_experience_summary_from_run(
                    run_dir,
                    settings=experience_redaction,
                    repo_root=repo,
                )
                _hist_enabled = bool(getattr(args, "task_history_enabled", True))
                _hist_max = int(getattr(args, "task_history_max_items", 50) or 50)
                if _hist_enabled:
                    _done_blk, _failed_blk = _format_split_history_blocks(repo, max_items=_hist_max)
                else:
                    _done_blk, _failed_blk = "(disabled)", "(disabled)"
                _pm_max_turns_boot = int(getattr(args, "pm_bootstrap_max_turns", 28) or 28)
                ctx = {
                    "analysis_md": str(analysis_md), "inv_md": str(inv_md),
                    "repo": str(repo), "run_dir": str(run_dir),
                    "todo_block": todo_block,
                    "docs_dir": str(docs_dir) if docs_dir else "(none)",
                    "docs_read_mode": docs_read_mode, "digest_rel": str(digest_rel),
                    "skills_index_summary": skills_index_summary,
                    "codex_call_hint": "Use Claude Code built-in tools (Read, Write, Edit, Grep, Glob, Bash) directly. Do NOT call Codex MCP.",
                    "pm_experience_summary": pm_experience_summary,
                    "task_history_block": _format_history_block(repo, max_items=_hist_max) if _hist_enabled else "(disabled)",
                }
                pm_prompt = _patch_prompt_for_claude(append_pm_essential_context(
                    append_pm_output_contract(store.render("pm_bootstrap_prompt", PM_BOOTSTRAP_TEMPLATE_DEFAULT, ctx)),
                    turn_budget_warning=PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {_pm_max_turns_boot} turns)"),
                    done_tasks_block=_done_blk,
                    failed_tasks_block=_failed_blk,
                    goals_block=goals_block,
                    goals_instruction=goals_instruction,
                    experience_summary_block=experience_summary_block,
                ))
                pm_out = await _run_pm_structured(pm_prompt, max_turns=_pm_max_turns_boot, cycle_idx=cycle_idx, kind="bootstrap", output_path=pm_output_path)
                if pm_out is None:
                    metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=1, error="structured_output_failed")
                    return False

                write_pm_output_artifacts(
                    run_dir=run_dir,
                    cycle_idx=cycle_idx,
                    pm_output_model_dump=pm_out.model_dump(),
                    notes_md=pm_out.notes_md,
                    dump_pretty_fn=dump_pretty,
                )

                _current_backlog_block, existing_tasks, done_ids, failed_ids = _load_backlog_context_for_pm()
                _pre_pm_tasks = list(existing_tasks)  # recycled ID 비교용 스냅샷
                merged_tasks = merge_pm_tasks_with_existing_pending(
                    pm_tasks=[t.model_dump() for t in (pm_out.tasks or [])],
                    existing_tasks=existing_tasks,
                    done_ids=done_ids,
                    failed_ids=failed_ids,
                )
                if merged_tasks:
                    merged_tasks = _normalize_backlog_tasks(merged_tasks)
                    merged_tasks = _validate_skill_ids(merged_tasks)
                    if merged_tasks:
                        try:
                            existing_tasks_2 = load_tasks()
                            state_obj = load_state(state_path)
                            done_ids_2 = set(state_obj.get("done", []) or [])
                            qa_followups = [{"id": t.id, "title": t.title, "prompt": t.prompt, "files": t.files, "done_when": t.done_when, "skills": t.skills, "skills_rationale": t.skills_rationale, "depends_on": t.depends_on} for t in existing_tasks_2 if t.id.startswith("QA-FU-") and t.id not in done_ids_2]
                            if qa_followups:
                                merged_tasks = _merge_qa_followups(merged_tasks, qa_followups, done_ids_2)
                        except Exception:
                            pass
                        write_backlog_files(run_dir, merged_tasks)
                        # Recycled ID 감지: PM이 기존 done 태스크 ID를 새 내용으로 재사용했는지 확인
                        try:
                            _new_tasks = load_tasks()
                            _st = load_state(state_path)
                            _ds = set(_st.get("done", []))
                            if _ds & {t.id for t in _new_tasks}:
                                detect_and_clear_recycled_ids(
                                    prev_tasks=_pre_pm_tasks, new_tasks=_new_tasks,
                                    done_set=_ds, state=_st, state_path=state_path,
                                    save_state_fn=save_state,
                                    on_changed_fn=lambda ids: eprint(f"[RECYCLE] Cleared {len(ids)} recycled task IDs with new content: {sorted(ids)}"),
                                )
                        except Exception:
                            pass

                last_pm_fp = repo_fp or last_pm_fp
                pm_fp_path.write_text(json.dumps({"fingerprint": last_pm_fp, "updated_at": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
                metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=0)
                return True

            if need_incremental or force_refresh:
                metrics.event("pm_start", cycle=cycle_idx, kind="incremental" if need_incremental else "refresh")
                changed_files_block = "\n".join([f"- {p}" for p in (changed_files or [])]) or "- (none)"
                experience_summary_block = render_pm_experience_summary_from_run(
                    run_dir,
                    settings=experience_redaction,
                    repo_root=repo,
                )
                hint_block = "(see <pm_experience_summary>)" if experience_summary_block not in {"(none)", "(disabled)"} else "(none)"
                current_backlog_block, _, _, _ = _load_backlog_context_for_pm()
                failed_tasks_block = _build_failed_tasks_block()

                # Collect build warnings from latest build log
                _build_warnings: list[str] = []
                _latest_build_logs = sorted(run_dir.glob("**/build.txt"), key=lambda x: x.stat().st_mtime)
                if _latest_build_logs:
                    _build_warnings = extract_build_warnings(_latest_build_logs[-1])
                build_warnings_block = "\n".join(_build_warnings) if _build_warnings else "(none)"

                _hist_enabled_i = bool(getattr(args, "task_history_enabled", True))
                _hist_max_i = int(getattr(args, "task_history_max_items", 50) or 50)
                if _hist_enabled_i:
                    _done_blk_i, _failed_blk_i = _format_split_history_blocks(repo, max_items=_hist_max_i)
                else:
                    _done_blk_i, _failed_blk_i = "(disabled)", "(disabled)"
                _pm_max_turns_inc = int(getattr(args, "pm_incremental_max_turns", 18) or 18)
                ctx = {
                    "analysis_md": str(analysis_md), "inv_md": str(inv_md),
                    "repo": str(repo), "run_dir": str(run_dir),
                    "todo_block": todo_block,
                    "docs_dir": str(docs_dir) if docs_dir else "(none)",
                    "docs_read_mode": docs_read_mode, "digest_rel": str(digest_rel),
                    "skills_index_summary": skills_index_summary,
                    "codex_call_hint": "Use Claude Code built-in tools (Read, Write, Edit, Grep, Glob, Bash) directly. Do NOT call Codex MCP.",
                    "prev_head": prev_head or curr_head, "curr_head": curr_head,
                    "changed_files_block": changed_files_block,
                    "current_backlog_block": current_backlog_block,
                    "failed_tasks_block": failed_tasks_block,
                    "hint_block": hint_block,
                    "pm_experience_summary": pm_experience_summary,
                    "task_history_block": _format_history_block(repo, max_items=_hist_max_i) if _hist_enabled_i else "(disabled)",
                }
                pm_prompt = _patch_prompt_for_claude(append_pm_essential_context(
                    append_pm_output_contract(store.render("pm_incremental_prompt", PM_INCREMENTAL_TEMPLATE_DEFAULT, ctx)),
                    turn_budget_warning=PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {_pm_max_turns_inc} turns)"),
                    done_tasks_block=_done_blk_i,
                    failed_tasks_block=_failed_blk_i,
                    goals_block=goals_block,
                    goals_instruction=goals_instruction,
                    build_warnings_block=build_warnings_block,
                    experience_summary_block=experience_summary_block,
                ))
                pm_out = await _run_pm_structured(pm_prompt, max_turns=_pm_max_turns_inc, cycle_idx=cycle_idx, kind="incremental" if need_incremental else "refresh", output_path=pm_output_path)
                if pm_out is None:
                    metrics.event("pm_end", cycle=cycle_idx, kind="incremental" if need_incremental else "refresh", rc=1, error="structured_output_failed")
                    return False

                write_pm_output_artifacts(
                    run_dir=run_dir,
                    cycle_idx=cycle_idx,
                    pm_output_model_dump=pm_out.model_dump(),
                    notes_md=pm_out.notes_md,
                    dump_pretty_fn=dump_pretty,
                )

                _current_backlog_block, existing_tasks, done_ids, failed_ids = _load_backlog_context_for_pm()
                _pre_pm_tasks_inc = list(existing_tasks)
                merged_tasks = merge_pm_tasks_with_existing_pending(
                    pm_tasks=[t.model_dump() for t in (pm_out.tasks or [])],
                    existing_tasks=existing_tasks,
                    done_ids=done_ids,
                    failed_ids=failed_ids,
                )
                if merged_tasks:
                    merged_tasks = _normalize_backlog_tasks(merged_tasks)
                    merged_tasks = _validate_skill_ids(merged_tasks)
                    if merged_tasks:
                        write_backlog_files(run_dir, merged_tasks)
                        # Recycled ID 감지
                        try:
                            _new_tasks = load_tasks()
                            _st = load_state(state_path)
                            _ds = set(_st.get("done", []))
                            if _ds & {t.id for t in _new_tasks}:
                                detect_and_clear_recycled_ids(
                                    prev_tasks=_pre_pm_tasks_inc, new_tasks=_new_tasks,
                                    done_set=_ds, state=_st, state_path=state_path,
                                    save_state_fn=save_state,
                                    on_changed_fn=lambda ids: eprint(f"[RECYCLE] Cleared {len(ids)} recycled task IDs with new content: {sorted(ids)}"),
                                )
                        except Exception:
                            pass

                last_pm_fp = repo_fp or last_pm_fp
                pm_fp_path.write_text(json.dumps({"fingerprint": last_pm_fp, "updated_at": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
                metrics.event("pm_end", cycle=cycle_idx, kind="incremental" if need_incremental else "refresh", rc=0)
                return True

            metrics.event("pm_skip", cycle=cycle_idx)
            return True
        except (StopRequested, BudgetExceeded):
            raise
        except Exception as ex:
            eprint(f"[PM ERROR] {ex}")
            if is_quota_exception(ex):
                pm_stop_reason["reason"] = STOP_REASON_QUOTA
                try:
                    stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass
                metrics.event("runner_stop", cycle=cycle_idx, reason=STOP_REASON_QUOTA)
                try:
                    await write_shutdown_report(STOP_REASON_QUOTA, cycle=cycle_idx, step=-1)
                except Exception:
                    pass
            metrics.event("pm_end", cycle=cycle_idx, rc=1, error=str(ex))
            return False

    # ---------------------------------------------------------------------------
    # Dev loop (full parity with Codex - escalation, continuations, policy scan)
    # ---------------------------------------------------------------------------

    policy_scan_summary: Optional[dict[str, Any]] = None
    security_scan_summary: Optional[dict[str, Any]] = None

    async def run_dev_loop(
        cycle_idx: int, tasks: list[TaskItem],
        curr_head: str, changed_files: list[str], repo_fp: str, cycle_t0: float,
    ) -> tuple[int, str, int, bool]:
        nonlocal policy_scan_summary

        state = load_state(state_path)
        done_set = set(state.get("done", []))
        skipped_set: set[str] = set()  # Track skipped/failed tasks separately from done
        task_results: list[dict] = []  # Per-task results for cycle-end progress report
        token_tracker = TokenTracker()  # Per-cycle token usage accumulator
        task_ids = {t.id for t in tasks}
        class _ScopedDoneSet(set[str]):
            def __len__(self) -> int:
                return len(set.intersection(self, task_ids))

        done_set = _ScopedDoneSet(done_set)
        before_done = len(done_set.intersection(task_ids))

        pm_refresh = await maybe_refresh_tasks_after_pm(
            pm_stage_enabled=pm_stage_enabled,
            pm_refresh_backlog=bool(getattr(args, "pm_refresh_backlog", False)),
            cycle_idx=cycle_idx,
            curr_head=curr_head,
            changed_files=changed_files,
            repo_fp=repo_fp,
            before_done=before_done,
            tasks=tasks,
            task_ids=task_ids,
            done_set=done_set,
            state=state,
            state_path=state_path,
            run_pm_if_needed_fn=run_pm_if_needed,
            pm_stop_reason=pm_stop_reason,
            stop_reason_quota=STOP_REASON_QUOTA,
            stop_reason_stop_file=STOP_REASON_STOP_FILE,
            stop_reason_pm_refresh_no_backlog=STOP_REASON_PM_REFRESH_NO_BACKLOG,
            stop_path=stop_path,
            detect_stop_reason_fn=detect_stop_reason,
            ensure_backlog_fn=ensure_backlog,
            load_tasks_fn=load_tasks,
            save_state_fn=save_state,
            on_recycled_ids_changed_fn=lambda truly_new: eprint(
                f"[PM-REFRESH] Clearing {len(truly_new)} recycled task IDs with new content: {sorted(truly_new)}"
            ),
            on_recycled_ids_unchanged_fn=lambda recycled_ids: eprint(
                f"[PM-REFRESH] {len(recycled_ids)} task IDs unchanged - keeping done status."
            ),
        )
        if pm_refresh.should_return:
            return pm_refresh.rc, pm_refresh.reason, pm_refresh.done_delta, pm_refresh.ran_tasks
        tasks = pm_refresh.tasks
        task_ids = pm_refresh.task_ids
        done_set = pm_refresh.done_set
        before_done = pm_refresh.before_done

        tasks_root = run_dir / "tasks"
        tasks_root.mkdir(parents=True, exist_ok=True)

        iterations = int(getattr(args, "iterations", 10) or 10)

        # --- Cycle-start git health check ---
        ensure_clean_working_tree(repo)

        # --- Pre-cycle build health check ---
        if build_enabled and not stop_path.exists():
            pre_build_log = run_dir / "pre_cycle_build.txt"
            pre_build_ok = await run_build_gate_async(
                repo=repo, build_cmd=getattr(args, "build_cmd", []),
                build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                log_path=pre_build_log, stop_path=stop_path,
                command_repo=source_repo,
            )
            if not pre_build_ok:
                eprint("[BUILD-FIX] Build is broken before tasks start. Running auto-fix...")
                metrics.event("build_fix_start", cycle=cycle_idx)
                build_errors = pre_build_log.read_text(encoding="utf-8", errors="replace")
                error_lines = [ln for ln in build_errors.splitlines() if "error " in ln.lower()]
                error_summary = "\n".join(error_lines[:80]) or build_errors[-3000:]

                build_fix_prompt = _patch_prompt_for_claude(
                    f"The project at {repo} does NOT build. You must fix ALL build errors before any feature work.\n\n"
                    f"Build output (errors only):\n```\n{error_summary}\n```\n\n"
                    "Instructions:\n"
                    "1. Read the failing files and fix each error\n"
                    "2. After fixing, run the build command to verify\n"
                    "3. Keep fixing until the build succeeds with 0 errors\n"
                    "4. Do NOT add new features - only fix build errors\n"
                )

                build_fix_max_turns = int(getattr(args, "max_turns_per_task", 12) or 12) * 2
                build_fix_model = cfg.dev_model or cfg.model
                if ext_ctx:
                    ext_ctx.current_stage = "BuildFix"
                try:
                    await _run_with_continuations(
                        cfg, build_fix_prompt, repo=repo, stage="BuildFix",
                        stop_path=stop_path, debug=bool(getattr(args, "debug", False)),
                        model_override=build_fix_model,
                        stage_instructions=dev_instructions,
                        max_turns_override=build_fix_max_turns,
                        timeout_seconds=int(getattr(args, "dev_timeout_seconds", 600) or 600),
                        label="build_fix", max_continuations=int(getattr(args, "dev_max_turns_continuations", 0) or 0),
                        task_id="__build_fix__",
                        budget_state=budget_state, budgets_cfg=budgets_cfg,
                        metrics=metrics, _budget_exceeded=budget_exceeded,
                        ext_ctx=ext_ctx,
                    )
                except Exception as bfx:
                    eprint(f"[BUILD-FIX] Auto-fix agent error: {bfx}")

                # Verify build after fix attempt
                post_fix_ok = await run_build_gate_async(
                    repo=repo, build_cmd=getattr(args, "build_cmd", []),
                    build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                    legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                    log_path=run_dir / "pre_cycle_build_post_fix.txt", stop_path=stop_path,
                    command_repo=source_repo,
                )
                if post_fix_ok:
                    eprint("[BUILD-FIX] Build fixed successfully!")
                    metrics.event("build_fix_end", cycle=cycle_idx, rc=0)
                else:
                    eprint("[BUILD-FIX] Build still broken after fix attempt.")
                    metrics.event("build_fix_end", cycle=cycle_idx, rc=1)
                    # In continuous mode, proceed anyway - partial fixes may help
                    # In non-continuous mode, also proceed - tasks may fix remaining issues

        for step in range(iterations):
            if stop_path.exists():
                break

            max_consecutive_failures = int(getattr(args, "max_consecutive_task_failures", 3) or 3)
            next_task: Optional[TaskItem] = select_next_task_with_dependency_checks(
                tasks=tasks,
                done_set=done_set,
                skipped_set=skipped_set,
                state=state,
                state_path=state_path,
                cycle_idx=cycle_idx,
                max_consecutive_failures=max_consecutive_failures,
                task_history_enabled=bool(getattr(args, "task_history_enabled", True)),
                count_consecutive_title_failures_fn=lambda title: _count_consecutive_title_failures(
                    repo,
                    title,
                    excluded_task_statuses=(TASK_STATUS_BLOCKED_ENV, "test_contract_changed"),
                ),
                save_state_fn=save_state,
                record_history_fn=_record_history,
                logger=logger,
                metrics=metrics,
                eprint_fn=eprint,
                task_results=task_results,
                step_idx=step,
                record_task_experience_fn=_record_task_experience_event,
            )
            if not next_task:
                break

            task_dir = tasks_root / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}"
            task_dir.mkdir(parents=True, exist_ok=True)

            metrics.event("task_start", cycle=cycle_idx, step=step, task_id=next_task.id)
            task_outer_t0 = time.time()
            task_head_before = git_head(repo)
            task_already_implemented = False

            tb: Optional[TaskBranch] = None
            cp: Optional[RepoCheckpoint] = None
            if bool(getattr(args, "isolate_task", False)):
                try:
                    tb = create_task_branch(repo, next_task.id, task_title=next_task.title)
                    metrics.event("task_branch_created", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name)
                except Exception as _tb_ex:
                    eprint(f"[WARN] Task branch creation failed ({_tb_ex}); falling back to checkpoint")
                    metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id)
                    cp = create_checkpoint(repo, task_dir / "checkpoint")
                    metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0)

            # Dev model tiering
            dev_auto_escalate = bool(getattr(args, "dev_auto_escalate", False))
            dev_max_escalations = int(getattr(args, "dev_max_escalations", 0) or 0)
            dev_escalate_on = set(getattr(args, "dev_escalate_on", []) or [])
            per_task_escalations = budget_state["per_task_escalations"]
            per_task_escalations.setdefault(next_task.id, 0)
            max_escalations_per_task_budget = int(budgets_cfg.get("max_dev_escalations_per_task") or 0)
            tiers, max_attempts, dev_max_escalations = compute_dev_model_tiers(
                base_model=cfg.dev_model,
                tier1_model=cfg.dev_model_tier1,
                tier2_model=cfg.dev_model_tier2,
                dev_auto_escalate=dev_auto_escalate,
                dev_max_escalations=dev_max_escalations,
                max_escalations_per_task_budget=max_escalations_per_task_budget,
            )

            if dev_auto_escalate and not tb and not cp:
                try:
                    tb = create_task_branch(repo, next_task.id, task_title=next_task.title)
                    metrics.event("task_branch_created", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name, reason="retry_escalation")
                except Exception:
                    metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id, reason="retry_escalation")
                    cp = create_checkpoint(repo, task_dir / "checkpoint")
                    metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, reason="retry_escalation")

            task_completed = False
            task_failure_reason = ""
            _prev_gate_error: str = ""  # Carried across attempts for gate-aware retry
            _prev_gate_error_label: str = ""
            _blocked_env_guides_written: set[tuple[str, str, int]] = set()
            test_validation_result: dict[str, Any] | None = None
            fast_regression_triggered = False
            task_validation_status = "validation_pending"

            def _task_failure_status(
                reason: str,
                *,
                validations: list[dict[str, Any]] | None = None,
                detail: str = "",
            ) -> str:
                return classify_task_failure(reason, validations=validations or [], detail=detail)

            def _record_failed_state(
                reason: str,
                *,
                validations: list[dict[str, Any]] | None = None,
                detail: str = "",
                task_status: str = "",
                **extra: Any,
            ) -> dict[str, Any] | None:
                return record_task_failure_state(
                    state,
                    task_id=next_task.id,
                    reason=reason,
                    task_status=task_status,
                    validations=validations or [],
                    detail=detail,
                    extra=extra,
                )

            def _record_pending_review(
                reason: str,
                *,
                task_status: str = "",
                detail: str = "",
                branch: str = "",
                rescue_branch: str = "",
            ) -> None:
                outcome_status = task_status or _task_failure_status(reason, detail=detail)
                record_task_failure_state(
                    state,
                    bucket="pending_review",
                    task_id=next_task.id,
                    reason=reason,
                    task_status=outcome_status,
                    detail=detail,
                    extra={
                        "title": next_task.title,
                        "cycle": cycle_idx,
                        "step": step,
                        "attempt": attempt + 1,
                        "max_attempts": max_attempts,
                        "branch": branch,
                        "rescue_branch": rescue_branch,
                    },
                )

            def _record_failed_task_result(
                reason: str,
                *,
                task_status: str,
                validations: list[dict[str, Any]] | None = None,
                detail: str = "",
                validation_artifact: str = "",
            ) -> None:
                validation_artifacts = [validation_artifact] if validation_artifact else []
                if task_status == TASK_STATUS_BLOCKED_ENV and reason != "needs_dependency":
                    guide_key = (next_task.id, reason, attempt)
                    if guide_key not in _blocked_env_guides_written:
                        _blocked_env_guides_written.add(guide_key)
                        guide_path = run_dir / "DEPENDENCIES_NEEDED.md"
                        branch = tb.branch_name if tb else ""
                        log_hint = validation_artifact or str(attempt_dir)
                        try:
                            with guide_path.open("a", encoding="utf-8", errors="replace") as f:
                                f.write(
                                    f"\n## {next_task.id}: {next_task.title}\n\n"
                                    f"- reason: {reason}\n"
                                    f"- task_status: {task_status}\n"
                                    f"- branch: {branch or '(none)'}\n"
                                    f"- attempt: {attempt + 1}/{max_attempts}\n"
                                    f"- evidence: {log_hint}\n"
                                    f"- detail: {detail or '(none)'}\n\n"
                                    "---\n"
                                )
                        except Exception:
                            pass
                record_task_failure_result(
                    task_results,
                    task_id=next_task.id,
                    task_title=next_task.title,
                    reason=reason,
                    duration=time.time() - task_outer_t0,
                    task_status=task_status,
                    validations=validations,
                    detail=detail,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    validation_artifact=validation_artifact,
                    validation_status="failed",
                )
                _record_task_experience_event(
                    task_id=next_task.id,
                    title=next_task.title,
                    status="failed",
                    reason=reason,
                    task_status=task_status,
                    cycle_idx=cycle_idx,
                    step_idx=step,
                    attempt=attempt + 1,
                    max_attempts=max_attempts,
                    validation_status="validation_failed" if validation_artifacts else "",
                    validation_summary=detail,
                    artifact_pointers=validation_artifacts,
                    outcome_action="preserved_for_review" if should_preserve_for_review(task_status) else "discarded",
                    detail=detail,
                )

            def _write_task_validation_artifact(
                *,
                validations: list[dict[str, Any]],
                status: str,
                reason: str,
                detail: str = "",
                task_status: str = "",
            ) -> Path:
                return write_task_validation_artifacts(
                    attempt_dir=attempt_dir,
                    task_id=next_task.id,
                    task_title=next_task.title,
                    task_files=next_task.files,
                    cycle=cycle_idx,
                    step=step,
                    attempt=attempt,
                    validations=validations,
                    status=status,
                    reason=reason,
                    detail=detail,
                    task_status=task_status,
                )

            def _isolate_or_stop(reason: str, *, task_status: str = "", detail: str = "") -> tuple[bool, str]:
                """Isolate failed task work.

                Mirrors the Codex backend semantics: for non-preserve outcomes the branch
                is abandoned (non-destructive). For preserve-for-review statuses the
                branch action is the same but metrics/log are tagged ``preserved`` so
                operators can find the work on the same branch.
                """
                outcome_status = task_status or _task_failure_status(reason, detail=detail)
                preserve = should_preserve_for_review(outcome_status)
                if tb:
                    try:
                        abandon_task_branch(repo, tb)
                        _record_pending_review(reason, task_status=outcome_status, detail=detail, branch=tb.branch_name)
                        save_state(state_path, state)
                        event_name = "task_branch_preserved" if preserve else "task_branch_abandoned"
                        metrics.event(event_name, cycle=cycle_idx, step=step, task_id=next_task.id,
                                      reason=reason, branch=tb.branch_name,
                                      task_status=outcome_status, preserved=preserve)
                        if preserve:
                            eprint(f"[PRESERVE] {next_task.id} work kept on branch {tb.branch_name} for review (status={outcome_status}).")
                        return True, ""
                    except Exception as ex:
                        detail = str(ex)
                        eprint(f"[WARN] abandon_task_branch failed: {detail}")
                        abandon_status = _task_failure_status("abandon_failed", detail=detail)
                        _record_failed_state("abandon_failed", detail=detail, task_status=abandon_status)
                        save_state(state_path, state)
                        _record_history(next_task.id, next_task.title, "failed", reason="abandon_failed", detail=detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=abandon_status)
                        metrics.event("task_branch_abandon_failed", cycle=cycle_idx, step=step, task_id=next_task.id, reason=reason, detail=detail)
                        return False, "abandon_failed"
                if not cp:
                    _record_pending_review(reason, task_status=outcome_status, detail=detail)
                    save_state(state_path, state)
                    return True, ""
                try:
                    rescue_branch = restore_checkpoint(
                        repo, cp,
                        dangerous=bool(getattr(args, "dangerous_git_rollback", False)),
                        run_dir=run_dir, stop_path=stop_path,
                        task_id=next_task.id,
                    )
                    metrics.event("rollback", cycle=cycle_idx, step=step, task_id=next_task.id, reason=reason,
                                  rescue_branch=rescue_branch or "")
                    if rescue_branch:
                        eprint(f"[INFO] Work preserved in branch: {rescue_branch}")
                    _record_pending_review(reason, task_status=outcome_status, detail=detail, rescue_branch=rescue_branch or "")
                    save_state(state_path, state)
                    return True, ""
                except Exception as ex:
                    detail = str(ex)
                    blocked = "blocked" in detail.lower()
                    fail_reason = "rollback_blocked" if blocked else "rollback_failed"
                    rollback_status = _task_failure_status(fail_reason, detail=detail)
                    _record_failed_state(fail_reason, detail=detail, task_status=rollback_status)
                    save_state(state_path, state)
                    _record_history(next_task.id, next_task.title, "failed", reason=fail_reason, detail=detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=rollback_status)
                    metrics.event("rollback_failed", cycle=cycle_idx, step=step, task_id=next_task.id, reason=reason, detail=detail)
                    eprint(f"[STOP] Rollback {fail_reason}: {detail}")
                    return False, fail_reason

            task_stop_recorded = False

            def _record_task_stop(stage: str, attempt_num: int | None = None) -> None:
                nonlocal task_stop_recorded
                if task_stop_recorded:
                    return
                task_stop_recorded = True
                detail = "Stop requested; partial task artifacts and worktree state were preserved."
                state.setdefault("warnings", []).append(
                    {
                        "task": next_task.id,
                        "reason": STOP_REASON_STOP_FILE,
                        "detail": detail,
                        "cycle": cycle_idx,
                        "step": step,
                        "attempt": attempt_num,
                    }
                )
                save_state(state_path, state)
                task_results.append(
                    {
                        "id": next_task.id,
                        "title": next_task.title,
                        "status": "stopped",
                        "reason": STOP_REASON_STOP_FILE,
                        "duration": time.time() - task_outer_t0,
                        "attempt": attempt_num if attempt_num is not None else 0,
                        "max_attempts": max_attempts,
                    }
                )
                metrics.event(
                    "task_stop_requested",
                    cycle=cycle_idx,
                    step=step,
                    task_id=next_task.id,
                    attempt=attempt_num if attempt_num is not None else -1,
                    stage=stage,
                    reason=STOP_REASON_STOP_FILE,
                )
                logger.stop_event(
                    f"Stop requested during task {next_task.id}",
                    task_id=next_task.id,
                    attempt=attempt_num,
                    stage=stage,
                )
                record_stop_checkpoint(
                    stage=stage,
                    cycle=cycle_idx,
                    step=step,
                    task_id=next_task.id,
                    attempt=attempt_num,
                    message=detail,
                )

            for attempt in range(max_attempts):
                if stop_path.exists():
                    _record_task_stop("dev_before_attempt", attempt)
                    return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)

                if attempt > 0 and dev_auto_escalate:
                    if budget_exceeded("total_escalations", budget_state["total_escalations"], int(budgets_cfg.get("max_total_escalations_per_run") or 0)):
                        metrics.event("budget_exceeded", cycle=cycle_idx, step=step, task_id=next_task.id, reason="total_escalations")
                        return 1, "budget_exceeded", 0, (len(done_set) > before_done)
                    budget_state["total_escalations"] += 1
                    per_task_escalations[next_task.id] += 1
                    metrics.event("escalate_attempt", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)

                if attempt > 0 and (tb or cp):
                    if tb:
                        try:
                            reset_task_branch(repo, tb)
                            metrics.event("task_branch_reset", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)
                        except Exception as _rst_ex:
                            eprint(f"[WARN] reset_task_branch failed: {_rst_ex}; skipping task.")
                            if not continuous:
                                return 1, "branch_reset_failed", 0, (len(done_set) > before_done)
                            skipped_set.add(next_task.id)
                            break
                    else:
                        ok, fail_reason = _isolate_or_stop("retry")
                        if not ok:
                            if not continuous:
                                return 1, fail_reason, 0, (len(done_set) > before_done)
                            eprint(f"[WARN] Rollback {fail_reason} during retry for {next_task.id}; skipping task.")
                            skipped_set.add(next_task.id)
                            break

                model_name = tiers[attempt]
                attempt_dir = task_dir / f"attempt_{attempt:02d}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                validation_records: list[dict[str, Any]] = []

                before = git_porcelain(repo)
                before_untracked = set(git_untracked_files(repo))
                analysis_hint_out = dev_hints_dir / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.md"
                files_hint = "\n".join([f"- {f}" for f in (next_task.files or [])]) or "- (unspecified)"
                skills_context = _format_skill_selection(next_task.skills or [], skills_by_id)
                dev_ctx = {
                    "repo": str(repo), "run_dir": str(run_dir),
                    "task_id": next_task.id, "task_title": next_task.title,
                    "task_prompt": next_task.prompt, "files_hint": files_hint,
                    "skills_context": skills_context,
                    "done_when": next_task.done_when or "(unspecified)",
                    "docs_read_mode": docs_read_mode, "digest_rel": str(digest_rel),
                    "analysis_hint_out": str(analysis_hint_out),
                    "codex_call_hint": "Use Claude Code built-in tools (Read, Write, Edit, Grep, Glob, Bash) directly. Do NOT call Codex MCP.",
                }
                dev_prompt = _patch_prompt_for_claude(store.render("dev_task_prompt", DEV_TASK_TEMPLATE_DEFAULT, dev_ctx))

                # Inject gate failure context from a previous failed attempt.
                gate_error_for_prompt = _prev_gate_error
                gate_error_label = _prev_gate_error_label or "VERIFICATION FAILED"
                _prev_gate_error = ""
                _prev_gate_error_label = ""
                if gate_error_for_prompt:
                    dev_prompt = dev_prompt + (
                        f"\n\n[{gate_error_label}] The previous attempt failed a verification gate. "
                        f"Fix these errors:\n```\n{gate_error_for_prompt}\n```\n"
                        "Fix this verification failure first, then complete the task."
                    )

                metrics.event("dev_attempt_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, model=model_name)
                logger.set_context(cycle=cycle_idx, step=step, model=model_name, max_turns=int(getattr(args, "max_turns_per_task", 12) or 12), timeout_sec=0)
                logger.task_start(task_id=next_task.id, task_title=next_task.title, attempt=attempt, files=next_task.files or [])

                task_start_time = time.time()
                dev_exc: Optional[Exception] = None
                dev_is_max_turns = False
                dev_quota_exhausted = False
                dev_final = ""

                if ext_ctx:
                    ext_ctx.current_stage = "Dev"
                    ext_ctx.current_task_id = next_task.id
                    ext_ctx.current_task_files = list(next_task.files or [])
                try:
                    dev_max_conts = int(getattr(args, "dev_max_turns_continuations", 0) or 0)
                    text, _structured = await _run_with_continuations(
                        cfg, dev_prompt, repo=repo, stage="Dev",
                        stop_path=stop_path, debug=bool(getattr(args, "debug", False)),
                        model_override=model_name,
                        stage_instructions=dev_instructions,
                        max_turns_override=int(getattr(args, "max_turns_per_task", 12) or 12),
                        timeout_seconds=int(getattr(args, "dev_timeout_seconds", 600) or 600),
                        label="dev", max_continuations=dev_max_conts,
                        task_id=next_task.id,
                        budget_state=budget_state, budgets_cfg=budgets_cfg,
                        metrics=metrics, _budget_exceeded=budget_exceeded,
                        ext_ctx=ext_ctx,
                    )
                    dev_final = text or ""
                    _inp, _out = extract_claude_tokens(_structured)
                    token_tracker.add("Dev", _inp, _out)
                    task_duration = time.time() - task_start_time
                    logger.timing("dev_task_execution", task_duration, task_id=next_task.id, attempt=attempt)
                except StopRequested:
                    if tb or cp:
                        ok, fail_reason = _isolate_or_stop("stop_requested")
                        if not ok:
                            return 1, fail_reason, 0, (len(done_set) > before_done)
                    _record_task_stop("dev_attempt", attempt)
                    return 0, STOP_REASON_STOP_FILE, 0, (len(done_set) > before_done)
                except Exception as ex:
                    dev_exc = ex
                    dev_final = ""
                    dev_is_max_turns = is_max_turns_exception(ex)
                    dev_quota_exhausted = is_quota_exception(ex)
                    task_duration = time.time() - task_start_time
                    logger.error(
                        f"Dev task execution failed: {next_task.id}", exc=ex, include_traceback=True,
                        task_id=next_task.id, task_title=next_task.title, attempt=attempt,
                        duration_sec=task_duration, is_max_turns=dev_is_max_turns, is_quota_exhausted=dev_quota_exhausted,
                    )
                dev_log = dev_final or ""
                if dev_exc:
                    exc_header = f"{type(dev_exc).__name__}: {str(dev_exc)}" if str(dev_exc) else type(dev_exc).__name__
                    exc_traceback = "".join(traceback.format_exception(type(dev_exc), dev_exc, dev_exc.__traceback__))
                    dev_log += f"\n[EXCEPTION]\n{exc_header}\n\nTraceback:\n{exc_traceback}\n"

                (attempt_dir / "dev_output.txt").write_text(dev_log + "\n", encoding="utf-8", errors="replace")
                (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)
                (run_dir / "dev_logs" / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.txt").write_text(dev_log + "\n", encoding="utf-8", errors="replace")

                if stop_path.exists():
                    _record_task_stop("dev_attempt", attempt)
                    return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)

                dev_quota_exhausted = dev_quota_exhausted or has_quota_text(dev_log)
                if isinstance(dev_exc, BudgetExceeded):
                    metrics.event("budget_exceeded", cycle=cycle_idx, step=step, task_id=next_task.id, reason=str(dev_exc))
                    return 1, "budget_exceeded", 0, (len(done_set) > before_done)
                if dev_quota_exhausted:
                    state.setdefault("warnings", []).append({"task": next_task.id, "reason": STOP_REASON_QUOTA, "detail": str(dev_exc) if dev_exc else "usage limit"})
                    save_state(state_path, state)
                    metrics.event("runner_stop", cycle=cycle_idx, step=step, task_id=next_task.id, reason=STOP_REASON_QUOTA)
                    try:
                        stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                    except Exception:
                        pass
                    try:
                        await write_shutdown_report(STOP_REASON_QUOTA, cycle=cycle_idx, step=step, last_task_id=next_task.id)
                    except Exception:
                        pass
                    return 0, STOP_REASON_QUOTA, 0, (len(done_set) > before_done)

                if dev_exc and is_model_invalid_exception(dev_exc):
                    if (attempt + 1) < max_attempts:
                        metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="model_invalid")
                        continue

                if dev_exc and not dev_is_max_turns:
                    task_status = _task_failure_status("exception", detail=str(dev_exc))
                    _record_failed_state("exception", detail=str(dev_exc), task_status=task_status)
                    save_state(state_path, state)
                    _record_failed_task_result("exception", task_status=task_status, detail=str(dev_exc))
                    _record_history(next_task.id, next_task.title, "failed", reason="exception", detail=str(dev_exc), files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="exception", task_status=task_status)
                    logger.task_end(task_id=next_task.id, success=False, reason="exception", task_status=task_status, exception=str(dev_exc))
                    if tb or cp:
                        ok, fail_reason = _isolate_or_stop("exception", task_status=task_status, detail=str(dev_exc))
                        if not ok:
                            if not continuous:
                                return 1, fail_reason, 0, (len(done_set) > before_done)
                            eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; skipping task.")
                    if continuous:
                        # Continuous mode: skip this task and continue to next
                        eprint(f"[SKIP] Dev exception for {next_task.id}; skipping to next task.")
                        skipped_set.add(next_task.id)
                        break
                    else:
                        # Non-continuous mode: stop the run (matches cycle.py behavior)
                        return 1, "dev_exception", 0, (len(done_set) > before_done)

                if dev_exc and dev_is_max_turns:
                    state.setdefault("warnings", []).append({"task": next_task.id, "reason": "max_turns_exceeded", "detail": str(dev_exc)})
                    save_state(state_path, state)
                    metrics.event("task_warn", cycle=cycle_idx, step=step, task_id=next_task.id, reason="max_turns_exceeded")

                # Check for explicit dependency requirement signal
                dep_req_path = run_dir / "DEPENDENCY_REQUIRED.md"
                if not dep_req_path.exists():
                    dep_req_path = attempt_dir / "DEPENDENCY_REQUIRED.md"
                if dep_req_path.exists():
                    dep_content = dep_req_path.read_text(encoding="utf-8", errors="replace")
                    eprint(f"[SKIP] Task {next_task.id} requires new dependencies:")
                    eprint(dep_content.strip())
                    # Append to run-level summary
                    dep_summary_path = run_dir / "DEPENDENCIES_NEEDED.md"
                    with open(dep_summary_path, "a", encoding="utf-8", errors="replace") as f:
                        f.write(f"\n## {next_task.id}: {next_task.title}\n\n{dep_content.strip()}\n\n---\n")
                    dep_detail = dep_content.strip()[:500]
                    task_status = _task_failure_status("needs_dependency", detail=dep_detail)
                    _record_failed_state("needs_dependency", detail=dep_detail, task_status=task_status)
                    save_state(state_path, state)
                    _record_failed_task_result("needs_dependency", task_status=task_status, detail=dep_detail)
                    _record_history(next_task.id, next_task.title, "failed", reason="needs_dependency", detail=dep_detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="needs_dependency", task_status=task_status)
                    logger.task_end(task_id=next_task.id, success=False, reason="needs_dependency", task_status=task_status)
                    skipped_set.add(next_task.id)
                    # Clean up the signal file so it doesn't affect subsequent tasks
                    try:
                        dep_req_path.unlink()
                    except Exception:
                        pass
                    break

                after = git_porcelain(repo)
                changed = has_working_tree_changes(repo, before, after, before_untracked=before_untracked)

                if stop_on_no_diff and not changed:
                    # Blocked-task detection (only when no diff produced)
                    blocked_keywords = [
                        "blocked:",
                        "missing dependency",
                        "missing package",
                        "nuget package",
                        "npm package",
                        "pip install",
                        "dotnet add package",
                        "package is not installed",
                        "module not found",
                    ]
                    dev_output_lower = dev_log.lower() if dev_log else ""
                    task_is_blocked = any(kw in dev_output_lower for kw in blocked_keywords)
                    if not task_is_blocked:
                        notes_path = attempt_dir / "NOTES.md"
                        if notes_path.exists():
                            try:
                                notes_content = notes_path.read_text(encoding="utf-8", errors="ignore").lower()
                                task_is_blocked = any(kw in notes_content for kw in blocked_keywords)
                            except Exception:
                                pass
                    if task_is_blocked:
                        eprint(f"[SKIP] Task {next_task.id} appears blocked (dependency/resource missing). Skipping...")
                        task_status = _task_failure_status("blocked_dependency", detail=dev_log[:500])
                        _record_failed_state("blocked_dependency", detail=dev_log[:500], task_status=task_status)
                        save_state(state_path, state)
                        _record_failed_task_result("blocked_dependency", task_status=task_status, detail=dev_log[:500])
                        _record_history(next_task.id, next_task.title, "failed", reason="blocked_dependency", detail=dev_log[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="blocked_dependency", task_status=task_status)
                        logger.task_end(task_id=next_task.id, success=False, reason="blocked_dependency", task_status=task_status)
                        skipped_set.add(next_task.id)
                        break

                    # Check if agent determined the task was already implemented (legitimate no-diff)
                    already_done_keywords = [
                        "already implemented", "already correct", "already exists",
                        "already complete", "already working", "no changes needed",
                        "no code changes", "no changes were needed", "implementation is already",
                        "already has", "already in place",
                    ]
                    dev_lower = dev_log.lower() if dev_log else ""
                    task_already_done = any(kw in dev_lower for kw in already_done_keywords)
                    if task_already_done:
                        eprint(f"[INFO] Task {next_task.id} reports already implemented; marking as done (no diff expected).")
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, reason="already_implemented")
                        logger.task_end(task_id=next_task.id, success=True, reason="already_implemented")
                        task_results.append({"id": next_task.id, "title": next_task.title, "status": "done", "reason": "already_implemented", "duration": time.time() - task_outer_t0})
                        task_completed = True
                        task_already_implemented = True
                        break

                    # Detect phantom edits: agent claims success but no diff exists.
                    # Retry once with an explicit "your edits did not persist" warning.
                    phantom_keywords = [
                        "completed", "successfully", "i've made", "i made", "i've updated",
                        "i updated", "i've added", "i added", "i've modified", "changes are",
                        "task complete", "i'll add", "now i'll add", "let me add",
                    ]
                    agent_claims_edit = any(kw in dev_lower for kw in phantom_keywords)
                    no_diff_retry_key = f"_no_diff_retry_{next_task.id}"
                    already_retried = budget_state.get(no_diff_retry_key, False)
                    if agent_claims_edit and not already_retried:
                        budget_state[no_diff_retry_key] = True
                        eprint(f"[RETRY] Task {next_task.id}: agent claims edits but no git diff detected. Retrying with explicit instructions...")
                        metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="phantom_edit_retry")
                        # Rebuild prompt with explicit warning
                        phantom_retry_prompt = (
                            dev_prompt + "\n\n"
                            "[CRITICAL WARNING] Your previous attempt produced NO changes to any files. "
                            "Your edits did NOT persist. This likely means your tool calls failed silently.\n"
                            "To fix this:\n"
                            f"1. Use ABSOLUTE file paths (e.g. {repo}/Components/Pages/...)\n"
                            "2. Use the Edit tool with exact old_string matching (copy from Read output)\n"
                            "3. After editing, run: Bash(command='git diff') to VERIFY your changes exist\n"
                            "4. If Edit fails, use Write tool to rewrite the entire file\n"
                            "DO NOT just describe changes. Actually call the Edit/Write tool NOW."
                        )
                        try:
                            text2, _ = await _run_claude_query(
                                cfg, phantom_retry_prompt, repo=repo, stage="Dev",
                                stop_path=stop_path, debug=bool(getattr(args, "debug", False)),
                                model_override=model_name,
                                stage_instructions=dev_instructions,
                                max_turns_override=int(getattr(args, "max_turns_per_task", 12) or 12),
                                timeout_seconds=int(getattr(args, "dev_timeout_seconds", 600) or 600),
                                ext_ctx=ext_ctx,
                                heartbeat_callback=lambda: metrics.event("heartbeat", stage="Dev", task_id=next_task.id),
                            )
                            dev_log = (dev_log or "") + "\n[PHANTOM_RETRY]\n" + (text2 or "")
                            (attempt_dir / "dev_output.txt").write_text(dev_log + "\n", encoding="utf-8", errors="replace")
                        except (StopRequested, BudgetExceeded):
                            raise
                        except Exception as retry_ex:
                            eprint(f"[WARN] Phantom edit retry failed: {retry_ex}")
                        # Re-check diff after retry
                        after = git_porcelain(repo)
                        changed = has_working_tree_changes(repo, before, after, before_untracked=before_untracked)
                        if changed:
                            eprint(f"[INFO] Phantom edit retry succeeded for {next_task.id} - diff now exists.")
                            metrics.event("dev_attempt_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0, reason="phantom_retry_success")
                            logger.task_end(task_id=next_task.id, success=True, reason="completed_after_retry", attempt=attempt)
                            task_results.append({"id": next_task.id, "title": next_task.title, "status": "done", "reason": "phantom_retry_success", "duration": time.time() - task_outer_t0})
                            task_completed = True
                            break
                        # If still no diff after retry, fall through to normal no_diff handling

                    if dev_is_max_turns and dev_auto_escalate and (attempt + 1) < max_attempts:
                        logger.retry_event("dev", next_task.id, attempt=attempt, reason="max_turns_no_diff")
                        metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="max_turns_no_diff")
                        continue
                    if dev_auto_escalate and (attempt + 1) < max_attempts and "no_diff" in dev_escalate_on:
                        metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="no_diff")
                        continue
                    task_status = _task_failure_status("no_diff", detail=dev_log[:500])
                    _record_failed_state("no_diff", detail=dev_log[:500], task_status=task_status)
                    save_state(state_path, state)
                    _record_failed_task_result("no_diff", task_status=task_status, detail=dev_log[:500])
                    _record_history(next_task.id, next_task.title, "failed", reason="no_diff", detail=dev_log[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="no_diff", task_status=task_status)
                    logger.task_end(task_id=next_task.id, success=False, reason="no_diff", task_status=task_status, was_max_turns=dev_is_max_turns)
                    if tb or cp:
                        ok, fail_reason = _isolate_or_stop("no_diff", task_status=task_status, detail=dev_log[:500])
                        if not ok:
                            if not continuous:
                                return 1, fail_reason, 0, (len(done_set) > before_done)
                            eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                    if continuous:
                        logger.skip_event(next_task.id, "no diff produced")
                        skipped_set.add(next_task.id)
                        break
                    else:
                        return 1, "no_diff", 0, (len(done_set) > before_done)

                if build_enabled:
                    metrics.event("build_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)
                    build_validation = await run_build_validation_async(
                        repo=repo,
                        build_cmd=getattr(args, "build_cmd", []),
                        build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                        legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                        log_path=attempt_dir / "build.txt",
                        stop_path=stop_path,
                        command_repo=source_repo,
                    )
                    build_validation.update(
                        {
                            "cycle": cycle_idx,
                            "step": step,
                            "task_id": next_task.id,
                            "task_title": next_task.title,
                            "attempt": attempt,
                        }
                    )
                    validation_records.append(build_validation)
                    ok = bool(build_validation.get("ok", False))
                    metrics.event("build_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0 if ok else 1)
                    if stop_path.exists():
                        _record_task_stop("build_gate", attempt)
                        return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)
                    if ok and tb:
                        # Auto-commit on task branch as incremental checkpoint
                        try:
                            _porcelain = git_porcelain(repo)
                            if _porcelain.strip():
                                run_cmd(["git", "add", "-A"], cwd=repo, timeout_sec=120)
                                run_cmd(["git", "commit", "--no-verify", "-m", f"[{next_task.id}] {next_task.title} (build passed)"], cwd=repo, timeout_sec=120)
                                metrics.event("task_branch_commit", cycle=cycle_idx, step=step, task_id=next_task.id, trigger="build_passed")
                        except Exception as _tb_ex:
                            eprint(f"[WARN] Auto-commit on task branch failed: {_tb_ex}")
                    elif ok and cp:
                        # Incremental checkpoint: advance baseline to build-passing state
                        try:
                            cp = update_checkpoint(repo, cp)
                            metrics.event("checkpoint_incremental", cycle=cycle_idx, step=step, task_id=next_task.id, trigger="build_passed")
                        except Exception as _cp_ex:
                            eprint(f"[WARN] Incremental checkpoint failed: {_cp_ex}")
                    if not ok:
                        build_detail = str(build_validation.get("failure_summary") or build_validation.get("summary") or "")
                        task_status = _task_failure_status("build_failed", validations=validation_records, detail=build_detail)
                        if (
                            is_auto_retry_allowed(task_status)
                            and dev_auto_escalate
                            and (attempt + 1) < max_attempts
                            and "build_failed" in dev_escalate_on
                        ):
                            try:
                                _berr_raw = (attempt_dir / "build.txt").read_text(encoding="utf-8", errors="replace")
                            except Exception:
                                _berr_raw = ""
                            _berr_lines = [ln for ln in _berr_raw.splitlines() if "error " in ln.lower()]
                            _prev_gate_error = "\n".join(_berr_lines[:50]) or build_detail or _berr_raw[-4000:]
                            _prev_gate_error_label = "BUILD FAILED"
                            _write_task_validation_artifact(
                                validations=validation_records,
                                status="failed",
                                reason="build_failed",
                                detail=build_detail,
                                task_status=task_status,
                            )
                            _record_task_experience_event(
                                task_id=next_task.id,
                                title=next_task.title,
                                status="failed",
                                reason="build_failed",
                                task_status=task_status,
                                cycle_idx=cycle_idx,
                                step_idx=step,
                                attempt=attempt + 1,
                                max_attempts=max_attempts,
                                validation_status="validation_failed",
                                validation_summary=build_detail,
                                artifact_pointers=[str(attempt_dir / "build.txt"), str(attempt_dir / "validation.json")],
                                outcome_action="retry_scheduled",
                                detail=build_detail,
                            )
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="build_failed")
                            continue
                        _record_failed_state(
                            "build_failed",
                            validations=validation_records,
                            detail=build_detail[:500],
                            task_status=task_status,
                        )
                        save_state(state_path, state)
                        _record_failed_task_result(
                            "build_failed",
                            task_status=task_status,
                            detail=build_detail[:500],
                            validation_artifact=str(attempt_dir / "validation.json"),
                        )
                        _record_history(next_task.id, next_task.title, "failed", reason="build_failed", detail=build_detail[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                        logger.gate_event("build", next_task.id, passed=False)
                        _write_task_validation_artifact(
                            validations=validation_records,
                            status="failed",
                            reason="build_failed",
                            detail=build_detail,
                            task_status=task_status,
                        )
                        if tb or cp:
                            ok_r, fr = _isolate_or_stop("build_failed", task_status=task_status, detail=build_detail[:500])
                            if not ok_r:
                                if not continuous:
                                    return 1, fr, 0, (len(done_set) > before_done)
                                eprint(f"[WARN] Rollback {fr} for {next_task.id}; continuing anyway.")
                        if continuous:
                            skipped_set.add(next_task.id)
                            break
                        else:
                            return 1, "build_failed", 0, (len(done_set) > before_done)

                if run_tests:
                    metrics.event("test_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)
                    test_validation = await run_test_validation_async(
                        repo=repo,
                        test_cmd=getattr(args, "test_cmd", []),
                        test_timeout_sec=int(getattr(args, "test_timeout_seconds", 3600)),
                        legacy_test_target=str(getattr(args, "dotnet_test_target", "") or ""),
                        legacy_test_filter=str(getattr(args, "dotnet_test_filter", "") or ""),
                        log_path=attempt_dir / "test.txt",
                        stop_path=stop_path,
                        command_repo=source_repo,
                    )
                    test_validation.update(
                        {
                            "cycle": cycle_idx,
                            "step": step,
                            "task_id": next_task.id,
                            "task_title": next_task.title,
                            "attempt": attempt,
                        }
                    )
                    test_validation_result = test_validation
                    validation_records.append(test_validation)
                    ok = bool(test_validation.get("ok", False))
                    metrics.event("test_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0 if ok else 1)
                    if stop_path.exists():
                        _record_task_stop("test_gate", attempt)
                        return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)
                    if not ok:
                        test_detail = str(test_validation.get("failure_summary") or test_validation.get("summary") or "")
                        task_status = _task_failure_status("test_failed", validations=validation_records, detail=test_detail)
                        if (
                            is_auto_retry_allowed(task_status)
                            and dev_auto_escalate
                            and (attempt + 1) < max_attempts
                            and "test_failed" in dev_escalate_on
                        ):
                            try:
                                _prev_gate_error = (attempt_dir / "test.txt").read_text(encoding="utf-8", errors="replace")[-4000:]
                            except Exception:
                                _prev_gate_error = test_detail
                            _prev_gate_error_label = "TEST FAILED"
                            _write_task_validation_artifact(
                                validations=validation_records,
                                status="failed",
                                reason="test_failed",
                                detail=test_detail,
                                task_status=task_status,
                            )
                            _record_task_experience_event(
                                task_id=next_task.id,
                                title=next_task.title,
                                status="failed",
                                reason="test_failed",
                                task_status=task_status,
                                cycle_idx=cycle_idx,
                                step_idx=step,
                                attempt=attempt + 1,
                                max_attempts=max_attempts,
                                validation_status="validation_failed",
                                validation_summary=test_detail,
                                artifact_pointers=[str(attempt_dir / "test.txt"), str(attempt_dir / "validation.json")],
                                outcome_action="retry_scheduled",
                                detail=test_detail,
                            )
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="test_failed")
                            continue
                        _record_failed_state(
                            "test_failed",
                            validations=validation_records,
                            detail=test_detail[:500],
                            task_status=task_status,
                        )
                        save_state(state_path, state)
                        _record_failed_task_result(
                            "test_failed",
                            task_status=task_status,
                            detail=test_detail[:500],
                            validation_artifact=str(attempt_dir / "validation.json"),
                        )
                        _record_history(next_task.id, next_task.title, "failed", reason="test_failed", detail=test_detail[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                        logger.gate_event("test", next_task.id, passed=False)
                        _write_task_validation_artifact(
                            validations=validation_records,
                            status="failed",
                            reason="test_failed",
                            detail=test_detail,
                            task_status=task_status,
                        )
                        if tb or cp:
                            ok_r, fr = _isolate_or_stop("test_failed", task_status=task_status, detail=test_detail[:500])
                            if not ok_r:
                                if not continuous:
                                    return 1, fr, 0, (len(done_set) > before_done)
                                eprint(f"[WARN] Rollback {fr} for {next_task.id}; continuing anyway.")
                        if continuous:
                            skipped_set.add(next_task.id)
                            break
                        else:
                            return 1, "test_failed", 0, (len(done_set) > before_done)

                if policy_scan_enabled:
                    policy_scan_ignore = list(scan_ignore_paths)
                    if policy_ignore_paths:
                        policy_scan_ignore = list(dict.fromkeys([*policy_scan_ignore, *policy_ignore_paths]))
                    scan_files, scan_stats = _collect_scan(policy_scan_scope, ignore_paths=policy_scan_ignore)
                    scan_result = policy_scan_files(scan_files, policy_rules, allow_patterns=policy_allow_patterns, ignore_paths=policy_scan_ignore)
                    violations = scan_result.get("violations", [])
                    fail_hits = [v for v in violations if severity_at_or_above(str(v.get("severity", "")), policy_fail_severity)]
                    scan_result["stats"] = scan_stats
                    scan_result["ok"] = len(fail_hits) == 0
                    scan_result["fail_severity"] = policy_fail_severity
                    scan_result["fail_violations"] = fail_hits
                    policy_scan_summary = {
                        "scope": scan_stats.get("scope", policy_scan_scope),
                        "files_scanned": scan_stats.get("files_scanned", 0),
                        "bytes_scanned": scan_stats.get("bytes_scanned", 0),
                        "files_skipped": scan_stats.get("files_skipped", 0),
                        "violations_total": len(violations),
                        "violations_fail": len(fail_hits),
                    }
                    metrics.event("policy_scan_summary", cycle=cycle_idx, step=step, **policy_scan_summary)
                    (attempt_dir / "policy_scan.json").write_text(json.dumps(scan_result, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
                    (run_dir / "policy_scan.json").write_text(json.dumps({"cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
                    (run_dir / f"policy_scan_cycle_{cycle_idx:03d}.json").write_text(json.dumps({"cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
                    try:
                        with (run_dir / "policy_scan_history.jsonl").open("a", encoding="utf-8", errors="replace") as f:
                            f.write(json.dumps({"ts": now_iso(), "cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
                    if not scan_result.get("ok", True):
                        policy_detail = json.dumps(fail_hits, ensure_ascii=False, default=str)[:1000]
                        task_status = _task_failure_status("policy_violation", detail=policy_detail)
                        _record_failed_state("policy_violation", detail=policy_detail, task_status=task_status)
                        save_state(state_path, state)
                        _record_failed_task_result(
                            "policy_violation",
                            task_status=task_status,
                            detail=policy_detail,
                            validation_artifact=str(attempt_dir / "policy_scan.json"),
                        )
                        _record_history(next_task.id, next_task.title, "failed", reason="policy_violation", detail=policy_detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                        logger.gate_event("policy", next_task.id, passed=False)
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="policy_violation", task_status=task_status, violations=len(fail_hits))
                        if tb or cp:
                            ok_r, fr = _isolate_or_stop("policy_violation", task_status=task_status, detail=policy_detail)
                            if not ok_r:
                                if not continuous:
                                    return 1, fr, 0, (len(done_set) > before_done)
                                eprint(f"[WARN] Rollback {fr} for {next_task.id}; continuing anyway.")
                        if continuous:
                            skipped_set.add(next_task.id)
                            break
                        else:
                            return 1, "policy_violation", 0, (len(done_set) > before_done)
                fast_regression_files = list(next_task.files or [])
                try:
                    fast_regression_files.extend(git_changed_files(repo, task_head_before, git_head(repo)))
                except Exception:
                    pass
                try:
                    fast_regression_files.extend(git_worktree_changed_files(repo))
                except Exception:
                    pass
                fast_regression_triggered = should_run_fast_web_worktree_regression(repo, fast_regression_files)
                if fast_regression_triggered:
                    fast_regression_log = attempt_dir / "fast_web_worktree_regression.json"
                    metrics.event(
                        "fast_regression_start",
                        cycle=cycle_idx,
                        step=step,
                        task_id=next_task.id,
                        attempt=attempt,
                        log_path=str(fast_regression_log),
                    )
                    fast_regression = await run_fast_web_worktree_regression_async(
                        repo=repo,
                        log_path=fast_regression_log,
                        stop_path=stop_path,
                        trigger_files=fast_regression_files,
                    )
                    fast_validation = {
                        "name": "fast_web_worktree_regression",
                        "kind": "regression",
                        "gate": "fast_web_worktree_regression",
                        "cmd": list(fast_regression.get("suite_files") or fast_regression.get("suiteFiles") or []),
                        "rc": 0 if bool(fast_regression.get("ok", False)) else 1,
                        "ok": bool(fast_regression.get("ok", False)),
                        "artifact_path": str(fast_regression_log),
                        "artifactPath": str(fast_regression_log),
                        "log_path": str(fast_regression_log),
                        "logPath": str(fast_regression_log),
                        "summary": str(fast_regression.get("failure_summary") or fast_regression.get("failureSummary") or fast_regression_log),
                        "failure_summary": str(fast_regression.get("failure_summary") or ""),
                        "failureSummary": str(fast_regression.get("failureSummary") or ""),
                        "trigger_files": list(fast_regression.get("trigger_files") or fast_regression.get("triggerFiles") or fast_regression_files),
                        "triggerFiles": list(fast_regression.get("trigger_files") or fast_regression.get("triggerFiles") or fast_regression_files),
                        "suite_files": list(fast_regression.get("suite_files") or fast_regression.get("suiteFiles") or []),
                        "suiteFiles": list(fast_regression.get("suite_files") or fast_regression.get("suiteFiles") or []),
                        "commands": list(fast_regression.get("commands") or []),
                        "failed_command": fast_regression.get("failed_command"),
                        "started_at": fast_regression.get("started_at"),
                        "ended_at": fast_regression.get("ended_at"),
                    }
                    validation_records.append(fast_validation)
                    fast_command_count = len(fast_regression.get("commands", []) or [])
                    fast_ok = bool(fast_regression.get("ok", False))
                    metrics.event(
                        "fast_regression_end",
                        cycle=cycle_idx,
                        step=step,
                        task_id=next_task.id,
                        attempt=attempt,
                        rc=0 if fast_ok else 1,
                        command_count=fast_command_count,
                        log_path=str(fast_regression_log),
                    )
                    logger.gate_event("fast_regression", next_task.id, passed=fast_ok, commands=fast_command_count)
                    if stop_path.exists():
                        _record_task_stop("fast_regression_gate", attempt)
                        return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)
                    if not fast_ok:
                        failed_command = fast_regression.get("failed_command") or {}
                        failed_name = str(failed_command.get("name") or failed_command.get("test_file") or "fast_regression")
                        failed_summary = str(fast_regression.get("failure_summary") or fast_regression.get("failureSummary") or "")
                        if not failed_summary:
                            try:
                                failed_summary = summarize_fast_web_worktree_regression_failure(
                                    fast_regression,
                                    fast_regression_log,
                                )
                            except Exception:
                                failed_summary = str(fast_regression_log)
                        task_status = _task_failure_status("fast_regression_failed", validations=validation_records, detail=failed_summary)
                        if (
                            is_auto_retry_allowed(task_status)
                            and should_retry_fast_web_worktree_regression_failure(
                                dev_auto_escalate,
                                attempt,
                                max_attempts,
                                dev_escalate_on,
                            )
                        ):
                            _prev_gate_error = summarize_fast_web_worktree_regression_failure(
                                fast_regression,
                                fast_regression_log,
                            )
                            _prev_gate_error_label = "FAST REGRESSION FAILED"
                            _write_task_validation_artifact(
                                validations=validation_records,
                                status="failed",
                                reason="fast_regression_failed",
                                detail=failed_summary,
                                task_status=task_status,
                            )
                            _record_task_experience_event(
                                task_id=next_task.id,
                                title=next_task.title,
                                status="failed",
                                reason="fast_regression_failed",
                                task_status=task_status,
                                cycle_idx=cycle_idx,
                                step_idx=step,
                                attempt=attempt + 1,
                                max_attempts=max_attempts,
                                validation_status="validation_failed",
                                validation_summary=failed_summary,
                                artifact_pointers=[str(fast_regression_log), str(attempt_dir / "validation.json")],
                                outcome_action="retry_scheduled",
                                detail=failed_summary,
                            )
                            metrics.event(
                                "dev_attempt_retry",
                                cycle=cycle_idx,
                                step=step,
                                task_id=next_task.id,
                                attempt=attempt,
                                reason="fast_regression_failed",
                                failed_command=failed_name,
                                log_path=str(fast_regression_log),
                            )
                            continue
                        _record_failed_state(
                            "fast_regression_failed",
                            validations=validation_records,
                            detail=failed_summary[:500],
                            task_status=task_status,
                        )
                        save_state(state_path, state)
                        _record_task_experience_event(
                            task_id=next_task.id,
                            title=next_task.title,
                            status="failed",
                            reason="fast_regression_failed",
                            task_status=task_status,
                            cycle_idx=cycle_idx,
                            step_idx=step,
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            validation_status="validation_failed",
                            validation_summary=failed_summary,
                            artifact_pointers=[str(fast_regression_log)],
                            outcome_action="preserved_for_review" if should_preserve_for_review(task_status) else "discarded",
                            detail=failed_summary,
                        )
                        _record_history(
                            next_task.id,
                            next_task.title,
                            "failed",
                            reason="fast_regression_failed",
                            detail=failed_summary[:500],
                            files=next_task.files,
                            cycle=cycle_idx,
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            task_status=task_status,
                        )
                        _write_task_validation_artifact(
                            validations=validation_records,
                            status="failed",
                            reason="fast_regression_failed",
                            detail=failed_summary,
                            task_status=task_status,
                        )
                        record_task_failure_result(
                            task_results,
                            task_id=next_task.id,
                            task_title=next_task.title,
                            reason="fast_regression_failed",
                            duration=time.time() - task_outer_t0,
                            task_status=task_status,
                            validations=validation_records,
                            detail=failed_name,
                            validation_artifact=str(attempt_dir / "validation.json"),
                            validation_status="failed",
                        )
                        metrics.event(
                            "task_end",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            rc=1,
                            reason="fast_regression_failed",
                            task_status=task_status,
                            command_count=fast_command_count,
                        )
                        logger.task_end(task_id=next_task.id, success=False, reason="fast_regression_failed", task_status=task_status, attempt=attempt)
                        task_failure_reason = "fast_regression_failed"
                        if tb or cp:
                            ok_restore, fail_reason = _isolate_or_stop("fast_regression_failed", task_status=task_status, detail=failed_summary[:500])
                            if not ok_restore:
                                if not continuous:
                                    return 1, fail_reason, 0, (len(done_set) > before_done)
                                eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                        if continuous:
                            skipped_set.add(next_task.id)
                            break
                        return 1, "fast_regression_failed", 0, (len(done_set) > before_done)

                metrics.event("dev_attempt_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0)
                logger.task_end(task_id=next_task.id, success=True, reason="completed", attempt=attempt)
                task_completed = True
                break

            if task_failure_reason:
                continue

            task_validation_status = classify_task_validation_status(
                run_tests=run_tests,
                fast_regression_triggered=fast_regression_triggered,
                test_validation=test_validation_result,
                validation_records=validation_records,
            )

            if task_completed:
                _write_task_validation_artifact(
                    validations=validation_records,
                    status=task_validation_status,
                    reason="completed",
                    task_status=TASK_STATUS_COMPLETED,
                )

            # Merge or abandon task branch
            if task_completed and tb:
                merge_ok = merge_task_branch(repo, tb)
                if merge_ok:
                    metrics.event("task_branch_merged", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name)
                else:
                    eprint(f"[WARN] Merge failed for {tb.branch_name}; work preserved on branch")
                    metrics.event("task_branch_merge_failed", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name)
                    task_status = _task_failure_status("merge_conflict", detail=f"Merge conflict on branch {tb.branch_name}")
                    _record_failed_state(
                        "merge_conflict",
                        detail=f"Merge conflict on branch {tb.branch_name}",
                        task_status=task_status,
                        branch=tb.branch_name,
                    )
                    save_state(state_path, state)
                    _record_failed_task_result("merge_conflict", task_status=task_status, detail=f"Merge conflict on branch {tb.branch_name}")
                    _record_history(next_task.id, next_task.title, "failed", reason="merge_conflict",
                                    detail=f"Merge conflict on branch {tb.branch_name}", files=next_task.files,
                                    cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                    skipped_set.add(next_task.id)
                    tb = None
                    continue  # skip marking as done
                tb = None

            if not task_completed:
                if tb:
                    try:
                        abandon_task_branch(repo, tb)
                        metrics.event("task_branch_abandoned", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name, reason="exhausted_attempts")
                    except Exception as ex:
                        eprint(f"[WARN] abandon_task_branch failed: {ex}")
                        metrics.event("task_branch_abandon_failed", task_id=next_task.id, error=str(ex)[:200])
                    tb = None
                task_status = _task_failure_status("exhausted_attempts")
                _record_failed_state("exhausted_attempts", task_status=task_status)
                save_state(state_path, state)
                _record_history(next_task.id, next_task.title, "failed", reason="exhausted_attempts", files=next_task.files, cycle=cycle_idx, attempt=max_attempts, max_attempts=max_attempts, task_status=task_status)
                record_task_failure_result(
                    task_results,
                    task_id=next_task.id,
                    task_title=next_task.title,
                    reason="exhausted_attempts",
                    duration=time.time() - task_outer_t0,
                    task_status=task_status,
                    attempt=max_attempts,
                    max_attempts=max_attempts,
                )
                logger.task_end(task_id=next_task.id, success=False, reason="exhausted_attempts", task_status=task_status, attempts=max_attempts)
                eprint(f"[SKIP] Exhausted all attempts for {next_task.id}; skipping to next task.")
                skipped_set.add(next_task.id)
                continue

            # Phantom completion detection: task marked done but no git commits created
            # Skip check if task was legitimately already implemented (no commits expected)
            if not task_already_implemented and not has_new_commits(repo, task_head_before):
                logger.warning(f"Task {next_task.id} passed gates but no commits found (phantom completion)")
                metrics.event("phantom_completion_detected", task_id=next_task.id, cycle=cycle_idx)
                no_commits_detail = "Task passed all gates but no git commits were created (phantom completion)"
                task_status = _task_failure_status("no_commits", detail=no_commits_detail)
                _record_failed_state("no_commits", detail=no_commits_detail, task_status=task_status)
                save_state(state_path, state)
                _record_history(next_task.id, next_task.title, "failed",
                                reason="no_commits", detail=no_commits_detail, files=next_task.files, cycle=cycle_idx, task_status=task_status)
                record_task_failure_result(
                    task_results,
                    task_id=next_task.id,
                    task_title=next_task.title,
                    reason="no_commits",
                    duration=time.time() - task_outer_t0,
                    task_status=task_status,
                )
                logger.task_end(task_id=next_task.id, success=False, reason="no_commits", task_status=task_status)
                skipped_set.add(next_task.id)
                continue
            done_set.add(next_task.id)
            # Clean up previous failure entries for this task (e.g. from earlier cycles)
            if state.get("failed"):
                state["failed"] = [f for f in state["failed"] if f.get("task") != next_task.id]
            state["done"] = sorted(list(done_set))
            save_state(state_path, state)
            mark_backlog_done(backlog_md_path, next_task.id)
            _record_history(next_task.id, next_task.title, "done", files=next_task.files, cycle=cycle_idx, task_status=TASK_STATUS_COMPLETED)
            task_results.append({
                "id": next_task.id,
                "title": next_task.title,
                "status": "done",
                "duration": time.time() - task_outer_t0,
                "validation_artifact": str(attempt_dir / "validation.json"),
                "validation_status": task_validation_status,
                "task_status": TASK_STATUS_COMPLETED,
            })

            # Use current-cycle task IDs to avoid cross-cycle accumulation (done=16/11 bug)
            _done_this_cycle = len(done_set.intersection(task_ids))
            _skipped_this_cycle = len(skipped_set.intersection(task_ids))
            (run_dir / "progress.txt").write_text(f"done={_done_this_cycle}/{len(tasks)} skipped={_skipped_this_cycle} last={next_task.id}\n", encoding="utf-8", errors="replace")
            try:
                code, names = run_cmd(["git", "diff", "--name-only"], cwd=repo, timeout_sec=60)
                files_changed_count = len([ln for ln in names.splitlines() if ln.strip()]) if code == 0 else 0
            except Exception:
                files_changed_count = -1
            metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, files_changed_count=files_changed_count)

        try:
            merge_dev_hints_to_global_changelog(analysis_md, dev_hints_dir, curr_head)
        except Exception as ex:
            eprint(f"[WARN] merge_dev_hints failed: {ex}")

        ran_tasks = (len(done_set) > before_done)
        cycle_dt = time.time() - cycle_t0
        state_counts = count_state_task_ids(state, load_backlog_task_ids(run_dir / "BACKLOG.json"))
        done_count = state_counts["done"]
        failed_count = state_counts["failed"]
        warnings_count = state_counts["warnings"]
        failure_group_counts = count_task_status_groups(
            [
                str(item.get("task_status") or item.get("taskStatus") or item.get("outcome_status") or item.get("status") or "").strip().lower()
                for item in (state.get("failed", []) or [])
                if isinstance(item, dict)
            ]
        )
        total_count = len(task_ids)
        skipped_count = len(skipped_set.intersection(task_ids))
        summary = {
            "ts": now_iso(), "cycle": cycle_idx, "run_dir": str(run_dir),
            "done": done_count, "skipped": skipped_count,
            "total_tasks": total_count, "failed_count": failed_count,
            "tasks_regressed": failure_group_counts.get("regression", 0),
            "tasks_review": failure_group_counts.get("review", 0),
            "tasks_blocked_env": failure_group_counts.get("blocked_env", 0),
            "failure_group_counts": failure_group_counts,
            "warnings_count": warnings_count,
            "duration_seconds": cycle_dt, "build_enabled": build_enabled,
            "run_tests": run_tests, "policy_scan_enabled": policy_scan_enabled,
        }
        last_run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
        append_cycle_summary(
            f"{now_iso()} cycle={cycle_idx} done={done_count}/{total_count} failed={failed_count} warnings={warnings_count} dt={cycle_dt:.1f}s"
        )
        metrics.event(
            "cycle_end",
            cycle=cycle_idx,
            rc=0,
            done=done_count,
            total=total_count,
            failed=failed_count,
            warnings=warnings_count,
            duration_seconds=cycle_dt,
            tokens=token_tracker.summary(),
        )
        print_cycle_report(cycle_idx, cycle_dt, task_results, done_count, total_count, failed_count, skipped_count, token_tracker=token_tracker)

        done_delta = done_count - before_done

        try:
            latest_head = git_head(repo).strip()
            if latest_head:
                snapshot_json.write_text(json.dumps({"head": latest_head, "updated_at": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
                prev_head = latest_head
        except Exception:
            pass

        # --- Goals auto-check: update GOALS.md checkboxes based on completed tasks ---
        _goals_auto_chk = bool(getattr(args, "goals_auto_check", True))
        _goals_on = bool(getattr(args, "goals_enabled", True))
        if _goals_on and _goals_auto_chk and ran_tasks:
            try:
                done_titles = [t.title for t in tasks if t.id in done_set]
                done_prompts = [t.prompt for t in tasks if t.id in done_set]
                goals_update = update_goals_checkboxes(
                    repo,
                    done_titles,
                    done_prompts,
                    completion_level=goals_completion_level,
                )
                if goals_update.get("updated"):
                    checked = goals_update.get("checked_items", [])
                    eprint(f"[GOALS] Auto-checked {len(checked)} item(s): {checked[:5]}")
                    metrics.event("goals_updated", cycle=cycle_idx, checked_count=len(checked), items=checked[:10])
            except Exception as goals_ex:
                eprint(f"[WARN] Goals auto-check failed: {goals_ex}")

        # --- Completion evaluation ---
        if _goals_on:
            try:
                _gp_eval, _gt_eval = read_goals(repo)
                if _gt_eval:
                    comp_status = parse_goals_completion(_gt_eval, completion_level=goals_completion_level)
                    unresolved = _count_unresolved_failures(repo, done_set)
                    write_completion_status(run_dir, comp_status, failed_unresolved=unresolved,
                                           stop_reason="cycle_end")
                    if comp_status.get("project_complete") and unresolved == 0:
                        eprint(f"[GOALS] PROJECT COMPLETE - all goals met (level={goals_completion_level}), no unresolved failures.")
                        metrics.event("project_complete", cycle=cycle_idx, goals=comp_status)
                        return 0, STOP_REASON_PROJECT_COMPLETE, done_delta, ran_tasks
                    else:
                        p0d = comp_status.get("p0_done", 0)
                        p0t = comp_status.get("p0_total", 0)
                        unmet = comp_status.get("unmet_p0", [])
                        eprint(f"[GOALS] P0: {p0d}/{p0t} | unresolved failures: {unresolved} | unmet: {unmet[:3]}")
            except Exception as comp_ex:
                eprint(f"[WARN] Completion evaluation failed: {comp_ex}")

        if total_count > 0 and done_count >= total_count:
            return 0, STOP_REASON_ALL_TASKS_DONE, done_delta, ran_tasks
        if total_count > 0 and (done_count + skipped_count) >= total_count:
            # All tasks attempted but some were skipped - not truly "all done"
            logger.info(f"All tasks attempted: {done_count} done, {skipped_count} skipped out of {total_count}.")
            return 0, STOP_REASON_ALL_TASKS_ATTEMPTED, done_delta, ran_tasks

        return 0, "ok", done_delta, ran_tasks

    # ---------------------------------------------------------------------------
    # QA phase (with followup injection - same as Codex)
    # ---------------------------------------------------------------------------

    async def run_qa_if_needed(cycle_idx: int, ran_tasks: bool) -> dict[str, Any]:
        if stop_path.exists():
            return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0}
        qa_always = bool(getattr(args, "qa_always", False))
        if not (qa_always or ran_tasks):
            metrics.event("qa_skip", cycle=cycle_idx, reason="no_progress")
            return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0}
        try:
            metrics.event("qa_start", cycle=cycle_idx)
            skills_context = build_qa_skills_context(
                load_tasks_fn=load_tasks,
                skills_enabled=skills_enabled,
                skills_by_id=skills_by_id,
                skills_cfg=skills_cfg,
                inline_skills_for_fn=_inline_skills_for,
                build_skills_context_fn=build_skills_context,
            )

            qa_ctx = {"repo": str(repo), "run_dir": str(run_dir), "skills_context": skills_context}
            qa_prompt = _patch_prompt_for_claude(store.render("qa_prompt", QA_TEMPLATE_DEFAULT, qa_ctx))
            if bool(getattr(args, "qa_to_backlog", False)):
                qa_prompt = qa_prompt.rstrip() + "\n\n" + QA_FOLLOWUPS_OUTPUT_CONTRACT + "\n"

            if ext_ctx:
                ext_ctx.current_stage = "QA"
                ext_ctx.current_task_id = ""
                ext_ctx.current_task_files = []
            text, _structured = await _run_claude_query(
                cfg, qa_prompt, repo=repo, stage="QA",
                stop_path=stop_path, debug=bool(getattr(args, "debug", False)),
                model_override=cfg.qa_model,
                stage_instructions=qa_instructions,
                max_turns_override=int(getattr(args, "report_max_turns", 8) or 8),
                timeout_seconds=int(getattr(args, "pm_timeout_seconds", 900) or 900),
                ext_ctx=ext_ctx,
                heartbeat_callback=lambda: metrics.event("heartbeat", stage="qa"),
            )

            # Structured JSON fallback: text가 비어있으면 structured output을 사용
            qa_text_out = text or ""
            if not qa_text_out.strip() and _structured is not None:
                try:
                    if isinstance(_structured, dict):
                        qa_text_out = json.dumps(_structured, ensure_ascii=False, indent=2)
                    elif isinstance(_structured, str):
                        qa_text_out = _structured
                    else:
                        qa_text_out = str(_structured)
                    metrics.event("qa_structured_fallback", cycle=cycle_idx)
                except Exception:
                    pass

            qa_output_path = run_dir / f"qa_final_output_cycle_{cycle_idx:03d}.txt"
            qa_output_path.write_text(qa_text_out + "\n", encoding="utf-8", errors="replace")
            qa_summary = process_qa_followups(
                cycle_idx=cycle_idx,
                run_dir=run_dir,
                qa_text=qa_output_path.read_text(encoding="utf-8", errors="replace"),
                qa_to_backlog=bool(getattr(args, "qa_to_backlog", False)),
                max_qa_followups=int(getattr(args, "max_qa_followups", 5)) or 5,
                parse_qa_followups_fn=parse_qa_followups,
                followups_from_structured_fn=_followups_from_structured,
                extract_qa_followups_fn=_extract_qa_followups,
                split_followups_by_type_fn=split_followups_by_type,
                write_manual_checks_fn=write_manual_checks,
                load_state_fn=load_state,
                state_path=state_path,
                load_tasks_fn=load_tasks,
                merge_qa_followups_fn=_merge_qa_followups,
                write_backlog_files_fn=write_backlog_files,
                metrics=metrics,
            )
            metrics.event("qa_end", cycle=cycle_idx, rc=0)
            return qa_summary
        except StopRequested:
            return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0}
        except Exception as ex:
            if is_quota_exception(ex):
                logger.stage_event("qa", "quota_exhausted", cycle=cycle_idx, detail=str(ex))
                try:
                    stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass
                metrics.event("runner_stop", stage="qa", reason="quota_exhausted")
                return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0, "quota_exhausted": True}
            logger.stage_event("qa", "error", cycle=cycle_idx, detail=str(ex))
            metrics.event("qa_end", cycle=cycle_idx, rc=1, error=str(ex))
            return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0}

    # ---------------------------------------------------------------------------
    # Shutdown report
    # ---------------------------------------------------------------------------

    async def write_shutdown_report(stop_reason: str, *, cycle: int, step: int, last_task_id: Optional[str] = None) -> None:
        report_path = run_dir / "SHUTDOWN_REPORT.md"
        ctx_path = run_dir / "SHUTDOWN_CONTEXT.json"
        ctx_obj: dict[str, Any]
        try:
            ctx_obj = collect_shutdown_context(repo, run_dir)
            ctx_obj["stop_reason"] = stop_reason
            if last_task_id:
                ctx_obj["last_task_id"] = last_task_id
            ctx_path.write_text(json.dumps(ctx_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")
        except Exception:
            ctx_obj = {"stop_reason": stop_reason}
            try:
                ctx_path.write_text(json.dumps(ctx_obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")
            except Exception:
                pass
        try:
            local_md = build_local_shutdown_report(repo=repo, run_dir=run_dir, reason=stop_reason, last_task_id=last_task_id)
            report_path.write_text(local_md, encoding="utf-8", errors="replace")
        except Exception as _report_ex:
            eprint(f"[WARN] Failed to write local shutdown report: {_report_ex}")
        # Try PM-authored report via Claude SDK
        reporter_instructions = store.get("reporter_instructions", REPORTER_INSTRUCTIONS_DEFAULT)
        try:
            reporter_prompt = _patch_prompt_for_claude(store.render("pm_shutdown_report_prompt", PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT, {"stop_reason": stop_reason, "context_json": json.dumps(ctx_obj, ensure_ascii=False, indent=2)}))
            if ext_ctx:
                ext_ctx.current_stage = "Reporter"
                ext_ctx.current_task_id = ""
                ext_ctx.current_task_files = []
            text, _ = await _run_claude_query(
                cfg, reporter_prompt, repo=repo, stage="Reporter",
                stop_path=stop_path, debug=bool(getattr(args, "debug", False)),
                model_override=cfg.reporter_model,
                stage_instructions=reporter_instructions,
                max_turns_override=int(getattr(args, "report_max_turns", 8) or 8),
                timeout_seconds=300,
                ext_ctx=ext_ctx,
                heartbeat_callback=lambda: metrics.event("heartbeat", stage="reporter"),
            )
            if text and text.strip():
                clean_text = text.strip()
                # Detect and remove duplicate report content (PM model repeating itself)
                half = len(clean_text) // 2
                if half > 200 and clean_text[:half].strip() == clean_text[half:].strip():
                    clean_text = clean_text[:half].strip()
                report_path.write_text(clean_text + "\n", encoding="utf-8", errors="replace")
                (run_dir / "PM_SHUTDOWN_REPORT_OUTPUT.txt").write_text(clean_text + "\n", encoding="utf-8", errors="replace")
            metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=bool(text))
        except Exception as ex:
            metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=False, error=str(ex))

    # ---------------------------------------------------------------------------
    # Run cycle (shared runtime)
    # ---------------------------------------------------------------------------

    async def run_cycle(cycle_idx: int) -> tuple[int, str, int, int]:
        def _get_prev_head() -> str:
            return prev_head

        def _set_prev_head(value: str) -> None:
            nonlocal prev_head
            prev_head = value

        def _get_policy_scan_summary() -> Optional[dict[str, Any]]:
            return policy_scan_summary

        def _set_policy_scan_summary(value: Optional[dict[str, Any]]) -> None:
            nonlocal policy_scan_summary
            policy_scan_summary = value

        def _get_security_scan_summary() -> Optional[dict[str, Any]]:
            return security_scan_summary

        def _set_security_scan_summary(value: Optional[dict[str, Any]]) -> None:
            nonlocal security_scan_summary
            security_scan_summary = value

        result = await run_shared_cycle_once(
            cycle_idx,
            SharedCycleDeps(
                args=args,
                repo=repo,
                run_dir=run_dir,
                stop_path=stop_path,
                metrics=metrics,
                pipeline_mgr=pipeline_mgr,
                continuous=continuous,
                ensure_backlog=ensure_backlog,
                load_tasks=load_tasks,
                run_pm_if_needed=run_pm_if_needed,
                run_dev_loop=run_dev_loop,
                run_qa_if_needed=run_qa_if_needed,
                pm_stop_reason=pm_stop_reason,
                detect_stop_reason=detect_stop_reason,
                budget_state=budget_state,
                run_summary=run_summary,
                write_run_summary=_write_run_summary,
                snapshot_json=snapshot_json,
                get_prev_head=_get_prev_head,
                set_prev_head=_set_prev_head,
                get_policy_scan_summary=_get_policy_scan_summary,
                set_policy_scan_summary=_set_policy_scan_summary,
                get_security_scan_summary=_get_security_scan_summary,
                set_security_scan_summary=_set_security_scan_summary,
                policy_scan_enabled=policy_scan_enabled,
                policy_scan_scope=policy_scan_scope,
                security_enabled=security_enabled,
                security_scan_scope=security_scan_scope,
                security_rules=security_rules,
                security_fail_severity=security_fail_severity,
                security_end_include_totals=True,
                scan_ignore_paths=scan_ignore_paths,
                collect_scan=_collect_scan,
                security_scan_files_fn=security_scan_files,
                severity_at_or_above_fn=severity_at_or_above,
                git_head_fn=git_head,
                git_changed_files_fn=git_changed_files,
                git_worktree_changed_files_fn=git_worktree_changed_files,
                repo_fingerprint_fn=repo_fingerprint,
                eprint_fn=eprint,
                stop_reason_quota=STOP_REASON_QUOTA,
                stop_reason_stop_file=STOP_REASON_STOP_FILE,
                stop_reason_project_complete=STOP_REASON_PROJECT_COMPLETE,
                stop_reason_all_tasks_done=STOP_REASON_ALL_TASKS_DONE,
                stop_reason_no_tasks=STOP_REASON_NO_TASKS,
            ),
        )
        return result.rc, result.reason, result.done_delta, result.qa_followups_added

    # ---------------------------------------------------------------------------
    # Main loop (same as Codex - with idle tracking and shutdown report)
    # ---------------------------------------------------------------------------

    idle_accum = 0
    idle_cycle_count = 0
    try:
        idle_exit_cycles = max(0, int(getattr(args, "idle_exit_cycles", 3)))
    except (TypeError, ValueError):
        idle_exit_cycles = 3
    last_rc = 0
    last_reason = ""
    consecutive_failures = 0
    max_consecutive_failed_cycles = int(getattr(args, "max_consecutive_failed_cycles", 3) or 3)
    budget_reset_per_cycle = bool(getattr(args, "budget_reset_per_cycle", True))
    quota_check_enabled = bool(getattr(args, "quota_check_enabled", True))
    quota_5h_max = float(getattr(args, "quota_five_hour_max_utilization", 95) or 95)
    quota_7d_max = float(getattr(args, "quota_seven_day_max_utilization", 95) or 95)
    quota_wait_for_reset = bool(getattr(args, "quota_wait_for_reset", True))
    loop_mode = bool(getattr(args, "loop", False))
    loop_max_cycles = getattr(args, "loop_max_cycles", 0)
    loop_sleep_seconds = int(getattr(args, "loop_sleep_seconds", 60) or 60)
    loop_idle_exit_after = int(getattr(args, "loop_idle_exit_after", 0) or 0)
    cycle_indices = loop_cycle_indices(loop_mode, loop_max_cycles)

    # Goals auto-refresh state
    goals_refresh_count = 0
    goals_refresh_max = int(getattr(args, "goals_refresh_max_per_run", 3) or 3)
    goals_auto_refresh = bool(getattr(args, "goals_auto_refresh", False))
    goals_completion_level = resolve_goals_completion_level(getattr(args, "goals_completion_level", None))

    async def _try_goals_refresh(cycle_idx: int) -> bool:
        """Attempt LLM-driven GOALS.md refresh. Returns True if new items appended."""
        nonlocal goals_refresh_count
        eprint(f"[GOALS-REFRESH] Attempt {goals_refresh_count + 1}/{goals_refresh_max}")
        try:
            _gp, _gt = read_goals(repo)
            refresh_prompt = build_goals_refresh_prompt(_gt or "")
            refresh_text, _ = await _run_claude_query(
                cfg, refresh_prompt,
                repo=repo, stage="goals_refresh",
                stop_path=stop_path, debug=bool(getattr(args, "debug", False)),
                model_override=str(getattr(args, "pm_model", "") or ""),
                max_turns_override=15,
                timeout_seconds=int(getattr(args, "pm_timeout_seconds", 900) or 900),
                heartbeat_callback=lambda: metrics.event("heartbeat", stage="goals_refresh"),
            )
            result = parse_and_append_refreshed_goals(repo, refresh_text or "")
            if result.get("appended"):
                goals_refresh_count += 1
                eprint(
                    f"[GOALS-REFRESH] +{result.get('p0_count', 0)} P0, +{result.get('p1_count', 0)} P1 added"
                )
                metrics.event("goals_refresh_ok", cycle=cycle_idx,
                              p0=result.get("p0_count", 0), p1=result.get("p1_count", 0),
                              refresh_n=goals_refresh_count)
                return True
            eprint("[GOALS-REFRESH] No new valid goals appended")
            return False
        except Exception as ex:
            if is_quota_exception(ex):
                raise
            eprint(f"[WARN] Goals refresh failed: {ex}")
            return False

    def _state_failed_count() -> int:
        try:
            items = load_state(state_path).get("failed", [])
            return len(items) if isinstance(items, list) else 0
        except Exception:
            return 0

    def _failure_statuses_since(start_index: int) -> list[str]:
        try:
            items = load_state(state_path).get("failed", [])
        except Exception:
            items = []
        if not isinstance(items, list):
            return []
        selected = items[start_index:] if 0 <= start_index <= len(items) else items[-1:]
        if not selected and items:
            selected = items[-1:]
        statuses: list[str] = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            status = str(
                item.get("task_status")
                or item.get("taskStatus")
                or item.get("outcome_status")
                or item.get("status")
                or ""
            ).strip().lower()
            if status:
                statuses.append(status)
        return statuses

    try:
        for cycle_idx in cycle_indices:
            if stop_path.exists():
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=stop_file")
                break

            check_and_remove_stale_git_lock(repo)
            write_heartbeat(run_dir)
            quota_waited_this_cycle = False

            # --- Pre-cycle quota utilization check ---
            if quota_check_enabled:
                q_action, q_info, q_resets = check_quota_utilization(
                    five_hour_max=quota_5h_max, seven_day_max=quota_7d_max,
                )
                _q5h = q_info.get("five_hour", "N/A")
                _q7d = q_info.get("seven_day", "N/A")
                if q_action == "stop":
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=quota_utilization_7d 5h={_q5h}% 7d={_q7d}%")
                    logger.stop_event(f"7-day quota {_q7d}% >= {quota_7d_max}% - stopping run. (5h={_q5h}%)")
                    metrics.event("quota_utilization_stop", cycle=cycle_idx, window="seven_day",
                                  five_hour=_q5h, seven_day=_q7d, resets_at=q_resets or "")
                    last_reason = STOP_REASON_QUOTA_UTILIZATION
                    break
                if q_action == "wait":
                    wait_sec = seconds_until_reset(q_resets)
                    # Failover 판정: enabled + reason이 failover_on에 포함 + 대체 백엔드 존재
                    _fo_enabled = bool(getattr(args, "failover_enabled", False))
                    _fo_on = set(str(x).strip().lower() for x in (getattr(args, "failover_on", []) or []))
                    _fo_backends = [str(b).strip().lower() for b in (getattr(args, "failover_backends", []) or []) if str(b).strip().lower() != "claudecode"]
                    _can_failover = _fo_enabled and STOP_REASON_QUOTA_UTILIZATION in _fo_on and len(_fo_backends) > 0
                    if _can_failover:
                        # Failover 가능 → 즉시 종료하여 runner_entry가 다른 백엔드로 전환
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=quota_utilization_5h_failover 5h={_q5h}% 7d={_q7d}%")
                        logger.stop_event(f"5-hour quota {_q5h}% >= {quota_5h_max}% - failover enabled, stopping for backend switch. (7d={_q7d}%)")
                        metrics.event("quota_utilization_failover", cycle=cycle_idx, window="five_hour",
                                      five_hour=_q5h, seven_day=_q7d, resets_at=q_resets or "")
                        last_reason = STOP_REASON_QUOTA_UTILIZATION
                        try:
                            stop_path.write_text(STOP_REASON_QUOTA_UTILIZATION, encoding="utf-8")
                        except Exception:
                            pass
                        break
                    elif quota_wait_for_reset and wait_sec > 0:
                        quota_waited_this_cycle = True
                        wait_min = wait_sec / 60
                        logger.info(f"[QUOTA-WAIT] 5h quota {_q5h}% >= {quota_5h_max}% - waiting {wait_min:.1f}min for reset (resets_at={q_resets})")
                        logger.quota_event("wait", five_hour=_q5h, seven_day=_q7d, resets_at=q_resets, wait_seconds=wait_sec)
                        metrics.event("quota_utilization_wait", cycle=cycle_idx, window="five_hour",
                                      five_hour=_q5h, seven_day=_q7d, wait_seconds=wait_sec, resets_at=q_resets or "")
                        await asyncio.sleep(wait_sec)
                        logger.info(f"[QUOTA-WAIT] Resumed after {wait_min:.1f}min wait - continuing cycle {cycle_idx}")
                        logger.quota_event("resumed")
                    elif not quota_wait_for_reset:
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=quota_utilization_5h 5h={_q5h}% 7d={_q7d}%")
                        logger.stop_event(f"5-hour quota {_q5h}% >= {quota_5h_max}% - quota_wait_for_reset disabled, stopping. (7d={_q7d}%)")
                        metrics.event("quota_utilization_stop", cycle=cycle_idx, window="five_hour",
                                      five_hour=_q5h, seven_day=_q7d, resets_at=q_resets or "")
                        last_reason = STOP_REASON_QUOTA_UTILIZATION
                        break
                    else:
                        logger.quota_event("imminent", five_hour=_q5h, seven_day=_q7d)
                if q_action == "ok":
                    logger.quota_event("ok", five_hour=_q5h, seven_day=_q7d)

            # --- Per-cycle budget reset ---
            if budget_reset_per_cycle and cycle_idx > 0:
                prev_budget = {
                    "total_escalations": budget_state["total_escalations"],
                    "total_continuations": budget_state["total_continuations"],
                    "total_repairs": budget_state["total_repairs"],
                }
                budget_state["total_escalations"] = 0
                budget_state["total_continuations"] = 0
                budget_state["total_repairs"] = 0
                budget_state["per_task_escalations"] = {}
                budget_state["per_task_continuations"] = {}
                logger.budget_event("reset_per_cycle", prev_esc=prev_budget['total_escalations'], prev_cont=prev_budget['total_continuations'], prev_rep=prev_budget['total_repairs'])

            failed_count_before_cycle = _state_failed_count()
            rc, reason, delta, qa_followups = await run_cycle(cycle_idx)
            last_rc = rc
            last_reason = reason
            print(f"[CYCLE] {now_iso()} idx={cycle_idx} rc={rc} reason={reason} progress_delta={delta}")

            # Prune stale per-task keys from budget_state to prevent unbounded growth
            stale_keys = [k for k in budget_state if k.startswith("_no_diff_retry_")]
            for k in stale_keys:
                del budget_state[k]

            # Also prune completed task entries from per-task budgets
            try:
                _done_ids = set(load_state(state_path).get("done", []))
            except Exception:
                _done_ids = set()
            if budget_state.get("per_task_escalations"):
                budget_state["per_task_escalations"] = {k: v for k, v in budget_state["per_task_escalations"].items() if k not in _done_ids}
            if budget_state.get("per_task_continuations"):
                budget_state["per_task_continuations"] = {k: v for k, v in budget_state["per_task_continuations"].items() if k not in _done_ids}

            # --- Consecutive failure tracking ---
            raw_cycle_failed = (rc != 0) or reason == "budget_exceeded"
            cycle_failure_statuses = _failure_statuses_since(failed_count_before_cycle)
            cycle_failed = should_count_cycle_failure_for_stop(
                reason=reason,
                task_statuses=cycle_failure_statuses,
                rc=rc,
            )
            if raw_cycle_failed and not cycle_failed:
                status_counts = count_task_status_groups(cycle_failure_statuses)
                metrics.event(
                    "cycle_failure_not_counted",
                    cycle=cycle_idx,
                    reason=reason,
                    rc=rc,
                    task_statuses=cycle_failure_statuses,
                    status_counts=status_counts,
                )
                append_cycle_summary(
                    f"{now_iso()} cycle={cycle_idx} failure_not_counted reason={reason} statuses={','.join(cycle_failure_statuses) or '(none)'}"
                )
            if cycle_failed and delta <= 0:
                consecutive_failures += 1
                logger.warning(f"Consecutive failed cycles: {consecutive_failures}/{max_consecutive_failed_cycles}")
                if consecutive_failures >= max_consecutive_failed_cycles:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=consecutive_failures count={consecutive_failures}")
                    logger.stop_event(f"{consecutive_failures} consecutive failed cycles with no progress - stopping run.")
                    break
            else:
                consecutive_failures = 0

            if reason == STOP_REASON_QUOTA:
                # Prefer backend failover over local quota waiting when configured.
                _fo_enabled = bool(getattr(args, "failover_enabled", False))
                _fo_on = set(str(x).strip().lower() for x in (getattr(args, "failover_on", []) or []))
                _fo_backends = [
                    str(b).strip().lower()
                    for b in (getattr(args, "failover_backends", []) or [])
                    if str(b).strip().lower() != "claudecode"
                ]
                _can_failover = _fo_enabled and STOP_REASON_QUOTA in _fo_on and len(_fo_backends) > 0
                if _can_failover:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=quota_exhausted_failover")
                    logger.stop_event(
                        "quota_exhausted detected - failover enabled, stopping current backend for switch."
                    )
                    metrics.event("quota_exhausted_failover", cycle=cycle_idx)
                    last_reason = STOP_REASON_QUOTA
                    try:
                        stop_path.write_text(STOP_REASON_QUOTA, encoding="utf-8")
                    except Exception:
                        pass
                    break
                if quota_wait_for_reset:
                    # Mid-cycle quota exhaustion: wait for reset then continue
                    wait_sec = 0
                    try:
                        _q_action, _q_info, _q_resets = check_quota_utilization(
                            five_hour_max=quota_5h_max, seven_day_max=quota_7d_max,
                        )
                        if _q_resets:
                            wait_sec = seconds_until_reset(_q_resets)
                    except Exception:
                        pass
                    if wait_sec <= 0:
                        # Fallback: 5 minutes minimum wait
                        wait_sec = max(300, loop_sleep_seconds * 5)
                    wait_min = wait_sec / 60
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} quota_exhausted_wait wait_min={wait_min:.1f}")
                    eprint(f"[QUOTA-WAIT] quota_exhausted - waiting {wait_min:.1f}min for reset (quota_wait_for_reset=true)")
                    logger.quota_event("exhausted_wait", wait_seconds=wait_sec)
                    metrics.event("quota_exhausted_wait", cycle=cycle_idx, wait_seconds=wait_sec)
                    if stop_path.exists():
                        try:
                            stop_path.unlink()
                        except Exception:
                            pass
                    await asyncio.sleep(wait_sec)
                    eprint(f"[QUOTA-WAIT] Resumed after {wait_min:.1f}min wait - continuing next cycle")
                    logger.quota_event("exhausted_resumed")
                    consecutive_failures = 0
                    continue
                break

            # --- Goals auto-refresh rescue (frozenset dispatch) ---
            if reason in GOALS_REFRESH_RESCUABLE_REASONS:
                _should, _why = should_attempt_goals_refresh(
                    repo, reason, goals_refresh_count, goals_refresh_max,
                    goals_auto_refresh=goals_auto_refresh,
                    completion_level=goals_completion_level,
                )
                if _should:
                    if await _try_goals_refresh(cycle_idx):
                        # Remove STOP file if ensure_backlog created it.
                        if stop_path.exists():
                            try:
                                stop_path.unlink()
                            except Exception:
                                pass
                        consecutive_failures = max(0, consecutive_failures - 1)
                        continue  # Continue next cycle so PM can generate GOALS-based tasks.

                # Refresh not applicable/failed: keep existing stop behavior by reason.
                if reason == STOP_REASON_PROJECT_COMPLETE:
                    if qa_followups > 0:
                        eprint(f"[GOALS] project_complete deferred - QA added {qa_followups} followup task(s), continuing next cycle.")
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} project_complete_deferred qa_followups={qa_followups}")
                        consecutive_failures = 0
                        continue
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=project_complete")
                    logger.stop_event("Project complete - all goals met.")
                    break
                # no_tasks and pm_refresh_no_backlog fall through to consecutive-failure handling.

            if reason == STOP_REASON_ALL_TASKS_DONE:
                if not loop_mode:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_done")
                    break
                # In loop mode: allow ONE more cycle for PM to generate new tasks.
                # If idle exits are disabled, keep polling for new PM work.
                if delta <= 0:
                    if idle_exit_cycles <= 0 and loop_idle_exit_after <= 0:
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} all_tasks_done_keepalive idle_exits=disabled")
                        logger.info("All tasks done and PM produced no new work - keeping loop alive because idle exits are disabled.")
                    else:
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_done")
                        logger.stop_event("All tasks done and PM produced no new work - stopping loop.")
                        break
                else:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} all_tasks_done progress_delta={delta}")
            if reason == STOP_REASON_ALL_TASKS_ATTEMPTED:
                # All tasks tried but some skipped - in loop mode, next cycle may get new tasks from PM
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_attempted")
                if not loop_mode:
                    break
                # In loop mode, fall through to loop sleep - PM refresh may add new tasks
            if rc != 0 and not (loop_mode and continuous):
                # In continuous loop mode, non-critical failures don't stop the run
                break

            # --- Idle cycle tracking (cycle-count based) ---
            if delta <= 0:
                idle_cycle_count += 1
            else:
                idle_cycle_count = 0
            if idle_exit_cycles > 0 and idle_cycle_count >= idle_exit_cycles:
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=idle_exit idle_cycles={idle_cycle_count}")
                logger.stop_event(f"{idle_cycle_count} consecutive zero-progress cycles - idle exit.")
                break

            if loop_mode:
                if quota_waited_this_cycle:
                    # Quota wait is active waiting (work pending, rate-limited) - not idle
                    idle_accum = 0
                elif delta <= 0:
                    idle_accum += loop_sleep_seconds
                else:
                    idle_accum = 0
                if loop_idle_exit_after > 0 and idle_accum >= loop_idle_exit_after:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=idle_exit idle_accum={idle_accum}")
                    break
                await asyncio.sleep(max(0, loop_sleep_seconds))
            else:
                break
    finally:
        detected_reason = ""
        try:
            detected_reason = detect_stop_reason([stop_path])
        except Exception:
            detected_reason = ""
        final_reason = choose_stop_reason([last_reason, detected_reason]) or last_reason
        report_path = run_dir / "SHUTDOWN_REPORT.md"
        if not report_path.exists():
            try:
                await write_shutdown_report(final_reason or "ok", cycle=cycle_idx if "cycle_idx" in locals() else -1, step=-1)
            except Exception as ex:
                eprint(f"[WARN] Failed to write shutdown report: {ex}")
        # Print dependency summary if any tasks needed dependencies
        dep_summary = run_dir / "DEPENDENCIES_NEEDED.md"
        if dep_summary.exists():
            try:
                dep_text = dep_summary.read_text(encoding="utf-8", errors="replace").strip()
                if dep_text:
                    eprint("\n" + "=" * 60)
                    eprint("[ACTION REQUIRED] Some tasks need manual dependency installation:")
                    eprint(dep_text)
                    eprint("=" * 60 + "\n")
            except Exception:
                pass
        if worktree_dir is not None:
            gitops_cfg = getattr(args, "gitops", {}) if isinstance(getattr(args, "gitops", {}), dict) else {}
            exclude_globs = gitops_cfg.get("untracked_exclude_globs", []) or []
            merge_mode = str(gitops_cfg.get("worktree_merge_mode") or "manual").strip().lower()
            auto_apply_worktree = merge_mode in {"auto", "apply", "true", "yes", "y"}
            last_rc = handle_worktree_patch(
                repo,
                source_repo,
                run_dir,
                last_rc,
                base_ref=source_base_ref or "HEAD",
                auto_apply=auto_apply_worktree,
                exclude_globs=exclude_globs,
            )
            pending_merge = run_dir / "WORKTREE_MERGE_PENDING.json"
            apply_failure = run_dir / "WORKTREE_APPLY_FAILURE.md"
            if pending_merge.exists():
                eprint("")
                eprint("[ACTION REQUIRED] Worktree merge pending.")
                eprint(f" - worktree: {worktree_dir}")
                eprint(f" - patch:    {run_dir / 'worktree.patch'}")
                eprint(" - shell:    /merge-worktree  (or /discard-worktree)")
                eprint("")
            if auto_apply_worktree or (not pending_merge.exists() and not apply_failure.exists()):
                try:
                    remove_worktree(source_repo, worktree_dir)
                except Exception as ex:
                    eprint(f"[WARN] Failed to remove worktree: {ex}")
        run_summary["final"] = {"rc": last_rc, "reason": final_reason or ""}
        _write_run_summary()
        try:
            ctx_repo = source_repo if worktree_dir is not None else repo
            ctx = collect_shutdown_context(ctx_repo, run_dir)
            tasks_done = int(ctx.get("tasks_done") or 0)
            tasks_total = int(ctx.get("tasks_total") or 0)
            porcelain = (ctx.get("git_porcelain") or "").strip()
            change_count = len([ln for ln in porcelain.splitlines() if ln.strip()])
            policy_summary = ctx.get("policy_scan_summary") or {}
            policy_fail = policy_summary.get("fail_total")
            policy_part = f" policy_fail={policy_fail}" if policy_fail is not None else ""
            print(f"[SHUTDOWN] reason={final_reason or 'ok'} cycles={len(run_summary['cycles'])} tasks={tasks_done}/{tasks_total} changes={change_count} run_dir={run_dir}{policy_part}")
        except Exception:
            print(f"[SHUTDOWN] reason={final_reason or last_reason or 'ok'} run_dir={run_dir}")
        logger.close()

    return last_rc






