# CLAUDE.md - AgentCLI Project Guide

## Project Overview

**AgentCLI** (v2.0.0) is a CLI-based multi-agent orchestration runner that executes a PM → Dev → QA pipeline.
It supports dual execution backends: **Codex** (`codex exec` subprocess) and **Claude Code** (Claude Agent SDK).

- **Language**: Python 3.10+
- **Runtime**: 프로젝트 내 가상환경 (Windows)
- **Entry point**: `agent_cli.py`
- **Core package**: `agent_runner/`
- **Async engine**: asyncio
- **Data validation**: Pydantic v2

## Architecture

```
agent_cli.py (dispatcher + process_guard init)
  ├─ --run-now / --one-shot → agent_runner/main.py (immediate execution)
  ├─ --wizard → agent_runner/wizard.py (interactive config wizard)
  └─ default → agent_runner/shell.py (interactive shell via prompt_toolkit)

runner_entry.py (async dispatch + failover + signal handling)
  ├─ backends/codex_runner.py → cycle.py (Codex pipeline, ~2550 lines)
  │   └─ codex_exec.py (codex exec subprocess wrapper)
  └─ backends/claudecode_runner.py → claudecode.py (Claude pipeline, ~2900 lines)
      └─ claude_extensions.py (MCP tools, hooks, subagents)

Pipeline stages (backend-agnostic):
  pipeline/manager.py → PipelineManager.run_cycle() + _PROPAGATE_STOP_REASONS
  pipeline/session.py → PipelineSession (phase function pointers)
  pipeline/stage_registry.py → Built-in + plugin stage registration
  pipeline/stages/{pm_stage, dev_stage, qa_stage, security_stage}.py

Subsystems:
  prompts.py → PromptStore + append_pm_essential_context()
  goals.py → GOALS.md completion tracking + auto-refresh rescue
  task_history.py → SQLite cross-run task history
  skills/ → SKILL.md indexing, matching, excerpts
  utils.py → Stop reasons, quota utilization check, budget helpers
```

### Key Modules

| Module | Role |
|--------|------|
| `cli.py` | CLI argument parsing, DEFAULTS dict (~125 config keys) |
| `config.py` | Config load/save, path resolution (`app_home`, `resolve_prompts_dir`, `resolve_config_path`) |
| `cycle.py` | Codex backend main pipeline logic (~2550 lines) |
| `codex_exec.py` | `codex exec` subprocess wrapper (`CodexExecResult`, `codex_exec()`) |
| `backends/claudecode.py` | Claude backend main pipeline logic (~2900 lines) |
| `backends/claude_extensions.py` | Claude SDK extensions (MCP tools, hooks, can_use_tool, subagents, ~616 lines) |
| `state.py` | STATE.json / BACKLOG.json management |
| `gitops.py` | Git operations, checkpoints, worktree isolation, `has_new_commits()` |
| `gates.py` | Build/test gate execution, `extract_build_warnings()` |
| `process_guard.py` | Layered orphan process cleanup (Windows Job Object + parent watchdog) |
| `prompts.py` | `PromptStore`, `append_pm_essential_context()`, `append_pm_output_contract()`, template rendering |
| `structured.py` | JSON parsing, normalization, fence extraction, Pydantic repair |
| `schemas.py` | Pydantic models (`PMOutputV2`, `TaskItem`, etc.) |
| `logger.py` | `StructuredLogger` — console + file + events.jsonl |
| `metrics.py` | `MetricsLogger` — metrics.jsonl structured event logging |
| `goals.py` | GOALS.md read/parse/completion tracking (P0/P1), auto-refresh rescue, checkbox auto-update |
| `task_history.py` | SQLite cross-run task DB (`record_task`, `query_history`, `format_history_block`) |
| `todo.py` | Daily TODO file management (`ensure_todo_file`, `read_current_todo`) |
| `docs.py` | Docs directory resolution, digest generation |
| `inventory.py` | REPO_INVENTORY.md generation (git-tracked file listing) |
| `analysis_cache.py` | PM analysis fingerprint caching (skip redundant bootstrap) |
| `exceptions.py` | Shared exception classes (`BudgetExceeded`, `StopRequested`) |
| `exc_detect.py` | Exception chain detection (`is_quota_exception`, `is_transient_exception`, etc.) |
| `qa_utils.py` | QA followup extraction/merge (`extract_qa_followups`, `merge_qa_followups`) |
| `backlog_utils.py` | Backlog normalization/validation/context (`normalize_backlog_tasks`, `validate_skill_ids`) |
| `utils.py` | Stop reason constants, `choose_stop_reason()`, `has_quota_text()`, `budget_exceeded()`, quota utilization check, misc helpers |
| `shared.py` | Shared utilities between backends (skill parsing) |
| `preflight.py` | Backend preflight checks (`run_preflight()`) |
| `reporting.py` | Shutdown report generation (~475 lines) |
| `policy.py` | Policy rule evaluation |
| `scan.py` | File scanning for policy/security checks |
| `security.py` | Security scan logic |
| `run_dir.py` | Run directory creation/discovery (`make_run_dir`, `find_latest_run_dir`) |
| `progress.py` | Progress display utilities, `TokenTracker` |
| `tracing.py` | OpenTelemetry-compatible trace context |
| `wizard.py` | Interactive configuration wizard |
| `version.py` | Version string (`__version__`) |
| `backends/base.py` | `AbstractAgentRunner` interface |
| `backends/factory.py` | Backend selection (codex vs claudecode) |
| `backends/claude_smoke_test.py` | Claude backend connectivity test |
| `pipeline/manager.py` | `PipelineManager` — stage orchestration, `_PROPAGATE_STOP_REASONS` frozenset |
| `pipeline/session.py` | `PipelineSession` — backend-agnostic phase function wiring |
| `pipeline/stage_registry.py` | Stage registration, plugin loading, role parsing |
| `pipeline/stages/base.py` | `Stage` ABC, `StageOutcome` dataclass |
| `skills/indexer.py` | SKILL.md discovery, `build_skills_index()`, `resolve_skills_roots()` |
| `skills/parser.py` | SKILL.md frontmatter parsing (`SkillMetadata`) |
| `skills/match.py` | Fuzzy skill matching (`suggest_skills`, `SkillMatch`) |
| `skills/excerpt.py` | Skill excerpt extraction for prompts |
| `skills/summary.py` | Skills index summary for PM context |

