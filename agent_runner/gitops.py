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
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

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


def create_task_branch(repo: Path, task_id: str, task_title: str = "") -> TaskBranch:
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
    branch_name = f"task/{task_id}_{ts}"

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
        _msg = f"[{tb.task_id}] {tb.task_title}" if tb.task_title else f"[auto] task {tb.task_id} final commit"
        run_cmd(
            ["git", "commit", "--no-verify", "-m", _msg],
            cwd=repo, timeout_sec=120,
        )

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
        _msg = f"[{tb.task_id}] {tb.task_title} (abandoned)" if tb.task_title else f"[auto] task {tb.task_id} abandoned — preserving work"
        run_cmd(
            ["git", "commit", "--no-verify", "-m", _msg],
            cwd=repo, timeout_sec=120,
        )

    # Switch back to base
    checkout_target = tb.base_branch if tb.base_branch != "HEAD" else tb.base_commit
    rc, out = run_cmd(["git", "checkout", checkout_target], cwd=repo, timeout_sec=30)
    if rc != 0:
        raise RuntimeError(f"Failed to checkout {checkout_target} after abandon: {out}")

    eprint(f"[INFO] Abandoned task branch {tb.branch_name} (work preserved)")
    return tb.branch_name


def reset_task_branch(repo: Path, tb: TaskBranch) -> None:
    """Reset a task branch to its base commit for retry.

    This performs ``git reset --hard`` + ``git clean -fd`` but *only* on
    the task branch, so main is never affected.
    """
    check_and_remove_stale_git_lock(repo)

    rc, out = run_cmd(["git", "reset", "--hard", tb.base_commit], cwd=repo, timeout_sec=120)
    if rc != 0:
        raise RuntimeError(f"reset_task_branch: git reset --hard failed: {out}")

    rc, out = run_cmd(["git", "clean", "-fd"], cwd=repo, timeout_sec=120)
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
            time.sleep(backoff)
            backoff = min(backoff * 2 if backoff else initial_backoff_seconds, 0.25)

    blocked_path = attempts[-1]["path"] if attempts else worktree_dir.as_posix()
    message = f"Failed to remove generated worktree after {len(attempts) or total_attempts} attempts: {blocked_path}"
    if attempts:
        last_attempt = attempts[-1]
        last_attempt_message = str(last_attempt.get("message") or "").strip()
        last_attempt_type = str(last_attempt.get("error_type") or (last_error.__class__.__name__ if last_error else "PermissionError"))
        if last_attempt_message:
            message = f"{message} ({last_attempt_type}: {last_attempt_message})"
    details: dict[str, object] = {
        "path": blocked_path,
        "worktree_dir": worktree_dir.as_posix(),
        "attempts": attempts,
        "operation": "shutil.rmtree",
    }
    if git_remove is not None:
        details["git_worktree_remove"] = dict(git_remove)
    if git_prune is not None:
        details["git_worktree_prune"] = dict(git_prune)
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


WORKTREE_MERGE_PENDING = "WORKTREE_MERGE_PENDING.json"
WORKTREE_MERGE_PENDING_MD = "WORKTREE_MERGE_PENDING.md"
WORKTREE_REUSE_CONTRACT = "WORKTREE_REUSE_CONTRACT.json"


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
    details: dict[str, object] | None = None,
) -> dict[str, object]:
    issue: dict[str, object] = {
        "kind": str(kind),
        "severity": str(severity or "warn"),
        "message": str(message),
    }
    if path:
        issue["path"] = str(path)
    if details:
        issue["details"] = dict(details)
    return issue


