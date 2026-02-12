from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .docs import read_text_robust
from .gitops import git_head, git_porcelain, list_untracked
from .state import TaskItem, load_backlog_json, parse_backlog_md, load_state
from .todo import read_current_todo
from .utils import now_iso


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
    task_ids = [t.id for t in tasks]
    done_set = set(state.get("done", []) or [])

    ctx["state"] = state
    ctx["tasks_total"] = len(tasks)
    ctx["tasks_done"] = len([tid for tid in task_ids if tid in done_set])

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
    try:
        failed = state.get("failed") or []
        warnings = state.get("warnings") or []
        lines.append(f"- failed_count: {len(failed)}")
        lines.append(f"- warnings_count: {len(warnings)}")
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