## Running the Project

> **주의**: 반드시 프로젝트 내 가상환경의 Python을 사용한다. 가상환경 명칭은 노출하지 않는다.

```bash
# Interactive shell (default) — 프로젝트 가상환경에서 실행
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
claude-agent-sdk>=0.1.0
pydantic>=2.0.0
prompt_toolkit>=3.0.0
```

## Configuration System

**Priority chain**: CLI args > Config JSON > DEFAULTS (in `cli.py`)

- **Config location**: `{AGENTCLI_HOME}/configs/<repo-slug>-<hash>.json`
- **Config version**: 2 (with migration logic for legacy v1 configs)
- **Legacy fallback**: `.AgentCLI/agent_config.json` → `.doc/agent_config.json` (dual fallback, read-only)
- **Auth**: Login-based only (codex login / claude auth login — no API keys or .env needed)
- **Full reference**: `docs/CONFIG_REFERENCE_KO.md` (23개 섹션, ~125개 설정 변수)

### DEFAULTS 주요 섹션 (~125 keys)

| 섹션 | 주요 키 |
|------|---------|
| Core/paths | `repo`, `config`, `run_dir`, `resume_latest` |
| Execution backend | `execution_backend`, `claudecode_*` options |
| Claude SDK extensions | `mcp_tools`, `hooks`, `can_use_tool`, `subagents` |
| Pipeline roles | `roles`, `profile` |
| Runner behavior | `autopilot`, `loop`, `loop_sleep_seconds`, `loop_max_cycles`, `loop_idle_exit_after`, `idle_exit_cycles` |
| Goals completion | `goals_completion_level`, `goals_enabled`, `goals_auto_generate`, `goals_auto_check`, `goals_auto_refresh`, `goals_refresh_max_per_run` |
| Budget/quotas | `budget_reset_per_cycle`, `quota_check_enabled`, `quota_five_hour_max_utilization`, `quota_seven_day_max_utilization`, `quota_wait_for_reset` |
| Safety/gates | `no_policy_scan`, `policy_rules_file`, security config, scan settings |
| Models | `pm_model`, `dev_model`, `qa_model`, `reporter_model`, dev escalation tiers |
| Docs | `docs_read_mode`, `docs_dir`, `docs_digest_file`, `generate_digest` |
| Skills | `skills.enabled`, `skills.roots`, `skills.inline_mode`, `skill_match_autofix` |
| Failover | `failover_enabled`, `failover_backends`, `failover_on`, `failover_max_switches` |
| Plugin stages | `plugins_enabled`, `plugins_allowlist`, `plugins_strict` |
| Task history | `task_history_enabled`, `task_history_max_items`, `max_consecutive_task_failures` |
| PM tuning | `pm_structured_retries`, `pm_max_turns_continuations`, `pm_bootstrap_max_turns`, `pm_incremental_max_turns`, `pm_refresh_backlog`, `pm_refresh_every_cycles` |
| Dev tuning | `dev_max_turns_continuations`, `dev_auto_escalate`, `dev_max_escalations`, `dev_escalate_on` |
| Budgets | `max_pm_structured_retries`, `max_dev_escalations_per_task`, `max_dev_continuations_per_task`, `max_total_escalations_per_run`, `max_total_continuations_per_run`, `max_total_repair_attempts_per_run` |

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

