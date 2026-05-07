# Stability and security audit follow-up plan

> 작성일: 2026-04-30
> 상태: 후속 작업 노트. 현재 runner가 도는 동안 main worktree와 `.doc/GOALS.md`는 수정하지 않는다.
> 입력: Claude "AgentCLI 안정성 전수조사 — Synthesis Report"
> 검증: Codex read-only grep/code review at HEAD `91bd283`
> 목적: 5일 연휴 동안 진행할 안정성/보안성 작업을 확정/조건부/추가검증으로 분리한다.
> 현재 상태(2026-05-07): 본문은 HEAD `91bd283` 기준 후속 계획이다. 이후 P0/P1 GOALS에서 일부 안정성·보안성·무인운영 항목이 승격·구현됐으므로, 아래 항목은 그대로 현재 backlog가 아니다. 신규 작업화 전에는 `.doc/GOALS.md`, 관련 코드, 현재 테스트 결과로 재분류한다.

---

## 0. Executive summary

Claude 감사 보고서의 큰 결론은 맞다.

- Codex backend는 최근 사이클에서 validation 분류, PR queue, phantom completion guard, report artifact, STOP-aware sleep 쪽이 보강됐다.
- Claude backend는 같은 보강을 다 받지 못했다.
- 장기 무인 운영의 다음 병목은 state/index/GOALS 동시성, crash recovery marker, stale lock/readiness, process handle lifecycle, failover/quota/outer-loop 테스트 공백이다.

다만 일부 항목은 severity를 조정해야 한다.

- `A1~A4`는 확정 High다.
- `A5`는 parity gap은 맞지만, 현재 Claude backend의 task branch 흐름에서는 "모든 성공 task phantom"이라고 단정하지 않는다. F1/F3 PR queue parity 작업을 하면서 함께 처리할 항목이다.
- `L1`은 명시적 `close_fds=True`가 없는 call site가 보이지만, Python subprocess 기본값/Windows handle inheritance 의미론 때문에 "확정 누수"가 아니라 "명시적 방어와 재현 테스트 필요"로 분류한다.
- `R1/R2/E1/E2`는 장기 무인운영 관점에서 실제로 중요하다. 단, `STATE.json`은 runner-main write 중심이고 Web은 주로 read라 즉시 빈번한 lost update라기보다 future controller/Web mutation 확장 전의 안정화 항목이다.

---

## 1. Verified findings

### A1. Claude backend does not call `decide_failure_disposition`

Status: confirmed.

Evidence:

- `agent_runner/cycle.py` imports and calls `decide_failure_disposition`.
- `agent_runner/backends/claudecode.py` imports `build_failure_entry` but has no `decide_failure_disposition` call.

Impact:

- Claude backend failure behavior can diverge from Codex backend.
- Retry/preserve/stop decision semantics are not guaranteed to match.
- Failover can create mixed operational behavior even when STATE schema looks similar.

Fix direction:

- Move failure dispatch into a shared helper.
- Make both backends pass reason, task_status, validation records, attempt budget, and escalation settings into the same function.

Suggested tests:

- Claude backend build/test/fast-regression failure path uses shared disposition.
- `blocked_env` does not consume retry budget in both backends.
- `regression_failed` can retry only when attempt budget and `dev_escalate_on` allow it.

### A2. Claude backend validation status pipeline is incomplete

Status: confirmed.

Evidence:

- Codex backend has `validation_records`, `classify_task_validation_status`, `task_validation_status`, and writes `validation.json`.
- Claude backend still uses build/test/fast-regression logs but does not mirror the same `validation_records` pipeline.

Impact:

- `validation_pending`, `tests_skipped`, `no_tests_found`, `validation_failed`, and `blocked_env` are less reliable or absent in Claude task results.
- P0-T "Dev-stage test skipping is recorded as validation_pending/tests_skipped/no_tests_found, never as success" is not backend-agnostic.

Fix direction:

- Extract validation artifact writing and validation status classification to a neutral helper.
- Replace Claude inline build/test handling with the same validation record creation used by Codex.

