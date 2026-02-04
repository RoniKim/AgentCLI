import tempfile
import unittest
from pathlib import Path

from agent_runner.utils import ensure_relative_to_repo


class TestPathValidation(unittest.TestCase):
    def test_ensure_relative_to_repo_blocks_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            outside = Path(tmp) / "outside.txt"
            outside.write_text("x", encoding="utf-8")

            with self.assertRaises(ValueError):
                ensure_relative_to_repo(repo, str(outside))


if __name__ == "__main__":
    unittest.main()
