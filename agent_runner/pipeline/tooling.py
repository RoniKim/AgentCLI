from __future__ import annotations

"""Tooling backends for AgentCLI.

The runner currently uses an MCP stdio server process (e.g., Codex CLI) to
provide file editing, search, and other tools to the agents.

This module introduces a small abstraction layer so that different CLIs
(for example, future "Claude Code" integrations) can be plugged in without
touching the main cycle orchestration.

Notes:
  - [가정] Some third-party CLIs may use a different invocation contract.
    For maximum compatibility, we support overriding the command/args via
    --tool-command/--tool-args in config/CLI.
"""

import shlex
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional

from ..utils import eprint


def _norm_argv(v: Any) -> List[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        out: List[str] = []
        for x in v:
            s = str(x).strip()
            if s:
                out.append(s)
        return out
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # Support comma-separated argv lists in configs, and shell-like strings on CLI.
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return shlex.split(s)
    return []


@dataclass(frozen=True)
class ToolSpec:
    name: str
    command: str
    args: List[str]


def build_tool_spec(args: Any) -> Optional[ToolSpec]:
    """Build a tool specification from runner args.

    Priority:
      1) Explicit overrides: tool_command/tool_args
      2) tool_backend selector (codex/claude/disabled)
      3) Legacy args.mcp_mode + args.codex_package
    """
    # Legacy toggle
    legacy_mode = str(getattr(args, "mcp_mode", "codex") or "codex").strip().lower()
    tool_backend = str(getattr(args, "tool_backend", "") or "").strip().lower() or "auto"

    if tool_backend in {"disabled", "none", "off"} or legacy_mode == "disabled":
        return None

    name = str(getattr(args, "tool_name", "") or "").strip() or "Codex_CLI"

    tool_cmd = str(getattr(args, "tool_command", "") or "").strip()
    tool_args = _norm_argv(getattr(args, "tool_args", None))

    if tool_cmd:
        # Fully custom backend.
        return ToolSpec(name=name, command=tool_cmd, args=tool_args)

    # Backend presets
    if tool_backend in {"codex"}:
        return ToolSpec(name=name, command="codex", args=["mcp-server"])
    if tool_backend in {"claude", "claude_code"}:
        # [가정] Many MCP-enabled CLIs follow this shape. If not, override with tool_command/tool_args.
        return ToolSpec(name=name, command="claude", args=["mcp-server"])

    # Auto: map legacy mcp_mode
    if legacy_mode == "npx":
        pkg = str(getattr(args, "codex_package", "") or "").strip() or "@openai/codex@latest"
        return ToolSpec(name=name, command="npx", args=["-y", pkg, "mcp-server"])

    # Default: codex
    return ToolSpec(name=name, command="codex", args=["mcp-server"])


@asynccontextmanager
async def open_tool_server(MCPServerStdio: Any, spec: Optional[ToolSpec], *, session_timeout_seconds: int = 120):
    """Open an MCP stdio tool server.

    Yields:
      - MCP server object (SDK-specific) or None if disabled.
    """
    if spec is None:
        yield None
        return

    params = {"command": spec.command, "args": list(spec.args)}
    try:
        async with MCPServerStdio(
            name=spec.name,
            params=params,
            client_session_timeout_seconds=session_timeout_seconds,
        ) as server:
            yield server
    except FileNotFoundError as ex:
        eprint(f"[TOOL ERROR] Command not found: {spec.command} ({ex})")
        raise
    except Exception as ex:
        eprint(f"[TOOL ERROR] Failed to start tool backend '{spec.name}': {ex}")
        raise
