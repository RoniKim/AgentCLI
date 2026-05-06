from __future__ import annotations

import argparse
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.cli import DEFAULTS, _merge_effective
from agent_runner.runtime_contract import ENTERPRISE_BUDGET_FLOORS, enterprise_role_string
from agent_runner.web_config import _build_config_contract, _normalize_config_for_launch

ROOT = Path(__file__).resolve().parents[1]


class EnterpriseProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self._tmp = scratch / f"enterprise-profile-{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.config_path = self.repo / ".AgentCLI" / "agent_config.json"
        self.prompts_dir = self.repo / ".AgentCLI" / "agent_prompts"

    def test_enterprise_profile_inserts_security_enables_scans_and_enforces_budget_floors(self) -> None:
        effective = _merge_effective(
            DEFAULTS,
            {
                "profile": "enterprise",
                "policy": {"enabled": False},
                "security": {"enabled": False},
                "budgets": {
                    "max_total_escalations_per_run": 0,
                    "max_total_continuations_per_run": 1,
                    "max_total_repair_attempts_per_run": 2,
                },
            },
            argparse.Namespace(),
        )

        self.assertEqual("enterprise", effective["profile"])
        self.assertEqual(enterprise_role_string(), effective["roles"])
        self.assertTrue(effective["policy"]["enabled"])
        self.assertFalse(effective["no_policy_scan"])
        self.assertTrue(effective["security"]["enabled"])
        for key, floor in ENTERPRISE_BUDGET_FLOORS.items():
            self.assertGreaterEqual(effective["budgets"][key], floor)

    def test_enterprise_profile_preserves_configured_roles_but_keeps_guardrails(self) -> None:
        effective = _merge_effective(
            DEFAULTS,
            {
                "profile": "enterprise",
                "roles": "PM,Dev,QA",
                "policy": {"enabled": False},
                "security": {"enabled": False},
                "budgets": {
                    "max_total_escalations_per_run": 0,
                    "max_total_continuations_per_run": 0,
                    "max_total_repair_attempts_per_run": 0,
                },
            },
            argparse.Namespace(),
        )

        self.assertEqual("PM,Dev,QA", effective["roles"])
        self.assertTrue(effective["policy"]["enabled"])
        self.assertTrue(effective["security"]["enabled"])
        for key, floor in ENTERPRISE_BUDGET_FLOORS.items():
            self.assertGreaterEqual(effective["budgets"][key], floor)

    def test_web_launch_normalization_and_config_contract_show_enterprise_effective_profile(self) -> None:
        raw_config = {
            "profile": "enterprise",
            "policy": {"enabled": False},
            "security": {"enabled": False},
            "policy_scan_scope": "staged",
            "security_scan_scope": "full",
            "budgets": {
                "max_total_escalations_per_run": 0,
                "max_total_continuations_per_run": 0,
                "max_total_repair_attempts_per_run": 0,
            },
        }

        normalized = _normalize_config_for_launch(raw_config)
        self.assertEqual(enterprise_role_string(), normalized["roles"])
        self.assertTrue(normalized["policy"]["enabled"])
        self.assertTrue(normalized["security"]["enabled"])
        self.assertEqual("staged", normalized["policy_scan_scope"])
        self.assertEqual("full", normalized["security_scan_scope"])
        for key, floor in ENTERPRISE_BUDGET_FLOORS.items():
            self.assertGreaterEqual(normalized["budgets"][key], floor)

        contract = _build_config_contract(
            self.repo,
            raw_config,
            self.config_path,
            "test",
            self.prompts_dir,
            save_enabled=True,
        )
        project_group = next(group for group in contract["groups"] if group["id"] == "project")
        self.assertIn("policy.enabled", project_group["paths"])
        self.assertIn("policy_scan_scope", project_group["paths"])
        self.assertIn("security.enabled", project_group["paths"])
        self.assertIn("security_scan_scope", project_group["paths"])
        self.assertEqual("bool", contract["schema"]["policy.enabled"]["kind"])
        self.assertEqual(["", "quick", "staged", "full"], contract["schema"]["policy_scan_scope"]["options"])
        self.assertEqual(["", "quick", "staged", "full"], contract["schema"]["security_scan_scope"]["options"])

        effective_profile = contract["profile_effective"]
        self.assertTrue(effective_profile["enterprise"])
        self.assertTrue(effective_profile["security_stage_inserted"])
        self.assertTrue(effective_profile["policy_enabled"])
        self.assertTrue(effective_profile["security_enabled"])
        self.assertTrue(effective_profile["budget_floor_enforced"])
        self.assertEqual(list(enterprise_role_string().split(",")), effective_profile["roles"])

    def test_web_enterprise_effective_profile_preserves_explicit_roles_visibility(self) -> None:
        contract = _build_config_contract(
            self.repo,
            {
                "profile": "enterprise",
                "roles": "PM,Dev,QA",
                "policy": {"enabled": False},
                "security": {"enabled": False},
            },
            self.config_path,
            "test",
            self.prompts_dir,
        )

        effective_profile = contract["profileEffective"]
        self.assertFalse(effective_profile["securityStageInserted"])
        self.assertEqual(["PM", "Dev", "QA"], effective_profile["roles"])
        self.assertTrue(effective_profile["policyEnabled"])
        self.assertTrue(effective_profile["securityEnabled"])


if __name__ == "__main__":
    unittest.main()
