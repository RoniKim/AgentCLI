"""Pipeline/orchestration package.

This package contains the main runner orchestration logic and the tooling
backend abstraction.
"""

from .manager import main_async, run

__all__ = ["main_async", "run"]
