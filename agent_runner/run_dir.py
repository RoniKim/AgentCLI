from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional


def make_run_dir(repo: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = repo / ".doc" / "agent_runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def find_latest_run_dir(repo: Path) -> Optional[Path]:
    """Return the latest run directory under repo/.doc/agent_runs (or None)."""
    root = repo / ".doc" / "agent_runs"
    if not root.exists() or not root.is_dir():
        return None
    dirs = [d for d in root.iterdir() if d.is_dir()]
    if not dirs:
        return None
    # Timestamp naming (YYYYMMDD-HHMMSS) sorts lexicographically by recency.
    dirs.sort(key=lambda x: x.name)
    return dirs[-1]
