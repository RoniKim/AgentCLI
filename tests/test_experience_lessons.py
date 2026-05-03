import unittest

from agent_runner.experience import classify_experience_lessons


class ExperienceLessonTests(unittest.TestCase):
    def _lesson_by_kind(self, payload: dict[str, object], kind: str) -> dict[str, object]:
        for lesson in payload["lessons"]:
            if lesson["kind"] == kind:
                return lesson
        raise AssertionError(f"Lesson kind not found: {kind}")

    def test_oversized_multi_surface_work_yields_task_sizing_lesson(self) -> None:
        payload = classify_experience_lessons(
            [
                {
                    "task_id": "T16-A",
                    "goal_refs": ["G-101", "G-102"],
                    "changed_files": [
                        "web_console/app.js",
                        "agent_runner/pr_queue.py",
                        "web_console/styles.css",
                        "docs/notes.md",
                    ],
                    "gates": ["build", "fast_web_worktree_regression"],
                    "task_status": "review_required",
                    "validation_status": "validation_failed",
                    "evidence": [".AgentCLI/agent_runs/20260503-125328/tasks/T16-A/attempt_00/test.txt"],
                }
            ]
        )

        lesson = self._lesson_by_kind(payload, "task_sizing")
        self.assertEqual("task_sizing", lesson["recommendation_family"])
        self.assertIn("splitting multi-surface work", str(lesson["lesson"]).lower())
        self.assertIn("G-101", lesson["applies_to_goal_refs"])
        self.assertIn("web_console/**/*", lesson["applies_to_file_globs"])
        self.assertGreaterEqual(float(lesson["confidence"]), 0.6)

    def test_web_changes_with_skipped_or_missing_tests_yield_validation_selection_guidance(self) -> None:
        payload = classify_experience_lessons(
            [
                {
                    "task_id": "T16-B",
                    "goal_refs": ["G-201"],
                    "changed_files": ["web_console/app.js", "web_console/styles.css"],
                    "gates": ["fast_web_worktree_regression", "test"],
                    "task_status": "review_required",
                    "validation_status": "tests_skipped",
                    "evidence": [".AgentCLI/agent_runs/20260503-125328/tasks/T16-B/attempt_00/test.txt"],
                },
                {
                    "task_id": "T16-B2",
                    "goal_refs": ["G-201"],
                    "changed_files": ["web_console/app.js"],
                    "gates": ["fast_web_worktree_regression"],
                    "task_status": "review_required",
                    "validation_status": "no_tests_found",
                    "evidence": [".AgentCLI/agent_runs/20260503-125328/tasks/T16-B2/attempt_00/test.txt"],
                },
            ]
        )

        lesson = self._lesson_by_kind(payload, "validation_selection")
        self.assertIn("web-surface files", str(lesson["lesson"]).lower())
        self.assertIn("fast_web_worktree_regression", lesson["applies_to_gates"])
        self.assertIn("tests_skipped", lesson["applies_to_validation_statuses"])
        self.assertIn("no_tests_found", lesson["applies_to_validation_statuses"])
        self.assertEqual(payload["validation_lessons"][0]["kind"], "validation_selection")

    def test_repeated_failure_signature_or_discarded_pr_yields_retry_avoidance_guidance(self) -> None:
        payload = classify_experience_lessons(
            [
                {
                    "task_id": "T16-C1",
                    "goal_refs": ["G-301"],
                    "changed_files": ["web_console/app.js"],
                    "gates": ["fast_web_worktree_regression"],
                    "task_status": "regression_failed",
                    "validation_status": "validation_failed",
                    "reason": "fast_regression_failed",
                    "failure_signature": "fast-web-stale-lock",
                    "evidence": [".AgentCLI/agent_runs/20260503-125328/tasks/T16-C1/attempt_00/test.txt"],
                },
                {
                    "task_id": "T16-C2",
                    "goal_refs": ["G-301"],
                    "changed_files": ["web_console/app.js"],
                    "gates": ["fast_web_worktree_regression"],
                    "task_status": "review_required",
                    "validation_status": "validation_failed",
                    "reason": "fast_regression_failed",
                    "failure_signature": "fast-web-stale-lock",
                    "pr_decision": "discarded",
                    "evidence": [".AgentCLI/agent_runs/20260503-125328/pr_queue/packet-1.json"],
                },
            ]
        )

        lesson = self._lesson_by_kind(payload, "retry_avoidance")
        self.assertIn("same-signature retry", str(lesson["lesson"]).lower())
        self.assertIn("discarded", lesson["pr_decisions"])
        self.assertGreaterEqual(int(lesson["evidence_count"]), 2)
        self.assertGreaterEqual(float(lesson["confidence"]), 0.75)

    def test_dependency_failed_blockers_yield_dependency_cleanup_guidance(self) -> None:
        payload = classify_experience_lessons(
            [
                {
                    "task_id": "T16-D",
                    "goal_refs": ["G-401"],
                    "changed_files": ["agent_runner/experience.py"],
                    "gates": ["build"],
                    "task_status": "review_required",
                    "validation_status": "validation_pending",
                    "reason": "dependency_failed",
                    "blocked_dependencies": [
                        {
                            "task_id": "T15",
                            "status": "regression_failed",
                            "reason": "fast_regression_failed",
                            "validation_summary": "1 failing safety test",
                            "next_action": "Resolve T15 first.",
                        }
                    ],
                    "evidence": [".AgentCLI/agent_runs/20260503-125328/failed_tasks.json"],
                }
            ]
        )

        lesson = self._lesson_by_kind(payload, "dependency_cleanup")
        self.assertIn("dependency blockers", str(lesson["lesson"]).lower())
        self.assertIn("T15", str(lesson["lesson"]))
        self.assertIn("build", lesson["applies_to_gates"])
        self.assertGreaterEqual(int(lesson["evidence_count"]), 2)


if __name__ == "__main__":
    unittest.main()