Suggested tests:

- Claude build skipped -> `validation_pending` or `tests_skipped`.
- Claude no tests found -> `no_tests_found`.
- Claude dependency/toolchain block -> `blocked_env`.

### A3. Claude backend local PR queue missing

Status: confirmed.

Evidence:

- `queue_review_packet` is only imported/called in `agent_runner/cycle.py`.
- No `pr_queue` call exists in `agent_runner/backends/claudecode.py`.

Impact:

- `--execution-backend claudecode` does not produce the same local PR packet/index artifacts.
- Web PR Queue cannot show Claude backend output.
- P0-T local PR queue goals are only partly satisfied.

Fix direction:

- Use the helper described in `POST_RUN_BACKEND_PARITY_AND_PR_QUEUE_FIX_PLAN_20260430.md`.
- Implement `queue_task_review_packet()` in shared runtime and call it from both backends.

Suggested tests:

- Claude backend successful task creates PR queue packet.
- Packet includes task ids, base/head, branch, changed files, validation status, GOALS trace, QA notes.

### A4. Claude backend does not write cycle/run report artifacts with Codex parity

Status: confirmed.

Evidence:

- `write_run_report_artifacts` and `write_cycle_change_summary_artifacts` are imported/called in `cycle.py`.
- Equivalent calls are not present in `backends/claudecode.py`.

Impact:

- Web History "what changed this cycle" and final report surfaces can be incomplete for Claude backend runs.
- Failover runs can have inconsistent history artifacts across backend segments.

Fix direction:

- Extract final report/cycle summary artifact writing behind a shared helper.
- Call it from both backends during shutdown/finalization and after cycle completion.

Suggested tests:

- Claude backend writes cycle change summary artifacts.
- Claude backend writes final run report artifacts.
- Web History can render both Codex and Claude run artifacts from the same schema.

### A5. Claude phantom completion guard parity gap

Status: confirmed as parity gap, not confirmed as unconditional current failure.

Evidence:

- Codex backend uses `ref_has_new_commits` and `preserved_task_branch_has_new_commits`.
- Claude backend only checks `has_new_commits(repo, task_head_before)`.

Nuance:

- Current Claude backend still calls `merge_task_branch(repo, tb)` inside the active worktree path. This can move worktree HEAD, so the exact Codex preserved-branch phantom bug is not guaranteed for every successful task.
- If Claude backend is changed to preserve task branches and queue PR packets like Codex, the same preserved-branch guard becomes mandatory.

Fix direction:

- When implementing A3, add the Codex-style guard to Claude at the same time.
- If task branch preserve behavior is unified, compute `preserved_task_branch_has_new_commits` before checkout returns to base.

Suggested tests:

- Preserved Claude task branch with new commits is not marked phantom after checkout returns to base.
- No-commit success is still marked `no_commits`.

---

## 2. Concurrency and state integrity

### R1. `STATE.json` has unlocked read-modify-write patterns

Status: confirmed pattern; operational severity depends on writer count.

Evidence:

- `state.py::load_state()` and `state.py::save_state()` are separate operations.
- Both `cycle.py` and `backends/claudecode.py` load/mutate/save state many times.
- `save_state()` uses `atomic_write_json`, which prevents torn writes but not lost updates across concurrent writers.

Current nuance:

- Web mostly reads STATE today.
- Remote/controller paths read STATE for status.
- The risk grows if Web/Telegram/controller starts mutating STATE or if backend callbacks write concurrently.

Fix direction:

- Add module-level per-path `RLock` for in-process critical sections.
- Add optional lockfile for cross-process safety, scoped to `STATE.json.lock`.
- Provide helper APIs for common state mutations instead of exposing load/mutate/save at call sites.

Suggested tests:

- Concurrent append to `failed` preserves both entries.
- Concurrent done/failed update preserves both lists.
- Corrupt state backup still works under lock.

### R2. GOALS auto-check writes non-atomically and without compare-and-swap

Status: confirmed.

Evidence:

- `goals.py` uses `gp.write_text(updated_text, ...)` for checkbox auto-update.
- GOALS auto-refresh appends refreshed goals with direct file append, so it shares the same conflict/atomicity concern.
- Web Goals save uses `atomic_write_text`, but runner auto-check does not.

Impact:

- Web edit vs runner auto-check can overwrite changes.
- Web/operator edit vs GOALS auto-refresh append can also interleave without compare-and-swap.
- Process death during write can partially corrupt GOALS on some filesystems.

Fix direction:

- Use `atomic_write_text`.
- Capture original mtime/hash before parsing and compare before write.
- On conflict, write a conflict artifact and skip auto-check instead of overwriting Web edits.

Suggested tests:

- Auto-check writes atomically.
- If GOALS changes between read and write, auto-check reports conflict and does not overwrite.
- Web save and auto-check interleaving preserves operator changes.

### R3/R4. Event and STOP progress append/update races

Status: plausible, needs targeted tests.

Evidence:

- `STOP_PROGRESS.json` is read/normalized then written by shell and remote controller paths.
- `stop_progress.log` and logger/event streams append from multiple places.

Fix direction:

- Add in-process lock for stop progress update.
- Consider append helper with Windows file lock for multi-process logs only where needed.

Suggested tests:

- Shell and remote controller stop progress updates interleave without dropping final phase.
- Multiple appenders do not corrupt JSONL lines.

---

## 3. Resource lifecycle and Windows handle safety

### L1. Subprocess handle inheritance should be explicit

Status: needs hardening; direct leak not fully proven by grep alone.

Evidence:

- `utils.py::run_subprocess_streaming()` calls `asyncio.create_subprocess_exec` without explicit `close_fds=True`.
- `codex_exec.py` calls `asyncio.create_subprocess_exec` without explicit `close_fds=True`.
- `_CodexAppServerClient` uses `subprocess.Popen` without explicit `close_fds=True`.
- `remote/controller.py` subprocess runner mode also uses `subprocess.Popen` without explicit `close_fds=True`.
- `process_guard.py` watchdog Popen already sets `close_fds=True`.

Security/stability interpretation:

- For local automation, this is primarily stability and data integrity risk, not remote exploit risk.
- On Windows, leaked inheritable handles can keep log files, sqlite WAL, pipes, or worktree paths alive longer than expected.
- Python defaults may already close many handles, but relying on defaults is fragile for long-running runner code.

Fix direction:

- Add explicit `close_fds=True` where compatible.
- Audit `subprocess`/`asyncio` call sites for stdio handle lifecycle.
- Mark opened internal file descriptors non-inheritable where practical.

Suggested tests:

- App-server client close releases stdio pipes and registered PID.
- Gate subprocess does not keep log file open after timeout.
- Windows-only smoke verifies worktree cleanup after subprocess exit.

### L2. Preflight stale lock coverage is incomplete

Status: confirmed.

Evidence:

- `preflight.py` checks source `.venv`, git ownership/safe-directory, stale STOP/runner_wait, and generated worktree diagnostics.
- It does not explicitly check `.git/index.lock`, `.git/worktrees/*/HEAD.lock`, `web_console.lock.json`, or `%TEMP%/agentcli_tg_*.lock`.

Impact:

- After abnormal termination, the next unattended run can fail immediately on git/web/Telegram lock residue.

Fix direction:

- Add read-only lock diagnostics to preflight.
- Distinguish active lock, stale lock, and unknown lock.
- Do not auto-delete without explicit operator approval unless existing policy says stale-safe.

Suggested tests:

- `.git/index.lock` reported as blocker/warning with age/path.
- worktree `HEAD.lock` reported.
- stale web instance lock reported or reclaimed according to current web lock policy.
- Telegram lock PID reuse/stale cases reported.

### L3. Run/cache retention is still mostly future work

Status: partially tracked.

Evidence:

- GOALS P1 includes local retention settings and dry-run prune reports.
- Metrics log rotation/retention exists, but run directories and PM_CACHE retention are not broadly managed.

