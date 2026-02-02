from __future__ import annotations

from datetime import datetime
from pathlib import Path


def make_run_dir(repo: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = repo / ".doc" / "agent_runs" / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir
