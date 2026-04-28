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
from agent_runner.gitops import scan_worktree_diagnostics, sha256_text


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


def _run_worktree_render_harness(
    snapshot: dict[str, object],
    *,
    view: str = 'worktree',
    locale: str | None = None,
    actions: list[dict[str, object]] | None = None,
) -> dict[str, str]:
    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    script = textwrap.dedent(
        """
        const fs = require('fs');
        const vm = require('vm');
        const sourcePath = __SOURCE_PATH__;
        const source = fs.readFileSync(sourcePath, 'utf8');
        const roots = {
          app: { innerHTML: '' },
          topbar: { innerHTML: '' },
          sidebar: { innerHTML: '' },
          main: {
            innerHTML: '',
            dataset: Object.create(null),
            scrollTop: 0,
            scrollHeight: 0,
          },
          'overlay-root': { innerHTML: '' },
        };
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
          getElementById(id) { return roots[id] || null; },
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
          fetch() { throw new Error('fetch should not run during render harness'); },
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
        const presetLocale = __LOCALE__;
        if (presetLocale) {
          context.localStorage._data['agentcli.console.locale.v1'] = JSON.stringify(presetLocale);
        }
        context.window = context;
        context.globalThis = context;
        context.__AGENTCLI_SKIP_BOOTSTRAP__ = true;
        vm.runInNewContext(source, context, { filename: sourcePath });
        const adapters = context.__AGENTCLI_ADAPTERS__;
        if (!adapters) {
          throw new Error('Missing __AGENTCLI_ADAPTERS__ export');
        }
        const normalized = adapters.normalizeSnapshot(__SNAPSHOT__);
        adapters.applySnapshotModel(normalized);
        adapters.setView(__VIEW__);
        const actions = __ACTIONS__;
        for (const action of actions) {
          if (!action || action.kind !== 'call') {
            throw new Error('Unsupported render harness action');
          }
          const fn = adapters[action.name];
          if (typeof fn !== 'function') {
            throw new Error(`Missing adapter function: ${action.name}`);
          }
          fn(...(Array.isArray(action.args) ? action.args : []));
        }
        adapters.renderShell({ force: true, preserveScroll: false });
        process.stdout.write(JSON.stringify({
          main: roots.main.innerHTML,
          overlay: roots['overlay-root'].innerHTML,
          title: document.title,
        }));
        """
    ).replace("__SOURCE_PATH__", json.dumps(str(WEB_CONSOLE / "app.js"))).replace(
        "__SNAPSHOT__", json.dumps(snapshot, ensure_ascii=False)
    ).replace("__VIEW__", json.dumps(view)).replace("__LOCALE__", json.dumps(locale)).replace("__ACTIONS__", json.dumps(actions or [], ensure_ascii=False))
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
        generated_root = self._tmp / ".agentcli_worktrees"
        if generated_root.exists():
            shutil.rmtree(generated_root, ignore_errors=True)

    def _seed_worktree_diagnostics_fixture(self) -> dict[str, Path]:
        self._clear_worktree_artifacts()

        generated_root = self._tmp / ".agentcli_worktrees" / self.repo.name
        active_worktree = generated_root / "active"
        cleanup_worktree = generated_root / "cleanup"
        orphaned_worktree = generated_root / "orphaned"
        stale_worktree = self._tmp / "stale-worktree"
        for worktree in (active_worktree, cleanup_worktree, orphaned_worktree):
            worktree.mkdir(parents=True, exist_ok=True)
            _write(worktree / ".git", f"gitdir: ../.git/worktrees/{worktree.name}\n")

        self._write_pending_payload(
            patch_text="diff --git a/active.txt b/active.txt\n",
            base_ref="main",
            expected_head="abc12345",
            branch="main",
            source_repo_state="clean",
            worktree_state="clean",
            head_ref="abc12345",
        )

        central_patch = self.run_dir / "central-stale.patch"
        _write(central_patch, "diff --git a/stale.txt b/stale.txt\n")
        _write(
            self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "created_at": "2026-04-26T12:03:00",
                    "source_repo": self.repo.as_posix(),
                    "run_dir": self.run_dir.as_posix(),
                    "worktree_dir": stale_worktree.as_posix(),
                    "patch_path": central_patch.as_posix(),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        central_patch.unlink()

        cleanup_patch = self.run_dir / "cleanup.patch"
        _write(cleanup_patch, "diff --git a/cleanup.txt b/cleanup.txt\n")
        self._write_status_artifact(
            "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
            {
                "schema_version": 1,
                "status": "applied_cleanup_failed",
                "created_at": "2026-04-26T12:04:00",
                "source_repo": self.repo.as_posix(),
                "run_dir": self.run_dir.as_posix(),
                "worktree_dir": cleanup_worktree.as_posix(),
                "patch_path": cleanup_patch.as_posix(),
                "cleanup_path": cleanup_worktree.as_posix(),
                "cleanup_message": "cleanup failed",
                "base_ref": "main",
                "head_ref": "abc12345",
                "last_rc": 0,
            },
        )

        return {
            "active_marker": self.pending_path,
            "central_marker": self.repo / ".AgentCLI" / "WORKTREE_MERGE_PENDING.json",
            "cleanup_artifact": self.run_dir / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
            "active_patch": self.patch_path,
            "cleanup_patch": cleanup_patch,
            "central_patch": central_patch,
            "generated_root": generated_root,
            "active_worktree": active_worktree,
            "cleanup_worktree": cleanup_worktree,
            "orphaned_worktree": orphaned_worktree,
            "stale_worktree": stale_worktree,
        }

    def _worktree_diagnostics_artifact_state(self, paths: dict[str, Path]) -> dict[str, object]:
        def read_text(path: Path) -> str | None:
            return path.read_text(encoding="utf-8") if path.exists() else None

        def list_dir(path: Path) -> list[str]:
            return sorted(child.name for child in path.iterdir()) if path.exists() else []

        return {
            "active_marker": read_text(paths["active_marker"]),
            "central_marker": read_text(paths["central_marker"]),
            "cleanup_artifact": read_text(paths["cleanup_artifact"]),
            "active_patch": read_text(paths["active_patch"]),
            "cleanup_patch": read_text(paths["cleanup_patch"]),
            "central_patch_exists": paths["central_patch"].exists(),
            "generated_root_entries": list_dir(paths["generated_root"]),
            "active_worktree_entries": list_dir(paths["active_worktree"]),
            "cleanup_worktree_entries": list_dir(paths["cleanup_worktree"]),
            "orphaned_worktree_entries": list_dir(paths["orphaned_worktree"]),
            "stale_worktree_exists": paths["stale_worktree"].exists(),
        }

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

    def _write_pending_payload(
        self,
        *,
        patch_text: str,
        base_ref: str,
        expected_head: str,
        branch: str,
        source_repo_state: str = "clean",
        worktree_state: str = "dirty",
        run_id: str | None = None,
        head_ref: str | None = None,
    ) -> dict[str, object]:
        _write(self.patch_path, patch_text)
        payload: dict[str, object] = {
            "schema_version": 1,
            "status": "pending",
            "created_at": "2026-04-26T12:02:00",
            "run_id": run_id or self.run_dir.name,
            "run_dir": self.run_dir.as_posix(),
            "source_repo": self.repo.as_posix(),
            "source_repo_root": self.repo.as_posix(),
            "branch": branch,
            "source_branch": branch,
            "expected_head": expected_head,
            "source_repo_state": source_repo_state,
            "worktree_state": worktree_state,
            "worktree_dir": self.worktree_dir.as_posix(),
            "patch_path": self.patch_path.as_posix(),
            "patch_hash": "",
            "base_ref": base_ref,
            "head_ref": head_ref or expected_head,
            "last_rc": 0,
        }
        payload["sourceRepoRoot"] = payload["source_repo_root"]
        payload["sourceRepoState"] = payload["source_repo_state"]
        payload["worktreeState"] = payload["worktree_state"]
        payload["patchHash"] = payload["patch_hash"]
        payload["runId"] = payload["run_id"]
        payload["runDir"] = payload["run_dir"]
        payload["sourceRepo"] = payload["source_repo"]
        payload["sourceBranch"] = payload["branch"]
        payload["source_repo_state"] = source_repo_state
        payload["sourceRepoState"] = source_repo_state
        payload["worktreeState"] = worktree_state
        payload["worktree_state"] = worktree_state
        payload["baseRef"] = base_ref
        payload["headRef"] = head_ref or expected_head
        payload["patchPath"] = self.patch_path.as_posix()
        payload["pendingFile"] = self.pending_path.as_posix()
        self.pending_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload

    def test_no_pending_file_returns_read_only_empty_state(self) -> None:
        snapshot = self._build_snapshot()
        normalized = _run_adapter_harness([snapshot])[0]
        worktree = snapshot["worktree"]
        diagnostics = snapshot["worktree_diagnostics"]

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
        self.assertEqual("ok", diagnostics["status"])
        self.assertTrue(diagnostics["summary"]["healthy"])
        self.assertEqual([], diagnostics["issues"])

    def test_worktree_diagnostics_contract_reports_stale_missing_cleanup_and_orphaned_cases(self) -> None:
        try:
            from agent_runner.web import create_app
            from fastapi.testclient import TestClient
        except Exception as exc:
            self.skipTest(f"FastAPI test client is unavailable: {exc}")

        client = TestClient(create_app(self.repo, web_dir=WEB_CONSOLE))

        def read_diagnostics() -> dict[str, object]:
            snapshot = self._build_snapshot()
            api_payload = client.get("/api/worktree/diagnostics").json()
            self.assertEqual(snapshot["worktree_diagnostics"]["status"], api_payload["status"])
            self.assertEqual(snapshot["worktree_diagnostics"]["summary"], api_payload["summary"])
            return api_payload

        def marker_payload() -> dict[str, object]:
            return {
                "schema_version": 1,
                "status": "pending",
                "created_at": "2026-04-26T12:03:00",
                "source_repo": self.repo.as_posix(),
                "run_dir": self.run_dir.as_posix(),
                "worktree_dir": self.worktree_dir.as_posix(),
                "patch_path": self.patch_path.as_posix(),
                "base_ref": "main",
                "head_ref": "abc12345",
                "last_rc": 0,
            }

        with self.subTest("stale-and-missing-patch"):
            self._clear_worktree_artifacts()
            _write(self.patch_path, "diff --git a/src/app.py b/src/app.py\n")
            _write(self.pending_path, json.dumps(marker_payload(), ensure_ascii=False, indent=2) + "\n")
            self.patch_path.unlink()

            diagnostics = read_diagnostics()
            issue_kinds = {str(issue["kind"]) for issue in diagnostics["issues"]}

            self.assertEqual("warning", diagnostics["status"])
            self.assertIn("stale_pending_marker", issue_kinds)
            self.assertIn("missing_patch", issue_kinds)
            self.assertTrue(diagnostics["pending_markers"][0]["stale"])

        with self.subTest("cleanup-failed"):
            self._clear_worktree_artifacts()
            generated_worktree = self._tmp / ".agentcli_worktrees" / self.repo.name / "cleanup-failed"
            generated_worktree.mkdir(parents=True, exist_ok=True)
            _write(generated_worktree / ".git", "gitdir: ../.git/worktrees/cleanup-failed\n")
            _write(self.patch_path, "diff --git a/src/app.py b/src/app.py\n")
            _write(
                self.run_dir / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
                json.dumps(
                    {
                        "schema_version": 1,
                        "status": "applied_cleanup_failed",
                        "created_at": "2026-04-26T12:04:00",
                        "source_repo": self.repo.as_posix(),
                        "run_dir": self.run_dir.as_posix(),
                        "worktree_dir": generated_worktree.as_posix(),
                        "patch_path": self.patch_path.as_posix(),
                        "cleanup_path": generated_worktree.as_posix(),
                        "cleanup_message": "cleanup failed",
                        "base_ref": "main",
                        "head_ref": "abc12345",
                        "last_rc": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )

            diagnostics = read_diagnostics()
            issue_kinds = {str(issue["kind"]) for issue in diagnostics["issues"]}

            self.assertEqual("error", diagnostics["status"])
            self.assertIn("cleanup_failed", issue_kinds)
            self.assertTrue(diagnostics["cleanup_failed"])
            self.assertFalse(diagnostics["generated_worktrees"][0]["orphaned"])

        with self.subTest("orphaned"):
            self._clear_worktree_artifacts()
            orphaned_worktree = self._tmp / ".agentcli_worktrees" / self.repo.name / "orphaned"
            orphaned_worktree.mkdir(parents=True, exist_ok=True)
            _write(orphaned_worktree / ".git", "gitdir: ../.git/worktrees/orphaned\n")

            diagnostics = read_diagnostics()
            issue_kinds = {str(issue["kind"]) for issue in diagnostics["issues"]}

            self.assertEqual("warning", diagnostics["status"])
            self.assertIn("orphaned_worktree", issue_kinds)
            self.assertTrue(any(item["orphaned"] for item in diagnostics["generated_worktrees"]))

    def test_worktree_diagnostics_api_filters_are_read_only_and_category_scoped(self) -> None:
        try:
            from agent_runner.web import create_app
            from fastapi.testclient import TestClient
        except Exception as exc:
            self.skipTest(f"FastAPI test client is unavailable: {exc}")

        paths = self._seed_worktree_diagnostics_fixture()
        before = self._worktree_diagnostics_artifact_state(paths)
        client = TestClient(create_app(self.repo, web_dir=WEB_CONSOLE))

        for category in ("active", "pending", "stale", "orphaned", "cleanup_failed", "missing_patch"):
            with self.subTest(category=category):
                payload = client.get("/api/worktree/diagnostics", params=[("categories", category)]).json()
                selected_entries = [
                    *payload["pending_markers"],
                    *payload["cleanup_failed"],
                    *payload["generated_worktrees"],
                    *payload["issues"],
                ]

                self.assertEqual([category], payload["filters"]["categories"])
                self.assertEqual(
                    ["active", "pending", "stale", "orphaned", "cleanup_failed", "missing_patch"],
                    payload["filters"]["availableCategories"],
                )
                self.assertTrue(selected_entries)
                for entry in selected_entries:
                    self.assertIn(category, entry["categories"])

        after = self._worktree_diagnostics_artifact_state(paths)
        self.assertEqual(before, after)

    def test_worktree_diagnostics_panel_filters_and_localizes_labels(self) -> None:
        self._seed_worktree_diagnostics_fixture()
        snapshot = self._build_snapshot()
        snapshot["worktree_diagnostics"] = scan_worktree_diagnostics(self.repo)

        diagnostics = snapshot["worktree_diagnostics"]
        entries = [
            *diagnostics["pending_markers"],
            *diagnostics["cleanup_failed"],
            *diagnostics["generated_worktrees"],
            *diagnostics["issues"],
        ]
        expected_total = len(entries)
        expected_visible = sum(1 for item in entries if "missing_patch" in item["categories"])
        selected_paths = [item["path"] for item in entries if "missing_patch" in item["categories"]]
        hidden_paths = [item["path"] for item in diagnostics["generated_worktrees"] if item["path"]]

        rendered = _run_worktree_render_harness(
            snapshot,
            locale="en",
            actions=[{"kind": "call", "name": "setWorktreeDiagnosticsFilter", "args": [["missing_patch"]]}],
        )
        html = rendered["main"]

        self.assertIn("All (11)", html)
        self.assertIn(f"{expected_visible} visible | {expected_total} total", html)
        for path in selected_paths:
            self.assertIn(path, html)
        for path in hidden_paths:
            self.assertNotIn(path, html)

        pruned_snapshot = json.loads(json.dumps(snapshot))
        pruned_snapshot["worktree_diagnostics"] = {
            "status": "warning",
            "source_repo": self.repo.as_posix(),
            "source_repo_root": self.repo.as_posix(),
            "generated_worktree_home": (self._tmp / ".agentcli_worktrees" / self.repo.name).as_posix(),
            "scanned_at": "2026-04-26T12:05:00",
            "summary": {
                "run_dirs_scanned": 1,
                "pending_markers": 1,
                "stale_pending_markers": 0,
                "missing_patches": 0,
                "cleanup_failed": 1,
                "generated_worktrees": 1,
                "orphaned_worktrees": 0,
                "issue_count": 0,
                "healthy": True,
                "category_counts": {
                    "active": 3,
                    "pending": 1,
                    "stale": 0,
                    "orphaned": 0,
                    "cleanup_failed": 1,
                    "missing_patch": 0,
                },
            },
            "filters": {
                "categories": [],
                "available_categories": ["active", "pending", "stale", "orphaned", "cleanup_failed", "missing_patch"],
            },
            "pending_markers": [
                {
                    "path": self.pending_path.as_posix(),
                    "scope": "run",
                    "status": "pending",
                    "reason": "",
                    "run_dir": self.run_dir.as_posix(),
                    "source_repo": self.repo.as_posix(),
                    "worktree_dir": self.worktree_dir.as_posix(),
                    "patch_path": self.patch_path.as_posix(),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "exists": True,
                    "stale": False,
                    "categories": ["pending", "active"],
                }
            ],
            "cleanup_failed": [
                {
                    "path": (self.run_dir / "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json").as_posix(),
                    "status": "applied_cleanup_failed",
                    "run_dir": self.run_dir.as_posix(),
                    "source_repo": self.repo.as_posix(),
                    "worktree_dir": "",
                    "patch_path": self.patch_path.as_posix(),
                    "cleanup_path": self.worktree_dir.as_posix(),
                    "cleanup_message": "cleanup failed",
                    "cleanup_details": {},
                    "cleanup_attempts": [],
                    "categories": ["cleanup_failed", "active"],
                }
            ],
            "generated_worktrees": [
                {
                    "path": (self._tmp / ".agentcli_worktrees" / self.repo.name / "cleanup").as_posix(),
                    "exists": True,
                    "contract_path": "",
                    "contract_run_dir": "",
                    "contract_status": "missing_contract",
                    "reason": "missing reuse contract",
                    "tracked": False,
                    "orphaned": False,
                    "referenced": True,
                    "categories": ["active"],
                }
            ],
            "issues": [],
        }

        empty_rendered = _run_worktree_render_harness(
            pruned_snapshot,
            locale="ko",
            actions=[
                {"kind": "call", "name": "setWorktreeDiagnosticsFilter", "args": [["orphaned"]]},
            ],
        )
        empty_html = empty_rendered["main"]

        self.assertIn("진단", empty_html)
        self.assertIn("읽기 전용 진단입니다. 필터링은 파일을 변경하지 않습니다.", empty_html)
        self.assertIn("안정적인 진단 분류로 필터링합니다.", empty_html)
        self.assertIn("선택한 필터와 일치하는 진단이 없습니다.", empty_html)
        self.assertIn("활성", empty_html)
        self.assertIn("대기", empty_html)
        self.assertIn("정리 실패", empty_html)
        self.assertIn("패치 없음", empty_html)

    def test_valid_pending_file_surfaces_review_required_fields(self) -> None:
        changed_files = [
            {
                "path": "bin/data.bin",
                "oldPath": "bin/data.bin",
                "newPath": "bin/data.bin",
                "kind": "binary",
                "state": "binary",
                "note": "binary patch",
                "summary": "Binary patch",
                "binary": True,
                "deleted": False,
                "renamed": False,
                "large": False,
                "truncated": False,
                "hunks": [],
                "lineCount": 0,
            },
            {
                "path": "docs/old.md",
                "oldPath": "docs/old.md",
                "newPath": "docs/old.md",
                "kind": "deleted",
                "state": "deleted",
                "note": "docs/old.md",
                "summary": "Deleted file",
                "binary": False,
                "deleted": True,
                "renamed": False,
                "large": False,
                "truncated": False,
                "hunks": [],
                "lineCount": 0,
            },
            {
                "path": "docs/new.md",
                "oldPath": "docs/old.md",
                "newPath": "docs/new.md",
                "kind": "renamed",
                "state": "renamed",
                "note": "docs/old.md -> docs/new.md",
                "summary": "Renamed docs/old.md -> docs/new.md",
                "binary": False,
                "deleted": False,
                "renamed": True,
                "large": False,
                "truncated": False,
                "hunks": [],
                "lineCount": 0,
            },
            {
                "path": "src/large.txt",
                "oldPath": "src/large.txt",
                "newPath": "src/large.txt",
                "kind": "modified",
                "state": "modified",
                "note": "preview truncated",
                "summary": "Text patch | preview truncated",
                "binary": False,
                "deleted": False,
                "renamed": False,
                "large": True,
                "truncated": True,
                "hunks": [
                    {
                        "header": "@@ -1,4 +1,4 @@",
                        "oldStart": 1,
                        "oldCount": 4,
                        "newStart": 1,
                        "newCount": 4,
                        "lines": [
                            "-old line 1",
                            "+new line 1",
                            " context line",
                        ],
                        "truncated": True,
                        "lineCount": 18,
                    }
                ],
                "lineCount": 18,
            },
        ]
        preflight = {
            "sourceRepoState": "dirty",
            "sourceRepoDirty": True,
            "sourceHead": "abc12345",
            "expectedBaseRef": "main",
            "patchHash": "f" * 64,
            "pendingMarkerPath": self.pending_path.as_posix(),
            "applyCheck": {
                "command": "git apply --check --binary --whitespace=nowarn",
                "rc": 1,
                "ok": False,
                "status": "failed",
                "message": "git apply --check failed.",
                "output": "error: patch failed: src/large.txt:1",
                "failedFiles": [
                    {
                        "path": "src/large.txt",
                        "line": 1,
                        "reason": "patch failed",
                    }
                ],
                "failedHunks": [
                    {
                        "path": "src/large.txt",
                        "line": 1,
                        "reason": "patch failed",
                        "header": "@@ -1,4 +1,4 @@",
                        "lines": ["-old line 1", "+new line 1"],
                        "truncated": False,
                    }
                ],
            },
        }
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
        payload = self._write_pending_payload(
            patch_text=self.patch_path.read_text(encoding="utf-8"),
            base_ref="main",
            expected_head="abc12345",
            branch="main",
        )
        payload.update(
            {
                "changedFiles": changed_files,
                "changed_files": changed_files,
                "preflight": preflight,
                "applyCheck": preflight["applyCheck"],
                "apply_check": preflight["applyCheck"],
                "source_repo_state": preflight["sourceRepoState"],
                "sourceRepoState": preflight["sourceRepoState"],
                "source_repo_dirty": preflight["sourceRepoDirty"],
                "sourceRepoDirty": preflight["sourceRepoDirty"],
                "source_head": preflight["sourceHead"],
                "sourceHead": preflight["sourceHead"],
                "expected_base_ref": preflight["expectedBaseRef"],
                "expectedBaseRef": preflight["expectedBaseRef"],
                "patch_hash": preflight["patchHash"],
                "patchHash": preflight["patchHash"],
                "pending_marker_path": preflight["pendingMarkerPath"],
                "pendingMarkerPath": preflight["pendingMarkerPath"],
            }
        )
        self.pending_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

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
        self.assertTrue(worktree["sourceRepoDirty"])
        self.assertEqual(self.pending_path.as_posix(), worktree["pendingMarkerPath"])
        self.assertEqual("binary", worktree["changedFiles"][0]["kind"])
        self.assertTrue(worktree["changedFiles"][0]["binary"])
        self.assertEqual("deleted", worktree["changedFiles"][1]["kind"])
        self.assertTrue(worktree["changedFiles"][1]["deleted"])
        self.assertEqual("renamed", worktree["changedFiles"][2]["kind"])
        self.assertTrue(worktree["changedFiles"][2]["renamed"])
        self.assertEqual("docs/old.md", worktree["changedFiles"][2]["oldPath"])
        self.assertEqual("docs/new.md", worktree["changedFiles"][2]["newPath"])
        self.assertTrue(worktree["changedFiles"][3]["large"])
        self.assertTrue(worktree["changedFiles"][3]["truncated"])
        self.assertEqual("failed", worktree["preflight"]["applyCheck"]["status"])
        self.assertEqual("src/large.txt", worktree["preflight"]["applyCheck"]["failedFiles"][0]["path"])
        self.assertEqual("@@ -1,4 +1,4 @@", worktree["preflight"]["applyCheck"]["failedHunks"][0]["header"])
        self.assertEqual("binary", normalized["worktreeMerge"]["changedFiles"][0]["kind"])
        self.assertTrue(normalized["worktreeMerge"]["changedFiles"][0]["binary"])
        self.assertEqual("failed", normalized["worktreeMerge"]["preflight"]["applyCheck"]["status"])
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

        def write_artifact(
            name: str,
            status: str,
            *,
            cleanup_message: str = "",
            cleanup_path: str = "",
            cleanup_details: dict[str, object] | None = None,
            cleanup_attempts: list[dict[str, object]] | None = None,
        ) -> Path:
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
            if cleanup_path:
                payload["cleanup_path"] = cleanup_path
            if cleanup_details is not None:
                payload["cleanup_details"] = cleanup_details
            if cleanup_attempts is not None:
                payload["cleanup_attempts"] = cleanup_attempts
            path = self.run_dir / name
            _write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            return path

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

        locked_path = (self.worktree_dir / "nested" / "locked.txt").as_posix()
        locked_message = str(PermissionError(13, "Permission denied", locked_path))
        locked_details = {
            "path": locked_path,
            "worktree_dir": self.worktree_dir.as_posix(),
            "attempts": [
                {
                    "attempt": 1,
                    "operation": "shutil.rmtree",
                    "path": locked_path,
                    "worktree_dir": self.worktree_dir.as_posix(),
                    "error_type": "PermissionError",
                    "message": locked_message,
                    "errno": 13,
                }
            ],
            "operation": "shutil.rmtree",
            "error_type": "PermissionError",
            "message": locked_message,
        }

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
                write_artifact(
                    artifact_name,
                    status_name,
                    cleanup_message=locked_message,
                    cleanup_path=locked_path,
                    cleanup_details=locked_details,
                    cleanup_attempts=locked_details["attempts"],
                )
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
                self.assertEqual(locked_path, snapshot["worktree"]["cleanupPath"])
                self.assertEqual(locked_message, snapshot["worktree"]["cleanupMessage"])
                self.assertEqual(locked_path, snapshot["worktree"]["cleanupDetails"]["path"])
                self.assertEqual(locked_path, snapshot["worktree"]["cleanupDetails"]["attempts"][0]["path"])

        with self.subTest("reconciled-after-cleanup-failure"):
            self._clear_worktree_artifacts()
            write_patch()
            failure_path = write_artifact(
                "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
                "applied_cleanup_failed",
                cleanup_message=locked_message,
                cleanup_path=locked_path,
                cleanup_details=locked_details,
                cleanup_attempts=locked_details["attempts"],
            )
            self.assertTrue(failure_path.exists())
            success_path = write_artifact("WORKTREE_MERGE_APPLIED.json", "applied")
            os.utime(success_path, (success_path.stat().st_atime, success_path.stat().st_mtime + 10))
            snapshot = assert_snapshot(
                label="reconciled-after-cleanup-failure",
                expected_status="applied",
                expected_section_status="ready",
                expected_review_required=False,
                expected_cleanup_state="done",
                expected_status_file=success_path.as_posix(),
                expected_changed_files=True,
            )
            self.assertEqual("Patch applied to the source repository.", snapshot["worktree"]["reviewRequiredMessage"])

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

    def test_worktree_view_renders_split_merge_recovery_and_empty_states(self) -> None:
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

        def write_status_artifact(name: str, payload: dict[str, object]) -> None:
            _write(self.run_dir / name, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")

        with self.subTest("split-merge-recovery"):
            self._clear_worktree_artifacts()
            _write(self.patch_path, patch_text)
            split_patch_path = self.run_dir / "worktree_dirty_uncommitted.patch"
            _write(self.run_dir / "worktree_dirty_uncommitted.patch", patch_text)
            split_check = {
                "command": "git apply --check --binary --whitespace=nowarn",
                "rc": 0,
                "ok": True,
                "status": "ok",
                "message": "git apply --check passed.",
                "output": "",
                "failed_files": [],
                "failed_hunks": [],
                "pending_file": "",
            }
            split_hash = sha256_text(split_patch_path.read_text(encoding="utf-8", errors="replace"))
            write_status_artifact(
                "WORKTREE_MERGE_APPLIED.json",
                {
                    "schema_version": 1,
                    "status": "applied",
                    "created_at": "2026-04-26T12:12:00",
                    "source_repo": self.repo.as_posix(),
                    "run_dir": self.run_dir.as_posix(),
                    "worktree_dir": self.worktree_dir.as_posix(),
                    "patch_path": self.patch_path.as_posix(),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                    "fast_forward_ref": "fedcba98",
                    "fastForwardRef": "fedcba98",
                    "dirty_patch_path": split_patch_path.as_posix(),
                    "dirtyPatchPath": split_patch_path.as_posix(),
                    "dirty_patch_hash": split_hash,
                    "dirtyPatchHash": split_hash,
                    "dirty_patch_check": split_check,
                    "dirtyPatchCheck": split_check,
                    "dirty_patch_applied": True,
                    "dirtyPatchApplied": True,
                },
            )

            snapshot = self._build_snapshot()
            rendered = _run_worktree_render_harness(snapshot)
            main_html = rendered["main"]

            self.assertIn("Split-merge recovery", main_html)
            self.assertIn('chip--accent">available</span>', main_html)
            self.assertIn("Fast-forward ref", main_html)
            self.assertIn("fedcba98", main_html)
            self.assertIn("Dirty patch path", main_html)
            self.assertIn(split_patch_path.as_posix(), main_html)
            self.assertIn("Dirty patch hash", main_html)
            self.assertIn(split_hash, main_html)
            self.assertIn("Dirty patch check", main_html)
            self.assertIn("passed | rc=0", main_html)
            self.assertIn("Dirty patch applied", main_html)
            self.assertIn("Applied", main_html)

        with self.subTest("plain-merge-unavailable"):
            self._clear_worktree_artifacts()
            _write(self.patch_path, patch_text)
            write_status_artifact(
                "WORKTREE_MERGE_APPLIED.json",
                {
                    "schema_version": 1,
                    "status": "applied",
                    "created_at": "2026-04-26T12:12:30",
                    "source_repo": self.repo.as_posix(),
                    "run_dir": self.run_dir.as_posix(),
                    "worktree_dir": self.worktree_dir.as_posix(),
                    "patch_path": self.patch_path.as_posix(),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
            )

            snapshot = self._build_snapshot()
            rendered = _run_worktree_render_harness(snapshot)
            main_html = rendered["main"].lower()

            self.assertIn("split-merge recovery", main_html)
            self.assertIn('chip--info">unavailable</span>', main_html)
            self.assertIn("fast-forward ref", main_html)
            self.assertIn("dirty patch path", main_html)
            self.assertIn("dirty patch hash", main_html)
            self.assertIn("dirty patch applied", main_html)


if __name__ == "__main__":
    unittest.main()
