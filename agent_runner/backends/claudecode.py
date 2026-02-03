from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from ..docs import load_dotenv_best_effort
from ..utils import eprint, force_utf8_stdio


async def main_async_claudecode(args: argparse.Namespace, repo: Path) -> int:
    """
    Claude Code backend (scaffold).

    This intentionally keeps the initial scope minimal:
    - Ensure backend routing works via config/CLI.
    - Avoid affecting codex default behavior.
    - Provide actionable guidance when dependencies are missing.

    Next steps (planned):
    - Implement PM/Dev/QA using Claude Agent SDK (claude_agent_sdk) features such as:
      - ClaudeSDKClient sessions, interrupts, hooks, custom tools, and sandbox controls.
    """
    force_utf8_stdio()

    # Load repo .env as best-effort so ANTHROPIC_API_KEY can be picked up
    try:
        _ = load_dotenv_best_effort(repo, explicit_env_file=getattr(args, "env_file", ""), override=True)
    except Exception:
        pass

    # Claude Agent SDK import (Python package: claude-agent-sdk; import name: claude_agent_sdk)
    try:
        import claude_agent_sdk  # type: ignore  # noqa: F401
    except Exception:
        eprint("[ERR] execution_backend=claudecode requires Claude Agent SDK.")
        eprint("      Install: pip install -U claude-agent-sdk")
        eprint("      Docs: https://platform.claude.com/docs/ko/agent-sdk/python")
        return 2

    # Runtime: Agent SDK uses Claude Code (claude CLI) as runtime.
    if shutil.which("claude") is None:
        eprint("[ERR] Claude Code runtime (claude CLI) not found in PATH.")
        eprint("      Install Claude Code and authenticate (run: claude) first.")
        eprint("      Docs: https://platform.claude.com/docs/en/agent-sdk/quickstart")
        return 2

    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        # It's still possible that Claude Code auth exists, but warn.
        eprint("[WARN] ANTHROPIC_API_KEY is not set. If Claude Code is authenticated, SDK may still work.")

    eprint("[INFO] claudecode backend is scaffolded. Implementation will be added incrementally.")
    eprint("       For now, switch back to codex: /set execution_backend codex")
    return 2
