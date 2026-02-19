# Agent.md - Codex Project Guide

## Project Overview

**AgentCLI** (v2.0.0) is a CLI-first multi-agent orchestration runner that executes a PM -> Dev -> QA pipeline.
It supports dual execution backends:

- **Codex** (`codex exec` subprocess) - default backend
- **Claude Code** (Claude Agent SDK backend)

Core facts:

- **Language**: Python 3.10+
- **Entry point**: `agent_cli.py`
- **Core package**: `agent_runner/`
- **Async model**: `asyncio`
- **Schema validation**: Pydantic v2

## Architecture

```text
agent_cli.py
  - --run-now or one-shot flags -> agent_runner/main.py
  - default (no --run-now)      -> agent_runner/shell.py (interactive shell)

agent_runner/main.py
  -> parse_args() in agent_runner/cli.py
  -> run() in agent_runner/runner_entry.py

runner_entry.py
  -> preflight checks (preflight.py)
  -> backend dispatch (backends/factory.py)
     - codex      -> backends/codex_runner.py -> cycle.py
     - claudecode -> backends/claudecode_runner.py -> backends/claudecode.py
  -> failover chain support + signal handling + process cleanup

codex execution path
  cycle.py -> codex_exec.py (codex exec subprocess wrapper)

backend-agnostic pipeline layer
  pipeline/manager.py      (PipelineManager, _PROPAGATE_STOP_REASONS)
  pipeline/session.py      (PipelineSession)
  pipeline/stage_registry.py (built-in + plugin stage wiring)
  pipeline/stages/*        (PM, Dev, QA, Security stage interfaces)
```

## Running the Project

```bash
# Interactive shell (default)
python agent_cli.py

# Interactive shell with repo preset
python agent_cli.py --repo <path>

# Immediate execution
python agent_cli.py --run-now --repo <path>

# Configuration wizard
python agent_cli.py --wizard --repo <path>

# Explicit codex backend (default anyway)
python agent_cli.py --run-now --repo <path> --execution-backend codex

# Claude backend smoke test
python -m agent_runner.backends.claude_smoke_test --prompt "hi"
```

## Dependencies

```text
openai>=1.0.0
openai-agents>=0.0.0
claude-agent-sdk>=0.1.0
pydantic>=2.0.0
prompt_toolkit>=3.0.0
```

## Configuration System

Priority chain:

```text
CLI args > Config JSON > DEFAULTS (agent_runner/cli.py)
```

Key paths (resolved via `agent_runner/config.py`):

- Config: `{AGENTCLI_HOME}/configs/{repo-slug}.json`
- Prompts: `{AGENTCLI_HOME}/prompts/{repo-slug}/`
- Task history DB: `{AGENTCLI_HOME}/databases/{repo-slug}.db`
- Legacy config fallback (read): `{repo}/.AgentCLI/agent_config.json` then `{repo}/.doc/agent_config.json`

Default execution/backend highlights:

- `execution_backend`: `"codex"` (default)
- Codex models default to `gpt-5.1-codex-mini` for PM/Dev/QA/reporter
- Dev escalation tiers:
  - `dev_model_tier1 = gpt-5.1-codex`
  - `dev_model_tier2 = gpt-5.2-codex`
- Failover can chain backends: `["codex", "claudecode"]`

## Code Conventions

### Style

- Use type hints consistently (`from __future__ import annotations` is standard)
- Naming:
  - functions: `snake_case`
  - classes: `PascalCase`
  - constants: `UPPER_SNAKE_CASE`
- Prefer async functions for I/O and long-running operations

### Key Patterns to Follow

1. **Use stop reason constants from `agent_runner/utils.py`**
   - `STOP_REASON_QUOTA_UTILIZATION`
   - `STOP_REASON_QUOTA`
   - `STOP_REASON_STOP_FILE`
   - `STOP_REASON_ALL_TASKS_DONE`
   - `STOP_REASON_PROJECT_COMPLETE`
   - `STOP_REASON_ALL_TASKS_ATTEMPTED`
   - `STOP_REASON_PREPARED_ONLY`
   - `STOP_REASON_NO_TASKS`
   - `STOP_REASON_PM_REFRESH_NO_BACKLOG`
   - `STOP_REASON_IDLE_EXIT`
   - `STOP_REASON_OK`

2. **Reason propagation for outer loops**
   - `pipeline/manager.py` uses `_PROPAGATE_STOP_REASONS` (frozenset) to forward terminal reasons such as `all_tasks_done` and `project_complete`.

3. **Structured logging over raw print**
   - Use `StructuredLogger` methods (`info`, `error`, `task_start`, etc.) when adding new pipeline logs.

4. **JSON parsing resilience**
   - Parse PM/QA structured responses through `agent_runner/structured.py` helpers (`parse_pm_output_with_errors`, `parse_qa_followups`, etc.).

5. **Stage interface contract**
   - Implement pipeline stages by subclassing `pipeline/stages/base.py::Stage`.
   - Return `StageOutcome.ok()`, `.skip()`, `.stop()`, or `.fail()`.

6. **Backend interface contract**
   - New backends must subclass `backends/base.py::AbstractAgentRunner`.

7. **Budget and escalation controls**
   - Respect `budgets` limits in config (`max_*` keys).
   - `0` means unlimited for those counters.

8. **Git safety**
   - Prefer safe operations/checkpoints (`gitops.py` helpers).
   - Never add destructive reset/rollback behavior without explicit user intent.

9. **Process lifecycle safety**
   - Register and clean child processes through `process_guard.py` path already wired in entrypoints.

10. **Goals auto-refresh rescue**
   - Use `goals.py` helpers (`GOALS_REFRESH_RESCUABLE_REASONS`, `should_attempt_goals_refresh`) for controlled refresh attempts.

## State and Artifacts

Primary run outputs live in:

```text
{repo}/.AgentCLI/agent_runs/<timestamp>/
```

Common artifacts:

- `BACKLOG.json`, `BACKLOG.md`
- `STATE.json`
- `PM_OUTPUT_cycle_XXX.json`
- `COMPLETION_STATUS.json`
- `run_summary.json`, `run_summary_cycle_XXX.json`, `last_run_summary.json`
- `SHUTDOWN_CONTEXT.json`, `SHUTDOWN_REPORT.md`
- `PM_SHUTDOWN_REPORT_OUTPUT.txt`
- `metrics.jsonl`
- `logs/debug.log`, `logs/error.log`, `logs/events.jsonl`
- `HEARTBEAT`
- `tasks/<task-id>/attempt_XX/` artifacts

Other runtime roots:

- `{repo}/.AgentCLI/PM_CACHE/`
- `{repo}/.AgentCLI/todo/`
- `{repo}/.AgentCLI/skills/` (snapshot)
- `{repo}/.AgentCLI/agent_cli_history.txt`

Design docs typically live under:

- `{repo}/.doc/GOALS.md`
- `{repo}/.doc/Docs/`
- `{repo}/.doc/DOCS_DIGEST.md`
- `{repo}/.doc/REPO_INVENTORY.md`

## Important Warnings

- Keep `DEFAULTS` key compatibility in `agent_runner/cli.py`; shell/run paths rely on those keys being present.
- `cycle.py` and `backends/claudecode.py` are large and high-risk to refactor; validate both interactive and `--run-now` flows after changes.
- `process_guard.py` contains Windows-specific behavior (Job Object/process tree handling).
- Always resolve config/prompt paths via `config.py` helpers instead of hardcoding.
- `.AgentCLI/` and `.doc/` are runtime/documentation work areas and are commonly gitignored.

## Current Hotspots

- `agent_runner/cycle.py` (very large; nested async orchestration)
- `agent_runner/backends/claudecode.py` (feature parity layer, also very large)
- `agent_runner/cli.py` (many defaults and argument merge rules)

## Code Verification

Recommended minimum checks after edits:

```bash
# 1) Syntax check touched files
python -m py_compile <changed_file.py>

# 2) Run project tests (if present)
python -m pytest -q

# 3) Smoke run (codex default backend)
python agent_cli.py --run-now --repo <path> --non-interactive --autopilot
```

If you cannot run one of these checks, document that gap in your change notes.

## Directory Structure Summary

```text
agent_cli.py
CLAUDE.md
Agent.md
README.md
requirements.txt

agent_runner/
  cli.py
  main.py
  runner_entry.py
  cycle.py
  codex_exec.py
  shell.py
  config.py
  state.py
  goals.py
  utils.py
  process_guard.py
  reporting.py
  pipeline/
  backends/
  skills/

docs/
templates/
prompts/
configs/
databases/
```
