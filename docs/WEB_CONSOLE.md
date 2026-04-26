# AgentCLI Web Console

AgentCLI has an alpha FastAPI web console for browser-based monitoring. It is repo-owned code in `web_console/` served by `agent_runner.web`; the exported design files under `docs/Design/project/` are reference input only.

This is not yet a complete operational web runner. Treat the current implementation as an alpha shell with read-only snapshots plus early runner-control plumbing.

## Current Status

Verified on 2026-04-26 with a local server and Playwright:

- Static console serving works from `agent_runner.web`.
- Read-only endpoints exist for health, status, progress, config, prompts, logs, history, and worktree review state.
- Additional read-only contracts now cover Goals metadata and backend log tailing.
- The UI has first-pass routes for Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, and Worktree Review.
- Runner controls are disabled by default and require explicit opt-in.
- Config rendering now tolerates `roles` as either `PM,Dev,QA` or `["PM", "Dev", "QA"]`.
- Empty timestamped run directories are ignored so they do not appear as active runs.
- Completed runs with final reason `ok`, `prepared_only`, `project_complete`, or `all_tasks_done` are no longer displayed as still running.

Known blockers:

- Goals, Config, and Prompts are not yet full edit/save workflows.
- The Logs view still needs frontend live-tail fetch, pause/resume, filtering, copy, and download behavior.
- English/Korean locale switching is not implemented.
- Runner controls need end-to-end validation against a real AgentCLI process before they should be considered usable.
- The UI still needs Playwright coverage across primary views, mobile width, and both future locales.
- There is no authentication layer. Do not expose this server to an untrusted network.

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

Bind to all interfaces only on a trusted private network:

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

## Validation

```powershell
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m compileall agent_runner agent_cli.py
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_web_console*.py"
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_worktree*.py"
```

The web smoke path should cover desktop/mobile layout, navigation, command palette, Config rendering, Goals/Prompts views, log filtering, runner controls, and worktree review before marking the product complete.
