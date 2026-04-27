from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import AGENT_WORK_DIR, ensure_work_dir


def make_run_dir(repo: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    work_root = ensure_work_dir(repo)
    runs_root = work_root / "agent_runs"
    runs_root.mkdir(parents=True, exist_ok=True)

    # Keep second-level timestamps for readability, but guarantee uniqueness when
    # multiple starts land in the same second.
    for suffix in range(0, 10_000):
        name = ts if suffix == 0 else f"{ts}-{suffix:04d}"
        run_dir = runs_root / name
        try:
            run_dir.mkdir(parents=True, exist_ok=False)
            return run_dir
        except FileExistsError:
            continue

    raise RuntimeError("Unable to allocate a unique run directory.")


def find_latest_run_dir(repo: Path) -> Optional[Path]:
    """Return the latest run directory (or None).

    Checks .AgentCLI/agent_runs first; falls back to legacy .doc/agent_runs.
    """
    for parent in (AGENT_WORK_DIR, ".doc"):
        root = repo / parent / "agent_runs"
        if not root.exists() or not root.is_dir():
            continue
        dirs = [d for d in root.iterdir() if d.is_dir()]
        if not dirs:
            continue
        # Timestamp naming (YYYYMMDD-HHMMSS) sorts lexicographically by recency.
        dirs.sort(key=lambda x: x.name)
        return dirs[-1]
    return None
