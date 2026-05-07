# Docs classification and GOALS gap review

> 작성일: 2026-04-30
> 상태: 분류 노트. 현재 runner가 도는 동안 main worktree와 `.doc/GOALS.md`는 수정하지 않는다.
> 범위: `docs/`, `.doc/Docs/`, `.doc/Docs/incidents/`
> 목적: 과거 문서와 현재 필요한 문서를 분리하고, GOALS에 명시적으로 없는 기능 후보를 정리한다.
> 현재 상태(2026-05-07): 본문은 2026-04-30 시점의 gap review다. TODO, Skills, Claude advanced controls, MCP, Plugin, Enterprise, Command Palette, Instance Health 등은 이후 `.doc/GOALS.md`에 승격되어 체크된 항목이 있으므로, 아래 unchecked 후보 목록을 현재 backlog로 재사용하지 않는다. 현재 기준은 `.doc/GOALS.md`, `docs/WEB_CONSOLE.md`, 그리고 체크인된 테스트다.

---

## 0. 요약

현재 문서들은 네 그룹으로 봐야 한다.

1. **Active canonical docs**: 사용자/개발자가 계속 봐야 하는 영구 문서. 코드와 맞춰 갱신해야 한다.
2. **Agent planning docs**: PM/Dev/QA가 읽는 `.doc/Docs` 설계/추적 문서. GOALS와 연결된 작업 소스다.
3. **Proposal docs**: 아직 제품화되지 않은 기능 설계. 이미 일부는 GOALS로 승격됐고, 일부는 아직 후보로 남아 있다.
4. **Historical/read-only docs**: 과거 상태, 비교, 디자인 소스. GOALS 후보가 아니라 참고 자료다.

중요한 결론:

- Local PR Queue, Experience DB, Analyzer, module decomposition, unattended follow-up, Web operational UX는 이미 GOALS에 들어가 있다.
- 아직 GOALS에 약하거나 빠진 기능 후보는 TODO, Skills, Claude advanced controls, MCP operations, Plugin loading operations, Enterprise profile verification, `WEB_SNAPSHOT.json`, Command Palette operator hub, Instance Health다.
- `docs/archive/`와 `docs/Design/`은 새 GOALS 후보로 직접 끌어오지 않는다.

---

## 1. Active canonical docs

이 문서들은 유지한다. 다만 `docs/MASTER_INDEX.md`가 이미 stale 가능성을 표시한 문서가 많으므로, 별도 docs refresh task가 필요하다.

| 문서 | 분류 | 유지 이유 | GOALS 반영 상태 |
|---|---|---|---|
| `docs/INSTALLATION.md` | 사용자 진입점 | 설치/첫 실행 안내 | 직접 기능 목표라기보다 docs validation 범위 |
| `docs/CONFIGURATION.md` | 사용자 설정 가이드 | shell/config/backend/model 설정 | P0-D/O/S에 부분 반영 |
| `docs/CONFIG_REFERENCE_KO.md` | 설정 레퍼런스 | live parser/defaults와 맞아야 하는 기준 문서 | P0-S에 docs validation으로 반영, 일부 기능은 gap 있음 |
| `docs/OPERATIONS.md` | 운영 가이드 | stop, worktree, budget, artifacts, preflight, failover, doctor | P0-L/M/N/P/Q/S/X에 넓게 반영 |
| `docs/PIPELINE.md` | pipeline/roles 가이드 | PM/Dev/QA, roles, plugin stage, enterprise profile | roles는 P0-O, shared backend는 P0-V. plugin/enterprise 검증은 gap |
| `docs/ADVANCED_FEATURES.md` | advanced features | TODO, GOALS, task history, QA follow-up, shutdown report | GOALS/task history/report는 반영. TODO/QA follow-up은 gap |
| `docs/CUSTOMIZATION.md` | prompt/docs/skills | prompt override, docs digest, skills | prompt/docs는 반영. Skills system은 gap |
| `docs/DEVELOPER_GUIDE.md` | 개발자 가이드 | stage/backend 확장, logging, process guard, subsystem | P0-V/S/X에 부분 반영 |
| `docs/WEB_CONSOLE.md` | web console guide | serving, LAN, runner controls, validation, worktree diagnostics | P0-B~W 전반에 반영. 문서 stale 여부는 별도 |
| `docs/TELEGRAM.md` | Telegram guide | hybrid mode, commands, push notification | P0-T/U에 queued PR/experience notification 일부 반영 |
| `docs/TROUBLESHOOTING.md` | troubleshooting | quota, no_diff, failover, plugin, worktree, preflight | 운영 docs로 유지. 특정 기능 목표보다는 support surface |
| `docs/MASTER_INDEX.md` | doc inventory | 문서 상태와 우선순위 인덱스 | P0-S docs validation과 직접 연결 |
| `docs/archive/WORKTREE_MERGE_FAILURE_20260428.md` | incident record | 이미 반영된 worktree merge incident 기록 | 새 GOALS 후보 아님 |

