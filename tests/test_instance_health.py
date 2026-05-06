from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runner.instance_health import build_instance_health


class InstanceHealthTests(unittest.TestCase):
    def test_malformed_handle_diagnostics_promote_instance_health_warning(self) -> None:
        with patch(
            "agent_runner.instance_health.process_guard_state",
            return_value={
                "initialized": True,
                "job_object_active": False,
                "stop_path_configured": True,
                "tracked_pid_count": 0,
                "current_pid": 123,
                "platform": "win32",
            },
        ), patch(
            "agent_runner.instance_health._tracked_children_payload",
            return_value={"items": [], "pids": [], "summary": {"total": 0, "alive": 0}},
        ), patch(
            "agent_runner.instance_health._windows_handle_diagnostics",
            return_value={
                "status": "malformed",
                "warnings": [],
                "errors": [{"line": 1, "message": "invalid JSON"}],
                "summary": {"healthy": False, "warning_count": 0},
            },
        ), patch(
            "agent_runner.instance_health._collect_lock_diagnostics",
            return_value={"status": "ok", "summary": {"total": 0, "active": 0, "stale": 0, "unknown": 0}, "items": []},
        ), patch(
            "agent_runner.instance_health._stale_artifact_risks",
            return_value={"status": "ok", "summary": {"blockers": 0, "warnings": 0}},
        ):
            payload = build_instance_health(ROOT)

        self.assertEqual("warning", payload["status"])
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(payload["summary"]["handleWarnings"], 1)
        self.assertGreaterEqual(payload["summary"]["handle_warnings"], 1)


if __name__ == "__main__":
    unittest.main()
