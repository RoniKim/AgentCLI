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
        [node, "-"],
        input=script,
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

    def _clear_worktree_artifacts(self) -> None:
        for relative in [
            "WORKTREE_MERGE_PENDING.json",
            "WORKTREE_MERGE_APPLIED.json",
            "WORKTREE_MERGE_DISCARDED.json",
            "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
            "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json",
            "WORKTREE_APPLY_FAILURE.md",
            "WORKTREE_PATCH_NOT_APPLIED.md",
            "WORKTREE_NOT_APPLIED.md",
            "WORKTREE_MERGE_PENDING.md",
            "worktree.patch",
        ]:
            path = self.run_dir / relative
            if path.exists():
                path.unlink()
        central_marker = self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json"
        if central_marker.exists():
            central_marker.unlink()

    def _write_status_artifact(self, relative: str, payload: dict[str, object]) -> Path:
        path = self.run_dir / relative
        _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return path

    def _write_patch(self) -> Path:
        text = "\n".join(
            [
                "diff --git a/agent_runner/web.py b/agent_runner/web.py",
                "--- a/agent_runner/web.py",
                "+++ b/agent_runner/web.py",
                "@@ -1 +1 @@",
                "-old",
                "+new",
                "",
            ]
        )
        _write(self.patch_path, text)
        return self.patch_path

    def test_no_pending_file_returns_read_only_empty_state(self) -> None:
        snapshot = self._build_snapshot()
        normalized = _run_adapter_harness([snapshot])[0]
        worktree = snapshot["worktree"]

        self.assertIsNone(snapshot["latest_run_dir"])
        self.assertEqual("idle", snapshot["active_run"]["status"])
        self.assertEqual("idle", snapshot["active_run"]["stage"])
        self.assertEqual(0, snapshot["active_run"]["iteration"])
        self.assertEqual("none", worktree["status"])
        self.assertFalse(worktree["reviewRequired"])
        self.assertEqual(self.repo.as_posix(), worktree["sourceRepo"])
        self.assertEqual("", worktree["runDir"])
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
        self.assertEqual(self.pending_path.as_posix(), worktree["statusFile"])
        self.assertEqual(self.worktree_dir.as_posix(), worktree["cleanupPath"])
        self.assertEqual("main", worktree["baseRef"])
        self.assertEqual("abc12345", worktree["headRef"])
        self.assertEqual(0, worktree["runnerRc"])
        self.assertEqual("pending", worktree["cleanupState"])
        self.assertEqual("Cleanup has not run yet.", worktree["cleanupMessage"])
        self.assertIn("merge-worktree", worktree["reviewRequiredMessage"])
        self.assertIn("discard-worktree", worktree["reviewRequiredMessage"])
        self.assertIn(self.patch_path.as_posix(), worktree["reviewRequiredMessage"])
        self.assertGreaterEqual(len(worktree["changedFiles"]), 1)
        self.assertEqual("src/app.py", worktree["changedFiles"][0]["path"])
        self.assertEqual("partial", normalized["sectionState"]["worktree"]["status"])

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

    def test_snapshot_and_adapter_cover_terminal_and_cleanup_failed_states(self) -> None:
        patch_text = "\n".join(
            [
                "diff --git a/agent_runner/web.py b/agent_runner/web.py",
                "--- a/agent_runner/web.py",
                "+++ b/agent_runner/web.py",
                "@@ -1 +1 @@",
                "-old",
                "+new",
                "",
            ]
        )

        def write_patch() -> None:
            _write(self.patch_path, patch_text)

        def write_artifact(name: str, status: str, *, cleanup_message: str = "") -> None:
            payload: dict[str, object] = {
                "schema_version": 1,
                "status": status,
                "created_at": "2026-04-26T12:10:00",
                "source_repo": self.repo.as_posix(),
                "run_dir": self.run_dir.as_posix(),
                "worktree_dir": self.worktree_dir.as_posix(),
                "patch_path": self.patch_path.as_posix(),
                "base_ref": "main",
                "head_ref": "abc12345",
                "last_rc": 0,
            }
            if cleanup_message:
                payload["cleanup_message"] = cleanup_message
            _write(self.run_dir / name, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

        def assert_snapshot(
            *,
            label: str,
            expected_status: str,
            expected_section_status: str,
            expected_review_required: bool,
            expected_cleanup_state: str,
            expected_status_file: str,
            expected_changed_files: bool,
        ) -> dict[str, object]:
            snapshot = self._build_snapshot()
            normalized = _run_adapter_harness([snapshot])[0]
            worktree = snapshot["worktree"]
            adapted = normalized["worktreeMerge"]

            self.assertEqual(expected_status, worktree["status"], msg=label)
            self.assertEqual(expected_status, adapted["status"], msg=label)
            self.assertEqual(expected_section_status, normalized["sectionState"]["worktree"]["status"], msg=label)
            self.assertEqual(expected_review_required, worktree["reviewRequired"], msg=label)
            self.assertEqual(expected_review_required, adapted["reviewRequired"], msg=label)
            self.assertEqual(expected_cleanup_state, worktree["cleanupState"], msg=label)
            self.assertEqual(expected_cleanup_state, adapted["cleanupState"], msg=label)
            self.assertEqual(expected_status_file, worktree["statusFile"], msg=label)
            self.assertEqual(expected_status_file, adapted["statusFile"], msg=label)
            self.assertEqual(expected_changed_files, bool(worktree["changedFiles"]), msg=label)
            self.assertEqual(expected_changed_files, bool(adapted["changedFiles"]), msg=label)
            return snapshot

        with self.subTest("no-pending"):
            self._clear_worktree_artifacts()
            snapshot = assert_snapshot(
                label="no-pending",
                expected_status="none",
                expected_section_status="empty",
                expected_review_required=False,
                expected_cleanup_state="none",
                expected_status_file="",
                expected_changed_files=False,
            )
            self.assertEqual("No pending worktree merge.", snapshot["worktree"]["reviewRequiredMessage"])

        with self.subTest("malformed"):
            self._clear_worktree_artifacts()
            _write(self.pending_path, "{ not-json }\n")
            snapshot = assert_snapshot(
                label="malformed",
                expected_status="error",
                expected_section_status="error",
                expected_review_required=True,
                expected_cleanup_state="none",
                expected_status_file=self.pending_path.as_posix(),
                expected_changed_files=False,
            )
            self.assertIn("malformed", snapshot["worktree"]["reviewRequiredMessage"].lower())

        for status_name, section_status, cleanup_state in [
            ("applied", "ready", "done"),
            ("discarded", "ready", "done"),
        ]:
            with self.subTest(status_name):
                self._clear_worktree_artifacts()
                write_patch()
                write_artifact(
                    "WORKTREE_MERGE_APPLIED.json" if status_name == "applied" else "WORKTREE_MERGE_DISCARDED.json",
                    status_name,
                )
                snapshot = assert_snapshot(
                    label=status_name,
                    expected_status=status_name,
                    expected_section_status=section_status,
                    expected_review_required=False,
                    expected_cleanup_state=cleanup_state,
                    expected_status_file=(self.run_dir / ("WORKTREE_MERGE_APPLIED.json" if status_name == "applied" else "WORKTREE_MERGE_DISCARDED.json")).as_posix(),
                    expected_changed_files=True,
                )
                if status_name == "applied":
                    self.assertEqual("Patch applied to the source repository.", snapshot["worktree"]["reviewRequiredMessage"])
                else:
                    self.assertEqual("Pending worktree result discarded.", snapshot["worktree"]["reviewRequiredMessage"])

        for status_name, artifact_name in [
            ("applied_cleanup_failed", "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json"),
            ("discard_cleanup_failed", "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json"),
        ]:
            with self.subTest(status_name):
                self._clear_worktree_artifacts()
                write_patch()
                write_artifact(artifact_name, status_name, cleanup_message=f"{status_name} cleanup failed")
                snapshot = assert_snapshot(
                    label=status_name,
                    expected_status=status_name,
                    expected_section_status="partial",
                    expected_review_required=True,
                    expected_cleanup_state="failed",
                    expected_status_file=(self.run_dir / artifact_name).as_posix(),
                    expected_changed_files=True,
                )
                self.assertIn("cleanup failed", snapshot["worktree"]["reviewRequiredMessage"].lower())
                self.assertEqual(self.worktree_dir.as_posix(), snapshot["worktree"]["cleanupPath"])

        with self.subTest("stale-central-marker"):
            self._clear_worktree_artifacts()
            _write(
                self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "pending",
                        "created_at": "2026-04-26T12:10:00",
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
            write_patch()
            snapshot = assert_snapshot(
                label="stale-central-marker",
                expected_status="error",
                expected_section_status="error",
                expected_review_required=True,
                expected_cleanup_state="none",
                expected_status_file=(self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json").as_posix(),
                expected_changed_files=False,
            )
            self.assertIn("stale", snapshot["worktree"]["reviewRequiredMessage"].lower())


if __name__ == "__main__":
    unittest.main()