Action:

- 다음 docs refresh에서 `MASTER_INDEX.md`의 stale 표시를 실제 코드 기준으로 갱신한다.
- 이 분류 노트는 `MASTER_INDEX.md`를 대체하지 않고, GOALS gap 판단 근거로만 쓴다.

---

## 2. Agent planning docs

`.doc/Docs`는 PM/Dev/QA가 읽는 안정 컨텍스트다. 이쪽 문서는 GOALS와 직접 연결되거나, 다음 GOALS 후보의 근거다.

| 문서 | 분류 | GOALS 상태 | 처리 |
|---|---|---|---|
| `.doc/Docs/ARCHITECTURE.md` | architecture context | 전반 반영 | 유지 |
| `.doc/Docs/CONVENTIONS.md` | self-dev contract | P0-Q/S/V와 연결 | 유지 |
| `.doc/Docs/WEB_CONSOLE_TARGET.md` | web target/design contract | P0-A~R에 반영 | 유지 |
| `.doc/Docs/claude.md` | Claude backend ops | backend parity gap 있음 | F1/F2 후속과 연결 |
| `.doc/Docs/LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md` | PR queue design | P0-T에 반영 | 유지 |
| `.doc/Docs/EXPERIENCE_DB_AND_ANALYZER_STAGE.md` | experience/analyzer design | P0-U에 반영 | 유지 |
| `.doc/Docs/LARGE_MODULE_DECOMPOSITION_PLAN.md` | decomposition plan | P0-V에 반영 | 유지 |
| `.doc/Docs/WEB_OPERATIONAL_UX_GAPS_20260430.md` | web UX gap | P0-W에 반영 | 유지 |
| `.doc/Docs/UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md` | unattended audit | P0-X에 반영 | 유지 |
| `.doc/Docs/TASK_STATUS_CLASSIFICATION_REVIEW.md` | task status/failure policy review | 대부분 구현 완료, 일부 parity/schema 후속 | 유지하되 stale section 주의 |
| `.doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md` | open incident | P0-X 일부와 연결 | OPEN 유지 |
| `.doc/Docs/POST_RUN_BACKEND_PARITY_AND_PR_QUEUE_FIX_PLAN_20260430.md` | post-run fix plan | 일부 GOALS 간접 반영, F3/F4/F1/F2/F5 명시 gap | runner 종료 후 처리 |
| `.doc/Docs/STABILITY_SECURITY_AUDIT_FOLLOWUP_20260430.md` | stability/security audit follow-up | backend parity, state/GOALS locking, handle/preflight/recovery gaps는 일부만 GOALS 반영 | 5일 연휴 작업계획으로 사용 |
| `.doc/Docs/STAGE_EXTENSIBILITY_AND_BACKLOG_REFINER_DESIGN_20260430.md` | pipeline stage extensibility / PL design | Stage add/remove freedom, StageOutcome effects, backlog-mutating stage contract는 아직 GOALS 미반영 | runner 종료 후 PL/Stage contract 후보로 반영 |

Action:

- `POST_RUN_BACKEND_PARITY...`의 F3/F4/F1/F2/F5와 `STABILITY_SECURITY_AUDIT_FOLLOWUP...`의 A1/A2/A4/R1/R2/L1/L2/E1/E2는 다음 GOALS 갱신 때 명시 항목으로 추가할 가치가 있다.
- `STAGE_EXTENSIBILITY_AND_BACKLOG_REFINER_DESIGN...`의 StageOutcome effects, PipelineSession backlog write API, PL/Backlog Refiner는 큰 task 실패율을 줄이는 구조 개선 후보로 별도 GOALS 묶음이 필요하다.
- `TASK_STATUS_CLASSIFICATION_REVIEW.md`는 구현 업데이트가 붙어 있으나, 오래된 gap 분석 문장이 남아 있어 PM이 오해하지 않게 상단 업데이트를 더 강조할 수 있다.

---

## 3. Proposal docs

### `docs/proposals/PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md`

이 문서는 그대로 실행 backlog가 아니라 future design source다. 이미 상당 부분이 GOALS로 승격됐다.

Already promoted:

