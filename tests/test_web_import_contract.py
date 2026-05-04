from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PUBLIC_IMPORT_NAMES = (
    "create_app",
    "build_snapshot",
)

PRIVATE_HELPER_IMPORT_NAMES = (
    "_load_backlog_payload",
    "_build_live_state_payload",
    "_redact_web_log_payload",
    "_redact_config",
    "_goal_save_serialize_draft",
    "_goal_save_has_required_sections",
    "_parse_goal_items_and_warnings",
    "_build_goals_payload",
)

PRIVATE_CONSTANT_IMPORT_NAMES = (
    "GOALS_SAVE_CONFIRMATION_PHRASE",
)

ALL_LOCKED_IMPORT_NAMES = PUBLIC_IMPORT_NAMES + PRIVATE_HELPER_IMPORT_NAMES + PRIVATE_CONSTANT_IMPORT_NAMES

EXPECTED_CONSTANT_VALUES = {
    "GOALS_SAVE_CONFIRMATION_PHRASE": "DELETE OR DOWNGRADE UNMET P0 GOALS",
}


class WebImportContractTests(unittest.TestCase):
    def _exec_import(self, statement: str, *, description: str) -> dict[str, object]:
        namespace: dict[str, object] = {}
        try:
            exec(statement, namespace)
        except Exception as exc:  # pragma: no cover - exercised when the contract breaks
            self.fail(f"{description} failed: {exc}")
        return namespace

    def test_importing_agent_runner_web_module_exposes_locked_contract(self) -> None:
        module_name = "agent_runner.web"

        web_module = importlib.import_module(module_name)

        missing = [name for name in ALL_LOCKED_IMPORT_NAMES if not hasattr(web_module, name)]
        self.assertEqual(
            [],
            missing,
            f"{module_name} is missing locked import-facade names: {', '.join(missing)}",
        )
        self.assertEqual(module_name, web_module.__name__)

    def test_direct_public_imports_remain_available(self) -> None:
        import_statement = "from agent_runner.web import create_app, build_snapshot"
        web_module = importlib.import_module("agent_runner.web")

        namespace = self._exec_import(
            import_statement,
            description="direct public import from agent_runner.web",
        )

        for name in PUBLIC_IMPORT_NAMES:
            self.assertIn(name, namespace, f"{name} was not bound by `{import_statement}`")
            self.assertIs(
                namespace[name],
                getattr(web_module, name),
                f"{name} no longer resolves to agent_runner.web.{name}",
            )
            self.assertTrue(callable(namespace[name]), f"{name} must remain callable")

    def test_direct_private_helpers_and_constant_remain_available_for_tests(self) -> None:
        import_names = ", ".join(PRIVATE_HELPER_IMPORT_NAMES + PRIVATE_CONSTANT_IMPORT_NAMES)
        import_statement = f"from agent_runner.web import {import_names}"
        web_module = importlib.import_module("agent_runner.web")

        namespace = self._exec_import(
            import_statement,
            description="direct private helper import from agent_runner.web",
        )

        for name in PRIVATE_HELPER_IMPORT_NAMES:
            self.assertIn(name, namespace, f"{name} was not bound by `{import_statement}`")
            self.assertIs(
                namespace[name],
                getattr(web_module, name),
                f"{name} no longer resolves to agent_runner.web.{name}",
            )
            self.assertTrue(callable(namespace[name]), f"{name} must remain callable for existing tests")

        for name, expected_value in EXPECTED_CONSTANT_VALUES.items():
            self.assertIn(name, namespace, f"{name} was not bound by `{import_statement}`")
            self.assertEqual(expected_value, namespace[name], f"{name} changed unexpectedly")
            self.assertEqual(expected_value, getattr(web_module, name), f"agent_runner.web.{name} changed unexpectedly")
