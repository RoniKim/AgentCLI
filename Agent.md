# Agent.md - AgentCLI Codex Guide

## Project Overview

AgentCLI is a CLI-first multi-agent orchestration runner for a PM -> Dev -> QA pipeline.

Current codebase signals:

- Version: `2.0.0` (`agent_runner/version.py`)
- Main language: Python
- Runtime style: async orchestration with subprocess-based tool execution
- Default execution backend: `codex`
- Secondary backend: `claudecode`

Current repository shape (from code scan):

- Python files: 64
- Markdown files: 39
- Largest modules:
  - `agent_runner/cycle.py` (~2493 lines)
  - `agent_runner/backends/claudecode.py` (~2692 lines)
  - `agent_runner/cli.py` (~812 lines)
  - `agent_runner/shell.py` (~876 lines)

## Entry Modes

Entry point: `agent_cli.py`

Execution behavior:

- Default (no `--run-now`): interactive shell mode via `agent_runner/shell.py`
- Immediate run (`--run-now`) or one-shot flags (`--wizard`, `--init-prompts`, help flags): `agent_runner/main.py`
- `main.py` parses config/args and delegates to `agent_runner/runner_entry.py`

Control flow summary:

```text
agent_cli.py
  -> (default) shell_main()
  -> (--run-now / one-shot flags) main()

main()
  -> parse_args() in cli.py
  -> run() in runner_entry.py

runner_entry.run()
  -> process_guard init
  -> run_dir ensure
  -> backend dispatch (with optional failover)
```

## Backend Architecture

Backend interface:

- `agent_runner/backends/base.py::AbstractAgentRunner`
- Factory: `agent_runner/backends/factory.py::get_runner()`

Backends:

- `codex`:
  - Runner: `agent_runner/backends/codex_runner.py`
  - Core pipeline: `agent_runner/cycle.py`
  - LLM execution wrapper: `agent_runner/codex_exec.py` (`codex exec --json`)
- `claudecode`:
  - Runner: `agent_runner/backends/claudecode_runner.py`
  - Core pipeline: `agent_runner/backends/claudecode.py`
  - Extensions: `agent_runner/backends/claude_extensions.py`

Preflight (`agent_runner/preflight.py`):

- `codex`: verifies `codex` executable in `PATH`
- `claudecode`: verifies `claude_agent_sdk` import

Failover (`agent_runner/runner_entry.py`):

- Triggered when `failover_enabled` is true
- Uses ordered backend list with trigger reasons (`failover_on`)
- `failover_max_switches=0` means unlimited, with internal safety cap of 100 switches

## Pipeline Layer

Backend-agnostic pipeline package: `agent_runner/pipeline/`

- Orchestrator: `manager.py::PipelineManager`
- Session context: `session.py::PipelineSession`
- Stage registry and plugin loading: `stage_registry.py`
- Stage contracts:
  - `stages/base.py::Stage`
  - `stages/base.py::StageOutcome` with statuses: `ok`, `skip`, `stop`, `fail`

Built-in stages:

- `PM`, `Dev`, `QA`, `Security`

Roles:

- Parsed from `roles` string (`PM,Dev,QA` default)
- Supports plugin stage spec format: `package.module:ClassName`

Reason propagation:

- `_PROPAGATE_STOP_REASONS` in `pipeline/manager.py` carries terminal reasons to outer loops:
  - `all_tasks_done`
  - `all_tasks_attempted`
  - `project_complete`

## Configuration System

Primary source: `agent_runner/cli.py`

- Top-level defaults in `DEFAULTS`: 140 keys
- CLI argument declarations: 141 `add_argument` entries

Merge priority:

```text
CLI args > config JSON > DEFAULTS
```

Important config behaviors:

- Backend normalization accepts aliases (`openai`, `claude`, `anthropic`, etc.)
- Config versioning: `config_version = 2`
- Legacy compatibility logic exists in `_merge_effective()`

Path resolution and migration (`agent_runner/config.py`):

- Runtime work dir name: `.AgentCLI`
- App home:
  - `AGENTCLI_HOME` env if valid directory
  - fallback to project root
- Config path:
  - `{AGENTCLI_HOME}/configs/{repo-slug}.json`
- Prompts path:
  - `{AGENTCLI_HOME}/prompts/{repo-slug}/`
- DB path:
  - `{AGENTCLI_HOME}/databases/{repo-slug}.db`
- Legacy fallback support:
  - `.AgentCLI/agent_config.json`
  - `.doc/agent_config.json`

Default backend/model highlights:

- `execution_backend`: `codex`
- Codex defaults:
  - `pm_model`, `dev_model`, `qa_model`, `reporter_model`: `gpt-5.1-codex-mini`
  - dev escalation tiers: `gpt-5.1-codex`, `gpt-5.2-codex`
- Failover defaults:
  - `failover_backends = ["codex", "claudecode"]`
  - `failover_on = ["quota_exhausted", "quota_utilization"]`

## Prompting and Structured Output

Prompt module: `agent_runner/prompts.py`

- `PromptStore` reads default templates + project overrides
- PM hard contracts:
  - `append_pm_output_contract()`
  - `append_pm_essential_context()`
  - `ensure_pm_instructions_have_output_schema()`

Structured parsing module: `agent_runner/structured.py`

- JSON extraction from mixed text
- PM-specific extractor with balanced brace scan
- PM output normalization and validation:
  - `parse_pm_output_with_errors()`
- QA followup parsing:
  - `parse_qa_followups()`

Schema module: `agent_runner/schemas.py`

- `PMOutputV2`
- `BacklogTaskV2`
- `QAFollowupsV1`

## Runtime State and Artifacts

Run root:

```text
{repo}/.AgentCLI/agent_runs/<YYYYMMDD-HHMMSS>/
```

Common run artifacts (written by cycle/backends/reporting/state):

- `BACKLOG.json`
- `BACKLOG.md`
- `STATE.json`
- `PM_OUTPUT_cycle_XXX.json`
- `COMPLETION_STATUS.json`
- `run_summary.json`
- `run_summary_cycle_XXX.json`
- `last_run_summary.json`
- `SHUTDOWN_CONTEXT.json`
- `SHUTDOWN_REPORT.md`
- `PM_SHUTDOWN_REPORT_OUTPUT.txt`
- `metrics.jsonl`
- `HEARTBEAT`
- `NOTES.md`
- `NOTES_PM.md`
- `policy_scan.json`
- `policy_scan_history.jsonl`
- `DEPENDENCIES_NEEDED.md`
- `DEPENDENCY_REQUIRED.md`
- `REPO_INVENTORY.md`
- `VALIDATION_FAILURE.md`
- `PLUGIN_LOAD_FAILURE.md`
- `cycle_summary.log`
- `tasks/` (per-task attempt artifacts)
- `dev_logs/`

Other runtime paths:

- `{repo}/.AgentCLI/PM_CACHE/`
- `{repo}/.AgentCLI/todo/`
- `{repo}/.AgentCLI/agent_cli_history.txt`

Documentation area:

- `{repo}/.doc/GOALS.md`
- `{repo}/.doc/Docs/`
- `{repo}/.doc/DOCS_DIGEST.md`

## Stop Reasons and Control Rules

Stop reasons are centralized in `agent_runner/utils.py`:

- `quota_utilization`
- `quota_exhausted`
- `stop_file`
- `all_tasks_done`
- `project_complete`
- `all_tasks_attempted`
- `prepared_only`
- `no_tasks`
- `pm_refresh_no_backlog`
- `idle_exit`
- `ok`

Selection helper:

- `choose_stop_reason()` uses priority ordering (`STOP_REASON_PRIORITY`)

Quota controls:

- Claude-style OAuth quota utilities:
  - `fetch_quota_usage()`
  - `check_quota_utilization()`
  - `seconds_until_reset()`
- Codex app-server quota utilities:
  - `check_codex_quota_utilization()`
  - `parse_codex_rate_limit_windows()`

Goals rescue logic (`agent_runner/goals.py`):

- Rescuable reasons set:
  - `project_complete`
  - `no_tasks`
  - `pm_refresh_no_backlog`
- Guard function:
  - `should_attempt_goals_refresh()`

## Shell Operations

Interactive shell module: `agent_runner/shell.py`

Core commands:

- `/help`
- `/doctor`
- `/repo <path>`
- `/config [--all]`
- `/set <key> <value>`
- `/add <key> <value>`
- `/load [path]`
- `/save [path]`
- `/start [--flags...]`
- `/stop [--wait]`
- `/status`
- `/todo --save`
- `/todo --load <path|latest>`
- `/exit`

Shell diagnostics (`/doctor`) covers:

- Git availability and repo validity
- Config readability
- Run dir writability
- Backend preflight status
- Build/test executable checks
- Skills/goals/todo/docs/process-guard health hints

## Security, Scanning, and Gates

Security and policy modules:

- `policy.py`: regex-based policy scan rules and file scanning
- `security.py`: security scan rule loading and scanning
- `scan.py`: candidate file collection by scope (`quick`, `staged`, `full`)

Build/test gates:

- `gates.py` provides sync + async gate execution
- Supports generic commands (`build_cmd`, `test_cmd`)
- Legacy dotnet fallback support remains

## Skills and History Subsystems

Skills system modules:

- `skills/indexer.py`: SKILL discovery and index building
- `skills/parser.py`: frontmatter parsing
- `skills/match.py`: fuzzy suggestions
- `skills/excerpt.py`: skill snippet extraction
- `skills/summary.py`: capped summary for PM context

Task history subsystem:

- `task_history.py` stores cross-run data in SQLite
- APIs:
  - `record_task()`
  - `query_history()`
  - `format_history_block()`
  - `count_unresolved_failures()`
  - `count_consecutive_title_failures()`

Backlog and QA helper modules:

- `backlog_utils.py`: task normalization, PM-only task filtering, dependency cleanup, skill-id validation
- `qa_utils.py`: QA followup extraction/merge/manual check generation

## Process Safety

Process safety is centralized in `agent_runner/process_guard.py`.

4-layer model:

- L1: Windows Job Object (`KILL_ON_JOB_CLOSE`)
- L2: PID tracking + `atexit` cleanup
- L3: signal handlers (`SIGINT`, `SIGTERM`, `SIGBREAK`)
- L4: startup orphan cleanup from session files

Notes:

- Designed for Windows-first execution (Job Object path)
- Child processes are registered/unregistered explicitly

## Key Module Map

Core runtime:

- `agent_cli.py`: entry dispatch (shell vs immediate mode)
- `agent_runner/main.py`: parse + run glue
- `agent_runner/runner_entry.py`: async dispatch, failover, emergency handling
- `agent_runner/cli.py`: defaults and parser definitions
- `agent_runner/cycle.py`: codex backend full orchestration
- `agent_runner/backends/claudecode.py`: claude backend full orchestration
- `agent_runner/codex_exec.py`: codex CLI subprocess wrapper

State and reporting:

- `agent_runner/state.py`: backlog/state read-write and normalization
- `agent_runner/reporting.py`: shutdown context/report generation
- `agent_runner/metrics.py`: append-only JSONL metrics
- `agent_runner/logger.py`: structured logs + events

Project understanding:

- `agent_runner/docs.py`: docs discovery + digest generation
- `agent_runner/inventory.py`: repo inventory generation
- `agent_runner/analysis_cache.py`: project analysis changelog merge
- `agent_runner/prompts.py`: prompt templates and enforced contracts
- `agent_runner/structured.py`: robust JSON parsing and repair
- `agent_runner/schemas.py`: pydantic schema contracts

Safety and ops:

- `agent_runner/gitops.py`: git helpers, checkpoints, worktree support
- `agent_runner/gates.py`: build/test gates
- `agent_runner/policy.py`: policy rules and scans
- `agent_runner/security.py`: security scan
- `agent_runner/scan.py`: scan candidate selection
- `agent_runner/process_guard.py`: child-process lifecycle safety

## Running the Project

Interactive shell:

```bash
python agent_cli.py
python agent_cli.py --repo <repo_path>
```

Immediate run:

```bash
python agent_cli.py --run-now --repo <repo_path>
python agent_cli.py --run-now --repo <repo_path> --execution-backend codex
python agent_cli.py --run-now --repo <repo_path> --execution-backend claudecode
```

Wizard and prompt init:

```bash
python agent_cli.py --wizard --repo <repo_path>
python agent_cli.py --run-now --repo <repo_path> --init-prompts
```

Claude backend smoke test:

```bash
python -m agent_runner.backends.claude_smoke_test --prompt "hi"
```

Dependencies (`requirements.txt`):

```text
openai>=1.0.0
openai-agents>=0.0.0
claude-agent-sdk>=0.1.0
pydantic>=2.0.0
prompt_toolkit>=3.0.0
```

## Code Conventions and Rules

General conventions used across the codebase:

- Use type hints and `from __future__ import annotations`
- Prefer async APIs for I/O and subprocess-heavy paths
- Keep stop reasons aligned with constants in `utils.py`
- Parse model JSON through `structured.py` helpers, not ad-hoc parsing
- Use `StructuredLogger`/`MetricsLogger` instead of ad-hoc print logs for new runtime events

Safety rules:

- Do not hardcode config paths; use `config.py` resolvers
- Keep `.AgentCLI` as runtime-only workspace
- Avoid destructive git behavior by default
- Preserve compatibility with `DEFAULTS` keys because shell and run paths depend on them

## Verification Checklist

Minimal verification after code changes:

```bash
# Syntax check touched files
python -m py_compile <changed_file.py>

# Run tests if available
python -m pytest -q

# Smoke run (codex default backend)
python agent_cli.py --run-now --repo <repo_path> --non-interactive --autopilot
```

Recommended operational checks:

- `python agent_cli.py --repo <repo_path>` then `/doctor`
- Verify `run_summary.json` and `SHUTDOWN_REPORT.md` are generated in the latest run dir

## High-Risk Areas

Files requiring extra caution:

- `agent_runner/cycle.py`
- `agent_runner/backends/claudecode.py`
- `agent_runner/cli.py`
- `agent_runner/shell.py`
- `agent_runner/process_guard.py`

Why:

- Deep orchestration logic
- Shared state mutation
- Cross-platform/process-signal behavior
- Configuration compatibility and migration impact

## Directory Summary

```text
agent_cli.py
Agent.md
CLAUDE.md
README.md
requirements.txt

agent_runner/
  backends/
  pipeline/
  skills/
  cli.py
  cycle.py
  shell.py
  runner_entry.py
  codex_exec.py
  config.py
  state.py
  reporting.py
  prompts.py
  structured.py
  utils.py
  process_guard.py
  gitops.py
  gates.py
  goals.py
  docs.py
  inventory.py
  task_history.py
  todo.py
  policy.py
  security.py
  scan.py
  metrics.py
  logger.py

docs/
templates/
prompts/
configs/
databases/
```

