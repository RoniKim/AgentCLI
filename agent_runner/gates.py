from __future__ import annotations

"""
Gating helpers for build and test steps.

This module provides utilities to execute build and test commands
synchronously or asynchronously.  The original AgentCLI used
synchronous subprocess calls inside an asynchronous context, which
blocked the event loop and delayed handling of stop signals or other
tasks.  To address this, this module exposes both synchronous and
asynchronous variants for build and test gates.  The asynchronous
functions delegate to ``run_cmd_async`` from ``utils``, ensuring that
long‑running operations do not block the event loop.
"""

from pathlib import Path
from typing import List

from .utils import run_cmd, run_cmd_async


def _norm_cmd(v: object) -> List[str]:
    """Normalize a command specification into an argv list.

    Accepts ``None``, strings, or lists of strings.  Comma‑separated
    strings are split on commas, and whitespace‑separated strings are
    split on whitespace.  Empty values yield an empty list.
    """
    if v is None:
        return []
    if isinstance(v, list):
        out: List[str] = []
        for it in v:
            s = str(it).strip()
            if s:
                out.append(s)
        return out
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return [p for p in s.split() if p]
    return []


def run_build_gate(repo: Path, build_cmd: object, build_timeout_sec: int, legacy_build_target: str, log_path: Path) -> bool:
    """Run the build gate synchronously.

    This helper resolves a build command from the user‑specified
    ``build_cmd`` or falls back to a dotnet build invocation via
    ``find_build_cmd``.  It then executes the command using the
    synchronous ``run_cmd``.  The build output is always written to
    ``log_path``.  A non‑zero return code indicates a failed build.
    """
    cmd = _norm_cmd(build_cmd)
    if not cmd:
        cmd = find_build_cmd(repo, legacy_build_target)
    timeout = int(build_timeout_sec or 1800)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


async def run_build_gate_async(
    repo: Path,
    build_cmd: object,
    build_timeout_sec: int,
    legacy_build_target: str,
    log_path: Path,
) -> bool:
    """Run the build gate asynchronously without blocking the event loop.

    This version mirrors ``run_build_gate`` but uses ``run_cmd_async``
    under the hood.  It should be awaited from within an async
    context.  The build command is resolved in the same manner as the
    synchronous variant.  Output is written to ``log_path``.
    """
    cmd = _norm_cmd(build_cmd)
    if not cmd:
        cmd = find_build_cmd(repo, legacy_build_target)
    timeout = int(build_timeout_sec or 1800)
    code, out = await run_cmd_async(cmd, cwd=repo, timeout_sec=timeout)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


def run_test_gate(
    repo: Path,
    test_cmd: object,
    test_timeout_sec: int,
    legacy_test_target: str,
    legacy_test_filter: str,
    log_path: Path,
) -> bool:
    """Run the test gate synchronously.

    A test command is resolved either from the provided ``test_cmd`` or
    by calling ``find_test_cmd``.  It is then executed with
    ``run_cmd``.  Test output is captured and written to ``log_path``.
    A non‑zero return code indicates test failure.
    """
    cmd = _norm_cmd(test_cmd)
    if not cmd:
        cmd = find_test_cmd(repo, legacy_test_target, legacy_test_filter)
    timeout = int(test_timeout_sec or 3600)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


async def run_test_gate_async(
    repo: Path,
    test_cmd: object,
    test_timeout_sec: int,
    legacy_test_target: str,
    legacy_test_filter: str,
    log_path: Path,
) -> bool:
    """Run the test gate asynchronously without blocking the event loop.

    This helper resolves and executes the test command in a non‑blocking
    manner using ``run_cmd_async``.  The resolved command and timeout
    semantics mirror those of ``run_test_gate``.  Test output is
    captured and written to ``log_path``.
    """
    cmd = _norm_cmd(test_cmd)
    if not cmd:
        cmd = find_test_cmd(repo, legacy_test_target, legacy_test_filter)
    timeout = int(test_timeout_sec or 3600)
    code, out = await run_cmd_async(cmd, cwd=repo, timeout_sec=timeout)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


def find_build_cmd(repo: Path, explicit: str) -> List[str]:
    """Determine a dotnet build command for the given repository.

    If ``explicit`` is provided, the returned command is ``['dotnet',
    'build', explicit]``.  Otherwise, if there is exactly one
    ``.csproj`` file at the repository root, the project file is used
    as the build target.  Fallback is simply ``['dotnet', 'build']``.
    """
    if explicit:
        return ["dotnet", "build", explicit]
    root_csprojs = list(repo.glob("*.csproj"))
    if len(root_csprojs) == 1:
        return ["dotnet", "build", root_csprojs[0].name]
    return ["dotnet", "build"]


def dotnet_build(repo: Path, build_target: str, log_path: Path) -> bool:
    """Run a dotnet build with a fixed timeout synchronously."""
    cmd = find_build_cmd(repo, build_target)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=1800)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


def find_test_cmd(repo: Path, explicit: str, test_filter: str) -> List[str]:
    """Determine a dotnet test command for the given repository."""
    cmd = ["dotnet", "test"]
    if explicit:
        cmd.append(explicit)
    if test_filter:
        cmd.extend(["--filter", test_filter])
    return cmd


def dotnet_test(repo: Path, test_target: str, test_filter: str, log_path: Path, timeout_sec: int) -> bool:
    """Run a dotnet test with a configurable timeout synchronously."""
    cmd = find_test_cmd(repo, test_target, test_filter)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout_sec)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0