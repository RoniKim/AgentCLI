# Web Console Review Notes - 2026-04-26

## Review Method

- Started the FastAPI console locally and inspected it with Playwright.
- Checked `/api/health`, `/api/status`, `/api/progress`, and `/api/config`.
- Opened Dashboard and Config routes in a real browser context.

## What Exists

- `agent_runner.web` serves static assets from `web_console/`.
- The browser UI is a first-pass implementation of the Direction A design.
- Read-only API adapters exist for status, progress, logs, backlog, goals, config, prompts, notifications, metrics, history, and worktree review.
- Runner control APIs exist but are disabled by default.

## Problems Found

- The dashboard treated an empty latest run directory as `running`.
- A completed run with final reason `ok` could still be displayed as active.
- Config crashed when `roles` was loaded as the legacy string `PM,Dev,QA` instead of an array.
- The UI is still mostly a prototype: edit/save flows for Goals, Config, and Prompts are incomplete.
- English/Korean switching is not implemented.

## Fixes Applied

- Empty run directories are ignored unless they contain observable AgentCLI artifacts.
- Successful final reasons now map to `success` instead of falling through to `running`.
- Stage duration fallback no longer invents PM/Dev durations when there is no elapsed run.
- Idle snapshots now publish `stage: idle` and `iteration: 0` instead of defaulting to Dev iteration 1.
- Config adapter normalizes string/list multienum values and common boolean/number string values.
- Goals were reset to reflect alpha status instead of overclaiming production usability.

## Post-Fix Browser Check

- Dashboard on `http://127.0.0.1:8767/` showed `no-run`, `idle`, `iter 0/5`, and no active run.
- Config on `http://127.0.0.1:8767/#config` rendered without the previous `fmtList(...).join` JavaScript error.
- The current config still comes from read-only API data; browser save flows are not production-ready.

## Remaining Production Gates

- Verify against a real live AgentCLI run, not only persisted artifacts.
- Implement edit/save/backup flows for Goals, Config, and Prompts.
- Add English/Korean locale coverage.
- Add Playwright smoke coverage for desktop, mobile, Config, Goals, Prompts, Logs, runner controls, and locale switching.
- Add authentication or a documented private-network-only deployment model before external use.
