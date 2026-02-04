import sys
import tempfile
import unittest
from pathlib import Path

from agent_runner.pipeline.stage_registry import make_stages


class TestPluginLoading(unittest.TestCase):
    def test_plugins_disabled(self) -> None:
        with self.assertRaises(ValueError):
            make_stages(
                "temp_plugin:MyStage",
                plugins_enabled=False,
                plugins_allowlist=["temp_plugin"],
                plugins_strict=True,
            )

    def test_allowlist_enforced_and_stage_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mod_path = tmp_path / "temp_plugin.py"
            mod_path.write_text(
                "from agent_runner.pipeline.stages.base import Stage, StageOutcome\n"
                "class MyStage(Stage):\n"
                "    name = 'MyStage'\n"
                "    async def run(self, session, cycle_idx):\n"
                "        return StageOutcome.ok('ok')\n",
                encoding="utf-8",
            )

            sys.path.insert(0, str(tmp_path))
            try:
                with self.assertRaises(ValueError):
                    make_stages(
                        "temp_plugin:MyStage",
                        plugins_enabled=True,
                        plugins_allowlist=[],
                        plugins_strict=True,
                    )

                stages = make_stages(
                    "temp_plugin:MyStage",
                    plugins_enabled=True,
                    plugins_allowlist=["temp_plugin"],
                    plugins_strict=True,
                )
                self.assertEqual(len(stages), 1)
                self.assertEqual(stages[0].name, "MyStage")
            finally:
                sys.path.remove(str(tmp_path))

    def test_non_stage_class_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            mod_path = tmp_path / "bad_plugin.py"
            mod_path.write_text(
                "class NotStage:\n"
                "    pass\n",
                encoding="utf-8",
            )

            sys.path.insert(0, str(tmp_path))
            try:
                with self.assertRaises(TypeError):
                    make_stages(
                        "bad_plugin:NotStage",
                        plugins_enabled=True,
                        plugins_allowlist=["bad_plugin"],
                        plugins_strict=True,
                    )
            finally:
                sys.path.remove(str(tmp_path))


if __name__ == "__main__":
    unittest.main()
