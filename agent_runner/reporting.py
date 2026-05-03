from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence

from .analyzer import write_analyzer_artifacts
from .docs import read_text_robust
from .gates import FAST_WEB_WORKTREE_REGRESSION_TEST_FILES, find_build_cmd, find_test_cmd, looks_like_no_tests_found
from .goals import parse_goals_completion, read_goals
from .gitops import find_pending_worktree_merge, git_head, git_porcelain, list_untracked, read_pending_worktree_merge
from .state import TaskItem, count_state_task_ids, load_backlog_json, load_backlog_task_ids, parse_backlog_md, load_state
from .task_history import build_failed_tasks_artifact
from .failure_policy import (
    STATUS_GROUP_BLOCKED_ENV,
    STATUS_GROUP_REGRESSION,
    STATUS_GROUP_REVIEW,
    count_task_status_groups,
)
from .todo import read_current_todo
from .utils import atomic_write_json, now_iso, run_cmd, safe_write_text


def _read_text_limited(p: Path, max_chars: int = 8000) -> str:
    if not p or not p.exists() or not p.is_file():
        return ""
    try:
        txt, _enc = read_text_robust(p)
    except Exception:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return ""
    if max_chars and len(txt) > max_chars:
        return txt[:max_chars] + "\n\n...(truncated)"
    return txt


def _last_n_lines(txt: str, n: int = 60) -> str:
    lines = (txt or "").splitlines()
    if len(lines) <= n:
        return txt
    return "\n".join(lines[-n:])


def _load_backlog_tasks(run_dir: Path) -> list[TaskItem]:
    bj = run_dir / "BACKLOG.json"
    bm = run_dir / "BACKLOG.md"
    if bj.exists():
        try:
            return load_backlog_json(bj)
        except Exception:
            return []
    if bm.exists():
        try:
            return parse_backlog_md(bm)
        except Exception:
            return []
    return []


