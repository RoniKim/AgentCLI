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
    """Inventory of ALL git-tracked files (binary/too large still listed as skipped)."""
    items: list[InventoryItem] = []
    for rel in git_ls_files(repo):
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
    lines.append("# REPO INVENTORY (git-tracked)")
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
