from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .experience import experience_root, list_lessons, normalize_trigger, upsert_lessons
from .state import load_backlog_json, load_state, parse_backlog_md
from .utils import atomic_write_json, eprint, now_iso, safe_write_text

ANALYZER_SCHEMA_VERSION = 2
_VALIDATION_GAP_STATUSES = {"no_tests_found", "tests_skipped", "validation_pending"}


def build_analyzer_summary(repo: Path, run_dir: Path) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    run_dir_path = Path(run_dir).expanduser().resolve()
    backlog_map = _load_backlog_context(run_dir_path)
    state = load_state(run_dir_path / "STATE.json")
    validation_by_task = _load_validation_payloads(run_dir_path)

    lessons: list[dict[str, Any]] = []
    lessons.extend(_build_failed_task_lessons(repo_path, run_dir_path, backlog_map, validation_by_task))
    lessons.extend(_build_validation_gap_lessons(repo_path, run_dir_path, backlog_map, validation_by_task))
    lessons.extend(_build_pr_decision_lessons(repo_path, run_dir_path))

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
        "counts": {
            "lessons": len(stored_lessons),
            "task_lessons": len(task_lessons),
            "validation_lessons": len(validation_lessons),
            "merge_lessons": len(merge_lessons),
        },
        "artifacts": artifacts,
    }


def write_analyzer_artifacts(repo: Path, run_dir: Path) -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    run_dir_path = Path(run_dir).expanduser().resolve()
    try:
        summary = build_analyzer_summary(repo_path, run_dir_path)
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


def _json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


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
