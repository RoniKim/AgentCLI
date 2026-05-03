from __future__ import annotations

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
