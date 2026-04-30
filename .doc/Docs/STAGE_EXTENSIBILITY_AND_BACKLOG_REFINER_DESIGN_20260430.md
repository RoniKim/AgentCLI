# Stage Extensibility And Backlog Refiner Design

Date: 2026-04-30
Status: Design note only. Do not implement while the active runner is using the main worktree or `.doc/GOALS.md`.

## 1. Problem

AgentCLI already has a stage abstraction:

- `agent_runner/pipeline/stages/base.py`
- `agent_runner/pipeline/manager.py`
- `agent_runner/pipeline/stage_registry.py`
- `agent_runner/pipeline/session.py`
- `agent_runner/pipeline/shared_runtime.py`

The current system supports configurable roles such as:

```json
{
  "roles": "PM,Dev,QA"
}
```

It also supports plugin stage specs such as:

```json
{
  "plugins_enabled": true,
  "plugins_allowlist": ["my_pkg.*"],
  "roles": "PM,Dev,my_pkg.stages:MyStage,QA"
}
```

This preserves role order and plugin specs, and it is enough for the existing built-in pattern.

It is not yet fully free even for independent read-only/pass-fail stages, because `PipelineManager` currently loads tasks before every non-PM stage and stages cannot declare `requires_tasks=False`.

It is not yet enough for stages that mutate pipeline state, especially a proposed `PL` / `BacklogRefiner` stage that should run after PM and before Dev.

The concrete failure pattern is T2/P0-L236:

- PM generated one large task containing backend API, serializer, redaction, Web UI, static tests, and Playwright smoke coverage.
- Dev attempted the whole slice at once.
- Unit tests mostly passed, but Playwright caught a route/rendering integration gap.
- A PL stage could have split the task into smaller implementation contracts before Dev started.

## 2. Design Goal

Stage add/remove/reorder should be a first-class operation.

Desired user-facing examples:

```json
{
  "roles": "PM,PL,Dev,QA"
}
```

```json
{
  "roles": "PM,PL,Security,Dev,QA"
}
```

```json
{
  "plugins_enabled": true,
  "plugins_allowlist": ["team_stages.*"],
  "roles": "PM,team_stages.backlog:Refiner,Dev,QA"
}
```

Adding or removing an independent stage should not require changing `cycle.py` or `backends/claudecode.py`.

Adding a state-mutating stage should require declaring its effects, but the generic pipeline manager should apply those effects.

## 3. Current Constraint

`PipelineManager.run_cycle()` currently loads tasks before the first non-PM stage:

```python
if stage_name != "pm" and not tasks_checked:
    if not session.ensure_tasks_loaded():
        return CycleResult(rc=1, reason=STOP_REASON_NO_TASKS, ...)
    tasks_checked = True
```

That means this role sequence:

```text
PM,PL,Dev,QA
```

will load `session.tasks` before `PL` runs.

If `PL` rewrites `BACKLOG.json`, Dev may still receive the old in-memory `session.tasks` unless PL manually does:

```python
session.tasks = session.load_tasks()
```

This makes PL possible, but not cleanly pluggable. The mutation/reload behavior is implicit and easy to forget.

## 4. Target Architecture

Introduce a formal stage contract:

1. Stage metadata declares what the stage needs.
2. Stage output declares what the stage changed.
3. `PipelineManager` applies declared effects generically.
4. `PipelineSession` exposes safe artifact/task APIs instead of requiring stages to directly rewrite files.

### 4.1 Stage Metadata

Each stage should expose metadata, either as class attributes or a method.

```python
@dataclass(frozen=True)
class StageSpec:
    name: str
    requires_backlog: bool = False
    requires_tasks: bool = False
    can_run_without_pm: bool = False
    mutates_backlog: bool = False
    mutates_state: bool = False
    mutates_goals: bool = False
    needs_model: bool = False
```

Example:

```python
class PLStage(Stage):
    spec = StageSpec(
        name="PL",
        requires_backlog=True,
        requires_tasks=True,
        mutates_backlog=True,
        needs_model=False,
    )
```

Independent stage example:

```python
class SecurityStage(Stage):
    spec = StageSpec(
        name="Security",
        requires_backlog=False,
        requires_tasks=False,
    )
```