Fix direction:

- Implement retention as dry-run first.
- Never delete pending PR queue, active run, pending worktree review, cleanup-failed artifacts, or open incident evidence.

---

## 4. Crash recovery and panic safety

### E1. PR queue packet/index ordering can desync

Status: confirmed.

Evidence:

- `queue_review_packet()` currently writes index before marking/writing packet as `branch_index_status=written`.
- `validate_review_packet_async()` writes packet first then index.

Impact:

- Crash after index write but before packet write/update can leave orphan or stale index entries.
- Crash after packet write but before index write can leave unindexed packet.

Fix direction:

- Choose a canonical two-phase status model.
- Prefer packet-first with `branch_index_status=pending`, then index write, then packet mark `written`.
- Add reconcile pass that scans packet files and branch index and repairs missing/stale entries.

Suggested tests:

- Simulated crash after packet write before index write -> reconcile adds index.
- Simulated crash after index write before packet mark -> reconcile fixes packet/index status.
- Missing packet referenced by index -> index entry marked stale or removed with audit artifact.

### E2. Attempt directories have no STARTED/FINISHED marker

Status: confirmed.

Evidence:

- Both backends create `attempt_dir` and write logs/artifacts later.
- There is no clear `STARTED.json`/`FINISHED.json` lifecycle marker per attempt.

Impact:

- Process death after `attempt_dir.mkdir()` can leave empty or half-written attempt directories.
- Resume/preflight cannot distinguish in-progress, crashed, skipped, or intentionally empty attempts.

Fix direction:

- Write `STARTED.json` immediately after attempt directory creation.
- Write `FINISHED.json` in `finally` or on success/failure finalization.
- Preflight/doctor scans old `STARTED` without `FINISHED`.

Suggested tests:

- Empty attempt dir older than threshold is reported.
- STARTED without FINISHED is reported as interrupted.
- FINISHED attempt is ignored by stale scanner.

### E5. Goals refresh failed attempts do not consume budget

Status: confirmed.

Evidence:

- `goals_refresh_count` increments only after valid new items are appended.
- Exceptions or invalid/no-op outputs do not count against `goals_refresh_max`.

Impact:

- If a refresh reason recurs and the model repeatedly fails to produce valid goals, the runner can keep retrying across cycles.

Fix direction:

- Track `goals_refresh_attempt_count` separately from `goals_refresh_success_count`.
- Use attempt count for max guard, success count for reporting.

Suggested tests:

- Invalid refresh output consumes attempt budget.
- Exception consumes attempt budget unless quota exception is re-raised by design.

### E6. Emergency shutdown does not mark interrupted STATE

Status: confirmed for top-level emergency exception paths; task-level interruption metadata still needs design.

Impact:

- Resume may retry a task with partial commits/artifacts without explicit interrupted marker.

Fix direction:

- On emergency shutdown, append an `interrupted` entry with current task/cycle/attempt when known.
- Include pointer to latest attempt dir and git head/porcelain.

---

## 5. Security interpretation

This audit is mostly stability and local-operator safety, not internet-facing application security.

Relevant security principles from the FastAPI/Frontend security references:

- Privileged mutating routes should default to deny unless explicitly enabled.
- Path containment must fail closed for filesystem writes and runner starts.
- Browser controls are not authentication.
- LAN mode without authentication must remain trusted-network-only and redacted.
- Dangerous frontend sinks should avoid untrusted HTML.
- Logs/prompts/config values should not leak secrets through UI or persisted summaries.

Current posture:

- FastAPI app disables docs/openapi by construction.
- LAN raw prompt reads and mutating actions are already blocked or gated according to current GOALS.
- Remaining high-value security work is not TLS/CORS/auth polish; it is local privileged action containment, stale state recovery, redaction preservation, and eventually an authentication plan before untrusted LAN use.

Security items to keep explicit:

