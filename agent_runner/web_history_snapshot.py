from __future__ import annotations

from pathlib import Path
from typing import Any

from .goals import parse_goals_completion, read_goals
from .state import count_state_task_ids, load_backlog_task_ids, load_state
from .utils import atomic_write_json, now_iso


WEB_HISTORY_SNAPSHOT_JSON = "WEB_HISTORY_SNAPSHOT.json"


def _safe_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.is_file():
            import json

            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default
    return default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _pick_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _goal_summary(repo: Path) -> dict[str, Any]:
    try:
        _path, text = read_goals(repo)
        parsed = parse_goals_completion(text, completion_level="all")
    except Exception:
        parsed = {}
    return {
        "project_complete": bool(parsed.get("project_complete", False)),
        "p0_done": int(parsed.get("p0_done") or 0),
        "p0_total": int(parsed.get("p0_total") or 0),
        "p1_done": int(parsed.get("p1_done") or 0),
        "p1_total": int(parsed.get("p1_total") or 0),
        "all_done": int(parsed.get("all_done") or 0),
        "all_total": int(parsed.get("all_total") or 0),
    }


def build_final_web_history_snapshot(repo: Path, run_dir: Path) -> dict[str, Any]:
    run_summary = _as_dict(_safe_json(run_dir / "run_summary.json", {}))
    last_summary = _as_dict(_safe_json(run_dir / "last_run_summary.json", {}))
    final = _as_dict(run_summary.get("final"))
    final_report = _as_dict(_safe_json(run_dir / "FINAL_RUN_REPORT.json", {}))
    qa_report = _as_dict(_safe_json(run_dir / "QA_VALIDATION_REPORT.json", {}))
    operations = _as_dict(_safe_json(run_dir / "OPERATIONS_SUMMARY.json", {}))
    state = load_state(run_dir / "STATE.json")
    state_counts = count_state_task_ids(state, load_backlog_task_ids(run_dir / "BACKLOG.json"))
    task_counts = {
        "done": int(state_counts.get("done") or 0),
        "failed": int(state_counts.get("failed") or 0),
        "warnings": int(state_counts.get("warnings") or 0),
        "total": int(last_summary.get("tasks_total") or last_summary.get("total_tasks") or 0),
    }
    final_reason = _pick_text(final.get("reason"), last_summary.get("reason"), last_summary.get("stop_reason"))
    rc = final.get("rc")
    if rc is None:
        rc = last_summary.get("rc")
    return {
        "schema": "agentcli.final_web_history_snapshot.v1",
        "generated_at": now_iso(),
        "redacted": True,
        "redaction": {
            "excluded": ["raw_prompts", "raw_logs", "raw_goals_text", "backlog_prompts", "full_goals_text"],
            "notes": "Lightweight replay snapshot only; raw prompts, logs, and full GOALS text are intentionally omitted.",
        },
        "repo": {"name": repo.name, "path": repo.as_posix()},
        "run": {
            "id": run_dir.name,
            "run_dir": run_dir.as_posix(),
            "branch": _pick_text(run_summary.get("branch"), last_summary.get("branch"), "HEAD"),
            "rc": rc,
            "final_reason": final_reason,
            "cycle_count": len(run_summary.get("cycles") or []),
            "duration_seconds": int(last_summary.get("duration_seconds") or run_summary.get("duration_seconds") or 0),
        },
        "goals": _goal_summary(repo),
        "tasks": task_counts,
        "reports": {
            "final": {
                "status": _pick_text(final_report.get("status")),
                "summary": _pick_text(final_report.get("summary")),
            },
            "qa": {
                "status": _pick_text(qa_report.get("status")),
                "summary": _pick_text(qa_report.get("summary")),
            },
            "operations": {
                "status": _pick_text(operations.get("status")),
                "summary": _pick_text(operations.get("summary")),
            },
        },
        "artifacts": {
            "run_summary": (run_dir / "run_summary.json").as_posix(),
            "last_run_summary": (run_dir / "last_run_summary.json").as_posix(),
            "final_run_report": (run_dir / "FINAL_RUN_REPORT.json").as_posix(),
            "qa_validation_report": (run_dir / "QA_VALIDATION_REPORT.json").as_posix(),
            "operations_summary": (run_dir / "OPERATIONS_SUMMARY.json").as_posix(),
        },
    }


def write_final_web_history_snapshot(repo: Path, run_dir: Path) -> dict[str, Any]:
    snapshot = build_final_web_history_snapshot(repo, run_dir)
    atomic_write_json(run_dir / WEB_HISTORY_SNAPSHOT_JSON, snapshot)
    return snapshot
