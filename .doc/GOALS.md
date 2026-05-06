# Project Goals - AgentCLI Web Console

> Product target: make AgentCLI fully usable from a browser for monitoring, configuration, prompt review/editing, logs, goals, worktree review, and safe runner operations.
> Design source: `docs/Design/`, especially `docs/Design/project/AgentCLI Web - A.html`.
> This file is the target backlog, not a status report. Current implementation notes belong in `docs/WEB_CONSOLE.md`, `docs/MASTER_INDEX.md`, or archived incident/design notes.
> Do not downgrade, remove, or merge unmet P0 goals to make progress look complete; implement them until they are true.
> Task sizing rule: each unchecked P0 item should be small enough for one focused AgentCLI task branch.
> Last reviewed: 2026-05-06.

## P0 (Must-Have)

### P0-A. Foundation Already Established

- [x] The web console direction is grounded in the checked-in design bundle under `docs/Design/`.
- [x] Repo-owned web console files exist under `web_console/` instead of running directly from the exported design prototype.
- [x] A FastAPI entry point can serve the web console and read-only JSON snapshots from `agent_runner.web`.
- [x] The dashboard no longer treats an empty timestamped run directory as an active run.
- [x] Completed runs with final reason `ok`, `prepared_only`, `project_complete`, or `all_tasks_done` are not displayed as still running.
- [x] The Config page can render the current config snapshot when schema fields such as `roles` arrive as comma-separated strings or arrays.
- [x] Documentation separates current implementation status from the product target backlog.

### P0-B. Live Run Status And Dashboard Accuracy

- [x] Define a stable web snapshot contract for idle, running, stopped, failed, and completed AgentCLI runs.
- [x] Dashboard prefers live `RunnerController` state when available and falls back to persisted run artifacts only when no live controller data exists.
- [x] Dashboard shows current task id, title, attempt, branch, worktree mode, and run directory from real AgentCLI data.
- [x] Dashboard quota panel reads real 5h/7d quota state or shows an explicit unavailable state without fake percentages.
- [x] Dashboard budget/token panels read real metrics or show unavailable state without fabricated values.
- [x] PM/Dev/QA stage cards are populated from actual lifecycle events instead of synthetic stage defaults.
- [x] Completed, stopped, failed, and idle runs have visually distinct dashboard states and API values.
- [x] API tests cover no-run, live-running, completed-success, stopped, and failed run snapshots.

### P0-C. Core Screen Data Contracts

- [x] Pipeline screen renders actual PM/Dev/QA lifecycle records from run artifacts or control-plane events.
- [x] Backlog screen renders real task dependency, status, attempt, file scope, and failure information.
- [x] Goals screen renders the active `.doc/GOALS.md` with stable P0/P1 grouping and exact checkbox state.
- [x] Config screen renders all active config keys needed by this repo, including codex models, quota settings, worktree settings, and prompt paths.
- [x] Prompts screen renders profile-aware prompt inventory with file path, source, override/template mode, and redacted preview.
- [x] Run History screen renders persisted run summaries, shutdown reasons, task counts, and worktree outcomes.
- [x] Notifications screen renders real AgentCLI/control-plane notification events instead of placeholder-only state.
- [x] Worktree Review screen renders pending merge/discard metadata, changed files, patch path, base/head refs, and cleanup state.

### P0-D. Config Editing Workflow

- [x] Add backend schema metadata for editable AgentCLI config fields used by this project.
- [x] Add a read endpoint that returns editable config values, default values, schema metadata, redaction metadata, and restart-required flags.
- [x] Add frontend validation for editable config fields before save is allowed.
- [x] Add frontend diff view for config changes with changed field, old value, new value, and restart-required indicator.
- [x] Add backend config backup creation before any save writes to disk.
- [x] Add backend config save endpoint with validation, redaction safety, and atomic write behavior.
- [x] Add UI success/error states for config save, backup path, and reload-required status.
- [x] Add tests for config read, validation failure, backup creation, successful save, and rejected unsafe save.

### P0-E. Prompt Editing Workflow

- [x] Add backend prompt read endpoint that can return full prompt content only when explicitly requested by the browser UI.
- [x] Add prompt editor UI with file selector, scope, source, dirty state, and redaction warning.
- [x] Add prompt validation for empty files, missing required template variables, and invalid prompt file names.
- [x] Add prompt diff view before save.
- [x] Add backend prompt backup creation before any prompt save writes to disk.
- [x] Add backend prompt save endpoint with path containment checks and atomic write behavior.
- [x] Add prompt restore-from-backup workflow.
- [x] Add tests for prompt read, validation failure, backup creation, save, restore, and path traversal rejection.

