from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .runtime_contract import ATTEMPT_FINISHED_MARKER, ATTEMPT_STARTED_MARKER
from .utils import run_cmd, now_iso, safe_write_text, eprint


class WorktreeSafetyError(RuntimeError):
    """Structured safety failure for worktree reuse/merge preflight."""

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


class WorktreeCleanupError(RuntimeError):
    """Structured failure raised when isolated worktree cleanup cannot finish."""

    def __init__(
        self,
        message: str,
        *,
        cleanup_path: str = "",
        details: dict[str, object] | None = None,
        attempts: list[dict[str, object]] | None = None,
        status_code: int = 409,
        status: str = "conflict",
    ) -> None:
        super().__init__(message)
        self.code = "worktree_cleanup_failed"
        self.cleanup_path = str(cleanup_path or "").strip()
        self.cleanup_message = str(message)
        self.details = dict(details or {})
        self.attempts = [dict(attempt) for attempt in attempts or []]
        if self.cleanup_path:
            self.details.setdefault("path", self.cleanup_path)
        if self.attempts:
            self.details.setdefault("attempts", self.attempts)
        self.status_code = int(status_code)
        self.status = str(status)


WORKTREE_DIAGNOSTIC_CATEGORY_ORDER = (
    "active",
    "pending",
    "stale",
    "orphaned",
    "cleanup_failed",
    "missing_patch",
)

PYTEST_CACHE_TEMP_PREFIX = "pytest-cache-files-"

WORKTREE_RESOLUTION_ACTION_ORDER = (
    "source_safe_discard",
    "generated_worktree_remove",
    "stale_marker_prune",
    "cleanup_failed_reconcile",
)

WORKTREE_CLEANUP_FAILED_STATUS_MAP: dict[str, dict[str, str]] = {
    "applied_cleanup_failed": {
        "artifact_name": "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
        "final_status": "applied",
        "final_artifact_name": "WORKTREE_MERGE_APPLIED.json",
    },
    "discard_cleanup_failed": {
        "artifact_name": "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json",
        "final_status": "discarded",
        "final_artifact_name": "WORKTREE_MERGE_DISCARDED.json",
    },
}


def _worktree_normalize_diagnostic_category(value: object) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text == "cleanupfailed":
        text = "cleanup_failed"
    elif text == "missingpatch":
        text = "missing_patch"
    return text


def _worktree_normalize_diagnostic_categories(value: object) -> list[str]:
    raw_values: list[object]
    if value is None:
        raw_values = []
    elif isinstance(value, str):
        raw_values = [part for part in re.split(r"[\s,]+", value) if part]
    else:
        try:
            raw_values = list(value)  # type: ignore[arg-type]
        except TypeError:
            raw_values = [value]
    requested = {
        _worktree_normalize_diagnostic_category(item)
        for item in raw_values
        if _worktree_normalize_diagnostic_category(item) in WORKTREE_DIAGNOSTIC_CATEGORY_ORDER
    }
    return [category for category in WORKTREE_DIAGNOSTIC_CATEGORY_ORDER if category in requested]


def _worktree_resolution_action(
    kind: str,
    status: str,
    *,
    path: str = "",
    detail: str = "",
) -> dict[str, object]:
    action = {
        "kind": str(kind),
        "status": str(status),
    }
    if path:
        action["path"] = str(path)
    if detail:
        action["detail"] = str(detail)
    return action


def worktree_resolution_actions(
    status: str,
    *,
    source_repo: str = "",
    worktree_dir: str = "",
    cleanup_path: str = "",
    pending_paths: Sequence[str] | None = None,
    cleanup_message: str = "",
    artifact_path: str = "",
    reconciliation: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    normalized = str(status or "").strip().lower()
    marker_path = next((str(path).strip() for path in pending_paths or [] if str(path).strip()), "")
    cleanup_target = str(cleanup_path or worktree_dir).strip()
    worktree_target = str(worktree_dir or cleanup_target).strip()
    source_repo_text = str(source_repo).strip()
    artifact_path_text = str(artifact_path).strip()
    reconciliation_data = dict(reconciliation or {})
    residual_directory = bool(reconciliation_data.get("residual_directory"))
    actions: list[dict[str, object]] = []

    if normalized in {"discarded", "discard_cleanup_failed"}:
        actions.append(
            _worktree_resolution_action(
                "source_safe_discard",
                "done",
                path=source_repo_text,
                detail="Discard recorded without changing source repository files.",
            )
        )

    if normalized in {"applied", "discarded", "applied_cleanup_failed", "discard_cleanup_failed"}:
        remove_status = "failed" if normalized in WORKTREE_CLEANUP_FAILED_STATUS_MAP else "done"
        remove_detail = (
            cleanup_message or "Generated worktree removal is still blocked."
            if remove_status == "failed"
            else "Generated worktree removed."
        )
        actions.append(
            _worktree_resolution_action(
                "generated_worktree_remove",
                remove_status,
                path=worktree_target,
                detail=remove_detail,
            )
        )
        actions.append(
            _worktree_resolution_action(
                "stale_marker_prune",
                "done",
                path=marker_path,
                detail="Pending marker paths were cleared after the worktree result was finalized.",
            )
        )

    if normalized == "stale_pending_marker":
        actions.append(
            _worktree_resolution_action(
                "stale_marker_prune",
                "required",
                path=marker_path,
                detail="Remove or repair the stale pending marker only after verifying the patch metadata.",
            )
        )

    if normalized == "orphaned_worktree":
        actions.append(
            _worktree_resolution_action(
                "generated_worktree_remove",
                "required",
                path=worktree_target,
                detail="Remove the orphaned generated worktree after verifying no active marker still references it.",
            )
        )

    if normalized in WORKTREE_CLEANUP_FAILED_STATUS_MAP:
        blocking_paths = [
            str(path).strip()
            for path in reconciliation_data.get("blocking_paths", [])
            if str(path).strip()
        ]
        reconcile_status = "done" if reconciliation_data.get("reconciled") else "required"
        if reconcile_status == "done":
            reconcile_detail = "Cleanup-failed artifact reconciled after the worktree path and marker state were cleared."
        elif residual_directory and blocking_paths:
            reconcile_detail = f"Git no longer registers the worktree; residual directory remains at: {', '.join(blocking_paths)}"
        elif residual_directory:
            reconcile_detail = "Git no longer registers the worktree; residual directory cleanup is still pending."
        elif blocking_paths:
            reconcile_detail = f"Still blocked by: {', '.join(blocking_paths)}"
        else:
            reconcile_detail = "Keep the cleanup-failed artifact visible until the worktree path and marker state are reconciled."
        actions.append(
            _worktree_resolution_action(
                "cleanup_failed_reconcile",
                reconcile_status,
                path=artifact_path_text,
                detail=reconcile_detail,
            )
        )

    reconciled_from = str(
        reconciliation_data.get("reconciled_from")
        or reconciliation_data.get("cleanup_reconciled_from")
        or ""
    ).strip().lower()
    if normalized in {"applied", "discarded"} and reconciled_from in WORKTREE_CLEANUP_FAILED_STATUS_MAP:
        actions.append(
            _worktree_resolution_action(
                "cleanup_failed_reconcile",
                "done",
                path=artifact_path_text,
                detail="Cleanup-failed artifact was reconciled after the generated worktree path and marker state cleared.",
            )
        )

    ordered_actions: list[dict[str, object]] = []
    for action_kind in WORKTREE_RESOLUTION_ACTION_ORDER:
        for action in actions:
            if action.get("kind") == action_kind:
                ordered_actions.append(action)
    return ordered_actions


def _worktree_diagnostic_category_counts(*collections: Sequence[dict[str, object]]) -> dict[str, int]:
    counts = {category: 0 for category in WORKTREE_DIAGNOSTIC_CATEGORY_ORDER}
    for collection in collections:
        for item in collection:
            item_categories = _worktree_normalize_diagnostic_categories(item.get("categories"))
            for category in item_categories:
                counts[category] += 1
    return counts


def _worktree_diagnostic_matches_categories(item: dict[str, object], selected_categories: set[str]) -> bool:
    if not selected_categories:
        return True
    item_categories = set(_worktree_normalize_diagnostic_categories(item.get("categories")))
    return bool(item_categories & selected_categories)


def check_and_remove_stale_git_lock(repo: Path, max_age_seconds: int = 300) -> bool:
    """Remove stale .git/index.lock if older than *max_age_seconds*.

    Returns True if a stale lock was successfully removed.
    On Windows, PermissionError means another process still holds the file — skip removal.
    """
    lock = repo / ".git" / "index.lock"
    if not lock.exists():
        return False
    try:
        age = time.time() - lock.stat().st_mtime
    except Exception:
        return False
    if age < max_age_seconds:
        return False
    try:
        lock.unlink()
        eprint(f"[WARN] Removed stale .git/index.lock (age={age:.0f}s)")
        return True
    except PermissionError:
        # Another process is actively using this lock — leave it alone.
        return False
    except Exception as ex:
        eprint(f"[WARN] Failed to remove stale .git/index.lock: {ex}")
        return False


def ensure_clean_working_tree(repo: Path) -> bool:
    """Check for and resolve conflict/dirty state at cycle start.

    Detects UU/AA/DD conflict markers via ``git status --porcelain`` and
    attempts recovery via ``git merge --abort`` → ``git checkout -- .`` →
    ``git clean -fd``.

    Returns True if the tree is clean (or was cleaned), False if recovery failed.
    """
    check_and_remove_stale_git_lock(repo)
    code, out = run_cmd(["git", "status", "--porcelain"], cwd=repo, timeout_sec=60)
    if code != 0:
        eprint(f"[WARN] ensure_clean_working_tree: git status failed (rc={code})")
        return False

    if not out.strip():
        return True  # already clean

    # Check for conflict markers (UU, AA, DD, AU, UA, DU, UD)
    conflict_prefixes = {"UU", "AA", "DD", "AU", "UA", "DU", "UD"}
    has_conflicts = False
    for line in out.splitlines():
        if len(line) >= 2 and line[:2] in conflict_prefixes:
            has_conflicts = True
            break

    if not has_conflicts:
        return True  # dirty but no conflicts — leave as-is

    eprint("[WARN] Conflict state detected in working tree; attempting recovery...")

    # Try merge --abort first (covers mid-merge conflicts)
    run_cmd(["git", "merge", "--abort"], cwd=repo, timeout_sec=30)

    # Reset tracked files and remove untracked artifacts
    run_cmd(["git", "checkout", "--", "."], cwd=repo, timeout_sec=60)
    run_cmd(["git", "clean", "-fd"], cwd=repo, timeout_sec=60)

    # Verify recovery
    code2, out2 = run_cmd(["git", "status", "--porcelain"], cwd=repo, timeout_sec=60)
    if code2 == 0 and not any(
        ln[:2] in conflict_prefixes for ln in (out2 or "").splitlines() if len(ln) >= 2
    ):
        eprint("[INFO] Working tree conflict state resolved.")
        return True

    eprint("[WARN] ensure_clean_working_tree: conflict recovery incomplete.")
    return False


def git_head(repo: Path) -> str:
    code, out = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo, timeout_sec=60)
    lines = _git_data_lines(out)
    return lines[-1] if code == 0 and lines else ""


def git_rev_parse_ref(repo: Path, ref: str) -> str:
    ref_text = str(ref or "").strip()
    if not ref_text:
        return ""
    code, out = run_cmd(["git", "rev-parse", ref_text], cwd=repo, timeout_sec=60)
    lines = _git_data_lines(out)
    return lines[-1] if code == 0 and lines else ""


def git_current_branch(repo: Path) -> str:
    code, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout_sec=60)
    lines = _git_data_lines(out)
    return lines[-1] if code == 0 and lines else ""


def git_show_toplevel(repo: Path) -> str:
    code, out = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo, timeout_sec=60)
    lines = _git_data_lines(out)
    return lines[-1] if code == 0 and lines else ""


def _git_data_lines(out: str) -> list[str]:
    """Return git stdout-like lines, dropping stderr warnings captured by run_cmd."""
    data: list[str] = []
    for raw in (out or "").splitlines():
        probe = raw.strip()
        if not probe:
            continue
        lower = probe.lower()
        if lower.startswith("warning:") or lower.startswith("hint:"):
            continue
        data.append(raw.rstrip("\r\n"))
    return data


def _git_status_path(line: str) -> str:
    text = str(line or "").rstrip("\r\n")
    if len(text) < 4:
        return ""
    path_part = text[3:].strip()
    if "->" in path_part:
        path_part = path_part.split("->", 1)[1].strip()
    if path_part.startswith('"') and path_part.endswith('"'):
        path_part = path_part[1:-1]
    if "\\" in path_part:
        import codecs

        try:
            path_part = codecs.decode(path_part, "unicode_escape")
        except Exception:
            pass
    return path_part.replace("\\", "/")


def _is_agentcli_metadata_path(path: str) -> bool:
    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized:
        return False
    parts = [part for part in normalized.split("/") if part]
    return any(part in {".AgentCLI", ".doc"} for part in parts)


def has_new_commits(repo: Path, before_head: str) -> bool:
    """Check if new commits exist since *before_head*."""
    current = git_head(repo)
    if not before_head or not current:
        return False
    return before_head != current


def ref_has_new_commits(repo: Path, ref: str, before_head: str) -> bool:
    """Check whether *ref* points at a commit different from *before_head*."""
    current = git_rev_parse_ref(repo, ref)
    if not before_head or not current:
        return False
    return before_head != current


def git_ls_files(repo: Path) -> list[str]:
    code, out = run_cmd(["git", "ls-files"], cwd=repo, timeout_sec=120)
    if code != 0:
        return []
    return _git_data_lines(out)


def git_changed_files(repo: Path, prev_head: str, curr_head: str) -> list[str]:
    if not prev_head or not curr_head or prev_head == curr_head:
        return []
    code, out = run_cmd(["git", "diff", "--name-only", prev_head, curr_head], cwd=repo, timeout_sec=120)
    if code != 0:
        return []
    return _git_data_lines(out)


def git_porcelain(repo: Path) -> str:
    code, out = run_cmd(["git", "status", "--porcelain"], cwd=repo, timeout_sec=60)
    if code != 0:
        return ""
    lines = _git_data_lines(out)
    filtered = [line for line in lines if not _is_agentcli_metadata_path(_git_status_path(line))]
    return "\n".join(filtered)


def git_repo_state(repo: Path) -> str:
    return "dirty" if git_porcelain(repo).strip() else "clean"


def git_untracked_files(repo: Path) -> list[str]:
    """Get list of untracked files (not ignored)."""
    code, out = run_cmd(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo, timeout_sec=30)
    if code != 0 or not out.strip():
        return []
    return _git_data_lines(out)


def has_working_tree_changes(
    repo: Path,
    before_porcelain: str,
    after_porcelain: str,
    before_untracked: "set[str] | None" = None,
) -> bool:
    """
    Check if there are actual working tree changes, including new untracked files.

    This handles the edge case where new files are created in already-untracked directories,
    which don't change the porcelain output (since the directory is already marked as untracked).

    Args:
        repo: Repository path
        before_porcelain: Porcelain output before task
        after_porcelain: Porcelain output after task
        before_untracked: Untracked file set recorded BEFORE task execution

    Returns:
        True if there are changes (modified, staged, or new untracked files)
    """
    # Quick check: if porcelain changed, definitely changed
    if before_porcelain != after_porcelain:
        return True

    # Deeper check: compare untracked file lists (before vs current)
    # before_untracked must be captured before task execution in cycle.py
    if before_untracked is not None:
        after_untracked = set(git_untracked_files(repo))
        if before_untracked != after_untracked:
            return True

    # Check for staged changes
    code, staged = run_cmd(["git", "diff", "--cached", "--name-only"], cwd=repo, timeout_sec=30)
    if code == 0 and _git_data_lines(staged):
        return True

    return False


def git_worktree_changed_files(repo: Path) -> list[str]:
    """Best-effort list of files changed in the working tree (incl. untracked).

    This complements `git_changed_files(prev_head, curr_head)` which only captures
    committed HEAD-to-HEAD changes. When users run the runner in a dirty worktree
    (common during iterative agent edits), PM incremental should see those paths.

    We parse `git status --porcelain` and extract the *current* path. For renames,
    we use the destination path after `->`.
    """

    s = git_porcelain(repo)
    if not s.strip():
        return []

    out: list[str] = []
    for line in s.splitlines():
        ln = line.rstrip("\n")
        if len(ln) < 4:
            continue

        # Porcelain: XY <path> (or XY <src> -> <dst> for renames)
        path_part = ln[3:].strip()
        if not path_part:
            continue

        if "->" in path_part:
            # rename/copy
            path_part = path_part.split("->", 1)[1].strip()

        # strip quotes if present
        if path_part.startswith('"') and path_part.endswith('"'):
            path_part = path_part[1:-1]

        # Unescape git's C-string octal escapes (e.g. \nnn for non-ASCII paths)
        import codecs
        if '\\' in path_part:
            try:
                path_part = codecs.decode(path_part, 'unicode_escape')
            except Exception:
                pass

        if path_part:
            out.append(path_part.replace("\\", "/"))

    # de-dupe
    seen: set[str] = set()
    uniq: list[str] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="replace")).hexdigest()


def repo_fingerprint(repo: Path) -> str:
    """Fingerprint (HEAD + status porcelain) used to avoid re-running PM every loop."""
    head = git_head(repo).strip()
    porcelain = git_porcelain(repo)
    return sha256_text(head + "\n" + porcelain)


def list_untracked(repo: Path) -> list[str]:
    code, out = run_cmd(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo, timeout_sec=60)
    if code != 0:
        return []
    return _git_data_lines(out)


def normalize_glob_patterns(patterns: Sequence[str] | None) -> list[str]:
    if not patterns:
        return []
    out: list[str] = []
    for raw in patterns:
        if raw is None:
            continue
        s = str(raw).strip()
        if not s:
            continue
        s = s.replace("\\", "/")
        if s.startswith("./"):
            s = s[2:]
        out.append(s)
    return out


def filter_untracked_paths(paths: Sequence[str], exclude_globs: Sequence[str] | None) -> list[str]:
    if not paths:
        return []
    patterns = normalize_glob_patterns(exclude_globs)
    if not patterns:
        return [p for p in paths if p]
    out: list[str] = []
    for raw in paths:
        if not raw:
            continue
        path = raw.replace("\\", "/")
        if path.startswith("./"):
            path = path[2:]
        if any(fnmatch.fnmatch(path, pat) for pat in patterns):
            continue
        out.append(raw)
    return out


def _chunked(items: Sequence[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [list(items)]
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


def copy_untracked(repo: Path, files: list[str], dest_dir: Path) -> None:
    for rel in files:
        src = repo / rel
        if not src.exists() or src.is_dir():
            continue
        dst = dest_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except Exception:
            pass


@dataclass
class TaskBranch:
    """Metadata for a per-task isolation branch."""
    branch_name: str   # e.g. "task/T1_2026-02-08T14-30-22"
    base_branch: str   # e.g. "main" or "HEAD" (detached)
    base_commit: str   # SHA at creation
    created_at: str
    task_id: str
    task_title: str = ""  # e.g. "Add IDisposable + CTS to TransactionEntry.razor"
    goal_trace: list[dict[str, object]] = field(default_factory=list)


def _sanitize_branch_fragment(value: str, *, max_len: int = 48) -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip())
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    if len(text) > max_len:
        text = text[:max_len].rstrip("-._")
    return text


def _task_branch_goal_fragment(goal_trace: Sequence[dict[str, object]] | None) -> str:
    if not goal_trace:
        return ""
    first = goal_trace[0] if isinstance(goal_trace, Sequence) else None
    if not isinstance(first, dict):
        return ""
    goal_ref = _sanitize_branch_fragment(
        str(first.get("goal_ref") or first.get("goal_id") or "").strip(),
        max_len=48,
    )
    if goal_ref:
        return f"__goal-{goal_ref}"
    goal_text = _sanitize_branch_fragment(str(first.get("goal_text") or first.get("text") or "").strip(), max_len=48)
    if goal_text:
        return f"__goal-{goal_text}"
    return ""


def format_task_commit_message(
    tb: TaskBranch,
    *,
    action: str = "",
) -> tuple[str, str]:
    """Return a git commit subject/body that preserves GOALS traceability."""
    base_subject = f"[{tb.task_id}] {tb.task_title}" if tb.task_title else f"[auto] task {tb.task_id}"
    goal_fragment = _task_branch_goal_fragment(tb.goal_trace)
    if goal_fragment:
        goal_label = goal_fragment.removeprefix("__goal-")
        base_subject = f"{base_subject} [GOAL {goal_label}]"
    if action:
        base_subject = f"{base_subject} ({action})"

    body_lines: list[str] = []
    for trace in (tb.goal_trace or [])[:3]:
        if not isinstance(trace, dict):
            continue
        goal_ref = str(trace.get("goal_ref") or trace.get("goal_id") or "").strip()
        goal_text = str(trace.get("goal_text") or trace.get("text") or "").strip()
        if goal_ref or goal_text:
            body_lines.append(f"GOAL: {goal_ref} {goal_text}".strip())
        matched_fields = trace.get("matched_fields")
        if isinstance(matched_fields, Sequence) and matched_fields:
            fields = ", ".join(str(field).strip() for field in matched_fields if str(field).strip())
            if fields:
                body_lines.append(f"Matched fields: {fields}")
    return base_subject, "\n".join(body_lines).strip()


def create_task_branch(
    repo: Path,
    task_id: str,
    task_title: str = "",
    *,
    goal_trace: Sequence[dict[str, object]] | None = None,
) -> TaskBranch:
    """Create a ``task/<id>_<timestamp>`` branch for isolated work.

    If the working tree is dirty the changes are stashed, the branch is
    created, and the stash is popped so work-in-progress is preserved on
    the new branch.
    """
    check_and_remove_stale_git_lock(repo)

    # Determine base branch
    rc, ref = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout_sec=30)
    base_branch = ref.strip() if rc == 0 and ref.strip() != "HEAD" else "HEAD"

    base_commit = git_head(repo)
    if not base_commit:
        raise RuntimeError("Cannot create task branch: unable to determine HEAD")

    ts = _safe_ts()
    goal_fragment = _task_branch_goal_fragment(goal_trace)
    branch_name = f"task/{task_id}{goal_fragment}_{ts}"

    # Stash dirty tree if needed
    dirty = bool(git_porcelain(repo).strip())
    if dirty:
        run_cmd(["git", "stash", "push", "-m", f"task-branch-{task_id}"], cwd=repo, timeout_sec=120)

    rc, out = run_cmd(["git", "checkout", "-b", branch_name], cwd=repo, timeout_sec=30)
    if rc != 0:
        # Restore stash on failure
        if dirty:
            run_cmd(["git", "stash", "pop"], cwd=repo, timeout_sec=120)
        raise RuntimeError(f"Failed to create task branch {branch_name}: {out}")

    if dirty:
        rc_pop, out_pop = run_cmd(["git", "stash", "pop"], cwd=repo, timeout_sec=120)
        if rc_pop != 0:
            eprint(f"[WARN] git stash pop failed (rc={rc_pop}): {out_pop[:200]}")
            # Clean up conflict state so the branch starts clean
            run_cmd(["git", "checkout", "--", "."], cwd=repo, timeout_sec=60)
            run_cmd(["git", "clean", "-fd"], cwd=repo, timeout_sec=60)
            run_cmd(["git", "stash", "drop"], cwd=repo, timeout_sec=30)
            eprint("[INFO] Cleaned up failed stash pop; branch starts clean (stashed changes dropped).")

    eprint(f"[INFO] Created task branch: {branch_name} (base={base_branch}, commit={base_commit[:8]})")
    return TaskBranch(
        branch_name=branch_name,
        base_branch=base_branch,
        base_commit=base_commit,
        created_at=now_iso(),
        task_id=task_id,
        task_title=task_title,
        goal_trace=[dict(trace) for trace in goal_trace or [] if isinstance(trace, dict)],
    )


