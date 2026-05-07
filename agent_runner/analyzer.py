from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .active_goal import active_goal_role_context, build_active_goal_status
from .experience import load_run_experience_records
from .failure_policy import count_task_status_groups
from .state import TaskItem, load_backlog_json, load_state, parse_backlog_md
from .utils import atomic_write_json


ANALYZER_SCHEMA_VERSION = 1
ANALYZER_SUMMARY_FILENAME = "ANALYZER_SUMMARY.json"


def _text(value: Any, default: str = "", *, max_chars: int = 0) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if max_chars > 0 and len(text) > max_chars:
        return text[:max_chars].rstrip()
    return text


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return []


def _json_file(path: Path, default: Any) -> Any:
    if not path.exists() or not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _load_backlog_tasks(run_dir: Path) -> list[TaskItem]:
    backlog_json = run_dir / "BACKLOG.json"
    backlog_md = run_dir / "BACKLOG.md"
    if backlog_json.exists():
        try:
            return load_backlog_json(backlog_json)
        except Exception:
            return []
    if backlog_md.exists():
        try:
            return parse_backlog_md(backlog_md)
        except Exception:
            return []
    return []


def _normalize_task_status(value: Any) -> str:
    status = _text(value).lower()
    if status in {"done", "completed", "success", "ok"}:
        return "completed"
    return status or "failed"


def _normalize_validation_status(value: Any) -> str:
    status = _text(value).lower()
    if status in {"passed", "pass", "success", "completed", "ok", "validation_passed"}:
        return "validation_passed"
    if status in {"failed", "fail", "error", "validation_failed"}:
        return "validation_failed"
    if status in {"validation_pending", "tests_skipped", "no_tests_found", "blocked_env", "stopped"}:
        return status
    return status


def _validation_status_label(status: str) -> str:
    labels = {
        "validation_passed": "passed",
        "validation_failed": "failed",
        "blocked_env": "blocked environment",
        "no_tests_found": "no tests found",
        "validation_pending": "pending",
        "tests_skipped": "tests skipped",
        "stopped": "stopped",
    }
    return labels.get(status, status.replace("_", " "))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _relative_run_path(run_dir: Path, raw_path: Any) -> str:
    text = _text(raw_path)
    if not text:
        return ""
    try:
        path = Path(text)
        if not path.is_absolute():
            return text.replace("\\", "/")
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except Exception:
        return text.replace("\\", "/")


