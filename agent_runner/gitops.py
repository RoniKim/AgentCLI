from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import List

from .utils import run_cmd, now_iso


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

    # Tracked changes patch (includes staged/unstaged). Untracked handled separately.
    _, out = run_cmd(["git", "diff", "--binary"], cwd=repo, timeout_sec=600)
    patch_path.write_text(out + "\n", encoding="utf-8", errors="replace")

    untracked = list_untracked(repo)
    if untracked:
        copy_untracked(repo, untracked, untracked_dir)

    return RepoCheckpoint(patch_path=patch_path, untracked_dir=untracked_dir, created_at=now_iso())


def restore_checkpoint(repo: Path, cp: RepoCheckpoint) -> None:
    # Clean working tree
    run_cmd(["git", "reset", "--hard"], cwd=repo, timeout_sec=120)
    run_cmd(["git", "clean", "-fd"], cwd=repo, timeout_sec=120)

    # Restore tracked patch
    try:
        patch_text = cp.patch_path.read_text(encoding="utf-8", errors="replace")
        if patch_text.strip():
            run_cmd(["git", "apply", "--binary", "--whitespace=nowarn", str(cp.patch_path)], cwd=repo, timeout_sec=120)
    except Exception:
        pass

    # Restore untracked files
    if cp.untracked_dir.exists():
        for src in cp.untracked_dir.rglob("*"):
            if src.is_dir():
                continue
            rel = src.relative_to(cp.untracked_dir)
            dst = repo / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass
