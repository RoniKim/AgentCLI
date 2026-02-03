from __future__ import annotations

import argparse
from pathlib import Path

from .base import AbstractAgentRunner


class CodexRunner(AbstractAgentRunner):
    """Default backend: preserve the legacy OpenAI/Codex flow."""

    name = "codex"

    async def run(self, args: argparse.Namespace, repo: Path) -> int:
        # Keep legacy behavior by delegating to the existing codex pipeline.
        # We accept `repo` for interface consistency; the legacy entrypoint resolves it again.
        from ..cycle import main_async as codex_main_async

        return await codex_main_async(args)
