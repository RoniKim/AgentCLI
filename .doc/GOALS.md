# Project Goals - AgentCLI Web Console

> Prepared for running AgentCLI against this repository itself.
> Goal source: implement the design bundle under `docs/Design/` as a real web page.
> Primary design target: `docs/Design/project/AgentCLI Web - A.html`.
> Last reviewed: 2026-04-25.

## P0 (Must-Have)

- [ ] Direction A web console exists as repo-owned production code, not just the exported prototype in `docs/Design/project/`.
- [ ] Desktop shell matches the primary design: top bar, left navigation, run status, quota/budget indicators, stop confirmation, and command palette.
- [ ] Core screens are implemented from the design bundle: Dashboard, Pipeline, Logs, Backlog, Goals, Config, Run History, and Notifications.
- [ ] Visual language is preserved from Direction A: dark terminal-style palette, compact information density, JetBrains Mono/Inter typography fallback, 2-4px radii, thin borders, status colors, and live-running accents.
- [ ] Mobile preview/responsive layout is implemented from the `A_Mobile` design path with no incoherent text overlap at mobile widths.
- [ ] Prototype-only dependencies are removed from the production page path: no Babel-in-browser runtime, no unpinned CDN-only app logic, and no design-source mutation.
- [ ] UI data is wired through explicit AgentCLI-shaped adapters for active run, stages, backlog, goals, config, logs, notifications, metrics, and run history.
- [ ] FastAPI-based local web server can serve the production web console and expose read-only AgentCLI progress data from the current notebook.
- [ ] Web server default mode is safe for LAN/external viewing: read-only progress endpoints first, no destructive run-control endpoints exposed without explicit opt-in and confirmation.
- [ ] Safe local actions are represented with clear boundaries: start/status/stop/config/goals controls must not perform destructive filesystem or git operations without explicit confirmation.
- [ ] Validation path is documented and executable for the chosen implementation style, including at least `.venv/Scripts/python.exe -m compileall agent_runner agent_cli.py` and a web smoke check.
- [ ] README or dedicated docs explain how to run the FastAPI web server, bind it for localhost or LAN access, and where future API/control-plane integration should connect.

## P1 (Should-Have)

- [ ] Live log tailing can read AgentCLI run artifacts from `.AgentCLI/agent_runs/` or a local control-plane endpoint.
- [ ] FastAPI server supports configurable host/port, latest-run discovery, health/status endpoints, and static asset serving for the console.
- [ ] Goals and Config screens support validated edits against the real `GOALS.md` and AgentCLI config schema.
- [ ] Run History can browse persisted run summaries, task results, QA output, and shutdown reports.
- [ ] Notifications mirror Telegram/control-plane events with filtering and read/unread state.
- [ ] Keyboard navigation is complete: command palette, `g` navigation chords, Escape handling, focus states, and accessible button labels.
- [ ] Browser smoke tests cover desktop and mobile rendering, command palette, stop modal, log filtering, and navigation.
- [ ] The CLI can optionally launch or serve the web console from a documented command.

## Completion Criteria

- All P0 items are checked.
- The production web page can be opened or served without relying on `docs/Design/project/` as runtime source.
- AgentCLI Python compile check passes.
- The web page has no blank primary views and no obvious layout overlap on desktop or mobile widths.
- Remaining P1 items are documented as follow-up work, not hidden blockers.
