from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner import process_guard


class ProcessGuardTests(unittest.TestCase):
    def test_descendant_pids_walks_nested_tree_without_self(self) -> None:
        child_map = {
            10: [11, 12],
            11: [13],
            12: [14],
            13: [999],
        }

        with patch("agent_runner.process_guard.os.getpid", return_value=999):
            descendants = process_guard._descendant_pids(10, child_map)

        self.assertEqual({11, 12, 13, 14}, set(descendants))
        self.assertNotIn(999, descendants)

    def test_terminate_process_tree_kills_windows_descendants_before_root(self) -> None:
        killed: list[int] = []
        child_map = {
            10: [11, 12],
            11: [13],
        }

        with (
            patch.object(process_guard.sys, "platform", "win32"),
            patch("agent_runner.process_guard.os.getpid", return_value=999),
            patch("agent_runner.process_guard._windows_child_pid_map", return_value=child_map),
            patch("agent_runner.process_guard._kill_pid", side_effect=lambda pid: killed.append(pid)),
        ):
            process_guard.terminate_process_tree(10, include_root=True)

        self.assertEqual([13, 11, 12, 10], killed)

    def test_terminate_process_tree_can_kill_leaked_descendants_only(self) -> None:
        killed: list[int] = []
        child_map = {
            20: [21],
            21: [22],
        }

        with (
            patch.object(process_guard.sys, "platform", "win32"),
            patch("agent_runner.process_guard.os.getpid", return_value=999),
            patch("agent_runner.process_guard._windows_child_pid_map", return_value=child_map),
            patch("agent_runner.process_guard._kill_pid", side_effect=lambda pid: killed.append(pid)),
        ):
            process_guard.terminate_process_tree(20, include_root=False)

        self.assertEqual([22, 21], killed)

    def test_process_descendant_pids_uses_windows_snapshot_map(self) -> None:
        child_map = {
            30: [31],
            31: [32],
        }

        with (
            patch.object(process_guard.sys, "platform", "win32"),
            patch("agent_runner.process_guard.os.getpid", return_value=999),
            patch("agent_runner.process_guard._windows_child_pid_map", return_value=child_map),
        ):
            descendants = process_guard.process_descendant_pids(30)

        self.assertEqual({31, 32}, set(descendants))


if __name__ == "__main__":
    unittest.main()
