from __future__ import annotations

import argparse
import importlib
import os
import shutil
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class PreflightResult:
    backend: str
    ok: bool
    issues: list[str]


def _normalize_backend_list(backends: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in backends:
        val = str(raw or "").strip().lower()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def _check_import(module_name: str, label: str, issues: list[str]) -> None:
    try:
        importlib.import_module(module_name)
    except Exception:
        issues.append(f"missing dependency: {label}")


def _check_command(cmd: str, issues: list[str]) -> None:
    if shutil.which(cmd) is None:
        issues.append(f"missing executable in PATH: {cmd}")


def preflight_codex(args: argparse.Namespace) -> PreflightResult:
    issues: list[str] = []
    if not (os.getenv("OPENAI_API_KEY") or "").strip():
        issues.append("missing OPENAI_API_KEY")

    _check_import("agents", "openai-agents (agents)", issues)

    mcp_mode = str(getattr(args, "mcp_mode", "npx") or "npx").strip().lower()
    if mcp_mode == "npx":
        _check_command("npx", issues)
    elif mcp_mode == "codex":
        _check_command("codex", issues)

    return PreflightResult(backend="codex", ok=not issues, issues=issues)


def preflight_claudecode(args: argparse.Namespace) -> PreflightResult:
    issues: list[str] = []
    _check_import("claude_agent_sdk", "claude-agent-sdk", issues)

    return PreflightResult(backend="claudecode", ok=not issues, issues=issues)


def run_preflight(args: argparse.Namespace, backends: Iterable[str]) -> list[PreflightResult]:
    results: list[PreflightResult] = []
    for backend in _normalize_backend_list(backends):
        if backend == "codex":
            results.append(preflight_codex(args))
        elif backend == "claudecode":
            results.append(preflight_claudecode(args))
        else:
            results.append(PreflightResult(backend=backend, ok=False, issues=[f"unknown backend: {backend}"]))
    return results
