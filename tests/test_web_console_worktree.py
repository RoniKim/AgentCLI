from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
import unittest
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


WEB_CONSOLE = ROOT / "web_console"


from agent_runner.web import build_snapshot


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _run_adapter_harness(fixtures: list[dict[str, object]]) -> list[dict[str, object]]:
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    script = textwrap.dedent(
        """
        const fs = require('fs');
        const vm = require('vm');
        const sourcePath = __SOURCE_PATH__;
        const source = fs.readFileSync(sourcePath, 'utf8');
        const root = { innerHTML: '' };
        const document = {
          title: '',
          body: {
            appendChild() {},
            removeChild() {},
          },
          createElement(tag) {
            return {
              tagName: String(tag).toUpperCase(),
              style: {},
              value: '',
              setAttribute() {},
              select() {},
              focus() {},
              setSelectionRange() {},
            };
          },
          execCommand() { return true; },
          getElementById() { return root; },
          addEventListener() {},
          querySelector() { return null; },
        };
        const context = {
          console,
          JSON,
          Date,
          Math,
          Number,
          String,
          Boolean,
          Array,
          Object,
          RegExp,
          Error,
          Promise,
          setTimeout() { return 1; },
          clearTimeout() {},
          setInterval() { return 1; },
          clearInterval() {},
          fetch() { throw new Error('fetch should not run during adapter import'); },
          navigator: { clipboard: { writeText() { return Promise.resolve(); } } },
          history: { replaceState() {} },
          location: { hash: '' },
          localStorage: {
            _data: Object.create(null),
            getItem(key) {
              return Object.prototype.hasOwnProperty.call(this._data, key) ? this._data[key] : null;
            },
            setItem(key, value) {
              this._data[key] = String(value);
            },
            removeItem(key) {
              delete this._data[key];
            },
          },
          document,
          addEventListener() {},
          removeEventListener() {},
        };
        context.window = context;
        context.globalThis = context;
        context.__AGENTCLI_SKIP_BOOTSTRAP__ = true;
        vm.runInNewContext(source, context, { filename: sourcePath });
        const adapters = context.__AGENTCLI_ADAPTERS__;
        if (!adapters) {
          throw new Error('Missing __AGENTCLI_ADAPTERS__ export');
        }
        const fixtures = __FIXTURES__;
        const results = fixtures.map((fixture) => adapters.normalizeSnapshot(fixture));
        process.stdout.write(JSON.stringify(results));
        """
    ).replace("__SOURCE_PATH__", json.dumps(str(WEB_CONSOLE / "app.js"))).replace(
        "__FIXTURES__", json.dumps(fixtures, ensure_ascii=False)
    )
    completed = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return json.loads(completed.stdout)


class WorktreeReviewSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_root = Path.home() / ".codex" / "memories" / "agentcli-web-console-tests"
        self._tmp_root.mkdir(parents=True, exist_ok=True)
        self._tmp = self._tmp_root / f"worktree-{uuid.uuid4().hex}"
        self._tmp.mkdir(parents=True, exist_ok=False)
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

        self.repo = self._tmp / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260426-120000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.worktree_dir = self._tmp / "worktree"
        self.worktree_dir.mkdir(parents=True, exist_ok=True)
        self.patch_path = self.run_dir / "worktree.patch"
        self.pending_path = self.run_dir / "WORKTREE_MERGE_PENDING.json"

    def _build_snapshot(self) -> dict[str, object]:
        return build_snapshot(self.repo)

    def test_no_pending_file_returns_read_only_empty_state(self) -> None:
        snapshot = self._build_snapshot()
        normalized = _run_adapter_harness([snapshot])[0]
        worktree = snapshot["worktree"]

        self.assertEqual("none", worktree["status"])
        self.assertFalse(worktree["reviewRequired"])
        self.assertEqual(self.repo.as_posix(), worktree["sourceRepo"])
        self.assertEqual(self.run_dir.as_posix(), worktree["runDir"])
        self.assertEqual("HEAD", worktree["sourceBranch"])
        self.assertEqual("", worktree["baseRef"])
        self.assertEqual("", worktree["headRef"])
        self.assertEqual("", worktree["worktreeDir"])
        self.assertEqual("", worktree["patchPath"])
        self.assertEqual("", worktree["pendingFile"])
        self.assertEqual(0, worktree["runnerRc"])
        self.assertEqual("No pending worktree merge.", worktree["reviewRequiredMessage"])
        self.assertEqual("empty", normalized["sectionState"]["worktree"]["status"])

    def test_valid_pending_file_surfaces_review_required_fields(self) -> None:
        _write(
            self.patch_path,
            "\n".join(
                [
                    "diff --git a/src/app.py b/src/app.py",
                    "--- a/src/app.py",
                    "+++ b/src/app.py",
                    "@@ -1 +1 @@",
                    "-old",
                    "+new",
                    "",
                ]
            ),
        )
        _write(
            self.pending_path,
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "created_at": "2026-04-26T12:02:00",
                    "source_repo": self.repo.as_posix(),
                    "run_dir": self.run_dir.as_posix(),
                    "worktree_dir": self.worktree_dir.as_posix(),
                    "patch_path": self.patch_path.as_posix(),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )

        snapshot = self._build_snapshot()
        normalized = _run_adapter_harness([snapshot])[0]
        worktree = snapshot["worktree"]

        self.assertEqual("pending review", worktree["status"])
        self.assertTrue(worktree["reviewRequired"])
        self.assertEqual(self.repo.as_posix(), worktree["sourceRepo"])
        self.assertEqual(self.run_dir.as_posix(), worktree["runDir"])
        self.assertEqual(self.worktree_dir.as_posix(), worktree["worktreeDir"])
        self.assertEqual(self.patch_path.as_posix(), worktree["patchPath"])
        self.assertEqual("main", worktree["baseRef"])
        self.assertEqual("abc12345", worktree["headRef"])
        self.assertEqual(0, worktree["runnerRc"])
        self.assertIn("merge-worktree", worktree["reviewRequiredMessage"])
        self.assertIn("discard-worktree", worktree["reviewRequiredMessage"])
        self.assertGreaterEqual(len(worktree["changedFiles"]), 1)
        self.assertEqual("src/app.py", worktree["changedFiles"][0]["path"])
        self.assertEqual("ready", normalized["sectionState"]["worktree"]["status"])

    def test_malformed_pending_file_returns_error_state(self) -> None:
        _write(self.pending_path, "{ not-json }\n")

        snapshot = self._build_snapshot()
        normalized = _run_adapter_harness([snapshot])[0]
        worktree = snapshot["worktree"]

        self.assertEqual("error", worktree["status"])
        self.assertTrue(worktree["reviewRequired"])
        self.assertEqual(self.repo.as_posix(), worktree["sourceRepo"])
        self.assertEqual(self.run_dir.as_posix(), worktree["runDir"])
        self.assertEqual("", worktree["worktreeDir"])
        self.assertEqual("", worktree["patchPath"])
        self.assertEqual("", worktree["baseRef"])
        self.assertEqual("", worktree["headRef"])
        self.assertEqual(self.pending_path.as_posix(), worktree["pendingFile"])
        self.assertEqual([], worktree["changedFiles"])
        self.assertIn("malformed", worktree["reviewRequiredMessage"].lower())
        self.assertEqual("error", normalized["sectionState"]["worktree"]["status"])


if __name__ == "__main__":
    unittest.main()
