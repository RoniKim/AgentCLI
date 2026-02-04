import asyncio
import tempfile
from pathlib import Path
import time
import unittest

from agent_runner.utils import run_cmd_async


class TestRunCmdAsync(unittest.TestCase):
    def test_stop_file_terminates(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            stop_path = run_dir / "STOP"
            log_path = run_dir / "cmd.log"

            async def _run():
                async def _trigger_stop():
                    await asyncio.sleep(0.5)
                    stop_path.write_text("stop\n", encoding="utf-8")

                stopper = asyncio.create_task(_trigger_stop())
                rc, _ = await run_cmd_async(
                    ["python", "-c", "import time; time.sleep(60)"],
                    cwd=run_dir,
                    log_path=log_path,
                    timeout_sec=60,
                    stop_path=stop_path,
                )
                await stopper
                return rc

            t0 = time.time()
            rc = asyncio.run(_run())
            dt = time.time() - t0
            self.assertNotEqual(rc, 0)
            self.assertLess(dt, 5)

    def test_output_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            run_dir = Path(td)
            log_path = run_dir / "cmd.log"

            async def _run():
                rc, _ = await run_cmd_async(
                    ["python", "-c", "print('a'*50000)"],
                    cwd=run_dir,
                    log_path=log_path,
                    timeout_sec=10,
                    max_output_bytes=1000,
                )
                return rc

            rc = asyncio.run(_run())
            self.assertEqual(rc, 0)
            data = log_path.read_text(encoding="utf-8", errors="replace")
            self.assertIn("TRUNCATED", data)


if __name__ == "__main__":
    unittest.main()