def merge_task_branch(repo: Path, tb: TaskBranch) -> bool:
    """Merge the task branch back to the base branch.

    Commits any uncommitted work first, then attempts fast-forward merge
    followed by a regular merge.  Returns ``True`` on success, ``False``
    on conflict.
    """
    check_and_remove_stale_git_lock(repo)

    # Commit any remaining uncommitted changes on the task branch
    porcelain = git_porcelain(repo)
    if porcelain.strip():
        run_cmd(["git", "add", "-A"], cwd=repo, timeout_sec=120)
        subject, body = format_task_commit_message(tb, action="final commit")
        commit_cmd = ["git", "commit", "--no-verify", "-m", subject]
        if body:
            commit_cmd.extend(["-m", body])
        run_cmd(commit_cmd, cwd=repo, timeout_sec=120)

    # Switch back to base
    checkout_target = tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit
    rc, out = run_cmd(["git", "checkout", checkout_target], cwd=repo, timeout_sec=30)
    if rc != 0:
        eprint(f"[WARN] Failed to checkout {checkout_target}: {out}")
        return False

    # Try fast-forward first
    rc, out = run_cmd(["git", "merge", "--ff-only", tb.branch_name], cwd=repo, timeout_sec=120)
    if rc == 0:
        run_cmd(["git", "branch", "-d", tb.branch_name], cwd=repo, timeout_sec=30)
        eprint(f"[INFO] Fast-forward merged and deleted {tb.branch_name}")
        return True

    # Fall back to regular merge
    rc, out = run_cmd(["git", "merge", "--no-edit", tb.branch_name], cwd=repo, timeout_sec=120)
    if rc == 0:
        run_cmd(["git", "branch", "-d", tb.branch_name], cwd=repo, timeout_sec=30)
        eprint(f"[INFO] Merged and deleted {tb.branch_name}")
        return True

    # Conflict — abort merge and report failure
    run_cmd(["git", "merge", "--abort"], cwd=repo, timeout_sec=30)
    eprint(f"[WARN] Merge conflict for {tb.branch_name}; work preserved on branch")
    return False


def abandon_task_branch(repo: Path, tb: TaskBranch) -> str:
    """Abandon a task branch, preserving work, and return to the base branch.

    Any uncommitted changes are committed so nothing is lost.  The branch
    is *not* deleted — the user can inspect or cherry-pick from it later.

    Returns the branch name for reference.
    """
    check_and_remove_stale_git_lock(repo)

    # Commit uncommitted work so it's not lost
    porcelain = git_porcelain(repo)
    if porcelain.strip():
        run_cmd(["git", "add", "-A"], cwd=repo, timeout_sec=120)
        subject, body = format_task_commit_message(tb, action="abandoned")
        commit_cmd = ["git", "commit", "--no-verify", "-m", subject]
        if body:
            commit_cmd.extend(["-m", body])
        run_cmd(commit_cmd, cwd=repo, timeout_sec=120)

    # Switch back to base
    checkout_target = tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit
    rc, out = run_cmd(["git", "checkout", checkout_target], cwd=repo, timeout_sec=30)
    if rc != 0:
        raise RuntimeError(f"Failed to checkout {checkout_target} after abandon: {out}")

    eprint(f"[INFO] Abandoned task branch {tb.branch_name} (work preserved)")
    return tb.branch_name


def preserve_task_branch_and_advance_head(repo: Path, tb: TaskBranch) -> str:
    """Preserve a task branch and advance the current checkout to its commit.

    This is used by isolated generated worktrees. The source repository remains
    untouched, but the worktree HEAD accumulates completed task results so the
    next task starts from the previous task's output instead of the original
    source HEAD.
    """
    check_and_remove_stale_git_lock(repo)

    porcelain = git_porcelain(repo)
    if porcelain.strip():
        rc, out = run_cmd(["git", "add", "-A"], cwd=repo, timeout_sec=120)
        if rc != 0:
            raise RuntimeError(f"Failed to stage changes before preserving {tb.branch_name}: {out}")
        subject, body = format_task_commit_message(tb, action="preserved")
        commit_cmd = ["git", "commit", "--no-verify", "-m", subject]
        if body:
            commit_cmd.extend(["-m", body])
        rc, out = run_cmd(commit_cmd, cwd=repo, timeout_sec=120)
        if rc != 0:
            raise RuntimeError(f"Failed to commit changes before preserving {tb.branch_name}: {out}")

    checkout_target = tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit
    rc, out = run_cmd(["git", "checkout", checkout_target], cwd=repo, timeout_sec=30)
    if rc != 0:
        raise RuntimeError(f"Failed to checkout {checkout_target} before preserving {tb.branch_name}: {out}")

    rc, out = run_cmd(["git", "merge", "--ff-only", tb.branch_name], cwd=repo, timeout_sec=120)
    if rc != 0:
        raise RuntimeError(f"Failed to advance worktree HEAD to {tb.branch_name}: {out}")

    advanced_head = git_head(repo)
    eprint(f"[INFO] Preserved task branch {tb.branch_name}; worktree HEAD advanced to {advanced_head[:8]}")
    return advanced_head


def _cleanup_pytest_cache_tempdirs(
    repo: Path,
    *,
    max_attempts: int = 4,
    initial_backoff_seconds: float = 0.05,
) -> dict[str, object]:
    """Best-effort cleanup for pytest cache bootstrap dirs left in a worktree root.

    Pytest creates top-level ``pytest-cache-files-*`` directories while
    initializing ``.pytest_cache``. On Windows those empty temporary directories
    can remain briefly locked after a test subprocess exits, which makes
    ``git clean -fd`` noisy and can block generated-worktree removal.
    """
    repo_resolved = repo.expanduser().resolve()
    candidates = [
        path
        for path in repo_resolved.glob(f"{PYTEST_CACHE_TEMP_PREFIX}*")
        if path.is_dir() and path.parent == repo_resolved
    ]
    removed: list[str] = []
    locked: list[dict[str, object]] = []
    attempts_by_path: dict[str, list[dict[str, object]]] = {}
    total_attempts = max(1, int(max_attempts))
    initial_backoff = max(0.0, float(initial_backoff_seconds))

    for path in candidates:
        path_key = path.as_posix()
        backoff = initial_backoff
        for attempt in range(1, total_attempts + 1):
            try:
                shutil.rmtree(path)
                removed.append(path_key)
                break
            except FileNotFoundError:
                removed.append(path_key)
                break
            except PermissionError as ex:
                attempts_by_path.setdefault(path_key, []).append(
                    _worktree_cleanup_attempt_details(ex, path, attempt=attempt, operation="pytest_cache_temp_cleanup")
                )
            except OSError as ex:
                if getattr(ex, "errno", None) not in {errno.EACCES, errno.EPERM}:
                    raise
                attempts_by_path.setdefault(path_key, []).append(
                    _worktree_cleanup_attempt_details(ex, path, attempt=attempt, operation="pytest_cache_temp_cleanup")
                )

            if not path.exists():
                removed.append(path_key)
                break
            if attempt < total_attempts:
                time.sleep(backoff)
                backoff = min(backoff * 2 if backoff else initial_backoff, 0.5)
        else:
            locked.append(
                {
                    "path": path_key,
                    "attempts": attempts_by_path.get(path_key, []),
                }
            )

    return {
        "found": len(candidates),
        "removed": removed,
        "locked": locked,
    }


def reset_task_branch(repo: Path, tb: TaskBranch) -> None:
    """Reset a task branch to its base commit for retry.

    This performs ``git reset --hard`` + ``git clean -fd`` but *only* on
    the task branch, so main is never affected.
    """
    check_and_remove_stale_git_lock(repo)

    rc, out = run_cmd(["git", "reset", "--hard", tb.base_commit], cwd=repo, timeout_sec=120)
    if rc != 0:
        raise RuntimeError(f"reset_task_branch: git reset --hard failed: {out}")

    _cleanup_pytest_cache_tempdirs(repo, max_attempts=3, initial_backoff_seconds=0.05)
    rc, out = run_cmd(["git", "clean", "-fd"], cwd=repo, timeout_sec=120)
    if rc != 0 and PYTEST_CACHE_TEMP_PREFIX in out:
        cleanup = _cleanup_pytest_cache_tempdirs(repo, max_attempts=6, initial_backoff_seconds=0.2)
        rc, out = run_cmd(["git", "clean", "-fd"], cwd=repo, timeout_sec=120)
        if rc != 0 and PYTEST_CACHE_TEMP_PREFIX in out:
            exclude_rc, exclude_out = run_cmd(
                ["git", "clean", "-fd", "-e", f"{PYTEST_CACHE_TEMP_PREFIX}*"],
                cwd=repo,
                timeout_sec=120,
            )
            if exclude_rc == 0:
                locked_count = len(cleanup.get("locked") or [])
                eprint(
                    "[WARN] reset_task_branch: left locked pytest cache temp "
                    f"director{'y' if locked_count == 1 else 'ies'} for later cleanup: {locked_count}"
                )
                rc, out = 0, exclude_out
    if rc != 0:
        eprint(f"[WARN] reset_task_branch: git clean -fd failed: {out}")

    eprint(f"[INFO] Reset task branch {tb.branch_name} to {tb.base_commit[:8]}")


@dataclass
class RepoCheckpoint:
    patch_path: Path
    untracked_dir: Path
    created_at: str
    head_commit: str = ""  # HEAD at checkpoint time; used to reset before applying patch


def create_checkpoint(repo: Path, checkpoint_dir: Path) -> RepoCheckpoint:
    check_and_remove_stale_git_lock(repo)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    patch_path = checkpoint_dir / "tracked.patch"
    untracked_dir = checkpoint_dir / "untracked"

    # Save HEAD commit so restore can reset to it even if dev agent commits
    head_commit = ""
    _rc, _head = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo, timeout_sec=30)
    if _rc == 0 and _head.strip():
        head_commit = _head.strip()

    # Tracked changes patch.
    # IMPORTANT: `git diff` (without a commit-ish) only captures *unstaged* changes.
    # If a user has already staged changes (`git add`), they would be LOST on rollback.
    #
    # Preferred: diff working tree vs HEAD => includes both staged + unstaged.
    # Fallback: on repos without a valid HEAD (e.g., before first commit), merge staged + unstaged diffs.
    code, out = run_cmd(["git", "diff", "--binary", "HEAD"], cwd=repo, timeout_sec=600)
    if code != 0:
        _, out_staged = run_cmd(["git", "diff", "--binary", "--cached"], cwd=repo, timeout_sec=600)
        _, out_unstaged = run_cmd(["git", "diff", "--binary"], cwd=repo, timeout_sec=600)
        out = (out_staged or "") + "\n" + (out_unstaged or "")

    patch_path.write_text((out or "") + "\n", encoding="utf-8", errors="replace")

    untracked = list_untracked(repo)
    if untracked:
        copy_untracked(repo, untracked, untracked_dir)

    return RepoCheckpoint(patch_path=patch_path, untracked_dir=untracked_dir, created_at=now_iso(), head_commit=head_commit)


def _safe_ts() -> str:
    return now_iso().replace(":", "-")


def _write_report(path: Path, content: str) -> None:
    try:
        safe_write_text(path, content)
    except Exception:
        pass


def _create_rescue_branch(repo: Path, task_id: str = "") -> str | None:
    """Create a rescue branch preserving all current work before rollback.

    Commits staged, unstaged, and untracked changes to a detached rescue branch
    so the work can be recovered later (e.g. ``git cherry-pick`` or ``git diff``).

    Returns the rescue branch name on success, or ``None`` if nothing to save.
    """
    ts = _safe_ts()
    label = f"{task_id}_" if task_id else ""
    branch_name = f"rescue/{label}{ts}"

    # Check if there are any changes worth saving
    porcelain = git_porcelain(repo)
    untracked = list_untracked(repo)
    if not porcelain.strip() and not untracked:
        return None  # Nothing to save

    # Remember current branch/HEAD to restore after
    _rc, original_ref = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, timeout_sec=30)
    original_ref = original_ref.strip() if _rc == 0 else ""

    _, original_sha = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo, timeout_sec=30)
    original_sha = original_sha.strip()

    try:
        # Create and switch to rescue branch
        code, out = run_cmd(["git", "checkout", "-b", branch_name], cwd=repo, timeout_sec=30)
        if code != 0:
            eprint(f"[WARN] Failed to create rescue branch {branch_name}: {out}")
            return None

        # Stage all changes including untracked files
        run_cmd(["git", "add", "-A"], cwd=repo, timeout_sec=120)

        # Commit everything
        msg = f"[rescue] Work preserved before rollback (task: {task_id or 'unknown'})"
        code, out = run_cmd(["git", "commit", "-m", msg, "--no-verify"], cwd=repo, timeout_sec=120)
        if code != 0:
            eprint(f"[WARN] Rescue commit failed: {out}")
            # Switch back even if commit failed
            if original_sha:
                run_cmd(["git", "checkout", original_sha], cwd=repo, timeout_sec=30)
            elif original_ref and original_ref != "HEAD":
                run_cmd(["git", "checkout", original_ref], cwd=repo, timeout_sec=30)
            return None

        eprint(f"[INFO] Work preserved in rescue branch: {branch_name}")
        return branch_name
    except Exception as ex:
        eprint(f"[WARN] Rescue branch creation failed: {ex}")
        return None
    finally:
        # Switch back to original branch/commit for the actual rollback
        if original_sha:
            run_cmd(["git", "checkout", original_sha], cwd=repo, timeout_sec=30)
        elif original_ref and original_ref != "HEAD":
            run_cmd(["git", "checkout", original_ref], cwd=repo, timeout_sec=30)


def update_checkpoint(repo: Path, cp: RepoCheckpoint) -> RepoCheckpoint:
    """Refresh an existing checkpoint to the current working tree state.

    This advances the rollback baseline so that a subsequent rollback returns
    to this (more recent) state rather than the original task start.
    """
    check_and_remove_stale_git_lock(repo)
    checkpoint_dir = cp.patch_path.parent

    # Overwrite the existing checkpoint in-place
    new_cp = create_checkpoint(repo, checkpoint_dir)
    eprint(f"[INFO] Checkpoint updated (head={new_cp.head_commit[:8] if new_cp.head_commit else '?'})")
    return new_cp


def restore_checkpoint(
    repo: Path,
    cp: RepoCheckpoint,
    *,
    dangerous: bool,
    run_dir: Path | None,
    stop_path: Path | None,
    task_id: str = "",
) -> str | None:
    """Restore working tree to a checkpoint.

    Returns the rescue branch name if work was preserved, or ``None``.
    """
    check_and_remove_stale_git_lock(repo)
    report_dir = run_dir if run_dir is not None else cp.patch_path.parent
    blocked_path = report_dir / "ROLLBACK_BLOCKED.md"
    failure_path = report_dir / "ROLLBACK_FAILURE.md"

    if not dangerous:
        msg = (
            "# Rollback blocked\n\n"
            "Destructive git rollback is disabled (dangerous=False).\n"
            "No `git reset --hard` or `git clean -fd` has been executed.\n\n"
            "To allow rollback, re-run with `--dangerous-git-rollback` (or set in shell).\n"
        )
        _write_report(blocked_path, msg)
        raise RuntimeError("Rollback blocked: dangerous_git_rollback is disabled.")

    # Preserve current work in a rescue branch before destructive rollback
    rescue_branch = _create_rescue_branch(repo, task_id=task_id)

    rescue_dir = (run_dir or cp.patch_path.parent.parent) / f"checkpoint_rescue_{_safe_ts()}"
    rescue_cp: RepoCheckpoint | None = None
    try:
        rescue_cp = create_checkpoint(repo, rescue_dir)
    except Exception as ex:
        _write_report(failure_path, f"# Rollback failure\n\nFailed to create rescue checkpoint: {ex}\n")
        raise

    def fail(reason: str) -> None:
        rescue_note = f"\n\nRescue checkpoint: {rescue_dir}\n" if rescue_cp else "\n\nRescue checkpoint: (none)\n"
        if rescue_branch:
            rescue_note += f"Rescue branch: {rescue_branch}\n"
        _write_report(failure_path, f"# Rollback failure\n\n{reason}{rescue_note}")
        raise RuntimeError(reason)

    code, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, timeout_sec=60)
    if code != 0 or "true" not in out.lower():
        fail(f"Not a git repository (rev-parse failed): rc={code}\n{out}")

    if not cp.patch_path.exists():
        fail(f"Missing checkpoint patch: {cp.patch_path}")

    patch_text = cp.patch_path.read_text(encoding="utf-8", errors="replace")
    empty_patch = not patch_text.strip()

    temp_patch: Path | None = None
    if not empty_patch:
        _fd, _tmp_name = tempfile.mkstemp(prefix="rollback_patch_", suffix=".patch")
        os.close(_fd)
        temp_patch = Path(_tmp_name)
        temp_patch.write_text(patch_text + "\n", encoding="utf-8", errors="replace")

    temp_untracked_dir: Path | None = None
    if cp.untracked_dir.exists():
        temp_untracked_dir = Path(tempfile.mkdtemp(prefix="rollback_untracked_"))
        shutil.copytree(cp.untracked_dir, temp_untracked_dir, dirs_exist_ok=True)

    untracked_warnings: list[str] = []
    try:
        # Reset to checkpoint HEAD first (reverts any commits made by the dev agent)
        reset_target = cp.head_commit or "HEAD"
        code, out = run_cmd(["git", "reset", "--hard", reset_target], cwd=repo, timeout_sec=120)
        if code != 0:
            fail(f"git reset --hard {reset_target} failed: rc={code}\n{out}")

        clean_cmd = ["git", "clean", "-fd"]
        for path in (run_dir, rescue_dir):
            if path is None:
                continue
            try:
                rel = path.resolve().relative_to(repo.resolve()).as_posix()
                clean_cmd.extend(["-e", rel])
            except Exception:
                continue
        code, out = run_cmd(clean_cmd, cwd=repo, timeout_sec=120)
        if code != 0:
            fail(f"git clean -fd failed: rc={code}\n{out}")

        # Apply patch only if non-empty (empty = working tree was clean at checkpoint)
        if temp_patch is not None:
            code, out = run_cmd(["git", "apply", "--binary", "--whitespace=nowarn", str(temp_patch)], cwd=repo, timeout_sec=120)
            if code != 0:
                fail(f"git apply failed: rc={code}\n{out}")

        # Restore untracked files
        if temp_untracked_dir and temp_untracked_dir.exists():
            for src in temp_untracked_dir.rglob("*"):
                if src.is_dir():
                    continue
                rel = src.relative_to(temp_untracked_dir)
                dst = repo / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                except Exception as ex:
                    untracked_warnings.append(f"{rel}: {ex}")
    finally:
        # Clean up temp files to prevent leaks during unattended operation
        if temp_patch is not None:
            try:
                temp_patch.unlink(missing_ok=True)
            except Exception:
                pass
        if temp_untracked_dir is not None:
            try:
                shutil.rmtree(temp_untracked_dir, ignore_errors=True)
            except Exception:
                pass

    if untracked_warnings and run_dir is not None:
        warn_path = report_dir / "ROLLBACK_UNTRACKED_WARNING.md"
        _write_report(
            warn_path,
            "# Untracked restore warning\n\n" + "\n".join(f"- {w}" for w in untracked_warnings) + "\n",
        )

    # Only validate non-empty working tree if the checkpoint patch had content.
    # An empty patch means the tree was clean at checkpoint time — clean after restore is correct.
    if not empty_patch:
        porcelain = git_porcelain(repo)
        if not porcelain.strip():
            fail("Rollback applied but working tree is clean; expected changes after restore.")

    return rescue_branch


def _path_is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def default_worktree_dir(repo: Path, run_dir: Path) -> Path:
    """Return the default isolated worktree path for a run.

    Run directories normally live under the repository's ignored `.AgentCLI`
    folder. A git worktree placed there is nested inside the source repo and can
    be treated as ignored repo content instead of an isolated checkout. Keep
    worktrees outside the source tree by default, with an env override for
    operators who want a different external location.
    """
    repo_resolved = repo.expanduser().resolve()
    run_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (run_dir.name or "run"))
    repo_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in (repo_resolved.name or "repo"))

    configured_root = os.environ.get("AGENTCLI_WORKTREE_HOME", "").strip()
    if configured_root:
        base = Path(configured_root).expanduser()
    else:
        base = repo_resolved.parent / ".agentcli_worktrees"
    return (base / repo_name / run_name).resolve()


