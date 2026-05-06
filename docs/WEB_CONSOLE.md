# AgentCLI Web Console

> Last verified: 2026-05-06 (against live FastAPI route inventory and GOALS verification).

AgentCLI has a FastAPI web console for browser-based local-operator monitoring and guarded repo operations. It is repo-owned code in `web_console/` served by `agent_runner.web`; the exported design files under `docs/Design/project/` are reference input only.

The console is optimized for a trusted local repository workflow: read-only snapshots are the default, while config, prompt, goals, runner, PR queue decisions, and worktree mutation routes require explicit server opt-in and confirmation where applicable.

## Operating Model

AgentCLI Web currently runs as `one repo, one web instance`.

- Each web process owns exactly one active repository, selected at startup with `--repo`.
- The active repo identity shown in the UI and API snapshots is that single startup-bound repository; it is not a browser-switchable scope and it does not aggregate multiple repositories.
- The repo-level instance lock is a local duplicate-instance guard for the same repository. It prevents accidental duplicate local-operator control, but it is not a multi-repo coordinator.
- Multi-repo dashboards are deferred future scope and belong to a later phase.

## Current Status

Verified on 2026-05-06 with local API, unit, and checked-in browser smoke coverage:

- Static console serving works from `agent_runner.web`.
- Live FastAPI routes cover `/api/health`, `/api/status`, `/api/progress`, `/api/history`, `/api/reports/export`, `/api/experience`, `/api/logs`, `/api/logs/tail`, `/api/logs/live`, `/api/artifacts/open`, `/api/worktree`, `/api/worktree/diagnostics`, guarded `/api/config`, `/api/config/restore`, `/api/config/save`, `/api/prompts`, `/api/prompts/read`, `/api/prompts/content`, `/api/prompts/save`, `/api/prompts/restore`, `/api/goals`, `/api/goals/save`, `/api/runner/status`, `/api/runner/start`, `/api/runner/stop`, `/api/runner/reload`, `/api/runner/restart`, `/api/worktree/merge`, `/api/worktree/discard`, `/api/pr-queue`, `/api/pr-queue/{packet_id}`, `/api/pr-queue/validate`, `/api/pr-queue/merge`, `/api/pr-queue/discard`, `/api/pr-queue/rebase`, `/api/pr_queue/validate`, `/api/pr_queue/merge`, `/api/pr_queue/discard`, and `/api/pr_queue/rebase`.
- `/api/health` exposes web diagnostics for FastAPI/uvicorn availability and repo `.venv` health, including missing executables and stale `pyvenv.cfg` base paths; startup dependency failures include the same diagnostic issue codes when the HTTP app cannot start.
- The Runbook route renders active-repo commands for venv activation, shell/web startup, status/stop, worktree merge/discard, PR queue review, diagnostics, and long unattended runs.
- Completed run report generation also writes `WORK_SUMMARY.md`, a short daily-work-log Markdown artifact without raw logs, prompts, transcripts, or diffs.
- Mutating web actions append redacted `WEB_ACTION_AUDIT.jsonl` records with timestamps and result summaries: run/worktree-bound actions write under the active `run_dir`, while config, prompt, and goals edits write to `.AgentCLI/WEB_ACTION_AUDIT.jsonl`.
- Browser-rendered `.AgentCLI` artifact paths use the loopback-only read-only `/api/artifacts/open` helper for allowed text-like artifacts instead of invoking raw local filesystem operations.
- Run History can compare the selected run against another run side-by-side, including commits, task outcomes, token/quota telemetry, validation status/results, and worktree outcomes.
- Notifications now keep browser-local read/unread state, filter by event kind/read status/severity, group the feed by severity, and provide links into the related run, backlog task, or filtered log view when metadata is available.
- Backup creation happens inside save/restore flows; there is no standalone `/api/*/backup` route family.
- Read-only worktree diagnostics now scan `.AgentCLI/agent_runs`, the central pending marker, patch paths, cleanup-failed artifacts, and generated worktree directories without deleting anything by default.
- Additional read-only contracts now cover Goals metadata and backend log tailing.
- LAN or external binds now redact logs, log file metadata, GOALS raw text, backlog/task excerpts, config snapshots, prompt previews/content, and serialized runner arguments before the browser sees them, while the browser renders hidden/unavailable copy for those fields.
- The explicit prompt-read path stays available for the editor flow after a user selects a prompt; the inventory view remains redacted and the raw-content path is only used by that explicit editor request.
- The UI has first-pass routes for Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, and Worktree Review.
- The shell now exposes `/worktree` for the same diagnostics summary.
- PR Queue browser controls can validate, approve merge, discard, and request rebase for queued packets through guarded backend routes. Merge approval requires a validated packet plus exact `MERGE PR <packet_id>`; discard and rebase require exact `DISCARD PR <packet_id>` / `REBASE PR <packet_id>` confirmation phrases; every browser PR Queue decision uses shared packet helper gates plus the web opt-in, LAN, and confirmation gates.
- Checked-in Playwright smoke coverage now exercises Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, Worktree Review, EN/KO Dashboard and Config locale switching, and a mobile-width viewport.
- Runner controls are disabled by default and require explicit opt-in.
- Config saves now reuse that opt-in and create a timestamped backup before atomic disk writes.
- Config rendering now tolerates `roles` as strings or arrays and uses the runtime role hint: Built-in order: PM, PL, Security, Dev, QA. Plugin specs like pkg.mod:Class are preserved.
- Empty timestamped run directories are ignored so they do not appear as active runs.
- Completed runs with final reason `ok`, `prepared_only`, `project_complete`, or `all_tasks_done` are no longer displayed as still running.