def collect_shutdown_context(repo: Path, run_dir: Path) -> dict[str, Any]:
    """Collect run-local context for shutdown reporting.

    This is designed to work even when model/tool usage is unavailable.
    """

    ctx: dict[str, Any] = {
        "generated_at": now_iso(),
        "repo": str(repo),
        "run_dir": str(run_dir),
    }

    # Git
    try:
        ctx["git_head"] = git_head(repo).strip()
    except Exception:
        ctx["git_head"] = ""

    try:
        ctx["git_porcelain"] = git_porcelain(repo)
    except Exception:
        ctx["git_porcelain"] = ""

    try:
        ctx["untracked"] = list_untracked(repo)[:200]
    except Exception:
        ctx["untracked"] = []

    # State / backlog
    state_path = run_dir / "STATE.json"
    state = {}
    try:
        state = load_state(state_path)
    except Exception:
        state = {"done": [], "failed": []}

    tasks = _load_backlog_tasks(run_dir)
    state_counts = count_state_task_ids(state, load_backlog_task_ids(run_dir / "BACKLOG.json"))
    done_set = set(state.get("done", []) or [])

    ctx["state"] = state
    ctx["tasks_total"] = len(tasks)
    ctx["tasks_done"] = state_counts["done"]
    ctx["state_counts"] = state_counts

    backlog_lines: list[str] = []
    for t in tasks[:200]:
        mark = "x" if t.id in done_set else " "
        backlog_lines.append(f"- [{mark}] {t.id} {t.title}")
    ctx["backlog_lines"] = backlog_lines

    # TODO
    try:
        todo_path, todo_text = read_current_todo(repo)
        ctx["todo_path"] = str(todo_path) if todo_path else ""
        ctx["todo_text"] = (todo_text or "")
    except Exception:
        ctx["todo_path"] = ""
        ctx["todo_text"] = ""

    # Recent dev output
    dev_logs_dir = run_dir / "dev_logs"
    latest_dev_log: Optional[Path] = None
    if dev_logs_dir.exists() and dev_logs_dir.is_dir():
        try:
            files = [p for p in dev_logs_dir.glob("*.txt") if p.is_file()]
            files.sort(key=lambda p: p.stat().st_mtime)
            latest_dev_log = files[-1] if files else None
        except Exception:
            latest_dev_log = None

    ctx["latest_dev_log_path"] = str(latest_dev_log) if latest_dev_log else ""
    ctx["latest_dev_log_tail"] = _last_n_lines(_read_text_limited(latest_dev_log, 12000), 120) if latest_dev_log else ""

    # Recent task dir build/test logs
    tasks_root = run_dir / "tasks"
    latest_task_dir: Optional[Path] = None
    if tasks_root.exists() and tasks_root.is_dir():
        try:
            dirs = [p for p in tasks_root.iterdir() if p.is_dir()]
            dirs.sort(key=lambda p: p.stat().st_mtime)
            latest_task_dir = dirs[-1] if dirs else None
        except Exception:
            latest_task_dir = None

    ctx["latest_task_dir"] = str(latest_task_dir) if latest_task_dir else ""
    if latest_task_dir:
        # Build/test logs are written per-attempt under tasks/<task_id>/attempt_XX/
        attempt_dir: Optional[Path] = None
        try:
            attempts = [p for p in latest_task_dir.iterdir() if p.is_dir() and p.name.startswith("attempt_")]
            attempts.sort(key=lambda p: p.stat().st_mtime)
            attempt_dir = attempts[-1] if attempts else None
        except Exception:
            attempt_dir = None

        search_dirs = [attempt_dir, latest_task_dir]
        build_tail = ""
        test_tail = ""
        for d in [p for p in search_dirs if p]:
            # New generic names
            b1 = d / "build.txt"
            t1 = d / "test.txt"
            # Legacy names
            b0 = d / "dotnet_build.txt"
            t0 = d / "dotnet_test.txt"
            if not build_tail:
                for bp in (b1, b0):
                    if bp.exists():
                        build_tail = _last_n_lines(_read_text_limited(bp, 12000), 120)
                        break
            if not test_tail:
                for tp in (t1, t0):
                    if tp.exists():
                        test_tail = _last_n_lines(_read_text_limited(tp, 12000), 120)
                        break
        ctx["build_log_tail"] = build_tail
        ctx["test_log_tail"] = test_tail

    # Analysis hints
    hints_dir = run_dir / "analysis_hints"
    hint_files: list[str] = []
    if hints_dir.exists() and hints_dir.is_dir():
        try:
            mds = [p for p in hints_dir.glob("*.md") if p.is_file()]
            mds.sort(key=lambda p: p.stat().st_mtime)
            for p in mds[-30:]:
                hint_files.append(p.name)
        except Exception:
            hint_files = []
    ctx["analysis_hints"] = hint_files

    # Runner summaries
    cycle_summary = run_dir / "cycle_summary.log"
    if cycle_summary.exists():
        ctx["cycle_summary_tail"] = _last_n_lines(_read_text_limited(cycle_summary, 12000), 80)
    else:
        ctx["cycle_summary_tail"] = ""

    cycle_change_summary_path = run_dir / "cycle_change_summary.json"
    if cycle_change_summary_path.exists():
        try:
            ctx["cycle_change_summary"] = json.loads(cycle_change_summary_path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            ctx["cycle_change_summary"] = {}
    else:
        ctx["cycle_change_summary"] = {}

    last_run_summary = run_dir / "last_run_summary.json"
    if last_run_summary.exists():
        try:
            ctx["last_run_summary"] = json.loads(last_run_summary.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            ctx["last_run_summary"] = {}
    else:
        ctx["last_run_summary"] = {}

    policy_scan_path = run_dir / "policy_scan.json"
    policy_summary: dict[str, Any] = {}
    if policy_scan_path.exists():
        try:
            scan = json.loads(policy_scan_path.read_text(encoding="utf-8", errors="replace"))
            violations = scan.get("violations") or []
            sev_counts = {"high": 0, "medium": 0, "low": 0, "other": 0}
            items: list[dict[str, Any]] = []
            for v in violations:
                sev = str(v.get("severity") or "").lower()
                if sev in sev_counts:
                    sev_counts[sev] += 1
                else:
                    sev_counts["other"] += 1
                if len(items) < 10:
                    items.append(
                        {
                            "path": v.get("path") or v.get("location", {}).get("path"),
                            "rule_id": v.get("rule_id"),
                            "severity": sev or "unknown",
                            "match_preview": v.get("match_preview"),
                        }
                    )
            policy_summary = {
                "counts": sev_counts,
                "total": len(violations),
                "fail_total": len(scan.get("fail_violations") or []),
                "items": items,
                "fail_severity": scan.get("fail_severity"),
            }
        except Exception:
            policy_summary = {}
    ctx["policy_scan_summary"] = policy_summary

    return ctx


def _json_file(path: Path, default: Any) -> Any:
    if not path or not path.exists() or not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _limit_items(items: Sequence[Any], limit: int) -> list[Any]:
    if limit <= 0:
        return list(items)
    return list(items[:limit])


def _short_text(value: Any, *, max_chars: int = 240, default: str = "") -> str:
    text = _text(value, default)
    if not text:
        return default
    if max_chars and len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def _safe_rel_path(base: Path, path: Any) -> str:
    try:
        path_obj = Path(str(path))
    except Exception:
        return _text(path)
    try:
        return path_obj.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        try:
            return path_obj.as_posix()
        except Exception:
            return _text(path)


def _summarize_commit_range(repo: Path, start_head: str, end_head: str, *, max_items: int = 10) -> list[dict[str, Any]]:
    if not start_head or not end_head or start_head == end_head:
        return []
    code, output = run_cmd(
        [
            "git",
            "log",
            "--reverse",
            "--no-merges",
            f"--max-count={max_items}",
            "--date=iso-strict",
            "--format=%H%x1f%ad%x1f%s",
            f"{start_head}..{end_head}",
        ],
        cwd=repo,
        timeout_sec=60,
    )
    if code != 0 or not output.strip():
        return []
    commits: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        sha, committed_at, subject = parts[:3]
        commits.append(
            {
                "sha": _short_text(sha, max_chars=12),
                "full_sha": _short_text(sha, max_chars=40),
                "committed_at": _text(committed_at),
                "subject": _short_text(subject, max_chars=160),
            }
        )
    return commits[:max_items]


def _summarize_validation_result(raw_result: dict[str, Any], artifact_path: str = "") -> dict[str, Any]:
    validation = _json_file(Path(artifact_path), {}) if artifact_path else {}
    if not isinstance(validation, dict):
        validation = {}
    summary = validation.get("summary")
    if isinstance(summary, dict):
        commands_total = _int(summary.get("commands_total") or summary.get("total"), 0)
        commands_executed = _int(summary.get("commands_executed") or summary.get("executed"), 0)
        commands_passed = _int(summary.get("commands_passed") or summary.get("passed"), 0)
        commands_failed = _int(summary.get("commands_failed") or summary.get("failed"), 0)
        commands_skipped = _int(summary.get("commands_skipped") or summary.get("skipped"), 0)
    else:
        commands_total = 0
        commands_executed = 0
        commands_passed = 0
        commands_failed = 0
        commands_skipped = 0
    return {
        "task_id": _text(raw_result.get("task_id") or raw_result.get("taskId") or validation.get("task_id") or validation.get("taskId")),
        "task_title": _text(raw_result.get("task_title") or raw_result.get("taskTitle") or validation.get("task_title") or validation.get("taskTitle")),
        "status": _text(validation.get("status") or validation.get("validation_status") or validation.get("validationStatus") or raw_result.get("status"), "unknown"),
        "reason": _text(validation.get("reason") or validation.get("validation_reason") or validation.get("validationReason") or raw_result.get("reason"), ""),
        "detail": _short_text(validation.get("detail") or validation.get("validation_detail") or validation.get("validationDetail") or raw_result.get("detail"), max_chars=240),
        "artifact_path": artifact_path,
        "summary": _short_text(validation.get("summary_text") or validation.get("summary") or raw_result.get("validation_summary") or raw_result.get("summary"), max_chars=240),
        "attempt": _int(validation.get("attempt") or raw_result.get("attempt"), 0),
        "cycle": _int(validation.get("cycle") or raw_result.get("cycle") or validation.get("cycle_idx"), 0),
        "step": _int(validation.get("step") or raw_result.get("step"), 0),
        "command_counts": {
            "total": commands_total,
            "executed": commands_executed,
            "passed": commands_passed,
            "failed": commands_failed,
            "skipped": commands_skipped,
        },
    }


def _summarize_pending_worktree(repo: Path, run_dir: Path) -> dict[str, Any]:
    pending_path = find_pending_worktree_merge(repo, run_dir)
    if not pending_path:
        return {
            "status": "none",
            "summary": "",
            "pending_path": "",
            "worktree_dir": "",
            "patch_path": "",
            "cleanup_path": "",
            "changed_files": [],
            "resolution_actions": [],
        }
    payload = read_pending_worktree_merge(pending_path)
    if not isinstance(payload, dict):
        payload = {}
    changed_files_raw = _as_list(payload.get("changed_files") or payload.get("changedFiles"))
    changed_files: list[dict[str, Any]] = []
    for item in changed_files_raw:
        if isinstance(item, dict):
            changed_files.append(
                {
                    "path": _text(item.get("path") or item.get("old_path") or item.get("new_path")),
                    "kind": _text(item.get("kind") or item.get("state") or item.get("type") or "modified"),
                    "summary": _short_text(item.get("summary") or item.get("note") or item.get("message"), max_chars=180),
                }
            )
        else:
            path_text = _text(item)
            if path_text:
                changed_files.append({"path": path_text, "kind": "modified", "summary": ""})
    resolution_actions_raw = _as_list(payload.get("resolution_actions") or payload.get("resolutionActions"))
    resolution_actions: list[dict[str, Any]] = []
    for item in resolution_actions_raw:
        if isinstance(item, dict):
            resolution_actions.append(
                {
                    "action": _text(item.get("action") or item.get("kind") or item.get("name") or ""),
                    "label": _short_text(item.get("label") or item.get("title") or item.get("summary"), max_chars=180),
                    "path": _safe_rel_path(run_dir, item.get("path") or item.get("artifact_path") or item.get("artifactPath")),
                }
            )
        else:
            text = _text(item)
            if text:
                resolution_actions.append({"action": text, "label": text, "path": ""})
    return {
        "status": _text(payload.get("status") or payload.get("worktree_state") or payload.get("worktreeState") or "pending", "pending"),
        "summary": _short_text(payload.get("summary") or payload.get("message") or payload.get("risk") or "", max_chars=240),
        "pending_path": pending_path.as_posix(),
        "worktree_dir": _text(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree"), ""),
        "patch_path": _text(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch"), ""),
        "cleanup_path": _text(payload.get("cleanup_path") or payload.get("cleanupPath"), ""),
        "base_ref": _text(payload.get("base_ref") or payload.get("baseRef"), ""),
        "head_ref": _text(payload.get("head_ref") or payload.get("headRef"), ""),
        "changed_files": changed_files[:10],
        "resolution_actions": resolution_actions[:10],
    }


def build_cycle_change_summary(
    *,
    repo: Path,
    run_dir: Path,
    cycle_idx: int,
    start_head: str,
    end_head: str,
    changed_files: Sequence[str] | None = None,
    task_results: Sequence[dict[str, Any]] | None = None,
    goals_before: dict[str, Any] | None = None,
    goals_after: dict[str, Any] | None = None,
    goals_update: dict[str, Any] | None = None,
    completion_level: str = "all",
) -> dict[str, Any]:
    changed_files_list = []
    for path in _limit_items(changed_files or [], 200):
        text = _text(path)
        if text and text not in changed_files_list:
            changed_files_list.append(text)

    commits = _summarize_commit_range(repo, start_head, end_head, max_items=10)

    validation_results: list[dict[str, Any]] = []
    task_results_list = [item for item in list(task_results or []) if isinstance(item, dict)]
    for result in task_results_list[:20]:
        artifact_path = _text(result.get("validation_artifact") or result.get("validationArtifact") or "")
        validation_results.append(_summarize_validation_result(result, artifact_path))

    state = {}
    try:
        state = load_state(run_dir / "STATE.json")
    except Exception:
        state = {"done": [], "failed": [], "warnings": []}
    backlog = _load_backlog_tasks(run_dir)
    title_lookup = {task.id: task.title for task in backlog}
    done_ids = set(state.get("done", []) or [])
    state_failed_items = [item for item in (state.get("failed", []) or []) if isinstance(item, dict)]
    failed_artifact = build_failed_tasks_artifact(
        repo,
        run_dir,
        failed_items=state_failed_items,
        task_lookup=title_lookup,
        done_ids=done_ids,
        source="state",
    )

    goals_before_status = goals_before if isinstance(goals_before, dict) else {}
    goals_after_status = goals_after if isinstance(goals_after, dict) else {}
    goals_update_obj = goals_update if isinstance(goals_update, dict) else {}
    checked_items = [text for text in (_text(item) for item in _as_list(goals_update_obj.get("checked_items"))) if text][:20]
    goals_changes = {
        "updated": bool(goals_update_obj.get("updated")),
        "checked_items": checked_items,
        "checked_count": len(checked_items),
        "completion_level": completion_level,
        "before": goals_before_status,
        "after": goals_after_status or goals_update_obj.get("new_status") or {},
    }

    pending_worktree = _summarize_pending_worktree(repo, run_dir)

    task_total = len(task_results_list)
    task_done = len([item for item in task_results_list if _text(item.get("status")) in {"done", "completed"}])
    task_failed = len([
        item for item in task_results_list
        if _text(item.get("status")) in {"failed", "review_required", "blocked_env", "test_contract_changed", "regression_failed"}
    ])
    failure_status_counts: dict[str, int] = {}
    failure_statuses: list[str] = []
    for item in state_failed_items:
        status_key = _text(item.get("task_status") or item.get("taskStatus") or item.get("outcome_status") or item.get("status") or "failed").lower()
        failure_status_counts[status_key] = failure_status_counts.get(status_key, 0) + 1
        failure_statuses.append(status_key)
    failure_group_counts = count_task_status_groups(failure_statuses)
    if state_failed_items:
        task_failed = max(task_failed, len(state_failed_items))
    task_skipped = len([item for item in task_results_list if _text(item.get("status")) == "skipped"])
    validation_statuses = [_normalize_validation_status(item.get("status")) for item in validation_results]
    validation_passed = len([status for status in validation_statuses if status == "passed"])
    validation_failed = len([status for status in validation_statuses if status == "failed"])
    validation_skipped = len([status for status in validation_statuses if status in {"tests_skipped", "validation_pending", "no_tests_found"}])

    summary_bits = [
        f"{len(commits)} commit(s)",
        f"{len(changed_files_list)} changed file(s)",
        f"{len(validation_results)} validation result(s)",
    ]
    if goals_changes["updated"]:
        summary_bits.append(f"{goals_changes['checked_count']} GOALS checkbox update(s)")
    else:
        summary_bits.append("0 GOALS checkbox updates")
    summary_bits.append(f"worktree {pending_worktree['status']}")
    if failed_artifact.get("unresolved_count"):
        summary_bits.append(f"{failed_artifact['unresolved_count']} unresolved failure(s)")
    summary_text = " | ".join(summary_bits)

    return {
        "schema_version": 1,
        "kind": "cycle_change_summary",
        "generated_at": now_iso(),
        "repo": repo.as_posix(),
        "run_dir": run_dir.as_posix(),
        "run_id": run_dir.name,
        "cycle": cycle_idx,
        "start_head": _short_text(start_head, max_chars=40),
        "end_head": _short_text(end_head, max_chars=40),
        "commits": commits,
        "changed_files": changed_files_list,
        "validation_results": validation_results,
        "validation_summary": {
            "total": len(validation_results),
            "passed": validation_passed,
            "failed": validation_failed,
            "skipped": validation_skipped,
            "tasks_total": task_total,
            "tasks_done": task_done,
            "tasks_failed": task_failed,
            "tasks_skipped": task_skipped,
            "tasks_regressed": failure_group_counts.get(STATUS_GROUP_REGRESSION, 0),
            "tasks_review": failure_group_counts.get(STATUS_GROUP_REVIEW, 0),
            "tasks_blocked_env": failure_group_counts.get(STATUS_GROUP_BLOCKED_ENV, 0),
            "tasksRegressed": failure_group_counts.get(STATUS_GROUP_REGRESSION, 0),
            "tasksReview": failure_group_counts.get(STATUS_GROUP_REVIEW, 0),
            "tasksBlockedEnv": failure_group_counts.get(STATUS_GROUP_BLOCKED_ENV, 0),
            "failed_state_total": len(state_failed_items),
            "failure_status_counts": failure_status_counts,
            "failureStatusCounts": failure_status_counts,
            "failure_group_counts": failure_group_counts,
            "failureGroupCounts": failure_group_counts,
        },
        "goals": goals_changes,
        "pending_worktree": pending_worktree,
        "failed_tasks": failed_artifact,
        "summary_text": summary_text,
        "artifacts": {
            "json": (run_dir / "cycle_change_summary.json").as_posix(),
            "markdown": (run_dir / "cycle_change_summary.md").as_posix(),
        },
    }


def _render_cycle_change_summary_md(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Cycle Change Summary")
    lines.append("")
    lines.append(f"- cycle: {summary.get('cycle')}")
    lines.append(f"- start_head: {summary.get('start_head') or '(none)'}")
    lines.append(f"- end_head: {summary.get('end_head') or '(none)'}")
    lines.append(f"- summary: {summary.get('summary_text') or '(none)'}")
    lines.append("")
    commits = _as_list(summary.get("commits"))
    if commits:
        lines.append("## Commits")
        lines.append("")
        for commit in commits:
            if not isinstance(commit, dict):
                continue
            lines.append(f"- {commit.get('full_sha') or commit.get('sha')}: {commit.get('subject') or '(unknown)'}")
        lines.append("")
    changed_files = _as_list(summary.get("changed_files"))
    if changed_files:
        lines.append("## Changed files")
        lines.append("")
        for path in changed_files[:40]:
            lines.append(f"- {path}")
        lines.append("")
    validation = summary.get("validation_summary") if isinstance(summary.get("validation_summary"), dict) else {}
    if validation:
        lines.append("## Validation")
        lines.append("")
        lines.append(
            "- totals: "
            f"tasks={validation.get('tasks_total', 0)} done={validation.get('tasks_done', 0)} "
            f"failed={validation.get('tasks_failed', 0)} skipped={validation.get('tasks_skipped', 0)} "
            f"results={validation.get('total', 0)} passed={validation.get('passed', 0)} "
            f"failed={validation.get('failed', 0)} skipped={validation.get('skipped', 0)}"
        )
        lines.append("")
    goals = summary.get("goals") if isinstance(summary.get("goals"), dict) else {}
    if goals:
        lines.append("## GOALS")
        lines.append("")
        lines.append(f"- updated: {bool(goals.get('updated'))}")
        if goals.get("checked_items"):
            lines.append(f"- checked_items: {', '.join(_text(item) for item in goals.get('checked_items') or [])}")
        before = goals.get("before") if isinstance(goals.get("before"), dict) else {}
        after = goals.get("after") if isinstance(goals.get("after"), dict) else {}
        if before or after:
            lines.append(f"- before: {before.get('completion_status') or before.get('completionStatus') or before.get('project_complete') or 'unknown'}")
            lines.append(f"- after: {after.get('completion_status') or after.get('completionStatus') or after.get('project_complete') or 'unknown'}")
        lines.append("")
    pending_worktree = summary.get("pending_worktree") if isinstance(summary.get("pending_worktree"), dict) else {}
    if pending_worktree and pending_worktree.get("status") != "none":
        lines.append("## Pending worktree")
        lines.append("")
        lines.append(f"- status: {pending_worktree.get('status')}")
        lines.append(f"- summary: {pending_worktree.get('summary') or '(none)'}")
        lines.append(f"- worktree_dir: {pending_worktree.get('worktree_dir') or '(none)'}")
        lines.append(f"- patch_path: {pending_worktree.get('patch_path') or '(none)'}")
        lines.append("")
    failed_tasks = summary.get("failed_tasks") if isinstance(summary.get("failed_tasks"), dict) else {}
    if failed_tasks and failed_tasks.get("items"):
        lines.append("## Unresolved failures")
        lines.append("")
        for item in _as_list(failed_tasks.get("items"))[:20]:
            if not isinstance(item, dict):
                continue
            attempts = item.get("attempts") if isinstance(item.get("attempts"), dict) else {}
            lines.append(
                f"- {item.get('task_id') or item.get('taskId')}: {item.get('title') or '(unknown)'} "
                f"| {item.get('reason') or 'unknown'} | {attempts.get('current', 0)}/{attempts.get('max', 0) or '?'}"
            )
            blockers = item.get("blocked_dependencies") or item.get("blockedDependencies")
            if isinstance(blockers, list) and blockers:
                blocker_bits = []
                for blocker in blockers[:5]:
                    if not isinstance(blocker, dict):
                        continue
                    blocker_bits.append(
                        f"{blocker.get('task_id') or blocker.get('taskId')}:"
                        f"{blocker.get('reason') or 'unknown'}"
                    )
                if blocker_bits:
                    lines.append(f"  - blocked_by: {', '.join(blocker_bits)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_failed_tasks_md(artifact: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Failed Tasks")
    lines.append("")
    lines.append(f"- summary: {artifact.get('summary') or '(none)'}")
    lines.append(f"- unresolved_count: {artifact.get('unresolved_count', 0)}")
    lines.append("")
    items = artifact.get("items") if isinstance(artifact.get("items"), list) else []
    if items:
        lines.append("## Items")
        lines.append("")
        for item in items:
            if not isinstance(item, dict):
                continue
            attempts = item.get("attempts") if isinstance(item.get("attempts"), dict) else {}
            lines.append(
                f"- {item.get('task_id') or item.get('taskId')}: {item.get('title') or '(unknown)'} "
                f"| {item.get('reason') or 'unknown'} | attempts {attempts.get('current', 0)}/{attempts.get('max', 0) or '?'}"
            )
            blockers = item.get("blocked_dependencies") or item.get("blockedDependencies")
            if isinstance(blockers, list) and blockers:
                for blocker in blockers[:5]:
                    if not isinstance(blocker, dict):
                        continue
                    lines.append(
                        f"  - blocked_by: {blocker.get('task_id') or blocker.get('taskId') or '?'} "
                        f"| {blocker.get('title') or '(unknown)'} "
                        f"| status={blocker.get('status') or 'unknown'} "
                        f"| reason={blocker.get('reason') or 'unknown'} "
                        f"| next={blocker.get('next_action') or blocker.get('nextAction') or '(none)'}"
                    )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_cycle_change_summary_artifacts(
    *,
    repo: Path,
    run_dir: Path,
    cycle_idx: int,
    start_head: str,
    end_head: str,
    changed_files: Sequence[str] | None = None,
    task_results: Sequence[dict[str, Any]] | None = None,
    goals_before: dict[str, Any] | None = None,
    goals_after: dict[str, Any] | None = None,
    goals_update: dict[str, Any] | None = None,
    completion_level: str = "all",
) -> dict[str, Any]:
    summary = build_cycle_change_summary(
        repo=repo,
        run_dir=run_dir,
        cycle_idx=cycle_idx,
        start_head=start_head,
        end_head=end_head,
        changed_files=changed_files,
        task_results=task_results,
        goals_before=goals_before,
        goals_after=goals_after,
        goals_update=goals_update,
        completion_level=completion_level,
    )
    json_path = run_dir / "cycle_change_summary.json"
    md_path = run_dir / "cycle_change_summary.md"
    try:
        atomic_write_json(json_path, summary)
    except Exception:
        safe_write_text(json_path, json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n")
    try:
        safe_write_text(md_path, _render_cycle_change_summary_md(summary))
    except Exception:
        pass
    failed_tasks = summary.get("failed_tasks") if isinstance(summary.get("failed_tasks"), dict) else {}
    failed_json_path = run_dir / "failed_tasks.json"
    failed_md_path = run_dir / "failed_tasks.md"
    if failed_tasks:
        try:
            atomic_write_json(failed_json_path, failed_tasks)
        except Exception:
            safe_write_text(failed_json_path, json.dumps(failed_tasks, ensure_ascii=False, indent=2, default=str) + "\n")
        try:
            safe_write_text(failed_md_path, _render_failed_tasks_md(failed_tasks))
        except Exception:
            pass
    summary["artifacts"] = {
        "json": json_path.as_posix(),
        "markdown": md_path.as_posix(),
    }
    return summary


def _cmd_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    if isinstance(value, tuple):
        return [str(part).strip() for part in value if str(part).strip()]
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def _command_status(raw: dict[str, Any]) -> str:
    status = _text(raw.get("status") or raw.get("validation_status") or raw.get("validationStatus"), "").lower()
    if status:
        return status
    summary = _text(raw.get("summary") or raw.get("failure_summary") or raw.get("failureSummary"), "").lower()
    rc = raw.get("rc")
    if summary == "stopped":
        return "stopped"
    try:
        rc_int = int(rc) if rc is not None and rc != "" else None
    except Exception:
        rc_int = None
    if rc_int is None:
        return "skipped"
    return "passed" if rc_int == 0 else "failed"


def _normalize_command_record(raw: dict[str, Any], *, skipped_rationale: str = "", group_name: str = "", group_status: str = "", group_artifact_path: str = "") -> dict[str, Any]:
    rc = raw.get("rc")
    status = _command_status(raw)
    summary = _text(raw.get("summary") or raw.get("failure_summary") or raw.get("failureSummary"), "")
    failure_summary = _text(raw.get("failure_summary") or raw.get("failureSummary"), "")
    if not failure_summary and status == "failed":
        failure_summary = summary
    if not summary and skipped_rationale:
        summary = skipped_rationale
    elapsed = raw.get("elapsed_sec") if raw.get("elapsed_sec") is not None else raw.get("elapsedSec")
    elapsed_sec = round(_float(elapsed, 0.0), 3)
    artifact_path = _text(
        raw.get("artifact_path")
        or raw.get("artifactPath")
        or raw.get("log_path")
        or raw.get("logPath")
        or group_artifact_path,
        "",
    )
    started_at = _text(raw.get("started_at") or raw.get("startedAt"), "")
    ended_at = _text(raw.get("ended_at") or raw.get("endedAt"), "")
    record: dict[str, Any] = {
        "name": _text(raw.get("name") or raw.get("gate") or raw.get("kind") or "validation", "validation"),
        "kind": _text(raw.get("kind") or raw.get("gate") or "validation", "validation"),
        "gate": _text(raw.get("gate") or raw.get("kind") or "validation", "validation"),
        "cmd": _cmd_list(raw.get("cmd") or raw.get("command")),
        "rc": rc,
        "status": status,
        "ok": status == "passed",
        "started_at": started_at,
        "startedAt": started_at,
        "ended_at": ended_at,
        "endedAt": ended_at,
        "elapsed_sec": elapsed_sec,
        "elapsedSec": elapsed_sec,
        "artifact_path": artifact_path,
        "artifactPath": artifact_path,
        "log_path": artifact_path,
        "logPath": artifact_path,
        "summary": summary,
        "failure_summary": failure_summary,
        "failureSummary": failure_summary,
        "skipped_rationale": _text(raw.get("skipped_rationale") or raw.get("skippedRationale"), skipped_rationale),
        "skippedRationale": _text(raw.get("skipped_rationale") or raw.get("skippedRationale"), skipped_rationale),
    }
    if group_name:
        record["group_name"] = group_name
        record["groupName"] = group_name
    if group_status:
        record["group_status"] = group_status
        record["groupStatus"] = group_status
    if group_artifact_path:
        record["group_artifact_path"] = group_artifact_path
        record["groupArtifactPath"] = group_artifact_path
    return record


def _skipped_command_record(
    *,
    name: str,
    kind: str,
    gate: str,
    cmd: list[str],
    rationale: str,
    artifact_path: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "gate": gate,
        "cmd": list(cmd),
        "rc": None,
        "status": "skipped",
        "ok": False,
        "started_at": "",
        "startedAt": "",
        "ended_at": "",
        "endedAt": "",
        "elapsed_sec": 0.0,
        "elapsedSec": 0.0,
        "artifact_path": artifact_path,
        "artifactPath": artifact_path,
        "log_path": artifact_path,
        "logPath": artifact_path,
        "summary": rationale,
        "failure_summary": "",
        "failureSummary": "",
        "skipped_rationale": rationale,
        "skippedRationale": rationale,
    }


_VALIDATION_STATUS_ORDER = {
    "no_tests_found": 0,
    "tests_skipped": 1,
    "validation_pending": 2,
    "stopped": 3,
    "failed": 4,
    "passed": 10,
    "missing": 99,
}


def _normalize_validation_status(value: Any) -> str:
    status = _text(value, "").lower()
    if not status:
        return ""
    aliases = {
        "passed": "passed",
        "pass": "passed",
        "success": "passed",
        "completed": "passed",
        "ok": "passed",
        "validation_passed": "passed",
        "failed": "failed",
        "fail": "failed",
        "error": "failed",
        "validation_failed": "failed",
        "stopped": "stopped",
        "skipped": "tests_skipped",
        "tests_skipped": "tests_skipped",
        "validation_pending": "validation_pending",
        "no_tests_found": "no_tests_found",
    }
    return aliases.get(status, status)


def _choose_validation_status(*statuses: Any) -> str:
    chosen = ""
    chosen_rank = 100
    for raw_status in statuses:
        status = _normalize_validation_status(raw_status)
        if not status:
            continue
        rank = _VALIDATION_STATUS_ORDER.get(status, 50)
        if rank < chosen_rank:
            chosen = status
            chosen_rank = rank
    return chosen


def _infer_skip_validation_status(
    *,
    repo: Path,
    attempt_raw: dict[str, Any],
    config: dict[str, Any],
    skip_records: list[dict[str, Any]],
) -> str:
    overall_status = _text(attempt_raw.get("status") or attempt_raw.get("validation_status") or attempt_raw.get("validationStatus"), "")
    overall_reason = _text(attempt_raw.get("reason") or attempt_raw.get("validation_reason") or attempt_raw.get("validationReason"), "")
    if overall_status == "stopped" or overall_reason == "stop_file":
        return "validation_pending"
    if not skip_records:
        return ""
    rationale_text = " ".join(
        _text(record.get("skipped_rationale") or record.get("skippedRationale") or record.get("summary") or "")
        for record in skip_records
    ).lower()
    if "not triggered by the task file scope" in rationale_text:
        return "tests_skipped"
    if "disabled for this run" in rationale_text:
        return "validation_pending" if not config["run_tests"] else "tests_skipped"
    if "not reached" in rationale_text or "before" in rationale_text:
        return "validation_pending"
    if "skipped because build validation failed" in rationale_text or "skipped because test validation failed" in rationale_text:
        return "validation_pending"
    if "stopped" in rationale_text:
        return "validation_pending"
    return "tests_skipped"


def _validation_attempt_sort_key(path: Path, raw: dict[str, Any]) -> tuple[int, int, str, int, str]:
    return (
        _int(raw.get("cycle"), 0),
        _int(raw.get("step"), 0),
        _text(raw.get("task_id") or raw.get("taskId") or path.parent.parent.name, ""),
        _int(raw.get("attempt"), 0),
        path.as_posix(),
    )


def _load_validation_attempts(run_dir: Path) -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    tasks_root = run_dir / "tasks"
    if not tasks_root.exists() or not tasks_root.is_dir():
        return attempts
    for validation_path in tasks_root.glob("*/attempt_*/validation.json"):
        raw = _json_file(validation_path, {})
        if not isinstance(raw, dict) or not raw:
            continue
        attempts.append({"path": validation_path, "raw": raw})
    attempts.sort(key=lambda item: _validation_attempt_sort_key(item["path"], item["raw"]))
    return attempts


def _load_run_command_config(run_dir: Path) -> dict[str, Any]:
    last_summary = _json_file(run_dir / "last_run_summary.json", {})
    run_summary = _json_file(run_dir / "run_summary.json", {})
    if not isinstance(last_summary, dict):
        last_summary = {}
    if not isinstance(run_summary, dict):
        run_summary = {}
    return {
        "build_enabled": bool(last_summary.get("build_enabled", True)),
        "run_tests": bool(last_summary.get("run_tests", True)),
        "build_cmd": _cmd_list(last_summary.get("build_cmd") or last_summary.get("buildCmd") or run_summary.get("build_cmd") or run_summary.get("buildCmd")),
        "legacy_build_target": _text(last_summary.get("legacy_build_target") or last_summary.get("legacyBuildTarget") or run_summary.get("legacy_build_target") or run_summary.get("legacyBuildTarget"), ""),
        "test_cmd": _cmd_list(last_summary.get("test_cmd") or last_summary.get("testCmd") or run_summary.get("test_cmd") or run_summary.get("testCmd")),
        "legacy_test_target": _text(last_summary.get("legacy_test_target") or last_summary.get("legacyTestTarget") or run_summary.get("legacy_test_target") or run_summary.get("legacyTestTarget"), ""),
        "legacy_test_filter": _text(last_summary.get("legacy_test_filter") or last_summary.get("legacyTestFilter") or run_summary.get("legacy_test_filter") or run_summary.get("legacyTestFilter"), ""),
        "qa_always": bool(last_summary.get("qa_always") or last_summary.get("qaAlways")),
        "qa_to_backlog": bool(last_summary.get("qa_to_backlog") or last_summary.get("qaToBacklog")),
    }


def _build_goals_summary(repo: Path, run_dir: Path) -> dict[str, Any]:
    completion = _json_file(run_dir / "COMPLETION_STATUS.json", {})
    completion_goals = completion.get("goals") if isinstance(completion, dict) else {}
    if isinstance(completion_goals, dict) and completion_goals:
        goals = dict(completion_goals)
        goals["has_goals"] = bool(completion.get("has_goals", completion.get("hasGoals", goals.get("has_goals", False))))
        goals["project_complete"] = bool(completion.get("project_complete", goals.get("project_complete", False)))
        goals["completion_status"] = _text(completion.get("completion_status") or completion.get("completionStatus") or goals.get("completion_status"), "unknown")
        goals["completion_reason"] = _text(completion.get("completion_reason") or completion.get("completionReason") or goals.get("completion_reason"), "")
        goals["stop_reason"] = _text(completion.get("stop_reason") or completion.get("stopReason"), "")
        return goals

    _path, goals_text = read_goals(repo)
    if goals_text:
        parsed = parse_goals_completion(goals_text, completion_level="all")
        parsed["completion_status"] = _text(parsed.get("completion_status") or parsed.get("completionStatus"), "unknown")
        parsed["completion_reason"] = _text(parsed.get("completion_reason") or parsed.get("completionReason"), "")
        parsed["stop_reason"] = _text(completion.get("stop_reason") or completion.get("stopReason"), "")
        return parsed

    return {
        "has_goals": False,
        "project_complete": False,
        "completion_status": "no_goals",
        "completion_reason": "ok",
        "stop_reason": _text(completion.get("stop_reason") or completion.get("stopReason"), ""),
    }


def _build_qa_skip_reasons(
    *,
    repo: Path,
    attempt_raw: dict[str, Any],
    config: dict[str, Any],
    executed_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    task_files = [str(item).replace("\\", "/") for item in _as_list(attempt_raw.get("task_files") or attempt_raw.get("taskFiles")) if str(item).strip()]
    overall_status = _text(attempt_raw.get("status") or attempt_raw.get("validation_status") or attempt_raw.get("validationStatus"), "")
    overall_reason = _text(attempt_raw.get("reason") or attempt_raw.get("validation_reason") or attempt_raw.get("validationReason"), "")
    build_record = next((record for record in executed_records if record["gate"] == "build"), None)
    test_record = next((record for record in executed_records if record["gate"] == "test"), None)
    fast_groups = [record for record in executed_records if record["gate"] == "fast_web_worktree_regression"]
    skip_records: list[dict[str, Any]] = []

    if not build_record:
        if not config["build_enabled"]:
            rationale = "Build validation was disabled for this run."
        elif overall_status == "stopped" or overall_reason == "stop_file":
            rationale = "The run stopped before build validation could run."
        else:
            rationale = "Build validation was not reached."
        planned_build_cmd = config["build_cmd"] or find_build_cmd(repo, config["legacy_build_target"])
        skip_records.append(_skipped_command_record(name="build", kind="compile", gate="build", cmd=planned_build_cmd, rationale=rationale))

    if not test_record:
        if not config["run_tests"]:
            rationale = "Test validation was disabled for this run."
        elif build_record and build_record["status"] == "failed":
            rationale = "Test validation was skipped because build validation failed."
        elif overall_status == "stopped" or overall_reason == "stop_file":
            rationale = "The run stopped before test validation could run."
        else:
            rationale = "Test validation was not reached."
        planned_test_cmd = config["test_cmd"] or find_test_cmd(repo, config["legacy_test_target"], config["legacy_test_filter"])
        skip_records.append(_skipped_command_record(name="test", kind="test", gate="test", cmd=planned_test_cmd, rationale=rationale))

    if not fast_groups:
        if build_record and build_record["status"] == "failed":
            rationale = "Fast web/worktree regression was skipped because build validation failed."
        elif test_record and test_record["status"] == "failed":
            rationale = "Fast web/worktree regression was skipped because test validation failed."
        elif overall_status == "stopped" or overall_reason == "stop_file":
            rationale = "The run stopped before fast web/worktree regression could run."
        elif task_files:
            rationale = "Fast web/worktree regression was not triggered by the task file scope."
        else:
            rationale = "Fast web/worktree regression was not reached."
        for index, test_file in enumerate(FAST_WEB_WORKTREE_REGRESSION_TEST_FILES, start=1):
            cmd = [sys.executable or "python", "-B", "-m", "unittest", "discover", "-s", "tests", "-p", Path(test_file).name]
            skip_records.append(
                _skipped_command_record(
                    name=f"fast_web_worktree_regression_{index:02d}",
                    kind="regression",
                    gate="fast_web_worktree_regression",
                    cmd=cmd,
                    rationale=rationale,
                )
            )

    return skip_records


def _build_qa_attempt_report(repo: Path, attempt_path: Path, attempt_raw: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    executed: list[dict[str, Any]] = []
    command_groups: list[dict[str, Any]] = []
    for raw_record in _as_list(attempt_raw.get("validations") or attempt_raw.get("validation_records") or []):
        if not isinstance(raw_record, dict):
            continue
        gate = _text(raw_record.get("gate") or raw_record.get("kind") or raw_record.get("name"), "validation")
        if gate == "fast_web_worktree_regression" and isinstance(raw_record.get("commands"), list) and raw_record.get("commands"):
            group_status = _text(raw_record.get("status") or raw_record.get("validation_status") or raw_record.get("validationStatus"), "")
            if not group_status:
                group_status = "passed" if bool(raw_record.get("ok", False)) else "failed"
            group_artifact = _text(raw_record.get("artifact_path") or raw_record.get("artifactPath") or raw_record.get("log_path") or raw_record.get("logPath"), "")
            group = _normalize_command_record(raw_record, group_name=gate, group_status=group_status, group_artifact_path=group_artifact)
            command_groups.append(group)
            for nested_raw in _as_list(raw_record.get("commands")):
                if isinstance(nested_raw, dict):
                    nested = dict(nested_raw)
                    nested.setdefault("group_name", gate)
                    nested.setdefault("groupName", gate)
                    nested.setdefault("group_status", group_status)
                    nested.setdefault("groupStatus", group_status)
                    nested.setdefault("group_artifact_path", group_artifact)
                    nested.setdefault("groupArtifactPath", group_artifact)
                    executed.append(_normalize_command_record(nested, group_name=gate, group_status=group_status, group_artifact_path=group_artifact))
            continue
        executed.append(_normalize_command_record(raw_record))

    skip_records = _build_qa_skip_reasons(repo=repo, attempt_raw=attempt_raw, config=config, executed_records=executed + command_groups)

    commands = [dict(item) for item in executed]
    command_counts = {
        "total": len(commands) + len(skip_records),
        "executed": len(commands),
        "passed": len([item for item in commands if item["status"] == "passed"]),
        "failed": len([item for item in commands if item["status"] == "failed"]),
        "stopped": len([item for item in commands if item["status"] == "stopped"]),
        "skipped": len(skip_records),
    }
    raw_status = _normalize_validation_status(
        attempt_raw.get("status") or attempt_raw.get("validation_status") or attempt_raw.get("validationStatus")
    )
    command_statuses = [_normalize_validation_status(item.get("status") or item.get("validation_status") or item.get("validationStatus")) for item in commands]
    command_summaries = [
        _text(item.get("summary") or item.get("failure_summary") or item.get("failureSummary") or "")
        for item in commands
    ]
    if command_counts["failed"] > 0 or raw_status == "failed":
        attempt_status = "failed"
    elif command_counts["stopped"] > 0 or raw_status == "stopped":
        attempt_status = "stopped"
    elif "no_tests_found" in command_statuses or raw_status == "no_tests_found" or any(
        looks_like_no_tests_found(text) for text in command_summaries
    ):
        attempt_status = "no_tests_found"
    elif raw_status in {"validation_pending", "tests_skipped"}:
        attempt_status = raw_status
    else:
        inferred_skip = ""
        if command_counts["executed"] == 0:
            inferred_skip = _infer_skip_validation_status(repo=repo, attempt_raw=attempt_raw, config=config, skip_records=skip_records)
        if inferred_skip:
            attempt_status = inferred_skip
        elif command_counts["executed"] > 0:
            attempt_status = "passed"
        elif command_counts["skipped"] > 0:
            attempt_status = "validation_pending" if not config["run_tests"] else "tests_skipped"
        elif raw_status:
            attempt_status = raw_status
        else:
            attempt_status = "skipped"

    attempt_summary = _text(
        attempt_raw.get("detail")
        or attempt_raw.get("failure_summary")
        or attempt_raw.get("failureSummary")
        or attempt_raw.get("summary"),
        "",
    )
    if not attempt_summary and skip_records:
        attempt_summary = skip_records[0]["skipped_rationale"]

    elapsed_candidates = [item["elapsed_sec"] for item in commands if item["elapsed_sec"] is not None]
    elapsed_sec = round(sum(_float(value, 0.0) for value in elapsed_candidates), 3) if elapsed_candidates else 0.0
    if elapsed_sec == 0.0:
        elapsed_sec = round(sum(_float(item.get("elapsed_sec"), 0.0) for item in command_groups if isinstance(item, dict)), 3)

    validation_path = attempt_path
    artifact_path = _text(attempt_raw.get("artifact_path") or attempt_raw.get("artifactPath") or validation_path.as_posix(), validation_path.as_posix())
    task_id = _text(attempt_raw.get("task_id") or attempt_raw.get("taskId"), "")
    task_title = _text(attempt_raw.get("task_title") or attempt_raw.get("taskTitle"), "")
    report = {
        "schema_version": 1,
        "kind": "qa_validation_attempt",
        "task_id": task_id,
        "taskId": task_id,
        "task_title": task_title,
        "taskTitle": task_title,
        "cycle": _int(attempt_raw.get("cycle"), 0),
        "step": _int(attempt_raw.get("step"), 0),
        "attempt": _int(attempt_raw.get("attempt"), 0),
        "status": attempt_status,
        "reason": _text(attempt_raw.get("reason") or attempt_raw.get("validation_reason") or attempt_raw.get("validationReason"), attempt_status),
        "detail": attempt_summary,
        "artifact_path": artifact_path,
        "artifactPath": artifact_path,
        "validation_path": validation_path.as_posix(),
        "validationPath": validation_path.as_posix(),
        "commands": commands,
        "command_groups": command_groups,
        "commandGroups": command_groups,
        "skipped_commands": skip_records,
        "skippedCommands": skip_records,
        "command_counts": command_counts,
        "commandCounts": command_counts,
        "elapsed_sec": elapsed_sec,
        "elapsedSec": elapsed_sec,
    }
    if attempt_summary:
        report["summary"] = attempt_summary
    return report


def build_qa_validation_report(repo: Path, run_dir: Path) -> dict[str, Any]:
    config = _load_run_command_config(run_dir)
    attempts_raw = _load_validation_attempts(run_dir)
    attempts = [_build_qa_attempt_report(repo, item["path"], item["raw"], config) for item in attempts_raw]
    passed = len([item for item in attempts if item["status"] == "passed"])
    failed = len([item for item in attempts if item["status"] == "failed"])
    stopped = len([item for item in attempts if item["status"] == "stopped"])
    skipped = len([item for item in attempts if item["status"] == "skipped"])
    command_total = sum(int(item["command_counts"]["total"]) for item in attempts) if attempts else 0
    command_executed = sum(int(item["command_counts"]["executed"]) for item in attempts) if attempts else 0
    command_passed = sum(int(item["command_counts"]["passed"]) for item in attempts) if attempts else 0
    command_failed = sum(int(item["command_counts"]["failed"]) for item in attempts) if attempts else 0
    command_stopped = sum(int(item["command_counts"]["stopped"]) for item in attempts) if attempts else 0
    command_skipped = sum(int(item["command_counts"]["skipped"]) for item in attempts) if attempts else 0
    report_status = "missing"
    if attempts:
        attempt_statuses = [_normalize_validation_status(item.get("status")) for item in attempts]
        if "failed" in attempt_statuses or command_failed > 0 or failed > 0:
            report_status = "failed"
        elif "stopped" in attempt_statuses or command_stopped > 0 or stopped > 0:
            report_status = "stopped"
        elif "no_tests_found" in attempt_statuses:
            report_status = "no_tests_found"
        elif "tests_skipped" in attempt_statuses:
            report_status = "tests_skipped"
        elif "validation_pending" in attempt_statuses:
            report_status = "validation_pending"
        elif command_executed > 0:
            report_status = "passed"
        else:
            report_status = "skipped"

    artifacts = {
        "json": (run_dir / "QA_VALIDATION_REPORT.json").as_posix(),
        "markdown": (run_dir / "QA_VALIDATION_REPORT.md").as_posix(),
    }

    summary = {
        "attempts": len(attempts),
        "task_attempts": len(attempts),
        "commands_total": command_total,
        "commands_executed": command_executed,
        "commands_passed": command_passed,
        "commands_failed": command_failed,
        "commands_stopped": command_stopped,
        "commands_skipped": command_skipped,
    }
    if attempts:
        summary_text = f"{summary['commands_executed']} executed, {summary['commands_passed']} passed, {summary['commands_failed']} failed, {summary['commands_skipped']} skipped"
    else:
        summary_text = "No QA validation artifacts were found."

    return {
        "schema_version": 1,
        "kind": "qa_validation_report",
        "generated_at": now_iso(),
        "repo": str(repo),
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "status": report_status,
        "summary": summary,
        "summary_text": summary_text,
        "artifacts": artifacts,
        "attempts": attempts,
    }


def _flatten_qa_commands(report: dict[str, Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for attempt in _as_list(report.get("attempts") or []):
        if not isinstance(attempt, dict):
            continue
        for item in _as_list(attempt.get("commands") or []):
            if isinstance(item, dict):
                flattened.append(item)
        for item in _as_list(attempt.get("skipped_commands") or []):
            if isinstance(item, dict):
                flattened.append(item)
    return flattened


def _load_run_progress(run_dir: Path) -> dict[str, Any]:
    progress = _json_file(run_dir / "progress.txt", {})
    if isinstance(progress, dict):
        return progress
    return {}


def _build_run_failures(state: dict[str, Any], backlog: list[TaskItem]) -> list[dict[str, Any]]:
    backlog_by_id = {task.id: task for task in backlog}
    failures: list[dict[str, Any]] = []
    for raw in _as_list(state.get("failed") or []):
        if not isinstance(raw, dict):
            continue
        task_id = _text(raw.get("task") or raw.get("task_id") or raw.get("taskId"), "")
        task = backlog_by_id.get(task_id)
        failures.append(
            {
                "task_id": task_id,
                "taskId": task_id,
                "task_title": task.title if task else _text(raw.get("task_title") or raw.get("taskTitle"), ""),
                "taskTitle": task.title if task else _text(raw.get("task_title") or raw.get("taskTitle"), ""),
                "reason": _text(raw.get("reason"), ""),
                "detail": _text(raw.get("detail"), ""),
                "attempt": _int(raw.get("attempt"), 0),
                "cycle": _int(raw.get("cycle"), 0),
                "step": _int(raw.get("step"), 0),
                "rc": raw.get("rc"),
            }
        )
    return failures


def _build_next_actions(
    *,
    repo: Path,
    run_dir: Path,
    stop_reason: str,
    goals: dict[str, Any],
    qa_report: dict[str, Any],
    state: dict[str, Any],
    failures: list[dict[str, Any]],
) -> list[str]:
    actions: list[str] = []
    if stop_reason == "stop_file":
        actions.append("Remove the STOP file and resume the run.")
    if failures:
        first_failure = failures[0]
        task_label = first_failure.get("task_title") or first_failure.get("task_id") or "the failed task"
        reason = first_failure.get("reason") or "failure"
        actions.append(f"Fix {task_label} ({reason}) and rerun.")
    if qa_report.get("status") == "failed":
        actions.append("Review the QA validation report before resuming.")
    if bool(goals.get("has_goals")) and not bool(goals.get("project_complete")):
        actions.append("Continue the remaining GOALS items.")
    done_count = len(_as_list(state.get("done") or []))
    failed_count = len(failures)
    skipped_count = len(_as_list(state.get("warnings") or []))
    if done_count or failed_count or skipped_count:
        actions.append("Resume with --resume-latest.")
    if not actions:
        actions.append("No follow-up action was recorded.")
    deduped: list[str] = []
    seen: set[str] = set()
    for item in actions:
        text = _text(item, "")
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped[:4]


def _build_final_run_report(repo: Path, run_dir: Path, *, stop_reason: str, qa_report: dict[str, Any]) -> dict[str, Any]:
    state = {}
    try:
        state = load_state(run_dir / "STATE.json")
    except Exception:
        state = {"done": [], "failed": [], "warnings": []}
    backlog = _load_backlog_tasks(run_dir)
    state_counts = count_state_task_ids(state, load_backlog_task_ids(run_dir / "BACKLOG.json"))
    tasks_total = len(backlog)
    tasks_done = _int(state_counts.get("done"), 0)
    tasks_failed = _int(state_counts.get("failed"), 0)
    run_summary = _json_file(run_dir / "run_summary.json", {})
    last_summary = _json_file(run_dir / "last_run_summary.json", {})
    if not isinstance(run_summary, dict):
        run_summary = {}
    if not isinstance(last_summary, dict):
        last_summary = {}
    tasks_skipped = _int(last_summary.get("skipped"), _int(state_counts.get("warnings"), 0))
    tasks_pending = max(0, tasks_total - tasks_done - tasks_failed - tasks_skipped)
    completion = _build_goals_summary(repo, run_dir)
    failures = _build_run_failures(state, backlog)
    validation_summary = {
        "status": _text(qa_report.get("status"), "missing"),
        "attempts": _int(qa_report.get("summary", {}).get("attempts"), 0),
        "commands_total": _int(qa_report.get("summary", {}).get("commands_total"), 0),
        "commands_passed": _int(qa_report.get("summary", {}).get("commands_passed"), 0),
        "commands_failed": _int(qa_report.get("summary", {}).get("commands_failed"), 0),
        "commands_skipped": _int(qa_report.get("summary", {}).get("commands_skipped"), 0),
        "report_path": (run_dir / "QA_VALIDATION_REPORT.json").as_posix(),
        "reportMarkdownPath": (run_dir / "QA_VALIDATION_REPORT.md").as_posix(),
    }
    next_actions = _build_next_actions(
        repo=repo,
        run_dir=run_dir,
        stop_reason=stop_reason,
        goals=completion,
        qa_report=qa_report,
        state=state,
        failures=failures,
    )
    run_status = _text(last_summary.get("status"), _text(run_summary.get("final", {}).get("reason"), "unknown"))
    if run_status == "success":
        run_status = "completed"
    if run_status == "ok":
        run_status = "completed"
    execution_status = _text(last_summary.get("status"), "")
    if execution_status == "success":
        execution_status = "completed"
    if execution_status == "ok":
        execution_status = "completed"
    if not execution_status:
        execution_status = "completed" if _int(last_summary.get("rc"), 0) == 0 else "failed"
    summary_bits = [
        f"{tasks_done}/{tasks_total} tasks done",
        f"GOALS {completion.get('completion_status', 'unknown')}",
        f"QA {validation_summary['status']}",
    ]
    if tasks_failed:
        summary_bits.append(f"{tasks_failed} failed task(s)")
    if tasks_skipped:
        summary_bits.append(f"{tasks_skipped} skipped task(s)")
    if next_actions:
        summary_bits.append(f"next: {next_actions[0]}")

    report = {
        "schema_version": 1,
        "kind": "final_run_report",
        "generated_at": now_iso(),
        "repo": str(repo),
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "status": run_status,
        "execution_status": execution_status,
        "stop_reason": stop_reason,
        "summary": "; ".join(summary_bits),
        "tasks": {
            "total": tasks_total,
            "done": tasks_done,
            "failed": tasks_failed,
            "skipped": tasks_skipped,
            "pending": tasks_pending,
        },
        "goals": completion,
        "validation": validation_summary,
        "failures": failures,
        "next_actions": next_actions,
        "artifacts": {
            "qa_validation_json": validation_summary["report_path"],
            "qa_validation_markdown": validation_summary["reportMarkdownPath"],
            "final_run_json": (run_dir / "FINAL_RUN_REPORT.json").as_posix(),
            "final_run_markdown": (run_dir / "FINAL_RUN_REPORT.md").as_posix(),
            "shutdown_report": (run_dir / "SHUTDOWN_REPORT.md").as_posix(),
        },
        "run_summary": run_summary,
        "last_run_summary": last_summary,
    }
    return report


def _render_qa_validation_report_md(report: dict[str, Any]) -> str:
    summary_data = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    lines: list[str] = []
    lines.append("# QA Validation Report")
    lines.append("")
    lines.append(f"- run_id: {report.get('run_id')}")
    lines.append(f"- status: {report.get('status')}")
    lines.append(f"- summary: {report.get('summary_text')}")
    lines.append(f"- commands_total: {summary_data.get('commands_total', 0)}")
    lines.append(f"- commands_passed: {summary_data.get('commands_passed', 0)}")
    lines.append(f"- commands_failed: {summary_data.get('commands_failed', 0)}")
    lines.append(f"- commands_skipped: {summary_data.get('commands_skipped', 0)}")
    lines.append("")
    for attempt in _as_list(report.get("attempts") or []):
        if not isinstance(attempt, dict):
            continue
        lines.append(f"## {attempt.get('task_title') or attempt.get('task_id') or 'Attempt'}")
        lines.append("")
        lines.append(f"- status: {attempt.get('status')}")
        lines.append(f"- reason: {attempt.get('reason')}")
        lines.append(f"- artifact_path: {attempt.get('artifact_path')}")
        lines.append(f"- validation_path: {attempt.get('validation_path')}")
        lines.append(f"- command_counts: {json.dumps(attempt.get('command_counts') or {}, ensure_ascii=False)}")
        lines.append("")
        for command in _as_list(attempt.get("commands") or []):
            if not isinstance(command, dict):
                continue
            cmd_text = " ".join(command.get("cmd") or [])
            lines.append(f"### {command.get('name')}")
            lines.append(f"- status: {command.get('status')}")
            lines.append(f"- rc: {command.get('rc')}")
            if cmd_text:
                lines.append(f"- cmd: {cmd_text}")
            if command.get("artifact_path"):
                lines.append(f"- artifact: {command.get('artifact_path')}")
            if command.get("elapsed_sec") is not None:
                lines.append(f"- elapsed_sec: {command.get('elapsed_sec')}")
            if command.get("skipped_rationale"):
                lines.append(f"- skipped: {command.get('skipped_rationale')}")
            lines.append("")
        for command in _as_list(attempt.get("skipped_commands") or []):
            if not isinstance(command, dict):
                continue
            cmd_text = " ".join(command.get("cmd") or [])
            lines.append(f"### {command.get('name')}")
            lines.append(f"- status: {command.get('status')}")
            if cmd_text:
                lines.append(f"- cmd: {cmd_text}")
            if command.get("skipped_rationale"):
                lines.append(f"- skipped: {command.get('skipped_rationale')}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_final_run_report_md(report: dict[str, Any]) -> str:
    tasks = report.get("tasks") if isinstance(report.get("tasks"), dict) else {}
    validation = report.get("validation") if isinstance(report.get("validation"), dict) else {}
    goals = report.get("goals") if isinstance(report.get("goals"), dict) else {}
    lines: list[str] = []
    lines.append("# Final Run Report")
    lines.append("")
    lines.append(f"- run_id: {report.get('run_id')}")
    lines.append(f"- status: {report.get('status')}")
    lines.append(f"- execution_status: {report.get('execution_status')}")
    lines.append(f"- stop_reason: {report.get('stop_reason')}")
    lines.append(f"- summary: {report.get('summary')}")
    lines.append("")
    lines.append("## Tasks")
    lines.append("")
    lines.append(f"- total: {tasks.get('total', 0)}")
    lines.append(f"- done: {tasks.get('done', 0)}")
    lines.append(f"- failed: {tasks.get('failed', 0)}")
    lines.append(f"- skipped: {tasks.get('skipped', 0)}")
    lines.append(f"- pending: {tasks.get('pending', 0)}")
    lines.append("")
    lines.append("## Goals")
    lines.append("")
    lines.append(f"- project_complete: {goals.get('project_complete', False)}")
    lines.append(f"- completion_status: {goals.get('completion_status', 'unknown')}")
    lines.append(f"- completion_reason: {goals.get('completion_reason', '')}")
    lines.append("")
    lines.append("## Validation")
    lines.append("")
    lines.append(f"- status: {validation.get('status', 'missing')}")
    lines.append(f"- attempts: {validation.get('attempts', 0)}")
    lines.append(f"- commands_total: {validation.get('commands_total', 0)}")
    lines.append(f"- commands_passed: {validation.get('commands_passed', 0)}")
    lines.append(f"- commands_failed: {validation.get('commands_failed', 0)}")
    lines.append(f"- commands_skipped: {validation.get('commands_skipped', 0)}")
    lines.append("")
    lines.append("## Next Actions")
    lines.append("")
    next_actions = _as_list(report.get("next_actions") or [])
    if next_actions:
        for item in next_actions:
            lines.append(f"- {item}")
    else:
        lines.append("- No follow-up action was recorded.")
    lines.append("")
    failures = _as_list(report.get("failures") or [])
    if failures:
        lines.append("## Failures")
        lines.append("")
        for item in failures:
            if not isinstance(item, dict):
                continue
            label = item.get("task_title") or item.get("task_id") or "failure"
            reason = item.get("reason") or "unknown"
            detail = item.get("detail") or ""
            lines.append(f"- {label}: {reason}{f' | {detail}' if detail else ''}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_run_report_artifacts(
    *,
    repo: Path,
    run_dir: Path,
    stop_reason: str,
    last_task_id: Optional[str] = None,
) -> dict[str, Any]:
    """Write QA and final run reports for browser consumption."""
    qa_report = build_qa_validation_report(repo, run_dir)
    final_report = _build_final_run_report(repo, run_dir, stop_reason=stop_reason, qa_report=qa_report)
    analyzer_result: dict[str, Any] = {}
    if last_task_id:
        final_report["last_task_id"] = last_task_id
    qa_json = run_dir / "QA_VALIDATION_REPORT.json"
    qa_md = run_dir / "QA_VALIDATION_REPORT.md"
    final_json = run_dir / "FINAL_RUN_REPORT.json"
    final_md = run_dir / "FINAL_RUN_REPORT.md"
    try:
        atomic_write_json(qa_json, qa_report)
    except Exception:
        safe_write_text(qa_json, json.dumps(qa_report, ensure_ascii=False, indent=2, default=str) + "\n")
    try:
        safe_write_text(qa_md, _render_qa_validation_report_md(qa_report))
    except Exception:
        pass
    try:
        atomic_write_json(final_json, final_report)
    except Exception:
        safe_write_text(final_json, json.dumps(final_report, ensure_ascii=False, indent=2, default=str) + "\n")
    try:
        safe_write_text(final_md, _render_final_run_report_md(final_report))
    except Exception:
        pass
    try:
        analyzer_result = write_analyzer_artifacts(repo, run_dir)
    except Exception:
        analyzer_result = {}
    return {
        "qa_validation_report": qa_report,
        "final_run_report": final_report,
        "analyzer_summary": analyzer_result.get("summary") if isinstance(analyzer_result, dict) else None,
        "artifacts": {
            "qa_validation_json": qa_json.as_posix(),
            "qa_validation_markdown": qa_md.as_posix(),
            "final_run_json": final_json.as_posix(),
            "final_run_markdown": final_md.as_posix(),
            "analyzer_summary_json": str(
                (
                    (analyzer_result.get("artifacts") or {}).get("summary_json")
                    if isinstance(analyzer_result, dict)
                    else ""
                )
                or (run_dir / "ANALYZER_SUMMARY.json").as_posix()
            ),
            "experience_updates_jsonl": str(
                (
                    (analyzer_result.get("artifacts") or {}).get("experience_updates_jsonl")
                    if isinstance(analyzer_result, dict)
                    else ""
                )
                or (run_dir / "EXPERIENCE_UPDATES.jsonl").as_posix()
            ),
        },
    }


def build_local_shutdown_report(
    *,
    repo: Path,
    run_dir: Path,
    reason: str,
    last_task_id: Optional[str] = None,
) -> str:
    ctx = collect_shutdown_context(repo, run_dir)

    tasks_done = int(ctx.get("tasks_done") or 0)
    tasks_total = int(ctx.get("tasks_total") or 0)

    todo_path = (ctx.get("todo_path") or "").strip()
    todo_text = (ctx.get("todo_text") or "").strip()
    todo_preview = "\n".join(todo_text.splitlines()[:60]) if todo_text else "(none)"

    backlog_lines = ctx.get("backlog_lines") or []
    backlog_preview = "\n".join(backlog_lines[:120]) if backlog_lines else "(no backlog found)"

    porcelain = (ctx.get("git_porcelain") or "").strip()
    porcelain_preview = "\n".join(porcelain.splitlines()[:200]) if porcelain else "(clean or unavailable)"

    untracked = ctx.get("untracked") or []
    untracked_preview = "\n".join([f"- {p}" for p in untracked[:80]]) if untracked else "(none)"

    latest_dev_log_path = (ctx.get("latest_dev_log_path") or "").strip() or "(none)"
    latest_dev_log_tail = (ctx.get("latest_dev_log_tail") or "").strip() or "(none)"

    build_tail = (ctx.get("build_log_tail") or "").strip()
    test_tail = (ctx.get("test_log_tail") or "").strip()

    hints = ctx.get("analysis_hints") or []
    hints_preview = "\n".join([f"- {n}" for n in hints[:40]]) if hints else "(none)"

    cycle_summary_tail = (ctx.get("cycle_summary_tail") or "").strip() or "(none)"
    cycle_change_summary = ctx.get("cycle_change_summary") or {}

    resume_cmd = (
        f"python agent_cli.py --repo \"{repo}\" --resume-latest --continuous --autopilot"
    )

    lines: list[str] = []
    lines.append("# Shutdown Report")
    lines.append("")
    lines.append(f"- generated_at: {ctx.get('generated_at')}")
    lines.append(f"- reason: {reason}")
    if last_task_id:
        lines.append(f"- last_task: {last_task_id}")
    lines.append(f"- repo: {repo}")
    lines.append(f"- run_dir: {run_dir}")
    head = (ctx.get("git_head") or "").strip()
    if head:
        lines.append(f"- git_head: {head}")
    lines.append("")

    lines.append("## Progress")
    lines.append("")
    lines.append(f"- done: {tasks_done}/{tasks_total}")
    lines.append("")

    lines.append("## Backlog snapshot")
    lines.append("")
    lines.append(backlog_preview)
    lines.append("")

    lines.append("## State")
    lines.append("")
    state = ctx.get("state") or {}
    state_counts = ctx.get("state_counts") or {}
    try:
        failed = state.get("failed") or []
        warnings = state.get("warnings") or []
        failed_count = state_counts.get("failed") if isinstance(state_counts, dict) else None
        if failed_count is None:
            failed_count = len(failed)
        warnings_count = state_counts.get("warnings") if isinstance(state_counts, dict) else None
        if warnings_count is None:
            warnings_count = len(warnings)
        lines.append(f"- failed_count: {int(failed_count)}")
        lines.append(f"- warnings_count: {int(warnings_count)}")
        if failed:
            lines.append("")
            lines.append("### Failed")
            for item in failed[:20]:
                if isinstance(item, dict):
                    lines.append(f"- {item.get('task')} ({item.get('reason')})")
                else:
                    lines.append(f"- {str(item)}")
        if warnings:
            lines.append("")
            lines.append("### Warnings")
            for w in warnings[:20]:
                if isinstance(w, dict):
                    lines.append(f"- {w.get('task')} ({w.get('reason')})")
                else:
                    lines.append(f"- {str(w)}")
    except Exception:
        lines.append("- (state parse failed)")
    lines.append("")

    policy_summary = ctx.get("policy_scan_summary") or {}
    if policy_summary:
        counts = policy_summary.get("counts") or {}
        items = policy_summary.get("items") or []
        lines.append("## Policy scan summary")
        lines.append("")
        lines.append(
            "- counts: "
            f"high={counts.get('high', 0)} "
            f"medium={counts.get('medium', 0)} "
            f"low={counts.get('low', 0)} "
            f"other={counts.get('other', 0)} "
            f"total={policy_summary.get('total', 0)} "
            f"fail_total={policy_summary.get('fail_total', 0)}"
        )
        fail_severity = policy_summary.get("fail_severity")
        if fail_severity:
            lines.append(f"- fail_severity: {fail_severity}")
        if items:
            lines.append("")
            lines.append("### Top policy violations (sample)")
            for v in items:
                path = v.get("path") or "(unknown)"
                rule = v.get("rule_id") or "(rule)"
                sev = v.get("severity") or "unknown"
                preview = v.get("match_preview") or ""
                lines.append(f"- [{sev}] {path} :: {rule} :: {preview}")
        lines.append("")
        lines.append("### False positive guidance")
        lines.append(
            "- Consider adding a safe allow pattern to `policy.allow_patterns` in config "
            "or via `/add policy_allow_pattern <regex>` in the shell if this is a known false positive."
        )
        lines.append("- Review `policy.fail_severity` to control which severities stop the run.")
        lines.append("")

    lines.append("## Git status (porcelain)")
    lines.append("")
    lines.append("```text")
    lines.append(porcelain_preview)
    lines.append("```")
    lines.append("")

    lines.append("## Untracked files")
    lines.append("")
    lines.append(untracked_preview)
    lines.append("")

    lines.append("## Recent Dev output")
    lines.append("")
    lines.append(f"- latest_dev_log: {latest_dev_log_path}")
    lines.append("")
    lines.append("```text")
    lines.append(latest_dev_log_tail)
    lines.append("```")
    lines.append("")

    if build_tail:
        lines.append("## Recent build log tail")
        lines.append("")
        lines.append("```text")
        lines.append(build_tail)
        lines.append("```")
        lines.append("")

    if test_tail:
        lines.append("## Recent test log tail")
        lines.append("")
        lines.append("```text")
        lines.append(test_tail)
        lines.append("```")
        lines.append("")

    lines.append("## Analysis hints")
    lines.append("")
    lines.append(hints_preview)
    lines.append("")

    lines.append("## Cycle summary tail")
    lines.append("")
    lines.append("```text")
    lines.append(cycle_summary_tail)
    lines.append("```")
    lines.append("")

    if isinstance(cycle_change_summary, dict) and cycle_change_summary:
        lines.append("## Cycle change summary")
        lines.append("")
        lines.append(f"- summary: {cycle_change_summary.get('summary_text') or '(none)'}")
        lines.append(f"- commits: {len(cycle_change_summary.get('commits') or [])}")
        lines.append(f"- changed_files: {len(cycle_change_summary.get('changed_files') or [])}")
        lines.append(f"- validations: {len(cycle_change_summary.get('validation_results') or [])}")
        failed_tasks = cycle_change_summary.get("failed_tasks") if isinstance(cycle_change_summary.get("failed_tasks"), dict) else {}
        if failed_tasks and failed_tasks.get("items"):
            lines.append(f"- unresolved_failures: {len(failed_tasks.get('items') or [])}")
        pending_worktree = cycle_change_summary.get("pending_worktree") if isinstance(cycle_change_summary.get("pending_worktree"), dict) else {}
        if pending_worktree:
            lines.append(f"- pending_worktree: {pending_worktree.get('status') or 'unknown'}")
        lines.append("")

    lines.append("## TODO (selected)")
    lines.append("")
    lines.append(f"- todo_path: {todo_path or '(none)'}")
    lines.append("")
    lines.append("```text")
    lines.append(todo_preview)
    lines.append("```")
    lines.append("")

    lines.append("## How to resume")
    lines.append("")
    lines.append("```bash")
    lines.append(resume_cmd)
    lines.append("```")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_emergency_shutdown_report(
    run_dir: Path,
    reason: str,
    *,
    repo: Optional[Path] = None,
) -> Optional[Path]:
    """Generate EMERGENCY_SHUTDOWN.md using existing report infrastructure.

    Designed to be called from exception handlers — never raises.
    Skips if SHUTDOWN_REPORT.md already exists (normal shutdown already handled).
    Returns the path to the written file, or None on skip/error.
    """
    try:
        run_dir = Path(run_dir)
        if not run_dir.exists():
            return None

        # Skip if normal shutdown report already exists
        if (run_dir / "SHUTDOWN_REPORT.md").exists():
            return None

        emergency_path = run_dir / "EMERGENCY_SHUTDOWN.md"
        # Also skip if emergency report already exists
        if emergency_path.exists():
            return None

        # Infer repo from run_dir if not provided
        if repo is None:
            # run_dir is typically <repo>/.agent_runs/<run_id>
            candidate = run_dir.parent.parent
            if candidate.exists() and (candidate / ".git").exists():
                repo = candidate
            else:
                repo = run_dir

        report = build_local_shutdown_report(
            repo=repo,
            run_dir=run_dir,
            reason=f"EMERGENCY: {reason}",
        )
        emergency_path.write_text(report, encoding="utf-8", errors="replace")
        return emergency_path
    except Exception:
        return None