2. **Stop reasons**: Use predefined constants from `utils.py` for pipeline termination:
   ```python
   # utils.py — 전체 Stop Reason 상수 (11개)
   STOP_REASON_QUOTA = "quota_exhausted"
   STOP_REASON_QUOTA_UTILIZATION = "quota_utilization"
   STOP_REASON_STOP_FILE = "stop_file"
   STOP_REASON_ALL_TASKS_DONE = "all_tasks_done"
   STOP_REASON_ALL_TASKS_ATTEMPTED = "all_tasks_attempted"
   STOP_REASON_PROJECT_COMPLETE = "project_complete"
   STOP_REASON_NO_TASKS = "no_tasks"
   STOP_REASON_PM_REFRESH_NO_BACKLOG = "pm_refresh_no_backlog"
   STOP_REASON_PREPARED_ONLY = "prepared_only"
   STOP_REASON_IDLE_EXIT = "idle_exit"
   STOP_REASON_OK = "ok"

   STOP_REASON_PRIORITY: list[str]  # choose_stop_reason() 순서 결정
   ```
   **Stop reason propagation**: `ALL_TASKS_DONE`, `ALL_TASKS_ATTEMPTED`, `PROJECT_COMPLETE`는 `StageOutcome.ok(reason)`으로 반환하여 QA가 실행된 후 outer loop에서 처리. `QUOTA`와 `STOP_FILE`은 `StageOutcome.stop()`으로 즉시 중단.

3. **frozenset 분류 상수 패턴**: 관련 reason을 frozenset으로 묶어 `in` 연산으로 dispatch:
   ```python
   # pipeline/manager.py — 파이프라인 내부 전파 대상
   _PROPAGATE_STOP_REASONS = frozenset({
       STOP_REASON_ALL_TASKS_DONE,
       STOP_REASON_ALL_TASKS_ATTEMPTED,
       STOP_REASON_PROJECT_COMPLETE,
   })

   # goals.py — goals auto-refresh 시도 대상
   GOALS_REFRESH_RESCUABLE_REASONS = frozenset({
       "project_complete",        # Dev→QA 후 GOALS 전체 완료
       "no_tasks",                # PipelineManager: 백로그 없음/빈 태스크
       "pm_refresh_no_backlog",   # run_dev_loop: PM refresh 후 백로그 없음
   })
   ```
   새로운 reason 분류가 필요하면 이 frozenset 패턴을 따른다.

4. **Structured logging**: Use `StructuredLogger` (not print/logging):
   ```python
   logger.info("message")
   logger.error("message", exc=exception, context={...})
   logger.task_start(task_id, title, attempt)
   ```

5. **JSON resilience**: Always parse JSON through `structured.py` (handles fence extraction, loose JSON repair, Pydantic validation).

6. **Pipeline stages**: Inherit from `Stage` ABC:
   ```python
   class MyStage(Stage):
       name = "MyStage"
       async def run(self, session: PipelineSession, cycle_idx: int) -> StageOutcome
   ```
   Return `StageOutcome.ok()`, `.skip()`, `.stop()`, or `.fail()`.
   - `ok` / `skip` → 다음 stage로 진행
   - `stop` → 즉시 파이프라인 종료
   - `fail` → 즉시 파이프라인 종료 (rc != 0)

