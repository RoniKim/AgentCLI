from __future__ import annotations

import argparse
from pathlib import Path
from collections.abc import Callable
from typing import Any

from .base import AbstractAgentRunner, BackendAdapter, BackendQuotaStatus
from ..codex_exec import CodexExecResult, _parse_events, codex_exec
from ..utils import check_codex_quota_utilization, has_quota_text


class CodexBackendAdapter(BackendAdapter):
    """Codex CLI adapter for invocation, output normalization, and quota."""

    name = "codex"

    def __init__(
        self,
        *,
        executor: Callable[..., Any] = codex_exec,
        quota_probe_fn: Callable[..., tuple[str, dict[str, Any], Any]] = check_codex_quota_utilization,
    ) -> None:
        self._executor = executor
        self._quota_probe_fn = quota_probe_fn

    def build_model_options(self, **kwargs: Any) -> dict[str, Any]:
        model = kwargs["model"] if "model" in kwargs else "gpt-5.5"
        timeout_seconds = kwargs["timeout_seconds"] if "timeout_seconds" in kwargs else 900
        heartbeat_interval_seconds = (
            kwargs["heartbeat_interval_seconds"] if "heartbeat_interval_seconds" in kwargs else 120
        )
        if timeout_seconds is None:
            timeout_seconds = 900
        if heartbeat_interval_seconds is None:
            heartbeat_interval_seconds = 120
        return {
            "instructions": str(kwargs.get("instructions") or ""),
            "model": str(model or ""),
            "reasoning_effort": str(kwargs.get("reasoning_effort") or ""),
            "full_auto": bool(kwargs.get("full_auto", False)),
            "cwd": kwargs.get("cwd"),
            "timeout_seconds": int(timeout_seconds),
            "heartbeat_callback": kwargs.get("heartbeat_callback"),
            "heartbeat_interval_seconds": int(heartbeat_interval_seconds),
        }

    async def invoke_model(self, prompt: str, **kwargs: Any) -> CodexExecResult:
        options = self.build_model_options(**kwargs)
        return await self._executor(prompt, **options)

    def normalize_stream_messages(self, raw_lines: list[str] | tuple[str, ...]) -> CodexExecResult:
        events, final_output, thread_id, input_tokens, output_tokens = _parse_events(list(raw_lines))
        combined = "\n".join([final_output, *[str(ev) for ev in events]])
        return CodexExecResult(
            exit_code=0,
            final_output=final_output,
            events=events,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            thread_id=thread_id,
            is_quota_exhausted=has_quota_text(combined),
        )

    async def collect_messages(self, stream: Any, **kwargs: Any) -> tuple[str, Any | None]:
        if isinstance(stream, (list, tuple)):
            result = self.normalize_stream_messages(stream)
            return result.final_output, {
                "events": result.events,
                "thread_id": result.thread_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "is_quota_exhausted": result.is_quota_exhausted,
            }
        text = str(stream or "")
        return text, None

    def probe_quota(self, *, five_hour_max: float = 95.0, seven_day_max: float = 95.0) -> BackendQuotaStatus:
        action, info, resets_at = self._quota_probe_fn(
            five_hour_max=five_hour_max,
            seven_day_max=seven_day_max,
        )
        return BackendQuotaStatus.from_probe(action, info, resets_at)


class CodexRunner(AbstractAgentRunner):
    """Default backend: preserve the legacy OpenAI/Codex flow."""

    name = "codex"

    async def run(self, args: argparse.Namespace, repo: Path) -> int:
        # Keep legacy behavior by delegating to the existing codex pipeline.
        # We accept `repo` for interface consistency; the legacy entrypoint resolves it again.
        from ..cycle import main_async as codex_main_async

        return await codex_main_async(args)
