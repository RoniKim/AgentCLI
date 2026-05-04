from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from ..scan import collect_scan_files
from ..task_status import TASK_STATUS_REVIEW_REQUIRED
from .session import PipelineSession
from .stages.base import StageOutcome


_DEFAULT_QA_FOLLOWUPS_SUMMARY: dict[str, Any] = {
    "parse_ok": None,
    "candidates": 0,
    "added": 0,
    "skipped": 0,
    "manual_test_count": 0,
}


def write_run_summary_file(
    *,
    run_summary: dict[str, Any],
    run_dir: Path,
    max_summary_cycles: int,
    warn: Callable[[str], None],
) -> None:
    try:
        cycles = run_summary.get("cycles")
        if isinstance(cycles, list) and len(cycles) > max_summary_cycles:
            run_summary["cycles"] = cycles[-max_summary_cycles:]
        (run_dir / "run_summary.json").write_text(
            json.dumps(run_summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
            errors="replace",
        )
    except Exception as ex:
        warn(f"[WARN] Failed to write run_summary.json: {ex}")


def append_cycle_summary_line(
    *,
    cycle_summary_path: Path,
    line: str,
    rotate_log_file_fn: Optional[Callable[..., None]],
) -> None:
    try:
        if callable(rotate_log_file_fn):
            rotate_log_file_fn(cycle_summary_path, max_bytes=2_000_000, backup_count=5, max_age_days=14)
        with cycle_summary_path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(line.rstrip() + "\n")
    except Exception:
        pass


def ensure_backlog_artifacts(
    *,
    run_dir: Path,
    stop_path: Path,
    metrics: Any,
    warn: Callable[[str], None],
) -> bool:
    backlog_json = run_dir / "BACKLOG.json"
    backlog_md = run_dir / "BACKLOG.md"
    if backlog_json.exists() or backlog_md.exists():
        return True

    warn("[PM ERROR] BACKLOG not created by PM. Stopping to avoid running irrelevant tasks.")
    try:
        stop_path.write_text("BACKLOG missing\n", encoding="utf-8", errors="replace")
    except Exception:
        pass
    metrics.event("pm_backlog_missing", cycle=-1)
    return False


def load_backlog_tasks(
    *,
    run_dir: Path,
    load_backlog_json_fn: Callable[[Path], list[Any]],
    parse_backlog_md_fn: Callable[[Path], list[Any]],
    warn: Callable[[str], None],
) -> list[Any]:
    backlog_json = run_dir / "BACKLOG.json"
    backlog_md = run_dir / "BACKLOG.md"
    tasks: list[Any] = []
    if backlog_json.exists():
        try:
            tasks = load_backlog_json_fn(backlog_json)
        except Exception as ex:
            warn(f"Failed to parse BACKLOG.json: {ex}")
    if not tasks and backlog_md.exists():
        tasks = parse_backlog_md_fn(backlog_md)
    return tasks


def collect_scan_with_config(
    *,
    repo: Path,
    scope: str,
    scan_ignore_paths: list[str],
    scan_ignore_globs: list[str],
    scan_max_files: int,
    scan_max_bytes_per_file: int,
    scan_max_total_bytes: int,
    scan_timeout_seconds: int,
    scan_include_untracked_in_full: bool,
    ignore_paths: Optional[list[str]] = None,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
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


def prepare_pm_inventory_markdown(
    *,
    repo: Path,
    run_dir: Path,
    pm_cache_dir: Path,
    cycle_idx: int,
    metrics: Any,
    build_repo_inventory_fn: Callable[[Path], Any],
    write_repo_inventory_files_fn: Callable[[Path, Path, Any], tuple[Any, Path]],
) -> Path:
    try:
        inventory = build_repo_inventory_fn(repo)
        _, inv_md = write_repo_inventory_files_fn(repo, pm_cache_dir, inventory)
        return inv_md
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
        return inv_md


def build_goals_prompt_context(
    *,
    repo: Path,
    goals_enabled: bool,
    goals_auto_generate: bool,
    read_goals_fn: Callable[[Path], tuple[Optional[Path], str]],
    format_goals_block_fn: Callable[[Optional[Path], str], str],
    goals_evaluation_instruction: str,
    goals_generation_instruction: str,
) -> tuple[str, str]:
    if goals_enabled:
        goals_path, goals_text = read_goals_fn(repo)
        goals_block = format_goals_block_fn(goals_path, goals_text)
        goals_instruction = (
            goals_evaluation_instruction
            if goals_path
            else (goals_generation_instruction if goals_auto_generate else "")
        )
        return goals_block, goals_instruction
    return "(disabled)", ""


def merge_pm_tasks_with_existing_pending(
    *,
    pm_tasks: list[dict[str, Any]],
    existing_tasks: list[Any],
    done_ids: set[str],
    failed_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    # PM 결과를 권위적 소스로 취급: done + failed 태스크는 기존 pending에서 제외
    excluded = done_ids | (failed_ids or set())
    existing_pending = [t for t in existing_tasks if t.id not in excluded]
    merged_tasks: list[dict[str, Any]] = list(pm_tasks)
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
                    "goal_trace": [dict(trace) for trace in (t.goal_trace or []) if isinstance(trace, dict)],
                }
            )
    return merged_tasks


def write_pm_output_artifacts(
    *,
    run_dir: Path,
    cycle_idx: int,
    pm_output_model_dump: dict[str, Any],
    notes_md: Optional[str],
    dump_pretty_fn: Callable[[dict[str, Any]], str],
) -> None:
    (run_dir / f"PM_OUTPUT_cycle_{cycle_idx:03d}.json").write_text(
        dump_pretty_fn(pm_output_model_dump) + "\n",
        encoding="utf-8",
        errors="replace",
    )
    if notes_md:
        (run_dir / "NOTES_PM.md").write_text(
            notes_md.strip() + "\n",
            encoding="utf-8",
            errors="replace",
        )


def build_qa_skills_context(
    *,
    load_tasks_fn: Callable[[], list[Any]],
    skills_enabled: bool,
    skills_by_id: dict[str, Any],
    skills_cfg: dict[str, Any],
    inline_skills_for_fn: Callable[[str, Any], bool],
    build_skills_context_fn: Callable[..., str],
) -> str:
    skills_context = "(skills disabled)"
    if not skills_enabled:
        return skills_context

    skill_ids: list[str] = []
    for t in load_tasks_fn():
        skill_ids.extend(t.skills or [])
    deduped = list(dict.fromkeys([s for s in skill_ids if s]))
    selected_records = [skills_by_id[sid] for sid in deduped if sid in skills_by_id]
    include_excerpts = inline_skills_for_fn("qa", skills_cfg.get("inline_mode", ""))
    skills_context = build_skills_context_fn(
        selected_records,
        max_excerpt_lines=int(skills_cfg.get("max_excerpt_lines", 0) or 0),
        total_char_cap=int(skills_cfg.get("qa_max_total_chars", 0) or 0),
        include_excerpts=include_excerpts,
    )
    missing = [sid for sid in deduped if sid not in skills_by_id]
    if missing:
        skills_context += "\nMissing skills: " + ", ".join(missing)
    return skills_context


def process_qa_followups(
    *,
    cycle_idx: int,
    run_dir: Path,
    qa_text: str,
    qa_to_backlog: bool,
    max_qa_followups: int,
    parse_qa_followups_fn: Callable[[str], tuple[Optional[Any], Optional[Exception]]],
    followups_from_structured_fn: Callable[..., list[dict[str, Any]]],
    extract_qa_followups_fn: Callable[..., list[dict[str, Any]]],
    split_followups_by_type_fn: Callable[[list[dict[str, Any]]], tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    write_manual_checks_fn: Callable[[Path, int, list[dict[str, Any]]], None],
    load_state_fn: Callable[[Path], dict[str, Any]],
    state_path: Path,
    load_tasks_fn: Callable[[], list[Any]],
    merge_qa_followups_fn: Callable[[list[dict[str, Any]], list[dict[str, Any]], set[str]], list[dict[str, Any]]],
    write_backlog_files_fn: Callable[[Path, list[dict[str, Any]]], None],
    metrics: Any,
) -> dict[str, Any]:
    followups_added = 0
    followups_candidates = 0
    followups_skipped = 0
    manual_test_count = 0
    parse_ok: Optional[bool] = None
    followups: list[dict[str, Any]] = []

    if qa_to_backlog:
        parsed, parse_err = parse_qa_followups_fn(qa_text)
        if parsed is not None:
            parse_ok = True
            followups = followups_from_structured_fn(parsed, max_items=max_qa_followups)
        else:
            parse_ok = False
            followups = extract_qa_followups_fn(qa_text, max_items=max_qa_followups, run_dir=run_dir)
            metrics.event("qa_followups_parse", cycle=cycle_idx, parse_ok=False, error=str(parse_err or "parse_failed"))
        if parse_ok:
            metrics.event("qa_followups_parse", cycle=cycle_idx, parse_ok=True)
        if followups:
            followups_candidates = len(followups)
            code_fix_items, manual_test_items = split_followups_by_type_fn(followups)
            manual_test_count = len(manual_test_items)
            if manual_test_items:
                write_manual_checks_fn(run_dir, cycle_idx, manual_test_items)
            if code_fix_items:
                state_obj = load_state_fn(state_path)
                done_ids = set(state_obj.get("done", []) or [])
                existing = load_tasks_fn()
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
                merged = merge_qa_followups_fn(base_tasks, code_fix_items, done_ids)
                followups_added = max(0, len(merged) - len(base_tasks))
                followups_skipped = max(0, len(code_fix_items) - followups_added)
                write_backlog_files_fn(run_dir, merged)
        (run_dir / f"qa_followups_cycle_{cycle_idx:03d}.json").write_text(
            json.dumps(
                {
                    "cycle": cycle_idx,
                    "parse_ok": parse_ok,
                    "candidates_count": followups_candidates,
                    "added_count": followups_added,
                    "skipped_count": followups_skipped,
                    "manual_test_count": manual_test_count,
                    "tasks": followups,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
            errors="replace",
        )

    return {
        "parse_ok": parse_ok,
        "candidates": followups_candidates,
        "added": followups_added,
        "skipped": followups_skipped,
        "manual_test_count": manual_test_count,
    }


def detect_and_clear_recycled_ids(
    *,
    prev_tasks: list[Any],
    new_tasks: list[Any],
    done_set: set[str],
    state: dict[str, Any],
    state_path: Path,
    save_state_fn: Callable[[Path, dict[str, Any]], None],
    on_changed_fn: Callable[[set[str]], None] | None = None,
    on_unchanged_fn: Callable[[set[str]], None] | None = None,
) -> tuple[set[str], set[str]]:
    """PM 실행 후 재활용된 태스크 ID를 감지하고 done_set에서 제거.

    Returns (task_ids, done_set) — 갱신된 값.
    done_set은 in-place로도 변경됨.
    """
    old_task_map = {t.id: (t.title, t.prompt) for t in prev_tasks}
    new_task_ids = {t.id for t in new_tasks}
    recycled_ids = done_set & new_task_ids
    truly_new: set[str] = set()
    for t in new_tasks:
        if t.id in recycled_ids:
            old = old_task_map.get(t.id)
            if old is None or old != (t.title, t.prompt):
                truly_new.add(t.id)
    if truly_new:
        if on_changed_fn:
            on_changed_fn(truly_new)
        done_set -= truly_new
        state["done"] = sorted(done_set)
        save_state_fn(state_path, state)
    elif recycled_ids and on_unchanged_fn:
        on_unchanged_fn(recycled_ids)
    return new_task_ids, done_set


@dataclass
class PMRefreshOutcome:
    should_return: bool = False
    rc: int = 0
    reason: str = ""
    done_delta: int = 0
    ran_tasks: bool = False
    tasks: list[Any] = field(default_factory=list)
    task_ids: set[str] = field(default_factory=set)
    done_set: set[str] = field(default_factory=set)
    before_done: int = 0


async def maybe_refresh_tasks_after_pm(
    *,
    pm_stage_enabled: bool,
    pm_refresh_backlog: bool,
    cycle_idx: int,
    curr_head: str,
    changed_files: list[str],
    repo_fp: str,
    before_done: int,
    tasks: list[Any],
    task_ids: set[str],
    done_set: set[str],
    state: dict[str, Any],
    state_path: Path,
    run_pm_if_needed_fn: Callable[[int, str, list[str], str, bool], Awaitable[bool]],
    pm_stop_reason: dict[str, Any],
    stop_reason_quota: str,
    stop_reason_stop_file: str,
    stop_reason_pm_refresh_no_backlog: str,
    stop_path: Path,
    detect_stop_reason_fn: Callable[[list[Path]], Optional[str]],
    ensure_backlog_fn: Callable[[], bool],
    load_tasks_fn: Callable[[], list[Any]],
    save_state_fn: Callable[[Path, dict[str, Any]], None],
    on_recycled_ids_changed_fn: Callable[[set[str]], None],
    on_recycled_ids_unchanged_fn: Callable[[set[str]], None],
) -> PMRefreshOutcome:
    if pm_stage_enabled and pm_refresh_backlog and (before_done >= len(task_ids)):
        pm_ok = await run_pm_if_needed_fn(cycle_idx, curr_head, changed_files, repo_fp, True)
        if not pm_ok:
            if pm_stop_reason.get("reason") == stop_reason_quota:
                return PMRefreshOutcome(
                    should_return=True,
                    rc=0,
                    reason=stop_reason_quota,
                    done_delta=0,
                    ran_tasks=(len(done_set) > before_done),
                    tasks=tasks,
                    task_ids=task_ids,
                    done_set=done_set,
                    before_done=before_done,
                )
            if stop_path.exists():
                detected = detect_stop_reason_fn([stop_path])
                return PMRefreshOutcome(
                    should_return=True,
                    rc=0,
                    reason=(detected or stop_reason_stop_file),
                    done_delta=0,
                    ran_tasks=(len(done_set) > before_done),
                    tasks=tasks,
                    task_ids=task_ids,
                    done_set=done_set,
                    before_done=before_done,
                )
            return PMRefreshOutcome(
                should_return=True,
                rc=1,
                reason="pm_failed",
                done_delta=0,
                ran_tasks=(len(done_set) > before_done),
                tasks=tasks,
                task_ids=task_ids,
                done_set=done_set,
                before_done=before_done,
            )
        if not ensure_backlog_fn():
            return PMRefreshOutcome(
                should_return=True,
                rc=1,
                reason=stop_reason_pm_refresh_no_backlog,
                done_delta=0,
                ran_tasks=(len(done_set) > before_done),
                tasks=tasks,
                task_ids=task_ids,
                done_set=done_set,
                before_done=before_done,
            )
        prev_tasks = list(tasks)
        tasks = load_tasks_fn()
        task_ids, done_set = detect_and_clear_recycled_ids(
            prev_tasks=prev_tasks,
            new_tasks=tasks,
            done_set=done_set,
            state=state,
            state_path=state_path,
            save_state_fn=save_state_fn,
            on_changed_fn=on_recycled_ids_changed_fn,
            on_unchanged_fn=on_recycled_ids_unchanged_fn,
        )
        before_done = len(done_set.intersection(task_ids))

    return PMRefreshOutcome(
        should_return=False,
        tasks=tasks,
        task_ids=task_ids,
        done_set=done_set,
        before_done=before_done,
    )


def compute_dev_model_tiers(
    *,
    base_model: str,
    tier1_model: str,
    tier2_model: str,
    dev_auto_escalate: bool,
    dev_max_escalations: int,
    max_escalations_per_task_budget: int,
) -> tuple[list[str], int, int]:
    if max_escalations_per_task_budget > 0:
        dev_max_escalations = min(dev_max_escalations, max_escalations_per_task_budget)

    tiers: list[str] = [base_model]
    t1 = str(tier1_model or "").strip()
    t2 = str(tier2_model or "").strip()
    if t1 and t1 not in tiers:
        tiers.append(t1)
    if t2 and t2 not in tiers:
        tiers.append(t2)

    max_attempts = 1
    if dev_auto_escalate and dev_max_escalations > 0:
        max_attempts = min(1 + dev_max_escalations, len(tiers))
    return tiers, max_attempts, dev_max_escalations


def _short_dependency_text(value: Any, *, max_chars: int = 240) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _latest_failures_by_task(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    failures: dict[str, dict[str, Any]] = {}
    failed_items = state.get("failed") if isinstance(state.get("failed"), list) else []
    for item in failed_items:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task") or item.get("task_id") or item.get("taskId") or "").strip()
        if task_id:
            failures[task_id] = dict(item)
    return failures


def _dependency_blocker_details(
    *,
    blocked_ids: list[str],
    orphaned_ids: list[str],
    tasks: list[Any],
    skipped_set: set[str],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    task_titles = {str(getattr(task, "id", "") or ""): str(getattr(task, "title", "") or "") for task in tasks}
    failures = _latest_failures_by_task(state)
    orphaned = set(orphaned_ids)
    blockers: list[dict[str, Any]] = []
    for dep_id in blocked_ids:
        dep = str(dep_id or "").strip()
        if not dep:
            continue
        failure = failures.get(dep, {})
        title = _short_dependency_text(task_titles.get(dep) or failure.get("title") or failure.get("task_title") or dep)
        status = _short_dependency_text(
            failure.get("task_status")
            or failure.get("taskStatus")
            or failure.get("outcome_status")
            or failure.get("status")
            or ("orphaned" if dep in orphaned else "skipped" if dep in skipped_set else "blocked")
        )
        reason = _short_dependency_text(
            failure.get("reason")
            or ("dependency_orphaned" if dep in orphaned else "dependency_failed")
        )
        detail = _short_dependency_text(failure.get("detail") or failure.get("message") or "")
        validation_summary = _short_dependency_text(
            failure.get("validation_summary")
            or failure.get("validationSummary")
            or failure.get("failure_summary")
            or failure.get("failureSummary")
            or failure.get("summary")
            or detail
        )
        if dep in orphaned:
            next_action = "Regenerate the backlog or remove this missing dependency before retrying the blocked task."
        elif status.lower() in {"blocked_env", "test_contract_changed", "review_required"}:
            next_action = f"Resolve or review upstream task {dep}, then retry the dependent task."
        else:
            next_action = f"Complete upstream task {dep}, then retry the dependent task."
        blockers.append(
            {
                "task_id": dep,
                "taskId": dep,
                "title": title,
                "status": status,
                "reason": reason,
                "detail": detail,
                "validation_summary": validation_summary,
                "validationSummary": validation_summary,
                "next_action": next_action,
                "nextAction": next_action,
            }
        )
    return blockers


def _dependency_blocked_detail(blockers: list[dict[str, Any]]) -> str:
    if not blockers:
        return "Blocked by unresolved task dependencies."
    lines = ["Blocked by unresolved task dependencies:"]
    for blocker in blockers:
        task_id = _short_dependency_text(blocker.get("task_id") or blocker.get("taskId") or "?")
        title = _short_dependency_text(blocker.get("title") or task_id)
        status = _short_dependency_text(blocker.get("status") or "unknown")
        reason = _short_dependency_text(blocker.get("reason") or "unknown")
        validation = _short_dependency_text(blocker.get("validation_summary") or blocker.get("detail") or "")
        next_action = _short_dependency_text(blocker.get("next_action") or "")
        line = f"- {task_id}: {title} | status={status} | reason={reason}"
        if validation:
            line += f" | validation={validation}"
        if next_action:
            line += f" | next={next_action}"
        lines.append(line)
    return "\n".join(lines)


def select_next_task_with_dependency_checks(
    *,
    tasks: list[Any],
    done_set: set[str],
    skipped_set: set[str],
    state: dict[str, Any],
    state_path: Path,
    cycle_idx: int,
    max_consecutive_failures: int,
    task_history_enabled: bool,
    count_consecutive_title_failures_fn: Callable[[str], int],
    save_state_fn: Callable[[Path, dict[str, Any]], None],
    record_history_fn: Callable[..., None],
    logger: Any,
    metrics: Any,
    eprint_fn: Callable[[str], None],
    task_results: list[dict[str, Any]],
    step_idx: int = 0,
    record_task_experience_fn: Callable[..., None] | None = None,
) -> Optional[Any]:
    processed = done_set | skipped_set
    for t in tasks:
        if t.id in processed:
            continue

        if task_history_enabled:
            consec = count_consecutive_title_failures_fn(t.title)
            if consec >= max_consecutive_failures:
                logger.skip_event(t.id, f"failed {consec} times consecutively (>= {max_consecutive_failures})")
                skipped_set.add(t.id)
                state.setdefault("failed", []).append(
                    {
                        "task": t.id,
                        "reason": "persistent_failure",
                        "detail": f"Failed {consec} consecutive times across runs",
                    }
                )
                save_state_fn(state_path, state)
                record_history_fn(
                    t.id,
                    t.title,
                    "failed",
                    reason="persistent_failure",
                    detail=f"Auto-skipped after {consec} consecutive failures",
                    files=t.files,
                    cycle=cycle_idx,
                )
                metrics.event("task_persistent_skip", cycle=cycle_idx, task_id=t.id, consecutive_failures=consec)
                task_results.append(
                    {
                        "id": t.id,
                        "title": t.title,
                        "status": "skipped",
                        "reason": "persistent_failure",
                        "duration": -1,
                    }
                )
                if callable(record_task_experience_fn):
                    record_task_experience_fn(
                        task_id=t.id,
                        title=t.title,
                        status="skipped",
                        task_status=TASK_STATUS_REVIEW_REQUIRED,
                        reason="persistent_failure",
                        cycle_idx=cycle_idx,
                        step_idx=step_idx,
                        attempt=0,
                        max_attempts=max_consecutive_failures,
                        validation_summary=f"Failed {consec} consecutive times across runs",
                        outcome_action="skipped_after_repeated_failures",
                    )
                continue

        if t.depends_on:
            unmet = [dep for dep in t.depends_on if dep not in done_set]
            if unmet:
                failed_ids = {f.get("task") for f in state.get("failed", []) if isinstance(f, dict)}
                all_known_ids = {bt.id for bt in tasks} | done_set | failed_ids | skipped_set
                orphaned = [dep for dep in unmet if dep not in all_known_ids]
                permanently_blocked = [dep for dep in unmet if dep in (skipped_set | failed_ids)] + orphaned
                if permanently_blocked:
                    reason = "dependency_orphaned" if orphaned else "dependency_failed"
                    blockers = _dependency_blocker_details(
                        blocked_ids=permanently_blocked,
                        orphaned_ids=orphaned,
                        tasks=tasks,
                        skipped_set=skipped_set,
                        state=state,
                    )
                    detail = _dependency_blocked_detail(blockers)
                    eprint_fn(f"[SKIP] Task {t.id} depends on unresolvable tasks {permanently_blocked}; skipping.")
                    skipped_set.add(t.id)
                    state.setdefault("failed", []).append(
                        {
                            "task": t.id,
                            "reason": reason,
                            "status": TASK_STATUS_REVIEW_REQUIRED,
                            "task_status": TASK_STATUS_REVIEW_REQUIRED,
                            "taskStatus": TASK_STATUS_REVIEW_REQUIRED,
                            "detail": detail,
                            "blocked_dependencies": blockers,
                            "blockedDependencies": blockers,
                            "blocking_dependencies": blockers,
                            "blockingDependencies": blockers,
                            "next_action": "Resolve blocking upstream tasks before retrying this task.",
                            "nextAction": "Resolve blocking upstream tasks before retrying this task.",
                        }
                    )
                    save_state_fn(state_path, state)
                    record_history_fn(
                        t.id,
                        t.title,
                        "failed",
                        reason=reason,
                        detail=detail,
                        files=t.files,
                        cycle=cycle_idx,
                    )
                    task_results.append(
                        {
                            "id": t.id,
                            "title": t.title,
                            "status": "skipped",
                            "reason": reason,
                            "task_status": TASK_STATUS_REVIEW_REQUIRED,
                            "detail": detail,
                            "blocked_dependencies": blockers,
                            "blockedDependencies": blockers,
                            "duration": -1,
                        }
                    )
                    if callable(record_task_experience_fn):
                        record_task_experience_fn(
                            task_id=t.id,
                            title=t.title,
                            status="skipped",
                            task_status=TASK_STATUS_REVIEW_REQUIRED,
                            reason=reason,
                            cycle_idx=cycle_idx,
                            step_idx=step_idx,
                            attempt=0,
                            max_attempts=0,
                            blocked_dependencies=blockers,
                            outcome_action="not_run_dependency_blocked",
                        )
                    continue
                continue

        return t

    return None


@dataclass
class SharedCycleResult:
    rc: int
    reason: str
    done_delta: int
    qa_followups_added: int


@dataclass
class SharedCycleDeps:
    args: argparse.Namespace
    repo: Path
    run_dir: Path
    stop_path: Path
    metrics: Any
    pipeline_mgr: Any
    continuous: bool

    ensure_backlog: Callable[[], bool]
    load_tasks: Callable[[], list[Any]]
    run_pm_if_needed: Callable[[int, str, list[str], str, bool], Awaitable[bool]]
    run_dev_loop: Callable[[int, list[Any], str, list[str], str, float], Awaitable[tuple[int, str, int, bool]]]
    run_qa_if_needed: Callable[[int, bool], Awaitable[dict[str, Any]]]

    pm_stop_reason: dict[str, Any]
    detect_stop_reason: Callable[[list[Path]], Optional[str]]
    budget_state: dict[str, Any]
    run_summary: dict[str, Any]
    write_run_summary: Callable[[], None]
    snapshot_json: Path

    get_prev_head: Callable[[], str]
    set_prev_head: Callable[[str], None]
    get_policy_scan_summary: Callable[[], Optional[dict[str, Any]]]
    set_policy_scan_summary: Callable[[Optional[dict[str, Any]]], None]
    get_security_scan_summary: Callable[[], Optional[dict[str, Any]]]
    set_security_scan_summary: Callable[[Optional[dict[str, Any]]], None]

    policy_scan_enabled: bool
    policy_scan_scope: str
    security_enabled: bool
    security_scan_scope: str
    security_rules: list[dict[str, Any]]
    security_fail_severity: str
    security_end_include_totals: bool
    scan_ignore_paths: list[str]

    collect_scan: Callable[[str], tuple[list[tuple[str, str]], dict[str, Any]]]
    security_scan_files_fn: Callable[[list[tuple[str, str]], list[dict[str, Any]], Optional[list[str]]], dict[str, Any]]
    severity_at_or_above_fn: Callable[[str, str], bool]
    git_head_fn: Callable[[Path], str]
    git_changed_files_fn: Callable[[Path, str, str], list[str]]
    git_worktree_changed_files_fn: Callable[[Path], list[str]]
    repo_fingerprint_fn: Callable[[Path], str]
    eprint_fn: Callable[[str], None]

    stop_reason_quota: str
    stop_reason_stop_file: str
    stop_reason_project_complete: str
    stop_reason_all_tasks_done: str
    stop_reason_no_tasks: str


async def run_shared_cycle_once(
    cycle_idx: int,
    deps: SharedCycleDeps,
) -> SharedCycleResult:
    if deps.stop_path.exists():
        return SharedCycleResult(rc=0, reason=deps.stop_reason_stop_file, done_delta=0, qa_followups_added=0)

    deps.set_policy_scan_summary(None)
    deps.set_security_scan_summary(None)
    cycle_t0 = time.time()
    deps.metrics.event("cycle_start", cycle=cycle_idx)

    prev_head = deps.get_prev_head()
    curr_head = deps.git_head_fn(deps.repo).strip()
    head_changed_files = deps.git_changed_files_fn(deps.repo, prev_head, curr_head)

    wt_changed_files: list[str] = []
    if bool(getattr(deps.args, "pm_include_working_tree", False)):
        try:
            wt_changed_files = deps.git_worktree_changed_files_fn(deps.repo)
        except Exception as ex:
            deps.eprint_fn(f"[WARN] working-tree change detection failed: {ex}")
            wt_changed_files = []

    changed_files = sorted(set([*head_changed_files, *wt_changed_files]))
    repo_fp = deps.repo_fingerprint_fn(deps.repo)

    async def pm_phase(ci: int) -> StageOutcome:
        if deps.stop_path.exists():
            return StageOutcome.stop(deps.stop_reason_stop_file)
        deps.metrics.event("pm_stage_start", cycle=ci)
        ok = await deps.run_pm_if_needed(ci, curr_head, changed_files, repo_fp, False)
        if not ok:
            if deps.pm_stop_reason.get("reason") == deps.stop_reason_quota:
                deps.metrics.event("pm_stage_end", cycle=ci, rc=0, reason=deps.stop_reason_quota)
                return StageOutcome.stop(deps.stop_reason_quota, rc=0)
            if deps.stop_path.exists():
                detected = deps.detect_stop_reason([deps.stop_path]) or deps.stop_reason_stop_file
                deps.metrics.event("pm_stage_end", cycle=ci, rc=0, reason=detected)
                return StageOutcome.stop(detected, rc=0)
            deps.metrics.event("pm_stage_end", cycle=ci, rc=1)
            return StageOutcome.fail("pm_failed", rc=1)
        deps.metrics.event("pm_stage_end", cycle=ci, rc=0)
        return StageOutcome.ok("pm_ok")

    async def security_phase(ci: int) -> StageOutcome:
        if not deps.security_enabled:
            deps.metrics.event("security_skipped", cycle=ci, reason="security_disabled")
            return StageOutcome.skip("security_disabled")
        if deps.stop_path.exists():
            return StageOutcome.stop(deps.stop_reason_stop_file)
        deps.metrics.event("security_start", cycle=ci)
        scan_files, scan_stats = deps.collect_scan(deps.security_scan_scope)
        scan_result = deps.security_scan_files_fn(scan_files, deps.security_rules, ignore_paths=deps.scan_ignore_paths)
        findings = scan_result.get("findings", [])
        fail_hits = [f for f in findings if deps.severity_at_or_above_fn(str(f.get("severity", "")), deps.security_fail_severity)]
        ok = len(fail_hits) == 0
        security_scan_summary = {
            "scope": scan_stats.get("scope", deps.security_scan_scope),
            "files_scanned": scan_stats.get("files_scanned", 0),
            "bytes_scanned": scan_stats.get("bytes_scanned", 0),
            "files_skipped": scan_stats.get("files_skipped", 0),
            "findings_total": len(findings),
            "findings_fail": len(fail_hits),
        }
        deps.set_security_scan_summary(security_scan_summary)
        out = {
            "cycle": ci,
            "ok": ok,
            "fail_severity": deps.security_fail_severity,
            "findings": findings,
            "stats": scan_stats,
        }
        (deps.run_dir / f"security_scan_cycle_{ci:03d}.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2),
            encoding="utf-8",
            errors="replace",
        )
        if deps.security_end_include_totals:
            deps.metrics.event(
                "security_end",
                cycle=ci,
                rc=0 if ok else 1,
                findings=len(fail_hits),
                **security_scan_summary,
            )
        else:
            deps.metrics.event(
                "security_end",
                cycle=ci,
                rc=0 if ok else 1,
                findings=len(fail_hits),
                scope=security_scan_summary["scope"],
                files_scanned=security_scan_summary["files_scanned"],
                bytes_scanned=security_scan_summary["bytes_scanned"],
                files_skipped=security_scan_summary["files_skipped"],
            )
        deps.metrics.event(
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
            deps.metrics.event("security_violation", cycle=ci, findings=len(fail_hits))
            return StageOutcome.fail("security_violation", rc=1)
        return StageOutcome.ok("security_ok")

    async def dev_phase(ci: int) -> StageOutcome:
        if deps.stop_path.exists():
            return StageOutcome.stop(deps.stop_reason_stop_file)
        if not session.tasks:
            return StageOutcome.fail(deps.stop_reason_no_tasks, rc=1)

        rc, reason, done_delta, ran_tasks = await deps.run_dev_loop(
            ci,
            session.tasks,
            curr_head,
            changed_files,
            repo_fp,
            cycle_t0,
        )
        session.done_delta = int(done_delta or 0)
        session.ran_tasks = bool(ran_tasks)

        if reason == deps.stop_reason_quota:
            return StageOutcome.stop(deps.stop_reason_quota, rc=0)
        if reason == deps.stop_reason_project_complete:
            return StageOutcome.ok(deps.stop_reason_project_complete)
        if reason == deps.stop_reason_all_tasks_done:
            return StageOutcome.ok(deps.stop_reason_all_tasks_done)
        if reason == deps.stop_reason_stop_file:
            return StageOutcome.stop(deps.stop_reason_stop_file, rc=0)
        if rc != 0:
            return StageOutcome.fail(reason, rc=rc)
        return StageOutcome.ok(reason)

    async def qa_phase(ci: int) -> StageOutcome:
        if deps.stop_path.exists():
            return StageOutcome.stop(deps.stop_reason_stop_file)
        qa_summary = await deps.run_qa_if_needed(ci, session.ran_tasks)
        session.data["qa_followups_summary"] = qa_summary
        session.data["qa_followups_added"] = int(qa_summary.get("added", 0) or 0)
        if qa_summary.get("quota_exhausted"):
            return StageOutcome.stop(deps.stop_reason_quota)
        return StageOutcome.ok("qa_done")

    session = PipelineSession(
        args=deps.args,
        repo=deps.repo,
        run_dir=deps.run_dir,
        stop_path=deps.stop_path,
        ensure_backlog=deps.ensure_backlog,
        load_tasks=deps.load_tasks,
        pm_phase=pm_phase,
        dev_phase=dev_phase,
        qa_phase=qa_phase,
        security_phase=security_phase,
    )

    res = await deps.pipeline_mgr.run_cycle(session, cycle_idx, continuous=deps.continuous)

    policy_scan_summary = deps.get_policy_scan_summary()
    security_scan_summary = deps.get_security_scan_summary()
    cycle_entry = {
        "cycle": cycle_idx,
        "stages": [],
        "budget": {
            "total_escalations": deps.budget_state["total_escalations"],
            "total_continuations": deps.budget_state["total_continuations"],
            "total_repairs": deps.budget_state["total_repairs"],
        },
        "policy_scan": policy_scan_summary
        or (
            {"scope": "disabled", "files_scanned": 0, "bytes_scanned": 0, "files_skipped": 0, "violations_total": 0, "violations_fail": 0}
            if not deps.policy_scan_enabled
            else {"scope": deps.policy_scan_scope, "files_scanned": 0, "bytes_scanned": 0, "files_skipped": 0, "violations_total": 0, "violations_fail": 0}
        ),
        "security_scan": security_scan_summary
        or (
            {"scope": "disabled", "files_scanned": 0, "bytes_scanned": 0, "files_skipped": 0, "findings_total": 0, "findings_fail": 0}
            if not deps.security_enabled
            else {"scope": deps.security_scan_scope, "files_scanned": 0, "bytes_scanned": 0, "files_skipped": 0, "findings_total": 0, "findings_fail": 0}
        ),
        "qa_followups": session.data.get("qa_followups_summary") or dict(_DEFAULT_QA_FOLLOWUPS_SUMMARY),
    }
    for st in res.stages:
        entry = dict(st)
        if str(entry.get("name", "")).lower() == "qa":
            entry["followups_added"] = int(session.data.get("qa_followups_added", 0) or 0)
        cycle_entry["stages"].append(entry)
    deps.run_summary["cycles"].append(cycle_entry)
    try:
        (deps.run_dir / f"run_summary_cycle_{cycle_idx:03d}.json").write_text(
            json.dumps(cycle_entry, ensure_ascii=False, indent=2),
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        pass
    deps.write_run_summary()

    if res.reason not in (deps.stop_reason_stop_file,) and not deps.stop_path.exists():
        try:
            final_head = deps.git_head_fn(deps.repo).strip()
            if final_head:
                deps.snapshot_json.write_text(
                    json.dumps(
                        {
                            "prev_head": deps.get_prev_head(),
                            "head": final_head,
                            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                    errors="replace",
                )
                deps.set_prev_head(final_head)
        except Exception as ex:
            deps.eprint_fn(f"[WARN] snapshot update failed: {ex}")

    qa_added = int(session.data.get("qa_followups_added", 0) or 0)
    return SharedCycleResult(rc=res.rc, reason=res.reason, done_delta=res.done_delta, qa_followups_added=qa_added)
