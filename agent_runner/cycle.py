from __future__ import annotations

"""Compatibility wrapper.

Historically, the runner orchestration lived in this module (cycle.py) and
grew large over time. It has been refactored into the modular pipeline
package, centered around :mod:`agent_runner.pipeline.manager`.

External entrypoints (agent_runner/main.py, agent_runner/shell.py) import
``run`` from here, so we keep this thin wrapper to avoid breaking users.
"""

import argparse

from .pipeline.manager import main_async, run

__all__ = ["main_async", "run"]