7. **Backend interface**: Inherit from `AbstractAgentRunner`:
   ```python
   class MyRunner(AbstractAgentRunner):
       name = "my_backend"
       async def run(self, args: argparse.Namespace, repo: Path) -> int
   ```

8. **Budget tracking**: Respect per-task and per-run limits for escalations, continuations, and repairs. Check budget before escalation. `budget_exceeded(key, current, limit)` — 0 = unlimited.

9. **Git safety**:
   - Use `RepoCheckpoint` for state snapshots
   - Use `has_new_commits(repo, before_head)` to detect phantom completions
   - Default to safe mode (no destructive rollbacks)
   - Prefer worktree isolation for long-running sessions
   - Never force-push or hard-reset without explicit user flag

10. **Process safety**: Register child processes with `process_guard` for proper cleanup on exit.

11. **PM prompt construction**: PM 프롬프트는 3단계로 구성:
    ```python
    # 1) 템플릿 렌더링 (PromptStore — 외부 오버라이드 또는 기본 내장)
    rendered = store.render("pm_bootstrap_prompt", DEFAULT_TEMPLATE, ctx)
    # 2) JSON 출력 계약 추가
    with_contract = append_pm_output_contract(rendered)
    # 3) 필수 런타임 블록 자동 주입 (goals, done/failed tasks, build warnings 등)
    final = append_pm_essential_context(with_contract,
        turn_budget_warning=..., done_tasks_block=..., failed_tasks_block=...,
        goals_block=..., goals_instruction=..., build_warnings_block=...)
    ```
    `append_pm_essential_context()`는 HTML 마커로 중복 주입을 방지.

12. **Goals auto-refresh rescue**: 외부 루프에서 rescuable reason 발생 시 GOALS.md 자동 갱신:
    ```python
    # 판정: should_attempt_goals_refresh() → (bool, why_tag)
    # 실행: _try_goals_refresh() async closure (backend별 LLM 호출)
    # 안전장치: goals_refresh_max_per_run (기본 3), project_complete 확인, STOP 파일 정리
    ```
    `should_attempt_goals_refresh()` 판정 순서:
    1. `goals_auto_refresh` 비활성 → `(False, "disabled")`
    2. `reason not in GOALS_REFRESH_RESCUABLE_REASONS` → `(False, "not_rescuable")`
    3. `goals_refresh_count >= goals_refresh_max` → `(False, "max_reached")`
    4. GOALS.md 없음 → `(False, "no_goals")`
    5. goals 미완료 → `(False, "goals_incomplete")`
    6. 모두 통과 → `(True, "ok")`

13. **Quota utilization check**: Claude OAuth 사용량 기반 선제적 쿼타 관리:
    ```python
    # utils.py
    fetch_quota_usage() → Optional[dict]       # ~/.claude/.credentials.json 기반
    check_quota_utilization(five_hour_max, seven_day_max) → (action, info, resets_at)
    seconds_until_reset(resets_at) → int
    # action: "ok" | "wait" | "stop" | "skip"
    ```

### Error Handling

- Quota exhaustion → detect via `has_quota_text()` → trigger failover or graceful exit
- Quota utilization → `check_quota_utilization()` → pre-cycle wait or stop
- Parse failures → retry with repair prompt (up to `max_pm_structured_retries`)
- Dev failures → escalate to higher-tier model (up to budget limit)
- Build/test failures → log to STATE.json with failure details
- Phantom completion → `has_new_commits()` 검사, STATE.json warnings에 기록
- Goals auto-refresh → empty backlog / project_complete 시 GOALS.md 갱신 시도

### Outer Loop Reason Handling (cycle.py / claudecode.py)

외부 루프에서 `run_cycle()` 반환 후 reason 처리 순서:

```
1. STOP_REASON_QUOTA → break (즉시 중단)
2. reason ∈ GOALS_REFRESH_RESCUABLE_REASONS → should_attempt_goals_refresh() 판정
   ├─ 시도 가능 + _try_goals_refresh() 성공 → STOP 파일 삭제, consecutive_failures 보정, continue
   ├─ PROJECT_COMPLETE + refresh 불가/실패 → break (정상 종료)
   └─ no_tasks/pm_refresh_no_backlog + refresh 불가/실패 → fallthrough (consecutive_failures 처리)
3. STOP_REASON_ALL_TASKS_DONE → loop_mode 시 PM이 새 태스크 생성 기회 부여, 아니면 break
4. STOP_REASON_ALL_TASKS_ATTEMPTED → loop_mode 시 fallthrough, 아니면 break
5. rc != 0 → 비-loop 모드면 break
6. Idle cycle tracking → idle_exit_cycles / loop_idle_exit_after 기반 중단
```

