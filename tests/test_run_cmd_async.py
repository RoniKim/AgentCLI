import asyncio
import tempfile
import unittest
from pathlib import Path

from agent_runner.utils import run_cmd_async


class TestRunCmdAsync(unittest.TestCase):
    def test_stop_file_terminates_process(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                stop_path = tmp_path / "STOP"
                log_path = tmp_path / "log.txt"

                async def _stopper() -> None:
                    await asyncio.sleep(0.5)
                    stop_path.write_text("stop\n", encoding="utf-8")

                task = asyncio.create_task(
                    run_cmd_async(
                        ["python", "-c", "import time; time.sleep(60)"],
                        cwd=tmp_path,
                        log_path=log_path,
                        timeout_sec=60,
                        stop_path=stop_path,
                    )
                )
                stopper = asyncio.create_task(_stopper())
                rc, summary = await task
                await stopper
                self.assertNotEqual(rc, 0)
                self.assertIn("stopped", summary)

        asyncio.run(_run())

    def test_output_truncation(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                log_path = tmp_path / "log.txt"
                rc, summary = await run_cmd_async(
                    ["python", "-c", "print('x' * 10000)"],
                    cwd=tmp_path,
                    log_path=log_path,
                    timeout_sec=10,
                    max_output_bytes=100,
                )
                self.assertEqual(rc, 0)
                self.assertIn("truncated", summary)
                self.assertIn("TRUNCATED OUTPUT", log_path.read_text(encoding="utf-8", errors="replace"))

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