### 4.2 Stage Effects

Extend `StageOutcome` with declared effects.

```python
@dataclass
class StageEffects:
    backlog_written: bool = False
    tasks_reload_required: bool = False
    state_written: bool = False
    goals_written: bool = False
    artifacts_written: list[str] = field(default_factory=list)
    followups_added: int = 0
```

```python
@dataclass
class StageOutcome:
    status: str
    rc: int = 0
    reason: str = ""
    detail: str = ""
    effects: StageEffects = field(default_factory=StageEffects)
```

Convenience helper:

```python
return StageOutcome.ok(
    "backlog_refined",
    effects=StageEffects(
        backlog_written=True,
        tasks_reload_required=True,
        artifacts_written=["BACKLOG_REFINEMENT_cycle_000.json", "NOTES_PL.md"],
    ),
)
```

### 4.3 Manager Effect Application

`PipelineManager` should become responsible for reload semantics:

```python
out = await stage.run(session, cycle_idx)

if out.effects.backlog_written or out.effects.tasks_reload_required:
    session.tasks = session.load_tasks() or []
    tasks_checked = bool(session.tasks)

if out.effects.state_written:
    session.refresh_state_summary()

if out.effects.goals_written:
    session.refresh_goals_summary()
```

This makes backlog-changing stages safe by contract.

### 4.4 Session Task API

Do not make stages hand-edit `BACKLOG.json` by default.

Add explicit helpers to `PipelineSession`:

```python
def load_current_tasks(self) -> list[TaskItem]: ...
def write_backlog_tasks(self, tasks: list[dict[str, Any]], *, source_stage: str, cycle_idx: int) -> Path: ...
def append_backlog_tasks(self, tasks: list[dict[str, Any]], *, source_stage: str, cycle_idx: int) -> Path: ...
def write_stage_artifact(self, relative_path: str, payload: dict[str, Any] | str) -> Path: ...
```

`write_backlog_tasks()` should:

- write atomically;
- preserve `goal_trace`;
- preserve parent task trace fields;
- update `BACKLOG.md`;
- write a stage audit artifact;
- reject empty task lists unless the stage explicitly returns `StageOutcome.stop("no_tasks_after_refinement")`.

## 5. Backlog Refiner / PL Stage

### 5.1 Responsibility

PL is a backlog-shaping stage.

It should not decide project priority. PM owns priority and GOALS selection.

PL should:

- read PM-generated tasks;
- detect oversized or overly coupled tasks;
- split them into smaller Dev-ready tasks;
- preserve GOALS trace;
- preserve dependency relationships;
- write a refinement artifact;
- reload `session.tasks` through declared effects.

PL should not:

- create new project goals;
- mark GOALS complete;
- modify source code;
- run build/test;
- change retry policy;
- silently discard PM tasks.

### 5.2 Oversized Task Heuristics

PL should split when one or more triggers are true:

- task touches 4 or more files;
- task touches backend + frontend + Playwright in one slice;
- task includes API + serializer + UI + tests;
- `done_when` has 5 or more independent conditions;
- title/prompt includes multiple display obligations, such as list/detail/logs/preflight/blocking reasons;
- task includes both mutating behavior and read-only UI;
- task references large files such as `web.py`, `cycle.py`, `backends/claudecode.py`, or `web_console/app.js` plus tests.

T2 example triggers:

- `agent_runner/web.py`
- `web_console/app.js`
- `web_console/styles.css`
- `tests/test_web_console_readonly.py`
- `tests/test_web_console_static.py`
- `tests/web_console_playwright_smoke.py`
- API + UI + redaction + Playwright smoke in one task

### 5.3 T2 Split Example

Input:

```text
T2: Web PR Queue shows diff, QA notes, validation logs, merge preflight, and blocking reasons
```

Output:

```json
[
  {
    "id": "T2a",
    "parent_task_id": "T2",
    "title": "Expose PR Queue read-only Web API",
    "files": ["agent_runner/web.py", "agent_runner/pr_queue.py", "tests/test_web_console_readonly.py"],
    "done_when": "API list/detail tests pass; payload is bounded and redacted; no Web UI route required."
  },
  {
    "id": "T2b",
    "parent_task_id": "T2",
    "title": "Add PR Queue route and empty/loading/error states",
    "files": ["web_console/app.js", "web_console/styles.css", "tests/test_web_console_static.py"],
    "depends_on": ["T2a"],
    "done_when": "PR Queue nav/route renders the correct view shell and empty/loading/error states."
  },
  {
    "id": "T2c",
    "parent_task_id": "T2",
    "title": "Render PR Queue packet list and detail data",
    "files": ["web_console/app.js", "web_console/styles.css", "tests/test_web_console_static.py"],
    "depends_on": ["T2a", "T2b"],
    "done_when": "List/detail UI shows task id, GOALS refs, branch/base/head, changed files, diff pointers, QA notes, validation artifacts, preflight, and blockers."
  },
  {
    "id": "T2d",
    "parent_task_id": "T2",
    "title": "Cover PR Queue route with Playwright smoke",
    "files": ["tests/web_console_playwright_smoke.py"],
    "depends_on": ["T2b", "T2c"],
    "done_when": "Playwright proves the PR Queue route renders without console errors or desktop overflow."
  }
]
```

All children must carry the original `goal_trace` for `P0-L236`.

### 5.4 PL Output Artifacts

PL should write:

- `PL_OUTPUT_cycle_000.json`
- `BACKLOG_REFINEMENT_cycle_000.json`
- `NOTES_PL.md`

Suggested artifact:

```json
{
  "cycle": 0,
  "decision": "split",
  "input_task_count": 5,
  "output_task_count": 8,
  "items": [
    {
      "task_id": "T2",
      "decision": "split",
      "reason": "API, UI, redaction, and Playwright are coupled in one task.",
      "children": ["T2a", "T2b", "T2c", "T2d"]
    }
  ]
}
```

## 6. Built-In Stage Vs Plugin Stage

### 6.1 Built-In PL

Recommended for the first implementation.

Reasons:

- PL mutates core pipeline artifacts.
- It must behave identically in Codex and Claude backends.
- It must share task normalization, GOALS trace, and dependency handling.
- It should be covered by core tests.

### 6.2 Plugin PL

Plugin support should still work after the stage contract is improved.

However, plugin stages that mutate backlog/state/goals should be required to declare effects. Without effects, they should be treated as read-only.

Policy:

- plugin stage can read `session.repo`, `session.run_dir`, and `session.data`;
- plugin stage can write stage artifacts through `session.write_stage_artifact()`;
- plugin stage can mutate backlog only through `session.write_backlog_tasks()`;
- direct writes to `BACKLOG.json`, `STATE.json`, and `GOALS.md` should be discouraged and eventually guarded.

## 7. Runtime Flow

Target manager flow:

```text
parse roles
load stage specs
for each stage:
  stop check
  satisfy stage input requirements
  emit stage_start
  run stage
  emit stage_end
  apply StageOutcome.effects
  handle stop/fail/skip/ok
write run_summary
```

For `PM,PL,Dev,QA`:

```text
PM
  writes BACKLOG.json
PL
  requires backlog/tasks
  loads tasks
  splits oversized tasks
  writes BACKLOG.json + refinement artifact
  returns effects.tasks_reload_required
PipelineManager
  reloads session.tasks
Dev
  receives refined tasks
QA
  reviews completed work
```

## 8. Configuration

Initial opt-in:

```json
{
  "roles": "PM,PL,Dev,QA",
  "pl_enabled": true,
  "pl_mode": "deterministic",
  "pl_max_children_per_task": 5,
  "pl_max_files_per_task": 3,
  "pl_split_when_api_ui_tests_mixed": true
}
```

Future hybrid mode:

```json
{
  "roles": "PM,PL,Dev,QA",
  "pl_mode": "hybrid",
  "pl_model": "gpt-5.4-mini",
  "pl_timeout_seconds": 300
}
```

Recommended rollout:

1. deterministic PL only;
2. no model call in PL;
3. split by conservative heuristics;
4. later add LLM-assisted refinement behind `pl_mode=hybrid`.

## 9. CLI And Web Console

CLI:

```bash
python agent_cli.py --run-now --roles PM,PL,Dev,QA
```

Shell:

```text
> /set roles PM,PL,Dev,QA
> /save
```

Web Console:

- Config page must include `PL` in built-in role options.
- Unknown/plugin stage specs must still be preserved.
- Pipeline screen should render `PL` lifecycle records.
- Run History should show PL output artifacts.
- Backlog view should show `parent_task_id` / split lineage where present.

## 10. Metrics And Events

Add stage-generic events:

```text
stage_start
stage_end
stage_effects
```

PL-specific events:

```text
pl_start
pl_end
pl_split
pl_pass
pl_backlog_written
```

Example:

```json
{
  "event": "stage_effects",
  "stage": "PL",
  "cycle": 0,
  "effects": {
    "backlog_written": true,
    "tasks_reload_required": true
  }
}
```

## 11. Backend Symmetry

The stage contract must be enforced in shared runtime, not separately in each backend.

Codex and Claude should both call:

```python
run_shared_cycle_once(...)
```

and shared runtime should wire:

- `pm_phase`
- `pl_phase`
- `security_phase`
- `dev_phase`
- `qa_phase`

Avoid putting PL logic only in `cycle.py`.

The previous audit found multiple Codex/Claude parity gaps. PL must not repeat that pattern.

## 12. Compatibility

Default remains:

```json
{
  "roles": "PM,Dev,QA"
}
```

Initial PL is opt-in:

```json
{
  "roles": "PM,PL,Dev,QA"
}
```

After burn-in, consider making PL default if:

- no measurable quota/time penalty for small backlogs;
- oversized tasks are reliably split;
- no false split of already atomic tasks;
- both Codex and Claude backends pass the same tests.

## 13. Implementation Plan

### Phase 1: Stage Contract

- Add `StageSpec`.
- Add `StageEffects`.
- Extend `StageOutcome`.
- Keep old `StageOutcome.ok()/skip()/stop()/fail()` signatures backward compatible.
- Add manager support for applying effects.

### Phase 2: Session APIs

- Add `PipelineSession.write_stage_artifact()`.
- Add `PipelineSession.write_backlog_tasks()`.
- Add `PipelineSession.reload_tasks()`.
- Make backlog write atomic.
- Preserve `goal_trace`, `parent_task_id`, `split_reason`, and `depends_on`.

### Phase 3: Built-In PL Stage And Runtime Wiring

- Add `agent_runner/pipeline/stages/pl_stage.py`.
- Register `PL` in `stage_registry.py`.
- Add `PL` to `runtime_contract.py` built-in roles.
- Add `pl_phase` to `PipelineSession`.
- Add `pl_phase()` inside `run_shared_cycle_once()`.
- Add config defaults for PL.
- Add deterministic split heuristics.
- Write PL artifacts.
- Do not register `PL` as a runnable built-in until `pl_phase` wiring lands in the same patch set.

### Phase 4: Shared Runtime Parity Check

- Preconditions:
  - Backend parity Day 1 work from `STABILITY_SECURITY_AUDIT_FOLLOWUP_20260430.md` is complete.
  - PR queue helper extraction and Claude backend parity wiring are stable enough that `shared_runtime.py` is not being rewritten in parallel.
  - `branch_index.json` cross-process locking is complete if PL work is tested in the same run as PR queue work.

- Ensure both Codex and Claude backends pass the same `SharedCycleDeps`.
- Keep PL disabled when the role is absent.

### Phase 5: UI And Docs

- Add `PL` to Web Config role options.
- Add PL lifecycle display.
- Add docs in `docs/PIPELINE.md`, `docs/DEVELOPER_GUIDE.md`, and `docs/CONFIG_REFERENCE_KO.md`.
- Add GOALS entry before implementation starts.

## 14. Test Plan

Required tests:

- `PipelineManager` reloads `session.tasks` after a stage returns `tasks_reload_required=True`.
- Plugin stage with no declared effects does not trigger manager reload or file-backed backlog mutation; direct in-memory mutation remains out of contract until `PipelineSession` task state is encapsulated.
- Built-in `PL` preserves `goal_trace`.
- Built-in `PL` preserves dependency order.
- Built-in `PL` splits T2-like API+UI+Playwright task.
- Built-in `PL` does not split small atomic tasks.
- `roles="PM,Dev,QA"` remains unchanged.
- `roles="PM,PL,Dev,QA"` runs PL before Dev.
- `roles="PM,Security,Dev,QA"` remains unchanged.
- `roles="PM,team_stages:Plugin,Dev,QA"` preserves plugin order.
- Web Config preserves unknown/plugin specs.
- Codex backend and Claude backend both support the same role sequence.
- Built-in `PL` registration and `pl_phase` wiring land together; there is no intermediate state where `roles="PM,PL,Dev,QA"` can select an unwired role.
- Phase 4 shared-runtime parity verification is not started until backend parity/shared helper extraction has landed.

Suggested focused command:

```bash
python -m unittest tests.test_pipeline_roles tests.test_goals_gate tests.test_dependency_blocking_detail
```

Add new tests:

```text
tests/test_pipeline_stage_effects.py
tests/test_backlog_refiner_stage.py
tests/test_pipeline_roles_pl.py
```

## 15. Risks

### R1. PL Creates Too Many Tiny Tasks

Mitigation:

- cap children per parent task;
- only split when multiple strong heuristics are true;
- preserve original task if uncertain.

### R2. PL Breaks Dependency Graph

Mitigation:

- validate all `depends_on` references after split;
- fail PL before Dev if dependencies are invalid;
- reuse existing backlog normalization utilities.

### R3. Plugin Stages Mutate Core Files Unsafely

Mitigation:

- require declared effects;
- prefer session write helpers;
- add audit artifacts;
- later add guarded write APIs for `BACKLOG.json`, `STATE.json`, and `GOALS.md`.

### R4. Backend Parity Drift

Mitigation:

- implement PL in shared runtime;
- add tests that exercise both backend dispatch paths where possible;
- avoid Codex-only helper calls.

### R5. Stale In-Memory Tasks

Mitigation:

- manager-level effect application;
- never rely on individual PL implementation to remember `session.tasks = session.load_tasks()`.

## 16. Acceptance Criteria

Stage extensibility is considered complete when:

- a user can add/remove/reorder independent built-in stages through `roles`;
- a user can insert `PL` through `roles="PM,PL,Dev,QA"`;
- Dev receives the refined backlog without backend-specific glue;
- plugin stages can declare backlog mutation effects;
- manager reloads tasks after backlog mutation;
- run summaries and Web Pipeline show the inserted stage;
- Codex and Claude backends support the same role sequence;
- T2-like oversized tasks are split before Dev;
- small tasks pass through without extra model calls.
- Phase 1/2 (`StageEffects`, manager reload semantics, session APIs) can land before PL, but Phase 3+ (`PL` built-in plus `pl_phase` wiring) waits until backend parity and state integrity work are stable.

## 17. GOALS Candidates

Add after the current runner finishes:

```markdown
- [ ] StageOutcome supports declared effects such as backlog_written and tasks_reload_required so PipelineManager can safely apply stage side effects.
- [ ] PipelineSession exposes safe artifact and backlog write APIs for state-mutating stages.
- [ ] PipelineManager reloads task state after any stage declares backlog mutation.
- [ ] PL/Backlog Refiner can run between PM and Dev and split oversized tasks while preserving GOALS trace and dependencies.
- [ ] Web Config and Pipeline views support PL and plugin stages without dropping unknown role specs.
```

## 18. Recommended First Patch Set

Do not implement this in the same patch as backend parity or PR queue fixes.

Scheduling:

- Safe to do near P0 work: Phase 1 only, if it does not touch backend-specific runtime wiring.
- Defer until after backend parity/state integrity: Phase 3 and later. `PL` built-in registration and `shared_runtime.py`/`pl_phase` wiring are one patch set.

Recommended commit split:

1. `pipeline: add stage effects contract`
2. `pipeline: reload tasks after backlog-mutating stages`
3. `pipeline: add built-in PL stage with deterministic split heuristics`
4. `web: expose PL role and lifecycle display`
5. `docs: document stage effects and PL backlog refinement`
