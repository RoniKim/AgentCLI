"""Claude Code backend — full feature parity with Codex backend (cycle.py).

This backend uses the Claude Agent SDK (claude_agent_sdk) as the execution engine
while providing the same artifacts, logging, and orchestration as the Codex backend.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple
import inspect

from ..process_guard import register_pid, unregister_pid
from ..analysis_cache import merge_dev_hints_to_global_changelog
from ..docs import load_dotenv_best_effort, resolve_docs_dir, generate_docs_digest
from ..gates import run_build_gate_async, run_test_gate_async
from ..gitops import (
    git_head,
    git_changed_files,
    git_worktree_changed_files,
    git_porcelain,
    git_untracked_files,
    has_working_tree_changes,
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
from ..scan import collect_scan_files, DEFAULT_SCAN_IGNORE_GLOBS
from ..prompts import (
    PromptStore,
    ensure_pm_instructions_have_output_schema,
    append_pm_output_contract,
    codex_call_hint,
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
from ..reporting import collect_shutdown_context, build_local_shutdown_report
from ..pipeline import PipelineManager, make_stages
from ..pipeline.session import PipelineSession
from ..pipeline.stages.base import StageOutcome
from ..run_dir import make_run_dir, find_latest_run_dir
from ..schemas import PMOutputV2, pm_output_json_schema
from ..state import (
    load_backlog_json,
    parse_backlog_md,
    load_state,
    save_state,
    write_backlog_files,
    mark_backlog_done,
    write_default_p0_backlog,
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
from ..skills.match import suggest_skills
from ..shared import load_json_if_exists as _load_json_if_exists, inline_skills_for as _inline_skills_for, format_skill_selection as _format_skill_selection
from ..task_history import record_task as _record_task_history, format_history_block as _format_history_block, format_split_history_blocks as _format_split_history_blocks, count_unresolved_failures as _count_unresolved_failures, count_consecutive_title_failures as _count_consecutive_title_failures
from ..progress import print_cycle_report, TokenTracker, extract_claude_tokens
from ..tracing import TraceCtx, new_trace_id
from ..utils import (
    force_utf8_stdio,
    eprint,
    now_iso,
    run_cmd,
    safe_write_text,
    has_quota_text,
    choose_stop_reason,
    detect_stop_reason,
    write_heartbeat,
    STOP_REASON_QUOTA,
    STOP_REASON_STOP_FILE,
    STOP_REASON_ALL_TASKS_DONE,
    STOP_REASON_PROJECT_COMPLETE,
)
from ..goals import (
    read_goals,
    format_goals_block,
    parse_goals_completion,
    update_goals_checkboxes,
    write_completion_status,
    GOALS_GENERATION_INSTRUCTION,
    GOALS_EVALUATION_INSTRUCTION,
)


def _patch_prompt_for_claude(prompt: str) -> str:
    """Replace ALL Codex/OpenAI-specific references in prompts with Claude Code equivalents."""
    # 1) Catch-all first: replace any "Codex MCP" mentions before inserting new text
    prompt = re.sub(r"Codex MCP", "Claude Code built-in tools", prompt)

    # 2) "When editing files, call Claude Code built-in tools with ..." → clean instruction
    prompt = re.sub(
        r"When editing files,\s*(?:call|use)\s+Claude Code built-in tools\s+with[^\n]*",
        "When editing files, use Claude Code built-in tools (Read, Write, Edit, Grep, Glob, Bash) directly.",
        prompt,
    )

    # 3) "use Codex skills system" → neutral phrasing
    prompt = re.sub(
        r"\(use Codex skills system;\s*do NOT inline skill text\)",
        "(apply the skills listed below; do NOT inline full skill text)",
        prompt,
    )

    # 4) "Prefer apply_patch for edits" → Claude Code Edit tool
    prompt = re.sub(
        r"Prefer apply_patch for (?:edits|modifications)[^.\n]*\.?",
        "Use the Edit tool for targeted modifications and the Write tool for new files.",
        prompt,
    )

    return prompt


# ---------------------------------------------------------------------------
# Claude SDK adapter helpers (SDK-specific, not shared with Codex)
# ---------------------------------------------------------------------------

class StopRequested(Exception):
    pass


class BudgetExceeded(Exception):
    pass


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
    elif stage_low == "dev":
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
    }
    # Apply SDK extensions (MCP tools, hooks, can_use_tool, subagents)
    from .claude_extensions import apply_extensions, _MCP_TOOL_NAMES
    apply_extensions(ext_ctx, cfg, kwargs, stage)

    # Snapshot which extension keys were added before filtering
    _ext_keys_before = {k for k in ("mcp_servers", "hooks", "can_use_tool", "agents") if k in kwargs}

    kwargs = _filter_kwargs_for_ctor(ClaudeAgentOptions, kwargs)

    # Clean up orphaned tool names when _filter_kwargs_for_ctor drops extension keys
    # (SDK version doesn't support these params yet → tool names would reference nothing)
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
        import queue
        _q: queue.Queue = queue.Queue()
        _SENTINEL = object()
        async def _threaded_sync_iter():
            loop = asyncio.get_event_loop()
            def _consume():
                try:
                    for msg in stream:
                        _q.put(msg)
                finally:
                    _q.put(_SENTINEL)
            fut = loop.run_in_executor(None, _consume)
            while True:
                item = await loop.run_in_executor(None, _q.get)
                if item is _SENTINEL:
                    break
                yield item
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
                eprint(f"  [TOOL_RESULT] {tool_name} → {status}")

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

        # Capture error/system messages — Claude Code may surface quota or
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
    Single attempt — by the time ``__aenter__`` returns, the subprocess
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
) -> Tuple[str, Optional[Any]]:
    """High-level helper: create client, send query, collect messages.

    Includes retry with exponential backoff for transient errors (429, 5xx, timeout).
    Quota/budget/stop exceptions are never retried.

    A safety timeout of 1 hour is applied when timeout_seconds is 0 (unlimited)
    to prevent indefinite hangs from stalled SDK streams.
    """
    from claude_agent_sdk import ClaudeSDKClient

    _DEFAULT_SAFETY_TIMEOUT = 3600  # 1 hour — prevents infinite hang
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
                try:
                    await asyncio.wait_for(_start_query(client, prompt), timeout=120)
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
                    if child_pid is not None:
                        unregister_pid(child_pid)
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
            return await _run_claude_query(
                cfg, prompt, repo=repo, stage=stage, stop_path=stop_path, debug=debug,
                model_override=model_override, stage_instructions=stage_instructions,
                max_turns_override=max_turns_override, timeout_seconds=timeout_seconds,
                ext_ctx=ext_ctx,
            )
        except (StopRequested, BudgetExceeded):
            raise
        except Exception as ex:
            if is_quota_exception(ex):
                raise
            if cont_left > 0 and is_max_turns_exception(ex):
                # Budget checks
                if _budget_exceeded and callable(_budget_exceeded):
                    if _budget_exceeded("total_continuations", bs.get("total_continuations", 0),
                                        int(bc.get("max_total_continuations_per_run") or 0)):
                        if metrics:
                            metrics.event("budget_exceeded", cycle=-1, reason="total_continuations")
                        raise BudgetExceeded("total_continuations")
                    per_task.setdefault(task_key, 0)
                    if _budget_exceeded("dev_continuations_per_task", per_task[task_key],
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
# Exception detection helpers (ported from cycle.py)
# ---------------------------------------------------------------------------

def _iter_exc_chain(ex: Exception, max_depth: int = 6):
    cur = ex
    seen: set[int] = set()
    for _ in range(max_depth):
        if cur is None or id(cur) in seen:
            break
        seen.add(id(cur))
        yield cur
        nxt = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        if nxt is None or not isinstance(nxt, BaseException):
            break
        cur = nxt  # type: ignore[assignment]


def is_max_turns_exception(ex: Exception) -> bool:
    for e in _iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        name = type(e).__name__.lower()
        rep = (repr(e) or "").lower()
        if (
            "max turns" in msg or "max_turn" in msg or "maxturn" in msg
            or "maxturn" in name or "max_turn" in name
            or ("turn" in name and "max" in name)
            or "maxturnsexceeded" in rep
        ):
            return True
    return False


def is_quota_exception(ex: Exception) -> bool:
    """Detect quota/billing/rate-limit exhaustion.

    Delegates to the canonical ``has_quota_text`` in utils.py.
    """
    for e in _iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        rep = (repr(e) or "").lower()
        if has_quota_text(msg) or has_quota_text(rep):
            return True
    return False


def is_model_invalid_exception(ex: Exception) -> bool:
    needles = (
        "model_not_found", "model not found", "does not exist",
        "unknown model", "invalid model", "is not available",
    )
    for e in _iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        rep = (repr(e) or "").lower()
        if ("model" in msg or "model" in rep) and (any(n in msg for n in needles) or any(n in rep for n in needles)):
            return True
    return False


def is_transient_exception(ex: Exception) -> bool:
    """Detect transient / retryable API errors (rate-limit, 5xx, timeout)."""
    # NOTE: "500" removed from needles to avoid false positives on port numbers
    # (e.g. "localhost:5000"). HTTP 500 is caught by the status_code check below.
    needles = (
        "rate_limit", " 429", " 503", " 502",
        "overloaded", "connection", "timeout", "timed out",
        "internal server error",
    )
    for e in _iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        rep = (repr(e) or "").lower()
        if any(n in msg for n in needles) or any(n in rep for n in needles):
            return True
        status = getattr(e, "status_code", None) or getattr(e, "status", None)
        if status is not None:
            try:
                if int(status) in (429, 500, 502, 503):
                    return True
            except (ValueError, TypeError):
                pass
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main_async_claudecode(args: argparse.Namespace, repo: Path) -> int:
    """Claude Code backend main — full parity with Codex backend (cycle.py)."""

    force_utf8_stdio()

    repo = repo.expanduser().resolve()
    if not repo.exists():
        eprint(f"Repo not found: {repo}")
        return 2

    load_dotenv_best_effort(repo, getattr(args, "env_file", ""))

    if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        eprint(
            "[WARN] ANTHROPIC_API_KEY is not set. "
            "Claude Agent SDK will use Claude Code authentication if available."
        )

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
        try:
            # Trim in-memory cycles to prevent unbounded growth
            if len(run_summary["cycles"]) > _MAX_SUMMARY_CYCLES:
                run_summary["cycles"] = run_summary["cycles"][-_MAX_SUMMARY_CYCLES:]
            (run_dir / "run_summary.json").write_text(
                json.dumps(run_summary, ensure_ascii=False, indent=2),
                encoding="utf-8", errors="replace",
            )
        except Exception as ex:
            eprint(f"[WARN] Failed to write run_summary.json: {ex}")

    def _is_unsafe_path(raw: str) -> bool:
        try:
            return ".." in Path(raw).parts
        except Exception:
            return True

    def _fail_validation(name: str, value: str) -> None:
        msg = (
            "# Validation failure\n\n"
            f"Blocked unsafe path for `{name}`: `{value}`\n\n"
            "Path traversal patterns like `..` are not allowed. Use an absolute path or a safe relative path.\n"
        )
        safe_write_text(run_dir / "VALIDATION_FAILURE.md", msg)

    for _name, _value in (("env_file", getattr(args, "env_file", "") or ""), ("prompts_dir", getattr(args, "prompts_dir", "") or "")):
        if str(_value).strip() and _is_unsafe_path(str(_value)):
            _fail_validation(_name, str(_value))
            eprint(f"[STOP] Validation failure for {_name}: {_value}")
            return 2

    source_repo = repo
    worktree_dir: Optional[Path] = None
    if bool(getattr(args, "worktree_isolation", False)):
        worktree_dir = run_dir / "worktree"
        try:
            create_worktree(source_repo, worktree_dir)
        except Exception as ex:
            eprint(f"[STOP] Failed to create worktree: {ex}")
            return 2
        repo = worktree_dir

    # NOTE: Do NOT call os.chdir(repo) here — it is process-global and
    # thread-unsafe when the runner executes in shell mode's background thread.
    # The SDK receives 'cwd' via ClaudeAgentOptions instead.

    # Observability
    metrics = MetricsLogger(run_dir / "metrics.jsonl")
    logger = create_logger(run_dir, debug=bool(getattr(args, "debug", False)))
    # trace_ctx reserved for future tracing support
    stop_path = run_dir / str(getattr(args, "stop_file", "STOP"))
    cycle_summary_path = run_dir / "cycle_summary.log"
    last_run_summary_path = run_dir / "last_run_summary.json"

    # Global PM cache
    pm_cache_dir = repo / ".doc" / "PM_CACHE"
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
    policy_scan_enabled = bool(policy_cfg.get("enabled", not bool(getattr(args, "no_policy_scan", False))))
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

    def _severity_at_or_above(found: str, threshold: str) -> bool:
        order = {"low": 0, "medium": 1, "high": 2}
        return order.get(found, 1) >= order.get(threshold, 1)

    def _budget_exceeded(key: str, current: int, limit: int) -> bool:
        if limit <= 0:
            return False
        return current >= limit

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

    continuous = bool(getattr(args, "continuous", False) or getattr(args, "loop", False))

    roles_raw = str(getattr(args, "roles", "PM,Dev,QA") or "PM,Dev,QA")
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
        try:
            with cycle_summary_path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(line.rstrip() + "\n")
        except Exception:
            pass

    # Backlog state
    backlog_json_path = run_dir / "BACKLOG.json"
    backlog_md_path = run_dir / "BACKLOG.md"
    state_path = run_dir / "STATE.json"

    # ---------------------------------------------------------------------------
    # Shared helpers (same as Codex)
    # ---------------------------------------------------------------------------

    def _collect_scan(scope: str, *, ignore_paths: Optional[list[str]] = None) -> tuple[list[tuple[str, str]], dict[str, Any]]:
        return collect_scan_files(
            repo, scope,
            ignore_paths=scan_ignore_paths if ignore_paths is None else ignore_paths,
            ignore_globs=scan_ignore_globs,
            max_files=scan_max_files,
            max_bytes_per_file=scan_max_bytes_per_file,
            max_total_bytes=scan_max_total_bytes,
            timeout_seconds=scan_timeout_seconds,
            include_untracked_in_full=scan_include_untracked_in_full,
        )

    def _hash_prompt(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:10]

    def _normalize_followup_prompt(text: str) -> str:
        s = str(text or "").strip()
        if len(s) > 1000:
            s = s[:1000].rstrip()
        return s

    def _extract_qa_followups(text: str, *, max_items: int) -> list[dict[str, Any]]:
        items: list[str] = []
        for line in (text or "").splitlines():
            s = line.strip()
            if not s:
                continue
            if re.match(r"^[-*•]\s+", s) or re.match(r"^\d+[\.\)]\s+", s):
                s = re.sub(r"^[-*•]\s+", "", s)
                s = re.sub(r"^\d+[\.\)]\s+", "", s)
                if len(s) >= 10:
                    items.append(s)
            if len(items) >= max_items:
                break
        tasks: list[dict[str, Any]] = []
        for s in items:
            prompt = _normalize_followup_prompt(s)
            if not prompt:
                continue
            tid = f"QA-FU-{_hash_prompt(prompt)}"
            title = f"QA Follow-up: {s[:60]}".strip()
            tasks.append({
                "id": tid, "title": title, "prompt": prompt, "files": [],
                "done_when": "QA follow-up addressed and relevant tests/builds pass.",
                "skills": [], "skills_rationale": None, "depends_on": [],
            })
        return tasks

    def _followups_from_structured(model: Any, *, max_items: int) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        if not model or not getattr(model, "followups", None):
            return tasks
        for item in list(model.followups)[:max_items]:
            prompt = _normalize_followup_prompt(getattr(item, "prompt", ""))
            if not prompt:
                continue
            tid = f"QA-FU-{_hash_prompt(prompt)}"
            title = str(getattr(item, "title", "") or f"QA Follow-up: {prompt[:60]}").strip()
            severity = str(getattr(item, "severity", "") or "").strip()
            if severity:
                title = f"[{severity}] {title}"
            files = list(getattr(item, "files", []) or [])
            tasks.append({
                "id": tid, "title": title, "prompt": prompt, "files": files,
                "done_when": "QA follow-up addressed and relevant tests/builds pass.",
                "skills": [], "skills_rationale": None, "depends_on": [],
            })
        return tasks

    def _merge_qa_followups(
        base_tasks: list[dict[str, Any]],
        followups: list[dict[str, Any]],
        done_ids: set[str],
    ) -> list[dict[str, Any]]:
        existing_ids = {str(t.get("id") or "") for t in base_tasks if str(t.get("id") or "")}
        merged = list(base_tasks)
        for t in followups:
            tid = str(t.get("id") or "")
            if not tid or tid in existing_ids or tid in done_ids:
                continue
            merged.append(t)
        return merged

    def _normalize_backlog_tasks(raw_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def _looks_like_pm_work(t: dict[str, Any]) -> bool:
            txt = f"{t.get('title','')}\n{t.get('prompt','')}".lower()
            forbidden = (
                "create backlog", "generate backlog", "backlog.json", "backlog.md",
                "backlog", "triage", "prioritize", "roadmap", "plan", "planning",
                "analysis", "review", "audit", "repo_inventory", "repo inventory",
                "inventory", "prompt engineering", "update prompts", "pm instructions",
                "status report", "progress report", "shutdown report", "postmortem",
                "project_analysis.md", "project analysis", "pm_cache", "pm cache",
                "agent_runs", "run_dir", "state.json", "notes_pm.md", "requirements.md",
                "agent_tasks.md", "notes.md",
                "백로그", "분석", "검토", "리포트", "보고서", "인벤토리", "프롬프트", "계획", "정리",
            )
            if any(k in txt for k in forbidden):
                positive = ("implement", "fix", "build", "test", "ui", "screen", "page", "component", "refactor")
                if any(p in txt for p in positive):
                    return False
                return True
            files = t.get("files") or []
            if isinstance(files, list) and files:
                fl = [str(x).replace("\\", "/").lower().strip() for x in files if str(x).strip()]
                if all((p.startswith(".doc/") or "/.doc/" in p) for p in fl):
                    return True
                if any("agent_runs" in p or "pm_cache" in p or "project_analysis" in p or "repo_inventory" in p for p in fl):
                    return True
            return False

        filtered: list[dict[str, Any]] = []
        removed: list[dict[str, Any]] = []
        for t in raw_tasks:
            if not isinstance(t, dict):
                continue
            if _looks_like_pm_work(t):
                removed.append(t)
            else:
                filtered.append(t)

        if removed:
            try:
                notes_path = run_dir / "NOTES_PM.md"
                existing = ""
                if notes_path.exists():
                    existing = notes_path.read_text(encoding="utf-8-sig", errors="replace")
                extra = ["\n\n## Removed PM-only tasks (auto-filter)", "(These were removed to avoid PM delegating planning artifacts to Dev.)", ""]
                for t in removed[:20]:
                    extra.append(f"- {t.get('id','(no id)')} {t.get('title','')}")
                notes_path.write_text((existing.rstrip() + "\n" + "\n".join(extra)).strip() + "\n", encoding="utf-8", errors="replace")
            except Exception:
                pass

        used: set[str] = set()
        next_num = 1
        out: list[dict[str, Any]] = []
        for t in filtered:
            tid = str(t.get("id") or "").strip()
            m = re.match(r"^T(\d+)$", tid)
            n = int(m.group(1)) if m else 0

            if n >= 1 and tid and tid not in used:
                fixed_id = tid
            else:
                while True:
                    cand = f"T{next_num}"
                    next_num += 1
                    if cand not in used:
                        fixed_id = cand
                        break

            used.add(fixed_id)
            skills_val = t.get("skills") or []
            if isinstance(skills_val, list):
                skills = [str(s).strip() for s in skills_val if str(s).strip()]
            elif isinstance(skills_val, str):
                skills = [s.strip() for s in skills_val.split(",") if s.strip()]
            else:
                skills = []
            depends_on_val = t.get("depends_on") or []
            if isinstance(depends_on_val, list):
                depends_on = [str(d).strip() for d in depends_on_val if str(d).strip()]
            else:
                depends_on = []
            out.append({
                "id": fixed_id,
                "title": str(t.get("title") or fixed_id).strip() or fixed_id,
                "prompt": str(t.get("prompt") or "").strip() or f"Implement {fixed_id}.",
                "files": t.get("files") if isinstance(t.get("files"), list) else [],
                "done_when": str(t.get("done_when") or "Git diff exists and build passes.").strip(),
                "skills": skills,
                "skills_rationale": None if t.get("skills_rationale") is None else str(t.get("skills_rationale")),
                "depends_on": depends_on,
            })
        return out

    def _validate_skill_ids(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not skills_enabled or not tasks:
            return tasks
        autofix = bool(skills_cfg.get("skill_match_autofix", False))
        threshold = float(skills_cfg.get("skill_match_autofix_threshold") or 0)
        updated: list[dict[str, Any]] = []
        for task in tasks:
            skills_list = [str(s).strip() for s in (task.get("skills") or []) if str(s).strip()]
            new_skills: list[str] = []
            for sid in skills_list:
                if sid in skills_by_id:
                    new_skills.append(sid)
                    continue
                suggestions = suggest_skills(sid, skills_records, max_results=3)
                if suggestions:
                    top = suggestions[0]
                    suggestion_msg = ", ".join([f"{s.skill_id}({s.name}, {s.score:.2f})" for s in suggestions])
                    eprint(f"[SKILLS] Unknown skill_id '{sid}'. Suggestions: {suggestion_msg}")
                    if autofix and top.score >= threshold:
                        eprint(f"[SKILLS] Auto-fix: '{sid}' -> '{top.skill_id}' (score {top.score:.2f})")
                        new_skills.append(top.skill_id)
                    else:
                        new_skills.append(sid)
                else:
                    eprint(f"[SKILLS] Unknown skill_id '{sid}' (no suggestions)")
                    new_skills.append(sid)
            new_task = dict(task)
            new_task["skills"] = new_skills
            updated.append(new_task)
        return updated

    def ensure_backlog() -> bool:
        if backlog_json_path.exists() or backlog_md_path.exists():
            return True
        eprint("[PM ERROR] BACKLOG not created by PM. Stopping to avoid running irrelevant tasks.")
        try:
            stop_path.write_text("BACKLOG missing\n", encoding="utf-8", errors="replace")
        except Exception:
            pass
        metrics.event("pm_backlog_missing", cycle=-1)
        return False

    def load_tasks() -> list[TaskItem]:
        tasks: list[TaskItem] = []
        if backlog_json_path.exists():
            try:
                tasks = load_backlog_json(backlog_json_path)
            except Exception as ex:
                eprint(f"Failed to parse BACKLOG.json: {ex}")
        if not tasks and backlog_md_path.exists():
            tasks = parse_backlog_md(backlog_md_path)
        return tasks

    def _load_backlog_context_for_pm() -> tuple[str, list[TaskItem], set[str]]:
        tasks: list[TaskItem] = []
        if backlog_json_path.exists():
            try:
                tasks = load_backlog_json(backlog_json_path)
            except Exception:
                pass
        if not tasks and backlog_md_path.exists():
            try:
                tasks = parse_backlog_md(backlog_md_path)
            except Exception:
                pass
        try:
            state_obj = load_state(state_path)
        except Exception:
            state_obj = {"done": [], "failed": []}
        done_ids = set(state_obj.get("done", []) or [])
        failed_list = state_obj.get("failed", []) or []
        failed_ids = {(f.get("task", "") if isinstance(f, dict) else f) for f in failed_list}
        lines: list[str] = []
        for t in tasks:
            if t.id in done_ids:
                mark = "x"
            elif t.id in failed_ids:
                mark = "F"
            else:
                mark = " "
            lines.append(f"- [{mark}] {t.id} {t.title}")
        block = "\n".join(lines) if lines else "(no backlog found)"
        return block, tasks, done_ids

    def _build_failed_tasks_block() -> str:
        """Build a summary of failed tasks with reasons for PM context."""
        try:
            state_obj = load_state(state_path)
        except Exception:
            state_obj = {"failed": []}
        failed_list = state_obj.get("failed", []) or []
        if not failed_list:
            return "(none)"
        lines: list[str] = []
        for f in failed_list:
            if isinstance(f, dict):
                tid = f.get("task", "?")
                reason = f.get("reason", "unknown")
                lines.append(f"- {tid}: {reason}")
            else:
                lines.append(f"- {f}: unknown")
        return "\n".join(lines)

    def _record_history(task_id: str, title: str, status: str, reason: str = "",
                        detail: str = "", files: list[str] | None = None, cycle: int = 0,
                        attempt: int = 0, max_attempts: int = 1) -> None:
        if not bool(getattr(args, "task_history_enabled", True)):
            return
        _record_task_history(repo, task_id=task_id, title=title, status=status,
                             reason=reason, detail=detail, files=files,
                             cycle_idx=cycle, attempt=attempt, max_attempts=max_attempts,
                             run_id=run_dir.name, backend="claudecode")

    # ---------------------------------------------------------------------------
    # PM phase (structured output with repair — same as Codex)
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
                    metrics=metrics, _budget_exceeded=_budget_exceeded,
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
                eprint(f"[PM] Claude error: {ex}")
                if bool(getattr(args, "debug", False)):
                    eprint(traceback.format_exc())
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

            # Early exit: quota or repetitive garbage — no point retrying
            if "<quota_detected>" in missing:
                eprint("[PM] Quota/rate-limit text detected in PM output — aborting PM structured retries.")
                metrics.event("pm_garbage_detected", cycle=cycle_idx, kind="quota")
                raise Exception("quota exceeded — detected in PM output")
            if "<repetitive_output>" in missing:
                eprint("[PM] Repetitive/garbage output detected — aborting PM structured retries.")
                metrics.event("pm_garbage_detected", cycle=cycle_idx, kind="repetitive")
                break

            if attempt < retries:
                repair_limit = int(budgets_cfg.get("max_total_repair_attempts_per_run") or 0)
                if _budget_exceeded("total_repairs", budget_state["total_repairs"], repair_limit):
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

        try:
            inventory = build_repo_inventory(repo)
            _, inv_md = write_repo_inventory_files(repo, pm_cache_dir, inventory)
        except Exception as inv_ex:
            metrics.event("inventory_error", cycle=cycle_idx, error=str(inv_ex))
            inv_md = pm_cache_dir / "REPO_INVENTORY.md"
            try:
                pm_cache_dir.mkdir(parents=True, exist_ok=True)
                inv_md.write_text("# REPO_INVENTORY\n\n- (inventory generation failed)\n", encoding="utf-8", errors="replace")
            except Exception:
                inv_md = run_dir / "REPO_INVENTORY.md"
                try:
                    inv_md.write_text("# REPO_INVENTORY\n\n- (inventory generation failed)\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass

        todo_path, todo_text = read_current_todo(repo)
        todo_block = format_todo_block(todo_path, todo_text)

        # Goals context
        _goals_enabled = bool(getattr(args, "goals_enabled", True))
        _goals_auto_gen = bool(getattr(args, "goals_auto_generate", True))
        if _goals_enabled:
            _gp, _gt = read_goals(repo)
            goals_block = format_goals_block(_gp, _gt)
            goals_instruction = GOALS_EVALUATION_INSTRUCTION if _gp else (GOALS_GENERATION_INSTRUCTION if _goals_auto_gen else "")
        else:
            goals_block = "(disabled)"
            goals_instruction = ""

        try:
            if need_bootstrap:
                metrics.event("pm_start", cycle=cycle_idx, kind="bootstrap")
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
                    "goals_block": goals_block,
                    "goals_instruction": goals_instruction,
                    "docs_dir": str(docs_dir) if docs_dir else "(none)",
                    "docs_read_mode": docs_read_mode, "digest_rel": str(digest_rel),
                    "skills_index_summary": skills_index_summary,
                    "codex_call_hint": "Use Claude Code built-in tools (Read, Write, Edit, Grep, Glob, Bash) directly. Do NOT call Codex MCP.",
                    "task_history_block": _format_history_block(repo, max_items=_hist_max) if _hist_enabled else "(disabled)",
                    "done_tasks_block": _done_blk,
                    "failed_tasks_block": _failed_blk,
                    "turn_budget_warning": PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {_pm_max_turns_boot} turns)"),
                }
                pm_prompt = _patch_prompt_for_claude(append_pm_output_contract(store.render("pm_bootstrap_prompt", PM_BOOTSTRAP_TEMPLATE_DEFAULT, ctx)))
                pm_out = await _run_pm_structured(pm_prompt, max_turns=_pm_max_turns_boot, cycle_idx=cycle_idx, kind="bootstrap", output_path=pm_output_path)
                if pm_out is None:
                    metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=1, error="structured_output_failed")
                    return False

                (run_dir / f"PM_OUTPUT_cycle_{cycle_idx:03d}.json").write_text(dump_pretty(pm_out.model_dump()) + "\n", encoding="utf-8", errors="replace")
                if pm_out.notes_md:
                    (run_dir / "NOTES_PM.md").write_text(pm_out.notes_md.strip() + "\n", encoding="utf-8", errors="replace")

                current_backlog_block, existing_tasks, done_ids = _load_backlog_context_for_pm()
                existing_pending = [t for t in existing_tasks if t.id not in done_ids]
                merged_tasks: list[dict[str, Any]] = [t.model_dump() for t in (pm_out.tasks or [])]
                pm_ids = {str(t.get("id", "")).strip() for t in merged_tasks if isinstance(t, dict)}
                for t in existing_pending:
                    if t.id not in pm_ids:
                        merged_tasks.append({"id": t.id, "title": t.title, "prompt": t.prompt, "files": t.files or [], "done_when": t.done_when, "skills": t.skills or [], "skills_rationale": t.skills_rationale, "depends_on": t.depends_on})
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

                last_pm_fp = repo_fp or last_pm_fp
                pm_fp_path.write_text(json.dumps({"fingerprint": last_pm_fp, "updated_at": now_iso()}, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
                metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=0)
                return True

            if need_incremental or force_refresh:
                metrics.event("pm_start", cycle=cycle_idx, kind="incremental" if need_incremental else "refresh")
                changed_files_block = "\n".join([f"- {p}" for p in (changed_files or [])]) or "- (none)"
                hint_lines: list[str] = []
                if dev_hints_dir.exists():
                    for hf in sorted(dev_hints_dir.glob("*.md"), key=lambda x: x.stat().st_mtime)[-12:]:
                        try:
                            rel = hf.relative_to(run_dir).as_posix()
                            content = hf.read_text(encoding="utf-8", errors="replace").strip()
                            hint_lines.append(f"- {rel}:")
                            hint_lines.extend([f"  {ln}" for ln in content.splitlines()[:10]])
                        except Exception:
                            continue
                hint_block = "\n".join(hint_lines) or "(none)"
                current_backlog_block, _, _ = _load_backlog_context_for_pm()
                failed_tasks_block = _build_failed_tasks_block()

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
                    "goals_block": goals_block,
                    "goals_instruction": goals_instruction,
                    "docs_dir": str(docs_dir) if docs_dir else "(none)",
                    "docs_read_mode": docs_read_mode, "digest_rel": str(digest_rel),
                    "skills_index_summary": skills_index_summary,
                    "codex_call_hint": "Use Claude Code built-in tools (Read, Write, Edit, Grep, Glob, Bash) directly. Do NOT call Codex MCP.",
                    "prev_head": prev_head or curr_head, "curr_head": curr_head,
                    "changed_files_block": changed_files_block,
                    "current_backlog_block": current_backlog_block,
                    "hint_block": hint_block,
                    "failed_tasks_block": _failed_blk_i,
                    "task_history_block": _format_history_block(repo, max_items=_hist_max_i) if _hist_enabled_i else "(disabled)",
                    "done_tasks_block": _done_blk_i,
                    "turn_budget_warning": PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {_pm_max_turns_inc} turns)"),
                }
                pm_prompt = _patch_prompt_for_claude(append_pm_output_contract(store.render("pm_incremental_prompt", PM_INCREMENTAL_TEMPLATE_DEFAULT, ctx)))
                pm_out = await _run_pm_structured(pm_prompt, max_turns=_pm_max_turns_inc, cycle_idx=cycle_idx, kind="incremental" if need_incremental else "refresh", output_path=pm_output_path)
                if pm_out is None:
                    metrics.event("pm_end", cycle=cycle_idx, kind="incremental" if need_incremental else "refresh", rc=1, error="structured_output_failed")
                    return False

                (run_dir / f"PM_OUTPUT_cycle_{cycle_idx:03d}.json").write_text(dump_pretty(pm_out.model_dump()) + "\n", encoding="utf-8", errors="replace")
                if pm_out.notes_md:
                    (run_dir / "NOTES_PM.md").write_text(pm_out.notes_md.strip() + "\n", encoding="utf-8", errors="replace")

                current_backlog_block, existing_tasks, done_ids = _load_backlog_context_for_pm()
                existing_pending = [t for t in existing_tasks if t.id not in done_ids]
                merged_tasks = [t.model_dump() for t in (pm_out.tasks or [])]
                pm_ids = {str(t.get("id", "")).strip() for t in merged_tasks if isinstance(t, dict)}
                for t in existing_pending:
                    if t.id not in pm_ids:
                        merged_tasks.append({"id": t.id, "title": t.title, "prompt": t.prompt, "files": t.files or [], "done_when": t.done_when, "skills": t.skills or [], "skills_rationale": t.skills_rationale, "depends_on": t.depends_on})
                if merged_tasks:
                    merged_tasks = _normalize_backlog_tasks(merged_tasks)
                    merged_tasks = _validate_skill_ids(merged_tasks)
                    if merged_tasks:
                        write_backlog_files(run_dir, merged_tasks)

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
    # Dev loop (full parity with Codex — escalation, continuations, policy scan)
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
        before_done = len(done_set.intersection(task_ids))

        if pm_stage_enabled and bool(getattr(args, "pm_refresh_backlog", False)) and (before_done >= len(task_ids)):
            pm_ok2 = await run_pm_if_needed(cycle_idx, curr_head, changed_files, repo_fp, force_refresh_backlog=True)
            if not pm_ok2:
                if pm_stop_reason.get("reason") == STOP_REASON_QUOTA:
                    return 0, STOP_REASON_QUOTA, 0, (len(done_set) > before_done)
                if stop_path.exists():
                    detected = detect_stop_reason([stop_path])
                    return 0, (detected or STOP_REASON_STOP_FILE), 0, (len(done_set) > before_done)
                return 1, "pm_failed", 0, (len(done_set) > before_done)
            ensure_backlog()
            tasks = load_tasks()
            task_ids = {t.id for t in tasks}
            # Clear done_set for IDs that appear in the new backlog — PM recycled these IDs for new tasks
            recycled_ids = done_set & task_ids
            if recycled_ids:
                eprint(f"[PM-REFRESH] Clearing {len(recycled_ids)} recycled task IDs from done set: {sorted(recycled_ids)}")
                done_set -= recycled_ids
                state["done"] = sorted(done_set)
                save_state(state_path, state)
            before_done = len(done_set.intersection(task_ids))

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
                    "4. Do NOT add new features — only fix build errors\n"
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
                        metrics=metrics, _budget_exceeded=_budget_exceeded,
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
                )
                if post_fix_ok:
                    eprint("[BUILD-FIX] Build fixed successfully!")
                    metrics.event("build_fix_end", cycle=cycle_idx, rc=0)
                else:
                    eprint("[BUILD-FIX] Build still broken after fix attempt.")
                    metrics.event("build_fix_end", cycle=cycle_idx, rc=1)
                    # In continuous mode, proceed anyway — partial fixes may help
                    # In non-continuous mode, also proceed — tasks may fix remaining issues

        for step in range(iterations):
            if stop_path.exists():
                break

            max_consecutive_failures = int(getattr(args, "max_consecutive_task_failures", 3) or 3)
            next_task: Optional[TaskItem] = None
            processed = done_set | skipped_set
            for t in tasks:
                if t.id in processed:
                    continue
                # Persistent failure check: skip tasks that failed too many times consecutively
                if bool(getattr(args, "task_history_enabled", True)):
                    consec = _count_consecutive_title_failures(repo, t.title)
                    if consec >= max_consecutive_failures:
                        eprint(f"[SKIP] Task {t.id} '{t.title}' failed {consec} times consecutively (>= {max_consecutive_failures}); skipping.")
                        skipped_set.add(t.id)
                        state.setdefault("failed", []).append({
                            "task": t.id, "reason": "persistent_failure",
                            "detail": f"Failed {consec} consecutive times across runs"
                        })
                        save_state(state_path, state)
                        _record_history(t.id, t.title, "failed", reason="persistent_failure",
                                        detail=f"Auto-skipped after {consec} consecutive failures", files=t.files, cycle=cycle_idx)
                        metrics.event("task_persistent_skip", cycle=cycle_idx, task_id=t.id, consecutive_failures=consec)
                        task_results.append({"id": t.id, "title": t.title, "status": "skipped", "reason": "persistent_failure", "duration": -1})
                        continue
                # Dependency check: all depends_on tasks must be in done_set
                if t.depends_on:
                    unmet = [dep for dep in t.depends_on if dep not in done_set]
                    if unmet:
                        failed_ids = {f.get("task") for f in state.get("failed", []) if isinstance(f, dict)}
                        permanently_blocked = [dep for dep in unmet if dep in (skipped_set | failed_ids)]
                        if permanently_blocked:
                            eprint(f"[SKIP] Task {t.id} depends on failed tasks {permanently_blocked}; skipping.")
                            skipped_set.add(t.id)
                            state.setdefault("failed", []).append({
                                "task": t.id, "reason": "dependency_failed",
                                "detail": f"Depends on: {permanently_blocked}"
                            })
                            save_state(state_path, state)
                            _record_history(t.id, t.title, "failed", reason="dependency_failed",
                                            detail=f"Depends on: {permanently_blocked}", files=t.files, cycle=cycle_idx)
                            task_results.append({"id": t.id, "title": t.title, "status": "skipped", "reason": "dependency_failed", "duration": -1})
                            continue
                        continue  # pending dependencies — try later
                next_task = t
                break
            if not next_task:
                break

            task_dir = tasks_root / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}"
            task_dir.mkdir(parents=True, exist_ok=True)

            metrics.event("task_start", cycle=cycle_idx, step=step, task_id=next_task.id)
            task_outer_t0 = time.time()

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
            if max_escalations_per_task_budget > 0:
                dev_max_escalations = min(dev_max_escalations, max_escalations_per_task_budget)

            base_model = cfg.dev_model or cfg.model
            tiers: list[str] = [base_model]
            t1 = cfg.dev_model_tier1.strip()
            t2 = cfg.dev_model_tier2.strip()
            if t1 and t1 not in tiers:
                tiers.append(t1)
            if t2 and t2 not in tiers:
                tiers.append(t2)

            max_attempts = 1
            if dev_auto_escalate and dev_max_escalations > 0:
                max_attempts = min(1 + dev_max_escalations, len(tiers))

            if dev_auto_escalate and not tb and not cp:
                try:
                    tb = create_task_branch(repo, next_task.id, task_title=next_task.title)
                    metrics.event("task_branch_created", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name, reason="retry_escalation")
                except Exception:
                    metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id, reason="retry_escalation")
                    cp = create_checkpoint(repo, task_dir / "checkpoint")
                    metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, reason="retry_escalation")

            task_completed = False
            _prev_build_error: str = ""  # Carried across attempts for build-error-aware retry

            def _isolate_or_stop(reason: str) -> tuple[bool, str]:
                """Isolate failed task work: abandon branch (non-destructive) or restore checkpoint (fallback)."""
                if tb:
                    try:
                        abandon_task_branch(repo, tb)
                        metrics.event("task_branch_abandoned", cycle=cycle_idx, step=step, task_id=next_task.id,
                                      reason=reason, branch=tb.branch_name)
                        return True, ""
                    except Exception as ex:
                        detail = str(ex)
                        eprint(f"[WARN] abandon_task_branch failed: {detail}")
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "abandon_failed", "detail": detail})
                        save_state(state_path, state)
                        _record_history(next_task.id, next_task.title, "failed", reason="abandon_failed", detail=detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                        metrics.event("task_branch_abandon_failed", cycle=cycle_idx, step=step, task_id=next_task.id, reason=reason, detail=detail)
                        return False, "abandon_failed"
                if not cp:
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
                    return True, ""
                except Exception as ex:
                    detail = str(ex)
                    blocked = "blocked" in detail.lower()
                    fail_reason = "rollback_blocked" if blocked else "rollback_failed"
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": fail_reason, "detail": detail})
                    save_state(state_path, state)
                    _record_history(next_task.id, next_task.title, "failed", reason=fail_reason, detail=detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                    metrics.event("rollback_failed", cycle=cycle_idx, step=step, task_id=next_task.id, reason=reason, detail=detail)
                    eprint(f"[STOP] Rollback {fail_reason}: {detail}")
                    return False, fail_reason

            for attempt in range(max_attempts):
                if stop_path.exists():
                    break

                if attempt > 0 and dev_auto_escalate:
                    if _budget_exceeded("total_escalations", budget_state["total_escalations"], int(budgets_cfg.get("max_total_escalations_per_run") or 0)):
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

                # Inject build error context from a previous failed attempt
                if _prev_build_error:
                    dev_prompt = dev_prompt + (
                        f"\n\n[BUILD FAILED] The previous attempt broke the build. "
                        f"Fix these errors:\n```\n{_prev_build_error}\n```\n"
                        "Fix the build errors first, then complete the task."
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
                        metrics=metrics, _budget_exceeded=_budget_exceeded,
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
                    eprint(f"[DEV ERROR] {ex}")
                    if bool(getattr(args, "debug", False)):
                        eprint(traceback.format_exc())

                dev_log = dev_final or ""
                if dev_exc:
                    exc_header = f"{type(dev_exc).__name__}: {str(dev_exc)}" if str(dev_exc) else type(dev_exc).__name__
                    exc_traceback = "".join(traceback.format_exception(type(dev_exc), dev_exc, dev_exc.__traceback__))
                    dev_log += f"\n[EXCEPTION]\n{exc_header}\n\nTraceback:\n{exc_traceback}\n"

                (attempt_dir / "dev_output.txt").write_text(dev_log + "\n", encoding="utf-8", errors="replace")
                (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)
                (run_dir / "dev_logs" / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.txt").write_text(dev_log + "\n", encoding="utf-8", errors="replace")

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
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "exception", "detail": str(dev_exc)})
                    save_state(state_path, state)
                    _record_history(next_task.id, next_task.title, "failed", reason="exception", detail=str(dev_exc), files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="exception")
                    logger.task_end(task_id=next_task.id, success=False, reason="exception", exception=str(dev_exc))
                    if tb or cp:
                        ok, fail_reason = _isolate_or_stop("exception")
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
                    state.setdefault("failed", []).append({
                        "task": next_task.id,
                        "reason": "needs_dependency",
                        "detail": dep_content.strip()[:500],
                    })
                    save_state(state_path, state)
                    _record_history(next_task.id, next_task.title, "failed", reason="needs_dependency", detail=dep_content.strip()[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="needs_dependency")
                    logger.task_end(task_id=next_task.id, success=False, reason="needs_dependency")
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
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "blocked_dependency"})
                        save_state(state_path, state)
                        _record_history(next_task.id, next_task.title, "failed", reason="blocked_dependency", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="blocked_dependency")
                        logger.task_end(task_id=next_task.id, success=False, reason="blocked_dependency")
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
                        task_completed = True
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
                            eprint(f"[INFO] Phantom edit retry succeeded for {next_task.id} — diff now exists.")
                            metrics.event("dev_attempt_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0, reason="phantom_retry_success")
                            logger.task_end(task_id=next_task.id, success=True, reason="completed_after_retry", attempt=attempt)
                            task_completed = True
                            break
                        # If still no diff after retry, fall through to normal no_diff handling

                    if dev_is_max_turns and dev_auto_escalate and (attempt + 1) < max_attempts:
                        eprint(f"[INFO] Max turns exceeded with no diff for {next_task.id}. Auto-retrying with escalation...")
                        metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="max_turns_no_diff")
                        continue
                    if dev_auto_escalate and (attempt + 1) < max_attempts and "no_diff" in dev_escalate_on:
                        metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="no_diff")
                        continue
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "no_diff"})
                    save_state(state_path, state)
                    _record_history(next_task.id, next_task.title, "failed", reason="no_diff", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="no_diff")
                    logger.task_end(task_id=next_task.id, success=False, reason="no_diff", was_max_turns=dev_is_max_turns)
                    if tb or cp:
                        ok, fail_reason = _isolate_or_stop("no_diff")
                        if not ok:
                            if not continuous:
                                return 1, fail_reason, 0, (len(done_set) > before_done)
                            eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                    if continuous:
                        eprint(f"[SKIP] No diff produced for {next_task.id}; skipping to next task.")
                        skipped_set.add(next_task.id)
                        break
                    else:
                        return 1, "no_diff", 0, (len(done_set) > before_done)

                if build_enabled:
                    metrics.event("build_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)
                    ok = await run_build_gate_async(
                        repo=repo, build_cmd=getattr(args, "build_cmd", []),
                        build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                        legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                        log_path=attempt_dir / "build.txt", stop_path=stop_path,
                    )
                    metrics.event("build_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0 if ok else 1)
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
                        if dev_auto_escalate and (attempt + 1) < max_attempts and "build_failed" in dev_escalate_on:
                            # Capture build errors for injection into the next attempt's prompt
                            try:
                                _berr_raw = (attempt_dir / "build.txt").read_text(encoding="utf-8", errors="replace")
                                _berr_lines = [ln for ln in _berr_raw.splitlines() if "error " in ln.lower()]
                                _prev_build_error = "\n".join(_berr_lines[:50]) or _berr_raw[-2000:]
                            except Exception:
                                _prev_build_error = ""
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="build_failed")
                            continue
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "build_failed"})
                        save_state(state_path, state)
                        _record_history(next_task.id, next_task.title, "failed", reason="build_failed", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                        eprint(f"[SKIP] Build failed after {next_task.id}. See {attempt_dir / 'build.txt'}")
                        if tb or cp:
                            ok_r, fr = _isolate_or_stop("build_failed")
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
                    ok = await run_test_gate_async(
                        repo=repo, test_cmd=getattr(args, "test_cmd", []),
                        test_timeout_sec=int(getattr(args, "test_timeout_seconds", 3600)),
                        legacy_test_target=str(getattr(args, "dotnet_test_target", "") or ""),
                        legacy_test_filter=str(getattr(args, "dotnet_test_filter", "") or ""),
                        log_path=attempt_dir / "test.txt", stop_path=stop_path,
                    )
                    metrics.event("test_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0 if ok else 1)
                    if not ok:
                        if dev_auto_escalate and (attempt + 1) < max_attempts and "test_failed" in dev_escalate_on:
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="test_failed")
                            continue
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "test_failed"})
                        save_state(state_path, state)
                        _record_history(next_task.id, next_task.title, "failed", reason="test_failed", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                        eprint(f"[SKIP] Tests failed after {next_task.id}. See {attempt_dir / 'test.txt'}")
                        if tb or cp:
                            ok_r, fr = _isolate_or_stop("test_failed")
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
                    fail_hits = [v for v in violations if _severity_at_or_above(str(v.get("severity", "")), policy_fail_severity)]
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
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "policy_violation"})
                        save_state(state_path, state)
                        _record_history(next_task.id, next_task.title, "failed", reason="policy_violation", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                        eprint(f"[SKIP] Policy scan failed after {next_task.id}. See {attempt_dir / 'policy_scan.json'}")
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="policy_violation", violations=len(fail_hits))
                        if tb or cp:
                            ok_r, fr = _isolate_or_stop("policy_violation")
                            if not ok_r:
                                if not continuous:
                                    return 1, fr, 0, (len(done_set) > before_done)
                                eprint(f"[WARN] Rollback {fr} for {next_task.id}; continuing anyway.")
                        if continuous:
                            skipped_set.add(next_task.id)
                            break
                        else:
                            return 1, "policy_violation", 0, (len(done_set) > before_done)

                metrics.event("dev_attempt_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0)
                logger.task_end(task_id=next_task.id, success=True, reason="completed", attempt=attempt)
                task_completed = True
                break

            # Merge or abandon task branch
            if task_completed and tb:
                merge_ok = merge_task_branch(repo, tb)
                if merge_ok:
                    metrics.event("task_branch_merged", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name)
                else:
                    eprint(f"[WARN] Merge failed for {tb.branch_name}; work preserved on branch")
                    metrics.event("task_branch_merge_failed", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name)
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "merge_conflict", "branch": tb.branch_name})
                    save_state(state_path, state)
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
                state.setdefault("failed", []).append({"task": next_task.id, "reason": "exhausted_attempts"})
                save_state(state_path, state)
                _record_history(next_task.id, next_task.title, "failed", reason="exhausted_attempts", files=next_task.files, cycle=cycle_idx, attempt=max_attempts, max_attempts=max_attempts)
                task_results.append({"id": next_task.id, "title": next_task.title, "status": "failed", "reason": "exhausted_attempts", "duration": time.time() - task_outer_t0, "attempt": max_attempts, "max_attempts": max_attempts})
                logger.task_end(task_id=next_task.id, success=False, reason="exhausted_attempts", attempts=max_attempts)
                eprint(f"[SKIP] Exhausted all attempts for {next_task.id}; skipping to next task.")
                skipped_set.add(next_task.id)
                continue

            done_set.add(next_task.id)
            # Clean up previous failure entries for this task (e.g. from earlier cycles)
            if state.get("failed"):
                state["failed"] = [f for f in state["failed"] if f.get("task") != next_task.id]
            state["done"] = sorted(list(done_set))
            save_state(state_path, state)
            mark_backlog_done(backlog_md_path, next_task.id)
            _record_history(next_task.id, next_task.title, "done", files=next_task.files, cycle=cycle_idx)
            task_results.append({"id": next_task.id, "title": next_task.title, "status": "done", "duration": time.time() - task_outer_t0})

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
        # Count unique failed tasks (not raw entries — one task can have multiple failure records)
        failed_count = len({f.get("task") for f in state.get("failed", []) if f.get("task")})
        done_count = len(done_set.intersection(task_ids))
        total_count = len(task_ids)
        skipped_count = len(skipped_set.intersection(task_ids))
        summary = {
            "ts": now_iso(), "cycle": cycle_idx, "run_dir": str(run_dir),
            "done": done_count, "skipped": skipped_count,
            "total_tasks": total_count, "failed_count": failed_count,
            "duration_seconds": cycle_dt, "build_enabled": build_enabled,
            "run_tests": run_tests, "policy_scan_enabled": policy_scan_enabled,
        }
        last_run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} done={done_count}/{total_count} failed={failed_count} dt={cycle_dt:.1f}s")
        metrics.event("cycle_end", cycle=cycle_idx, rc=0, done=done_count, total=total_count, failed=failed_count, duration_seconds=cycle_dt, tokens=token_tracker.summary())
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
                goals_update = update_goals_checkboxes(repo, done_titles, done_prompts)
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
                    comp_status = parse_goals_completion(_gt_eval)
                    unresolved = _count_unresolved_failures(repo, done_set)
                    write_completion_status(run_dir, comp_status, failed_unresolved=unresolved,
                                           stop_reason="cycle_end")
                    if comp_status.get("project_complete") and unresolved == 0:
                        eprint(f"[GOALS] PROJECT COMPLETE — all P0 goals met, no unresolved failures.")
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
            # All tasks attempted but some were skipped — not truly "all done"
            eprint(f"[INFO] All tasks attempted: {done_count} done, {skipped_count} skipped out of {total_count}.")
            return 0, "all_tasks_attempted", done_delta, ran_tasks

        return 0, "ok", done_delta, ran_tasks

    # ---------------------------------------------------------------------------
    # QA phase (with followup injection — same as Codex)
    # ---------------------------------------------------------------------------

    async def run_qa_if_needed(cycle_idx: int, ran_tasks: bool) -> dict[str, Any]:
        if stop_path.exists():
            return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0}
        qa_always = bool(getattr(args, "qa_always", False))
        if not (qa_always or ran_tasks):
            metrics.event("qa_skip", cycle=cycle_idx, reason="no_progress")
            return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0}
        try:
            metrics.event("qa_start", cycle=cycle_idx)
            skills_context = "(skills disabled)"
            if skills_enabled:
                skill_ids: list[str] = []
                for t in load_tasks():
                    skill_ids.extend(t.skills or [])
                deduped = list(dict.fromkeys([s for s in skill_ids if s]))
                selected_records = [skills_by_id[sid] for sid in deduped if sid in skills_by_id]
                include_excerpts = _inline_skills_for("qa", skills_cfg.get("inline_mode", ""))
                skills_context = build_skills_context(
                    selected_records,
                    max_excerpt_lines=int(skills_cfg.get("max_excerpt_lines", 0) or 0),
                    total_char_cap=int(skills_cfg.get("qa_max_total_chars", 0) or 0),
                    include_excerpts=include_excerpts,
                )
                missing = [sid for sid in deduped if sid not in skills_by_id]
                if missing:
                    skills_context += "\nMissing skills: " + ", ".join(missing)

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
            )

            qa_output_path = run_dir / f"qa_final_output_cycle_{cycle_idx:03d}.txt"
            qa_output_path.write_text((text or "") + "\n", encoding="utf-8", errors="replace")

            followups_added = 0
            followups_candidates = 0
            followups_skipped = 0
            parse_ok: Optional[bool] = None
            followups: list = []

            if bool(getattr(args, "qa_to_backlog", False)):
                qa_text = qa_output_path.read_text(encoding="utf-8", errors="replace")
                max_items = int(getattr(args, "max_qa_followups", 5)) or 5
                parsed_qa, parse_err = parse_qa_followups(qa_text)
                if parsed_qa is not None:
                    parse_ok = True
                    followups = _followups_from_structured(parsed_qa, max_items=max_items)
                else:
                    parse_ok = False
                    followups = _extract_qa_followups(qa_text, max_items=max_items)
                    metrics.event("qa_followups_parse", cycle=cycle_idx, parse_ok=False, error=str(parse_err or "parse_failed"))
                if parse_ok:
                    metrics.event("qa_followups_parse", cycle=cycle_idx, parse_ok=True)
                if followups:
                    followups_candidates = len(followups)
                    state_obj = load_state(state_path)
                    done_ids = set(state_obj.get("done", []) or [])
                    existing = load_tasks()
                    base_tasks = [{"id": t.id, "title": t.title, "prompt": t.prompt, "files": t.files, "done_when": t.done_when, "skills": t.skills, "skills_rationale": t.skills_rationale, "depends_on": t.depends_on} for t in existing]
                    merged = _merge_qa_followups(base_tasks, followups, done_ids)
                    followups_added = max(0, len(merged) - len(base_tasks))
                    followups_skipped = max(0, followups_candidates - followups_added)
                    write_backlog_files(run_dir, merged)
                (run_dir / f"qa_followups_cycle_{cycle_idx:03d}.json").write_text(
                    json.dumps({"cycle": cycle_idx, "parse_ok": parse_ok, "candidates_count": followups_candidates, "added_count": followups_added, "skipped_count": followups_skipped, "tasks": followups}, ensure_ascii=False, indent=2),
                    encoding="utf-8", errors="replace",
                )
            metrics.event("qa_end", cycle=cycle_idx, rc=0)
            return {"parse_ok": parse_ok, "candidates": followups_candidates, "added": followups_added, "skipped": followups_skipped}
        except StopRequested:
            return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0}
        except Exception as ex:
            if is_quota_exception(ex):
                eprint(f"[QA] Quota exhausted during QA stage: {ex}")
                try:
                    stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass
                metrics.event("runner_stop", stage="qa", reason="quota_exhausted")
                return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0, "quota_exhausted": True}
            eprint(f"[QA] QA stage error: {ex}")
            metrics.event("qa_end", cycle=cycle_idx, rc=1, error=str(ex))
            return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0}

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
            local_md = build_local_shutdown_report(repo, run_dir, reason=stop_reason, last_task_id=last_task_id)
            report_path.write_text(local_md, encoding="utf-8", errors="replace")
        except Exception:
            pass
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
            )
            if text and text.strip():
                report_path.write_text(text.strip() + "\n", encoding="utf-8", errors="replace")
                (run_dir / "PM_SHUTDOWN_REPORT_OUTPUT.txt").write_text(text.strip() + "\n", encoding="utf-8", errors="replace")
            metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=bool(text))
        except Exception as ex:
            metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=False, error=str(ex))

    # ---------------------------------------------------------------------------
    # Security phase
    # ---------------------------------------------------------------------------

    async def security_phase_fn(ci: int) -> StageOutcome:
        nonlocal security_scan_summary
        if not security_enabled:
            return StageOutcome.skip("security_disabled")
        if stop_path.exists():
            return StageOutcome.stop("stop_file")
        metrics.event("security_start", cycle=ci)
        scan_files, scan_stats = _collect_scan(security_scan_scope)
        scan_result = security_scan_files(scan_files, security_rules, ignore_paths=scan_ignore_paths)
        findings = scan_result.get("findings", [])
        fail_hits = [f for f in findings if _severity_at_or_above(str(f.get("severity", "")), security_fail_severity)]
        ok = len(fail_hits) == 0
        security_scan_summary = {
            "scope": scan_stats.get("scope", security_scan_scope),
            "files_scanned": scan_stats.get("files_scanned", 0),
            "bytes_scanned": scan_stats.get("bytes_scanned", 0),
            "files_skipped": scan_stats.get("files_skipped", 0),
            "findings_total": len(findings),
            "findings_fail": len(fail_hits),
        }
        out = {"cycle": ci, "ok": ok, "fail_severity": security_fail_severity, "findings": findings, "stats": scan_stats}
        (run_dir / f"security_scan_cycle_{ci:03d}.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
        metrics.event("security_end", cycle=ci, rc=0 if ok else 1, findings=len(fail_hits), **security_scan_summary)
        metrics.event("security_scan_summary", cycle=ci, **security_scan_summary)
        if not ok:
            metrics.event("security_violation", cycle=ci, findings=len(fail_hits))
            return StageOutcome.fail("security_violation", rc=1)
        return StageOutcome.ok("security_ok")

    # ---------------------------------------------------------------------------
    # Run cycle (same structure as Codex)
    # ---------------------------------------------------------------------------

    async def run_cycle(cycle_idx: int) -> tuple[int, str, int]:
        nonlocal prev_head, policy_scan_summary, security_scan_summary

        if stop_path.exists():
            return 0, STOP_REASON_STOP_FILE, 0

        policy_scan_summary = None
        security_scan_summary = None
        cycle_t0 = time.time()
        metrics.event("cycle_start", cycle=cycle_idx)

        curr_head = git_head(repo).strip()
        head_changed_files = git_changed_files(repo, prev_head, curr_head)
        wt_changed_files: list[str] = []
        if bool(getattr(args, "pm_include_working_tree", False)):
            try:
                wt_changed_files = git_worktree_changed_files(repo)
            except Exception as ex:
                eprint(f"[WARN] working-tree change detection failed: {ex}")
        changed_files = sorted(set([*head_changed_files, *wt_changed_files]))
        repo_fp = repo_fingerprint(repo)

        async def pm_phase_fn(ci: int) -> StageOutcome:
            if stop_path.exists():
                return StageOutcome.stop("stop_file")
            metrics.event("pm_stage_start", cycle=ci)
            ok = await run_pm_if_needed(ci, curr_head, changed_files, repo_fp, force_refresh_backlog=False)
            if not ok:
                if pm_stop_reason.get("reason") == STOP_REASON_QUOTA:
                    metrics.event("pm_stage_end", cycle=ci, rc=0, reason=STOP_REASON_QUOTA)
                    return StageOutcome.stop(STOP_REASON_QUOTA, rc=0)
                if stop_path.exists():
                    detected = detect_stop_reason([stop_path]) or STOP_REASON_STOP_FILE
                    metrics.event("pm_stage_end", cycle=ci, rc=0, reason=detected)
                    return StageOutcome.stop(detected, rc=0)
                metrics.event("pm_stage_end", cycle=ci, rc=1)
                return StageOutcome.fail("pm_failed", rc=1)
            metrics.event("pm_stage_end", cycle=ci, rc=0)
            return StageOutcome.ok("pm_ok")

        async def dev_phase_fn(ci: int) -> StageOutcome:
            if stop_path.exists():
                return StageOutcome.stop("stop_file")
            if not session.tasks:
                return StageOutcome.fail("no_tasks", rc=1)
            rc, reason, done_delta, ran = await run_dev_loop(ci, session.tasks, curr_head, changed_files, repo_fp, cycle_t0)
            session.done_delta = int(done_delta or 0)
            session.ran_tasks = bool(ran)
            if reason == STOP_REASON_QUOTA:
                return StageOutcome.stop(STOP_REASON_QUOTA, rc=0)
            if reason == STOP_REASON_PROJECT_COMPLETE:
                return StageOutcome.stop(STOP_REASON_PROJECT_COMPLETE, rc=0)
            if reason == STOP_REASON_ALL_TASKS_DONE:
                return StageOutcome.stop(STOP_REASON_ALL_TASKS_DONE, rc=0)
            if reason == STOP_REASON_STOP_FILE:
                return StageOutcome.stop(STOP_REASON_STOP_FILE, rc=0)
            if rc != 0:
                return StageOutcome.fail(reason, rc=rc)
            return StageOutcome.ok(reason)

        async def qa_phase_fn(ci: int) -> StageOutcome:
            if stop_path.exists():
                return StageOutcome.stop("stop_file")
            qa_summary = await run_qa_if_needed(ci, ran_tasks=session.ran_tasks)
            session.data["qa_followups_summary"] = qa_summary
            session.data["qa_followups_added"] = int(qa_summary.get("added", 0) or 0)
            if qa_summary.get("quota_exhausted"):
                return StageOutcome.stop(STOP_REASON_QUOTA)
            return StageOutcome.ok("qa_done")

        session = PipelineSession(
            args=args, repo=repo, run_dir=run_dir, stop_path=stop_path,
            ensure_backlog=ensure_backlog, load_tasks=load_tasks,
            pm_phase=pm_phase_fn, dev_phase=dev_phase_fn, qa_phase=qa_phase_fn,
            security_phase=security_phase_fn,
        )

        res = await pipeline_mgr.run_cycle(session, cycle_idx, continuous=continuous)

        cycle_entry: dict[str, Any] = {
            "cycle": cycle_idx, "stages": [],
            "budget": {
                "total_escalations": budget_state["total_escalations"],
                "total_continuations": budget_state["total_continuations"],
                "total_repairs": budget_state["total_repairs"],
            },
            "policy_scan": policy_scan_summary or {"scope": policy_scan_scope, "files_scanned": 0, "bytes_scanned": 0, "files_skipped": 0, "violations_total": 0, "violations_fail": 0},
            "security_scan": security_scan_summary or {"scope": security_scan_scope, "files_scanned": 0, "bytes_scanned": 0, "files_skipped": 0, "findings_total": 0, "findings_fail": 0},
            "qa_followups": session.data.get("qa_followups_summary") or {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0},
        }
        for st in res.stages:
            entry = dict(st)
            if str(entry.get("name", "")).lower() == "qa":
                entry["followups_added"] = int(session.data.get("qa_followups_added", 0) or 0)
            cycle_entry["stages"].append(entry)
        run_summary["cycles"].append(cycle_entry)
        try:
            (run_dir / f"run_summary_cycle_{cycle_idx:03d}.json").write_text(json.dumps(cycle_entry, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
        except Exception:
            pass
        _write_run_summary()

        if res.reason not in ("stop_file",) and not stop_path.exists():
            try:
                final_head = git_head(repo).strip()
                if final_head:
                    snapshot_json.write_text(json.dumps({"prev_head": prev_head, "head": final_head, "ts": datetime.now(timezone.utc).isoformat() + "Z"}, indent=2, sort_keys=True) + "\n", encoding="utf-8", errors="replace")
                    prev_head = final_head
            except Exception as ex:
                eprint(f"[WARN] snapshot update failed: {ex}")

        return res.rc, res.reason, res.done_delta

    # ---------------------------------------------------------------------------
    # Main loop (same as Codex — with idle tracking and shutdown report)
    # ---------------------------------------------------------------------------

    idle_accum = 0
    last_rc = 0
    last_reason = ""
    loop_mode = bool(getattr(args, "loop", False))
    loop_max_cycles = int(getattr(args, "loop_max_cycles", 0) or 0)
    loop_sleep_seconds = int(getattr(args, "loop_sleep_seconds", 60) or 60)
    loop_idle_exit_after = int(getattr(args, "loop_idle_exit_after", 0) or 0)
    if loop_mode and loop_max_cycles <= 0:
        eprint("[WARN] loop_max_cycles not set; defaulting to 1000 to prevent infinite loops.")
    cycles = 1 if not loop_mode else (loop_max_cycles if loop_max_cycles > 0 else 1000)

    try:
        for cycle_idx in range(int(cycles)):
            if stop_path.exists():
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=stop_file")
                break

            check_and_remove_stale_git_lock(repo)
            write_heartbeat(run_dir)

            rc, reason, delta = await run_cycle(cycle_idx)
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

            if reason == STOP_REASON_QUOTA:
                break
            if reason == STOP_REASON_PROJECT_COMPLETE:
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=project_complete")
                eprint(f"[STOP] Project complete — all P0 goals met.")
                break  # Always stop on project complete, even in loop mode
            if reason == STOP_REASON_ALL_TASKS_DONE:
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_done")
                if not loop_mode:
                    break
                # In loop mode, fall through — PM refresh may generate new tasks
            if reason == "all_tasks_attempted":
                # All tasks tried but some skipped — in loop mode, next cycle may get new tasks from PM
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_attempted")
                if not loop_mode:
                    break
                # In loop mode, fall through to loop sleep — PM refresh may add new tasks
            if rc != 0 and not (loop_mode and continuous):
                # In continuous loop mode, non-critical failures don't stop the run
                break

            if loop_mode:
                if delta <= 0:
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
            last_rc = handle_worktree_patch(repo, source_repo, run_dir, last_rc, exclude_globs=exclude_globs)
            try:
                remove_worktree(source_repo, worktree_dir)
            except Exception as ex:
                eprint(f"[WARN] Failed to remove worktree: {ex}")
        run_summary["final"] = {"rc": last_rc, "reason": final_reason or ""}
        _write_run_summary()
        try:
            ctx = collect_shutdown_context(repo, run_dir)
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

    return last_rc