def allocate_temporary_worktree_dir(repo: Path, *, prefix: str = "pr-queue-validation") -> Path:
    """Return a unique external worktree path for a one-off isolated run."""
    root = _generated_worktree_home(repo)
    root.mkdir(parents=True, exist_ok=True)
    prefix_text = _worktree_safe_name(prefix, "validation")
    for _ in range(256):
        candidate = root / f"{prefix_text}-{uuid.uuid4().hex[:12]}"
        if not candidate.exists():
            return candidate.resolve()
    raise RuntimeError("Unable to allocate a unique temporary worktree path.")


def generated_worktree_path_state(
    repo: Path,
    *,
    run_dir: Path | None = None,
    worktree_dir: str = "",
) -> dict[str, object]:
    repo_resolved = Path(repo).expanduser().resolve()
    run_dir_resolved: Path | None = None
    if run_dir is not None:
        try:
            run_dir_resolved = Path(run_dir).expanduser().resolve()
        except Exception:
            run_dir_resolved = Path(run_dir).expanduser()

    worktree_text = str(worktree_dir or "").strip()
    candidate: Path | None = None
    derived_from = ""
    if worktree_text:
        try:
            candidate = Path(worktree_text).expanduser().resolve()
        except Exception:
            candidate = Path(worktree_text).expanduser()
        derived_from = "explicit"
    elif run_dir_resolved is not None:
        candidate = default_worktree_dir(repo_resolved, run_dir_resolved)
        derived_from = "default"

    if candidate is None:
        return {
            "path": "",
            "exists": False,
            "state": "not_requested",
            "generated": False,
            "derived_from": "",
        }

    generated_root = _generated_worktree_home(repo_resolved)
    generated = candidate == generated_root or _path_is_relative_to(candidate, generated_root)
    exists = candidate.exists()
    return {
        "path": candidate.as_posix(),
        "exists": exists,
        "state": "present" if exists else "deleted",
        "generated": generated,
        "derived_from": derived_from,
    }


def _worktree_validation_error(repo: Path, worktree_dir: Path) -> str | None:
    repo_resolved = repo.expanduser().resolve()
    worktree_resolved = worktree_dir.expanduser().resolve()
    if _path_is_relative_to(worktree_resolved, repo_resolved):
        return (
            "worktree path is inside the source repository; this can make git "
            f"treat generated files as ignored repo content: {worktree_resolved}"
        )
    if not (worktree_resolved / ".git").exists():
        return f"worktree .git file is missing: {worktree_resolved / '.git'}"

    code, out = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=worktree_resolved, timeout_sec=60)
    if code != 0:
        return f"git rev-parse failed in worktree: rc={code}\n{out}"
    lines = _git_data_lines(out)
    if not lines:
        return "git rev-parse did not return a top-level path"
    actual_top = Path(lines[-1]).expanduser().resolve()
    if actual_top != worktree_resolved:
        return f"git top-level mismatch: expected {worktree_resolved}, got {actual_top}"
    return None


def _safe_generated_worktree_path(worktree_dir: Path) -> bool:
    parts = {p.lower() for p in worktree_dir.expanduser().resolve().parts}
    return ".agentcli_worktrees" in parts


def _validate_existing_worktree_reuse(repo: Path, worktree_dir: Path, run_dir: Path) -> None:
    contract_path = _worktree_contract_path(run_dir)
    if not contract_path.exists():
        raise WorktreeSafetyError(
            "worktree_reuse_contract_missing",
            "Existing worktree cannot be reused without a run contract.",
            details={
                "run_dir": run_dir.as_posix(),
                "worktree_dir": worktree_dir.expanduser().resolve().as_posix(),
                "contract": contract_path.as_posix(),
            },
        )
    try:
        contract = _read_json_payload(contract_path)
    except Exception as ex:
        raise WorktreeSafetyError(
            "worktree_reuse_contract_invalid",
            f"Existing worktree reuse contract is malformed: {str(ex).strip() or ex.__class__.__name__}",
            status_code=400,
            details={"contract": contract_path.as_posix()},
            status="invalid_request",
        ) from ex
    _validate_worktree_contract(repo=repo, worktree_dir=worktree_dir, run_dir=run_dir, contract=contract)


def create_worktree(repo: Path, worktree_dir: Path, *, run_dir: Path | None = None) -> None:
    repo_resolved = repo.expanduser().resolve()
    worktree_resolved = worktree_dir.expanduser().resolve()
    if _path_is_relative_to(worktree_resolved, repo_resolved):
        raise RuntimeError(
            "Refusing to create worktree inside the source repository. "
            f"repo={repo_resolved} worktree={worktree_resolved}"
        )
    if worktree_dir.exists():
        error = _worktree_validation_error(repo, worktree_dir)
        if error is not None:
            raise RuntimeError(f"Existing worktree path is not a valid isolated git worktree: {error}")
        if run_dir is None:
            raise WorktreeSafetyError(
                "worktree_reuse_contract_missing",
                "Existing worktree cannot be reused without a run contract.",
                details={
                    "run_dir": "",
                    "worktree_dir": worktree_resolved.as_posix(),
                },
            )
        _validate_existing_worktree_reuse(repo, worktree_dir, run_dir)
        return
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_cmd(["git", "worktree", "add", "--detach", str(worktree_dir), "HEAD"], cwd=repo, timeout_sec=120)
    if code != 0:
        raise RuntimeError(f"git worktree add failed: rc={code}\n{out}")
    error = _worktree_validation_error(repo, worktree_dir)
    if error is not None:
        raise RuntimeError(f"git worktree add did not create a valid isolated worktree: {error}")
    if run_dir is not None:
        try:
            _write_worktree_contract(run_dir, _worktree_contract_payload(worktree_dir, repo, run_dir))
        except Exception as ex:
            raise RuntimeError(f"Failed to write worktree reuse contract: {ex}") from ex


def _worktree_cleanup_attempt_details(
    error: BaseException,
    worktree_dir: Path,
    *,
    attempt: int,
    operation: str,
) -> dict[str, object]:
    raw_path = getattr(error, "filename", "") or getattr(error, "filename2", "") or worktree_dir.as_posix()
    cleanup_path = str(raw_path or "").strip().replace("\\", "/") or worktree_dir.as_posix()
    details: dict[str, object] = {
        "attempt": int(attempt),
        "operation": str(operation),
        "path": cleanup_path,
        "locking_path": cleanup_path,
        "affected_artifact": worktree_dir.as_posix(),
        "worktree_dir": worktree_dir.as_posix(),
        "error_type": error.__class__.__name__,
        "message": str(error).strip() or error.__class__.__name__,
    }
    filename = getattr(error, "filename", "")
    if filename:
        details["filename"] = str(filename).strip().replace("\\", "/")
    filename2 = getattr(error, "filename2", "")
    if filename2:
        details["filename2"] = str(filename2).strip().replace("\\", "/")
    errno_value = getattr(error, "errno", None)
    if errno_value is not None:
        try:
            details["errno"] = int(errno_value)
        except Exception:
            details["errno"] = errno_value
    winerror = getattr(error, "winerror", None)
    if winerror is not None:
        try:
            details["winerror"] = int(winerror)
        except Exception:
            details["winerror"] = winerror
    return details


def _remove_generated_worktree_with_retry(
    worktree_dir: Path,
    *,
    git_remove: dict[str, object] | None = None,
    git_prune: dict[str, object] | None = None,
    max_attempts: int = 4,
    initial_backoff_seconds: float = 0.05,
) -> None:
    attempts: list[dict[str, object]] = []
    last_error: BaseException | None = None
    total_attempts = max(1, int(max_attempts))
    backoff = max(0.0, float(initial_backoff_seconds))
    retry_schedule_seconds: list[float] = []

    for attempt in range(1, total_attempts + 1):
        try:
            shutil.rmtree(worktree_dir)
            return
        except PermissionError as ex:
            attempt_details = _worktree_cleanup_attempt_details(ex, worktree_dir, attempt=attempt, operation="shutil.rmtree")
            attempts.append(attempt_details)
            last_error = ex
        except OSError as ex:
            if getattr(ex, "errno", None) not in {errno.EACCES, errno.EPERM}:
                raise
            attempt_details = _worktree_cleanup_attempt_details(ex, worktree_dir, attempt=attempt, operation="shutil.rmtree")
            attempts.append(attempt_details)
            last_error = ex

        if not worktree_dir.exists():
            return
        if attempt < total_attempts:
            retry_delay = round(backoff or initial_backoff_seconds, 3)
            attempts[-1]["next_retry_seconds"] = retry_delay
            retry_schedule_seconds.append(retry_delay)
            time.sleep(backoff)
            backoff = min(backoff * 2 if backoff else initial_backoff_seconds, 0.25)

    blocked_path = attempts[-1]["path"] if attempts else worktree_dir.as_posix()
    locking_path = attempts[-1].get("locking_path") if attempts else blocked_path
    locking_path_text = str(locking_path or blocked_path).strip() or blocked_path
    message = f"Failed to remove generated worktree after {len(attempts) or total_attempts} attempts: {blocked_path}"
    if attempts:
        last_attempt = attempts[-1]
        last_attempt_message = str(last_attempt.get("message") or "").strip()
        last_attempt_type = str(last_attempt.get("error_type") or (last_error.__class__.__name__ if last_error else "PermissionError"))
        if last_attempt_message:
            message = f"{message} ({last_attempt_type}: {last_attempt_message})"
    details: dict[str, object] = {
        "path": blocked_path,
        "locking_path": locking_path_text,
        "affected_artifact": worktree_dir.as_posix(),
        "worktree_dir": worktree_dir.as_posix(),
        "attempts": attempts,
        "operation": "shutil.rmtree",
        "retry_schedule_seconds": retry_schedule_seconds,
        "retry_schedule": retry_schedule_seconds,
        "user_mode_cleanup_progress": False,
    }
    if git_remove is not None:
        details["git_worktree_remove"] = dict(git_remove)
    if git_prune is not None:
        details["git_worktree_prune"] = dict(git_prune)
    if os.name == "nt":
        details["reboot_required"] = True
        details["reboot_guidance"] = (
            "Close the process holding the locking path, retry after the scheduled backoff, "
            "and reboot Windows if user-mode cleanup still cannot make progress."
        )
    raise WorktreeCleanupError(message, cleanup_path=str(blocked_path), details=details, attempts=attempts) from last_error


def remove_worktree(repo: Path, worktree_dir: Path) -> None:
    if not worktree_dir.exists():
        return
    code, out = run_cmd(["git", "worktree", "remove", "--force", str(worktree_dir)], cwd=repo, timeout_sec=120)
    if code == 0:
        return

    prune_code, prune_out = run_cmd(["git", "worktree", "prune"], cwd=repo, timeout_sec=120)
    if not worktree_dir.exists():
        return

    if _safe_generated_worktree_path(worktree_dir):
        _remove_generated_worktree_with_retry(
            worktree_dir,
            git_remove={"rc": code, "output": out},
            git_prune={"rc": prune_code, "output": prune_out},
        )
        return

    raise RuntimeError(f"git worktree remove failed: rc={code}\n{out}")


def export_worktree_patch(
    worktree_dir: Path,
    patch_path: Path,
    *,
    base_ref: str = "HEAD",
    exclude_globs: Sequence[str] | None = None,
    chunk_size: int = 50,
) -> None:
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    untracked = filter_untracked_paths(list_untracked(worktree_dir), exclude_globs)
    if untracked:
        for chunk in _chunked(untracked, chunk_size):
            code, out = run_cmd(["git", "add", "-N", "--", *chunk], cwd=worktree_dir, timeout_sec=120)
            if code != 0:
                eprint(f"[WARN] git add -N failed in worktree: rc={code}\n{out}")
    out = ""
    try:
        code, out = run_cmd(["git", "diff", "--binary", base_ref], cwd=worktree_dir, timeout_sec=120)
        if code != 0:
            raise RuntimeError(f"git diff failed in worktree: rc={code}\n{out}")
    finally:
        if untracked:
            for chunk in _chunked(untracked, chunk_size):
                code, out_reset = run_cmd(["git", "reset", "--", *chunk], cwd=worktree_dir, timeout_sec=120)
                if code != 0:
                    eprint(f"[WARN] git reset failed in worktree: rc={code}\n{out_reset}")
    patch_path.write_text(out + "\n", encoding="utf-8", errors="replace")


def apply_patch_to_repo(repo: Path, patch_path: Path) -> None:
    if not patch_path.exists():
        raise RuntimeError(f"Patch not found: {patch_path}")
    code, out = run_cmd(["git", "apply", "--binary", "--whitespace=nowarn", str(patch_path)], cwd=repo, timeout_sec=120)
    if code != 0:
        failure_details = _worktree_parse_apply_failure(out, patch_path)
        raise WorktreeSafetyError(
            "worktree_patch_apply_failed",
            "git apply failed while applying the pending worktree patch.",
            details={
                "path": patch_path.as_posix(),
                "source_repo": repo.as_posix(),
                "command": "git apply --binary --whitespace=nowarn",
                "output": out.strip(),
                "failed_files": failure_details["failed_files"],
                "failed_hunks": failure_details["failed_hunks"],
            },
        )


def _patch_has_changes(patch_path: Path) -> bool:
    try:
        for line in patch_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("diff --git "):
                return True
    except Exception:
        return False
    return False


def _git_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    code, _ = run_cmd(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo, timeout_sec=60)
    return code == 0


def _git_worktree_registration_state(repo: Path, worktree_dir: Path) -> dict[str, object]:
    repo_resolved = repo.expanduser().resolve()
    worktree_resolved = worktree_dir.expanduser().resolve()
    code, out = run_cmd(["git", "worktree", "list", "--porcelain"], cwd=repo_resolved, timeout_sec=60)
    registered: bool | None = None
    registered_path = ""
    if code == 0:
        for raw_line in (out or "").splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("worktree "):
                continue
            candidate = line[len("worktree ") :].strip()
            if not candidate:
                continue
            try:
                candidate_path = Path(candidate).expanduser().resolve()
            except Exception:
                candidate_path = Path(candidate).expanduser()
            if candidate_path == worktree_resolved:
                registered = True
                registered_path = candidate_path.as_posix()
                break
        if registered is None:
            registered = False
    return {
        "repo": repo_resolved.as_posix(),
        "worktree_dir": worktree_resolved.as_posix(),
        "registered": registered,
        "registered_path": registered_path,
        "rc": int(code),
        "output": "" if code == 0 else str(out or "").strip(),
    }


WORKTREE_MERGE_PENDING = "WORKTREE_MERGE_PENDING.json"
WORKTREE_MERGE_PENDING_MD = "WORKTREE_MERGE_PENDING.md"
WORKTREE_MERGE_PENDING_RECONCILED = "WORKTREE_MERGE_PENDING_RECONCILED.json"
WORKTREE_REUSE_CONTRACT = "WORKTREE_REUSE_CONTRACT.json"
WORKTREE_CLEANUP_DRY_RUN = "WORKTREE_CLEANUP_DRY_RUN.json"
WORKTREE_CLEANUP_APPLIED = "WORKTREE_CLEANUP_APPLIED.json"
WORKTREE_CLEANUP_APPROVAL_PREFIX = "APPLY WORKTREE CLEANUP"


