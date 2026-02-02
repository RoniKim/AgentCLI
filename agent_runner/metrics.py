from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .utils import now_iso


class MetricsLogger:
    """Append-only JSONL event logger for unattended operations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event_type: str, **fields: Any) -> None:
        rec: Dict[str, Any] = {"ts": now_iso(), "type": event_type}
        rec.update(fields)
        try:
            with self.path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            # Avoid breaking the runner on observability failures.
            pass