Known remaining scope:

- Instance Health remains tracked in `.doc/GOALS.md` P1.
- There is no implemented authentication layer yet. Treat LAN binds as trusted-network-only until [AUTHENTICATION_PLAN.md](AUTHENTICATION_PLAN.md) is implemented.

## Web Server Flags

| 옵션 | 설명 |
|------|------|
| `--repo` | repo root path |
| `--host` | bind host |
| `--port` | listen port |
| `--web-dir` | static web console directory |
| `--config-path` | config file path |
| `--enable-runner-controls` | runner control actions를 켭니다 |
| `--trusted-network` | LAN bind를 trusted-network bind로 표시합니다 |

## Install Dependencies

Use the project virtual environment:

```powershell
cd D:\000.Work\001.Private\000.API\agent_cli
python -m pip install -r requirements.txt
```

The web server requires `fastapi` and `uvicorn`. If the venv points to a missing base Python, recreate the venv or repair it before serving.
The `/api/health` diagnostics payload and startup errors report missing `fastapi`, missing `uvicorn`, missing `.venv`, missing `.venv` Python executables, and broken `pyvenv.cfg` base paths.

## Localhost

```powershell
cd D:\000.Work\001.Private\000.API\agent_cli
.\.venv\Scripts\python.exe agent_cli.py --web `
  --repo "." `
  --host 127.0.0.1 `
  --port 8000
```

Equivalent module entrypoint:

```powershell
.\.venv\Scripts\python.exe -m agent_runner.web `
  --repo "." `
  --host 127.0.0.1 `
  --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## LAN Viewing

Bind to all interfaces only on a trusted network. Do not expose the console on an untrusted LAN until authentication exists:

```powershell
.\.venv\Scripts\python.exe agent_cli.py --web `
  --repo "." `
  --host 0.0.0.0 `
  --port 8000
```

Open from another device:

```text
http://<notebook-ip>:8000
```

## Browser Smoke

Run the checked-in Playwright smoke against fixture data:

```powershell
python .\tests\web_console_playwright_smoke.py
```

If Playwright or the browser runtime is unavailable, the smoke skips cleanly and prints the optional setup command instead of installing packages.

## Optional Playwright Setup

Install the optional browser test dependency and Chromium binary only if you want to run the smoke locally:

```powershell
python -m pip install playwright
python -m playwright install chromium
```

## Runner Controls

Runner controls are disabled by default. Enable them explicitly:

```powershell
python -m agent_runner.web `
  --repo "." `
  --host 127.0.0.1 `
  --port 8000 `
  --enable-runner-controls
```

`AGENTCLI_WEB_RUNNER_CONTROLS=1`은 `--enable-runner-controls`와 같은 opt-in입니다.
`AGENTCLI_WEB_TRUSTED_NETWORK=1`은 trusted-network bind 경로를 명시할 때 사용합니다.

Mutating actions require confirmation phrases. Use localhost first before enabling controls on a LAN bind.
The guarded config save endpoint uses the same opt-in, rejects unsafe or redacted placeholder writes, and writes a timestamped backup before replacing the config atomically.

Authentication is planned separately in [AUTHENTICATION_PLAN.md](AUTHENTICATION_PLAN.md). Confirmation phrases and `--trusted-network` are not authentication.

## Validation

```powershell
python -B -m py_compile agent_runner\web.py tests\web_console_playwright_smoke.py
python -B -m unittest discover -s tests -p "test_web_console*.py"
python -B -m unittest discover -s tests -p "test_worktree*.py"
python -B .\tests\web_console_playwright_smoke.py
```

The web smoke path covers the primary views, Dashboard and Config locale switching, prompt read loading, worktree review, and the mobile-width layout. Install Playwright locally before using the browser smoke command as a release gate.

## Worktree Diagnostics

Use the read-only diagnostics endpoint when you need to inspect stale markers, missing patches, cleanup-failed artifacts, or orphaned generated worktrees:

```text
GET /api/worktree/diagnostics
```

The shell command `/worktree` prints the same report in a concise text form. It is read-only by default and does not delete any generated artifacts.

## Artifact Open Helper

The browser opens local run artifacts through a read-only helper:

```text
GET /api/artifacts/open?path=<absolute-or-repo-relative-artifact-path>
```

The helper only serves files under the active repo's `.AgentCLI` artifact root, rejects directories, oversized files, unsupported extensions, and non-loopback web binds, and uses inline text/JSON/Markdown responses by default. Add `download=true` when an explicit browser download is desired.

## Report Export

The Run History selected-run panel can generate browser-ready summary artifacts for a completed run:

```text
GET /api/reports/export?run_id=<run-id>&format=json
GET /api/reports/export?run_id=<run-id>&format=markdown
```

Each request writes both `WEB_REPORT_EXPORT.json` and `WEB_REPORT_EXPORT.md` into the selected run directory, then returns the requested format. The export is loopback-only because it contains local artifact paths; non-loopback binds receive a redaction-boundary error instead of raw report content.