- `approved roots empty` must be fail-closed.
- PR queue/Experience DB summaries must not store raw secrets, raw prompts, raw logs, or long transcripts.
- Web/Telegram operations must not expose raw runner args on LAN.
- Retention/prune must never delete pending review evidence without explicit approval.

---

## 6. Five-day implementation plan

Execution note:

- The canonical patch order is `POST_RUN_BACKEND_PARITY_AND_PR_QUEUE_FIX_PLAN_20260430.md` Step 2-7: F3, F4, F1, F2, F5, docs.
- The Day plan below is a work-bucket view, not permission to bundle all Day 1 items into one large commit.
- Do not restart unattended runner between PR queue write-order changes and the corresponding reconcile pass.

### Day 1: PR queue lock and backend parity foundation

Goal:

- Stop expanding PR queue writers before `branch_index.json` is cross-process safe.
- Make Claude backend stop drifting from Codex backend on artifacts and task outcome semantics.

Tasks:

1. `branch_index.json` thread + cross-process advisory lock.
2. PR queue packet/index write-order cleanup plus reconcile pass in the same patch set.
3. Failure metadata contract cleanup before helper emits disposition/retry fields.
4. Shared validation record/artifact helper.
5. Shared failure disposition integration.
6. Shared PR queue packet helper.
7. Claude backend calls shared helpers.
8. Add Claude-specific unit tests with monkeypatches.

Validation:

```powershell
python -m pytest -q tests/test_pr_queue.py tests/test_failure_policy.py tests/test_task_status.py tests/test_task_status_reporting.py
```

### Day 2: State, event, and queue recovery integrity

Goal:

- Make local PR queue, STATE, event append, and STOP progress updates robust under concurrency and crash recovery.

Tasks:

1. PR queue packet/index reconcile pass for `pending`, `written`, missing packet, and orphan index states.
2. `STATE.json` in-process lock and mutation helpers.
3. Optional `STATE.json.lock` cross-process lock if controller/Web mutation paths are added.
4. R3: add append helper or focused lock for multi-writer `events.jsonl`/metrics paths where parent/watchdog can collide.
5. R4: add in-process lock and atomic write discipline for `STOP_PROGRESS.json`.

Validation:

```powershell
python -m pytest -q tests/test_pr_queue.py tests/test_worktree_isolation.py tests/test_stop_progress.py tests/test_logger.py
```

Add targeted concurrency tests.

### Day 3: GOALS/attempt crash safety

Goal:

- Prevent GOALS lost update and make interrupted attempts visible.

Tasks:

1. GOALS auto-check uses atomic write.
2. GOALS auto-check mtime/hash conflict detection.
3. Attempt `STARTED.json` / `FINISHED.json` markers.
4. Preflight/doctor scan for interrupted attempt dirs.
5. Goals refresh attempt-count budget.
6. E5: failed GOALS refresh attempts consume the per-run refresh budget and write a diagnostic reason.
7. E6: emergency shutdown records interrupted task/attempt metadata in STATE or an explicit interrupted artifact before resume.

Validation:

```powershell
python -m pytest -q tests/test_goals_gate.py tests/test_web_console_safety.py tests/test_worktree_isolation.py tests/test_stop_progress.py
```

### Day 4: Windows resource/readiness hardening

Goal:

- Reduce stuck locks and inherited handle problems before long unattended runs.

Tasks:

1. Add explicit `close_fds=True` where compatible.
2. Ensure subprocess stdio pipes are closed on timeout/failure.
3. Fail closed when runner start approved roots are unavailable, while preserving Web start path behavior that supplies roots.
4. Preflight lock diagnostics for `.git/index.lock`, worktree `HEAD.lock`, web instance lock, Telegram temp lock.
5. Optional stale lock guidance artifacts.

Validation:

```powershell
python -m pytest -q tests/test_process_guard.py tests/test_codex_app_server_cleanup.py tests/test_worktree_isolation.py tests/test_web_console_safety.py
```

### Day 5: Critical path tests and docs

Goal:

- Convert the audit into durable regression coverage and docs.

Tasks:

1. Add `tests/test_critical_paths_smoke.py` or focused equivalents:
   - backend failover dispatch
   - quota wait STOP-aware behavior
   - outer-loop reason handling
   - PR queue reconcile
   - interrupted attempt detection
2. Refresh `CLAUDE.md` backend parity notes.
3. Refresh docs/MASTER_INDEX coverage table.
4. Update `.doc/GOALS.md` only after runner has stopped and only with selected explicit gaps.

Validation:

```powershell
python -m pytest -q tests/test_docs_validation.py
python -m pytest -q tests/test_pr_queue.py tests/test_worktree_isolation.py tests/test_failure_policy.py tests/test_stop_progress.py tests/test_web_console_safety.py
```

Required scenario matrix:

| Scenario | Setup | Expected |
|---|---|---|
| P6 backend failover | `failover_enabled=true`, `failover_on=["quota_exhausted"]`, primary returns quota | runner exits/switches with `backend_failover` event and preserves run context |
| P6 quota utilization failover | `failover_on=["quota_utilization"]`, Codex 5h exceeds threshold, alternate backend configured | run stops for backend switch rather than sleeping forever |
| P7 quota wait stop-aware | 5h quota wait path + STOP file created while waiting | wait exits promptly with `stop_file`, no extra task starts |
| P8 outer-loop project_complete rescue | `GOALS_REFRESH_RESCUABLE_REASONS` contains `project_complete` and unmet goals remain | refresh attempt counted once and outer loop continues only if valid goals appended |
| P8 outer-loop no_tasks rescue | PM refresh creates no backlog | refresh attempt counted and run stops after budget exhausted |
| P8 outer-loop all_tasks_attempted | done+skipped reaches total with unresolved work | status remains incomplete, no GOALS refresh is attempted because this reason is not rescuable, and stop reason is explicit |
| PR queue reconcile | packet pending/index missing, orphan index, missing packet | reconcile reports recoverable/non-recoverable states without deleting evidence |
| interrupted attempt | attempt has STARTED but no FINISHED | preflight reports interrupted attempt before new unattended run |

If time allows:

```powershell
python -m pytest -q
```

---

## 7. Proposed GOALS additions after current runner stops

Do not edit `.doc/GOALS.md` while the active runner is using it.

Candidate P0 additions:

```md
- [ ] Codex and Claude backends use the same failure disposition, validation artifact, local PR queue, and run report helpers so failover cannot produce mixed schemas.
- [ ] PR queue packet/index writes are lock-protected and recoverable after interrupted packet or index updates.
- [ ] STATE.json mutation helpers preserve concurrent done/failed/warning updates across runner, controller, and future Web mutation paths.
- [ ] GOALS auto-check writes atomically and detects operator edit conflicts instead of overwriting Web edits.
- [ ] Attempt directories record STARTED/FINISHED markers and preflight reports interrupted attempts before a new unattended run.
- [ ] Preflight reports stale git, web instance, and Telegram lock files with age, owner evidence when available, and safe operator guidance.
- [ ] Runner subprocess launch paths explicitly close inherited file descriptors or document tested handle inheritance behavior on Windows.
```

Candidate P1 additions:

```md
- [ ] Critical path smoke tests cover backend failover, quota wait, outer-loop reason handling, interrupted attempt recovery, and PR queue reconcile.
- [ ] Local retention dry-run includes agent_runs, PM_CACHE, logs, diagnostics, and backups while preserving pending review evidence.
```

---

## 8. P1 backlog appendix for lower-priority audit items

These were present in the original audit but are not part of the first 5-day P0 slice. Keep them visible so they are not lost.

