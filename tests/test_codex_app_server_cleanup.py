from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.backends.codex_quota import _CodexAppServerClient


class _FakePipe:
    def __init__(self) -> None:
        self.close_calls = 0
        self.closed = False

    def close(self) -> None:
        self.close_calls += 1
        self.closed = True


class _FakeReaderThread:
    def __init__(self) -> None:
        self.started = False
        self.join_calls: list[float | None] = []
        self._alive = True

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)
        self._alive = False


class _FakeProc:
    def __init__(self, *, pid: int = 4321, terminate_stops: bool = True) -> None:
        self.pid = pid
        self.stdin = _FakePipe()
        self.stdout = _FakePipe()
        self.stderr = _FakePipe()
        self._alive = True
        self._returncode: int | None = None
        self._terminate_stops = terminate_stops
        self.terminate_calls = 0
        self.kill_calls = 0
        self.wait_calls: list[float | None] = []

    def poll(self) -> int | None:
        return None if self._alive else self._returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self._terminate_stops:
            self._alive = False
            self._returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self._alive = False
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if self._alive and not self._terminate_stops:
            raise subprocess.TimeoutExpired(cmd=["codex", "app-server"], timeout=timeout)
        if self._returncode is None:
            self._returncode = 0
        self._alive = False
        return self._returncode


class CodexAppServerCleanupTests(unittest.TestCase):
    def _build_client(self, proc: _FakeProc, reader: _FakeReaderThread) -> _CodexAppServerClient:
        with (
            patch("agent_runner.utils.sys.platform", "win32"),
            patch("agent_runner.backends.codex_quota.subprocess.CREATE_NO_WINDOW", 0x08000000, create=True),
            patch("agent_runner.backends.codex_quota.subprocess.Popen", return_value=proc) as popen,
            patch("agent_runner.backends.codex_quota.threading.Thread", return_value=reader),
            patch("agent_runner.backends.codex_quota._CodexAppServerClient._initialize", return_value=None),
            patch("agent_runner.process_guard.register_pid") as register_pid,
        ):
            client = _CodexAppServerClient(codex_path="codex", timeout_s=0.1)
        kwargs = popen.call_args.kwargs
        self.assertTrue(bool(kwargs["close_fds"]))
        self.assertEqual(0x08000000, int(kwargs["creationflags"]))
        self.assertIs(kwargs["stdin"], subprocess.PIPE)
        self.assertIs(kwargs["stdout"], subprocess.PIPE)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        register_pid.assert_called_once_with(proc.pid)
        return client

    def test_close_closes_pipes_joins_reader_and_unregisters_pid(self) -> None:
        proc = _FakeProc(terminate_stops=True)
        reader = _FakeReaderThread()

        with (
            patch("agent_runner.process_guard.terminate_process_tree") as terminate_tree,
            patch("agent_runner.process_guard.unregister_pid_if_exited") as unregister_pid,
        ):
            client = self._build_client(proc, reader)
            client.close()
            client.close()

        self.assertEqual(1, proc.stdin.close_calls)
        self.assertEqual(1, proc.stdout.close_calls)
        self.assertEqual(1, proc.stderr.close_calls)
        self.assertEqual([2], reader.join_calls)
        self.assertEqual(1, proc.terminate_calls)
        self.assertEqual(0, proc.kill_calls)
        self.assertEqual([2], proc.wait_calls)
        terminate_tree.assert_called_once_with(proc.pid, include_root=False, wait=True)
        unregister_pid.assert_called_once_with(proc.pid)
        self.assertIsNone(client._registered_pid)

    def test_close_forced_termination_still_cleans_up_everything(self) -> None:
        proc = _FakeProc(terminate_stops=False)
        reader = _FakeReaderThread()

        with (
            patch("agent_runner.process_guard.terminate_process_tree") as terminate_tree,
            patch("agent_runner.process_guard.unregister_pid_if_exited") as unregister_pid,
        ):
            client = self._build_client(proc, reader)
            client.close()

        self.assertEqual(1, proc.stdin.close_calls)
        self.assertEqual(1, proc.stdout.close_calls)
        self.assertEqual(1, proc.stderr.close_calls)
        self.assertEqual([2], reader.join_calls)
        self.assertEqual(1, proc.terminate_calls)
        self.assertEqual(1, proc.kill_calls)
        self.assertEqual([2, 2], proc.wait_calls)
        terminate_tree.assert_called_once_with(proc.pid, include_root=False, wait=True)
        unregister_pid.assert_called_once_with(proc.pid)
        self.assertIsNone(client._registered_pid)


if __name__ == "__main__":
    unittest.main()
