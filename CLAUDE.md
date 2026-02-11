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

## Code Verification (코드 검증)

> **중요**: 프로젝트 코드를 수정한 후 빌드/실행 테스트를 수행하지 마시오.
> 대신 아래의 **철저한 정적 검사 및 검증** 절차를 따르시오.

코드 변경 후 반드시 다음 정적 검증 절차를 수행한다:

### 1. 구문 및 import 검증
- 수정된 파일의 Python 구문 오류 여부를 확인 (`py_compile` 수준)
- import 경로가 실제 모듈/패키지 구조와 일치하는지 검증
- 순환 import가 발생하지 않는지 확인

### 2. 타입 및 시그니처 정합성
- 함수/메서드 시그니처 변경 시 모든 호출부(caller)가 새 시그니처와 일치하는지 검증
- 반환 타입이 호출부의 기대 타입과 호환되는지 확인
- Pydantic 모델 필드 변경 시 해당 모델을 사용하는 모든 코드의 정합성 검증

### 3. 인터페이스 계약 준수
- ABC/프로토콜 변경 시 모든 하위 구현 클래스가 계약을 준수하는지 확인
- `Stage`, `AbstractAgentRunner` 등 핵심 인터페이스의 구현 완전성 검증
- 필수 메서드 누락, 시그니처 불일치 여부 확인

### 4. 상수 및 설정 정합성
- `DEFAULTS` dict 키 추가/변경 시 `shell.py`, `cycle.py` 양쪽 모두 반영 여부 확인
- 설정 키 이름이 CLI args, config JSON, DEFAULTS 간에 일관성 있는지 검증
- 문자열 상수(STOP_REASON_* 등)가 참조하는 모든 곳에서 동일한지 확인

### 5. 영향 범위 분석
- 변경된 함수/클래스/상수를 참조하는 모든 파일을 Grep으로 탐색
- 변경이 파급되는 모든 모듈에서 논리적 정합성 확인
- 특히 `cycle.py`(4000+ lines) 변경 시 `--run-now` 경로와 interactive shell 경로 양쪽 검증

### 6. 보안 및 안전성 검토
- OWASP Top 10 취약점 (command injection, path traversal 등) 도입 여부 점검
- `process_guard.py` 관련 변경 시 Windows API 호환성 확인
- Git 조작 코드 변경 시 force-push, hard-reset 등 위험 동작이 추가되지 않았는지 확인

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