| ID group | Item | Priority | Notes |
|---|---|---:|---|
| A6 | Claude backend sleep/wait should use STOP-aware sleep for quota/loop waits | P1 | Promote if Claude backend becomes production before Day 5 tests land. |
| A7 | Claude manual worktree merge approval flow parity | P1 | Depends on PR queue/manual merge UX decisions. |
| A8-A10 | Claude commit message/schema/tooling parity details | P1 | Bundle with backend parity cleanup after A1-A5. |
| R5 | Web config save lock and runner reload lock are separate | P1 | Needs focused lock-order review to avoid deadlock. |
| R6 | HEARTBEAT non-atomic update | P2 | Low risk unless readers depend on strict consistency. |
| R7 | DEFAULTS nested mutation risk | P1 | Config immutability/regression test candidate. |
| R8 | Worktree allocation TOCTOU | P1 | Promote if parallel runner/worktree allocation increases. |
| R9-R10 | Remaining state/concurrency edge cases | P1 | Keep with state integrity follow-up. |
| L4 | SQLite WAL lifecycle | P2 | Relevant if task history DB sees locked WAL residue. |
| L5 | Watchdog breakaway fallback | P1 | Windows unattended reliability item. |
| L6 | Telegram lock PID reuse | P1 | Related to Day 4 lock diagnostics. |
| L7 | tree_watch_task await/cleanup | P1 | Resource lifecycle cleanup. |
| L8 | logger fd lifecycle | P1 | Related to handle inheritance/close tests. |
| L9 | WORKTREE_REUSE_CONTRACT ACL lock holder diagnostics | P1 | Add owner evidence where possible. |
| L10 | Remaining retention/cleanup lifecycle items | P1 | Coordinate with retention dry-run goal. |
| E3 | PM bootstrap death + stale analysis cache success | P1 | Needs stale-success invalidation test. |
| E4 | STOP file cleanup after normal shutdown | P1 | Promote if `--resume-latest` is frequently blocked. |
| E7 | events/log growth retention | P1 | Coordinate with retention dry-run goal. |
| E8 | git worktree residue recovery | P1 | Current status OK, keep doctor/preflight coverage. |

---

## 9. Relationship to existing follow-up notes

This document extends:

- `POST_RUN_BACKEND_PARITY_AND_PR_QUEUE_FIX_PLAN_20260430.md`
  - F1/F2/F3/F4/F5 are still valid.
  - This audit adds A1/A2/A4, R1/R2, L1/L2, E1/E2.
- `DOCS_CLASSIFICATION_AND_GOALS_GAP_20260430.md`
  - This audit should be added as an active Agent planning document.
- `UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md`
  - This audit gives the next hardening slice for long unattended runs.
- `LARGE_MODULE_DECOMPOSITION_PLAN.md`
  - Shared backend helpers should be extracted before large module splits.
- `STAGE_EXTENSIBILITY_AND_BACKLOG_REFINER_DESIGN_20260430.md`
  - StageOutcome effects, task reload semantics, and PL/Backlog Refiner should be used to prevent oversized PM tasks from reaching Dev unchanged.

---

## 10. Validation performed for this note

Read-only checks:

```powershell
rg -n "decide_failure_disposition|validation_records|queue_review_packet|write_cycle_change_summary_artifacts|write_run_report_artifacts|ref_has_new_commits|sleep_or_stop|read_pending_worktree_merge" agent_runner\cycle.py agent_runner\backends\claudecode.py
rg -n "def load_state|def save_state|load_state\(|save_state\(" agent_runner\state.py agent_runner\cycle.py agent_runner\backends\claudecode.py agent_runner\web.py agent_runner\remote\controller.py
rg -n "gp\.write_text|atomic_write_text|GOALS" agent_runner\goals.py agent_runner\web.py
rg -n "create_subprocess_exec|subprocess\.Popen|close_fds|set_inheritable" agent_runner\codex_exec.py agent_runner\utils.py agent_runner\process_guard.py agent_runner\backends\claudecode.py agent_runner\remote\controller.py
rg -n "index\.lock|HEAD\.lock|web_console\.lock|agentcli_tg_.*lock|preflight" agent_runner\preflight.py agent_runner\remote agent_runner\web.py
rg -n "attempt_dir =|attempt_dir\.mkdir|STARTED|FINISHED" agent_runner\cycle.py agent_runner\backends\claudecode.py agent_runner\preflight.py tests
```

No tests were run for this document update.