- one-repo-one-web operating model: P0-S
- no implicit run_dir reuse: P0-S/M
- stopped reload/restart safety: P0-S/M
- run_dir/config_path containment: P0-S
- LAN raw prompt block / trusted-network gate: P0-K/S
- worktree marker consistency and review details: P0-I/P/S
- identity header: P0-S
- repo-level web instance lock: P0-S
- Runbook panel: P1
- `WORK_SUMMARY.md`: P1
- `WEB_ACTION_AUDIT.jsonl`: P1
- retention policy: P1 / P0-X safe cleanup adjacent
- long-running task health: P0-N / P0-X partially
- local automation unattended preset: P0-X partially

Still not clearly promoted:

| Candidate | Proposed priority | Reason |
|---|---:|---|
| `WEB_SNAPSHOT.json` final/replay artifact | P1 | Useful for history/replay, but not needed for current core unattended safety |
| Command Palette as Operator Hub | P1 | UX acceleration, not current correctness blocker |
| Instance Health panel | P1/P0-X if handle/process incidents recur | P0-X covers diagnostics, but not a named panel |
| Local start presets with argv preview | P1 or fold into P0-X unattended preset | P0-X has unattended preset, but generic local presets are not explicit |
| `.AgentCLI/web/INSTANCE_LOCK.json` exact artifact contract | No new goal unless current lock file contract is insufficient | P0-S already covers repo-level web instance lock behavior |

Action:

- Do not copy the whole proposal into GOALS.
- Only promote the remaining candidates if they solve an active operational problem.

---

## 4. Historical/read-only docs

These should not produce new GOALS directly.

### Archive

| 문서 | 처리 |
|---|---|
| `docs/archive/CURRENT_STATE.md` | superseded by current GOALS and Web docs |
| `docs/archive/WEB_CONSOLE_STATUS.md` | superseded by P0-A~W and current tests |
| `docs/archive/TELEGRAM_WORKLOG_2026-02-20.md` | worklog only |
| `docs/archive/TELEGRAM_EVENING_TASKLIST.md` | one-off validation checklist |
| `docs/archive/AgentCLI_vs_BlueKiwi_Comparison_20260427.md` | strategic comparison only; do not import RBAC/team platform scope into current personal-local phase |

### Design

| 경로 | 처리 |
|---|---|
| `docs/Design/README.md` | keep as design bundle instruction |
| `docs/Design/project/AgentCLI Web - A.html` | canonical visual reference |
| `docs/Design/project/*.jsx`, `directions/*`, `shared/*` | read-only design source |

Action:

- Archive/design docs are references.
- Do not add GOALS from them unless a current active planning doc repeats the need.

---

## 5. GOALS gap candidates

These are the useful features found in active docs that are not clearly tracked as GOALS.

After the 2026-04-30 code/doc consistency debate, treat this section as a triage list rather than a direct GOALS import list:

- Some entries are true product gaps.
- Some entries are narrower test, diagnostics, docs, or route-naming gaps.
- Do not promote a candidate if an existing GOALS item already covers the product behavior and only the tests/docs are missing.

### G1. TODO system operational visibility

Source:

- `docs/ADVANCED_FEATURES.md`
- `docs/CONFIGURATION.md`
- `docs/CUSTOMIZATION.md` prompt variables

Current capability described:

- `.AgentCLI/todo`
- `/todo`
- `/todo --save`
- `/todo --load`
- PM `{todo_block}` injection

GOALS status:

- Not explicitly tracked.

Suggested goal:

```md
- [ ] TODO management is visible from shell/web status with active TODO path, freshness, PM injection state, and safe preview/edit controls.
```

Acceptance:

- Tests or CLI smoke cover missing TODO dir, loaded TODO file, saved TODO state, and PM prompt injection summary.
- Web/shell output never exposes raw long TODO content unless an explicit preview/read action is used.

Priority:

- P1 by default.
- P0 only if daily TODO becomes the primary operator workflow.
- TODO must not override GOALS-first PM gating. It may rank or enrich work that is already valid under unmet GOALS, but it must not cause PM to create irrelevant tasks while unmet P0 GOALS remain.

### G2. Skills system operational visibility and validation

Source:

- `docs/CUSTOMIZATION.md`
- `docs/CONFIG_REFERENCE_KO.md`
- `docs/OPERATIONS.md` doctor row

Current capability described:

- skill roots
- `skills_index.json`
- PM summary injection
- Dev/QA excerpts
- fuzzy skill id matching/autofix
- optional snapshot dir

GOALS status:

- Not explicitly tracked.

Suggested goals:

