from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_runner.backends.claude_extensions import (
    ClaudeExtensionContext,
    build_claude_advanced_diagnostics,
    format_claude_advanced_diagnostics_lines,
    validate_claude_advanced_config,
)
from agent_runner.backends.claudecode import _build_options, _load_claudecode_cfg
from agent_runner.web_config import _build_config_contract, _config_save_validate_change

ROOT = Path(__file__).resolve().parents[1]


class FakeMetrics:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def event(self, name: str, **kwargs: object) -> None:
        self.events.append((name, kwargs))


class FakeLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def info(self, message: str, **kwargs: object) -> None:
        self.records.append(("info", message))

    def error(self, message: str, **kwargs: object) -> None:
        self.records.append(("error", message))


class FakeOptions:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakePermissionAllow:
    allowed = True

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class FakePermissionDeny:
    allowed = False

    def __init__(self, message: str) -> None:
        self.message = message


class FakeHookMatcher:
    def __init__(self, matcher: str | None = None, hooks: list[object] | None = None) -> None:
        self.matcher = matcher
        self.hooks = hooks or []


class FakeAgentDefinition:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


def _fake_tool(name: str, description: str, params: dict[str, object]):
    def _decorate(func):
        func.tool_name = name
        func.description = description
        func.params = params
        return func

    return _decorate


def _fake_mcp_server(*, name: str, version: str, tools: list[object]) -> dict[str, object]:
    return {
        "name": name,
        "version": version,
        "tools": [getattr(tool, "tool_name", "") for tool in tools],
    }


def _fake_sdk() -> SimpleNamespace:
    return SimpleNamespace(
        __version__="test-sdk",
        ClaudeAgentOptions=FakeOptions,
        PermissionResultAllow=FakePermissionAllow,
        PermissionResultDeny=FakePermissionDeny,
        HookMatcher=FakeHookMatcher,
        AgentDefinition=FakeAgentDefinition,
        tool=_fake_tool,
        create_sdk_mcp_server=_fake_mcp_server,
    )


class ClaudeAdvancedControlsTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self._tmp = scratch / f"claude-advanced-{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "run-1"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def _advanced_args(self, **overrides: object) -> argparse.Namespace:
        values: dict[str, object] = {
            "claudecode_model": "sonnet",
            "claudecode_permission_mode": "acceptEdits",
            "claudecode_max_turns": 8,
            "claudecode_setting_sources": "project",
            "claudecode_dev_allowed_tools": "Read,Write,Edit,Grep,Glob,Bash",
            "claudecode_qa_allowed_tools": "Read,Grep,Glob,Bash",
            "claudecode_mcp_tools_enabled": True,
            "claudecode_hooks_enabled": True,
            "claudecode_can_use_tool_enabled": True,
            "claudecode_can_use_tool_strict_isolation": True,
            "claudecode_subagents_enabled": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_validation_and_diagnostics_cover_advanced_modes(self) -> None:
        invalid = validate_claude_advanced_config(
            {
                "claudecode_permission_mode": "root",
                "claudecode_max_turns": 0,
                "claudecode_setting_sources": ["project", "remote"],
            }
        )
        self.assertEqual("error", invalid["status"])
        self.assertFalse(invalid["valid"])
        self.assertIn("claude_invalid_permission_mode", {issue["code"] for issue in invalid["errors"]})

        strict_without_permission = build_claude_advanced_diagnostics(
            {
                "claudecode_can_use_tool_enabled": False,
                "claudecode_can_use_tool_strict_isolation": True,
            },
            options_cls=FakeOptions,
        )
        self.assertEqual("warning", strict_without_permission["status"])
        self.assertIn(
            "claude_strict_isolation_not_enforced",
            {issue["code"] for issue in strict_without_permission["warnings"]},
        )

        with patch.dict(sys.modules, {"claude_agent_sdk": _fake_sdk()}):
            diagnostics = build_claude_advanced_diagnostics(self._advanced_args())
        self.assertEqual("ok", diagnostics["status"])
        self.assertTrue(diagnostics["sdk"]["available"])
        self.assertTrue(diagnostics["features"]["mcp_tools"]["sdkSupported"])
        self.assertTrue(diagnostics["features"]["hooks"]["sdkSupported"])
        self.assertTrue(diagnostics["features"]["can_use_tool"]["sdkSupported"])
        self.assertTrue(diagnostics["features"]["strict_isolation"]["enforced"])
        self.assertTrue(diagnostics["features"]["subagents"]["sdkSupported"])

        lines = format_claude_advanced_diagnostics_lines(diagnostics, indent="  ")
        self.assertTrue(any("mcp_tools: enabled=True" in line for line in lines))
        self.assertTrue(any("subagents: enabled=True" in line for line in lines))

    def test_build_options_applies_mcp_hooks_dynamic_permission_and_subagents(self) -> None:
        cfg = _load_claudecode_cfg(self._advanced_args())
        metrics = FakeMetrics()
        ext_ctx = ClaudeExtensionContext(
            repo=self.repo,
            run_dir=self.run_dir,
            stop_path=self.run_dir / "STOP",
            logger=FakeLogger(),
            metrics=metrics,
            args=self._advanced_args(),
            debug=True,
            policy_rules=[],
            current_stage="Dev",
            current_task_id="T1",
            current_task_files=["allowed.py"],
        )

        with patch.dict(sys.modules, {"claude_agent_sdk": _fake_sdk()}):
            options = _build_options(cfg, repo=self.repo, stage="Dev", ext_ctx=ext_ctx)

        kwargs = options.kwargs
        self.assertIn("mcp_servers", kwargs)
        self.assertEqual("agentcli", kwargs["mcp_servers"]["agentcli"]["name"])
        self.assertIn("mcp__agentcli__check_state", kwargs["allowed_tools"])
        self.assertIn("Task", kwargs["allowed_tools"])
        self.assertIn("hooks", kwargs)
        self.assertIn("can_use_tool", kwargs)
        self.assertIn("agents", kwargs)
        self.assertEqual({"code-reviewer", "test-runner", "security-auditor"}, set(kwargs["agents"]))
        self.assertIn("AgentCLI pipeline tools", kwargs["system_prompt"])

        can_use_tool = kwargs["can_use_tool"]
        ext_ctx.current_stage = "QA"
        qa_denied = asyncio.run(can_use_tool("Write", {"file_path": (self.repo / "qa.py").as_posix()}, None))
        self.assertFalse(qa_denied.allowed)
        self.assertIn("QA stage is read-only", qa_denied.message)

        ext_ctx.current_stage = "Dev"
        outside_denied = asyncio.run(can_use_tool("Write", {"file_path": (self._tmp / "outside.py").as_posix()}, None))
        self.assertFalse(outside_denied.allowed)
        self.assertIn("outside repository", outside_denied.message)

        sensitive_denied = asyncio.run(can_use_tool("Write", {"file_path": (self.repo / ".env").as_posix()}, None))
        self.assertFalse(sensitive_denied.allowed)
        self.assertIn("sensitive file", sensitive_denied.message)

        state_denied = asyncio.run(can_use_tool("Write", {"file_path": (self.repo / "STATE.json").as_posix()}, None))
        self.assertFalse(state_denied.allowed)
        self.assertIn("pipeline state file", state_denied.message)

        strict_allowed = asyncio.run(can_use_tool("Write", {"file_path": (self.repo / "other.py").as_posix()}, None))
        self.assertTrue(strict_allowed.allowed)
        self.assertIn("outside_task_scope", {event[1].get("reason") for event in metrics.events})

    def test_web_config_contract_exposes_and_validates_claude_advanced_fields(self) -> None:
        contract = _build_config_contract(
            self.repo,
            {
                "execution_backend": "claudecode",
                "claudecode_mcp_tools_enabled": True,
                "claudecode_hooks_enabled": True,
                "claudecode_can_use_tool_enabled": True,
                "claudecode_can_use_tool_strict_isolation": True,
                "claudecode_subagents_enabled": True,
            },
            self.repo / ".AgentCLI" / "agent_config.json",
            "test",
            self.repo / ".AgentCLI" / "agent_prompts",
            save_enabled=True,
        )
        group = next(item for item in contract["groups"] if item["id"] == "claude")
        self.assertIn("claudecode_mcp_tools_enabled", group["paths"])
        self.assertIn("claudecode_subagents_enabled", group["paths"])
        self.assertEqual("enum", contract["schema"]["claudecode_permission_mode"]["kind"])
        self.assertEqual(["default", "acceptEdits", "bypassPermissions", "plan"], contract["schema"]["claudecode_permission_mode"]["options"])
        self.assertEqual("multienum", contract["schema"]["claudecode_setting_sources"]["kind"])
        self.assertEqual(["project"], contract["values"]["claudecode_setting_sources"])
        self.assertTrue(contract["values"]["claudecode_mcp_tools_enabled"])
        self.assertTrue(contract["values"]["claudecode_can_use_tool_strict_isolation"])

        invalid_mode, mode_code, _ = _config_save_validate_change(
            "claudecode_permission_mode",
            "root",
            contract["schema"]["claudecode_permission_mode"],
            "acceptEdits",
        )
        self.assertEqual("root", invalid_mode)
        self.assertEqual("config_value_invalid_choice", mode_code)

        invalid_turns, turns_code, _ = _config_save_validate_change(
            "claudecode_max_turns",
            0,
            contract["schema"]["claudecode_max_turns"],
            8,
        )
        self.assertEqual(0, invalid_turns)
        self.assertEqual("config_value_out_of_range", turns_code)


if __name__ == "__main__":
    unittest.main()
