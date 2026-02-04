import json
import tempfile
from pathlib import Path
import unittest

from agent_runner.utils import atomic_write_json, atomic_write_text


class TestAtomicWrite(unittest.TestCase):
    def test_atomic_write_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            payload = {"ok": True, "count": 3}
            atomic_write_json(path, payload)
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            self.assertEqual(data, payload)

    def test_atomic_write_text(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "note.txt"
            atomic_write_text(path, "hello\n")
            self.assertEqual(path.read_text(encoding="utf-8"), "hello\n")


if __name__ == "__main__":
    unittest.main()
