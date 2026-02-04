from __future__ import annotations

import argparse
import asyncio
import traceback
from pathlib import Path

from .backends.factory import get_runner
from .utils import eprint


async def _main_async_dispatch(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        eprint(f"Repo not found: {repo}")
        return 2
    runner = get_runner(getattr(args, "execution_backend", None))
    return await runner.run(args, repo)


def run(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_main_async_dispatch(args))
    except KeyboardInterrupt:
        return 130
    except Exception as ex:
        eprint(f"[FATAL] {ex}")
        if bool(getattr(args, "debug", False)):
            eprint(traceback.format_exc())
        return 1