def scan_worktree_diagnostics(repo: Path) -> dict[str, object]:
    repo_resolved = repo.expanduser().resolve()
    source_root = git_show_toplevel(repo_resolved) or repo_resolved.as_posix()
    scanned_at = now_iso()
    run_dirs = _worktree_run_dirs(repo_resolved)
    central_pending = repo_resolved / ".AgentCLI" / WORKTREE_MERGE_PENDING
    generated_root = _generated_worktree_home(repo_resolved)
    generated_worktrees: list[dict[str, object]] = []
    pending_markers: list[dict[str, object]] = []
    cleanup_failed: list[dict[str, object]] = []
    issues: list[dict[str, object]] = []
    referenced_worktrees: set[str] = set()
    seen_missing_patch_paths: set[str] = set()

    def add_issue(
        kind: str,
        message: str,
        *,
        severity: str = "warn",
        path: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        issues.append(_worktree_diagnostics_issue(kind, message, severity=severity, path=path, details=details))

    def register_reference(worktree_dir: str) -> None:
        if worktree_dir:
            referenced_worktrees.add(_worktree_text_path(worktree_dir))

    def register_missing_patch(patch_path: str, *, reason: str, marker_path: str, run_dir: str, scope: str) -> None:
        patch_path_text = _worktree_text_path(patch_path)
        if not patch_path_text or patch_path_text in seen_missing_patch_paths:
            return
        seen_missing_patch_paths.add(patch_path_text)
        add_issue(
            "missing_patch",
            f"Pending worktree patch is missing: {reason}",
            severity="warn",
            path=patch_path_text,
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
                }
            )
            add_issue(
                "stale_pending_marker",
                f"Pending worktree marker is malformed: {reason}",
                severity="warn",
                path=marker_path,
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
            }
        )
        register_reference(payload_worktree_dir)
        if reason:
            add_issue(
                "stale_pending_marker",
                f"Pending worktree marker is stale: {reason}",
                severity="warn",
                path=marker_path,
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
            )

    scan_pending_marker(central_pending, scope="central", run_dir_text="")
    for run_dir in run_dirs:
        scan_pending_marker(run_dir / WORKTREE_MERGE_PENDING, scope="run", run_dir_text=run_dir.resolve().as_posix())

        for artifact_name in ("WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json", "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json"):
            artifact_path = run_dir / artifact_name
            if not artifact_path.exists():
                continue
            try:
                payload = _read_json_payload(artifact_path)
            except Exception as ex:
                reason = str(ex).strip() or ex.__class__.__name__
                cleanup_failed.append(
                    {
                        "path": artifact_path.resolve().as_posix(),
                        "status": "malformed",
                        "run_dir": run_dir.resolve().as_posix(),
                        "source_repo": "",
                        "worktree_dir": "",
                        "patch_path": "",
                        "cleanup_path": "",
                        "cleanup_message": reason,
                        "cleanup_details": {},
                        "cleanup_attempts": [],
                    }
                )
                add_issue(
                    "cleanup_failed",
                    f"Cleanup-failed artifact is malformed: {reason}",
                    severity="error",
                    path=artifact_path.resolve().as_posix(),
                    details={"run_dir": run_dir.resolve().as_posix(), "reason": reason},
                )
                continue

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
            cleanup_failed.append(
                {
                    "path": artifact_path.resolve().as_posix(),
                    "status": status,
                    "run_dir": run_dir.resolve().as_posix(),
                    "source_repo": _worktree_text_path(payload.get("source_repo") or payload.get("sourceRepo")),
                    "worktree_dir": payload_worktree_dir,
                    "patch_path": payload_patch_path,
                    "cleanup_path": payload_cleanup_path,
                    "cleanup_message": payload_cleanup_message,
                    "cleanup_details": payload_cleanup_details,
                    "cleanup_attempts": payload_cleanup_attempts,
                }
            )
            register_reference(payload_worktree_dir)
            add_issue(
                "cleanup_failed",
                f"Cleanup failed for {payload_cleanup_path or payload_worktree_dir or artifact_path.as_posix()}: {payload_cleanup_message or status}",
                severity="error",
                path=artifact_path.resolve().as_posix(),
                details={
                    "run_dir": run_dir.resolve().as_posix(),
                    "worktree_dir": payload_worktree_dir,
                    "patch_path": payload_patch_path,
                    "cleanup_path": payload_cleanup_path,
                    "cleanup_message": payload_cleanup_message,
                    "status": status,
                },
            )
            if payload_patch_path and not Path(payload_patch_path).exists():
                register_missing_patch(
                    payload_patch_path,
                    reason=payload_cleanup_message or "cleanup-failed artifact references a missing patch",
                    marker_path=artifact_path.resolve().as_posix(),
                    run_dir=run_dir.resolve().as_posix(),
                    scope="cleanup_failed",
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
            }
        )
        if is_orphaned:
            add_issue(
                "orphaned_worktree",
                f"Generated worktree is orphaned: {reason}",
                severity="warn",
                path=worktree_path,
                details={
                    "contract_path": contract_path,
                    "run_dir": contract_run_dir,
                    "reason": reason,
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
        "issue_count": len(issues_sorted),
        "healthy": not issues_sorted,
    }

    return {
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
    }


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
) -> None:
    updated = dict(payload)
    updated["status"] = status
    updated["resolved_at"] = now_iso()
    if message:
        updated["message"] = message
    if extra:
        updated.update(extra)
    text = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    for path in reversed(_pending_companion_paths(payload, pending_path)):
        if path.exists():
            safe_write_text(path.with_name(f"WORKTREE_MERGE_{status.upper()}.json"), text)
            try:
                path.unlink()
            except Exception:
                pass


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

    updates: dict[str, object] = {
        "cleanup_error": cleanup_message,
        "cleanup_message": cleanup_message,
        "cleanup_path": cleanup_path,
    }
    if cleanup_details:
        updates["cleanup_details"] = cleanup_details
    if cleanup_attempts:
        updates["cleanup_attempts"] = cleanup_attempts

    _write_pending_status(payload, pending_path, status, cleanup_message, extra=updates)
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
    dirty_patch_has_changes = _patch_has_changes(dirty_patch_path)

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
            },
        )

    if dirty_patch_has_changes:
        check_result = summarize_worktree_apply_check(source_repo, dirty_patch_path)
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
                    "merge_mode": "fast_forward_then_patch",
                    "failed_files": check_result.get("failed_files", []),
                    "failed_hunks": check_result.get("failed_hunks", []),
                    "apply_check": check_result,
                    "rollback": {"rc": rollback_rc, "output": rollback_out},
                },
            )
        apply_patch_to_repo(source_repo, dirty_patch_path)

    updates: dict[str, object] = {
        "merge_mode": "fast_forward_then_patch",
        "fast_forward_ref": resolved_head_ref,
        "dirty_patch_path": dirty_patch_path.as_posix(),
        "dirty_patch_applied": dirty_patch_has_changes,
    }
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
    _write_pending_status(payload, pending_path, "applied")
    result = dict(payload)
    result["status"] = "applied"
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
    _write_pending_status(payload, pending_path, "discarded")
    result = dict(payload)
    result["status"] = "discarded"
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