## State & Artifacts

```
run_dir/
  ├─ BACKLOG.json              # PM-generated task list
  ├─ BACKLOG.md                # Human-readable backlog
  ├─ STATE.json                # Done/failed/warning task tracking
  ├─ PM_OUTPUT_cycle_N.json    # PM raw output per cycle
  ├─ COMPLETION_STATUS.json    # Goals completion + project_complete flag
  ├─ SHUTDOWN_REPORT.md        # End-of-run report
  ├─ DOCTOR.md                 # /doctor 환경 진단 보고서
  ├─ HEARTBEAT                 # Periodic timestamp for external monitoring
  ├─ metrics.jsonl             # Structured metrics events
  ├─ logs/
  │   ├─ debug.log
  │   ├─ error.log
  │   └─ events.jsonl
  └─ tasks/T1/attempt_00/      # Per-task artifacts

{AGENTCLI_HOME}/
  ├─ configs/<slug>.json       # Per-project config
  ├─ databases/<slug>.db       # Task history SQLite
  └─ prompts/<slug>/           # External prompt overrides

{repo}/.doc/                    # Design documents (gitignored)
  ├─ GOALS.md                  # Project completion goals (P0/P1)
  ├─ Docs/                     # Design/reference documents
  ├─ DOCS_DIGEST.md            # Docs heading index digest
  └─ REPO_INVENTORY.md         # Git-tracked file listing

{repo}/.AgentCLI/              # Runtime artifacts (gitignored)
  ├─ agent_runs/               # Per-run directories (timestamps)
  ├─ PM_CACHE/                 # PM analysis cache (fingerprinted)
  ├─ todo/                     # Daily TODO files
  ├─ skills/                   # Skills snapshot
  └─ agent_cli_history.txt     # CLI history (prompt_toolkit)
```

## Important Warnings

- **Never modify `DEFAULTS` dict structure** without updating both shell.py and cycle.py (they construct args from DEFAULTS)
- **cycle.py (~2550 lines) and claudecode.py (~2900 lines)** — changes here need extra care; test with both `--run-now` and interactive shell modes
- **process_guard.py** uses Windows-specific APIs (Job Objects) — platform-aware changes only
- **Config JSON paths** may be absolute or relative; always resolve through `config.py` helpers
- **prompts.py**: 외부 프롬프트 변경 시 `append_pm_essential_context()`가 자동 주입하는 블록을 고려 — 템플릿에서 빠진 `{variable}`은 `_SafeDict`가 경고만 출력하고 리터럴 유지
- **Stop reason 상수**: 반드시 `utils.py`의 상수를 import해서 사용 — 문자열 리터럴 직접 사용 금지
- **frozenset 분류 상수**: `_PROPAGATE_STOP_REASONS` (manager.py), `GOALS_REFRESH_RESCUABLE_REASONS` (goals.py) — 새 reason 추가 시 해당 frozenset도 업데이트 필요
- **Goals auto-refresh**: `should_attempt_goals_refresh()` 판정 함수와 `_try_goals_refresh()` 클로저가 cycle.py / claudecode.py 양쪽에 대칭 구현 — 변경 시 양쪽 모두 반영 필수
- `.doc/` and `.AgentCLI/` and `configs/` directories are gitignored — don't expect them in fresh clones
- `.claude/` directory is gitignored — session state is ephemeral
- Runtime artifacts (agent_runs, PM_CACHE, todo, skills snapshot, CLI history) are in `.AgentCLI/`; design docs (GOALS.md, Docs/, DOCS_DIGEST.md) stay in `.doc/`

## Known Code Improvement Opportunities

> 아래는 현재 알려진 리팩토링 기회. 기능에는 영향 없으나 향후 개선 시 참고.

