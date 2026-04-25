from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB_CONSOLE = ROOT / "web_console"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _make_log_entries(count):
    levels = ["debug", "info", "warn", "err"]
    stages = ["PM", "Dev", "QA"]
    entries = []
    for index in range(count):
        entries.append(
            {
                "ts": f"2026-04-26T12:{index % 60:02d}:00",
                "lvl": levels[index % len(levels)],
                "stage": stages[index % len(stages)],
                "message": f"log entry {index:03d}",
            }
        )
    return entries


def _make_no_run_snapshot():
    return {
        "ok": True,
        "latest_run_dir": "",
        "repo": {
            "path": "",
            "name": "agentcli",
            "head": "",
            "branch": "HEAD",
        },
        "active_run": {},
        "stages": [],
        "backlog": {
            "items": [],
            "counts": {},
            "selected_id": "",
        },
        "goals": {
            "items": {"p0": [], "p1": []},
            "path": ".doc/GOALS.md",
            "completion": {},
        },
        "config": {
            "path": "config/agentcli.json",
            "source": "default",
            "data": {},
            "resolved_prompts_dir": "",
        },
        "prompts": {
            "items": [],
            "dir": "",
            "exists": False,
        },
        "logs": {
            "entries": [],
            "tail": "",
            "files": {},
        },
        "notifications": [],
        "history": {
            "items": [],
        },
        "metrics": {},
        "worktree": {
            "status": "none",
        },
        "progress": {
            "latest_run_dir": None,
            "run_status": "idle",
            "tasks_done": 0,
            "tasks_total": 0,
            "tasks_failed": 0,
            "progress": 0,
            "current_task_id": "",
            "current_task_title": "",
            "goals": {"p0": [], "p1": []},
            "backlog": {"items": [], "counts": {}, "selected_id": ""},
            "final_reason": "",
            "state": {"done": [], "failed": [], "warnings": []},
        },
    }


