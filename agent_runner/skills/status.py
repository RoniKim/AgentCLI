from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from ..state import TaskItem, load_backlog_json
from .indexer import SkillRecord, build_skills_index, resolve_skills_roots
from .match import suggest_skills


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _as_roots(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, dict)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def selected_skill_ids_from_tasks(tasks: Iterable[Any]) -> list[str]:
    selected: list[str] = []
    for task in tasks:
        if isinstance(task, TaskItem):
            skills = task.skills
        elif isinstance(task, dict):
            raw_skills = task.get("skills") or task.get("skill_ids") or task.get("skillIds") or []
            if isinstance(raw_skills, str):
                skills = [part.strip() for part in raw_skills.split(",") if part.strip()]
            elif isinstance(raw_skills, list):
                skills = [str(item).strip() for item in raw_skills if str(item).strip()]
            else:
                skills = []
        else:
            skills = [str(item).strip() for item in getattr(task, "skills", []) or [] if str(item).strip()]
        for skill_id in skills:
            if skill_id and skill_id not in selected:
                selected.append(skill_id)
    return selected


def selected_skill_ids_from_run_dir(run_dir: Path | None) -> list[str]:
    if run_dir is None:
        return []
    backlog_json = Path(run_dir) / "BACKLOG.json"
    if not backlog_json.exists():
        return []
    try:
        return selected_skill_ids_from_tasks(load_backlog_json(backlog_json))
    except Exception:
        return []


def _root_status(raw: str, path: Path) -> dict[str, Any]:
    exists = path.exists()
    is_dir = path.is_dir()
    status = "ready" if exists and is_dir else "not_directory" if exists else "missing"
    return {
        "raw": raw,
        "path": path.as_posix(),
        "exists": exists,
        "is_dir": is_dir,
        "status": status,
    }


def _suggestion_payload(missing_skill_id: str, records: list[SkillRecord]) -> dict[str, Any]:
    matches = suggest_skills(missing_skill_id, records, max_results=3)
    return {
        "missing": missing_skill_id,
        "matches": [
            {
                "skill_id": match.skill_id,
                "skillId": match.skill_id,
                "name": match.name,
                "score": round(float(match.score), 3),
            }
            for match in matches
        ],
    }


def build_skills_status(
    repo: Path,
    skills_cfg: dict[str, Any] | None,
    *,
    run_dir: Path | None = None,
    selected_skill_ids: Iterable[str] | None = None,
    records: list[SkillRecord] | None = None,
) -> dict[str, Any]:
    cfg = skills_cfg if isinstance(skills_cfg, dict) else {}
    enabled = _as_bool(cfg.get("enabled", False))
    raw_roots = _as_roots(cfg.get("roots") or [])
    resolved_roots = resolve_skills_roots(repo, raw_roots)
    root_items = [_root_status(raw, path) for raw, path in zip(raw_roots, resolved_roots)]

    indexed_records = records if records is not None else (build_skills_index(resolved_roots) if enabled else [])
    skills_by_id = {record.skill_id: record for record in indexed_records}
    selected = list(dict.fromkeys(
        str(skill_id).strip()
        for skill_id in (selected_skill_ids if selected_skill_ids is not None else selected_skill_ids_from_run_dir(run_dir))
        if str(skill_id).strip()
    ))
    missing = [skill_id for skill_id in selected if skill_id not in skills_by_id] if enabled else []
    suggestions = [_suggestion_payload(skill_id, indexed_records) for skill_id in missing]

    warnings: list[str] = []
    if enabled:
        for root in root_items:
            if root["status"] == "missing":
                warnings.append(f"missing skill root: {root['path']}")
            elif root["status"] == "not_directory":
                warnings.append(f"skill root is not a directory: {root['path']}")
        if not indexed_records:
            warnings.append("skills enabled but no SKILL.md files were discovered")
        for skill_id in missing:
            warnings.append(f"missing selected skill: {skill_id}")

    existing_root_count = sum(1 for item in root_items if item["exists"] and item["is_dir"])
    skill_ids = [record.skill_id for record in indexed_records]
    return {
        "enabled": enabled,
        "roots": root_items,
        "configured_roots": [item["path"] for item in root_items],
        "configuredRoots": [item["path"] for item in root_items],
        "root_count": len(root_items),
        "rootCount": len(root_items),
        "existing_root_count": existing_root_count,
        "existingRootCount": existing_root_count,
        "discovered_count": len(indexed_records),
        "discoveredCount": len(indexed_records),
        "skill_ids": skill_ids,
        "skillIds": skill_ids,
        "selected_skill_ids": selected,
        "selectedSkillIds": selected,
        "missing_skill_ids": missing,
        "missingSkillIds": missing,
        "suggestions": suggestions,
        "warnings": warnings,
        "inline_mode": str(cfg.get("inline_mode") or ""),
        "inlineMode": str(cfg.get("inline_mode") or ""),
        "snapshot_dir": str(cfg.get("snapshot_dir") or ""),
        "snapshotDir": str(cfg.get("snapshot_dir") or ""),
        "skill_match_autofix": _as_bool(cfg.get("skill_match_autofix", False)),
        "skillMatchAutofix": _as_bool(cfg.get("skill_match_autofix", False)),
        "skill_match_autofix_threshold": _as_float(cfg.get("skill_match_autofix_threshold"), 0.0),
        "skillMatchAutofixThreshold": _as_float(cfg.get("skill_match_autofix_threshold"), 0.0),
    }


def format_skills_status_lines(status: dict[str, Any], *, indent: str = "") -> list[str]:
    roots = status.get("roots") if isinstance(status.get("roots"), list) else []
    selected = status.get("selected_skill_ids") if isinstance(status.get("selected_skill_ids"), list) else []
    missing = status.get("missing_skill_ids") if isinstance(status.get("missing_skill_ids"), list) else []
    warnings = status.get("warnings") if isinstance(status.get("warnings"), list) else []
    suggestions = status.get("suggestions") if isinstance(status.get("suggestions"), list) else []

    lines = [
        (
            f"{indent}skills: enabled={bool(status.get('enabled'))} "
            f"roots={int(status.get('existing_root_count') or 0)}/{int(status.get('root_count') or 0)} "
            f"discovered={int(status.get('discovered_count') or 0)} "
            f"selected={len(selected)} missing={len(missing)}"
        )
    ]
    for root in roots:
        if isinstance(root, dict):
            lines.append(f"{indent}  - root {root.get('status') or 'unknown'}: {root.get('path') or root.get('raw') or ''}")
    if selected:
        lines.append(f"{indent}  - selected skill ids: {', '.join(str(item) for item in selected)}")
    if missing:
        lines.append(f"{indent}  - missing skill ids: {', '.join(str(item) for item in missing)}")
    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            continue
        matches = suggestion.get("matches") if isinstance(suggestion.get("matches"), list) else []
        match_text = ", ".join(
            f"{match.get('skill_id') or match.get('skillId')} ({match.get('name')}, {float(match.get('score') or 0):.2f})"
            for match in matches
            if isinstance(match, dict)
        )
        if match_text:
            lines.append(f"{indent}  - suggestion for {suggestion.get('missing')}: {match_text}")
    for warning in warnings:
        lines.append(f"{indent}  - WARNING: {warning}")
    return lines
