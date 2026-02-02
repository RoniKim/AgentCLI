from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional, Any

from .analysis_cache import merge_dev_hints_to_global_changelog
from .docs import load_dotenv_best_effort, resolve_docs_dir, generate_docs_digest, read_text_robust
from .gates import dotnet_build, dotnet_test
from .gitops import (
    git_head,
    git_changed_files,
    git_porcelain,
    repo_fingerprint,
    create_checkpoint,
    restore_checkpoint,
    RepoCheckpoint,
    list_untracked,
)
from .inventory import build_repo_inventory, write_repo_inventory_files
from .metrics import MetricsLogger
from .policy import load_policy_rules, policy_scan_text
from .prompts import (
    PromptStore,
    codex_call_hint,
    PM_BOOTSTRAP_TEMPLATE_DEFAULT,
    PM_INCREMENTAL_TEMPLATE_DEFAULT,
    DEV_TASK_TEMPLATE_DEFAULT,
    QA_TEMPLATE_DEFAULT,
    PM_INSTRUCTIONS_DEFAULT,
    DEV_INSTRUCTIONS_DEFAULT,
    QA_INSTRUCTIONS_DEFAULT,
)
from .run_dir import make_run_dir
from .state import (
    TaskItem,
    load_backlog_json,
    parse_backlog_md,
    load_state,
    save_state,
    mark_backlog_done,
    write_default_p0_backlog,
)
from .utils import force_utf8_stdio, eprint, now_iso, run_cmd


def _load_json_if_exists(path: Path, default: Any) -> Any:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return default
    return default