def _make_partial_snapshot():
    repo_path = "C:/Dev/AgentCLI"
    return {
        "ok": True,
        "latest_run_dir": ".AgentCLI/agent_runs/20260426-121500",
        "repo": {
            "path": repo_path,
            "name": "AgentCLI",
            "head": "abc12345",
            "branch": "main",
        },
        "active_run": {
            "id": "run_20260426_121500",
            "repo": repo_path,
            "repoLabel": "AgentCLI",
            "branch": "main",
            "backend": "codex",
            "startedAt": 1714133700000,
            "stage": "Dev",
            "stageIndex": 1,
            "iteration": 2,
            "maxIterations": 4,
            "progress": 0.5,
            "budgetUsed": 0.4,
            "tokens": {"in": 1200, "out": 450},
            "quota": {"window": "5h", "used": 0.4},
            "elapsedSec": 1200,
            "status": "running",
            "task": "T-020",
            "taskTitle": "API-backed observation path",
        },
        "stages": [
            {
                "id": "PM",
                "label": "PM",
                "title": "Backlog planning",
                "status": "done",
                "durationSec": 300,
                "model": "gpt-5.1-codex-mini",
            },
            {
                "id": "Dev",
                "label": "Dev",
                "title": "API-backed observation path",
                "status": "running",
                "durationSec": 960,
                "model": "gpt-5.1-codex-mini",
            },
        ],
        "backlog": {
            "items": [
                {
                    "id": "T-020",
                    "title": "API-backed observation path",
                    "status": "in_progress",
                    "priority": "P0",
                    "tags": ["web", "api"],
                    "estimate": "M",
                    "skill": "observability",
                    "description": "Wire the browser console to read-only status endpoints.",
                }
            ],
            "counts": {"pending": 0, "in_progress": 1, "done": 0},
            "selected_id": "T-020",
        },
        "goals": {
            "items": {
                "p0": [
                    {
                        "done": False,
                        "text": "Observe the current run in a browser without CLI shell access",
                        "note": "",
                    }
                ],
                "p1": [
                    {
                        "done": False,
                        "text": "Keep the browser useful when no run exists",
                        "note": "",
                    }
                ],
            },
            "path": ".doc/GOALS.md",
            "completion": {"project_complete": False},
        },
        "config": {
            "path": "config/agentcli.json",
            "source": "read-only",
            "data": {
                "repo": repo_path,
                "execution_backend": "codex",
                "iterations": 4,
                "prompts_dir": "prompts/agentcli",
            },
            "resolved_prompts_dir": "prompts/agentcli",
        },
        "prompts": {
            "items": [
                {
                    "id": "bootstrap",
                    "file": "bootstrap_prompt.md",
                    "scope": "PM",
                    "source": "repo",
                    "mode": "template",
                    "updated": "2026-04-26T12:00:00",
                    "summary": "Bootstrap the read-only console view.",
                    "preview": "Open the status dashboard first.",
                }
            ],
            "dir": "prompts/agentcli",
            "exists": True,
        },
        "logs": {
            "entries": _make_log_entries(130),
            "tail": "partial snapshot tail",
            "files": {
                "cycle_summary": ".AgentCLI/agent_runs/20260426-121500/cycle_summary.log",
            },
        },
        "notifications": [
            {
                "ts": 1714133760000,
                "kind": "task_done",
                "text": "T-019 | QA verification completed",
                "run": "run_20260426_121000",
            }
        ],
        "history": {
            "items": [
                {
                    "id": "run_20260426_121000",
                    "startedAt": 1714133400000,
                    "status": "success",
                    "tasksDone": 2,
                    "tasksTotal": 2,
                    "branch": "main",
                    "durationSec": 780,
                    "stopReason": "",
                    "runDir": ".AgentCLI/agent_runs/20260426-121000",
                    "lastCycle": "cycle=1 done=2/2",
                }
            ],
        },
        "metrics": {
            "tokens24h": [10, 20, 30],
            "success24h": [1, 1, 0],
            "budget": [0.1, 0.2, 0.3],
            "tokens": {"in": 1200, "out": 450},
            "last_stage": "Dev",
            "quota_used": 0.4,
        },
        "worktree": {
            "status": "pending",
            "mode": "manual",
            "branch": "main",
            "worktree": "C:/Dev/AgentCLI.worktree",
            "patch": ".AgentCLI/agent_runs/20260426-121500/worktree.patch",
            "pendingFile": ".AgentCLI/agent_runs/20260426-121500/WORKTREE_MERGE_PENDING.json",
            "summary": "Pending merge review.",
            "risk": "Review before merge.",
            "changedFiles": [
                {
                    "path": "web_console/app.js",
                    "kind": "modified",
                    "note": "adapter wiring",
                }
            ],
            "checklist": ["Inspect patch hunks", "Verify no secret leakage"],
            "runDir": ".AgentCLI/agent_runs/20260426-121500",
            "headRef": "abc12345",
            "lastRc": 0,
        },
        "progress": {
            "latest_run_dir": ".AgentCLI/agent_runs/20260426-121500",
            "run_status": "running",
            "tasks_done": 1,
            "tasks_total": 2,
            "tasks_failed": 0,
            "progress": 0.5,
            "current_task_id": "T-020",
            "current_task_title": "API-backed observation path",
            "goals": {"p0": [], "p1": []},
            "backlog": {"items": [], "counts": {}, "selected_id": "T-020"},
            "final_reason": "",
            "state": {"done": [], "failed": [], "warnings": []},
        },
    }


