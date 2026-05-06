from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
import json
import shutil
import unittest
from pathlib import Path
import uuid

from agent_runner.shell import RunnerShell
from agent_runner.skills.indexer import build_skills_index, resolve_skills_roots
from agent_runner.skills.status import build_skills_status, format_skills_status_lines

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


class SkillsStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self._tmp = scratch / f"skills-status-{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.skills_root = self._tmp / "skills"
        _write(
            self.skills_root / "alpha" / "SKILL.md",
            "\n".join(
                [
                    "---",
                    "name: Alpha Skill",
                    "description: Handles alpha workflows.",
                    "tags: [alpha, qa]",
                    "---",
                    "# Alpha Skill",
                    "",
                ]
            ),
        )
        records = build_skills_index(resolve_skills_roots(self.repo, [self.skills_root.as_posix()]))
        self.assertEqual(1, len(records))
        self.alpha_skill_id = records[0].skill_id
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "run-1"
        _write(
            self.run_dir / "BACKLOG.json",
            json.dumps(
                {
                    "tasks": [
                        {
                            "id": "T1",
                            "title": "Use selected skills",
                            "prompt": "Exercise skill status.",
                            "skills": [self.alpha_skill_id, "alpah"],
                        }
                    ]
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )

    def _skills_config(self, *roots: str) -> dict[str, object]:
        return {
            "enabled": True,
            "roots": list(roots),
            "inline_mode": "qa",
            "skill_match_autofix": False,
            "skill_match_autofix_threshold": 0.9,
        }

    def test_build_skills_status_reports_roots_selected_missing_and_suggestions(self) -> None:
        missing_root = self._tmp / "missing-skills"
        status = build_skills_status(
            self.repo,
            self._skills_config(self.skills_root.as_posix(), missing_root.as_posix()),
            run_dir=self.run_dir,
        )

        self.assertTrue(status["enabled"])
        self.assertEqual(2, status["root_count"])
        self.assertEqual(1, status["existing_root_count"])
        self.assertEqual(1, status["discovered_count"])
        self.assertIn(self.alpha_skill_id, status["selected_skill_ids"])
        self.assertIn("alpah", status["selected_skill_ids"])
        self.assertEqual(["alpah"], status["missing_skill_ids"])
        self.assertTrue(any("missing skill root" in warning for warning in status["warnings"]))

        suggestion = status["suggestions"][0]
        self.assertEqual("alpah", suggestion["missing"])
        self.assertEqual(self.alpha_skill_id, suggestion["matches"][0]["skill_id"])

        lines = format_skills_status_lines(status)
        self.assertIn("roots=1/2", lines[0])
        self.assertTrue(any("selected skill ids" in line for line in lines))
        self.assertTrue(any("suggestion for alpah" in line for line in lines))

    def test_shell_status_and_doctor_show_skills_status(self) -> None:
        shell = RunnerShell(["--repo", self.repo.as_posix()])
        shell.overrides["skills"] = self._skills_config(self.skills_root.as_posix())
        shell.run_dir = self.run_dir

        status_buffer = StringIO()
        with redirect_stdout(status_buffer):
            shell.status()
        status_output = status_buffer.getvalue()
        self.assertIn("skills: enabled=True roots=1/1 discovered=1 selected=2 missing=1", status_output)
        self.assertIn("selected skill ids:", status_output)
        self.assertIn("missing skill ids: alpah", status_output)

        doctor_buffer = StringIO()
        with redirect_stdout(doctor_buffer):
            shell.doctor()
        doctor_output = doctor_buffer.getvalue()
        self.assertIn("- skills status:", doctor_output)
        self.assertIn("suggestion for alpah", doctor_output)
        self.assertTrue((self.run_dir / "DOCTOR.md").exists())


if __name__ == "__main__":
    unittest.main()
