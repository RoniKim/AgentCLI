import argparse
from itertools import islice
import unittest
from pathlib import Path

from agent_runner.cli import DEFAULTS, _merge_effective
from agent_runner.utils import loop_cycle_indices


class LoopConfigTests(unittest.TestCase):
    def test_non_loop_runs_once(self) -> None:
        self.assertEqual([0], list(loop_cycle_indices(False, 0)))

    def test_positive_loop_max_caps_cycles(self) -> None:
        self.assertEqual([0, 1, 2], list(loop_cycle_indices(True, 3)))

    def test_zero_loop_max_is_unbounded(self) -> None:
        self.assertEqual([0, 1, 2, 3, 4], list(islice(loop_cycle_indices(True, 0), 5)))

    def test_negative_loop_max_is_unbounded(self) -> None:
        self.assertEqual([0, 1, 2], list(islice(loop_cycle_indices(True, -1), 3)))

    def test_claude_wait_sites_use_shared_stop_aware_sleep(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "agent_runner" / "backends" / "claudecode.py").read_text(
            encoding="utf-8",
            errors="replace",
        )
        self.assertIn("stop_aware_sleep(", source)
        self.assertNotIn("await asyncio.sleep(wait)", source)
        self.assertNotIn("await asyncio.sleep(wait_sec)", source)
        self.assertNotIn("await asyncio.sleep(max(0, loop_sleep_seconds))", source)

    def test_unattended_preset_respects_explicit_config_and_cli_overrides(self) -> None:
        effective = _merge_effective(
            DEFAULTS,
            {
                "unattended": True,
                "goals_auto_refresh": False,
                "quota_wait_for_reset": False,
                "idle_exit_cycles": 4,
                "gitops": {"worktree_merge_mode": "auto"},
            },
            argparse.Namespace(
                loop=False,
                loop_idle_exit_after=120,
                iterations=9,
                debug=False,
                budget_max_total_repair_attempts_per_run=7,
            ),
        )

        self.assertTrue(effective["unattended"])
        self.assertFalse(effective["loop"])
        self.assertFalse(effective["goals_auto_refresh"])
        self.assertFalse(effective["quota_wait_for_reset"])
        self.assertEqual(4, effective["idle_exit_cycles"])
        self.assertEqual(120, effective["loop_idle_exit_after"])
        self.assertEqual(9, effective["iterations"])
        self.assertFalse(effective["debug"])
        self.assertEqual(7, effective["budgets"]["max_total_repair_attempts_per_run"])
        self.assertEqual("auto", effective["gitops"]["worktree_merge_mode"])


if __name__ == "__main__":
    unittest.main()
