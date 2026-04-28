import argparse
import unittest

from agent_runner.cli import DEFAULTS, _merge_effective


class CliRetryConfigTests(unittest.TestCase):
    def test_legacy_test_failed_escalation_also_retries_fast_regression(self) -> None:
        effective = _merge_effective(
            DEFAULTS,
            {
                "dev_escalate_on": ["no_diff", "build_failed", "test_failed", "no_commits"],
            },
            argparse.Namespace(),
        )

        self.assertIn("test_failed", effective["dev_escalate_on"])
        self.assertIn("fast_regression_failed", effective["dev_escalate_on"])


if __name__ == "__main__":
    unittest.main()
