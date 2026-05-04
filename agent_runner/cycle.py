from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
import traceback
from pathlib import Path
from typing import Optional, Any

from .analysis_cache import merge_dev_hints_to_global_changelog
from .docs import resolve_docs_dir, generate_docs_digest
from .experience import (
    load_pm_experience_summary,
    record_completed_task_experience,
    record_validation_experiences,
)
from .gates import (
    extract_build_warnings,
    classify_task_validation_status,
    run_build_validation_async,
    run_build_gate_async,
    run_fast_web_worktree_regression_async,
    run_test_validation_async,
    run_test_gate_async,
    should_run_fast_web_worktree_regression,
    should_retry_fast_web_worktree_regression_failure,
    summarize_fast_web_worktree_regression_failure,
)
from .pr_queue import queue_review_packet
from .gitops import (
    git_head,
    git_changed_files,
    git_worktree_changed_files,
    git_porcelain,
    git_rev_parse_ref,
    has_working_tree_changes,
    has_new_commits,
    ref_has_new_commits,
    git_untracked_files,
    repo_fingerprint,
    create_checkpoint,
    restore_checkpoint,
    update_checkpoint,
    RepoCheckpoint,
    TaskBranch,
    create_task_branch,
    format_task_commit_message,
    merge_task_branch,
    abandon_task_branch,
    reset_task_branch,
    default_worktree_dir,
    create_worktree,
    remove_worktree,
    handle_worktree_patch,
    read_pending_worktree_merge,
    check_and_remove_stale_git_lock,
    ensure_clean_working_tree,
)
from .inventory import build_repo_inventory, write_repo_inventory_files
from .todo import read_current_todo, format_todo_block
from .metrics import MetricsLogger
from .logger import create_logger
from .policy import load_policy_rules, policy_scan_files
from .security import load_security_rules, security_scan_files
from .scan import DEFAULT_SCAN_IGNORE_GLOBS
from .prompts import (
    PromptStore,
    ensure_pm_instructions_have_output_schema,
    append_pm_output_contract,
    append_pm_essential_context,
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
from .reporting import (
    collect_shutdown_context,
    build_local_shutdown_report,
    write_cycle_change_summary_artifacts,
    write_run_report_artifacts,
)
from .runtime_contract import (
    AttemptContext,
    RunnerContext,
    dispatch_task_branch_disposition,
    dispatch_worktree_cleanup,
)
from .run_dir import make_run_dir, find_latest_run_dir
from .stop_progress import write_stop_snapshot
from .state import (
    TaskItem,
    count_state_task_ids,
    load_backlog_task_ids,
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
    rotate_log_file,
    has_quota_text,
    choose_stop_reason,
    detect_stop_reason,
    write_heartbeat,
    loop_cycle_indices,
    check_codex_quota_utilization,
    seconds_until_unix_reset,
    severity_at_or_above,
    budget_exceeded,
    is_unsafe_path,
    STOP_REASON_QUOTA_UTILIZATION,
    STOP_REASON_QUOTA,
    STOP_REASON_STOP_FILE,
    STOP_REASON_ALL_TASKS_DONE,
    STOP_REASON_ALL_TASKS_ATTEMPTED,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_NO_TASKS,
    STOP_REASON_PM_REFRESH_NO_BACKLOG,
)
from .exceptions import BudgetExceeded
from .exc_detect import (
    is_quota_exception,
    is_model_invalid_exception,
)
from .experience import record_task_experience
from .qa_utils import (
    extract_qa_followups,
    followups_from_structured,
    merge_qa_followups,
    split_followups_by_type,
    write_manual_checks,
)
from .backlog_utils import (
    postprocess_pm_output_tasks,
    validate_skill_ids,
    load_backlog_context_for_pm,
    build_failed_tasks_block,
    record_history,
)
from .goals import (
    classify_goals_completion_status,
    GOALS_INCOMPLETE_STATUS,
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
from .schemas import PMOutputV2
from .structured import (
    parse_pm_output_with_errors,
    dump_pretty,
    describe_parse_failure,
    parse_qa_followups,
    is_model_error_payload,
    model_error_message,
)
from .skills import (
    build_skills_index,
    resolve_skills_roots,
    resolve_snapshot_dir,
    write_skills_snapshot,
    build_skills_context,
    summarize_skills_index_capped,
)
from .skills.match import suggest_skills
from .validation_artifacts import write_task_validation_artifacts
from .shared import (
    load_json_if_exists as _load_json_if_exists,
    inline_skills_for as _inline_skills_for,
    format_skill_selection as _format_skill_selection,
    coerce_roles_arg as _coerce_roles_arg,
)
from .task_history import format_history_block as _format_history_block, format_split_history_blocks as _format_split_history_blocks, count_unresolved_failures as _count_unresolved_failures, count_consecutive_title_failures as _count_consecutive_title_failures
from .task_status import (
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_COMPLETED,
    classify_task_failure,
    is_manual_review_required,
)
from .experience import (
    ExperienceRedactionSettings,
    render_pm_experience_summary_from_run,
)
from .failure_policy import (
    ACTION_RETRY,
    count_task_status_groups,
    decide_failure_disposition,
    should_count_cycle_failure_for_stop,
    should_preserve_for_review,
)
from .task_failures import record_task_failure_result, record_task_failure_state
from .progress import print_cycle_report, TokenTracker
from .codex_exec import codex_exec, CodexExecResult

from .pipeline import PipelineManager, make_stages
from .pipeline.shared_runtime import (
    SharedCycleDeps,
    append_cycle_summary_line,
    build_goals_prompt_context,
    build_qa_skills_context,
    compute_dev_model_tiers,
    collect_scan_with_config,
    detect_and_clear_recycled_ids,
    ensure_backlog_artifacts,
    load_backlog_tasks,
    maybe_refresh_tasks_after_pm,
    prepare_pm_inventory_markdown,
    process_qa_followups,
    run_shared_cycle_once,
    select_next_task_with_dependency_checks,
    write_pm_output_artifacts,
    write_run_summary_file,
)


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

    # Verify codex CLI is available (login-based auth - no API key needed)
    import shutil
    if shutil.which("codex") is None:
        eprint("ERROR: 'codex' CLI not found in PATH.")
        eprint("Install: npm install -g @openai/codex")
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
    # The SDK/MCP receives 'cwd' via options instead.

    # Observability
    metrics = MetricsLogger(run_dir / "metrics.jsonl")
    logger = create_logger(run_dir, debug=bool(getattr(args, "debug", False)))
    stop_path = run_dir / str(getattr(args, "stop_file", "STOP"))
    cycle_summary_path = run_dir / "cycle_summary.log"
    last_run_summary_path = run_dir / "last_run_summary.json"

    async def sleep_or_stop(seconds: float | int, *, poll_seconds: float = 1.0) -> bool:
        """Sleep in small chunks and return True if STOP appears."""
        total = max(0.0, float(seconds or 0))
        deadline = time.monotonic() + total
        poll = max(0.1, min(float(poll_seconds or 1.0), 5.0))
        while True:
            if stop_path.exists():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return stop_path.exists()
            await asyncio.sleep(min(poll, remaining))

    def record_stop_checkpoint(
        *,
        stage: str,
        cycle: int,
        step: int = -1,
        task_id: str = "",
        attempt: int | None = None,
        message: str = "",
    ) -> dict[str, Any]:
        """Persist a lightweight stop snapshot so manual stops leave usable context."""
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
                stage=stage,
                task_id=task_id,
                attempt=attempt if attempt is not None else -1,
                reason=reason,
                message=message,
            )
        except Exception:
            pass
        return payload

    # Global PM cache
    from .config import AGENT_WORK_DIR
    pm_cache_dir = repo / AGENT_WORK_DIR / "PM_CACHE"
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
    codex_reasoning_effort = str(getattr(args, "codex_reasoning_effort", "") or "").strip().lower()

    # Prompt store
    prompts_dir = (repo / args.prompts_dir).resolve() if not Path(args.prompts_dir).is_absolute() else Path(args.prompts_dir).resolve()
    store = PromptStore(prompts_dir=prompts_dir)

    # MCP server params (retained for reference; codex exec handles tools internally)

    # Gates configuration
    build_enabled = (not bool(getattr(args, "no_build", False))) or bool(getattr(args, "require_build", False))
    stop_on_no_diff = (not bool(getattr(args, "allow_no_diff", False))) or bool(getattr(args, "stop_if_no_diff", False))
    run_tests = bool(getattr(args, "run_tests", False))

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
    experience_redaction = ExperienceRedactionSettings.from_source(args)

    # Ensure continuous in loop mode
    continuous = bool(args.continuous or args.loop)

    # Roles/stages selection (forward-compatible with pluggable stages).
    # Current implementation supports coarse on/off for PM/Dev/QA.
    roles_raw = _coerce_roles_arg(getattr(args, "roles", None))

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
        append_cycle_summary_line(
            cycle_summary_path=cycle_summary_path,
            line=line,
            rotate_log_file_fn=rotate_log_file,
        )

    # --- Begin pipeline scope (was: async with MCPServerStdio) ---
    # codex exec handles MCP/tools internally; no SDK agent objects needed.
    if True:  # preserve indent level for minimal diff
        pm_instructions = ensure_pm_instructions_have_output_schema(store.get("pm_instructions", PM_INSTRUCTIONS_DEFAULT))
        dev_instructions = store.get("dev_instructions", DEV_INSTRUCTIONS_DEFAULT)
        qa_instructions = store.get("qa_instructions", QA_INSTRUCTIONS_DEFAULT)
        reporter_instructions = store.get("reporter_instructions", REPORTER_INSTRUCTIONS_DEFAULT)



        async def write_shutdown_report(stop_reason: str, *, cycle: int, step: int, last_task_id: Optional[str] = None) -> None:
            """Best-effort shutdown report.

            1) Always write SHUTDOWN_CONTEXT.json + a local fallback SHUTDOWN_REPORT.md.
            2) If PM model call succeeds, overwrite SHUTDOWN_REPORT.md with PM-authored markdown.

            This MUST NOT rely on MCP tools; it should still work when Codex credits are exhausted.
            """
            report_path = run_dir / "SHUTDOWN_REPORT.md"
            ctx_path = run_dir / "SHUTDOWN_CONTEXT.json"
            report_artifacts: dict[str, Any] = {}
            try:
                report_artifacts = write_run_report_artifacts(
                    repo=repo,
                    run_dir=run_dir,
                    stop_reason=stop_reason,
                    last_task_id=last_task_id,
                )
            except Exception as _report_artifacts_ex:
                eprint(f"[WARN] Failed to write run reports: {_report_artifacts_ex}")

            # Build context JSON (best-effort)
            ctx_obj: dict[str, Any]
            try:
                ctx_obj = collect_shutdown_context(repo, run_dir)
                ctx_obj["stop_reason"] = stop_reason
                if last_task_id:
                    ctx_obj["last_task_id"] = last_task_id
                if report_artifacts:
                    ctx_obj["qa_validation_report"] = report_artifacts.get("qa_validation_report", {})
                    ctx_obj["final_run_report"] = report_artifacts.get("final_run_report", {})
                    ctx_obj["report_artifacts"] = report_artifacts.get("artifacts", {})
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
                local_md = build_local_shutdown_report(repo=repo, run_dir=run_dir, reason=stop_reason, last_task_id=last_task_id)
                report_path.write_text(local_md, encoding="utf-8", errors="replace")
            except Exception as _report_ex:
                eprint(f"[WARN] Failed to write local shutdown report: {_report_ex}")

            if stop_reason == STOP_REASON_STOP_FILE or stop_path.exists():
                metrics.event(
                    "shutdown_report",
                    cycle=cycle,
                    step=step,
                    reason=stop_reason,
                    ok=True,
                    mode="local_only",
                    note="llm_report_skipped_stop_requested",
                )
                return

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
                res = await codex_exec(
                    prompt,
                    instructions=reporter_instructions,
                    model=getattr(args, "reporter_model", None) or args.pm_model,
                    reasoning_effort=codex_reasoning_effort,
                    cwd=repo,
                    timeout_seconds=300,
                    heartbeat_callback=lambda: metrics.event("heartbeat", stage="reporter"),
                )
                out = (res.final_output or "").strip()
                report_error = (res.error or "").strip()
                if out:
                    try:
                        out_payload = json.loads(out)
                    except Exception:
                        out_payload = None
                    if is_model_error_payload(out_payload):
                        report_error = model_error_message(out_payload) or "model error"
                        out = ""
                if out and res.exit_code == 0 and not report_error:
                    # Detect and remove duplicate report content (PM model repeating itself)
                    half = len(out) // 2
                    if half > 200 and out[:half].strip() == out[half:].strip():
                        out = out[:half].strip()
                    report_path.write_text(out + "\n", encoding="utf-8", errors="replace")
                    (run_dir / "PM_SHUTDOWN_REPORT_OUTPUT.txt").write_text(out + "\n", encoding="utf-8", errors="replace")
                metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=bool(out), error=report_error)
            except Exception as ex:
                metrics.event("shutdown_report", cycle=cycle, step=step, reason=stop_reason, ok=False, error=str(ex))


        async def _run_codex_with_continuations(
            prompt: str,
            *,
            instructions: str = "",
            model: str = "",
            full_auto: bool = False,
            label: str,
            timeout_sec: int = 0,
            max_continuations: int = 0,
            task_id: str = "",
        ) -> CodexExecResult:
            """Run ``codex exec``, optionally continuing on timeout.

            Returns a :class:`CodexExecResult`. Raises :class:`BudgetExceeded`
            if continuation or quota budgets are blown.
            """
            cont_left = int(max_continuations or 0)
            per_task = budget_state["per_task_continuations"]
            task_key = task_id or label
            per_task.setdefault(task_key, 0)

            continuation_msg = (
                f"\n\n[CONTINUE] The previous '{label}' session was terminated due to a timeout. "
                "The repository is in the exact state left by the previous run - some files may have been partially modified.\n"
                "- Inspect the current state of files (read them) before making changes.\n"
                "- Continue EXACTLY from where the previous session left off.\n"
                "- Do NOT restate a plan or summarize.\n"
                "- Apply remaining changes now (edit files / run commands).\n"
                "- End with only the required output."
            )

            _MAX_RETRIES = 3
            _INITIAL_BACKOFF = 5.0

            effective_timeout = timeout_sec if (timeout_sec and timeout_sec > 0) else 900

            while True:
                if stop_path.exists():
                    metrics.event("model_call_skipped_stop", stage=label, task_id=task_id)
                    return CodexExecResult(exit_code=130, error=STOP_REASON_STOP_FILE)

                # Retry loop for transient errors
                last_result: CodexExecResult | None = None
                for retry_attempt in range(_MAX_RETRIES + 1):
                    if stop_path.exists():
                        metrics.event("model_retry_skipped_stop", stage=label, task_id=task_id, attempt=retry_attempt)
                        return last_result or CodexExecResult(exit_code=130, error=STOP_REASON_STOP_FILE)

                    result = await codex_exec(
                        prompt,
                        instructions=instructions,
                        model=model,
                        reasoning_effort=codex_reasoning_effort,
                        full_auto=full_auto,
                        cwd=repo,
                        timeout_seconds=effective_timeout,
                        heartbeat_callback=lambda: metrics.event("heartbeat", stage=label, task_id=task_id),
                    )
                    last_result = result

                    if stop_path.exists():
                        result.error = result.error or STOP_REASON_STOP_FILE
                        metrics.event("model_call_stopped", stage=label, task_id=task_id, attempt=retry_attempt)
                        return result

                    if result.is_quota_exhausted:
                        raise Exception("quota exhausted - detected in codex exec output")
                    if result.exit_code == 0 or result.is_timeout:
                        break  # success or timeout - exit retry loop
                    # Transient error detection
                    err_text = result.error or ""
                    if retry_attempt < _MAX_RETRIES and (
                        "ECONNRESET" in err_text
                        or "ETIMEDOUT" in err_text
                        or "rate limit" in err_text.lower()
                        or "503" in err_text
                        or "502" in err_text
                    ):
                        wait = _INITIAL_BACKOFF * (2 ** retry_attempt)
                        eprint(f"[RETRY] {label} transient error (attempt {retry_attempt + 1}/{_MAX_RETRIES}): {err_text[:200]}; retrying in {wait:.0f}s")
                        if await sleep_or_stop(wait):
                            result.error = result.error or STOP_REASON_STOP_FILE
                            metrics.event("model_retry_stopped", stage=label, task_id=task_id, attempt=retry_attempt)
                            return result
                        continue
                    break  # non-transient error - exit retry loop

                assert last_result is not None
                result = last_result

                if stop_path.exists():
                    result.error = result.error or STOP_REASON_STOP_FILE
                    metrics.event("model_call_stopped", stage=label, task_id=task_id)
                    return result

                # Continuation on timeout
                if result.is_timeout and cont_left > 0:
                    if stop_path.exists():
                        result.error = result.error or STOP_REASON_STOP_FILE
                        metrics.event("continuation_skipped_stop", stage=label, task_id=task_id)
                        return result
                    if budget_exceeded("total_continuations", budget_state["total_continuations"], int(budgets_cfg.get("max_total_continuations_per_run") or 0)):
                        metrics.event("budget_exceeded", cycle=-1, reason="total_continuations")
                        raise BudgetExceeded("total_continuations")
                    if budget_exceeded(
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

                return result
        async def _run_pm_structured(pm_prompt: str, *, max_turns: int, cycle_idx: int, kind: str, output_path: Path) -> PMOutputV2 | None:
            """Run PM and validate its final output against PMOutputV2 schema."""
            retries = int(getattr(args, "pm_structured_retries", 2))
            max_budget_retries = int(budgets_cfg.get("max_pm_structured_retries") or retries)
            retries = min(retries, max_budget_retries) if max_budget_retries > 0 else retries
            max_cont = int(getattr(args, "pm_max_turns_continuations", 0))
            last_raw = ""
            repair_prompt = ""
            for attempt in range(retries + 1):
                if stop_path.exists():
                    record_stop_checkpoint(
                        stage=f"pm_{kind}",
                        cycle=cycle_idx,
                        message="Stop requested before PM attempt; skipping further PM work.",
                    )
                    return None

                prompt = pm_prompt if attempt == 0 else repair_prompt
                try:
                    res = await _run_codex_with_continuations(
                        prompt,
                        instructions=pm_instructions,
                        model=args.pm_model,
                        full_auto=False,
                        label=f"pm_{kind}",
                        timeout_sec=int(getattr(args, "pm_timeout_seconds", 0)) or 0,
                        max_continuations=max_cont,
                        task_id="",
                    )
                except BudgetExceeded as ex:
                    metrics.event("budget_exceeded", cycle=cycle_idx, reason=str(ex))
                    return None
                last_raw = (res.final_output or "").strip()
                try:
                    output_path.write_text(last_raw + "\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass

                if stop_path.exists():
                    metrics.event("pm_stop_requested", cycle=cycle_idx, attempt=attempt, kind=kind)
                    record_stop_checkpoint(
                        stage=f"pm_{kind}",
                        cycle=cycle_idx,
                        message="Stop requested during PM; raw PM output was preserved.",
                    )
                    return None

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
                model_errors = [m for m in missing if str(m).startswith("<model_error")]
                if model_errors:
                    detail = model_errors[0].strip("<>")
                    logger.stage_event("pm", "model_error", cycle=cycle_idx)
                    metrics.event("pm_model_error", cycle=cycle_idx, detail=detail)
                    raise Exception(f"PM model error - {detail}")
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
                    "Previous response (for repair):\n"
                    + last_raw[:8000]
                )

            if stop_path.exists():
                record_stop_checkpoint(
                    stage=f"pm_{kind}",
                    cycle=cycle_idx,
                    message="Stop requested after PM attempts; skipping fallback backlog loading.",
                )
                return None

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

        
        def _validate_skill_ids(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
            return validate_skill_ids(
                tasks,
                skills_enabled=skills_enabled,
                skills_by_id=skills_by_id,
                skills_records=skills_records,
                skills_cfg=skills_cfg,
            )

        def _load_backlog_context_for_pm() -> tuple[str, list[TaskItem], set[str], set[str]]:
            return load_backlog_context_for_pm(
                run_dir / "BACKLOG.json",
                run_dir / "BACKLOG.md",
                run_dir / "STATE.json",
            )

        def _build_failed_tasks_block() -> str:
            return build_failed_tasks_block(run_dir / "STATE.json", run_dir)

        def _record_history(task_id: str, title: str, status: str, reason: str = "",
                            detail: str = "", files: list[str] | None = None, cycle: int = 0,
                            attempt: int = 0, max_attempts: int = 1, task_status: str = "") -> None:
            record_history(
                repo, run_dir, "codex",
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
                backend="codex",
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
            inv_md = prepare_pm_inventory_markdown(
                repo=repo,
                run_dir=run_dir,
                pm_cache_dir=pm_cache_dir,
                cycle_idx=cycle_idx,
                metrics=metrics,
                build_repo_inventory_fn=build_repo_inventory,
                write_repo_inventory_files_fn=write_repo_inventory_files,
            )

            # Optional TODO context (user-authored; drives backlog priority)
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
                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "todo_block": todo_block,
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "skills_index_summary": skills_index_summary,
                        "codex_call_hint": codex_call_hint(autopilot),
                        "pm_experience_summary": pm_experience_summary,
                        "task_history_block": _format_history_block(repo, max_items=_hist_max) if _hist_enabled else "(disabled)",
                    }
                    pm_prompt = append_pm_essential_context(
                        append_pm_output_contract(store.render("pm_bootstrap_prompt", PM_BOOTSTRAP_TEMPLATE_DEFAULT, ctx)),
                        turn_budget_warning=PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {args.pm_bootstrap_max_turns} turns)"),
                        done_tasks_block=_done_blk,
                        failed_tasks_block=_failed_blk,
                        goals_block=goals_block,
                        goals_instruction=goals_instruction,
                        experience_summary_block=experience_summary_block,
                    )
                    pm_out = await _run_pm_structured(
                        pm_prompt,
                        max_turns=args.pm_bootstrap_max_turns,
                        cycle_idx=cycle_idx,
                        kind="bootstrap",
                        output_path=pm_output_path,
                    )
                    if pm_out is None:
                        if stop_path.exists():
                            record_stop_checkpoint(
                                stage="pm_bootstrap",
                                cycle=cycle_idx,
                                message="Stop requested during PM bootstrap; stopping without PM retry or backlog mutation.",
                            )
                            metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=0, reason=STOP_REASON_STOP_FILE)
                            return False
                        metrics.event("pm_end", cycle=cycle_idx, kind="bootstrap", rc=1, error="structured_output_failed")
                        return False

                    _current_backlog_block, existing_tasks, done_ids, failed_ids = _load_backlog_context_for_pm()
                    _pre_pm_tasks = list(existing_tasks)  # recycled ID 鍮꾧탳???ㅻ깄??
                    pm_postprocess = postprocess_pm_output_tasks(
                        repo=repo,
                        run_dir=run_dir,
                        cycle_idx=cycle_idx,
                        kind="bootstrap",
                        raw_pm_output_path=pm_output_path,
                        pm_output_model_dump=pm_out.model_dump(),
                        existing_tasks=existing_tasks,
                        done_ids=done_ids,
                        failed_ids=failed_ids,
                        completion_level=goals_completion_level,
                    )
                    gate = pm_postprocess["pm_gate"]
                    _pm_dump = pm_postprocess["pm_output_model_dump"]
                    accepted_tasks = pm_postprocess["accepted_pm_tasks"]
                    rejected_tasks = pm_postprocess["rejected_pm_tasks"]
                    merged_tasks = pm_postprocess["backlog_tasks"]
                    write_pm_output_artifacts(
                        run_dir=run_dir,
                        cycle_idx=cycle_idx,
                        pm_output_model_dump=_pm_dump,
                        notes_md=pm_out.notes_md,
                        dump_pretty_fn=dump_pretty,
                    )
                    if gate.get("status") == "partial":
                        metrics.event(
                            "pm_goal_gate",
                            cycle=cycle_idx,
                            kind="bootstrap",
                            status=str(gate.get("status") or ""),
                            accepted_count=len(accepted_tasks),
                            rejected_count=len(rejected_tasks),
                            gate_required=bool(gate.get("gate_required")),
                            goal_path=str(gate.get("goal_path") or ""),
                        )
                    elif gate.get("status") == "rejected":
                        metrics.event(
                            "pm_goal_gate_rejected",
                            cycle=cycle_idx,
                            kind="bootstrap",
                            status=str(gate.get("status") or ""),
                            rejected_count=len(rejected_tasks),
                            accepted_count=len(accepted_tasks),
                            gate_required=bool(gate.get("gate_required")),
                            goal_path=str(gate.get("goal_path") or ""),
                        )
                    _current_backlog_block, existing_tasks, done_ids, failed_ids = _load_backlog_context_for_pm()
                    _pre_pm_tasks = list(existing_tasks)  # recycled ID 鍮꾧탳???ㅻ깄??

                    merged_tasks = pm_postprocess["backlog_tasks"]

                    if merged_tasks:
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
                            except Exception as _qa_merge_ex:
                                eprint(f"[WARN] QA followup merge during PM bootstrap failed: {_qa_merge_ex}")
                            write_backlog_files(run_dir, merged_tasks)
                            # Recycled ID 媛먯?: PM??湲곗〈 done ?쒖뒪??ID瑜????댁슜?쇰줈 ?ъ궗?⑺뻽?붿? ?뺤씤
                            try:
                                _new_tasks = load_tasks()
                                _st = load_state(run_dir / "STATE.json")
                                _ds = set(_st.get("done", []))
                                if _ds & {t.id for t in _new_tasks}:
                                    detect_and_clear_recycled_ids(
                                        prev_tasks=_pre_pm_tasks, new_tasks=_new_tasks,
                                        done_set=_ds, state=_st, state_path=run_dir / "STATE.json",
                                        save_state_fn=save_state,
                                        on_changed_fn=lambda ids: eprint(f"[RECYCLE] Cleared {len(ids)} recycled task IDs with new content: {sorted(ids)}"),
                                    )
                            except Exception:
                                pass
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
                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "todo_block": todo_block,
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "skills_index_summary": skills_index_summary,
                        "codex_call_hint": codex_call_hint(autopilot),
                        "prev_head": prev_head or curr_head,
                        "curr_head": curr_head,
                        "changed_files_block": changed_files_block,
                        "current_backlog_block": current_backlog_block,
                        "failed_tasks_block": failed_tasks_block,
                        "hint_block": hint_block,
                        "pm_experience_summary": pm_experience_summary,
                        "task_history_block": _format_history_block(repo, max_items=_hist_max_i) if _hist_enabled_i else "(disabled)",
                    }
                    pm_prompt = append_pm_essential_context(
                        append_pm_output_contract(store.render("pm_incremental_prompt", PM_INCREMENTAL_TEMPLATE_DEFAULT, ctx)),
                        turn_budget_warning=PM_TURN_BUDGET_WARNING.replace("LIMITED", f"LIMITED (max {args.pm_incremental_max_turns} turns)"),
                        done_tasks_block=_done_blk_i,
                        failed_tasks_block=_failed_blk_i,
                        goals_block=goals_block,
                        goals_instruction=goals_instruction,
                        build_warnings_block=build_warnings_block,
                        experience_summary_block=experience_summary_block,
                    )
                    pm_out = await _run_pm_structured(
                        pm_prompt,
                        max_turns=args.pm_incremental_max_turns,
                        cycle_idx=cycle_idx,
                        kind="incremental" if need_incremental else "refresh",
                        output_path=pm_output_path,
                    )
                    if pm_out is None:
                        if stop_path.exists():
                            record_stop_checkpoint(
                                stage="pm_incremental" if need_incremental else "pm_refresh",
                                cycle=cycle_idx,
                                message="Stop requested during PM; stopping without PM retry or backlog mutation.",
                            )
                            metrics.event(
                                "pm_end",
                                cycle=cycle_idx,
                                kind="incremental" if need_incremental else "refresh",
                                rc=0,
                                reason=STOP_REASON_STOP_FILE,
                            )
                            return False
                        metrics.event(
                            "pm_end",
                            cycle=cycle_idx,
                            kind="incremental" if need_incremental else "refresh",
                            rc=1,
                            error="structured_output_failed",
                        )
                        return False

                    _current_backlog_block, existing_tasks, done_ids, failed_ids = _load_backlog_context_for_pm()
                    _pre_pm_tasks_inc = list(existing_tasks)
                    pm_postprocess = postprocess_pm_output_tasks(
                        repo=repo,
                        run_dir=run_dir,
                        cycle_idx=cycle_idx,
                        kind="incremental" if need_incremental else "refresh",
                        raw_pm_output_path=pm_output_path,
                        pm_output_model_dump=pm_out.model_dump(),
                        existing_tasks=existing_tasks,
                        done_ids=done_ids,
                        failed_ids=failed_ids,
                        completion_level=goals_completion_level,
                    )
                    gate = pm_postprocess["pm_gate"]
                    _pm_dump = pm_postprocess["pm_output_model_dump"]
                    accepted_tasks = pm_postprocess["accepted_pm_tasks"]
                    rejected_tasks = pm_postprocess["rejected_pm_tasks"]
                    merged_tasks = pm_postprocess["backlog_tasks"]
                    write_pm_output_artifacts(
                        run_dir=run_dir,
                        cycle_idx=cycle_idx,
                        pm_output_model_dump=_pm_dump,
                        notes_md=pm_out.notes_md,
                        dump_pretty_fn=dump_pretty,
                    )
                    if gate.get("status") == "partial":
                        metrics.event(
                            "pm_goal_gate",
                            cycle=cycle_idx,
                            kind="incremental" if need_incremental else "refresh",
                            status=str(gate.get("status") or ""),
                            accepted_count=len(accepted_tasks),
                            rejected_count=len(rejected_tasks),
                            gate_required=bool(gate.get("gate_required")),
                            goal_path=str(gate.get("goal_path") or ""),
                        )
                    elif gate.get("status") == "rejected":
                        metrics.event(
                            "pm_goal_gate_rejected",
                            cycle=cycle_idx,
                            kind="incremental" if need_incremental else "refresh",
                            status=str(gate.get("status") or ""),
                            rejected_count=len(rejected_tasks),
                            accepted_count=len(accepted_tasks),
                            gate_required=bool(gate.get("gate_required")),
                            goal_path=str(gate.get("goal_path") or ""),
                        )
                    _current_backlog_block, existing_tasks, done_ids, failed_ids = _load_backlog_context_for_pm()
                    _pre_pm_tasks_inc = list(existing_tasks)

                    merged_tasks = pm_postprocess["backlog_tasks"]

                    if merged_tasks:
                        merged_tasks = _validate_skill_ids(merged_tasks)
                        if merged_tasks:
                            write_backlog_files(run_dir, merged_tasks)
                            # Recycled ID 媛먯?
                            try:
                                _new_tasks = load_tasks()
                                _st = load_state(run_dir / "STATE.json")
                                _ds = set(_st.get("done", []))
                                if _ds & {t.id for t in _new_tasks}:
                                    detect_and_clear_recycled_ids(
                                        prev_tasks=_pre_pm_tasks_inc, new_tasks=_new_tasks,
                                        done_set=_ds, state=_st, state_path=run_dir / "STATE.json",
                                        save_state_fn=save_state,
                                        on_changed_fn=lambda ids: eprint(f"[RECYCLE] Cleared {len(ids)} recycled task IDs with new content: {sorted(ids)}"),
                                    )
                            except Exception:
                                pass
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

        async def run_qa_if_needed(cycle_idx: int, ran_tasks: bool) -> dict[str, Any]:
            if stop_path.exists():
                return {"parse_ok": None, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0}
            if not (args.qa_always or ran_tasks):
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

                ctx = {"repo": str(repo), "run_dir": str(run_dir), "skills_context": skills_context}
                qa_prompt = store.render("qa_prompt", QA_TEMPLATE_DEFAULT, ctx)
                if bool(getattr(args, "qa_to_backlog", False)):
                    qa_prompt = qa_prompt.rstrip() + "\n\n" + QA_FOLLOWUPS_OUTPUT_CONTRACT + "\n"
                qa_result = await codex_exec(
                    qa_prompt,
                    instructions=qa_instructions,
                    model=args.qa_model,
                    reasoning_effort=codex_reasoning_effort,
                    cwd=repo,
                    timeout_seconds=600,
                    heartbeat_callback=lambda: metrics.event("heartbeat", stage="qa"),
                )
                qa_output_path = run_dir / f"qa_final_output_cycle_{cycle_idx:03d}.txt"
                qa_output_path.write_text(
                    (qa_result.final_output or "") + "\n", encoding="utf-8", errors="replace"
                )
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
                    state_path=run_dir / "STATE.json",
                    load_tasks_fn=load_tasks,
                    merge_qa_followups_fn=_merge_qa_followups,
                    write_backlog_files_fn=write_backlog_files,
                    metrics=metrics,
                )
                metrics.event("qa_end", cycle=cycle_idx, rc=0)
                return qa_summary
            except Exception as ex:
                metrics.event("qa_end", cycle=cycle_idx, rc=1, error=str(ex))
                if is_quota_exception(ex):
                    stop_path.write_text(STOP_REASON_QUOTA, encoding="utf-8")
                    return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0, "quota_exhausted": True}
                return {"parse_ok": False, "candidates": 0, "added": 0, "skipped": 0, "manual_test_count": 0}

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
            goals_update_result: dict[str, Any] = {}
            goals_before_status: dict[str, Any] = {}
            goals_after_status: dict[str, Any] = {}

            task_ids = {t.id for t in tasks}
            class _ScopedDoneSet(set[str]):
                def __len__(self) -> int:
                    return len(set.intersection(self, task_ids))

            done_set = _ScopedDoneSet(done_set)
            before_done = len(done_set.intersection(task_ids))

            pm_refresh = await maybe_refresh_tasks_after_pm(
                pm_stage_enabled=pm_stage_enabled,
                pm_refresh_backlog=bool(args.pm_refresh_backlog),
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

            if stop_path.exists():
                record_stop_checkpoint(
                    stage="dev",
                    cycle=cycle_idx,
                    message="Stop requested before Dev work; current backlog/state preserved.",
                )
                return 0, STOP_REASON_STOP_FILE, 0, (len(done_set) > before_done)

            tasks_root = run_dir / "tasks"
            tasks_root.mkdir(parents=True, exist_ok=True)
            runner_context = RunnerContext(run_dir)

            def _write_task_validation_artifact(
                *,
                task: TaskItem,
                attempt_context: AttemptContext,
                validations: list[dict[str, Any]],
                status: str,
                reason: str,
                detail: str = "",
                task_status: str = "",
            ) -> Path:
                records = [record for record in validations if isinstance(record, dict)]
                artifact_path = write_task_validation_artifacts(
                    attempt_dir=attempt_context.attempt_dir,
                    task_id=task.id,
                    task_title=task.title,
                    task_files=task.files,
                    cycle=cycle_idx,
                    step=step,
                    attempt=attempt,
                    validations=records,
                    status=status,
                    reason=reason,
                    detail=detail,
                    task_status=task_status,
                    goal_ref=task_goal_ref,
                    goal_text=task_goal_text,
                    goal_trace=task_goal_trace,
                )
                validation_artifacts: list[str] = [artifact_path.as_posix()]
                for record in records:
                    for key in ("artifact_path", "artifactPath", "log_path", "logPath"):
                        record_artifact = str(record.get(key) or "").strip()
                        if record_artifact and record_artifact not in validation_artifacts:
                            validation_artifacts.append(record_artifact)
                record_validation_experiences(
                    source_repo,
                    source_kind="task_validation",
                    run_id=run_dir.name,
                    task_id=task.id,
                    task_title=task.title,
                    task_ids=[task.id],
                    validation_status=str(status or "").strip() or "unknown",
                    validation_reason=str(reason or "").strip(),
                    validation_detail=str(detail or "").strip(),
                    validation_artifact_path=artifact_path.as_posix(),
                    validation_artifacts=validation_artifacts,
                    validation_records=records,
                )
                return artifact_path

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
                    command_repo=source_repo,
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
                        "4. Do NOT add new features - only fix build errors\n"
                    )

                    try:
                        await _run_codex_with_continuations(
                            build_fix_prompt,
                            instructions=dev_instructions,
                            model=str(args.dev_model),
                            full_auto=True,
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
                        command_repo=source_repo,
                    )
                    if post_fix_ok:
                        eprint("[BUILD-FIX] Build fixed successfully!")
                        metrics.event("build_fix_end", cycle=cycle_idx, rc=0)
                    else:
                        eprint("[BUILD-FIX] Build still broken after fix attempt.")
                        metrics.event("build_fix_end", cycle=cycle_idx, rc=1)

            if stop_path.exists():
                record_stop_checkpoint(
                    stage="dev_prebuild",
                    cycle=cycle_idx,
                    message="Stop requested during pre-build/build-fix; current artifacts preserved.",
                )
                return 0, STOP_REASON_STOP_FILE, 0, (len(done_set) > before_done)

            for step in range(int(args.iterations)):
                if stop_path.exists():
                    record_stop_checkpoint(
                        stage="dev",
                        cycle=cycle_idx,
                        step=step,
                        message="Stop requested before next task; current progress preserved.",
                    )
                    return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)

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

                task_context = runner_context.task_context(cycle=cycle_idx, step=step, task_id=next_task.id)
                task_dir = task_context.task_dir
                task_dir.mkdir(parents=True, exist_ok=True)

                task_goal_trace = [dict(trace) for trace in (next_task.goal_trace or []) if isinstance(trace, dict)]
                task_goal_ref = str(task_goal_trace[0].get("goal_ref") or task_goal_trace[0].get("goal_id") or "").strip() if task_goal_trace else ""
                task_goal_text = str(task_goal_trace[0].get("goal_text") or task_goal_trace[0].get("text") or "").strip() if task_goal_trace else ""

                metrics.event(
                    "task_start",
                    cycle=cycle_idx,
                    step=step,
                    task_id=next_task.id,
                    goal_trace=task_goal_trace,
                    goal_ref=task_goal_ref,
                    goal_text=task_goal_text,
                )
                task_outer_t0 = time.time()
                task_head_before = git_head(repo)

                tb: Optional[TaskBranch] = None
                cp: Optional[RepoCheckpoint] = None
                if args.isolate_task:
                    try:
                        tb = create_task_branch(
                            repo,
                            next_task.id,
                            task_title=next_task.title,
                            goal_trace=task_goal_trace,
                        )
                        metrics.event(
                            "task_branch_created",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            branch=tb.branch_name,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
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
                tiers, max_attempts, dev_max_escalations = compute_dev_model_tiers(
                    base_model=str(args.dev_model),
                    tier1_model=str(getattr(args, "dev_model_tier1", "") or ""),
                    tier2_model=str(getattr(args, "dev_model_tier2", "") or ""),
                    dev_auto_escalate=dev_auto_escalate,
                    dev_max_escalations=dev_max_escalations,
                    max_escalations_per_task_budget=max_escalations_per_task_budget,
                )

                # Ensure a rollback point when we may retry/escalate.
                # (Even if isolate_task is false, retries need a clean baseline.)
                if dev_auto_escalate and not tb and not cp:
                    try:
                        tb = create_task_branch(
                            repo,
                            next_task.id,
                            task_title=next_task.title,
                            goal_trace=task_goal_trace,
                        )
                        metrics.event(
                            "task_branch_created",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            branch=tb.branch_name,
                            reason="retry_escalation",
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                    except Exception:
                        metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id, reason="retry_escalation")
                        cp = create_checkpoint(repo, task_dir / "checkpoint")
                        metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, reason="retry_escalation")

                task_completed = False
                task_blocked = False
                task_failure_reason = ""
                _prev_gate_error: str = ""  # Carried across attempts for gate-aware retry
                _prev_gate_error_label: str = ""
                _blocked_env_guides_written: set[tuple[str, str, int]] = set()
                test_validation_result: dict[str, Any] | None = None
                fast_regression_triggered = False

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
                    validation_artifact: str = "",
                ) -> None:
                    outcome_status = _task_failure_status(reason, detail=detail) if not task_status else task_status
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
                            "validation_artifact": validation_artifact,
                            "goal_trace": task_goal_trace,
                            "goal_ref": task_goal_ref,
                            "goal_text": task_goal_text,
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
                        extra={
                            "goal_trace": task_goal_trace,
                            "goal_ref": task_goal_ref,
                            "goal_text": task_goal_text,
                        },
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
                        validation_status="validation_failed" if validations or validation_artifacts else "",
                        validation_summary=detail,
                        validations=validations,
                        artifact_pointers=validation_artifacts,
                        outcome_action="preserved_for_review" if should_preserve_for_review(task_status) else "discarded",
                        detail=detail,
                    )

                def _isolate_or_stop(reason: str, *, task_status: str = "", detail: str = "", validation_artifact: str = "") -> tuple[bool, str]:
                    """Apply task-branch disposition while preserving the old event/state semantics."""

                    def _on_branch_success(disposition: Any, branch_name: str) -> None:
                        metrics.event(
                            disposition.event_name,
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            reason=reason,
                            branch=branch_name,
                            task_status=disposition.outcome_status,
                            preserved=disposition.preserve_for_review,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        if disposition.preserve_for_review:
                            eprint(
                                f"[PRESERVE] {next_task.id} work kept on branch {branch_name} for review "
                                f"(status={disposition.outcome_status})."
                            )

                    def _on_abandon_failed(failure_detail: str) -> None:
                        eprint(f"[WARN] abandon_task_branch failed: {failure_detail}")
                        abandon_status = _task_failure_status("abandon_failed", detail=failure_detail)
                        _record_failed_state("abandon_failed", detail=failure_detail, task_status=abandon_status)
                        save_state(state_path, state)
                        _record_history(
                            next_task.id,
                            next_task.title,
                            "failed",
                            reason="abandon_failed",
                            detail=failure_detail,
                            files=next_task.files,
                            cycle=cycle_idx,
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            task_status=abandon_status,
                        )
                        metrics.event(
                            "task_branch_abandon_failed",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            reason=reason,
                            detail=failure_detail,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )

                    def _on_rollback_success(_disposition: Any, rescue_branch: str) -> None:
                        metrics.event(
                            "rollback",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            reason=reason,
                            rescue_branch=rescue_branch,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        if rescue_branch:
                            eprint(f"[INFO] Work preserved in branch: {rescue_branch}")

                    def _on_rollback_failed(fail_reason: str, failure_detail: str) -> None:
                        rollback_status = _task_failure_status(fail_reason, detail=failure_detail)
                        _record_failed_state(fail_reason, detail=failure_detail, task_status=rollback_status)
                        save_state(state_path, state)
                        _record_history(
                            next_task.id,
                            next_task.title,
                            "failed",
                            reason=fail_reason,
                            detail=failure_detail,
                            files=next_task.files,
                            cycle=cycle_idx,
                            attempt=attempt + 1,
                            max_attempts=max_attempts,
                            task_status=rollback_status,
                        )
                        metrics.event(
                            "rollback_failed",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            reason=reason,
                            detail=failure_detail,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        eprint(f"[STOP] Rollback {fail_reason}: {failure_detail}")

                    dispatch_result = dispatch_task_branch_disposition(
                        reason,
                        task_status=task_status,
                        detail=detail,
                        validation_artifact=validation_artifact,
                        has_task_branch=bool(tb),
                        has_checkpoint=bool(cp),
                        task_status_resolver=lambda failure_reason, failure_detail: _task_failure_status(
                            failure_reason,
                            detail=failure_detail,
                        ),
                        abandon_branch=(lambda: abandon_task_branch(repo, tb)) if tb else None,
                        restore_checkpoint=(
                            lambda: restore_checkpoint(
                                repo,
                                cp,
                                dangerous=bool(getattr(args, "dangerous_git_rollback", False)),
                                run_dir=run_dir,
                                stop_path=stop_path,
                                task_id=next_task.id,
                            )
                        ) if cp else None,
                        record_pending_review=_record_pending_review,
                        persist_state=lambda: save_state(state_path, state),
                        on_branch_success=_on_branch_success,
                        on_abandon_failed=_on_abandon_failed,
                        on_rollback_success=_on_rollback_success,
                        on_rollback_failed=_on_rollback_failed,
                    )
                    return dispatch_result.ok, dispatch_result.stop_reason

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
                            "goal_trace": task_goal_trace,
                            "goal_ref": task_goal_ref,
                            "goal_text": task_goal_text,
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
                        goal_trace=task_goal_trace,
                        goal_ref=task_goal_ref,
                        goal_text=task_goal_text,
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
                        if budget_exceeded(
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
                                task_blocked = True
                                break

                    model_name = tiers[attempt]

                    attempt_context = task_context.attempt_context(attempt)
                    attempt_dir = attempt_context.attempt_dir
                    attempt_dir.mkdir(parents=True, exist_ok=True)
                    validation_records: list[dict[str, Any]] = []

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
                        files=next_task.files or [],
                        goal_trace=task_goal_trace,
                        goal_ref=task_goal_ref,
                        goal_text=task_goal_text,
                    )

                    task_start_time = time.time()
                    dev_exc: Optional[Exception] = None
                    dev_result: CodexExecResult | None = None
                    dev_is_max_turns = False
                    dev_quota_exhausted = False
                    dev_final = ""

                    try:
                        dev_result = await _run_codex_with_continuations(
                            dev_prompt,
                            instructions=dev_instructions,
                            model=model_name,
                            full_auto=True,
                            label="dev",
                            timeout_sec=int(getattr(args, "dev_timeout_seconds", 0)) or 0,
                            max_continuations=int(getattr(args, "dev_max_turns_continuations", 0)) or 0,
                            task_id=next_task.id,
                        )
                        dev_final = (dev_result.final_output or "")
                        _inp, _out = dev_result.input_tokens, dev_result.output_tokens
                        token_tracker.add("Dev", _inp, _out)
                        task_duration = time.time() - task_start_time
                        logger.timing("dev_task_execution", task_duration, task_id=next_task.id, attempt=attempt)
                        # Check result-based error signals (codex exec returns errors in result, not exceptions)
                        dev_is_max_turns = dev_result.is_timeout
                        dev_quota_exhausted = dev_result.is_quota_exhausted
                        if dev_result.exit_code != 0 and dev_result.error:
                            dev_exc = RuntimeError(dev_result.error)
                    except Exception as ex:
                        dev_exc = ex
                        dev_final = ""
                        dev_is_max_turns = False
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

                    attempt_context.dev_output_path.write_text(dev_log + "\n", encoding="utf-8", errors="replace")
                    (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)
                    (run_dir / "dev_logs" / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.txt").write_text(
                        dev_log + "\n", encoding="utf-8", errors="replace"
                    )

                    if stop_path.exists():
                        _record_task_stop("dev_attempt", attempt)
                        return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)

                    # Quota/credits exhausted: graceful stop with artifacts preserved.
                    dev_quota_exhausted = dev_quota_exhausted or has_quota_text(dev_log)
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
                    _model_invalid = (dev_exc and is_model_invalid_exception(dev_exc)) or (
                        not dev_exc and dev_result is not None and dev_result.exit_code != 0
                        and dev_result.error and ("model" in dev_result.error.lower() and ("invalid" in dev_result.error.lower() or "not found" in dev_result.error.lower()))
                    )
                    if _model_invalid:
                        if (attempt + 1) < max_attempts:
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="model_invalid")
                            continue

                    # Non-max-turn exceptions are treated as fatal (rollback + stop)
                    if dev_exc and not dev_is_max_turns:
                        task_status = _task_failure_status("exception", detail=str(dev_exc))
                        _record_failed_state("exception", detail=str(dev_exc), task_status=task_status)
                        save_state(state_path, state)
                        _record_failed_task_result("exception", task_status=task_status, detail=str(dev_exc))
                        _record_history(next_task.id, next_task.title, "failed", reason="exception", detail=str(dev_exc), files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                        metrics.event(
                            "task_end",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            rc=1,
                            reason="exception",
                            task_status=task_status,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        logger.task_end(
                            task_id=next_task.id,
                            success=False,
                            reason="exception",
                            task_status=task_status,
                            exception=str(dev_exc),
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        if tb or cp:
                            ok, fail_reason = _isolate_or_stop("exception", task_status=task_status, detail=str(dev_exc))
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
                    dep_req_path = runner_context.dependency_required_path
                    if not dep_req_path.exists():
                        dep_req_path = attempt_context.dependency_required_path
                    if dep_req_path.exists():
                        dep_content = dep_req_path.read_text(encoding="utf-8", errors="replace")
                        eprint(f"[SKIP] Task {next_task.id} requires new dependencies:")
                        eprint(dep_content.strip())
                        # Append to run-level summary
                        dep_summary_path = run_dir / "DEPENDENCIES_NEEDED.md"
                        with open(dep_summary_path, "a", encoding="utf-8") as f:
                            f.write(f"\n## {next_task.id}: {next_task.title}\n\n{dep_content.strip()}\n\n---\n")
                        dep_detail = dep_content.strip()[:500]
                        task_status = _task_failure_status("needs_dependency", detail=dep_detail)
                        _record_failed_state("needs_dependency", detail=dep_detail, task_status=task_status)
                        save_state(state_path, state)
                        _record_failed_task_result("needs_dependency", task_status=task_status, detail=dep_detail)
                        _record_history(next_task.id, next_task.title, "failed", reason="needs_dependency", detail=dep_detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                        metrics.event(
                            "task_end",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            rc=1,
                            reason="needs_dependency",
                            task_status=task_status,
                            was_max_turns=dev_is_max_turns,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        logger.task_end(
                            task_id=next_task.id,
                            success=False,
                            reason="needs_dependency",
                            task_status=task_status,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        skipped_set.add(next_task.id)
                        # Clean up the signal file so it doesn't affect subsequent tasks
                        try:
                            dep_req_path.unlink()
                        except Exception:
                            pass
                        task_blocked = True
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
                            notes_path = attempt_context.notes_path
                            if notes_path.exists():
                                try:
                                    notes_content = notes_path.read_text(encoding="utf-8", errors="ignore").lower()
                                    is_blocked = any(keyword in notes_content for keyword in blocked_keywords)
                                except Exception:
                                    pass

                        if is_blocked:
                            eprint(f"[SKIP] Task {next_task.id} appears blocked (dependency/resource missing). Skipping...")
                            task_status = _task_failure_status("blocked_dependency", detail=dev_log)
                            _record_failed_state("blocked_dependency", detail=dev_log[:500], task_status=task_status)
                            save_state(state_path, state)
                            _record_failed_task_result("blocked_dependency", task_status=task_status, detail=dev_log[:500])
                            _record_history(next_task.id, next_task.title, "failed", reason="blocked_dependency", detail=dev_log[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                            metrics.event(
                                "task_end",
                                cycle=cycle_idx,
                                step=step,
                                task_id=next_task.id,
                                rc=1,
                                reason="blocked_dependency",
                                task_status=task_status,
                                was_max_turns=dev_is_max_turns,
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
                            logger.task_end(
                                task_id=next_task.id,
                                success=False,
                                reason="blocked_dependency",
                                task_status=task_status,
                                was_max_turns=dev_is_max_turns,
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
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
                        task_status = _task_failure_status("no_diff", detail=dev_log)
                        _record_failed_state("no_diff", detail=dev_log[:500], task_status=task_status)
                        save_state(state_path, state)
                        _record_failed_task_result("no_diff", task_status=task_status, detail=dev_log[:500])
                        _record_history(next_task.id, next_task.title, "failed", reason="no_diff", detail=dev_log[:500], files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                        metrics.event(
                            "task_end",
                            cycle=cycle_idx,
                            step=step,
                            task_id=next_task.id,
                            rc=1,
                            reason="no_diff",
                            task_status=task_status,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        logger.task_end(
                            task_id=next_task.id,
                            success=False,
                            reason="no_diff",
                            task_status=task_status,
                            was_max_turns=dev_is_max_turns,
                            goal_trace=task_goal_trace,
                            goal_ref=task_goal_ref,
                            goal_text=task_goal_text,
                        )
                        logger.skip_event(next_task.id, "no diff produced")
                        if tb or cp:
                            ok, fail_reason = _isolate_or_stop("no_diff", task_status=task_status, detail=dev_log[:500])
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
                        build_validation = await run_build_validation_async(
                            repo=repo,
                            build_cmd=getattr(args, "build_cmd", []),
                            build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800)),
                            legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                            log_path=attempt_context.build_log_path,
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
                                    commit_subject, commit_body = format_task_commit_message(tb, action="build passed")
                                    commit_cmd = ["git", "commit", "--no-verify", "-m", commit_subject]
                                    if commit_body:
                                        commit_cmd.extend(["-m", commit_body])
                                    run_cmd(commit_cmd, cwd=repo, timeout_sec=120)
                                    metrics.event(
                                        "task_branch_commit",
                                        cycle=cycle_idx,
                                        step=step,
                                        task_id=next_task.id,
                                        trigger="build_passed",
                                        branch=tb.branch_name,
                                        goal_trace=task_goal_trace,
                                        goal_ref=task_goal_ref,
                                        goal_text=task_goal_text,
                                    )
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
                            failure_disposition = decide_failure_disposition(
                                "build_failed",
                                task_status=task_status,
                                validations=validation_records,
                                detail=build_detail,
                                attempt=attempt,
                                max_attempts=max_attempts,
                                dev_auto_escalate=dev_auto_escalate,
                                dev_escalate_on=dev_escalate_on,
                            )
                            if failure_disposition.action == ACTION_RETRY:
                                # Capture build errors for injection into the next attempt's prompt.
                                try:
                                    _berr_raw = attempt_context.build_log_path.read_text(encoding="utf-8", errors="replace")
                                    _berr_lines = [ln for ln in _berr_raw.splitlines() if "error " in ln.lower()]
                                    _prev_gate_error = "\n".join(_berr_lines[:50]) or _berr_raw[-4000:]
                                    _prev_gate_error_label = "BUILD FAILED"
                                except Exception:
                                    _prev_gate_error = ""
                                    _prev_gate_error_label = ""
                                _write_task_validation_artifact(
                                    task=next_task,
                                    attempt_context=attempt_context,
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
                                    validations=validation_records,
                                    artifact_pointers=[str(attempt_dir / "validation.json")],
                                    outcome_action="retry_scheduled",
                                    detail=build_detail,
                                )
                                metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="build_failed")
                                continue
                            _record_failed_state(
                                "build_failed",
                                validations=validation_records,
                                detail=build_detail,
                                task_status=task_status,
                            )
                            save_state(state_path, state)
                            _record_failed_task_result(
                                "build_failed",
                                task_status=task_status,
                                validations=validation_records,
                                detail=build_detail,
                                validation_artifact=str(attempt_context.validation_json_path),
                            )
                            _record_history(next_task.id, next_task.title, "failed", reason="build_failed", detail=build_detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                            logger.gate_event("build", next_task.id, passed=False)
                            _write_task_validation_artifact(
                                task=next_task,
                                attempt_context=attempt_context,
                                validations=validation_records,
                                status="failed",
                                reason="build_failed",
                                detail=build_detail,
                                task_status=task_status,
                            )
                            if tb or cp:
                                ok_restore, fail_reason = _isolate_or_stop(
                                    "build_failed",
                                    task_status=task_status,
                                    detail=build_detail,
                                    validation_artifact=str(attempt_context.validation_json_path),
                                )
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
                        test_validation = await run_test_validation_async(
                            repo=repo,
                            test_cmd=getattr(args, "test_cmd", []),
                            test_timeout_sec=int(getattr(args, "test_timeout_seconds", 3600)),
                            legacy_test_target=str(getattr(args, "dotnet_test_target", "") or ""),
                            legacy_test_filter=str(getattr(args, "dotnet_test_filter", "") or ""),
                            log_path=attempt_context.test_log_path,
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
                            failure_disposition = decide_failure_disposition(
                                "test_failed",
                                task_status=task_status,
                                validations=validation_records,
                                detail=test_detail,
                                attempt=attempt,
                                max_attempts=max_attempts,
                                dev_auto_escalate=dev_auto_escalate,
                                dev_escalate_on=dev_escalate_on,
                            )
                            if failure_disposition.action == ACTION_RETRY:
                                try:
                                    _prev_gate_error = attempt_context.test_log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
                                    _prev_gate_error_label = "TEST FAILED"
                                except Exception:
                                    _prev_gate_error = ""
                                    _prev_gate_error_label = ""
                                _write_task_validation_artifact(
                                    task=next_task,
                                    attempt_context=attempt_context,
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
                                    validations=validation_records,
                                    artifact_pointers=[str(attempt_dir / "validation.json")],
                                    outcome_action="retry_scheduled",
                                    detail=test_detail,
                                )
                                metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="test_failed")
                                continue
                            _record_failed_state(
                                "test_failed",
                                validations=validation_records,
                                detail=test_detail,
                                task_status=task_status,
                            )
                            save_state(state_path, state)
                            _record_failed_task_result(
                                "test_failed",
                                task_status=task_status,
                                validations=validation_records,
                                detail=test_detail,
                                validation_artifact=str(attempt_context.validation_json_path),
                            )
                            _record_history(next_task.id, next_task.title, "failed", reason="test_failed", detail=test_detail, files=next_task.files, cycle=cycle_idx, attempt=attempt + 1, max_attempts=max_attempts, task_status=task_status)
                            logger.gate_event("test", next_task.id, passed=False)
                            _write_task_validation_artifact(
                                task=next_task,
                                attempt_context=attempt_context,
                                validations=validation_records,
                                status="failed",
                                reason="test_failed",
                                detail=test_detail,
                                task_status=task_status,
                            )
                            if tb or cp:
                                ok_restore, fail_reason = _isolate_or_stop(
                                    "test_failed",
                                    task_status=task_status,
                                    detail=test_detail,
                                    validation_artifact=str(attempt_context.validation_json_path),
                                )
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
                                f.write(json.dumps({"ts": now_iso(), "cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False, default=str) + "\n")
                        except Exception:
                            pass

                        if not scan_result.get("ok", True):
                            policy_detail = json.dumps(scan_result.get("fail_violations", []), ensure_ascii=False, default=str)[:1000]
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
                            metrics.event(
                                "task_end",
                                cycle=cycle_idx,
                                step=step,
                                task_id=next_task.id,
                                rc=1,
                                reason="policy_violation",
                                task_status=task_status,
                                violations=len(scan_result.get("fail_violations", [])),
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
                            if tb or cp:
                                ok_restore, fail_reason = _isolate_or_stop("policy_violation", task_status=task_status, detail=policy_detail)
                                if not ok_restore:
                                    if not continuous:
                                        return 1, fail_reason, 0, (len(done_set) > before_done)
                                    eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                            if continuous:
                                skipped_set.add(next_task.id)
                                break
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
                        fast_regression_log = attempt_context.fast_web_worktree_regression_path
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
                            task_status = _task_failure_status(
                                "fast_regression_failed",
                                validations=validation_records,
                                detail=failed_summary,
                            )
                            failure_disposition = decide_failure_disposition(
                                "fast_regression_failed",
                                task_status=task_status,
                                validations=validation_records,
                                detail=failed_summary,
                                attempt=attempt,
                                max_attempts=max_attempts,
                                dev_auto_escalate=dev_auto_escalate,
                                dev_escalate_on=dev_escalate_on,
                            )
                            if (
                                failure_disposition.action == ACTION_RETRY
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
                                metrics.event(
                                    "dev_attempt_retry",
                                    cycle=cycle_idx,
                                    step=step,
                                    task_id=next_task.id,
                                    attempt=attempt,
                                    reason="fast_regression_failed",
                                    task_status=task_status,
                                    failed_command=failed_name,
                                    log_path=str(fast_regression_log),
                                )
                                _write_task_validation_artifact(
                                    task=next_task,
                                    attempt_context=attempt_context,
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
                                    validations=validation_records,
                                    artifact_pointers=[str(attempt_dir / "validation.json")],
                                    outcome_action="retry_scheduled",
                                    detail=failed_summary,
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
                                validations=validation_records,
                                artifact_pointers=[str(attempt_dir / "validation.json")],
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
                                extra={
                                    "goal_trace": task_goal_trace,
                                    "goal_ref": task_goal_ref,
                                    "goal_text": task_goal_text,
                                },
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
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
                            logger.task_end(
                                task_id=next_task.id,
                                success=False,
                                reason="fast_regression_failed",
                                task_status=task_status,
                                attempt=attempt,
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
                            _write_task_validation_artifact(
                                task=next_task,
                                attempt_context=attempt_context,
                                validations=validation_records,
                                status="failed",
                                reason="fast_regression_failed",
                                detail=failed_summary,
                                task_status=task_status,
                            )
                            task_failure_reason = "fast_regression_failed"
                            if tb or cp:
                                ok_restore, fail_reason = _isolate_or_stop(
                                    "fast_regression_failed",
                                    task_status=task_status,
                                    detail=failed_summary,
                                    validation_artifact=str(attempt_context.validation_json_path),
                                )
                                if not ok_restore:
                                    if not continuous:
                                        return 1, fail_reason, 0, (len(done_set) > before_done)
                                    eprint(f"[WARN] Rollback {fail_reason} for {next_task.id}; continuing anyway.")
                            if continuous:
                                skipped_set.add(next_task.id)
                                break
                            return 1, "fast_regression_failed", 0, (len(done_set) > before_done)
                    # Success: exit attempt loop
                    metrics.event("dev_attempt_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0)
                    logger.task_end(
                        task_id=next_task.id,
                        success=True,
                        reason="completed",
                        attempt=attempt,
                        goal_trace=task_goal_trace,
                        goal_ref=task_goal_ref,
                        goal_text=task_goal_text,
                    )
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
                        task=next_task,
                        attempt_context=attempt_context,
                        validations=validation_records,
                        status=task_validation_status,
                        reason="completed",
                        task_status=TASK_STATUS_COMPLETED,
                    )

                task_validation_artifacts: list[str] = []
                task_validation_notes: list[str] = []
                if task_completed:
                    task_validation_artifacts.append(str(attempt_dir / "validation.json"))
                    for record in validation_records:
                        if not isinstance(record, dict):
                            continue
                        artifact_path = str(
                            record.get("artifact_path")
                            or record.get("log_path")
                            or record.get("path")
                            or ""
                        ).strip()
                        if artifact_path and artifact_path not in task_validation_artifacts:
                            task_validation_artifacts.append(artifact_path)
                        note = str(record.get("summary") or record.get("detail") or "").strip()
                        if note and note not in task_validation_notes:
                            task_validation_notes.append(note)

                if stop_path.exists():
                    _record_task_stop("dev_after_attempt", locals().get("attempt") if "attempt" in locals() else None)
                    return 0, STOP_REASON_STOP_FILE, len(done_set.intersection(task_ids)) - before_done, (len(done_set) > before_done)

                # Merge or preserve task branch
                preserved_task_branch_has_new_commits = False
                packet_result: dict[str, object] = {}
                completed_task_branch_ref = ""
                completed_task_head_ref = ""
                completed_task_base_ref = ""
                completed_task_changed_files = list(next_task.files or [])
                completed_task_validation_artifacts = list(task_validation_artifacts)
                completed_task_pr_packet_ids: list[str] = []
                if task_completed and tb:
                    if worktree_dir is not None:
                        try:
                            abandon_task_branch(repo, tb)
                        except Exception as _ab_ex:
                            eprint(f"[WARN] abandon_task_branch failed for preserved worktree task {tb.branch_name}: {_ab_ex}")
                            metrics.event(
                                "task_branch_preserve_failed",
                                cycle=cycle_idx,
                                step=step,
                                task_id=next_task.id,
                                branch=tb.branch_name,
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                                error=str(_ab_ex),
                            )
                        else:
                            branch_head = git_rev_parse_ref(repo, tb.branch_name) or ""
                            completed_task_branch_ref = tb.branch_name
                            completed_task_head_ref = branch_head
                            completed_task_base_ref = tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit
                            preserved_task_branch_has_new_commits = ref_has_new_commits(
                                repo,
                                tb.branch_name,
                                task_head_before,
                            )
                            source_head_after = git_head(repo)
                            completed_task_changed_files = git_changed_files(source_repo, tb.base_commit, branch_head)
                            try:
                                packet_result = queue_review_packet(
                                    source_repo,
                                    run_id=run_dir.name,
                                    task_ids=[next_task.id],
                                    base_ref=tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit,
                                    head_ref=branch_head,
                                    branch=tb.branch_name,
                                    created_at=tb.created_at,
                                    source_head_before=source_base_ref,
                                    source_head_after=source_head_after,
                                    worktree_dir=worktree_dir.as_posix(),
                                    validation_status=task_validation_status,
                                    validation_artifacts=task_validation_artifacts,
                                    qa_notes=task_validation_notes,
                                    goal_trace=tb.goal_trace,
                                    changed_files=completed_task_changed_files,
                                    merge_preflight={
                                        "base_ref": tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit,
                                        "head_ref": branch_head,
                                        "branch": tb.branch_name,
                                        "source_head_before": source_base_ref,
                                        "source_head_after": source_head_after,
                                        "source_main_mutated": source_base_ref != source_head_after,
                                    },
                                    status="pr_queued",
                                )
                            except Exception as _pq_ex:
                                packet_result = {
                                    "ok": False,
                                    "status": "pr_queue_failed",
                                    "recoverable": False,
                                    "recoverable_reason": str(_pq_ex),
                                    "packet_path": "",
                                    "branch_index_path": "",
                                    "packet_id": "",
                                }
                                eprint(f"[WARN] queue_review_packet failed for {tb.branch_name}: {_pq_ex}")
                                metrics.event(
                                    "task_review_packet_failed",
                                    cycle=cycle_idx,
                                    step=step,
                                    task_id=next_task.id,
                                    branch=tb.branch_name,
                                    error=str(_pq_ex),
                                    goal_trace=task_goal_trace,
                                    goal_ref=task_goal_ref,
                                    goal_text=task_goal_text,
                                )
                            else:
                                packet_payload = packet_result.get("packet")
                                if isinstance(packet_payload, dict):
                                    completed_task_branch_ref = str(packet_payload.get("branch") or completed_task_branch_ref).strip()
                                    completed_task_head_ref = str(packet_payload.get("head_ref") or completed_task_head_ref).strip()
                                    completed_task_base_ref = str(packet_payload.get("base_ref") or completed_task_base_ref).strip()
                                    packet_changed_files = packet_payload.get("changed_files")
                                    if isinstance(packet_changed_files, list):
                                        completed_task_changed_files = [str(item).strip() for item in packet_changed_files if str(item).strip()]
                                    packet_validation_artifacts = packet_payload.get("validation_artifacts")
                                    if isinstance(packet_validation_artifacts, list):
                                        completed_task_validation_artifacts = [
                                            str(item).strip() for item in packet_validation_artifacts if str(item).strip()
                                        ]
                                packet_id_text = str(packet_result.get("packet_id") or "").strip()
                                if packet_id_text:
                                    completed_task_pr_packet_ids = [packet_id_text]
                                metrics.event(
                                    "task_review_packet_queued",
                                    cycle=cycle_idx,
                                    step=step,
                                    task_id=next_task.id,
                                    branch=tb.branch_name,
                                    packet_id=packet_result.get("packet_id", ""),
                                    packet_path=packet_result.get("packet_path", ""),
                                    branch_index_path=packet_result.get("branch_index_path", ""),
                                    recoverable=bool(packet_result.get("recoverable")),
                                    goal_trace=task_goal_trace,
                                    goal_ref=task_goal_ref,
                                    goal_text=task_goal_text,
                                )
                                if packet_result.get("recoverable"):
                                    eprint(
                                        f"[WARN] Task review packet for {next_task.id} is recoverable: "
                                        f"{packet_result.get('recoverable_reason') or packet_result.get('status')}"
                                    )
                        tb = None
                    else:
                        completed_task_branch_ref = tb.branch_name
                        completed_task_base_ref = tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit
                        completed_task_head_ref = git_head(repo)
                        merge_ok = merge_task_branch(repo, tb)
                        if merge_ok:
                            completed_task_changed_files = git_changed_files(repo, task_head_before, git_head(repo))
                            metrics.event(
                                "task_branch_merged",
                                cycle=cycle_idx,
                                step=step,
                                task_id=next_task.id,
                                branch=tb.branch_name,
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
                        else:
                            eprint(f"[WARN] Merge failed for {tb.branch_name}; work preserved on branch")
                            metrics.event(
                                "task_branch_merge_failed",
                                cycle=cycle_idx,
                                step=step,
                                task_id=next_task.id,
                                branch=tb.branch_name,
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
                        tb = None

                if task_blocked:
                    # Blocked tasks: skip to next task instead of stopping
                    if tb:
                        try:
                            abandon_task_branch(repo, tb)
                        except Exception as _ab_ex:
                            eprint(f"[WARN] abandon_task_branch failed for blocked task: {_ab_ex}")
                        tb = None
                    skipped_set.add(next_task.id)
                    eprint(f"[INFO] Skipped blocked task {next_task.id}, continuing to next task...")
                    continue

                if not task_completed:
                    if tb:
                        try:
                            abandon_task_branch(repo, tb)
                            metrics.event(
                                "task_branch_abandoned",
                                cycle=cycle_idx,
                                step=step,
                                task_id=next_task.id,
                                branch=tb.branch_name,
                                reason="exhausted_attempts",
                                goal_trace=task_goal_trace,
                                goal_ref=task_goal_ref,
                                goal_text=task_goal_text,
                            )
                        except Exception as _ab_ex:
                            eprint(f"[WARN] abandon_task_branch failed: {_ab_ex}")
                        tb = None
                    # No success after attempts and not otherwise returned: treat as failure.
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
                        extra={
                            "goal_trace": task_goal_trace,
                            "goal_ref": task_goal_ref,
                            "goal_text": task_goal_text,
                        },
                    )
                    logger.task_end(task_id=next_task.id, success=False, reason="exhausted_attempts", task_status=task_status, attempts=max_attempts, goal_trace=task_goal_trace, goal_ref=task_goal_ref, goal_text=task_goal_text)
                    if continuous:
                        eprint(f"[SKIP] Exhausted all attempts for {next_task.id}; skipping to next task.")
                        skipped_set.add(next_task.id)
                        continue
                    return 1, "exhausted_attempts", 0, (len(done_set) > before_done)
                # Phantom completion detection: task marked done but no git commits created.
                # In isolated worktree mode, a completed task branch is preserved and the
                # checkout returns to the base ref before this check, so inspect the preserved
                # branch head instead of only the current checkout HEAD.
                if not (preserved_task_branch_has_new_commits or has_new_commits(repo, task_head_before)):
                    logger.warning(f"Task {next_task.id} passed gates but no commits found (phantom completion)")
                    metrics.event("phantom_completion_detected", task_id=next_task.id, cycle=cycle_idx)
                    # Treat as failure - do NOT mark done
                    no_commits_detail = "Task passed all gates but no git commits were created (phantom completion)"
                    task_status = _task_failure_status("no_commits", detail=no_commits_detail)
                    _record_failed_state("no_commits", detail=no_commits_detail, task_status=task_status)
                    save_state(state_path, state)
                    _record_history(next_task.id, next_task.title, "failed",
                                    reason="no_commits", detail=no_commits_detail, files=next_task.files,
                                    cycle=cycle_idx, attempt=attempt + 1, task_status=task_status)
                    record_task_failure_result(
                        task_results,
                        task_id=next_task.id,
                        task_title=next_task.title,
                        reason="no_commits",
                        duration=time.time() - task_outer_t0,
                        task_status=task_status,
                        extra={
                            "goal_trace": task_goal_trace,
                            "goal_ref": task_goal_ref,
                            "goal_text": task_goal_text,
                        },
                    )
                    logger.task_end(task_id=next_task.id, success=False, reason="no_commits", task_status=task_status, goal_trace=task_goal_trace, goal_ref=task_goal_ref, goal_text=task_goal_text)
                    if continuous:
                        eprint(f"[PHANTOM] {next_task.id} has no commits; marking failed and continuing.")
                        skipped_set.add(next_task.id)
                        continue
                    break
                # Mark done only after gates AND commit verification
                done_set.add(next_task.id)
                # Clean up previous failure entries for this task (e.g. from earlier cycles)
                if state.get("failed"):
                    state["failed"] = [f for f in state["failed"] if f.get("task") != next_task.id]
                state["done"] = sorted(list(done_set))
                save_state(state_path, state)
                mark_backlog_done(backlog_md, next_task.id)
                _record_history(next_task.id, next_task.title, "done", files=next_task.files, cycle=cycle_idx, task_status=TASK_STATUS_COMPLETED)
                record_completed_task_experience(
                    repo,
                    run_id=run_dir.name,
                    task_id=next_task.id,
                    title=next_task.title,
                    status="done",
                    task_status=TASK_STATUS_COMPLETED,
                    validation_status=task_validation_status,
                    goal_trace=task_goal_trace,
                    changed_files=completed_task_changed_files,
                    branch_ref=completed_task_branch_ref,
                    head_ref=completed_task_head_ref,
                    base_ref=completed_task_base_ref,
                    validation_artifacts=completed_task_validation_artifacts,
                    validation_records=validation_records,
                    pr_packet_ids=completed_task_pr_packet_ids,
                )
                task_results.append({
                    "id": next_task.id,
                    "title": next_task.title,
                    "status": "done",
                    "duration": time.time() - task_outer_t0,
                    "goal_trace": task_goal_trace,
                    "goal_ref": task_goal_ref,
                    "goal_text": task_goal_text,
                    "validation_artifact": str(attempt_context.validation_json_path),
                    "validation_status": task_validation_status,
                    "task_status": TASK_STATUS_COMPLETED,
                })

                # Use current-cycle task IDs to avoid cross-cycle accumulation (done=16/11 bug)
                _done_this_cycle = len(done_set.intersection(task_ids))
                _skipped_this_cycle = len(skipped_set.intersection(task_ids))
                (run_dir / "progress.txt").write_text(f"done={_done_this_cycle}/{len(tasks)} skipped={_skipped_this_cycle} last={next_task.id}\n",
                                                     encoding="utf-8", errors="replace")

                code, names = run_cmd(["git", "diff", "--name-only"], cwd=repo, timeout_sec=60)
                files_changed_count = len([ln for ln in names.splitlines() if ln.strip()]) if code == 0 else 0
                metrics.event(
                    "task_end",
                    cycle=cycle_idx,
                    step=step,
                    task_id=next_task.id,
                    rc=0,
                    files_changed_count=files_changed_count,
                    goal_trace=task_goal_trace,
                    goal_ref=task_goal_ref,
                    goal_text=task_goal_text,
                )

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
                "ts": now_iso(),
                "cycle": cycle_idx,
                "run_dir": str(run_dir),
                "done": done_count,
                "skipped": skipped_count,
                "total_tasks": total_count,
                "failed_count": failed_count,
                "tasks_regressed": failure_group_counts.get("regression", 0),
                "tasks_review": failure_group_counts.get("review", 0),
                "tasks_blocked_env": failure_group_counts.get("blocked_env", 0),
                "failure_group_counts": failure_group_counts,
                "warnings_count": warnings_count,
                "duration_seconds": cycle_dt,
                "build_enabled": build_enabled,
                "build_cmd": list(getattr(args, "build_cmd", []) or []),
                "legacy_build_target": str(getattr(args, "dotnet_build_target", "") or ""),
                "run_tests": run_tests,
                "test_cmd": list(getattr(args, "test_cmd", []) or []),
                "legacy_test_target": str(getattr(args, "dotnet_test_target", "") or ""),
                "legacy_test_filter": str(getattr(args, "dotnet_test_filter", "") or ""),
                "qa_always": bool(getattr(args, "qa_always", False)),
                "qa_to_backlog": bool(getattr(args, "qa_to_backlog", False)),
                "policy_scan_enabled": policy_scan_enabled,
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

            # Update repo snapshot at END of cycle as well (helps resume/restart correctness when HEAD changes during work).
            latest_head = ""
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
                    _gp_before, _gt_before = read_goals(repo)
                    if _gt_before:
                        goals_before_status = parse_goals_completion(_gt_before, completion_level=goals_completion_level)
                    goals_update = update_goals_checkboxes(
                        repo,
                        done_titles,
                        done_prompts,
                        completion_level=goals_completion_level,
                    )
                    goals_update_result = dict(goals_update)
                    goals_after_status = dict(goals_update.get("new_status") or goals_before_status)
                    if goals_update.get("updated"):
                        checked = goals_update.get("checked_items", [])
                        eprint(f"[GOALS] Auto-checked {len(checked)} item(s): {checked[:5]}")
                        metrics.event("goals_updated", cycle=cycle_idx, checked_count=len(checked), items=checked[:10])
                except Exception as goals_ex:
                    eprint(f"[WARN] Goals auto-check failed: {goals_ex}")

            try:
                final_head = git_head(repo).strip() or latest_head or curr_head
            except Exception:
                final_head = latest_head or curr_head
            try:
                write_cycle_change_summary_artifacts(
                    repo=repo,
                    run_dir=run_dir,
                    cycle_idx=cycle_idx,
                    start_head=curr_head,
                    end_head=final_head,
                    changed_files=changed_files,
                    task_results=task_results,
                    goals_before=goals_before_status,
                    goals_after=goals_after_status,
                    goals_update=goals_update_result,
                    completion_level=goals_completion_level,
                )
            except Exception as cycle_summary_ex:
                eprint(f"[WARN] Cycle change summary write failed: {cycle_summary_ex}")

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
                    security_end_include_totals=False,
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
        quota_wait_for_reset = bool(getattr(args, "quota_wait_for_reset", True))
        try:
            quota_5h_max = max(1.0, float(getattr(args, "quota_five_hour_max_utilization", 95) or 95))
            quota_7d_max = max(1.0, float(getattr(args, "quota_seven_day_max_utilization", 95) or 95))
        except Exception:
            quota_5h_max = 95.0
            quota_7d_max = 95.0
        loop_sleep_seconds = int(getattr(args, "loop_sleep_seconds", 60) or 60)
        loop_idle_exit_after = int(getattr(args, "loop_idle_exit_after", 0) or 0)
        cycle_indices = loop_cycle_indices(
            bool(getattr(args, "loop", False)),
            getattr(args, "loop_max_cycles", 0),
        )

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
                _gr_res = await codex_exec(
                    refresh_prompt,
                    model=str(getattr(args, "pm_model", "gpt-5.5") or "gpt-5.5"),
                    reasoning_effort=codex_reasoning_effort,
                    cwd=repo,
                    timeout_seconds=int(getattr(args, "pm_timeout_seconds", 900) or 900),
                    heartbeat_callback=lambda: metrics.event("heartbeat", stage="goals_refresh"),
                )
                refresh_text = (_gr_res.final_output or "").strip()
                result = parse_and_append_refreshed_goals(repo, refresh_text)
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
                pm_stop_reason.clear()

                if stop_path.exists():
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=stop_file")
                    break

                check_and_remove_stale_git_lock(repo)
                write_heartbeat(run_dir)

                # --- Pre-cycle quota utilization check (codex app-server) ---
                if quota_check_enabled:
                    q_action, q_info, q_reset_unix = check_codex_quota_utilization(
                        five_hour_max=quota_5h_max,
                        seven_day_max=quota_7d_max,
                    )
                    _q5h = q_info.get("five_hour", "N/A")
                    _q7d = q_info.get("seven_day", "N/A")
                    q_limit = str(q_info.get("max_used_limit_id", "") or "")
                    q_account = str(q_info.get("account_type", "") or "")
                    q_plan = str(q_info.get("plan_type", "") or "")

                    if q_action == "stop":
                        # 7-day hard limit exceeded - stop immediately (mirrors Claude backend)
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=quota_utilization_7d 5h={_q5h}% 7d={_q7d}%")
                        logger.stop_event(f"Codex 7-day quota {_q7d}% >= {quota_7d_max}% - stopping run. (5h={_q5h}%)")
                        metrics.event(
                            "quota_utilization_stop",
                            cycle=cycle_idx,
                            window="seven_day",
                            five_hour=_q5h,
                            seven_day=_q7d,
                            limit_id=q_limit,
                            resets_at_unix=int(q_reset_unix or 0),
                        )
                        last_reason = STOP_REASON_QUOTA_UTILIZATION
                        try:
                            stop_path.write_text(STOP_REASON_QUOTA_UTILIZATION, encoding="utf-8", errors="replace")
                        except Exception:
                            pass
                        break
                    elif q_action == "wait":
                        wait_sec = seconds_until_unix_reset(q_reset_unix)
                        if wait_sec <= 0:
                            wait_sec = 30  # minimum wait even if reset time already passed
                        # Failover ?먯젙: enabled + reason??failover_on???ы븿 + ?泥?諛깆뿏??議댁옱
                        _fo_enabled = bool(getattr(args, "failover_enabled", False))
                        _fo_on = set(str(x).strip().lower() for x in (getattr(args, "failover_on", []) or []))
                        _fo_backends = [str(b).strip().lower() for b in (getattr(args, "failover_backends", []) or []) if str(b).strip().lower() != "codex"]
                        _can_failover = _fo_enabled and STOP_REASON_QUOTA_UTILIZATION in _fo_on and len(_fo_backends) > 0
                        if _can_failover:
                            # Failover 媛????利됱떆 醫낅즺?섏뿬 runner_entry媛 ?ㅻⅨ 諛깆뿏?쒕줈 ?꾪솚
                            append_cycle_summary(
                                f"{now_iso()} cycle={cycle_idx} stop=quota_utilization_5h_failover "
                                f"5h={_q5h}% 7d={_q7d}% limit={q_limit or 'unknown'}"
                            )
                            logger.stop_event(
                                f"Codex 5h quota {_q5h}% >= {quota_5h_max}% - "
                                f"failover enabled, stopping for backend switch. (7d={_q7d}%)"
                            )
                            metrics.event(
                                "quota_utilization_failover",
                                cycle=cycle_idx,
                                window="five_hour",
                                five_hour=_q5h,
                                seven_day=_q7d,
                                limit_id=q_limit,
                                resets_at_unix=int(q_reset_unix or 0),
                            )
                            last_reason = STOP_REASON_QUOTA_UTILIZATION
                            try:
                                stop_path.write_text(STOP_REASON_QUOTA_UTILIZATION, encoding="utf-8", errors="replace")
                            except Exception:
                                pass
                            break
                        elif quota_wait_for_reset:
                            wait_min = wait_sec / 60
                            logger.info(
                                f"[QUOTA-WAIT] Codex 5h quota {_q5h}% >= {quota_5h_max}% "
                                f"(limit={q_limit or 'unknown'}) - waiting {wait_min:.1f}min for reset (7d={_q7d}%)"
                            )
                            logger.quota_event(
                                "wait",
                                five_hour=_q5h,
                                seven_day=_q7d,
                                wait_seconds=wait_sec,
                                backend="codex",
                                limit_id=q_limit,
                                account_type=q_account,
                                plan_type=q_plan,
                                resets_at_unix=q_reset_unix,
                            )
                            metrics.event(
                                "quota_utilization_wait",
                                cycle=cycle_idx,
                                window="five_hour",
                                five_hour=_q5h,
                                seven_day=_q7d,
                                limit_id=q_limit,
                                wait_seconds=wait_sec,
                                resets_at_unix=int(q_reset_unix or 0),
                            )
                            if await sleep_or_stop(wait_sec):
                                last_reason = STOP_REASON_STOP_FILE
                                logger.stop_event("Stop requested during quota wait.")
                                break
                            logger.info(f"[QUOTA-WAIT] Resumed after {wait_min:.1f}min wait - continuing cycle {cycle_idx}")
                            logger.quota_event("resumed", backend="codex", limit_id=q_limit)
                        else:
                            # quota_wait_for_reset=false - stop immediately
                            append_cycle_summary(
                                f"{now_iso()} cycle={cycle_idx} stop=quota_utilization_5h "
                                f"5h={_q5h}% 7d={_q7d}% limit={q_limit or 'unknown'}"
                            )
                            logger.stop_event(
                                f"Codex 5h quota {_q5h}% >= {quota_5h_max}% - "
                                f"quota_wait_for_reset disabled, stopping. (7d={_q7d}%)"
                            )
                            metrics.event(
                                "quota_utilization_stop",
                                cycle=cycle_idx,
                                window="five_hour",
                                five_hour=_q5h,
                                seven_day=_q7d,
                                limit_id=q_limit,
                                resets_at_unix=int(q_reset_unix or 0),
                            )
                            last_reason = STOP_REASON_QUOTA_UTILIZATION
                            try:
                                stop_path.write_text(STOP_REASON_QUOTA_UTILIZATION, encoding="utf-8", errors="replace")
                            except Exception:
                                pass
                            break
                    elif q_action == "ok":
                        logger.quota_event(
                            "ok",
                            five_hour=_q5h,
                            seven_day=_q7d,
                            backend="codex",
                            limit_id=q_limit,
                            account_type=q_account,
                            plan_type=q_plan,
                        )
                    else:
                        logger.quota_event("skip", backend="codex")

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
                # 1-line per-cycle summary for unattended ops
                print(f"[CYCLE] {now_iso()} idx={cycle_idx} rc={rc} reason={reason} progress_delta={delta}")

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
                        if str(b).strip().lower() != "codex"
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
                            stop_path.write_text(STOP_REASON_QUOTA, encoding="utf-8", errors="replace")
                        except Exception:
                            pass
                        break
                    if quota_wait_for_reset:
                        # Mid-cycle quota exhaustion: wait for reset then continue
                        wait_sec = 0
                        q_limit = ""
                        try:
                            _q_action, _q_info, _q_reset_unix = check_codex_quota_utilization(
                                five_hour_max=quota_5h_max,
                                seven_day_max=quota_7d_max,
                            )
                            q_limit = str(_q_info.get("max_used_limit_id", "") or "")
                            if _q_reset_unix is not None:
                                wait_sec = seconds_until_unix_reset(_q_reset_unix)
                        except Exception:
                            pass
                        if wait_sec <= 0:
                            # Fallback: 5 minutes minimum wait
                            wait_sec = max(300, loop_sleep_seconds * 5)
                        wait_min = wait_sec / 60
                        append_cycle_summary(
                            f"{now_iso()} cycle={cycle_idx} quota_exhausted_wait wait_min={wait_min:.1f} "
                            f"limit={q_limit or 'unknown'}"
                        )
                        eprint(
                            f"[QUOTA-WAIT] quota_exhausted - waiting {wait_min:.1f}min for reset "
                            f"(limit={q_limit or 'unknown'}, quota_wait_for_reset=true)"
                        )
                        logger.quota_event("exhausted_wait", wait_seconds=wait_sec, backend="codex", limit_id=q_limit)
                        metrics.event(
                            "quota_exhausted_wait",
                            cycle=cycle_idx,
                            wait_seconds=wait_sec,
                            window="codex",
                            limit_id=q_limit,
                        )
                        if stop_path.exists():
                            try:
                                stop_path.unlink()
                            except Exception:
                                pass
                        if await sleep_or_stop(wait_sec):
                            last_reason = STOP_REASON_STOP_FILE
                            logger.stop_event("Stop requested during quota exhaustion wait.")
                            break
                        eprint(f"[QUOTA-WAIT] Resumed after {wait_min:.1f}min wait - continuing next cycle")
                        logger.quota_event("exhausted_resumed", backend="codex", limit_id=q_limit)
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
                    if not args.loop:
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
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=all_tasks_attempted")
                    if not args.loop:
                        break
                    # In loop mode, fall through - PM refresh may add new tasks or retry skipped

                # --- Idle cycle tracking (cycle-count based) ---
                if delta <= 0:
                    idle_cycle_count += 1
                else:
                    idle_cycle_count = 0
                if idle_exit_cycles > 0 and idle_cycle_count >= idle_exit_cycles:
                    append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=idle_exit idle_cycles={idle_cycle_count}")
                    logger.stop_event(f"{idle_cycle_count} consecutive zero-progress cycles - idle exit.")
                    break

                if args.loop:
                    if delta <= 0:
                        idle_accum += int(args.loop_sleep_seconds)
                    else:
                        idle_accum = 0

                    if loop_idle_exit_after > 0 and idle_accum >= loop_idle_exit_after:
                        append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=idle_exit idle_accum={idle_accum}")
                        break

                    if await sleep_or_stop(max(0, int(args.loop_sleep_seconds))):
                        last_reason = STOP_REASON_STOP_FILE
                        break
                else:
                    break
        finally:
            detected_reason = ""
            try:
                detected_reason = detect_stop_reason([stop_path])
            except Exception:
                detected_reason = ""
            final_reason = choose_stop_reason([last_reason, detected_reason]) or last_reason
            if last_rc == 0 and final_reason in {"", "ok"}:
                try:
                    completion_status = ""
                    completion_payload = _load_json_if_exists(run_dir / "COMPLETION_STATUS.json", {})
                    if isinstance(completion_payload, dict):
                        completion_status = str(
                            completion_payload.get("completion_status")
                            or completion_payload.get("completionStatus")
                            or completion_payload.get("completion_reason")
                            or completion_payload.get("completionReason")
                            or ""
                        ).strip().lower()
                    if completion_status in {GOALS_INCOMPLETE_STATUS, STOP_REASON_PROJECT_COMPLETE}:
                        final_reason = completion_status
                        last_reason = completion_status
                except Exception:
                    pass
            report_path = run_dir / "SHUTDOWN_REPORT.md"
            if final_reason == STOP_REASON_STOP_FILE or not report_path.exists():
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
                    base_ref=source_base_ref or "HEAD",
                    auto_apply=False,
                    exclude_globs=exclude_globs,
                )
                pending_merge = run_dir / "WORKTREE_MERGE_PENDING.json"
                apply_failure = run_dir / "WORKTREE_APPLY_FAILURE.md"
                queue_result: dict[str, object] | None = None
                if pending_merge.exists():
                    try:
                        pending_payload = read_pending_worktree_merge(pending_merge)
                    except Exception:
                        pending_payload = {}
                    state_payload = {}
                    try:
                        state_payload = load_state(run_dir / "STATE.json")
                    except Exception:
                        state_payload = {}
                    done_task_ids = [
                        str(task_id).strip()
                        for task_id in list(state_payload.get("done", []) or [])
                        if str(task_id).strip()
                    ]
                    try:
                        queue_result = queue_review_packet(
                            source_repo,
                            run_id=run_dir.name,
                            task_ids=done_task_ids,
                            base_ref=str(pending_payload.get("base_ref") or source_base_ref or "HEAD"),
                            head_ref=str(pending_payload.get("head_ref") or git_head(source_repo)),
                            branch=str(pending_payload.get("branch") or pending_payload.get("source_branch") or ""),
                            source_head_before=source_base_ref,
                            source_head_after=git_head(source_repo),
                            worktree_dir=worktree_dir.as_posix(),
                            validation_status=locals().get("task_validation_status", "validation_pending") if last_rc == 0 else "validation_pending",
                            validation_artifacts=[
                                str(value).strip()
                                for value in (
                                    pending_payload.get("patch_path"),
                                    run_dir / "worktree.patch",
                                    pending_payload.get("pending_file"),
                                )
                                if value is not None and str(value).strip()
                            ],
                            qa_notes=[],
                            goal_trace=list(pending_payload.get("goal_trace") or pending_payload.get("goalTrace") or []),
                            changed_files=pending_payload.get("changed_files") or pending_payload.get("changedFiles") or [],
                            merge_preflight=pending_payload.get("preflight") or pending_payload.get("apply_check") or {},
                            status="pr_queued",
                        )
                    except Exception as _pq_ex:
                        queue_result = {
                            "ok": False,
                            "status": "pr_queue_failed",
                            "recoverable": False,
                            "recoverable_reason": str(_pq_ex),
                            "packet_path": "",
                            "branch_index_path": "",
                            "packet_id": "",
                        }
                        eprint(f"[WARN] Failed to queue review packet for isolated worktree: {_pq_ex}")
                        metrics.event(
                            "worktree_review_packet_failed",
                            run_id=run_dir.name,
                            error=str(_pq_ex),
                            worktree=worktree_dir.as_posix(),
                        )
                        if final_reason in {"", "ok"}:
                            final_reason = "pr_queue_failed"
                    else:
                        if queue_result.get("recoverable"):
                            final_reason = str(queue_result.get("status") or final_reason or "review_required")
                        eprint("")
                        eprint("[INFO] Review packet queued for isolated worktree.")
                        eprint(f" - packet:  {queue_result.get('packet_path')}")
                        eprint(f" - branch index: {queue_result.get('branch_index_path')}")
                        eprint(f" - worktree: {worktree_dir}")
                        if queue_result.get("recoverable"):
                            eprint(f" - status:   {queue_result.get('status')} ({queue_result.get('recoverable_reason') or 'recoverable'})")
                        eprint("")
                cleanup_result = dispatch_worktree_cleanup(
                    source_repo=source_repo,
                    worktree_dir=worktree_dir,
                    run_dir=run_dir,
                    should_remove=True,
                    remove_worktree_fn=remove_worktree,
                    eprint_fn=eprint,
                )
                if not cleanup_result.ok:
                    if last_rc == 0:
                        last_rc = 1
                    final_reason = cleanup_result.final_reason
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
                print(
                    f"[SHUTDOWN] reason={final_reason or 'ok'} cycles={len(run_summary['cycles'])} "
                    f"tasks={tasks_done}/{tasks_total} changes={change_count} run_dir={run_dir}{policy_part}"
                )
            except Exception:
                print(f"[SHUTDOWN] reason={final_reason or last_reason or 'ok'} run_dir={run_dir}")
            logger.close()

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






