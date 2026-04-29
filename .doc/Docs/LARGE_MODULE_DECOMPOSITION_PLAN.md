# Large Module Decomposition Plan

## Purpose

AgentCLI now has several modules that are too large to maintain safely:

| Module | Current size | Primary risk |
|---|---:|---|
| `agent_runner/web.py` | ~9,401 lines | API contract drift, route/helper coupling, UI regression surface |
| `agent_runner/cycle.py` | ~4,060 lines | runner state coupling, retry/merge/validation behavior drift |
| `agent_runner/backends/claudecode.py` | ~3,343 lines | backend parity drift with Codex lifecycle |

The goal is not to rewrite these files. The goal is to reduce change risk by extracting stable, low-side-effect helpers first while preserving public import paths, run artifacts, and API response contracts.

## Debate Summary

Parallel review reached four consistent conclusions:

1. Keep `agent_runner.web` as a compatibility facade.
2. Do not extract `create_app()` or the Dev loop first.
3. Introduce explicit context/contracts before moving stateful orchestration.
4. Split Codex and Claude lifecycle policy symmetrically, with backend adapters only for model execution and backend-specific quota probing.

One concrete backend drift candidate was also found:

- `agent_runner/backends/claudecode.py` calls `_record_task_stop("fast_regression_gate", attempt)`, but `_record_task_stop` is only defined inside `agent_runner/cycle.py`.
- This should be treated as a pre-refactor bug-fix candidate before broader backend decomposition.

## Non-Goals

- Do not convert everything to classes.
- Do not turn `agent_runner.web` into a package in the first pass.
- Do not change endpoint payload shapes while extracting helpers.
- Do not rename run-dir artifacts.
- Do not merge Codex and Claude model-call code into one implementation.
- Do not mix feature development with module decomposition tasks.

## Compatibility Surfaces

These must remain stable during decomposition:

- `agent_runner.web.create_app`
- `agent_runner.web.build_snapshot`
- directly imported helper names used by tests, including `_build_goals_payload`, `_goal_save_serialize_draft`, `_redact_web_log_payload`, `_build_live_state_payload`, and `_load_backlog_payload`
- `/api/status`, `/api/progress`, `/api/worktree`, `/api/runner/status`, `/api/config`, `/api/goals`, `/api/prompts`, `/api/logs`
- snake_case and camelCase duplicate fields in web payloads
- `.AgentCLI/agent_runs/<run_id>/tasks/.../validation.json`
- `STATE.json`, `run_summary.json`, `metrics.jsonl`, `cycle_summary.log`, `WORKTREE_MERGE_PENDING.json`
- source repo versus isolated worktree path semantics

## Target Shape

### Web

Keep `agent_runner/web.py` as the facade:

```text
agent_runner/web.py
  - create_app()
  - build_snapshot()
  - build_health()
  - serve()
  - main()
  - compatibility re-exports for existing tests

agent_runner/web_modules/
  common.py
  redaction.py
  goals_payload.py
  prompts_payload.py
  config_contract.py
  log_tail.py
  history_payload.py
  worktree_payload.py
  runner_control_payload.py
  snapshot_payload.py
```

Do not move route registration first. `create_app()` owns FastAPI setup, process guard shutdown, mutable config state, `control_lock`, runner controller state, and mutating endpoints. Extract pure payload helpers before route handlers.

### Runner

Introduce contracts before phase extraction:

```text
agent_runner/orchestration/
  context.py             # RunnerContext, TaskRunContext, AttemptContext
  validation_artifacts.py
  task_result_writer.py
  task_branch_flow.py
  quota_flow.py
  shutdown_flow.py
```

`cycle.py` should keep the top-level `main_async()` entrypoint while delegating small, tested operations. The Dev loop should be split last because it combines model calls, task branch rollback, build/test gates, policy checks, history, GOALS updates, progress, and final merge decisions.

### Backend Parity

Long-term target:

```text
RunnerOrchestrator
  -> BackendAdapter
       run_pm(prompt, context) -> BackendResult
       run_dev(prompt, context) -> BackendResult
       run_qa(prompt, context) -> BackendResult
       run_reporter(prompt, context) -> BackendResult
       check_quota(context) -> QuotaResult
```

Shared lifecycle policy should include:

- PM GOALS gating and `goal_trace` preservation
- task branch/checkpoint lifecycle
- validation artifact writing
- failure disposition and task status classification
- retry/escalation decisions
- stop progress records
- run summary and shutdown reporting
- quota wait/failover orchestration

Backend-specific code should include:

- Codex CLI invocation
- Claude SDK invocation
- backend-specific streaming/message collection
- backend-specific quota detection/probing
- model option construction

## Extraction Order

### Phase 0. Guardrails

1. Add import compatibility tests for `agent_runner.web` public and test-used private helpers.
2. Add API golden snapshot tests for key read-only endpoints.
3. Add Codex/Claude parity tests for PM GOALS gating and validation artifact fields.
4. Fix or explicitly document the Claude `_record_task_stop` drift before backend refactoring.

### Phase 1. Web Leaf Helpers

Extract low-side-effect helpers while re-exporting names from `agent_runner.web`:

1. Redaction helpers.
2. GOALS parsing and serialization helpers.
3. Prompt inventory/read/validation helpers.
4. Log tail source and parsing helpers.
5. Config contract normalization helpers.