### P0-F. Goals Editing Workflow

- [x] Add backend Goals read endpoint that returns raw text, parsed P0/P1 items, and file metadata.
- [x] Add Goals editor UI for add, edit, reorder, check/uncheck, and delete with dirty state.
- [x] Add Goals validation that prevents deleting or downgrading unmet P0 goals without explicit confirmation.
- [x] Add Goals diff view before save.
- [x] Add backend Goals backup creation before save.
- [x] Add backend Goals save endpoint with atomic write behavior.
- [x] Add tests for Goals parse, edit, backup, save, and downgrade-confirmation behavior.

### P0-G. Logs And Live Tail

- [x] Logs screen can live-tail the active run log without reloading the whole page.
- [x] Logs screen supports pause/resume tailing.
- [x] Logs screen supports level, stage, task id, and free-text filters.
- [x] Logs screen supports copy selected log lines and download current filtered logs.
- [x] Logs screen distinguishes empty logs, missing files, read errors, and loading state.
- [x] API tests cover log tailing, filters, missing log file, and malformed log lines.

### P0-H. Runner Controls

- [x] Browser start action can launch AgentCLI with the selected repo/config when runner controls are explicitly enabled.
- [x] Browser stop action creates the expected stop signal and refreshes status until the runner exits or times out.
- [x] Browser reload action restarts the runner through a confirmed stop/start flow.
- [x] Browser restart action uses the `RESTART RUNNER` confirmation phrase and reports a distinct restart result.
- [x] Runner controls show live busy, disabled, error, and success states.
- [x] Runner controls are blocked by default on LAN/external binds unless explicitly enabled.
- [x] API tests cover start, stop, reload, restart, confirmation mismatch, disabled controls, and controller errors.

### P0-I. Manual Worktree Review

- [x] Worktree Review screen shows pending worktree patch summary, changed file list, risk notes, source branch, base ref, and head ref.
- [x] Worktree Review screen exposes explicit merge and discard actions only when runner controls/worktree actions are enabled.
- [x] Worktree merge action requires confirmation and applies the pending patch to the source repo without auto-committing.
- [x] Worktree discard action requires confirmation and removes or marks the pending worktree state without touching source repo files.
- [x] Worktree cleanup failures are shown as recoverable warnings with exact path and manual cleanup instructions.
- [x] API tests cover pending merge state, merge apply, discard, cleanup failure, and no-pending state.

### P0-J. English And Korean UI

- [x] Add a locale state model with `en` and `ko` options and persisted browser preference.
- [x] Add a visible language toggle in the shell header or settings area.
- [x] Translate primary navigation, page titles, buttons, badges, empty states, validation errors, and confirmation text to English.
- [x] Translate primary navigation, page titles, buttons, badges, empty states, validation errors, and confirmation text to Korean.
- [x] Ensure command palette and keyboard shortcut labels use the active locale.
- [x] Add Playwright checks that Dashboard and Config render in both English and Korean.

### P0-K. Safety, LAN, And Validation

- [x] Read-only mode is the default for localhost and LAN binds.
- [x] Mutating config, prompt, goals, runner, and worktree actions require explicit server opt-in.
- [x] Sensitive config and prompt values are redacted by default in API responses and UI previews.
- [x] LAN documentation clearly states trusted-network-only usage until authentication is implemented.
- [x] Playwright smoke covers Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, Worktree Review, and mobile width.
- [x] Validation commands for compile, unit tests, and Playwright smoke are documented and runnable from this repo.

### P0-L. Self-Development And Worktree Reliability

