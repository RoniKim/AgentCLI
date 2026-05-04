from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_runner.backends.base import BackendQuotaStatus
from agent_runner.backends.claudecode import (
    ClaudeCodeBackendAdapter,
    _load_claudecode_cfg,
)
from agent_runner.backends.codex_runner import CodexBackendAdapter
from agent_runner.codex_exec import CodexExecResult
from agent_runner.stop_progress import StopAwareSleepResult


@contextmanager
def _temp_path() -> Path:
    root = Path(__file__).resolve().parents[1] / ".tmp_backend_adapter_tests"
    root.mkdir(parents=True, exist_ok=True)
    temp_path = root / f"case_{uuid.uuid4().hex}"
    temp_path.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_path
    finally:
        shutil.rmtree(temp_path, ignore_errors=True)


def test_codex_adapter_passes_invocation_options() -> None:
    captured: dict[str, object] = {}

    async def fake_executor(prompt: str, **kwargs: object) -> CodexExecResult:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return CodexExecResult(exit_code=0, final_output="ok")

    def heartbeat() -> None:
        captured["heartbeat"] = True

    adapter = CodexBackendAdapter(executor=fake_executor)

    with _temp_path() as tmp_path:
        result = asyncio.run(
            adapter.invoke_model(
                "prompt text",
                instructions="stage rules",
                model="gpt-test",
                reasoning_effort="high",
                full_auto=True,
                cwd=tmp_path,
                timeout_seconds=17,
                heartbeat_callback=heartbeat,
                heartbeat_interval_seconds=9,
            )
        )

        assert result.final_output == "ok"
        assert captured["prompt"] == "prompt text"
        assert captured["kwargs"] == {
            "instructions": "stage rules",
            "model": "gpt-test",
            "reasoning_effort": "high",
            "full_auto": True,
            "cwd": tmp_path,
            "timeout_seconds": 17,
            "heartbeat_callback": heartbeat,
            "heartbeat_interval_seconds": 9,
        }


def test_codex_adapter_normalizes_jsonl_stream_messages() -> None:
    adapter = CodexBackendAdapter()
    raw_lines = [
        json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 5}}),
        json.dumps(
            {
                "type": "thread.completed",
                "final_output": "final response",
                "usage": {"input_tokens": 7, "output_tokens": 11},
            }
        ),
    ]

    text, structured = asyncio.run(adapter.collect_messages(raw_lines))

    assert text == "final response"
    assert structured == {
        "events": [
            {"type": "thread.started", "thread_id": "thread-1"},
            {"type": "turn.completed", "usage": {"input_tokens": 3, "output_tokens": 5}},
            {
                "type": "thread.completed",
                "final_output": "final response",
                "usage": {"input_tokens": 7, "output_tokens": 11},
            },
        ],
        "thread_id": "thread-1",
        "input_tokens": 7,
        "output_tokens": 11,
        "is_quota_exhausted": False,
    }


def test_claude_adapter_collect_messages_preserves_public_text_and_structured_values() -> None:
    class TextBlock:
        def __init__(self, text: str) -> None:
            self.text = text

    class AssistantMessage:
        def __init__(self, content: list[object]) -> None:
            self.content = content

    class ResultMessage:
        def __init__(self, *, result: dict[str, object], content: list[object]) -> None:
            self.result = result
            self.content = content

    class FakeStream:
        def __aiter__(self):
            async def _iterate():
                yield AssistantMessage([TextBlock("draft reply")])
                yield ResultMessage(
                    result={"session_id": "claude-session-1", "stop_reason": "end_turn"},
                    content=[TextBlock("final reply")],
                )

            return _iterate()

    cfg = _load_claudecode_cfg(argparse.Namespace())
    adapter = ClaudeCodeBackendAdapter(cfg)

    with _temp_path() as tmp_path:
        text, structured = asyncio.run(
            adapter.collect_messages(
                FakeStream(),
                stop_path=tmp_path / "STOP",
                debug=False,
            )
        )

        assert text == "draft reply\nfinal reply"
        assert structured == {"session_id": "claude-session-1", "stop_reason": "end_turn"}


