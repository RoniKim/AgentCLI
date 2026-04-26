# AgentCLI Web Console

AgentCLI has an alpha FastAPI web console for browser-based monitoring. It is repo-owned code in `web_console/` served by `agent_runner.web`; the exported design files under `docs/Design/project/` are reference input only.

This is not yet a complete operational web runner. Treat the current implementation as an alpha shell with read-only snapshots plus early runner-control plumbing.

## Current Status

Verified on 2026-04-26 with a local server and Playwright:

- Static console serving works from `agent_runner.web`.
- Read-only endpoints exist for health, status, progress, config, prompts, logs, history, and worktree review state.
- Additional read-only contracts now cover Goals metadata and backend log tailing.
- The UI has first-pass routes for Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, and Worktree Review.
- Checked-in Playwright smoke coverage now exercises Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, Worktree Review, EN/KO Dashboard and Config locale switching, and a mobile-width viewport.
- Runner controls are disabled by default and require explicit opt-in.
- Config saves now reuse that opt-in and create a timestamped backup before atomic disk writes.
- Config rendering now tolerates `roles` as either `PM,Dev,QA` or `["PM", "Dev", "QA"]`.
- Empty timestamped run directories are ignored so they do not appear as active runs.
- Completed runs with final reason `ok`, `prepared_only`, `project_complete`, or `all_tasks_done` are no longer displayed as still running.

Known blockers:

- Goals and Prompts are not yet full edit/save workflows.
- The Logs view still needs frontend live-tail fetch, pause/resume, filtering, copy, and download behavior.
- Broader English/Korean locale coverage still needs more views and copy polish.
- Runner controls need end-to-end validation against a real AgentCLI process before they should be considered usable.
- The UI still needs broader Playwright coverage beyond the checked-in smoke path.
- There is no authentication layer. Treat LAN binds as trusted-network-only until authentication exists.

## Install Dependencies

Use the project virtual environment:

```powershell
cd D:\999.AgentCLI
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

The web server requires `fastapi` and `uvicorn`. If the venv points to a missing base Python, recreate the venv or repair it before serving.

## Localhost

```powershell
cd D:\999.AgentCLI
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m agent_runner.web `
  --repo "D:\999.AgentCLI" `
  --config-path "D:\999.AgentCLI\configs\AgentCLI-86741102.json" `
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
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m agent_runner.web `
  --repo "D:\999.AgentCLI" `
  --config-path "D:\999.AgentCLI\configs\AgentCLI-86741102.json" `
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
& "D:\999.AgentCLI\.venv\Scripts\python.exe" "D:\999.AgentCLI\tests\web_console_playwright_smoke.py"
```

If Playwright is missing, the smoke skips cleanly and prints the optional setup command instead of installing packages.

## Optional Playwright Setup

Install the optional browser test dependency and Chromium binary only if you want to run the smoke locally:

```powershell
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m pip install playwright
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m playwright install chromium
```

## Runner Controls

Runner controls are disabled by default. Enable them explicitly:

```powershell
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m agent_runner.web `
  --repo "D:\999.AgentCLI" `
  --config-path "D:\999.AgentCLI\configs\AgentCLI-86741102.json" `
  --host 127.0.0.1 `
  --port 8000 `
  --enable-runner-controls
```

Mutating actions require confirmation phrases. Use localhost first before enabling controls on a LAN bind.
The guarded config save endpoint uses the same opt-in, rejects unsafe or redacted placeholder writes, and writes a timestamped backup before replacing the config atomically.

## Validation

```powershell
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m compileall agent_runner agent_cli.py
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_web_console*.py"
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_worktree*.py"
& "D:\999.AgentCLI\.venv\Scripts\python.exe" "D:\999.AgentCLI\tests\web_console_playwright_smoke.py"
```

The web smoke path should cover the primary views, Dashboard and Config locale switching, prompt read loading, worktree review, and the mobile-width layout before marking the product complete.
