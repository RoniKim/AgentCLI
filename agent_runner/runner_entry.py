from __future__ import annotations

import argparse
import asyncio
import json
import traceback
from pathlib import Path

from .backends.factory import get_runner
from .logger import close_all_loggers, register_structured_logger_cleanup
from .metrics import MetricsLogger
from .preflight import run_preflight
from .process_guard import init_process_guard, install_signal_handlers, terminate_all_children
from .reporting import write_emergency_shutdown_report
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
    eprint(f"[BACKEND] Starting execution with backend: {runner.name}")
    return await runner.run(args, repo)


def _detect_backend_stop_reason(run_dir: Path, stop_paths: list[Path]) -> str:
    """Detect stop reason from STOP file first, then run_summary.json final reason."""
    reason = ""
    try:
        reason = detect_stop_reason(stop_paths)
    except Exception:
        reason = ""
    if reason:
        return reason

    summary_path = run_dir / "run_summary.json"
    try:
        if not summary_path.exists():
            return ""
        raw = summary_path.read_text(encoding="utf-8", errors="replace")
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            return ""
        final = payload.get("final")
        if not isinstance(final, dict):
            return ""
        return str(final.get("reason") or "").strip().lower()
    except Exception:
        return ""


async def _main_async_dispatch(args: argparse.Namespace) -> int:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.exists():
        eprint(f"Repo not found: {repo}")
        return 2

    failover_enabled = bool(getattr(args, "failover_enabled", False))
    if not failover_enabled:
        return await _run_single_backend(args, repo, str(getattr(args, "execution_backend", "codex") or "codex"))

    base_backend = str(getattr(args, "execution_backend", "codex") or "codex").strip().lower()
    raw_backends = _normalize_backend_list(getattr(args, "failover_backends", None), base_backend)
    # Ensure primary backend runs first, then the rest in failover order
    backends = [base_backend] + [b for b in raw_backends if b != base_backend]
    failover_on = set(str(x).strip().lower() for x in (getattr(args, "failover_on", []) or []))
    raw_max_switches = getattr(args, "failover_max_switches", 1)
    try:
        max_switches = int(raw_max_switches if raw_max_switches is not None else 1)
    except Exception:
        max_switches = 1

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

    _MAX_ABSOLUTE_SWITCHES = 100  # safety cap even for unlimited (max_switches=0)
    switch_count = 0
    stop_file = str(getattr(args, "stop_file", "STOP") or "STOP")
    stop_paths = [run_dir / stop_file, run_dir / "STOP"]
    if len(available) == 1:
        return await _run_single_backend(args, repo, available[0])

    idx = 0
    while True:
        backend = available[idx]
        rc = await _run_single_backend(args, repo, backend)
        reason = _detect_backend_stop_reason(run_dir, stop_paths)

        if max_switches <= 0:
            has_switch_budget = switch_count < _MAX_ABSOLUTE_SWITCHES
        else:
            has_switch_budget = switch_count < max_switches
        if not (reason and reason in failover_on and has_switch_budget):
            return rc

        next_idx = (idx + 1) % len(available)
        if next_idx == idx:
            return rc

        switch_count += 1
        try:
            ml = MetricsLogger(run_dir / "metrics.jsonl")
            ml.event(
                "backend_failover",
                from_backend=backend,
                to_backend=available[next_idx],
                reason=reason,
                switch_count=switch_count,
            )
        except Exception:
            pass
        for path in stop_paths:
            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

        limit_txt = "unlimited" if max_switches <= 0 else str(max_switches)
        eprint(
            f"[FAILOVER] Switching backend after {reason}: "
            f"{backend} -> {available[next_idx]} (switch {switch_count}/{limit_txt})"
        )
        idx = next_idx


def _install_signal_handlers(args: argparse.Namespace) -> None:
    """Install enhanced signal handlers: STOP file + child process kill (L3)."""
    run_dir_str = str(getattr(args, "run_dir", "") or "").strip()
    if not run_dir_str:
        return

    run_dir = Path(run_dir_str)

    def _stop_path_func() -> Path:
        stop_file = str(getattr(args, "stop_file", "STOP") or "STOP")
        return run_dir / stop_file

    install_signal_handlers(stop_path_func=_stop_path_func)


def run(args: argparse.Namespace) -> int:
    # Init process guard (L1/L2/L4) before anything else
    try:
        init_process_guard()
    except Exception:
        pass
    try:
        register_structured_logger_cleanup()
    except Exception:
        pass

    # Ensure run_dir is set for signal handler STOP file creation
    if not getattr(args, "run_dir", ""):
        repo = Path(str(getattr(args, "repo", "") or ".")).resolve()
        args.run_dir = str(_ensure_run_dir(repo, args))

    try:
        _install_signal_handlers(args)
    except ValueError:
        # signal handlers can only be set from the main thread;
        # in shell mode the runner runs in a background thread, so skip gracefully.
        pass
    try:
        return asyncio.run(_main_async_dispatch(args))
    except KeyboardInterrupt:
        _run_dir = str(getattr(args, "run_dir", "") or "").strip()
        if _run_dir:
            _repo = Path(str(getattr(args, "repo", "") or ".")).resolve()
            write_emergency_shutdown_report(Path(_run_dir), "KeyboardInterrupt", repo=_repo)
        terminate_all_children()
        return 130
    except Exception as ex:
        eprint(f"[FATAL] {ex}")
        eprint(traceback.format_exc())
        _run_dir = str(getattr(args, "run_dir", "") or "").strip()
        if _run_dir:
            _repo = Path(str(getattr(args, "repo", "") or ".")).resolve()
            write_emergency_shutdown_report(Path(_run_dir), f"{type(ex).__name__}: {ex}", repo=_repo)
        terminate_all_children()
        return 1
    finally:
        close_all_loggers()
