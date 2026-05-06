#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-first multi-agent runner (PM → Dev → QA).

This bundle supports two entry modes:

1) Interactive shell (default, Codex-CLI style)
   - Allows checking/changing config before starting
   - Commands: /repo, /config, /start, /stop, /status, /save, /load, /set, /add ...

2) Web console server
   - Use: python agent_cli.py --web --repo /path/to/repo --port 8000

3) Legacy immediate run
   - For scripting / CI usage
   - Use: python agent_cli.py --run-now --repo /path/to/repo [other args...]

Notes:
- If you pass one-shot flags like --wizard or --init-prompts, it runs immediately
  (so existing workflows keep working).
"""

from __future__ import annotations

import sys


_ONE_SHOT_FLAGS = {"--wizard", "--init-prompts", "--preflight", "-h", "--help"}
_WEB_FLAGS = {"--web", "--serve-web"}


def _has_any_flag(argv: list[str], flags: set[str]) -> bool:
    return any(a in flags for a in argv)


def _has_flag(argv: list[str], flag: str) -> bool:
    return any(a == flag for a in argv)


def _without_flags(argv: list[str], flags: set[str]) -> list[str]:
    return [arg for arg in argv if arg not in flags]


def main(argv: list[str] | None = None) -> int:
    # Initialize process guard as early as possible (L1 Job Object before any child spawns)
    try:
        from agent_runner.process_guard import init_process_guard
        init_process_guard()
    except Exception:
        pass
    try:
        from agent_runner.logger import register_structured_logger_cleanup
        register_structured_logger_cleanup()
    except Exception:
        pass

    argv = list(sys.argv[1:] if argv is None else argv)

    # Web console server path. Accept both names so scripts can be explicit
    # without depending on the module-level `python -m agent_runner.web` form.
    if _has_any_flag(argv, _WEB_FLAGS):
        from agent_runner.web import main as web_main

        return int(web_main(_without_flags(argv, _WEB_FLAGS)))

    # Legacy / non-interactive paths
    if _has_flag(argv, "--run-now") or _has_any_flag(argv, _ONE_SHOT_FLAGS):
        from agent_runner.main import main
        return int(main(argv))

    # Telegram hybrid mode (local shell + Telegram control-plane)
    if _has_flag(argv, "--telegram"):
        from agent_runner.remote.telegram_service import telegram_hybrid_main
        return int(telegram_hybrid_main(argv))

    # Default: interactive shell
    from agent_runner.shell import shell_main
    return int(shell_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
