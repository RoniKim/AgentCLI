# Project Goals - AgentCLI Web Console

> Prepared for running AgentCLI against this repository itself.
> Goal source: implement the design bundle under `docs/Design/` as a real web page.
> Primary design target: `docs/Design/project/AgentCLI Web - A.html`.
> Last reviewed: 2026-04-26.

## P0 (Must-Have)

- [x] Direction A web console exists as repo-owned production code, not just the exported prototype in `docs/Design/project/`.
- [x] Implementation starts from the real design source: read `docs/Design/README.md`, `docs/Design/project/AgentCLI Web - A.html`, and its imported shared/direction files before building production UI.
- [x] Desktop shell matches the primary design: top bar, left navigation, run status, quota/budget indicators, stop confirmation, and command palette.
- [x] Web accessibility is the product goal: AgentCLI can be observed and operated from a browser instead of requiring the CLI shell for routine monitoring.
- [x] Core screens are implemented from the design bundle: Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, and Notifications.
- [x] Visual language is preserved from Direction A: dark terminal-style palette, compact information density, JetBrains Mono/Inter typography fallback, 2-4px radii, thin borders, status colors, and live-running accents.
- [ ] Mobile preview/responsive layout is implemented from the `A_Mobile` design path with no incoherent text overlap at mobile widths.
- [x] Prototype-only dependencies are removed from the production page path: no Babel-in-browser runtime, no unpinned CDN-only app logic, and no design-source mutation.
- [x] UI data is wired through explicit AgentCLI-shaped adapters for active run, stages, backlog, goals, config, prompts, logs, notifications, metrics, and run history.
- [x] FastAPI-based local web server can serve the production web console and expose AgentCLI progress, logs, config, prompts, and run state from the current notebook.
- [x] Web runner controls support status, start, stop, and restart/reload flows through safe backend APIs with confirmation and clear disabled/error states.
- [x] Worktree isolation results are visible and controlled: pending merge/discard state, patch path, source branch/base/head, and review-required status are shown before any source-repo change is applied.
- [ ] Config and Prompt screens can read, validate, diff, backup, and save the repo-local AgentCLI config and prompt files used by the active profile.
- [x] Web server default mode is safe for LAN/external viewing: progress/log endpoints are read-only by default, and mutating runner/config/prompt actions require explicit opt-in and confirmation.
- [x] Safe local actions are represented with clear boundaries: start/status/stop/config/goals/prompts controls must not perform destructive filesystem or git operations without explicit confirmation.
- [x] Validation path is documented and executable for the chosen implementation style, including at least `.venv/Scripts/python.exe -m compileall agent_runner agent_cli.py` and a web smoke check.
- [x] README or dedicated docs explain how to run the FastAPI web server, bind it for localhost or LAN access, and where future API/control-plane integration should connect.

## P1 (Should-Have)

- [x] Live log tailing can read AgentCLI run artifacts from `.AgentCLI/agent_runs/` or a local control-plane endpoint.
- [x] FastAPI server supports configurable host/port, latest-run discovery, health/status endpoints, and static asset serving for the console.
- [ ] Web dashboard exposes the manual worktree merge workflow currently available in CLI (`/merge-worktree`, `/discard-worktree`) as an explicit review/approval surface.
- [ ] Goals and Config screens support validated edits against the real `GOALS.md` and AgentCLI config schema.
- [ ] Prompt management supports profile-aware prompt browsing, editing, validation, backup/restore, and change history.
- [x] Run History can browse persisted run summaries, task results, QA output, and shutdown reports.
- [ ] Notifications mirror Telegram/control-plane events with filtering and read/unread state.
- [x] Keyboard navigation is complete: command palette, `g` navigation chords, Escape handling, focus states, and accessible button labels.
- [ ] Browser smoke tests cover desktop and mobile rendering, command palette, stop modal, log filtering, and navigation.
- [ ] The CLI can optionally launch or serve the web console from a documented command.

## Completion Criteria

- All P0 items are checked.
- The production web page can be opened or served without relying on `docs/Design/project/` as runtime source.
- Dashboard, run progress, logs, config, prompts, and runner controls are available from the browser with safe confirmations.
- Worktree merge/discard approval is never automatic in manual mode and is visible from CLI now, with a documented path for Web dashboard approval.
- AgentCLI Python compile check passes.
- The web page has no blank primary views and no obvious layout overlap on desktop or mobile widths.
- Remaining P1 items are documented as follow-up work, not hidden blockers.
