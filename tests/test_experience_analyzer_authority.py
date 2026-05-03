from __future__ import annotations

import json
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_runner.analyzer import (
    ANALYZER_AUTHORITY,
    ANALYZER_REPORT_FILENAME,
    ANALYZER_SUMMARY_FILENAME,
    EXPERIENCE_UPDATES_FILENAME,
    execute_analyzer,
)


class ExperienceAnalyzerAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_base = Path.home() / ".codex" / "memories" / "agentcli-analyzer-authority-tests"
        self.fixture_base.mkdir(parents=True, exist_ok=True)
        self.fixture_root = self.fixture_base / f"t-{uuid.uuid4().hex[:12]}"
        self.fixture_root.mkdir()
        self.run_dir = self.fixture_root / ".AgentCLI" / "agent_runs" / "20260503-125328"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: shutil.rmtree(self.fixture_root, ignore_errors=True))

    def _execute(self) -> dict[str, object]:
        return execute_analyzer(
            self.run_dir,
            run_id="20260503-125328",
            summary="High-confidence lessons were recorded for operator review only.",
            task_lessons=[
                {
                    "task_id": "T13",
                    "kind": "merge",
                    "severity": "high",
                    "confidence": 0.99,
                    "lesson": "Even high-confidence lessons remain advisory and cannot approve a merge.",
                    "evidence": ["tasks/T13/attempt_00/validation.json"],
                }
            ],
            validation_lessons=[
                {
                    "task_id": "T13",
                    "kind": "validation",
                    "severity": "high",
                    "confidence": 0.97,
                    "lesson": "Skipped or missing validation still requires deterministic gate outcomes.",
                    "evidence": ["pr_queue/packet-T13.json"],
                }
            ],
            pm_hints=["Do not treat lessons as permission to mark GOALS complete."],
            merge_hints=["Manual review still decides merge approval."],
            operator_actions=["Review the advisory report before any human approval."],
        )

    def test_execute_analyzer_writes_advisory_authority_metadata(self) -> None:
        result = self._execute()

        self.assertTrue(result["ok"])
        self.assertEqual(ANALYZER_AUTHORITY, result["authority"]["level"])

        summary_path = self.run_dir / ANALYZER_SUMMARY_FILENAME
        report_path = self.run_dir / ANALYZER_REPORT_FILENAME
        updates_path = self.run_dir / EXPERIENCE_UPDATES_FILENAME

        self.assertTrue(summary_path.exists())
        self.assertTrue(report_path.exists())
        self.assertTrue(updates_path.exists())

        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(ANALYZER_AUTHORITY, payload["authority"]["level"])
        self.assertFalse(payload["authority"]["can_mark_goals_complete"])
        self.assertFalse(payload["authority"]["can_approve_merge"])
        self.assertFalse(payload["authority"]["can_mutate_source"])
        self.assertFalse(payload["authority"]["can_bypass_validation_gates"])
        self.assertIn("goals_save_or_auto_check", payload["authority"]["forbidden_actions"])
        self.assertIn("source_code_mutation", payload["authority"]["forbidden_actions"])

        report_text = report_path.read_text(encoding="utf-8")
        self.assertIn("Authority: advisory only", report_text)
        self.assertIn("cannot mark GOALS complete, approve merges, mutate source code", report_text)

        updates_lines = [line for line in updates_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(2, len(updates_lines))
        first_record = json.loads(updates_lines[0])
        self.assertEqual(ANALYZER_AUTHORITY, first_record["authority"])

    def test_execute_analyzer_cannot_call_forbidden_authority_apis(self) -> None:
        def _forbidden(*args: object, **kwargs: object) -> None:
            raise AssertionError("Analyzer attempted to call a forbidden authority API.")

        with (
            patch("agent_runner.goals.update_goals_checkboxes", side_effect=_forbidden),
            patch("agent_runner.goals.parse_and_append_refreshed_goals", side_effect=_forbidden),
            patch("agent_runner.pr_queue.merge_review_packet", side_effect=_forbidden),
            patch("agent_runner.gitops.apply_patch_to_repo", side_effect=_forbidden),
            patch("agent_runner.gitops.apply_pending_worktree_merge", side_effect=_forbidden),
            patch("agent_runner.gates.classify_pr_queue_validation_status", side_effect=_forbidden),
        ):
            result = self._execute()

        self.assertTrue(result["ok"])
        payload = json.loads((self.run_dir / ANALYZER_SUMMARY_FILENAME).read_text(encoding="utf-8"))
        self.assertEqual(0.99, payload["task_lessons"][0]["confidence"])
        self.assertEqual(0.97, payload["validation_lessons"][0]["confidence"])
        self.assertEqual(ANALYZER_AUTHORITY, payload["authority"]["level"])


if __name__ == "__main__":
    unittest.main()
