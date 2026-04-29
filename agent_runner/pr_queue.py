from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Sequence

from .gitops import git_changed_files
from .utils import atomic_write_json, now_iso, run_cmd


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
    validation_artifacts_value = _normalize_str_list(validation_artifacts)
    qa_notes_value = _normalize_str_list(qa_notes)
    goal_trace_value = _normalize_list_value(goal_trace)
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
        "validation_status": str(validation_status or "").strip() or "validation_pending",
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
