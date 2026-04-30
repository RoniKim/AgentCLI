from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Sequence

from .gates import (
    classify_pr_queue_validation_status,
    repo_has_web_worktree_markers,
    run_build_validation_async,
    run_fast_web_worktree_regression_async,
    run_test_validation_async,
)
from .gitops import (
    allocate_temporary_worktree_dir,
    create_worktree,
    find_pending_worktree_merge,
    git_changed_files,
    git_head,
    git_repo_state,
    read_pending_worktree_merge,
    remove_worktree,
)
from .task_status import classify_task_failure
from .utils import atomic_write_json, now_iso, run_cmd, safe_write_text


PR_QUEUE_DIRNAME = "pr_queue"
PR_QUEUE_SCHEMA_VERSION = 1
PR_QUEUE_INDEX_FILENAME = "branch_index.json"


def pr_queue_root(source_repo: Path) -> Path:
    return Path(source_repo).expanduser().resolve() / ".AgentCLI" / PR_QUEUE_DIRNAME


def pr_packet_path(source_repo: Path, packet_id: str) -> Path:
    return pr_queue_root(source_repo) / f"{packet_id}.json"


def pr_branch_index_path(source_repo: Path) -> Path:
    return pr_queue_root(source_repo) / PR_QUEUE_INDEX_FILENAME


