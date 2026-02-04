import asyncio
import tempfile
import unittest
from pathlib import Path

from agent_runner.gates import run_build_gate_async


class TestGatesStop(unittest.TestCase):
    def test_build_gate_stops_on_stop_file(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                stop_path = tmp_path / "STOP"
                log_path = tmp_path / "build.txt"

                async def _stopper() -> None:
                    await asyncio.sleep(0.5)
                    stop_path.write_text("stop\n", encoding="utf-8")

                task = asyncio.create_task(
                    run_build_gate_async(
                        repo=tmp_path,
                        build_cmd=["python", "-c", "import time; time.sleep(60)"],
                        build_timeout_sec=60,
                        legacy_build_target="",
                        log_path=log_path,
                        stop_path=stop_path,
                    )
                )
                stopper = asyncio.create_task(_stopper())
                ok = await task
                await stopper
                self.assertFalse(ok)

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
