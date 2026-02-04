from __future__ import annotations

import sys

from .cli import parse_args
from .runner_entry import run


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return run(args)
