from __future__ import annotations

import ctypes
import contextlib
import io
import os
import shutil
import unittest
from pathlib import Path

from agent_runner.logger import close_all_loggers, create_logger


def _assert_exclusive_open(path: Path) -> None:
    if os.name != "nt":
        return
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.CreateFileW(
        str(path),
        0x80000000 | 0x40000000,  # GENERIC_READ | GENERIC_WRITE
        0,  # no sharing
        None,
        3,  # OPEN_EXISTING
        0x80,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    invalid = ctypes.c_void_p(-1).value
    try:
        if handle == invalid:
            raise OSError(f"exclusive open failed for {path}")
    finally:
        if handle and handle != invalid:
            kernel32.CloseHandle(handle)


class StructuredLoggerTests(unittest.TestCase):
    def test_close_releases_all_log_file_handlers(self) -> None:
        run_dir = Path.cwd() / ".test-scratch" / "logger-close-test"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                logger = create_logger(run_dir, debug=True)
                logger.info("runner started")
                logger.error("runner error", include_traceback=False)

            logger.close()

            self.assertEqual([], logger.logger.handlers)
            self.assertIsNone(logger._events_fh)
            for name in ("run.log", "error.log", "debug.log", "events.jsonl"):
                _assert_exclusive_open(run_dir / "logs" / name)
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)

    def test_close_all_loggers_releases_active_handlers(self) -> None:
        run_dir = Path.cwd() / ".test-scratch" / "logger-close-all-test"
        shutil.rmtree(run_dir, ignore_errors=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                logger = create_logger(run_dir, debug=False)
                logger.info("runner started")

            close_all_loggers()

            self.assertEqual([], logger.logger.handlers)
            _assert_exclusive_open(run_dir / "logs" / "run.log")
            _assert_exclusive_open(run_dir / "logs" / "events.jsonl")
        finally:
            shutil.rmtree(run_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