- [x] Stale central `WORKTREE_MERGE_PENDING.json` markers are detected and treated as invalid when the run-local pending file or patch path is missing.
- [x] Existing isolated worktree directories are reused only when run id, expected head, current branch, clean/dirty state, and source repo ownership match the active run contract.
- [x] Completed isolated task branches advance the generated worktree HEAD so each following task starts from the previous task output while source `main` remains unchanged.
- [x] Worktree merge preflight blocks unsafe apply when the source repo is dirty, source `HEAD` differs from `base_ref`, the patch hash does not match metadata, or `git apply --check` fails.
- [x] Worktree cleanup on Windows retries locked-path removal, records exact permission failures, and keeps cleanup-failed states visible until reconciled.
- [x] A worktree doctor/list/prune command or API can report orphaned worktrees, stale pending markers, missing patches, and cleanup-failed artifacts without mutating source files by default.
- [x] Shell, web, and remote-controller starts create a new `run_dir` by default; latest run reuse requires explicit resume or `--run-dir` intent.
- [x] `STATE.json` done/failed counts are scoped to the current backlog generation so stale task ids from previous backlogs cannot inflate progress or history.
- [x] Web snapshots separate execution status from project completion status, so `rc=0`/`reason=ok` does not appear as project success while required goals or backlog items remain incomplete.
- [x] Goals parsing treats missing or malformed required priority sections as invalid/incomplete instead of silently completing P0/P1 modes.
- [x] Goals auto-refresh and completion checks use the same configured `goals_completion_level` in shell, runner, web, and tests.
- [x] Web console tests are aligned with `goals_completion_level=all` defaults and pass without relying on operator-specific local config.
- [x] Self-development runs execute at least the fast web/worktree regression suite, not only `compileall`, before marking task branches complete.
- [x] LAN web serving refuses runner controls without real authentication or an explicit trusted-network gate; confirmation phrases remain UX confirmations, not authentication.
- [x] Redaction covers logs, GOALS raw text, task output excerpts, config, prompts, and serialized runner arguments consistently before LAN exposure.

### P0-M. Browser-First Runner Operation

- [x] Start controls expose the same practical run modes operators use in the shell: autopilot, continuous, loop, one-shot, max cycles, profile, backend, and config path.
- [x] Start controls validate incompatible run options before launch and show the exact command-equivalent runner arguments that will be used.
- [x] Stop controls show phase-by-phase progress from request, stop file write, child termination, runner wait, final artifact collection, timeout, and finalized states.
- [x] Stop timeout state stays visibly actionable in the browser, including whether the runner is still alive, which child PIDs remain tracked, and which files may still be locked.
- [x] Browser restart/reload flows preserve intended run options instead of falling back to hidden defaults.
- [x] The browser can distinguish "runner process alive", "task backend alive", "tracked children alive", and "artifact writer still flushing" as separate states.
- [x] Runner control events are written to durable artifacts so shell, web, and remote-controller views agree after refresh or reconnect.

### P0-N. Real-Time Monitoring Completeness

- [x] Active run state updates use one consistent polling or streaming model with backoff, reconnect, stale-data detection, and visible last-updated timestamps.
- [x] Dashboard, Pipeline, Logs, Notifications, and Runner Controls consume the same normalized live-run contract instead of each reconstructing status independently.
- [x] A long-running PM/Dev/QA task shows elapsed time, last log line, latest backend event, and "no output for N minutes" warning without requiring terminal access.
- [x] Live logs preserve scroll position, selection, pause state, and filters across route changes and refreshes.
- [x] Log tailing can switch between `run.log`, `error.log`, `events.jsonl`, `cycle_summary.log`, and backend transcript sources when available.
- [x] UI explicitly marks stale snapshots when the run directory, controller state, and process table disagree.
- [x] Playwright coverage verifies a simulated long-running task, stop-in-progress sequence, reconnect, stale snapshot, and completed-run transition.

### P0-O. Enterprise-Grade Config And Role Management

- [x] Role editing preserves unknown/plugin stage specs such as `pkg.mod:Class` instead of silently dropping values outside the built-in enum.
- [x] Built-in role choices, defaults, and ordering have a single source of truth shared by CLI, web schema, stage registry, tests, and docs.
- [x] The web config editor exposes PM, Security, Dev, QA, reporter, and fallback model settings with labels that match their runtime behavior.
- [x] Model selection supports the approved Codex model ladder: PM `gpt-5.5`; Dev fallback `gpt-5.4-mini -> gpt-5.4 -> gpt-5.5`; QA `gpt-5.5`; reporter `gpt-5.4-mini`.
- [x] Security role UX shows both requirements: the role must be selected and `security.enabled` must be true, with a warning when one side is missing.
- [x] Config saves normalize list/string fields before runner launch so web-saved `roles`, models, allowlists, and paths cannot produce zero-stage runs.
- [x] Config save validation reports every rejected field in one response, not only the first failure.
- [x] Config backup/restore UI can list recent backups and restore a selected backup with confirmation.

### P0-P. Worktree Review, Merge, And Cleanup Operations

