from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..runner_entry import run as run_runner


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--args-json", required=True, help="Path to runner args payload")
    ns = parser.parse_args(argv)

    payload_path = Path(ns.args_json).expanduser().resolve()
    if not payload_path.exists():
        print(f"[ERR] args payload not found: {payload_path}")
        return 2

    try:
        raw = payload_path.read_text(encoding="utf-8", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as ex:
        print(f"[ERR] failed to load args payload: {ex}")
        return 2

    if not isinstance(payload, dict):
        print(f"[ERR] invalid args payload (expected object): {payload_path}")
        return 2

    args = argparse.Namespace(**payload)
    return run_runner(args)


if __name__ == "__main__":
    raise SystemExit(main())

