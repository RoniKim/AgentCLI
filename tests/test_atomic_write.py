import json
import tempfile
import unittest
from pathlib import Path

from agent_runner.utils import atomic_write_json, atomic_write_text


class TestAtomicWrite(unittest.TestCase):
    def test_atomic_write_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            data = {"ok": True, "value": 1}
            atomic_write_json(path, data)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded, data)

    def test_atomic_write_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.txt"
            atomic_write_text(path, "hello\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")


if __name__ == "__main__":
    unittest.main()