def _make_normal_snapshot():
    repo_path = "C:/Dev/AgentCLI"
    return {
        "ok": True,
        "latest_run_dir": ".AgentCLI/agent_runs/20260426-120000",
        "repo": {
            "path": repo_path,
            "name": "AgentCLI",
            "head": "fedcba98",
            "branch": "main",
        },
        "active_run": {
            "id": "run_20260426_120000",
            "repo": repo_path,
            "repoLabel": "AgentCLI",
            "branch": "main",
            "backend": "codex",
            "startedAt": 1714132800000,
            "stage": "Dev",
            "stageIndex": 1,
            "iteration": 3,
            "maxIterations": 5,
            "progress": 0.72,
            "budgetUsed": 0.56,
            "tokens": {"in": 18420, "out": 6421},
            "quota": {"window": "5h", "used": 0.41},
            "elapsedSec": 1680,
            "status": "running",
            "task": "T-020",
            "taskTitle": "API-backed observation path",
        },
        "stages": [
            {
                "id": "PM",
                "label": "PM",
                "title": "Backlog planning",
                "status": "done",
                "durationSec": 300,
                "model": "gpt-5.1-codex-mini",
            },
            {
                "id": "Dev",
                "label": "Dev",
                "title": "API-backed observation path",
                "status": "running",
                "durationSec": 960,
                "model": "gpt-5.1-codex-mini",
            },
            {
                "id": "QA",
                "label": "QA",
                "title": "Verification",
                "status": "pending",
                "durationSec": 0,
                "model": "gpt-5.1-codex-mini",
            },
        ],
        "backlog": {
            "items": [
                {
                    "id": "T-020",
                    "title": "API-backed observation path",
                    "status": "in_progress",
                    "priority": "P0",
                    "tags": ["web", "api"],
                    "estimate": "M",
                    "skill": "observability",
                    "description": "Wire the browser console to read-only status endpoints.",
                },
                {
                    "id": "T-021",
                    "title": "Bounded log tail",
                    "status": "pending",
                    "priority": "P1",
                    "tags": ["logs"],
                    "estimate": "S",
                    "skill": "",
                    "description": "Keep the log DOM bounded during refresh.",
                },
            ],
            "counts": {"pending": 1, "in_progress": 1, "done": 0},
            "selected_id": "T-020",
        },
        "goals": {
            "items": {
                "p0": [
                    {
                        "done": False,
                        "text": "Observe the current run in a browser without CLI shell access",
                        "note": "",
                    }
                ],
                "p1": [
                    {
                        "done": True,
                        "text": "Keep the browser useful when no run exists",
                        "note": "",
                    }
                ],
            },
            "path": ".doc/GOALS.md",
            "completion": {"project_complete": False},
        },
        "config": {
            "path": "config/agentcli.json",
            "source": "read-only",
            "data": {
                "repo": repo_path,
                "execution_backend": "codex",
                "iterations": 5,
                "prompts_dir": "prompts/agentcli",
                "telegram": {
                    "enabled": True,
                    "instance_name": "home-pc-main",
                },
            },
            "resolved_prompts_dir": "prompts/agentcli",
        },
        "prompts": {
            "items": [
                {
                    "id": "bootstrap",
                    "file": "bootstrap_prompt.md",
                    "scope": "PM",
                    "source": "repo",
                    "mode": "template",
                    "updated": "2026-04-26T12:00:00",
                    "summary": "Bootstrap the read-only console view.",
                    "preview": "Open the status dashboard first.",
                },
                {
                    "id": "dev",
                    "file": "dev_prompt.md",
                    "scope": "Dev",
                    "source": "repo",
                    "mode": "override",
                    "updated": "2026-04-26T12:05:00",
                    "summary": "Use browser data adapters for read-only observation.",
                    "preview": "Prefer adapter outputs over hardcoded shell data.",
                },
            ],
            "dir": "prompts/agentcli",
            "exists": True,
        },
        "logs": {
            "entries": [
                {
                    "ts": "2026-04-26T12:00:00",
                    "lvl": "info",
                    "stage": "boot",
                    "message": "AgentCLI web console started.",
                },
                {
                    "ts": "2026-04-26T12:01:00",
                    "lvl": "info",
                    "stage": "PM",
                    "message": "Backlog emitted from read-only API.",
                },
                {
                    "ts": "2026-04-26T12:02:00",
                    "lvl": "warn",
                    "stage": "Dev",
                    "message": "Browser view should stay bounded on refresh.",
                },
            ],
            "tail": "normal snapshot tail",
            "files": {
                "cycle_summary": ".AgentCLI/agent_runs/20260426-120000/cycle_summary.log",
                "run_log": ".AgentCLI/agent_runs/20260426-120000/logs/run.log",
            },
        },
        "notifications": [
            {
                "ts": 1714132860000,
                "kind": "run_start",
                "text": "Run started | main",
                "run": "run_20260426_120000",
            },
            {
                "ts": 1714132920000,
                "kind": "task_done",
                "text": "T-019 | verification completed",
                "run": "run_20260426_120000",
            },
        ],
        "history": {
            "items": [
                {
                    "id": "run_20260426_120000",
                    "startedAt": 1714132800000,
                    "status": "running",
                    "tasksDone": 1,
                    "tasksTotal": 2,
                    "branch": "main",
                    "durationSec": 1680,
                    "stopReason": "",
                    "runDir": ".AgentCLI/agent_runs/20260426-120000",
                    "lastCycle": "cycle=3 done=1/2",
                }
            ],
        },
        "metrics": {
            "tokens24h": [120, 240, 360, 480],
            "success24h": [1, 1, 1, 0],
            "budget": [0.1, 0.2, 0.35, 0.56],
            "tokens": {"in": 18420, "out": 6421},
            "last_stage": "Dev",
            "quota_used": 0.41,
        },
        "worktree": {
            "status": "pending",
            "mode": "manual",
            "branch": "main",
            "worktree": "C:/Dev/AgentCLI.worktree",
            "patch": ".AgentCLI/agent_runs/20260426-120000/worktree.patch",
            "pendingFile": ".AgentCLI/agent_runs/20260426-120000/WORKTREE_MERGE_PENDING.json",
            "summary": "Pending merge review.",
            "risk": "Review before merge.",
            "changedFiles": [
                {
                    "path": "web_console/app.js",
                    "kind": "modified",
                    "note": "adapter wiring",
                },
                {
                    "path": "web_console/styles.css",
                    "kind": "modified",
                    "note": "section banners",
                },
            ],
            "checklist": [
                "Inspect patch hunks",
                "Verify no secret leakage",
                "Approve merge only after review",
            ],
            "runDir": ".AgentCLI/agent_runs/20260426-120000",
            "headRef": "fedcba98",
            "lastRc": 0,
        },
        "progress": {
            "latest_run_dir": ".AgentCLI/agent_runs/20260426-120000",
            "run_status": "running",
            "tasks_done": 1,
            "tasks_total": 2,
            "tasks_failed": 0,
            "progress": 0.5,
            "current_task_id": "T-020",
            "current_task_title": "API-backed observation path",
            "goals": {
                "p0": ["Observe the current run in a browser without CLI shell access"],
                "p1": ["Keep the browser useful when no run exists"],
            },
            "backlog": {"items": [], "counts": {}, "selected_id": "T-020"},
            "final_reason": "",
            "state": {"done": ["T-019"], "failed": [], "warnings": []},
        },
    }


