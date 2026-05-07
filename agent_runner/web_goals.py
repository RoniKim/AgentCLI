from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .goals import goals_path, parse_goals_completion, read_goals, resolve_goals_completion_level
from .utils import atomic_write_text


GOALS_SAVE_CONFIRMATION_PHRASE = "DELETE OR DOWNGRADE UNMET P0 GOALS"


class GoalSaveFailure(Exception):
    def __init__(self, status_code: int, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details)


@dataclass(frozen=True)
class GoalSavePlan:
    goal_path: Path
    backup_path: Path
    current_path: Path | None
    current_text: str
    next_text: str
    next_items: dict[str, list[dict[str, Any]]]
    risk_report: dict[str, Any]


def _parse_goal_items_and_warnings(goals_text: str | None) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    items: dict[str, list[dict[str, Any]]] = {"p0": [], "p1": []}
    warnings: list[dict[str, Any]] = []
    if not goals_text or not goals_text.strip():
        return items, warnings

    checkbox_re = re.compile(r"^\s*-\s*\[(x| )\]\s*(.*)$", re.IGNORECASE)
    list_re = re.compile(r"^\s*[-*+]\s+")
    current_bucket: str | None = None
    ignore_outside_list_items = False
    last_item: dict[str, Any] | None = None

    def _parse_goal_note_comment(comment_text: str) -> str:
        payload = comment_text.strip()
        if not payload:
            return ""
        try:
            parsed = json.loads(payload)
        except Exception:
            return payload.strip().strip('"').strip("'")
        if parsed is None:
            return ""
        if isinstance(parsed, str):
            return parsed
        return str(parsed)

    for line_number, line in enumerate(goals_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        heading = re.match(r"^(#+)\s+(.+)$", stripped)
        if heading:
            last_item = None
            level = len(heading.group(1))
            title = heading.group(2).strip().lower()
            if level == 2 and re.match(r"p0\b", title):
                current_bucket = "p0"
                ignore_outside_list_items = False
                continue
            if level == 2 and re.match(r"p1\b", title):
                current_bucket = "p1"
                ignore_outside_list_items = False
                continue
            if level == 2 and re.match(r"p[\s_-]*[01]\b", title):
                warnings.append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "reason": "malformed_priority_section_heading",
                        "message": "Priority section headings must use ## P0 or ## P1.",
                    }
                )
            if level == 2:
                current_bucket = None
                ignore_outside_list_items = title.startswith("completion criteria")
                last_item = None
                continue
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            comment_body = stripped[4:-3].strip()
            if current_bucket in ("p0", "p1") and last_item is not None and comment_body.lower().startswith("goal-note:"):
                note_text = _parse_goal_note_comment(comment_body[len("goal-note:"):].strip())
                if note_text:
                    existing_note = str(last_item.get("note") or "")
                    last_item["note"] = f"{existing_note}\n{note_text}".strip() if existing_note else note_text
                continue
            continue

        match = checkbox_re.match(line)
        if match:
            done = match.group(1).strip().lower() == "x"
            if current_bucket in ("p0", "p1"):
                item_text = match.group(2).strip()
                item = {
                    "done": done,
                    "checked": done,
                    "checkbox": "[x]" if done else "[ ]",
                    "text": item_text,
                    "note": "",
                    "line_number": line_number,
                    "line": line_number,
                }
                items[current_bucket].append(item)
                last_item = item
            else:
                warnings.append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "reason": "checkbox_outside_goal_section",
                        "message": "Checkbox item outside P0/P1 was ignored.",
                    }
                )
            continue

        if current_bucket in ("p0", "p1"):
            last_item = None
            warnings.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "unsupported_goal_line",
                    "message": "Non-checkbox content inside a GOALS section was ignored.",
                }
            )
            continue

        if list_re.match(line) and not ignore_outside_list_items:
            warnings.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "unsupported_list_item",
                    "message": "List item outside P0/P1 was ignored.",
                }
            )
            last_item = None

    return items, warnings


