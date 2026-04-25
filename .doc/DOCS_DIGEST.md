# DOCS DIGEST

This digest is a compact index for AgentCLI PM/Dev/QA runs.

## Inventory

- `.doc/Docs/ARCHITECTURE.md`
- `.doc/Docs/CONVENTIONS.md`
- `.doc/Docs/CURRENT_STATE.md`
- `.doc/Docs/WEB_CONSOLE_TARGET.md`

## ARCHITECTURE.md

- Current Product: Python CLI-first multi-agent runner, PM -> Dev -> QA.
- Runtime entry points: `agent_cli.py`, `agent_runner/main.py`, `agent_runner/shell.py`, `agent_runner/runner_entry.py`.
- Backend layers: Codex in `agent_runner/cycle.py`, Claude Code in `agent_runner/backends/claudecode.py`, pipeline in `agent_runner/pipeline/`.
- Runtime artifacts: `.AgentCLI/agent_runs/`, `.AgentCLI/PM_CACHE/`, `.doc/GOALS.md`.
- Web target: implement `docs/Design/project/AgentCLI Web - A.html` as production web console.
- Server target: add a FastAPI local web server for static console assets and read-only AgentCLI progress, logs, config, prompts, run state, and pending worktree merge data.
- Data boundaries: active run, stages, backlog, goals, config, prompts, logs, history, metrics, notifications, worktree merge state.

## CONVENTIONS.md

- Keep task scope narrow and tied to GOALS.
- Treat `docs/Design/project/` as read-only reference.
- Preserve Windows support.
- Web console should match Direction A, avoid Babel-in-browser production runtime, and avoid CDN-only app logic.
- Dev must not install packages; declare dependencies if needed.
- QA verifies web rendering, Python compile safety, and manual worktree merge/discard safety.

## CURRENT_STATE.md

- Mature Python CLI exists.
- No production web app exists yet.
- `docs/Design/` contains the design export and is currently working-tree context.
- Project-specific config and prompts have been created for Codex-only self-runs.
- Codex CLI is the intended backend for this profile; `/doctor` should confirm it before each run.

## WEB_CONSOLE_TARGET.md

- Primary design source: `docs/Design/project/AgentCLI Web - A.html`.
- Required reading order: `docs/Design/README.md`, then `docs/Design/project/AgentCLI Web - A.html`, then imported shared/direction files.
- Required imports: `shared/mock-data.js`, `shared/primitives.jsx`, `directions/direction-a.jsx`, `directions/direction-a-screens.jsx`.
- Required shell: 44px top bar, 220px sidebar, scrollable main area.
- Required screens: Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, Worktree Review, Landing preview, Mobile preview.
- Preserve Direction A palette, typography, density, small radii, thin borders, and keyboard interactions.
- FastAPI server target: serve static console assets, expose health/status/latest-run/read-only progress endpoints, config/prompt summaries, pending worktree merge state, default to localhost, and allow explicit LAN binding.
- Do not expose destructive run-control, config/prompt mutation, or worktree merge/discard endpoints without explicit opt-in, confirmation, and a documented backend contract.