- [x] Worktree Review includes an inspectable per-file diff view with binary, deleted, renamed, and large-file states handled explicitly.
- [x] Merge preflight details are shown in the UI: source dirty state, source `HEAD`, expected base ref, patch hash, `git apply --check`, and pending marker path.
- [x] Merge conflicts or patch apply failures show exact failed files/hunks and leave the pending state recoverable.
- [x] Worktree merge prefers fast-forwarding committed worktree history before applying a dirty-only patch when `head_ref` descends from `base_ref`, so one stale hunk in a cumulative patch cannot block an otherwise mergeable run.
- [x] Worktree Review shows split-merge metadata, including fast-forward ref, dirty patch path, and whether the dirty patch was applied, when a merge uses the fast-forward-then-patch recovery path.
- [x] Discard and cleanup actions distinguish source-safe discard, generated worktree removal, stale marker pruning, and cleanup-failed reconciliation.
- [x] Worktree diagnostics can filter active, pending, stale, orphaned, cleanup-failed, and missing-patch entries without mutating files by default.
- [x] Cleanup-failed artifacts are cleared automatically only after the worktree path and marker state are actually reconciled.
- [x] Windows locked-path cleanup reports the locking path, affected artifact, retry schedule, and reboot-required guidance when user-mode cleanup cannot progress.
- [x] API and Playwright tests cover failed merge preflight, patch apply failure, stale marker repair, orphaned worktree reporting, and cleanup-failed reconciliation.

### P0-Q. Safe Self-Development Automation

- [x] PM task generation consumes `.doc/GOALS.md` directly and refuses to create irrelevant backlog tasks when GOALS has unmet P0 items.
- [x] Generated task branches include the GOALS item id or exact text they satisfy, and completion can be traced from commit to GOALS checkbox.
- [x] AgentCLI self-runs cannot stop early with `reason=ok` while any required GOALS completion level remains unmet.
- [x] Task completion requires compile checks plus the fast regression suite relevant to touched files, with failures persisted in run artifacts.
- [x] Worktree-isolated build/test gates resolve repo-local virtualenv commands such as `.venv/Scripts/python.exe` from the source repo while executing against the generated worktree.
- [x] Validation subprocesses return deterministic stop/timeout results and clean reader tasks/child processes so a stuck gate cannot strand a run indefinitely at `test_start`, `fast_regression_start`, or `runner_wait`.
- [x] QA stage records the validation commands, return codes, artifacts, and skipped-test rationale in a browser-readable report.
- [x] Reporter stage writes a concise final run report that the Web UI can render without reading raw terminal scrollback.
- [x] Failed tasks are carried into the next PM prompt through a structured failed-tasks block and are visible in Web History.
- [x] The browser can show "what changed this cycle" from git commits, changed files, tests, GOALS updates, and pending worktree state.

### P0-R. Web UI Production Polish And Accessibility

- [x] All primary routes have dense but readable desktop layouts with no nested-card clutter, no text overlap, and stable control dimensions.
- [x] Mobile routes match the intended operational workflow, not only a visual preview, with usable navigation, filters, editors, and confirmations.
- [x] Background and surface colors use a neutral charcoal base; green is reserved for success/accent/status signals rather than global page tint.
- [x] Direction A visual fidelity is reviewed against `docs/Design/project/AgentCLI Web - A.html`, covering shell/nav density, command palette, dashboard/Pipeline composition, mobile Telegram-style view, and status color semantics with desktop/mobile screenshots.
- [x] Design-token audit aligns production `web_console/styles.css` with Direction A's deep near-black/green terminal palette while preserving readable contrast and reserving green for success/accent/status semantics.
- [x] Destructive, mutating, and long-running actions use consistent confirmation, busy, disabled, success, failure, timeout, and retry states.
- [x] Empty, partial, loading, stale, permission-denied, and backend-unavailable states are visually distinct across every screen.
- [x] Keyboard navigation covers route switching, command palette, modals, editors, diff views, logs, and confirmation dialogs.
- [x] Accessibility checks cover focus handling, accessible control names, design-token contrast, reduced motion, and ARIA states for primary controls.
- [x] Playwright screenshots validate every primary desktop route in English and Korean plus mobile workflow views.

### P0-S. Documentation And Personal Automation Readiness