def _unique_strings(values: list[str], *, max_items: int = 12) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = _text(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
        if len(items) >= max_items:
            break
    return items


def _unique_evidence(values: list[str], *, max_items: int = 3) -> list[str]:
    return _unique_strings(values, max_items=max_items)


def _lesson_key(item: dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True)


def _dedupe_lessons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in sorted(
        items,
        key=lambda row: (
            _text(row.get("task_id") or row.get("taskId")),
            _text(row.get("packet_id") or row.get("packetId")),
            _text(row.get("kind")),
            _text(row.get("lesson")),
        ),
    ):
        key = _lesson_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _task_label(task_id: str, title: str) -> str:
    if task_id and title:
        return f"{task_id} ({title})"
    return task_id or title or "task"


def _load_failed_items(run_dir: Path, title_lookup: dict[str, str]) -> list[dict[str, Any]]:
    state = load_state(run_dir / "STATE.json")
    state_failed = [item for item in _as_list(state.get("failed")) if isinstance(item, dict)]
    state_by_task: dict[str, dict[str, Any]] = {}
    for item in state_failed:
        task_id = _text(item.get("task") or item.get("task_id") or item.get("taskId"))
        if task_id and task_id not in state_by_task:
            state_by_task[task_id] = dict(item)

    failed_artifact = _json_file(run_dir / "failed_tasks.json", {})
    artifact_items = _as_list(failed_artifact.get("items")) if isinstance(failed_artifact, dict) else []

    raw_items: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for raw in artifact_items:
        if not isinstance(raw, dict):
            continue
        task_id = _text(raw.get("task_id") or raw.get("taskId") or raw.get("task"))
        merged = dict(state_by_task.get(task_id, {}))
        merged.update(raw)
        raw_items.append(merged)
        if task_id:
            seen_task_ids.add(task_id)
    for raw in state_failed:
        task_id = _text(raw.get("task") or raw.get("task_id") or raw.get("taskId"))
        if task_id and task_id in seen_task_ids:
            continue
        raw_items.append(dict(raw))

    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        task_id = _text(raw.get("task_id") or raw.get("taskId") or raw.get("task"))
        title = _text(
            raw.get("title")
            or raw.get("task_title")
            or raw.get("taskTitle")
            or title_lookup.get(task_id),
            task_id,
        )
        status = _normalize_task_status(
            raw.get("task_status") or raw.get("taskStatus") or raw.get("outcome_status") or raw.get("status")
        )
        blocked_dependencies = [
            dict(item)
            for item in _as_list(
                raw.get("blocked_dependencies")
                or raw.get("blockedDependencies")
                or raw.get("blocking_dependencies")
                or raw.get("blockingDependencies")
            )
            if isinstance(item, dict)
        ]
        evidence: list[str] = []
        for item in _as_list(raw.get("artifact_links") or raw.get("artifactLinks")):
            if isinstance(item, dict):
                evidence.append(_relative_run_path(run_dir, item.get("path") or item.get("artifact_path")))
            else:
                evidence.append(_relative_run_path(run_dir, item))
        normalized.append(
            {
                "task_id": task_id,
                "title": title,
                "task_status": status,
                "reason": _text(raw.get("reason"), "unknown"),
                "detail": _text(raw.get("detail"), max_chars=240),
                "next_action": _text(raw.get("next_action") or raw.get("nextAction")),
                "blocked_dependencies": blocked_dependencies,
                "evidence": _unique_evidence(evidence),
            }
        )
    return sorted(normalized, key=lambda item: (item["task_id"], item["title"], item["reason"]))


def _normalize_validation_entry(raw: dict[str, Any], artifact_path: Path, run_dir: Path) -> dict[str, Any]:
    kind = _text(raw.get("kind")).lower()
    source_type = "pr" if kind == "pr_queue_validation_attempt" or _text(raw.get("packet_id") or raw.get("packetId")) else "task"
    task_id = _text(raw.get("task_id") or raw.get("taskId"))
    task_title = _text(raw.get("task_title") or raw.get("taskTitle"))
    packet_id = _text(raw.get("packet_id") or raw.get("packetId"))
    status = _normalize_validation_status(raw.get("status") or raw.get("validation_status") or raw.get("validationStatus"))
    reason = _text(raw.get("reason") or raw.get("validation_reason") or raw.get("validationReason"), status)
    summary = _text(
        raw.get("summary")
        or raw.get("detail")
        or raw.get("summary_text")
        or raw.get("validation_detail")
        or raw.get("validationDetail")
        or reason,
        reason,
        max_chars=240,
    )
    return {
        "source_type": source_type,
        "task_id": task_id,
        "task_title": task_title,
        "packet_id": packet_id,
        "status": status,
        "reason": reason,
        "summary": summary,
        "artifact_path": _relative_run_path(run_dir, artifact_path),
    }


def _load_validation_entries(run_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(run_dir.rglob("validation.json")):
        raw = _json_file(path, {})
        if isinstance(raw, dict):
            entries.append(_normalize_validation_entry(raw, path, run_dir))
    if entries:
        return entries

    qa_report = _json_file(run_dir / "QA_VALIDATION_REPORT.json", {})
    attempts = _as_list(qa_report.get("attempts")) if isinstance(qa_report, dict) else []
    for index, raw in enumerate(attempts, start=1):
        if not isinstance(raw, dict):
            continue
        artifact_hint = raw.get("artifact_path") or raw.get("artifactPath") or raw.get("validation_path") or raw.get("validationPath")
        path = Path(_text(artifact_hint, f"QA_VALIDATION_REPORT.json#{index}"))
        entries.append(_normalize_validation_entry(raw, path, run_dir))
    return entries


def _pick_preferred(existing: str, candidate: str) -> str:
    candidate_text = _text(candidate)
    if candidate_text and not existing:
        return candidate_text
    return existing


def _load_pr_packet_summaries(run_dir: Path) -> list[dict[str, Any]]:
    packets: dict[str, dict[str, Any]] = {}
    for path in sorted(run_dir.rglob("*.json")):
        raw = _json_file(path, None)
        if not isinstance(raw, dict):
            continue
        packet_id = _text(raw.get("packet_id") or raw.get("packetId"))
        if not packet_id:
            continue
        merge_outcome = raw.get("merge_outcome") if isinstance(raw.get("merge_outcome"), dict) else raw.get("mergeOutcome")
        if not isinstance(merge_outcome, dict):
            merge_outcome = {}
        is_validation_summary = (
            _text(raw.get("kind")).lower() == "pr_queue_validation_attempt"
            or path.name.lower() == "validation.json"
        )
        entry = packets.setdefault(
            packet_id,
            {
                "packet_id": packet_id,
                "status": "",
                "validation_status": "",
                "approval_status": "",
                "merge_status": "",
                "merge_preflight_status": "",
                "reason": "",
                "detail": "",
                "artifact_path": "",
            },
        )
        entry["artifact_path"] = _pick_preferred(entry["artifact_path"], _relative_run_path(run_dir, path))
        candidate_status = "" if is_validation_summary else _text(raw.get("status"))
        candidate_validation = _text(raw.get("validation_status") or raw.get("validationStatus"))
        if is_validation_summary and not candidate_validation:
            candidate_validation = _text(raw.get("status"))
        entry["status"] = _pick_preferred(entry["status"], candidate_status)
        entry["validation_status"] = _normalize_validation_status(
            _pick_preferred(entry["validation_status"], candidate_validation)
        )
        entry["approval_status"] = _pick_preferred(entry["approval_status"], _text(raw.get("approval_status") or raw.get("approvalStatus")))
        entry["merge_status"] = _pick_preferred(
            entry["merge_status"],
            _text(raw.get("merge_status") or raw.get("mergeStatus") or merge_outcome.get("status")),
        )
        entry["merge_preflight_status"] = _pick_preferred(
            entry["merge_preflight_status"],
            _text(
                merge_outcome.get("preflight_status")
                or merge_outcome.get("preflightStatus")
                or raw.get("preflight_status")
                or raw.get("preflightStatus")
            ),
        )
        entry["reason"] = _pick_preferred(
            entry["reason"],
            _text(
                raw.get("validation_reason")
                or raw.get("validationReason")
                or raw.get("reason")
                or merge_outcome.get("validation_reason")
                or merge_outcome.get("validationReason")
            ),
        )
        entry["detail"] = _pick_preferred(
            entry["detail"],
            _text(
                raw.get("validation_detail")
                or raw.get("validationDetail")
                or raw.get("detail")
                or merge_outcome.get("detail")
                or merge_outcome.get("validation_detail")
                or merge_outcome.get("validationDetail"),
                max_chars=240,
            ),
        )
    return [packets[key] for key in sorted(packets)]


def _count_validation_statuses(entries: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "validation_passed": 0,
        "validation_failed": 0,
        "blocked_env": 0,
        "no_tests_found": 0,
        "validation_pending": 0,
        "tests_skipped": 0,
        "stopped": 0,
        "other": 0,
    }
    for entry in entries:
        status = _normalize_validation_status(entry.get("status"))
        if status in counts:
            counts[status] += 1
        else:
            counts["other"] += 1
    return counts


def _overall_validation_status(counts: dict[str, int]) -> str:
    for status in (
        "validation_failed",
        "blocked_env",
        "no_tests_found",
        "validation_pending",
        "tests_skipped",
        "stopped",
        "validation_passed",
    ):
        if counts.get(status, 0) > 0:
            return status
    return "missing"


def _pr_is_blocked(packet: dict[str, Any]) -> bool:
    status = _text(packet.get("status")).lower()
    validation_status = _normalize_validation_status(packet.get("validation_status"))
    approval_status = _text(packet.get("approval_status")).lower()
    merge_status = _text(packet.get("merge_status")).lower()
    preflight_status = _text(packet.get("merge_preflight_status")).lower()
    if approval_status in {"discarded", "rejected"} or status in {"discarded", "rejected"}:
        return True
    if merge_status in {"blocked", "conflict", "failed", "error", "discarded", "rejected"}:
        return True
    if preflight_status and preflight_status != "passed":
        return True
    return validation_status in {"validation_failed", "blocked_env", "validation_pending", "no_tests_found", "tests_skipped", "stopped"}


def _pr_is_ready(packet: dict[str, Any]) -> bool:
    validation_status = _normalize_validation_status(packet.get("validation_status"))
    approval_status = _text(packet.get("approval_status")).lower()
    merge_status = _text(packet.get("merge_status")).lower()
    return (
        validation_status == "validation_passed"
        and approval_status not in {"approved", "merged"}
        and merge_status not in {"approved", "merged"}
        and not _pr_is_blocked(packet)
    )


def _build_task_lessons(failed_items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    lessons: list[dict[str, Any]] = []
    pm_hints: list[str] = []
    operator_actions: list[str] = []
    for item in failed_items:
        task_id = _text(item.get("task_id"))
        title = _text(item.get("title"))
        label = _task_label(task_id, title)
        status = _normalize_task_status(item.get("task_status"))
        evidence = _unique_evidence(list(item.get("evidence") or []))
        if status == "blocked_env":
            lessons.append(
                {
                    "task_id": task_id,
                    "kind": "env",
                    "severity": "high",
                    "confidence": 0.95,
                    "lesson": f"Preserve {label} for review and fix the environment before retrying.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("Queue environment and setup fixes separately from feature work until blocked tasks are cleared.")
            operator_actions.append(item.get("next_action") or f"Resolve the environment blocker for {label} before rerunning.")
        elif status == "test_contract_changed":
            lessons.append(
                {
                    "task_id": task_id,
                    "kind": "task_contract",
                    "severity": "medium",
                    "confidence": 0.86,
                    "lesson": f"{label} changed a validation contract; update the affected tests or fixtures before merging.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("Split contract or fixture updates from product changes when a task changes validation expectations.")
            operator_actions.append(item.get("next_action") or f"Review the contract change for {label} before merge.")
        elif status == "review_required":
            lessons.append(
                {
                    "task_id": task_id,
                    "kind": "review_required",
                    "severity": "medium",
                    "confidence": 0.78,
                    "lesson": f"Review {label} manually before retrying or merging because validation did not produce a clean pass.",
                    "evidence": evidence,
                }
            )
        elif status in {"regression_failed", "failed"}:
            lessons.append(
                {
                    "task_id": task_id,
                    "kind": "regression",
                    "severity": "high",
                    "confidence": 0.9,
                    "lesson": f"Keep {label} focused on fixing the failing regression before adding more scope.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("Keep regression fixes isolated from new feature work until validation passes.")
            operator_actions.append(f"Inspect the failing validation for {label} and rerun the affected gate.")
        if item.get("blocked_dependencies") and item.get("next_action"):
            operator_actions.append(_text(item.get("next_action")))
    return lessons, pm_hints, operator_actions


def _build_validation_lessons(validation_entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    lessons: list[dict[str, Any]] = []
    pm_hints: list[str] = []
    operator_actions: list[str] = []
    for entry in validation_entries:
        status = _normalize_validation_status(entry.get("status"))
        task_label = _task_label(_text(entry.get("task_id")), _text(entry.get("task_title")))
        subject = f"PR packet {_text(entry.get('packet_id'))}" if _text(entry.get("source_type")) == "pr" else task_label
        evidence = _unique_evidence([_text(entry.get("artifact_path"))])
        if status == "validation_failed":
            lessons.append(
                {
                    "task_id": _text(entry.get("task_id")),
                    "packet_id": _text(entry.get("packet_id")),
                    "kind": "validation_failed",
                    "severity": "high",
                    "confidence": 0.92,
                    "lesson": f"{subject} failed validation; fix the regression and rerun the affected gate.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("Keep regression fixes isolated from new feature work until validation passes.")
            operator_actions.append(f"Inspect the failing validation artifact for {subject} and rerun the affected gate.")
        elif status == "blocked_env":
            lessons.append(
                {
                    "task_id": _text(entry.get("task_id")),
                    "packet_id": _text(entry.get("packet_id")),
                    "kind": "blocked_env",
                    "severity": "high",
                    "confidence": 0.95,
                    "lesson": f"{subject} could not finish validation because the environment is blocked.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("Queue environment and setup fixes separately from feature work until blocked tasks are cleared.")
            operator_actions.append(f"Fix the environment blocker for {subject} before rerunning validation.")
        elif status == "no_tests_found":
            lessons.append(
                {
                    "task_id": _text(entry.get("task_id")),
                    "packet_id": _text(entry.get("packet_id")),
                    "kind": "no_tests_found",
                    "severity": "medium",
                    "confidence": 0.88,
                    "lesson": f"{subject} has no discovered tests; preserve the work for review and add explicit coverage before merge.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("When a task has no tests, preserve the work for review and schedule explicit coverage.")
            operator_actions.append(f"Decide whether {subject} needs new tests or an explicit build-only review before merge.")
        elif status == "validation_pending":
            lessons.append(
                {
                    "task_id": _text(entry.get("task_id")),
                    "packet_id": _text(entry.get("packet_id")),
                    "kind": "validation_pending",
                    "severity": "low",
                    "confidence": 0.72,
                    "lesson": f"{subject} still needs validation before it is merge-ready.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("Do not treat pending validation as success; keep that work in review until the gate runs.")
            operator_actions.append(f"Run the missing validation for {subject} before merge.")
        elif status == "tests_skipped":
            lessons.append(
                {
                    "task_id": _text(entry.get("task_id")),
                    "packet_id": _text(entry.get("packet_id")),
                    "kind": "tests_skipped",
                    "severity": "low",
                    "confidence": 0.74,
                    "lesson": f"{subject} skipped planned tests; treat the work as review-only until the missing gate runs.",
                    "evidence": evidence,
                }
            )
            pm_hints.append("Do not treat skipped validation as success; keep that work in review until the gate runs.")
            operator_actions.append(f"Run the skipped tests for {subject} before merge.")
    return lessons, pm_hints, operator_actions


def _build_merge_hints(pr_packets: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    merge_hints: list[str] = []
    operator_actions: list[str] = []
    for packet in pr_packets:
        packet_id = _text(packet.get("packet_id"), "unknown")
        validation_status = _normalize_validation_status(packet.get("validation_status"))
        approval_status = _text(packet.get("approval_status")).lower()
        merge_status = _text(packet.get("merge_status")).lower()
        detail = _text(packet.get("detail") or packet.get("reason"), max_chars=240)
        if approval_status in {"discarded", "rejected"} or _text(packet.get("status")).lower() in {"discarded", "rejected"}:
            merge_hints.append(f"PR packet {packet_id} was {approval_status or _text(packet.get('status')).lower()}; keep the discard reason as future planning evidence.")
            operator_actions.append(f"Record the discard reason for PR packet {packet_id} before retrying the work.")
        elif merge_status in {"blocked", "conflict", "failed", "error"}:
            merge_hints.append(f"PR packet {packet_id} is blocked by merge status {merge_status}.")
            operator_actions.append(
                f"Review PR packet {packet_id} and resolve the merge blocker{f': {detail}' if detail else '.'}"
            )
        elif _text(packet.get("merge_preflight_status")).lower() not in {"", "passed"}:
            merge_hints.append(f"PR packet {packet_id} failed merge preflight.")
            operator_actions.append(
                f"Review PR packet {packet_id} and resolve the merge preflight failure{f': {detail}' if detail else '.'}"
            )
        elif validation_status in {"validation_failed", "blocked_env", "validation_pending", "no_tests_found", "tests_skipped", "stopped"}:
            merge_hints.append(f"PR packet {packet_id} is not merge-ready: {_validation_status_label(validation_status)}.")
            operator_actions.append(
                f"Review PR packet {packet_id} and clear the validation blocker{f': {detail}' if detail else '.'}"
            )
        elif _pr_is_ready(packet):
            merge_hints.append(f"PR packet {packet_id} passed validation and is ready for operator approval.")
            operator_actions.append(f"Approve or discard PR packet {packet_id} after reviewing its diff and validation artifacts.")
    return merge_hints, operator_actions


def _apply_experience_records(
    records: list[dict[str, Any]],
    *,
    task_lessons: list[dict[str, Any]],
    validation_lessons: list[dict[str, Any]],
    pm_hints: list[str],
    merge_hints: list[str],
    operator_actions: list[str],
) -> None:
    for record in records:
        kind = _text(record.get("kind")).lower()
        message = _text(record.get("lesson") or record.get("message") or record.get("hint") or record.get("action"), max_chars=240)
        evidence = _unique_evidence(
            [
                _text(record.get("artifact_path") or record.get("artifactPath")),
                _text(record.get("record_path")),
            ]
        )
        if kind in {"task_lesson", "task"} and message:
            task_lessons.append(
                {
                    "task_id": _text(record.get("task_id") or record.get("taskId")),
                    "kind": _text(record.get("lesson_kind") or record.get("lessonKind"), "task_record"),
                    "severity": _text(record.get("severity"), "medium"),
                    "confidence": _float(record.get("confidence"), 0.75),
                    "lesson": message,
                    "evidence": evidence,
                }
            )
        elif kind in {"validation_lesson", "validation"} and message:
            validation_lessons.append(
                {
                    "task_id": _text(record.get("task_id") or record.get("taskId")),
                    "packet_id": _text(record.get("packet_id") or record.get("packetId")),
                    "kind": _text(record.get("lesson_kind") or record.get("lessonKind"), "validation_record"),
                    "severity": _text(record.get("severity"), "medium"),
                    "confidence": _float(record.get("confidence"), 0.75),
                    "lesson": message,
                    "evidence": evidence,
                }
            )
        elif kind == "pm_hint" and message:
            pm_hints.append(message)
        elif kind == "merge_hint" and message:
            merge_hints.append(message)
        elif kind in {"operator_action", "operator"} and message:
            operator_actions.append(message)


def _compose_summary(
    *,
    done_count: int,
    failed_count: int,
    validation_counts: dict[str, int],
    pr_packets: list[dict[str, Any]],
) -> str:
    bits: list[str] = [f"{done_count} completed task(s)"]
    if failed_count:
        bits.append(f"{failed_count} review/failed task(s)")
    overall_validation = _overall_validation_status(validation_counts)
    if overall_validation != "missing":
        bits.append(f"validation {_validation_status_label(overall_validation)}")
    pr_blockers = len([packet for packet in pr_packets if _pr_is_blocked(packet)])
    pr_ready = len([packet for packet in pr_packets if _pr_is_ready(packet)])
    if pr_blockers:
        bits.append(f"{pr_blockers} PR merge blocker(s)")
    elif pr_ready:
        bits.append(f"{pr_ready} PR packet(s) ready for review")
    return "; ".join(bits) + "."


def _build_local_analyzer_summary(run_dir: Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser()
    backlog = _load_backlog_tasks(root)
    title_lookup = {task.id: task.title for task in backlog}
    state = load_state(root / "STATE.json")
    done_ids = sorted({_text(item) for item in _as_list(state.get("done")) if _text(item)})
    failed_items = _load_failed_items(root, title_lookup)
    validation_entries = _load_validation_entries(root)
    pr_packets = _load_pr_packet_summaries(root)
    validation_counts = _count_validation_statuses(validation_entries)
    failure_groups = count_task_status_groups([_text(item.get("task_status")) for item in failed_items])

    task_lessons, task_pm_hints, task_operator_actions = _build_task_lessons(failed_items)
    validation_lessons, validation_pm_hints, validation_operator_actions = _build_validation_lessons(validation_entries)
    merge_hints, merge_operator_actions = _build_merge_hints(pr_packets)

    pm_hints = list(task_pm_hints) + list(validation_pm_hints)
    operator_actions = list(task_operator_actions) + list(validation_operator_actions) + list(merge_operator_actions)
    if done_ids and not failed_items and validation_counts.get("validation_passed", 0) > 0:
        pm_hints.append("Completed tasks with passing validation are good candidates for merge review.")
    if failure_groups.get("review", 0) > 0 and "Split contract or fixture updates from product changes when a task changes validation expectations." not in pm_hints:
        pm_hints.append("Preserved review-required work should return as a narrow follow-up instead of being retried unchanged.")

    experience_records = load_run_experience_records(root)
    _apply_experience_records(
        experience_records,
        task_lessons=task_lessons,
        validation_lessons=validation_lessons,
        pm_hints=pm_hints,
        merge_hints=merge_hints,
        operator_actions=operator_actions,
    )

    return {
        "schema_version": ANALYZER_SCHEMA_VERSION,
        "run_id": root.name,
        "summary": _compose_summary(
            done_count=len(done_ids),
            failed_count=len(failed_items),
            validation_counts=validation_counts,
            pr_packets=pr_packets,
        ),
        "task_lessons": _dedupe_lessons(task_lessons),
        "validation_lessons": _dedupe_lessons(validation_lessons),
        "pm_hints": _unique_strings(pm_hints),
        "merge_hints": _unique_strings(merge_hints),
        "operator_actions": _unique_strings(operator_actions),
    }


def write_analyzer_summary(run_dir: Path) -> dict[str, Any]:
    root = Path(run_dir).expanduser()
    summary = _build_local_analyzer_summary(root)
    atomic_write_json(root / ANALYZER_SUMMARY_FILENAME, summary)
    return summary


import json
from pathlib import Path
from typing import Any

from .experience import experience_root, list_lessons, normalize_trigger, upsert_lessons
from .state import load_backlog_json, load_state, parse_backlog_md
from .utils import atomic_write_json, eprint, now_iso, safe_write_text

ANALYZER_SCHEMA_VERSION = 2
_VALIDATION_GAP_STATUSES = {"no_tests_found", "tests_skipped", "validation_pending"}


def _build_experience_analyzer_summary(repo: Path, run_dir: Path) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    run_dir_path = Path(run_dir).expanduser().resolve()
    active_goal_context = active_goal_role_context(build_active_goal_status(repo_path), role="Analyzer")
    backlog_map = _load_backlog_context(run_dir_path)
    state = load_state(run_dir_path / "STATE.json")
    validation_by_task = _load_validation_payloads(run_dir_path)

    lessons: list[dict[str, Any]] = []
    lessons.extend(_build_failed_task_lessons(repo_path, run_dir_path, backlog_map, validation_by_task))
    lessons.extend(_build_validation_gap_lessons(repo_path, run_dir_path, backlog_map, validation_by_task))
    lessons.extend(_build_pr_decision_lessons(repo_path, run_dir_path))
    lessons.extend(_build_active_goal_lessons(repo_path, run_dir_path, active_goal_context, backlog_map, validation_by_task))

    stored_lessons = upsert_lessons(repo_path, lessons)
    task_lessons = [lesson for lesson in stored_lessons if lesson.get("kind") not in {"merge"}]
    validation_lessons = [
        lesson
        for lesson in stored_lessons
        if lesson.get("kind") in {"validation", "env"}
        or str(lesson.get("validation_status") or "") in _VALIDATION_GAP_STATUSES
    ]
    merge_lessons = [lesson for lesson in stored_lessons if lesson.get("kind") == "merge"]

    done_count = len(state.get("done") or [])
    failed_count = len(state.get("failed") or [])
    summary_text = _summary_text(stored_lessons, done_count=done_count, failed_count=failed_count)
    artifacts = {
        "summary_json": (run_dir_path / "ANALYZER_SUMMARY.json").as_posix(),
        "experience_updates_jsonl": (run_dir_path / "EXPERIENCE_UPDATES.jsonl").as_posix(),
        "latest_summary_json": (experience_root(repo_path) / "latest_summary.json").as_posix(),
        "latest_summary_markdown": (experience_root(repo_path) / "latest_summary.md").as_posix(),
    }
    return {
        "schema_version": ANALYZER_SCHEMA_VERSION,
        "kind": "analyzer_summary",
        "generated_at": now_iso(),
        "repo": repo_path.as_posix(),
        "run_dir": run_dir_path.as_posix(),
        "run_id": run_dir_path.name,
        "summary": summary_text,
        "lessons": stored_lessons,
        "task_lessons": task_lessons,
        "validation_lessons": validation_lessons,
        "merge_lessons": merge_lessons,
        "pm_hints": [lesson["lesson"] for lesson in stored_lessons[:10]],
        "merge_hints": [lesson["lesson"] for lesson in merge_lessons[:10]],
        "operator_actions": [],
        "active_goal_context": active_goal_context,
        "activeGoalContext": active_goal_context,
        "counts": {
            "lessons": len(stored_lessons),
            "task_lessons": len(task_lessons),
            "validation_lessons": len(validation_lessons),
            "merge_lessons": len(merge_lessons),
        },
        "artifacts": artifacts,
    }


def _write_experience_analyzer_artifacts(repo: Path, run_dir: Path) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    run_dir_path = Path(run_dir).expanduser().resolve()
    try:
        summary = _build_experience_analyzer_summary(repo_path, run_dir_path)
    except Exception as exc:
        eprint(f"[WARN] analyzer.write_analyzer_artifacts failed: {exc}")
        summary = {
            "schema_version": ANALYZER_SCHEMA_VERSION,
            "kind": "analyzer_summary",
            "generated_at": now_iso(),
            "repo": repo_path.as_posix(),
            "run_dir": run_dir_path.as_posix(),
            "run_id": run_dir_path.name,
            "summary": f"Analyzer unavailable: {exc}",
            "lessons": [],
            "task_lessons": [],
            "validation_lessons": [],
            "merge_lessons": [],
            "pm_hints": [],
            "merge_hints": [],
            "operator_actions": [],
            "active_goal_context": active_goal_role_context(build_active_goal_status(repo_path), role="Analyzer"),
            "activeGoalContext": active_goal_role_context(build_active_goal_status(repo_path), role="Analyzer"),
            "counts": {"lessons": 0, "task_lessons": 0, "validation_lessons": 0, "merge_lessons": 0},
            "artifacts": {
                "summary_json": (run_dir_path / "ANALYZER_SUMMARY.json").as_posix(),
                "experience_updates_jsonl": (run_dir_path / "EXPERIENCE_UPDATES.jsonl").as_posix(),
                "latest_summary_json": (experience_root(repo_path) / "latest_summary.json").as_posix(),
                "latest_summary_markdown": (experience_root(repo_path) / "latest_summary.md").as_posix(),
            },
            "warning": str(exc),
        }

    summary_path = run_dir_path / "ANALYZER_SUMMARY.json"
    updates_path = run_dir_path / "EXPERIENCE_UPDATES.jsonl"
    latest_json = experience_root(repo_path) / "latest_summary.json"
    latest_md = experience_root(repo_path) / "latest_summary.md"

    try:
        atomic_write_json(summary_path, summary)
    except Exception:
        safe_write_text(summary_path, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    try:
        updates_lines = [
            json.dumps(lesson, ensure_ascii=False, sort_keys=True)
            for lesson in summary.get("lessons") or []
            if isinstance(lesson, dict)
        ]
        safe_write_text(updates_path, ("\n".join(updates_lines) + "\n") if updates_lines else "")
    except Exception:
        pass

    try:
        latest_json.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(latest_json, summary)
    except Exception:
        safe_write_text(latest_json, json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    try:
        safe_write_text(latest_md, _render_latest_summary_markdown(summary))
    except Exception:
        pass

    return {
        "summary": summary,
        "artifacts": summary.get("artifacts") or {},
        "stored_lessons": list_lessons(repo_path),
    }


def _load_backlog_context(run_dir: Path) -> dict[str, dict[str, Any]]:
    backlog_json = run_dir / "BACKLOG.json"
    backlog_md = run_dir / "BACKLOG.md"
    if backlog_json.exists():
        tasks = load_backlog_json(backlog_json)
    elif backlog_md.exists():
        tasks = parse_backlog_md(backlog_md)
    else:
        tasks = []
    context: dict[str, dict[str, Any]] = {}
    for task in tasks:
        context[str(task.id)] = {
            "task_id": str(task.id),
            "title": str(task.title),
            "goal_refs": _goal_refs(task.goal_trace),
            "file_globs": _normalize_file_globs(task.files),
            "active_goal_id": str(getattr(task, "active_goal_id", "") or ""),
            "active_goal": dict(getattr(task, "active_goal", {}) or {}),
        }
    return context


def _load_validation_payloads(run_dir: Path) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    tasks_root = run_dir / "tasks"
    if not tasks_root.exists():
        return payloads
    for path in tasks_root.glob("*/attempt_*/validation.json"):
        payload = _json_file(path)
        task_id = str(payload.get("task_id") or payload.get("taskId") or path.parents[1].name).strip()
        if not task_id:
            continue
        payload["_artifact_path"] = _relative_path(run_dir, path)
        payloads[task_id] = payload
    return payloads


def _build_failed_task_lessons(
    repo: Path,
    run_dir: Path,
    backlog_map: dict[str, dict[str, Any]],
    validation_by_task: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    artifact = _json_file(run_dir / "failed_tasks.json")
    items = artifact.get("items") if isinstance(artifact, dict) else []
    lessons: list[dict[str, Any]] = []
    for raw in items if isinstance(items, list) else []:
        if not isinstance(raw, dict):
            continue
        task_id = str(raw.get("task_id") or raw.get("taskId") or "").strip()
        task_context = backlog_map.get(task_id, {})
        validation = validation_by_task.get(task_id, {})
        validation_status = _token(
            validation.get("validation_status") or validation.get("validationStatus") or validation.get("status")
        )
        if validation_status in _VALIDATION_GAP_STATUSES:
            continue
        task_status = _token(raw.get("task_status") or raw.get("taskStatus") or raw.get("status") or "failed")
        gate = _validation_gate(validation)
        goal_refs = _unique([*task_context.get("goal_refs", []), *_goal_refs(validation.get("goal_trace") or validation.get("goalTrace"))])
        file_globs = _unique([*task_context.get("file_globs", []), *_normalize_file_globs(raw.get("files"))])
        evidence = _evidence_from_failed_task(raw, run_dir)
        if validation:
            evidence.append(
                {
                    "kind": "artifact",
                    "path": _relative_path(run_dir, validation.get("artifact_path") or validation.get("_artifact_path") or ""),
                    "run_id": run_dir.name,
                    "task_id": task_id,
                    "gate": gate,
                    "status": validation_status,
                }
            )
        kind = "env" if task_status == "blocked_env" else "validation"
        lesson_text = (
            "Keep blocked environment work in blocked_env state and preserve the failing gate evidence before retrying."
            if kind == "env"
            else "Repeated task failures should preserve focused validation evidence and avoid blind retries."
        )
        lessons.append(
            {
                "kind": kind,
                "normalized_trigger": normalize_trigger(
                    "repeated_failure",
                    task_context.get("title") or raw.get("title") or raw.get("task_title") or task_id,
                    gate,
                    task_status,
                    validation_status,
                    goal_refs,
                    file_globs,
                ),
                "lesson": lesson_text,
                "goal_refs": goal_refs,
                "file_globs": file_globs,
                "gate": gate,
                "task_status": task_status,
                "validation_status": validation_status,
                "evidence_pointers": evidence,
                "confidence": 0.72 if kind == "env" else 0.68,
            }
        )
    return lessons


def _build_validation_gap_lessons(
    repo: Path,
    run_dir: Path,
    backlog_map: dict[str, dict[str, Any]],
    validation_by_task: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for task_id, payload in validation_by_task.items():
        validation_status = _token(payload.get("validation_status") or payload.get("validationStatus") or payload.get("status"))
        if validation_status not in _VALIDATION_GAP_STATUSES:
            continue
        gate = _validation_gate(payload) or "test"
        task_context = backlog_map.get(task_id, {})
        goal_refs = _unique([*task_context.get("goal_refs", []), *_goal_refs(payload.get("goal_trace") or payload.get("goalTrace"))])
        file_globs = _unique([*task_context.get("file_globs", []), *_normalize_file_globs(payload.get("files"))])
        lessons.append(
            {
                "kind": "validation",
                "normalized_trigger": normalize_trigger(
                    "validation_gap",
                    task_context.get("title") or payload.get("task_title") or task_id,
                    gate,
                    validation_status,
                    goal_refs,
                    file_globs,
                ),
                "lesson": _validation_gap_lesson_text(validation_status, gate),
                "goal_refs": goal_refs,
                "file_globs": file_globs,
                "gate": gate,
                "task_status": "validation_gap",
                "validation_status": validation_status,
                "evidence_pointers": _evidence_from_validation_payload(payload, run_dir, task_id=task_id, gate=gate, status=validation_status),
                "confidence": 0.74,
            }
        )
    return lessons


def _build_pr_decision_lessons(repo: Path, run_dir: Path) -> list[dict[str, Any]]:
    pr_root = repo / ".AgentCLI" / "pr_queue"
    if not pr_root.exists():
        return []
    lessons: list[dict[str, Any]] = []
    for path in pr_root.glob("*.json"):
        packet = _json_file(path)
        if str(packet.get("run_id") or packet.get("runId") or "").strip() != run_dir.name:
            continue
        decision = _token(packet.get("approval_status") or packet.get("approvalStatus") or packet.get("merge_status") or packet.get("mergeStatus") or packet.get("status"))
        if decision not in {"approved", "discarded", "rejected"}:
            continue
        validation_status = _token(packet.get("validation_status") or packet.get("validationStatus"))
        goal_refs = _goal_refs(packet.get("goal_trace") or packet.get("goalTrace"))
        file_globs = _normalize_file_globs(packet.get("changed_files") or packet.get("changedFiles"))
        evidence = [
            {
                "kind": "pr_packet",
                "path": _relative_path(repo, path),
                "packet_id": str(packet.get("id") or path.stem),
                "status": decision,
                "gate": "pr_review",
            }
        ]
        for artifact in _as_list(packet.get("validation_artifacts") or packet.get("validationArtifacts")):
            rel_path = _relative_path(run_dir, artifact)
            if rel_path:
                evidence.append(
                    {
                        "kind": "artifact",
                        "path": rel_path,
                        "packet_id": str(packet.get("id") or path.stem),
                        "gate": "pr_review",
                        "status": validation_status,
                    }
                )
        lessons.append(
            {
                "kind": "merge",
                "normalized_trigger": normalize_trigger("pr_decision", decision, goal_refs, file_globs, validation_status),
                "lesson": "Preserve GOALS-linked PR decisions with packet and validation evidence pointers.",
                "goal_refs": goal_refs,
                "file_globs": file_globs,
                "gate": "pr_review",
                "task_status": decision,
                "validation_status": validation_status,
                "evidence_pointers": evidence,
                "confidence": 0.81 if decision == "approved" else 0.78,
            }
        )
    return lessons


def _build_active_goal_lessons(
    repo: Path,
    run_dir: Path,
    active_goal_context: dict[str, Any],
    backlog_map: dict[str, dict[str, Any]],
    validation_by_task: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    active_goal_id = str(active_goal_context.get("active_goal_id") or active_goal_context.get("activeGoalId") or "").strip()
    lessons: list[dict[str, Any]] = []
    if not active_goal_id:
        return lessons
    lessons.extend(_build_active_goal_decomposition_lessons(run_dir, active_goal_id))
    lessons.extend(_build_active_goal_validation_gap_lessons(run_dir, active_goal_id, backlog_map, validation_by_task))
    budget_lesson = _build_active_goal_budget_lesson(repo, run_dir, active_goal_id, active_goal_context)
    if budget_lesson:
        lessons.append(budget_lesson)
    return lessons


def _build_active_goal_decomposition_lessons(run_dir: Path, active_goal_id: str) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for path in sorted(run_dir.glob("BACKLOG_REFINEMENT_cycle_*.json")):
        payload = _json_file(path)
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_goal_id = str(item.get("active_goal_id") or item.get("activeGoalId") or "").strip()
            axes = [str(axis).strip() for axis in _as_list(item.get("split_axes") or item.get("splitAxes")) if str(axis).strip()]
            if item_goal_id != active_goal_id and "remaining_budget" not in axes and "file_scope" not in axes:
                continue
            task_id = str(item.get("task_id") or item.get("taskId") or "").strip()
            reason = str(item.get("reason") or "").strip()
            children = [str(child).strip() for child in _as_list(item.get("children")) if str(child).strip()]
            lessons.append(
                {
                    "kind": "active_goal_decomposition",
                    "normalized_trigger": normalize_trigger(
                        "active_goal_decomposition",
                        active_goal_id,
                        task_id,
                        axes,
                        reason,
                    ),
                    "lesson": (
                        "For active-goal work, decompose broad tasks by risk, file scope, dependencies, "
                        "and remaining budget before Dev starts."
                    ),
                    "goal_refs": [],
                    "file_globs": [],
                    "gate": "planning",
                    "task_status": "active_goal_split",
                    "validation_status": "",
                    "evidence_pointers": [
                        {
                            "kind": "backlog_refinement",
                            "path": _relative_path(run_dir, path),
                            "run_id": run_dir.name,
                            "task_id": task_id,
                            "gate": "planning",
                            "status": "split",
                            "children": children[:6],
                        }
                    ],
                    "confidence": 0.80,
                }
            )
    return lessons


def _validation_active_goal_id(payload: dict[str, Any], task_context: dict[str, Any]) -> str:
    context = payload.get("active_goal_context") if isinstance(payload.get("active_goal_context"), dict) else payload.get("activeGoalContext")
    if isinstance(context, dict):
        goal_id = str(context.get("active_goal_id") or context.get("activeGoalId") or "").strip()
        if goal_id:
            return goal_id
    return str(task_context.get("active_goal_id") or "").strip()


def _build_active_goal_validation_gap_lessons(
    run_dir: Path,
    active_goal_id: str,
    backlog_map: dict[str, dict[str, Any]],
    validation_by_task: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for task_id, payload in validation_by_task.items():
        task_context = backlog_map.get(task_id, {})
        if _validation_active_goal_id(payload, task_context) != active_goal_id:
            continue
        validation_status = _token(payload.get("validation_status") or payload.get("validationStatus") or payload.get("status"))
        if validation_status not in _VALIDATION_GAP_STATUSES:
            continue
        gate = _validation_gate(payload) or "test"
        goal_refs = _unique([*task_context.get("goal_refs", []), *_goal_refs(payload.get("goal_trace") or payload.get("goalTrace"))])
        file_globs = _unique([*task_context.get("file_globs", []), *_normalize_file_globs(payload.get("files"))])
        lessons.append(
            {
                "kind": "active_goal_validation_gap",
                "normalized_trigger": normalize_trigger(
                    "active_goal_validation_gap",
                    active_goal_id,
                    task_context.get("title") or payload.get("task_title") or task_id,
                    gate,
                    validation_status,
                    file_globs,
                ),
                "lesson": (
                    "Do not complete active-goal work on missing, skipped, or pending validation; "
                    "plan a narrow validation follow-up with explicit evidence."
                ),
                "goal_refs": goal_refs,
                "file_globs": file_globs,
                "gate": gate,
                "task_status": "active_goal_validation_gap",
                "validation_status": validation_status,
                "evidence_pointers": _evidence_from_validation_payload(
                    payload,
                    run_dir,
                    task_id=task_id,
                    gate=gate,
                    status=validation_status,
                ),
                "confidence": 0.82,
            }
        )
    return lessons


def _build_active_goal_budget_lesson(
    repo: Path,
    run_dir: Path,
    active_goal_id: str,
    active_goal_context: dict[str, Any],
) -> dict[str, Any] | None:
    progress = active_goal_context.get("progress") if isinstance(active_goal_context.get("progress"), dict) else {}
    terminal_reason = str(
        active_goal_context.get("terminal_reason")
        or active_goal_context.get("terminalReason")
        or progress.get("terminal_reason")
        or progress.get("terminalReason")
        or ""
    ).strip()
    budget_exhausted = bool(progress.get("budget_exhausted") or progress.get("budgetExhausted"))
    if not budget_exhausted and "budget" not in terminal_reason:
        return None
    source_path = str(active_goal_context.get("source_path") or active_goal_context.get("sourcePath") or "").strip()
    evidence_path = _relative_path(repo, source_path) if source_path else ".AgentCLI/goals/ACTIVE_GOAL.json"
    return {
        "kind": "active_goal_budget",
        "normalized_trigger": normalize_trigger(
            "active_goal_budget",
            active_goal_id,
            terminal_reason,
            progress.get("summary"),
        ),
        "lesson": (
            "When active-goal budget is exhausted, shrink the next PM decomposition and validation scope "
            "before retrying."
        ),
        "goal_refs": [],
        "file_globs": [],
        "gate": "budget",
        "task_status": terminal_reason or "active_goal_budget_exhausted",
        "validation_status": "",
        "evidence_pointers": [
            {
                "kind": "active_goal",
                "path": evidence_path,
                "run_id": run_dir.name,
                "goal_id": active_goal_id,
                "gate": "budget",
                "status": terminal_reason or "budget_exhausted",
            }
        ],
        "confidence": 0.84,
    }


def _validation_gate(payload: dict[str, Any]) -> str:
    gate = _token(payload.get("gate"))
    if gate:
        return gate
    for key in ("validation_records", "validationRecords", "records"):
        records = payload.get(key)
        if not isinstance(records, list):
            continue
        for entry in records:
            if not isinstance(entry, dict):
                continue
            gate = _token(entry.get("gate") or entry.get("kind") or entry.get("name"))
            if gate:
                return gate
    validation_status = _token(payload.get("validation_status") or payload.get("validationStatus") or payload.get("status"))
    if validation_status in {"no_tests_found", "tests_skipped", "validation_pending"}:
        return "test"
    return ""


def _validation_gap_lesson_text(validation_status: str, gate: str) -> str:
    if validation_status == "no_tests_found":
        return f"Keep {gate or 'test'} gaps in no_tests_found state until real gate coverage exists."
    if validation_status == "tests_skipped":
        return f"Keep skipped {gate or 'test'} gates tied to explicit rationale and non-passed validation state."
    return f"Keep pending {gate or 'validation'} work in a non-passed state until the gate runs."


def _evidence_from_failed_task(raw: dict[str, Any], run_dir: Path) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for link in _as_list(raw.get("artifact_links") or raw.get("artifactLinks")):
        if not isinstance(link, dict):
            continue
        rel_path = _relative_path(run_dir, link.get("path") or link.get("artifact_path") or "")
        if not rel_path:
            continue
        evidence.append(
            {
                "kind": "artifact",
                "path": rel_path,
                "run_id": run_dir.name,
                "task_id": str(raw.get("task_id") or raw.get("taskId") or "").strip(),
            }
        )
    return evidence


def _evidence_from_validation_payload(
    payload: dict[str, Any],
    run_dir: Path,
    *,
    task_id: str,
    gate: str,
    status: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    artifact_path = payload.get("artifact_path") or payload.get("artifactPath") or payload.get("_artifact_path") or ""
    rel_artifact = _relative_path(run_dir, artifact_path)
    if rel_artifact:
        evidence.append(
            {
                "kind": "artifact",
                "path": rel_artifact,
                "run_id": run_dir.name,
                "task_id": task_id,
                "gate": gate,
                "status": status,
            }
        )
    for key in ("validation_records", "validationRecords", "records"):
        records = payload.get(key)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, dict):
                continue
            rel_path = _relative_path(run_dir, record.get("artifact_path") or record.get("artifactPath") or record.get("log_path") or record.get("logPath") or "")
            if not rel_path:
                continue
            evidence.append(
                {
                    "kind": "artifact",
                    "path": rel_path,
                    "run_id": run_dir.name,
                    "task_id": task_id,
                    "gate": _token(record.get("gate")) or gate,
                    "status": _token(record.get("validation_status") or record.get("validationStatus") or record.get("status")) or status,
                }
            )
    return evidence


def _goal_refs(value: object) -> list[str]:
    refs: list[str] = []
    for entry in _as_list(value):
        if isinstance(entry, dict):
            text = str(entry.get("goal_ref") or entry.get("goal_id") or entry.get("id") or "").strip()
        else:
            text = str(entry or "").strip()
        if text:
            refs.append(text)
    return _unique(refs)


def _normalize_file_globs(value: object) -> list[str]:
    globs: list[str] = []
    for entry in _as_list(value):
        text = str(entry or "").strip().replace("\\", "/").lstrip("./")
        if text:
            globs.append(text)
    return _unique(globs)


def _relative_path(base: Path, value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        text = text.replace("\\", "/")
        return text[2:] if text.startswith("./") else text
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except Exception:
        return path.name


_JSON_FILE_MISSING = object()


def _json_file(path: Path, default: Any = _JSON_FILE_MISSING) -> Any:
    fallback = {} if default is _JSON_FILE_MISSING else default
    if not path.exists() or not path.is_file():
        return fallback
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return fallback
    if default is _JSON_FILE_MISSING:
        return payload if isinstance(payload, dict) else {}
    return payload


def _summary_text(lessons: list[dict[str, Any]], *, done_count: int, failed_count: int) -> str:
    if not lessons:
        return f"No analyzer lessons recorded; state done={done_count}, failed={failed_count}."
    kinds = sorted({str(lesson.get("kind") or "lesson") for lesson in lessons})
    return (
        f"Generated {len(lessons)} analyzer lesson(s) from state done={done_count}, failed={failed_count}; "
        f"kinds: {', '.join(kinds)}."
    )


def _render_latest_summary_markdown(summary: dict[str, Any]) -> str:
    lines = ["# Latest Analyzer Summary", ""]
    lines.append(f"- generated_at: {summary.get('generated_at')}")
    lines.append(f"- run_id: {summary.get('run_id')}")
    lines.append(f"- summary: {summary.get('summary')}")
    lines.append("")
    lines.append("## Lessons")
    lines.append("")
    lessons = summary.get("lessons") or []
    if not lessons:
        lines.append("- No lessons recorded.")
    else:
        for lesson in lessons:
            if not isinstance(lesson, dict):
                continue
            lines.append(
                f"- [{lesson.get('kind')}/{lesson.get('confidence')}] {lesson.get('lesson')} "
                f"(trigger={lesson.get('normalized_trigger')})"
            )
    lines.append("")
    return "\n".join(lines)


def _token(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("\\", "/")
    text = text.replace(" ", "_").replace("-", "_")
    return text


def _as_list(value: object) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .utils import atomic_write_json, now_iso, safe_write_text


ANALYZER_SUMMARY_FILENAME = "ANALYZER_SUMMARY.json"
ANALYZER_REPORT_FILENAME = "ANALYZER_REPORT.md"
EXPERIENCE_UPDATES_FILENAME = "EXPERIENCE_UPDATES.jsonl"

ANALYZER_AUTHORITY = "advisory"
ANALYZER_FORBIDDEN_ACTIONS: tuple[str, ...] = (
    "goals_save_or_auto_check",
    "pr_merge_or_approval",
    "source_code_mutation",
    "validation_gate_bypass",
)


def analyzer_authority_metadata() -> dict[str, Any]:
    return {
        "level": ANALYZER_AUTHORITY,
        "label": ANALYZER_AUTHORITY,
        "summary": (
            "Analyzer output is advisory only. It cannot mark GOALS complete, "
            "approve merges, mutate source code, or bypass deterministic validation gates."
        ),
        "forbidden_actions": list(ANALYZER_FORBIDDEN_ACTIONS),
        "forbiddenActions": list(ANALYZER_FORBIDDEN_ACTIONS),
        "can_mark_goals_complete": False,
        "canMarkGoalsComplete": False,
        "can_approve_merge": False,
        "canApproveMerge": False,
        "can_mutate_source": False,
        "canMutateSource": False,
        "can_bypass_validation_gates": False,
        "canBypassValidationGates": False,
    }


def _coerce_text_list(values: Sequence[object] | object | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        items = [values]
    else:
        try:
            items = list(values)  # type: ignore[arg-type]
        except TypeError:
            items = [values]
    out: list[str] = []
    seen: set[str] = set()
    for value in items:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalize_lesson(record: Mapping[str, object], *, default_kind: str) -> dict[str, Any]:
    confidence_raw = record.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    evidence = _coerce_text_list(record.get("evidence"))
    lesson = {
        "task_id": str(record.get("task_id") or record.get("taskId") or "").strip(),
        "kind": str(record.get("kind") or default_kind).strip() or default_kind,
        "severity": str(record.get("severity") or "medium").strip() or "medium",
        "confidence": confidence,
        "lesson": str(record.get("lesson") or "").strip(),
        "evidence": evidence,
        "authority": ANALYZER_AUTHORITY,
        "authorityLevel": ANALYZER_AUTHORITY,
    }
    if record.get("source"):
        lesson["source"] = str(record.get("source") or "").strip()
    return lesson


def _normalize_lessons(
    records: Sequence[Mapping[str, object]] | None,
    *,
    default_kind: str,
) -> list[dict[str, Any]]:
    if not records:
        return []
    normalized = [_normalize_lesson(record, default_kind=default_kind) for record in records]
    normalized.sort(
        key=lambda record: (
            -float(record.get("confidence", 0.0) or 0.0),
            str(record.get("task_id") or ""),
            str(record.get("lesson") or ""),
        )
    )
    return normalized


def build_analyzer_summary(
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, object]] | None = None,
    validation_lessons: Sequence[Mapping[str, object]] | None = None,
    pm_hints: Sequence[object] | object | None = None,
    merge_hints: Sequence[object] | object | None = None,
    operator_actions: Sequence[object] | object | None = None,
) -> dict[str, Any]:
    authority = analyzer_authority_metadata()
    normalized_task_lessons = _normalize_lessons(task_lessons, default_kind="task")
    normalized_validation_lessons = _normalize_lessons(validation_lessons, default_kind="validation")
    return {
        "schema_version": 1,
        "kind": "analyzer_summary",
        "generated_at": now_iso(),
        "run_id": str(run_id or "").strip(),
        "summary": str(summary or "").strip(),
        "authority": authority,
        "authority_level": ANALYZER_AUTHORITY,
        "authorityLevel": ANALYZER_AUTHORITY,
        "task_lessons": normalized_task_lessons,
        "taskLessons": normalized_task_lessons,
        "validation_lessons": normalized_validation_lessons,
        "validationLessons": normalized_validation_lessons,
        "pm_hints": _coerce_text_list(pm_hints),
        "pmHints": _coerce_text_list(pm_hints),
        "merge_hints": _coerce_text_list(merge_hints),
        "mergeHints": _coerce_text_list(merge_hints),
        "operator_actions": _coerce_text_list(operator_actions),
        "operatorActions": _coerce_text_list(operator_actions),
    }


def _render_report(summary_payload: Mapping[str, Any]) -> str:
    authority = summary_payload.get("authority") or {}
    report_lines = [
        "# Analyzer Report",
        "",
        f"- Run ID: {summary_payload.get('run_id') or ''}",
        f"- Authority: {authority.get('level') or ANALYZER_AUTHORITY} only",
        (
            "- Boundaries: cannot mark GOALS complete, approve merges, mutate source code, "
            "or bypass deterministic validation gates."
        ),
        "",
    ]

    summary_text = str(summary_payload.get("summary") or "").strip()
    if summary_text:
        report_lines.extend(["## Summary", "", summary_text, ""])

    task_lessons = list(summary_payload.get("task_lessons") or [])
    if task_lessons:
        report_lines.extend(["## Task Lessons", ""])
        for lesson in task_lessons:
            report_lines.append(
                "- "
                f"[{lesson.get('kind')}/{lesson.get('severity')} conf={lesson.get('confidence', 0.0):.2f}] "
                f"{lesson.get('lesson') or ''}"
            )
        report_lines.append("")

    validation_lessons = list(summary_payload.get("validation_lessons") or [])
    if validation_lessons:
        report_lines.extend(["## Validation Lessons", ""])
        for lesson in validation_lessons:
            report_lines.append(
                "- "
                f"[{lesson.get('kind')}/{lesson.get('severity')} conf={lesson.get('confidence', 0.0):.2f}] "
                f"{lesson.get('lesson') or ''}"
            )
        report_lines.append("")

    pm_hints = list(summary_payload.get("pm_hints") or [])
    if pm_hints:
        report_lines.extend(["## PM Hints", ""])
        for hint in pm_hints:
            report_lines.append(f"- {hint}")
        report_lines.append("")

    merge_hints = list(summary_payload.get("merge_hints") or [])
    if merge_hints:
        report_lines.extend(["## Merge Hints", ""])
        for hint in merge_hints:
            report_lines.append(f"- {hint}")
        report_lines.append("")

    operator_actions = list(summary_payload.get("operator_actions") or [])
    if operator_actions:
        report_lines.extend(["## Operator Actions", ""])
        for action in operator_actions:
            report_lines.append(f"- {action}")
        report_lines.append("")

    return "\n".join(report_lines).rstrip() + "\n"


def _render_experience_updates(summary_payload: Mapping[str, Any]) -> str:
    records: list[dict[str, Any]] = []
    generated_at = str(summary_payload.get("generated_at") or now_iso())
    run_id = str(summary_payload.get("run_id") or "").strip()
    for collection_name in ("task_lessons", "validation_lessons"):
        for lesson in list(summary_payload.get(collection_name) or []):
            if not isinstance(lesson, dict):
                continue
            record = dict(lesson)
            record["kind"] = str(record.get("kind") or collection_name[:-8]).strip() or collection_name[:-8]
            record["run_id"] = run_id
            record["generated_at"] = generated_at
            record["authority"] = ANALYZER_AUTHORITY
            record["authorityLevel"] = ANALYZER_AUTHORITY
            records.append(record)
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def write_analyzer_artifacts(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, object]] | None = None,
    validation_lessons: Sequence[Mapping[str, object]] | None = None,
    pm_hints: Sequence[object] | object | None = None,
    merge_hints: Sequence[object] | object | None = None,
    operator_actions: Sequence[object] | object | None = None,
) -> dict[str, Any]:
    run_dir_path = Path(run_dir)
    summary_payload = build_analyzer_summary(
        run_id=run_id,
        summary=summary,
        task_lessons=task_lessons,
        validation_lessons=validation_lessons,
        pm_hints=pm_hints,
        merge_hints=merge_hints,
        operator_actions=operator_actions,
    )

    summary_path = run_dir_path / ANALYZER_SUMMARY_FILENAME
    report_path = run_dir_path / ANALYZER_REPORT_FILENAME
    updates_path = run_dir_path / EXPERIENCE_UPDATES_FILENAME

    atomic_write_json(summary_path, summary_payload)
    safe_write_text(report_path, _render_report(summary_payload))
    safe_write_text(updates_path, _render_experience_updates(summary_payload))

    return {
        "ok": True,
        "run_id": summary_payload["run_id"],
        "authority": summary_payload["authority"],
        "summary_path": summary_path.as_posix(),
        "report_path": report_path.as_posix(),
        "experience_updates_path": updates_path.as_posix(),
        "summary": summary_payload,
    }


def run_advisory_analyzer(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, object]] | None = None,
    validation_lessons: Sequence[Mapping[str, object]] | None = None,
    pm_hints: Sequence[object] | object | None = None,
    merge_hints: Sequence[object] | object | None = None,
    operator_actions: Sequence[object] | object | None = None,
) -> dict[str, Any]:
    return write_analyzer_artifacts(
        run_dir,
        run_id=run_id,
        summary=summary,
        task_lessons=task_lessons,
        validation_lessons=validation_lessons,
        pm_hints=pm_hints,
        merge_hints=merge_hints,
        operator_actions=operator_actions,
    )


execute_analyzer = run_advisory_analyzer


__all__ = [
    "ANALYZER_AUTHORITY",
    "ANALYZER_FORBIDDEN_ACTIONS",
    "ANALYZER_REPORT_FILENAME",
    "ANALYZER_SUMMARY_FILENAME",
    "EXPERIENCE_UPDATES_FILENAME",
    "analyzer_authority_metadata",
    "build_analyzer_summary",
    "execute_analyzer",
    "run_advisory_analyzer",
    "write_analyzer_artifacts",
]


def build_analyzer_summary(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if len(args) == 1 and not kwargs:
        return _build_local_analyzer_summary(Path(args[0]))
    if len(args) == 2 and not kwargs:
        return _build_experience_analyzer_summary(Path(args[0]), Path(args[1]))
    return _build_advisory_analyzer_summary(*args, **kwargs)


def write_analyzer_artifacts(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if len(args) == 2 and not kwargs:
        return _write_experience_analyzer_artifacts(Path(args[0]), Path(args[1]))
    return _write_advisory_analyzer_artifacts(*args, **kwargs)


def run_advisory_analyzer(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
    active_goal_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _run_advisory_analyzer_impl(
        run_dir,
        run_id=run_id,
        summary=summary,
        task_lessons=task_lessons,
        validation_lessons=validation_lessons,
        pm_hints=pm_hints,
        merge_hints=merge_hints,
        operator_actions=operator_actions,
        active_goal_context=active_goal_context,
    )


def execute_analyzer(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
    experience_records: Sequence[Mapping[str, Any]] | None = None,
    active_goal_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if experience_records is not None:
        derived = classify_experience_lessons(experience_records)
        task_lessons = task_lessons if task_lessons is not None else derived.get("task_lessons") or []
        validation_lessons = (
            validation_lessons if validation_lessons is not None else derived.get("validation_lessons") or []
        )
        pm_hints = pm_hints if pm_hints is not None else derived.get("pm_hints") or []
        merge_hints = merge_hints if merge_hints is not None else derived.get("merge_hints") or []
        operator_actions = operator_actions if operator_actions is not None else derived.get("operator_actions") or []
    return run_advisory_analyzer(
        run_dir,
        run_id=run_id,
        summary=summary,
        task_lessons=task_lessons,
        validation_lessons=validation_lessons,
        pm_hints=pm_hints,
        merge_hints=merge_hints,
        operator_actions=operator_actions,
        active_goal_context=active_goal_context,
    )


import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .experience import classify_experience_lessons
from .utils import atomic_write_json, now_iso, safe_write_text

ANALYZER_SUMMARY_FILENAME = "ANALYZER_SUMMARY.json"
ANALYZER_REPORT_FILENAME = "ANALYZER_REPORT.md"
EXPERIENCE_UPDATES_FILENAME = "EXPERIENCE_UPDATES.jsonl"
ANALYZER_AUTHORITY = "advisory"
ANALYZER_FORBIDDEN_ACTIONS = (
    "goals_save_or_auto_check",
    "pr_merge_or_approval",
    "source_code_mutation",
    "validation_gate_bypass",
)


def analyzer_authority_metadata() -> dict[str, Any]:
    return {
        "level": ANALYZER_AUTHORITY,
        "label": ANALYZER_AUTHORITY,
        "summary": (
            "Analyzer output is advisory only. It cannot mark GOALS complete, approve merges, "
            "mutate source code, or bypass deterministic validation gates."
        ),
        "forbidden_actions": list(ANALYZER_FORBIDDEN_ACTIONS),
        "forbiddenActions": list(ANALYZER_FORBIDDEN_ACTIONS),
        "can_mark_goals_complete": False,
        "canMarkGoalsComplete": False,
        "can_approve_merge": False,
        "canApproveMerge": False,
        "can_mutate_source": False,
        "canMutateSource": False,
        "can_bypass_validation_gates": False,
        "canBypassValidationGates": False,
    }


def _coerce_text_list(values: Sequence[Any] | Any) -> list[str]:
    if not values:
        return []
    if isinstance(values, (str, bytes)):
        values = [values]
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text:
            result.append(text)
    return result


def _normalize_lesson(record: Mapping[str, Any], *, default_kind: str) -> dict[str, Any]:
    confidence_raw = record.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    evidence = _coerce_text_list(record.get("evidence"))
    lesson = {
        "task_id": str(record.get("task_id") or record.get("taskId") or "").strip(),
        "kind": str(record.get("kind") or default_kind).strip() or default_kind,
        "severity": str(record.get("severity") or "medium").strip() or "medium",
        "confidence": confidence,
        "lesson": str(record.get("lesson") or "").strip(),
        "evidence": evidence,
        "authority": ANALYZER_AUTHORITY,
        "authorityLevel": ANALYZER_AUTHORITY,
    }
    if record.get("source"):
        lesson["source"] = str(record.get("source") or "").strip()
    for key in (
        "score",
        "rank",
        "summary",
        "evidence_count",
        "evidenceCount",
        "trigger",
        "recommendation_family",
        "recommendationFamily",
        "applies_to_goal_refs",
        "appliesToGoalRefs",
        "applies_to_file_globs",
        "appliesToFileGlobs",
        "applies_to_gates",
        "appliesToGates",
        "applies_to_statuses",
        "appliesToStatuses",
        "applies_to_validation_statuses",
        "appliesToValidationStatuses",
        "pr_decisions",
        "prDecisions",
        "task_ids",
        "taskIds",
    ):
        if key in record:
            lesson[key] = record[key]
    return lesson


def _normalize_lessons(records: Sequence[Mapping[str, Any]] | None, *, default_kind: str) -> list[dict[str, Any]]:
    if not records:
        return []
    normalized = [_normalize_lesson(record, default_kind=default_kind) for record in records]
    normalized.sort(
        key=lambda record: (
            -float(record.get("confidence", 0.0) or 0.0),
            str(record.get("task_id") or ""),
            str(record.get("lesson") or ""),
        )
    )
    return normalized


def _build_advisory_analyzer_summary(
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
    active_goal_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    authority = analyzer_authority_metadata()
    normalized_task_lessons = _normalize_lessons(task_lessons, default_kind="task")
    normalized_validation_lessons = _normalize_lessons(validation_lessons, default_kind="validation")
    payload = {
        "schema_version": 1,
        "kind": "analyzer_summary",
        "generated_at": now_iso(),
        "run_id": str(run_id or "").strip(),
        "summary": str(summary or "").strip(),
        "authority": authority,
        "authority_level": ANALYZER_AUTHORITY,
        "authorityLevel": ANALYZER_AUTHORITY,
        "task_lessons": normalized_task_lessons,
        "taskLessons": normalized_task_lessons,
        "validation_lessons": normalized_validation_lessons,
        "validationLessons": normalized_validation_lessons,
        "pm_hints": _coerce_text_list(pm_hints),
        "pmHints": _coerce_text_list(pm_hints),
        "merge_hints": _coerce_text_list(merge_hints),
        "mergeHints": _coerce_text_list(merge_hints),
        "operator_actions": _coerce_text_list(operator_actions),
        "operatorActions": _coerce_text_list(operator_actions),
    }
    if isinstance(active_goal_context, Mapping):
        payload["active_goal_context"] = dict(active_goal_context)
        payload["activeGoalContext"] = dict(active_goal_context)
    return payload


def _render_report(summary_payload: Mapping[str, Any]) -> str:
    authority = summary_payload.get("authority") or {}
    report_lines = [
        "# Analyzer Report",
        "",
        f"- Run ID: {summary_payload.get('run_id') or ''}",
        f"- Generated At: {summary_payload.get('generated_at') or ''}",
        f"- Authority: {authority.get('level') if isinstance(authority, Mapping) else ANALYZER_AUTHORITY} only",
        (
            "- Boundaries: "
            f"{authority.get('summary') if isinstance(authority, Mapping) else ''}"
        ),
        "",
        "## Summary",
        "",
        str(summary_payload.get("summary") or "No summary."),
        "",
    ]
    for heading, key in (("Task Lessons", "task_lessons"), ("Validation Lessons", "validation_lessons")):
        lessons = list(summary_payload.get(key) or [])
        if not lessons:
            continue
        report_lines.extend([f"## {heading}", ""])
        for lesson in lessons:
            report_lines.append(
                f"- [{lesson.get('kind')} {lesson.get('severity')} conf={float(lesson.get('confidence') or 0.0):.2f}] "
                f"{lesson.get('lesson') or ''}"
            )
        report_lines.append("")
    for heading, key in (("PM Hints", "pm_hints"), ("Merge Hints", "merge_hints"), ("Operator Actions", "operator_actions")):
        items = list(summary_payload.get(key) or [])
        if not items:
            continue
        report_lines.extend([f"## {heading}", ""])
        for item in items:
            report_lines.append(f"- {item}")
        report_lines.append("")
    return "\n".join(report_lines).rstrip() + "\n"


def _render_experience_updates(summary_payload: Mapping[str, Any]) -> str:
    records: list[dict[str, Any]] = []
    generated_at = str(summary_payload.get("generated_at") or now_iso())
    run_id = str(summary_payload.get("run_id") or "").strip()
    for collection_name in ("task_lessons", "validation_lessons"):
        for lesson in list(summary_payload.get(collection_name) or []):
            if not isinstance(lesson, Mapping):
                continue
            record = dict(lesson)
            kind = str(record.get("kind") or collection_name[:-8]).strip() or collection_name[:-8]
            record["kind"] = kind
            record["run_id"] = run_id
            record["generated_at"] = generated_at
            record["authority"] = ANALYZER_AUTHORITY
            record["authorityLevel"] = ANALYZER_AUTHORITY
            records.append(record)
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _write_advisory_analyzer_artifacts(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
    active_goal_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir_path = Path(run_dir)
    summary_payload = _build_advisory_analyzer_summary(
        run_id=run_id,
        summary=summary,
        task_lessons=task_lessons,
        validation_lessons=validation_lessons,
        pm_hints=pm_hints,
        merge_hints=merge_hints,
        operator_actions=operator_actions,
        active_goal_context=active_goal_context,
    )
    summary_path = run_dir_path / ANALYZER_SUMMARY_FILENAME
    report_path = run_dir_path / ANALYZER_REPORT_FILENAME
    updates_path = run_dir_path / EXPERIENCE_UPDATES_FILENAME
    atomic_write_json(summary_path, summary_payload)
    safe_write_text(report_path, _render_report(summary_payload))
    safe_write_text(updates_path, _render_experience_updates(summary_payload))
    return {
        "ok": True,
        "run_id": summary_payload["run_id"],
        "authority": summary_payload["authority"],
        "summary_path": summary_path.as_posix(),
        "report_path": report_path.as_posix(),
        "experience_updates_path": updates_path.as_posix(),
        "summary": summary_payload,
    }


def _run_advisory_analyzer_impl(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
    active_goal_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return _write_advisory_analyzer_artifacts(
        run_dir,
        run_id=run_id,
        summary=summary,
        task_lessons=task_lessons,
        validation_lessons=validation_lessons,
        pm_hints=pm_hints,
        merge_hints=merge_hints,
        operator_actions=operator_actions,
        active_goal_context=active_goal_context,
    )


def _execute_analyzer_from_experience_records(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    experience_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    derived = classify_experience_lessons(experience_records or [])
    return _run_advisory_analyzer_impl(
        run_dir,
        run_id=run_id,
        summary=summary,
        task_lessons=derived.get("task_lessons") or [],
        validation_lessons=derived.get("validation_lessons") or [],
        pm_hints=derived.get("pm_hints") or [],
        merge_hints=derived.get("merge_hints") or [],
        operator_actions=derived.get("operator_actions") or [],
    )


__all__ = [
    "ANALYZER_AUTHORITY",
    "ANALYZER_FORBIDDEN_ACTIONS",
    "ANALYZER_REPORT_FILENAME",
    "ANALYZER_SUMMARY_FILENAME",
    "EXPERIENCE_UPDATES_FILENAME",
    "analyzer_authority_metadata",
    "build_analyzer_summary",
    "execute_analyzer",
    "run_advisory_analyzer",
    "write_analyzer_artifacts",
]

