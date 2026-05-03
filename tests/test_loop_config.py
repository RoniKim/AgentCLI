from itertools import islice
import unittest

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


if __name__ == "__main__":
    unittest.main()
