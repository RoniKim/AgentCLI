from __future__ import annotations

from typing import Any


def _normalize_backend(v: Any) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return "codex"
    if s in {"codex", "openai", "openai-agents", "agents"}:
        return "codex"
    if s in {"claude", "claude-code", "claude_code", "claudecode", "anthropic"}:
        return "claudecode"
    return s


def get_runner(execution_backend: Any):
    """Return the runner implementation for the configured execution backend.

    IMPORTANT: default is **codex** to preserve backward compatibility.
    """
    backend = _normalize_backend(execution_backend)
    if backend == "claudecode":
        from .claudecode_runner import ClaudeCodeRunner

        return ClaudeCodeRunner()

    # default
    from .codex_runner import CodexRunner

    return CodexRunner()
