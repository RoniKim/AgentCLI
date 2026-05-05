import argparse
import unittest

from agent_runner.cli import DEFAULTS, _merge_effective


class CliRetryConfigTests(unittest.TestCase):
    def test_legacy_test_failed_escalation_also_retries_fast_regression(self) -> None:
        effective = _merge_effective(
            DEFAULTS,
            {
                "dev_escalate_on": ["no_diff", "build_failed", "test_failed", "no_commits"],
            },
            argparse.Namespace(),
        )

        self.assertIn("test_failed", effective["dev_escalate_on"])
        self.assertIn("fast_regression_failed", effective["dev_escalate_on"])

    def test_unattended_preset_is_default_off(self) -> None:
        effective = _merge_effective(DEFAULTS, {}, argparse.Namespace())

        self.assertFalse(effective["unattended"])
        self.assertFalse(effective["goals_auto_refresh"])
        self.assertFalse(effective["loop"])
        self.assertEqual(3, effective["idle_exit_cycles"])
        self.assertEqual(0, effective["loop_idle_exit_after"])
        self.assertEqual(30, effective["iterations"])
        self.assertFalse(effective["debug"])
        self.assertEqual(5, effective["budgets"]["max_total_repair_attempts_per_run"])
        self.assertEqual("manual", effective["gitops"]["worktree_merge_mode"])

    def test_unattended_preset_applies_defaults_and_preserves_enterprise_profile(self) -> None:
        effective = _merge_effective(
            DEFAULTS,
            {
                "profile": "enterprise",
                "unattended": True,
            },
            argparse.Namespace(),
        )

        self.assertTrue(effective["unattended"])
        self.assertEqual("enterprise", effective["profile"])
        self.assertTrue(effective["security"]["enabled"])
        self.assertTrue(effective["goals_auto_refresh"])
        self.assertTrue(effective["quota_wait_for_reset"])
        self.assertTrue(effective["loop"])
        self.assertEqual(0, effective["idle_exit_cycles"])
        self.assertEqual(1800, effective["loop_idle_exit_after"])
        self.assertEqual(5, effective["iterations"])
        self.assertTrue(effective["debug"])
        self.assertGreaterEqual(effective["budgets"]["max_total_repair_attempts_per_run"], 3)
        self.assertEqual("manual", effective["gitops"]["worktree_merge_mode"])


if __name__ == "__main__":
    unittest.main()
