from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXPERIENCE_RECORD_FILENAMES = (
    "EXPERIENCE_UPDATES.jsonl",
    "experience_updates.jsonl",
    "experience_records.jsonl",
    "experience.jsonl",
)


def _relative_run_path(run_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(run_dir.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return records
    for line_no, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            item = dict(payload)
            item.setdefault("record_line", line_no)
            records.append(item)
    return records


def load_run_experience_records(run_dir: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    root = Path(run_dir).expanduser()
    for filename in EXPERIENCE_RECORD_FILENAMES:
        for path in sorted(root.rglob(filename)):
            relative_path = _relative_run_path(root, path)
            for item in _load_jsonl_records(path):
                record = dict(item)
                record.setdefault("record_path", relative_path)
                records.append(record)
    return records

