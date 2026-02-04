from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .utils import run_cmd, now_iso, safe_write_text


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


def create_worktree(repo: Path, worktree_dir: Path) -> Path:
    if worktree_dir.exists():
        shutil.rmtree(worktree_dir, ignore_errors=True)
    code, out = run_cmd(["git", "worktree", "add", "--detach", str(worktree_dir)], cwd=repo, timeout_sec=120)
    if code != 0:
        raise RuntimeError(f"Failed to create worktree: {out}")
    return worktree_dir


def remove_worktree(repo: Path, worktree_dir: Path) -> None:
    if not worktree_dir.exists():
        return
    run_cmd(["git", "worktree", "remove", "--force", str(worktree_dir)], cwd=repo, timeout_sec=120)
    shutil.rmtree(worktree_dir, ignore_errors=True)


def apply_worktree_changes(repo: Path, worktree_dir: Path) -> None:
    code, out = run_cmd(["git", "diff", "--binary", "HEAD"], cwd=worktree_dir, timeout_sec=120)
    if code != 0:
        raise RuntimeError(f"Failed to diff worktree changes: {out}")
    if out.strip():
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", errors="replace", delete=False) as tmp:
            tmp.write(out + "\n")
            patch_path = tmp.name
        try:
            code, out = run_cmd(
                ["git", "apply", "--binary", "--whitespace=nowarn", patch_path], cwd=repo, timeout_sec=120
            )
        finally:
            Path(patch_path).unlink(missing_ok=True)
        if code != 0:
            raise RuntimeError(f"Failed to apply worktree patch: {out}")

    untracked = list_untracked(worktree_dir)
    if untracked:
        copy_untracked(worktree_dir, untracked, repo)


def _write_rollback_report(path: Path, content: str) -> None:
    try:
        safe_write_text(path, content)
    except Exception:
        pass


def _format_cmd_result(label: str, cmd: list[str], rc: int, out: str) -> str:
    return "\n".join(
        [
            f"## {label}",
            f"- cmd: {' '.join(cmd)}",
            f"- rc: {rc}",
            "```",
            out or "(no output)",
            "```",
            "",
        ]
    )


