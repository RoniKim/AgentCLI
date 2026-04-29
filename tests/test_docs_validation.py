from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from agent_runner.docs import (
    build_docs_digest_text,
    collect_fastapi_route_inventory,
    validate_docs_digest,
    validate_master_index,
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

        self.assertEqual(validate_web_console_route_claims(web_doc.read_text(encoding="utf-8"), route_inventory), [])

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

    def test_nonexistent_route_claim_is_rejected(self) -> None:
        route_inventory = collect_fastapi_route_inventory(ROOT)
        errors = validate_web_console_route_claims("The console does not expose `/api/config/backup`.", route_inventory)

        self.assertTrue(any("/api/config/backup" in error for error in errors), errors)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
