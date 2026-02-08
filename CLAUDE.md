# CLAUDE.md - AgentCLI Project Guide

## Project Overview

**AgentCLI** (v2.0.0) is a CLI-based multi-agent orchestration runner that executes a PM → Dev → QA pipeline.
It supports dual execution backends: **Codex** (OpenAI Agents SDK) and **Claude Code** (Claude Agent SDK).

- **Language**: Python 3.10+
- **Entry point**: `agent_cli.py`
- **Core package**: `agent_runner/`
- **Async engine**: asyncio
- **Data validation**: Pydantic v2

## Architecture

```
agent_cli.py (dispatcher)
  ├─ --run-now / --one-shot → agent_runner/main.py (immediate execution)
  └─ default → agent_runner/shell.py (interactive shell via prompt_toolkit)

runner_entry.py (async dispatch + failover)
  ├─ backends/codex_runner.py → cycle.py (Codex pipeline, 4000+ lines)
  └─ backends/claudecode_runner.py → claudecode.py (Claude pipeline)

Pipeline stages: PM → Dev → QA → Reporter (+ optional Security)
  pipeline/stages/{pm_stage, dev_stage, qa_stage, security_stage}.py
```

### Key Modules

| Module | Role |
|--------|------|
| `cli.py` | CLI argument parsing, DEFAULTS dict (400+ config keys) |
| `cycle.py` | Codex backend main pipeline logic |
| `claudecode.py` | Claude backend main pipeline logic |
| `state.py` | STATE.json / BACKLOG.json management |
| `gitops.py` | Git operations, checkpoints, worktree isolation |
| `gates.py` | Build/test gate execution |
| `process_guard.py` | 4-layer orphan process cleanup (Windows Job Object) |
| `prompts.py` | Prompt templates, contract validation |
| `structured.py` | JSON parsing, normalization, repair |
| `schemas.py` | Pydantic models (PMOutputV2, etc.) |
| `logger.py` | Structured logging to console + files |
| `backends/base.py` | AbstractAgentRunner interface |
| `backends/factory.py` | Backend selection (codex vs claudecode) |
| `pipeline/manager.py` | Pipeline orchestration |
| `pipeline/session.py` | Shared pipeline session state |

## Running the Project

```bash
# Interactive shell (default)
python agent_cli.py

# Immediate execution
python agent_cli.py --run-now --repo <path>

# One-shot mode
python agent_cli.py --one-shot "task description"

# Configuration wizard
python agent_cli.py --wizard

# Claude backend
python agent_cli.py --execution-backend claudecode

# Smoke test (Claude backend)
python -m agent_runner.backends.claude_smoke_test --prompt "hi"
```

## Dependencies

```
openai>=1.0.0
openai-agents>=0.0.0
python-dotenv>=1.0.0
pydantic>=2.0.0
prompt_toolkit>=3.0.0
```

Optional: `claude-agent-sdk` (for Claude backend)

## Configuration System

**Priority chain**: CLI args > Config JSON > DEFAULTS (in `cli.py`)

- **Config location**: `{AGENTCLI_HOME}/configs/<repo-slug>-<hash>.json`
- **Legacy fallback**: `.doc/agent_config.json` (read-only)
- **Environment**: `.env` file (loaded via python-dotenv)

## Code Conventions

### Style

- **Language**: Korean + English mixed in comments and docs
- **Type hints**: Always use (with `from __future__ import annotations`)
- **Functions**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private**: Leading underscore (`_normalize_backend`, `_main_async_dispatch`)

### Patterns to Follow

1. **Async-first**: Use `async def` for I/O operations. Gate execution, subprocess calls, backend runners are all async.

2. **Stop reasons**: Use predefined constants for pipeline termination:
   ```python
   STOP_REASON_QUOTA = "quota_exhausted"
   STOP_REASON_STOP_FILE = "stop_file"
   STOP_REASON_ALL_TASKS_DONE = "all_tasks_done"
   STOP_REASON_OK = "ok"
   ```

3. **Structured logging**: Use `StructuredLogger` (not print/logging):
   ```python
   logger.info("message")
   logger.error("message", exc=exception, context={...})
   logger.task_start(task_id, title, attempt)
   ```

4. **JSON resilience**: Always parse JSON through `structured.py` (handles fence extraction, loose JSON repair, Pydantic validation).

