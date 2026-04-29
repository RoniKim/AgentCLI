from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner import utils


class _FakePipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakeStdout(_FakePipe):
    def __iter__(self):
        return iter(())


class _FakePopen:
    def __init__(self) -> None:
        self.pid = 12345
        self.stdin = _FakePipe()
        self.stdout = _FakeStdout()
        self.stderr = None
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls += 1
        self.returncode = 0
        return 0


class CodexAppServerCleanupTests(unittest.TestCase):
    def test_close_releases_stdio_and_unregisters_process(self) -> None:
        fake_proc = _FakePopen()
        unregistered: list[int] = []
        terminated: list[tuple[int, bool]] = []

        with (
            patch("agent_runner.utils.subprocess.Popen", return_value=fake_proc),
            patch.object(utils._CodexAppServerClient, "_initialize", return_value=None),
            patch("agent_runner.process_guard.register_pid"),
            patch(
                "agent_runner.process_guard.terminate_process_tree",
                side_effect=lambda pid, include_root=True: terminated.append((pid, include_root)) or True,
            ),
            patch(
                "agent_runner.process_guard.unregister_pid_if_exited",
                side_effect=lambda pid: unregistered.append(pid),
            ),
        ):
            client = utils._CodexAppServerClient(codex_path="codex")
            client.close()
            client.close()

        self.assertTrue(fake_proc.stdin.closed)
        self.assertTrue(fake_proc.stdout.closed)
        self.assertTrue(fake_proc.terminated)
        self.assertEqual(1, fake_proc.wait_calls)
        self.assertEqual([(12345, False)], terminated)
        self.assertEqual([12345], unregistered)


if __name__ == "__main__":
    unittest.main()
