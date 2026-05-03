from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

from .config import AGENT_WORK_DIR
from .failure_policy import should_preserve_for_review

DEFAULT_EXPERIENCE_PROMPT_MAX_ITEMS = 12
DEFAULT_EXPERIENCE_PROMPT_MAX_CHARS = 4000
DEFAULT_EXPERIENCE_LESSON_MAX_CHARS = 240
DEFAULT_EXPERIENCE_EVIDENCE_MAX_ITEMS = 3
DEFAULT_EXPERIENCE_RETENTION_DAYS = 90

_RAW_ARTIFACT_TOKENS = (
    "metrics.jsonl",
    "run.log",
    "error.log",
    "test.txt",
    "diff --git",
    "@@ ",
    "+++ ",
    "--- ",
    "ignore previous instructions",
    "system prompt",
    "assistant:",
    "user:",
    "<pm_output_contract>",
)
_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*+]\s+(?P<text>.+\S)\s*$")

_ADVISORY_AUTHORITY = "advisory"
_WEB_FILE_SUFFIXES = {
    ".css",
    ".cshtml",
    ".html",
    ".htm",
    ".js",
    ".jsx",
    ".razor",
    ".scss",
    ".sass",
    ".ts",
    ".tsx",
}
_VALIDATION_GAP_STATUSES = {"validation_pending", "tests_skipped", "no_tests_found"}
_ADVERSE_TASK_STATUSES = {
    "blocked_env",
    "failed",
    "regression_failed",
    "review_required",
    "test_contract_changed",
}
_ADVERSE_VALIDATION_STATUSES = {
    "blocked_env",
    "no_tests_found",
    "tests_skipped",
    "validation_failed",
    "validation_pending",
}
_DISCARD_DECISIONS = {"discarded", "worktree_merge_discarded"}
_IGNORED_SURFACE_ROOTS = {".agentcli", ".doc", "docs", "tests"}
_EVIDENCE_FIELD_CANDIDATES = (
    "evidence",
    "evidence_pointers",
    "evidencePointers",
    "artifact_links",
    "artifactLinks",
)
_TIMESTAMP_FIELD_CANDIDATES = (
    "last_seen_at",
    "lastSeenAt",
    "updated_at",
    "updatedAt",
    "created_at",
    "createdAt",
    "seen_at",
    "seenAt",
    "timestamp",
)
_LESSON_ID_FIELD_CANDIDATES = (
    "id",
    "lesson_id",
    "lessonId",
    "task_id",
    "taskId",
    "trigger",
)


@dataclass(frozen=True)
class ExperiencePromptConfig:
    enabled: bool = True
    max_items: int = DEFAULT_EXPERIENCE_PROMPT_MAX_ITEMS
    max_chars: int = DEFAULT_EXPERIENCE_PROMPT_MAX_CHARS
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS
    evidence_max_items: int = DEFAULT_EXPERIENCE_EVIDENCE_MAX_ITEMS
    redact_paths: bool = True


@dataclass(frozen=True)
class ExperienceRetentionConfig:
    enabled: bool = True
    retention_days: int = DEFAULT_EXPERIENCE_RETENTION_DAYS
    delete_artifacts: bool = True
    preserve_pending_pr_queue: bool = True
    preserve_active_run_artifacts: bool = True
    preserve_review_required_evidence: bool = True


@dataclass
class ExperienceRetentionResult:
    dry_run: bool
    retention_days: int
    cutoff_iso: str
    lessons_before: int
    lessons_after: int = 0
    pruned_lessons: list[dict[str, Any]] = field(default_factory=list)
    preserved_lessons: list[dict[str, Any]] = field(default_factory=list)
    pruned_evidence: list[dict[str, Any]] = field(default_factory=list)
    preserved_evidence: list[dict[str, Any]] = field(default_factory=list)
    skipped_evidence: list[dict[str, Any]] = field(default_factory=list)
    deleted_paths: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    updated_payload: Any = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "retention_days": self.retention_days,
            "cutoff_iso": self.cutoff_iso,
            "lessons_before": self.lessons_before,
            "lessons_after": self.lessons_after,
            "pruned_lessons": list(self.pruned_lessons),
            "preserved_lessons": list(self.preserved_lessons),
            "pruned_evidence": list(self.pruned_evidence),
            "preserved_evidence": list(self.preserved_evidence),
            "skipped_evidence": list(self.skipped_evidence),
            "deleted_paths": list(self.deleted_paths),
            "missing_paths": list(self.missing_paths),
            "updated_payload": deepcopy(self.updated_payload),
        }


@dataclass(frozen=True)
class _ExperienceRecord:
    task_id: str
    title: str
    goal_refs: tuple[str, ...]
    changed_files: tuple[str, ...]
    file_globs: tuple[str, ...]
    gates: tuple[str, ...]
    task_status: str
    validation_status: str
    pr_decision: str
    reason: str
    failure_signature: str
    evidence: tuple[str, ...]
    blocked_dependencies: tuple[dict[str, Any], ...]


def _get_arg_value(args: Any, name: str, default: Any = None) -> Any:
    if isinstance(args, Mapping):
        return args.get(name, default)
    return getattr(args, name, default)


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return default


def _coerce_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except Exception:
        return max(minimum, int(default))


def experience_prompt_config_from_args(args: Any) -> ExperiencePromptConfig:
    raw_cfg = _get_arg_value(args, "experience", {})
    cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
    enabled = _coerce_bool(
        cfg.get("pm_use_experience_summary", _get_arg_value(args, "pm_use_experience_summary", None)),
        True,
    )
    db_enabled = _coerce_bool(
        cfg.get("experience_db_enabled", _get_arg_value(args, "experience_db_enabled", None)),
        True,
    )
    return ExperiencePromptConfig(
        enabled=enabled and db_enabled,
        max_items=_coerce_int(
            cfg.get("experience_prompt_max_items", _get_arg_value(args, "experience_prompt_max_items", None)),
            DEFAULT_EXPERIENCE_PROMPT_MAX_ITEMS,
            minimum=0,
        ),
        max_chars=_coerce_int(
            cfg.get("experience_prompt_max_chars", _get_arg_value(args, "experience_prompt_max_chars", None)),
            DEFAULT_EXPERIENCE_PROMPT_MAX_CHARS,
            minimum=0,
        ),
        lesson_max_chars=_coerce_int(
            cfg.get("experience_lesson_max_chars", _get_arg_value(args, "experience_lesson_max_chars", None)),
            DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
            minimum=0,
        ),
        evidence_max_items=_coerce_int(
            cfg.get("experience_evidence_max_items", _get_arg_value(args, "experience_evidence_max_items", None)),
            DEFAULT_EXPERIENCE_EVIDENCE_MAX_ITEMS,
            minimum=0,
        ),
        redact_paths=_coerce_bool(
            cfg.get("experience_redact_paths", _get_arg_value(args, "experience_redact_paths", None)),
            True,
        ),
    )


