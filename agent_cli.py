#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-first multi-agent runner (PM → Dev → QA) with token-optimized unattended ops.

Entry point.

Usage:
  python agent_cli.py --repo /path/to/repo

If no config exists, an interactive wizard can generate one (Codex-CLI style).
"""

from agent_runner.main import main


if __name__ == "__main__":
    raise SystemExit(main())
