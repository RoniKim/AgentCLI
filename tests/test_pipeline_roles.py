from __future__ import annotations

import asyncio
import sys
import unittest
import types

from agent_runner.config import builtin_roles, normalize_roles_value, validate_roles_value
from agent_runner.pipeline import PipelineManager, make_stages, parse_roles
from agent_runner.pipeline.stages.base import Stage, StageOutcome
from agent_runner.runtime_contract import BUILTIN_ROLE_SPECS, CODEX_MODEL_DEFAULTS, DEFAULT_ROLE_SPECS
from agent_runner.shared import coerce_roles_arg


class PipelineRolesTests(unittest.TestCase):
    def test_coerce_roles_arg_normalizes_web_list_values(self) -> None:
        roles_raw = coerce_roles_arg(["PM", "Security", "Dev", "QA"])

        self.assertEqual("PM,Security,Dev,QA", roles_raw)
        self.assertEqual(["PM", "Security", "Dev", "QA"], parse_roles(roles_raw))
        self.assertEqual(
            ["PM", "Security", "Dev", "QA"],
            [stage.name for stage in make_stages(roles_raw, plugins_enabled=False, plugins_allowlist=[], plugins_strict=True)],
        )

    def test_roles_preserve_plugin_specs_across_string_and_array_inputs(self) -> None:
        roles_string = "PM, pkg.mod:Class, QA"
        roles_list = ["PM", "pkg.mod:Class", "QA"]

        self.assertEqual(["PM", "pkg.mod:Class", "QA"], parse_roles(roles_string))
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], parse_roles(roles_list))
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], normalize_roles_value(roles_string))
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], normalize_roles_value(roles_list))

        items, invalid = validate_roles_value(roles_string)
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], items)
        self.assertEqual([], invalid)

        malformed_items, malformed_invalid = validate_roles_value(["PM", "bad role", "QA"])
        self.assertEqual(["PM", "bad role", "QA"], malformed_items)
        self.assertEqual(["bad role"], malformed_invalid)

    def test_make_stages_launches_allowed_plugin_stage_in_order(self) -> None:
        module_name = "fake_role_plugin"
        module = types.ModuleType(module_name)

        class PluginStage(Stage):
            name = "pkg.mod:Class"

            async def run(self, session, cycle_idx):  # type: ignore[override]
                return StageOutcome.ok()

        module.PluginStage = PluginStage
        sys.modules[module_name] = module
        self.addCleanup(lambda: sys.modules.pop(module_name, None))

        raw_roles = coerce_roles_arg(["PM", f"{module_name}:PluginStage", "QA"])
        self.assertEqual(["PM", f"{module_name}:PluginStage", "QA"], parse_roles(raw_roles))

        stages = make_stages(
            raw_roles,
            plugins_enabled=True,
            plugins_allowlist=[module_name],
            plugins_strict=True,
        )

        self.assertEqual(["PM", "pkg.mod:Class", "QA"], [stage.name for stage in stages])

    def test_coerce_roles_arg_uses_default_for_empty_values(self) -> None:
        default_role_string = ",".join(DEFAULT_ROLE_SPECS)
        self.assertEqual(default_role_string, coerce_roles_arg([]))
        self.assertEqual(default_role_string, coerce_roles_arg(""))
        self.assertEqual(default_role_string, coerce_roles_arg(None))

    def test_builtin_role_choices_and_defaults_are_shared(self) -> None:
        self.assertEqual(list(BUILTIN_ROLE_SPECS), builtin_roles())
        self.assertEqual(list(DEFAULT_ROLE_SPECS), parse_roles(None))
        self.assertEqual(",".join(DEFAULT_ROLE_SPECS), coerce_roles_arg(None))
        self.assertEqual("gpt-5.5", CODEX_MODEL_DEFAULTS["pm_model"])
        self.assertEqual("gpt-5.4-mini", CODEX_MODEL_DEFAULTS["dev_model"])
        self.assertEqual("gpt-5.4", CODEX_MODEL_DEFAULTS["dev_model_tier1"])
        self.assertEqual("gpt-5.5", CODEX_MODEL_DEFAULTS["dev_model_tier2"])
        self.assertEqual("gpt-5.5", CODEX_MODEL_DEFAULTS["qa_model"])
        self.assertEqual("gpt-5.4-mini", CODEX_MODEL_DEFAULTS["reporter_model"])

    def test_make_stages_includes_builtin_pl_between_pm_and_dev(self) -> None:
        stages = make_stages("PM,PL,Dev,QA", plugins_enabled=False, plugins_allowlist=[], plugins_strict=True)

        self.assertEqual(["PM", "PL", "Dev", "QA"], [stage.name for stage in stages])

    def test_empty_pipeline_stage_list_fails_instead_of_succeeding(self) -> None:
        class Session:
            done_delta = 0

            def has_stop(self) -> bool:
                return False

        result = asyncio.run(PipelineManager([]).run_cycle(Session(), 0, continuous=True))

        self.assertEqual(1, result.rc)
        self.assertEqual("no_stages_configured", result.reason)
        self.assertEqual([], result.stages)


if __name__ == "__main__":
    unittest.main()
