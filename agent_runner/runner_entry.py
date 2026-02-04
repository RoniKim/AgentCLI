from __future__ import annotations

import argparse
import asyncio
import traceback
from pathlib import Path

from .backends.factory import get_runner
from .preflight import run_preflight
from .run_dir import find_latest_run_dir, make_run_dir
from .utils import detect_stop_reason, eprint


def _normalize_backend_list(raw: object, fallback: str) -> list[str]:
    if raw is None:
        return [fallback]
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
        return parts or [fallback]
    if isinstance(raw, list):
        out = [str(p).strip().lower() for p in raw if str(p).strip()]
        return out or [fallback]
    return [str(raw).strip().lower() or fallback]


def _ensure_run_dir(repo: Path, args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "run_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if bool(getattr(args, "resume_latest", False)):
        latest = find_latest_run_dir(repo)
        if latest is not None:
            return latest.expanduser().resolve()
    return make_run_dir(repo)


async def _run_single_backend(args: argparse.Namespace, repo: Path, backend: str) -> int:
    args.execution_backend = backend
    runner = get_runner(backend)
    return await runner.run(args, repo)


async def _main_async_dispatch(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        eprint(f"Repo not found: {repo}")
        return 2

    failover_enabled = bool(getattr(args, "failover_enabled", False))
    if not failover_enabled:
        return await _run_single_backend(args, repo, str(getattr(args, "execution_backend", "codex") or "codex"))

    base_backend = str(getattr(args, "execution_backend", "codex") or "codex").strip().lower()
    backends = _normalize_backend_list(getattr(args, "failover_backends", None), base_backend)
    failover_on = set(str(x).strip().lower() for x in (getattr(args, "failover_on", []) or []))
    max_switches = int(getattr(args, "failover_max_switches", 1) or 1)

    preflight_results = run_preflight(args, backends)
    available = [r.backend for r in preflight_results if r.ok]
    if not available:
        eprint("[FAILOVER] No backends passed preflight checks.")
        for result in preflight_results:
            detail = "; ".join(result.issues) if result.issues else "ok"
            eprint(f"[FAILOVER] {result.backend}: {detail}")
        return 2

    run_dir = _ensure_run_dir(repo, args)
    args.run_dir = str(run_dir)

    switch_count = 0
    stop_file = str(getattr(args, "stop_file", "STOP") or "STOP")
    stop_paths = [run_dir / stop_file, run_dir / "STOP"]

    for idx, backend in enumerate(available):
        rc = await _run_single_backend(args, repo, backend)
        reason = detect_stop_reason(stop_paths)
        if (
            reason
            and reason in failover_on
            and switch_count < max_switches
            and idx < (len(available) - 1)
        ):
            switch_count += 1
            for path in stop_paths:
                try:
                    if path.exists():
                        path.unlink()
                except Exception:
                    pass
            eprint(f"[FAILOVER] Switching backend after {reason}: {backend} -> {available[idx + 1]}")
            continue
        return rc

    return 1


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