```md
- [ ] Skills doctor/status shows configured roots, discovered skill count, selected skill ids, missing skill warnings, and fuzzy-match suggestions.
- [ ] PM/Dev/QA skill injection is covered by tests for disabled, enabled, missing-root, missing-skill, and fuzzy-autofix modes.
```

Acceptance:

- Skill status output includes root count, discovered count, selected ids, missing ids, and fuzzy suggestions.
- Tests cover disabled/enabled/missing-root/missing-skill/fuzzy-autofix modes without requiring external network access.

Priority:

- P1.
- Could be P0 if skills become required for self-development quality.

### G3. Claude advanced controls

Source:

- `docs/CONFIG_REFERENCE_KO.md`
- `docs/CONFIGURATION.md`
- `.doc/Docs/claude.md`

Current capability described:

- Claude MCP tools
- hooks
- can-use-tool dynamic permission
- strict isolation
- subagents
- role-specific Claude models

GOALS status:

- Not explicitly tracked except broad backend parity/decomposition.

Suggested goals:

```md
- [ ] Claude advanced controls expose validated config, diagnostics, and tests for MCP tools, hooks, dynamic permission, strict isolation, and subagent enablement.
- [ ] Claude backend parity tests cover PR queue, task status, failure policy, validation artifacts, and advanced-control disabled/enabled modes.
```

Acceptance:

- Config validation rejects malformed Claude controls with structured errors.
- Claude parity tests prove disabled and enabled modes for MCP/tools/hooks/strict isolation do not bypass safety gates.

Priority:

- P0 if `claudecode` is production/failover target.
- P1 if Codex backend remains primary.

### G4. MCP operations

Source:

- `docs/CONFIG_REFERENCE_KO.md`
- `docs/CUSTOMIZATION.md`

Current capability described:

- `mcp_mode`
- `mcp_timeout_seconds`
- Codex MCP hint in prompts

GOALS status:

- Not explicitly tracked.

Suggested goal:

```md
- [ ] MCP mode diagnostics report selected mode, timeout, unavailable tools, and safe fallback behavior without blocking non-MCP runs.
```

Acceptance:

- Diagnostics distinguish disabled, enabled/unavailable, timeout, and available MCP modes.
- Non-MCP runs continue without blocked startup when MCP tooling is unavailable.

Priority:

- P1.

### G5. Plugin Stage loading operations

Source:

- `docs/PIPELINE.md`
- `docs/CONFIG_REFERENCE_KO.md`
- `docs/TROUBLESHOOTING.md`

Current capability described:

- `plugins_enabled`
- `plugins_allowlist`
- `plugins_strict`
- `pkg.module:ClassName` stage specs

GOALS status:

- P0-O covers preserving unknown/plugin role specs.
- Plugin spec preservation, allowlist, strict mode, and allowed-plugin loading already exist in code/tests.
- Remaining gap is load diagnostics/UX/tests for blocked, missing, and load-error stages.

Suggested goal:

```md
- [ ] Plugin stage loading has allowlist, strict-mode, failure diagnostics, Web config validation, and tests for allowed, blocked, missing, and load-error stages.
```

Acceptance:

- Tests cover allowed plugin, blocked plugin, missing module/class, and plugin load exception with `plugins_strict=true/false`.
- Web Config preserves unknown plugin specs while surfacing validation/failure diagnostics.

Priority:

- P1 test/diagnostics gap unless plugin stages are used in current self-development runs.

### G5a. Stage add/remove freedom and PL backlog refiner

Source:

- `.doc/Docs/STAGE_EXTENSIBILITY_AND_BACKLOG_REFINER_DESIGN_20260430.md`
- `agent_runner/pipeline/manager.py`
- `agent_runner/pipeline/session.py`
- `agent_runner/pipeline/shared_runtime.py`
- `docs/PIPELINE.md`
- `docs/DEVELOPER_GUIDE.md`

Current capability described:

- `roles` can add/remove/reorder built-in and plugin stages.
- Plugin specs are preserved through config.
- Existing stages can be loaded through `PipelineManager`.

Gap:

- The current contract is not fully free for state-mutating stages.
- A stage that rewrites `BACKLOG.json` must manually reload `session.tasks`.
- `StageOutcome` does not declare effects such as `backlog_written` or `tasks_reload_required`.
- PL/Backlog Refiner is not available as a built-in stage.

Suggested goals:

```md
- [ ] StageOutcome supports declared effects such as backlog_written and tasks_reload_required so PipelineManager can safely apply stage side effects.
- [ ] PipelineSession exposes safe artifact and backlog write APIs for state-mutating stages.
- [ ] PipelineManager reloads task state after any stage declares backlog mutation.
- [ ] PL/Backlog Refiner can run between PM and Dev and split oversized tasks while preserving GOALS trace and dependencies.
- [ ] Web Config and Pipeline views support PL and plugin stages without dropping unknown role specs.
```

