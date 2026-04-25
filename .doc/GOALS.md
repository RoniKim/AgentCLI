# Project Goals - AgentCLI Web Console

> Product target: make AgentCLI usable from a browser for monitoring, configuration, prompt review, logs, goals, and safe runner operations.
> Design source: `docs/Design/`, especially `docs/Design/project/AgentCLI Web - A.html`.
> Current status: alpha/prototype shell. It is not yet a complete operational web runner.
> Last reviewed: 2026-04-26.

## P0 (Must-Have)

- [x] The web console direction is grounded in the checked-in design bundle under `docs/Design/`.
- [x] Repo-owned web console files exist under `web_console/` instead of running directly from the exported design prototype.
- [x] A FastAPI entry point can serve the web console and read-only JSON snapshots from `agent_runner.web`.
- [x] The dashboard no longer treats an empty timestamped run directory as an active run.
- [x] Completed runs with final reason `ok`, `prepared_only`, `project_complete`, or `all_tasks_done` are not displayed as still running.
- [x] The Config page can render the current config snapshot when schema fields such as `roles` arrive as comma-separated strings or arrays.
- [ ] Dashboard status is accurate against a real live AgentCLI process: idle, running, stopped, failed, completed, quota, current task, stages, and elapsed time must come from real artifacts or a control-plane endpoint.
- [ ] Core screens are operational, not just visual: Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, and Worktree Review.
- [ ] English and Korean UI modes exist with a persistent locale toggle and complete copy coverage for all primary views, modals, validation messages, and controls.
- [ ] Config screen can read, validate, diff, backup, save, and reload the active AgentCLI config without requiring manual CLI editing.
- [ ] Prompt screen can browse, view, edit, validate, backup, and save active profile prompts with redaction where needed.
- [ ] Goals screen can add, edit, reorder, check/uncheck, validate, backup, and save the active `GOALS.md`.
- [ ] Logs screen supports live tailing, level/stage filters, search, pause/resume, and clear distinction between no logs and loading failure.
- [ ] Runner controls are end-to-end usable from the browser in opt-in mode: start, stop, reload/restart, confirmation prompts, disabled states, and error reporting.
- [ ] Manual worktree merge/discard workflow is visible and controllable from the browser without auto-applying patches.
- [ ] LAN viewing is safe by default: read-only without opt-in controls, documented network binding, redaction, and a clear warning that authentication is not complete.
- [ ] Playwright smoke validation covers desktop and mobile rendering, Dashboard, Config, Goals, Prompts, Logs, runner controls, and both English/Korean modes.
- [x] Documentation states the current alpha limits, run commands, dependency setup, validation commands, and the path to production readiness.

## P1 (Should-Have)

- [ ] Mobile layout matches the `A_Mobile` design path without text overlap or broken controls.
- [ ] Notifications mirror AgentCLI/control-plane events with filtering and read/unread state.
- [ ] Run History can browse persisted summaries, task results, QA output, shutdown reports, and worktree outcomes.
- [ ] Keyboard navigation is complete: command palette, `g` navigation chords, Escape handling, focus states, and accessible labels.
- [ ] The CLI can optionally launch or serve the web console from a documented command.
- [ ] Web console exposes diagnostics for missing FastAPI/uvicorn dependencies and broken virtual environments.
- [ ] UI state clearly distinguishes fallback/demo data from real API data.
- [ ] External/LAN operation has an authentication plan before use outside trusted private networks.

## Completion Criteria

- All P0 items are checked from real browser and API validation, not visual inspection alone.
- The production page runs from `web_console/` and `agent_runner.web`; `docs/Design/project/` remains reference-only.
- A user can monitor a real run, inspect logs, edit Goals/Config/Prompts safely, and operate the runner from the browser with confirmations.
- The page has no blank primary views, no uncaught JavaScript errors, and no obvious layout overlap on desktop or mobile.
- AgentCLI Python compile checks and web console unit tests pass.
- Playwright screenshots/snapshots prove the key views render correctly in both English and Korean.
