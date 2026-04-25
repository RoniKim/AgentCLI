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

## Web Console Target

The new work target is a real AgentCLI web console based on `docs/Design/`.

Canonical design source:

- `docs/Design/README.md` says the primary open design is `docs/Design/project/AgentCLI Web - A.html`.
- That file imports `shared/mock-data.js`, `shared/primitives.jsx`, `directions/direction-a.jsx`, and `directions/direction-a-screens.jsx`.
- Treat the design bundle as read-only reference material unless a task explicitly says otherwise.

Recommended first production shape:

- Add a repo-owned web surface under `web/` or another clearly documented path.
- Keep the first version local-first and deterministic.
- Use explicit adapter modules for AgentCLI-shaped data rather than hardcoding UI state inside components.
- If a frontend build tool is introduced, add the manifest and docs in the same task, but do not install dependencies from inside AgentCLI.

Suggested data boundaries:

- Active run: id, repo, branch, backend, stage, iteration, progress, elapsed time, budget, quota, token totals.
- Stages: PM/Dev/QA statuses, duration, model, current task.
- Backlog: task id, title, priority, status, estimate, files, skills.
- Goals: P0/P1 checkbox groups from `.doc/GOALS.md`.
- Config: AgentCLI config keys from `agent_runner/cli.py::DEFAULTS` plus saved JSON.
- Logs: `cycle_summary.log`, structured events, metrics JSONL.
- Run history: `run_summary*.json`, shutdown reports, completion status.

## Integration Direction

Start with a static or mock-backed implementation that matches the design exactly enough to validate layout and workflows. Then add real artifact readers or a local API in later tasks. Do not invent remote persistence, cloud auth, or destructive controls for the first pass.

Safe control actions must be explicit. Stop/restart/run commands should require confirmation and should call a local backend only after that backend contract is implemented.
