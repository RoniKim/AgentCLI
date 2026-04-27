from __future__ import annotations

import asyncio
import unittest

from agent_runner.pipeline import PipelineManager, make_stages, parse_roles
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

    def test_coerce_roles_arg_uses_default_for_empty_values(self) -> None:
        self.assertEqual("PM,Dev,QA", coerce_roles_arg([]))
        self.assertEqual("PM,Dev,QA", coerce_roles_arg(""))
        self.assertEqual("PM,Dev,QA", coerce_roles_arg(None))

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
