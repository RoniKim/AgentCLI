from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any, Mapping, Sequence

from .experience import (
    record_pr_queue_signal,
    record_validation_experiences,
    redact_validation_summary,
    sanitize_experience_lesson,
)
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
    git_rev_parse_ref,
    generated_worktree_path_state,
    read_pending_worktree_merge,
    remove_worktree,
)
from .task_status import classify_task_failure
from .utils import atomic_write_json, now_iso, run_cmd, safe_write_text


PR_QUEUE_DIRNAME = "pr_queue"
PR_QUEUE_SCHEMA_VERSION = 1
PR_QUEUE_INDEX_FILENAME = "branch_index.json"
PR_QUEUE_MERGE_CONFIRMATION_PREFIX = "MERGE PR"
PR_QUEUE_LOCK_FILENAME = ".queue.lock"
PR_QUEUE_LOCK_TIMEOUT_SEC = 30.0
PR_QUEUE_LOCK_POLL_SEC = 0.05


_PR_QUEUE_THREAD_LOCKS: dict[str, threading.RLock] = {}
_PR_QUEUE_THREAD_LOCKS_GUARD = threading.Lock()


class PrQueueMergeError(RuntimeError):
    """Structured failure raised when a queued PR cannot be approved for merge."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, object] | None = None,
        status_code: int = 409,
        status: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.details = dict(details or {})
        self.status_code = int(status_code)
        if status:
            self.status = str(status)
        elif self.status_code == 404:
            self.status = "unavailable"
        elif self.status_code >= 500:
            self.status = "error"
        elif self.status_code >= 409:
            self.status = "conflict"
        else:
            self.status = "invalid_request"


def pr_queue_root(source_repo: Path) -> Path:
    return Path(source_repo).expanduser().resolve() / ".AgentCLI" / PR_QUEUE_DIRNAME


def pr_packet_path(source_repo: Path, packet_id: str) -> Path:
    return pr_queue_root(source_repo) / f"{packet_id}.json"


def pr_branch_index_path(source_repo: Path) -> Path:
    return pr_queue_root(source_repo) / PR_QUEUE_INDEX_FILENAME


def _pr_queue_lock_path(source_repo: Path) -> Path:
    return pr_queue_root(source_repo) / PR_QUEUE_LOCK_FILENAME


def _pr_queue_thread_lock(path: Path) -> threading.RLock:
    key = path.resolve().as_posix()
    with _PR_QUEUE_THREAD_LOCKS_GUARD:
        lock = _PR_QUEUE_THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PR_QUEUE_THREAD_LOCKS[key] = lock
        return lock


def _prepare_pr_queue_lock_handle(handle: Any) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)


def _try_acquire_pr_queue_file_lock(handle: Any) -> bool:
    _prepare_pr_queue_lock_handle(handle)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _release_pr_queue_file_lock(handle: Any) -> None:
    _prepare_pr_queue_lock_handle(handle)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def _pr_queue_write_lock(source_repo: Path):
    source_repo_path = Path(source_repo).expanduser().resolve()
    queue_root = pr_queue_root(source_repo_path)
    queue_root.mkdir(parents=True, exist_ok=True)
    lock_path = _pr_queue_lock_path(source_repo_path)
    thread_lock = _pr_queue_thread_lock(lock_path)
    started = time.monotonic()
    with thread_lock, lock_path.open("a+b") as handle:
        while True:
            if _try_acquire_pr_queue_file_lock(handle):
                break
            if (time.monotonic() - started) >= PR_QUEUE_LOCK_TIMEOUT_SEC:
                raise RuntimeError(f"Timed out acquiring PR queue lock: {lock_path}")
            time.sleep(PR_QUEUE_LOCK_POLL_SEC)
        try:
            yield
        finally:
            _release_pr_queue_file_lock(handle)


def _packet_revision_token(packet: Mapping[str, Any]) -> str:
    payload = json.dumps(packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _packet_index_sync_extra(packet: Mapping[str, Any]) -> dict[str, object]:
    extra: dict[str, object] = {}
    for keys in (
        ("discard_status", "discardStatus"),
        ("discard_reason", "discardReason"),
        ("rebase_status", "rebaseStatus"),
        ("rebase_reason", "rebaseReason"),
    ):
        value = str(packet.get(keys[0]) or packet.get(keys[1]) or "").strip()
        if value:
            extra[keys[0]] = value
    reconciliation = (
        packet.get("queue_reconciliation")
        if isinstance(packet.get("queue_reconciliation"), dict)
        else packet.get("queueReconciliation")
        if isinstance(packet.get("queueReconciliation"), dict)
        else None
    )
    if isinstance(reconciliation, dict) and reconciliation:
        extra["queue_reconciliation"] = dict(reconciliation)
    return extra


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
        with _pr_queue_write_lock(source_repo_path):
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

    with _pr_queue_write_lock(source_repo_path):
        packet, index_entry = _write_packet_and_index_state(
            source_repo_path,
            packet_path,
            packet,
            packet_id=packet_id_text,
            updated_at=updated_at_text,
            status=packet_status,
            validation_status=validation_status_text,
            fallback_branch_index_status="skipped",
        )
        updated_index = load_branch_index(source_repo_path)

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


def pr_queue_merge_confirmation_phrase(packet_id: str) -> str:
    packet_id_text = str(packet_id or "").strip()
    return f"{PR_QUEUE_MERGE_CONFIRMATION_PREFIX} {packet_id_text}".strip()


def _pr_queue_signal_evidence(
    packet_path: Path,
    *,
    primary_path: str = "",
    artifact_paths: Sequence[object] | object | None = None,
    extra: Sequence[object] | object | None = None,
) -> list[object]:
    evidence: list[object] = [
        {
            "kind": "pr_packet",
            "path": packet_path.as_posix(),
        }
    ]
    primary_text = str(primary_path or "").strip()
    if primary_text:
        evidence.append(
            {
                "kind": "primary_artifact",
                "path": primary_text,
            }
        )
    for item in _normalize_list_value(artifact_paths):
        path_text = str(item or "").strip()
        if path_text:
            evidence.append(
                {
                    "kind": "artifact",
                    "path": path_text,
                }
            )
    for item in _normalize_list_value(extra):
        if isinstance(item, dict):
            evidence.append(dict(item))
            continue
        path_text = str(item or "").strip()
        if path_text:
            evidence.append(
                {
                    "kind": "artifact",
                    "path": path_text,
                }
            )
    deduped: list[object] = []
    seen: set[str] = set()
    for item in evidence:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _record_pr_queue_decision_signal(
    source_repo: Path,
    *,
    packet_id: str,
    packet: dict[str, Any],
    signal_kind: str,
    decision_status: str,
    reason: str,
    packet_path: Path,
    primary_path: str = "",
    artifact_paths: Sequence[object] | object | None = None,
    extra_evidence: Sequence[object] | object | None = None,
    metadata: dict[str, object] | None = None,
    recorded_at: str | None = None,
) -> list[dict[str, Any]]:
    evidence = _pr_queue_signal_evidence(
        packet_path,
        primary_path=primary_path,
        artifact_paths=artifact_paths,
        extra=extra_evidence,
    )
    return record_pr_queue_signal(
        source_repo,
        packet_id=packet_id,
        packet=packet,
        signal_kind=signal_kind,
        decision_status=decision_status,
        reason=reason,
        evidence=evidence,
        metadata=metadata,
        recorded_at=recorded_at,
    )


def record_review_packet_decision(
    source_repo: Path,
    packet_id: str,
    *,
    action: str,
    decision_status: str,
    reason: str = "",
    evidence: Sequence[object] | object | None = None,
    metadata: dict[str, object] | None = None,
    recorded_at: str | None = None,
) -> list[dict[str, Any]]:
    """Record PR queue decision experience without mutating the packet.

    This is the integration point for later discard/rebase queue operations.
    It only records structured experience state and does not claim the queue
    action itself succeeded.
    """

    source_repo_path = Path(source_repo).expanduser().resolve()
    packet_id_text = str(packet_id or "").strip()
    if not packet_id_text:
        raise ValueError("PR packet id is required.")

    action_text = str(action or "").strip().lower()
    if action_text not in {"validate", "merge", "discard", "rebase"}:
        raise ValueError(f"Unsupported PR queue decision action: {action_text or action}")

    packet_path = pr_packet_path(source_repo_path, packet_id_text)
    packet = load_review_packet(source_repo_path, packet_id_text)
    if not packet:
        raise FileNotFoundError(f"PR packet not found: {packet_path}")

    return _record_pr_queue_decision_signal(
        source_repo_path,
        packet_id=packet_id_text,
        packet=packet,
        signal_kind=action_text,
        decision_status=str(decision_status or "").strip().lower(),
        reason=str(reason or "").strip(),
        packet_path=packet_path,
        extra_evidence=evidence,
        metadata=metadata,
        recorded_at=recorded_at,
    )


def _packet_id_key(value: object) -> str:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        return ""
    return text


def _path_is_within(root: Path, path: Path) -> bool:
    try:
        resolved_root = root.resolve()
    except Exception:
        resolved_root = root
    try:
        resolved_path = path.resolve()
    except Exception:
        resolved_path = path
    return resolved_path == resolved_root or resolved_root in resolved_path.parents


def _find_branch_index_entry(index: dict[str, object], packet_id: str) -> dict[str, Any] | None:
    packet_id_text = str(packet_id or "").strip()
    if not packet_id_text:
        return None
    for item in index.get("entries", []) if isinstance(index.get("entries"), list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() == packet_id_text:
            return dict(item)
    return None


def _require_review_packet(source_repo: Path, packet_id: str) -> tuple[Path, str, Path, dict[str, Any]]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    packet_id_text = _packet_id_key(packet_id)
    if not packet_id_text:
        raise ValueError("PR packet id is required.")
    packet_path = pr_packet_path(source_repo_path, packet_id_text)
    if not packet_path.exists() or not packet_path.is_file():
        raise FileNotFoundError(f"PR packet not found: {packet_path}")
    packet = load_review_packet(source_repo_path, packet_id_text)
    if not packet:
        raise RuntimeError(f"PR packet is empty or malformed: {packet_path}")
    return source_repo_path, packet_id_text, packet_path, packet


def _write_packet_index_state(
    source_repo: Path,
    packet_path: Path,
    packet: dict[str, Any],
    *,
    packet_id: str,
    updated_at: str,
    status: str = "",
    approval_status: str = "",
    validation_status: str = "",
    extra: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    source_repo_path = Path(source_repo).expanduser().resolve()
    index = load_branch_index(source_repo_path)
    existing = _find_branch_index_entry(index, packet_id)
    base_ref_text = str(packet.get("base_ref") or packet.get("baseRef") or "").strip()
    head_ref_text = str(packet.get("head_ref") or packet.get("headRef") or "").strip()
    branch_text = str(packet.get("branch") or "").strip()
    if existing is None and not (base_ref_text and head_ref_text and branch_text):
        return None

    entry: dict[str, object] = {
        "id": packet_id,
        "updated_at": updated_at,
        "packet_path": packet_path.as_posix(),
    }
    source_repo_text = str(packet.get("source_repo") or packet.get("sourceRepo") or source_repo_path.as_posix()).strip()
    run_id_text = str(packet.get("run_id") or packet.get("runId") or "").strip()
    created_at_text = str(packet.get("created_at") or packet.get("createdAt") or updated_at).strip() or updated_at
    task_ids = _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds"))
    if source_repo_text:
        entry["source_repo"] = source_repo_text
    if run_id_text:
        entry["run_id"] = run_id_text
    if task_ids:
        entry["task_ids"] = task_ids
    if created_at_text:
        entry["created_at"] = created_at_text
    if base_ref_text:
        entry["base_ref"] = base_ref_text
    if head_ref_text:
        entry["head_ref"] = head_ref_text
    if branch_text:
        entry["branch"] = branch_text
    if status:
        entry["status"] = status
    if approval_status:
        entry["approval_status"] = approval_status
    if validation_status:
        entry["validation_status"] = validation_status
    for key, value in _packet_index_sync_extra(packet).items():
        entry[key] = value
    if isinstance(extra, dict):
        for key, value in extra.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            entry[key] = value

    updated_index = _upsert_index_entry(index, entry)
    _write_branch_index(source_repo_path, updated_index)
    return _find_branch_index_entry(updated_index, packet_id)


def _write_packet_and_index_state(
    source_repo: Path,
    packet_path: Path,
    packet: dict[str, Any],
    *,
    packet_id: str,
    updated_at: str,
    status: str = "",
    approval_status: str = "",
    validation_status: str = "",
    extra: dict[str, object] | None = None,
    fallback_branch_index_status: str = "skipped",
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    pending_packet = dict(packet)
    pending_packet["branch_index_status"] = "pending"
    atomic_write_json(packet_path, pending_packet)

    index_entry = _write_packet_index_state(
        source_repo,
        packet_path,
        pending_packet,
        packet_id=packet_id,
        updated_at=updated_at,
        status=status,
        approval_status=approval_status,
        validation_status=validation_status,
        extra=extra,
    )
    final_packet = dict(pending_packet)
    final_packet["branch_index_status"] = "written" if index_entry is not None else str(fallback_branch_index_status or "skipped")
    if final_packet != pending_packet:
        atomic_write_json(packet_path, final_packet)
    return final_packet, index_entry


def _write_branch_index_entry_state(
    source_repo: Path,
    entry: dict[str, Any],
    *,
    updated_at: str,
    extra: dict[str, object] | None = None,
) -> dict[str, Any] | None:
    entry_id = _packet_id_key(entry.get("id"))
    if not entry_id:
        return None
    updated_entry = dict(entry)
    updated_entry["id"] = entry_id
    updated_entry["updated_at"] = updated_at
    if isinstance(extra, dict):
        for key, value in extra.items():
            updated_entry[key] = value
    index = load_branch_index(source_repo)
    updated_index = _upsert_index_entry(index, updated_entry)
    _write_branch_index(source_repo, updated_index)
    return _find_branch_index_entry(updated_index, entry_id)


def _reconciliation_issue(
    kind: str,
    state: str,
    message: str,
    *,
    path: str = "",
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    issue: dict[str, object] = {
        "kind": str(kind or "").strip(),
        "state": str(state or "").strip(),
        "message": str(message or "").strip(),
    }
    if path:
        issue["path"] = str(path).strip()
    if details:
        issue["details"] = dict(details)
    return issue


def _reconciliation_issue_keys(issues: Sequence[dict[str, object]]) -> list[str]:
    keys: list[str] = []
    for issue in issues:
        kind = str(issue.get("kind") or "").strip()
        state = str(issue.get("state") or "").strip()
        if not kind or not state:
            continue
        key = f"{kind}:{state}"
        if key not in keys:
            keys.append(key)
    return keys


def _reconciliation_packet_paths(source_repo: Path, index: dict[str, object]) -> list[Path]:
    queue_root = pr_queue_root(source_repo)
    candidates: dict[str, Path] = {}
    for path in queue_root.glob("*.json"):
        if path.is_file() and path.name != PR_QUEUE_INDEX_FILENAME:
            candidates[path.resolve().as_posix()] = path.resolve()
    for entry in index.get("entries", []) if isinstance(index.get("entries"), list) else []:
        if not isinstance(entry, dict):
            continue
        packet_path_text = str(entry.get("packet_path") or entry.get("packetPath") or "").strip()
        packet_id = _packet_id_key(entry.get("id"))
        candidate: Path | None = None
        if packet_path_text:
            try:
                candidate = Path(packet_path_text).expanduser().resolve()
            except Exception:
                candidate = Path(packet_path_text).expanduser()
        elif packet_id:
            candidate = pr_packet_path(source_repo, packet_id)
            try:
                candidate = candidate.resolve()
            except Exception:
                pass
        if candidate is None or not _path_is_within(queue_root, candidate):
            continue
        candidates[candidate.as_posix()] = candidate
    return [candidates[key] for key in sorted(candidates)]


def _pr_queue_diff_artifact_candidates(packet: dict[str, Any], run_dir: Path | None) -> list[str]:
    paths = _normalize_str_list(packet.get("diff_artifacts") or packet.get("diffArtifacts"))
    for key in ("patch_path", "patchPath", "patch", "diff_artifact", "diffArtifact"):
        value = str(packet.get(key) or "").strip()
        if value and value not in paths:
            paths.append(value)
    preflight = packet.get("merge_preflight") if isinstance(packet.get("merge_preflight"), dict) else packet.get("mergePreflight")
    if isinstance(preflight, dict):
        for key in ("patch_path", "patchPath", "patch", "diff_artifact", "diffArtifact"):
            value = str(preflight.get(key) or "").strip()
            if value and value not in paths:
                paths.append(value)
    if run_dir is not None:
        for name in ("worktree.patch", "worktree_dirty_uncommitted.patch"):
            candidate = run_dir / name
            if candidate.exists():
                value = candidate.as_posix()
                if value not in paths:
                    paths.append(value)
    return paths


def _packet_expected_index(packet: dict[str, Any]) -> bool:
    packet_status = str(packet.get("status") or "").strip().lower()
    branch_index_status = str(packet.get("branch_index_status") or "").strip().lower()
    if packet_status == "branch_metadata_missing":
        return False
    return branch_index_status != "skipped"


def _branch_index_entry_is_stale(packet_path: Path, packet: dict[str, Any], index_entry: dict[str, Any]) -> bool:
    expected_packet_path = packet_path.resolve().as_posix()
    actual_packet_path = str(index_entry.get("packet_path") or index_entry.get("packetPath") or "").strip()
    if actual_packet_path:
        try:
            actual_packet_path = Path(actual_packet_path).expanduser().resolve().as_posix()
        except Exception:
            actual_packet_path = Path(actual_packet_path).expanduser().as_posix()
    if actual_packet_path != expected_packet_path:
        return True

    expected_values = {
        "source_repo": str(packet.get("source_repo") or packet.get("sourceRepo") or "").strip(),
        "run_id": str(packet.get("run_id") or packet.get("runId") or "").strip(),
        "branch": str(packet.get("branch") or "").strip(),
        "base_ref": str(packet.get("base_ref") or packet.get("baseRef") or "").strip(),
        "head_ref": str(packet.get("head_ref") or packet.get("headRef") or "").strip(),
    }
    for key, expected in expected_values.items():
        actual = str(index_entry.get(key) or "").strip()
        if expected != actual:
            return True
    expected_status = str(packet.get("status") or "").strip()
    actual_status = str(index_entry.get("status") or "").strip()
    if expected_status != actual_status:
        return True
    expected_approval_status = str(packet.get("approval_status") or packet.get("approvalStatus") or "").strip()
    actual_approval_status = str(index_entry.get("approval_status") or index_entry.get("approvalStatus") or "").strip()
    if expected_approval_status != actual_approval_status:
        return True
    expected_validation_status = _normalize_packet_validation_status(
        packet.get("validation_status") or packet.get("validationStatus") or packet.get("status")
    )
    actual_validation_status = _normalize_packet_validation_status(
        index_entry.get("validation_status") or index_entry.get("validationStatus")
    )
    if expected_validation_status != actual_validation_status:
        return True
    for packet_keys, index_keys in (
        (("discard_status", "discardStatus"), ("discard_status", "discardStatus")),
        (("discard_reason", "discardReason"), ("discard_reason", "discardReason")),
        (("rebase_status", "rebaseStatus"), ("rebase_status", "rebaseStatus")),
        (("rebase_reason", "rebaseReason"), ("rebase_reason", "rebaseReason")),
    ):
        expected_extra = str(packet.get(packet_keys[0]) or packet.get(packet_keys[1]) or "").strip()
        actual_extra = str(index_entry.get(index_keys[0]) or index_entry.get(index_keys[1]) or "").strip()
        if expected_extra != actual_extra:
            return True
    expected_reconciliation = (
        dict(packet.get("queue_reconciliation"))
        if isinstance(packet.get("queue_reconciliation"), dict)
        else dict(packet.get("queueReconciliation"))
        if isinstance(packet.get("queueReconciliation"), dict)
        else {}
    )
    actual_reconciliation = (
        dict(index_entry.get("queue_reconciliation"))
        if isinstance(index_entry.get("queue_reconciliation"), dict)
        else dict(index_entry.get("queueReconciliation"))
        if isinstance(index_entry.get("queueReconciliation"), dict)
        else {}
    )
    if expected_reconciliation != actual_reconciliation:
        return True
    return _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")) != _normalize_task_ids(
        index_entry.get("task_ids") or index_entry.get("taskIds")
    )


def _packet_reconciliation_mode(
    packet: dict[str, Any],
    *,
    diff_artifacts: Sequence[str],
    branch_ref: str,
    head_ref: str,
    worktree_state: dict[str, object],
) -> str:
    if not diff_artifacts:
        return "branch"
    if branch_ref and head_ref and branch_ref == head_ref:
        return "branch"
    if str(worktree_state.get("path") or "").strip():
        return "patch"
    return "patch"


def _packet_reconciliation_payload(item: dict[str, object], *, scanned_at: str) -> dict[str, object]:
    return {
        "scanned_at": scanned_at,
        "status": str(item.get("reconciliation_status") or "ok"),
        "mode": str(item.get("mode") or ""),
        "packet_state": str(item.get("packet_state") or ""),
        "branch_state": str(item.get("branch_state") or ""),
        "branch_index_state": str(item.get("branch_index_state") or ""),
        "patch_artifact_state": str(item.get("patch_artifact_state") or ""),
        "worktree_state": str(item.get("worktree_state") or ""),
        "worktree_path": str(item.get("worktree_path") or ""),
        "generated_worktree": bool(item.get("generated_worktree")),
        "diff_artifacts": list(item.get("diff_artifacts") or []),
        "missing_patch_artifacts": list(item.get("missing_patch_artifacts") or []),
        "issue_keys": list(item.get("issue_keys") or []),
        "issues": [dict(issue) for issue in item.get("issues") if isinstance(issue, dict)] if isinstance(item.get("issues"), list) else [],
    }


def _reconcile_packet_item(
    source_repo: Path,
    packet_path: Path,
    packet: dict[str, Any],
    *,
    index: dict[str, object],
) -> dict[str, object]:
    packet_id = str(packet.get("id") or packet_path.stem).strip() or packet_path.stem
    run_id = str(packet.get("run_id") or packet.get("runId") or "").strip()
    run_dir = _resolve_run_dir(source_repo, run_id)
    branch = str(packet.get("branch") or "").strip()
    base_ref = str(packet.get("base_ref") or packet.get("baseRef") or "").strip()
    head_ref = str(packet.get("head_ref") or packet.get("headRef") or "").strip()
    packet_status = str(packet.get("status") or "pr_queued").strip().lower() or "pr_queued"

    branch_ref = git_rev_parse_ref(source_repo, branch) if branch else ""
    base_ref_resolved = git_rev_parse_ref(source_repo, base_ref) if base_ref else ""
    head_ref_resolved = git_rev_parse_ref(source_repo, head_ref) if head_ref else ""
    worktree_state = generated_worktree_path_state(
        source_repo,
        run_dir=run_dir,
        worktree_dir=str(packet.get("worktree_dir") or packet.get("worktreeDir") or "").strip(),
    )
    diff_artifacts = _pr_queue_diff_artifact_candidates(packet, run_dir)
    mode = _packet_reconciliation_mode(
        packet,
        diff_artifacts=diff_artifacts,
        branch_ref=branch_ref,
        head_ref=head_ref_resolved,
        worktree_state=worktree_state,
    )

    issues: list[dict[str, object]] = []
    branch_state = "not_requested"
    if mode == "branch":
        if not branch:
            branch_state = "missing"
            issues.append(
                _reconciliation_issue(
                    "branch_ref",
                    "missing",
                    "Packet is missing its queued branch reference.",
                    details={"packet_id": packet_id},
                )
            )
        elif not branch_ref:
            branch_state = "missing"
            issues.append(
                _reconciliation_issue(
                    "branch_ref",
                    "missing",
                    "Queued branch no longer exists.",
                    path=branch,
                    details={"packet_id": packet_id, "head_ref": head_ref},
                )
            )
        elif head_ref_resolved and branch_ref != head_ref_resolved:
            branch_state = "stale"
            issues.append(
                _reconciliation_issue(
                    "branch_ref",
                    "stale",
                    "Queued branch no longer points at the recorded packet head.",
                    path=branch,
                    details={"packet_id": packet_id, "expected_head_ref": head_ref_resolved, "actual_head_ref": branch_ref},
                )
            )
        else:
            branch_state = "present"

    patch_artifact_state = "not_requested"
    missing_patch_artifacts: list[str] = []
    if mode == "patch":
        if diff_artifacts:
            for artifact in diff_artifacts:
                if not Path(artifact).expanduser().exists():
                    missing_patch_artifacts.append(artifact)
                    issues.append(
                        _reconciliation_issue(
                            "patch_artifact",
                            "missing",
                            "Referenced patch or diff artifact is missing.",
                            path=artifact,
                            details={"packet_id": packet_id},
                        )
                    )
            patch_artifact_state = "missing" if missing_patch_artifacts else "present"
        worktree_path = str(worktree_state.get("path") or "").strip()
        if worktree_path:
            if str(worktree_state.get("state") or "") == "deleted":
                issues.append(
                    _reconciliation_issue(
                        "generated_worktree",
                        "deleted",
                        "Generated worktree path no longer exists.",
                        path=worktree_path,
                        details={"packet_id": packet_id, "generated": bool(worktree_state.get("generated"))},
                    )
                )
        elif patch_artifact_state == "present":
            worktree_state = {
                **worktree_state,
                "state": "not_requested",
            }

    branch_index_entry = _find_branch_index_entry(index, packet_id)
    branch_index_state = "not_requested"
    if _packet_expected_index(packet):
        if branch_index_entry is None:
            branch_index_state = "missing"
            issues.append(
                _reconciliation_issue(
                    "branch_index",
                    "missing",
                    "branch_index.json does not contain this packet.",
                    path=pr_branch_index_path(source_repo).as_posix(),
                    details={"packet_id": packet_id},
                )
            )
        elif _branch_index_entry_is_stale(packet_path, packet, branch_index_entry):
            branch_index_state = "stale"
            issues.append(
                _reconciliation_issue(
                    "branch_index",
                    "stale",
                    "branch_index.json entry does not match the packet metadata.",
                    path=pr_branch_index_path(source_repo).as_posix(),
                    details={"packet_id": packet_id},
                )
            )
        else:
            branch_index_state = "present"

    issue_keys = _reconciliation_issue_keys(issues)
    return {
        "id": packet_id,
        "kind": "packet",
        "packet_state": "present",
        "packet_path": packet_path.resolve().as_posix(),
        "status": packet_status,
        "run_id": run_id,
        "task_ids": _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")),
        "branch": branch,
        "base_ref": base_ref,
        "head_ref": head_ref,
        "mode": mode,
        "branch_state": branch_state,
        "branch_index_state": branch_index_state,
        "patch_artifact_state": patch_artifact_state,
        "worktree_state": str(worktree_state.get("state") or "not_requested"),
        "worktree_path": str(worktree_state.get("path") or "").strip(),
        "generated_worktree": bool(worktree_state.get("generated")),
        "diff_artifacts": list(diff_artifacts),
        "missing_patch_artifacts": missing_patch_artifacts,
        "branch_index_entry": dict(branch_index_entry or {}),
        "branch_ref": branch_ref,
        "base_ref_resolved": base_ref_resolved,
        "head_ref_resolved": head_ref_resolved,
        "issues": issues,
        "issue_keys": issue_keys,
        "reconciliation_status": "issues_found" if issues else "ok",
        "ok": not issues,
    }


def _reconcile_orphan_index_entry(
    source_repo: Path,
    entry: dict[str, Any],
) -> dict[str, object]:
    packet_id = str(entry.get("id") or "").strip()
    packet_path_text = str(entry.get("packet_path") or entry.get("packetPath") or "").strip()
    packet_path = packet_path_text or pr_packet_path(source_repo, packet_id).as_posix()
    issues = [
        _reconciliation_issue(
            "packet",
            "missing",
            "branch_index.json entry points at a packet that no longer exists.",
            path=packet_path,
            details={"packet_id": packet_id},
        )
    ]
    return {
        "id": packet_id,
        "kind": "orphan_index_entry",
        "packet_state": "missing",
        "packet_path": packet_path,
        "status": str(entry.get("status") or "").strip().lower(),
        "run_id": str(entry.get("run_id") or entry.get("runId") or "").strip(),
        "task_ids": _normalize_task_ids(entry.get("task_ids") or entry.get("taskIds")),
        "branch": str(entry.get("branch") or "").strip(),
        "base_ref": str(entry.get("base_ref") or entry.get("baseRef") or "").strip(),
        "head_ref": str(entry.get("head_ref") or entry.get("headRef") or "").strip(),
        "mode": "index_only",
        "branch_state": "not_requested",
        "branch_index_state": "orphaned",
        "patch_artifact_state": "not_requested",
        "worktree_state": "not_requested",
        "worktree_path": "",
        "generated_worktree": False,
        "diff_artifacts": [],
        "missing_patch_artifacts": [],
        "branch_index_entry": dict(entry),
        "issues": issues,
        "issue_keys": _reconciliation_issue_keys(issues),
        "reconciliation_status": "issues_found",
        "ok": False,
    }


def reconcile_review_queue(
    source_repo: Path,
    *,
    apply: bool = False,
) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    lock_ctx = _pr_queue_write_lock(source_repo_path) if apply else nullcontext()
    with lock_ctx:
        index = load_branch_index(source_repo_path)
        queue_root = pr_queue_root(source_repo_path)
        scanned_at = now_iso()

        items: list[dict[str, object]] = []
        seen_packet_ids: set[str] = set()
        for packet_path in _reconciliation_packet_paths(source_repo_path, index):
            packet = _load_json_dict(packet_path)
            if not packet:
                continue
            item = _reconcile_packet_item(source_repo_path, packet_path, packet, index=index)
            seen_packet_ids.add(str(item.get("id") or ""))
            items.append(item)

        for entry in index.get("entries", []) if isinstance(index.get("entries"), list) else []:
            if not isinstance(entry, dict):
                continue
            packet_id = str(entry.get("id") or "").strip()
            if not packet_id or packet_id in seen_packet_ids:
                continue
            items.append(_reconcile_orphan_index_entry(source_repo_path, dict(entry)))

        items.sort(key=lambda item: (str(item.get("id") or ""), str(item.get("kind") or ""), str(item.get("packet_path") or "")))

        applied_updates = 0
        if apply:
            for item in items:
                if not item.get("issues"):
                    continue
                metadata = _packet_reconciliation_payload(item, scanned_at=scanned_at)
                packet_id = str(item.get("id") or "").strip()
                if item.get("kind") == "packet":
                    try:
                        _, packet_id_text, packet_path, packet = _require_review_packet(source_repo_path, packet_id)
                    except Exception:
                        continue
                    updated_packet = dict(packet)
                    packet_changed = updated_packet.get("queue_reconciliation") != metadata
                    updated_packet["queue_reconciliation"] = metadata
                    item_applied = packet_changed
                    updated_packet, index_entry = _write_packet_and_index_state(
                        source_repo_path,
                        packet_path,
                        updated_packet,
                        packet_id=packet_id_text,
                        updated_at=scanned_at,
                        status=str(updated_packet.get("status") or "").strip(),
                        approval_status=str(updated_packet.get("approval_status") or updated_packet.get("approvalStatus") or "").strip(),
                        validation_status=_normalize_packet_validation_status(
                            updated_packet.get("validation_status") or updated_packet.get("validationStatus") or updated_packet.get("status")
                        ),
                        extra={"queue_reconciliation": metadata},
                        fallback_branch_index_status=str(packet.get("branch_index_status") or "skipped"),
                    )
                    if index_entry is not None:
                        item_applied = True
                    if packet_changed or index_entry is not None:
                        applied_updates += 1
                elif item.get("kind") == "orphan_index_entry":
                    entry = item.get("branch_index_entry")
                    if isinstance(entry, dict) and _write_branch_index_entry_state(
                        source_repo_path,
                        entry,
                        updated_at=scanned_at,
                        extra={"queue_reconciliation": metadata},
                    ) is not None:
                        applied_updates += 1

        summary = {
            "total": len(items),
            "packets": len([item for item in items if item.get("kind") == "packet"]),
            "orphan_index_entries": len([item for item in items if item.get("kind") == "orphan_index_entry"]),
            "healthy": len([item for item in items if item.get("ok")]),
            "issue_count": sum(len(item.get("issues") or []) for item in items),
            "missing_patch_artifacts": sum(
                1 for item in items for issue in item.get("issues") or [] if issue.get("kind") == "patch_artifact" and issue.get("state") == "missing"
            ),
            "deleted_worktrees": sum(
                1 for item in items for issue in item.get("issues") or [] if issue.get("kind") == "generated_worktree" and issue.get("state") == "deleted"
            ),
            "stale_branches": sum(
                1 for item in items for issue in item.get("issues") or [] if issue.get("kind") == "branch_ref" and issue.get("state") in {"missing", "stale"}
            ),
            "stale_branch_index_entries": sum(
                1 for item in items for issue in item.get("issues") or [] if issue.get("kind") == "branch_index" and issue.get("state") == "stale"
            ),
            "missing_branch_index_entries": sum(
                1 for item in items for issue in item.get("issues") or [] if issue.get("kind") == "branch_index" and issue.get("state") == "missing"
            ),
            "missing_packets": sum(
                1 for item in items for issue in item.get("issues") or [] if issue.get("kind") == "packet" and issue.get("state") == "missing"
            ),
            "applied_updates": applied_updates,
        }
        issue_count = int(summary["issue_count"])
        if not items:
            state = "empty"
        elif issue_count:
            state = "issues_found"
        else:
            state = "ok"
        return {
            "ok": issue_count == 0,
            "state": state,
            "dry_run": not apply,
            "queue_root": queue_root.as_posix(),
            "branch_index_path": pr_branch_index_path(source_repo_path).as_posix(),
            "scanned_at": scanned_at,
            "items": items,
            "summary": summary,
        }


def _review_packet_paths(source_repo: Path) -> list[Path]:
    queue_root = pr_queue_root(source_repo)
    if not queue_root.exists() or not queue_root.is_dir():
        return []

    def _sort_key(path: Path) -> tuple[float, str]:
        try:
            stamp = float(path.stat().st_mtime)
        except Exception:
            stamp = 0.0
        return (stamp, path.name)

    packet_paths = [
        path
        for path in queue_root.glob("*.json")
        if path.is_file() and path.name != PR_QUEUE_INDEX_FILENAME
    ]
    try:
        index = load_branch_index(source_repo)
    except Exception:
        index = {"entries": []}

    ordered: list[Path] = []
    seen: set[str] = set()
    for entry in index.get("entries", []) if isinstance(index.get("entries"), list) else []:
        if not isinstance(entry, dict):
            continue
        packet_path_text = str(entry.get("packet_path") or entry.get("packetPath") or "").strip()
        packet_id = _packet_id_key(entry.get("id"))
        candidate: Path | None = None
        if packet_path_text:
            candidate = Path(packet_path_text).expanduser()
            try:
                candidate = candidate.resolve()
            except Exception:
                pass
        elif packet_id:
            candidate = pr_packet_path(source_repo, packet_id)
            try:
                candidate = candidate.resolve()
            except Exception:
                pass
        if candidate is None or not candidate.exists() or not candidate.is_file() or not _path_is_within(queue_root, candidate):
            continue
        try:
            key = candidate.resolve().as_posix()
        except Exception:
            key = candidate.as_posix()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(candidate)

    for path in sorted(packet_paths, key=_sort_key, reverse=True):
        try:
            key = path.resolve().as_posix()
        except Exception:
            key = path.as_posix()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _summary_artifact_name(packet: dict[str, Any]) -> str:
    candidates = [
        packet.get("validation_artifact_path"),
        packet.get("validationArtifactPath"),
    ]
    artifacts = packet.get("validation_artifacts")
    if not isinstance(artifacts, list):
        artifacts = packet.get("validationArtifacts")
    if isinstance(artifacts, list):
        candidates.extend(artifacts)
    for raw in candidates:
        text = str(raw or "").strip()
        if not text:
            continue
        name = Path(text).name.strip()
        if name:
            return name
    return ""


def _append_summary_ref(values: list[str], value: str) -> None:
    text = str(value or "").strip()
    if not text or text in values:
        return
    values.append(text)


def _review_packet_evidence_refs(packet: dict[str, Any], packet_id: str) -> list[str]:
    refs: list[str] = []
    run_id = str(packet.get("run_id") or packet.get("runId") or "").strip()
    if run_id:
        _append_summary_ref(refs, f"run:{run_id}")
    for task_id in _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds"))[:2]:
        _append_summary_ref(refs, f"task:{task_id}")
    artifact_name = _summary_artifact_name(packet)
    if artifact_name:
        _append_summary_ref(refs, f"artifact:{artifact_name}")
    if packet_id:
        _append_summary_ref(refs, f"pr:{packet_id}")
    return refs


def _review_packet_need(packet: dict[str, Any]) -> tuple[str, str]:
    packet_status = str(packet.get("status") or "pr_queued").strip().lower()
    approval_status = str(packet.get("approval_status") or packet.get("approvalStatus") or "").strip().lower()
    validation_status = _normalize_packet_validation_status(
        packet.get("validation_status") or packet.get("validationStatus") or packet.get("status")
    )
    if validation_status == "validation_passed" and approval_status != "approved":
        return "approval", "approval_required"
    if packet_status == "branch_metadata_missing":
        return "validation", "metadata_blocked"
    labels = {
        "validation_failed": "validation_failed",
        "blocked_env": "validation_blocked_env",
        "tests_skipped": "validation_skipped",
        "no_tests_found": "validation_no_tests",
        "validation_pending": "validation_pending",
    }
    return "validation", labels.get(validation_status, "validation_pending")


def _review_packet_merge_status(packet: dict[str, Any]) -> tuple[str, str]:
    packet_status = str(packet.get("status") or "pr_queued").strip().lower()
    approval_status = str(packet.get("approval_status") or packet.get("approvalStatus") or "").strip().lower()
    validation_status = _normalize_packet_validation_status(
        packet.get("validation_status") or packet.get("validationStatus") or packet.get("status")
    )
    merge_status = str(packet.get("merge_status") or packet.get("mergeStatus") or packet_status).strip().lower()
    rebase_status = str(packet.get("rebase_status") or packet.get("rebaseStatus") or "").strip().lower()
    discard_status = str(packet.get("discard_status") or packet.get("discardStatus") or "").strip().lower()
    if discard_status == "discarded" or packet_status == "discarded":
        return "discarded", "discarded"
    if merge_status == "merged" or packet_status == "merged":
        return "merged", "merged"
    if merge_status == "approved" or approval_status == "approved" or packet_status == "approved":
        return "approved", "approved"
    if rebase_status == "requested":
        return "rebase_requested", "rebase_requested"
    if validation_status != "validation_passed":
        return "blocked_on_validation", "blocked_on_validation"
    return "approval_required", "approval_required"


def _sanitize_telegram_packet_text(
    source_repo: Path,
    value: object,
    *,
    run_dir: Path | None = None,
    limit: int = 160,
) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    sanitized = sanitize_experience_lesson(
        redact_validation_summary(raw),
        repo_root=Path(source_repo).expanduser().resolve(),
        run_dir=run_dir,
    )
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" -:")
    if not sanitized:
        return ""
    if len(sanitized) > limit:
        return sanitized[: max(1, limit - 3)].rstrip() + "..."
    return sanitized


def _review_packet_artifact_refs(packet: dict[str, Any], *, limit: int = 3) -> list[str]:
    refs: list[str] = []
    candidates = [
        packet.get("validation_artifact_path"),
        packet.get("validationArtifactPath"),
        *(_normalize_str_list(packet.get("validation_artifacts") or packet.get("validationArtifacts"))),
    ]
    for raw in candidates:
        name = Path(str(raw or "").strip()).name.strip()
        ref = f"artifact:{name}" if name else ""
        if not ref or ref in refs:
            continue
        refs.append(ref)
        if len(refs) >= max(1, int(limit)):
            break
    return refs


def list_review_packets(source_repo: Path) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    queue_root = pr_queue_root(source_repo_path)
    items: list[dict[str, object]] = []
    for path in _review_packet_paths(source_repo_path):
        packet = _load_json_dict(path)
        if not packet:
            continue
        packet_id = str(packet.get("id") or path.stem).strip() or path.stem
        validation_status = _normalize_packet_validation_status(
            packet.get("validation_status") or packet.get("validationStatus") or packet.get("status")
        ) or "validation_pending"
        approval_status = str(packet.get("approval_status") or packet.get("approvalStatus") or "").strip().lower()
        need, need_label = _review_packet_need(packet)
        items.append(
            {
                "id": packet_id,
                "status": str(packet.get("status") or "pr_queued").strip().lower() or "pr_queued",
                "run_id": str(packet.get("run_id") or packet.get("runId") or "").strip(),
                "task_ids": _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")),
                "branch": str(packet.get("branch") or "").strip(),
                "validation_status": validation_status,
                "approval_status": approval_status,
                "need": need,
                "need_label": need_label,
                "updated_at": str(packet.get("updated_at") or packet.get("updatedAt") or "").strip(),
                "changed_file_count": len(_normalize_str_list(packet.get("changed_files") or packet.get("changedFiles"))),
                "qa_note_count": len(_normalize_str_list(packet.get("qa_notes") or packet.get("qaNotes"))),
            }
        )
    summary = {
        "total": len(items),
        "needs_validation": len([item for item in items if item.get("need") == "validation"]),
        "needs_approval": len([item for item in items if item.get("need") == "approval"]),
        "validation_passed": len([item for item in items if item.get("validation_status") == "validation_passed"]),
        "validation_pending": len([item for item in items if item.get("validation_status") == "validation_pending"]),
        "validation_failed": len([item for item in items if item.get("validation_status") == "validation_failed"]),
        "blocked_env": len([item for item in items if item.get("validation_status") == "blocked_env"]),
    }
    return {
        "ok": True,
        "state": "ready" if items else "empty",
        "queue_root": queue_root.as_posix(),
        "items": items,
        "summary": summary,
        "message": "" if items else "No queued PR packets.",
    }


def describe_review_packet(source_repo: Path, packet_id: str) -> dict[str, object]:
    source_repo_path, packet_id_text, packet_path, packet = _require_review_packet(source_repo, packet_id)
    validation_status = _normalize_packet_validation_status(
        packet.get("validation_status") or packet.get("validationStatus") or packet.get("status")
    ) or "validation_pending"
    approval_status = str(packet.get("approval_status") or packet.get("approvalStatus") or "").strip().lower()
    need, need_label = _review_packet_need(packet)
    index_entry = _find_branch_index_entry(load_branch_index(source_repo_path), packet_id_text)
    return {
        "ok": True,
        "id": packet_id_text,
        "packet_path": packet_path.as_posix(),
        "status": str(packet.get("status") or "pr_queued").strip().lower() or "pr_queued",
        "run_id": str(packet.get("run_id") or packet.get("runId") or "").strip(),
        "task_ids": _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")),
        "branch": str(packet.get("branch") or "").strip(),
        "base_ref": str(packet.get("base_ref") or packet.get("baseRef") or "").strip(),
        "head_ref": str(packet.get("head_ref") or packet.get("headRef") or "").strip(),
        "created_at": str(packet.get("created_at") or packet.get("createdAt") or "").strip(),
        "updated_at": str(packet.get("updated_at") or packet.get("updatedAt") or "").strip(),
        "validation_status": validation_status,
        "validation_reason": str(packet.get("validation_reason") or packet.get("validationReason") or "").strip(),
        "validation_detail": str(packet.get("validation_detail") or packet.get("validationDetail") or "").strip(),
        "validation_artifacts": _normalize_str_list(packet.get("validation_artifacts") or packet.get("validationArtifacts")),
        "approval_status": approval_status,
        "need": need,
        "need_label": need_label,
        "qa_notes": _normalize_str_list(packet.get("qa_notes") or packet.get("qaNotes")),
        "goal_trace": _normalize_list_value(packet.get("goal_trace") or packet.get("goalTrace")),
        "changed_files": _normalize_str_list(packet.get("changed_files") or packet.get("changedFiles")),
        "commits": _normalize_list_value(packet.get("commits")),
        "merge_preflight": dict(packet.get("merge_preflight") if isinstance(packet.get("merge_preflight"), dict) else packet.get("mergePreflight") or {}),
        "rebase_status": str(packet.get("rebase_status") or packet.get("rebaseStatus") or "").strip().lower(),
        "rebase_reason": str(packet.get("rebase_reason") or packet.get("rebaseReason") or "").strip(),
        "discard_status": str(packet.get("discard_status") or packet.get("discardStatus") or "").strip().lower(),
        "discard_reason": str(packet.get("discard_reason") or packet.get("discardReason") or "").strip(),
        "branch_index_status": str(packet.get("branch_index_status") or ("written" if index_entry else "missing")).strip(),
        "branch_index_entry": index_entry,
    }


def build_telegram_pr_queue_summary(source_repo: Path, *, limit: int = 3) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    attention_items: list[dict[str, object]] = []
    for path in _review_packet_paths(source_repo_path):
        packet = _load_json_dict(path)
        if not packet:
            continue
        packet_status = str(packet.get("status") or "pr_queued").strip().lower()
        if packet_status in {"approved", "discarded", "merged", "closed"}:
            continue
        packet_id = str(packet.get("id") or path.stem).strip() or path.stem
        validation_status = _normalize_packet_validation_status(
            packet.get("validation_status") or packet.get("validationStatus") or packet.get("status")
        )
        approval_status = str(packet.get("approval_status") or packet.get("approvalStatus") or "").strip().lower()
        need, label = _review_packet_need(packet)
        merge_status, merge_label = _review_packet_merge_status(packet)
        if validation_status == "validation_passed" and approval_status == "approved":
            continue
        attention_items.append(
            {
                "id": packet_id,
                "status": packet_status or "pr_queued",
                "need": need,
                "label": label,
                "run_id": str(packet.get("run_id") or packet.get("runId") or "").strip(),
                "task_ids": _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds")),
                "branch": str(packet.get("branch") or "").strip(),
                "validation_status": validation_status or "validation_pending",
                "approval_status": approval_status,
                "merge_status": merge_status,
                "merge_label": merge_label,
                "updated_at": str(packet.get("updated_at") or packet.get("updatedAt") or "").strip(),
                "evidence_refs": _review_packet_evidence_refs(packet, packet_id),
            }
        )
    needs_validation = len([item for item in attention_items if item.get("need") == "validation"])
    needs_approval = len([item for item in attention_items if item.get("need") == "approval"])
    max_items = max(1, int(limit))
    return {
        "total": len(attention_items),
        "needs_validation": needs_validation,
        "needs_approval": needs_approval,
        "items": attention_items[:max_items],
    }


def build_telegram_pr_queue_detail(source_repo: Path, packet_id: str) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    detail = describe_review_packet(source_repo_path, packet_id)
    run_id_text = str(detail.get("run_id") or "").strip()
    run_dir = _resolve_run_dir(source_repo_path, run_id_text)
    merge_status, merge_label = _review_packet_merge_status(detail)

    qa_notes: list[str] = []
    for raw_note in detail.get("qa_notes") if isinstance(detail.get("qa_notes"), list) else []:
        note = _sanitize_telegram_packet_text(source_repo_path, raw_note, run_dir=run_dir, limit=120)
        if note and note not in qa_notes:
            qa_notes.append(note)
        if len(qa_notes) >= 2:
            break

    return {
        "ok": True,
        "id": str(detail.get("id") or packet_id).strip(),
        "status": str(detail.get("status") or "pr_queued").strip().lower() or "pr_queued",
        "need": str(detail.get("need") or "").strip(),
        "need_label": str(detail.get("need_label") or "").strip(),
        "validation_status": str(detail.get("validation_status") or "validation_pending").strip() or "validation_pending",
        "validation_reason": _sanitize_telegram_packet_text(
            source_repo_path,
            detail.get("validation_reason"),
            run_dir=run_dir,
            limit=120,
        ),
        "validation_detail": _sanitize_telegram_packet_text(
            source_repo_path,
            detail.get("validation_detail"),
            run_dir=run_dir,
            limit=160,
        ),
        "approval_status": str(detail.get("approval_status") or "").strip().lower(),
        "merge_status": merge_status,
        "merge_label": merge_label,
        "run_id": run_id_text,
        "task_ids": _normalize_task_ids(detail.get("task_ids") or []),
        "branch": str(detail.get("branch") or "").strip(),
        "base_ref": str(detail.get("base_ref") or "").strip(),
        "head_ref": str(detail.get("head_ref") or "").strip(),
        "created_at": str(detail.get("created_at") or "").strip(),
        "updated_at": str(detail.get("updated_at") or "").strip(),
        "qa_notes": qa_notes,
        "validation_artifacts": _review_packet_artifact_refs(detail, limit=3),
        "evidence_refs": _review_packet_evidence_refs(detail, str(detail.get("id") or packet_id).strip())[:4],
        "branch_index_status": str(detail.get("branch_index_status") or "").strip(),
        "rebase_status": str(detail.get("rebase_status") or "").strip().lower(),
        "discard_status": str(detail.get("discard_status") or "").strip().lower(),
    }


def _pr_queue_validation_artifact_candidates(source_repo: Path, packet: dict[str, Any], packet_id: str) -> list[Path]:
    packet_id_text = str(packet_id or "").strip()
    candidates: list[Path] = []
    explicit_path = str(packet.get("validation_artifact_path") or packet.get("validationArtifactPath") or "").strip()
    if explicit_path:
        candidates.append(Path(explicit_path))
    run_id_text = str(packet.get("run_id") or packet.get("runId") or "").strip()
    if run_id_text:
        candidates.append(_resolve_run_dir(source_repo, run_id_text) / "pr_queue_validation" / packet_id_text / "attempt_01" / "validation.json")
    for raw_path in reversed(_normalize_list_value(packet.get("validation_artifacts") or packet.get("validationArtifacts"))):
        text = str(raw_path or "").strip()
        if text:
            candidates.append(Path(text))
    unique_candidates: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.as_posix()
        if key not in seen:
            seen.add(key)
            unique_candidates.append(candidate)
    return unique_candidates


def _load_pr_queue_validation_evidence(source_repo: Path, packet: dict[str, Any], packet_id: str) -> dict[str, object]:
    packet_validation_status = _normalize_packet_validation_status(packet.get("validation_status") or packet.get("validationStatus") or packet.get("status"))
    packet_validation_reason = str(
        packet.get("validation_reason")
        or packet.get("validationReason")
        or packet.get("reason")
        or ""
    ).strip()
    packet_validation_detail = str(
        packet.get("validation_detail")
        or packet.get("validationDetail")
        or packet.get("detail")
        or ""
    ).strip()
    validation_artifact_candidates = [candidate.as_posix() for candidate in _pr_queue_validation_artifact_candidates(source_repo, packet, packet_id)]

    for candidate in _pr_queue_validation_artifact_candidates(source_repo, packet, packet_id):
        if not candidate.exists() or not candidate.is_file():
            continue
        raw = _load_json_dict(candidate)
        if not raw:
            continue
        validation_status = _normalize_packet_validation_status(
            raw.get("validation_status") or raw.get("validationStatus") or raw.get("status")
        )
        if not validation_status:
            continue
        return {
            "source": "artifact",
            "artifact_path": candidate.as_posix(),
            "artifact": raw,
            "status": validation_status,
            "reason": str(
                raw.get("validation_reason")
                or raw.get("validationReason")
                or raw.get("reason")
                or raw.get("status")
                or ""
            ).strip(),
            "detail": str(
                raw.get("validation_detail")
                or raw.get("validationDetail")
                or raw.get("detail")
                or raw.get("summary")
                or raw.get("failure_summary")
                or raw.get("failureSummary")
                or ""
            ).strip(),
            "candidates": validation_artifact_candidates,
        }

    return {
        "source": "packet" if packet_validation_status else "missing",
        "artifact_path": "",
        "artifact": {},
        "status": packet_validation_status,
        "reason": packet_validation_reason or packet_validation_status,
        "detail": packet_validation_detail,
        "candidates": validation_artifact_candidates,
    }


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


def _pr_queue_merge_validation_error(
    packet_id: str,
    packet_path: Path,
    validation_evidence: dict[str, object],
) -> None:
    status = str(validation_evidence.get("status") or "").strip().lower()
    artifact_path = str(validation_evidence.get("artifact_path") or "").strip()
    details: dict[str, object] = {
        "packet_id": str(packet_id or "").strip(),
        "packet_path": packet_path.as_posix(),
        "validation_status": status,
        "validation_artifact_path": artifact_path,
        "validation_artifact_candidates": list(validation_evidence.get("candidates") or []),
        "validation_source": str(validation_evidence.get("source") or ""),
        "validation_reason": str(validation_evidence.get("reason") or "").strip(),
        "validation_detail": str(validation_evidence.get("detail") or "").strip(),
    }
    validation_messages = {
        "validation_pending": "PR packet validation is still pending.",
        "tests_skipped": "PR packet validation skipped tests.",
        "no_tests_found": "PR packet validation found no tests.",
        "validation_failed": "PR packet validation failed.",
        "blocked_env": "PR packet validation is blocked by the environment.",
    }
    if status in validation_messages:
        raise PrQueueMergeError(status, validation_messages[status], details=details, status_code=409)
    if status == "validation_passed":
        if str(validation_evidence.get("source") or "") != "artifact":
            raise PrQueueMergeError(
                "packet_stale",
                "PR packet validation evidence is missing or stale.",
                details=details,
                status_code=409,
            )
        return
    raise PrQueueMergeError(
        "packet_stale",
        "PR packet validation evidence is missing or stale.",
        details=details,
        status_code=409,
    )


def _record_pr_queue_merge_rejection(
    source_repo: Path,
    *,
    packet_id: str,
    packet_path: Path,
    packet: dict[str, Any],
    reason: str,
    message: str,
    details: dict[str, object] | None = None,
    validation_evidence: dict[str, object] | None = None,
    expected_phrase: str = "",
    provided_phrase: str = "",
) -> list[dict[str, Any]]:
    validation_evidence = dict(validation_evidence or {})
    validation_artifact_path = str(validation_evidence.get("artifact_path") or "").strip()
    validation_artifacts = _normalize_str_list(
        packet.get("validation_artifacts") or packet.get("validationArtifacts")
    )
    if validation_artifact_path and validation_artifact_path not in validation_artifacts:
        validation_artifacts.append(validation_artifact_path)
    extra_evidence = [
        {
            "kind": "validation_candidate",
            "path": str(candidate or "").strip(),
        }
        for candidate in list(validation_evidence.get("candidates") or [])
        if str(candidate or "").strip()
    ]
    metadata: dict[str, object] = {
        "message": str(message or "").strip(),
        "details": dict(details or {}),
    }
    if expected_phrase:
        metadata["expected_phrase"] = expected_phrase
    if provided_phrase:
        metadata["provided_phrase"] = provided_phrase
    if validation_evidence:
        metadata["validation_evidence"] = validation_evidence
    return _record_pr_queue_decision_signal(
        source_repo,
        packet_id=packet_id,
        packet=packet,
        signal_kind="merge",
        decision_status="rejected",
        reason=reason,
        packet_path=packet_path,
        primary_path=validation_artifact_path,
        artifact_paths=validation_artifacts,
        extra_evidence=extra_evidence,
        metadata=metadata,
    )


def _pr_queue_merge_preflight(
    source_repo: Path,
    packet: dict[str, Any],
    *,
    packet_id: str,
    validation_evidence: dict[str, object],
) -> tuple[dict[str, object], str, str, str, str, list[str], list[str]]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    packet_path = pr_packet_path(source_repo_path, packet_id)
    packet_source_repo_text = str(packet.get("source_repo") or packet.get("sourceRepo") or "").strip()
    if not packet_source_repo_text:
        raise PrQueueMergeError(
            "packet_stale",
            "PR packet is missing source repository metadata.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
            },
            status_code=409,
        )
    try:
        packet_source_repo = Path(packet_source_repo_text).expanduser().resolve()
    except Exception:
        packet_source_repo = Path(packet_source_repo_text).expanduser()
    if packet_source_repo != source_repo_path:
        raise PrQueueMergeError(
            "packet_stale",
            "PR packet source repository does not match the current repository.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "expected": source_repo_path.as_posix(),
                "actual": packet_source_repo.as_posix(),
            },
            status_code=409,
        )

    base_ref_text = str(packet.get("base_ref") or packet.get("baseRef") or "").strip()
    head_ref_text = str(packet.get("head_ref") or packet.get("headRef") or "").strip()
    branch_text = str(packet.get("branch") or "").strip()
    if not base_ref_text or not head_ref_text or not branch_text:
        missing = [name for name, value in (("base_ref", base_ref_text), ("head_ref", head_ref_text), ("branch", branch_text)) if not value]
        raise PrQueueMergeError(
            "packet_stale",
            "PR packet is missing recorded merge metadata.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "missing": missing,
            },
            status_code=409,
        )

    packet_status = str(packet.get("status") or "").strip().lower()
    if packet_status in {"discarded", "merged"}:
        raise PrQueueMergeError(
            "packet_stale",
            "PR packet has already been finalized.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "status": packet_status,
            },
            status_code=409,
        )

    source_repo_state = git_repo_state(source_repo_path)
    if source_repo_state != "clean":
        raise PrQueueMergeError(
            "source_dirty",
            "Source repository must be clean before approving the PR packet.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "source_repo": source_repo_path.as_posix(),
                "source_repo_state": source_repo_state,
            },
            status_code=409,
        )

    current_source_head = git_head(source_repo_path)
    recorded_preflight = packet.get("merge_preflight") if isinstance(packet.get("merge_preflight"), dict) else packet.get("mergePreflight")
    if not isinstance(recorded_preflight, dict):
        recorded_preflight = {}
    expected_source_head = str(
        recorded_preflight.get("source_head_after")
        or recorded_preflight.get("sourceHeadAfter")
        or recorded_preflight.get("source_head_before")
        or recorded_preflight.get("sourceHeadBefore")
        or packet.get("source_head_after")
        or packet.get("sourceHeadAfter")
        or packet.get("source_head_before")
        or packet.get("sourceHeadBefore")
        or ""
    ).strip()
    if not expected_source_head:
        raise PrQueueMergeError(
            "packet_stale",
            "PR packet is missing recorded source head metadata.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
            },
            status_code=409,
        )
    if current_source_head != expected_source_head:
        raise PrQueueMergeError(
            "packet_stale",
            "Source HEAD no longer matches the recorded packet metadata.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "expected_source_head": expected_source_head,
                "actual_source_head": current_source_head,
            },
            status_code=409,
        )

    resolved_base_ref = git_rev_parse_ref(source_repo_path, base_ref_text)
    resolved_head_ref = git_rev_parse_ref(source_repo_path, head_ref_text)
    if not resolved_base_ref or not resolved_head_ref:
        raise PrQueueMergeError(
            "packet_stale",
            "Recorded base or head ref can no longer be resolved.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "base_ref": base_ref_text,
                "head_ref": head_ref_text,
            },
            status_code=409,
        )

    recorded_changed_files = _normalize_str_list(packet.get("changed_files") or packet.get("changedFiles"))
    try:
        current_changed_files = list(git_changed_files(source_repo_path, resolved_base_ref, resolved_head_ref))
    except Exception as ex:
        raise PrQueueMergeError(
            "packet_stale",
            "Recorded changed files can no longer be recomputed.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "base_ref": resolved_base_ref,
                "head_ref": resolved_head_ref,
                "error": f"{type(ex).__name__}: {str(ex).strip() or type(ex).__name__}",
            },
            status_code=409,
        ) from ex

    if current_changed_files != recorded_changed_files:
        raise PrQueueMergeError(
            "packet_stale",
            "Recorded changed files no longer match the packet diff.",
            details={
                "packet_id": packet_id,
                "packet_path": packet_path.as_posix(),
                "base_ref": resolved_base_ref,
                "head_ref": resolved_head_ref,
                "recorded_changed_files": recorded_changed_files,
                "current_changed_files": current_changed_files,
            },
            status_code=409,
        )

    validation_status = str(validation_evidence.get("status") or "").strip().lower()
    validation_artifact_path = str(validation_evidence.get("artifact_path") or "").strip()
    merge_preflight = {
        "base_ref": resolved_base_ref,
        "baseRef": resolved_base_ref,
        "head_ref": resolved_head_ref,
        "headRef": resolved_head_ref,
        "branch": branch_text,
        "source_head_before": expected_source_head,
        "sourceHeadBefore": expected_source_head,
        "source_head_after": current_source_head,
        "sourceHeadAfter": current_source_head,
        "source_head": current_source_head,
        "sourceHead": current_source_head,
        "source_main_mutated": False,
        "sourceMainMutated": False,
        "source_repo_state": source_repo_state,
        "sourceRepoState": source_repo_state,
        "source_repo_dirty": False,
        "sourceRepoDirty": False,
        "recorded_changed_files": recorded_changed_files,
        "recordedChangedFiles": recorded_changed_files,
        "current_changed_files": current_changed_files,
        "currentChangedFiles": current_changed_files,
        "changed_files_match": True,
        "changedFilesMatch": True,
        "validation_status": validation_status,
        "validationStatus": validation_status,
        "validation_artifact_path": validation_artifact_path,
        "validationArtifactPath": validation_artifact_path,
        "validation_source": str(validation_evidence.get("source") or ""),
        "validationSource": str(validation_evidence.get("source") or ""),
        "validation_reason": str(validation_evidence.get("reason") or "").strip(),
        "validationReason": str(validation_evidence.get("reason") or "").strip(),
        "validation_detail": str(validation_evidence.get("detail") or "").strip(),
        "validationDetail": str(validation_evidence.get("detail") or "").strip(),
        "packet_merge_preflight": recorded_preflight,
        "packetMergePreflight": recorded_preflight,
    }
    return merge_preflight, resolved_base_ref, resolved_head_ref, current_source_head, source_repo_state, current_changed_files, recorded_changed_files


async def validate_review_packet_async(
    source_repo: Path,
    packet_id: str,
    *,
    stop_path: Path | None = None,
    full: bool = False,
) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    packet_id_text = str(packet_id or "").strip()
    if not packet_id_text:
        raise RuntimeError("Packet id is required for PR queue validation.")

    packet_path = pr_packet_path(source_repo_path, packet_id_text)
    packet = load_review_packet(source_repo_path, packet_id_text)
    if not packet:
        raise FileNotFoundError(f"PR packet not found: {packet_path}")
    packet_revision = _packet_revision_token(packet)
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
    if full:
        config = dict(config)
        config["build_enabled"] = True
        config["run_tests"] = True
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
        "mode": "full" if full else "configured",
        "full": bool(full),
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

    task_ids_value = _normalize_task_ids(packet.get("task_ids") or packet.get("taskIds"))
    record_validation_experiences(
        source_repo_path,
        source_kind="pr_queue_validation",
        run_id=run_id_text,
        task_id=task_ids_value[0] if task_ids_value else "",
        task_ids=task_ids_value,
        packet_id=packet_id_text,
        validation_status=validation_status,
        validation_reason=validation_reason,
        validation_detail=validation_detail,
        validation_artifact_path=summary_path.as_posix(),
        validation_artifacts=validation_artifacts,
        validation_records=validation_records,
    )

    with _pr_queue_write_lock(source_repo_path):
        latest_packet = load_review_packet(source_repo_path, packet_id_text)
        if not latest_packet:
            raise FileNotFoundError(f"PR packet not found: {packet_path}")
        if _packet_revision_token(latest_packet) != packet_revision:
            raise RuntimeError(f"PR packet {packet_id_text} changed while validation was running. Rerun validation.")
        updated_packet = _update_packet_validation_metadata(
            latest_packet,
            status=validation_status,
            reason=validation_reason,
            detail=validation_detail,
            artifact_path=summary_path,
            artifacts=validation_artifacts,
            updated_at=ended_at,
        )
        updated_packet, index_entry = _write_packet_and_index_state(
            source_repo_path,
            packet_path,
            updated_packet,
            packet_id=packet_id_text,
            updated_at=ended_at,
            status=str(updated_packet.get("status") or "").strip(),
            validation_status=validation_status,
            fallback_branch_index_status=str(latest_packet.get("branch_index_status") or "skipped"),
        )

    _record_pr_queue_decision_signal(
        source_repo_path,
        packet_id=packet_id_text,
        packet=updated_packet,
        signal_kind="validate",
        decision_status=validation_status,
        reason=validation_reason,
        packet_path=packet_path,
        primary_path=summary_path.as_posix(),
        artifact_paths=validation_artifacts,
        metadata={
            "validation_summary": validation_summary,
            "validation_plan": validation_plan,
            "summary_path": summary_path.as_posix(),
            "source_head_before": source_head_before,
            "source_head_after": source_head_after,
            "source_repo_state_before": source_repo_state_before,
            "source_repo_state_after": source_repo_state_after,
            "source_main_mutated": source_main_mutated,
            "worktree_dir": worktree_dir.as_posix(),
            "worktree_created": worktree_created,
            "worktree_removed": worktree_removed,
            "cleanup_error": cleanup_error,
        },
        recorded_at=ended_at,
    )

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
    full: bool = False,
) -> dict[str, object]:
    return asyncio.run(
        validate_review_packet_async(
            source_repo,
            packet_id,
            stop_path=stop_path,
            full=full,
        )
    )


def discard_review_packet(
    source_repo: Path,
    packet_id: str,
    *,
    reason: str = "operator_discarded",
) -> dict[str, object]:
    with _pr_queue_write_lock(source_repo):
        source_repo_path, packet_id_text, packet_path, packet = _require_review_packet(source_repo, packet_id)
        packet_status = str(packet.get("status") or "").strip().lower()
        if packet_status == "merged":
            raise RuntimeError("PR packet has already been merged.")
        discard_reason = str(reason or "operator_discarded").strip() or "operator_discarded"
        now = now_iso()
        updated_packet = dict(packet)
        updated_packet["status"] = "discarded"
        updated_packet["discard_status"] = "discarded"
        updated_packet["discardStatus"] = "discarded"
        updated_packet["discard_reason"] = discard_reason
        updated_packet["discardReason"] = discard_reason
        updated_packet["discarded_at"] = now
        updated_packet["discardedAt"] = now
        updated_packet["updated_at"] = now

        updated_packet, index_entry = _write_packet_and_index_state(
            source_repo_path,
            packet_path,
            updated_packet,
            packet_id=packet_id_text,
            updated_at=now,
            status="discarded",
            validation_status=_normalize_packet_validation_status(
                updated_packet.get("validation_status") or updated_packet.get("validationStatus")
            ),
            extra={
                "discard_status": "discarded",
                "discard_reason": discard_reason,
            },
            fallback_branch_index_status=str(packet.get("branch_index_status") or "skipped"),
        )
    signal_rows = _record_pr_queue_decision_signal(
        source_repo_path,
        packet_id=packet_id_text,
        packet=updated_packet,
        signal_kind="discard",
        decision_status="discarded",
        reason=discard_reason,
        packet_path=packet_path,
        artifact_paths=_normalize_str_list(updated_packet.get("validation_artifacts") or updated_packet.get("validationArtifacts")),
        metadata={
            "discard_status": "discarded",
            "discarded_at": now,
        },
        recorded_at=now,
    )
    return {
        "ok": True,
        "action": "discard",
        "status": "discarded",
        "packet_id": packet_id_text,
        "packet_path": packet_path.as_posix(),
        "packet": updated_packet,
        "branch_index_entry": index_entry,
        "signals": signal_rows,
    }


def rebase_review_packet(
    source_repo: Path,
    packet_id: str,
    *,
    reason: str = "operator_rebase_requested",
) -> dict[str, object]:
    with _pr_queue_write_lock(source_repo):
        source_repo_path, packet_id_text, packet_path, packet = _require_review_packet(source_repo, packet_id)
        packet_status = str(packet.get("status") or "").strip().lower()
        if packet_status in {"discarded", "merged"}:
            raise RuntimeError("PR packet has already been finalized.")
        rebase_reason = str(reason or "operator_rebase_requested").strip() or "operator_rebase_requested"
        now = now_iso()
        updated_packet = dict(packet)
        updated_packet["status"] = "review_required"
        updated_packet["approval_status"] = "rebase_requested"
        updated_packet["approvalStatus"] = "rebase_requested"
        updated_packet["validation_status"] = "validation_pending"
        updated_packet["validationStatus"] = "validation_pending"
        updated_packet["validation_reason"] = "rebase_requested"
        updated_packet["validationReason"] = "rebase_requested"
        updated_packet["validation_detail"] = "Rebase requested; rerun validation after updating the branch."
        updated_packet["validationDetail"] = "Rebase requested; rerun validation after updating the branch."
        updated_packet["rebase_status"] = "requested"
        updated_packet["rebaseStatus"] = "requested"
        updated_packet["rebase_reason"] = rebase_reason
        updated_packet["rebaseReason"] = rebase_reason
        updated_packet["rebase_requested_at"] = now
        updated_packet["rebaseRequestedAt"] = now
        updated_packet["updated_at"] = now

        updated_packet, index_entry = _write_packet_and_index_state(
            source_repo_path,
            packet_path,
            updated_packet,
            packet_id=packet_id_text,
            updated_at=now,
            status="review_required",
            approval_status="rebase_requested",
            validation_status="validation_pending",
            extra={
                "rebase_status": "requested",
                "rebase_reason": rebase_reason,
            },
            fallback_branch_index_status=str(packet.get("branch_index_status") or "skipped"),
        )
    signal_rows = _record_pr_queue_decision_signal(
        source_repo_path,
        packet_id=packet_id_text,
        packet=updated_packet,
        signal_kind="rebase",
        decision_status="requested",
        reason=rebase_reason,
        packet_path=packet_path,
        artifact_paths=_normalize_str_list(updated_packet.get("validation_artifacts") or updated_packet.get("validationArtifacts")),
        metadata={
            "rebase_status": "requested",
            "validation_status": "validation_pending",
            "recorded_at": now,
        },
        recorded_at=now,
    )
    return {
        "ok": True,
        "action": "rebase",
        "status": "review_required",
        "packet_id": packet_id_text,
        "packet_path": packet_path.as_posix(),
        "packet": updated_packet,
        "branch_index_entry": index_entry,
        "signals": signal_rows,
    }


def merge_review_packet(
    source_repo: Path,
    packet_id: str,
    *,
    approval_phrase: str,
) -> dict[str, object]:
    source_repo_path = Path(source_repo).expanduser().resolve()
    packet_id_text = str(packet_id or "").strip()
    if not packet_id_text:
        raise PrQueueMergeError(
            "packet_stale",
            "PR packet id is required.",
            details={"packet_id": packet_id_text},
            status_code=400,
            status="invalid_request",
        )
    with _pr_queue_write_lock(source_repo_path):
        packet_path = pr_packet_path(source_repo_path, packet_id_text)
        if not packet_path.exists() or not packet_path.is_file():
            raise PrQueueMergeError(
                "packet_missing",
                "PR packet not found.",
                details={
                    "packet_id": packet_id_text,
                    "packet_path": packet_path.as_posix(),
                },
                status_code=404,
            )

        packet = load_review_packet(source_repo_path, packet_id_text)
        if not packet:
            raise PrQueueMergeError(
                "packet_stale",
                "PR packet file is empty or malformed.",
                details={
                    "packet_id": packet_id_text,
                    "packet_path": packet_path.as_posix(),
                },
                status_code=409,
            )

        expected_phrase = pr_queue_merge_confirmation_phrase(packet_id_text)
        provided_phrase = str(approval_phrase or "").strip()
        if not provided_phrase:
            _record_pr_queue_merge_rejection(
                source_repo_path,
                packet_id=packet_id_text,
                packet_path=packet_path,
                packet=packet,
                reason="approval_required",
                message="A merge approval phrase is required.",
                details={
                    "packet_id": packet_id_text,
                    "packet_path": packet_path.as_posix(),
                    "expected_phrase": expected_phrase,
                },
                expected_phrase=expected_phrase,
            )
            raise PrQueueMergeError(
                "approval_required",
                "A merge approval phrase is required.",
                details={
                    "packet_id": packet_id_text,
                    "packet_path": packet_path.as_posix(),
                    "expected_phrase": expected_phrase,
                },
                status_code=400,
                status="invalid_request",
            )
        if provided_phrase != expected_phrase:
            _record_pr_queue_merge_rejection(
                source_repo_path,
                packet_id=packet_id_text,
                packet_path=packet_path,
                packet=packet,
                reason="approval_mismatch",
                message="The merge approval phrase did not match.",
                details={
                    "packet_id": packet_id_text,
                    "packet_path": packet_path.as_posix(),
                    "expected_phrase": expected_phrase,
                    "provided_phrase": provided_phrase,
                },
                expected_phrase=expected_phrase,
                provided_phrase=provided_phrase,
            )
            raise PrQueueMergeError(
                "approval_mismatch",
                "The merge approval phrase did not match.",
                details={
                    "packet_id": packet_id_text,
                    "packet_path": packet_path.as_posix(),
                    "expected_phrase": expected_phrase,
                    "provided_phrase": provided_phrase,
                },
                status_code=400,
                status="invalid_request",
            )

        validation_evidence = _load_pr_queue_validation_evidence(source_repo_path, packet, packet_id_text)
        try:
            _pr_queue_merge_validation_error(packet_id_text, packet_path, validation_evidence)

            (
                merge_preflight,
                resolved_base_ref,
                resolved_head_ref,
                current_source_head,
                source_repo_state,
                current_changed_files,
                recorded_changed_files,
            ) = _pr_queue_merge_preflight(
                source_repo_path,
                packet,
                packet_id=packet_id_text,
                validation_evidence=validation_evidence,
            )
        except PrQueueMergeError as ex:
            _record_pr_queue_merge_rejection(
                source_repo_path,
                packet_id=packet_id_text,
                packet_path=packet_path,
                packet=packet,
                reason=ex.code,
                message=str(ex),
                details=ex.details,
                validation_evidence=validation_evidence,
            )
            raise

        now = now_iso()
        validation_artifact_path = str(validation_evidence.get("artifact_path") or "").strip()
        validation_artifacts_value = _normalize_str_list(packet.get("validation_artifacts") or packet.get("validationArtifacts"))
        if validation_artifact_path and validation_artifact_path not in validation_artifacts_value:
            validation_artifacts_value.append(validation_artifact_path)
        if not validation_artifacts_value and validation_artifact_path:
            validation_artifacts_value = [validation_artifact_path]

        updated_packet = _update_packet_validation_metadata(
            packet,
            status="validation_passed",
            reason=str(validation_evidence.get("reason") or "validation_passed").strip() or "validation_passed",
            detail=str(validation_evidence.get("detail") or "").strip(),
            artifact_path=Path(validation_artifact_path or packet_path),
            artifacts=validation_artifacts_value,
            updated_at=now,
        )

        approval_record = {
            "status": "approved",
            "approval_status": "approved",
            "approvalStatus": "approved",
            "packet_id": packet_id_text,
            "packetId": packet_id_text,
            "required_phrase": expected_phrase,
            "requiredPhrase": expected_phrase,
            "confirmed_at": now,
            "confirmedAt": now,
            "matched": True,
            "matchedPhrase": True,
        }
        merge_outcome = {
            "status": "approved",
            "approval_status": "approved",
            "approvalStatus": "approved",
            "approved_at": now,
            "approvedAt": now,
            "recorded_at": now,
            "recordedAt": now,
            "source_repo_state": source_repo_state,
            "sourceRepoState": source_repo_state,
            "source_head": current_source_head,
            "sourceHead": current_source_head,
            "source_main_mutated": False,
            "sourceMainMutated": False,
            "committed": False,
            "applied": False,
            "source_repo_mutated": False,
            "sourceRepoMutated": False,
            "validation_status": str(validation_evidence.get("status") or ""),
            "validationStatus": str(validation_evidence.get("status") or ""),
            "validation_artifact_path": validation_artifact_path,
            "validationArtifactPath": validation_artifact_path,
            "validation_reason": str(validation_evidence.get("reason") or "").strip(),
            "validationReason": str(validation_evidence.get("reason") or "").strip(),
            "validation_detail": str(validation_evidence.get("detail") or "").strip(),
            "validationDetail": str(validation_evidence.get("detail") or "").strip(),
            "preflight": merge_preflight,
            "preflightStatus": "passed",
            "preflight_status": "passed",
            "base_ref": resolved_base_ref,
            "baseRef": resolved_base_ref,
            "head_ref": resolved_head_ref,
            "headRef": resolved_head_ref,
            "branch": str(packet.get("branch") or "").strip(),
            "recorded_changed_files": recorded_changed_files,
            "recordedChangedFiles": recorded_changed_files,
            "current_changed_files": current_changed_files,
            "currentChangedFiles": current_changed_files,
            "changed_files_match": True,
            "changedFilesMatch": True,
            "detail": "Merge approval recorded without auto-committing source changes.",
        }

        updated_packet["status"] = "approved"
        updated_packet["approval_status"] = "approved"
        updated_packet["approvalStatus"] = "approved"
        updated_packet["approved_at"] = now
        updated_packet["approvedAt"] = now
        updated_packet["merge_recorded_at"] = now
        updated_packet["mergeRecordedAt"] = now
        updated_packet["merge_status"] = "approved"
        updated_packet["mergeStatus"] = "approved"
        updated_packet["approval"] = approval_record
        updated_packet["merge_outcome"] = merge_outcome
        updated_packet["mergeOutcome"] = merge_outcome
        updated_packet["merge_preflight"] = merge_preflight
        updated_packet["mergePreflight"] = merge_preflight
        updated_packet["validation_artifact_path"] = validation_artifact_path
        updated_packet["validationArtifactPath"] = validation_artifact_path
        updated_packet["validation_artifacts"] = validation_artifacts_value
        updated_packet["validationArtifacts"] = validation_artifacts_value
        updated_packet["updated_at"] = now

        updated_packet, index_entry = _write_packet_and_index_state(
            source_repo_path,
            packet_path,
            updated_packet,
            packet_id=packet_id_text,
            updated_at=now,
            status="approved",
            approval_status="approved",
            validation_status="validation_passed",
            fallback_branch_index_status=str(packet.get("branch_index_status") or "skipped"),
        )

    _record_pr_queue_decision_signal(
        source_repo_path,
        packet_id=packet_id_text,
        packet=updated_packet,
        signal_kind="merge",
        decision_status="approved",
        reason="approval_confirmed",
        packet_path=packet_path,
        primary_path=validation_artifact_path,
        artifact_paths=validation_artifacts_value,
        metadata={
            "approval": approval_record,
            "merge_outcome": merge_outcome,
            "merge_preflight": merge_preflight,
            "source_head": current_source_head,
            "source_repo_state": source_repo_state,
        },
        recorded_at=now,
    )

    return {
        "ok": True,
        "action": "merge",
        "status": "approved",
        "approval_status": "approved",
        "packet_id": packet_id_text,
        "packet_path": packet_path.as_posix(),
        "packet": updated_packet,
        "approval": approval_record,
        "merge_preflight": merge_preflight,
        "merge_outcome": merge_outcome,
        "validation_status": "validation_passed",
        "validation_artifact_path": validation_artifact_path,
        "validation_artifacts": validation_artifacts_value,
        "source_head": current_source_head,
        "source_repo_state": source_repo_state,
        "source_main_mutated": False,
        "summary": merge_outcome,
    }
