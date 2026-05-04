import json
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.task_status import TASK_STATUS_COMPLETED
from agent_runner.validation_artifacts import write_task_validation_artifacts


class ValidationArtifactsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path.cwd() / ".test-scratch" / f"validation-artifacts-{uuid.uuid4().hex}"
        self.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _attempt_dir(self, name: str) -> Path:
        attempt_dir = self.root / name
        attempt_dir.mkdir(parents=True, exist_ok=True)
        return attempt_dir

    def _load_artifacts(self, attempt_dir: Path) -> tuple[dict[str, object], str]:
        payload = json.loads((attempt_dir / "validation.json").read_text(encoding="utf-8"))
        summary = (attempt_dir / "validation.txt").read_text(encoding="utf-8")
        return payload, summary

    def test_writes_passed_validation_payload_and_summary(self) -> None:
        attempt_dir = self._attempt_dir("passed")
        build_log = attempt_dir / "build.txt"
        test_log = attempt_dir / "test.txt"
        validations = [
            {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "rc": 0,
                "artifact_path": build_log.as_posix(),
                "summary": "build ok",
                "failure_summary": "",
            },
            {
                "name": "test",
                "kind": "test",
                "gate": "test",
                "rc": 0,
                "artifactPath": test_log.as_posix(),
                "summary": "test ok",
                "failureSummary": "",
            },
        ]

        artifact_path = write_task_validation_artifacts(
            attempt_dir=attempt_dir,
            task_id="T32",
            task_title="Share validation artifact writer",
            task_files=["agent_runner/cycle.py", "agent_runner/backends/claudecode.py"],
            cycle=3,
            step=2,
            attempt=0,
            validations=validations,
            status="passed",
            reason="completed",
            task_status=TASK_STATUS_COMPLETED,
            goal_ref="P0-V",
            goal_text="Keep validation artifacts shared.",
            goal_trace=["P0-V", "T32"],
        )

        payload, summary = self._load_artifacts(attempt_dir)

        self.assertEqual(artifact_path.as_posix(), payload["artifact_path"])
        self.assertEqual("passed", payload["status"])
        self.assertEqual("passed", payload["validation_status"])
        self.assertEqual("passed", payload["validationStatus"])
        self.assertEqual("completed", payload["reason"])
        self.assertEqual(TASK_STATUS_COMPLETED, payload["task_status"])
        self.assertFalse(payload["review_required"])
        self.assertFalse(payload["reviewRequired"])
        self.assertTrue(payload["auto_merge_allowed"])
        self.assertTrue(payload["autoMergeAllowed"])
        self.assertEqual("P0-V", payload["goal_ref"])
        self.assertEqual(["P0-V", "T32"], payload["goal_trace"])
        self.assertEqual("build", payload["compile_validation"]["name"])
        self.assertEqual("test", payload["testValidation"]["name"])
        self.assertEqual("", payload["failure_summary"])
        self.assertEqual("", payload["failureSummary"])
        self.assertIn("validation_status=passed", summary)
        self.assertIn("task_status=completed", summary)
        self.assertIn(f"build: rc=0 artifact={build_log.as_posix()}", summary)
        self.assertIn(f"test: rc=0 artifact={test_log.as_posix()}", summary)

    def test_writes_failed_compile_validation_payload_and_summary(self) -> None:
        attempt_dir = self._attempt_dir("build-failed")
        build_log = attempt_dir / "build.txt"
        detail = "Build validation failed."
        validations = [
            {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "rc": 1,
                "artifact_path": build_log.as_posix(),
                "summary": detail,
                "failure_summary": detail,
            }
        ]

        write_task_validation_artifacts(
            attempt_dir=attempt_dir,
            task_id="T32",
            task_title="Share validation artifact writer",
            task_files=["agent_runner/cycle.py"],
            cycle=3,
            step=2,
            attempt=0,
            validations=validations,
            status="failed",
            reason="build_failed",
            detail=detail,
        )

        payload, summary = self._load_artifacts(attempt_dir)

        self.assertEqual("failed", payload["status"])
        self.assertEqual("build_failed", payload["reason"])
        self.assertEqual(detail, payload["detail"])
        self.assertEqual(detail, payload["validationDetail"])
        self.assertEqual("regression_failed", payload["task_status"])
        self.assertEqual(validations[0], payload["compileValidation"])
        self.assertEqual({}, payload["test_validation"])
        self.assertEqual(detail, payload["failure_summary"])
        self.assertIn("validation_status=failed", summary)
        self.assertIn("reason=build_failed", summary)
        self.assertIn(f"build: rc=1 artifact={build_log.as_posix()}", summary)
        self.assertIn(f"failure_summary={detail}", summary)

    def test_writes_failed_test_validation_payload_and_summary(self) -> None:
        attempt_dir = self._attempt_dir("test-failed")
        build_log = attempt_dir / "build.txt"
        test_log = attempt_dir / "test.txt"
        detail = "AssertionError: expected 1 == 2"
        validations = [
            {
                "name": "build",
                "kind": "compile",
                "gate": "build",
                "rc": 0,
                "artifact_path": build_log.as_posix(),
                "summary": "build ok",
                "failure_summary": "",
            },
            {
                "name": "test",
                "kind": "test",
                "gate": "test",
                "rc": 1,
                "artifact_path": test_log.as_posix(),
                "summary": detail,
                "failure_summary": detail,
            },
        ]

        write_task_validation_artifacts(
            attempt_dir=attempt_dir,
            task_id="T32",
            task_title="Share validation artifact writer",
            task_files=["agent_runner/backends/claudecode.py"],
            cycle=3,
            step=2,
            attempt=1,
            validations=validations,
            status="failed",
            reason="test_failed",
            detail=detail,
        )

        payload, summary = self._load_artifacts(attempt_dir)

        self.assertEqual("failed", payload["validationStatus"])
        self.assertEqual("test_failed", payload["validationReason"])
        self.assertEqual(detail, payload["validation_detail"])
        self.assertEqual("regression_failed", payload["taskStatus"])
        self.assertEqual("test", payload["test_validation"]["name"])
        self.assertEqual(detail, payload["test_validation"]["failure_summary"])
        self.assertEqual("", payload["compile_validation"]["failure_summary"])
        self.assertIn(f"test: rc=1 artifact={test_log.as_posix()}", summary)
        self.assertIn(f"failure_summary={detail}", summary)

    def test_writes_fast_regression_suite_and_trigger_metadata(self) -> None:
        attempt_dir = self._attempt_dir("fast-regression")
        fast_log = attempt_dir / "fast_web_worktree_regression.json"
        detail = "fast_web_worktree_regression failed: tests/test_web_console_static.py"
        validations = [
            {
                "name": "fast_web_worktree_regression",
                "kind": "regression",
                "gate": "fast_web_worktree_regression",
                "rc": 1,
                "artifactPath": fast_log.as_posix(),
                "summary": detail,
                "failureSummary": detail,
                "suiteFiles": [
                    "tests/test_web_console_static.py",
                    "tests/test_worktree_manual_merge.py",
                ],
                "triggerFiles": [
                    "web_console/app.js",
                    "tests/test_web_console_static.py",
                ],
            }
        ]

        write_task_validation_artifacts(
            attempt_dir=attempt_dir,
            task_id="T32",
            task_title="Share validation artifact writer",
            task_files=["web_console/app.js"],
            cycle=3,
            step=2,
            attempt=1,
            validations=validations,
            status="failed",
            reason="fast_regression_failed",
            detail=detail,
        )

        payload, summary = self._load_artifacts(attempt_dir)

        self.assertEqual(validations[0], payload["fastRegressionValidation"])
        self.assertEqual(validations[0]["suiteFiles"], payload["selected_fast_regression_suite"])
        self.assertEqual(validations[0]["suiteFiles"], payload["selectedFastRegressionSuite"])
        self.assertEqual(validations[0]["triggerFiles"], payload["trigger_files"])
        self.assertEqual(validations[0]["triggerFiles"], payload["triggerFiles"])
        self.assertIn(
            "fast_suite=tests/test_web_console_static.py, tests/test_worktree_manual_merge.py",
            summary,
        )
        self.assertIn(
            "trigger_files=web_console/app.js, tests/test_web_console_static.py",
            summary,
        )


if __name__ == "__main__":
    unittest.main()