- [x] `.doc/DOCS_DIGEST.md` and `docs/MASTER_INDEX.md` are generated or validated from real files so moved, archived, or case-mismatched paths cannot mislead PM/Dev/QA runs.
- [x] User-facing docs for config, CLI flags, model defaults, Telegram options, stop reasons, and worktree merge behavior are checked against the live parser/defaults and fail validation on stale claims.
- [x] Web console docs are checked against the live FastAPI route inventory and do not claim nonexistent backup, config, prompt, goals, or runner endpoints.
- [x] Documentation for shutdown reports, duplicate-report handling, and artifact writers matches the current implementation instead of preserving obsolete incident assumptions.
- [x] Web documentation defines the current one-repo-one-web operating model and explicitly defers multi-repo dashboard scope to a later phase.
- [x] A visible identity header shows active repo, branch, run id, run dir, port, mode, runner-control status, and redaction status on every primary route.
- [x] A repo-level web instance lock prevents accidental duplicate local-operator control of the same repo.
- [x] Reload/restart while stopped behaves as start-only or no-op and does not write STOP or stop-progress artifacts into historical runs.
- [x] Pre-run readiness checks verify source `.venv`, Git worktree ownership/safe-directory state, stale STOP/runner_wait artifacts, and already-merged generated worktrees before a long unattended run starts.
- [x] Worktree cleanup diagnostics can report ACL-denied residual directories after Git unregisters a worktree and provide reboot/admin cleanup guidance without blocking new runs.
- [x] Parent watchdog cleanup does not hold parent process handles indefinitely; it polls PID/create-time state with bounded sleeps and closes handles on every check.
- [x] Codex app-server subprocess cleanup explicitly closes stdio pipes, joins the reader thread, unregisters process-guard PIDs, and cannot leave inherited handles behind after timeout or forced termination.
- [x] AgentCLI entrypoints register structured logger cleanup with `atexit` so `run.log`, `error.log`, `debug.log`, and `events.jsonl` handlers are closed on interpreter shutdown.
- [x] Web runner start rejects `run_dir` values outside the active repo's approved AgentCLI run root and rejects `config_path` values outside approved config roots.
- [x] LAN mode blocks raw prompt reads and keeps mutating actions disabled until authentication exists or a stronger trusted-operator gate is implemented.

### P0-T. Local PR Queue And Deferred Validation

- [x] GOALS-constrained PM task generation prevents oversized task bundles by splitting tasks that match more than two unchecked GOALS items while preserving `goal_trace`.
- [x] Runner can create a local PR review packet for each completed task or run without mutating source `main`.
- [x] Task branches are preserved and indexed after worktree cleanup.
- [x] PR queue records base/head refs, branch, commits, changed files, GOALS trace, QA notes, validation status, and merge preflight state.
- [x] Dev-stage test skipping is recorded as `validation_pending`, `tests_skipped`, or `no_tests_found`, never as success.
- [x] Full validation runs on demand in an isolated temporary worktree.
- [x] Merge approval requires validation result plus explicit user approval.
- [x] Web PR Queue shows diff, QA notes, validation logs, merge preflight, and blocking reasons.
- [x] Dependency-blocked tasks expose the blocking upstream task id, title, status, reason, validation summary, and next action instead of only `Depends on: ['Tn']`.
- [x] Shell commands support listing, validating, rebasing, merge-approving, and discarding queued PRs.
- [x] Telegram can list queued PRs and report validation/merge status.
- [x] Stale branches, missing patch artifacts, and deleted worktrees are reconciled without corrupting the queue.

### P0-U. Experience DB And Analyzer Stage

- [x] Experience DB schema and migration exist under `.AgentCLI/experience` with tables for runs, task experiences, validation experiences, file patterns, and lessons.
- [x] Completed task experience records link run id, task id, GOALS refs, changed files, branch/head refs, validation artifacts, and local PR packet ids.
- [x] Failed task experience records preserve task status, reason, dependency blockers, validation summary, artifact pointers, and retry/discard outcome without storing raw logs.
- [x] Validation experience records classify `validation_pending`, `tests_skipped`, `no_tests_found`, `validation_failed`, `blocked_env`, and `validation_passed` separately.
- [x] Local PR queue validate, merge-approval, discard, and rebase decisions are recorded as experience signals tied to the PR packet and GOALS trace.
- [x] Deterministic Analyzer rules produce `ANALYZER_SUMMARY.json` from run artifacts without calling an LLM.
- [x] Analyzer lesson records include kind, normalized trigger, GOALS refs, file globs, gate, task status, evidence pointers, confidence, and last-applied metadata.
- [x] Analyzer output is advisory only and cannot mark GOALS complete, approve merges, mutate source code, or bypass deterministic validation gates.
- [x] PM receives a token-bounded experience summary block even when a custom PM prompt is configured.
- [x] Experience summary injection enforces max item count, max characters, no raw logs, no raw diffs, and redacted evidence pointers.
- [x] Experience lessons can recommend task sizing, validation selection, retry avoidance, and dependency cleanup based on recorded evidence.
- [x] Web Console shows recent lessons, repeated failure patterns, validation gaps, and merge blockers from read-only Experience DB data.
- [x] Telegram can summarize latest experience blockers and queued PR validation needs without exposing raw prompts or logs.
- [x] Experience retention settings prune old lessons and evidence pointers without deleting pending PR queue or active run artifacts.
- [x] Experience redaction settings prevent secrets, raw backend transcripts, raw prompts, and long test output from leaking into future PM prompts.

