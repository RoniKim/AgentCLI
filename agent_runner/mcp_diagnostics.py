from __future__ import annotations

import shutil
from typing import Any, Callable


MCP_MODE_CHOICES = ("npx", "codex", "disabled")
DEFAULT_MCP_MODE = "npx"
DEFAULT_MCP_TIMEOUT_SECONDS = 120
DEFAULT_CODEX_PACKAGE = "@openai/codex@latest"


def _source_get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(key, default)
    try:
        return getattr(source, key)
    except Exception:
        return default


def _issue(code: str, message: str, **details: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": message}
    if details:
        payload["details"] = details
    return payload


def _normalize_mode(raw_mode: Any) -> tuple[str, str, list[dict[str, Any]]]:
    selected = str(raw_mode if raw_mode not in (None, "") else DEFAULT_MCP_MODE).strip().lower()
    if not selected:
        selected = DEFAULT_MCP_MODE
    aliases = {
        "off": "disabled",
        "disable": "disabled",
        "false": "disabled",
        "none": "disabled",
        "openai": "codex",
        "codex-cli": "codex",
    }
    normalized = aliases.get(selected, selected)
    if normalized in MCP_MODE_CHOICES:
        return selected, normalized, []
    return (
        selected,
        "disabled",
        [
            _issue(
                "mcp_invalid_mode",
                "Unknown MCP mode; diagnostics will use disabled mode as a non-blocking fallback.",
                selected_mode=selected,
                supported_modes=list(MCP_MODE_CHOICES),
            )
        ],
    )


def _normalize_timeout(raw_timeout: Any) -> tuple[int, list[dict[str, Any]]]:
    if raw_timeout in (None, ""):
        return DEFAULT_MCP_TIMEOUT_SECONDS, []
    try:
        if isinstance(raw_timeout, bool):
            raise ValueError("boolean is not a timeout")
        timeout = int(raw_timeout)
    except Exception:
        return (
            DEFAULT_MCP_TIMEOUT_SECONDS,
            [
                _issue(
                    "mcp_timeout_invalid",
                    "MCP timeout is invalid; diagnostics will use the default timeout.",
                    configured_timeout=raw_timeout,
                    default_timeout_seconds=DEFAULT_MCP_TIMEOUT_SECONDS,
                )
            ],
        )
    if timeout < 0:
        return (
            0,
            [
                _issue(
                    "mcp_timeout_invalid",
                    "MCP timeout is negative; diagnostics will clamp it to zero.",
                    configured_timeout=raw_timeout,
                )
            ],
        )
    return timeout, []


def _required_tool_for_mode(mode: str) -> str:
    if mode == "npx":
        return "npx"
    if mode == "codex":
        return "codex"
    return ""


def _fallback_payload(reason: str, *, active: bool) -> dict[str, Any]:
    messages = {
        "tool_available": "MCP launcher is available; non-MCP runs remain independent.",
        "tool_unavailable": "MCP launcher is unavailable; backend built-in or local execution can continue without blocking non-MCP runs.",
        "mcp_disabled": "MCP mode is disabled; non-MCP runs continue normally.",
        "invalid_mode": "MCP mode is invalid; diagnostics selected disabled mode so non-MCP runs continue normally.",
        "invalid_timeout": "MCP timeout was normalized; non-MCP runs remain unblocked.",
    }
    return {
        "safe": True,
        "enabled": True,
        "active": bool(active),
        "blocking": False,
        "non_mcp_runs_blocked": False,
        "nonMcpRunsBlocked": False,
        "reason": reason,
        "message": messages.get(reason, messages["tool_available"]),
    }


def build_mcp_diagnostics(
    source: Any,
    *,
    tool_lookup: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    selected_mode, mode, issues = _normalize_mode(_source_get(source, "mcp_mode", DEFAULT_MCP_MODE))
    timeout_seconds, timeout_issues = _normalize_timeout(_source_get(source, "mcp_timeout_seconds", DEFAULT_MCP_TIMEOUT_SECONDS))
    issues.extend(timeout_issues)

    package = str(_source_get(source, "codex_package", DEFAULT_CODEX_PACKAGE) or DEFAULT_CODEX_PACKAGE).strip()
    required_tool = _required_tool_for_mode(mode)
    required_tools = [required_tool] if required_tool else []
    lookup = tool_lookup or shutil.which
    tool_details: list[dict[str, Any]] = []
    unavailable_tools: list[str] = []
    for tool_name in required_tools:
        resolved_path = lookup(tool_name)
        available = bool(resolved_path)
        if not available:
            unavailable_tools.append(tool_name)
        tool_details.append(
            {
                "name": tool_name,
                "available": available,
                "path": str(resolved_path or ""),
            }
        )

    warnings = list(issues)
    if unavailable_tools:
        warnings.append(
            _issue(
                "mcp_tool_unavailable",
                "The selected MCP mode requires a launcher that is not available on PATH.",
                selected_mode=selected_mode,
                mode=mode,
                unavailable_tools=list(unavailable_tools),
            )
        )

    status = "warning" if warnings else "ok"
    if any(item.get("code") == "mcp_invalid_mode" for item in warnings):
        fallback_reason = "invalid_mode"
    elif any(item.get("code") == "mcp_timeout_invalid" for item in warnings):
        fallback_reason = "invalid_timeout"
    elif mode == "disabled":
        fallback_reason = "mcp_disabled"
    elif unavailable_tools:
        fallback_reason = "tool_unavailable"
    else:
        fallback_reason = "tool_available"
    fallback_active = fallback_reason != "tool_available"
    safe_fallback = _fallback_payload(fallback_reason, active=fallback_active)

    return {
        "status": status,
        "valid": not bool(issues),
        "selected_mode": selected_mode,
        "selectedMode": selected_mode,
        "mode": mode,
        "effective_mode": mode,
        "effectiveMode": mode,
        "timeout_seconds": timeout_seconds,
        "timeoutSeconds": timeout_seconds,
        "codex_package": package,
        "codexPackage": package,
        "required_tools": required_tools,
        "requiredTools": required_tools,
        "tool_details": tool_details,
        "toolDetails": tool_details,
        "unavailable_tools": unavailable_tools,
        "unavailableTools": unavailable_tools,
        "issues": issues,
        "warnings": warnings,
        "safe_fallback": safe_fallback,
        "safeFallback": safe_fallback,
        "non_mcp_runs_blocked": False,
        "nonMcpRunsBlocked": False,
    }


def format_mcp_diagnostics_lines(diagnostics: dict[str, Any], *, indent: str = "") -> list[str]:
    status = str(diagnostics.get("status") or "unknown")
    selected_mode = str(diagnostics.get("selected_mode") or diagnostics.get("selectedMode") or "")
    effective_mode = str(diagnostics.get("effective_mode") or diagnostics.get("effectiveMode") or diagnostics.get("mode") or "")
    timeout_seconds = diagnostics.get("timeout_seconds", diagnostics.get("timeoutSeconds", ""))
    package = str(diagnostics.get("codex_package") or diagnostics.get("codexPackage") or "")
    required_tools = [str(item) for item in diagnostics.get("required_tools") or diagnostics.get("requiredTools") or []]
    unavailable_tools = [str(item) for item in diagnostics.get("unavailable_tools") or diagnostics.get("unavailableTools") or []]
    fallback = diagnostics.get("safe_fallback") if isinstance(diagnostics.get("safe_fallback"), dict) else {}
    lines = [
        f"{indent}mode: selected={selected_mode or '(default)'} effective={effective_mode or '(unknown)'} status={status}",
        f"{indent}timeout: {timeout_seconds}s",
        f"{indent}codex_package: {package or '(default)'}",
        f"{indent}required tools: {', '.join(required_tools) if required_tools else 'none'}",
        f"{indent}unavailable tools: {', '.join(unavailable_tools) if unavailable_tools else 'none'}",
        (
            f"{indent}safe fallback: active={bool(fallback.get('active', False))} "
            f"blocking={bool(fallback.get('blocking', False))} "
            f"reason={fallback.get('reason') or 'unknown'} "
            f"non_mcp_runs_blocked={bool(fallback.get('non_mcp_runs_blocked', fallback.get('nonMcpRunsBlocked', False)))}"
        ),
    ]
    for issue in diagnostics.get("warnings") or []:
        if not isinstance(issue, dict):
            continue
        code = str(issue.get("code") or "mcp_warning")
        message = str(issue.get("message") or "").strip()
        lines.append(f"{indent}warning: {code}" + (f" - {message}" if message else ""))
    return lines