Acceptance:

- `roles="PM,PL,Dev,QA"` causes Dev to receive the refined backlog, not stale pre-PL `session.tasks`.
- Tests cover StageOutcome effects, task reload, T2-like task splitting, small-task pass-through, and plugin role preservation.
- Built-in `PL` registration and `pl_phase` wiring land together; there is no selectable but unwired PL role.

Priority:

- P0 if large PM-generated tasks continue to cause Dev retry/escalation waste.
- Otherwise P1 immediately after backend parity and state integrity work.
- Product-facing PL should not duplicate existing GOALS-constrained PM splitting unless evidence shows PM splitting still leaves tasks too large for Dev.

### G6. Enterprise profile verification

Source:

- `docs/PIPELINE.md`
- `docs/CONFIG_REFERENCE_KO.md`
- `docs/OPERATIONS.md`

Current capability described:

- enterprise profile adds Security stage
- policy/security scans auto-enable
- budget floors are enforced

GOALS status:

- Pieces exist across P0-O/Q/S, but enterprise profile itself is not a named verification goal.
- This is mainly a test/docs/visibility gap, not a new product capability gap.

Suggested goal:

```md
- [ ] Enterprise profile has tests and Web config visibility for Security stage insertion, policy/security scan enablement, and budget floor enforcement.
```

Acceptance:

- Tests prove enterprise profile inserts/enables Security/policy defaults without dropping explicit user overrides.
- Web Config/readonly snapshot shows effective enterprise role/security settings.

Priority:

- P1.

### G7. `WEB_SNAPSHOT.json` artifact

Source:

- `docs/proposals/PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md`

Current capability described:

- final UI-visible snapshot for later replay

GOALS status:

- Not explicit.
- P0-N covers live normalized snapshot behavior, not durable `WEB_SNAPSHOT.json`.

Suggested goal:

```md
- [ ] Completed runs can persist a lightweight redacted final web-history snapshot for replay without storing raw prompts, raw logs, or full GOALS text.
```

Acceptance:

- Snapshot schema includes run identity, stage/task summary, redacted status, and artifact pointers.
- Redaction tests reject raw prompts, raw logs, secrets, and full GOALS body.
- This must not reintroduce multi-megabyte `/api/status` style payloads or full durable UI dumps.

Priority:

- P1.

### G8. Command Palette as Operator Hub

Source:

- `docs/proposals/PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md`
- design bundle

GOALS status:

- Keyboard navigation and command palette coverage exists in P0-R.
- Current palette already exposes existing views/actions.
- Missing pieces are route/action coverage for future Runbook, PR Queue, and health diagnostics once those routes exist.

Suggested goal:

```md
- [ ] Command Palette exposes operator actions for Runbook, PR Queue, diagnostics, run history, config changes, and safe runner controls with disabled/read-only states.
```

Acceptance:

- Static and Playwright tests verify palette entries route to the expected views/actions.
- Unsafe/mutating actions show disabled/read-only states unless existing control gates allow them.

Priority:

- P1 route/action coverage gap.

### G9. Instance Health panel

Source:

- `docs/proposals/PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md`
- handle/process incident docs

GOALS status:

- P0-X covers diagnostics and handle/process anomaly flags.
- A named UI panel is not explicit, but much of the underlying product intent is already covered by diagnostics goals.

Suggested goal:

```md
- [ ] Instance Health view summarizes process guard state, tracked child PIDs, handle/process diagnostic warnings, web instance lock state, and stale artifact risks.
```

Acceptance:

- Health payload reports process guard, child PID status, web lock status, stale lock hints, and artifact risk counts.
- LAN/redaction tests ensure diagnostics do not leak raw args, tokens, or local secret-bearing paths.

Priority:

- P1 as a UI consolidation gap, or P0-X subtask if Windows instability recurs.

---

## 6. Recommended next classification update

When the current runner stops:

1. Do not add all candidates to GOALS at once.
2. Add only the next useful operational slice:
   - F3/F4/F1/F2/F5 from `POST_RUN_BACKEND_PARITY...`
   - Stage effects/PL only after backend parity and state integrity are stable, unless oversized PM tasks keep causing retries
   - TODO/Skills only if they affect the next unattended cycle
3. Refresh `docs/MASTER_INDEX.md` so it has two tables:
   - document freshness
   - GOALS coverage classification
4. Run docs validation:

```powershell
python -m pytest -q tests/test_docs_validation.py
```