5. **Pipeline stages**: Inherit from `Stage` ABC:
   ```python
   class MyStage(Stage):
       name = "MyStage"
       async def run(self, session: PipelineSession, cycle_idx: int) -> StageOutcome
   ```
   Return `StageOutcome.ok()`, `.skip()`, `.stop()`, or `.fail()`.

6. **Backend interface**: Inherit from `AbstractAgentRunner`:
   ```python
   class MyRunner(AbstractAgentRunner):
       name = "my_backend"
       async def run(self, args: argparse.Namespace, repo: Path) -> int
   ```

7. **Budget tracking**: Respect per-task and per-run limits for escalations, continuations, and repairs. Check budget before escalation.

8. **Git safety**:
   - Use `RepoCheckpoint` for state snapshots
   - Default to safe mode (no destructive rollbacks)
   - Prefer worktree isolation for long-running sessions
   - Never force-push or hard-reset without explicit user flag

9. **Process safety**: Register child processes with `process_guard` for proper cleanup on exit.

### Error Handling

- Quota exhaustion → detect via `has_quota_text()` → trigger failover or graceful exit
- Parse failures → retry with repair prompt (up to `max_pm_structured_retries`)
- Dev failures → escalate to higher-tier model (up to budget limit)
- Build/test failures → log to STATE.json with failure details

## State & Artifacts

```
run_dir/
  ├─ BACKLOG.json          # PM-generated task list
  ├─ STATE.json            # Done/failed/warning task tracking
  ├─ PM_OUTPUT_cycle_N.json
  ├─ SHUTDOWN_REPORT.md
  ├─ metrics.jsonl
  ├─ logs/
  │   ├─ debug.log
  │   ├─ error.log
  │   └─ events.jsonl
  └─ tasks/T1/attempt_00/  # Per-task artifacts
```

## Important Warnings

- **Never modify `DEFAULTS` dict structure** without updating both shell.py and cycle.py (they construct args from DEFAULTS)
- **cycle.py is 4000+ lines** — changes here need extra care; test with both `--run-now` and interactive shell modes
- **process_guard.py** uses Windows-specific APIs (Job Objects) — platform-aware changes only
- **Config JSON paths** may be absolute or relative; always resolve through `config.py` helpers
- `.doc/` and `configs/` directories are gitignored — don't expect them in fresh clones
- `.claude/` directory is gitignored — session state is ephemeral

## Testing

No formal test suite exists yet. Verify changes using:

1. **Preflight check**: `python agent_cli.py --preflight` (environment validation)
2. **Smoke test**: `python -m agent_runner.backends.claude_smoke_test --prompt "test"`
3. **Interactive shell**: Launch `python agent_cli.py` and use `/doctor` command
4. **Build/test gates**: Configure `build_cmd` and `test_cmd` in config for target project

## Directory Structure Summary

```
agent_cli/
├── agent_cli.py              # Main entry point
├── requirements.txt          # Python dependencies
├── README.md                 # Full documentation (Korean)
├── CLAUDE.md                 # This file
├── agent_runner/             # Core package
│   ├── main.py               # Runner entry
│   ├── runner_entry.py       # Backend dispatch
│   ├── shell.py              # Interactive shell
│   ├── cli.py                # CLI parsing + DEFAULTS
│   ├── cycle.py              # Codex pipeline (main logic)
│   ├── state.py              # State management
│   ├── gitops.py             # Git operations
│   ├── gates.py              # Build/test gates
│   ├── process_guard.py      # Process cleanup
│   ├── structured.py         # JSON parsing/repair
│   ├── schemas.py            # Pydantic models
│   ├── logger.py             # Structured logging
│   ├── backends/             # Backend implementations
│   │   ├── base.py           # AbstractAgentRunner
│   │   ├── factory.py        # Backend selector
│   │   ├── codex_runner.py   # Codex backend
│   │   └── claudecode_runner.py # Claude backend
│   ├── pipeline/             # Pipeline system
│   │   ├── manager.py        # Pipeline orchestration
│   │   ├── session.py        # Shared state
│   │   └── stages/           # PM, Dev, QA, Security stages
│   └── skills/               # Skills matching system
├── templates/                # Default prompt templates
├── prompts/                  # Per-project prompt overrides
└── docs/                     # Additional documentation
```
