# Web Console Design Target

## Source Of Truth

Use `docs/Design/project/AgentCLI Web - A.html` as the canonical target. The design bundle says this file was open when the handoff was exported.

When changing implementation, read `docs/Design/README.md`, then read `docs/Design/project/AgentCLI Web - A.html` in full, then follow its imports.

Required imports to understand before implementation:

- `docs/Design/project/shared/mock-data.js`
- `docs/Design/project/shared/primitives.jsx`
- `docs/Design/project/directions/direction-a.jsx`
- `docs/Design/project/directions/direction-a-screens.jsx`

Direction B and Direction C are useful alternatives, but P0 tracks Direction A unless the user changes the target.

## Current Implementation Baseline

As of 2026-05-07, Direction A remains the visual and workflow reference, but the production implementation is no longer a future mock-backed target.

- Production static assets live under `web_console/`.
- The local FastAPI server is implemented in `agent_runner.web`.
- The first read-only server pass has been superseded by guarded local mutation support for runner controls, config, prompts, goals, TODO, PR queue, and worktree actions.
- Guarded mutation must keep explicit opt-in controls, local/trusted access checks, confirmation-oriented UI, and audit-friendly status/error payloads.
- Browser proof is covered by `tests/web_console_playwright_smoke.py` when it runs tests instead of skipping because of local browser/runtime constraints.

## Shell Structure

Desktop shell:

- 44px top bar across the viewport.
- 220px left sidebar.
- Main content area scrolls independently.
- Top bar shows product name, repo, active run id, command palette trigger, stage status, quota, budget, and stop action.
- Sidebar groups: Run, Project, History, and preview links.

Navigation targets:

- Dashboard
- Pipeline
- Logs
- Backlog
- Goals
- Config
- Prompts
- Run History
- Notifications
- Worktree Review
- Landing preview
- Mobile preview

Interactions:

- `/`, Cmd+K, or Ctrl+K toggles command palette.
- Escape closes command palette and stop modal.
- `g` navigation chords switch screens.
- Stop action opens a confirmation modal before any action.
- Worktree merge/discard actions are review-required and must not auto-apply in manual mode.
- Logs screen supports pause/resume and level filtering.
- Goals and Config screens include editable local UI patterns in the design.

## Visual System

Preserve the Direction A palette and density:

- Background: `#0a0c0a`
- Primary surface: `#10130f`
- Secondary surface: `#161a14`
- Border: `#1f2620`
- Elevated border: `#2a3328`
- Text: `#c8d2be`
- Dim text: `#7a8275`
- Subtle text: `#505a4c`
- Accent: `#7ee38a`
- Warning: `#f3c26b`
- Error: `#e87a6a`
- Info: `#7ab0e8`

Typography:

- Sans: Inter with system-ui fallback.
- Mono: JetBrains Mono with ui-monospace fallback.
- Keep compact UI type sizes; avoid oversized hero typography inside console panels.

Layout:

- Use thin 1px borders.
- Use small radii, usually 2-4px.
- Keep cards/panels practical and dense.
- Avoid nested decorative cards.
- Avoid gradient-orb/background decoration.

## Data Contract

The web page should treat `shared/mock-data.js` as a shape reference, not a permanent global dependency.

Core data groups:

- `activeRun`
- `stages`
- `backlog`
- `goals`
- `runs`
- `metrics`
- `logs`
- `notifications`
- `config`
- `configSchema`
- `prompts`
- `worktreeMerge`

Adapters should make it easy to later replace mock data with local files or an API:

- `.AgentCLI/agent_runs/<run>/STATE.json`
- `.AgentCLI/agent_runs/<run>/BACKLOG.json`
- `.AgentCLI/agent_runs/<run>/metrics.jsonl`
- `.AgentCLI/agent_runs/<run>/cycle_summary.log`
- `.doc/GOALS.md`
- AgentCLI config JSON
- AgentCLI prompt directories
- `.AgentCLI/WORKTREE_MERGE_PENDING.json` and run-local pending merge artifacts

## FastAPI Server Target

The intended server shape is a local FastAPI app that can:

- Serve the production web console static assets.
- Expose health/status endpoints.
- Discover the latest `.AgentCLI/agent_runs/` directory for a configured repo.
- Return read-only active run, stage, backlog, goals, logs, notifications, metrics, config/prompt summaries, pending worktree merge state, and run-history data.
- Bind to `127.0.0.1` by default and optionally to `0.0.0.0` or a LAN interface when the user wants external viewing from another machine.

Historical first-pass guidance was read-only. The current server includes guarded mutation endpoints, so any new mutating surface must follow the same explicit tasking, confirmation UX, local/trusted-access checks, and documented safety contract.

## Non-Goals

- Do not build cloud hosting.
- Do not add authentication casually; follow `docs/AUTHENTICATION_PLAN.md` before widening exposure beyond trusted local/LAN operation.
- Do not implement destructive run control without a backend contract, explicit opt-in, and confirmation.
- Do not auto-apply isolated worktree results in manual mode; expose pending merge/discard status for user approval.
- Do not rewrite AgentCLI's Python backend or create a second web app just to render a new page.