### 완료된 리팩토링 (v2.0.1)
- ~~**Exception 감지 함수 5개**~~ → `exc_detect.py`로 추출 완료
- ~~**QA followup 함수 4개**~~ → `qa_utils.py`로 추출 완료
- ~~**백로그 유틸리티 6개**~~ → `backlog_utils.py`로 추출 완료 (파라미터화)
- ~~**예외 클래스 2개**~~ → `exceptions.py`로 추출 완료
- ~~**순수 유틸리티 4개**~~ → `utils.py`에 통합 완료
- ~~**미사용 import**~~ (`TraceCtx`, `new_trace_id`, `codex_call_hint`, `_record_task_history`) 제거 완료
- ~~**state.py 비원자적 fallback**~~ → `safe_write_text` 적용 완료

### 완료된 리팩토링 (v2.0.2)
- ~~**Goals auto-refresh inline 로직**~~ → `should_attempt_goals_refresh()` 판정 함수 + `_try_goals_refresh()` 클로저로 추출 완료
- ~~**외부 루프 PROJECT_COMPLETE 전용 핸들러**~~ → `GOALS_REFRESH_RESCUABLE_REASONS` frozenset dispatch로 리팩토링 완료 (empty backlog 경로 추가)

### 장함수 분리
- `cycle.py`: `run_pm_if_needed()` (~323줄)
- `claudecode.py`: `main_async_claudecode()` (~2000줄, nested closures), `run_dev_loop()` (~850줄)
- 양쪽 모두 nested async 함수가 outer scope 변수에 강하게 의존 (closure coupling)

### 미사용 코드
- `cycle.py:429` — `_ms = ModelSettings(...)` 생성 후 미사용

## Code Verification (코드 검증)

> **중요**: 프로젝트 코드를 수정한 후 빌드/실행 테스트를 수행하지 마시오.
> 대신 아래의 **철저한 정적 검사 및 검증** 절차를 따르시오.
> Python 구문 검증 시 프로젝트 내 가상환경의 Python을 사용한다: `.venv/Scripts/python.exe -m py_compile <file>`

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
- frozenset 분류 상수(`_PROPAGATE_STOP_REASONS`, `GOALS_REFRESH_RESCUABLE_REASONS`) 변경 시 관련 dispatch 코드 확인

### 5. 영향 범위 분석
- 변경된 함수/클래스/상수를 참조하는 모든 파일을 Grep으로 탐색
- 변경이 파급되는 모든 모듈에서 논리적 정합성 확인
- 특히 `cycle.py` / `claudecode.py` 변경 시 `--run-now` 경로와 interactive shell 경로 양쪽 검증
- **대칭 구현** 확인: goals auto-refresh, outer loop reason handling 등 양쪽 백엔드에 동일 패턴이 적용되었는지 확인

### 6. 보안 및 안전성 검토
- OWASP Top 10 취약점 (command injection, path traversal 등) 도입 여부 점검
- `process_guard.py` 관련 변경 시 Windows API 호환성 확인
- Git 조작 코드 변경 시 force-push, hard-reset 등 위험 동작이 추가되지 않았는지 확인

## Directory Structure Summary

