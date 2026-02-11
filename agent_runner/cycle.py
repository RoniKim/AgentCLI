from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import hashlib
import os
import re
import time
import traceback
from pathlib import Path
from typing import Optional, Any

from .analysis_cache import merge_dev_hints_to_global_changelog
from .docs import load_dotenv_best_effort, resolve_docs_dir, generate_docs_digest
from .gates import run_build_gate_async, run_test_gate_async
from .gitops import (
    git_head,
    git_changed_files,
    git_worktree_changed_files,
    git_porcelain,
    has_working_tree_changes,
    git_untracked_files,
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
from .inventory import build_repo_inventory, write_repo_inventory_files
from .todo import read_current_todo, format_todo_block
from .metrics import MetricsLogger
from .logger import create_logger
from .policy import load_policy_rules, policy_scan_files
from .security import load_security_rules, security_scan_files
from .scan import collect_scan_files, DEFAULT_SCAN_IGNORE_GLOBS
from .prompts import (
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
from .reporting import collect_shutdown_context, build_local_shutdown_report
from .run_dir import make_run_dir, find_latest_run_dir
from .state import (
    TaskItem,
    load_backlog_json,
    parse_backlog_md,
    load_state,
    save_state,
    mark_backlog_done,
    write_default_p0_backlog,
    write_backlog_files,
)
from .utils import (
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
from .goals import (
    read_goals,
    format_goals_block,
    parse_goals_completion,
    update_goals_checkboxes,
    write_completion_status,
    GOALS_GENERATION_INSTRUCTION,
    GOALS_EVALUATION_INSTRUCTION,
)
from .schemas import PMOutputV2
from .structured import parse_pm_output_with_errors, dump_pretty, describe_parse_failure, parse_qa_followups
from .tracing import TraceCtx, new_trace_id
from .skills import (
    build_skills_index,
    resolve_skills_roots,
    resolve_snapshot_dir,
    write_skills_snapshot,
    build_skills_context,
    summarize_skills_index_capped,
)
from .skills.match import suggest_skills
from .shared import load_json_if_exists as _load_json_if_exists, inline_skills_for as _inline_skills_for, format_skill_selection as _format_skill_selection
from .task_history import record_task as _record_task_history, format_history_block as _format_history_block, format_split_history_blocks as _format_split_history_blocks, count_unresolved_failures as _count_unresolved_failures, count_consecutive_title_failures as _count_consecutive_title_failures
from .progress import print_cycle_report, TokenTracker, extract_codex_tokens

from .pipeline import PipelineManager, make_stages
from .pipeline.session import PipelineSession
from .pipeline.stages.base import StageOutcome


async def main_async(args: argparse.Namespace) -> int:
    force_utf8_stdio()

    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        eprint(f"Repo not found: {repo}")
        return 2

    # Execution backend routing (default: codex)
    backend = str(getattr(args, "execution_backend", "codex") or "codex").strip().lower()
    if backend in ("claudecode", "claude", "claude-code", "claude_code"):
        from .backends.claudecode import main_async_claudecode
        return await main_async_claudecode(args, repo)

    # Load env (.env) BEFORE importing agents so OPENAI_API_KEY is visible even if
    # the SDK reads environment variables at import time.
    env_debug = load_dotenv_best_effort(repo, explicit_env_file=getattr(args, "env_file", ""), override=True)
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        eprint("ERROR: OPENAI_API_KEY is not set.")
        eprint("Tried loading .env from:")
        for pth in env_debug.get("tried", []):
            eprint(f" - {pth}")
        eprint("Loaded from:")
        for pth in env_debug.get("loaded", []):
            eprint(f" - {pth}")
        eprint(r"Fix: set OPENAI_API_KEY env var, or pass --env-file C:\path\to\.env")
        return 2

    try:
        from agents import Agent, Runner
        try:
            from agents import ModelSettings  # type: ignore
        except Exception:
            ModelSettings = None  # type: ignore

        # Optional helper (newer SDKs): explicitly set the API key in-process.
        try:
            from agents import set_default_openai_key  # type: ignore
        except Exception:
            set_default_openai_key = None  # type: ignore

        from agents.mcp import MCPServerStdio
        from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX

        if set_default_openai_key is not None:
            set_default_openai_key(api_key)
    except ImportError:
        eprint("Missing dependency: openai-agents. Install: pip install -U openai-agents openai")
        return 2

    # Run dir (resume or new). In --loop mode, run_dir must be reused for the whole session.
    if getattr(args, "run_dir", ""):
        run_dir = Path(args.run_dir).expanduser().resolve()
    elif bool(getattr(args, "resume_latest", False)):
        latest = find_latest_run_dir(repo)
        run_dir = latest.expanduser().resolve() if latest is not None else make_run_dir(repo)
    else:
        latest = find_latest_run_dir(repo)
        if latest is not None and (bool(getattr(args, "loop", False)) or bool(getattr(args, "continuous", False))):
            eprint(f"[WARN] No --run-dir specified. A previous run exists: {latest}. Use --resume-latest or --run-dir to resume.")
        run_dir = make_run_dir(repo)
    run_dir.mkdir(parents=True, exist_ok=True)

    _MAX_SUMMARY_CYCLES = 50

    run_summary: dict[str, Any] = {
        "run_id": run_dir.name,
        "repo": str(repo),
        "profile": str(getattr(args, "profile", "personal") or "personal"),
        "cycles": [],
    }

    def _write_run_summary() -> None:
        try:
            # Cap cycles to prevent unbounded memory growth
            if len(run_summary["cycles"]) > _MAX_SUMMARY_CYCLES:
                run_summary["cycles"] = run_summary["cycles"][-_MAX_SUMMARY_CYCLES:]
            (run_dir / "run_summary.json").write_text(
                json.dumps(run_summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
                errors="replace",
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
    # The SDK/MCP receives 'cwd' via options instead.

    # Observability
    metrics = MetricsLogger(run_dir / "metrics.jsonl")
    logger = create_logger(run_dir, debug=bool(getattr(args, "debug", False)))
    trace_ctx = TraceCtx(trace_id=new_trace_id(), parent_span_id=None)
    stop_path = run_dir / str(getattr(args, "stop_file", "STOP"))
    cycle_summary_path = run_dir / "cycle_summary.log"
    last_run_summary_path = run_dir / "last_run_summary.json"

    # Global PM cache
    pm_cache_dir = repo / ".doc" / "PM_CACHE"
    pm_cache_dir.mkdir(parents=True, exist_ok=True)
    analysis_md = pm_cache_dir / "PROJECT_ANALYSIS.md"

    # Docs
    docs_dir = resolve_docs_dir(repo, args.docs_dir)
    digest_path = (repo / Path(args.docs_digest_file)).resolve()
    try:
        digest_rel = digest_path.relative_to(repo).as_posix()
    except ValueError:
        digest_rel = digest_path.as_posix()

    if args.docs_read_mode == "digest":
        if args.generate_digest and docs_dir:
            generate_docs_digest(repo, docs_dir, digest_path)
        elif not digest_path.exists() and docs_dir:
            generate_docs_digest(repo, docs_dir, digest_path)

    skills_cfg = getattr(args, "skills", {}) if isinstance(getattr(args, "skills", {}), dict) else {}
    skills_enabled = bool(skills_cfg.get("enabled", False))
    skills_records = []
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

    autopilot = bool(args.autopilot)

    # Prompt store
    prompts_dir = (repo / args.prompts_dir).resolve() if not Path(args.prompts_dir).is_absolute() else Path(args.prompts_dir).resolve()
    store = PromptStore(prompts_dir=prompts_dir)

    # MCP server params
    if args.mcp_mode == "npx":
        mcp_params = {"command": "npx", "args": ["-y", args.codex_package, "mcp-server"]}
    else:
        mcp_params = {"command": "codex", "args": ["mcp-server"]}

    # Gates configuration
    build_enabled = (not bool(getattr(args, "no_build", False))) or bool(getattr(args, "require_build", False))
    stop_on_no_diff = (not bool(getattr(args, "allow_no_diff", False))) or bool(getattr(args, "stop_if_no_diff", False))
    run_tests = bool(getattr(args, "run_tests", False))

    policy_cfg = getattr(args, "policy", {}) if isinstance(getattr(args, "policy", {}), dict) else {}
    policy_scan_enabled = bool(policy_cfg.get("enabled", not bool(getattr(args, "no_policy_scan", False))))
    policy_fail_severity = str(policy_cfg.get("fail_severity") or "high")
    policy_rules = load_policy_rules(getattr(args, "policy_rules_file", ""), list(getattr(args, "policy_rule", []) or []))
    policy_rules.extend(list(policy_cfg.get("rules", []) or []))
    policy_ignore_paths = list(policy_cfg.get("ignore_paths", []) or [])
    policy_allow_patterns = list(policy_cfg.get("allow_patterns", []) or [])

    security_cfg = getattr(args, "security", {}) if isinstance(getattr(args, "security", {}), dict) else {}
    security_enabled = bool(security_cfg.get("enabled", False))
    security_fail_severity = str(security_cfg.get("fail_severity") or "high")
    security_rules = load_security_rules(str(security_cfg.get("rules_path") or ""))

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

    budgets_cfg = getattr(args, "budgets", {}) if isinstance(getattr(args, "budgets", {}), dict) else {}
    budget_state = {
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

    class BudgetExceeded(Exception):
        pass

    # Drift guard for PM incremental
    pm_fp_path = pm_cache_dir / "PM_LAST_FINGERPRINT.json"
    pm_fp_obj = _load_json_if_exists(pm_fp_path, default={"fingerprint": "", "updated_at": ""})
    last_pm_fp = str(pm_fp_obj.get("fingerprint") or "")

    # Used to propagate "graceful stop" reasons out of nested helpers.
    pm_stop_reason: dict[str, str] = {}

    # Snapshot (HEAD tracking)
    snapshot_json = pm_cache_dir / "REPO_SNAPSHOT.json"
    snapshot = _load_json_if_exists(snapshot_json, default={"head": "", "updated_at": ""})
    prev_head = (snapshot.get("head") or "").strip()

    # Dev hints dir (run-local)
    dev_hints_dir = run_dir / "analysis_hints"
    dev_hints_dir.mkdir(parents=True, exist_ok=True)

    # Ensure continuous in loop mode
    continuous = bool(args.continuous or args.loop)

    # Roles/stages selection (forward-compatible with pluggable stages).
    # Current implementation supports coarse on/off for PM/Dev/QA.
    roles_raw = str(getattr(args, "roles", "PM,Dev,QA") or "PM,Dev,QA")

    plugins_allowlist = getattr(args, "plugins_allowlist", []) or []
    if isinstance(plugins_allowlist, str):
        plugins_allowlist = [p.strip() for p in plugins_allowlist.split(",") if p.strip()]

    # Stage pipeline (ordered, pluggable).
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
        msg = f"# Plugin load failure\n\n{ex}\n"
        safe_write_text(run_dir / "PLUGIN_LOAD_FAILURE.md", msg)
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

    async with MCPServerStdio(
        name="Codex_CLI",
        params=mcp_params,
        client_session_timeout_seconds=args.mcp_timeout_seconds,
    ) as codex_mcp_server:

        pm_instructions = ensure_pm_instructions_have_output_schema(store.get("pm_instructions", PM_INSTRUCTIONS_DEFAULT))
        dev_instructions = store.get("dev_instructions", DEV_INSTRUCTIONS_DEFAULT)
        qa_instructions = store.get("qa_instructions", QA_INSTRUCTIONS_DEFAULT)

        # Enable parallel tool calls when supported by the SDK/model.
        _ms = None
        if ModelSettings is not None:
            try:
                _ms = ModelSettings(parallel_tool_calls=True)
            except Exception:
                _ms = None

        def _agent_kwargs() -> dict:
            return {"model_settings": _ms} if _ms is not None else {}


        pm = Agent(
            name="Project_Manager",
            model=args.pm_model,
            mcp_servers=[codex_mcp_server],
            instructions=f"{RECOMMENDED_PROMPT_PREFIX}\n{pm_instructions}".strip(),
            **_agent_kwargs(),
        )

        def make_dev_agent(model_name: str):
            return Agent(
                name="MAUI_Developer",
                model=model_name,
                mcp_servers=[codex_mcp_server],
                instructions=f"{RECOMMENDED_PROMPT_PREFIX}\n{dev_instructions}".strip(),
                **_agent_kwargs(),
            )

        dev = make_dev_agent(args.dev_model)

        qa = Agent(
            name="QA",
            model=args.qa_model,
            mcp_servers=[codex_mcp_server],
            instructions=f"{RECOMMENDED_PROMPT_PREFIX}\n{qa_instructions}".strip(),
            **_agent_kwargs(),
        )

        reporter_instructions = store.get("reporter_instructions", REPORTER_INSTRUCTIONS_DEFAULT)
        reporter = Agent(
            name="PM_Reporter",
            model=getattr(args, "reporter_model", None) or args.pm_model,
            instructions=f"{RECOMMENDED_PROMPT_PREFIX}\n{reporter_instructions}".strip(),
            **_agent_kwargs(),
        )



        def _iter_exc_chain(ex: Exception, max_depth: int = 6):
            """Yield exception + its causes/contexts (best-effort)."""
            cur = ex
            seen = set()
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
                    "max turns" in msg
                    or "max_turn" in msg
                    or "maxturn" in msg
                    or "maxturn" in name
                    or "max_turn" in name
                    or ("turn" in name and "max" in name)
                    or "maxturnsexceeded" in rep
                ):
                    return True
            return False


        def is_quota_exception(ex: Exception) -> bool:
            """Detect OpenAI/SDK quota/billing exhaustion to exit gracefully.

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

        def is_quota_text(text: str) -> bool:
            return has_quota_text(text)

        def is_model_invalid_exception(ex: Exception) -> bool:
            """Detect invalid/unknown model errors and allow escalation fallback."""
            needles = (
                "model_not_found",
                "model not found",
                "does not exist",
                "unknown model",
                "invalid model",
                "is not available",
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


        async def write_shutdown_report(stop_reason: str, *, cycle: int, step: int, last_task_id: Optional[str] = None) -> None:
            """Best-effort shutdown report.

            1) Always write SHUTDOWN_CONTEXT.json + a local fallback SHUTDOWN_REPORT.md.
            2) If PM model call succeeds, overwrite SHUTDOWN_REPORT.md with PM-authored markdown.

            This MUST NOT rely on MCP tools; it should still work when Codex credits are exhausted.
            """
            report_path = run_dir / "SHUTDOWN_REPORT.md"
            ctx_path = run_dir / "SHUTDOWN_CONTEXT.json"

            # Build context JSON (best-effort)
            ctx_obj: dict[str, Any]
            try:
                ctx_obj = collect_shutdown_context(repo, run_dir)
                ctx_obj["stop_reason"] = stop_reason
                if last_task_id:
                    ctx_obj["last_task_id"] = last_task_id
                ctx_path.write_text(
                    json.dumps(ctx_obj, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception:
                ctx_obj = {"stop_reason": stop_reason}
                try:
                    ctx_path.write_text(
                        json.dumps(ctx_obj, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                        errors="replace",
                    )
                except Exception:
                    pass

            # Always write a local fallback first.
            try:
                local_md = build_local_shutdown_report(repo, run_dir, reason=stop_reason, last_task_id=last_task_id)
                report_path.write_text(local_md, encoding="utf-8", errors="replace")
            except Exception:
                pass

            # Try to have PM author a concise report, overwriting the fallback.
            try:
                prompt = store.render(
                    "pm_shutdown_report_prompt",
                    PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT,
                    {
                        "stop_reason": stop_reason,
                        "context_json": json.dumps(ctx_obj, ensure_ascii=False, indent=2),
                    },
                )
                res = await Runner.run(reporter, prompt, max_turns=int(getattr(args, "report_max_turns", 8)) or 8)
                out = (res.final_output or "").strip()
                if out:
                    report_path.write_text(out + "\n", encoding="utf-8", errors="replace")
                    (run_dir / "PM_SHUTDOWN_REPORT_OUTPUT.txt").write_text(out + "\n", encoding="utf-8", errors="replace")
                metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=bool(out))
            except Exception as ex:
                metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=False, error=str(ex))


        async def _run_with_continuations(
            agent_obj,
            prompt: str,
            max_turns: int,
            *,
            label: str,
            timeout_sec: int = 0,
            max_continuations: int = 0,
            task_id: str = "",
        ) -> Any:
            """Run an agent, optionally continuing if a max-turns exception occurs.

            Notes:
            - max_continuations controls how many *additional* Runner.run calls we will attempt after a MaxTurnsExceeded-style failure.
            - We detect turn-caps both by exception message and by exception class name (for SDK variations).
            """
            cont_left = int(max_continuations or 0)
            per_task = budget_state["per_task_continuations"]
            task_key = task_id or label
            per_task.setdefault(task_key, 0)

            continuation_msg = (
                f"\n\n[CONTINUE] You hit a turn limit previously while running '{label}'. Continue EXACTLY from where you left off.\n"
                "- Do NOT restate a plan.\n"
                "- Do NOT summarize.\n"
                "- Apply changes now (call tools / edit files).\n"
                "- End with only the required output."
            )

            _MAX_RETRIES = 3
            _INITIAL_BACKOFF = 5.0

            async def _run_once_with_retry(p: str) -> Any:
                for attempt in range(_MAX_RETRIES + 1):
                    try:
                        if timeout_sec and timeout_sec > 0:
                            return await asyncio.wait_for(Runner.run(agent_obj, p, max_turns=max_turns), timeout=timeout_sec)
                        return await Runner.run(agent_obj, p, max_turns=max_turns)
                    except Exception as ex:
                        if is_quota_exception(ex) or isinstance(ex, BudgetExceeded):
                            raise
                        if is_transient_exception(ex) and attempt < _MAX_RETRIES:
                            wait = _INITIAL_BACKOFF * (2 ** attempt)
                            eprint(f"[RETRY] {label} transient error (attempt {attempt + 1}/{_MAX_RETRIES}): {ex}; retrying in {wait:.0f}s")
                            await asyncio.sleep(wait)
                            continue
                        raise
                raise RuntimeError("unreachable")  # pragma: no cover

            while True:
                try:
                    return await _run_once_with_retry(prompt)
                except Exception as ex:
                    if cont_left > 0 and is_max_turns_exception(ex):
                        if _budget_exceeded("total_continuations", budget_state["total_continuations"], int(budgets_cfg.get("max_total_continuations_per_run") or 0)):
                            metrics.event("budget_exceeded", cycle=-1, reason="total_continuations")
                            raise BudgetExceeded("total_continuations")
                        if _budget_exceeded(
                            "dev_continuations_per_task",
                            per_task[task_key],
                            int(budgets_cfg.get("max_dev_continuations_per_task") or 0),
                        ):
                            metrics.event("budget_exceeded", cycle=-1, reason="dev_continuations_per_task", task_id=task_id)
                            raise BudgetExceeded("dev_continuations_per_task")
                        budget_state["total_continuations"] += 1
                        per_task[task_key] += 1
                        metrics.event("continuation_attempt", stage=label, task_id=task_id, count=budget_state["total_continuations"])
                        cont_left -= 1
                        # Replace continuation message instead of appending to avoid prompt bloat
                        if "[CONTINUE]" in prompt:
                            prompt = prompt.split("[CONTINUE]")[0] + continuation_msg
                        else:
                            prompt = prompt + continuation_msg
                        continue
                    raise
        async def _run_pm_structured(pm_prompt: str, *, max_turns: int, cycle_idx: int, kind: str, output_path: Path) -> PMOutputV2 | None:
            """Run PM and validate its final output against PMOutputV2 schema."""
            retries = int(getattr(args, "pm_structured_retries", 2))
            max_budget_retries = int(budgets_cfg.get("max_pm_structured_retries") or retries)
            retries = min(retries, max_budget_retries) if max_budget_retries > 0 else retries
            max_cont = int(getattr(args, "pm_max_turns_continuations", 0))
            last_raw = ""
            repair_prompt = ""
            for attempt in range(retries + 1):
                prompt = pm_prompt if attempt == 0 else repair_prompt
                try:
                    res = await _run_with_continuations(
                        pm,
                        prompt,
                        max_turns=max_turns,
                        label=f"pm_{kind}",
                        timeout_sec=int(getattr(args, "pm_timeout_seconds", 0)) or 0,
                        max_continuations=max_cont,
                        task_id="",
                    )
                except BudgetExceeded as ex:
                    metrics.event("budget_exceeded", cycle=cycle_idx, reason=str(ex))
                    return None
                last_raw = (getattr(res, "final_output", "") or "").strip()
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
                    logger.stage_event("pm", "quota_detected", cycle=cycle_idx)
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
                    "Previous response (for repair):\n"
                    + last_raw[:8000]
                )

            if last_raw:
                describe_parse_failure(f"pm_{kind}", last_raw)

            # Fallback: if PM wrote file-based artifacts (BACKLOG.json), continue with those.
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
                            kind=kind,  # keep current mode hint
                            summary="PM output JSON did not validate; loaded tasks from run_dir/BACKLOG.json.",
                            tasks=[
                                {
                                    "id": t.id,
                                    "title": t.title,
                                    "prompt": t.prompt,
                                    "files": t.files,
                                    "done_when": t.done_when or "Git diff exists and build passes.",
                                }
                                for t in fb_tasks
                            ],
                            notes_md=notes_md,
                            warnings=[],
                            open_questions=[],
                            analysis_updated=False,
                            analysis_path=str(analysis_md),
                        )
            except Exception:
                pass

            return None

        
        def _load_backlog_context_for_pm() -> tuple[str, list[TaskItem], set[str]]:
            """Load backlog + state to provide PM with stable context for incremental planning."""
            backlog_json = run_dir / "BACKLOG.json"
            backlog_md = run_dir / "BACKLOG.md"

            tasks: list[TaskItem] = []
            if backlog_json.exists():
                try:
                    tasks = load_backlog_json(backlog_json)
                except Exception:
                    tasks = []
            if not tasks and backlog_md.exists():
                try:
                    tasks = parse_backlog_md(backlog_md)
                except Exception:
                    tasks = []

            state_path = run_dir / "STATE.json"
            try:
                state_obj = load_state(state_path)
            except Exception:
                state_obj = {"done": [], "failed": []}

            done_ids = set(state_obj.get("done", []) or [])
            failed_list = state_obj.get("failed", []) or []
            failed_ids = {(f["task"] if isinstance(f, dict) else f) for f in failed_list}
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
            state_path_local = run_dir / "STATE.json"
            try:
                state_obj = load_state(state_path_local)
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
                                 run_id=run_dir.name, backend="codex")

        def _normalize_backlog_tasks(raw_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            """Normalize/defend backlog tasks produced by PM.

            Goals:
            - Prevent PM from delegating PM-only work to Dev (e.g., "create backlog" tasks)
            - Keep task IDs stable and unique (T1/T2 allowed)
            - Keep token usage predictable by keeping tasks atomic and concrete
            """

            def _looks_like_pm_work(t: dict[str, Any]) -> bool:
                txt = f"{t.get('title','')}\n{t.get('prompt','')}".lower()
                # Disallow PM/meta tasks: the backlog should contain only development work
                # (features, UI/screens, bugfixes, tests, and required in-repo docs).
                forbidden = (
                    # backlog / planning / analysis
                    "create backlog",
                    "generate backlog",
                    "backlog.json",
                    "backlog.md",
                    "backlog",
                    "triage",
                    "prioritize",
                    "roadmap",
                    "plan",
                    "planning",
                    "analysis",
                    "review",
                    "audit",
                    # inventory / prompt / reports
                    "repo_inventory",
                    "repo inventory",
                    "inventory",
                    "prompt engineering",
                    "update prompts",
                    "pm instructions",
                    "status report",
                    "progress report",
                    "shutdown report",
                    "postmortem",
                    # runner artifacts / cache
                    "project_analysis.md",
                    "project analysis",
                    "pm_cache",
                    "pm cache",
                    "agent_runs",
                    "run_dir",
                    "state.json",
                    "notes_pm.md",
                    "requirements.md",
                    "agent_tasks.md",
                    "notes.md",
                    # Korean common terms
                    "백로그",
                    "분석",
                    "검토",
                    "리포트",
                    "보고서",
                    "인벤토리",
                    "프롬프트",
                    "계획",
                    "정리",
                )
                if any(k in txt for k in forbidden):
                    # Allow cases where the task clearly includes implementation + fix.
                    positive = ("implement", "fix", "build", "test", "ui", "screen", "page", "component", "refactor")
                    if any(p in txt for p in positive):
                        return False
                    return True
                files = t.get("files") or []
                if isinstance(files, list) and files:
                    fl = [str(x).replace("\\", "/").lower().strip() for x in files if str(x).strip()]
                    # If the task only touches internal runner artifacts (.doc), treat as PM/meta.
                    if all((p.startswith(".doc/") or "/.doc/" in p) for p in fl):
                        return True
                    if any("agent_runs" in p or "pm_cache" in p or "project_analysis" in p or "repo_inventory" in p for p in fl):
                        return True
                return False

            # Filter + keep order
            filtered: list[dict[str, Any]] = []
            removed: list[dict[str, Any]] = []
            for t in raw_tasks:
                if not isinstance(t, dict):
                    continue
                if _looks_like_pm_work(t):
                    removed.append(t)
                    continue
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

            # Enforce unique IDs; keep existing IDs if valid.
            used: set[str] = set()
            next_num = 1
            out: list[dict[str, Any]] = []
            for t in filtered:
                tid = str(t.get("id") or "").strip()
                m = re.match(r"^T(\d+)$", tid)
                if m:
                    try:
                        n = int(m.group(1))
                    except Exception:
                        n = 0
                else:
                    n = 0

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
                # Normalize keys
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
                out.append(
                    {
                        "id": fixed_id,
                        "title": str(t.get("title") or fixed_id).strip() or fixed_id,
                        "prompt": str(t.get("prompt") or "").strip() or f"Implement {fixed_id}.",
                        "files": t.get("files") if isinstance(t.get("files"), list) else [],
                        "done_when": str(t.get("done_when") or "Git diff exists and build passes.").strip(),
                        "skills": skills,
                        "skills_rationale": (
                            None if t.get("skills_rationale") is None else str(t.get("skills_rationale"))
                        ),
                        "depends_on": depends_on,
                    }
                )
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
                        suggestion_msg = ", ".join(
                            [f"{s.skill_id}({s.name}, {s.score:.2f})" for s in suggestions]
                        )
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

        async def run_pm_if_needed(cycle_idx: int, curr_head: str, changed_files: list[str], repo_fp: str, force_refresh_backlog: bool = False) -> bool:
            """Returns True if PM is OK (ran successfully or skipped safely)."""
            nonlocal last_pm_fp, prev_head

            need_bootstrap = not analysis_md.exists()
            need_incremental = False
            force_refresh = bool(force_refresh_backlog)

            if not need_bootstrap:
                if changed_files:
                    need_incremental = True
                elif args.pm_include_working_tree and repo_fp and repo_fp != last_pm_fp:
                    need_incremental = True

                if args.pm_refresh_backlog:
                    if args.pm_refresh_every_cycles and args.pm_refresh_every_cycles > 0:
                        if (cycle_idx % args.pm_refresh_every_cycles) == 0:
                            force_refresh = True

            if stop_path.exists():
                return True

            pm_output_path = run_dir / f"pm_final_output_cycle_{cycle_idx:03d}.txt"

            # Keep repo inventory up-to-date (local, no tokens)
            try:
                inventory = build_repo_inventory(repo)
                _, inv_md = write_repo_inventory_files(repo, pm_cache_dir, inventory)
            except Exception as inv_ex:
                metrics.event("inventory_error", cycle=cycle_idx, error=str(inv_ex))
                # Last-resort fallback: create a minimal placeholder file so PM can proceed.
                inv_md = (pm_cache_dir / "REPO_INVENTORY.md")
                try:
                    pm_cache_dir.mkdir(parents=True, exist_ok=True)
                    inv_md.write_text("# REPO_INVENTORY\n\n- (inventory generation failed)\n", encoding="utf-8", errors="replace")
                except Exception:
                    inv_md = (run_dir / "REPO_INVENTORY.md")
                    try:
                        inv_md.write_text("# REPO_INVENTORY\n\n- (inventory generation failed)\n", encoding="utf-8", errors="replace")
                    except Exception:
                        pass

            # Optional TODO context (user-authored; drives backlog priority)
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
                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "todo_block": todo_block,
                        "goals_block": goals_block,
                        "goals_instruction": goals_instruction,
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "skills_index_summary": skills_index_summary,
                        "codex_call_hint": codex_call_hint(autopilot),
                        "task_history_block": _format_history_block(repo, max_items=_hist_max) if _hist_enabled else "(disabled)",
                        "done_tasks_block": _done_blk,
                        "failed_tasks_block": _failed_blk,
                        "turn_budget_warning": PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {args.pm_bootstrap_max_turns} turns)"),
                    }
                    pm_prompt = append_pm_output_contract(store.render("pm_bootstrap_prompt", PM_BOOTSTRAP_TEMPLATE_DEFAULT, ctx))
                    pm_out = await _run_pm_structured(
                        pm_prompt,
                        max_turns=args.pm_bootstrap_max_turns,
                        cycle_idx=cycle_idx,
                        kind="bootstrap",
                        output_path=pm_output_path,
                    )
                    if pm_out is None:
                        metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=1, error="structured_output_failed")
                        return False

                    # Persist parsed JSON for debugging
                    (run_dir / f"PM_OUTPUT_cycle_{cycle_idx:03d}.json").write_text(
                        dump_pretty(pm_out.model_dump()) + "\n", encoding="utf-8", errors="replace"
                    )
                    if pm_out.notes_md:
                        (run_dir / "NOTES_PM.md").write_text(
                            pm_out.notes_md.strip() + "\n", encoding="utf-8", errors="replace"
                        )
                    current_backlog_block, existing_tasks, done_ids = _load_backlog_context_for_pm()
                    existing_pending = [t for t in existing_tasks if t.id not in done_ids]

                    merged_tasks: list[dict[str, Any]] = [t.model_dump() for t in (pm_out.tasks or [])]
                    pm_ids = {str(t.get("id", "")).strip() for t in merged_tasks if isinstance(t, dict)}

                    for t in existing_pending:
                        if t.id not in pm_ids:
                            merged_tasks.append(
                                {
                                    "id": t.id,
                                    "title": t.title,
                                    "prompt": t.prompt,
                                    "files": t.files or [],
                                    "done_when": t.done_when,
                                    "skills": t.skills or [],
                                    "skills_rationale": t.skills_rationale,
                                    "depends_on": t.depends_on,
                                }
                            )

                    if merged_tasks:
                        merged_tasks = _normalize_backlog_tasks(merged_tasks)
                        merged_tasks = _validate_skill_ids(merged_tasks)
                        if merged_tasks:
                            try:
                                existing_tasks = load_tasks()
                                state_obj = load_state(run_dir / "STATE.json")
                                done_ids = set(state_obj.get("done", []) or [])
                                qa_followups = [
                                    {
                                        "id": t.id,
                                        "title": t.title,
                                        "prompt": t.prompt,
                                        "files": t.files,
                                        "done_when": t.done_when,
                                        "skills": t.skills,
                                        "skills_rationale": t.skills_rationale,
                                        "depends_on": t.depends_on,
                                    }
                                    for t in existing_tasks
                                    if t.id.startswith("QA-FU-") and t.id not in done_ids
                                ]
                                if qa_followups:
                                    merged_tasks = _merge_qa_followups(merged_tasks, qa_followups, done_ids)
                            except Exception:
                                pass
                            write_backlog_files(run_dir, merged_tasks)

                    last_pm_fp = repo_fp or last_pm_fp
                    pm_fp_path.write_text(
                        json.dumps({"fingerprint": last_pm_fp, "updated_at": now_iso()}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                        errors="replace",
                    )
                    metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=0)
                    return True

                if need_incremental or force_refresh:
                    metrics.event("pm_start", cycle=cycle_idx, kind="incremental" if need_incremental else "refresh")
                    changed_files_block = "\n".join([f"- {p}" for p in (changed_files or [])]) or "- (none)"
                    # Render dev hint block (local, no tokens)
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
                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "todo_block": todo_block,
                        "goals_block": goals_block,
                        "goals_instruction": goals_instruction,
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "skills_index_summary": skills_index_summary,
                        "codex_call_hint": codex_call_hint(autopilot),
                        "prev_head": prev_head or curr_head,
                        "curr_head": curr_head,
                        "changed_files_block": changed_files_block,
                        "current_backlog_block": current_backlog_block,
                        "hint_block": hint_block,
                        "failed_tasks_block": _failed_blk_i,
                        "task_history_block": _format_history_block(repo, max_items=_hist_max_i) if _hist_enabled_i else "(disabled)",
                        "done_tasks_block": _done_blk_i,
                        "turn_budget_warning": PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {args.pm_incremental_max_turns} turns)"),
                    }
                    pm_prompt = append_pm_output_contract(store.render("pm_incremental_prompt", PM_INCREMENTAL_TEMPLATE_DEFAULT, ctx))
                    pm_out = await _run_pm_structured(
                        pm_prompt,
                        max_turns=args.pm_incremental_max_turns,
                        cycle_idx=cycle_idx,
                        kind="incremental" if need_incremental else "refresh",
                        output_path=pm_output_path,
                    )
                    if pm_out is None:
                        metrics.event(
                            "pm_end",
                            cycle=cycle_idx,
                            kind="incremental" if need_incremental else "refresh",
                            rc=1,
                            error="structured_output_failed",
                        )
                        return False

                    (run_dir / f"PM_OUTPUT_cycle_{cycle_idx:03d}.json").write_text(
                        dump_pretty(pm_out.model_dump()) + "\n", encoding="utf-8", errors="replace"
                    )
                    if pm_out.notes_md:
                        (run_dir / "NOTES_PM.md").write_text(
                            pm_out.notes_md.strip() + "\n", encoding="utf-8", errors="replace"
                        )
                    current_backlog_block, existing_tasks, done_ids = _load_backlog_context_for_pm()
                    existing_pending = [t for t in existing_tasks if t.id not in done_ids]

                    merged_tasks: list[dict[str, Any]] = [t.model_dump() for t in (pm_out.tasks or [])]
                    pm_ids = {str(t.get("id", "")).strip() for t in merged_tasks if isinstance(t, dict)}

                    for t in existing_pending:
                        if t.id not in pm_ids:
                            merged_tasks.append(
                                {
                                    "id": t.id,
                                    "title": t.title,
                                    "prompt": t.prompt,
                                    "files": t.files or [],
                                    "done_when": t.done_when,
                                    "skills": t.skills or [],
                                    "skills_rationale": t.skills_rationale,
                                    "depends_on": t.depends_on,
                                }
                            )

                    if merged_tasks:
                        merged_tasks = _normalize_backlog_tasks(merged_tasks)
                        merged_tasks = _validate_skill_ids(merged_tasks)
                        if merged_tasks:
                            write_backlog_files(run_dir, merged_tasks)

                    last_pm_fp = repo_fp or last_pm_fp
                    pm_fp_path.write_text(
                        json.dumps({"fingerprint": last_pm_fp, "updated_at": now_iso()}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                        errors="replace",
                    )
                    metrics.event(
                        "pm_end",
                        cycle=cycle_idx,
                        kind="incremental" if need_incremental else "refresh",
                        rc=0,
                    )
                    return True

                metrics.event("pm_skip", cycle=cycle_idx)
                return True
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

        def ensure_backlog() -> bool:
            backlog_json = run_dir / "BACKLOG.json"
            backlog_md = run_dir / "BACKLOG.md"
            if backlog_json.exists() or backlog_md.exists():
                return True
            # Avoid generating misleading hardcoded tasks.
            eprint("[PM ERROR] BACKLOG not created by PM. Stopping to avoid running irrelevant tasks.")
            try:
                stop_path.write_text("BACKLOG missing\n", encoding="utf-8", errors="replace")
            except Exception:
                pass
            metrics.event("pm_backlog_missing", cycle=-1)
            return False

        def load_tasks() -> list[TaskItem]:
            backlog_json = run_dir / "BACKLOG.json"
            backlog_md = run_dir / "BACKLOG.md"
            tasks: list[TaskItem] = []
            if backlog_json.exists():
                try:
                    tasks = load_backlog_json(backlog_json)
                except Exception as ex:
                    eprint(f"Failed to parse BACKLOG.json: {ex}")
            if not tasks and backlog_md.exists():
                tasks = parse_backlog_md(backlog_md)
            return tasks

        def _collect_scan(scope: str, *, ignore_paths: Optional[list[str]] = None) -> tuple[list[tuple[str, str]], dict[str, Any]]:
            return collect_scan_files(
                repo,
                scope,
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
                tasks.append(
                    {
                        "id": tid,
                        "title": title,
                        "prompt": prompt,
                        "files": [],
                        "done_when": "QA follow-up addressed and relevant tests/builds pass.",
                        "skills": [],
                        "skills_rationale": None,
                        "depends_on": [],
                    }
                )
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
                tasks.append(
                    {
                        "id": tid,
                        "title": title,
                        "prompt": prompt,
                        "files": files,
                        "done_when": "QA follow-up addressed and relevant tests/builds pass.",
                        "skills": [],
                        "skills_rationale": None,
                        "depends_on": [],
                    }
                )
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

        async def run_qa_if_needed(cycle_idx: int, ran_tasks: bool) -> dict[str, Any]:
            if stop_path.exists():
                return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0}
            if not (args.qa_always or ran_tasks):
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

                ctx = {"repo": str(repo), "run_dir": str(run_dir), "skills_context": skills_context}
                qa_prompt = store.render("qa_prompt", QA_TEMPLATE_DEFAULT, ctx)
                if bool(getattr(args, "qa_to_backlog", False)):
                    qa_prompt = qa_prompt.rstrip() + "\n\n" + QA_FOLLOWUPS_OUTPUT_CONTRACT + "\n"
                qa_result = await Runner.run(qa, qa_prompt, max_turns=10)
                qa_output_path = run_dir / f"qa_final_output_cycle_{cycle_idx:03d}.txt"
                qa_output_path.write_text(
                    (qa_result.final_output or "") + "\n", encoding="utf-8", errors="replace"
                )
                followups_added = 0
                followups_candidates = 0
                followups_skipped = 0
                parse_ok: Optional[bool] = None
                if bool(getattr(args, "qa_to_backlog", False)):
                    qa_text = qa_output_path.read_text(encoding="utf-8", errors="replace")
                    max_items = int(getattr(args, "max_qa_followups", 5)) or 5
                    parsed, parse_err = parse_qa_followups(qa_text)
                    if parsed is not None:
                        parse_ok = True
                        followups = _followups_from_structured(parsed, max_items=max_items)
                    else:
                        parse_ok = False
                        followups = _extract_qa_followups(qa_text, max_items=max_items)
                        metrics.event("qa_followups_parse", cycle=cycle_idx, parse_ok=False, error=str(parse_err or "parse_failed"))
                    if parse_ok:
                        metrics.event("qa_followups_parse", cycle=cycle_idx, parse_ok=True)
                    if followups:
                        followups_candidates = len(followups)
                        state_path = run_dir / "STATE.json"
                        state_obj = load_state(state_path)
                        done_ids = set(state_obj.get("done", []) or [])
                        existing = load_tasks()
                        base_tasks = [
                            {
                                "id": t.id,
                                "title": t.title,
                                "prompt": t.prompt,
                                "files": t.files,
                                "done_when": t.done_when,
                                "skills": t.skills,
                                "skills_rationale": t.skills_rationale,
                                "depends_on": t.depends_on,
                            }
                            for t in existing
                        ]
                        merged = _merge_qa_followups(base_tasks, followups, done_ids)
                        followups_added = max(0, len(merged) - len(base_tasks))
                        followups_skipped = max(0, followups_candidates - followups_added)
                        write_backlog_files(run_dir, merged)
                    (run_dir / f"qa_followups_cycle_{cycle_idx:03d}.json").write_text(
                        json.dumps(
                            {
                                "cycle": cycle_idx,
                                "parse_ok": parse_ok,
                                "candidates_count": followups_candidates,
                                "added_count": followups_added,
                                "skipped_count": followups_skipped,
                                "tasks": followups,
                            },
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                        errors="replace",
                    )
                metrics.event("qa_end", cycle=cycle_idx, rc=0)
                return {
                    "parse_ok": parse_ok,
                    "candidates": followups_candidates,
                    "added": followups_added,
                    "skipped": followups_skipped,
                }
            except Exception as ex:
                metrics.event("qa_end", cycle=cycle_idx, rc=1, error=str(ex))
                if is_quota_exception(ex):
                    stop_path.write_text(STOP_REASON_QUOTA, encoding="utf-8")
                    return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0, "quota_exhausted": True}
                return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0}

        policy_scan_summary: Optional[dict[str, Any]] = None
        security_scan_summary: Optional[dict[str, Any]] = None

        async def run_dev_loop(
            cycle_idx: int,
            tasks: list[TaskItem],
            curr_head: str,
            changed_files: list[str],
            repo_fp: str,
            cycle_t0: float,
        ) -> tuple[int, str, int, bool]:
            """Run the legacy Dev loop and return (rc, reason, done_delta, ran_tasks).

            NOTE: QA is handled by a separate stage.
            """
            nonlocal policy_scan_summary
            # Dev loop
            state_path = run_dir / "STATE.json"
            backlog_md = run_dir / "BACKLOG.md"
            state = load_state(state_path)
            done_set = set(state.get("done", []))
            skipped_set: set[str] = set()  # Track skipped/failed tasks separately from done
            task_results: list[dict] = []  # Per-task results for cycle-end progress report
            token_tracker = TokenTracker()  # Per-cycle token usage accumulator

            task_ids = {t.id for t in tasks}
            before_done = len(done_set.intersection(task_ids))

            if pm_stage_enabled and args.pm_refresh_backlog and (before_done >= len(task_ids)):
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

            # --- Cycle-start git health check ---
            ensure_clean_working_tree(repo)

            # --- Pre-cycle build health check ---
            if build_enabled and not stop_path.exists():
                pre_build_log = run_dir / "pre_cycle_build.txt"
                pre_build_ok = await run_build_gate_async(
                    repo=repo,
                    build_cmd=getattr(args, "build_cmd", []),
                    build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                    legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                    log_path=pre_build_log,
                    stop_path=stop_path,
                )
                if not pre_build_ok:
                    eprint("[BUILD-FIX] Build is broken before tasks start. Running auto-fix...")
                    metrics.event("build_fix_start", cycle=cycle_idx)
                    build_errors = pre_build_log.read_text(encoding="utf-8", errors="replace")
                    error_lines = [ln for ln in build_errors.splitlines() if "error " in ln.lower()]
                    error_summary = "\n".join(error_lines[:80]) or build_errors[-3000:]

                    build_fix_prompt = (
                        f"The project at {repo} does NOT build. You must fix ALL build errors before any feature work.\n\n"
                        f"Build output (errors only):\n```\n{error_summary}\n```\n\n"
                        "Instructions:\n"
                        "1. Read the failing files and fix each error\n"
                        "2. After fixing, run the build command to verify\n"
                        "3. Keep fixing until the build succeeds with 0 errors\n"
                        "4. Do NOT add new features — only fix build errors\n"
                    )

                    build_fix_max_turns = int(getattr(args, "max_turns_per_task", 12) or 12) * 2
                    build_fix_agent = make_dev_agent(str(args.dev_model))
                    try:
                        await _run_with_continuations(
                            build_fix_agent,
                            build_fix_prompt,
                            max_turns=build_fix_max_turns,
                            label="build_fix",
                            timeout_sec=int(getattr(args, "dev_timeout_seconds", 0)) or 0,
                            max_continuations=int(getattr(args, "dev_max_turns_continuations", 0)) or 0,
                            task_id="__build_fix__",
                        )
                    except Exception as bfx:
                        eprint(f"[BUILD-FIX] Auto-fix agent error: {bfx}")

                    # Verify build after fix attempt
                    post_fix_ok = await run_build_gate_async(
                        repo=repo,
                        build_cmd=getattr(args, "build_cmd", []),
                        build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                        legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                        log_path=run_dir / "pre_cycle_build_post_fix.txt",
                        stop_path=stop_path,
                    )
                    if post_fix_ok:
                        eprint("[BUILD-FIX] Build fixed successfully!")
                        metrics.event("build_fix_end", cycle=cycle_idx, rc=0)
                    else:
                        eprint("[BUILD-FIX] Build still broken after fix attempt.")
                        metrics.event("build_fix_end", cycle=cycle_idx, rc=1)

            for step in range(int(args.iterations)):
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
                            logger.skip_event(t.id, f"failed {consec} times consecutively (>= {max_consecutive_failures})")
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
                if args.isolate_task:
                    try:
                        tb = create_task_branch(repo, next_task.id, task_title=next_task.title)
                        metrics.event("task_branch_created", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name)
                    except Exception as _tb_ex:
                        eprint(f"[WARN] Task branch creation failed ({_tb_ex}); falling back to checkpoint")
                        metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id)
                        cp = create_checkpoint(repo, task_dir / "checkpoint")
                        metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0)

                before = git_porcelain(repo)

                analysis_hint_out = dev_hints_dir / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}.md"
                # Dev model tiering (cost saver): base -> tier1 -> tier2 (best-effort)
                dev_auto_escalate = bool(getattr(args, "dev_auto_escalate", False))
                dev_max_escalations = int(getattr(args, "dev_max_escalations", 0) or 0)
                dev_escalate_on = set(getattr(args, "dev_escalate_on", []) or [])
                per_task_escalations = budget_state["per_task_escalations"]
                per_task_escalations.setdefault(next_task.id, 0)
                max_escalations_per_task_budget = int(budgets_cfg.get("max_dev_escalations_per_task") or 0)
                if max_escalations_per_task_budget > 0:
                    dev_max_escalations = min(dev_max_escalations, max_escalations_per_task_budget)

                tiers: list[str] = [str(args.dev_model)]
                t1 = str(getattr(args, "dev_model_tier1", "") or "").strip()
                t2 = str(getattr(args, "dev_model_tier2", "") or "").strip()
                if t1 and t1 not in tiers:
                    tiers.append(t1)
                if t2 and t2 not in tiers:
                    tiers.append(t2)

                # Clamp attempts: base + max escalations, and never exceed tier list.
                max_attempts = 1
                if dev_auto_escalate and dev_max_escalations > 0:
                    max_attempts = min(1 + dev_max_escalations, len(tiers))

                # Ensure a rollback point when we may retry/escalate.
                # (Even if isolate_task is false, retries need a clean baseline.)
                if dev_auto_escalate and not tb and not cp:
                    try:
                        tb = create_task_branch(repo, next_task.id, task_title=next_task.title)
                        metrics.event("task_branch_created", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name, reason="retry_escalation")
                    except Exception:
                        metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id, reason="retry_escalation")
                        cp = create_checkpoint(repo, task_dir / "checkpoint")
                        metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, reason="retry_escalation")

                task_completed = False
                task_blocked = False
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
                            repo,
                            cp,
                            dangerous=bool(getattr(args, "dangerous_git_rollback", False)),
                            run_dir=run_dir,
                            stop_path=stop_path,
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
                        if _budget_exceeded(
                            "total_escalations",
                            budget_state["total_escalations"],
                            int(budgets_cfg.get("max_total_escalations_per_run") or 0),
                        ):
                            metrics.event("budget_exceeded", cycle=cycle_idx, step=step, task_id=next_task.id, reason="total_escalations")
                            return 1, "budget_exceeded", 0, (len(done_set) > before_done)
                        budget_state["total_escalations"] += 1
                        per_task_escalations[next_task.id] += 1
                        metrics.event("escalate_attempt", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)

                    # Restore baseline before retries
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
                    dev_agent = dev if attempt == 0 else make_dev_agent(model_name)

                    attempt_dir = task_dir / f"attempt_{attempt:02d}"
                    attempt_dir.mkdir(parents=True, exist_ok=True)

                    before = git_porcelain(repo)
                    before_untracked = set(git_untracked_files(repo))

                    analysis_hint_out = dev_hints_dir / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.md"

                    files_hint = "\n".join([f"- {f}" for f in (next_task.files or [])]) or "- (unspecified)"
                    skills_context = _format_skill_selection(next_task.skills or [], skills_by_id)
                    ctx = {
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "task_id": next_task.id,
                        "task_title": next_task.title,
                        "task_prompt": next_task.prompt,
                        "files_hint": files_hint,
                        "skills_context": skills_context,
                        "done_when": next_task.done_when or "(unspecified)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "analysis_hint_out": str(analysis_hint_out),
                        "codex_call_hint": codex_call_hint(autopilot),
                    }
                    dev_prompt = store.render("dev_task_prompt", DEV_TASK_TEMPLATE_DEFAULT, ctx)

                    # Inject build error context from a previous failed attempt
                    if _prev_build_error:
                        dev_prompt = dev_prompt + (
                            f"\n\n[BUILD FAILED] The previous attempt broke the build. "
                            f"Fix these errors:\n```\n{_prev_build_error}\n```\n"
                            "Fix the build errors first, then complete the task."
                        )

                    metrics.event("dev_attempt_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, model=model_name)

                    # Structured logging: task start with context
                    logger.set_context(
                        cycle=cycle_idx,
                        step=step,
                        model=model_name,
                        max_turns=args.max_turns_per_task,
                        timeout_sec=int(getattr(args, "dev_timeout_seconds", 0)) or 0
                    )
                    logger.task_start(
                        task_id=next_task.id,
                        task_title=next_task.title,
                        attempt=attempt,
                        files=next_task.files or []
                    )

                    task_start_time = time.time()
                    dev_exc: Optional[Exception] = None
                    dev_is_max_turns = False
                    dev_quota_exhausted = False
                    dev_final = ""

                    try:
                        dev_result = await _run_with_continuations(
                            dev_agent,
                            dev_prompt,
                            max_turns=args.max_turns_per_task,
                            label="dev",
                            timeout_sec=int(getattr(args, "dev_timeout_seconds", 0)) or 0,
                            max_continuations=int(getattr(args, "dev_max_turns_continuations", 0)) or 0,
                            task_id=next_task.id,
                        )
                        dev_final = (dev_result.final_output or "")
                        _inp, _out = extract_codex_tokens(dev_result)
                        token_tracker.add("Dev", _inp, _out)
                        task_duration = time.time() - task_start_time
                        logger.timing("dev_task_execution", task_duration, task_id=next_task.id, attempt=attempt)
                    except Exception as ex:
                        dev_exc = ex
                        dev_final = ""
                        dev_is_max_turns = is_max_turns_exception(ex)
                        dev_quota_exhausted = is_quota_exception(ex)
                        task_duration = time.time() - task_start_time

                        # Structured error logging with full context
                        logger.error(
                            f"Dev task execution failed: {next_task.id}",
                            exc=ex,
                            include_traceback=True,
                            task_id=next_task.id,
                            task_title=next_task.title,
                            attempt=attempt,
                            duration_sec=task_duration,
                            is_max_turns=dev_is_max_turns,
                            is_quota_exhausted=dev_quota_exhausted
                        )

                    # Always persist whatever we have (even on exceptions)
                    dev_log = (dev_final or "")
                    if dev_exc:
                        # Include exception type, message, and full traceback for debugging
                        exc_header = f"{type(dev_exc).__name__}: {str(dev_exc)}" if str(dev_exc) else type(dev_exc).__name__
                        exc_traceback = "".join(traceback.format_exception(type(dev_exc), dev_exc, dev_exc.__traceback__))
                        dev_log += f"\n[EXCEPTION]\n{exc_header}\n\nTraceback:\n{exc_traceback}\n"

                    (attempt_dir / "dev_output.txt").write_text(dev_log + "\n", encoding="utf-8", errors="replace")
                    (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)
                    (run_dir / "dev_logs" / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.txt").write_text(
                        dev_log + "\n", encoding="utf-8", errors="replace"
                    )

                    # Quota/credits exhausted: graceful stop with artifacts preserved.
                    dev_quota_exhausted = dev_quota_exhausted or is_quota_text(dev_log)
                    if isinstance(dev_exc, BudgetExceeded):
                        metrics.event("budget_exceeded", cycle=cycle_idx, step=step, task_id=next_task.id, reason=str(dev_exc))
                        return 1, "budget_exceeded", 0, (len(done_set) > before_done)
                    if dev_quota_exhausted:
                        state.setdefault("warnings", []).append(
                            {"task": next_task.id, "reason": STOP_REASON_QUOTA, "detail": str(dev_exc) if dev_exc else "usage limit"}
                        )
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
                    # Invalid/unknown model: allow escalation fallback when available.
                    if dev_exc and is_model_invalid_exception(dev_exc):
                        if (attempt + 1) < max_attempts:
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="model_invalid")
                            continue

                    # Non-max-turn exceptions are treated as fatal (rollback + stop)
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
                            eprint(f"[SKIP] Dev exception for {next_task.id}; skipping to next task.")
                            skipped_set.add(next_task.id)
                            break
                        return 1, "dev_exception", 0, (len(done_set) > before_done)
                    # Max-turns exceptions are recoverable: continue to diff/build gates.
                    if dev_exc and dev_is_max_turns:
                        state.setdefault("warnings", []).append({"task": next_task.id, "reason": "max_turns_exceeded", "detail": str(dev_exc)})
                        save_state(state_path, state)
                        metrics.event("task_warn", cycle=cycle_idx, step=step, task_id=next_task.id, reason="max_turns_exceeded")

                    after = git_porcelain(repo)
                    # Use enhanced change detection that includes new untracked files
                    changed = has_working_tree_changes(repo, before, after, before_untracked=before_untracked)

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
                        with open(dep_summary_path, "a", encoding="utf-8") as f:
                            f.write(f"\n## {next_task.id}: {next_task.title}\n\n{dep_content.strip()}\n\n---\n")
                        state.setdefault("failed", []).append({
                            "task": next_task.id,
                            "reason": "needs_dependency",
                            "detail": dep_content.strip()[:500],
                        })
                        save_state(state_path, state)
                        _record_history(next_task.id, next_task.title, "failed", reason="needs_dependency", detail=dep_content.strip()[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="needs_dependency", was_max_turns=dev_is_max_turns)
                        logger.task_end(task_id=next_task.id, success=False, reason="needs_dependency")
                        skipped_set.add(next_task.id)
                        # Clean up the signal file so it doesn't affect subsequent tasks
                        try:
                            dep_req_path.unlink()
                        except Exception:
                            pass
                        break

                    # Escalate conditions: retry same task with a higher tier model.
                    if stop_on_no_diff and (not changed):
                        # Check if dev output or NOTES.md indicates task is blocked/impossible
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
                        is_blocked = any(keyword in dev_output_lower for keyword in blocked_keywords)

                        # Also check NOTES.md file for BLOCKED: marker (dev prompt instructs to write here)
                        if not is_blocked:
                            notes_path = attempt_dir / "NOTES.md"
                            if notes_path.exists():
                                try:
                                    notes_content = notes_path.read_text(encoding="utf-8", errors="ignore").lower()
                                    is_blocked = any(keyword in notes_content for keyword in blocked_keywords)
                                except Exception:
                                    pass

                        if is_blocked:
                            eprint(f"[SKIP] Task {next_task.id} appears blocked (dependency/resource missing). Skipping...")
                            state.setdefault("failed", []).append({"task": next_task.id, "reason": "blocked_dependency"})
                            save_state(state_path, state)
                            _record_history(next_task.id, next_task.title, "failed", reason="blocked_dependency", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                            metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="blocked_dependency", was_max_turns=dev_is_max_turns)
                            logger.task_end(task_id=next_task.id, success=False, reason="blocked_dependency", was_max_turns=dev_is_max_turns)
                            # Don't rollback for blocked tasks - continue to next task
                            task_blocked = True
                            break  # Exit retry loop

                        # Special case: if max_turns was hit and no diff, auto-retry instead of immediate failure
                        # This prevents wasting 30+ minutes on incomplete work
                        if dev_is_max_turns and dev_auto_escalate and (attempt + 1) < max_attempts:
                            logger.retry_event("dev", next_task.id, attempt=attempt, reason="max_turns_no_diff")
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
                        logger.skip_event(next_task.id, "no diff produced")
                        if tb or cp:
                            ok, fail_reason = _isolate_or_stop("no_diff")
                            if not ok:
                                if not continuous:
                                    return 1, fail_reason, 0, (len(done_set) > before_done)
                                eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                        if continuous:
                            skipped_set.add(next_task.id)
                            break
                        return 1, "no_diff", 0, (len(done_set) > before_done)
                    if build_enabled:
                        metrics.event("build_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)
                        ok = await run_build_gate_async(
                            repo=repo,
                            build_cmd=getattr(args, "build_cmd", []),
                            build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                            legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                            log_path=attempt_dir / "build.txt",
                            stop_path=stop_path,
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
                            logger.gate_event("build", next_task.id, passed=False)
                            if tb or cp:
                                ok_restore, fail_reason = _isolate_or_stop("build_failed")
                                if not ok_restore:
                                    if not continuous:
                                        return 1, fail_reason, 0, (len(done_set) > before_done)
                                    eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                            if continuous:
                                skipped_set.add(next_task.id)
                                break
                            return 1, "build_failed", 0, (len(done_set) > before_done)
                    if run_tests:
                        metrics.event("test_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt)
                        ok = await run_test_gate_async(
                            repo=repo,
                            test_cmd=getattr(args, "test_cmd", []),
                            test_timeout_sec=int(getattr(args, "test_timeout_seconds", 3600)),
                            legacy_test_target=str(getattr(args, "dotnet_test_target", "") or ""),
                            legacy_test_filter=str(getattr(args, "dotnet_test_filter", "") or ""),
                            log_path=attempt_dir / "test.txt",
                            stop_path=stop_path,
                        )
                        metrics.event("test_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0 if ok else 1)
                        if not ok:
                            if dev_auto_escalate and (attempt + 1) < max_attempts and "test_failed" in dev_escalate_on:
                                metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="test_failed")
                                continue
                            state.setdefault("failed", []).append({"task": next_task.id, "reason": "test_failed"})
                            save_state(state_path, state)
                            _record_history(next_task.id, next_task.title, "failed", reason="test_failed", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                            logger.gate_event("test", next_task.id, passed=False)
                            if tb or cp:
                                ok_restore, fail_reason = _isolate_or_stop("test_failed")
                                if not ok_restore:
                                    if not continuous:
                                        return 1, fail_reason, 0, (len(done_set) > before_done)
                                    eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                            if continuous:
                                skipped_set.add(next_task.id)
                                break
                            return 1, "test_failed", 0, (len(done_set) > before_done)
                    if policy_scan_enabled:
                        policy_scan_ignore_paths = list(scan_ignore_paths)
                        if policy_ignore_paths:
                            policy_scan_ignore_paths = list(dict.fromkeys([*policy_scan_ignore_paths, *policy_ignore_paths]))
                        scan_files, scan_stats = _collect_scan(policy_scan_scope, ignore_paths=policy_scan_ignore_paths)
                        scan_result = policy_scan_files(
                            scan_files,
                            policy_rules,
                            allow_patterns=policy_allow_patterns,
                            ignore_paths=policy_scan_ignore_paths,
                        )
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
                        metrics.event(
                            "policy_scan_summary",
                            cycle=cycle_idx,
                            step=step,
                            scope=policy_scan_summary["scope"],
                            files_scanned=policy_scan_summary["files_scanned"],
                            bytes_scanned=policy_scan_summary["bytes_scanned"],
                            files_skipped=policy_scan_summary["files_skipped"],
                            violations_total=policy_scan_summary["violations_total"],
                            violations_fail=policy_scan_summary["violations_fail"],
                        )
                        (attempt_dir / "policy_scan.json").write_text(json.dumps(scan_result, ensure_ascii=False, indent=2),
                                                                 encoding="utf-8", errors="replace")
                        (run_dir / "policy_scan.json").write_text(
                            json.dumps({"cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False, indent=2),
                            encoding="utf-8", errors="replace"
                        )
                        (run_dir / f"policy_scan_cycle_{cycle_idx:03d}.json").write_text(
                            json.dumps({"cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                            errors="replace",
                        )
                        try:
                            with (run_dir / "policy_scan_history.jsonl").open("a", encoding="utf-8", errors="replace") as f:
                                f.write(json.dumps({"ts": now_iso(), "cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False) + "\n")
                        except Exception:
                            pass

                        if not scan_result.get("ok", True):
                            state.setdefault("failed", []).append({"task": next_task.id, "reason": "policy_violation"})
                            save_state(state_path, state)
                            _record_history(next_task.id, next_task.title, "failed", reason="policy_violation", files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts)
                            logger.gate_event("policy", next_task.id, passed=False)
                            metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="policy_violation",
                                          violations=len(scan_result.get("fail_violations", [])))
                            if tb or cp:
                                ok_restore, fail_reason = _isolate_or_stop("policy_violation")
                                if not ok_restore:
                                    if not continuous:
                                        return 1, fail_reason, 0, (len(done_set) > before_done)
                                    eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                            if continuous:
                                skipped_set.add(next_task.id)
                                break
                            return 1, "policy_violation", 0, (len(done_set) > before_done)
                    # Success: exit attempt loop
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
                    tb = None

                if task_blocked:
                    # Blocked tasks: skip to next task instead of stopping
                    if tb:
                        abandon_task_branch(repo, tb)
                        tb = None
                    skipped_set.add(next_task.id)
                    eprint(f"[INFO] Skipped blocked task {next_task.id}, continuing to next task...")
                    continue

                if not task_completed:
                    if tb:
                        abandon_task_branch(repo, tb)
                        metrics.event("task_branch_abandoned", cycle=cycle_idx, step=step, task_id=next_task.id, branch=tb.branch_name, reason="exhausted_attempts")
                        tb = None
                    # No success after attempts and not otherwise returned: treat as failure.
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "exhausted_attempts"})
                    save_state(state_path, state)
                    _record_history(next_task.id, next_task.title, "failed", reason="exhausted_attempts", files=next_task.files, cycle=cycle_idx, attempt=max_attempts, max_attempts=max_attempts)
                    task_results.append({"id": next_task.id, "title": next_task.title, "status": "failed", "reason": "exhausted_attempts", "duration": time.time() - task_outer_t0, "attempt": max_attempts, "max_attempts": max_attempts})
                    logger.task_end(task_id=next_task.id, success=False, reason="exhausted_attempts", attempts=max_attempts)
                    if continuous:
                        eprint(f"[SKIP] Exhausted all attempts for {next_task.id}; skipping to next task.")
                        skipped_set.add(next_task.id)
                        continue
                    return 1, "exhausted_attempts", 0, (len(done_set) > before_done)
                # Mark done only after gates
                done_set.add(next_task.id)
                # Clean up previous failure entries for this task (e.g. from earlier cycles)
                if state.get("failed"):
                    state["failed"] = [f for f in state["failed"] if f.get("task") != next_task.id]
                state["done"] = sorted(list(done_set))
                save_state(state_path, state)
                mark_backlog_done(backlog_md, next_task.id)
                _record_history(next_task.id, next_task.title, "done", files=next_task.files, cycle=cycle_idx)
                task_results.append({"id": next_task.id, "title": next_task.title, "status": "done", "duration": time.time() - task_outer_t0})

                # Use current-cycle task IDs to avoid cross-cycle accumulation (done=16/11 bug)
                _done_this_cycle = len(done_set.intersection(task_ids))
                _skipped_this_cycle = len(skipped_set.intersection(task_ids))
                (run_dir / "progress.txt").write_text(f"done={_done_this_cycle}/{len(tasks)} skipped={_skipped_this_cycle} last={next_task.id}\n",
                                                     encoding="utf-8", errors="replace")

                code, names = run_cmd(["git", "diff", "--name-only"], cwd=repo, timeout_sec=60)
                files_changed_count = len([ln for ln in names.splitlines() if ln.strip()]) if code == 0 else 0
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
                "ts": now_iso(),
                "cycle": cycle_idx,
                "run_dir": str(run_dir),
                "done": done_count,
                "skipped": skipped_count,
                "total_tasks": total_count,
                "failed_count": failed_count,
                "duration_seconds": cycle_dt,
                "build_enabled": build_enabled,
                "run_tests": run_tests,
                "policy_scan_enabled": policy_scan_enabled,
            }
            last_run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
            append_cycle_summary(f"{now_iso()} cycle={cycle_idx} done={done_count}/{total_count} failed={failed_count} dt={cycle_dt:.1f}s")
            metrics.event("cycle_end", cycle=cycle_idx, rc=0, done=done_count, total=total_count, failed=failed_count, duration_seconds=cycle_dt, tokens=token_tracker.summary())
            print_cycle_report(cycle_idx, cycle_dt, task_results, done_count, total_count, failed_count, skipped_count, token_tracker=token_tracker)

            done_delta = done_count - before_done

            # Update repo snapshot at END of cycle as well (helps resume/restart correctness when HEAD changes during work).
            try:
                latest_head = git_head(repo).strip()
                if latest_head:
                    snapshot_json.write_text(
                        json.dumps({"head": latest_head, "updated_at": now_iso()}, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                        errors="replace",
                    )
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
                logger.info(f"All tasks attempted: {done_count} done, {skipped_count} skipped out of {total_count}.")
                return 0, "all_tasks_attempted", done_delta, ran_tasks

            return 0, "ok", done_delta, ran_tasks


        async def run_cycle(cycle_idx: int) -> tuple[int, str, int]:
            nonlocal prev_head
            nonlocal policy_scan_summary, security_scan_summary

            if stop_path.exists():
                return 0, STOP_REASON_STOP_FILE, 0

            policy_scan_summary = None
            security_scan_summary = None
            cycle_t0 = time.time()
            metrics.event("cycle_start", cycle=cycle_idx)

            curr_head = git_head(repo).strip()
            head_changed_files = git_changed_files(repo, prev_head, curr_head)

            wt_changed_files: list[str] = []
            if args.pm_include_working_tree:
                try:
                    wt_changed_files = git_worktree_changed_files(repo)
                except Exception as ex:
                    eprint(f"[WARN] working-tree change detection failed: {ex}")
                    wt_changed_files = []

            changed_files = sorted(set([*head_changed_files, *wt_changed_files]))
            repo_fp = repo_fingerprint(repo)

            async def pm_phase(ci: int) -> StageOutcome:
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

            async def security_phase(ci: int) -> StageOutcome:
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
                out = {
                    "cycle": ci,
                    "ok": ok,
                    "fail_severity": security_fail_severity,
                    "findings": findings,
                    "stats": scan_stats,
                }
                (run_dir / f"security_scan_cycle_{ci:03d}.json").write_text(
                    json.dumps(out, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                    errors="replace",
                )
                metrics.event(
                    "security_end",
                    cycle=ci,
                    rc=0 if ok else 1,
                    findings=len(fail_hits),
                    scope=security_scan_summary["scope"],
                    files_scanned=security_scan_summary["files_scanned"],
                    bytes_scanned=security_scan_summary["bytes_scanned"],
                    files_skipped=security_scan_summary["files_skipped"],
                )
                metrics.event(
                    "security_scan_summary",
                    cycle=ci,
                    scope=security_scan_summary["scope"],
                    files_scanned=security_scan_summary["files_scanned"],
                    bytes_scanned=security_scan_summary["bytes_scanned"],
                    files_skipped=security_scan_summary["files_skipped"],
                    findings_total=security_scan_summary["findings_total"],
                    findings_fail=security_scan_summary["findings_fail"],
                )
                if not ok:
                    metrics.event("security_violation", cycle=ci, findings=len(fail_hits))
                    return StageOutcome.fail("security_violation", rc=1)
                return StageOutcome.ok("security_ok")

            async def dev_phase(ci: int) -> StageOutcome:
                if stop_path.exists():
                    return StageOutcome.stop("stop_file")
                # Dev loop expects tasks loaded by the pipeline manager.
                if not session.tasks:
                    return StageOutcome.fail("no_tasks", rc=1)

                rc, reason, done_delta, ran_tasks = await run_dev_loop(
                    ci,
                    session.tasks,
                    curr_head,
                    changed_files,
                    repo_fp,
                    cycle_t0,
                )
                session.done_delta = int(done_delta or 0)
                session.ran_tasks = bool(ran_tasks)

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

            async def qa_phase(ci: int) -> StageOutcome:
                if stop_path.exists():
                    return StageOutcome.stop("stop_file")
                qa_summary = await run_qa_if_needed(ci, ran_tasks=session.ran_tasks)
                session.data["qa_followups_summary"] = qa_summary
                session.data["qa_followups_added"] = int(qa_summary.get("added", 0) or 0)
                if qa_summary.get("quota_exhausted"):
                    return StageOutcome.stop(STOP_REASON_QUOTA)
                return StageOutcome.ok("qa_done")

            session = PipelineSession(
                args=args,
                repo=repo,
                run_dir=run_dir,
                stop_path=stop_path,
                ensure_backlog=ensure_backlog,
                load_tasks=load_tasks,
                pm_phase=pm_phase,
                dev_phase=dev_phase,
                qa_phase=qa_phase,
                security_phase=security_phase,
            )

            res = await pipeline_mgr.run_cycle(session, cycle_idx, continuous=continuous)

            cycle_entry = {
                "cycle": cycle_idx,
                "stages": [],
                "budget": {
                    "total_escalations": budget_state["total_escalations"],
                    "total_continuations": budget_state["total_continuations"],
                    "total_repairs": budget_state["total_repairs"],
                },
                "policy_scan": policy_scan_summary
                or {
                    "scope": policy_scan_scope,
                    "files_scanned": 0,
                    "bytes_scanned": 0,
                    "files_skipped": 0,
                    "violations_total": 0,
                    "violations_fail": 0,
                },
                "security_scan": security_scan_summary
                or {
                    "scope": security_scan_scope,
                    "files_scanned": 0,
                    "bytes_scanned": 0,
                    "files_skipped": 0,
                    "findings_total": 0,
                    "findings_fail": 0,
                },
                "qa_followups": session.data.get("qa_followups_summary")
                or {
                    "parse_ok": None,
                    "candidates": 0,
                    "added": 0,
                    "skipped": 0,
                },
            }
            for st in res.stages:
                entry = dict(st)
                if str(entry.get("name", "")).lower() == "qa":
                    entry["followups_added"] = int(session.data.get("qa_followups_added", 0) or 0)
                cycle_entry["stages"].append(entry)
            run_summary["cycles"].append(cycle_entry)
            try:
                (run_dir / f"run_summary_cycle_{cycle_idx:03d}.json").write_text(
                    json.dumps(cycle_entry, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                    errors="replace",
                )
            except Exception:
                pass
            _write_run_summary()

            # Update snapshot for the next cycle unless we're stopping immediately.
            if res.reason not in ("stop_file",) and not stop_path.exists():
                try:
                    final_head = git_head(repo).strip()
                    if final_head:
                        snapshot_json.write_text(
                            json.dumps(
                                {
                                    "prev_head": prev_head,
                                    "head": final_head,
                                    "ts": datetime.now(timezone.utc).isoformat() + "Z",
                                },
                                indent=2,
                                sort_keys=True,
                            )
                            + "\n",
                            encoding="utf-8",
                            errors="replace",
                        )
                        prev_head = final_head
                except Exception as ex:
                    eprint(f"[WARN] snapshot update failed: {ex}")

            return res.rc, res.reason, res.done_delta

        idle_accum = 0
        last_rc = 0
        last_reason = ""
        consecutive_failures = 0
        max_consecutive_failed_cycles = int(getattr(args, "max_consecutive_failed_cycles", 3) or 3)
        budget_reset_per_cycle = bool(getattr(args, "budget_reset_per_cycle", True))
        if args.loop and (not args.loop_max_cycles or args.loop_max_cycles <= 0):
            eprint("[WARN] loop_max_cycles not set; defaulting to 1000 to prevent infinite loops.")
        cycles = 1 if not args.loop else (args.loop_max_cycles if args.loop_max_cycles and args.loop_max_cycles > 0 else 1000)

        try:
            for cycle_idx in range(int(cycles)):
                pm_stop_reason.clear()

                if stop_path.exists():
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=stop_file")
                    break

                check_and_remove_stale_git_lock(repo)
                write_heartbeat(run_dir)

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

                rc, reason, delta = await run_cycle(cycle_idx)
                last_rc = rc
                last_reason = reason
                # 1-line per-cycle summary for unattended ops
                print(f"[CYCLE] {now_iso()} idx={cycle_idx} rc={rc} reason={reason} progress_delta={delta}")

                # --- Consecutive failure tracking ---
                cycle_failed = (rc != 0) or reason == "budget_exceeded"
                if cycle_failed and delta <= 0:
                    consecutive_failures += 1
                    logger.warning(f"Consecutive failed cycles: {consecutive_failures}/{max_consecutive_failed_cycles}")
                    if consecutive_failures >= max_consecutive_failed_cycles:
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=consecutive_failures count={consecutive_failures}")
                        logger.stop_event(f"{consecutive_failures} consecutive failed cycles with no progress — stopping run.")
                        break
                else:
                    consecutive_failures = 0

                if reason == STOP_REASON_QUOTA:
                    break
                if reason == STOP_REASON_PROJECT_COMPLETE:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=project_complete")
                    logger.stop_event("Project complete — all P0 goals met.")
                    break  # Always stop on project complete, even in loop mode
                if reason == STOP_REASON_ALL_TASKS_DONE:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_done")
                    if not args.loop:
                        break
                    # In loop mode, fall through — PM refresh may generate new tasks
                if reason == "all_tasks_attempted":
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_attempted")
                    if not args.loop:
                        break
                    # In loop mode, fall through — PM refresh may add new tasks or retry skipped

                if args.loop:
                    if delta <= 0:
                        idle_accum += int(args.loop_sleep_seconds)
                    else:
                        idle_accum = 0

                    if args.loop_idle_exit_after and args.loop_idle_exit_after > 0 and idle_accum >= args.loop_idle_exit_after:
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=idle_exit idle_accum={idle_accum}")
                        break

                    await asyncio.sleep(max(0, int(args.loop_sleep_seconds)))
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
                last_rc = handle_worktree_patch(
                    repo,
                    source_repo,
                    run_dir,
                    last_rc,
                    exclude_globs=exclude_globs,
                )
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
                print(
                    f"[SHUTDOWN] reason={final_reason or 'ok'} cycles={len(run_summary['cycles'])} "
                    f"tasks={tasks_done}/{tasks_total} changes={change_count} run_dir={run_dir}{policy_part}"
                )
            except Exception:
                print(f"[SHUTDOWN] reason={final_reason or last_reason or 'ok'} run_dir={run_dir}")

    return last_rc



def run(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        return 130
    except Exception as ex:
        eprint(f"[FATAL] {ex}")
        if bool(getattr(args, "debug", False)):
            eprint(traceback.format_exc())
        return 1
