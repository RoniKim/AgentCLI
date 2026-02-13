from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .gitops import git_ls_files
from .utils import now_iso


def is_probably_binary(path: Path, sample_bytes: int = 4096) -> bool:
    try:
        b = path.read_bytes()[:sample_bytes]
        return b"\x00" in b
    except Exception:
        return True


@dataclass(frozen=True)
class InventoryItem:
    path: str
    size: int
    ext: str
    binary: bool
    skipped_reason: str


def build_repo_inventory(repo: Path, max_file_size: int = 2_000_000) -> list[InventoryItem]:
    """Inventory of ALL repo files.

    Primary source is *git-tracked* files (via `git ls-files`).
    However, in the field we sometimes fail to read inventory because:
    - Git is not installed / not on PATH
    - repo path is not a Git worktree (or permissions issues)

    In those cases, we fall back to a conservative filesystem walk (excluding common large dirs).
    """

    def _fallback_walk() -> list[str]:
        # Keep this conservative to avoid accidentally including huge trees (node_modules, bin/obj, .venv, etc.)
        IGNORE_DIRS = {
            ".git",
            ".doc",
            ".AgentCLI",
            ".venv",
            "venv",
            "__pycache__",
            "node_modules",
            "bin",
            "obj",
            ".idea",
            ".vs",
            "dist",
            "build",
        }
        MAX_FILES = 5000

        rels: list[str] = []
        try:
            for p in repo.rglob("*"):
                if not p.is_file():
                    continue
                if any(part in IGNORE_DIRS for part in p.parts):
                    continue
                rels.append(p.relative_to(repo).as_posix())
                if len(rels) >= MAX_FILES:
                    rels.append("(truncated: too many files)")
                    break
        except Exception:
            return []

        rels.sort()
        return rels

    rel_paths = git_ls_files(repo)
    if not rel_paths:
        rel_paths = _fallback_walk()

    items: list[InventoryItem] = []
    for rel in rel_paths:
        p = repo / rel
        try:
            size = int(p.stat().st_size)
        except Exception:
            size = -1
        ext = p.suffix.lower()
        binary = is_probably_binary(p) if p.exists() and size >= 0 else True

        skipped_reason = ""
        if size < 0 or not p.exists():
            skipped_reason = "missing"
        elif size > max_file_size:
            skipped_reason = f"too_large>{max_file_size}"
        elif binary:
            skipped_reason = "binary"

        items.append(
            InventoryItem(
                path=rel.replace("\\", "/"),
                size=size,
                ext=ext,
                binary=binary,
                skipped_reason=skipped_reason,
            )
        )
    return items


def write_repo_inventory_files(repo: Path, pm_cache_dir: Path, inventory: list[InventoryItem]) -> tuple[Path, Path]:
    inv_json = pm_cache_dir / "REPO_INVENTORY.json"
    inv_md = pm_cache_dir / "REPO_INVENTORY.md"
    pm_cache_dir.mkdir(parents=True, exist_ok=True)

    inv_json.write_text(
        json.dumps(
            {"generated_at": now_iso(), "count": len(inventory), "files": [item.__dict__ for item in inventory]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
        errors="replace",
    )

    lines: list[str] = []
    lines.append("# REPO INVENTORY (git-tracked; fallback=walk)")
    lines.append("")
    lines.append(f"- generated_at: {now_iso()}")
    lines.append(f"- count: {len(inventory)}")
    lines.append("")
    lines.append("## Files")
    lines.append("")
    lines.append("| path | size | ext | binary | skipped_reason |")
    lines.append("|---|---:|---|---|---|")
    for it in inventory:
        lines.append(f"| `{it.path}` | {it.size} | `{it.ext}` | {str(it.binary).lower()} | `{it.skipped_reason}` |")
    inv_md.write_text("\n".join(lines) + "\n", encoding="utf-8", errors="replace")
    return inv_json, inv_md
