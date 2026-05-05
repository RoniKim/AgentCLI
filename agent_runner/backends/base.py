from __future__ import annotations

import argparse
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


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


@dataclass(frozen=True)
class BackendQuotaStatus:
    """Normalized quota probe result returned by backend adapters."""

    action: str
    info: dict[str, Any]
    resets_at: Any = None
    available: bool = False

    @classmethod
    def from_probe(cls, action: str, info: dict[str, Any] | None, resets_at: Any = None) -> "BackendQuotaStatus":
        normalized_action = str(action or "skip").strip().lower() or "skip"
        normalized_info = dict(info or {})
        return cls(
            action=normalized_action,
            info=normalized_info,
            resets_at=resets_at,
            available=normalized_action != "skip",
        )

    def as_tuple(self) -> tuple[str, dict[str, Any], Any]:
        """Legacy tuple shape used by existing quota orchestration."""

        return (self.action, self.info, self.resets_at)


class BackendAdapter(ABC):
    """Backend boundary for model options, invocation, streaming, and quota."""

    name: str = "unknown"

    @abstractmethod
    def build_model_options(self, **kwargs: Any) -> Any:
        """Construct backend-specific model invocation options."""

    @abstractmethod
    async def invoke_model(self, prompt: str, **kwargs: Any) -> Any:
        """Invoke the backend model and return the backend-specific result."""

    @abstractmethod
    async def collect_messages(self, stream: Any, **kwargs: Any) -> tuple[str, Any | None]:
        """Normalize backend stream/message output to text plus structured data."""

    @abstractmethod
    def probe_quota(self, *, five_hour_max: float = 95.0, seven_day_max: float = 95.0) -> BackendQuotaStatus:
        """Probe backend-specific quota availability."""