def _run_adapter_harness(fixtures):
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
        const results = fixtures.map((fixture) => {
          if (fixture.kind === 'snapshot') {
            return adapters.normalizeSnapshot(fixture.data);
          }
          if (fixture.kind === 'fallback') {
            return adapters.createFallbackFixture();
          }
          throw new Error('Unknown fixture kind: ' + fixture.kind);
        });
        process.stdout.write(JSON.stringify(results));
        """
    ).replace("__SOURCE_PATH__", json.dumps(str(WEB_CONSOLE / "app.js"))).replace(
        "__FIXTURES__", json.dumps(fixtures, ensure_ascii=False)
    )
    completed = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout)


class WebConsoleReadonlyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        try:
            from fastapi.testclient import TestClient  # noqa: F401
            import fastapi  # noqa: F401
        except Exception as exc:
            raise unittest.SkipTest(f"FastAPI is unavailable: {exc}") from exc

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)

        self.home = Path(self._tmp.name) / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self._old_home = os.environ.get("AGENTCLI_HOME")
        os.environ["AGENTCLI_HOME"] = str(self.home)
        self.addCleanup(self._restore_home)

        self.run_dir = self.repo / ".AgentCLI" / "agent_runs" / "20260426-120000"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / "logs").mkdir(parents=True, exist_ok=True)

        _write(
            self.repo / ".doc" / "GOALS.md",
            """# Project Goals

