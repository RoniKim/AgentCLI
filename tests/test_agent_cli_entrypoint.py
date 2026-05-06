from __future__ import annotations

import sys
import types

import agent_cli


def test_agent_cli_web_flag_dispatches_to_web_main(monkeypatch) -> None:
    calls: list[list[str]] = []
    fake_web = types.ModuleType("agent_runner.web")

    def fake_main(argv: list[str] | None = None) -> int:
        calls.append(list(argv or []))
        return 17

    fake_web.main = fake_main
    monkeypatch.setitem(sys.modules, "agent_runner.web", fake_web)

    rc = agent_cli.main(["--web", "--repo", ".", "--port", "8123"])

    assert rc == 17
    assert calls == [["--repo", ".", "--port", "8123"]]


def test_agent_cli_serve_web_alias_dispatches_to_web_main(monkeypatch) -> None:
    calls: list[list[str]] = []
    fake_web = types.ModuleType("agent_runner.web")

    def fake_main(argv: list[str] | None = None) -> int:
        calls.append(list(argv or []))
        return 0

    fake_web.main = fake_main
    monkeypatch.setitem(sys.modules, "agent_runner.web", fake_web)

    rc = agent_cli.main(["--serve-web", "--host", "127.0.0.1"])

    assert rc == 0
    assert calls == [["--host", "127.0.0.1"]]
