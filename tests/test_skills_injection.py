from __future__ import annotations

import shutil
import unittest
from pathlib import Path
import uuid

from agent_runner.backlog_utils import validate_skill_ids
from agent_runner.pipeline.shared_runtime import build_qa_skills_context
from agent_runner.shared import format_skill_selection, inline_skills_for
from agent_runner.skills.excerpt import build_skills_context
from agent_runner.skills.indexer import build_skills_index, resolve_skills_roots
from agent_runner.skills.summary import summarize_skills_index_capped
from agent_runner.state import TaskItem

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


class SkillsInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self._tmp = scratch / f"skills-injection-{uuid.uuid4().hex}"
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
                    "Use this skill when alpha workflow validation is required.",
                    "Check the alpha edge cases before closing the task.",
                ]
            )
            + "\n",
        )
        self.records = build_skills_index(resolve_skills_roots(self.repo, [self.skills_root.as_posix()]))
        self.assertEqual(1, len(self.records))
        self.alpha = self.records[0]
        self.skills_by_id = {self.alpha.skill_id: self.alpha}
        self.skills_cfg = {
            "enabled": True,
            "roots": [self.skills_root.as_posix()],
            "inline_mode": "qa",
            "pm_summary_max_items": 10,
            "pm_summary_max_chars": 2000,
            "max_excerpt_lines": 4,
            "qa_max_total_chars": 2000,
            "skill_match_autofix": False,
            "skill_match_autofix_threshold": 0.9,
        }

    def _task(self, skills: list[str]) -> TaskItem:
        return TaskItem(
            id="T1",
            title="Alpha workflow",
            prompt="Use the selected skills.",
            files=[],
            done_when="Skill context is injected.",
            skills=skills,
            skills_rationale=None,
            depends_on=[],
        )

    def test_disabled_and_enabled_pm_dev_qa_skill_injection(self) -> None:
        disabled_tasks = [{"id": "T1", "skills": ["alpah"]}]
        self.assertIs(
            disabled_tasks,
            validate_skill_ids(
                disabled_tasks,
                skills_enabled=False,
                skills_by_id=self.skills_by_id,
                skills_records=self.records,
                skills_cfg=self.skills_cfg,
            ),
        )
        self.assertEqual(
            "(skills disabled)",
            build_qa_skills_context(
                load_tasks_fn=lambda: [self._task([self.alpha.skill_id])],
                skills_enabled=False,
                skills_by_id=self.skills_by_id,
                skills_cfg=self.skills_cfg,
                inline_skills_for_fn=inline_skills_for,
                build_skills_context_fn=build_skills_context,
            ),
        )

        pm_summary = summarize_skills_index_capped(self.records, max_items=10, max_chars=2000)
        self.assertIn(self.alpha.skill_id, pm_summary)
        self.assertIn("Alpha Skill", pm_summary)
        self.assertIn("Handles alpha workflows.", pm_summary)

        dev_context = format_skill_selection([self.alpha.skill_id], self.skills_by_id)
        self.assertIn(f"- Alpha Skill ({self.alpha.skill_id})", dev_context)
        self.assertIn("relative_path: alpha", dev_context)
        self.assertIn("resolved_path:", dev_context)

        qa_context = build_qa_skills_context(
            load_tasks_fn=lambda: [self._task([self.alpha.skill_id])],
            skills_enabled=True,
            skills_by_id=self.skills_by_id,
            skills_cfg=self.skills_cfg,
            inline_skills_for_fn=inline_skills_for,
            build_skills_context_fn=build_skills_context,
        )
        self.assertIn(f"- Alpha Skill ({self.alpha.skill_id})", qa_context)
        self.assertIn("Handles alpha workflows.", qa_context)
        self.assertIn("excerpt:", qa_context)
        self.assertIn("Check the alpha edge cases", qa_context)

    def test_missing_root_missing_skill_and_fuzzy_autofix_modes(self) -> None:
        missing_root = self._tmp / "missing-skills"
        no_records = build_skills_index(resolve_skills_roots(self.repo, [missing_root.as_posix()]))
        self.assertEqual([], no_records)
        self.assertEqual("(no skills indexed)", summarize_skills_index_capped(no_records, max_items=10, max_chars=2000))

        dev_missing = format_skill_selection(["alpah"], self.skills_by_id)
        self.assertIn("- alpah (missing)", dev_missing)
        self.assertIn("Missing skills: alpah", dev_missing)

        qa_missing = build_qa_skills_context(
            load_tasks_fn=lambda: [self._task([self.alpha.skill_id, "alpah"])],
            skills_enabled=True,
            skills_by_id=self.skills_by_id,
            skills_cfg=self.skills_cfg,
            inline_skills_for_fn=inline_skills_for,
            build_skills_context_fn=build_skills_context,
        )
        self.assertIn(f"- Alpha Skill ({self.alpha.skill_id})", qa_missing)
        self.assertIn("Missing skills: alpah", qa_missing)

        tasks = [{"id": "T1", "skills": ["alpah"]}]
        no_autofix = validate_skill_ids(
            tasks,
            skills_enabled=True,
            skills_by_id=self.skills_by_id,
            skills_records=self.records,
            skills_cfg={**self.skills_cfg, "skill_match_autofix": False, "skill_match_autofix_threshold": 0.1},
        )
        self.assertEqual(["alpah"], no_autofix[0]["skills"])

        autofixed = validate_skill_ids(
            tasks,
            skills_enabled=True,
            skills_by_id=self.skills_by_id,
            skills_records=self.records,
            skills_cfg={**self.skills_cfg, "skill_match_autofix": True, "skill_match_autofix_threshold": 0.1},
        )
        self.assertEqual([self.alpha.skill_id], autofixed[0]["skills"])


if __name__ == "__main__":
    unittest.main()
