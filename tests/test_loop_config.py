from itertools import islice
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
