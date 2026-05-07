# AgentCLI Architecture Notes

## Current Product

AgentCLI is a Python CLI-first multi-agent runner. It orchestrates a `PM -> Dev -> QA` pipeline against a target repository and can use either the Codex CLI backend or the Claude Code backend.

Primary runtime entry points:

- `agent_cli.py`: top-level dispatcher.
- `agent_runner/main.py`: immediate `--run-now` execution.
- `agent_runner/shell.py`: interactive shell.
- `agent_runner/runner_entry.py`: backend dispatch, failover, signal handling.

Backend and pipeline layers:

- `agent_runner/backends/codex_runner.py` delegates to `agent_runner/cycle.py`.
- `agent_runner/backends/claudecode_runner.py` delegates to `agent_runner/backends/claudecode.py`.
- `agent_runner/pipeline/` contains backend-neutral stage orchestration.
- Built-in stages are PM, Dev, QA, and Security.

Runtime artifacts:

- `.AgentCLI/agent_runs/<timestamp>/` stores `BACKLOG.json`, `STATE.json`, logs, notes, QA files, summaries, and metrics.
- `.AgentCLI/PM_CACHE/PROJECT_ANALYSIS.md` stores PM analysis.
- `.doc/GOALS.md` is the canonical project completion target.
- `.doc/Docs/` and `.doc/DOCS_DIGEST.md` provide persistent project context to PM/Dev/QA.

## Web Console

AgentCLI now has a repo-owned production web console under `web_console/`, served by the local FastAPI app in `agent_runner.web`.

Canonical design source:

- `docs/Design/README.md` says the primary open design is `docs/Design/project/AgentCLI Web - A.html`.
- That file imports `shared/mock-data.js`, `shared/primitives.jsx`, `directions/direction-a.jsx`, and `directions/direction-a-screens.jsx`.
- Treat the design bundle as read-only reference material unless a task explicitly says otherwise.

Current production shape:

- Static assets live in `web_console/`.
- The FastAPI entry point in `agent_runner.web` serves the UI and JSON APIs.
- The app stays local-first, binds to `127.0.0.1` by default, and uses explicit LAN/trusted-access safeguards when exposed beyond localhost.
- Read-only views are available for status, progress, logs, goals, config, prompts, history, reports, retention, experience, PR queue, worktree state, and instance health.
- Mutating endpoints for runner control, config/prompt/goals/TODO edits, PR queue actions, and worktree merge/discard are guarded by explicit opt-in controls, local/trusted access checks, and confirmation-oriented UI.
- One repo should have one active web instance lock at a time.

Data/API boundaries:

- Active run: id, repo, branch, backend, stage, iteration, progress, elapsed time, budget, quota, token totals.
- Stages: PM/Dev/QA statuses, duration, model, current task.
- Backlog: task id, title, priority, status, estimate, files, skills.
- Goals: P0/P1 checkbox groups from `.doc/GOALS.md`.
- Config: AgentCLI config keys from `agent_runner/cli.py::DEFAULTS` plus saved JSON.
- Logs: `cycle_summary.log`, structured events, metrics JSONL.
- Run history: `run_summary*.json`, shutdown reports, completion status.
- Operations: TODO, Skills, Claude advanced controls, MCP, plugin stages, enterprise profile diagnostics.
- Worktree/PR queue: pending merge packets, apply-check diagnostics, cleanup states, validation and merge/discard controls.
- Instance health: process guard state, child PID tracking, handle/process diagnostic warnings, web lock state, stale artifact risk.

## Integration Direction

Continue evolving the existing `web_console/` and `agent_runner.web` implementation. Do not introduce a separate `web/` app or rebuild from `docs/Design/project/` unless a task explicitly changes the production path.

Safe control actions must remain explicit. Stop/restart/run commands, config/prompt/GOALS/TODO saves, worktree merge/discard, and PR queue actions must preserve the existing opt-in, local/trusted-access, and audit/diagnostic contracts.