def _build_goals_payload(repo: Path, *, completion_level: str = "all") -> dict[str, Any]:
    completion_level = resolve_goals_completion_level(completion_level)
    goal_path = goals_path(repo)
    exists = False
    mtime = None
    size = None
    try:
        exists = goal_path.exists() and goal_path.is_file()
    except OSError:
        exists = False
    if exists:
        try:
            stat = goal_path.stat()
            mtime = stat.st_mtime
            size = stat.st_size
        except OSError:
            mtime = None
            size = None

    _path, raw_text = read_goals(repo)
    raw_text = raw_text or ""
    items, warnings = _parse_goal_items_and_warnings(raw_text)
    completion = parse_goals_completion(raw_text, completion_level=completion_level)
    p0_total = len(items["p0"])
    p1_total = len(items["p1"])
    p0_done = len([item for item in items["p0"] if item.get("done")])
    p1_done = len([item for item in items["p1"] if item.get("done")])
    total = p0_total + p1_total
    done = p0_done + p1_done
    missing_sections = list(completion.get("missing_sections") or [])
    summary = {
        "has_goals": bool(completion.get("has_goals")),
        "project_complete": bool(completion.get("project_complete")),
        "valid": bool(completion.get("valid", False)),
        "missing_sections": missing_sections,
        "p0_total": p0_total,
        "p0_done": p0_done,
        "p1_total": p1_total,
        "p1_done": p1_done,
        "all_total": int(completion.get("all_total") or total),
        "all_done": int(completion.get("all_done") or done),
        "total": total,
        "done": done,
        "unchecked": max(0, total - done),
        "warnings": len(warnings),
    }
    return {
        "path": goal_path.as_posix(),
        "exists": bool(exists),
        "mtime": mtime,
        "size": size,
        "raw_text": raw_text,
        "items": items,
        "completion": completion,
        "summary": summary,
        "warnings": warnings,
        "completion_level": completion_level,
    }


def _goal_items(goals_text: str | None) -> dict[str, list[dict[str, Any]]]:
    return _parse_goal_items_and_warnings(goals_text)[0]


def _goal_save_backup_path(goal_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%fZ")
    return goal_path.with_name(f"{goal_path.stem}.{stamp}.bak{goal_path.suffix}")


def _goal_save_item_identity(item: dict[str, Any], *, use_line_number: bool = True) -> str:
    line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
    if use_line_number and line_number > 0:
        return f"line:{line_number}"
    return f"sig:{_goal_save_item_signature(item)}"


def _goal_save_item_signature(item: dict[str, Any]) -> str:
    return json.dumps(
        {
            "done": bool(item.get("done")),
            "checked": bool(item.get("checked", item.get("done"))),
            "checkbox": "[x]" if bool(item.get("done") or item.get("checked")) else "[ ]",
            "text": str(item.get("text") or "").strip(),
            "note": str(item.get("note") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _goal_save_normalize_item(raw_item: Any) -> dict[str, Any]:
    item = raw_item if isinstance(raw_item, dict) else {}
    done = bool(item.get("done") if "done" in item else item.get("checked"))
    checked = bool(item.get("checked") if "checked" in item else done)
    line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
    return {
        "done": done,
        "checked": checked,
        "checkbox": "[x]" if done else "[ ]",
        "text": str(item.get("text") or "").strip(),
        "note": str(item.get("note") or ""),
        "line_number": line_number,
        "lineNumber": line_number,
        "line": line_number,
    }


def _goal_save_normalize_draft(raw_draft: Any) -> dict[str, list[dict[str, Any]]]:
    raw = raw_draft if isinstance(raw_draft, dict) else {}
    items = raw.get("items") if isinstance(raw.get("items"), dict) else raw
    normalized: dict[str, list[dict[str, Any]]] = {"p0": [], "p1": []}
    for bucket in ("p0", "p1"):
        raw_bucket = items.get(bucket) if isinstance(items, dict) else []
        if not isinstance(raw_bucket, list):
            raw_bucket = []
        normalized[bucket] = [_goal_save_normalize_item(item) for item in raw_bucket]
    return normalized


def _goal_save_note_comment_line(note_line: str) -> str:
    return f"<!-- goal-note: {json.dumps(note_line, ensure_ascii=False)} -->"


def _goal_save_item_lines(item: dict[str, Any]) -> list[str]:
    text = str(item.get("text") or "").strip()
    if not text:
        raise ValueError("Goal text cannot be empty.")
    done = bool(item.get("done") or item.get("checked"))
    lines = [f"- [{'x' if done else ' '}] {text}"]
    note = str(item.get("note") or "")
    if note:
        for note_line in note.splitlines():
            lines.append(_goal_save_note_comment_line(note_line))
    return lines


def _goal_save_section_lines(items: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = [""]
    for item in items:
        lines.extend(_goal_save_item_lines(item))
        lines.append("")
    return lines


def _goal_save_serialize_draft(draft: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = ["# Project Goals", ""]
    lines.append("## P0")
    lines.extend(_goal_save_section_lines(list(draft.get("p0") or [])))
    lines.append("## P1")
    lines.extend(_goal_save_section_lines(list(draft.get("p1") or [])))
    return "\n".join(lines).rstrip() + "\n"


def _goal_save_structured_draft_loss_report(current_text: str) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    current_bucket: str | None = None
    last_goal_line = False
    checkbox_re = re.compile(r"^\s*-\s*\[(x| )\]\s*(.*)$", re.IGNORECASE)

    for line_number, line in enumerate((current_text or "").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue

        heading = re.match(r"^(#+)\s+(.+)$", stripped)
        if heading:
            level = len(heading.group(1))
            title = heading.group(2).strip()
            normalized_title = title.lower()
            last_goal_line = False
            if level == 1 and normalized_title == "project goals":
                continue
            if level == 2 and re.match(r"p0\b", normalized_title):
                current_bucket = "p0"
                continue
            if level == 2 and re.match(r"p1\b", normalized_title):
                current_bucket = "p1"
                continue
            if current_bucket in ("p0", "p1") and level >= 3:
                issues.append(
                    {
                        "line_number": line_number,
                        "line": line,
                        "reason": "subgroup_heading",
                        "message": "Structured goal drafts cannot preserve subgroup headings.",
                    }
                )
                continue
            current_bucket = None
            issues.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "surrounding_heading",
                    "message": "Structured goal drafts cannot preserve non-priority headings.",
                }
            )
            continue

        if checkbox_re.match(line):
            if current_bucket in ("p0", "p1"):
                last_goal_line = True
                continue
            issues.append(
                {
                    "line_number": line_number,
                    "line": line,
                    "reason": "checkbox_outside_goal_section",
                    "message": "Structured goal drafts cannot preserve checklist items outside P0/P1.",
                }
            )
            continue

        if stripped.startswith("<!--") and stripped.endswith("-->"):
            comment_body = stripped[4:-3].strip().lower()
            if current_bucket in ("p0", "p1") and last_goal_line and comment_body.startswith("goal-note:"):
                continue

        issues.append(
            {
                "line_number": line_number,
                "line": line,
                "reason": "unsupported_goal_line" if current_bucket in ("p0", "p1") else "surrounding_note",
                "message": "Structured goal drafts cannot preserve raw markdown around goal checkboxes.",
            }
        )
        last_goal_line = False

    return {
        "would_lose_structure": bool(issues),
        "wouldLoseStructure": bool(issues),
        "issues": issues,
        "issue_count": len(issues),
        "issueCount": len(issues),
    }


def _goal_save_has_required_sections(raw_text: str) -> bool:
    return bool(
        re.search(r"^##\s+p0\b", raw_text or "", re.IGNORECASE | re.MULTILINE)
        and re.search(r"^##\s+p1\b", raw_text or "", re.IGNORECASE | re.MULTILINE)
    )


def _goal_save_risk_report(
    current_items: dict[str, list[dict[str, Any]]],
    next_items: dict[str, list[dict[str, Any]]],
    *,
    use_line_numbers: bool,
) -> dict[str, Any]:
    next_line_index: dict[str, dict[str, int]] = {"p0": {}, "p1": {}}
    next_signature_index: dict[str, dict[str, int]] = {"p0": {}, "p1": {}}
    for bucket in ("p0", "p1"):
        for item in next_items.get(bucket, []):
            signature = _goal_save_item_signature(item)
            next_signature_index[bucket][signature] = next_signature_index[bucket].get(signature, 0) + 1
            if use_line_numbers:
                line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
                if line_number > 0:
                    line_identity = f"line:{line_number}"
                    next_line_index[bucket][line_identity] = next_line_index[bucket].get(line_identity, 0) + 1

    deleted: list[dict[str, Any]] = []
    downgraded: list[dict[str, Any]] = []

    def _consume(index: dict[str, dict[str, int]], bucket: str, identity: str) -> bool:
        if not identity:
            return False
        current_count = index[bucket].get(identity, 0)
        if current_count <= 0:
            return False
        index[bucket][identity] = current_count - 1
        return True

    for item in current_items.get("p0", []):
        if bool(item.get("done") or item.get("checked")):
            continue
        signature = _goal_save_item_signature(item)
        line_number = int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0)
        line_identity = f"line:{line_number}" if use_line_numbers and line_number > 0 else ""
        if _consume(next_line_index, "p0", line_identity) or _consume(next_signature_index, "p0", signature):
            continue
        if _consume(next_line_index, "p1", line_identity) or _consume(next_signature_index, "p1", signature):
            downgraded.append(item)
        else:
            deleted.append(item)

    return {
        "requires_confirmation": bool(deleted or downgraded),
        "requiresConfirmation": bool(deleted or downgraded),
        "confirmation_phrase": GOALS_SAVE_CONFIRMATION_PHRASE,
        "confirmationPhrase": GOALS_SAVE_CONFIRMATION_PHRASE,
        "deleted_unchecked_p0": deleted,
        "deletedUncheckedP0": deleted,
        "downgraded_unchecked_p0": downgraded,
        "downgradedUncheckedP0": downgraded,
        "risk_count": len(deleted) + len(downgraded),
        "riskCount": len(deleted) + len(downgraded),
    }


async def _goal_save_body(request: Any) -> dict[str, Any] | None:
    try:
        payload = await request.json()
    except Exception:
        return None
    if isinstance(payload, dict):
        return payload
    return None


def _goal_save_validate_request(body: dict[str, Any], *, repo_root: Path, goal_path: Path) -> GoalSavePlan:
    if goal_path.exists() and not goal_path.is_file():
        raise GoalSaveFailure(400, "goals_path_not_file", "GOALS.md path must reference a file.", path=goal_path.as_posix())

    raw_draft = body.get("draft")
    if raw_draft is None:
        raw_draft = body.get("goals")
    raw_text_value = body.get("raw_text")
    if raw_text_value is None:
        raw_text_value = body.get("text")
    if raw_text_value is None:
        raw_text_value = body.get("content")

    next_items: dict[str, list[dict[str, Any]]] | None = None
    next_text = ""
    use_line_numbers = False
    if isinstance(raw_draft, dict):
        next_items = _goal_save_normalize_draft(raw_draft)
        next_text = _goal_save_serialize_draft(next_items)
        use_line_numbers = True
    elif raw_text_value is not None:
        if not isinstance(raw_text_value, str):
            raise GoalSaveFailure(400, "goals_raw_text_invalid", "Goals save raw text must be a string.", field="raw_text")
        next_text = raw_text_value
        next_items = _goal_items(next_text)
    else:
        raise GoalSaveFailure(400, "goals_input_required", "Goals save request must include draft or raw_text.", field="draft")

    next_items = _goal_save_normalize_draft(next_items)
    if not _goal_save_has_required_sections(next_text):
        raise GoalSaveFailure(
            400,
            "goals_sections_required",
            "GOALS.md must include both ## P0 and ## P1 sections.",
            path=goal_path.as_posix(),
        )

    blank_item = next(
        (
            {
                "bucket": bucket,
                "line_number": int(item.get("line_number") or item.get("lineNumber") or item.get("line") or 0),
            }
            for bucket in ("p0", "p1")
            for item in next_items.get(bucket, [])
            if not str(item.get("text") or "").strip()
        ),
        None,
    )
    if blank_item is not None:
        raise GoalSaveFailure(
            400,
            "goals_item_text_required",
            "Goal text cannot be empty.",
            path=goal_path.as_posix(),
            bucket=blank_item["bucket"],
            line_number=blank_item["line_number"],
        )

    current_path, current_raw = read_goals(repo_root)
    if current_path is not None and current_raw is None:
        raise GoalSaveFailure(400, "goals_read_error", "Existing GOALS.md could not be read.", path=goal_path.as_posix())

    current_text = current_raw or ""
    if isinstance(raw_draft, dict):
        loss_report = _goal_save_structured_draft_loss_report(current_text)
        if loss_report["would_lose_structure"]:
            raise GoalSaveFailure(
                400,
                "goals_structured_draft_would_lose_markdown",
                "Structured goal draft save would lose GOALS.md headings, notes, or unsupported raw markdown; submit raw_text instead.",
                path=goal_path.as_posix(),
                loss=loss_report,
                use_raw_text=True,
            )
    current_items = _goal_save_normalize_draft(_goal_items(current_text))
    risk_report = _goal_save_risk_report(current_items, next_items, use_line_numbers=use_line_numbers)

    confirm_raw = body.get("confirm")
    if confirm_raw is None:
        confirm_raw = body.get("confirmation")
    if confirm_raw is None:
        confirm_raw = body.get("confirmation_phrase")
    confirmation = str(confirm_raw).strip() if confirm_raw is not None else ""
    if risk_report["requires_confirmation"]:
        if not confirmation:
            raise GoalSaveFailure(
                400,
                "goals_confirmation_required",
                "Deleting or downgrading unmet P0 goals requires the exact confirmation phrase.",
                path=goal_path.as_posix(),
                confirmation_phrase=risk_report["confirmation_phrase"],
                risk=risk_report,
            )
        if confirmation != risk_report["confirmation_phrase"]:
            raise GoalSaveFailure(
                400,
                "goals_confirmation_mismatch",
                "The goals confirmation phrase did not match.",
                path=goal_path.as_posix(),
                confirmation_phrase=risk_report["confirmation_phrase"],
                risk=risk_report,
            )

    if next_text == current_text:
        raise GoalSaveFailure(400, "goals_no_changes", "No goal changes were supplied.", path=goal_path.as_posix())

    return GoalSavePlan(
        goal_path=goal_path,
        backup_path=_goal_save_backup_path(goal_path),
        current_path=current_path,
        current_text=current_text,
        next_text=next_text,
        next_items=next_items,
        risk_report=risk_report,
    )


def _goal_save_commit(plan: GoalSavePlan, *, snapshot_factory: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    plan.backup_path.parent.mkdir(parents=True, exist_ok=True)
    if plan.current_path is not None and plan.goal_path.exists():
        shutil.copy2(plan.goal_path, plan.backup_path)
    else:
        atomic_write_text(plan.backup_path, plan.current_text)

    atomic_write_text(plan.goal_path, plan.next_text)
    snapshot = snapshot_factory()
    return {
        "ok": True,
        "action": "goals-save",
        "status": "saved",
        "message": f"Goals saved. Backup written to {plan.backup_path.as_posix()}.",
        "goals_path": plan.goal_path.as_posix(),
        "saved_path": plan.goal_path.as_posix(),
        "backup_path": plan.backup_path.as_posix(),
        "risk": plan.risk_report,
        "risk_report": plan.risk_report,
        "snapshot": snapshot,
    }
