from __future__ import annotations

import asyncio
import sys
import shutil
from types import SimpleNamespace
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner import process_guard


class ProcessGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = ROOT / ".tmp-process-guard-tests"
        self.fixture_base.mkdir(exist_ok=True)
        self.fixture_root = self.fixture_base / f"{self._testMethodName}-{uuid.uuid4().hex}"
        self.fixture_root.mkdir()
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))

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

    def test_terminate_pids_uses_one_windows_snapshot_for_bulk_cleanup(self) -> None:
        killed: list[int] = []
        child_map = {
            10: [11],
            11: [12],
            20: [21],
        }

        with (
            patch.object(process_guard.sys, "platform", "win32"),
            patch("agent_runner.process_guard.os.getpid", return_value=999),
            patch("agent_runner.process_guard._windows_child_pid_map", return_value=child_map) as snapshot,
            patch("agent_runner.process_guard._kill_pid", side_effect=lambda pid: killed.append(pid)),
            patch("agent_runner.process_guard._pid_alive", return_value=False),
        ):
            process_guard._terminate_pids([10, 20, 10], wait=True)

        self.assertEqual(1, snapshot.call_count)
        self.assertEqual([12, 11, 10, 21, 20], killed)

    def test_pid_summary_caps_large_stop_logs(self) -> None:
        summary = process_guard._summarize_pids(list(range(45)), limit=5)

        self.assertEqual("[0, 1, 2, 3, 4, ...] (45 total)", summary)

    def test_startup_orphan_detection_excludes_interactive_shell_images(self) -> None:
        def _looks_managed(image_name: str) -> bool:
            output = f'"{image_name}","1234","Console","1","10,000 K"\r\n'
            with (
                patch.object(process_guard.sys, "platform", "win32"),
                patch("subprocess.run", return_value=SimpleNamespace(stdout=output)),
            ):
                return process_guard._is_managed_child_process(1234)

        for image_name in ("cmd.exe", "powershell.exe", "python.exe"):
            with self.subTest(image_name=image_name):
                self.assertFalse(_looks_managed(image_name))

        for image_name in ("node.exe", "codex.exe", "claude.exe"):
            with self.subTest(image_name=image_name):
                self.assertTrue(_looks_managed(image_name))

    @unittest.skipUnless(sys.platform == "win32", "Windows process-tree smoke test")
    def test_run_cmd_async_cleans_inherited_stdout_child_process(self) -> None:
        from agent_runner.utils import run_cmd_async

        log_path = self.fixture_root / "process-smoke.log"
        child_code = "import time; time.sleep(60)"
        parent_code = (
            "import subprocess,sys,time; "
            f"p=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
            "print(p.pid, flush=True); "
            "time.sleep(2)"
        )

        started = time.monotonic()
        rc, summary = asyncio.run(
            run_cmd_async(
                [sys.executable, "-c", parent_code],
                self.fixture_root,
                log_path,
                timeout_sec=30,
            )
        )
        elapsed = time.monotonic() - started
        child_pid = int(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[0])
        time.sleep(0.5)

        self.assertEqual(0, rc)
        self.assertEqual("ok", summary)
        self.assertLess(elapsed, 10)
        self.assertFalse(process_guard._pid_alive(child_pid))


if __name__ == "__main__":
    unittest.main()
