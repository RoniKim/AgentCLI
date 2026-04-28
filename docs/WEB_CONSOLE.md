# AgentCLI Web Console

> Last verified: 2026-04-28 (against code).

AgentCLI has an alpha FastAPI web console for browser-based monitoring. It is repo-owned code in `web_console/` served by `agent_runner.web`; the exported design files under `docs/Design/project/` are reference input only.

This is not yet a complete operational web runner. Treat the current implementation as an alpha shell with read-only snapshots plus early runner-control plumbing.

## Current Status

Verified on 2026-04-26 with a local server and Playwright:

- Static console serving works from `agent_runner.web`.
- Read-only endpoints exist for health, status, progress, config, prompts, logs, history, and worktree review state.
- Read-only worktree diagnostics now scan `.AgentCLI/agent_runs`, the central pending marker, patch paths, cleanup-failed artifacts, and generated worktree directories without deleting anything by default.
- Additional read-only contracts now cover Goals metadata and backend log tailing.
- LAN or external binds now redact logs, log file metadata, GOALS raw text, backlog/task excerpts, config snapshots, prompt previews/content, and serialized runner arguments before the browser sees them, while the browser renders hidden/unavailable copy for those fields.
- The explicit prompt-read path stays available for the editor flow after a user selects a prompt; the inventory view remains redacted and the raw-content path is only used by that explicit editor request.
- The UI has first-pass routes for Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, and Worktree Review.
- The shell now exposes `/worktree` for the same diagnostics summary.
- Checked-in Playwright smoke coverage now exercises Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, Worktree Review, EN/KO Dashboard and Config locale switching, and a mobile-width viewport.
- Runner controls are disabled by default and require explicit opt-in.
- Config saves now reuse that opt-in and create a timestamped backup before atomic disk writes.
- Config rendering now tolerates `roles` as strings or arrays and preserves built-in order `PM, Security, Dev, QA`.
- Empty timestamped run directories are ignored so they do not appear as active runs.
- Completed runs with final reason `ok`, `prepared_only`, `project_complete`, or `all_tasks_done` are no longer displayed as still running.

Known blockers:

- Logs view live-tail, pause/resume, filtering, copy, and download are fully wired (see `web_console/app.js` `isLiveTailPaused`, level/stage/task/search filters in `web.py`); remaining work is broader UX polish.
- Default English/Korean locale toggle is implemented (`setLocale` in `app.js`); a few views and copy strings still need translation polish.
- Runner controls: T1/T2 durable runner-control events were merged 2026-04-28 (commits bc57841, c0557d1). The remaining gap is real-process end-to-end validation and stop-timeout edge-case coverage.
- The UI still needs broader Playwright coverage beyond the checked-in smoke path.
- There is no authentication layer. Treat LAN binds as trusted-network-only until authentication exists.

## Install Dependencies

Use the project virtual environment:

```powershell
cd D:\000.Work\001.Private\000.API\agent_cli
python -m pip install -r requirements.txt
```

The web server requires `fastapi` and `uvicorn`. If the venv points to a missing base Python, recreate the venv or repair it before serving.

## Localhost

```powershell
cd D:\000.Work\001.Private\000.API\agent_cli
python -m agent_runner.web `
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
python -m agent_runner.web `
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

Mutating actions require confirmation phrases. Use localhost first before enabling controls on a LAN bind.
The guarded config save endpoint uses the same opt-in, rejects unsafe or redacted placeholder writes, and writes a timestamped backup before replacing the config atomically.

## Validation

```powershell
python -B -m py_compile agent_runner\web.py tests\web_console_playwright_smoke.py
python -B -m unittest discover -s tests -p "test_web_console*.py"
python -B -m unittest discover -s tests -p "test_worktree*.py"
python -B .\tests\web_console_playwright_smoke.py
```

The web smoke path should cover the primary views, Dashboard and Config locale switching, prompt read loading, worktree review, and the mobile-width layout before marking the product complete.

## Worktree Diagnostics

Use the read-only diagnostics endpoint when you need to inspect stale markers, missing patches, cleanup-failed artifacts, or orphaned generated worktrees:

```text
GET /api/worktree/diagnostics
```

The shell command `/worktree` prints the same report in a concise text form. It is read-only by default and does not delete any generated artifacts.