### P0-V. Maintainability And Module Decomposition

- [x] Web import compatibility tests protect `agent_runner.web` public and test-used private helper names before any helper extraction.
- [x] Web endpoint golden tests protect `/api/status`, `/api/progress`, `/api/worktree`, and `/api/runner/status` payload contracts.
- [x] Web endpoint golden tests protect `/api/config`, `/api/goals`, `/api/prompts`, and `/api/logs` payload contracts.
- [x] Web redaction helpers are extracted into a focused module while `agent_runner.web` re-exports the old helper names.
- [x] Web GOALS parse, serialize, validate, backup, and save helpers are extracted while `/api/goals` behavior stays unchanged.
- [x] Web prompt inventory, read, validation, backup, and save helpers are extracted while `/api/prompts` behavior stays unchanged.
- [x] Web config schema, normalization, validation, backup, and save helpers are extracted while `/api/config` behavior stays unchanged.
- [x] Web log tail source discovery and line parsing helpers are extracted while `/api/logs` behavior stays unchanged.
- [x] Web history, metrics/progress, worktree, stage, and snapshot payload builders are extracted behind `agent_runner.web` facade functions.
- [x] Runner context objects define source repo, execution worktree, run directory, task directory, attempt directory, and task branch state.
- [x] `cycle.py` uses runner/task/attempt context objects for validation artifact paths without changing run artifact filenames.
- [x] Validation artifact writing is shared by Codex and Claude backends through a neutral helper module.
- [x] Failed-task result recording is shared by Codex and Claude backends through a neutral helper module.
- [x] Stop progress recording is shared by shell, Codex backend, Claude backend, and web runner controls.
- [x] Task branch preserve, abandon, rollback, and cleanup dispatch are shared by Codex and Claude backends.
- [x] Codex and Claude PM output postprocessing use the same GOALS gating, task splitting, and `goal_trace` preservation logic.
- [x] Backend adapter interfaces isolate model invocation, message streaming, model option construction, and quota probing from orchestration code.
- [x] Codex backend-specific code is limited to Codex CLI execution, Codex app-server integration, Codex quota probing, and Codex model options.
- [x] Claude backend-specific code is limited to Claude SDK/CLI execution, Claude streaming collection, Claude quota probing, and Claude model options.
- [x] Current decomposition boundaries are protected by import, endpoint, artifact-name, and backend-boundary tests so extraction changes do not silently change product contracts.

### P0-W. Web Operational UX Follow-Up

- [x] Dashboard stale snapshot badge only appears when the newest `/api/status` payload or selected run artifact is older than the configured freshness threshold.
- [x] Dashboard active task id, title, attempt, and branch are populated from live runner state, backlog state, task history, or latest task artifact instead of showing `task title unavailable`.
- [x] Runner liveness copy clearly separates shell runner process, task backend, tracked children, and artifact writer states without contradictory `running`/`stopped` wording.
- [x] Logs view renders structured `/api/logs` events immediately when `run.log` is sparse and labels structured events separately from live tail sources.
- [x] Log tail state leaves `loading` at EOF and shows last log line plus no-output warning when no new `run.log` lines arrive.
- [x] Worktree Review defaults to the active run's current pending/no-pending state and does not surface old applied/discarded artifacts unless the user opens historical context.
- [x] Mobile navigation confines overflow to an intentional visible horizontal scroll container or wraps route groups without hidden off-screen controls at 390px width.
- [x] Web snapshot polling uses route-appropriate payloads so Dashboard refresh does not require multi-megabyte `/api/status` responses containing full GOALS raw text/history data.
- [x] Web PR Queue route lists queued local PR packets with task id, GOALS refs, branch, changed files, validation status, QA notes, and merge preflight status.
- [x] Web PR Queue detail view shows per-file diff, validation logs, blockers, dependency detail, and explicit read-only disabled validate/merge/discard/rebase affordances.

