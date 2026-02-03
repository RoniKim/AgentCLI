from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from pathlib import Path


class AbstractAgentRunner(ABC):
    """Backend-agnostic runner interface.

    This is a *progressive refactor* scaffold: the goal is to keep the existing
    Codex-based behavior unchanged while allowing alternative engines (e.g.
    Claude Agent SDK) to be plugged in.

    The orchestration layer should only depend on this interface.
    """

    name: str = "unknown"

    @abstractmethod
    async def run(self, args: argparse.Namespace, repo: Path) -> int:
        """Run the full session (may include multiple cycles in loop mode)."""

    # The methods below are part of the long-term interface. Concrete backends
    # may implement them incrementally.
    async def run_cycle(self) -> int:  # pragma: no cover
        raise NotImplementedError

    async def handle_error(self, exc: Exception) -> None:  # pragma: no cover
        raise NotImplementedError

    async def generate_report(self) -> None:  # pragma: no cover
        raise NotImplementedError
