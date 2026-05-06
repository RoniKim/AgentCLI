from __future__ import annotations

import time
from typing import Optional


def seconds_until_unix_reset(resets_at_unix: Optional[int]) -> int:
    """Calculate seconds until a Unix reset timestamp, with a small buffer."""

    if resets_at_unix is None:
        return 0
    try:
        diff = int(resets_at_unix) - int(time.time())
        return max(0, diff + 60)
    except Exception:
        return 0
