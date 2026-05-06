from __future__ import annotations

from pathlib import Path
from typing import Any

from .utils import atomic_write_json, atomic_write_text, now_iso


WEB_REPORT_EXPORT_JSON = "WEB_REPORT_EXPORT.json"
WEB_REPORT_EXPORT_MD = "WEB_REPORT_EXPORT.md"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(*values: Any, default: str = "") -> str:
    for value in values:
        if value in (None, False):
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _report_summary(report: dict[str, Any]) -> str:
    summary = report.get("summary")
    if isinstance(summary, str):
        return summary.strip()
    return _text(
        report.get("summary_text"),
        report.get("summaryText"),
        report.get("summaryLine"),
        report.get("summary_line"),
    )


def _report_counts(report: dict[str, Any]) -> dict[str, int]:
    summary = _as_dict(report.get("summary"))
    validation = _as_dict(report.get("validation"))
    attempts = _as_list(report.get("attempts"))
    return {
        "attempts": _int(summary.get("attempts") or summary.get("task_attempts") or len(attempts)),
        "commands_total": _int(summary.get("commands_total") or validation.get("commands_total")),
        "commands_executed": _int(summary.get("commands_executed") or validation.get("commands_executed")),
        "commands_passed": _int(summary.get("commands_passed") or validation.get("commands_passed")),
        "commands_failed": _int(summary.get("commands_failed") or validation.get("commands_failed")),
        "commands_stopped": _int(summary.get("commands_stopped") or validation.get("commands_stopped")),
        "commands_skipped": _int(summary.get("commands_skipped") or validation.get("commands_skipped")),
    }


def _report_payload(report: dict[str, Any], *, fallback_status: Any = "") -> dict[str, Any]:
    validation = _as_dict(report.get("validation"))
    return {
        "status": _text(report.get("status"), validation.get("status"), fallback_status, default="missing"),
        "summary": _report_summary(report),
        "counts": _report_counts(report),
        "validation_status": _text(validation.get("status"), report.get("status"), default="missing"),
        "next_actions": [
            _text(item)
            for item in _as_list(report.get("next_actions") or report.get("nextActions"))
            if _text(item)
        ][:10],
    }


def build_web_report_export_payload(
    repo: Path,
    run_dir: Path,
    history_item: dict[str, Any],
) -> dict[str, Any]:
    final_report = _as_dict(history_item.get("finalRunReport") or history_item.get("final_run_report"))
    qa_report = _as_dict(history_item.get("qaValidationReport") or history_item.get("qa_validation_report"))
    cycle_change = _as_dict(history_item.get("cycleChangeSummary") or history_item.get("cycle_change_summary"))
    failed_tasks = _as_dict(history_item.get("failedTasks") or history_item.get("failed_tasks"))
    operations = _as_dict(history_item.get("operationsSummary") or history_item.get("operations_summary"))
    task_counts = _as_dict(history_item.get("taskCounts") or history_item.get("task_counts"))
    report_artifacts = dict(_as_dict(history_item.get("reportArtifacts") or history_item.get("report_artifacts")))

    report_artifacts.update(
        {
            "webReportExportJson": (run_dir / WEB_REPORT_EXPORT_JSON).as_posix(),
            "webReportExportMarkdown": (run_dir / WEB_REPORT_EXPORT_MD).as_posix(),
        }
    )

    failed_summary = _as_dict(cycle_change.get("failed_tasks") or cycle_change.get("failedTasks") or failed_tasks)
    operations_counts = _as_dict(operations.get("counts"))

    return {
        "schema_version": 1,
        "generated_at": now_iso(),
        "run": {
            "id": _text(history_item.get("id"), run_dir.name),
            "repo": repo.as_posix(),
            "run_dir": run_dir.as_posix(),
            "branch": _text(history_item.get("branch"), default="HEAD"),
            "status": _text(history_item.get("status"), default="unknown"),
            "execution_status": _text(history_item.get("executionStatus"), history_item.get("execution_status"), default="unknown"),
            "project_status": _text(history_item.get("projectStatus"), history_item.get("project_status"), default="unknown"),
            "completion_status": _text(history_item.get("completionStatus"), history_item.get("completion_status"), default="unknown"),
            "final_reason": _text(history_item.get("finalReason"), history_item.get("final_reason")),
            "shutdown_reason": _text(history_item.get("shutdownReason"), history_item.get("shutdown_reason"), history_item.get("stopReason")),
            "started_at": _int(history_item.get("startedAt") or history_item.get("started_at")),
            "ended_at": _int(history_item.get("endedAt") or history_item.get("ended_at")),
            "duration_sec": _int(history_item.get("durationSec") or history_item.get("duration_sec")),
            "worktree_outcome": _text(history_item.get("worktreeOutcome") or history_item.get("worktree_outcome"), default="none"),
        },
        "task_counts": {
            "done": _int(task_counts.get("done") or history_item.get("tasksDone")),
            "failed": _int(task_counts.get("failed") or history_item.get("tasksFailed")),
            "skipped": _int(task_counts.get("skipped") or history_item.get("tasksSkipped")),
            "total": _int(task_counts.get("total") or history_item.get("tasksTotal")),
            "cycles": _int(task_counts.get("cycles")),
        },
        "reports": {
            "final_run": _report_payload(final_report, fallback_status=history_item.get("reportStatus")),
            "qa_validation": _report_payload(qa_report, fallback_status=history_item.get("qaValidationReportStatus")),
            "cycle_change": {
                "summary": _text(cycle_change.get("summary_text"), cycle_change.get("summaryText"), cycle_change.get("summary")),
                "commits": _int(cycle_change.get("commit_count") or len(_as_list(cycle_change.get("commits")))),
                "changed_files": _int(cycle_change.get("changed_file_count") or len(_as_list(cycle_change.get("changed_files")))),
                "validation_results": _int(cycle_change.get("validation_result_count") or len(_as_list(cycle_change.get("validation_results")))),
                "unresolved_failures": _int(failed_summary.get("unresolved_count") or len(_as_list(failed_summary.get("items")))),
            },
            "operations": {
                "status": _text(operations.get("status"), default="missing"),
                "counts": {
                    "completed": _int(operations_counts.get("completed")),
                    "queued": _int(operations_counts.get("queued")),
                    "review_required": _int(operations_counts.get("review_required")),
                    "blocked_env": _int(operations_counts.get("blocked_env")),
                },
            },
        },
        "artifacts": report_artifacts,
    }


def _markdown_line(value: Any) -> str:
    text = _text(value)
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", " ").strip()


def _artifact_lines(artifacts: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for key in sorted(artifacts):
        value = _text(artifacts.get(key))
        if value:
            lines.append(f"- `{key}`: `{value}`")
    return lines or ["- none"]


def render_web_report_export_markdown(payload: dict[str, Any]) -> str:
    run = _as_dict(payload.get("run"))
    task_counts = _as_dict(payload.get("task_counts"))
    reports = _as_dict(payload.get("reports"))
    final_report = _as_dict(reports.get("final_run"))
    qa_report = _as_dict(reports.get("qa_validation"))
    cycle_change = _as_dict(reports.get("cycle_change"))
    operations = _as_dict(reports.get("operations"))
    operations_counts = _as_dict(operations.get("counts"))

    lines = [
        "# AgentCLI Web Report Export",
        "",
        f"- Run: `{_markdown_line(run.get('id'))}`",
        f"- Repo: `{_markdown_line(run.get('repo'))}`",
        f"- Branch: `{_markdown_line(run.get('branch'))}`",
        f"- Status: `{_markdown_line(run.get('status'))}`",
        f"- Execution status: `{_markdown_line(run.get('execution_status'))}`",
        f"- Project status: `{_markdown_line(run.get('project_status'))}`",
        f"- Final reason: {_markdown_line(run.get('final_reason')) or 'none'}",
        f"- Shutdown reason: {_markdown_line(run.get('shutdown_reason')) or 'none'}",
        f"- Duration: `{_int(run.get('duration_sec'))}s`",
        "",
        "## Task Counts",
        "",
        f"- Done: `{_int(task_counts.get('done'))}/{_int(task_counts.get('total'))}`",
        f"- Failed: `{_int(task_counts.get('failed'))}`",
        f"- Skipped: `{_int(task_counts.get('skipped'))}`",
        f"- Cycles: `{_int(task_counts.get('cycles'))}`",
        "",
        "## Reports",
        "",
        "### Final Run",
        "",
        f"- Status: `{_markdown_line(final_report.get('status'))}`",
        f"- Summary: {_markdown_line(final_report.get('summary')) or 'none'}",
        f"- Commands: `{_int(_as_dict(final_report.get('counts')).get('commands_passed'))}` passed, `{_int(_as_dict(final_report.get('counts')).get('commands_failed'))}` failed, `{_int(_as_dict(final_report.get('counts')).get('commands_skipped'))}` skipped",
        "",
        "### QA Validation",
        "",
        f"- Status: `{_markdown_line(qa_report.get('status'))}`",
        f"- Summary: {_markdown_line(qa_report.get('summary')) or 'none'}",
        f"- Commands: `{_int(_as_dict(qa_report.get('counts')).get('commands_passed'))}` passed, `{_int(_as_dict(qa_report.get('counts')).get('commands_failed'))}` failed, `{_int(_as_dict(qa_report.get('counts')).get('commands_skipped'))}` skipped",
        "",
        "### Cycle Change",
        "",
        f"- Summary: {_markdown_line(cycle_change.get('summary')) or 'none'}",
        f"- Commits: `{_int(cycle_change.get('commits'))}`",
        f"- Changed files: `{_int(cycle_change.get('changed_files'))}`",
        f"- Validation results: `{_int(cycle_change.get('validation_results'))}`",
        f"- Unresolved failures: `{_int(cycle_change.get('unresolved_failures'))}`",
        "",
        "### Operations",
        "",
        f"- Status: `{_markdown_line(operations.get('status'))}`",
        f"- Completed: `{_int(operations_counts.get('completed'))}`",
        f"- Queued: `{_int(operations_counts.get('queued'))}`",
        f"- Review required: `{_int(operations_counts.get('review_required'))}`",
        f"- Blocked environment: `{_int(operations_counts.get('blocked_env'))}`",
        "",
        "## Artifacts",
        "",
        *_artifact_lines(_as_dict(payload.get("artifacts"))),
        "",
    ]
    return "\n".join(lines)


def write_web_report_export_artifacts(run_dir: Path, payload: dict[str, Any]) -> dict[str, str]:
    json_path = run_dir / WEB_REPORT_EXPORT_JSON
    markdown_path = run_dir / WEB_REPORT_EXPORT_MD
    atomic_write_json(json_path, payload)
    atomic_write_text(markdown_path, render_web_report_export_markdown(payload))
    return {
        "json": json_path.as_posix(),
        "markdown": markdown_path.as_posix(),
    }
