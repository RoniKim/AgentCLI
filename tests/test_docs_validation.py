from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from agent_runner.docs import (
    build_docs_digest_text,
    collect_fastapi_route_inventory,
    validate_docs_digest,
    validate_advanced_features_doc,
    validate_configuration_doc,
    validate_master_index,
    validate_operations_doc,
    validate_troubleshooting_doc,
    validate_telegram_doc,
    validate_user_facing_docs,
    validate_web_console_doc,
    validate_web_console_route_inventory_coverage,
    validate_web_console_route_claims,
)


ROOT = Path(__file__).resolve().parents[1]


class DocsValidationTests(unittest.TestCase):
    def test_docs_digest_matches_real_inventory(self) -> None:
        docs_dir = ROOT / ".doc" / "Docs"
        digest_path = ROOT / ".doc" / "DOCS_DIGEST.md"

        self.assertEqual(validate_docs_digest(ROOT, docs_dir, digest_path), [])
        self.assertEqual(digest_path.read_text(encoding="utf-8"), build_docs_digest_text(ROOT, docs_dir))

    def test_master_index_paths_are_case_exact(self) -> None:
        index_path = ROOT / "docs" / "MASTER_INDEX.md"

        self.assertEqual(validate_master_index(ROOT, index_path), [])

    def test_web_console_docs_match_route_inventory(self) -> None:
        web_doc = ROOT / "docs" / "WEB_CONSOLE.md"
        route_inventory = collect_fastapi_route_inventory(ROOT)

        self.assertEqual(validate_web_console_doc(ROOT, web_doc.read_text(encoding="utf-8")), [])
        self.assertEqual(validate_web_console_route_claims(web_doc.read_text(encoding="utf-8"), route_inventory), [])

    def test_user_facing_docs_match_live_contracts(self) -> None:
        self.assertEqual(validate_user_facing_docs(ROOT), [])

    def test_configuration_doc_rejects_stale_model_defaults(self) -> None:
        text = (ROOT / "docs" / "CONFIGURATION.md").read_text(encoding="utf-8")
        stale = text.replace("| `pm_model` | `gpt-5.5` | PM model |", "| `pm_model` | `gpt-4o` | PM model |", 1)

        errors = validate_configuration_doc(stale)

        self.assertTrue(any("pm_model" in error for error in errors), errors)

    def test_telegram_doc_rejects_stale_instance_name_and_event_description(self) -> None:
        text = (ROOT / "docs" / "TELEGRAM.md").read_text(encoding="utf-8")
        stale_instance = text.replace('"instance_name": ""', '"instance_name": "home-pc-main"', 1)
        stale_event = text.replace(
            "| `project_complete` | 프로젝트 완료 (`goals_completion_level` 달성, unresolved failures == 0) | ✅ | `project_complete` |",
            "| `project_complete` | 프로젝트 완료 (Goals P0 전체 달성) | ✅ | `project_complete` |",
            1,
        )

        instance_errors = validate_telegram_doc(stale_instance)
        event_errors = validate_telegram_doc(stale_event)

        self.assertTrue(any("instance_name" in error for error in instance_errors), instance_errors)
        self.assertTrue(any("project_complete description" in error for error in event_errors), event_errors)

    def test_operations_doc_rejects_stale_cli_flag_stop_reason_and_worktree_mode(self) -> None:
        text = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        stale_flag = text.replace("`--allow-no-diff` / `--no-allow-no-diff`", "`--allow-everything`", 1)
        stale_reason = text.replace(
            "| `quota_exhausted` | API/quota 한도가 완전히 소진된 경우 |",
            "| `quota_exceeded` | API/quota 한도가 완전히 소진된 경우 |",
            1,
        )
        stale_worktree = text.replace("`manual`", "`merge`", 1)

        flag_errors = validate_operations_doc(stale_flag)
        reason_errors = validate_operations_doc(stale_reason)
        worktree_errors = validate_operations_doc(stale_worktree)

        self.assertTrue(any("stale CLI flag" in error for error in flag_errors), flag_errors)
        self.assertTrue(any("stale stop reason" in error for error in reason_errors), reason_errors)
        self.assertTrue(any("worktree merge mode" in error for error in worktree_errors), worktree_errors)

    def test_web_docs_reject_stale_multi_repo_operating_model_claims(self) -> None:
        web_text = (ROOT / "docs" / "WEB_CONSOLE.md").read_text(encoding="utf-8")
        stale_web = web_text.replace(
            "AgentCLI Web currently runs as `one repo, one web instance`.",
            "AgentCLI Web currently runs as a `multi-repo dashboard`.",
            1,
        )
        stale_web = stale_web.replace(
            "Multi-repo dashboards are deferred future scope and belong to a later phase.",
            "Multi-repo dashboards are current scope and belong to this phase.",
            1,
        )
        web_errors = validate_web_console_doc(ROOT, stale_web)

        operations_text = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        stale_operations = operations_text.replace(
            "AgentCLI Web은 현재 `one repo, one web instance` 모델입니다.",
            "AgentCLI Web은 현재 `multi-repo dashboard` 모델입니다.",
            1,
        )
        stale_operations = stale_operations.replace(
            "- `multi-repo dashboard` scope는 later phase로 deferred 됩니다.",
            "- `multi-repo dashboard` scope는 현재 지원됩니다.",
            1,
        )
        operations_errors = validate_operations_doc(stale_operations)

        self.assertTrue(
            any("one repo, one web instance" in error or "multi-repo dashboard scope" in error for error in web_errors),
            web_errors,
        )
        self.assertTrue(
            any("one repo, one web instance" in error or "multi-repo dashboard scope" in error for error in operations_errors),
            operations_errors,
        )

    def test_shutdown_report_docs_reject_stale_writer_and_recovery_claims(self) -> None:
        advanced_text = (ROOT / "docs" / "ADVANCED_FEATURES.md").read_text(encoding="utf-8")
        stale_advanced = advanced_text.replace("write_run_report_artifacts()", "build_local_shutdown_report()")
        stale_advanced = stale_advanced.replace("write_run_report_artifacts", "build_local_shutdown_report")
        advanced_errors = validate_advanced_features_doc(stale_advanced)

        operations_text = (ROOT / "docs" / "OPERATIONS.md").read_text(encoding="utf-8")
        stale_operations = operations_text.replace(
            "then best-effort PM output may overwrite the fallback copy.",
            "then PM output always overwrites the fallback copy.",
            1,
        )
        operations_errors = validate_operations_doc(stale_operations)

        troubleshooting_text = (ROOT / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
        stale_troubleshooting = troubleshooting_text.replace(
            "PM-authored shutdown pass는 best-effort라서 실패해도 local fallback 보고서는 그대로 남음",
            "PM-authored shutdown pass는 재부팅이 유일한 회복 경로입니다.",
            1,
        )
        troubleshooting_errors = validate_troubleshooting_doc(stale_troubleshooting)

        self.assertTrue(any("write_run_report_artifacts" in error for error in advanced_errors), advanced_errors)
        self.assertTrue(any("best-effort PM overwrite behavior" in error for error in operations_errors), operations_errors)
        self.assertTrue(
            any("obsolete reboot-only recovery claim" in error for error in troubleshooting_errors),
            troubleshooting_errors,
        )

    def test_web_console_doc_rejects_stale_server_flag(self) -> None:
        text = (ROOT / "docs" / "WEB_CONSOLE.md").read_text(encoding="utf-8")
        stale = text.replace(
            "| `--trusted-network` | LAN bind를 trusted-network bind로 표시합니다 |",
            "| `--trusted-lan` | LAN bind를 trusted-network bind로 표시합니다 |",
            1,
        )

        errors = validate_web_console_doc(ROOT, stale)

        self.assertTrue(any("stale web server flag" in error for error in errors), errors)

    def test_authentication_plan_documents_lan_gates_before_auth_exists(self) -> None:
        auth_plan = (ROOT / "docs" / "AUTHENTICATION_PLAN.md").read_text(encoding="utf-8")
        web_doc = (ROOT / "docs" / "WEB_CONSOLE.md").read_text(encoding="utf-8")

        required = [
            "not an active authentication layer",
            "Confirmation phrases are UX friction only",
            "LAN requires an authenticated session",
            "CSRF is blocked",
            "MVP Acceptance Gates",
            "Non-Goals",
        ]
        for token in required:
            self.assertIn(token, auth_plan)

        self.assertIn("AUTHENTICATION_PLAN.md", web_doc)
        self.assertIn("Confirmation phrases and `--trusted-network` are not authentication.", web_doc)

    def test_web_console_doc_rejects_stale_status_and_role_order(self) -> None:
        text = (ROOT / "docs" / "WEB_CONSOLE.md").read_text(encoding="utf-8")
        stale_status = text.replace(
            "Verified on 2026-05-06 with local API, unit, and checked-in browser smoke coverage:",
            "Verified on 2026-04-26 with a local server and Playwright:\n\nThis is not yet a complete operational web runner. Treat the current implementation as an alpha shell.",
            1,
        )
        stale_roles = text.replace(
            "PM, PL, Security, Dev, QA",
            "PM, Security, Dev, QA",
            1,
        )

        status_errors = validate_web_console_doc(ROOT, stale_status)
        role_errors = validate_web_console_doc(ROOT, stale_roles)

        self.assertTrue(any("stale alpha web-runner status claim" in error for error in status_errors), status_errors)
        self.assertTrue(any("built-in role order" in error for error in role_errors), role_errors)

    def test_web_console_doc_rejects_incomplete_route_inventory_and_stale_report_export_scope(self) -> None:
        text = (ROOT / "docs" / "WEB_CONSOLE.md").read_text(encoding="utf-8")
        route_inventory = collect_fastapi_route_inventory(ROOT)
        stale_routes = text.replace("`/api/reports/export`, ", "", 1)
        stale_scope = text.replace(
            "Run History comparison, Notifications read/unread workflow, and Instance Health remain tracked",
            "Run History comparison, Notifications read/unread workflow, report export, and Instance Health remain tracked",
            1,
        )
        stale_status_section = stale_routes.split("## Current Status", 1)[1].split("## Web Server Flags", 1)[0]

        route_errors = validate_web_console_doc(ROOT, stale_routes)
        direct_route_errors = validate_web_console_route_inventory_coverage(stale_status_section, route_inventory)
        scope_errors = validate_web_console_doc(ROOT, stale_scope)

        self.assertTrue(any("/api/reports/export" in error for error in route_errors), route_errors)
        self.assertTrue(any("/api/reports/export" in error for error in direct_route_errors), direct_route_errors)
        self.assertTrue(any("stale report-export" in error for error in scope_errors), scope_errors)

    def test_case_mismatched_paths_are_rejected(self) -> None:
        temp_root = ROOT / ".test-scratch" / "docs-validation-case-mismatch"
        shutil.rmtree(temp_root, ignore_errors=True)

        try:
            repo = temp_root
            docs_dir = repo / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "Real.md").write_text("# Real\n", encoding="utf-8")

            index_path = docs_dir / "MASTER_INDEX.md"
            index_path.write_text(
                "\n".join(
                    [
                        "# Index",
                        "",
                        "## 1. 운영 가이드 (docs/, 영구)",
                        "",
                        "| 문서 | 상태 |",
                        "|------|------|",
                        "| [real.md](real.md) | ✅ OK |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            errors = validate_master_index(repo, index_path)

            self.assertTrue(any("case mismatch" in error for error in errors), errors)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_master_index_rejects_stale_update_needed_status(self) -> None:
        temp_root = ROOT / ".test-scratch" / "docs-validation-stale-status"
        shutil.rmtree(temp_root, ignore_errors=True)

        try:
            repo = temp_root
            docs_dir = repo / "docs"
            docs_dir.mkdir(parents=True)
            (docs_dir / "Real.md").write_text("# Real\n", encoding="utf-8")

            index_path = docs_dir / "MASTER_INDEX.md"
            index_path.write_text(
                "\n".join(
                    [
                        "# Index",
                        "",
                        "## 1. 운영 가이드 (docs/, 영구)",
                        "",
                        "| 문서 | 상태 |",
                        "|------|------|",
                        "| [Real.md](Real.md) | ⚠️ 업데이트 필요 |",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            errors = validate_master_index(repo, index_path)

            self.assertTrue(any("stale master-index status" in error for error in errors), errors)
        finally:
            shutil.rmtree(temp_root, ignore_errors=True)

    def test_nonexistent_route_claim_is_rejected(self) -> None:
        route_inventory = collect_fastapi_route_inventory(ROOT)
        errors = validate_web_console_route_claims("The console does not expose `/api/config/backup`.", route_inventory)

        self.assertTrue(any("/api/config/backup" in error for error in errors), errors)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