### P0-X. Unattended Operations Follow-Up

- [x] Direct runner and resume entrypoints reconcile stale STOP files using heartbeat age and an audit event without deleting fresh operator STOP requests.
- [x] Long sleeps, quota waits, and loop idle waits refresh `HEARTBEAT` at bounded intervals while remaining STOP-aware.
- [x] Claude backend quota and wait paths use the shared STOP-aware sleep helper instead of raw long `asyncio.sleep` calls.
- [x] Startup readiness can auto-reconcile stale `WORKTREE_MERGE_PENDING.json` markers when the patch/worktree is missing or source `HEAD` already reflects the pending head, while preserving valid pending merges.
- [x] Stale task branches and attempt directories are listed by doctor/readiness with age, status, reason, and owning run before any cleanup is offered.
- [x] Stale branch, stale attempt, and old run cleanup require explicit operator approval and write dry-run plus applied cleanup artifacts.
- [x] An unattended preset documents or configures `goals_auto_refresh`, quota wait, loop, loop idle exit, iteration limits, diagnostics, and safe cleanup defaults as one operator-facing profile.
- [x] Backlog scheduling records task effort, priority, touched file globs, and dependencies before selection.
- [x] Backlog selection uses dependency-aware ordering plus remaining-window budget caps so overnight runs prefer small unblocked tasks before large risky tasks.
- [x] Overnight runs write a concise post-run operations summary covering completed, queued, review-required, blocked-env, stale-cleanup, handle/process warnings, and next operator actions.
- [x] Windows handle/process diagnostic collection is linked to run artifacts and flags process-count or handle-growth anomalies before Explorer/CMD instability recurs.

### P0-Y. Failure Disposition, Backend Parity, And State Integrity

- [x] A `failure_policy` module decides task disposition (`retry`, `preserve_for_review`, `abandon_branch`, `restore_checkpoint`, `stop_run`) from reason, task status, and attempt budget so Codex and Claude share one policy.
- [x] `_isolate_or_stop` consumes a typed `FailureOutcome` so `blocked_env` and `test_contract_changed` tasks are preserved for human review instead of abandoned.
- [x] `backends/claudecode.py` records failures with the same task-status enriched schema as `cycle.py` so failover cannot produce mixed `STATE.json` schemas.
- [x] `task_status.py` classifier covers Java, Go, Rust, C/C++, Kotlin, Swift, Maven, Gradle, NuGet, Cargo, regression, and dependency-resolution patterns with multi-language tests.
- [x] `task_history` SQLite stores `task_status` so PM failed-task context and consecutive-failure handling distinguish environment-blocked tasks from code regressions.
- [x] `needs_dependency` and `blocked_dependency` reasons are first-class `BLOCKED_ENV` mappings in `classify_task_failure` without relying on text-pattern inference.
- [x] Shutdown reports and Web Console task counters split regression, review-needed, and blocked-env groups so environment failures do not look like code regressions.
- [x] Codex and Claude backends use the same failure disposition, validation artifact, local PR queue, and run report helpers so failover cannot produce mixed schemas.
- [x] PR queue packet and index writes are lock-protected and recoverable after interrupted packet or index updates.
- [x] `STATE.json` mutation helpers preserve concurrent done, failed, and warning updates across runner, controller, and future Web mutation paths.
- [x] GOALS auto-check writes atomically and detects operator edit conflicts instead of overwriting Web edits.
- [x] Attempt directories record `STARTED` and `FINISHED` markers, and preflight reports interrupted attempts before a new unattended run starts.
- [x] Preflight reports stale Git, web instance, and Telegram lock files with age, owner evidence when available, and safe operator guidance.
- [x] Runner subprocess launch paths explicitly close inherited file descriptors or document tested handle inheritance behavior on Windows.

### P0-Z. Stage Effects And Backlog Refiner Runtime