async def main_async(args: argparse.Namespace) -> int:
    force_utf8_stdio()

    try:
        from agents import Agent, Runner, set_default_openai_api
        from agents.mcp import MCPServerStdio
        from agents.extensions.handoff_prompt import RECOMMENDED_PROMPT_PREFIX
    except ImportError:
        eprint("Missing dependency: openai-agents. Install: pip install -U openai-agents openai")
        return 2

    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        eprint(f"Repo not found: {repo}")
        return 2

    # Load env (.env) BEFORE chdir(repo) so .env in agent folder also works
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
    set_default_openai_api(api_key)

    # Ensure tools run inside repo
    os.chdir(repo)

    # Run dir (resume or new). In --loop mode, run_dir must be reused for the whole session.
    run_dir = Path(args.run_dir).expanduser().resolve() if args.run_dir else make_run_dir(repo)
    run_dir.mkdir(parents=True, exist_ok=True)

    # Observability
    metrics = MetricsLogger(run_dir / "metrics.jsonl")
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

    # Snapshot (HEAD tracking)
    snapshot_json = pm_cache_dir / "REPO_SNAPSHOT.json"
    snapshot = _load_json_if_exists(snapshot_json, default={"head": "", "updated_at": ""})
    prev_head = (snapshot.get("head") or "").strip()

    # Dev hints dir (run-local)
    dev_hints_dir = run_dir / "analysis_hints"
    dev_hints_dir.mkdir(parents=True, exist_ok=True)

    # Ensure continuous in loop mode
    continuous = bool(args.continuous or args.loop)

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

        pm_instructions = store.get("pm_instructions", PM_INSTRUCTIONS_DEFAULT)
        dev_instructions = store.get("dev_instructions", DEV_INSTRUCTIONS_DEFAULT)
        qa_instructions = store.get("qa_instructions", QA_INSTRUCTIONS_DEFAULT)

        pm = Agent(
            name="Project_Manager",
            model=args.pm_model,
            mcp_servers=[codex_mcp_server],
            instructions=f"{RECOMMENDED_PROMPT_PREFIX}\n{pm_instructions}".strip(),
        )

        dev = Agent(
            name="MAUI_Developer",
            model=args.dev_model,
            mcp_servers=[codex_mcp_server],
            instructions=f"{RECOMMENDED_PROMPT_PREFIX}\n{dev_instructions}".strip(),
        )

        qa = Agent(
            name="QA",
            model=args.qa_model,
            mcp_servers=[codex_mcp_server],
            instructions=f"{RECOMMENDED_PROMPT_PREFIX}\n{qa_instructions}".strip(),
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
            inventory = build_repo_inventory(repo)
            _, inv_md = write_repo_inventory_files(repo, pm_cache_dir, inventory)

            try:
                if need_bootstrap:
                    metrics.event("pm_start", cycle=cycle_idx, kind="bootstrap")
                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "codex_call_hint": codex_call_hint(autopilot),
                    }
                    pm_prompt = store.render("pm_bootstrap_prompt", PM_BOOTSTRAP_TEMPLATE_DEFAULT, ctx)
                    pm_result = await Runner.run(pm, pm_prompt, max_turns=args.pm_bootstrap_max_turns)
                    pm_output_path.write_text((pm_result.final_output or "") + "\n", encoding="utf-8", errors="replace")
                    last_pm_fp = repo_fp or last_pm_fp
                    pm_fp_path.write_text(json.dumps({"fingerprint": last_pm_fp, "updated_at": now_iso()}, ensure_ascii=False, indent=2),
                                          encoding="utf-8", errors="replace")
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

                    ctx = {
                        "analysis_md": str(analysis_md),
                        "inv_md": str(inv_md),
                        "repo": str(repo),
                        "run_dir": str(run_dir),
                        "docs_dir": str(docs_dir) if docs_dir else "(none)",
                        "docs_read_mode": str(args.docs_read_mode),
                        "digest_rel": str(digest_rel),
                        "codex_call_hint": codex_call_hint(autopilot),
                        "prev_head": prev_head or curr_head,
                        "curr_head": curr_head,
                        "changed_files_block": changed_files_block,
                        "hint_block": hint_block,
                    }
                    pm_prompt = store.render("pm_incremental_prompt", PM_INCREMENTAL_TEMPLATE_DEFAULT, ctx)
                    pm_result = await Runner.run(pm, pm_prompt, max_turns=args.pm_incremental_max_turns)
                    pm_output_path.write_text((pm_result.final_output or "") + "\n", encoding="utf-8", errors="replace")
                    last_pm_fp = repo_fp or last_pm_fp
                    pm_fp_path.write_text(json.dumps({"fingerprint": last_pm_fp, "updated_at": now_iso()}, ensure_ascii=False, indent=2),
                                          encoding="utf-8", errors="replace")
                    metrics.event("pm_end", cycle=cycle_idx, kind="incremental" if need_incremental else "refresh", rc=0)
                    return True

                metrics.event("pm_skip", cycle=cycle_idx)
                return True
            except Exception as ex:
                eprint(f"[PM ERROR] {ex}")
                metrics.event("pm_end", cycle=cycle_idx, rc=1, error=str(ex))
                return False

        def ensure_backlog() -> None:
            backlog_json = run_dir / "BACKLOG.json"
            backlog_md = run_dir / "BACKLOG.md"
            if not backlog_json.exists() and not backlog_md.exists():
                eprint("[PM WARNING] BACKLOG not created by PM. Creating default P0 backlog to continue.")
                write_default_p0_backlog(run_dir)

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

        async def run_cycle(cycle_idx: int) -> tuple[int, str, int]:
            """Returns (rc, reason, done_count_delta)."""
            nonlocal prev_head

            if stop_path.exists():
                return 0, "stop_file", 0

            cycle_t0 = time.time()
            metrics.event("cycle_start", cycle=cycle_idx)

            curr_head = git_head(repo).strip()
            head_changed_files = git_changed_files(repo, prev_head, curr_head)
            wt_changed_files: list[str] = []
            if args.pm_include_working_tree:
                for ln in git_porcelain(repo).splitlines():
                    ln = ln.strip()
                    if not ln:
                        continue
                    if ln.startswith("?? "):
                        wt_changed_files.append(ln[3:].strip())
                    elif " -> " in ln:
                        wt_changed_files.append(ln.split("->", 1)[1].strip())
                    else:
                        wt_changed_files.append(ln[3:].strip())
                wt_changed_files = [p for p in wt_changed_files if p]

            changed_files = sorted(set([*head_changed_files, *wt_changed_files]))
            repo_fp = repo_fingerprint(repo)

            # PM phase
            pm_ok = await run_pm_if_needed(cycle_idx, curr_head, changed_files, repo_fp, force_refresh_backlog=False)
            if not pm_ok:
                return 1, "pm_failed", 0

            # Update snapshot head only when HEAD changes
            if curr_head:
                snapshot_json.write_text(
                    json.dumps({"head": curr_head, "updated_at": now_iso()}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                    errors="replace",
                )
                prev_head = curr_head

            ensure_backlog()
            tasks = load_tasks()
            if not tasks:
                eprint("No tasks parsed from backlog. Stopping.")
                return 1, "no_tasks", 0

            if not continuous and not args.loop:
                print(f"[OK] Run artifacts: {run_dir}")
                print("PM/backlog prepared. Re-run with --continuous to execute tasks automatically.")
                metrics.event("cycle_end", cycle=cycle_idx, rc=0, reason="prepared_only")
                return 0, "prepared_only", 0

            # Dev loop
            state_path = run_dir / "STATE.json"
            backlog_md = run_dir / "BACKLOG.md"
            state = load_state(state_path)
            done_set = set(state.get("done", []))
            before_done = len(done_set)

            if args.pm_refresh_backlog and (before_done >= len(tasks)):
                pm_ok2 = await run_pm_if_needed(cycle_idx, curr_head, changed_files, repo_fp, force_refresh_backlog=True)
                if not pm_ok2:
                    return 1, "pm_failed", 0
                ensure_backlog()
                tasks = load_tasks()

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

                try:
                    dev_result = await Runner.run(dev, dev_prompt, max_turns=args.max_turns_per_task)
                    (task_dir / "dev_output.txt").write_text(
                        (dev_result.final_output or "") + "\n", encoding="utf-8", errors="replace"
                    )
                    (run_dir / "dev_logs").mkdir(parents=True, exist_ok=True)
                    (run_dir / "dev_logs" / f"c{cycle_idx:03d}_s{step:03d}_{next_task.id}.txt").write_text(
                        (dev_result.final_output or "") + "\n", encoding="utf-8", errors="replace"
                    )
                except Exception as ex:
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "exception", "detail": str(ex)})
                    save_state(state_path, state)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="exception")
                    eprint(f"[DEV ERROR] {ex}")
                    if cp:
                        restore_checkpoint(repo, cp)
                        metrics.event("rollback", cycle=cycle_idx, step=step, task_id=next_task.id, reason="exception")
                    return 1, "dev_exception", 0

                after = git_porcelain(repo)
                changed = (before != after)

                if stop_on_no_diff and (not changed):
                    state.setdefault("failed", []).append({"task": next_task.id, "reason": "no_diff"})
                    save_state(state_path, state)
                    metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="no_diff")
                    eprint(f"[STOP] No diff produced for {next_task.id}.")
                    if cp:
                        restore_checkpoint(repo, cp)
                        metrics.event("rollback", cycle=cycle_idx, step=step, task_id=next_task.id, reason="no_diff")
                    return 1, "no_diff", 0

                if build_enabled:
                    metrics.event("build_start", cycle=cycle_idx, step=step, task_id=next_task.id)
                    ok = dotnet_build(repo=repo, build_target=args.dotnet_build_target, log_path=task_dir / "dotnet_build.txt")
                    metrics.event("build_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0 if ok else 1)
                    if not ok:
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "build_failed"})
                        save_state(state_path, state)
                        eprint(f"[STOP] Build failed after {next_task.id}. See {task_dir / 'dotnet_build.txt'}")
                        if cp:
                            restore_checkpoint(repo, cp)
                            metrics.event("rollback", cycle=cycle_idx, step=step, task_id=next_task.id, reason="build_failed")
                        return 1, "build_failed", 0

                if run_tests:
                    metrics.event("test_start", cycle=cycle_idx, step=step, task_id=next_task.id)
                    ok = dotnet_test(
                        repo=repo,
                        test_target=args.dotnet_test_target,
                        test_filter=args.dotnet_test_filter,
                        log_path=task_dir / "dotnet_test.txt",
                        timeout_sec=int(args.test_timeout_seconds),
                    )
                    metrics.event("test_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=0 if ok else 1)
                    if not ok:
                        state.setdefault("failed", []).append({"task": next_task.id, "reason": "test_failed"})
                        save_state(state_path, state)
                        eprint(f"[STOP] Tests failed after {next_task.id}. See {task_dir / 'dotnet_test.txt'}")
                        if cp:
                            restore_checkpoint(repo, cp)
                            metrics.event("rollback", cycle=cycle_idx, step=step, task_id=next_task.id, reason="test_failed")
                        return 1, "test_failed", 0

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
                    (task_dir / "policy_scan.json").write_text(json.dumps(scan_result, ensure_ascii=False, indent=2),
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
                        eprint(f"[STOP] Policy scan failed after {next_task.id}. See {task_dir / 'policy_scan.json'}")
                        metrics.event("task_end", cycle=cycle_idx, step=step, task_id=next_task.id, rc=1, reason="policy_violation",
                                      violations=len(scan_result.get("violations", [])))
                        if cp:
                            restore_checkpoint(repo, cp)
                            metrics.event("rollback", cycle=cycle_idx, step=step, task_id=next_task.id, reason="policy_violation")
                        return 1, "policy_violation", 0

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
            await run_qa_if_needed(cycle_idx, ran_tasks=ran_tasks)

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
            append_cycle_summary(f"{now_iso()} cycle={cycle_idx} done={len(done_set)}/{len(tasks)} failed={failed_count} dt={cycle_dt:.1f}s")
            metrics.event("cycle_end", cycle=cycle_idx, rc=0, done=len(done_set), total=len(tasks), failed=failed_count, duration_seconds=cycle_dt)

            done_delta = len(done_set) - before_done

            if len(done_set) >= len(tasks):
                return 0, "all_tasks_done", done_delta

            return 0, "ok", done_delta

        idle_accum = 0
        cycles = 1 if not args.loop else (args.loop_max_cycles if args.loop_max_cycles and args.loop_max_cycles > 0 else 10**9)

        for cycle_idx in range(int(cycles)):
            if stop_path.exists():
                append_cycle_summary(f"{now_iso()} cycle={cycle_idx} stop=stop_file")
                break

            rc, reason, delta = await run_cycle(cycle_idx)
            # 1-line per-cycle summary for unattended ops
            print(f"[CYCLE] {now_iso()} idx={cycle_idx} rc={rc} reason={reason} progress_delta={delta}")

            if rc != 0:
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

    return 0


def run(args: argparse.Namespace) -> int:
    return asyncio.run(main_async(args))