```
AgentCLI/
├── agent_cli.py                # Main entry point (dispatcher + process_guard init)
├── requirements.txt            # Python dependencies
├── README.md                   # Full documentation (Korean)
├── CLAUDE.md                   # This file
├── agent_runner/               # Core package
│   ├── __init__.py
│   ├── main.py                 # Runner entry (--run-now)
│   ├── runner_entry.py         # Backend async dispatch + failover + signal handling
│   ├── shell.py                # Interactive shell (/start, /config, /doctor, /todo)
│   ├── cli.py                  # CLI argument parsing + DEFAULTS dict (~125 keys)
│   ├── config.py               # Config load/save, path resolution
│   ├── cycle.py                # Codex backend pipeline (~2550 lines)
│   ├── codex_exec.py           # codex exec subprocess wrapper (CodexExecResult)
│   ├── state.py                # STATE.json / BACKLOG.json I/O
│   ├── gitops.py               # Git operations, checkpoints, has_new_commits()
│   ├── gates.py                # Build/test gates, extract_build_warnings()
│   ├── process_guard.py        # Layered process cleanup (Windows Job Object + parent watchdog)
│   ├── prompts.py              # PromptStore, append_pm_essential_context(), templates
│   ├── structured.py           # JSON parsing/repair/fence extraction
│   ├── schemas.py              # Pydantic models (PMOutputV2, TaskItem)
│   ├── logger.py               # StructuredLogger (console + file + events)
│   ├── metrics.py              # MetricsLogger (metrics.jsonl)
│   ├── goals.py                # GOALS.md completion tracking + auto-refresh rescue + checkbox auto-update
│   ├── task_history.py         # SQLite cross-run task history
│   ├── todo.py                 # Daily TODO file management
│   ├── docs.py                 # Docs discovery, digest generation
│   ├── inventory.py            # REPO_INVENTORY.md generation
│   ├── analysis_cache.py       # PM analysis fingerprint cache
│   ├── exceptions.py           # Shared exceptions (BudgetExceeded, StopRequested)
│   ├── exc_detect.py           # Exception detection (quota, transient, max-turns)
│   ├── qa_utils.py             # QA followup extraction/merge
│   ├── backlog_utils.py        # Backlog normalization/validation/context
│   ├── utils.py                # Stop reasons (9개), quota check, budget, helpers
│   ├── shared.py               # Shared utilities between backends
│   ├── preflight.py            # Backend preflight checks
│   ├── reporting.py            # Shutdown report generation (~475 lines)
│   ├── policy.py               # Policy rule evaluation
│   ├── scan.py                 # File scanning for policy/security
│   ├── security.py             # Security scan logic
│   ├── run_dir.py              # Run directory creation/discovery
│   ├── progress.py             # Progress display utilities, TokenTracker
│   ├── tracing.py              # OpenTelemetry-compatible tracing
│   ├── wizard.py               # Interactive configuration wizard
│   ├── version.py              # __version__ = "2.0.0"
│   ├── backends/               # Backend implementations
│   │   ├── base.py             # AbstractAgentRunner ABC
│   │   ├── factory.py          # Backend selection logic
│   │   ├── codex_runner.py     # Codex backend entry
│   │   ├── claudecode_runner.py # Claude backend entry
│   │   ├── claudecode.py       # Claude pipeline (~2900 lines)
│   │   ├── claude_extensions.py # MCP tools, hooks, can_use_tool, subagents (~616 lines)
│   │   └── claude_smoke_test.py # Claude connectivity test
│   ├── pipeline/               # Pipeline orchestration system
│   │   ├── manager.py          # PipelineManager + _PROPAGATE_STOP_REASONS
│   │   ├── session.py          # PipelineSession dataclass
│   │   ├── stage_registry.py   # Stage registration + plugin loader
│   │   └── stages/
│   │       ├── __init__.py     # Re-exports
│   │       ├── base.py         # Stage ABC + StageOutcome
│   │       ├── pm_stage.py     # PM stage
│   │       ├── dev_stage.py    # Dev stage
│   │       ├── qa_stage.py     # QA stage
│   │       └── security_stage.py # Security stage (optional, skip if missing)
│   └── skills/                 # Skills matching system
│       ├── __init__.py         # Re-exports
│       ├── indexer.py          # SKILL.md discovery + index builder (SkillRecord)
│       ├── parser.py           # Frontmatter parser (SkillMetadata)
│       ├── match.py            # Fuzzy skill matching (SkillMatch)
│       ├── excerpt.py          # Skill excerpt for prompts
│       └── summary.py          # Skills summary for PM context
├── templates/                  # Default prompt templates
│   ├── agent_prompts/
│   │   ├── pm_bootstrap_prompt.md
│   │   ├── pm_incremental_prompt.md
│   │   ├── pm_instructions.md
│   │   ├── dev_task_prompt.md
│   │   ├── dev_instructions.md
│   │   ├── qa_instructions.md
│   │   └── qa_prompt.md
│   └── GOALS.md                # Goals template
├── prompts/                    # Per-project prompt overrides
└── docs/                       # Additional documentation (9 files)
    ├── CONFIG_REFERENCE_KO.md  # 전체 설정 변수 레퍼런스 (23개 섹션)
    ├── CONFIGURATION.md        # Setup and config overview
    ├── ADVANCED_FEATURES.md    # Extended capabilities
    ├── CUSTOMIZATION.md        # Template overrides, custom stages, plugins
    ├── DEVELOPER_GUIDE.md      # Code architecture and patterns
    ├── INSTALLATION.md         # Setup instructions
    ├── OPERATIONS.md           # Running the CLI
    ├── PIPELINE.md             # Pipeline stages and flow
    └── TROUBLESHOOTING.md      # Common issues and fixes
```
