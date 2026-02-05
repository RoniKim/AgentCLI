from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from ..utils import atomic_write_json, atomic_write_text, eprint, read_text_robust
from .parser import parse_skill_text

MAX_SKILL_FILE_BYTES = 200_000


@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    description: str
    tags: list[str]
    source_root: str
    relative_path: str
    skill_path: Path
    last_modified: str
    content_hash: str


def _hash_text(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", errors="ignore")).hexdigest()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _skill_id(source_root: Path, relative_path: str) -> str:
    seed = f"{source_root.as_posix()}::{relative_path}"
    return f"{relative_path}#{_hash_text(seed)[:10]}"


def resolve_skills_roots(repo: Path, roots: Iterable[str]) -> list[Path]:
    resolved: list[Path] = []
    for raw in roots:
        s = str(raw or "").strip()
        if not s:
            continue
        if "{repo}" in s:
            s = s.replace("{repo}", str(repo))
        p = Path(s).expanduser()
        p = p if p.is_absolute() else (repo / p)
        try:
            resolved.append(p.resolve())
        except Exception:
            resolved.append(p)
    return resolved


def resolve_snapshot_dir(run_dir: Path, snapshot_dir: str) -> Path:
    s = str(snapshot_dir or "").strip()
    if not s:
        return run_dir / ".doc" / "skills"
    p = Path(s).expanduser()
    return p.resolve() if p.is_absolute() else (run_dir / p).resolve()


def _iter_skill_files(root: Path) -> Iterable[Path]:
    if not root.exists() or not root.is_dir():
        return []
    skill_files: list[Path] = []
    for dirpath, _, filenames in os.walk(root):
        if "SKILL.md" in filenames:
            skill_files.append(Path(dirpath) / "SKILL.md")
    return skill_files


def build_skills_index(roots: Iterable[Path], *, max_file_bytes: int = MAX_SKILL_FILE_BYTES) -> list[SkillRecord]:
    records: list[SkillRecord] = []
    for root in roots:
        if not root.exists():
            continue
        for skill_path in _iter_skill_files(root):
            try:
                size = skill_path.stat().st_size
            except Exception:
                continue
            if size > max_file_bytes:
                eprint(f"[SKILLS] Skip large SKILL.md: {skill_path} ({size} bytes)")
                continue
            text, status = read_text_robust(skill_path)
            if status != "ok":
                continue
            meta = parse_skill_text(text, fallback_name=skill_path.parent.name)
            rel_path = skill_path.parent.relative_to(root).as_posix()
            skill_id = _skill_id(root, rel_path)
            last_modified = datetime.fromtimestamp(skill_path.stat().st_mtime).isoformat(timespec="seconds")
            content_hash = _hash_bytes(text.encode("utf-8", errors="replace"))
            records.append(
                SkillRecord(
                    skill_id=skill_id,
                    name=meta.name,
                    description=meta.description,
                    tags=meta.tags,
                    source_root=root.as_posix(),
                    relative_path=rel_path,
                    skill_path=skill_path,
                    last_modified=last_modified,
                    content_hash=content_hash,
                )
            )
    records.sort(key=lambda r: (r.name.lower(), r.skill_id))
    return records


def summarize_skills_index(records: Iterable[SkillRecord]) -> str:
    rows = [
        "| skill_id | name | description | source_root | relative_path | tags |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for r in records:
        tags = ", ".join(r.tags) if r.tags else "-"
        desc = r.description.replace("\n", " ").strip()
        rows.append(
            f"| {r.skill_id} | {r.name} | {desc} | {r.source_root} | {r.relative_path} | {tags} |"
        )
    return "\n".join(rows) if rows else "(no skills indexed)"


def write_skills_snapshot(records: Iterable[SkillRecord], snapshot_dir: Path) -> tuple[Path, Path]:
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    payload = []
    for r in records:
        payload.append(
            {
                "skill_id": r.skill_id,
                "name": r.name,
                "description": r.description,
                "tags": r.tags,
                "source_root": r.source_root,
                "relative_path": r.relative_path,
                "last_modified": r.last_modified,
                "content_hash": r.content_hash,
            }
        )
    json_path = snapshot_dir / "SKILLS_INDEX.json"
    md_path = snapshot_dir / "SKILLS_INDEX.md"
    try:
        atomic_write_json(json_path, payload)
    except Exception as ex:
        eprint(f"[WARN] Failed to write SKILLS_INDEX.json atomically: {ex}")
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            errors="replace",
        )
    md_content = summarize_skills_index(records)
    try:
        atomic_write_text(md_path, md_content + "\n")
    except Exception as ex:
        eprint(f"[WARN] Failed to write SKILLS_INDEX.md atomically: {ex}")
        md_path.write_text(md_content + "\n", encoding="utf-8", errors="replace")
    return json_path, md_path
