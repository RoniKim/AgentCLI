from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Optional

from .docs import read_text_robust
from .gates import FAST_WEB_WORKTREE_REGRESSION_TEST_FILES, find_build_cmd, find_test_cmd
from .goals import parse_goals_completion, read_goals
from .gitops import git_head, git_porcelain, list_untracked
from .state import TaskItem, count_state_task_ids, load_backlog_json, load_backlog_task_ids, parse_backlog_md, load_state
from .todo import read_current_todo
from .utils import atomic_write_json, now_iso, safe_write_text


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
    if command_counts["failed"] > 0:
        attempt_status = "failed"
    elif command_counts["stopped"] > 0:
        attempt_status = "stopped"
    elif command_counts["executed"] == 0 and command_counts["skipped"] > 0:
        attempt_status = "skipped"
    else:
        attempt_status = "passed"

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
        if command_failed > 0 or failed > 0:
            report_status = "failed"
        elif command_stopped > 0 or stopped > 0:
            report_status = "stopped"
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
    return {
        "qa_validation_report": qa_report,
        "final_run_report": final_report,
        "artifacts": {
            "qa_validation_json": qa_json.as_posix(),
            "qa_validation_markdown": qa_md.as_posix(),
            "final_run_json": final_json.as_posix(),
            "final_run_markdown": final_md.as_posix(),
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