## P0
- [x] Expose read-only progress views
- [ ] Add FastAPI web console

## P1
- [ ] Polish status panels
""",
        )

        backlog = {
            "generated_at": "2026-04-26T12:00:00",
            "tasks": [
                {
                    "id": "T1",
                    "title": "Expose read-only progress views",
                    "prompt": "Implement the read-only web status views.",
                    "files": ["agent_runner/web.py"],
                    "done_when": "Endpoint returns current run progress.",
                    "skills": [],
                    "skills_rationale": None,
                    "depends_on": [],
                },
                {
                    "id": "T2",
                    "title": "Add FastAPI web console",
                    "prompt": "Serve the production web console.",
                    "files": ["agent_runner/web.py", "web_console/app.js"],
                    "done_when": "Static assets and JSON endpoints respond.",
                    "skills": [],
                    "skills_rationale": None,
                    "depends_on": ["T1"],
                },
            ],
        }
        _write(self.run_dir / "BACKLOG.json", json.dumps(backlog, ensure_ascii=False, indent=2) + "\n")
        _write(self.run_dir / "STATE.json", json.dumps({"done": ["T1"], "failed": [], "warnings": []}, ensure_ascii=False, indent=2) + "\n")
        _write(
            self.run_dir / "metrics.jsonl",
            "\n".join(
                [
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:00:00",
                            "seq": 1,
                            "level": "info",
                            "event": "cycle_start",
                            "stage": "PM",
                            "message": "cycle start",
                            "payload": {"cycle": 1},
                        },
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        {
                            "ts": "2026-04-26T12:01:00",
                            "seq": 2,
                            "level": "info",
                            "event": "cycle_end",
                            "stage": "Dev",
                            "message": "cycle end",
                            "done": 1,
                            "total": 2,
                            "rc": 0,
                            "tokens": {"_total": {"input": 11, "output": 22, "total": 33}},
                        },
                        ensure_ascii=False,
                    ),
                    "",
                ]
            ),
        )
        _write(
            self.run_dir / "run_summary.json",
            json.dumps({"run_id": self.run_dir.name, "repo": str(self.repo), "profile": "personal", "cycles": [{"cycle": 1, "stages": [{"name": "PM", "status": "ok", "rc": 0, "reason": "ok"}]}], "final": {"rc": 0, "reason": "project_complete"}}, ensure_ascii=False, indent=2)
            + "\n",
        )
        _write(
            self.run_dir / "last_run_summary.json",
            json.dumps({"ts": "2026-04-26T12:01:00", "cycle": 1, "run_dir": str(self.run_dir), "done": 1, "skipped": 0, "total_tasks": 2, "failed_count": 0, "duration_seconds": 60, "build_enabled": True, "run_tests": True, "policy_scan_enabled": False}, ensure_ascii=False, indent=2)
            + "\n",
        )
        _write(
            self.run_dir / "cycle_summary.log",
            "2026-04-26T12:01:00 cycle=1 done=1/2 failed=0 dt=60.0s\n",
        )
        _write(
            self.run_dir / "logs" / "run.log",
            "2026-04-26 12:00:00 [INFO] cycle started\n2026-04-26 12:01:00 [INFO] cycle finished\n",
        )
        _write(
            self.run_dir / "WORKTREE_MERGE_PENDING.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "pending",
                    "created_at": "2026-04-26T12:02:00",
                    "source_repo": str(self.repo),
                    "run_dir": str(self.run_dir),
                    "worktree_dir": str(self.repo / "worktree"),
                    "patch_path": str(self.run_dir / "worktree.patch"),
                    "base_ref": "main",
                    "head_ref": "abc12345",
                    "last_rc": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        _write(
            self.run_dir / "worktree.patch",
            """diff --git a/agent_runner/web.py b/agent_runner/web.py
