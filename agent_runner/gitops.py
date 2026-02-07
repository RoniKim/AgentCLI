from __future__ import annotations

import fnmatch
import hashlib
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .utils import run_cmd, now_iso, safe_write_text, eprint


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


def git_head(repo: Path) -> str:
    code, out = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo, timeout_sec=60)
    return out.strip() if code == 0 else ""


def git_ls_files(repo: Path) -> list[str]:
    code, out = run_cmd(["git", "ls-files"], cwd=repo, timeout_sec=120)
    if code != 0:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()]


def git_changed_files(repo: Path, prev_head: str, curr_head: str) -> list[str]:
    if not prev_head or not curr_head or prev_head == curr_head:
        return []
    code, out = run_cmd(["git", "diff", "--name-only", prev_head, curr_head], cwd=repo, timeout_sec=120)
    if code != 0:
        return []
    return [x.strip() for x in out.splitlines() if x.strip()]


def git_porcelain(repo: Path) -> str:
    code, out = run_cmd(["git", "status", "--porcelain"], cwd=repo, timeout_sec=60)
    return out if code == 0 else ""


def git_untracked_files(repo: Path) -> list[str]:
    """Get list of untracked files (not ignored)."""
    code, out = run_cmd(["git", "ls-files", "--others", "--exclude-standard"], cwd=repo, timeout_sec=30)
    if code != 0 or not out.strip():
        return []
    return [f.strip() for f in out.splitlines() if f.strip()]


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
    if code == 0 and staged.strip():
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
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


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
class RepoCheckpoint:
    patch_path: Path
    untracked_dir: Path
    created_at: str


def create_checkpoint(repo: Path, checkpoint_dir: Path) -> RepoCheckpoint:
    check_and_remove_stale_git_lock(repo)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    patch_path = checkpoint_dir / "tracked.patch"
    untracked_dir = checkpoint_dir / "untracked"

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

    return RepoCheckpoint(patch_path=patch_path, untracked_dir=untracked_dir, created_at=now_iso())


def _safe_ts() -> str:
    return now_iso().replace(":", "-")


def _write_report(path: Path, content: str) -> None:
    try:
        safe_write_text(path, content)
    except Exception:
        pass


def restore_checkpoint(
    repo: Path,
    cp: RepoCheckpoint,
    *,
    dangerous: bool,
    run_dir: Path | None,
    stop_path: Path | None,
) -> None:
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

    rescue_dir = (run_dir or cp.patch_path.parent.parent) / f"checkpoint_rescue_{_safe_ts()}"
    rescue_cp: RepoCheckpoint | None = None
    try:
        rescue_cp = create_checkpoint(repo, rescue_dir)
    except Exception as ex:
        _write_report(failure_path, f"# Rollback failure\n\nFailed to create rescue checkpoint: {ex}\n")
        raise

    def fail(reason: str) -> None:
        rescue_note = f"\n\nRescue checkpoint: {rescue_dir}\n" if rescue_cp else "\n\nRescue checkpoint: (none)\n"
        _write_report(failure_path, f"# Rollback failure\n\n{reason}{rescue_note}")
        raise RuntimeError(reason)

    code, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, timeout_sec=60)
    if code != 0 or "true" not in out.lower():
        fail(f"Not a git repository (rev-parse failed): rc={code}\n{out}")

    if not cp.patch_path.exists():
        fail(f"Missing checkpoint patch: {cp.patch_path}")

    patch_text = cp.patch_path.read_text(encoding="utf-8", errors="replace")
    if not patch_text.strip():
        fail(f"Checkpoint patch is empty: {cp.patch_path}")

    temp_patch = Path(tempfile.mkstemp(prefix="rollback_patch_", suffix=".patch")[1])
    temp_patch.write_text(patch_text + "\n", encoding="utf-8", errors="replace")

    temp_untracked_dir: Path | None = None
    if cp.untracked_dir.exists():
        temp_untracked_dir = Path(tempfile.mkdtemp(prefix="rollback_untracked_"))
        shutil.copytree(cp.untracked_dir, temp_untracked_dir, dirs_exist_ok=True)

    untracked_warnings: list[str] = []
    try:
        code, out = run_cmd(["git", "apply", "--check", "--binary", str(temp_patch)], cwd=repo, timeout_sec=120)
        if code != 0:
            fail(f"Patch pre-check failed (git apply --check): rc={code}\n{out}")

        code, out = run_cmd(["git", "reset", "--hard"], cwd=repo, timeout_sec=120)
        if code != 0:
            fail(f"git reset --hard failed: rc={code}\n{out}")

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

    porcelain = git_porcelain(repo)
    if not porcelain.strip():
        fail("Rollback applied but working tree is clean; expected changes after restore.")


def create_worktree(repo: Path, worktree_dir: Path) -> None:
    if worktree_dir.exists():
        return
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_cmd(["git", "worktree", "add", "--detach", str(worktree_dir), "HEAD"], cwd=repo, timeout_sec=120)
    if code != 0:
        raise RuntimeError(f"git worktree add failed: rc={code}\n{out}")


def remove_worktree(repo: Path, worktree_dir: Path) -> None:
    if not worktree_dir.exists():
        return
    code, out = run_cmd(["git", "worktree", "remove", "--force", str(worktree_dir)], cwd=repo, timeout_sec=120)
    if code != 0:
        raise RuntimeError(f"git worktree remove failed: rc={code}\n{out}")


def export_worktree_patch(
    worktree_dir: Path,
    patch_path: Path,
    *,
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
        code, out = run_cmd(["git", "diff", "--binary", "HEAD"], cwd=worktree_dir, timeout_sec=120)
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
        raise RuntimeError(f"git apply failed: rc={code}\n{out}")


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
    exclude_globs: Sequence[str] | None = None,
) -> int:
    patch_path = run_dir / "worktree.patch"
    try:
        export_worktree_patch(worktree_dir, patch_path, exclude_globs=exclude_globs)
        patch_text = patch_path.read_text(encoding="utf-8", errors="replace").strip()
        if patch_text:
            if last_rc == 0:
                apply_patch_to_repo(source_repo, patch_path)
            else:
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
