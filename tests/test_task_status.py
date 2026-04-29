import unittest

from agent_runner.task_status import (
    TASK_STATUS_BLOCKED_ENV,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_REGRESSION_FAILED,
    TASK_STATUS_REVIEW_REQUIRED,
    TASK_STATUS_TEST_CONTRACT_CHANGED,
    classify_task_failure,
    is_auto_merge_allowed,
    is_auto_retry_allowed,
)


class TaskStatusClassificationTests(unittest.TestCase):
    def test_completed_is_only_auto_merge_status(self) -> None:
        self.assertEqual(TASK_STATUS_COMPLETED, classify_task_failure("completed"))
        self.assertTrue(is_auto_merge_allowed(TASK_STATUS_COMPLETED))
        self.assertFalse(is_auto_merge_allowed(TASK_STATUS_REVIEW_REQUIRED))

    def test_empty_reason_requires_manual_review(self) -> None:
        self.assertEqual(TASK_STATUS_REVIEW_REQUIRED, classify_task_failure(""))

    def test_empty_reason_can_be_explicitly_treated_as_completed(self) -> None:
        self.assertEqual(TASK_STATUS_COMPLETED, classify_task_failure("", treat_empty_as_completed=True))

    def test_environment_failures_do_not_auto_retry(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[
                {
                    "kind": "test",
                    "summary": "ModuleNotFoundError: No module named 'playwright'",
                }
            ],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)
        self.assertFalse(is_auto_retry_allowed(status))

    def test_ui_contract_failures_require_contract_review(self) -> None:
        status = classify_task_failure(
            "fast_regression_failed",
            validations=[
                {
                    "gate": "fast_web_worktree_regression",
                    "failure_summary": "Playwright locator expected button to_be_visible by accessible name",
                }
            ],
        )
        self.assertEqual(TASK_STATUS_TEST_CONTRACT_CHANGED, status)
        self.assertFalse(is_auto_retry_allowed(status))

    def test_broad_regression_defaults_to_manual_review(self) -> None:
        status = classify_task_failure(
            "fast_regression_failed",
            validations=[{"summary": "FAILED tests/test_integration.py::test_slow_path"}],
        )
        self.assertEqual(TASK_STATUS_REVIEW_REQUIRED, status)

    def test_compiler_errors_are_regression_failures(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "Program.cs(10,5): error CS1002: ; expected"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)
        self.assertTrue(is_auto_retry_allowed(status))

    def test_dependency_reasons_are_blocked_environment(self) -> None:
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, classify_task_failure("needs_dependency"))
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, classify_task_failure("blocked_dependency"))

    def test_multilanguage_missing_tool_is_blocked_environment(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "mvn: command not found"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)

    def test_multilanguage_compile_error_is_regression(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "error: could not compile `agent-cli` due to previous error"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_browser_contract_keywords_are_not_limited_to_playwright(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "Cypress expected locator role button by accessible name"}],
        )
        self.assertEqual(TASK_STATUS_TEST_CONTRACT_CHANGED, status)


class MultiLanguageDependencyResolutionTests(unittest.TestCase):
    """T4: dependency resolution failures across non-Python toolchains."""

    def test_maven_resolution_failure_is_blocked_env(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "[ERROR] Could not resolve dependencies for project com.example:app"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)
        self.assertFalse(is_auto_retry_allowed(status))

    def test_gradle_resolution_failure_is_blocked_env(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "Could not find org.example:lib:1.0.0 in repositories"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)

    def test_nuget_missing_package_is_blocked_env(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "error NU1101: Unable to find package Newtonsoft.Json"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)

    def test_cargo_download_failure_is_blocked_env(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "error: failed to download `serde v1.0.0`"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)

    def test_go_modules_missing_package_is_blocked_env(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "cannot find module providing package github.com/example/foo"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)

    def test_docker_pull_denied_is_blocked_env(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "pull access denied for private/image, repository does not exist"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)

    def test_node_missing_module_is_blocked_env(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "Error: Cannot find module 'express' from /app/server.js"}],
        )
        self.assertEqual(TASK_STATUS_BLOCKED_ENV, status)


class MultiLanguageRegressionPatternTests(unittest.TestCase):
    """T4: real regression keywords across non-Python toolchains."""

    def test_java_null_pointer_exception_is_regression(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "java.lang.NullPointerException at com.example.App.main(App.java:42)"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)
        self.assertTrue(is_auto_retry_allowed(status))

    def test_java_package_does_not_exist_is_regression(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "App.java:3: error: package com.unknown does not exist"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_kotlin_null_pointer_exception_is_regression(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "kotlin.KotlinNullPointerException raised at MainActivity.kt:88"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_go_runtime_error_is_regression(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "panic: runtime error: index out of range [3] with length 1\\ngoroutine 5 [running]:"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_android_gradle_task_failed_is_regression(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "> Task :app:compileDebugJavaWithJavac FAILED"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_android_fatal_exception_is_regression(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "FATAL EXCEPTION: main android.runtime.JavaProxyThrowable"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_node_unhandled_promise_rejection_is_regression(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "UnhandledPromiseRejection: Error at Object.<anonymous>"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_large_validation_text_is_bounded_and_still_classified(self) -> None:
        status = classify_task_failure(
            "test_failed",
            validations=[{"summary": "panic: runtime error " + ("x" * 200000)}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)

    def test_swift_cannot_find_identifier_is_regression(self) -> None:
        status = classify_task_failure(
            "build_failed",
            validations=[{"summary": "ContentView.swift:12:5: error: cannot find 'fooBarBaz' in scope"}],
        )
        self.assertEqual(TASK_STATUS_REGRESSION_FAILED, status)


if __name__ == "__main__":
    unittest.main()
