#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-first multi-agent runner (PM → Dev → QA).

This bundle supports two entry modes:

1) Interactive shell (default, Codex-CLI style)
   - Allows checking/changing config before starting
   - Commands: /repo, /config, /start, /stop, /status, /save, /load, /set, /add ...

2) Legacy immediate run
   - For scripting / CI usage
   - Use: python agent_cli.py --run-now --repo /path/to/repo [other args...]

Notes:
- If you pass one-shot flags like --wizard or --init-prompts, it runs immediately
  (so existing workflows keep working).
"""

from __future__ import annotations

import sys


_ONE_SHOT_FLAGS = {"--wizard", "--init-prompts", "--preflight", "-h", "--help"}


def _has_any_flag(argv: list[str], flags: set[str]) -> bool:
    return any(a in flags for a in argv)


def _has_flag(argv: list[str], flag: str) -> bool:
    return any(a == flag for a in argv)


if __name__ == "__main__":
    # Initialize process guard as early as possible (L1 Job Object before any child spawns)
    try:
        from agent_runner.process_guard import init_process_guard
        init_process_guard()
    except Exception:
        pass

    argv = sys.argv[1:]

    # Legacy / non-interactive paths
    if _has_flag(argv, "--run-now") or _has_any_flag(argv, _ONE_SHOT_FLAGS):
        from agent_runner.main import main
        raise SystemExit(main(argv))

    # Telegram hybrid mode (local shell + Telegram control-plane)
    if _has_flag(argv, "--telegram"):
        from agent_runner.remote.telegram_service import telegram_hybrid_main
        raise SystemExit(telegram_hybrid_main(argv))

    # Default: interactive shell
    from agent_runner.shell import shell_main
    raise SystemExit(shell_main(argv))
