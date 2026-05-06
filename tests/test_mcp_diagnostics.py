from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from agent_runner.mcp_diagnostics import build_mcp_diagnostics, format_mcp_diagnostics_lines
from agent_runner.shell import RunnerShell
from agent_runner.web_config import _build_config_contract, _config_save_validate_change

ROOT = Path(__file__).resolve().parents[1]


class McpDiagnosticsTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self._tmp = scratch / f"mcp-diagnostics-{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "run-1"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def test_available_launcher_reports_selected_mode_timeout_and_nonblocking_fallback(self) -> None:
        diagnostics = build_mcp_diagnostics(
            {
                "mcp_mode": "npx",
                "mcp_timeout_seconds": 17,
                "codex_package": "@openai/codex@test",
            },
            tool_lookup=lambda name: f"/bin/{name}",
        )

        self.assertEqual("ok", diagnostics["status"])
        self.assertEqual("npx", diagnostics["selected_mode"])
        self.assertEqual("npx", diagnostics["effective_mode"])
        self.assertEqual(17, diagnostics["timeout_seconds"])
        self.assertEqual("@openai/codex@test", diagnostics["codex_package"])
        self.assertEqual(["npx"], diagnostics["required_tools"])
        self.assertEqual([], diagnostics["unavailable_tools"])
        self.assertFalse(diagnostics["safe_fallback"]["active"])
        self.assertFalse(diagnostics["safe_fallback"]["blocking"])
        self.assertFalse(diagnostics["non_mcp_runs_blocked"])

    def test_missing_launcher_warns_but_keeps_non_mcp_runs_unblocked(self) -> None:
        diagnostics = build_mcp_diagnostics(
            {"mcp_mode": "npx", "mcp_timeout_seconds": 12},
            tool_lookup=lambda _name: None,
        )

        self.assertEqual("warning", diagnostics["status"])
        self.assertEqual(["npx"], diagnostics["unavailable_tools"])
        self.assertTrue(diagnostics["safe_fallback"]["active"])
        self.assertEqual("tool_unavailable", diagnostics["safe_fallback"]["reason"])
        self.assertFalse(diagnostics["safe_fallback"]["non_mcp_runs_blocked"])
        self.assertIn("mcp_tool_unavailable", {item["code"] for item in diagnostics["warnings"]})

    def test_disabled_invalid_mode_and_invalid_timeout_are_safe_fallbacks(self) -> None:
        disabled = build_mcp_diagnostics({"mcp_mode": "disabled"}, tool_lookup=lambda _name: None)
        self.assertEqual("ok", disabled["status"])
        self.assertEqual([], disabled["required_tools"])
        self.assertEqual("mcp_disabled", disabled["safe_fallback"]["reason"])
        self.assertFalse(disabled["safe_fallback"]["blocking"])

        invalid_mode = build_mcp_diagnostics({"mcp_mode": "bogus"}, tool_lookup=lambda _name: "/bin/tool")
        self.assertEqual("warning", invalid_mode["status"])
        self.assertEqual("disabled", invalid_mode["effective_mode"])
        self.assertEqual("invalid_mode", invalid_mode["safe_fallback"]["reason"])
        self.assertIn("mcp_invalid_mode", {item["code"] for item in invalid_mode["warnings"]})

        invalid_timeout = build_mcp_diagnostics(
            {"mcp_mode": "codex", "mcp_timeout_seconds": "forever"},
            tool_lookup=lambda name: f"/bin/{name}",
        )
        self.assertEqual("warning", invalid_timeout["status"])
        self.assertEqual(120, invalid_timeout["timeout_seconds"])
        self.assertEqual("invalid_timeout", invalid_timeout["safe_fallback"]["reason"])
        self.assertFalse(invalid_timeout["safe_fallback"]["blocking"])

        lines = format_mcp_diagnostics_lines(invalid_timeout, indent="  ")
        self.assertTrue(any("timeout: 120s" in line for line in lines))
        self.assertTrue(any("non_mcp_runs_blocked=False" in line for line in lines))

    def test_web_config_contract_exposes_and_validates_mcp_fields(self) -> None:
        contract = _build_config_contract(
            self.repo,
            {
                "mcp_mode": "codex",
                "mcp_timeout_seconds": 9,
                "codex_package": "@openai/codex@test",
            },
            self.repo / ".AgentCLI" / "agent_config.json",
            "test",
            self.repo / ".AgentCLI" / "agent_prompts",
            save_enabled=True,
        )

        group = next(item for item in contract["groups"] if item["id"] == "mcp")
        self.assertEqual(["mcp_mode", "mcp_timeout_seconds", "codex_package"], group["paths"])
        self.assertEqual("enum", contract["schema"]["mcp_mode"]["kind"])
        self.assertEqual(["npx", "codex", "disabled"], contract["schema"]["mcp_mode"]["options"])
        self.assertEqual(0, contract["schema"]["mcp_timeout_seconds"]["min"])
        self.assertEqual("codex", contract["values"]["mcp_mode"])
        self.assertEqual(9, contract["values"]["mcp_timeout_seconds"])
        self.assertEqual("@openai/codex@test", contract["values"]["codex_package"])

        invalid_mode, mode_code, _ = _config_save_validate_change(
            "mcp_mode",
            "bogus",
            contract["schema"]["mcp_mode"],
            "npx",
        )
        self.assertEqual("bogus", invalid_mode)
        self.assertEqual("config_value_invalid_choice", mode_code)

        invalid_timeout, timeout_code, _ = _config_save_validate_change(
            "mcp_timeout_seconds",
            -1,
            contract["schema"]["mcp_timeout_seconds"],
            120,
        )
        self.assertEqual(-1, invalid_timeout)
        self.assertEqual("config_value_out_of_range", timeout_code)

    def test_shell_status_and_doctor_surface_mcp_diagnostics(self) -> None:
        shell = RunnerShell(["--repo", self.repo.as_posix()])
        shell.run_dir = self.run_dir
        shell.overrides["mcp_mode"] = "npx"
        shell.overrides["mcp_timeout_seconds"] = 13

        with patch("agent_runner.mcp_diagnostics.shutil.which", return_value=None):
            status_buffer = StringIO()
            with redirect_stdout(status_buffer):
                shell.status()
            status_output = status_buffer.getvalue()

            doctor_buffer = StringIO()
            with redirect_stdout(doctor_buffer):
                shell.doctor()
            doctor_output = doctor_buffer.getvalue()

        self.assertIn("mcp_diagnostics: status=warning mode=npx->npx timeout=13s unavailable=npx", status_output)
        self.assertIn("non_mcp_blocking=False", status_output)
        self.assertIn("- mcp diagnostics:", doctor_output)
        self.assertIn("unavailable tools: npx", doctor_output)
        self.assertIn("safe fallback: active=True blocking=False", doctor_output)
        self.assertTrue((self.run_dir / "DOCTOR.md").exists())

    def test_web_snapshot_includes_mcp_diagnostics_and_section_state(self) -> None:
        from agent_runner.web import build_snapshot
        from agent_runner.web_payloads import status_snapshot_for_scope

        config_path = self.repo / ".AgentCLI" / "agent_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            json.dumps({"mcp_mode": "npx", "mcp_timeout_seconds": 120}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        with patch("agent_runner.mcp_diagnostics.shutil.which", return_value=None):
            snapshot = build_snapshot(self.repo, config_path=config_path.as_posix(), runner_controller_auto_build=False)

        diagnostics = snapshot["mcp_diagnostics"]
        self.assertEqual("warning", diagnostics["status"])
        self.assertEqual(["npx"], diagnostics["unavailable_tools"])
        self.assertFalse(diagnostics["safe_fallback"]["blocking"])
        self.assertEqual("partial", snapshot["sectionState"]["mcp"]["status"])
        self.assertIn("MCP diagnostics", snapshot["sectionState"]["mcp"]["message"])
        self.assertIs(snapshot["mcpDiagnostics"], diagnostics)

        compact = status_snapshot_for_scope(snapshot, scope="dashboard")
        self.assertEqual(diagnostics, compact["mcp_diagnostics"])
        self.assertEqual(diagnostics, compact["mcpDiagnostics"])


if __name__ == "__main__":
    unittest.main()
