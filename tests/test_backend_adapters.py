from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from agent_runner.backends.base import BackendQuotaStatus
from agent_runner.backends.claudecode import (
    ClaudeCodeBackendAdapter,
    _load_claudecode_cfg,
)
from agent_runner.backends.codex_runner import CodexBackendAdapter
from agent_runner.codex_exec import CodexExecResult


def test_codex_adapter_passes_invocation_options(tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    async def fake_executor(prompt: str, **kwargs: object) -> CodexExecResult:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return CodexExecResult(exit_code=0, final_output="ok")

    def heartbeat() -> None:
        captured["heartbeat"] = True

    adapter = CodexBackendAdapter(executor=fake_executor)

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
    assert structured is not None
    assert structured["thread_id"] == "thread-1"
    assert structured["input_tokens"] == 7
    assert structured["output_tokens"] == 11
    assert structured["is_quota_exhausted"] is False


def test_claude_adapter_builds_options_and_invokes_client(tmp_path: Path, monkeypatch) -> None:
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

    monkeypatch.setitem(
        sys.modules,
        "claude_agent_sdk",
        SimpleNamespace(ClaudeAgentOptions=FakeOptions),
    )
    cfg = _load_claudecode_cfg(argparse.Namespace())
    adapter = ClaudeCodeBackendAdapter(cfg, client_cls=FakeClient)

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
    assert claude_adapter.probe_quota().available is False
