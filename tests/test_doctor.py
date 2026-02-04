import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_runner.shell import RunnerShell
from agent_runner.run_dir import make_run_dir


def _run(cmd, cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)


class TestDoctor(unittest.TestCase):
    def test_doctor_creates_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _run(["git", "init"], repo)
            _run(["git", "config", "user.email", "test@example.com"], repo)
            _run(["git", "config", "user.name", "Tester"], repo)

            shell = RunnerShell()
            shell.set_repo(str(repo))
            shell.run_dir = make_run_dir(repo)

            shell.doctor()
            report_path = shell.run_dir / "DOCTOR.md"
            self.assertTrue(report_path.exists())


if __name__ == "__main__":
    unittest.main()