--- a/agent_runner/web.py
+++ b/agent_runner/web.py
@@ -1,1 +1,1 @@
-old
+new
""",
        )

        from agent_runner.web import create_app
        from fastapi.testclient import TestClient

        self.app = create_app(self.repo, web_dir=WEB_CONSOLE)
        self.client = TestClient(self.app)

    def _restore_home(self) -> None:
        if self._old_home is None:
            os.environ.pop("AGENTCLI_HOME", None)
        else:
            os.environ["AGENTCLI_HOME"] = self._old_home

    def test_health_and_status_expose_read_only_snapshot(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(200, health.status_code)
        health_payload = health.json()
        self.assertTrue(health_payload["ok"])
        self.assertTrue(health_payload["latest_run_dir"].endswith("20260426-120000"))

        status = self.client.get("/api/status")
        self.assertEqual(200, status.status_code)
        payload = status.json()
        for key in ("active_run", "stages", "backlog", "goals", "logs", "config", "prompts", "history", "metrics", "notifications", "worktree", "progress"):
            self.assertIn(key, payload)
        self.assertEqual("20260426-120000", payload["active_run"]["id"])
        self.assertEqual(3, len(payload["stages"]))
        self.assertEqual(2, len(payload["backlog"]["items"]))
        self.assertEqual(1, payload["progress"]["tasks_done"])
        self.assertEqual("success", payload["progress"]["run_status"])
        self.assertEqual("success", payload["active_run"]["status"])

    def test_empty_latest_timestamp_run_dir_does_not_mask_real_run(self) -> None:
        empty_run = self.repo / ".AgentCLI" / "agent_runs" / "20260426-130000"
        empty_run.mkdir(parents=True, exist_ok=True)

        from agent_runner.web import build_snapshot

        payload = build_snapshot(self.repo)

        self.assertTrue(payload["latest_run_dir"].endswith("20260426-120000"))
        self.assertEqual("20260426-120000", payload["active_run"]["id"])
        self.assertEqual("success", payload["progress"]["run_status"])

    def test_section_endpoints_return_stable_shapes(self) -> None:
        progress = self.client.get("/api/progress").json()
        for key in ("active_run", "stages", "backlog", "goals", "logs", "config", "prompts", "history", "metrics", "notifications", "worktree", "state"):
            self.assertIn(key, progress)
        self.assertEqual(1, progress["tasks_done"])
        self.assertEqual(2, progress["tasks_total"])
        self.assertIn("goals", progress)

        logs = self.client.get("/api/logs").json()
        self.assertIn("entries", logs)
        self.assertGreaterEqual(len(logs["entries"]), 1)

        config = self.client.get("/api/config").json()
        self.assertIn("data", config)
        self.assertIn("resolved_prompts_dir", config)

        prompts = self.client.get("/api/prompts").json()
        self.assertIn("items", prompts)
        self.assertGreaterEqual(len(prompts["items"]), 3)

        history = self.client.get("/api/history").json()
        self.assertIn("items", history)
        self.assertGreaterEqual(len(history["items"]), 1)

        worktree = self.client.get("/api/worktree").json()
        self.assertEqual("pending review", worktree["status"])
        self.assertTrue(worktree["changedFiles"])

    def test_config_redaction_masks_sensitive_values(self) -> None:
        from agent_runner.web import _redact_config

        redacted = _redact_config(
            {
                "repo": "C:/Dev/AgentCLI",
                "openai_api_key": "example-api-key",
                "telegram": {
                    "enabled": True,
                    "bot_token": "123:abc",
                    "chat_id": "999",
                },
                "nested": [{"password": "secret"}, {"name": "safe"}],
            }
        )

        self.assertEqual("C:/Dev/AgentCLI", redacted["repo"])
        self.assertEqual("[redacted]", redacted["openai_api_key"])
        self.assertTrue(redacted["telegram"]["enabled"])
        self.assertEqual("[redacted]", redacted["telegram"]["bot_token"])
        self.assertEqual("[redacted]", redacted["telegram"]["chat_id"])
        self.assertEqual("[redacted]", redacted["nested"][0]["password"])
        self.assertEqual("safe", redacted["nested"][1]["name"])

    def test_static_console_assets_are_served(self) -> None:
        root = self.client.get("/")
        self.assertEqual(200, root.status_code)
        self.assertIn("text/html", root.headers.get("content-type", ""))

        app_js = self.client.get("/app.js")
        self.assertEqual(200, app_js.status_code)
        self.assertIn("application/javascript", app_js.headers.get("content-type", ""))

        styles = self.client.get("/styles.css")
        self.assertEqual(200, styles.status_code)
        self.assertIn("text/css", styles.headers.get("content-type", ""))

    def test_unknown_api_paths_return_not_found(self) -> None:
        response = self.client.get("/api/unknown")
        self.assertEqual(404, response.status_code)

    def test_adapter_response_normalization_covers_no_run_partial_and_normal_fixtures(self) -> None:
        no_run, partial, normal, fallback = _run_adapter_harness(
            [
                {"kind": "snapshot", "data": _make_no_run_snapshot()},
                {"kind": "snapshot", "data": _make_partial_snapshot()},
                {"kind": "snapshot", "data": _make_normal_snapshot()},
                {"kind": "fallback"},
            ]
        )

        with self.subTest("no-run"):
            self.assertEqual("api", no_run["sourceMode"])
            self.assertEqual("no-run", no_run["activeRun"]["id"])
            self.assertEqual("empty", no_run["sectionState"]["activeRun"]["status"])
            self.assertEqual("empty", no_run["sectionState"]["stages"]["status"])
            self.assertEqual(3, len(no_run["stages"]))

        with self.subTest("partial-run"):
            self.assertEqual("partial", partial["sectionState"]["stages"]["status"])
            self.assertEqual("Only some stage records were published.", partial["sectionState"]["stages"]["message"])
            self.assertEqual(3, len(partial["stages"]))
            self.assertEqual(120, len(partial["logs"]))
            self.assertEqual("running", partial["activeRun"]["status"])

        with self.subTest("normal-run"):
            self.assertEqual("api", normal["sourceMode"])
            self.assertEqual("ready", normal["sectionState"]["stages"]["status"])
            self.assertEqual("ready", normal["sectionState"]["backlog"]["status"])
            self.assertEqual("ready", normal["sectionState"]["worktree"]["status"])
            self.assertEqual(3, len(normal["stages"]))
            self.assertEqual(2, len(normal["backlog"]))
            self.assertEqual("run_20260426_120000", normal["activeRun"]["id"])
            self.assertEqual("pending", normal["worktreeMerge"]["status"])

        with self.subTest("fallback-fixture"):
            self.assertEqual("fallback", fallback["sourceMode"])
            self.assertEqual("Fallback data", fallback["snapshotLabel"])

    def test_adapter_normalizes_string_config_values_for_schema_fields(self) -> None:
        snapshot = _make_no_run_snapshot()
        snapshot["config"]["data"] = {
            "roles": "PM,Dev,QA",
            "telegram": {"enabled": "false"},
            "budget": {"max_iters": "7"},
        }

        normalized = _run_adapter_harness([{"kind": "snapshot", "data": snapshot}])[0]

        self.assertEqual(["PM", "Dev", "QA"], normalized["config"]["roles"])
        self.assertFalse(normalized["config"]["telegram"]["enabled"])
        self.assertEqual(7, normalized["config"]["budget"]["max_iters"])


if __name__ == "__main__":
    unittest.main()