def _slug(value: object, *, max_len: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    if max_len > 0 and len(text) > max_len:
        text = text[:max_len].rstrip("-._")
    return text or "item"


def _normalize_str_list(value: Sequence[object] | object | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        try:
            items = list(value)  # type: ignore[arg-type]
        except TypeError:
            items = [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _normalize_task_ids(task_ids: Sequence[object] | object | None) -> list[str]:
    return _normalize_str_list(task_ids)


def _stable_packet_id(
    source_repo: Path,
    run_id: str,
    task_ids: Sequence[str],
    base_ref: str,
    head_ref: str,
    branch: str,
) -> str:
    seed = json.dumps(
        {
            "source_repo": Path(source_repo).expanduser().resolve().as_posix(),
            "run_id": str(run_id or ""),
            "task_ids": list(task_ids),
            "base_ref": str(base_ref or ""),
            "head_ref": str(head_ref or ""),
            "branch": str(branch or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(seed.encode("utf-8", errors="ignore")).hexdigest()[:12]
    run_fragment = _slug(run_id, max_len=24)
    task_fragment = _slug(task_ids[0] if task_ids else "run", max_len=24)
    branch_fragment = _slug(branch or head_ref or base_ref or "branch", max_len=24)
    return f"pr-{run_fragment}-{task_fragment}-{branch_fragment}-{digest}"


def _normalize_changed_files(
    source_repo: Path,
    base_ref: str,
    head_ref: str,
    changed_files: Sequence[object] | object | None,
) -> list[object]:
    if changed_files is not None:
        return _normalize_list_value(changed_files)
    if not base_ref or not head_ref or base_ref == head_ref:
        return []
    return list(git_changed_files(Path(source_repo), base_ref, head_ref))


def _normalize_commits(
    source_repo: Path,
    base_ref: str,
    head_ref: str,
    commits: Sequence[object] | object | None,
) -> list[object]:
    if commits is not None:
        return _normalize_list_value(commits)
    if not base_ref or not head_ref or base_ref == head_ref:
        return []
    code, out = run_cmd(
        [
            "git",
            "log",
            "--reverse",
            "--no-merges",
            "--max-count=20",
            "--date=iso-strict",
            "--format=%H%x1f%ad%x1f%s",
            f"{base_ref}..{head_ref}",
        ],
        cwd=Path(source_repo),
        timeout_sec=60,
    )
    if code != 0 or not out.strip():
        return []
    items: list[dict[str, object]] = []
    for line in out.splitlines():
        parts = line.split("\x1f")
        if len(parts) < 3:
            continue
        sha, committed_at, subject = parts[:3]
        items.append(
            {
                "sha": sha[:12],
                "full_sha": sha,
                "committed_at": committed_at,
                "subject": subject,
            }
        )
    return items


def _normalize_list_value(value: Sequence[object] | object | None) -> list[object]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value]
    try:
        return list(value)  # type: ignore[arg-type]
    except TypeError:
        return [value]


def _load_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        if isinstance(data, dict):
            return dict(data)
    except Exception:
        pass
    return {}


def _resolve_run_dir(source_repo: Path, run_id: str) -> Path | None:
    run_id_text = str(run_id or "").strip()
    if not run_id_text:
        return None
    return Path(source_repo).expanduser().resolve() / ".AgentCLI" / "agent_runs" / run_id_text


def _is_validation_artifact_path(path_text: str) -> bool:
    name = Path(str(path_text or "")).name.lower()
    if not name:
        return False
    if name.endswith(".patch"):
        return False
    if name.startswith("worktree_merge_pending"):
        return False
    if name.startswith("worktree_merge_applied"):
        return False
    if name.startswith("worktree_merge_discarded"):
        return False
    if name.startswith("worktree_apply_failure"):
        return False
    return True


def _normalize_packet_validation_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if not status:
        return ""
    aliases = {
        "passed": "validation_passed",
        "pass": "validation_passed",
        "success": "validation_passed",
        "completed": "validation_passed",
        "ok": "validation_passed",
        "validation_passed": "validation_passed",
        "validation_pending": "validation_pending",
        "validation_running": "validation_pending",
        "running": "validation_pending",
        "tests_skipped": "tests_skipped",
        "skipped": "tests_skipped",
        "no_tests_found": "no_tests_found",
        "failed": "validation_failed",
        "validation_failed": "validation_failed",
        "blocked_env": "blocked_env",
        "stopped": "validation_pending",
    }
    return aliases.get(status, status)


def _choose_packet_validation_status(explicit_status: object, artifact_statuses: Sequence[object]) -> str:
    explicit = _normalize_packet_validation_status(explicit_status)
    derived = [_normalize_packet_validation_status(status) for status in artifact_statuses if _normalize_packet_validation_status(status)]
    if derived:
        priority = {
            "validation_failed": 0,
            "blocked_env": 1,
            "no_tests_found": 2,
            "tests_skipped": 3,
            "validation_pending": 4,
            "validation_passed": 5,
        }
        return min(derived, key=lambda status: priority.get(status, 99))
    return explicit or "validation_pending"


def _load_task_validation_artifacts(run_dir: Path | None, task_ids: Sequence[str]) -> list[tuple[Path, dict[str, Any]]]:
    if run_dir is None:
        return []
    tasks_root = Path(run_dir) / "tasks"
    if not tasks_root.exists() or not tasks_root.is_dir():
        return []
    candidate_task_dirs: list[Path] = []
    normalized_task_ids = _normalize_task_ids(task_ids)
    if normalized_task_ids:
        for task_id in normalized_task_ids:
            task_dir = tasks_root / task_id
            if task_dir.exists() and task_dir.is_dir():
                candidate_task_dirs.append(task_dir)
    else:
        candidate_task_dirs = [path for path in tasks_root.iterdir() if path.is_dir()]
    artifacts: list[tuple[Path, dict[str, Any]]] = []
    for task_dir in candidate_task_dirs:
        validation_files = [path for path in task_dir.glob("attempt_*/validation.json") if path.is_file()]
        if not validation_files:
            continue
        validation_files.sort(key=lambda path: (path.stat().st_mtime, path.as_posix()))
        latest = validation_files[-1]
        raw = _load_json_dict(latest)
        if raw:
            artifacts.append((latest, raw))
    return artifacts


def _task_validation_notes(raw: dict[str, Any]) -> list[str]:
    notes: list[str] = []
    for value in (
        raw.get("qa_notes"),
        raw.get("qaNotes"),
        raw.get("summary"),
        raw.get("detail"),
        raw.get("failure_summary"),
        raw.get("failureSummary"),
    ):
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            candidates = _normalize_str_list(value)
        else:
            candidates = [str(value).strip()]
        for text in candidates:
            if text and text not in notes:
                notes.append(text)
    return notes


def _task_validation_goal_trace(raw: dict[str, Any]) -> list[object]:
    goal_trace = raw.get("goal_trace")
    if goal_trace is None:
        goal_trace = raw.get("goalTrace")
    return _normalize_list_value(goal_trace)


def load_branch_index(source_repo: Path) -> dict[str, object]:
    path = pr_branch_index_path(source_repo)
    default = {
        "schema_version": PR_QUEUE_SCHEMA_VERSION,
        "updated_at": "",
        "entries": [],
    }
    if not path.exists():
        return default
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return default
        data = json.loads(raw)
        if not isinstance(data, dict):
            return default
    except Exception:
        return default
    entries_raw = data.get("entries") if isinstance(data.get("entries"), list) else []
    entries = [dict(item) for item in entries_raw if isinstance(item, dict)]
    return {
        "schema_version": int(data.get("schema_version") or PR_QUEUE_SCHEMA_VERSION),
        "updated_at": str(data.get("updated_at") or ""),
        "entries": entries,
    }


def _write_branch_index(source_repo: Path, index: dict[str, object]) -> None:
    atomic_write_json(pr_branch_index_path(source_repo), index)


def _upsert_index_entry(index: dict[str, object], entry: dict[str, object]) -> dict[str, object]:
    entries = [dict(item) for item in index.get("entries", []) if isinstance(item, dict)]
    entry_id = str(entry.get("id") or "")
    updated = False
    for idx, existing in enumerate(entries):
        if str(existing.get("id") or "") == entry_id:
            merged = dict(existing)
            merged.update(entry)
            merged["created_at"] = str(existing.get("created_at") or merged.get("created_at") or "")
            entries[idx] = merged
            updated = True
            break
    if not updated:
        entries.append(dict(entry))
    return {
        "schema_version": PR_QUEUE_SCHEMA_VERSION,
        "updated_at": now_iso(),
        "entries": entries,
    }


def queue_review_packet(
    source_repo: Path,
    *,
    run_id: str,
    task_ids: Sequence[object] | object | None = None,
    base_ref: str = "",
    head_ref: str = "",
    branch: str = "",
    created_at: str | None = None,
    updated_at: str | None = None,
    packet_id: str | None = None,
    source_head_before: str = "",
    source_head_after: str = "",
    worktree_dir: str = "",
    validation_status: str = "validation_pending",
    validation_artifacts: Sequence[object] | object | None = None,
    qa_notes: Sequence[object] | object | None = None,
    goal_trace: Sequence[object] | object | None = None,
    merge_preflight: dict[str, object] | None = None,
    changed_files: Sequence[object] | object | None = None,
    commits: Sequence[object] | object | None = None,
    status: str = "pr_queued",
    recoverable_reason: str = "",
) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    queue_root = pr_queue_root(source_repo_path)
    queue_root.mkdir(parents=True, exist_ok=True)

    normalized_task_ids = _normalize_task_ids(task_ids)
    base_ref_text = str(base_ref or "").strip()
    head_ref_text = str(head_ref or "").strip()
    branch_text = str(branch or "").strip()
    run_id_text = str(run_id or "").strip()
    now = now_iso()
    created_at_text = str(created_at or now).strip() or now
    updated_at_text = str(updated_at or now).strip() or now
    packet_status = str(status or "pr_queued").strip() or "pr_queued"

    run_dir = _resolve_run_dir(source_repo_path, run_id_text)
    pending_payload: dict[str, Any] = {}
    if run_dir is not None:
        pending_path = find_pending_worktree_merge(source_repo_path, run_dir)
        if pending_path is not None:
            try:
                pending_payload = read_pending_worktree_merge(pending_path)
            except Exception:
                pending_payload = {}
            if not isinstance(pending_payload, dict):
                pending_payload = {}

    if not base_ref_text:
        base_ref_text = str(
            pending_payload.get("base_ref")
            or pending_payload.get("baseRef")
            or pending_payload.get("expected_base_ref")
            or pending_payload.get("expectedBaseRef")
            or ""
        ).strip()
    if not head_ref_text:
        head_ref_text = str(
            pending_payload.get("head_ref")
            or pending_payload.get("headRef")
            or pending_payload.get("source_head")
            or pending_payload.get("sourceHead")
            or ""
        ).strip()
    if not branch_text:
        branch_text = str(
            pending_payload.get("branch")
            or pending_payload.get("source_branch")
            or pending_payload.get("sourceBranch")
            or ""
        ).strip()
    if not source_head_before:
        source_head_before = str(
            pending_payload.get("source_head_before")
            or pending_payload.get("sourceHeadBefore")
            or pending_payload.get("source_head")
            or pending_payload.get("sourceHead")
            or ""
        ).strip()
    if not source_head_after:
        source_head_after = str(
            pending_payload.get("source_head_after")
            or pending_payload.get("sourceHeadAfter")
            or ""
        ).strip()
    if not worktree_dir:
        worktree_dir = str(
            pending_payload.get("worktree_dir")
            or pending_payload.get("worktreeDir")
            or pending_payload.get("worktree")
            or ""
        ).strip()

    pending_changed_files = pending_payload.get("changed_files") if isinstance(pending_payload.get("changed_files"), list) else pending_payload.get("changedFiles")
    if not changed_files and pending_changed_files is not None:
        changed_files = pending_changed_files

    if not merge_preflight:
        merge_preflight = pending_payload.get("preflight") if isinstance(pending_payload.get("preflight"), dict) else pending_payload.get("preflight") or pending_payload.get("apply_check") or pending_payload.get("applyCheck") or {}

    derived_validation_artifacts = _load_task_validation_artifacts(run_dir, normalized_task_ids)
    derived_validation_statuses: list[str] = []
    derived_validation_artifact_paths: list[str] = []
    derived_goal_trace: list[object] = []
    derived_qa_notes: list[str] = []
    for artifact_path, raw in derived_validation_artifacts:
        path_text = artifact_path.as_posix()
        if _is_validation_artifact_path(path_text):
            derived_validation_artifact_paths.append(path_text)
        derived_validation_statuses.append(
            _normalize_packet_validation_status(
                raw.get("validation_status") or raw.get("validationStatus") or raw.get("status")
            )
        )
        for item in _task_validation_goal_trace(raw):
            if item not in derived_goal_trace:
                derived_goal_trace.append(item)
        for note in _task_validation_notes(raw):
            if note not in derived_qa_notes:
                derived_qa_notes.append(note)

    validation_artifacts_value = _normalize_str_list(validation_artifacts)
    validation_artifacts_value = [item for item in validation_artifacts_value if _is_validation_artifact_path(item)]
    for item in derived_validation_artifact_paths:
        if item not in validation_artifacts_value:
            validation_artifacts_value.append(item)

    qa_notes_value = _normalize_str_list(qa_notes)
    for note in derived_qa_notes:
        if note not in qa_notes_value:
            qa_notes_value.append(note)

    goal_trace_value = _normalize_list_value(goal_trace)
    if derived_goal_trace:
        if not goal_trace_value:
            goal_trace_value = list(derived_goal_trace)
        else:
            for item in derived_goal_trace:
                if item not in goal_trace_value:
                    goal_trace_value.append(item)

    validation_status_text = _choose_packet_validation_status(validation_status, derived_validation_statuses)

    missing = [
        name
        for name, value in (
            ("base_ref", base_ref_text),
            ("head_ref", head_ref_text),
            ("branch", branch_text),
        )
        if not value
    ]
    recoverable = bool(missing)
    if recoverable:
        packet_status = "branch_metadata_missing"
        if not recoverable_reason:
            recoverable_reason = "missing branch metadata: " + ", ".join(missing)

    packet_id_text = str(
        packet_id or _stable_packet_id(source_repo_path, run_id_text, normalized_task_ids, base_ref_text, head_ref_text, branch_text)
    ).strip()
    packet_path = pr_packet_path(source_repo_path, packet_id_text)

    changed_files_value = _normalize_changed_files(source_repo_path, base_ref_text, head_ref_text, changed_files)
    commits_value = _normalize_commits(source_repo_path, base_ref_text, head_ref_text, commits)
    merge_preflight_value = dict(merge_preflight or {})
    source_main_mutated = bool(
        str(source_head_before or "").strip()
        and str(source_head_after or "").strip()
        and str(source_head_before).strip() != str(source_head_after).strip()
    )
    if not merge_preflight_value:
        merge_preflight_value = {
            "base_ref": base_ref_text,
            "head_ref": head_ref_text,
            "branch": branch_text,
            "source_head_before": str(source_head_before or "").strip(),
            "source_head_after": str(source_head_after or "").strip(),
            "source_main_mutated": source_main_mutated,
        }
    else:
        merge_preflight_value.setdefault("base_ref", base_ref_text)
        merge_preflight_value.setdefault("head_ref", head_ref_text)
        merge_preflight_value.setdefault("branch", branch_text)
        if source_head_before or "source_head_before" not in merge_preflight_value:
            merge_preflight_value.setdefault("source_head_before", str(source_head_before or "").strip())
        if source_head_after or "source_head_after" not in merge_preflight_value:
            merge_preflight_value.setdefault("source_head_after", str(source_head_after or "").strip())
        merge_preflight_value.setdefault("source_main_mutated", source_main_mutated)

    packet: dict[str, object] = {
        "schema_version": PR_QUEUE_SCHEMA_VERSION,
        "kind": "pr_review_packet",
        "id": packet_id_text,
        "status": packet_status,
        "recoverable": recoverable,
        "recoverable_reason": recoverable_reason,
        "source_repo": source_repo_path.as_posix(),
        "run_id": run_id_text,
        "task_ids": normalized_task_ids,
        "base_ref": base_ref_text,
        "head_ref": head_ref_text,
        "branch": branch_text,
        "created_at": created_at_text,
        "updated_at": updated_at_text,
        "source_head_before": str(source_head_before or "").strip(),
        "source_head_after": str(source_head_after or "").strip(),
        "source_main_mutated": source_main_mutated,
        "worktree_dir": str(worktree_dir or "").strip(),
        "validation_status": validation_status_text,
        "validation_artifacts": validation_artifacts_value,
        "qa_notes": qa_notes_value,
        "goal_trace": goal_trace_value,
        "merge_preflight": merge_preflight_value,
        "changed_files": changed_files_value,
        "commits": commits_value,
        "packet_path": packet_path.as_posix(),
        "branch_index_path": pr_branch_index_path(source_repo_path).as_posix(),
    }

    index_entry: dict[str, object] = {
        "id": packet_id_text,
        "source_repo": source_repo_path.as_posix(),
        "run_id": run_id_text,
        "task_ids": normalized_task_ids,
        "base_ref": base_ref_text,
        "head_ref": head_ref_text,
        "branch": branch_text,
        "created_at": created_at_text,
        "updated_at": now,
        "packet_path": packet_path.as_posix(),
    }

    if recoverable:
        packet["branch_index_status"] = "skipped"
        atomic_write_json(packet_path, packet)
        return {
            "ok": False,
            "status": packet_status,
            "recoverable": True,
            "recoverable_reason": recoverable_reason,
            "packet_path": packet_path.as_posix(),
            "branch_index_path": pr_branch_index_path(source_repo_path).as_posix(),
            "packet_id": packet_id_text,
            "packet": packet,
            "branch_index_entry": None,
        }

    index = load_branch_index(source_repo_path)
    updated_index = _upsert_index_entry(index, index_entry)
    _write_branch_index(source_repo_path, updated_index)
    packet["branch_index_status"] = "written"
    atomic_write_json(packet_path, packet)

    return {
        "ok": True,
        "status": packet_status,
        "recoverable": False,
        "recoverable_reason": "",
        "packet_path": packet_path.as_posix(),
        "branch_index_path": pr_branch_index_path(source_repo_path).as_posix(),
        "packet_id": packet_id_text,
        "packet": packet,
        "branch_index_entry": index_entry,
        "branch_index": updated_index,
    }


def write_review_packet(source_repo: Path, **kwargs: object) -> dict[str, object]:
    return queue_review_packet(source_repo, **kwargs)


def load_review_packet(source_repo: Path, packet_id: str) -> dict[str, Any]:
    return _load_json_dict(pr_packet_path(source_repo, packet_id))


def _load_pr_queue_validation_config(run_dir: Path | None) -> dict[str, object]:
    config: dict[str, Any] = {}
    if run_dir is not None:
        for candidate in (
            Path(run_dir) / "last_run_summary.json",
            Path(run_dir) / "run_summary.json",
        ):
            config = _load_json_dict(candidate)
            if config:
                break
    return {
        "build_enabled": bool(config.get("build_enabled") or config.get("buildEnabled")),
        "run_tests": bool(config.get("run_tests") or config.get("runTests")),
        "build_cmd": config.get("build_cmd") or config.get("buildCmd") or [],
        "legacy_build_target": str(
            config.get("legacy_build_target")
            or config.get("legacyBuildTarget")
            or ""
        ).strip(),
        "test_cmd": config.get("test_cmd") or config.get("testCmd") or [],
        "legacy_test_target": str(
            config.get("legacy_test_target")
            or config.get("legacyTestTarget")
            or ""
        ).strip(),
        "legacy_test_filter": str(
            config.get("legacy_test_filter")
            or config.get("legacyTestFilter")
            or ""
        ).strip(),
        "build_timeout_seconds": int(config.get("build_timeout_seconds") or config.get("buildTimeoutSeconds") or 1800),
        "test_timeout_seconds": int(config.get("test_timeout_seconds") or config.get("testTimeoutSeconds") or 3600),
    }


def _pr_queue_validation_artifact_root(run_dir: Path, packet_id: str) -> Path:
    return Path(run_dir) / "pr_queue_validation" / str(packet_id or "").strip() / "attempt_01"


def _attach_goal_trace_to_record(record: dict[str, Any], goal_trace: Sequence[object] | object | None) -> None:
    goal_trace_value = _normalize_list_value(goal_trace)
    record["goal_trace"] = goal_trace_value
    record["goalTrace"] = goal_trace_value


def _pr_queue_validation_stage_status(
    record: dict[str, Any],
    *,
    reason: str,
    detail: str = "",
) -> str:
    raw_status = str(
        record.get("status")
        or record.get("validation_status")
        or record.get("validationStatus")
        or ""
    ).strip().lower()
    summary = str(
        record.get("summary")
        or record.get("failure_summary")
        or record.get("failureSummary")
        or detail
        or ""
    ).strip()
    if not record.get("cmd") and summary.lower() == "empty command":
        return "validation_pending"
    if raw_status in {"validation_passed", "passed", "pass", "success", "completed", "ok"}:
        return "validation_passed"
    if bool(record.get("ok", False)) and raw_status not in {"validation_pending", "tests_skipped", "no_tests_found", "stopped"}:
        return "validation_passed"
    if raw_status in {"validation_pending", "tests_skipped", "no_tests_found", "stopped"}:
        return "validation_pending"
    if raw_status == "blocked_env":
        return "blocked_env"
    if raw_status in {"validation_failed", "failed", "fail", "error"}:
        task_status = classify_task_failure(
            reason,
            validations=[record],
            detail=summary,
        )
        return "blocked_env" if task_status == "blocked_env" else "validation_failed"
    if not record.get("cmd"):
        return "validation_pending"
    return "validation_passed"


def _pr_queue_validation_note_record(
    *,
    name: str,
    kind: str,
    gate: str,
    artifact_path: Path,
    note: str,
    goal_trace: Sequence[object] | object | None,
    required: bool,
    applicable: bool,
    status: str,
) -> dict[str, object]:
    safe_write_text(artifact_path, note.rstrip() + "\n")
    record: dict[str, object] = {
        "name": str(name),
        "kind": str(kind),
        "gate": str(gate),
        "cmd": [],
        "rc": 0,
        "ok": str(status or "").strip().lower() == "validation_passed",
        "status": str(status or "validation_pending"),
        "validation_status": str(status or "validation_pending"),
        "validationStatus": str(status or "validation_pending"),
        "artifact_path": artifact_path.as_posix(),
        "artifactPath": artifact_path.as_posix(),
        "log_path": artifact_path.as_posix(),
        "logPath": artifact_path.as_posix(),
        "summary": note,
        "failure_summary": "",
        "failureSummary": "",
        "required": bool(required),
        "applicable": bool(applicable),
    }
    _attach_goal_trace_to_record(record, goal_trace)
    return record


def _pr_queue_validation_record_with_classification(
    record: dict[str, Any],
    *,
    reason: str,
    detail: str,
    goal_trace: Sequence[object] | object | None,
    required: bool,
    applicable: bool,
) -> dict[str, Any]:
    stage_status = _pr_queue_validation_stage_status(record, reason=reason, detail=detail)
    record = dict(record)
    record["validation_status"] = stage_status
    record["validationStatus"] = stage_status
    record["required"] = bool(required)
    record["applicable"] = bool(applicable)
    if stage_status == "validation_passed":
        record["ok"] = True
    elif stage_status in {"validation_pending", "validation_failed", "blocked_env"}:
        record["ok"] = False
    _attach_goal_trace_to_record(record, goal_trace)
    return record


def _update_packet_validation_metadata(
    packet: dict[str, Any],
    *,
    status: str,
    reason: str,
    detail: str,
    artifact_path: Path,
    artifacts: Sequence[object] | object | None,
    updated_at: str,
) -> dict[str, Any]:
    artifact_list = _normalize_str_list(artifacts)
    updated = dict(packet)
    updated["status"] = status
    updated["validation_status"] = status
    updated["validationStatus"] = status
    updated["validation_reason"] = reason
    updated["validationReason"] = reason
    updated["validation_detail"] = detail
    updated["validationDetail"] = detail
    updated["validation_artifact_path"] = artifact_path.as_posix()
    updated["validationArtifactPath"] = artifact_path.as_posix()
    updated["validation_artifacts"] = artifact_list
    updated["validationArtifacts"] = artifact_list
    updated["updated_at"] = updated_at
    return updated


async def validate_review_packet_async(
    source_repo: Path,
    packet_id: str,
    *,
    stop_path: Path | None = None,
) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    packet_id_text = str(packet_id or "").strip()
    if not packet_id_text:
        raise RuntimeError("Packet id is required for PR queue validation.")

    packet_path = pr_packet_path(source_repo_path, packet_id_text)
    packet = load_review_packet(source_repo_path, packet_id_text)
    if not packet:
        raise FileNotFoundError(f"PR packet not found: {packet_path}")
    if str(packet.get("source_repo") or "").strip():
        packet_source_repo = Path(str(packet.get("source_repo") or "")).expanduser().resolve()
        if packet_source_repo != source_repo_path:
            raise RuntimeError(
                f"Packet source repo mismatch: expected {source_repo_path.as_posix()}, got {packet_source_repo.as_posix()}"
            )

    run_id_text = str(packet.get("run_id") or "").strip()
    if not run_id_text:
        raise RuntimeError(f"PR packet {packet_id_text} is missing run_id metadata.")

    run_dir = _resolve_run_dir(source_repo_path, run_id_text)
    if run_dir is None:
        run_dir = source_repo_path / ".AgentCLI" / "agent_runs" / run_id_text
    run_dir.mkdir(parents=True, exist_ok=True)

    config = _load_pr_queue_validation_config(run_dir)
    goal_trace_value = _normalize_list_value(packet.get("goal_trace") or packet.get("goalTrace"))
    qa_notes_value = _normalize_str_list(packet.get("qa_notes") or packet.get("qaNotes"))
    changed_files_value = _normalize_str_list(packet.get("changed_files") or packet.get("changedFiles"))
    base_ref_text = str(packet.get("base_ref") or packet.get("baseRef") or "").strip()
    head_ref_text = str(packet.get("head_ref") or packet.get("headRef") or "").strip()
    branch_text = str(packet.get("branch") or "").strip()
    if not changed_files_value and base_ref_text and head_ref_text and base_ref_text != head_ref_text:
        changed_files_value = git_changed_files(source_repo_path, base_ref_text, head_ref_text)

    validation_root = _pr_queue_validation_artifact_root(run_dir, packet_id_text)
    validation_root.mkdir(parents=True, exist_ok=True)
    summary_path = validation_root / "validation.json"
    build_artifact_path = validation_root / "build.txt"
    test_artifact_path = validation_root / "test.txt"
    fast_artifact_path = validation_root / "fast_web_worktree_regression.json"

    validation_records: list[dict[str, Any]] = []
    validation_artifacts: list[str] = []
    pending = False
    failed = False
    blocked_env = False
    terminal_reason = ""
    terminal_detail = ""
    worktree_dir = allocate_temporary_worktree_dir(
        source_repo_path,
        prefix=f"pr-queue-validation-{packet_id_text}",
    )
    worktree_created = False
    worktree_removed = False
    cleanup_error = ""
    started_at = now_iso()
    started_monotonic = time.monotonic()
    source_head_before = git_head(source_repo_path)
    source_repo_state_before = git_repo_state(source_repo_path)

    try:
        create_worktree(source_repo_path, worktree_dir)
        worktree_created = True

        if bool(config.get("build_enabled", False)):
            try:
                build_record = await run_build_validation_async(
                    repo=worktree_dir,
                    build_cmd=config.get("build_cmd") or [],
                    build_timeout_sec=int(config.get("build_timeout_seconds") or 1800),
                    legacy_build_target=str(config.get("legacy_build_target") or ""),
                    log_path=build_artifact_path,
                    stop_path=stop_path,
                    command_repo=source_repo_path,
                )
            except Exception as ex:
                build_detail = f"{type(ex).__name__}: {str(ex).strip() or type(ex).__name__}"
                safe_write_text(build_artifact_path, build_detail + "\n")
                build_record = {
                    "name": "build",
                    "kind": "compile",
                    "gate": "build",
                    "cmd": [],
                    "rc": 127,
                    "ok": False,
                    "status": "failed",
                    "artifact_path": build_artifact_path.as_posix(),
                    "artifactPath": build_artifact_path.as_posix(),
                    "log_path": build_artifact_path.as_posix(),
                    "logPath": build_artifact_path.as_posix(),
                    "summary": build_detail,
                    "failure_summary": build_detail,
                    "failureSummary": build_detail,
                }
            build_record = _pr_queue_validation_record_with_classification(
                build_record,
                reason="build_failed",
                detail=str(build_record.get("summary") or build_record.get("failure_summary") or ""),
                goal_trace=goal_trace_value,
                required=True,
                applicable=True,
            )
            validation_records.append(build_record)
            validation_artifacts.append(build_artifact_path.as_posix())
            build_status = str(build_record.get("validation_status") or "")
            if build_status == "validation_pending":
                pending = True
            elif build_status == "validation_failed":
                failed = True
                terminal_reason = terminal_reason or "build_failed"
                terminal_detail = terminal_detail or str(build_record.get("summary") or build_record.get("failure_summary") or "")
            elif build_status == "blocked_env":
                blocked_env = True
                terminal_reason = terminal_reason or "build_failed"
                terminal_detail = terminal_detail or str(build_record.get("summary") or build_record.get("failure_summary") or "")
        else:
            build_record = _pr_queue_validation_note_record(
                name="build",
                kind="compile",
                gate="build",
                artifact_path=build_artifact_path,
                note="Build validation disabled by run configuration.",
                goal_trace=goal_trace_value,
                required=False,
                applicable=True,
                status="validation_pending",
            )
            validation_records.append(build_record)
            validation_artifacts.append(build_artifact_path.as_posix())
            pending = True

        if terminal_reason:
            test_record = _pr_queue_validation_note_record(
                name="test",
                kind="test",
                gate="test",
                artifact_path=test_artifact_path,
                note=f"Not run because {terminal_reason} was reported first.",
                goal_trace=goal_trace_value,
                required=False,
                applicable=True,
                status="validation_pending",
            )
            validation_records.append(test_record)
            validation_artifacts.append(test_artifact_path.as_posix())
        elif bool(config.get("run_tests", False)):
            try:
                test_record = await run_test_validation_async(
                    repo=worktree_dir,
                    test_cmd=config.get("test_cmd") or [],
                    test_timeout_sec=int(config.get("test_timeout_seconds") or 3600),
                    legacy_test_target=str(config.get("legacy_test_target") or ""),
                    legacy_test_filter=str(config.get("legacy_test_filter") or ""),
                    log_path=test_artifact_path,
                    stop_path=stop_path,
                    max_output_bytes=10_000_000,
                    command_repo=source_repo_path,
                )
            except Exception as ex:
                test_detail = f"{type(ex).__name__}: {str(ex).strip() or type(ex).__name__}"
                safe_write_text(test_artifact_path, test_detail + "\n")
                test_record = {
                    "name": "test",
                    "kind": "test",
                    "gate": "test",
                    "cmd": [],
                    "rc": 127,
                    "ok": False,
                    "status": "failed",
                    "artifact_path": test_artifact_path.as_posix(),
                    "artifactPath": test_artifact_path.as_posix(),
                    "log_path": test_artifact_path.as_posix(),
                    "logPath": test_artifact_path.as_posix(),
                    "summary": test_detail,
                    "failure_summary": test_detail,
                    "failureSummary": test_detail,
                }
            test_record = _pr_queue_validation_record_with_classification(
                test_record,
                reason="test_failed",
                detail=str(test_record.get("summary") or test_record.get("failure_summary") or ""),
                goal_trace=goal_trace_value,
                required=True,
                applicable=True,
            )
            validation_records.append(test_record)
            validation_artifacts.append(test_artifact_path.as_posix())
            test_status = str(test_record.get("validation_status") or "")
            if test_status == "validation_pending":
                pending = True
            elif test_status == "validation_failed":
                failed = True
                terminal_reason = terminal_reason or "test_failed"
                terminal_detail = terminal_detail or str(test_record.get("summary") or test_record.get("failure_summary") or "")
            elif test_status == "blocked_env":
                blocked_env = True
                terminal_reason = terminal_reason or "test_failed"
                terminal_detail = terminal_detail or str(test_record.get("summary") or test_record.get("failure_summary") or "")
        else:
            test_record = _pr_queue_validation_note_record(
                name="test",
                kind="test",
                gate="test",
                artifact_path=test_artifact_path,
                note="Test validation disabled by run configuration.",
                goal_trace=goal_trace_value,
                required=False,
                applicable=True,
                status="validation_pending",
            )
            validation_records.append(test_record)
            validation_artifacts.append(test_artifact_path.as_posix())
            pending = True

        if terminal_reason:
            fast_record = _pr_queue_validation_note_record(
                name="fast_web_worktree_regression",
                kind="regression",
                gate="fast_web_worktree_regression",
                artifact_path=fast_artifact_path,
                note=f"Not run because {terminal_reason} was reported first.",
                goal_trace=goal_trace_value,
                required=False,
                applicable=True,
                status="validation_pending",
            )
            validation_records.append(fast_record)
            validation_artifacts.append(fast_artifact_path.as_posix())
        elif repo_has_web_worktree_markers(source_repo_path):
            try:
                fast_result = await run_fast_web_worktree_regression_async(
                    worktree_dir,
                    fast_artifact_path,
                    stop_path=stop_path,
                    trigger_files=changed_files_value,
                )
            except Exception as ex:
                fast_detail = f"{type(ex).__name__}: {str(ex).strip() or type(ex).__name__}"
                safe_write_text(fast_artifact_path, fast_detail + "\n")
                fast_result = {
                    "schema_version": 1,
                    "gate": "fast_web_worktree_regression",
                    "repo": worktree_dir.as_posix(),
                    "started_at": now_iso(),
                    "ended_at": now_iso(),
                    "elapsed_sec": 0.0,
                    "elapsedSec": 0.0,
                    "ok": False,
                    "commands": [],
                    "log_path": fast_artifact_path.as_posix(),
                    "artifact_path": fast_artifact_path.as_posix(),
                    "artifactPath": fast_artifact_path.as_posix(),
                    "failed_command": None,
                    "failure_summary": fast_detail,
                    "failureSummary": fast_detail,
                    "trigger_files": list(changed_files_value),
                    "triggerFiles": list(changed_files_value),
                    "suite_files": [],
                    "suiteFiles": [],
                }
            fast_record = {
                "name": "fast_web_worktree_regression",
                "kind": "regression",
                "gate": "fast_web_worktree_regression",
                "cmd": list(fast_result.get("suite_files") or fast_result.get("suiteFiles") or []),
                "rc": 0 if bool(fast_result.get("ok", False)) else 1,
                "ok": bool(fast_result.get("ok", False)),
                "status": "passed" if bool(fast_result.get("ok", False)) else "failed",
                "artifact_path": fast_artifact_path.as_posix(),
                "artifactPath": fast_artifact_path.as_posix(),
                "log_path": fast_artifact_path.as_posix(),
                "logPath": fast_artifact_path.as_posix(),
                "summary": str(fast_result.get("failure_summary") or fast_result.get("failureSummary") or fast_artifact_path.as_posix()),
                "failure_summary": str(fast_result.get("failure_summary") or ""),
                "failureSummary": str(fast_result.get("failureSummary") or ""),
                "commands": list(fast_result.get("commands") or []),
                "failed_command": fast_result.get("failed_command"),
                "started_at": fast_result.get("started_at"),
                "startedAt": fast_result.get("started_at"),
                "ended_at": fast_result.get("ended_at"),
                "endedAt": fast_result.get("ended_at"),
                "trigger_files": list(fast_result.get("trigger_files") or fast_result.get("triggerFiles") or changed_files_value),
                "triggerFiles": list(fast_result.get("trigger_files") or fast_result.get("triggerFiles") or changed_files_value),
                "suite_files": list(fast_result.get("suite_files") or fast_result.get("suiteFiles") or []),
                "suiteFiles": list(fast_result.get("suite_files") or fast_result.get("suiteFiles") or []),
            }
            fast_record = _pr_queue_validation_record_with_classification(
                fast_record,
                reason="fast_regression_failed",
                detail=str(fast_record.get("summary") or fast_record.get("failure_summary") or ""),
                goal_trace=goal_trace_value,
                required=True,
                applicable=True,
            )
            validation_records.append(fast_record)
            validation_artifacts.append(fast_artifact_path.as_posix())
            fast_status = str(fast_record.get("validation_status") or "")
            if fast_status == "validation_pending":
                pending = True
            elif fast_status == "validation_failed":
                failed = True
                terminal_reason = terminal_reason or "fast_regression_failed"
                terminal_detail = terminal_detail or str(fast_record.get("summary") or fast_record.get("failure_summary") or "")
            elif fast_status == "blocked_env":
                blocked_env = True
                terminal_reason = terminal_reason or "fast_regression_failed"
                terminal_detail = terminal_detail or str(fast_record.get("summary") or fast_record.get("failure_summary") or "")
        else:
            fast_record = _pr_queue_validation_note_record(
                name="fast_web_worktree_regression",
                kind="regression",
                gate="fast_web_worktree_regression",
                artifact_path=fast_artifact_path,
                note="Fast web/worktree regression is not applicable to this repository.",
                goal_trace=goal_trace_value,
                required=False,
                applicable=False,
                status="validation_passed",
            )
            validation_records.append(fast_record)
            validation_artifacts.append(fast_artifact_path.as_posix())

    except Exception as ex:
        blocked_env = True
        terminal_reason = terminal_reason or "worktree_creation_failed"
        terminal_detail = terminal_detail or f"{type(ex).__name__}: {str(ex).strip() or type(ex).__name__}"
    finally:
        if worktree_created:
            try:
                remove_worktree(source_repo_path, worktree_dir)
                worktree_removed = True
            except Exception as ex:
                cleanup_error = f"{type(ex).__name__}: {str(ex).strip() or type(ex).__name__}"
                blocked_env = True

    ended_at = now_iso()
    elapsed_sec = round(max(0.0, time.monotonic() - started_monotonic), 3)
    source_head_after = git_head(source_repo_path)
    source_repo_state_after = git_repo_state(source_repo_path)
    source_main_mutated = bool(source_head_before and source_head_after and source_head_before != source_head_after)
    if source_main_mutated:
        blocked_env = True
        if not terminal_reason:
            terminal_reason = "source_repo_mutated"
        if not terminal_detail:
            terminal_detail = "Validation mutated the source repository HEAD."
    if cleanup_error:
        if not terminal_reason:
            terminal_reason = "worktree_cleanup_failed"
        if terminal_detail:
            terminal_detail = f"{terminal_detail}\nCleanup: {cleanup_error}"
        else:
            terminal_detail = cleanup_error

    validation_status = classify_pr_queue_validation_status(
        pending=pending,
        failed=failed,
        blocked_env=blocked_env,
    )
    if terminal_reason in {"source_repo_mutated", "worktree_cleanup_failed"}:
        validation_status = "blocked_env"
    validation_reason = terminal_reason or validation_status
    validation_detail = terminal_detail or validation_reason

    validation_summary = {
        "records_total": len(validation_records),
        "records_passed": len([record for record in validation_records if str(record.get("validation_status") or "").strip().lower() == "validation_passed"]),
        "records_pending": len([record for record in validation_records if str(record.get("validation_status") or "").strip().lower() == "validation_pending"]),
        "records_failed": len([record for record in validation_records if str(record.get("validation_status") or "").strip().lower() == "validation_failed"]),
        "records_blocked_env": len([record for record in validation_records if str(record.get("validation_status") or "").strip().lower() == "blocked_env"]),
    }

    validation_plan = {
        "build_enabled": bool(config.get("build_enabled", False)),
        "run_tests": bool(config.get("run_tests", False)),
        "fast_regression_applicable": repo_has_web_worktree_markers(source_repo_path),
    }

    summary_payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "pr_queue_validation_attempt",
        "packet_id": packet_id_text,
        "packetId": packet_id_text,
        "packet_path": packet_path.as_posix(),
        "packetPath": packet_path.as_posix(),
        "run_id": run_id_text,
        "runId": run_id_text,
        "source_repo": source_repo_path.as_posix(),
        "sourceRepo": source_repo_path.as_posix(),
        "task_ids": _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")),
        "taskIds": _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")),
        "branch": branch_text,
        "base_ref": base_ref_text,
        "head_ref": head_ref_text,
        "goal_trace": goal_trace_value,
        "goalTrace": goal_trace_value,
        "qa_notes": qa_notes_value,
        "qaNotes": qa_notes_value,
        "status": validation_status,
        "validation_status": validation_status,
        "validationStatus": validation_status,
        "reason": validation_reason,
        "validation_reason": validation_reason,
        "validationReason": validation_reason,
        "detail": validation_detail,
        "validation_detail": validation_detail,
        "validationDetail": validation_detail,
        "artifact_path": summary_path.as_posix(),
        "artifactPath": summary_path.as_posix(),
        "validation_artifact_path": summary_path.as_posix(),
        "validationArtifactPath": summary_path.as_posix(),
        "validation_artifacts": list(validation_artifacts),
        "validationArtifacts": list(validation_artifacts),
        "validation_records": validation_records,
        "validationRecords": validation_records,
        "validation_summary": validation_summary,
        "validationSummary": validation_summary,
        "validation_plan": validation_plan,
        "validationPlan": validation_plan,
        "worktree_dir": worktree_dir.as_posix(),
        "worktreeDir": worktree_dir.as_posix(),
        "worktree_created": worktree_created,
        "worktreeCreated": worktree_created,
        "worktree_removed": worktree_removed,
        "worktreeRemoved": worktree_removed,
        "cleanup_error": cleanup_error,
        "cleanupError": cleanup_error,
        "started_at": started_at,
        "startedAt": started_at,
        "ended_at": ended_at,
        "endedAt": ended_at,
        "elapsed_sec": elapsed_sec,
        "elapsedSec": elapsed_sec,
        "source_head_before": source_head_before,
        "source_head_after": source_head_after,
        "source_repo_state_before": source_repo_state_before,
        "source_repo_state_after": source_repo_state_after,
        "source_main_mutated": source_main_mutated,
    }
    atomic_write_json(summary_path, summary_payload)

    updated_packet = _update_packet_validation_metadata(
        packet,
        status=validation_status,
        reason=validation_reason,
        detail=validation_detail,
        artifact_path=summary_path,
        artifacts=validation_artifacts,
        updated_at=ended_at,
    )
    atomic_write_json(packet_path, updated_packet)

    index = load_branch_index(source_repo_path)
    index_entry = {
        "id": packet_id_text,
        "source_repo": source_repo_path.as_posix(),
        "run_id": run_id_text,
        "task_ids": _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")),
        "base_ref": base_ref_text,
        "head_ref": head_ref_text,
        "branch": branch_text,
        "created_at": str(packet.get("created_at") or packet.get("createdAt") or ended_at),
        "updated_at": ended_at,
        "packet_path": packet_path.as_posix(),
    }
    updated_index = _upsert_index_entry(index, index_entry)
    _write_branch_index(source_repo_path, updated_index)

    return {
        "ok": validation_status == "validation_passed",
        "status": validation_status,
        "validation_status": validation_status,
        "packet_id": packet_id_text,
        "packet_path": packet_path.as_posix(),
        "packet": updated_packet,
        "validation_artifact_path": summary_path.as_posix(),
        "validation_artifacts": list(validation_artifacts),
        "validation_records": validation_records,
        "validation_summary": validation_summary,
        "validation_plan": validation_plan,
        "worktree_dir": worktree_dir.as_posix(),
        "worktree_created": worktree_created,
        "worktree_removed": worktree_removed,
        "cleanup_error": cleanup_error,
        "source_head_before": source_head_before,
        "source_head_after": source_head_after,
        "source_repo_state_before": source_repo_state_before,
        "source_repo_state_after": source_repo_state_after,
        "source_main_mutated": source_main_mutated,
        "summary_path": summary_path.as_posix(),
        "summary": summary_payload,
    }


def validate_review_packet(
    source_repo: Path,
    packet_id: str,
    *,
    stop_path: Path | None = None,
) -> dict[str, object]:
    return asyncio.run(
        validate_review_packet_async(
            source_repo,
            packet_id,
            stop_path=stop_path,
        )
    )