def restore_checkpoint(
    repo: Path,
    cp: RepoCheckpoint,
    *,
    dangerous: bool,
    run_dir: Path | None = None,
    stop_path: Path | None = None,
) -> None:
    run_dir = run_dir or repo
    if not dangerous:
        _write_rollback_report(
            run_dir / "ROLLBACK_BLOCKED.md",
            "\n".join(
                [
                    "# ROLLBACK BLOCKED",
                    "",
                    "Rollback was requested but dangerous rollback is disabled.",
                    "No destructive reset/clean was executed.",
                    "",
                    f"- checkpoint: {cp.patch_path}",
                    "To allow destructive rollback, re-run with --dangerous-git-rollback.",
                    "",
                ]
            ),
        )
        raise RuntimeError("Rollback blocked: dangerous rollback is disabled.")

    rescue_dir = cp.patch_path.parent.parent / f"{cp.patch_path.parent.name}_rescue_{now_iso().replace(':', '').replace('-', '')}"
    rescue_cp = create_checkpoint(repo, rescue_dir)

    if stop_path is not None and stop_path.exists():
        raise RuntimeError("Rollback aborted: stop requested.")

    if not cp.patch_path.exists():
        _write_rollback_report(
            run_dir / "ROLLBACK_FAILURE.md",
            "\n".join(
                [
                    "# ROLLBACK FAILURE",
                    "",
                    "Checkpoint patch is missing.",
                    f"- patch: {cp.patch_path}",
                    f"- rescue: {rescue_cp.patch_path.parent}",
                ]
            ),
        )
        raise FileNotFoundError(f"Checkpoint patch missing: {cp.patch_path}")

    code, out = run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, timeout_sec=30)
    if code != 0 or out.strip().lower() != "true":
        _write_rollback_report(
            run_dir / "ROLLBACK_FAILURE.md",
            "\n".join(
                [
                    "# ROLLBACK FAILURE",
                    "",
                    "Target repo is not a git work tree.",
                    f"- rescue: {rescue_cp.patch_path.parent}",
                    _format_cmd_result("git rev-parse", ["git", "rev-parse", "--is-inside-work-tree"], code, out),
                ]
            ),
        )
        raise RuntimeError("Target repo is not a git work tree.")

    patch_text = cp.patch_path.read_text(encoding="utf-8", errors="replace")
    temp_patch_path: Path | None = None
    if patch_text.strip():
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", errors="replace", delete=False) as tmp:
            tmp.write(patch_text.rstrip() + "\n")
            temp_patch_path = Path(tmp.name)
    if not patch_text.strip() and not cp.untracked_dir.exists():
        _write_rollback_report(
            run_dir / "ROLLBACK_FAILURE.md",
            "\n".join(
                [
                    "# ROLLBACK FAILURE",
                    "",
                    "Checkpoint is empty (no patch or untracked files).",
                    f"- rescue: {rescue_cp.patch_path.parent}",
                ]
            ),
        )
        raise RuntimeError("Checkpoint empty; refusing to rollback.")

    try:
        precheck_out = ""
        precheck_code = 0
        if patch_text.strip():
            patch_for_check = temp_patch_path or cp.patch_path
            precheck_code, precheck_out = run_cmd(
                ["git", "apply", "--check", "--binary", str(patch_for_check)], cwd=repo, timeout_sec=120
            )
            if precheck_code != 0:
                with tempfile.TemporaryDirectory() as td:
                    wt_dir = Path(td) / "precheck"
                    wt_code, wt_out = run_cmd(["git", "worktree", "add", "--detach", str(wt_dir)], cwd=repo, timeout_sec=120)
                    if wt_code == 0:
                        try:
                            precheck_code, precheck_out = run_cmd(
                                ["git", "apply", "--check", "--binary", str(patch_for_check)], cwd=wt_dir, timeout_sec=120
                            )
                        finally:
                            run_cmd(["git", "worktree", "remove", "--force", str(wt_dir)], cwd=repo, timeout_sec=120)
                    else:
                        precheck_out = wt_out
                        precheck_code = wt_code

            if precheck_code != 0:
                _write_rollback_report(
                    run_dir / "ROLLBACK_FAILURE.md",
                    "\n".join(
                        [
                            "# ROLLBACK FAILURE",
                            "",
                            "Patch pre-check failed.",
                            f"- rescue: {rescue_cp.patch_path.parent}",
                            _format_cmd_result("git apply --check", ["git", "apply", "--check", "--binary", str(cp.patch_path)], precheck_code, precheck_out),
                        ]
                    ),
                )
                raise RuntimeError("Patch pre-check failed.")

        reset_code, reset_out = run_cmd(["git", "reset", "--hard"], cwd=repo, timeout_sec=120)
        clean_code, clean_out = run_cmd(["git", "clean", "-fd"], cwd=repo, timeout_sec=120)
        if reset_code != 0 or clean_code != 0:
            _write_rollback_report(
                run_dir / "ROLLBACK_FAILURE.md",
                "\n".join(
                    [
                        "# ROLLBACK FAILURE",
                        "",
                        "Reset/clean failed.",
                        f"- rescue: {rescue_cp.patch_path.parent}",
                        _format_cmd_result("git reset --hard", ["git", "reset", "--hard"], reset_code, reset_out),
                        _format_cmd_result("git clean -fd", ["git", "clean", "-fd"], clean_code, clean_out),
                    ]
                ),
            )
            raise RuntimeError("Reset/clean failed.")

        apply_code = 0
        apply_out = ""
        if patch_text.strip():
            patch_for_apply = temp_patch_path or cp.patch_path
            apply_code, apply_out = run_cmd(
                ["git", "apply", "--binary", "--whitespace=nowarn", str(patch_for_apply)],
                cwd=repo,
                timeout_sec=120,
            )
            if apply_code != 0:
                _write_rollback_report(
                    run_dir / "ROLLBACK_FAILURE.md",
                    "\n".join(
                        [
                            "# ROLLBACK FAILURE",
                            "",
                            "Patch apply failed.",
                            f"- rescue: {rescue_cp.patch_path.parent}",
                            _format_cmd_result("git apply", ["git", "apply", "--binary", "--whitespace=nowarn", str(cp.patch_path)], apply_code, apply_out),
                        ]
                    ),
                )
                raise RuntimeError("Patch apply failed.")
    finally:
        if temp_patch_path is not None:
            temp_patch_path.unlink(missing_ok=True)

    untracked_failures: list[str] = []
    if cp.untracked_dir.exists():
        for src in cp.untracked_dir.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(cp.untracked_dir)
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst)
            except Exception as ex:
                untracked_failures.append(f"{rel}: {ex}")

    if untracked_failures:
        _write_rollback_report(
            run_dir / "ROLLBACK_UNTRACKED_WARNING.md",
            "\n".join(
                [
                    "# ROLLBACK WARNING",
                    "",
                    "Some untracked files failed to restore:",
                    "",
                    *[f"- {ln}" for ln in untracked_failures],
                    "",
                ]
            ),
        )

    expected_changes = bool(patch_text.strip() or cp.untracked_dir.exists())
    porcelain = git_porcelain(repo)
    if expected_changes and not porcelain.strip():
        _write_rollback_report(
            run_dir / "ROLLBACK_FAILURE.md",
            "\n".join(
                [
                    "# ROLLBACK FAILURE",
                    "",
                    "Working tree did not change after rollback.",
                    f"- rescue: {rescue_cp.patch_path.parent}",
                ]
            ),
        )
        raise RuntimeError("Rollback produced no changes.")