Do not move save/restore route handlers in this phase.

### Phase 2. Web Payload Builders

Extract cohesive read-model builders:

1. history payload
2. metrics/progress payload
3. worktree payload
4. stage payload
5. snapshot payload

`build_snapshot()` remains in `agent_runner.web` and delegates to the extracted module.

### Phase 3. Runner Context And Artifact Writers

Add typed context objects and move pure artifact writers:

1. `RunnerContext`
2. `TaskRunContext`
3. `AttemptContext`
4. validation artifact writer
5. failed-task result writer
6. stop progress writer

This phase should not change task execution order.

### Phase 4. Shared Runner Lifecycle

Move shared lifecycle pieces out of `cycle.py` and `claudecode.py`:

1. PM output postprocessing and GOALS gating
2. task branch lifecycle
3. validation gate orchestration
4. failure disposition and preserve/abandon dispatch
5. quota wait/failover orchestration

Codex and Claude should consume the same helper functions in the same phase.

### Phase 5. Backend Adapter Boundary

After the shared lifecycle is stable, formalize backend adapters:

1. Codex adapter wraps Codex CLI execution.
2. Claude adapter wraps Claude SDK execution.
3. Shared orchestrator owns PM/Dev/QA/reporter sequencing.
4. Backend-specific quota probes feed a shared quota decision loop.

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---:|---:|---|
| `agent_runner.web` private helper imports break tests | High | High | re-export old names until tests migrate |
| `create_app()` split creates stale locks/config/controller state | High | High | delay route extraction; use one explicit context object |
| API payload shape changes break frontend | High | High | golden endpoint snapshots before extraction |
| `cycle.py` split changes source/worktree path handling | Medium | High | name `source_repo` and `exec_repo` explicitly in contexts |
| Codex/Claude parity drift increases | High | High | extract both backends symmetrically |
| circular imports | Medium | Medium | put shared contracts in neutral modules, not under `web` or backend files |
| over-OOP hides behavior | Medium | Medium | use classes only for stateful contexts; keep pure helpers as functions |
| artifact filenames change | Low | High | treat run-dir files as compatibility surface |

## Validation Plan

Run these layers after each extraction PR:

```powershell
.\.venv\Scripts\python.exe -m py_compile agent_runner\web.py agent_runner\cycle.py agent_runner\backends\claudecode.py
.\.venv\Scripts\python.exe -m unittest tests.test_pipeline_roles tests.test_failure_policy tests.test_task_status tests.test_task_history_status tests.test_task_status_reporting
.\.venv\Scripts\python.exe -m unittest tests.test_web_console_static tests.test_web_console_readonly tests.test_web_console_safety tests.test_web_console_worktree
.\.venv\Scripts\python.exe -m unittest tests.test_worktree_isolation tests.test_worktree_manual_merge tests.test_stop_progress
.\.venv\Scripts\python.exe -B .\tests\web_console_playwright_smoke.py
```

For backend parity work, add or run focused tests that cover:

- PM GOALS gate output is identical for Codex and Claude.
- failed build/test writes the same validation artifact shape.
- stop during build/test/fast regression writes the same stop progress state.
- blocked/review/regression statuses produce the same preserve/abandon behavior.

## Proposed GOALS Items

These should be added only after the current Runner worktree has been merged or discarded:

```markdown
### P0-V. Maintainability And Module Decomposition

- [ ] `agent_runner.web` remains a stable compatibility facade while web redaction, GOALS, prompts, config contract, log tail, history, worktree, and snapshot helpers are extracted into focused modules.
- [ ] Web endpoint golden tests protect `/api/status`, `/api/progress`, `/api/worktree`, `/api/runner/status`, `/api/config`, `/api/goals`, `/api/prompts`, and `/api/logs` payload contracts during decomposition.
- [ ] Runner context objects explicitly distinguish source repo, execution worktree, run directory, task directory, attempt directory, and task branch state before `cycle.py` phase extraction.
- [ ] Validation artifact writing, failed-task result recording, stop progress recording, and task branch preserve/abandon dispatch are shared by Codex and Claude backends.
- [ ] Codex and Claude PM output postprocessing use the same GOALS gating, task splitting, and `goal_trace` preservation logic.
- [ ] Backend-specific code is reduced to model invocation, message streaming, model option construction, and backend quota probing behind a small adapter interface.
- [ ] Decomposition PRs are extraction-only: no product behavior changes, no endpoint contract changes, and no run artifact filename changes.
```

## First Implementation Candidates

1. Fix Claude `_record_task_stop` parity bug or add a failing test documenting it.
2. Add web import compatibility tests.
3. Extract redaction helpers from `web.py`.
4. Extract GOALS parsing/save serialization helpers from `web.py`.
5. Extract validation artifact writer from `cycle.py` into a neutral module and use it from Codex first, then Claude.

## Open Questions

1. Should tests migrate to new module imports immediately, or should all old private names remain re-exported for one release window?
2. Should web decomposition use `agent_runner/web_modules/*` or flat modules such as `web_redaction.py`, `web_goals.py`, and `web_logs.py`?
3. Should `RunnerContext` be introduced before or after the Experience DB/Analyzer stage?
4. Should Claude parity work be P0 before broad module decomposition?
5. Which endpoint payloads should become golden snapshots first?