def _payload_text(payload: dict[str, object], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _worktree_text_path(value: Path | str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().resolve().as_posix()
    except Exception:
        return raw.replace("\\", "/")


def _worktree_cleanup_permission_detail(
    cleanup_details: dict[str, object] | None,
    cleanup_attempts: Sequence[dict[str, object]] | None,
) -> str:
    details = dict(cleanup_details or {})
    for key in ("permission_detail", "permissionDetail"):
        value = str(details.get(key) or "").strip()
        if value:
            return value

    attempts = [dict(item) for item in cleanup_attempts or [] if isinstance(item, dict)]
    if not attempts:
        return ""

    last_attempt = attempts[-1]
    error_type = str(last_attempt.get("error_type") or last_attempt.get("errorType") or "").strip()
    message = str(last_attempt.get("message") or "").strip()
    errno_value = last_attempt.get("errno")
    winerror_value = last_attempt.get("winerror")

    detail = ""
    if error_type and message:
        detail = f"{error_type}: {message}"
    else:
        detail = message or error_type

    extras: list[str] = []
    if errno_value not in {None, ""}:
        extras.append(f"errno={errno_value}")
    if winerror_value not in {None, ""}:
        extras.append(f"winerror={winerror_value}")
    if extras:
        suffix = "; ".join(extras)
        detail = f"{detail} ({suffix})" if detail else suffix
    return detail


def _worktree_excerpt_line(line: str, *, max_chars: int = 200) -> str:
    text = str(line or "").rstrip("\r\n")
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _worktree_parse_hunk_header(header: str) -> dict[str, int]:
    match = re.match(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@", str(header or ""))
    if not match:
        return {}
    return {
        "oldStart": int(match.group("old_start")),
        "oldCount": int(match.group("old_count") or "1"),
        "newStart": int(match.group("new_start")),
        "newCount": int(match.group("new_count") or "1"),
    }


def _worktree_normalize_patch_path(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in {"/dev/null", "dev/null"}:
        return ""
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text.replace("\\", "/")


def _worktree_parse_patch_sections(patch_text: str) -> list[tuple[str, list[str]]]:
    sections: list[tuple[str, list[str]]] = []
    current_header = ""
    current_lines: list[str] = []
    for raw_line in (patch_text or "").splitlines():
        if raw_line.startswith("diff --git "):
            if current_header:
                sections.append((current_header, current_lines))
            current_header = raw_line.rstrip("\r\n")
            current_lines = []
            continue
        if current_header:
            current_lines.append(raw_line.rstrip("\r\n"))
    if current_header:
        sections.append((current_header, current_lines))
    return sections


def _worktree_summarize_patch_section(
    header: str,
    lines: list[str],
    *,
    max_hunks: int = 6,
    max_hunk_lines: int = 12,
    max_preview_chars: int = 12000,
) -> dict[str, object]:
    header_match = re.match(r"^diff --git a/(.+) b/(.+)$", header or "")
    left_path = _worktree_normalize_patch_path(header_match.group(1)) if header_match else ""
    right_path = _worktree_normalize_patch_path(header_match.group(2)) if header_match else ""
    old_path = left_path
    new_path = right_path
    rename_from = ""
    rename_to = ""
    binary = False
    deleted = False
    renamed = False
    new_file = False
    truncated = False
    preview_chars = 0
    total_lines = 0
    hunks: list[dict[str, object]] = []
    current_hunk: dict[str, object] | None = None

    for line in lines:
        total_lines += 1
        text = str(line or "").rstrip("\r\n")
        if text.startswith("rename from "):
            rename_from = _worktree_normalize_patch_path(text[len("rename from "):])
            if rename_from:
                old_path = rename_from
                renamed = True
            continue
        if text.startswith("rename to "):
            rename_to = _worktree_normalize_patch_path(text[len("rename to "):])
            if rename_to:
                new_path = rename_to
                renamed = True
            continue
        if text.startswith("new file mode "):
            new_file = True
            continue
        if text.startswith("deleted file mode "):
            deleted = True
            continue
        if text.startswith("Binary files "):
            binary = True
            continue
        if text.startswith("GIT binary patch"):
            binary = True
            continue
        if text.startswith("--- "):
            old_path = _worktree_normalize_patch_path(text[4:].strip())
            if old_path == "":
                new_file = True
            continue
        if text.startswith("+++ "):
            new_path = _worktree_normalize_patch_path(text[4:].strip())
            if new_path == "":
                deleted = True
            continue
        if text.startswith("@@"):
            hunk_meta = _worktree_parse_hunk_header(text)
            current_hunk = {
                "header": text,
                "oldStart": hunk_meta.get("oldStart", 0),
                "oldCount": hunk_meta.get("oldCount", 0),
                "newStart": hunk_meta.get("newStart", 0),
                "newCount": hunk_meta.get("newCount", 0),
                "lines": [],
                "truncated": False,
                "lineCount": 0,
            }
            if len(hunks) < max_hunks:
                hunks.append(current_hunk)
            else:
                current_hunk = None
                truncated = True
            continue
        if current_hunk is None:
            continue
        current_hunk["lineCount"] = int(current_hunk.get("lineCount", 0)) + 1
        lines_preview = current_hunk.setdefault("lines", [])
        if isinstance(lines_preview, list) and len(lines_preview) < max_hunk_lines and preview_chars < max_preview_chars:
            lines_preview.append(_worktree_excerpt_line(text))
            preview_chars += len(text)
        else:
            current_hunk["truncated"] = True
            truncated = True

    if deleted:
        kind = "deleted"
    elif renamed:
        kind = "renamed"
    elif binary:
        kind = "binary"
    elif new_file:
        kind = "added"
    else:
        kind = "modified"

    path = new_path or old_path or right_path or left_path
    note = ""
    summary = ""
    if binary:
        summary = "Binary patch"
        note = "binary patch"
    elif deleted:
        summary = "Deleted file"
        note = old_path or path or "deleted file"
    elif renamed:
        summary = "Renamed file"
        if rename_from and rename_to:
            note = f"{rename_from} -> {rename_to}"
            summary = f"Renamed {rename_from} -> {rename_to}"
        else:
            note = path or "renamed file"
    elif new_file:
        summary = "Added file"
        note = path or "new file"
    elif hunks:
        summary = "Text patch"
        note = f"{len(hunks)} hunk(s)"
    else:
        summary = "File metadata"
        note = path or "patch"

    if truncated:
        note = f"{note} | preview truncated" if note else "preview truncated"
        summary = f"{summary} | preview truncated"

    return {
        "path": path,
        "oldPath": old_path or path,
        "newPath": new_path or path,
        "kind": kind,
        "state": kind,
        "note": note,
        "summary": summary,
        "binary": binary,
        "deleted": deleted,
        "renamed": renamed,
        "large": truncated,
        "truncated": truncated,
        "hunks": hunks,
        "lineCount": total_lines,
    }


def summarize_worktree_diff(
    patch_path: Path,
    *,
    allow_placeholder: bool = True,
    max_files: int = 128,
    max_hunks_per_file: int = 6,
    max_hunk_lines: int = 12,
    max_preview_chars: int = 12000,
) -> list[dict[str, object]]:
    patch_file = Path(patch_path)
    if not patch_file.exists() or not patch_file.is_file():
        if allow_placeholder and str(patch_file).strip():
            return [
                {
                    "path": patch_file.as_posix(),
                    "oldPath": patch_file.as_posix(),
                    "newPath": patch_file.as_posix(),
                    "kind": "modified",
                    "state": "modified",
                    "note": "patch export",
                    "summary": "Patch export",
                    "binary": False,
                    "deleted": False,
                    "renamed": False,
                    "large": False,
                    "truncated": False,
                    "hunks": [],
                    "lineCount": 0,
                }
            ]
        return []

    try:
        patch_text = patch_file.read_text(encoding="utf-8", errors="replace")
    except Exception:
        if allow_placeholder:
            return [
                {
                    "path": patch_file.as_posix(),
                    "oldPath": patch_file.as_posix(),
                    "newPath": patch_file.as_posix(),
                    "kind": "modified",
                    "state": "modified",
                    "note": "patch export",
                    "summary": "Patch export",
                    "binary": False,
                    "deleted": False,
                    "renamed": False,
                    "large": False,
                    "truncated": False,
                    "hunks": [],
                    "lineCount": 0,
                }
            ]
        return []

    sections = _worktree_parse_patch_sections(patch_text)
    changed_files = [
        _worktree_summarize_patch_section(
            header,
            section_lines,
            max_hunks=max_hunks_per_file,
            max_hunk_lines=max_hunk_lines,
            max_preview_chars=max_preview_chars,
        )
        for header, section_lines in sections[:max_files]
    ]
    if not changed_files and allow_placeholder and patch_text.strip():
        changed_files = [
            {
                "path": patch_file.as_posix(),
                "oldPath": patch_file.as_posix(),
                "newPath": patch_file.as_posix(),
                "kind": "modified",
                "state": "modified",
                "note": "patch export",
                "summary": "Patch export",
                "binary": False,
                "deleted": False,
                "renamed": False,
                "large": False,
                "truncated": False,
                "hunks": [],
                "lineCount": 0,
            }
        ]
    return changed_files


def _worktree_parse_apply_failure(output: str, patch_path: Path) -> dict[str, list[dict[str, object]]]:
    failed_files: list[dict[str, object]] = []
    failed_hunks: list[dict[str, object]] = []
    seen_file_keys: set[tuple[str, int, str]] = set()

    section_map: dict[str, list[dict[str, object]]] = {}
    for item in summarize_worktree_diff(patch_path, allow_placeholder=False):
        for key in {str(item.get("path") or ""), str(item.get("oldPath") or ""), str(item.get("newPath") or "")}:
            if key:
                section_map.setdefault(key, []).append(item)

    def add_failure(path: str, line: int | None, reason: str) -> None:
        normalized_path = _worktree_normalize_patch_path(path)
        key = (normalized_path, int(line or 0), reason)
        if not normalized_path or key in seen_file_keys:
            return
        seen_file_keys.add(key)
        failed_files.append(
            {
                "path": normalized_path,
                "line": int(line or 0) if line else None,
                "reason": reason,
            }
        )
        candidates = section_map.get(normalized_path, [])
        if not candidates:
            return
        for candidate in candidates:
            hunks = candidate.get("hunks") if isinstance(candidate.get("hunks"), list) else []
            matched_hunk: dict[str, object] | None = None
            if line:
                for hunk in hunks:
                    if not isinstance(hunk, dict):
                        continue
                    new_start = int(hunk.get("newStart") or 0)
                    new_count = int(hunk.get("newCount") or 0) or 1
                    if new_start <= line < new_start + max(1, new_count):
                        matched_hunk = hunk
                        break
            if matched_hunk is None and hunks:
                first_hunk = hunks[0]
                matched_hunk = first_hunk if isinstance(first_hunk, dict) else None
            if matched_hunk is None:
                continue
            failed_hunks.append(
                {
                    "path": normalized_path,
                    "line": int(line or 0) if line else None,
                    "reason": reason,
                    "header": str(matched_hunk.get("header") or ""),
                }
            )
            break

    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^error: patch failed: (?P<path>.+?):(?P<line>\d+)$", line)
        if match:
            add_failure(match.group("path"), int(match.group("line")), "patch failed")
            continue
        match = re.match(r"^error: (?P<path>.+?): patch does not apply$", line)
        if match:
            add_failure(match.group("path"), None, "patch does not apply")
            continue
        match = re.match(r"^error: (?P<path>.+?): No such file or directory$", line)
        if match:
            add_failure(match.group("path"), None, "No such file or directory")
            continue
        match = re.match(r"^error: patch failed: (?P<path>.+?):(?P<line>\d+):", line)
        if match:
            add_failure(match.group("path"), int(match.group("line")), "patch failed")
            continue
        match = re.match(r"^error: corrupt patch at line (?P<line>\d+)(?:: (?P<reason>.*))?$", line)
        if match:
            failed_hunks.append(
                {
                    "path": "",
                    "line": int(match.group("line")),
                    "reason": match.group("reason") or "corrupt patch",
                    "header": "",
                }
            )
            continue
        match = re.match(r"^error: cannot apply binary patch to '(?P<path>.+?)'$", line)
        if match:
            add_failure(match.group("path"), None, "binary patch failed")
            continue
        match = re.match(r"^error: cannot apply binary patch to (?P<path>.+)$", line)
        if match:
            add_failure(match.group("path"), None, "binary patch failed")
            continue

    return {"failed_files": failed_files, "failed_hunks": failed_hunks}


def summarize_worktree_apply_check(
    source_repo: Path,
    patch_path: Path,
    *,
    pending_path: Path | None = None,
) -> dict[str, object]:
    source_repo_resolved = source_repo.expanduser().resolve()
    patch_resolved = patch_path.expanduser().resolve()
    command = "git apply --check --binary --whitespace=nowarn"
    if not source_repo_resolved.exists() or not patch_resolved.exists():
        return {
            "command": command,
            "rc": 1,
            "ok": False,
            "status": "missing",
            "message": "Patch file or source repository is missing.",
            "output": "",
            "failed_files": [],
            "failed_hunks": [],
            "pending_file": pending_path.as_posix() if pending_path is not None else "",
        }

    rc, out = run_cmd(["git", "apply", "--check", "--binary", "--whitespace=nowarn", str(patch_resolved)], cwd=source_repo_resolved, timeout_sec=120)
    failure_details = {"failed_files": [], "failed_hunks": []}
    if rc != 0:
        failure_details = _worktree_parse_apply_failure(out, patch_resolved)

    return {
        "command": command,
        "rc": rc,
        "ok": rc == 0,
        "status": "ok" if rc == 0 else "failed",
        "message": "git apply --check passed." if rc == 0 else "git apply --check failed.",
        "output": "" if rc == 0 else out.strip(),
        "failed_files": failure_details["failed_files"],
        "failed_hunks": failure_details["failed_hunks"],
        "pending_file": pending_path.as_posix() if pending_path is not None else "",
    }


def summarize_worktree_preflight(
    source_repo: Path,
    patch_path: Path,
    *,
    base_ref: str = "",
    pending_path: Path | None = None,
) -> dict[str, object]:
    source_repo_resolved = source_repo.expanduser().resolve()
    patch_resolved = patch_path.expanduser().resolve()
    source_state = git_repo_state(source_repo_resolved) if source_repo_resolved.exists() else "missing"
    source_head = git_head(source_repo_resolved) if source_repo_resolved.exists() else ""
    patch_hash = ""
    if patch_resolved.exists() and patch_resolved.is_file():
        try:
            patch_hash = sha256_text(patch_resolved.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            patch_hash = ""
    apply_check = summarize_worktree_apply_check(source_repo_resolved, patch_resolved, pending_path=pending_path)
    return {
        "sourceRepoState": source_state,
        "sourceRepoDirty": source_state != "clean",
        "sourceHead": source_head,
        "expectedBaseRef": str(base_ref or "").strip(),
        "patchHash": patch_hash,
        "pendingFile": pending_path.as_posix() if pending_path is not None else "",
        "pendingMarkerPath": pending_path.as_posix() if pending_path is not None else "",
        "applyCheck": apply_check,
    }


def _worktree_safe_name(value: str, fallback: str) -> str:
    text = str(value or "").strip()
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in text)
    return cleaned or fallback


def _generated_worktree_home(repo: Path) -> Path:
    repo_resolved = repo.expanduser().resolve()
    configured_root = os.environ.get("AGENTCLI_WORKTREE_HOME", "").strip()
    if configured_root:
        base = Path(configured_root).expanduser()
    else:
        base = repo_resolved.parent / ".agentcli_worktrees"
    repo_name = _worktree_safe_name(repo_resolved.name or "repo", "repo")
    return (base / repo_name).resolve()


def _worktree_run_dirs(repo: Path) -> list[Path]:
    runs_root = repo.expanduser().resolve() / ".AgentCLI" / "agent_runs"
    if not runs_root.exists():
        return []
    try:
        return sorted([candidate for candidate in runs_root.iterdir() if candidate.is_dir()], key=lambda path: path.name)
    except Exception:
        return []


def _worktree_format_age(seconds: int | float) -> str:
    age_seconds = max(0, int(seconds or 0))
    if age_seconds < 60:
        return f"{age_seconds}s"
    minutes = age_seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h"
    return f"{hours // 24}d"


def _worktree_age_fields(timestamp: int | float) -> dict[str, object]:
    if not timestamp:
        return {"age_seconds": 0, "age": "0s"}
    age_seconds = max(0, int(time.time() - float(timestamp)))
    return {"age_seconds": age_seconds, "age": _worktree_format_age(age_seconds)}


def _worktree_attempt_mtime(attempt_dir: Path) -> float:
    latest = 0.0
    try:
        latest = max(latest, attempt_dir.stat().st_mtime)
    except Exception:
        pass
    try:
        for child in attempt_dir.iterdir():
            try:
                latest = max(latest, child.stat().st_mtime)
            except Exception:
                continue
    except Exception:
        pass
    return latest


def _read_attempt_marker_file(path: Path) -> dict[str, object]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _worktree_attempt_output_evidence(attempt_dir: Path) -> list[str]:
    ignored = {
        ATTEMPT_STARTED_MARKER,
        ATTEMPT_FINISHED_MARKER,
        "validation.json",
        "DEPENDENCY_REQUIRED.md",
    }
    evidence: list[str] = []
    try:
        children = sorted(attempt_dir.iterdir(), key=lambda item: item.name.lower())
    except Exception:
        return evidence
    for child in children:
        if child.name in ignored:
            continue
        if child.is_file():
            evidence.append(child.name)
            continue
        try:
            has_entries = any(True for _ in child.iterdir())
        except Exception:
            has_entries = False
        if has_entries:
            evidence.append(child.name)
    return evidence


def _worktree_task_id_from_artifact_dir(name: str) -> str:
    match = re.match(r"^c\d+_s\d+_(?P<task_id>.+)$", str(name or ""))
    return str(match.group("task_id") if match else "").strip()


def _worktree_attempt_number(name: str) -> int:
    match = re.match(r"^attempt_(?P<attempt>\d+)$", str(name or ""))
    if not match:
        return -1
    try:
        return int(match.group("attempt"))
    except Exception:
        return -1


def _worktree_task_id_from_branch(branch: str) -> str:
    text = str(branch or "").strip()
    if text.startswith("task/"):
        text = text[len("task/") :]
    if "_" in text:
        return text.rsplit("_", 1)[0]
    return text


def _worktree_branch_owners(run_dirs: Sequence[Path]) -> dict[str, dict[str, object]]:
    owners: dict[str, dict[str, object]] = {}

    def remember(branch: str, run_dir: Path, *, source: str, payload: dict[str, object] | None = None) -> None:
        branch_text = str(branch or "").strip()
        if not branch_text or not branch_text.startswith("task/"):
            return
        data = dict(payload or {})
        owners[branch_text] = {
            "owning_run": run_dir.name,
            "owning_run_dir": run_dir.resolve().as_posix(),
            "run_id": run_dir.name,
            "source": source,
            "task_id": str(data.get("task_id") or data.get("taskId") or "").strip(),
            "cycle": data.get("cycle"),
            "step": data.get("step"),
            "event": str(data.get("event") or data.get("type") or "").strip(),
        }

    for run_dir in run_dirs:
        metrics_path = run_dir / "metrics.jsonl"
        if metrics_path.exists():
            try:
                for raw_line in metrics_path.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]:
                    try:
                        event = json.loads(raw_line)
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue
                    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                    branch = str(event.get("branch") or payload.get("branch") or "").strip()
                    remember(branch, run_dir, source="metrics", payload={**dict(payload), **event})
            except Exception:
                pass

        state_path = run_dir / "STATE.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8", errors="replace"))
            except Exception:
                state = {}
            if isinstance(state, dict):
                for bucket in ("pending_review", "failed", "completed"):
                    rows = state.get(bucket)
                    if not isinstance(rows, list):
                        continue
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        remember(str(row.get("branch") or row.get("rescue_branch") or ""), run_dir, source=f"state.{bucket}", payload=row)

    return owners


def _worktree_stale_task_branches(repo: Path, run_dirs: Sequence[Path], source_head: str) -> list[dict[str, object]]:
    owners = _worktree_branch_owners(run_dirs)
    code, out = run_cmd(
        ["git", "for-each-ref", "--format=%(refname:short)%09%(objectname)%09%(committerdate:unix)", "refs/heads/task"],
        cwd=repo,
        timeout_sec=60,
    )
    if code != 0:
        return []

    branches: list[dict[str, object]] = []
    for raw_line in (out or "").splitlines():
        parts = raw_line.split("\t")
        if len(parts) < 2:
            continue
        branch_name = parts[0].strip()
        head_ref = parts[1].strip()
        if not branch_name.startswith("task/") or not head_ref:
            continue
        try:
            commit_ts = float(parts[2]) if len(parts) > 2 and parts[2].strip() else 0.0
        except Exception:
            commit_ts = 0.0
        head_in_source = bool(source_head and (head_ref == source_head or _git_is_ancestor(repo, head_ref, source_head)))
        if not head_in_source:
            continue
        owner = owners.get(branch_name, {})
        task_id = str(owner.get("task_id") or _worktree_task_id_from_branch(branch_name)).strip()
        reason = "branch head is already contained in source HEAD"
        branches.append(
            {
                "branch": branch_name,
                "path": branch_name,
                "head_ref": head_ref,
                "source_head": source_head,
                "status": "merged",
                "reason": reason,
                "task_id": task_id,
                "owning_run": str(owner.get("owning_run") or "unknown"),
                "owning_run_dir": str(owner.get("owning_run_dir") or ""),
                "run_id": str(owner.get("run_id") or owner.get("owning_run") or "unknown"),
                "owner_source": str(owner.get("source") or ""),
                "categories": ["stale"],
                **_worktree_age_fields(commit_ts),
            }
        )
    return branches


def _worktree_interrupted_attempt_dirs(run_dirs: Sequence[Path]) -> list[dict[str, object]]:
    attempts: list[dict[str, object]] = []
    for run_dir in run_dirs:
        tasks_root = run_dir / "tasks"
        if not tasks_root.exists():
            continue
        try:
            task_dirs = [candidate for candidate in tasks_root.iterdir() if candidate.is_dir()]
        except Exception:
            continue
        for task_dir in task_dirs:
            task_id = _worktree_task_id_from_artifact_dir(task_dir.name)
            if not task_id:
                continue
            try:
                attempt_dirs = [candidate for candidate in task_dir.iterdir() if candidate.is_dir() and candidate.name.startswith("attempt_")]
            except Exception:
                continue
            for attempt_dir in attempt_dirs:
                started_path = attempt_dir / ATTEMPT_STARTED_MARKER
                finished_path = attempt_dir / ATTEMPT_FINISHED_MARKER
                if finished_path.exists():
                    continue
                started_payload = _read_attempt_marker_file(started_path)
                if started_path.exists():
                    reason = f"{ATTEMPT_STARTED_MARKER} marker exists without {ATTEMPT_FINISHED_MARKER}"
                    attempts.append(
                        {
                            "path": attempt_dir.resolve().as_posix(),
                            "run_dir": run_dir.resolve().as_posix(),
                            "owning_run": run_dir.name,
                            "run_id": run_dir.name,
                            "task_id": task_id,
                            "attempt": _worktree_attempt_number(attempt_dir.name),
                            "status": "interrupted",
                            "reason": reason,
                            "marker_source": "markers",
                            "started_marker_path": started_path.resolve().as_posix(),
                            "finished_marker_path": finished_path.resolve().as_posix(),
                            "started_at": str(
                                started_payload.get("timestamp")
                                or started_payload.get("started_at")
                                or ""
                            ).strip(),
                            "categories": ["stale"],
                            **_worktree_age_fields(started_path.stat().st_mtime),
                        }
                    )
                    continue
                evidence_files = _worktree_attempt_output_evidence(attempt_dir)
                if not evidence_files:
                    continue
                reason = "legacy attempt directory has output evidence without lifecycle markers"
                attempts.append(
                    {
                        "path": attempt_dir.resolve().as_posix(),
                        "run_dir": run_dir.resolve().as_posix(),
                        "owning_run": run_dir.name,
                        "run_id": run_dir.name,
                        "task_id": task_id,
                        "attempt": _worktree_attempt_number(attempt_dir.name),
                        "status": "interrupted",
                        "reason": reason,
                        "marker_source": "legacy_output",
                        "evidence_files": evidence_files,
                        "categories": ["stale"],
                        **_worktree_age_fields(_worktree_attempt_mtime(attempt_dir)),
                    }
                )
    return attempts


def _cleanup_plan_artifact_path(run_dir: Path) -> Path:
    return run_dir / WORKTREE_CLEANUP_DRY_RUN


def _cleanup_applied_artifact_path(run_dir: Path) -> Path:
    return run_dir / WORKTREE_CLEANUP_APPLIED


def _cleanup_candidate_id(kind: str, value: str) -> str:
    return f"{str(kind or '').strip()}:{str(value or '').strip()}"


def _cleanup_run_state(run_dir: Path) -> dict[str, object]:
    state_path = run_dir / "STATE.json"
    if not state_path.exists():
        return {}
    try:
        return _read_json_payload(state_path)
    except Exception:
        return {}


def _cleanup_review_packets_by_run(repo: Path) -> dict[str, list[dict[str, object]]]:
    try:
        from .pr_queue import list_review_packets, pr_packet_path
    except Exception:
        return {}

    try:
        listing = list_review_packets(repo)
    except Exception:
        return {}
    items = listing.get("items") if isinstance(listing.get("items"), list) else []
    by_run: dict[str, list[dict[str, object]]] = {}
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        run_id = str(raw_item.get("run_id") or "").strip()
        if not run_id:
            continue
        status = str(raw_item.get("status") or "").strip().lower()
        if status in {"approved", "discarded", "merged", "closed"}:
            continue
        packet_id = str(raw_item.get("id") or "").strip()
        by_run.setdefault(run_id, []).append(
            {
                "kind": "review_packet",
                "path": pr_packet_path(repo, packet_id).as_posix() if packet_id else "",
                "packet_id": packet_id,
                "need": str(raw_item.get("need") or "").strip(),
                "status": status or "pr_queued",
            }
        )
    return by_run


def _cleanup_pending_review_evidence(
    repo: Path,
    run_dir: Path,
    *,
    review_packets_by_run: dict[str, list[dict[str, object]]] | None = None,
) -> list[dict[str, object]]:
    evidence: list[dict[str, object]] = []
    state = _cleanup_run_state(run_dir)
    pending_review = state.get("pending_review") if isinstance(state.get("pending_review"), list) else []
    state_path = run_dir / "STATE.json"
    for row in pending_review:
        if not isinstance(row, dict):
            continue
        evidence.append(
            {
                "kind": "pending_review_state",
                "path": state_path.as_posix(),
                "task_id": str(row.get("task") or row.get("task_id") or row.get("taskId") or "").strip(),
                "branch": str(row.get("branch") or "").strip(),
                "rescue_branch": str(row.get("rescue_branch") or row.get("rescueBranch") or "").strip(),
                "validation_artifact": str(
                    row.get("validation_artifact") or row.get("validationArtifact") or ""
                ).strip(),
                "status": str(row.get("task_status") or row.get("status") or "").strip().lower(),
            }
        )

    for item in (review_packets_by_run or {}).get(run_dir.name, []):
        if not isinstance(item, dict):
            continue
        evidence.append(
            {
                "kind": "review_packet",
                "path": str(item.get("path") or "").strip(),
                "packet_id": str(item.get("packet_id") or "").strip(),
                "need": str(item.get("need") or "").strip(),
                "status": str(item.get("status") or "").strip().lower(),
            }
        )
    return evidence


def _cleanup_run_protections(
    repo: Path,
    run_dir: Path,
    *,
    review_packets_by_run: dict[str, list[dict[str, object]]] | None = None,
) -> list[dict[str, object]]:
    protections = _cleanup_pending_review_evidence(
        repo,
        run_dir,
        review_packets_by_run=review_packets_by_run,
    )
    pending_path = run_dir / WORKTREE_MERGE_PENDING
    if pending_path.exists():
        protections.append(
            {
                "kind": "pending_worktree_merge",
                "path": pending_path.as_posix(),
                "detail": "run directory still holds a pending worktree merge marker",
            }
        )
    for artifact_name in (
        WORKTREE_CLEANUP_FAILED_STATUS_MAP["applied_cleanup_failed"]["artifact_name"],
        WORKTREE_CLEANUP_FAILED_STATUS_MAP["discard_cleanup_failed"]["artifact_name"],
    ):
        artifact_path = run_dir / artifact_name
        if not artifact_path.exists():
            continue
        protections.append(
            {
                "kind": "cleanup_failed_artifact",
                "path": artifact_path.as_posix(),
                "detail": "run directory still holds a cleanup-failed worktree artifact",
            }
        )
    return protections


def _cleanup_branch_protections(
    branch: dict[str, object],
    protections: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    branch_name = str(branch.get("branch") or branch.get("path") or "").strip()
    task_id = str(branch.get("task_id") or "").strip()
    matched: list[dict[str, object]] = []
    for raw_item in protections:
        item = dict(raw_item)
        evidence_branch = str(item.get("branch") or item.get("rescue_branch") or "").strip()
        evidence_task_id = str(item.get("task_id") or "").strip()
        if branch_name and evidence_branch == branch_name:
            matched.append(item)
            continue
        if task_id and evidence_task_id == task_id:
            matched.append(item)
    return matched


def _cleanup_attempt_protections(
    attempt: dict[str, object],
    protections: Sequence[dict[str, object]],
) -> list[dict[str, object]]:
    task_id = str(attempt.get("task_id") or "").strip()
    attempt_path = str(attempt.get("path") or "").strip()
    matched: list[dict[str, object]] = []
    for raw_item in protections:
        item = dict(raw_item)
        evidence_task_id = str(item.get("task_id") or "").strip()
        validation_artifact = str(item.get("validation_artifact") or "").strip()
        if task_id and evidence_task_id == task_id:
            matched.append(item)
            continue
        if attempt_path and validation_artifact and validation_artifact.startswith(attempt_path):
            matched.append(item)
            continue
        if str(item.get("kind") or "").strip() in {"review_packet", "pending_worktree_merge", "cleanup_failed_artifact"}:
            matched.append(item)
    return matched


def _cleanup_old_run_age_fields(run_dir: Path) -> dict[str, object]:
    try:
        return _worktree_age_fields(run_dir.stat().st_mtime)
    except Exception:
        return {"age_seconds": 0, "age": "0s"}


def _cleanup_confirmation_phrase(run_dir: Path, candidates: Sequence[dict[str, object]]) -> str:
    seed = json.dumps(
        {
            "run_id": run_dir.name,
            "candidates": [
                {
                    "candidate_id": str(item.get("candidate_id") or "").strip(),
                    "action": str(item.get("action") or "").strip(),
                    "path": str(item.get("path") or "").strip(),
                }
                for item in candidates
                if isinstance(item, dict)
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    token = hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:8].upper()
    return f"{WORKTREE_CLEANUP_APPROVAL_PREFIX} {run_dir.name} {token}"


def _cleanup_candidate_summary(candidates: Sequence[dict[str, object]]) -> dict[str, object]:
    actions: dict[str, int] = {}
    protected = 0
    mutating = 0
    for candidate in candidates:
        action = str(candidate.get("action") or "").strip() or "unknown"
        actions[action] = actions.get(action, 0) + 1
        if bool(candidate.get("protected")):
            protected += 1
        if action not in {"preserve_pending_review_evidence", "remove_with_run_directory"}:
            if action:
                mutating += 1
        elif action == "remove_with_run_directory":
            mutating += 1
    return {
        "total": len(list(candidates)),
        "protected": protected,
        "mutating_candidates": mutating,
        "actions": actions,
    }


def collect_worktree_cleanup_candidates(repo: Path, *, run_dir: Path) -> list[dict[str, object]]:
    repo_resolved = repo.expanduser().resolve()
    plan_run_dir = run_dir.expanduser().resolve()
    run_dirs = _worktree_run_dirs(repo_resolved)
    review_packets_by_run = _cleanup_review_packets_by_run(repo_resolved)
    run_protections = {
        candidate.name: _cleanup_run_protections(
            repo_resolved,
            candidate,
            review_packets_by_run=review_packets_by_run,
        )
        for candidate in run_dirs
    }
    old_run_candidates: dict[str, dict[str, object]] = {}
    candidates: list[dict[str, object]] = []

    for candidate_run_dir in run_dirs:
        if candidate_run_dir.resolve() == plan_run_dir:
            continue
        if candidate_run_dir.name >= plan_run_dir.name:
            continue
        protections = [dict(item) for item in run_protections.get(candidate_run_dir.name, [])]
        protected = bool(protections)
        action = "preserve_pending_review_evidence" if protected else "remove_run_directory"
        exact_action = (
            f"Preserve run directory at {candidate_run_dir.as_posix()} because review evidence is still present."
            if protected
            else f"Remove run directory tree at {candidate_run_dir.as_posix()}."
        )
        old_candidate = {
            "candidate_id": _cleanup_candidate_id("old_run_directory", candidate_run_dir.name),
            "kind": "old_run_directory",
            "path": candidate_run_dir.as_posix(),
            "owning_run": candidate_run_dir.name,
            "owning_run_dir": candidate_run_dir.as_posix(),
            "reason": f"run directory is older than active run {plan_run_dir.name}",
            "protected": protected,
            "pending_review_evidence": protections,
            "action": action,
            "exact_action": exact_action,
            **_cleanup_old_run_age_fields(candidate_run_dir),
        }
        old_run_candidates[candidate_run_dir.as_posix()] = old_candidate

    current_source_head = git_head(repo_resolved)
    for branch in _worktree_stale_task_branches(repo_resolved, run_dirs, current_source_head):
        owning_run = str(branch.get("owning_run") or "").strip()
        protections = _cleanup_branch_protections(branch, run_protections.get(owning_run, []))
        protected = bool(protections)
        branch_name = str(branch.get("branch") or branch.get("path") or "").strip()
        candidates.append(
            {
                "candidate_id": _cleanup_candidate_id("stale_task_branch", branch_name),
                "kind": "stale_task_branch",
                "path": branch_name,
                "branch": branch_name,
                "head_ref": str(branch.get("head_ref") or "").strip(),
                "task_id": str(branch.get("task_id") or "").strip(),
                "owning_run": owning_run or "unknown",
                "owning_run_dir": str(branch.get("owning_run_dir") or "").strip(),
                "reason": str(branch.get("reason") or "branch head is already contained in source HEAD").strip(),
                "protected": protected,
                "pending_review_evidence": protections,
                "action": "preserve_pending_review_evidence" if protected else "delete_branch",
                "exact_action": (
                    f"Preserve branch {branch_name} because pending review evidence still references it."
                    if protected
                    else f"git branch -D {branch_name}"
                ),
                "status": str(branch.get("status") or "").strip(),
                "source_head": str(branch.get("source_head") or "").strip(),
                "age": str(branch.get("age") or "0s"),
                "age_seconds": int(branch.get("age_seconds") or 0),
            }
        )

    for attempt in _worktree_interrupted_attempt_dirs(run_dirs):
        owning_run_dir = str(attempt.get("run_dir") or "").strip()
        parent_run_candidate = old_run_candidates.get(owning_run_dir, {})
        protections = _cleanup_attempt_protections(
            attempt,
            run_protections.get(str(attempt.get("owning_run") or "").strip(), []),
        )
        protected = bool(protections)
        if parent_run_candidate and str(parent_run_candidate.get("action") or "").strip() == "remove_run_directory":
            action = "remove_with_run_directory"
            exact_action = f"Remove with run directory {owning_run_dir}."
            protected = False
        elif protected or (parent_run_candidate and bool(parent_run_candidate.get("protected"))):
            action = "preserve_pending_review_evidence"
            exact_action = f"Preserve attempt directory at {attempt.get('path')} because pending review evidence is still present."
        else:
            action = "remove_directory"
            exact_action = f"Remove attempt directory tree at {attempt.get('path')}."
        candidates.append(
            {
                "candidate_id": _cleanup_candidate_id("interrupted_attempt_directory", str(attempt.get("path") or "")),
                "kind": "interrupted_attempt_directory",
                "path": str(attempt.get("path") or "").strip(),
                "task_id": str(attempt.get("task_id") or "").strip(),
                "attempt": int(attempt.get("attempt") or -1),
                "owning_run": str(attempt.get("owning_run") or "").strip(),
                "owning_run_dir": owning_run_dir,
                "reason": str(attempt.get("reason") or "missing validation artifact").strip(),
                "protected": protected or (parent_run_candidate and bool(parent_run_candidate.get("protected"))),
                "pending_review_evidence": protections if protected else [dict(item) for item in parent_run_candidate.get("pending_review_evidence", [])] if parent_run_candidate and bool(parent_run_candidate.get("protected")) else [],
                "action": action,
                "covered_by": str(parent_run_candidate.get("candidate_id") or "").strip() if action == "remove_with_run_directory" else "",
                "exact_action": exact_action,
                "status": str(attempt.get("status") or "").strip(),
                "age": str(attempt.get("age") or "0s"),
                "age_seconds": int(attempt.get("age_seconds") or 0),
            }
        )

    candidates.extend(old_run_candidates.values())
    return sorted(
        candidates,
        key=lambda item: (
            str(item.get("kind") or ""),
            str(item.get("owning_run") or ""),
            str(item.get("path") or item.get("branch") or ""),
        ),
    )


def build_worktree_cleanup_dry_run(repo: Path, *, run_dir: Path) -> dict[str, object]:
    repo_resolved = repo.expanduser().resolve()
    run_dir_resolved = run_dir.expanduser().resolve()
    candidates = collect_worktree_cleanup_candidates(repo_resolved, run_dir=run_dir_resolved)
    payload = {
        "schema_version": 1,
        "status": "dry_run",
        "created_at": now_iso(),
        "source_repo": repo_resolved.as_posix(),
        "run_dir": run_dir_resolved.as_posix(),
        "approval_phrase": _cleanup_confirmation_phrase(run_dir_resolved, candidates),
        "candidates": candidates,
        "summary": _cleanup_candidate_summary(candidates),
    }
    safe_write_text(
        _cleanup_plan_artifact_path(run_dir_resolved),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return payload


def _cleanup_safe_directory_remove(target: Path, *, allowed_root: Path) -> str:
    resolved_target = target.expanduser().resolve()
    resolved_root = allowed_root.expanduser().resolve()
    if not _path_is_relative_to(resolved_target, resolved_root):
        raise RuntimeError(
            f"refusing to remove cleanup target outside allowed root: target={resolved_target} allowed_root={resolved_root}"
        )
    if not resolved_target.exists():
        return "already_absent"
    shutil.rmtree(resolved_target)
    return "deleted"


def _cleanup_safe_branch_delete(repo: Path, branch_name: str) -> str:
    branch_text = str(branch_name or "").strip()
    if not branch_text.startswith("task/"):
        raise RuntimeError(f"refusing to delete non-task branch: {branch_text}")
    if git_current_branch(repo) == branch_text:
        raise RuntimeError(f"refusing to delete checked-out branch: {branch_text}")
    rc, out = run_cmd(["git", "branch", "-D", branch_text], cwd=repo, timeout_sec=60)
    if rc != 0:
        if "not found" in str(out or "").lower() or "not a valid branch" in str(out or "").lower():
            return "already_absent"
        raise RuntimeError(f"git branch -D failed for {branch_text}: {str(out or '').strip()}")
    return "deleted"


def _cleanup_applied_payload(
    dry_run: dict[str, object],
    *,
    status: str,
    approval_received: str,
    results: Sequence[dict[str, object]],
    failures: Sequence[dict[str, object]],
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": status,
        "created_at": now_iso(),
        "source_repo": str(dry_run.get("source_repo") or "").strip(),
        "run_dir": str(dry_run.get("run_dir") or "").strip(),
        "dry_run_artifact": _cleanup_plan_artifact_path(Path(str(dry_run.get("run_dir") or ""))).as_posix(),
        "approval_phrase": str(dry_run.get("approval_phrase") or "").strip(),
        "approval_received": str(approval_received or "").strip(),
        "candidates": [dict(item) for item in dry_run.get("candidates", []) if isinstance(item, dict)],
        "results": [dict(item) for item in results],
        "failures": [dict(item) for item in failures],
        "summary": {
            "planned": len([item for item in dry_run.get("candidates", []) if isinstance(item, dict)]),
            "results": len(list(results)),
            "failures": len(list(failures)),
            "deleted": len([item for item in results if str(item.get("result") or "") in {"deleted", "removed_with_parent"}]),
            "preserved": len([item for item in results if str(item.get("result") or "") == "preserved"]),
            "already_absent": len([item for item in results if str(item.get("result") or "") == "already_absent"]),
        },
    }


def apply_worktree_cleanup(repo: Path, *, run_dir: Path, approval_phrase: str = "") -> dict[str, object]:
    repo_resolved = repo.expanduser().resolve()
    run_dir_resolved = run_dir.expanduser().resolve()
    dry_run_path = _cleanup_plan_artifact_path(run_dir_resolved)
    if dry_run_path.exists():
        try:
            dry_run = _read_json_payload(dry_run_path)
        except Exception:
            dry_run = build_worktree_cleanup_dry_run(repo_resolved, run_dir=run_dir_resolved)
    else:
        dry_run = build_worktree_cleanup_dry_run(repo_resolved, run_dir=run_dir_resolved)

    expected_phrase = str(dry_run.get("approval_phrase") or "").strip()
    results: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    applied_path = _cleanup_applied_artifact_path(run_dir_resolved)
    provided_phrase = str(approval_phrase or "").strip()

    if provided_phrase != expected_phrase:
        failures.append(
            {
                "code": "approval_phrase_mismatch",
                "message": "Cleanup apply rejected because the confirmation phrase did not match the dry-run artifact.",
                "expected": expected_phrase,
                "received": provided_phrase,
            }
        )
        payload = _cleanup_applied_payload(
            dry_run,
            status="approval_rejected",
            approval_received=provided_phrase,
            results=results,
            failures=failures,
        )
        safe_write_text(applied_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return payload

    run_root = repo_resolved / ".AgentCLI" / "agent_runs"
    parent_results: dict[str, dict[str, object]] = {}
    covered_candidates: list[dict[str, object]] = []
    candidates = [dict(item) for item in dry_run.get("candidates", []) if isinstance(item, dict)]
    for candidate in candidates:
        action = str(candidate.get("action") or "").strip()
        result = {
            "candidate_id": str(candidate.get("candidate_id") or "").strip(),
            "kind": str(candidate.get("kind") or "").strip(),
            "path": str(candidate.get("path") or "").strip(),
            "owning_run": str(candidate.get("owning_run") or "").strip(),
            "action": action,
            "exact_action": str(candidate.get("exact_action") or "").strip(),
            "protected": bool(candidate.get("protected")),
        }
        if action == "remove_with_run_directory":
            covered_candidates.append(candidate)
            continue
        if action == "preserve_pending_review_evidence":
            result["ok"] = True
            result["result"] = "preserved"
            results.append(result)
            parent_results[result["candidate_id"]] = result
            continue
        try:
            if action == "delete_branch":
                result["result"] = _cleanup_safe_branch_delete(repo_resolved, str(candidate.get("branch") or candidate.get("path") or ""))
            elif action == "remove_directory":
                owner_run_dir = Path(str(candidate.get("owning_run_dir") or "")).expanduser().resolve()
                result["result"] = _cleanup_safe_directory_remove(
                    Path(str(candidate.get("path") or "")),
                    allowed_root=owner_run_dir / "tasks",
                )
            elif action == "remove_run_directory":
                result["result"] = _cleanup_safe_directory_remove(
                    Path(str(candidate.get("path") or "")),
                    allowed_root=run_root,
                )
            else:
                raise RuntimeError(f"unsupported cleanup action: {action}")
            result["ok"] = True
        except Exception as ex:
            result["ok"] = False
            result["result"] = "failed"
            result["error"] = str(ex).strip() or ex.__class__.__name__
            failures.append(
                {
                    "candidate_id": str(result.get("candidate_id") or "").strip(),
                    "kind": str(result.get("kind") or "").strip(),
                    "path": str(result.get("path") or "").strip(),
                    "action": action,
                    "error": str(result.get("error") or "").strip(),
                }
            )
        results.append(result)
        parent_results[str(result.get("candidate_id") or "").strip()] = result

    for candidate in covered_candidates:
        parent_id = str(candidate.get("covered_by") or "").strip()
        parent_result = parent_results.get(parent_id, {})
        ok = bool(parent_result.get("ok"))
        result = {
            "candidate_id": str(candidate.get("candidate_id") or "").strip(),
            "kind": str(candidate.get("kind") or "").strip(),
            "path": str(candidate.get("path") or "").strip(),
            "owning_run": str(candidate.get("owning_run") or "").strip(),
            "action": str(candidate.get("action") or "").strip(),
            "exact_action": str(candidate.get("exact_action") or "").strip(),
            "covered_by": parent_id,
            "protected": False,
            "ok": ok,
            "result": "removed_with_parent" if ok else "failed",
        }
        if not ok:
            result["error"] = (
                f"parent cleanup action {parent_id or '(unknown)'} did not complete"
            )
            failures.append(
                {
                    "candidate_id": str(result.get("candidate_id") or "").strip(),
                    "kind": str(result.get("kind") or "").strip(),
                    "path": str(result.get("path") or "").strip(),
                    "action": str(result.get("action") or "").strip(),
                    "error": str(result.get("error") or "").strip(),
                }
            )
        results.append(result)

    status = "applied_with_failures" if failures else "applied"
    payload = _cleanup_applied_payload(
        dry_run,
        status=status,
        approval_received=provided_phrase,
        results=results,
        failures=failures,
    )
    safe_write_text(applied_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return payload


def _worktree_pending_stale_reason(payload: dict[str, object], pending_path: Path) -> str:
    run_dir_value = _worktree_text_path(payload.get("run_dir") or payload.get("runDir"))
    patch_path = _worktree_text_path(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch"))
    worktree_dir = _worktree_text_path(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree"))
    status = str(payload.get("status") or "").strip().lower()
    required_fields = {
        "source_repo": ("source_repo", "sourceRepo"),
        "run_dir": ("run_dir", "runDir"),
        "worktree_dir": ("worktree_dir", "worktreeDir", "worktree"),
        "patch_path": ("patch_path", "patchPath", "patch"),
        "base_ref": ("base_ref", "baseRef"),
        "head_ref": ("head_ref", "headRef"),
    }
    missing_fields = [field for field, aliases in required_fields.items() if not _payload_text(payload, *aliases)]
    if missing_fields:
        return f"missing required fields ({', '.join(missing_fields)})"
    if status != "pending":
        return f"pending marker status must be pending (got {status or 'empty'})"
    if worktree_dir and not Path(worktree_dir).exists():
        return f"worktree directory is missing ({worktree_dir})"
    if not patch_path or not Path(patch_path).exists():
        return "patch path is missing or no longer exists"
    if run_dir_value:
        expected_pending = Path(run_dir_value) / WORKTREE_MERGE_PENDING
        if pending_path.resolve() != expected_pending.resolve() and not expected_pending.exists():
            return f"run-local pending file is missing ({expected_pending.as_posix()})"
    return ""


def _worktree_diagnostics_issue(
    kind: str,
    message: str,
    *,
    severity: str = "warn",
    path: str = "",
    categories: Sequence[str] | None = None,
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    issue: dict[str, object] = {
        "kind": str(kind),
        "severity": str(severity or "warn"),
        "message": str(message),
    }
    if path:
        issue["path"] = str(path)
    normalized_categories = _worktree_normalize_diagnostic_categories(categories or [])
    if normalized_categories:
        issue["categories"] = normalized_categories
    if details:
        issue["details"] = dict(details)
    return issue


def _worktree_filter_diagnostics_result(
    diagnostics: dict[str, object],
    categories: Sequence[str] | None = None,
) -> dict[str, object]:
    selected_categories = _worktree_normalize_diagnostic_categories(categories or [])
    pending_markers = [dict(item) for item in diagnostics.get("pending_markers", []) if _worktree_diagnostic_matches_categories(dict(item), set(selected_categories))]
    cleanup_failed = [dict(item) for item in diagnostics.get("cleanup_failed", []) if _worktree_diagnostic_matches_categories(dict(item), set(selected_categories))]
    generated_worktrees = [dict(item) for item in diagnostics.get("generated_worktrees", []) if _worktree_diagnostic_matches_categories(dict(item), set(selected_categories))]
    stale_task_branches = [dict(item) for item in diagnostics.get("stale_task_branches", []) if _worktree_diagnostic_matches_categories(dict(item), set(selected_categories))]
    interrupted_attempts = [dict(item) for item in diagnostics.get("interrupted_attempts", []) if _worktree_diagnostic_matches_categories(dict(item), set(selected_categories))]
    issues = [dict(item) for item in diagnostics.get("issues", []) if _worktree_diagnostic_matches_categories(dict(item), set(selected_categories))]
    issues_sorted = sorted(
        issues,
        key=lambda item: (
            0 if item.get("severity") == "error" else 1,
            str(item.get("kind") or ""),
            str(item.get("path") or ""),
        ),
    )
    severity_order = [str(issue.get("severity") or "warn") for issue in issues_sorted]
    if any(severity == "error" for severity in severity_order):
        status = "error"
    elif issues_sorted:
        status = "warning"
    else:
        status = "ok"
    summary = dict(diagnostics.get("summary") or {})
    summary.update(
        {
            "pending_markers": len(pending_markers),
            "stale_pending_markers": sum(1 for marker in pending_markers if "stale" in _worktree_normalize_diagnostic_categories(marker.get("categories"))),
            "missing_patches": sum(1 for issue in issues_sorted if issue.get("kind") == "missing_patch"),
            "cleanup_failed": len(cleanup_failed),
            "generated_worktrees": len(generated_worktrees),
            "orphaned_worktrees": sum(1 for worktree in generated_worktrees if "orphaned" in _worktree_normalize_diagnostic_categories(worktree.get("categories"))),
            "stale_task_branches": len(stale_task_branches),
            "interrupted_attempts": len(interrupted_attempts),
            "issue_count": len(issues_sorted),
            "healthy": not issues_sorted,
            "category_counts": _worktree_diagnostic_category_counts(pending_markers, cleanup_failed, generated_worktrees, stale_task_branches, interrupted_attempts, issues_sorted),
        }
    )
    summary["categoryCounts"] = summary["category_counts"]
    filtered = {
        **dict(diagnostics),
        "status": status,
        "summary": summary,
        "issues": issues_sorted,
        "pending_markers": pending_markers,
        "cleanup_failed": cleanup_failed,
        "generated_worktrees": generated_worktrees,
        "stale_task_branches": stale_task_branches,
        "interrupted_attempts": interrupted_attempts,
        "filters": {
            "categories": selected_categories,
            "available_categories": list(WORKTREE_DIAGNOSTIC_CATEGORY_ORDER),
            "availableCategories": list(WORKTREE_DIAGNOSTIC_CATEGORY_ORDER),
        },
    }
    return filtered


def _worktree_cleanup_artifact_dirs(repo: Path, run_dir: Path | None = None) -> list[Path]:
    search_dirs: list[Path] = []
    if run_dir is not None:
        search_dirs.append(run_dir.expanduser().resolve())
    search_dirs.append((repo / ".AgentCLI").expanduser().resolve())
    runs_root = repo / ".AgentCLI" / "agent_runs"
    if runs_root.exists():
        for candidate in sorted([path for path in runs_root.iterdir() if path.is_dir()], key=lambda path: path.name, reverse=True):
            search_dirs.append(candidate.expanduser().resolve())
    uniq: list[Path] = []
    seen: set[str] = set()
    for directory in search_dirs:
        key = directory.as_posix()
        if key not in seen:
            seen.add(key)
            uniq.append(directory)
    return uniq


def _path_exists_safely(path_text: str) -> bool:
    text = str(path_text or "").strip()
    if not text:
        return False
    try:
        return Path(text).expanduser().exists()
    except Exception:
        return False


def _pending_marker_reconciliation_artifact_path(pending_path: Path) -> Path:
    target = pending_path.with_name(WORKTREE_MERGE_PENDING_RECONCILED)
    if not target.exists():
        return target
    suffix = hashlib.sha256(f"{pending_path.as_posix()}:{now_iso()}".encode("utf-8")).hexdigest()[:8]
    return pending_path.with_name(f"WORKTREE_MERGE_PENDING_RECONCILED_{_safe_ts()}_{suffix}.json")


def _pending_marker_source_contains_head(repo: Path, marker: dict[str, object], source_head: str) -> tuple[bool, str, str]:
    head_ref = str(marker.get("head_ref") or marker.get("headRef") or "").strip()
    base_ref = str(marker.get("base_ref") or marker.get("baseRef") or marker.get("expected_head") or marker.get("expectedHead") or "").strip()
    if not head_ref or not source_head or head_ref == base_ref:
        return False, source_head, head_ref
    source_repo_text = _worktree_text_path(marker.get("source_repo") or marker.get("sourceRepo")) or repo.as_posix()
    try:
        source_repo = Path(source_repo_text).expanduser().resolve()
    except Exception:
        source_repo = repo
    marker_source_head = source_head if source_repo == repo else git_head(source_repo)
    if not marker_source_head:
        return False, marker_source_head, head_ref
    return bool(head_ref == marker_source_head or _git_is_ancestor(source_repo, head_ref, marker_source_head)), marker_source_head, head_ref


def _pending_marker_reconciliation_reason(repo: Path, marker: dict[str, object], source_head: str) -> tuple[str, dict[str, object]]:
    reason_text = str(marker.get("reason") or "").strip().lower()
    patch_path = _worktree_text_path(marker.get("patch_path") or marker.get("patchPath") or marker.get("patch"))
    worktree_dir = _worktree_text_path(marker.get("worktree_dir") or marker.get("worktreeDir") or marker.get("worktree"))
    is_stale = bool(marker.get("stale")) or str(marker.get("status") or "").strip().lower() == "stale"
    patch_missing = bool(is_stale and patch_path and not _path_exists_safely(patch_path))
    worktree_missing = bool(is_stale and worktree_dir and not _path_exists_safely(worktree_dir))
    if patch_missing or "patch path is missing" in reason_text:
        return "missing_patch", {"patch_path": patch_path}
    if worktree_missing or "worktree directory is missing" in reason_text:
        return "missing_worktree", {"worktree_dir": worktree_dir}
    source_contains, marker_source_head, head_ref = _pending_marker_source_contains_head(repo, marker, source_head)
    if source_contains:
        return "source_head_contains_pending_head", {"source_head": marker_source_head, "head_ref": head_ref}
    return "", {}


def reconcile_stale_pending_worktree_markers(
    repo: Path,
    *,
    diagnostics: dict[str, object] | None = None,
    source_head: str = "",
) -> list[dict[str, object]]:
    """Clear pending merge markers that diagnostics prove are no longer actionable.

    This is intentionally separate from ``scan_worktree_diagnostics`` so normal
    doctor/API reads stay read-only. Startup readiness calls this after scanning
    and records an audit artifact beside each removed marker.
    """

    repo_resolved = repo.expanduser().resolve()
    scan = diagnostics if isinstance(diagnostics, dict) else scan_worktree_diagnostics(repo_resolved)
    source_head_text = str(source_head or git_head(repo_resolved) or "").strip()
    pending_markers = scan.get("pending_markers") if isinstance(scan.get("pending_markers"), list) else []
    reconciled: list[dict[str, object]] = []
    seen_paths: set[str] = set()

    for raw_marker in pending_markers:
        if not isinstance(raw_marker, dict):
            continue
        marker = dict(raw_marker)
        marker_path_text = _worktree_text_path(marker.get("path"))
        if not marker_path_text or marker_path_text in seen_paths:
            continue
        marker_path = Path(marker_path_text)
        if not marker_path.exists():
            continue
        reason, reason_details = _pending_marker_reconciliation_reason(repo_resolved, marker, source_head_text)
        if not reason:
            continue
        try:
            payload = _read_json_payload(marker_path)
        except Exception as ex:
            payload = {"read_error": str(ex).strip() or ex.__class__.__name__}

        companion_paths = _pending_companion_paths(payload if isinstance(payload, dict) else {}, marker_path)
        removed_paths: list[str] = []
        artifact_paths: list[str] = []
        reconciled_at = now_iso()
        for companion in companion_paths:
            companion_key = companion.resolve().as_posix() if companion.exists() else companion.as_posix()
            seen_paths.add(companion_key)
            if not companion.exists():
                continue
            audit_payload = {
                "schema_version": 1,
                "status": "reconciled",
                "reconciled_at": reconciled_at,
                "reconciliation_reason": reason,
                "reason": reason,
                "reason_details": dict(reason_details),
                "source_repo": repo_resolved.as_posix(),
                "source_head": source_head_text,
                "pending_marker_path": companion.resolve().as_posix(),
                "removed_pending_paths": [path.as_posix() for path in companion_paths],
                "original_pending_payload": dict(payload) if isinstance(payload, dict) else {},
            }
            artifact_path = _pending_marker_reconciliation_artifact_path(companion)
            safe_write_text(artifact_path, json.dumps(audit_payload, ensure_ascii=False, indent=2) + "\n")
            artifact_paths.append(artifact_path.resolve().as_posix())
            removed_paths.append(companion.resolve().as_posix())
            try:
                companion.unlink()
            except Exception:
                pass

        if removed_paths:
            reconciled.append(
                {
                    "status": "reconciled",
                    "reason": reason,
                    "reason_details": dict(reason_details),
                    "marker_path": marker_path_text,
                    "removed_paths": removed_paths,
                    "artifact_paths": artifact_paths,
                    "source_head": source_head_text,
                    "head_ref": str(marker.get("head_ref") or "").strip(),
                    "run_dir": str(marker.get("run_dir") or "").strip(),
                    "worktree_dir": str(marker.get("worktree_dir") or "").strip(),
                    "patch_path": str(marker.get("patch_path") or "").strip(),
                    "reconciled_at": reconciled_at,
                }
            )

    return reconciled


def _cleanup_failed_reconciliation_state(
    payload: dict[str, object],
    artifact_path: Path,
) -> dict[str, object]:
    artifact_status = _payload_text(payload, "status").lower()
    if not artifact_status:
        artifact_name = artifact_path.name
        artifact_status = next(
            (
                status_name
                for status_name, details in WORKTREE_CLEANUP_FAILED_STATUS_MAP.items()
                if details["artifact_name"] == artifact_name
            ),
            "cleanup_failed",
        )
    artifact_mapping = WORKTREE_CLEANUP_FAILED_STATUS_MAP.get(artifact_status, {})
    final_status = str(artifact_mapping.get("final_status") or "").strip().lower()
    worktree_dir = _worktree_text_path(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree"))
    cleanup_path = _worktree_text_path(payload.get("cleanup_path") or payload.get("cleanupPath") or worktree_dir)
    cleanup_details = payload.get("cleanup_details") if isinstance(payload.get("cleanup_details"), dict) else payload.get("cleanupDetails")
    if not isinstance(cleanup_details, dict):
        cleanup_details = {}
    git_worktree_registration = cleanup_details.get("git_worktree_registration")
    if not isinstance(git_worktree_registration, dict):
        git_worktree_registration = {}
    pending_candidates = _pending_companion_paths(payload, artifact_path.with_name(WORKTREE_MERGE_PENDING))
    existing_pending_markers = [
        candidate.resolve().as_posix() if candidate.exists() else candidate.as_posix()
        for candidate in pending_candidates
        if candidate.exists()
    ]
    worktree_exists = _path_exists_safely(worktree_dir)
    cleanup_path_exists = _path_exists_safely(cleanup_path)
    residual_directory = bool(cleanup_details.get("residual_directory"))
    if git_worktree_registration:
        registered = git_worktree_registration.get("registered")
        if registered is False and (worktree_exists or cleanup_path_exists):
            residual_directory = True
    blocking_paths: list[str] = []
    if worktree_exists and worktree_dir:
        blocking_paths.append(worktree_dir)
    if cleanup_path_exists and cleanup_path and cleanup_path not in blocking_paths:
        blocking_paths.append(cleanup_path)
    for marker_path in existing_pending_markers:
        if marker_path not in blocking_paths:
            blocking_paths.append(marker_path)
    worktree_state = "reconciled" if not worktree_exists and not cleanup_path_exists else "present"
    marker_state = "reconciled" if not existing_pending_markers else "present"
    reconciled = bool(final_status and worktree_state == "reconciled" and marker_state == "reconciled")
    return {
        "artifact_path": artifact_path.resolve().as_posix(),
        "artifact_status": artifact_status,
        "final_status": final_status,
        "final_artifact_name": str(artifact_mapping.get("final_artifact_name") or "").strip(),
        "worktree_dir": worktree_dir,
        "worktree_exists": worktree_exists,
        "cleanup_path": cleanup_path,
        "cleanup_path_exists": cleanup_path_exists,
        "git_worktree_registration": dict(git_worktree_registration),
        "residual_directory": residual_directory,
        "pending_marker_paths": [candidate.as_posix() for candidate in pending_candidates],
        "existing_pending_markers": existing_pending_markers,
        "marker_state": marker_state,
        "worktree_state": worktree_state,
        "blocking_paths": blocking_paths,
        "reconciled": reconciled,
        "reconciled_from": artifact_status if reconciled else "",
    }


def reconcile_cleanup_failed_artifacts(repo: Path, run_dir: Path | None = None) -> list[dict[str, object]]:
    repo_resolved = repo.expanduser().resolve()
    reconciled_states: list[dict[str, object]] = []
    for directory in _worktree_cleanup_artifact_dirs(repo_resolved, run_dir):
        for status_name, mapping in WORKTREE_CLEANUP_FAILED_STATUS_MAP.items():
            artifact_path = directory / mapping["artifact_name"]
            if not artifact_path.exists():
                continue
            try:
                payload = _read_json_payload(artifact_path)
            except Exception:
                reconciled_states.append(
                    {
                        "artifact_path": artifact_path.resolve().as_posix(),
                        "artifact_status": status_name,
                        "final_status": mapping["final_status"],
                        "reconciled": False,
                        "malformed": True,
                    }
                )
                continue
            state = _cleanup_failed_reconciliation_state(payload, artifact_path)
            if state.get("reconciled") and state.get("final_status") and state.get("final_artifact_name"):
                final_artifact = artifact_path.with_name(str(state["final_artifact_name"]))
                final_payload: dict[str, object] = {}
                if final_artifact.exists():
                    try:
                        final_payload = _read_json_payload(final_artifact)
                    except Exception:
                        final_payload = {}
                if not final_payload:
                    final_payload = dict(payload)
                final_payload["status"] = str(state["final_status"])
                final_payload["resolved_at"] = now_iso()
                final_payload["cleanup_reconciled_at"] = now_iso()
                final_payload["cleanup_reconciled_from"] = str(state["artifact_status"])
                final_payload["cleanup_path"] = _worktree_text_path(
                    final_payload.get("worktree_dir") or final_payload.get("worktreeDir") or final_payload.get("worktree")
                )
                final_payload["cleanup_reconciliation"] = {
                    **state,
                    "reconciled": True,
                    "reconciled_from": str(state["artifact_status"]),
                }
                final_payload["resolution_actions"] = worktree_resolution_actions(
                    str(state["final_status"]),
                    source_repo=_payload_text(final_payload, "source_repo", "sourceRepo"),
                    worktree_dir=_worktree_text_path(final_payload.get("worktree_dir") or final_payload.get("worktreeDir") or final_payload.get("worktree")),
                    cleanup_path=_worktree_text_path(final_payload.get("worktree_dir") or final_payload.get("worktreeDir") or final_payload.get("worktree")),
                    pending_paths=state.get("pending_marker_paths") if isinstance(state.get("pending_marker_paths"), list) else [],
                    artifact_path=artifact_path.resolve().as_posix(),
                    reconciliation=final_payload["cleanup_reconciliation"] if isinstance(final_payload.get("cleanup_reconciliation"), dict) else state,
                )
                final_payload.pop("cleanup_error", None)
                final_payload.pop("cleanup_message", None)
                final_payload.pop("cleanup_details", None)
                final_payload.pop("cleanup_attempts", None)
                safe_write_text(final_artifact, json.dumps(final_payload, ensure_ascii=False, indent=2) + "\n")
                try:
                    artifact_path.unlink()
                except Exception:
                    pass
                state["cleared"] = True
                state["final_artifact_path"] = final_artifact.resolve().as_posix()
            reconciled_states.append(state)
    return reconciled_states


def scan_worktree_diagnostics(repo: Path, categories: Sequence[str] | str | None = None) -> dict[str, object]:
    repo_resolved = repo.expanduser().resolve()
    source_root = git_show_toplevel(repo_resolved) or repo_resolved.as_posix()
    scanned_at = now_iso()
    run_dirs = _worktree_run_dirs(repo_resolved)
    reconcile_cleanup_failed_artifacts(repo_resolved)
    central_pending = repo_resolved / ".AgentCLI" / WORKTREE_MERGE_PENDING
    generated_root = _generated_worktree_home(repo_resolved)
    generated_worktrees: list[dict[str, object]] = []
    pending_markers: list[dict[str, object]] = []
    cleanup_failed: list[dict[str, object]] = []
    stale_task_branches: list[dict[str, object]] = []
    interrupted_attempts: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    referenced_worktrees: set[str] = set()
    seen_missing_patch_paths: set[str] = set()

    def add_issue(
        kind: str,
        message: str,
        *,
        severity: str = "warn",
        path: str = "",
        categories: Sequence[str] | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        issues.append(
            _worktree_diagnostics_issue(
                kind,
                message,
                severity=severity,
                path=path,
                categories=categories,
                details=details,
            )
        )

    def register_reference(worktree_dir: str) -> None:
        if worktree_dir:
            referenced_worktrees.add(_worktree_text_path(worktree_dir))

    def register_missing_patch(
        patch_path: str,
        *,
        reason: str,
        marker_path: str,
        run_dir: str,
        scope: str,
        categories: Sequence[str] | None = None,
    ) -> None:
        patch_path_text = _worktree_text_path(patch_path)
        if not patch_path_text or patch_path_text in seen_missing_patch_paths:
            return
        seen_missing_patch_paths.add(patch_path_text)
        add_issue(
            "missing_patch",
            f"Pending worktree patch is missing: {reason}",
            severity="warn",
            path=patch_path_text,
            categories=categories,
            details={
                "marker": marker_path,
                "run_dir": run_dir,
                "scope": scope,
                "patch_path": patch_path_text,
            },
        )

    def scan_pending_marker(path: Path, *, scope: str, run_dir_text: str = "") -> None:
        marker_path = path.resolve().as_posix()
        if not path.exists():
            return
        try:
            payload = read_pending_worktree_merge(path)
            if not isinstance(payload, dict):
                raise TypeError("Pending merge payload must be a JSON object.")
        except Exception as ex:
            reason = str(ex).strip() or ex.__class__.__name__
            marker_categories = ["pending", "stale"]
            resolution_actions = worktree_resolution_actions(
                "stale_pending_marker",
                pending_paths=[marker_path],
            )
            pending_markers.append(
                {
                    "path": marker_path,
                    "scope": scope,
                    "status": "malformed",
                    "reason": reason,
                    "run_dir": run_dir_text,
                    "source_repo": "",
                    "worktree_dir": "",
                    "patch_path": "",
                    "base_ref": "",
                    "head_ref": "",
                    "exists": True,
                    "stale": True,
                    "categories": marker_categories,
                    "resolution_actions": resolution_actions,
                }
            )
            add_issue(
                "stale_pending_marker",
                f"Pending worktree marker is malformed: {reason}",
                severity="warn",
                path=marker_path,
                categories=marker_categories,
                details={"scope": scope, "run_dir": run_dir_text, "reason": reason},
            )
            return

        payload_run_dir = _worktree_text_path(payload.get("run_dir") or payload.get("runDir") or run_dir_text)
        payload_worktree_dir = _worktree_text_path(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree"))
        payload_patch_path = _worktree_text_path(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch"))
        payload_source_repo = _worktree_text_path(payload.get("source_repo") or payload.get("sourceRepo"))
        payload_base_ref = _payload_text(payload, "base_ref", "baseRef")
        payload_head_ref = _payload_text(payload, "head_ref", "headRef")
        reason = _worktree_pending_stale_reason(payload, path)
        is_stale = bool(reason)
        marker_categories = ["pending"]
        if is_stale:
            marker_categories.append("stale")
        else:
            marker_categories.append("active")
        if payload_patch_path and not Path(payload_patch_path).exists():
            marker_categories.append("missing_patch")
        resolution_actions = worktree_resolution_actions(
            "stale_pending_marker" if is_stale else "pending",
            pending_paths=[marker_path],
        )
        pending_markers.append(
            {
                "path": marker_path,
                "scope": scope,
                "status": "stale" if is_stale else "pending",
                "reason": reason,
                "run_dir": payload_run_dir,
                "source_repo": payload_source_repo,
                "worktree_dir": payload_worktree_dir,
                "patch_path": payload_patch_path,
                "base_ref": payload_base_ref,
                "head_ref": payload_head_ref,
                "exists": True,
                "stale": is_stale,
                "categories": marker_categories,
                "resolution_actions": resolution_actions,
            }
        )
        register_reference(payload_worktree_dir)
        if reason:
            add_issue(
                "stale_pending_marker",
                f"Pending worktree marker is stale: {reason}",
                severity="warn",
                path=marker_path,
                categories=[category for category in marker_categories if category != "active"],
                details={
                    "scope": scope,
                    "run_dir": payload_run_dir,
                    "worktree_dir": payload_worktree_dir,
                    "patch_path": payload_patch_path,
                    "reason": reason,
                },
            )
        if payload_patch_path and not Path(payload_patch_path).exists():
            register_missing_patch(
                payload_patch_path,
                reason=reason or "patch path is missing or no longer exists",
                marker_path=marker_path,
                run_dir=payload_run_dir,
                scope=scope,
                categories=marker_categories,
            )

    scan_pending_marker(central_pending, scope="central", run_dir_text="")
    for run_dir in run_dirs:
        scan_pending_marker(run_dir / WORKTREE_MERGE_PENDING, scope="run", run_dir_text=run_dir.resolve().as_posix())

    for artifact_dir in _worktree_cleanup_artifact_dirs(repo_resolved):
        for artifact_name in (
            WORKTREE_CLEANUP_FAILED_STATUS_MAP["applied_cleanup_failed"]["artifact_name"],
            WORKTREE_CLEANUP_FAILED_STATUS_MAP["discard_cleanup_failed"]["artifact_name"],
        ):
            artifact_path = artifact_dir / artifact_name
            if not artifact_path.exists():
                continue
            try:
                payload = _read_json_payload(artifact_path)
            except Exception as ex:
                reason = str(ex).strip() or ex.__class__.__name__
                artifact_categories = ["cleanup_failed", "active"]
                artifact_path_text = artifact_path.resolve().as_posix()
                resolution_actions = worktree_resolution_actions(
                    "cleanup_failed",
                    artifact_path=artifact_path_text,
                    cleanup_message=reason,
                )
                cleanup_failed.append(
                    {
                        "path": artifact_path_text,
                        "status": "malformed",
                        "run_dir": artifact_dir.resolve().as_posix() if artifact_dir.name != ".AgentCLI" else "",
                        "source_repo": "",
                        "worktree_dir": "",
                        "patch_path": "",
                        "cleanup_path": "",
                        "cleanup_message": reason,
                        "cleanup_details": {},
                        "cleanup_attempts": [],
                        "categories": artifact_categories,
                        "resolution_actions": resolution_actions,
                    }
                )
                add_issue(
                    "cleanup_failed",
                    f"Cleanup-failed artifact is malformed: {reason}",
                    severity="error",
                    path=artifact_path_text,
                    categories=[category for category in artifact_categories if category != "active"],
                    details={"run_dir": artifact_dir.resolve().as_posix() if artifact_dir.name != ".AgentCLI" else "", "reason": reason},
                )
                continue

            reconciliation = _cleanup_failed_reconciliation_state(payload, artifact_path)
            payload_worktree_dir = _worktree_text_path(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree"))
            payload_patch_path = _worktree_text_path(payload.get("patch_path") or payload.get("patchPath") or payload.get("patch"))
            payload_cleanup_path = _worktree_text_path(payload.get("cleanup_path") or payload.get("cleanupPath") or payload_worktree_dir)
            payload_cleanup_message = _payload_text(payload, "cleanup_message", "cleanupMessage", "message", "cleanup_error")
            payload_cleanup_details = payload.get("cleanup_details") if isinstance(payload.get("cleanup_details"), dict) else payload.get("cleanupDetails")
            if not isinstance(payload_cleanup_details, dict):
                payload_cleanup_details = {}
            payload_cleanup_attempts = payload.get("cleanup_attempts") if isinstance(payload.get("cleanup_attempts"), list) else payload.get("cleanupAttempts")
            if not isinstance(payload_cleanup_attempts, list):
                payload_cleanup_attempts = []
            status = _payload_text(payload, "status").lower() or "cleanup_failed"
            cleanup_categories = ["cleanup_failed", "active"]
            if payload_patch_path and not Path(payload_patch_path).exists():
                cleanup_categories.append("missing_patch")
            last_attempt = payload_cleanup_attempts[-1] if payload_cleanup_attempts else {}
            cleanup_operation = _payload_text(payload_cleanup_details, "operation", "cleanup_operation", "cleanupOperation") or (
                _payload_text(last_attempt, "operation") if isinstance(last_attempt, dict) else ""
            )
            permission_detail = _worktree_cleanup_permission_detail(payload_cleanup_details, payload_cleanup_attempts)
            git_registration = payload_cleanup_details.get("git_worktree_registration") if isinstance(payload_cleanup_details.get("git_worktree_registration"), dict) else {}
            residual_directory = bool(payload_cleanup_details.get("residual_directory") or reconciliation.get("residual_directory"))
            if isinstance(git_registration, dict) and git_registration.get("registered") is False and (payload_worktree_dir or payload_cleanup_path):
                residual_directory = True
            reboot_guidance = _payload_text(payload_cleanup_details, "reboot_guidance", "rebootGuidance")
            admin_guidance = _payload_text(payload_cleanup_details, "admin_guidance", "adminGuidance", "admin_cleanup_guidance", "adminCleanupGuidance")
            reconciliation_blocking_paths = [
                str(path).strip()
                for path in reconciliation.get("blocking_paths", [])
                if str(path).strip()
            ] if isinstance(reconciliation.get("blocking_paths"), list) else []
            residual_target = reconciliation_blocking_paths[0] if reconciliation_blocking_paths else ""
            resolution_actions = worktree_resolution_actions(
                status,
                source_repo=_worktree_text_path(payload.get("source_repo") or payload.get("sourceRepo")),
                worktree_dir=payload_worktree_dir,
                cleanup_path=payload_cleanup_path,
                pending_paths=reconciliation.get("pending_marker_paths") if isinstance(reconciliation.get("pending_marker_paths"), list) else [],
                cleanup_message=payload_cleanup_message,
                artifact_path=artifact_path.resolve().as_posix(),
                reconciliation=reconciliation,
            )
            issue_target = (
                residual_target
                if residual_directory and residual_target
                else payload_worktree_dir
                if residual_directory and payload_worktree_dir
                else payload_cleanup_path or payload_worktree_dir or artifact_path.resolve().as_posix()
            )
            issue_severity = "warn" if residual_directory else "error"
            if residual_directory:
                issue_message = f"Residual worktree directory remains at {issue_target}: {payload_cleanup_message or status}"
            else:
                issue_message = f"Cleanup failed for {issue_target}: {payload_cleanup_message or status}"
            cleanup_failed.append(
                {
                    "path": artifact_path.resolve().as_posix(),
                    "status": status,
                    "run_dir": _worktree_text_path(payload.get("run_dir") or payload.get("runDir")),
                    "source_repo": _worktree_text_path(payload.get("source_repo") or payload.get("sourceRepo")),
                    "worktree_dir": payload_worktree_dir,
                    "patch_path": payload_patch_path,
                    "cleanup_path": payload_cleanup_path,
                    "cleanup_message": payload_cleanup_message,
                    "cleanup_details": payload_cleanup_details,
                    "cleanup_attempts": payload_cleanup_attempts,
                    "cleanup_operation": cleanup_operation,
                    "cleanupOperation": cleanup_operation,
                    "permission_detail": permission_detail,
                    "permissionDetail": permission_detail,
                    "reboot_guidance": reboot_guidance,
                    "rebootGuidance": reboot_guidance,
                    "admin_guidance": admin_guidance,
                    "adminGuidance": admin_guidance,
                    "residual_directory": residual_directory,
                    "residualDirectory": residual_directory,
                    "git_worktree_registration": dict(git_registration) if isinstance(git_registration, dict) else {},
                    "gitWorktreeRegistration": dict(git_registration) if isinstance(git_registration, dict) else {},
                    "categories": cleanup_categories,
                    "reconciliation": reconciliation,
                    "resolution_actions": resolution_actions,
                }
            )
            register_reference(payload_worktree_dir)
            add_issue(
                "cleanup_failed",
                issue_message,
                severity=issue_severity,
                path=issue_target,
                categories=[category for category in cleanup_categories if category != "active"],
                details={
                    "artifact_path": artifact_path.resolve().as_posix(),
                    "run_dir": _worktree_text_path(payload.get("run_dir") or payload.get("runDir")),
                    "worktree_dir": payload_worktree_dir,
                    "patch_path": payload_patch_path,
                    "cleanup_path": payload_cleanup_path,
                    "cleanup_message": payload_cleanup_message,
                    "cleanup_operation": cleanup_operation,
                    "permission_detail": permission_detail,
                    "reboot_guidance": reboot_guidance,
                    "admin_guidance": admin_guidance,
                    "residual_directory": residual_directory,
                    "git_worktree_registration": dict(git_registration) if isinstance(git_registration, dict) else {},
                    "status": status,
                    "reconciliation": reconciliation,
                },
            )
            if payload_patch_path and not Path(payload_patch_path).exists():
                register_missing_patch(
                    payload_patch_path,
                    reason=payload_cleanup_message or "cleanup-failed artifact references a missing patch",
                    marker_path=artifact_path.resolve().as_posix(),
                    run_dir=_worktree_text_path(payload.get("run_dir") or payload.get("runDir")),
                    scope="cleanup_failed",
                    categories=cleanup_categories,
                )

    generated_root_entries: list[Path] = []
    if generated_root.exists():
        try:
            generated_root_entries = sorted([candidate for candidate in generated_root.iterdir() if candidate.is_dir()], key=lambda path: path.name)
        except Exception:
            generated_root_entries = []

    contracts_by_worktree: dict[str, dict[str, object]] = {}
    for run_dir in run_dirs:
        contract_path = run_dir / WORKTREE_REUSE_CONTRACT
        if not contract_path.exists():
            continue
        try:
            contract = _read_json_payload(contract_path)
        except Exception:
            continue
        if not isinstance(contract, dict):
            continue
        worktree_dir_text = _worktree_text_path(contract.get("worktree_dir") or contract.get("worktreeDir") or contract.get("worktree"))
        if not worktree_dir_text:
            continue
        contracts_by_worktree[worktree_dir_text] = {
            "path": contract_path.resolve().as_posix(),
            "run_dir": run_dir.resolve().as_posix(),
            "worktree_dir": worktree_dir_text,
            "source_repo": _worktree_text_path(contract.get("source_repo") or contract.get("sourceRepo")),
            "source_repo_root": _worktree_text_path(contract.get("source_repo_root") or contract.get("sourceRepoRoot")),
            "branch": _payload_text(contract, "branch", "source_branch", "sourceBranch"),
            "expected_head": _payload_text(contract, "expected_head", "expectedHead", "base_ref", "baseRef"),
            "head_ref": _payload_text(contract, "head_ref", "headRef"),
            "source_repo_state": _payload_text(contract, "source_repo_state", "sourceRepoState"),
            "worktree_state": _payload_text(contract, "worktree_state", "worktreeState"),
            "worktree_branch": _payload_text(contract, "worktree_branch", "worktreeBranch"),
        }

    for worktree_dir in generated_root_entries:
        worktree_path = worktree_dir.resolve().as_posix()
        contract = contracts_by_worktree.get(worktree_path)
        worktree_exists = worktree_dir.exists()
        active_reference = worktree_path in referenced_worktrees
        contract_path = _worktree_text_path(contract.get("path")) if contract else ""
        contract_run_dir = _worktree_text_path(contract.get("run_dir")) if contract else ""
        contract_status = "tracked"
        reason = ""
        if not contract:
            contract_status = "missing_contract"
            reason = "missing reuse contract"
        elif contract_run_dir and not Path(contract_run_dir).exists():
            contract_status = "orphaned"
            reason = f"run directory is missing ({contract_run_dir})"
        elif not active_reference:
            contract_status = "orphaned"
            reason = "no pending or cleanup-failed marker references it"

        is_orphaned = bool(
            (contract and contract_status == "orphaned")
            or (not contract and not active_reference)
        )
        worktree_categories = ["orphaned"] if is_orphaned else ["active"]
        resolution_actions = worktree_resolution_actions(
            "orphaned_worktree" if is_orphaned else "generated_worktree",
            worktree_dir=worktree_path,
        )

        generated_worktrees.append(
            {
                "path": worktree_path,
                "exists": worktree_exists,
                "contract_path": contract_path,
                "contract_run_dir": contract_run_dir,
                "contract_status": contract_status,
                "reason": reason,
                "tracked": contract_status == "tracked" and active_reference,
                "orphaned": is_orphaned,
                "referenced": active_reference,
                "categories": worktree_categories,
                "resolution_actions": resolution_actions,
            }
        )
        if is_orphaned:
            add_issue(
                "orphaned_worktree",
                f"Generated worktree is orphaned: {reason}",
                severity="warn",
                path=worktree_path,
                categories=worktree_categories,
                details={
                    "contract_path": contract_path,
                    "run_dir": contract_run_dir,
                    "reason": reason,
                },
            )

    current_source_head = git_head(repo_resolved)
    for branch in _worktree_stale_task_branches(repo_resolved, run_dirs, current_source_head):
        stale_task_branches.append(branch)
        add_issue(
            "stale_task_branch",
            f"Stale task branch {branch.get('branch')}: {branch.get('reason')}",
            severity="warn",
            categories=["stale"],
            details={
                "branch": branch.get("branch"),
                "age": branch.get("age"),
                "age_seconds": branch.get("age_seconds"),
                "status": branch.get("status"),
                "reason": branch.get("reason"),
                "owning_run": branch.get("owning_run"),
                "owning_run_dir": branch.get("owning_run_dir"),
                "task_id": branch.get("task_id"),
                "head_ref": branch.get("head_ref"),
                "source_head": branch.get("source_head"),
            },
        )

    for attempt in _worktree_interrupted_attempt_dirs(run_dirs):
        interrupted_attempts.append(attempt)
        add_issue(
            "interrupted_attempt_directory",
            f"Interrupted attempt directory {attempt.get('path')}: {attempt.get('reason')}",
            severity="warn",
            path=str(attempt.get("path") or ""),
            categories=["stale"],
            details={
                "age": attempt.get("age"),
                "age_seconds": attempt.get("age_seconds"),
                "status": attempt.get("status"),
                "reason": attempt.get("reason"),
                "owning_run": attempt.get("owning_run"),
                "run_dir": attempt.get("run_dir"),
                "task_id": attempt.get("task_id"),
                "attempt": attempt.get("attempt"),
            },
        )

    issues_sorted = sorted(
        issues,
        key=lambda item: (
            0 if item.get("severity") == "error" else 1,
            str(item.get("kind") or ""),
            str(item.get("path") or ""),
        ),
    )
    severity_order = [str(issue.get("severity") or "warn") for issue in issues_sorted]
    if any(severity == "error" for severity in severity_order):
        status = "error"
    elif issues_sorted:
        status = "warning"
    else:
        status = "ok"

    summary = {
        "run_dirs_scanned": len(run_dirs),
        "pending_markers": len(pending_markers),
        "stale_pending_markers": sum(1 for marker in pending_markers if marker.get("stale")),
        "missing_patches": sum(1 for issue in issues_sorted if issue.get("kind") == "missing_patch"),
        "cleanup_failed": len(cleanup_failed),
        "generated_worktrees": len(generated_worktrees),
        "orphaned_worktrees": sum(1 for worktree in generated_worktrees if worktree.get("orphaned")),
        "stale_task_branches": len(stale_task_branches),
        "interrupted_attempts": len(interrupted_attempts),
        "issue_count": len(issues_sorted),
        "healthy": not issues_sorted,
        "category_counts": _worktree_diagnostic_category_counts(pending_markers, cleanup_failed, generated_worktrees, stale_task_branches, interrupted_attempts, issues_sorted),
    }
    summary["categoryCounts"] = summary["category_counts"]

    return _worktree_filter_diagnostics_result(
        {
            "schema_version": 1,
            "status": status,
            "source_repo": repo_resolved.as_posix(),
            "source_repo_root": source_root,
            "generated_worktree_home": generated_root.as_posix(),
            "scanned_at": scanned_at,
            "summary": summary,
            "issues": issues_sorted,
            "pending_markers": pending_markers,
            "cleanup_failed": cleanup_failed,
            "generated_worktrees": generated_worktrees,
            "stale_task_branches": stale_task_branches,
            "interrupted_attempts": interrupted_attempts,
        },
        categories=categories,
    )


def _worktree_contract_path(run_dir: Path) -> Path:
    return run_dir / WORKTREE_REUSE_CONTRACT


def _worktree_contract_payload(worktree_dir: Path, source_repo: Path, run_dir: Path) -> dict[str, object]:
    source_repo_resolved = source_repo.expanduser().resolve()
    worktree_resolved = worktree_dir.expanduser().resolve()
    source_head = git_head(source_repo_resolved)
    return {
        "schema_version": 1,
        "run_id": run_dir.name,
        "created_at": now_iso(),
        "source_repo": source_repo_resolved.as_posix(),
        "source_repo_root": git_show_toplevel(source_repo_resolved) or source_repo_resolved.as_posix(),
        "branch": git_current_branch(source_repo_resolved) or "HEAD",
        "expected_head": source_head,
        "base_ref": source_head,
        "head_ref": git_head(worktree_resolved),
        "source_repo_state": git_repo_state(source_repo_resolved),
        "worktree_state": git_repo_state(worktree_resolved),
        "worktree_branch": git_current_branch(worktree_resolved) or "HEAD",
        "worktree_dir": worktree_resolved.as_posix(),
    }


def _write_worktree_contract(run_dir: Path, payload: dict[str, object]) -> None:
    safe_write_text(_worktree_contract_path(run_dir), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _read_json_payload(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if not isinstance(payload, dict):
        raise TypeError("Payload must be a JSON object.")
    return payload


def _validate_worktree_contract(
    *,
    repo: Path,
    worktree_dir: Path,
    run_dir: Path,
    contract: dict[str, object],
) -> None:
    repo_resolved = repo.expanduser().resolve()
    worktree_resolved = worktree_dir.expanduser().resolve()
    current_source_root = git_show_toplevel(repo_resolved) or repo_resolved.as_posix()
    current_source_branch = git_current_branch(repo_resolved) or "HEAD"
    current_source_head = git_head(repo_resolved)
    current_source_state = git_repo_state(repo_resolved)
    current_worktree_branch = git_current_branch(worktree_resolved) or "HEAD"
    current_worktree_head = git_head(worktree_resolved)
    current_worktree_state = git_repo_state(worktree_resolved)

    contract_run_id = _payload_text(contract, "run_id", "runId")
    if not contract_run_id:
        raise WorktreeSafetyError(
            "worktree_reuse_contract_invalid",
            "Existing worktree reuse contract is missing a run id.",
            status_code=400,
            details={"contract": _worktree_contract_path(run_dir).as_posix()},
            status="invalid_request",
        )
    if contract_run_id != run_dir.name:
        raise WorktreeSafetyError(
            "worktree_reuse_run_id_mismatch",
            "Existing worktree belongs to a different run id.",
            details={"expected": run_dir.name, "actual": contract_run_id},
        )

    contract_repo = _payload_text(contract, "source_repo", "sourceRepo")
    contract_root = _payload_text(contract, "source_repo_root", "sourceRepoRoot")
    if not contract_repo or not contract_root:
        raise WorktreeSafetyError(
            "worktree_reuse_contract_invalid",
            "Existing worktree reuse contract is missing source repository ownership metadata.",
            status_code=400,
            details={"contract": _worktree_contract_path(run_dir).as_posix()},
            status="invalid_request",
        )
    if contract_repo != repo_resolved.as_posix() or contract_root != current_source_root:
        raise WorktreeSafetyError(
            "worktree_reuse_source_repo_mismatch",
            "Existing worktree belongs to a different source repository.",
            details={
                "expected": {"source_repo": repo_resolved.as_posix(), "source_repo_root": current_source_root},
                "actual": {"source_repo": contract_repo, "source_repo_root": contract_root},
            },
        )
    contract_worktree_dir = _payload_text(contract, "worktree_dir", "worktreeDir", "worktree")
    if contract_worktree_dir != worktree_resolved.as_posix():
        raise WorktreeSafetyError(
            "worktree_reuse_worktree_dir_mismatch",
            "Existing worktree directory does not match the active run contract.",
            details={"expected": worktree_resolved.as_posix(), "actual": contract_worktree_dir},
        )

    contract_branch = _payload_text(contract, "branch", "source_branch", "sourceBranch")
    if not contract_branch:
        raise WorktreeSafetyError(
            "worktree_reuse_contract_invalid",
            "Existing worktree reuse contract is missing the source branch.",
            status_code=400,
            details={"contract": _worktree_contract_path(run_dir).as_posix()},
            status="invalid_request",
        )
    if contract_branch != current_source_branch or _payload_text(contract, "worktree_branch", "worktreeBranch") not in {"", current_worktree_branch}:
        raise WorktreeSafetyError(
            "worktree_reuse_branch_mismatch",
            "Existing worktree branch no longer matches the active run contract.",
            details={
                "expected": {"branch": contract_branch, "worktree_branch": _payload_text(contract, "worktree_branch", "worktreeBranch")},
                "actual": {"branch": current_source_branch, "worktree_branch": current_worktree_branch},
            },
        )

    contract_expected_head = _payload_text(contract, "expected_head", "expectedHead", "base_ref", "baseRef")
    if not contract_expected_head:
        raise WorktreeSafetyError(
            "worktree_reuse_contract_invalid",
            "Existing worktree reuse contract is missing the expected head.",
            status_code=400,
            details={"contract": _worktree_contract_path(run_dir).as_posix()},
            status="invalid_request",
        )
    if contract_expected_head != current_source_head or _payload_text(contract, "head_ref", "headRef") not in {"", current_worktree_head}:
        raise WorktreeSafetyError(
            "worktree_reuse_expected_head_mismatch",
            "Existing worktree no longer matches the expected source head.",
            details={
                "expected": {"expected_head": contract_expected_head, "head_ref": _payload_text(contract, "head_ref", "headRef")},
                "actual": {"expected_head": current_source_head, "head_ref": current_worktree_head},
            },
        )

    contract_source_state = _payload_text(contract, "source_repo_state", "sourceRepoState")
    contract_worktree_state = _payload_text(contract, "worktree_state", "worktreeState")
    if not contract_source_state or not contract_worktree_state:
        raise WorktreeSafetyError(
            "worktree_reuse_contract_invalid",
            "Existing worktree reuse contract is missing clean/dirty state metadata.",
            status_code=400,
            details={"contract": _worktree_contract_path(run_dir).as_posix()},
            status="invalid_request",
        )
    if contract_source_state != current_source_state or contract_worktree_state != current_worktree_state:
        raise WorktreeSafetyError(
            "worktree_reuse_state_mismatch",
            "Existing worktree clean/dirty state no longer matches the active run contract.",
            details={
                "expected": {
                    "source_repo_state": contract_source_state,
                    "worktree_state": contract_worktree_state,
                },
                "actual": {
                    "source_repo_state": current_source_state,
                    "worktree_state": current_worktree_state,
                },
            },
        )


def _worktree_pending_payload(
    worktree_dir: Path,
    source_repo: Path,
    run_dir: Path,
    patch_path: Path,
    base_ref: str,
    last_rc: int,
) -> dict[str, object]:
    source_repo_resolved = source_repo.expanduser().resolve()
    worktree_resolved = worktree_dir.expanduser().resolve()
    source_head = git_head(source_repo_resolved)
    worktree_head = git_head(worktree_resolved)
    worktree_state = git_repo_state(worktree_resolved)
    patch_hash = sha256_text(patch_path.read_text(encoding="utf-8", errors="replace"))
    source_root = git_show_toplevel(source_repo_resolved) or source_repo_resolved.as_posix()
    branch = git_current_branch(source_repo_resolved) or "HEAD"
    resolved_base_ref = git_rev_parse_ref(source_repo_resolved, base_ref) or source_head
    pending_marker_path = (run_dir.resolve() / WORKTREE_MERGE_PENDING).as_posix()
    preflight = summarize_worktree_preflight(
        source_repo_resolved,
        patch_path,
        base_ref=resolved_base_ref,
        pending_path=run_dir.resolve() / WORKTREE_MERGE_PENDING,
    )
    changed_files = summarize_worktree_diff(patch_path)
    return {
        "schema_version": 1,
        "status": "pending",
        "created_at": now_iso(),
        "run_id": run_dir.name,
        "source_repo": source_repo_resolved.as_posix(),
        "source_repo_root": source_root,
        "branch": branch,
        "expected_head": resolved_base_ref,
        "source_repo_state": str(preflight.get("sourceRepoState") or git_repo_state(source_repo_resolved)),
        "worktree_state": worktree_state,
        "worktree_branch": git_current_branch(worktree_resolved) or "HEAD",
        "source_branch": branch,
        "source_head": source_head,
        "sourceHead": source_head,
        "sourceRepoState": str(preflight.get("sourceRepoState") or git_repo_state(source_repo_resolved)),
        "worktreeState": worktree_state,
        "sourceRepoRoot": source_root,
        "patch_hash": patch_hash,
        "patchHash": patch_hash,
        "expected_base_ref": resolved_base_ref,
        "expectedBaseRef": resolved_base_ref,
        "run_dir": str(run_dir.resolve()),
        "worktree_dir": str(worktree_resolved),
        "patch_path": str(patch_path.resolve()),
        "base_ref": resolved_base_ref,
        "head_ref": worktree_head,
        "pending_file": pending_marker_path,
        "pendingFile": pending_marker_path,
        "pending_marker_path": pending_marker_path,
        "pendingMarkerPath": pending_marker_path,
        "preflight": preflight,
        "apply_check": preflight.get("applyCheck", {}),
        "applyCheck": preflight.get("applyCheck", {}),
        "changed_files": changed_files,
        "changedFiles": changed_files,
        "last_rc": last_rc,
    }


def _write_pending_merge_files(source_repo: Path, run_dir: Path, payload: dict[str, object]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    safe_write_text(run_dir / WORKTREE_MERGE_PENDING, text)
    central = source_repo / ".AgentCLI" / WORKTREE_MERGE_PENDING
    safe_write_text(central, text)

    md = (
        "# Worktree Merge Pending\n\n"
        "AgentCLI completed work in an isolated git worktree. The source repository was not modified.\n\n"
        f"- run id: `{payload.get('run_id')}`\n"
        f"- source repo: `{payload.get('source_repo')}`\n"
        f"- branch: `{payload.get('branch')}`\n"
        f"- source state: `{payload.get('source_repo_state')}`\n"
        f"- worktree state: `{payload.get('worktree_state')}`\n"
        f"- worktree: `{payload.get('worktree_dir')}`\n"
        f"- patch: `{payload.get('patch_path')}`\n"
        f"- patch hash: `{payload.get('patch_hash')}`\n"
        f"- base: `{payload.get('base_ref')}`\n"
        f"- head: `{payload.get('head_ref')}`\n"
        f"- runner rc: `{payload.get('last_rc')}`\n\n"
        "From AgentCLI Shell, run `/merge-worktree` to apply this patch to the source repository, "
        "or `/discard-worktree` to remove the isolated worktree without applying it.\n"
    )
    safe_write_text(run_dir / WORKTREE_MERGE_PENDING_MD, md)


def find_pending_worktree_merge(repo: Path, run_dir: Path | None = None) -> Path | None:
    candidates: list[Path] = []
    if run_dir is not None:
        candidates.append(run_dir / WORKTREE_MERGE_PENDING)
    candidates.append(repo / ".AgentCLI" / WORKTREE_MERGE_PENDING)
    runs_root = repo / ".AgentCLI" / "agent_runs"
    if runs_root.exists():
        for d in sorted([p for p in runs_root.iterdir() if p.is_dir()], key=lambda p: p.name, reverse=True):
            candidates.append(d / WORKTREE_MERGE_PENDING)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def read_pending_worktree_merge(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _worktree_split_patch_path(run_dir: Path) -> Path:
    return run_dir / "worktree_dirty_uncommitted.patch"


def _pending_companion_paths(payload: dict[str, object], pending_path: Path) -> list[Path]:
    out = [pending_path]
    run_dir = str(payload.get("run_dir") or "").strip()
    source_repo = str(payload.get("source_repo") or "").strip()
    if run_dir:
        out.append(Path(run_dir) / WORKTREE_MERGE_PENDING)
    if source_repo:
        out.append(Path(source_repo) / ".AgentCLI" / WORKTREE_MERGE_PENDING)
    uniq: list[Path] = []
    seen: set[str] = set()
    for p in out:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            uniq.append(p)
    return uniq


def _write_pending_status(
    payload: dict[str, object],
    pending_path: Path,
    status: str,
    message: str = "",
    *,
    extra: dict[str, object] | None = None,
) -> list[Path]:
    updated = dict(payload)
    updated["status"] = status
    updated["resolved_at"] = now_iso()
    if message:
        updated["message"] = message
    if extra:
        updated.update(extra)
    text = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    written_paths: list[Path] = []
    for path in reversed(_pending_companion_paths(payload, pending_path)):
        if path.exists():
            target = path.with_name(f"WORKTREE_MERGE_{status.upper()}.json")
            safe_write_text(target, text)
            written_paths.append(target)
            try:
                path.unlink()
            except Exception:
                pass
    return written_paths


def _cleanup_failure_result(
    payload: dict[str, object],
    pending_path: Path,
    status: str,
    error: Exception,
    *,
    fallback_cleanup_path: Path,
) -> dict[str, object]:
    cleanup_message = str(error).strip() or error.__class__.__name__
    cleanup_path = fallback_cleanup_path.as_posix()
    cleanup_details: dict[str, object] = {}
    cleanup_attempts: list[dict[str, object]] = []

    if isinstance(error, WorktreeCleanupError):
        cleanup_message = str(error).strip() or error.cleanup_message or cleanup_message
        cleanup_path = str(error.cleanup_path or error.details.get("path") or cleanup_path).strip() or cleanup_path
        cleanup_details = dict(error.details)
        raw_attempts = cleanup_details.get("attempts")
        if isinstance(raw_attempts, list):
            cleanup_attempts = [dict(item) if isinstance(item, dict) else {"message": str(item)} for item in raw_attempts]
        elif error.attempts:
            cleanup_attempts = [dict(item) for item in error.attempts]
        if cleanup_attempts:
            cleanup_details["attempts"] = cleanup_attempts
        if cleanup_path:
            cleanup_details.setdefault("path", cleanup_path)

    source_repo_text = _payload_text(payload, "source_repo", "sourceRepo")
    worktree_dir_text = _worktree_text_path(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree"))
    if worktree_dir_text:
        cleanup_details.setdefault("permission_detail", _worktree_cleanup_permission_detail(cleanup_details, cleanup_attempts))
        cleanup_details.setdefault("operation", cleanup_details.get("operation") or cleanup_details.get("cleanup_operation") or "shutil.rmtree")
    if source_repo_text and worktree_dir_text:
        cleanup_details["git_worktree_registration"] = _git_worktree_registration_state(Path(source_repo_text), Path(worktree_dir_text))
        registration_state = cleanup_details["git_worktree_registration"]
        if isinstance(registration_state, dict) and registration_state.get("registered") is False:
            cleanup_details["residual_directory"] = True
    if os.name == "nt":
        cleanup_details.setdefault(
            "admin_guidance",
            "If reboot does not clear the ACL block, ask an administrator to remove the residual directory or fix its permissions.",
        )

    updates: dict[str, object] = {
        "cleanup_error": cleanup_message,
        "cleanup_message": cleanup_message,
        "cleanup_path": cleanup_path,
    }
    if cleanup_details:
        updates["cleanup_details"] = cleanup_details
    if cleanup_attempts:
        updates["cleanup_attempts"] = cleanup_attempts
    artifact_name = WORKTREE_CLEANUP_FAILED_STATUS_MAP.get(status, {}).get("artifact_name", f"WORKTREE_MERGE_{status.upper()}.json")
    artifact_hint = pending_path.with_name(artifact_name)
    written_paths = _write_pending_status(payload, pending_path, status, cleanup_message, extra=updates)
    artifact_path = next(
        (path for path in written_paths if path.resolve() == artifact_hint.resolve()),
        written_paths[0] if written_paths else artifact_hint,
    )
    cleanup_reconciliation = _cleanup_failed_reconciliation_state(
        {**payload, **updates, "status": status},
        artifact_path,
    )
    resolution_actions = worktree_resolution_actions(
        status,
        source_repo=_payload_text(payload, "source_repo", "sourceRepo"),
        worktree_dir=_worktree_text_path(payload.get("worktree_dir") or payload.get("worktreeDir") or payload.get("worktree")),
        cleanup_path=cleanup_path,
        pending_paths=[path.as_posix() for path in _pending_companion_paths(payload, pending_path)],
        cleanup_message=cleanup_message,
        artifact_path=artifact_path.as_posix(),
        reconciliation=cleanup_reconciliation,
    )
    updates["cleanup_reconciliation"] = cleanup_reconciliation
    updates["resolution_actions"] = resolution_actions
    artifact_payload = dict(payload)
    artifact_payload["status"] = status
    artifact_payload.update(updates)
    safe_write_text(artifact_path, json.dumps(artifact_payload, ensure_ascii=False, indent=2) + "\n")
    result = dict(payload)
    result["status"] = status
    result.update(updates)
    return result


def _apply_fast_forward_then_dirty_patch(
    payload: dict[str, object],
    pending_path: Path,
    *,
    source_repo: Path,
    worktree_dir: Path,
    run_dir: Path,
    base_ref: str,
    head_ref: str,
) -> dict[str, object] | None:
    resolved_base_ref = git_rev_parse_ref(source_repo, base_ref)
    resolved_head_ref = git_rev_parse_ref(source_repo, head_ref)
    if not resolved_base_ref or not resolved_head_ref or resolved_base_ref == resolved_head_ref:
        return None
    if not _git_is_ancestor(source_repo, resolved_base_ref, resolved_head_ref):
        return None

    dirty_patch_path = _worktree_split_patch_path(run_dir)
    export_worktree_patch(worktree_dir, dirty_patch_path, base_ref=resolved_head_ref)
    dirty_patch_hash = sha256_text(dirty_patch_path.read_text(encoding="utf-8", errors="replace"))
    dirty_patch_has_changes = _patch_has_changes(dirty_patch_path)
    split_merge_metadata: dict[str, object] = {
        "merge_mode": "fast_forward_then_patch",
        "mergeMode": "fast_forward_then_patch",
        "fast_forward_ref": resolved_head_ref,
        "fastForwardRef": resolved_head_ref,
        "dirty_patch_path": dirty_patch_path.as_posix(),
        "dirtyPatchPath": dirty_patch_path.as_posix(),
        "dirty_patch_hash": dirty_patch_hash,
        "dirtyPatchHash": dirty_patch_hash,
        "dirty_patch_check": None,
        "dirtyPatchCheck": None,
        "dirty_patch_applied": False,
        "dirtyPatchApplied": False,
    }

    merge_rc, merge_out = run_cmd(["git", "merge", "--ff-only", resolved_head_ref], cwd=source_repo, timeout_sec=120)
    if merge_rc != 0:
        raise WorktreeSafetyError(
            "worktree_fast_forward_failed",
            "Committed worktree history could not be fast-forwarded into the source repository.",
            details={
                "base_ref": resolved_base_ref,
                "head_ref": resolved_head_ref,
                "source_repo": source_repo.as_posix(),
                "output": merge_out,
                **split_merge_metadata,
            },
        )

    if dirty_patch_has_changes:
        check_result = summarize_worktree_apply_check(source_repo, dirty_patch_path)
        split_merge_metadata["dirty_patch_check"] = check_result
        split_merge_metadata["dirtyPatchCheck"] = check_result
        if not bool(check_result.get("ok")):
            rollback_rc, rollback_out = run_cmd(["git", "reset", "--hard", resolved_base_ref], cwd=source_repo, timeout_sec=120)
            raise WorktreeSafetyError(
                "worktree_patch_check_failed",
                "Worktree dirty patch did not pass git apply --check preflight after fast-forward.",
                details={
                    "path": dirty_patch_path.as_posix(),
                    "source_repo": source_repo.as_posix(),
                    "run_dir": run_dir.as_posix(),
                    "worktree_dir": worktree_dir.as_posix(),
                    "base_ref": resolved_base_ref,
                    "head_ref": resolved_head_ref,
                    "output": check_result.get("output", ""),
                    **split_merge_metadata,
                    "failed_files": check_result.get("failed_files", []),
                    "failed_hunks": check_result.get("failed_hunks", []),
                    "apply_check": check_result,
                    "rollback": {"rc": rollback_rc, "output": rollback_out},
                },
            )
        try:
            apply_patch_to_repo(source_repo, dirty_patch_path)
        except WorktreeSafetyError as ex:
            ex.details.update(split_merge_metadata)
            raise
        split_merge_metadata["dirty_patch_applied"] = True
        split_merge_metadata["dirtyPatchApplied"] = True

    updates = dict(split_merge_metadata)
    updates["resolution_actions"] = worktree_resolution_actions(
        "applied",
        source_repo=source_repo.as_posix(),
        worktree_dir=worktree_dir.as_posix(),
        cleanup_path=worktree_dir.as_posix(),
        pending_paths=[path.as_posix() for path in _pending_companion_paths(payload, pending_path)],
    )
    result_payload = dict(payload)
    result_payload.update(updates)
    try:
        remove_worktree(source_repo, worktree_dir)
    except Exception as ex:
        return _cleanup_failure_result(
            result_payload,
            pending_path,
            "applied_cleanup_failed",
            ex,
            fallback_cleanup_path=worktree_dir,
        )
    _write_pending_status(result_payload, pending_path, "applied", extra=updates)
    result = dict(result_payload)
    result["status"] = "applied"
    result["resolution_actions"] = updates["resolution_actions"]
    return result


def apply_pending_worktree_merge(pending_path: Path) -> dict[str, object]:
    payload = read_pending_worktree_merge(pending_path)
    source_repo_text = _payload_text(payload, "source_repo", "sourceRepo")
    run_dir_text = _payload_text(payload, "run_dir", "runDir")
    worktree_dir_text = _payload_text(payload, "worktree_dir", "worktreeDir", "worktree")
    patch_path_text = _payload_text(payload, "patch_path", "patchPath", "patch")
    base_ref = _payload_text(payload, "base_ref", "baseRef")
    expected_head = _payload_text(payload, "expected_head", "expectedHead")
    branch = _payload_text(payload, "branch", "source_branch", "sourceBranch")
    source_repo_root = _payload_text(payload, "source_repo_root", "sourceRepoRoot")
    run_id = _payload_text(payload, "run_id", "runId")
    source_repo_state = _payload_text(payload, "source_repo_state", "sourceRepoState")
    worktree_state = _payload_text(payload, "worktree_state", "worktreeState")
    patch_hash = _payload_text(payload, "patch_hash", "patchHash")
    head_ref = _payload_text(payload, "head_ref", "headRef")

    required_fields = {
        "run_id": run_id,
        "source_repo": source_repo_text,
        "source_repo_root": source_repo_root,
        "run_dir": run_dir_text,
        "worktree_dir": worktree_dir_text,
        "patch_path": patch_path_text,
        "base_ref": base_ref,
        "expected_head": expected_head,
        "branch": branch,
        "source_repo_state": source_repo_state,
        "worktree_state": worktree_state,
        "patch_hash": patch_hash,
        "head_ref": head_ref,
    }
    missing_fields = [name for name, value in required_fields.items() if not value]
    if missing_fields:
        raise WorktreeSafetyError(
            "worktree_metadata_required",
            "Pending worktree marker is missing required merge metadata.",
            status_code=400,
            details={"missing": missing_fields, "pending_file": pending_path.as_posix()},
            status="invalid_request",
        )

    source_repo = Path(source_repo_text).expanduser().resolve()
    worktree_dir = Path(worktree_dir_text).expanduser().resolve()
    patch_path = Path(patch_path_text).expanduser().resolve()
    run_dir = Path(run_dir_text).expanduser().resolve()

    if not source_repo.exists():
        raise WorktreeSafetyError(
            "worktree_source_repo_missing",
            f"Source repo not found: {source_repo}",
            status_code=404,
            details={"source_repo": source_repo.as_posix()},
            status="invalid_request",
        )
    if not patch_path.exists():
        raise WorktreeSafetyError(
            "worktree_patch_missing",
            f"Worktree patch not found: {patch_path}",
            status_code=404,
            details={"path": patch_path.as_posix()},
            status="invalid_request",
        )

    current_source_root = git_show_toplevel(source_repo) or source_repo.as_posix()
    if source_repo_root != current_source_root or source_repo.as_posix() != _payload_text(payload, "source_repo", "sourceRepo"):
        raise WorktreeSafetyError(
            "worktree_source_repo_mismatch",
            "Pending worktree marker points at a different source repository.",
            details={
                "expected": {"source_repo": source_repo.as_posix(), "source_repo_root": current_source_root},
                "actual": {"source_repo": _payload_text(payload, "source_repo", "sourceRepo"), "source_repo_root": source_repo_root},
            },
        )
    if run_id != run_dir.name:
        raise WorktreeSafetyError(
            "worktree_run_id_mismatch",
            "Pending worktree marker belongs to a different run id.",
            details={"expected": run_dir.name, "actual": run_id},
        )
    current_source_branch = git_current_branch(source_repo) or "HEAD"
    if branch != current_source_branch:
        raise WorktreeSafetyError(
            "worktree_branch_mismatch",
            "Pending worktree marker no longer matches the source branch.",
            details={"expected": branch, "actual": current_source_branch},
        )

    current_source_head = git_head(source_repo)
    if expected_head != base_ref:
        raise WorktreeSafetyError(
            "worktree_expected_head_mismatch",
            "Pending worktree marker has inconsistent expected head metadata.",
            details={"expected_head": expected_head, "base_ref": base_ref},
        )
    if current_source_head != base_ref:
        raise WorktreeSafetyError(
            "worktree_base_ref_mismatch",
            "Source HEAD does not match the pending base ref.",
            details={"expected": base_ref, "actual": current_source_head},
        )

    current_source_state = git_repo_state(source_repo)
    if source_repo_state != "clean" or current_source_state != "clean":
        raise WorktreeSafetyError(
            "worktree_source_repo_dirty",
            "Source repository must be clean before merging the pending worktree patch.",
            details={"expected": "clean", "actual": current_source_state, "metadata": source_repo_state},
        )

    if worktree_state != "dirty":
        raise WorktreeSafetyError(
            "worktree_state_mismatch",
            "Pending worktree metadata must describe a dirty worktree.",
            details={"expected": "dirty", "actual": worktree_state},
        )

    current_patch_hash = sha256_text(patch_path.read_text(encoding="utf-8", errors="replace"))
    if current_patch_hash != patch_hash:
        raise WorktreeSafetyError(
            "worktree_patch_hash_mismatch",
            "Pending worktree patch hash does not match the exported patch.",
            details={"expected": patch_hash, "actual": current_patch_hash},
        )

    split_result = _apply_fast_forward_then_dirty_patch(
        payload,
        pending_path,
        source_repo=source_repo,
        worktree_dir=worktree_dir,
        run_dir=run_dir,
        base_ref=base_ref,
        head_ref=head_ref,
    )
    if split_result is not None:
        return split_result

    check_result = summarize_worktree_apply_check(source_repo, patch_path, pending_path=pending_path)
    if not bool(check_result.get("ok")):
        raise WorktreeSafetyError(
            "worktree_patch_check_failed",
            "Worktree patch did not pass git apply --check preflight.",
            details={
                "path": patch_path.as_posix(),
                "source_repo": source_repo.as_posix(),
                "run_dir": run_dir.as_posix(),
                "worktree_dir": worktree_dir.as_posix(),
                "output": check_result.get("output", ""),
                "failed_files": check_result.get("failed_files", []),
                "failed_hunks": check_result.get("failed_hunks", []),
                "apply_check": check_result,
            },
        )

    apply_patch_to_repo(source_repo, patch_path)
    try:
        remove_worktree(source_repo, worktree_dir)
    except Exception as ex:
        return _cleanup_failure_result(
            payload,
            pending_path,
            "applied_cleanup_failed",
            ex,
            fallback_cleanup_path=worktree_dir,
        )
    applied_updates = {
        "resolution_actions": worktree_resolution_actions(
            "applied",
            source_repo=source_repo.as_posix(),
            worktree_dir=worktree_dir.as_posix(),
            cleanup_path=worktree_dir.as_posix(),
            pending_paths=[path.as_posix() for path in _pending_companion_paths(payload, pending_path)],
        )
    }
    _write_pending_status(payload, pending_path, "applied", extra=applied_updates)
    result = dict(payload)
    result["status"] = "applied"
    result.update(applied_updates)
    return result


def discard_pending_worktree_merge(pending_path: Path) -> dict[str, object]:
    payload = read_pending_worktree_merge(pending_path)
    source_repo = Path(str(payload.get("source_repo") or "")).expanduser().resolve()
    worktree_dir = Path(str(payload.get("worktree_dir") or "")).expanduser().resolve()
    if source_repo.exists() and worktree_dir.exists():
        try:
            remove_worktree(source_repo, worktree_dir)
        except Exception as ex:
            return _cleanup_failure_result(
                payload,
                pending_path,
                "discard_cleanup_failed",
                ex,
                fallback_cleanup_path=worktree_dir,
            )
    discarded_updates = {
        "resolution_actions": worktree_resolution_actions(
            "discarded",
            source_repo=source_repo.as_posix(),
            worktree_dir=worktree_dir.as_posix(),
            cleanup_path=worktree_dir.as_posix(),
            pending_paths=[path.as_posix() for path in _pending_companion_paths(payload, pending_path)],
        )
    }
    _write_pending_status(payload, pending_path, "discarded", extra=discarded_updates)
    result = dict(payload)
    result["status"] = "discarded"
    result.update(discarded_updates)
    return result


def _write_worktree_not_applied(run_dir: Path, patch_path: Path, last_rc: int) -> None:
    msg = (
        "# Worktree patch not applied\n\n"
        f"Patch export completed, but auto-apply was skipped because rc={last_rc}.\n\n"
        "Manual apply:\n"
        f"- git apply --binary --whitespace=nowarn {patch_path}\n"
        "- If conflicts occur, try: git apply --reject --whitespace=nowarn <patch>\n"
    )
    safe_write_text(run_dir / "WORKTREE_PATCH_NOT_APPLIED.md", msg)


def _write_recovery_md(run_dir: Path, patch_path: Path) -> None:
    msg = (
        "# Recovery Guide\n\n"
        "A worktree patch was exported for recovery.\n\n"
        "Apply patch manually:\n"
        f"- git apply --binary --whitespace=nowarn {patch_path}\n"
        "- If conflicts occur: git apply --reject --whitespace=nowarn <patch>\n"
        "\n"
        "If untracked files were included via intent-to-add, ensure they are restored after applying.\n"
    )
    safe_write_text(run_dir / "RECOVERY.md", msg)


def handle_worktree_patch(
    worktree_dir: Path,
    source_repo: Path,
    run_dir: Path,
    last_rc: int,
    *,
    base_ref: str = "HEAD",
    auto_apply: bool = True,
    exclude_globs: Sequence[str] | None = None,
) -> int:
    patch_path = run_dir / "worktree.patch"
    try:
        export_worktree_patch(worktree_dir, patch_path, base_ref=base_ref, exclude_globs=exclude_globs)
        patch_text = patch_path.read_text(encoding="utf-8", errors="replace").strip()
        if patch_text:
            if auto_apply and last_rc == 0:
                apply_patch_to_repo(source_repo, patch_path)
            else:
                payload = _worktree_pending_payload(worktree_dir, source_repo, run_dir, patch_path, base_ref, last_rc)
                _write_pending_merge_files(source_repo, run_dir, payload)
                _write_worktree_not_applied(run_dir, patch_path, last_rc)
                _write_recovery_md(run_dir, patch_path)
    except Exception as ex:
        msg = (
            "# Worktree apply failure\n\n"
            f"{ex}\n\n"
            "Manual recovery:\n"
            f"- git apply --binary --whitespace=nowarn {patch_path}\n"
            "- If conflicts occur, try: git apply --reject --whitespace=nowarn <patch>\n"
        )
        safe_write_text(run_dir / "WORKTREE_APPLY_FAILURE.md", msg)
        _write_recovery_md(run_dir, patch_path)
        return last_rc if last_rc != 0 else 1
    return last_rc
