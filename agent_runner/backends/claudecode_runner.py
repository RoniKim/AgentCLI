from __future__ import annotations

import argparse
from pathlib import Path

from .base import AbstractAgentRunner


class ClaudeCodeRunner(AbstractAgentRunner):
    """Claude Agent SDK backend (currently a scaffold).

    The implementation will be expanded incrementally using the Claude Agent SDK
    reference: https://platform.claude.com/docs/ko/agent-sdk/python
    """

    name = "claudecode"

    async def run(self, args: argparse.Namespace, repo: Path) -> int:
        from .claudecode import main_async_claudecode

        return await main_async_claudecode(args, repo)