def test_claude_adapter_builds_options_and_invokes_client() -> None:
    class FakeOptions:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeStream:
        async def __aiter__(self):
            yield SimpleNamespace(type="result", result="delegated output")

    clients: list[object] = []

    class FakeClient:
        def __init__(self, *, options: FakeOptions) -> None:
            self.options = options
            self.prompt = ""
            clients.append(self)

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        def query(self, prompt: str) -> None:
            self.prompt = prompt

        def receive_response(self) -> FakeStream:
            return FakeStream()

    with patch.dict(
        sys.modules,
        {"claude_agent_sdk": SimpleNamespace(ClaudeAgentOptions=FakeOptions)},
    ):
        cfg = _load_claudecode_cfg(argparse.Namespace())
        adapter = ClaudeCodeBackendAdapter(cfg, client_cls=FakeClient)

        with _temp_path() as tmp_path:
            text, structured = asyncio.run(
                adapter.invoke_model(
                    "hello claude",
                    repo=tmp_path,
                    stage="PM",
                    stop_path=tmp_path / "STOP",
                    debug=False,
                    model_override="claude-test",
                    stage_instructions="stage instructions",
                    max_turns_override=4,
                    timeout_seconds=5,
                    max_retries=0,
                )
            )

            assert text == "delegated output"
            assert structured is None
            assert len(clients) == 1
            client = clients[0]
            assert client.prompt == "hello claude"
            assert client.options.kwargs["model"] == "claude-test"
            assert client.options.kwargs["cwd"] == str(tmp_path)
            assert client.options.kwargs["max_turns"] == 4
            assert "stage instructions" in client.options.kwargs["system_prompt"]


def test_claude_adapter_retry_wait_uses_shared_stop_aware_sleep() -> None:
    class FakeOptions:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class FakeStream:
        async def __aiter__(self):
            yield SimpleNamespace(type="result", result="delegated output")

    attempts = {"count": 0}

    class FakeClient:
        def __init__(self, *, options: FakeOptions) -> None:
            self.options = options
            self.prompt = ""

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        def query(self, prompt: str) -> None:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("connection timed out")
            self.prompt = prompt

        def receive_response(self) -> FakeStream:
            return FakeStream()

    helper_calls: list[tuple[float, Path, tuple[Path, ...]]] = []

    async def fake_stop_aware_sleep(seconds: float, **kwargs: object) -> StopAwareSleepResult:
        helper_calls.append(
            (
                float(seconds),
                Path(str(kwargs["run_dir"])),
                tuple(Path(str(path)) for path in kwargs["stop_paths"]),
            )
        )
        return StopAwareSleepResult(status="timeout")

    async def fail_raw_sleep(delay: float, *args: object, **kwargs: object) -> None:
        raise AssertionError(f"raw asyncio.sleep should not handle retry wait: {delay}")

    with patch.dict(
        sys.modules,
        {"claude_agent_sdk": SimpleNamespace(ClaudeAgentOptions=FakeOptions)},
    ):
        cfg = _load_claudecode_cfg(argparse.Namespace())
        adapter = ClaudeCodeBackendAdapter(cfg, client_cls=FakeClient)

        with _temp_path() as tmp_path, patch(
            "agent_runner.backends.claudecode.stop_aware_sleep",
            side_effect=fake_stop_aware_sleep,
        ) as patched_sleep, patch(
            "agent_runner.backends.claudecode.asyncio.sleep",
            side_effect=fail_raw_sleep,
        ):
            text, structured = asyncio.run(
                adapter.invoke_model(
                    "hello claude",
                    repo=tmp_path,
                    stage="PM",
                    stop_path=tmp_path / "STOP",
                    debug=False,
                    timeout_seconds=5,
                    max_retries=1,
                    initial_backoff=7,
                )
            )

    assert text == "delegated output"
    assert structured is None
    assert patched_sleep.call_count == 1
    assert helper_calls == [(7.0, tmp_path, (tmp_path / "STOP",))]


def test_quota_probe_maps_availability() -> None:
    assert BackendQuotaStatus.from_probe("skip", {}, None).available is False
    assert BackendQuotaStatus.from_probe("ok", {}, None).available is True

    codex_adapter = CodexBackendAdapter(quota_probe_fn=lambda **_: ("wait", {"five_hour": 99.0}, 123))
    codex_status = codex_adapter.probe_quota(five_hour_max=95, seven_day_max=95)
    assert codex_status.available is True
    assert codex_status.as_tuple() == ("wait", {"five_hour": 99.0}, 123)

    cfg = _load_claudecode_cfg(argparse.Namespace())
    claude_adapter = ClaudeCodeBackendAdapter(
        cfg,
        quota_probe_fn=lambda **_: ("skip", {}, None),
    )
    claude_status = claude_adapter.probe_quota()
    assert claude_status.available is False
    assert claude_status.as_tuple() == ("skip", {}, None)
