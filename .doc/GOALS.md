# Project Goals - AgentCLI Web Console

> Product target: make AgentCLI fully usable from a browser for monitoring, configuration, prompt review/editing, logs, goals, worktree review, and safe runner operations.
> Design source: `docs/Design/`, especially `docs/Design/project/AgentCLI Web - A.html`.
> This file is the target backlog, not a status report. Current implementation notes belong in `docs/WEB_CONSOLE.md`, `docs/MASTER_INDEX.md`, or archived incident/design notes.
> Do not downgrade, remove, or merge unmet P0 goals to make progress look complete; implement them until they are true.
> Task sizing rule: each unchecked P0 item should be small enough for one focused AgentCLI task branch.
> Last reviewed: 2026-04-29.

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

- [ ] All primary routes have dense but readable desktop layouts with no nested-card clutter, no text overlap, and stable control dimensions.
- [ ] Mobile routes match the intended operational workflow, not only a visual preview, with usable navigation, filters, editors, and confirmations.
- [ ] Background and surface colors use a neutral charcoal base; green is reserved for success/accent/status signals rather than global page tint.
- [ ] Direction A visual fidelity is reviewed against `docs/Design/project/AgentCLI Web - A.html`, covering shell/nav density, command palette, dashboard/Pipeline composition, mobile Telegram-style view, and status color semantics with desktop/mobile screenshots.
- [ ] Design-token audit aligns production `web_console/styles.css` with Direction A's deep near-black/green terminal palette while preserving readable contrast and reserving green for success/accent/status semantics.
- [ ] Destructive, mutating, and long-running actions use consistent confirmation, busy, disabled, success, failure, timeout, and retry states.
- [ ] Empty, partial, loading, stale, permission-denied, and backend-unavailable states are visually distinct across every screen.
- [ ] Keyboard navigation covers route switching, command palette, modals, editors, diff views, logs, and confirmation dialogs.
- [ ] Accessibility checks cover focus visibility, labels, contrast, reduced motion, and screen-reader names for icon-only controls.
- [ ] Playwright screenshots validate Dashboard, Pipeline, Logs, Goals, Config, Prompts, History, Notifications, Worktree Review, Runner Controls, and mobile in both locales.

### P0-S. Documentation And Personal Automation Readiness

- [ ] `.doc/DOCS_DIGEST.md` and `docs/MASTER_INDEX.md` are generated or validated from real files so moved, archived, or case-mismatched paths cannot mislead PM/Dev/QA runs.
- [ ] User-facing docs for config, CLI flags, model defaults, Telegram options, stop reasons, and worktree merge behavior are checked against the live parser/defaults and fail validation on stale claims.
- [ ] Web console docs are checked against the live FastAPI route inventory and do not claim nonexistent backup, config, prompt, goals, or runner endpoints.
- [ ] Documentation for shutdown reports, duplicate-report handling, and artifact writers matches the current implementation instead of preserving obsolete incident assumptions.
- [ ] Web documentation defines the current one-repo-one-web operating model and explicitly defers multi-repo dashboard scope to a later phase.
- [ ] A visible identity header shows active repo, branch, run id, run dir, port, mode, runner-control status, and redaction status on every primary route.
- [ ] A repo-level web instance lock prevents accidental duplicate local-operator control of the same repo.
- [ ] Reload/restart while stopped behaves as start-only or no-op and does not write STOP or stop-progress artifacts into historical runs.
- [ ] Pre-run readiness checks verify source `.venv`, Git worktree ownership/safe-directory state, stale STOP/runner_wait artifacts, and already-merged generated worktrees before a long unattended run starts.
- [ ] Worktree cleanup diagnostics can report ACL-denied residual directories after Git unregisters a worktree and provide reboot/admin cleanup guidance without blocking new runs.
- [ ] Parent watchdog cleanup does not hold parent process handles indefinitely; it polls PID/create-time state with bounded sleeps and closes handles on every check.
- [ ] Codex app-server subprocess cleanup explicitly closes stdio pipes, joins the reader thread, unregisters process-guard PIDs, and cannot leave inherited handles behind after timeout or forced termination.
- [ ] AgentCLI entrypoints register structured logger cleanup with `atexit` so `run.log`, `error.log`, `debug.log`, and `events.jsonl` handlers are closed on interpreter shutdown.
- [ ] Web runner start rejects `run_dir` values outside the active repo's approved AgentCLI run root and rejects `config_path` values outside approved config roots.
- [ ] LAN mode blocks raw prompt reads and keeps mutating actions disabled until authentication exists or a stronger trusted-operator gate is implemented.

## P1 (Should-Have)

- [ ] Run History supports comparing two runs side-by-side with commits, task outcomes, token/quota usage, validation results, and worktree outcomes.
- [ ] Notifications support read/unread state, filtering, severity grouping, and links back to the relevant run/task/log lines.
- [ ] Web report export can create Markdown and JSON summaries for a selected run.
- [ ] The browser can open local artifact paths through a documented safe helper instead of exposing raw filesystem mutation.
- [ ] The CLI can optionally launch or serve the web console from a documented command.
- [ ] Web console exposes diagnostics for missing FastAPI/uvicorn dependencies and broken virtual environments.
- [x] UI state clearly distinguishes fallback/demo data from real API data.
- [ ] Authentication plan exists before use outside trusted private networks.
- [ ] A personal Runbook panel renders venv activation, shell start, web serve, status, stop, merge, discard, diagnostics, and recommended long-run commands for the active repo.
- [ ] Each run writes a concise `WORK_SUMMARY.md` suitable for daily work logs without exposing raw secrets or long transcripts.
- [ ] Web action audit artifacts record local start, stop, restart, config, prompt, goals, and worktree actions with timestamps and results.
- [ ] Local retention settings and dry-run prune reports manage run directories, logs, diagnostics, and backups without deleting pending worktree review state.

## Completion Criteria

- All P0 and P1 items are checked from real browser and API validation, not visual inspection alone.
- AgentCLI self-runs use `goals_completion_level=all`, so P0-only completion must not stop unattended web-console work.
- The production page runs from `web_console/` and `agent_runner.web`; `docs/Design/project/` remains reference-only.
- A user can monitor a real run, inspect logs, edit Goals/Config/Prompts safely, operate the runner, and review worktree merges from the browser with confirmations.
- The page has no blank primary views, no uncaught JavaScript errors, and no obvious layout overlap on desktop or mobile.
- AgentCLI Python compile checks and web console unit tests pass.
- Playwright screenshots/snapshots prove the key views render correctly in both English and Korean.
