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
from .docs import load_dotenv_best_effort, resolve_docs_dir, generate_docs_digest, read_text_robust
from .gates import run_build_gate_async, run_test_gate_async
from .gitops import (
    git_head,
    git_changed_files,
    git_worktree_changed_files,
    git_porcelain,
    repo_fingerprint,
    create_checkpoint,
    restore_checkpoint,
    RepoCheckpoint,
    list_untracked,
    create_worktree,
    remove_worktree,
    export_worktree_patch,
    apply_patch_to_repo,
)
from .inventory import build_repo_inventory, write_repo_inventory_files
from .todo import read_current_todo, format_todo_block
from .metrics import MetricsLogger
from .policy import load_policy_rules, policy_scan_text
from .prompts import (
    PromptStore,
    ensure_pm_instructions_have_output_schema,
    append_pm_output_contract,
    codex_call_hint,
    PM_BOOTSTRAP_TEMPLATE_DEFAULT,
    PM_INCREMENTAL_TEMPLATE_DEFAULT,
    DEV_TASK_TEMPLATE_DEFAULT,
    QA_TEMPLATE_DEFAULT,
    PM_INSTRUCTIONS_DEFAULT,
    DEV_INSTRUCTIONS_DEFAULT,
    QA_INSTRUCTIONS_DEFAULT,
    REPORTER_INSTRUCTIONS_DEFAULT,
    PM_SHUTDOWN_REPORT_TEMPLATE_DEFAULT,
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
from .utils import force_utf8_stdio, eprint, now_iso, run_cmd, safe_write_text
from .schemas import PMOutputV2
from .structured import parse_pm_output, dump_pretty, describe_parse_failure
from .tracing import TraceCtx, new_trace_id

from .pipeline import PipelineManager, make_stages
from .pipeline.session import PipelineSession
from .pipeline.stages.base import StageOutcome


def _load_json_if_exists(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return default
    return default


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

    # Ensure tools run inside repo
    os.chdir(repo)

    # Observability
    metrics = MetricsLogger(run_dir / "metrics.jsonl")
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
    digest_rel = digest_path.relative_to(repo).as_posix() if repo in digest_path.parents else digest_path.as_posix()

    if args.docs_read_mode == "digest":
        if args.generate_digest and docs_dir:
            generate_docs_digest(repo, docs_dir, digest_path)
        elif not digest_path.exists() and docs_dir:
            generate_docs_digest(repo, docs_dir, digest_path)

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
    policy_scan_enabled = not bool(getattr(args, "no_policy_scan", False))
    policy_rules = load_policy_rules(getattr(args, "policy_rules_file", ""), list(getattr(args, "policy_rule", []) or []))

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
            """Detect OpenAI/SDK quota/billing exhaustion to exit gracefully."""
            needles = (
                "insufficient_quota",
                "quota exceeded",
                "exceeded your current quota",
                "billing hard limit",
                "hard limit",
                "plan and billing",
                "payment required",
                # Codex/CLI style usage-limit strings
                "you've hit your usage limit",
                "purchase more credits",
                "upgrade to pro",
                "codex/settings/usage",
            )
            for e in _iter_exc_chain(ex):
                try:
                    msg = (str(e) or "").lower()
                except Exception:
                    msg = ""
                rep = (repr(e) or "").lower()
                if any(n in msg for n in needles) or any(n in rep for n in needles):
                    return True
            return False




        def is_quota_text(text: str) -> bool:
            s = (text or "").lower()
            if not s:
                return False
            return (
                "you've hit your usage limit" in s
                or "purchase more credits" in s
                or "upgrade to pro" in s
                or "codex/settings/usage" in s
                or ("usage limit" in s and "try again at" in s)
            )

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


        async def _run_with_continuations(agent_obj, prompt: str, max_turns: int, *, label: str, timeout_sec: int = 0, max_continuations: int = 0) -> Any:
            """Run an agent, optionally continuing if a max-turns exception occurs.

            Notes:
            - max_continuations controls how many *additional* Runner.run calls we will attempt after a MaxTurnsExceeded-style failure.
            - We detect turn-caps both by exception message and by exception class name (for SDK variations).
            """
            cont_left = int(max_continuations or 0)

            while True:
                try:
                    if timeout_sec and timeout_sec > 0:
                        return await asyncio.wait_for(Runner.run(agent_obj, prompt, max_turns=max_turns), timeout=timeout_sec)
                    return await Runner.run(agent_obj, prompt, max_turns=max_turns)
                except Exception as ex:
                    if cont_left > 0 and is_max_turns_exception(ex):
                        cont_left -= 1
                        prompt = (
                            prompt
                            + f"\n\n[CONTINUE] You hit a turn limit previously while running '{label}'. Continue EXACTLY from where you left off.\n"
                              "- Do NOT restate a plan.\n"
                              "- Do NOT summarize.\n"
                              "- Apply changes now (call tools / edit files).\n"
                              "- End with only the required output."
                        )
                        continue
                    raise
        async def _run_pm_structured(pm_prompt: str, *, max_turns: int, cycle_idx: int, kind: str, output_path: Path) -> PMOutputV2 | None:
            """Run PM and validate its final output against PMOutputV2 schema."""
            retries = int(getattr(args, "pm_structured_retries", 2))
            max_cont = int(getattr(args, "pm_max_turns_continuations", 0))
            last_raw = ""
            repair_prompt = ""
            for attempt in range(retries + 1):
                prompt = pm_prompt if attempt == 0 else repair_prompt
                res = await _run_with_continuations(
                    pm,
                    prompt,
                    max_turns=max_turns,
                    label=f"pm_{kind}",
                    timeout_sec=int(getattr(args, "pm_timeout_seconds", 0)) or 0,
                    max_continuations=max_cont,
                )
                last_raw = (getattr(res, "final_output", "") or "").strip()
                try:
                    output_path.write_text(last_raw + "\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass

                parsed = parse_pm_output(last_raw, kind_hint=kind)
                if parsed is not None:
                    return parsed

                repair_prompt = (
                    "Your previous response was invalid or did not match the required JSON schema. "
                    "Return ONLY a single JSON object with keys: kind, summary, tasks, notes_md, warnings, open_questions, analysis_updated, analysis_path. "
                    "No markdown, no prose outside JSON.\n\n"
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
            lines: list[str] = []
            for t in tasks:
                mark = "x" if t.id in done_ids else " "
                lines.append(f"- [{mark}] {t.id} {t.title}")

            block = "\n".join(lines) if lines else "(no backlog found)"
            return block, tasks, done_ids

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
                out.append(
                    {
                        "id": fixed_id,
                        "title": str(t.get("title") or fixed_id).strip() or fixed_id,
                        "prompt": str(t.get("prompt") or "").strip() or f"Implement {fixed_id}.",
                        "files": t.get("files") if isinstance(t.get("files"), list) else [],
                        "done_when": str(t.get("done_when") or "Git diff exists and build passes.").strip(),
                    }
                )
            return out

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

            try:
                if need_bootstrap:
                    metrics.event("pm_start", cycle=cycle_idx, kind="bootstrap")
                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "todo_block": todo_block,
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "codex_call_hint": codex_call_hint(autopilot),
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
                                }
                            )

                    if merged_tasks:
                        merged_tasks = _normalize_backlog_tasks(merged_tasks)
                        if merged_tasks:
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

                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "todo_block": todo_block,
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "codex_call_hint": codex_call_hint(autopilot),
                        "prev_head": prev_head or curr_head,
                        "curr_head": curr_head,
                        "changed_files_block": changed_files_block,
                        "current_backlog_block": current_backlog_block,
                        "hint_block": hint_block,
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
                                }
                            )

                    if merged_tasks:
                        merged_tasks = _normalize_backlog_tasks(merged_tasks)
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
                    pm_stop_reason["reason"] = "quota_exhausted"
                    try:
                        stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                    except Exception:
                        pass
                    metrics.event("runner_stop", cycle=cycle_idx, reason="quota_exhausted")
                    try:
                        await write_shutdown_report("quota_exhausted", cycle=cycle_idx, step=-1)
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

        async def run_qa_if_needed(cycle_idx: int, ran_tasks: bool) -> None:
            if stop_path.exists():
                return
            if not (args.qa_always or ran_tasks):
                metrics.event("qa_skip", cycle=cycle_idx, reason="no_progress")
                return
            try:
                metrics.event("qa_start", cycle=cycle_idx)
                ctx = {"repo": str(repo), "run_dir": str(run_dir)}
                qa_prompt = store.render("qa_prompt", QA_TEMPLATE_DEFAULT, ctx)
                qa_result = await Runner.run(qa, qa_prompt, max_turns=10)
                (run_dir / f"qa_final_output_cycle_{cycle_idx:03d}.txt").write_text(
                    (qa_result.final_output or "") + "\n", encoding="utf-8", errors="replace"
                )
                metrics.event("qa_end", cycle=cycle_idx, rc=0)
            except Exception as ex:
                metrics.event("qa_end", cycle=cycle_idx, rc=1, error=str(ex))

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
            # Dev loop
            state_path = run_dir / "STATE.json"
            backlog_md = run_dir / "BACKLOG.md"
            state = load_state(state_path)
            done_set = set(state.get("done", []))

            task_ids = {t.id for t in tasks}
            before_done = len(done_set.intersection(task_ids))

            if pm_stage_enabled and args.pm_refresh_backlog and (before_done >= len(task_ids)):
                pm_ok2 = await run_pm_if_needed(cycle_idx, curr_head, changed_files, repo_fp, force_refresh_backlog=True)
                if not pm_ok2:
                    if pm_stop_reason.get("reason") == "quota_exhausted" or stop_path.exists():
                        return 0, "quota_exhausted", 0, (len(done_set) > before_done)
                    return 1, "pm_failed", 0, (len(done_set) > before_done)
                ensure_backlog()
                tasks = load_tasks()
                task_ids = {t.id for t in tasks}
                before_done = len(done_set.intersection(task_ids))

            tasks_root = run_dir / "tasks"
            tasks_root.mkdir(parents=True, exist_ok=True)

            for step in range(int(args.iterations)):
                if stop_path.exists():
                    break

                next_task: Optional[TaskItem] = None
                for t in tasks:
                    if t.id not in done_set:
                        next_task = t
                        break
                if not next_task:
                    break

                task_dir = tasks_root / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}"
                task_dir.mkdir(parents=True, exist_ok=True)

                metrics.event("task_start", cycle=cycle_idx, step=step, task_id=next_task.id)

                cp: Optional[RepoCheckpoint] = None
                if args.isolate_task:
                    metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id)
                    cp = create_checkpoint(repo, task_dir / "checkpoint")
                    metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0)

                before = git_porcelain(repo)

                analysis_hint_out = dev_hints_dir / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}.md"
                # Dev model tiering (cost saver): base -> tier1 -> tier2 (best-effort)
                dev_auto_escalate = bool(getattr(args, "dev_auto_escalate", False))
                dev_max_escalations = int(getattr(args, "dev_max_escalations", 0) or 0)
                dev_escalate_on = set(getattr(args, "dev_escalate_on", []) or [])

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
                if dev_auto_escalate and not cp:
                    metrics.event("checkpoint_start", cycle=cycle_idx, step=step, task_id=next_task.id, reason="retry_escalation")
                    cp = create_checkpoint(repo, task_dir / "checkpoint")
                    metrics.event("checkpoint_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, reason="retry_escalation")

                task_completed = False

                def _restore_or_stop(reason: str) -> tuple[bool, str]:
                    if not cp:
                        return True, ""
                    try:
                        restore_checkpoint(
                            repo,
                            cp,
                            dangerous=bool(getattr(args, "dangerous_git_rollback", False)),
                            run_dir=run_dir,
                            stop_path=stop_path,
                        )
                        metrics.event("rollback", cycle=cycle_idx, step=step, task_id=next_task.id, reason=reason)
                        return True, ""
                    except Exception as ex:
                        detail = str(ex)
                        blocked = "blocked" in detail.lower()
                        fail_reason = "rollback_blocked" if blocked else "rollback_failed"
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": fail_reason, "detail": detail})
                        save_state(state_path, state)
                        metrics.event("rollback_failed", cycle=cycle_idx, step=step, task_id=next_task.id, reason=reason, detail=detail)
                        eprint(f"[STOP] Rollback {fail_reason}: {detail}")
                        return False, fail_reason

                for attempt in range(max_attempts):
                    if stop_path.exists():
                        break

                    # Restore baseline before retries
                    if attempt > 0 and cp:
                        ok, fail_reason = _restore_or_stop("retry")
                        if not ok:
                            return 1, fail_reason, 0, (len(done_set) > before_done)

                    model_name = tiers[attempt]
                    dev_agent = dev if attempt == 0 else make_dev_agent(model_name)

                    attempt_dir = task_dir / f"attempt_{attempt:02d}"
                    attempt_dir.mkdir(parents=True, exist_ok=True)

                    before = git_porcelain(repo)

                    analysis_hint_out = dev_hints_dir / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.md"

                    files_hint = "\n".join([f"- {f}" for f in (next_task.files or [])]) or "- (unspecified)"
                    ctx = {
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "task_id": next_task.id,
                        "task_title": next_task.title,
                        "task_prompt": next_task.prompt,
                        "files_hint": files_hint,
                        "done_when": next_task.done_when or "(unspecified)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "analysis_hint_out": str(analysis_hint_out),
                        "codex_call_hint": codex_call_hint(autopilot),
                    }
                    dev_prompt = store.render("dev_task_prompt", DEV_TASK_TEMPLATE_DEFAULT, ctx)

                    metrics.event("dev_attempt_start", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, model=model_name)

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
                        )
                        dev_final = (dev_result.final_output or "")
                    except Exception as ex:
                        dev_exc = ex
                        dev_final = ""
                        dev_is_max_turns = is_max_turns_exception(ex)
                        dev_quota_exhausted = is_quota_exception(ex)
                        eprint(f"[DEV ERROR] {ex}")
                        if bool(getattr(args, "debug", False)):
                            eprint(traceback.format_exc())

                    # Always persist whatever we have (even on exceptions)
                    dev_log = (dev_final or "")
                    if dev_exc:
                        dev_log += "\n[EXCEPTION]\n" + str(dev_exc) + "\n"

                    (attempt_dir / "dev_output.txt").write_text(dev_log + "\n", encoding="utf-8", errors="replace")
                    (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)
                    (run_dir / "dev_logs" / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}_a{attempt:02d}.txt").write_text(
                        dev_log + "\n", encoding="utf-8", errors="replace"
                    )

                    # Quota/credits exhausted: graceful stop with artifacts preserved.
                    dev_quota_exhausted = dev_quota_exhausted or is_quota_text(dev_log)
                    if dev_quota_exhausted:
                        state.setdefault("warnings", []).append(
                            {"task": next_task.id, "reason": "quota_exhausted", "detail": str(dev_exc) if dev_exc else "usage limit"}
                        )
                        save_state(state_path, state)
                        metrics.event("runner_stop", cycle=cycle_idx, step=step, task_id=next_task.id, reason="quota_exhausted")
                        try:
                            stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                        except Exception:
                            pass
                        try:
                            await write_shutdown_report("quota_exhausted", cycle=cycle_idx, step=step, last_task_id=next_task.id)
                        except Exception:
                            pass
                        return 0, "quota_exhausted", 0, (len(done_set) > before_done)
                    # Invalid/unknown model: allow escalation fallback when available.
                    if dev_exc and is_model_invalid_exception(dev_exc):
                        if (attempt + 1) < max_attempts:
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="model_invalid")
                            continue

                    # Non-max-turn exceptions are treated as fatal (rollback + stop)
                    if dev_exc and not dev_is_max_turns:
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "exception", "detail": str(dev_exc)})
                        save_state(state_path, state)
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="exception")
                        if cp:
                            ok, fail_reason = _restore_or_stop("exception")
                            if not ok:
                                return 1, fail_reason, 0, (len(done_set) > before_done)
                        return 1, "dev_exception", 0, (len(done_set) > before_done)
                    # Max-turns exceptions are recoverable: continue to diff/build gates.
                    if dev_exc and dev_is_max_turns:
                        state.setdefault("warnings", []).append({"task": next_task.id, "reason": "max_turns_exceeded", "detail": str(dev_exc)})
                        save_state(state_path, state)
                        metrics.event("task_warn", cycle=cycle_idx, step=step, task_id=next_task.id, reason="max_turns_exceeded")

                    after = git_porcelain(repo)
                    changed = (before != after)

                    # Escalate conditions: retry same task with a higher tier model.
                    if stop_on_no_diff and (not changed):
                        if dev_auto_escalate and (attempt + 1) < max_attempts and "no_diff" in dev_escalate_on:
                            metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="no_diff")
                            continue
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "no_diff"})
                        save_state(state_path, state)
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="no_diff")
                        eprint(f"[STOP] No diff produced for {next_task.id}.")
                        if cp:
                            ok, fail_reason = _restore_or_stop("no_diff")
                            if not ok:
                                return 1, fail_reason, 0, (len(done_set) > before_done)
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
                        if not ok:
                            if dev_auto_escalate and (attempt + 1) < max_attempts and "build_failed" in dev_escalate_on:
                                metrics.event("dev_attempt_retry", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, reason="build_failed")
                                continue
                            state.setdefault("failed", []).append({"task": next_task.id, "reason": "build_failed"})
                            save_state(state_path, state)
                            eprint(f"[STOP] Build failed after {next_task.id}. See {attempt_dir / 'build.txt'}")
                            if cp:
                                ok_restore, fail_reason = _restore_or_stop("build_failed")
                                if not ok_restore:
                                    return 1, fail_reason, 0, (len(done_set) > before_done)
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
                            eprint(f"[STOP] Tests failed after {next_task.id}. See {attempt_dir / 'test.txt'}")
                            if cp:
                                ok_restore, fail_reason = _restore_or_stop("test_failed")
                                if not ok_restore:
                                    return 1, fail_reason, 0, (len(done_set) > before_done)
                            return 1, "test_failed", 0, (len(done_set) > before_done)
                    if policy_scan_enabled:
                        code, diff_text = run_cmd(["git", "diff"], cwd=repo, timeout_sec=120)
                        scan_payload = diff_text if code == 0 else ""
                        for rel in list_untracked(repo)[:50]:
                            pth = repo / rel
                            try:
                                if pth.exists() and pth.is_file() and pth.stat().st_size < 2_000_000:
                                    txt, _enc = read_text_robust(pth)
                                    scan_payload += "\n\n# FILE: " + rel + "\n" + txt[:200_000]
                            except Exception:
                                pass

                        scan_result = policy_scan_text(scan_payload, policy_rules)
                        (attempt_dir / "policy_scan.json").write_text(json.dumps(scan_result, ensure_ascii=False, indent=2),
                                                                 encoding="utf-8", errors="replace")
                        (run_dir / "policy_scan.json").write_text(
                            json.dumps({"cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False, indent=2),
                            encoding="utf-8", errors="replace"
                        )
                        try:
                            with (run_dir / "policy_scan_history.jsonl").open("a", encoding="utf-8", errors="replace") as f:
                                f.write(json.dumps({"ts": now_iso(), "cycle": cycle_idx, "step": step, "task_id": next_task.id, **scan_result}, ensure_ascii=False) + "\n")
                        except Exception:
                            pass

                        if not scan_result.get("ok", True):
                            state.setdefault("failed", []).append({"task": next_task.id, "reason": "policy_violation"})
                            save_state(state_path, state)
                            eprint(f"[STOP] Policy scan failed after {next_task.id}. See {attempt_dir / 'policy_scan.json'}")
                            metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="policy_violation",
                                          violations=len(scan_result.get("violations", [])))
                            if cp:
                                ok_restore, fail_reason = _restore_or_stop("policy_violation")
                                if not ok_restore:
                                    return 1, fail_reason, 0, (len(done_set) > before_done)
                            return 1, "policy_violation", 0, (len(done_set) > before_done)
                    # Success: exit attempt loop
                    metrics.event("dev_attempt_end", cycle=cycle_idx, step=step, task_id=next_task.id, attempt=attempt, rc=0)
                    task_completed = True
                    break

                if not task_completed:
                    # No success after attempts and not otherwise returned: treat as failure.
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "exhausted_attempts"})
                    save_state(state_path, state)
                    return 1, "exhausted_attempts", 0, (len(done_set) > before_done)
                # Mark done only after gates
                done_set.add(next_task.id)
                state["done"] = sorted(list(done_set))
                save_state(state_path, state)
                mark_backlog_done(backlog_md, next_task.id)

                (run_dir / "progress.txt").write_text(f"done={len(done_set)}/{len(tasks)} last={next_task.id}\n",
                                                     encoding="utf-8", errors="replace")

                code, names = run_cmd(["git", "diff", "--name-only"], cwd=repo, timeout_sec=60)
                files_changed_count = len([ln for ln in names.splitlines() if ln.strip()]) if code == 0 else 0
                metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0, files_changed_count=files_changed_count)

            try:
                merge_dev_hints_to_global_changelog(analysis_md, dev_hints_dir, curr_head)
            except Exception:
                pass

            ran_tasks = (len(done_set) > before_done)

            cycle_dt = time.time() - cycle_t0
            failed_count = len(state.get("failed", []))
            summary = {
                "ts": now_iso(),
                "cycle": cycle_idx,
                "run_dir": str(run_dir),
                "done": len(done_set),
                "total_tasks": len(tasks),
                "failed_count": failed_count,
                "duration_seconds": cycle_dt,
                "build_enabled": build_enabled,
                "run_tests": run_tests,
                "policy_scan_enabled": policy_scan_enabled,
            }
            last_run_summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")
            done_count = len(done_set.intersection(task_ids))
            total_count = len(task_ids)
            append_cycle_summary(f"{now_iso()} cycle={cycle_idx} done={done_count}/{total_count} failed={failed_count} dt={cycle_dt:.1f}s")
            metrics.event("cycle_end", cycle=cycle_idx, rc=0, done=done_count, total=total_count, failed=failed_count, duration_seconds=cycle_dt)

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

            if total_count > 0 and done_count >= total_count:
                return 0, "all_tasks_done", done_delta, ran_tasks

            return 0, "ok", done_delta, ran_tasks


        async def run_cycle(cycle_idx: int) -> tuple[int, str, int]:
            nonlocal prev_head

            if stop_path.exists():
                return 0, "stop_file", 0

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
                    if pm_stop_reason.get("reason") == "quota_exhausted" or stop_path.exists():
                        metrics.event("pm_stage_end", cycle=ci, rc=0, reason="quota_exhausted")
                        return StageOutcome.stop("quota_exhausted", rc=0)
                    metrics.event("pm_stage_end", cycle=ci, rc=1)
                    return StageOutcome.fail("pm_failed", rc=1)
                metrics.event("pm_stage_end", cycle=ci, rc=0)
                return StageOutcome.ok("pm_ok")

            async def security_phase(ci: int) -> StageOutcome:
                # Placeholder for optional preflight security checks.
                # Built-in policy scan runs during Dev gates; this stage is here for extensibility.
                return StageOutcome.skip("security_not_configured")

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

                if reason == "stop_file":
                    return StageOutcome.stop("stop_file", rc=0)
                if rc != 0:
                    return StageOutcome.fail(reason, rc=rc)
                return StageOutcome.ok(reason)

            async def qa_phase(ci: int) -> StageOutcome:
                if stop_path.exists():
                    return StageOutcome.stop("stop_file")
                await run_qa_if_needed(ci, ran_tasks=session.ran_tasks)
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
                                    "ts": datetime.utcnow().isoformat() + "Z",
                                },
                                indent=2,
                                sort_keys=True,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        prev_head = final_head
                except Exception as ex:
                    eprint(f"[WARN] snapshot update failed: {ex}")

            return res.rc, res.reason, res.done_delta

        idle_accum = 0
        last_rc = 0
        last_reason = ""
        cycles = 1 if not args.loop else (args.loop_max_cycles if args.loop_max_cycles and args.loop_max_cycles > 0 else 10**9)

        for cycle_idx in range(int(cycles)):
            if stop_path.exists():
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=stop_file")
                break

            rc, reason, delta = await run_cycle(cycle_idx)
            last_rc = rc
            last_reason = reason
            # 1-line per-cycle summary for unattended ops
            print(f"[CYCLE] {now_iso()} idx={cycle_idx} rc={rc} reason={reason} progress_delta={delta}")

            if rc != 0:
                break

            if reason == "quota_exhausted":
                break

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
        if worktree_dir is not None:
            if last_rc == 0 and last_reason not in {"stop_file", "quota_exhausted"}:
                try:
                    patch_path = run_dir / "worktree.patch"
                    export_worktree_patch(repo, patch_path)
                    if patch_path.read_text(encoding="utf-8", errors="replace").strip():
                        apply_patch_to_repo(source_repo, patch_path)
                except Exception as ex:
                    safe_write_text(run_dir / "WORKTREE_APPLY_FAILURE.md", f"# Worktree apply failure\n\n{ex}\n")
                    last_rc = 1
            try:
                remove_worktree(source_repo, worktree_dir)
            except Exception as ex:
                eprint(f"[WARN] Failed to remove worktree: {ex}")

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
