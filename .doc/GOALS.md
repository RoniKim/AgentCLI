# Project Goals - AgentCLI Web Console

> Product target: make AgentCLI fully usable from a browser for monitoring, configuration, prompt review/editing, logs, goals, worktree review, and safe runner operations.
> Design source: `docs/Design/`, especially `docs/Design/project/AgentCLI Web - A.html`.
> This file is the target backlog, not a status report. Current implementation notes belong in `docs/WEB_CONSOLE_STATUS.md`.
> Do not downgrade, remove, or merge unmet P0 goals to make progress look complete; implement them until they are true.
> Task sizing rule: each unchecked P0 item should be small enough for one focused AgentCLI task branch.
> Last reviewed: 2026-04-27.

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
- [ ] Existing isolated worktree directories are reused only when run id, expected head, current branch, clean/dirty state, and source repo ownership match the active run contract.
- [ ] Worktree merge preflight blocks unsafe apply when the source repo is dirty, source `HEAD` differs from `base_ref`, the patch hash does not match metadata, or `git apply --check` fails.
- [ ] Worktree cleanup on Windows retries locked-path removal, records exact permission failures, and keeps cleanup-failed states visible until reconciled.
- [ ] A worktree doctor/list/prune command or API can report orphaned worktrees, stale pending markers, missing patches, and cleanup-failed artifacts without mutating source files by default.
- [ ] Shell, web, and remote-controller starts create a new `run_dir` by default; latest run reuse requires explicit resume or `--run-dir` intent.
- [ ] `STATE.json` done/failed counts are scoped to the current backlog generation so stale task ids from previous backlogs cannot inflate progress or history.
- [ ] Web snapshots separate execution status from project completion status, so `rc=0`/`reason=ok` does not appear as project success while required goals or backlog items remain incomplete.
- [ ] Goals parsing treats missing or malformed required priority sections as invalid/incomplete instead of silently completing P0/P1 modes.
- [ ] Goals auto-refresh and completion checks use the same configured `goals_completion_level` in shell, runner, web, and tests.
- [x] Web console tests are aligned with `goals_completion_level=all` defaults and pass without relying on operator-specific local config.
- [ ] Self-development runs execute at least the fast web/worktree regression suite, not only `compileall`, before marking task branches complete.
- [x] LAN web serving refuses runner controls without real authentication or an explicit trusted-network gate; confirmation phrases remain UX confirmations, not authentication.
- [ ] Redaction covers logs, GOALS raw text, task output excerpts, config, prompts, and serialized runner arguments consistently before LAN exposure.

## P1 (Should-Have)

- [ ] Mobile layout matches the `A_Mobile` design path without text overlap or broken controls.
- [ ] Run History supports comparing two runs side-by-side.
- [ ] Notifications support read/unread state and filtering.
- [ ] Keyboard navigation is complete: command palette, `g` navigation chords, Escape handling, focus states, and accessible labels.
- [ ] The CLI can optionally launch or serve the web console from a documented command.
- [ ] Web console exposes diagnostics for missing FastAPI/uvicorn dependencies and broken virtual environments.
- [x] UI state clearly distinguishes fallback/demo data from real API data.
- [ ] Authentication plan exists before use outside trusted private networks.

## Completion Criteria

- All P0 and P1 items are checked from real browser and API validation, not visual inspection alone.
- AgentCLI self-runs use `goals_completion_level=all`, so P0-only completion must not stop unattended web-console work.
- The production page runs from `web_console/` and `agent_runner.web`; `docs/Design/project/` remains reference-only.
- A user can monitor a real run, inspect logs, edit Goals/Config/Prompts safely, operate the runner, and review worktree merges from the browser with confirmations.
- The page has no blank primary views, no uncaught JavaScript errors, and no obvious layout overlap on desktop or mobile.
- AgentCLI Python compile checks and web console unit tests pass.
- Playwright screenshots/snapshots prove the key views render correctly in both English and Korean.
