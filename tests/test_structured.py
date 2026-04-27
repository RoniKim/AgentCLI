from __future__ import annotations

import unittest

from agent_runner.structured import parse_pm_output, parse_pm_output_with_errors


class StructuredOutputTests(unittest.TestCase):
    def test_pm_parser_rejects_codex_error_payload(self) -> None:
        raw = (
            '{"type":"error","status":400,'
            '"error":{"type":"invalid_request_error","message":"model is not supported"}}'
        )

        self.assertIsNone(parse_pm_output(raw, kind_hint="bootstrap"))
        parsed, missing, type_errors = parse_pm_output_with_errors(raw, kind_hint="bootstrap")

        self.assertIsNone(parsed)
        self.assertTrue(any(str(item).startswith("<model_error") for item in missing))
        self.assertEqual([], type_errors)


if __name__ == "__main__":
    unittest.main()

