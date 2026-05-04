from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
import unittest
import types
from uuid import uuid4

from agent_runner import web as web_module
from agent_runner.config import builtin_roles, normalize_roles_value, validate_roles_value
from agent_runner.pipeline import PipelineManager, make_stages, parse_roles
from agent_runner.pipeline.stages.base import Stage, StageOutcome
from agent_runner.runtime_contract import BUILTIN_ROLE_SPECS, CODEX_MODEL_DEFAULTS, DEFAULT_ROLE_SPECS, PIPELINE_ROLE_FIELD_SPEC
from agent_runner.shared import coerce_roles_arg
from agent_runner.web_config import _build_config_contract, _config_save_changes, _normalize_config_for_launch
from agent_runner.web_payloads import build_stage_payload


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

    def test_web_config_roles_round_trip_plugin_specs_through_save_and_launch_normalization(self) -> None:
        repo_root = Path.cwd() / ".tmp" / f"agentcli-pipeline-roles-{uuid4().hex}"
        repo_root.mkdir(parents=True, exist_ok=True)
        cfg_path = repo_root / "agentcli.json"
        cfg_path.write_text(
            json.dumps({"repo": repo_root.as_posix(), "roles": "PM,Dev,QA"}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        save_result, save_error = _config_save_changes(
            cfg_path,
            [{"path": "roles", "value": ["PM", "pkg.mod:Class", "QA"]}],
            schema={"roles": dict(PIPELINE_ROLE_FIELD_SPEC)},
            restart_required_paths=[],
        )

        self.assertIsNone(save_error)
        self.assertIsNotNone(save_result)

        saved_raw = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], saved_raw["roles"])
        self.assertEqual("PM,pkg.mod:Class,QA", _normalize_config_for_launch(saved_raw)["roles"])

        contract = _build_config_contract(
            repo_root,
            saved_raw,
            cfg_path,
            "unit-test",
            repo_root / "prompts",
        )
        self.assertEqual(["PM", "pkg.mod:Class", "QA"], contract["values"]["roles"])

    def test_build_stage_payload_preserves_pl_and_plugin_stage_records(self) -> None:
        stages = build_stage_payload(
            web_module,
            Path.cwd(),
            {"status": "done"},
            {},
            {},
            run_summary={
                "cycles": [
                    {
                        "cycle": 2,
                        "stages": [
                            {"name": "PM", "status": "ok", "rc": 0, "reason": "pm_ready", "cycle": 2},
                            {"name": "PL", "status": "ok", "rc": 0, "reason": "backlog_refined", "cycle": 2},
                            {"name": "pkg.mod:Class", "status": "ok", "rc": 0, "reason": "plugin_ok", "cycle": 2},
                            {"name": "QA", "status": "ok", "rc": 0, "reason": "qa_verified", "cycle": 2},
                        ],
                    }
                ]
            },
            events=[],
        )

        self.assertEqual(["PM", "PL", "pkg.mod:Class", "QA"], [stage["id"] for stage in stages])
        self.assertEqual(["PM", "PL", "pkg.mod:Class", "QA"], [stage["label"] for stage in stages])
        self.assertEqual("Backlog refinement", stages[1]["title"])
        self.assertEqual("pkg.mod:Class", stages[2]["title"])
        self.assertEqual("done", stages[2]["status"])

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
