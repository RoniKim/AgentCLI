from __future__ import annotations

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


def build_analyzer_summary(
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
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
        f"- Generated At: {summary_payload.get('generated_at') or ''}",
        f"- Authority: {authority.get('summary') if isinstance(authority, Mapping) else ''}",
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


def write_analyzer_artifacts(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
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
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
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


def execute_analyzer(
    run_dir: Path,
    *,
    run_id: str,
    summary: str,
    experience_records: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    derived = classify_experience_lessons(experience_records or [])
    return run_advisory_analyzer(
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

