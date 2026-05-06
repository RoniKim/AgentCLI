from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from agent_runner.web_history_snapshot import WEB_HISTORY_SNAPSHOT_JSON, write_final_web_history_snapshot

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


class WebHistorySnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        scratch = ROOT / ".test-scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        self._tmp = scratch / f"web-history-snapshot-{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)
        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "run-1"
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def test_final_web_history_snapshot_is_lightweight_and_redacted(self) -> None:
        secret = "RAW_SECRET_MARKER_20260506"
        _write(
            self.repo / ".doc" / "GOALS.md",
            f"# Goals\n\n## P0\n- [x] Finish public item\n\n## P1\n- [ ] Hidden detail {secret}\n",
        )
        _write(
            self.run_dir / "BACKLOG.json",
            json.dumps(
                {
                    "tasks": [
                        {"id": "T1", "title": "Done", "prompt": f"raw prompt {secret}"},
                        {"id": "T2", "title": "Pending", "prompt": "nope"},
                    ]
                },
                ensure_ascii=False,
            )
            + "\n",
        )
        _write(self.run_dir / "STATE.json", json.dumps({"done": ["T1"], "failed": []}) + "\n")
        _write(self.run_dir / "cycle_summary.log", f"raw log {secret}\n")
        _write(self.run_dir / "run_summary.json", json.dumps({"branch": "main", "cycles": [{"cycle": 0}], "final": {"rc": 0, "reason": "ok"}}) + "\n")
        _write(self.run_dir / "last_run_summary.json", json.dumps({"tasks_total": 2, "duration_seconds": 12}) + "\n")
        _write(self.run_dir / "FINAL_RUN_REPORT.json", json.dumps({"status": "ok", "summary": "Done summary"}) + "\n")

        snapshot = write_final_web_history_snapshot(self.repo, self.run_dir)
        snapshot_path = self.run_dir / WEB_HISTORY_SNAPSHOT_JSON
        self.assertTrue(snapshot_path.exists())
        persisted = json.loads(snapshot_path.read_text(encoding="utf-8"))
        persisted_text = json.dumps(persisted, ensure_ascii=False)

        self.assertEqual("agentcli.final_web_history_snapshot.v1", snapshot["schema"])
        self.assertTrue(persisted["redacted"])
        self.assertEqual("run-1", persisted["run"]["id"])
        self.assertEqual(1, persisted["tasks"]["done"])
        self.assertEqual(2, persisted["tasks"]["total"])
        self.assertEqual(1, persisted["goals"]["p0_done"])
        self.assertNotIn(secret, persisted_text)
        self.assertNotIn("raw_text", persisted_text)
        self.assertNotIn("rawText", persisted_text)
        self.assertNotIn("cycle_summary.log", persisted_text)

    def test_history_item_surfaces_persisted_final_snapshot_for_replay(self) -> None:
        from agent_runner.web import _history_item

        _write(self.repo / ".doc" / "GOALS.md", "# Goals\n\n## P0\n- [x] Done\n")
        _write(self.run_dir / "BACKLOG.json", json.dumps({"tasks": [{"id": "T1", "title": "Done"}]}) + "\n")
        _write(self.run_dir / "STATE.json", json.dumps({"done": ["T1"], "failed": []}) + "\n")
        _write(self.run_dir / "run_summary.json", json.dumps({"branch": "main", "cycles": [], "final": {"rc": 0, "reason": "ok"}}) + "\n")
        write_final_web_history_snapshot(self.repo, self.run_dir)

        item = _history_item(self.repo, self.run_dir, branch="main")
        self.assertIn("webHistorySnapshot", item)
        self.assertEqual("agentcli.final_web_history_snapshot.v1", item["webHistorySnapshot"]["schema"])
        self.assertEqual(
            (self.run_dir / WEB_HISTORY_SNAPSHOT_JSON).as_posix(),
            item["reportArtifacts"]["webHistorySnapshotJson"],
        )

    def test_codex_and_claude_finalize_paths_write_final_web_history_snapshot(self) -> None:
        for relative in ("agent_runner/cycle.py", "agent_runner/backends/claudecode.py"):
            source = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn("write_final_web_history_snapshot(", source, relative)
            self.assertIn("Failed to write final web history snapshot", source, relative)


if __name__ == "__main__":
    unittest.main()
