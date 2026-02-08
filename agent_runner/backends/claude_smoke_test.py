from __future__ import annotations

import argparse
import asyncio
from typing import Any


def _print_help_on_failure() -> None:
    print("\n[Claude SDK Smoke Test] 실패 시 점검 사항:")
    print("- claude-agent-sdk 설치: pip install -U claude-agent-sdk")
    print("- Claude Code 인증 또는 ANTHROPIC_API_KEY 설정")
    print("- Claude Code CLI가 설치되어 있다면 `claude auth login` 상태 확인")


async def _run(prompt: str, model: str) -> int:
    try:
        import claude_agent_sdk
        from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient
    except Exception as ex:  # pragma: no cover - import guard
        print(f"[error] claude-agent-sdk import 실패: {ex}")
        _print_help_on_failure()
        return 2

    version = getattr(claude_agent_sdk, "__version__", "unknown")
    print(f"[info] claude-agent-sdk version: {version}")

    options = ClaudeAgentOptions(
        model=model,
        permission_mode="acceptEdits",
        max_turns=1,
    )

    try:
        from .claudecode import _extract_client_pid
        from ..process_guard import register_pid, unregister_pid
    except Exception:
        _extract_client_pid = None  # type: ignore[assignment]
        register_pid = unregister_pid = None  # type: ignore[assignment]

    try:
        async with ClaudeSDKClient(options=options) as client:
            child_pid = _extract_client_pid(client) if _extract_client_pid else None
            if child_pid is not None and register_pid is not None:
                register_pid(child_pid)
            try:
                try:
                    result = client.query(prompt)
                except TypeError:
                    result = client.query(prompt=prompt)
                if asyncio.iscoroutine(result):
                    await result

                if hasattr(client, "receive_response"):
                    stream = client.receive_response()
                    if asyncio.iscoroutine(stream):
                        stream = await stream
                elif hasattr(client, "receive_messages"):
                    stream = client.receive_messages()
                    if asyncio.iscoroutine(stream):
                        stream = await stream
                else:
                    print("[error] ClaudeSDKClient receive_* API를 찾지 못했습니다.")
                    _print_help_on_failure()
                    return 2

                async for msg in stream:  # type: ignore[assignment]
                    name = msg.__class__.__name__
                    msg_type = getattr(msg, "type", None)
                    if name in {"AssistantMessage", "TextMessage"} or msg_type == "assistant":
                        content = getattr(msg, "content", None)
                        if isinstance(content, list):
                            texts = [getattr(b, "text", "") for b in content if getattr(b, "text", "").strip()]
                            if texts:
                                print("[assistant]", " ".join(texts)[:200])
                    if name in {"ResultMessage", "ResponseMessage"} or msg_type == "result":
                        result = getattr(msg, "result", None)
                        if isinstance(result, dict):
                            print("[result] structured keys:", ", ".join(result.keys())[:200])
                        elif isinstance(result, str) and result.strip():
                            print("[result]", result[:200])
                return 0
            finally:
                if child_pid is not None and unregister_pid is not None:
                    unregister_pid(child_pid)
    except Exception as ex:
        print(f"[error] Claude SDK 통신 실패: {ex}")
        _print_help_on_failure()
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude Agent SDK smoke test")
    parser.add_argument("--prompt", default="Hello from AgentCLI smoke test.")
    parser.add_argument("--model", default="sonnet")
    args = parser.parse_args()
    return asyncio.run(_run(args.prompt, args.model))


if __name__ == "__main__":
    raise SystemExit(main())