- [x] `StageOutcome` supports declared effects such as `backlog_written` and `tasks_reload_required` so `PipelineManager` can safely apply stage side effects.
- [x] `PipelineSession` exposes safe artifact and backlog write APIs for state-mutating stages.
- [x] `PipelineManager` reloads task state after any stage declares backlog mutation.
- [x] A built-in PL/Backlog Refiner can run between PM and Dev and split oversized tasks while preserving GOALS trace and dependencies.
- [x] Web Config and Pipeline views support PL and plugin stages without dropping unknown role specs.

## P1 (Should-Have)

- [ ] Run History supports comparing two runs side-by-side with commits, task outcomes, token/quota usage, validation results, and worktree outcomes.
- [ ] Notifications support read/unread state, filtering, severity grouping, and links back to the relevant run/task/log lines.
- [ ] Web report export can create Markdown and JSON summaries for a selected run.
- [x] The browser can open local artifact paths through a documented safe helper instead of exposing raw filesystem mutation.
- [x] The CLI can optionally launch or serve the web console from a documented command.
- [x] Web health and startup diagnostics report missing FastAPI/uvicorn dependencies and broken virtual environments.
- [x] UI state clearly distinguishes fallback/demo data from real API data.
- [ ] Web PR Queue browser controls can validate, merge, discard, and rebase queued packets with the same safety gates as shell commands.
- [ ] Authentication plan exists before use outside trusted private networks.
- [x] A personal Runbook panel renders venv activation, shell start, web serve, status, stop, merge, discard, diagnostics, and recommended long-run commands for the active repo.
- [x] Each run writes a concise `WORK_SUMMARY.md` suitable for daily work logs without exposing raw secrets or long transcripts.
- [x] Web action audit artifacts record local start, stop, restart, config, prompt, goals, and worktree actions with timestamps and results.
- [ ] Local retention settings and dry-run prune reports manage run directories, logs, diagnostics, and backups without deleting pending worktree review state.
- [ ] TODO management is visible from shell/web status with active TODO path, freshness, PM injection state, and safe preview/edit controls without overriding GOALS-first PM gating.
- [ ] Skills doctor/status shows configured roots, discovered skill count, selected skill ids, missing skill warnings, and fuzzy-match suggestions.
- [ ] PM/Dev/QA skill injection is covered by tests for disabled, enabled, missing-root, missing-skill, and fuzzy-autofix modes.
- [ ] Claude advanced controls expose validated config, diagnostics, and tests for MCP tools, hooks, dynamic permission, strict isolation, and subagent enablement.
- [ ] Claude backend parity tests cover PR queue, task status, failure policy, validation artifacts, and advanced-control disabled/enabled modes.
- [ ] MCP mode diagnostics report selected mode, timeout, unavailable tools, and safe fallback behavior without blocking non-MCP runs.
- [ ] Plugin stage loading has allowlist, strict-mode, failure diagnostics, Web config validation, and tests for allowed, blocked, missing, and load-error stages.
- [ ] Enterprise profile has tests and Web config visibility for Security stage insertion, policy/security scan enablement, and budget floor enforcement.
- [ ] Completed runs can persist a lightweight redacted final web-history snapshot for replay without storing raw prompts, raw logs, or full GOALS text.
- [ ] Command Palette exposes operator actions for Runbook, PR Queue, diagnostics, run history, config changes, and safe runner controls with disabled/read-only states.
- [ ] Instance Health view summarizes process guard state, tracked child PIDs, handle/process diagnostic warnings, web instance lock state, and stale artifact risks.
- [ ] Critical path smoke tests cover backend failover, quota wait, outer-loop reason handling, interrupted attempt recovery, and PR queue reconcile.
- [ ] Local retention dry-run includes `agent_runs`, `PM_CACHE`, logs, diagnostics, and backups while preserving pending review evidence.
- [ ] Latent risk hardening covers logger rotation, agent run retention, task-history indexes, and analysis-cache size caps.

## Completion Criteria

- All P0 and P1 items are checked from real browser and API validation, not visual inspection alone.
- AgentCLI self-runs use `goals_completion_level=all`, so P0-only completion must not stop unattended web-console work.
- The production page runs from `web_console/` and `agent_runner.web`; `docs/Design/project/` remains reference-only.
- A user can monitor a real run, inspect logs, edit Goals/Config/Prompts safely, operate the runner, and review worktree merges from the browser with confirmations.
- The page has no blank primary views, no uncaught JavaScript errors, and no obvious layout overlap on desktop or mobile.
- AgentCLI Python compile checks and web console unit tests pass.
- Playwright screenshots/snapshots prove the key views render correctly in both English and Korean.
