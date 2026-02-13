from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path
from typing import Any, Iterable, Sequence, Tuple

from .utils import run_cmd


DEFAULT_SCAN_IGNORE_GLOBS = [
    ".doc/**",
    ".doc",
    ".AgentCLI/**",
    ".AgentCLI",
    ".agent_runs/**",
    ".agent_runs",
    "worktree/**",
    "**/*.log",
]


def _normalize_relpath(raw: str) -> str:
    path = (raw or "").replace("\\", "/")
    if path.startswith("./"):
        path = path[2:]
    return path


def _normalize_globs(patterns: Iterable[str]) -> list[str]:
    out: list[str] = []
    for raw in patterns:
        s = str(raw or "").strip()
        if not s:
            continue
        out.append(_normalize_relpath(s))
    return out


def _matches_ignore(path: str, ignore_paths: Sequence[str], ignore_globs: Sequence[str]) -> bool:
    rel = _normalize_relpath(path)
    for prefix in ignore_paths:
        pref = _normalize_relpath(prefix)
        if pref and rel.startswith(pref):
            return True
    for pat in ignore_globs:
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


def _read_worktree_file(path: Path, max_bytes: int) -> Tuple[str, bytes] | None:
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    if len(raw) > max_bytes:
        return None
    if b"\x00" in raw:
        return None
    text = raw.decode("utf-8", errors="replace")
    return text, raw


def _read_staged_blob(repo: Path, rel: str, timeout_seconds: int) -> Tuple[str, bytes] | None:
    try:
        r = subprocess.run(
            ["git", "show", f":{rel}"],
            cwd=str(repo),
            capture_output=True,
            text=False,
            timeout=timeout_seconds,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    raw = r.stdout or b""
    if b"\x00" in raw:
        return None
    return raw.decode("utf-8", errors="replace"), raw


def _collect_candidates(repo: Path, scope: str, timeout_seconds: int, include_untracked_in_full: bool) -> list[str]:
    if scope == "staged":
        code, names = run_cmd(["git", "diff", "--cached", "--name-only"], cwd=repo, timeout_sec=timeout_seconds)
        return [ln.strip() for ln in names.splitlines() if ln.strip()] if code == 0 else []
    if scope == "full":
        code, names = run_cmd(["git", "ls-files"], cwd=repo, timeout_sec=timeout_seconds)
        tracked = [ln.strip() for ln in names.splitlines() if ln.strip()] if code == 0 else []
        if not include_untracked_in_full:
            return tracked
        code, untracked = run_cmd(
            ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo, timeout_sec=timeout_seconds
        )
        extra = [ln.strip() for ln in untracked.splitlines() if ln.strip()] if code == 0 else []
        return list(dict.fromkeys([*tracked, *extra]))
    code, names = run_cmd(["git", "diff", "--name-only"], cwd=repo, timeout_sec=timeout_seconds)
    changed = [ln.strip() for ln in names.splitlines() if ln.strip()] if code == 0 else []
    code, untracked = run_cmd(
        ["git", "ls-files", "--others", "--exclude-standard"], cwd=repo, timeout_sec=timeout_seconds
    )
    extra = [ln.strip() for ln in untracked.splitlines() if ln.strip()] if code == 0 else []
    return list(dict.fromkeys([*changed, *extra]))


def collect_scan_files(
    repo: Path,
    scope: str,
    *,
    ignore_paths: Sequence[str] = (),
    ignore_globs: Sequence[str] = (),
    max_files: int = 500,
    max_bytes_per_file: int = 200_000,
    max_total_bytes: int = 20_000_000,
    timeout_seconds: int = 60,
    include_untracked_in_full: bool = False,
) -> tuple[list[tuple[str, str]], dict[str, Any]]:
    scope_norm = str(scope or "quick").strip().lower()
    if scope_norm not in {"quick", "staged", "full"}:
        scope_norm = "quick"

    ignore_globs_norm = _normalize_globs(ignore_globs)
    ignore_paths_norm = [_normalize_relpath(p) for p in ignore_paths if str(p).strip()]

    candidates = _collect_candidates(repo, scope_norm, timeout_seconds, include_untracked_in_full)

    files: list[tuple[str, str]] = []
    skipped: dict[str, int] = {}
    files_scanned = 0
    bytes_scanned = 0

    def _skip(reason: str, count: int = 1) -> None:
        skipped[reason] = skipped.get(reason, 0) + count

    for idx, rel in enumerate(candidates):
        if max_files > 0 and files_scanned >= max_files:
            _skip("max_files", len(candidates) - idx)
            break
        if not rel:
            _skip("empty_path")
            continue
        if _matches_ignore(rel, ignore_paths_norm, ignore_globs_norm):
            _skip("ignored")
            continue

        if scope_norm == "staged":
            blob = _read_staged_blob(repo, rel, timeout_seconds)
            if blob is None:
                _skip("missing_or_binary")
                continue
            text, raw = blob
        else:
            pth = repo / rel
            if not pth.exists() or not pth.is_file():
                _skip("missing")
                continue
            blob = _read_worktree_file(pth, max_bytes_per_file)
            if blob is None:
                _skip("binary_or_too_large")
                continue
            text, raw = blob

        raw_size = len(raw)
        if max_bytes_per_file > 0 and raw_size > max_bytes_per_file:
            _skip("too_large")
            continue
        if max_total_bytes > 0 and (bytes_scanned + raw_size) > max_total_bytes:
            _skip("max_total_bytes", len(candidates) - idx)
            break

        files.append((rel, text))
        files_scanned += 1
        bytes_scanned += raw_size

    stats = {
        "scope": scope_norm,
        "files_scanned": files_scanned,
        "bytes_scanned": bytes_scanned,
        "files_skipped": sum(skipped.values()),
        "skipped": skipped,
    }
    return files, stats