def experience_retention_config_from_args(args: Any) -> ExperienceRetentionConfig:
    raw_cfg = _get_arg_value(args, "experience", {})
    cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
    return ExperienceRetentionConfig(
        enabled=_coerce_bool(
            cfg.get("experience_retention_enabled", _get_arg_value(args, "experience_retention_enabled", None)),
            True,
        )
        and _coerce_bool(
            cfg.get("experience_db_enabled", _get_arg_value(args, "experience_db_enabled", None)),
            True,
        ),
        retention_days=_coerce_int(
            cfg.get("experience_retention_days", _get_arg_value(args, "experience_retention_days", None)),
            DEFAULT_EXPERIENCE_RETENTION_DAYS,
            minimum=0,
        ),
        delete_artifacts=_coerce_bool(
            cfg.get("experience_retention_delete_artifacts", _get_arg_value(args, "experience_retention_delete_artifacts", None)),
            True,
        ),
        preserve_pending_pr_queue=_coerce_bool(
            cfg.get("experience_preserve_pending_pr_queue", _get_arg_value(args, "experience_preserve_pending_pr_queue", None)),
            True,
        ),
        preserve_active_run_artifacts=_coerce_bool(
            cfg.get("experience_preserve_active_run_artifacts", _get_arg_value(args, "experience_preserve_active_run_artifacts", None)),
            True,
        ),
        preserve_review_required_evidence=_coerce_bool(
            cfg.get("experience_preserve_review_required_evidence", _get_arg_value(args, "experience_preserve_review_required_evidence", None)),
            True,
        ),
    )


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_markdown_bullets(path: Path) -> list[str]:
    try:
        if not path.exists() or not path.is_file():
            return []
        raw = path.read_text(encoding="utf-8", errors="replace")
        bullets: list[str] = []
        for line in raw.splitlines():
            match = _MARKDOWN_BULLET_RE.match(line)
            if match:
                bullets.append(match.group("text"))
        return bullets
    except Exception:
        return []


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _utc_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _parse_timestamp(value: Any) -> datetime | None:
    text = _normalize_whitespace(str(value or ""))
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        parsed = None
    if parsed is None:
        for fmt in ("%Y%m%d-%H%M%S", "%Y%m%d-%H%M%S-%f"):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except Exception:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _path_is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except Exception:
        return False


def _experience_managed_roots(repo_root: Path) -> tuple[Path, Path, Path]:
    work_root = repo_root / AGENT_WORK_DIR
    return (
        work_root / "agent_runs",
        work_root / "experience",
        work_root / "pr_queue",
    )


def _normalize_active_run_dirs(active_run_dirs: Sequence[Path | str] | None) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for value in active_run_dirs or []:
        try:
            resolved = Path(value).expanduser().resolve(strict=False)
        except Exception:
            continue
        if resolved not in normalized:
            normalized.append(resolved)
    return tuple(normalized)


def _extract_lessons_container(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> tuple[Any, str | None, list[dict[str, Any]]]:
    cloned = deepcopy(payload)
    if isinstance(cloned, list):
        lessons = [dict(item) for item in cloned if isinstance(item, Mapping)]
        return cloned, None, lessons
    if isinstance(cloned, Mapping):
        container = dict(cloned)
        for key in ("lessons", "items"):
            value = container.get(key)
            if isinstance(value, list):
                lessons = [dict(item) for item in value if isinstance(item, Mapping)]
                return container, key, lessons
        container["lessons"] = []
        return container, "lessons", []
    raise TypeError("Experience payload must be a mapping or list of lesson records.")


def _decode_json_sequence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        if text.startswith("[") and text.endswith("]"):
            try:
                loaded = json.loads(text)
            except Exception:
                loaded = None
            if isinstance(loaded, list):
                return list(loaded)
        return [value]
    return [value] if value not in (None, "") else []


def _record_identifier(record: Mapping[str, Any]) -> str:
    for key in _LESSON_ID_FIELD_CANDIDATES:
        text = _normalize_whitespace(str(record.get(key) or ""))
        if text:
            return text
    lesson = _normalize_whitespace(str(record.get("lesson") or record.get("summary") or ""))
    return lesson[:80] if lesson else "lesson"


def _record_timestamp(record: Mapping[str, Any]) -> tuple[datetime | None, str]:
    for key in _TIMESTAMP_FIELD_CANDIDATES:
        parsed = _parse_timestamp(record.get(key))
        if parsed is not None:
            return parsed, key
    return None, ""


def _record_requires_review(record: Mapping[str, Any]) -> bool:
    if bool(record.get("review_required") or record.get("reviewRequired")):
        return True
    for key in ("task_status", "taskStatus", "status"):
        status = _normalize_whitespace(str(record.get(key) or "")).lower()
        if status and should_preserve_for_review(status):
            return True
    statuses = record.get("applies_to_statuses") or record.get("appliesToStatuses") or []
    for status in _decode_json_sequence(statuses):
        if should_preserve_for_review(str(status or "").strip().lower()):
            return True
    return False


def _record_evidence_key(record: Mapping[str, Any]) -> str:
    for key in _EVIDENCE_FIELD_CANDIDATES:
        if key in record:
            return key
    return "evidence"


def _record_evidence_values(record: Mapping[str, Any]) -> list[str]:
    key = _record_evidence_key(record)
    raw_items = _decode_json_sequence(record.get(key))
    items: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _normalize_whitespace(str(item or ""))
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _resolve_evidence_path(pointer: str, repo_root: Path) -> tuple[Path | None, str]:
    text = _normalize_whitespace(pointer)
    if not text or "\x00" in text:
        return None, "malformed_pointer"
    try:
        candidate = Path(text).expanduser()
    except Exception:
        return None, "malformed_pointer"
    try:
        resolved = candidate.resolve(strict=False) if candidate.is_absolute() else (repo_root / candidate).resolve(strict=False)
    except Exception:
        return None, "malformed_pointer"
    return resolved, ""


def _evidence_path_kind(
    resolved: Path,
    *,
    repo_root: Path,
    active_run_dirs: Sequence[Path],
) -> str:
    _runs_root, _experience_root, pr_queue_root = _experience_managed_roots(repo_root)
    if _path_is_within(resolved, pr_queue_root):
        return "pending_pr_queue"
    for active_run_dir in active_run_dirs:
        if resolved == active_run_dir or _path_is_within(resolved, active_run_dir):
            return "active_run_dir" if resolved == active_run_dir else "active_run_artifact"
    if any(_path_is_within(resolved, root) for root in _experience_managed_roots(repo_root)):
        return "managed"
    return "unmanaged"


def _safe_delete_artifact(path: Path, *, repo_root: Path) -> str:
    managed_roots = _experience_managed_roots(repo_root)
    if not any(path == root or _path_is_within(path, root) for root in managed_roots):
        return "unmanaged_path"
    if any(path == root for root in managed_roots):
        return "managed_root"
    if not path.exists():
        return "missing_path"
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    except FileNotFoundError:
        return "missing_path"
    except Exception:
        return "delete_failed"
    return "deleted"


def _make_pointer_entry(
    *,
    lesson_id: str,
    pointer: str,
    resolved: Path | None,
    reason: str,
) -> dict[str, Any]:
    entry = {
        "lesson_id": lesson_id,
        "pointer": pointer,
        "reason": reason,
    }
    if resolved is not None:
        entry["path"] = resolved.as_posix()
    return entry


def _evaluate_evidence_pointer(
    *,
    pointer: str,
    lesson_pruned: bool,
    lesson_requires_review: bool,
    repo_root: Path,
    cutoff: datetime,
    active_run_dirs: Sequence[Path],
    cfg: ExperienceRetentionConfig,
) -> dict[str, Any]:
    resolved, error_reason = _resolve_evidence_path(pointer, repo_root)
    if error_reason:
        return {
            "keep_pointer": False,
            "keep_artifact": False,
            "delete_candidate": False,
            "reason": error_reason,
            "resolved": None,
        }
    assert resolved is not None
    kind = _evidence_path_kind(
        resolved,
        repo_root=repo_root,
        active_run_dirs=active_run_dirs,
    )
    if kind == "pending_pr_queue" and cfg.preserve_pending_pr_queue:
        return {
            "keep_pointer": not lesson_pruned,
            "keep_artifact": True,
            "delete_candidate": False,
            "reason": "pending_pr_queue",
            "resolved": resolved,
        }
    if kind in {"active_run_artifact", "active_run_dir"} and cfg.preserve_active_run_artifacts:
        return {
            "keep_pointer": not lesson_pruned,
            "keep_artifact": True,
            "delete_candidate": False,
            "reason": kind,
            "resolved": resolved,
        }
    if lesson_requires_review and cfg.preserve_review_required_evidence:
        return {
            "keep_pointer": True,
            "keep_artifact": True,
            "delete_candidate": False,
            "reason": "review_required",
            "resolved": resolved,
        }
    if not resolved.exists():
        return {
            "keep_pointer": False,
            "keep_artifact": False,
            "delete_candidate": False,
            "reason": "missing_pointer",
            "resolved": resolved,
        }
    if kind != "managed":
        return {
            "keep_pointer": not lesson_pruned,
            "keep_artifact": False,
            "delete_candidate": False,
            "reason": "unmanaged_pointer",
            "resolved": resolved,
        }
    if lesson_pruned:
        return {
            "keep_pointer": False,
            "keep_artifact": False,
            "delete_candidate": True,
            "reason": "stale_lesson_evidence",
            "resolved": resolved,
        }
    try:
        modified_at = datetime.fromtimestamp(resolved.stat().st_mtime, tz=timezone.utc)
    except Exception:
        modified_at = None
    if modified_at is not None and modified_at < cutoff:
        return {
            "keep_pointer": False,
            "keep_artifact": False,
            "delete_candidate": True,
            "reason": "stale_evidence_pointer",
            "resolved": resolved,
        }
    return {
        "keep_pointer": True,
        "keep_artifact": False,
        "delete_candidate": False,
        "reason": "current_pointer",
        "resolved": resolved,
    }


def prune_experience_payload(
    payload: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    repo: Path,
    args: Any = None,
    cfg: ExperienceRetentionConfig | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
    active_run_dirs: Sequence[Path | str] | None = None,
) -> ExperienceRetentionResult:
    repo_root = Path(repo).expanduser().resolve()
    retention_cfg = cfg or experience_retention_config_from_args(args)
    current_time = _utc_now(now)
    cutoff = current_time - timedelta(days=max(0, retention_cfg.retention_days))
    container, lesson_key, lessons = _extract_lessons_container(payload)
    result = ExperienceRetentionResult(
        dry_run=bool(dry_run),
        retention_days=retention_cfg.retention_days,
        cutoff_iso=cutoff.isoformat(),
        lessons_before=len(lessons),
    )
    if not retention_cfg.enabled:
        result.lessons_after = len(lessons)
        result.updated_payload = container
        return result

    normalized_active_run_dirs = _normalize_active_run_dirs(active_run_dirs)
    updated_lessons: list[dict[str, Any]] = []
    delete_candidates: dict[str, Path] = {}
    preserved_paths: set[str] = set()

    for lesson in lessons:
        lesson_id = _record_identifier(lesson)
        lesson_time, timestamp_field = _record_timestamp(lesson)
        lesson_requires_review = _record_requires_review(lesson)
        lesson_stale = lesson_time is not None and lesson_time < cutoff
        lesson_pruned = lesson_stale and not lesson_requires_review
        lesson_reason = "stale_lesson"
        if lesson_pruned:
            result.pruned_lessons.append(
                {
                    "lesson_id": lesson_id,
                    "reason": lesson_reason,
                    "timestamp_field": timestamp_field,
                    "timestamp": lesson_time.isoformat() if lesson_time is not None else "",
                }
            )
        else:
            result.preserved_lessons.append(
                {
                    "lesson_id": lesson_id,
                    "reason": "review_required" if lesson_requires_review and lesson_stale else "within_retention",
                    "timestamp_field": timestamp_field,
                    "timestamp": lesson_time.isoformat() if lesson_time is not None else "",
                }
            )

        kept_evidence: list[str] = []
        for pointer in _record_evidence_values(lesson):
            decision = _evaluate_evidence_pointer(
                pointer=pointer,
                lesson_pruned=lesson_pruned,
                lesson_requires_review=lesson_requires_review,
                repo_root=repo_root,
                cutoff=cutoff,
                active_run_dirs=normalized_active_run_dirs,
                cfg=retention_cfg,
            )
            resolved = decision.get("resolved")
            if isinstance(resolved, Path) and decision.get("keep_artifact"):
                preserved_paths.add(resolved.as_posix())
            if decision.get("keep_pointer"):
                kept_evidence.append(pointer)
            elif decision.get("reason") == "missing_pointer":
                entry = _make_pointer_entry(lesson_id=lesson_id, pointer=pointer, resolved=resolved if isinstance(resolved, Path) else None, reason="missing_pointer")
                result.pruned_evidence.append(entry)
                if isinstance(resolved, Path):
                    result.missing_paths.append(resolved.as_posix())
            elif decision.get("reason") == "malformed_pointer":
                result.pruned_evidence.append(
                    _make_pointer_entry(lesson_id=lesson_id, pointer=pointer, resolved=None, reason="malformed_pointer")
                )
            elif decision.get("reason") == "unmanaged_pointer":
                if lesson_pruned:
                    result.skipped_evidence.append(
                        _make_pointer_entry(lesson_id=lesson_id, pointer=pointer, resolved=resolved if isinstance(resolved, Path) else None, reason="unmanaged_pointer")
                    )
            else:
                result.pruned_evidence.append(
                    _make_pointer_entry(
                        lesson_id=lesson_id,
                        pointer=pointer,
                        resolved=resolved if isinstance(resolved, Path) else None,
                        reason=str(decision.get("reason") or "pruned_pointer"),
                    )
                )
                if decision.get("delete_candidate") and isinstance(resolved, Path):
                    delete_candidates[resolved.as_posix()] = resolved

            if decision.get("reason") in {"pending_pr_queue", "active_run_artifact", "active_run_dir", "review_required"}:
                result.preserved_evidence.append(
                    _make_pointer_entry(
                        lesson_id=lesson_id,
                        pointer=pointer,
                        resolved=resolved if isinstance(resolved, Path) else None,
                        reason=str(decision.get("reason") or "preserved"),
                    )
                )

        if not lesson_pruned:
            updated_lesson = dict(lesson)
            updated_lesson[_record_evidence_key(updated_lesson)] = kept_evidence
            updated_lessons.append(updated_lesson)

    for path_text in list(delete_candidates):
        if path_text in preserved_paths:
            delete_candidates.pop(path_text, None)

    if not dry_run and retention_cfg.delete_artifacts:
        for path_text, path in sorted(delete_candidates.items()):
            delete_status = _safe_delete_artifact(path, repo_root=repo_root)
            if delete_status == "deleted":
                result.deleted_paths.append(path_text)
            elif delete_status == "missing_path":
                result.missing_paths.append(path_text)
            else:
                result.skipped_evidence.append(
                    {
                        "lesson_id": "",
                        "pointer": path_text,
                        "path": path_text,
                        "reason": delete_status,
                    }
                )

    if isinstance(container, list):
        result.updated_payload = updated_lessons
    else:
        assert isinstance(container, dict)
        container[lesson_key or "lessons"] = updated_lessons
        result.updated_payload = container
    result.lessons_after = len(updated_lessons)
    result.deleted_paths = sorted(set(result.deleted_paths))
    result.missing_paths = sorted(set(result.missing_paths))
    return result


def prune_experience_json(
    path: Path,
    *,
    repo: Path,
    args: Any = None,
    cfg: ExperienceRetentionConfig | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
    active_run_dirs: Sequence[Path | str] | None = None,
) -> ExperienceRetentionResult:
    raw = path.read_text(encoding="utf-8", errors="replace").strip() if path.exists() else ""
    payload = json.loads(raw) if raw else {"lessons": []}
    result = prune_experience_payload(
        payload,
        repo=repo,
        args=args,
        cfg=cfg,
        dry_run=dry_run,
        now=now,
        active_run_dirs=active_run_dirs,
    )
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result.updated_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def prune_experience_database(
    path: Path,
    *,
    repo: Path,
    args: Any = None,
    cfg: ExperienceRetentionConfig | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
    active_run_dirs: Sequence[Path | str] | None = None,
) -> ExperienceRetentionResult:
    if not path.exists():
        return ExperienceRetentionResult(
            dry_run=bool(dry_run),
            retention_days=(cfg or experience_retention_config_from_args(args)).retention_days,
            cutoff_iso=(_utc_now(now) - timedelta(days=max(0, (cfg or experience_retention_config_from_args(args)).retention_days))).isoformat(),
            lessons_before=0,
            lessons_after=0,
            updated_payload={"lessons": []},
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        lesson_columns = [
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(lessons)").fetchall()
            if str(row["name"] or "")
        ]
        if not lesson_columns:
            return ExperienceRetentionResult(
                dry_run=bool(dry_run),
                retention_days=(cfg or experience_retention_config_from_args(args)).retention_days,
                cutoff_iso=(_utc_now(now) - timedelta(days=max(0, (cfg or experience_retention_config_from_args(args)).retention_days))).isoformat(),
                lessons_before=0,
                lessons_after=0,
                updated_payload={"lessons": []},
            )
        select_columns = ", ".join(f'"{column}"' for column in lesson_columns)
        rows = [
            dict(row)
            for row in conn.execute(f'SELECT rowid AS "__rowid__", {select_columns} FROM lessons').fetchall()
        ]
        for row in rows:
            evidence_key = _record_evidence_key(row)
            row[evidence_key] = _decode_json_sequence(row.get(evidence_key))
        result = prune_experience_payload(
            {"lessons": rows},
            repo=repo,
            args=args,
            cfg=cfg,
            dry_run=dry_run,
            now=now,
            active_run_dirs=active_run_dirs,
        )
        if not dry_run:
            updated_lessons = result.updated_payload.get("lessons") if isinstance(result.updated_payload, dict) else []
            updated_by_rowid = {
                int(item["__rowid__"]): item
                for item in updated_lessons
                if isinstance(item, Mapping) and str(item.get("__rowid__") or "").isdigit()
            }
            retained_rowids = set(updated_by_rowid)
            original_rowids = {
                int(item["__rowid__"])
                for item in rows
                if isinstance(item, Mapping) and str(item.get("__rowid__") or "").isdigit()
            }
            for rowid in sorted(original_rowids - retained_rowids):
                conn.execute("DELETE FROM lessons WHERE rowid = ?", (rowid,))
            if "evidence" in lesson_columns:
                for rowid, item in updated_by_rowid.items():
                    conn.execute(
                        'UPDATE lessons SET "evidence" = ? WHERE rowid = ?',
                        (json.dumps(_record_evidence_values(item), ensure_ascii=False), rowid),
                    )
            conn.commit()
        return result
    finally:
        conn.close()


def prune_experience_retention(
    target: Path | str | Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    repo: Path,
    args: Any = None,
    cfg: ExperienceRetentionConfig | None = None,
    dry_run: bool = True,
    now: datetime | None = None,
    active_run_dirs: Sequence[Path | str] | None = None,
) -> ExperienceRetentionResult:
    if isinstance(target, (str, Path)):
        path = Path(target)
        if path.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            return prune_experience_database(
                path,
                repo=repo,
                args=args,
                cfg=cfg,
                dry_run=dry_run,
                now=now,
                active_run_dirs=active_run_dirs,
            )
        return prune_experience_json(
            path,
            repo=repo,
            args=args,
            cfg=cfg,
            dry_run=dry_run,
            now=now,
            active_run_dirs=active_run_dirs,
        )
    return prune_experience_payload(
        target,
        repo=repo,
        args=args,
        cfg=cfg,
        dry_run=dry_run,
        now=now,
        active_run_dirs=active_run_dirs,
    )



def _looks_like_raw_artifact(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    if "```" in text:
        return True
    if any(token in lowered for token in _RAW_ARTIFACT_TOKENS):
        return True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("diff --git", "@@", "+++", "---", "index ", "$ ", "> ")):
            return True
    return False


def _sanitize_lesson_text(text: Any, max_chars: int) -> str:
    normalized = _normalize_whitespace(str(text or ""))
    if not normalized or _looks_like_raw_artifact(normalized):
        return ""
    if max_chars > 0 and len(normalized) > max_chars:
        normalized = normalized[: max_chars - 3].rstrip() + "..."
    return normalized


def _coerce_evidence_count(value: Any) -> int:
    if isinstance(value, list):
        return len([item for item in value if str(item).strip()])
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except Exception:
        return None
    return confidence if confidence >= 0 else None


def _unique_strings(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for value in values:
        text = _normalize_whitespace(str(value or ""))
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        items.append(text)
    return items


def _normalize_path_text(value: Any) -> str:
    text = _normalize_whitespace(str(value or ""))
    if not text:
        return ""
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _sanitize_evidence_pointer(value: Any, *, redact_paths: bool = True) -> str:
    text = _normalize_path_text(value)
    if not text:
        return ""
    if not redact_paths:
        return text
    if re.match(r"^[a-zA-Z]:/", text) or text.startswith("//"):
        parts = [part for part in PurePosixPath(text).parts if part not in {"/", ""}]
        if len(parts) >= 2:
            return ".../" + "/".join(parts[-2:])
        if parts:
            return ".../" + parts[-1]
    return text


def _sanitize_evidence_list(
    values: Iterable[Any],
    *,
    redact_paths: bool = True,
) -> list[str]:
    return _unique_strings(
        _sanitize_evidence_pointer(item, redact_paths=redact_paths)
        for item in values
        if _sanitize_evidence_pointer(item, redact_paths=redact_paths)
    )


def _derive_file_globs(paths: Sequence[str]) -> list[str]:
    globs: list[str] = []
    for path in paths:
        normalized = _normalize_path_text(path)
        if not normalized:
            continue
        parts = PurePosixPath(normalized).parts
        root = parts[0] if parts else ""
        suffix = PurePosixPath(normalized).suffix.lower()
        if root:
            globs.append(f"{root}/**/*")
            if suffix:
                globs.append(f"{root}/**/*{suffix}")
        elif suffix:
            globs.append(f"**/*{suffix}")
    return _unique_strings(globs)


def _surface_keys(files: Sequence[str], globs: Sequence[str]) -> list[str]:
    keys: list[str] = []
    for value in list(files) + list(globs):
        normalized = _normalize_path_text(value)
        if not normalized:
            continue
        parts = PurePosixPath(normalized).parts
        root = parts[0].lower() if parts else ""
        if not root or root in _IGNORED_SURFACE_ROOTS:
            continue
        keys.append(root)
    return _unique_strings(keys)


def _is_web_surface(files: Sequence[str], globs: Sequence[str]) -> bool:
    for value in list(files) + list(globs):
        normalized = _normalize_path_text(value).lower()
        if not normalized:
            continue
        suffix = PurePosixPath(normalized).suffix.lower()
        if normalized.startswith("web_console/") or suffix in _WEB_FILE_SUFFIXES:
            return True
    return False


def _normalize_validation_statuses(records: Sequence[Mapping[str, Any]]) -> list[str]:
    statuses: list[str] = []
    for record in records:
        status = _normalize_whitespace(
            str(record.get("validation_status") or record.get("validationStatus") or record.get("status") or "")
        ).lower()
        if status:
            statuses.append(status)
    return _unique_strings(statuses)


def _normalize_gates(record: Mapping[str, Any]) -> list[str]:
    gates = _unique_strings(
        value
        for value in (
            *(record.get("gates") or [] if isinstance(record.get("gates"), list) else []),
            *(record.get("applies_to_gates") or [] if isinstance(record.get("applies_to_gates"), list) else []),
            *(record.get("appliesToGates") or [] if isinstance(record.get("appliesToGates"), list) else []),
        )
    )
    validation_records = record.get("validations") or record.get("validation_records") or record.get("validationRecords") or []
    if isinstance(validation_records, list):
        for item in validation_records:
            if not isinstance(item, Mapping):
                continue
            gate = _normalize_whitespace(str(item.get("gate") or item.get("kind") or item.get("name") or ""))
            if gate:
                gates.append(gate)
    return _unique_strings(gates)


def _normalize_blockers(record: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = (
        record.get("blocked_dependencies")
        or record.get("blockedDependencies")
        or record.get("blocking_dependencies")
        or record.get("blockingDependencies")
        or []
    )
    if not isinstance(raw, list):
        return ()
    blockers: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        blocker = {
            "task_id": _normalize_whitespace(str(item.get("task_id") or item.get("taskId") or "")),
            "title": _normalize_whitespace(str(item.get("title") or item.get("task_title") or item.get("taskTitle") or "")),
            "status": _normalize_whitespace(str(item.get("status") or item.get("task_status") or item.get("taskStatus") or "")).lower(),
            "reason": _normalize_whitespace(str(item.get("reason") or "")).lower(),
            "validation_summary": _normalize_whitespace(str(item.get("validation_summary") or item.get("validationSummary") or "")),
            "next_action": _normalize_whitespace(str(item.get("next_action") or item.get("nextAction") or "")),
        }
        if blocker["task_id"] or blocker["title"] or blocker["reason"]:
            blockers.append(blocker)
    return tuple(blockers)


def _compose_failure_signature(
    *,
    reason: str,
    task_status: str,
    validation_status: str,
    pr_decision: str,
    files: Sequence[str],
    globs: Sequence[str],
) -> str:
    parts = [
        reason.lower(),
        task_status.lower(),
        validation_status.lower(),
        pr_decision.lower() if pr_decision in _DISCARD_DECISIONS else "",
        ",".join(_surface_keys(files, globs)[:2]),
    ]
    return "|".join(part for part in parts if part)


def _normalize_record(raw: Any, *, redact_paths: bool = True) -> _ExperienceRecord | None:
    if not isinstance(raw, Mapping):
        return None
    changed_files = tuple(
        _unique_strings(
            _normalize_path_text(item)
            for item in (
                raw.get("changed_files")
                or raw.get("changedFiles")
                or raw.get("files")
                or []
            )
            if _normalize_path_text(item)
        )
    )
    explicit_globs = _unique_strings(
        item
        for item in (
            raw.get("file_globs")
            or raw.get("fileGlobs")
            or raw.get("applies_to_file_globs")
            or raw.get("appliesToFileGlobs")
            or []
        )
    )
    file_globs = tuple(_unique_strings([*explicit_globs, *_derive_file_globs(changed_files)]))
    goal_refs = tuple(
        _unique_strings(
            item
            for item in (
                raw.get("goal_refs")
                or raw.get("goalRefs")
                or raw.get("goal_trace")
                or raw.get("goalTrace")
                or []
            )
        )
    )
    validations = raw.get("validations") or raw.get("validation_records") or raw.get("validationRecords") or []
    validation_statuses = _normalize_validation_statuses(validations if isinstance(validations, list) else [])
    validation_status = _normalize_whitespace(
        str(raw.get("validation_status") or raw.get("validationStatus") or (validation_statuses[0] if validation_statuses else ""))
    ).lower()
    task_status = _normalize_whitespace(str(raw.get("task_status") or raw.get("taskStatus") or "")).lower()
    pr_decision = _normalize_whitespace(
        str(
            raw.get("pr_decision")
            or raw.get("prDecision")
            or raw.get("packet_status")
            or raw.get("packetStatus")
            or raw.get("approval_status")
            or raw.get("approvalStatus")
            or raw.get("merge_status")
            or raw.get("mergeStatus")
            or ""
        )
    ).lower()
    reason = _normalize_whitespace(str(raw.get("reason") or "")).lower()
    gates = tuple(_normalize_gates(raw))
    blocked_dependencies = _normalize_blockers(raw)
    evidence = tuple(
        _sanitize_evidence_list(
            raw.get("evidence")
            or raw.get("evidence_pointers")
            or raw.get("evidencePointers")
            or raw.get("artifact_links")
            or raw.get("artifactLinks")
            or [],
            redact_paths=redact_paths,
        )
    )
    failure_signature = _normalize_whitespace(
        str(raw.get("failure_signature") or raw.get("failureSignature") or raw.get("trigger") or "")
    ).lower()
    if not failure_signature:
        failure_signature = _compose_failure_signature(
            reason=reason,
            task_status=task_status,
            validation_status=validation_status,
            pr_decision=pr_decision,
            files=changed_files,
            globs=file_globs,
        )
    return _ExperienceRecord(
        task_id=_normalize_whitespace(str(raw.get("task_id") or raw.get("taskId") or "")),
        title=_normalize_whitespace(str(raw.get("title") or raw.get("task_title") or raw.get("taskTitle") or "")),
        goal_refs=goal_refs,
        changed_files=changed_files,
        file_globs=file_globs,
        gates=gates,
        task_status=task_status,
        validation_status=validation_status,
        pr_decision=pr_decision,
        reason=reason,
        failure_signature=failure_signature,
        evidence=evidence,
        blocked_dependencies=blocked_dependencies,
    )


def _build_candidate(
    raw: Any,
    kind_hint: str | None = None,
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
    source_priority: int = 0,
    ordinal: int = 0,
) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        lesson_text = (
            raw.get("lesson")
            or raw.get("text")
            or raw.get("summary")
            or raw.get("hint")
            or raw.get("title")
            or raw.get("message")
            or ""
        )
        kind = _normalize_whitespace(str(raw.get("kind") or kind_hint or "hint")).lower() or "hint"
        severity = _normalize_whitespace(str(raw.get("severity") or "")).lower()
        confidence = _coerce_confidence(raw.get("confidence"))
        evidence_count = _coerce_evidence_count(raw.get("evidence_count"))
        if evidence_count <= 0:
            evidence_count = _coerce_evidence_count(raw.get("evidence"))
        score = _coerce_confidence(raw.get("relevance_score"))
        if score is None:
            score = _coerce_confidence(raw.get("score"))
        if score is None:
            score = confidence if confidence is not None else 0.0
    else:
        lesson_text = raw
        kind = _normalize_whitespace(kind_hint or "hint").lower() or "hint"
        severity = ""
        confidence = None
        evidence_count = 0
        score = 0.0
    sanitized_text = _sanitize_lesson_text(lesson_text, max_chars=lesson_max_chars)
    if not sanitized_text:
        return None
    return {
        "kind": kind,
        "severity": severity,
        "confidence": confidence,
        "evidence_count": evidence_count,
        "score": score if score is not None else 0.0,
        "text": sanitized_text,
        "source_priority": source_priority,
        "ordinal": ordinal,
    }


def _collect_candidates_from_payload(
    payload: Mapping[str, Any],
    lesson_max_chars: int,
    source_priority: int,
    ordinal_start: int = 0,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ordinal = ordinal_start
    list_sections = (
        ("task_lessons", "task_sizing"),
        ("validation_lessons", "validation"),
        ("lessons", "lesson"),
        ("items", "lesson"),
        ("pm_hints", "pm_hint"),
        ("merge_hints", "merge_hint"),
        ("operator_actions", "operator_action"),
    )
    for key, kind_hint in list_sections:
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            candidate = _build_candidate(
                raw_item,
                kind_hint=kind_hint,
                lesson_max_chars=lesson_max_chars,
                source_priority=source_priority,
                ordinal=ordinal,
            )
            ordinal += 1
            if candidate is not None:
                candidates.append(candidate)
    if candidates:
        return candidates
    summary_text = payload.get("summary")
    candidate = _build_candidate(
        summary_text,
        kind_hint="summary",
        lesson_max_chars=lesson_max_chars,
        source_priority=source_priority,
        ordinal=ordinal,
    )
    if candidate is not None:
        candidates.append(candidate)
    return candidates


def _collect_candidates_from_markdown(
    items: Sequence[Any],
    lesson_max_chars: int,
    source_priority: int,
    ordinal_start: int = 0,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ordinal = ordinal_start
    for item in items:
        candidate = _build_candidate(
            item,
            kind_hint="lesson",
            lesson_max_chars=lesson_max_chars,
            source_priority=source_priority,
            ordinal=ordinal,
        )
        ordinal += 1
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _dedupe_candidates(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _normalize_whitespace(candidate.get("text", "")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(dict(candidate))
    return deduped


def _format_candidate_line(candidate: Mapping[str, Any], evidence_max_items: int = DEFAULT_EXPERIENCE_EVIDENCE_MAX_ITEMS) -> str:
    meta: list[str] = []
    kind = _normalize_whitespace(str(candidate.get("kind") or "")).lower()
    severity = _normalize_whitespace(str(candidate.get("severity") or "")).lower()
    confidence = candidate.get("confidence")
    evidence_count = int(candidate.get("evidence_count") or 0)
    if kind:
        meta.append(kind)
    if severity:
        meta.append(severity)
    if confidence is not None:
        meta.append(f"conf={confidence:.2f}")
    if evidence_count > 0:
        bounded_count = evidence_count if evidence_max_items <= 0 else min(evidence_count, evidence_max_items)
        meta.append(f"evidence={bounded_count}")
    text = str(candidate.get("text") or "").strip()
    if meta:
        return f"- [{' '.join(meta)}] {text}"
    return f"- {text}"


def _compose_block(lines: Sequence[str], cfg: ExperiencePromptConfig) -> str:
    opening = (
        f'<pm_experience_summary version="1" items="{len(lines)}" '
        f'max_items="{cfg.max_items}" max_chars="{cfg.max_chars}" authority="advisory">'
    )
    return "\n".join([opening, *lines, "</pm_experience_summary>"])


def render_experience_summary(
    payload: Mapping[str, Any] | None = None,
    *,
    task_lessons: Sequence[Mapping[str, Any]] | None = None,
    validation_lessons: Sequence[Mapping[str, Any]] | None = None,
    pm_hints: Sequence[str] | None = None,
    merge_hints: Sequence[str] | None = None,
    operator_actions: Sequence[str] | None = None,
    args: Any = None,
    max_items: int | None = None,
    max_chars: int | None = None,
    lesson_max_chars: int | None = None,
    evidence_max_items: int | None = None,
) -> str:
    cfg = experience_prompt_config_from_args(args)
    if max_items is not None or max_chars is not None or lesson_max_chars is not None or evidence_max_items is not None:
        cfg = ExperiencePromptConfig(
            enabled=cfg.enabled,
            max_items=cfg.max_items if max_items is None else max_items,
            max_chars=cfg.max_chars if max_chars is None else max_chars,
            lesson_max_chars=cfg.lesson_max_chars if lesson_max_chars is None else lesson_max_chars,
            evidence_max_items=cfg.evidence_max_items if evidence_max_items is None else evidence_max_items,
            redact_paths=cfg.redact_paths,
        )
    if not cfg.enabled or cfg.max_items <= 0 or cfg.max_chars <= 0 or cfg.lesson_max_chars <= 0:
        return ""
    summary_payload = dict(payload or {})
    if not summary_payload:
        summary_payload = {
            "task_lessons": list(task_lessons or []),
            "validation_lessons": list(validation_lessons or []),
            "pm_hints": list(pm_hints or []),
            "merge_hints": list(merge_hints or []),
            "operator_actions": list(operator_actions or []),
        }
    candidates = _dedupe_candidates(
        _collect_candidates_from_payload(summary_payload, cfg.lesson_max_chars, source_priority=1, ordinal_start=0)
    )
    if not candidates:
        return ""
    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("score") or 0.0),
            -int(item.get("source_priority") or 0),
            int(item.get("ordinal") or 0),
        ),
    )
    rendered_lines: list[str] = []
    for candidate in ranked:
        if cfg.max_items > 0 and len(rendered_lines) >= cfg.max_items:
            break
        line = _format_candidate_line(candidate, evidence_max_items=cfg.evidence_max_items)
        tentative = [*rendered_lines, line]
        block = _compose_block(tentative, cfg=cfg)
        if len(block) > cfg.max_chars:
            continue
        rendered_lines.append(line)
    if not rendered_lines:
        return ""
    return _compose_block(rendered_lines, cfg=cfg)


def load_pm_experience_summary(repo: Path, run_dir: Path, args: Any) -> str:
    cfg = experience_prompt_config_from_args(args)
    if not cfg.enabled or cfg.max_items <= 0 or cfg.max_chars <= 0 or cfg.lesson_max_chars <= 0:
        return ""
    experience_root = repo / AGENT_WORK_DIR / "experience"
    sources = [
        (2, _read_json_if_exists(run_dir / "ANALYZER_SUMMARY.json"), []),
        (1, _read_json_if_exists(experience_root / "latest_summary.json"), _read_markdown_bullets(experience_root / "latest_summary.md")),
    ]
    candidates: list[dict[str, Any]] = []
    ordinal = 0
    for source_priority, payload, markdown_items in sources:
        if payload:
            found = _collect_candidates_from_payload(
                payload,
                lesson_max_chars=cfg.lesson_max_chars,
                source_priority=source_priority,
                ordinal_start=ordinal,
            )
            if found:
                ordinal += len(found)
                candidates.extend(found)
                continue
        if markdown_items:
            found = _collect_candidates_from_markdown(
                markdown_items,
                lesson_max_chars=cfg.lesson_max_chars,
                source_priority=source_priority,
                ordinal_start=ordinal,
            )
            ordinal += len(found)
            candidates.extend(found)
    candidates = _dedupe_candidates(candidates)
    if not candidates:
        return ""
    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("score") or 0.0),
            -int(item.get("source_priority") or 0),
            int(item.get("ordinal") or 0),
        ),
    )
    rendered_lines: list[str] = []
    for candidate in ranked:
        if cfg.max_items > 0 and len(rendered_lines) >= cfg.max_items:
            break
        line = _format_candidate_line(candidate, evidence_max_items=cfg.evidence_max_items)
        tentative = [*rendered_lines, line]
        block = _compose_block(tentative, cfg=cfg)
        if len(block) > cfg.max_chars:
            continue
        rendered_lines.append(line)
    if not rendered_lines:
        return ""
    return _compose_block(rendered_lines, cfg=cfg)


def _is_oversized_record(record: _ExperienceRecord) -> bool:
    surfaces = _surface_keys(record.changed_files, record.file_globs)
    adverse = (
        record.task_status in _ADVERSE_TASK_STATUSES
        or record.validation_status in _ADVERSE_VALIDATION_STATUSES
        or record.pr_decision in _DISCARD_DECISIONS
    )
    return (
        adverse
        and (
            len(surfaces) >= 2
            or len(record.goal_refs) >= 2
            or len(record.changed_files) >= 5
            or (len(surfaces) >= 2 and len(record.gates) >= 2)
        )
    )


def _build_validation_gap_records(records: Sequence[_ExperienceRecord]) -> list[_ExperienceRecord]:
    return [
        record
        for record in records
        if _is_web_surface(record.changed_files, record.file_globs)
        and record.validation_status in _VALIDATION_GAP_STATUSES
    ]


def _severity_weight(severity: str) -> float:
    return {"low": 1.0, "medium": 2.0, "high": 3.0}.get(severity, 1.0)


def _compute_confidence(base: float, evidence_count: int, *, bonus: float = 0.0) -> float:
    value = base + min(0.24, max(0, evidence_count - 1) * 0.06) + bonus
    return round(min(0.95, max(0.05, value)), 2)


def _evidence_units(records: Sequence[_ExperienceRecord]) -> int:
    pointers = _unique_strings(item for record in records for item in record.evidence)
    units: set[str] = set()
    blocker_count = 0
    for record in records:
        key = record.task_id or record.failure_signature or record.reason or record.title
        if key:
            units.add(key)
        for blocker in record.blocked_dependencies:
            blocker_count += 1
            blocker_id = blocker.get("task_id") or blocker.get("title") or blocker.get("reason") or ""
            if blocker_id:
                units.add(f"blocker:{blocker_id}")
    return max(1, len(pointers), len(units) + blocker_count)


def _merge_metadata(records: Sequence[_ExperienceRecord]) -> dict[str, list[str]]:
    goal_refs = _unique_strings(item for record in records for item in record.goal_refs)
    file_globs = _unique_strings(item for record in records for item in record.file_globs)
    gates = _unique_strings(item for record in records for item in record.gates)
    statuses = _unique_strings(record.task_status for record in records if record.task_status)
    validation_statuses = _unique_strings(record.validation_status for record in records if record.validation_status)
    pr_decisions = _unique_strings(record.pr_decision for record in records if record.pr_decision)
    task_ids = _unique_strings(record.task_id for record in records if record.task_id)
    evidence = _sanitize_evidence_list(item for record in records for item in record.evidence)
    return {
        "goal_refs": goal_refs,
        "file_globs": file_globs,
        "gates": gates,
        "statuses": statuses,
        "validation_statuses": validation_statuses,
        "pr_decisions": pr_decisions,
        "task_ids": task_ids,
        "evidence": evidence,
    }


def _build_advisory_lesson(
    kind: str,
    lesson_text: str,
    severity: str,
    records: Sequence[_ExperienceRecord],
    *,
    trigger: str,
    confidence_base: float,
    confidence_bonus: float = 0.0,
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
) -> dict[str, Any]:
    metadata = _merge_metadata(records)
    evidence_count = _evidence_units(records)
    confidence = _compute_confidence(confidence_base, evidence_count, bonus=confidence_bonus)
    score = round(_severity_weight(severity) + confidence * 2 + min(4, evidence_count) * 0.5, 2)
    lesson = _sanitize_lesson_text(lesson_text, lesson_max_chars)
    primary_task_id = metadata["task_ids"][0] if metadata["task_ids"] else ""
    payload = {
        "task_id": primary_task_id,
        "taskId": primary_task_id,
        "kind": kind,
        "recommendation_family": kind,
        "recommendationFamily": kind,
        "severity": severity,
        "confidence": confidence,
        "score": score,
        "lesson": lesson,
        "summary": lesson,
        "evidence": metadata["evidence"],
        "evidence_count": evidence_count,
        "evidenceCount": evidence_count,
        "authority": _ADVISORY_AUTHORITY,
        "authorityLevel": _ADVISORY_AUTHORITY,
        "source": "deterministic_experience",
        "trigger": trigger,
        "applies_to_goal_refs": metadata["goal_refs"],
        "appliesToGoalRefs": metadata["goal_refs"],
        "applies_to_file_globs": metadata["file_globs"],
        "appliesToFileGlobs": metadata["file_globs"],
        "applies_to_gates": metadata["gates"],
        "appliesToGates": metadata["gates"],
        "applies_to_statuses": metadata["statuses"],
        "appliesToStatuses": metadata["statuses"],
        "applies_to_validation_statuses": metadata["validation_statuses"],
        "appliesToValidationStatuses": metadata["validation_statuses"],
        "pr_decisions": metadata["pr_decisions"],
        "prDecisions": metadata["pr_decisions"],
        "task_ids": metadata["task_ids"],
        "taskIds": metadata["task_ids"],
    }
    return payload


def recommend_experience_lessons(
    experience_records: Sequence[Mapping[str, Any]] | None,
    *,
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
    redact_paths: bool = True,
) -> list[dict[str, Any]]:
    normalized = [
        record
        for record in (
            _normalize_record(raw, redact_paths=redact_paths)
            for raw in list(experience_records or [])
        )
        if record is not None
    ]
    lessons: list[dict[str, Any]] = []

    oversized = [record for record in normalized if _is_oversized_record(record)]
    if oversized:
        surfaces = _unique_strings(item for record in oversized for item in _surface_keys(record.changed_files, record.file_globs))
        surface_text = ", ".join(surfaces[:2]) if surfaces else "the recorded surfaces"
        severity = "high" if any(record.pr_decision in _DISCARD_DECISIONS for record in oversized) or len(surfaces) >= 3 else "medium"
        lessons.append(
            _build_advisory_lesson(
                "task_sizing",
                f"Consider splitting multi-surface work around {surface_text} into smaller tasks before retrying.",
                severity,
                oversized,
                trigger="oversized_multi_surface",
                confidence_base=0.64,
                confidence_bonus=0.08 if len(surfaces) >= 3 else 0.0,
                lesson_max_chars=lesson_max_chars,
            )
        )

    validation_gap_records = _build_validation_gap_records(normalized)
    if validation_gap_records:
        recorded_gates = _unique_strings(item for record in validation_gap_records for item in record.gates)
        gate_hint = ", ".join(recorded_gates[:2])
        status_hint = "/".join(
            _unique_strings(record.validation_status for record in validation_gap_records if record.validation_status)[:2]
        )
        lesson_text = (
            f"When web-surface files change, select recorded validation gates such as {gate_hint} "
            f"instead of leaving validation at {status_hint}."
            if gate_hint
            else "When web-surface files change, select matching validation early instead of leaving tests skipped or missing."
        )
        lessons.append(
            _build_advisory_lesson(
                "validation_selection",
                lesson_text,
                "medium",
                validation_gap_records,
                trigger="web_validation_gap",
                confidence_base=0.66,
                confidence_bonus=0.06 if any(record.pr_decision in _DISCARD_DECISIONS for record in validation_gap_records) else 0.0,
                lesson_max_chars=lesson_max_chars,
            )
        )

    retry_groups: dict[str, list[_ExperienceRecord]] = {}
    for record in normalized:
        if record.failure_signature:
            retry_groups.setdefault(record.failure_signature, []).append(record)
    retry_candidates: list[tuple[str, list[_ExperienceRecord], int]] = []
    for signature, group in retry_groups.items():
        discard_count = len([record for record in group if record.pr_decision in _DISCARD_DECISIONS])
        if len(group) >= 2 or discard_count > 0:
            retry_candidates.append((signature, group, discard_count))
    if retry_candidates:
        retry_candidates.sort(
            key=lambda item: (
                -(len(item[1]) + item[2]),
                -item[2],
                item[0],
            )
        )
        signature, retry_records, discard_count = retry_candidates[0]
        severity = "high" if len(retry_records) >= 3 or discard_count > 0 else "medium"
        lessons.append(
            _build_advisory_lesson(
                "retry_avoidance",
                "Avoid another same-signature retry; change approach or split the task before rerunning the recorded failure shape.",
                severity,
                retry_records,
                trigger=f"retry_signature:{signature}",
                confidence_base=0.7,
                confidence_bonus=0.1 if discard_count > 0 else 0.0,
                lesson_max_chars=lesson_max_chars,
            )
        )

    dependency_records = [
        record
        for record in normalized
        if record.reason == "dependency_failed" or bool(record.blocked_dependencies)
    ]
    if dependency_records:
        blocker_ids = _unique_strings(
            blocker.get("task_id") or blocker.get("title") or ""
            for record in dependency_records
            for blocker in record.blocked_dependencies
        )
        blocker_hint = blocker_ids[0] if blocker_ids else "the recorded blockers"
        severity = "high" if sum(len(record.blocked_dependencies) for record in dependency_records) >= 2 else "medium"
        lessons.append(
            _build_advisory_lesson(
                "dependency_cleanup",
                f"Clean up unresolved dependency blockers such as {blocker_hint} before retrying dependent work.",
                severity,
                dependency_records,
                trigger="dependency_blockers",
                confidence_base=0.68,
                confidence_bonus=0.04 if blocker_ids else 0.0,
                lesson_max_chars=lesson_max_chars,
            )
        )

    lessons.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("kind") or ""),
            str(item.get("lesson") or ""),
        )
    )
    for index, lesson in enumerate(lessons, start=1):
        lesson["rank"] = index
    return lessons


