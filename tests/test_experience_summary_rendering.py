from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.experience import ExperienceRenderContext, ExperienceSummaryConfig, render_experience_summary


class ExperienceSummaryRenderingTests(unittest.TestCase):
    def test_item_count_truncation_reports_omitted_count(self) -> None:
        # Arrange
        lessons = [
            {"kind": "validation", "summary": "Run Playwright smoke when app.js changes.", "score": 9.0},
            {"kind": "task_sizing", "summary": "Split keyboard and accessibility work into separate tasks.", "score": 8.0},
            {"kind": "env", "summary": "Use short temp paths for nested worktrees on Windows.", "score": 1.0},
        ]
        config = ExperienceSummaryConfig(max_items=2, max_chars=800, lesson_max_chars=160, evidence_max_items=2)

        # Act
        rendered = render_experience_summary(lessons, config=config)

        # Assert
        self.assertIn('items="2"', rendered)
        self.assertIn('omitted="1"', rendered)
        self.assertIn("Run Playwright smoke when app.js changes.", rendered)
        self.assertIn("Split keyboard and accessibility work into separate tasks.", rendered)
        self.assertNotIn("Use short temp paths for nested worktrees on Windows.", rendered)

    def test_character_budget_truncates_lowest_ranked_lessons(self) -> None:
        # Arrange
        lessons = [
            {"kind": "validation", "summary": "Run static web console tests before Playwright smoke.", "score": 9.0},
            {"kind": "merge", "summary": "Review worktree cleanup before applying packet patches.", "score": 8.0},
        ]
        config = ExperienceSummaryConfig(max_items=4, max_chars=220, lesson_max_chars=120, evidence_max_items=2)

        # Act
        rendered = render_experience_summary(lessons, config=config)

        # Assert
        self.assertLessEqual(len(rendered), config.max_chars)
        self.assertIn('items="1"', rendered)
        self.assertIn('omitted="1"', rendered)
        self.assertIn("Run static web console tests before Playwright smoke.", rendered)
        self.assertNotIn("Review worktree cleanup before applying packet patches.", rendered)

    def test_raw_log_and_diff_content_is_rejected(self) -> None:
        # Arrange
        lessons = [
            {
                "kind": "validation",
                "summary": "web_console/app.js changes usually need static tests and Playwright smoke.",
                "score": 10.0,
            },
            {
                "kind": "debug",
                "summary": "diff --git a/app.js b/app.js\n@@ -1,2 +1,2 @@\n-console.log('old')\n+console.log('new')",
                "score": 9.0,
            },
            {
                "kind": "debug",
                "summary": "2026-05-03 10:00:00 INFO starting\n2026-05-03 10:00:01 ERROR failed\n2026-05-03 10:00:02 INFO retrying\n2026-05-03 10:00:03 WARN stale lock",
                "score": 8.0,
            },
        ]
        config = ExperienceSummaryConfig(max_items=5, max_chars=800, lesson_max_chars=160, evidence_max_items=2)

        # Act
        rendered = render_experience_summary(lessons, config=config)

        # Assert
        self.assertIn('items="1"', rendered)
        self.assertIn("web_console/app.js changes usually need static tests and Playwright smoke.", rendered)
        self.assertNotIn("diff --git", rendered)
        self.assertNotIn("INFO starting", rendered)
        self.assertNotIn("ERROR failed", rendered)

    def test_evidence_paths_are_redacted_but_debuggable(self) -> None:
        # Arrange
        lessons = [
            {
                "kind": "validation",
                "summary": "Re-run the failing validation after prompt changes to verify the gate selection.",
                "confidence": 0.83,
                "score": 10.0,
                "evidence": [
                    r"C:\Dev\AgentCLI\.AgentCLI\agent_runs\20260503-125328\tasks\T14\attempt_1\build.txt",
                    r"C:\Dev\.agentcli_worktrees\AgentCLI\20260503-125328\agent_runner\prompts.py:154",
                ],
            }
        ]
        config = ExperienceSummaryConfig(max_items=3, max_chars=900, lesson_max_chars=180, evidence_max_items=3)

        # Act
        rendered = render_experience_summary(lessons, config=config)

        # Assert
        self.assertIn("tasks/T14/attempt_1/[log]", rendered)
        self.assertIn("prompts.py:154", rendered)
        self.assertIn("agent_runner/prompts.py", rendered)
        self.assertNotIn("C:\\Dev\\AgentCLI", rendered)
        self.assertNotIn("C:\\Dev\\.agentcli_worktrees", rendered)

    def test_relevance_scoring_prefers_context_matches_without_explicit_scores(self) -> None:
        # Arrange
        lessons = [
            {
                "kind": "task_sizing",
                "summary": "Split accessibility changes from screenshot-driven UI work.",
                "applies_to_goal_refs": ["G3"],
                "confidence": 0.9,
            },
            {
                "kind": "validation",
                "summary": "web_console/app.js changes need static tests before smoke.",
                "applies_to_file_globs": ["web_console/app.js"],
                "applies_to_gates": ["playwright"],
                "confidence": 0.4,
            },
        ]
        context = ExperienceRenderContext.from_values(
            changed_files=["web_console/app.js"],
            validation_gates=["playwright"],
        )
        config = ExperienceSummaryConfig(max_items=1, max_chars=500, lesson_max_chars=160, evidence_max_items=2)

        # Act
        rendered = render_experience_summary(lessons, config=config, context=context)

        # Assert
        self.assertIn("web_console/app.js changes need static tests before smoke.", rendered)
        self.assertNotIn("Split accessibility changes from screenshot-driven UI work.", rendered)


if __name__ == "__main__":
    unittest.main()
