"""Shared exception classes used by both Codex and Claude Code backends."""
from __future__ import annotations


class BudgetExceeded(Exception):
    """Raised when a budget limit (escalation / continuation / repair) is exceeded."""
    pass


class StopRequested(Exception):
    """Raised when a graceful stop is requested (stop file, user interrupt)."""
    pass