def classify_experience_lessons(
    experience_records: Sequence[Mapping[str, Any]] | None,
    *,
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
    redact_paths: bool = True,
) -> dict[str, Any]:
    lessons = recommend_experience_lessons(
        experience_records,
        lesson_max_chars=lesson_max_chars,
        redact_paths=redact_paths,
    )
    task_lessons = [lesson for lesson in lessons if lesson.get("kind") != "validation_selection"]
    validation_lessons = [lesson for lesson in lessons if lesson.get("kind") == "validation_selection"]
    pm_hints = [str(lesson.get("lesson") or "").strip() for lesson in lessons[:4] if str(lesson.get("lesson") or "").strip()]
    return {
        "task_lessons": task_lessons,
        "taskLessons": task_lessons,
        "validation_lessons": validation_lessons,
        "validationLessons": validation_lessons,
        "lessons": lessons,
        "pm_hints": pm_hints,
        "pmHints": pm_hints,
        "merge_hints": [],
        "mergeHints": [],
        "operator_actions": [],
        "operatorActions": [],
    }


def build_experience_lessons(
    experience_records: Sequence[Mapping[str, Any]] | None,
    *,
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
    redact_paths: bool = True,
) -> dict[str, Any]:
    return classify_experience_lessons(
        experience_records,
        lesson_max_chars=lesson_max_chars,
        redact_paths=redact_paths,
    )


def derive_experience_lessons(
    experience_records: Sequence[Mapping[str, Any]] | None,
    *,
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
    redact_paths: bool = True,
) -> dict[str, Any]:
    return classify_experience_lessons(
        experience_records,
        lesson_max_chars=lesson_max_chars,
        redact_paths=redact_paths,
    )


extract_experience_lessons = recommend_experience_lessons
prune_experience_store = prune_experience_retention
apply_experience_retention = prune_experience_retention
prune_experience_lessons = prune_experience_payload
