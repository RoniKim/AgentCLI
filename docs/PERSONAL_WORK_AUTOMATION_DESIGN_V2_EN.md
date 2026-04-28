# Personal Work Automation Design v2

> Date: 2026-04-28  
> Scope: AgentCLI Web as a local, single-operator cockpit for one active repository.  
> Status: Updated design proposal. Safety-first revision based on current AgentCLI project direction.  
> Primary goal: make AgentCLI Web safe enough for daily personal work before expanding convenience features.

---

## Executive Summary

AgentCLI Web should remain a **one repo, one web instance, one local operator** tool for the current phase.

This is the strongest fit for the existing product shape:

- One resolved repository root.
- One FastAPI web process.
- One runner controller.
- One latest-run view.
- Repo-local `.AgentCLI/agent_runs` artifacts.
- Human-reviewed worktree output before merge.

The current design should **not** grow into a central multi-repo team dashboard yet. That would require a different product surface:

- Repo registry.
- Per-repo controller isolation.
- Authentication and authorization.
- Cross-repo routing.
- Cross-repo history.
- Stronger process and artifact isolation.
- Team audit semantics.

That is outside the correct near-term scope.

The near-term target is narrower and more valuable:

> A developer runs AgentCLI on a company PC, opens a local web console for the active repo, observes status/logs/config/prompts/GOALS/worktree output, and manually reviews isolated work before merge.

The highest-priority work is not visual polish.  
The highest-priority work is making local automation **safe, predictable, recoverable, and hard to misuse**.

---

## Revised Product Positioning

### Product Shape

AgentCLI Web is a **local-first personal automation cockpit**.

It is:

- **Repo-scoped**: one server instance owns exactly one active repository.
- **Operator-scoped**: one human operator controls start, stop, review, merge, discard, and cleanup.
- **Artifact-scoped**: durable state lives in repo-owned or AgentCLI-owned files, not in a shared central database.
- **Safety-scoped**: mutating actions require explicit operator intent, confirmation, and server-side containment checks.
- **Review-first**: generated worktree output must be inspected before being applied to the source repository.

### Non-Goals For This Phase

The following must stay out of scope for the current phase:

- Multi-repo central dashboard.
- Team-shared web portal.
- Cloud-hosted control plane.
- Remote LAN operator mode without authentication.
- Automatic worktree merge without human review.
- General automation unrelated to the active repository.
- Mobile-first remote control that bypasses guarded action contracts.
- Background daemon behavior that silently mutates repositories.

### Deferred Goals

These can be revisited later after the local single-repo cockpit is stable:

- Authenticated LAN mode.
- Multi-repo read-only overview.
- Team audit and role permissions.
- Central run history across repositories.
- External issue-tracker integration.
- Release-grade browser test gate.
- Long-term historical analytics.

---

## Core Design Principle

### Safety Before Convenience

The implementation should follow this order:

1. Prevent incorrect runner starts.
2. Prevent historical run mutation.
3. Prevent path escape.
4. Prevent LAN data exposure.
5. Prevent wrong worktree marker actions.
6. Add instance identity and operator confidence.
7. Add summaries, runbooks, retention, and polish.

Convenience features should not be implemented before the safety invariants are in place.

---

## Operating Modes

### Mode 1: Local Read-Only

Default mode.

Command shape:

```powershell
python -m agent_runner.web --repo "." --host 127.0.0.1 --port 8000
```

Properties:

- Reads one repository.
- Shows status, logs, GOALS, config, prompts inventory, history, metrics, notifications, and worktree state.
- Mutating actions are disabled.
- Safe for normal observation.
- Should be the default mode for casual monitoring.

---

### Mode 2: Local Operator

Explicit opt-in mode.

Command shape:

```powershell
python -m agent_runner.web --repo "." --host 127.0.0.1 --port 8000 --enable-runner-controls
```

Properties:

- Allows runner start, stop, reload, restart.
- Allows config save, prompt save/restore, GOALS save.
- Allows guarded worktree merge/discard/cleanup.
- Every destructive or long-running action requires explicit confirmation.
- UI clearly shows `Local Operator`.
- UI clearly shows the active repository path.
- Browser cannot change the active repository after server startup.

---

### Mode 3: Trusted LAN Read-Only

Read-only viewing from another personal device on a trusted network.

Properties:

- Runner controls disabled.
- Redaction active.
- Raw prompt reads blocked.
- Config secret-like values redacted.
- UI shows a visible LAN/read-only warning.

This mode should be treated as **view-only**, not operator-safe.

---

### Mode 4: LAN Operator

Not supported in this design.

Required before support:

- Authentication.
- CSRF/origin protection.
- Explicit local audit log.
- Redaction policy reviewed.
- Prompt raw-read policy closed.
- Mutating actions tied to an authenticated identity.
- Clear operator session boundary.

Until those exist, non-loopback operator control must be rejected.

---

## Architecture Decisions

### Decision 1: One Repository Per Web Instance

Each web process serves exactly one repository.

Required behavior:

- Repository path is resolved at server startup.
- Repository path is displayed on every primary route.
- Browser config cannot change `repo`.
- Runner start payload cannot override the active repo.
- Worktree actions reject source repository mismatches.
- API payloads include active repo, branch, run id, run dir, and worktree root.

Rationale:

- Current app state is organized around a single repo root.
- `.AgentCLI/agent_runs` is repo-local.
- GOALS and worktree pending markers are repo-local.
- Multiple web processes controlling the same repo can interfere unless explicitly guarded.

---

### Decision 2: One OS Process Per Repository

Parallel repository work should use separate server processes and separate ports:

```text
Repo A -> AgentCLI Web A -> 127.0.0.1:8001
Repo B -> AgentCLI Web B -> 127.0.0.1:8002
Repo C -> AgentCLI Web C -> 127.0.0.1:8003
```

Do not host multiple repositories inside one Python process in this phase.

Rationale:

- Process guard state is process-global.
- Controller state is app-local.
- Multi-repo hosting would require a new isolation model.

---

### Decision 3: Localhost First

Default bind remains `127.0.0.1`.

Requirements:

- Any non-loopback bind shows a warning banner.
- Runner controls are disabled on non-loopback until authentication exists.
- Redaction remains active on non-loopback.
- Raw prompt reads are blocked on non-loopback.
- Config values matching sensitive key patterns are redacted on non-loopback.

---

### Decision 4: Browser Cannot Escape Repository Boundaries

For web-initiated starts and saves:

- `run_dir` must be empty, explicitly resumed, or inside `<repo>/.AgentCLI/agent_runs`.
- `config_path` must be the server-selected config or an approved AgentCLI config path.
- `prompts_dir` must be the configured prompt root.
- GOALS writes must target the active repo’s GOALS path only.
- Worktree actions must validate:
  - source repo
  - pending marker path
  - run dir
  - worktree dir
  - patch path
  - base/head refs
  - patch hash, if available

---

### Decision 5: Repo-Level Instance Lock

Add a repo-level web owner lock:

```text
.AgentCLI/web/INSTANCE_LOCK.json
```

Suggested shape:

```json
{
  "schema_version": 1,
  "repo": "D:/work/project-a",
  "pid": 12345,
  "host": "127.0.0.1",
  "port": 8000,
  "mode": "local-operator",
  "started_at": "2026-04-28T09:00:00+09:00",
  "heartbeat_at": "2026-04-28T09:01:00+09:00"
}
```

Behavior:

- Server startup warns or refuses when a live lock exists for the same repo.
- `--force-instance-lock` can override stale locks.
- UI shows lock owner and current mode.
- Lock heartbeat is updated by the web process.
- Lock cleanup happens on normal shutdown when possible.

Recommended policy:

- **Warn by default in read-only mode.**
- **Fail by default in local-operator mode.**
- Allow explicit override for stale locks.

---

## Daily Workflow

### Setup

```powershell
cd D:\000.Work\001.Private\000.API\agent_cli
.\.venv\Scripts\Activate.ps1
python --version
```

### Start Shell

```powershell
python agent_cli.py --repo "D:\000.Work\001.Private\000.API\agent_cli"
```

Inside shell:

```text
/doctor
/status
/start --autopilot --continuous --iterations 3
```

### Watch Web

Read-only:

```powershell
python -m agent_runner.web --repo "." --host 127.0.0.1 --port 8000
```

Operator mode:

```powershell
python -m agent_runner.web --repo "." --host 127.0.0.1 --port 8000 --enable-runner-controls
```

### Stop

Preferred shell command:

```text
/stop --wait
```

Web stop is acceptable only in Local Operator mode.

### Review Output

Operator checks:

- Current stage and task.
- Latest backend output.
- Logs and errors.
- GOALS state.
- Worktree pending state.
- Generated diff.
- Test/build artifacts.
- Shutdown report.

### Merge

Manual default:

```text
/merge-worktree
```

or guarded web merge after inspecting Worktree Review.

### Close Out

Expected final artifacts:

- `run_summary.json`
- `SHUTDOWN_REPORT.md`
- `metrics.jsonl`
- `BACKLOG.json`
- `STATE.json`
- Worktree applied/discarded artifact, if relevant.
- `WORK_SUMMARY.md`, after Phase 3.

---

# MVP Scope

## MVP Goal

The first stable version should prove this:

> A single local operator can safely start AgentCLI Web for one repository, run or observe automation, review worktree output, and apply or discard changes without accidentally mutating the wrong run, wrong path, or stale worktree marker.

Everything else is secondary.

---

## P0 Safety Features

### P0-1. Start Intent Guard

Problem:

A web start draft can accidentally inherit a previous `run_dir`, making a fresh start behave like reuse/resume.

Target behavior:

- Default web start never sends `run_dir`.
- `run_dir` is sent only after explicit operator selection.
- `resume_latest` is a separate explicit toggle.
- Preview argv clearly shows whether `--run-dir` or `--resume-latest` will be used.
- Start validation rejects ambiguous start intent.

Acceptance criteria:

- Fresh web start creates a new run dir.
- Explicit resume uses the selected previous run dir.
- Explicit run dir selection is visible in the preview.
- Tests cover:
  - start after completed run
  - explicit resume
  - explicit run dir
  - start with no inherited run dir
  - invalid run dir outside repo

---

### P0-2. Stopped Reload/Restart Safety

Problem:

Reload/restart while the runner is already stopped may mutate historical run artifacts, such as writing STOP or stop-progress state into an old run.

Target behavior:

- Reload/restart while stopped behaves as start-only or returns a clear no-op.
- Historical run directories are not mutated by stopped reload/restart.
- STOP artifacts are written only to the currently active running run.

Acceptance criteria:

- Reload while stopped does not modify old run dir.
- Restart while stopped does not write STOP into historical run.
- UI copy clearly distinguishes:
  - reload active runner
  - restart active runner
  - start new runner
  - no active runner

---

### P0-3. Path Containment

Problem:

Web-provided paths can become dangerous if not server-validated.

Target behavior:

- `run_dir` must be inside `<repo>/.AgentCLI/agent_runs` unless an explicit approved policy exists.
- `config_path` must be the resolved config path or approved config root.
- `prompts_dir` must be the configured prompt root.
- Worktree patch and pending paths must be repo-owned or AgentCLI-owned.
- Path validation must be server-side, not only UI-side.

Acceptance criteria:

- `../` path escape is rejected.
- Absolute path outside repo is rejected.
- Symlink escape is rejected where possible.
- Tests cover `run_dir` escape and `config_path` escape.

---

### P0-4. LAN Prompt Raw Read Block

Problem:

Raw prompts can contain sensitive instructions, environment details, or operational context.

Target behavior:

- Raw prompt content cannot be fetched on non-loopback binds.
- Prompt inventory can still show names and metadata.
- Sensitive config values remain redacted.
- UI clearly states that LAN mode is read-only and redacted.

Acceptance criteria:

- Non-loopback prompt raw-read API returns blocked response.
- Localhost behavior remains unchanged.
- Tests cover prompt read on LAN and redaction metadata.

---

### P0-5. Worktree Marker Consistency

Problem:

The UI can show one pending marker while merge/discard acts on another, especially if stale central markers and run-local markers coexist.

Target behavior:

- The pending marker displayed in Worktree Review must be the exact marker used by merge/discard.
- Run-local pending marker wins over stale central marker.
- Central stale marker is not allowed to shadow valid run-local pending work.
- Merge/discard responses include the marker path acted on.

Acceptance criteria:

- UI shows selected marker path.
- Merge/discard validates the selected marker.
- Tests cover stale central marker plus valid run-local marker.
- Tests cover missing patch and malformed marker.

---

### P0-6. Stale Marker Cleanup

Problem:

Stale markers can block valid worktree review or create operator confusion.

Target behavior:

- Stale marker cleanup is a separate explicit action.
- Cleanup is not the same as discard.
- Cleanup validates that the marker is stale before deleting or archiving it.
- Cleanup cannot remove a valid pending marker.

Acceptance criteria:

- Stale central marker can be pruned.
- Valid pending marker cannot be pruned by stale cleanup.
- Cleanup action writes a small status artifact or audit event.
- Tests cover stale cleanup and invalid cleanup attempt.

---

## P1 Operator Confidence Features

### P1-1. Instance Identity Header

Add a visible identity strip on every primary route.

Fields:

- Mode: `Read-only`, `Local Operator`, `Trusted LAN Read-only`, or `Unsupported LAN Operator`.
- Repo name.
- Full repo path.
- Branch.
- Current git HEAD short SHA.
- Web host and port.
- Run id.
- Run dir.
- Worktree root.
- Runner controls enabled/disabled.
- Redaction active/inactive.
- Instance lock owner, if available.

Acceptance criteria:

- Every primary route exposes active repo and mode.
- Worktree Review and Runner Controls cannot be used without seeing the active repo.
- LAN mode visibly states redaction and control limitations.

---

### P1-2. Repo-Level Instance Lock

Implement `.AgentCLI/web/INSTANCE_LOCK.json`.

Acceptance criteria:

- Starting a second local-operator web server for the same repo fails or requires explicit override.
- Stale lock can be cleared with explicit CLI option.
- UI displays current lock owner.
- Tests cover:
  - live lock
  - stale lock
  - forced takeover
  - read-only warning mode

---

### P1-3. Worktree Review Queue

Promote Worktree Review from a status panel to a guarded workflow.

Sections:

- Pending summary.
- Source repo and branch.
- Base/head refs.
- Patch hash.
- Merge mode.
- Dirty patch path.
- Changed file list.
- Per-file diff.
- Binary/deleted/renamed/large file markers.
- Preflight checklist.
- Cleanup state.
- Recovery actions.

Acceptance criteria:

- UI shows the same pending marker that merge/discard will operate on.
- Fast-forward metadata is visible if applicable.
- Missing worktree behavior is explicit.
- Merge and discard use different validation paths.
- Cleanup is separate from discard.

---

### P1-4. Local Automation Start Presets

Provide small presets instead of exposing free-form flags first.

Presets:

- `Plan only`: PM/backlog preparation.
- `One pass`: one controlled automation cycle.
- `Focused work`: `--autopilot --continuous --iterations N`.
- `Loop watch`: `--loop --loop-max-cycles N`.
- `Safe worktree`: worktree isolation and manual merge.

Each preset shows the exact equivalent shell command.

Acceptance criteria:

- Operator can select a preset and inspect argv before start.
- Dangerous combinations are rejected before launch.
- Presets are UI sugar over existing runner options, not a separate execution path.

---

## P2 Daily Work Features

### P2-1. Personal Runbook Panel

Add a Web route or panel that shows the daily workflow for the active repo.

Content:

- Activate venv command.
- Shell start command.
- Web serve command.
- Recommended `/start` command.
- `/status`
- `/stop --wait`
- `/merge-worktree`
- Artifact locations.
- Current instance lock owner.

Acceptance criteria:

- Commands are generated from current repo path and port.
- Commands do not include secrets.
- Copy buttons are available.
- LAN mode shows read-only warning.

---

### P2-2. Work Summary Artifact

Generate a concise personal work summary per run.

Artifact:

```text
run_dir/WORK_SUMMARY.md
```

Sections:

- What was attempted.
- Tasks completed.
- Files changed.
- Tests/build commands and results.
- Worktree merge status.
- Remaining issues.
- Suggested next command.

Acceptance criteria:

- Web History can render `WORK_SUMMARY.md`.
- Summary avoids raw secrets and long backend transcripts.
- Operator can copy it into a daily work note.

---

### P2-3. Web Action Audit Artifact

Add local audit for personal accountability.

Artifact:

```text
run_dir/WEB_ACTION_AUDIT.jsonl
```

Events:

- `runner_start`
- `runner_stop`
- `runner_reload`
- `runner_restart`
- `config_save`
- `prompt_save`
- `prompt_restore`
- `goals_save`
- `worktree_merge`
- `worktree_discard`
- `worktree_cleanup`

Suggested event shape:

```json
{
  "ts": "2026-04-28T09:00:00+09:00",
  "action": "runner_start",
  "repo": "D:/work/repo",
  "run_id": "20260428-090000",
  "mode": "local-operator",
  "source": "web",
  "result": "ok",
  "details": {}
}
```

This is not team audit.  
It is local operator traceability.

---

## P3 Monitoring And Retention

### P3-1. Long-Running Task Health

Add active task health signals.

Signals:

- Elapsed time.
- Last log event.
- Last backend event.
- Last artifact write.
- No-output duration.
- Quota state.
- Budget state.
- Child process count.

Acceptance criteria:

- UI flags `no output for N minutes`.
- UI distinguishes:
  - runner alive
  - backend alive
  - child process alive
  - artifact writer flushing
- A stalled run shows safe actions:
  - wait
  - stop
  - inspect logs
  - retry

---

### P3-2. Snapshot And Log Performance

Reduce polling read amplification.

Design:

- Keep cursor-based log tail.
- Avoid scanning large logs from the beginning.
- Cache latest run history summaries between snapshot polls.
- Expose lightweight `/api/live` for dashboard polling.
- Keep heavy history/worktree diagnostics behind route-specific refresh.

Acceptance criteria:

- Dashboard polling does not rebuild expensive history every tick.
- Log tail handles large files without full scans.
- Tests cover large synthetic logs.

---

### P3-3. Artifact Retention Policy

Add local retention controls.

Config candidate:

```json
{
  "retention": {
    "max_run_dirs": 50,
    "max_days": 30,
    "keep_failed_runs": true,
    "keep_pending_worktree_runs": true,
    "prune_logs_over_mb": 100
  }
}
```

Acceptance criteria:

- Retention never deletes pending worktree review state.
- Dry-run prune report exists.
- Web shows disk usage by run artifacts.
- Operator can manually prune stale runs after confirmation.

---

## P4 Config And Role Confidence

### P4-1. Config Backup And Restore

Features:

- Config backup list.
- Restore flow.
- Restart-required diff summary.
- Save validation returns all field errors.

Acceptance criteria:

- Web-saved config cannot produce zero-stage runs.
- Unknown plugin role specs remain preserved.
- Operator sees exactly what requires restart.

---

### P4-2. Role Source Of Truth

Features:

- Single source of truth for role options.
- PM/Security/Dev/QA/Reporter model fields with runtime-aligned labels.
- Clear labels for backend and model selection.

Acceptance criteria:

- UI labels match runtime behavior.
- Config validation uses the same role definitions as runner start.
- Tests cover unknown role preservation and invalid role rejection.

---

## P5 Design Polish And Release Gates

### P5-1. Command Palette As Operator Hub

Commands:

- Open current run.
- Open logs.
- Pause/resume live tail.
- Open Worktree Review.
- Open Runbook.
- Copy status command.
- Copy stop command.
- Copy merge command.
- Switch locale.

Acceptance criteria:

- Palette commands are route-aware.
- Destructive actions still require confirmation overlays.
- Keyboard navigation is tested.

---

### P5-2. Mobile Scope

Mobile remains a status and light-control surface for this phase.

Supported:

- Current run status.
- Stage/task.
- Last log event.
- Notifications.
- Stop status visibility.

Not supported yet:

- Full config editing.
- Full prompt editing.
- Worktree merge.
- Raw prompt reads.

Acceptance criteria:

- Mobile view does not expose unsupported actions.
- Telegram or shell remains the better remote-control path until web auth exists.

---

## Data And Artifact Contracts

### Existing Durable Artifacts

AgentCLI already uses file artifacts as the primary source of truth:

```text
.AgentCLI/agent_runs/<run_id>/BACKLOG.json
.AgentCLI/agent_runs/<run_id>/STATE.json
.AgentCLI/agent_runs/<run_id>/metrics.jsonl
.AgentCLI/agent_runs/<run_id>/logs/run.log
.AgentCLI/agent_runs/<run_id>/run_summary.json
.AgentCLI/agent_runs/<run_id>/last_run_summary.json
.AgentCLI/agent_runs/<run_id>/SHUTDOWN_REPORT.md
.AgentCLI/agent_runs/<run_id>/WORKTREE_MERGE_*.json
.doc/GOALS.md
```

### New Proposed Artifacts

#### `.AgentCLI/web/INSTANCE_LOCK.json`

Purpose:

- Prevent accidental duplicate local web control of the same repo.

Priority:

- P1.

---

#### `run_dir/WORK_SUMMARY.md`

Purpose:

- Human-copyable work result for daily reporting.

Priority:

- P2.

---

#### `run_dir/WEB_ACTION_AUDIT.jsonl`

Purpose:

- Local action traceability for personal accountability.

Priority:

- P2.

---

#### `run_dir/WEB_SNAPSHOT.json`

Optional.

Purpose:

- Store final UI-visible snapshot for later replay.

Recommendation:

- Do not persist every polling snapshot.
- Persist final snapshot and selected state transitions first.

Priority:

- P3 or later.

---

## UI Information Architecture

### Existing Primary Routes To Keep

- Dashboard
- Pipeline
- Logs
- Backlog
- Goals
- Config
- Prompts
- Run History
- Notifications
- Worktree Review
- Mobile

### New Routes Or Panels

Add in this order:

1. Instance identity header, not a route.
2. Worktree Review workflow improvements.
3. Runbook.
4. Instance Health.
5. Artifact Retention.

### Dashboard Additions

Add compact sections:

- Instance identity.
- Current run health.
- Start intent preview.
- Latest warning.
- Next safe action.
- Worktree pending banner.
- LAN/redaction warning, if applicable.

### Worktree Review Additions

Make this the most defensive screen:

- Never hide source repo.
- Never hide base ref, head ref, patch path, or pending marker path.
- Show stale marker warning before action buttons.
- Separate cleanup from discard.
- Require exact confirmation phrase.
- Show what marker will be acted on.

### Run History Additions

Add later:

- Work summary link.
- Commit range.
- Tests run.
- Worktree outcome.
- Quota/budget summary.
- Follow-up tasks.

---

## Safety Invariants

The implementation must preserve these invariants:

- The web server never silently changes repo scope after startup.
- Default web starts create a fresh run dir.
- `run_dir` reuse requires explicit operator intent.
- `resume_latest` is explicit, never inherited accidentally.
- Stop/reload/restart do not mutate historical runs when the runner is already stopped.
- Worktree merge never proceeds when source repo validation fails.
- Worktree actions operate on the same pending marker shown by the UI.
- Stale markers can be cleaned through a separate recovery action.
- LAN mode is read-only until authentication exists.
- Raw prompt content is not exposed on LAN.
- Config secret-like values are redacted on LAN.
- `.AgentCLI/**` artifacts are ignored by git and never auto-staged.
- Retention never deletes pending review or cleanup-failed artifacts.
- Confirmation phrases are UX safeguards, not authentication.
- UI-side validation is never trusted without server-side validation.

---

## Implementation Phases

### Phase 0: Scope Lock

Deliverables:

- This v2 design document.
- One-repo-one-web statement in docs.
- Explicit rejection of current-phase multi-repo dashboard scope.
- Safety-first MVP definition.

Exit criteria:

- Project direction is clear.
- No feature work is blocked by product ambiguity.

---

### Phase 1: Critical Safety Fixes

Deliverables:

- Start Intent Guard.
- Stopped reload/restart safety.
- Web `run_dir` containment.
- Web `config_path` containment.
- LAN prompt raw-read block.
- Worktree displayed marker/action marker consistency.
- Stale marker cleanup flow.

Exit criteria:

- A local operator cannot accidentally start against a stale run dir.
- A stopped runner cannot mutate historical runs through reload/restart.
- Web-provided paths cannot escape allowed roots.
- LAN read-only mode does not expose raw prompts.
- Worktree action target matches what the UI displays.

---

### Phase 2: Operator Confidence

Deliverables:

- Instance identity header.
- Repo-level instance lock.
- Worktree Review metadata and diff improvements.
- Local start presets with argv preview.

Exit criteria:

- Operator always knows which repo, branch, run dir, and mode they are controlling.
- Duplicate local-operator web instances are guarded.
- Worktree review is defensive enough for daily use.

---

### Phase 3: Daily Work Output

Deliverables:

- Personal Runbook panel.
- `WORK_SUMMARY.md`.
- `WEB_ACTION_AUDIT.jsonl`.

Exit criteria:

- Operator can copy a clear daily work summary.
- Web actions have local traceability.
- Common commands are available from the UI without secrets.

---

### Phase 4: Monitoring And Retention

Deliverables:

- Long-running task health.
- No-output warning.
- Lightweight live endpoint.
- Large log tail optimization.
- Retention dry-run and prune.
- Disk usage summary.

Exit criteria:

- Long-running local automation can be monitored safely.
- Large logs do not degrade dashboard polling.
- Artifact growth can be managed without deleting pending work.

---

### Phase 5: Config, Design Polish, Release Gates

Deliverables:

- Config backup list and restore.
- Runtime-aligned model/role labels.
- All-field validation response.
- Direction A visual fidelity pass.
- Desktop and mobile screenshot validation.
- Keyboard and accessibility checks.
- Playwright long-run/reconnect/stale/stop sequence coverage.

Exit criteria:

- UI is reliable enough for repeated daily use.
- Config editing is safe.
- Release regressions are covered.

---

## Proposed GOALS Additions

Do not add these to `.doc/GOALS.md` while an AgentCLI run is actively using the current GOALS file.  
Add them after the active run is stopped or after confirming that the source repo can safely become dirty.

Suggested new section:

```md
### P0-S. Personal Work Automation Safety Readiness

- [ ] Web documentation defines the one-repo-one-web model and explicitly rejects multi-repo dashboard scope for the current phase.
- [ ] Default web start never reuses a previous run_dir unless the operator explicitly selects resume_latest or an explicit run_dir.
- [ ] Reload/restart while stopped behaves as start-only or no-op and does not write STOP or stop-progress artifacts into historical runs.
- [ ] Web runner start rejects run_dir values outside the active repo's approved AgentCLI run root.
- [ ] Web runner start rejects config_path values outside the approved AgentCLI config roots.
- [ ] LAN mode blocks raw prompt reads and keeps mutating actions disabled until authentication exists.
- [ ] Worktree Review actions operate on the same pending marker shown by the UI.
- [ ] Run-local pending marker wins over stale central marker.
- [ ] Stale central markers can be cleaned through a separate explicit cleanup action.
- [ ] Worktree Review shows source repo, branch, base ref, head ref, patch path, pending marker path, merge mode, and cleanup/recovery state.
- [ ] A visible identity header shows active repo, branch, run id, run dir, port, mode, runner-control status, and redaction status on every primary route.
- [ ] A repo-level web instance lock prevents accidental duplicate local-operator control of the same repo.
```

Suggested later section:

```md
### P1-W. Personal Work Automation Workflow Readiness

- [ ] A personal Runbook panel renders venv activation, shell start, web serve, status, stop, and merge commands for the active repo.
- [ ] Each run writes a concise WORK_SUMMARY.md suitable for daily work logs without exposing raw secrets or long transcripts.
- [ ] Web action audit artifacts record local start/stop/restart/config/prompt/goals/worktree actions with timestamps and results.
- [ ] Long-running task health shows elapsed time, last log event, last backend event, no-output duration, quota/budget state, and next safe operator action.
- [ ] Local retention settings and dry-run prune reports manage run_dirs and logs without deleting pending worktree review state.
```

---

## Validation Plan

### Unit/API Tests

Required existing or related suites:

```powershell
python -B -m unittest tests.test_web_console_safety
python -B -m unittest tests.test_web_console_readonly
python -B -m unittest tests.test_web_console_worktree
python -B -m unittest tests.test_worktree_manual_merge
python -B -m unittest tests.test_worktree_isolation
python -B -m unittest tests.test_stop_progress
```

New tests needed for Phase 1:

- Web start without inherited `run_dir`.
- Explicit resume latest.
- Explicit run dir.
- Reload while stopped does not touch old run.
- Restart while stopped does not touch old run.
- LAN prompt raw read blocked.
- `run_dir` path escape rejected.
- `config_path` path escape rejected.
- Central stale marker does not shadow run-local pending marker.
- Stale marker cleanup action.
- Missing patch recovery policy.
- Malformed marker response.

New tests needed for Phase 2:

- Duplicate web instance lock.
- Stale lock force takeover.
- Identity header payload.
- Worktree marker selected equals action marker.
- Start preset argv preview.
- Dangerous preset combination rejected.

New tests needed for later phases:

- Work summary generation.
- Web action audit append.
- Retention dry-run.
- Large log tail.
- Long-running no-output warning.

---

### Browser Tests

Required Playwright flows:

- Dashboard identity strip.
- Start preset preview.
- Runner start validation errors.
- Stop-in-progress and timeout.
- Reconnect and stale snapshot.
- Logs pause/resume/filter/large tail.
- Worktree Review diff and merge metadata.
- Stale marker cleanup.
- Runbook commands.
- Retention dry-run.
- Desktop and mobile screenshots in EN/KO.

Recommendation:

- Browser tests should become a release gate only after Phase 2.
- Before Phase 2, prioritize unit/API tests.

---

### Manual Validation

Required local manual validation:

1. Start shell with venv.
2. Start read-only web.
3. Start local-operator web.
4. Run `/start --autopilot --continuous --iterations 3`.
5. Observe PM bootstrap and task progress.
6. Stop with `/stop --wait`.
7. Review worktree pending state.
8. Merge or discard.
9. Confirm no stale marker confusion.
10. Confirm no historical run mutation.
11. Confirm LAN mode blocks raw prompt reads.
12. Confirm work summary after Phase 3.
13. Confirm retention dry-run after Phase 4.

---

## Open Decisions

These should be answered before implementation begins:

1. Should repo-level instance lock fail hard by default in local-operator mode?
2. Should read-only duplicate instances be allowed with warning?
3. Should missing worktree directory block merge completely, or allow patch-only recovery?
4. Should prompt raw read be blocked on all non-loopback binds immediately?
5. Should same-remote local clones share history by slug, or remain isolated by local path?
6. Should local budget guardrails include wall time, token count, and spend, not only attempt counters?
7. Should Playwright be required for release gates after Phase 2?
8. Should `WEB_ACTION_AUDIT.jsonl` live in `run_dir` only, or also in `.AgentCLI/web/` for actions not tied to a run?

Recommended answers for now:

| Decision | Recommendation |
|---|---|
| Local-operator duplicate lock | Fail by default |
| Read-only duplicate lock | Warn by default |
| Missing worktree | Block merge unless explicit patch-only recovery is implemented |
| LAN prompt raw read | Block immediately |
| Same-remote clones | Isolate by local path for now |
| Budget guardrails | Add wall time first; token/spend later |
| Playwright release gate | Required after Phase 2 |
| Web audit location | Start in run_dir; add web-level audit later if needed |

---

## Recommended Next Implementation Order

1. Fix web start intent and remove inherited `run_dir` from default start.
2. Fix stopped reload/restart behavior.
3. Enforce web `run_dir` containment.
4. Enforce web `config_path` containment.
5. Block LAN prompt raw reads.
6. Make Worktree Review action target match the displayed pending marker.
7. Add stale marker cleanup path.
8. Add instance identity header.
9. Add repo-level instance lock.
10. Add start presets with argv preview.
11. Add personal Runbook panel.
12. Add `WORK_SUMMARY.md`.
13. Add `WEB_ACTION_AUDIT.jsonl`.
14. Add long-running health signals.
15. Add retention dry-run.

This order improves safety before adding convenience.

---

## Final Product Direction

AgentCLI Web should become a **local AI workbench for one active repository**, not a team platform yet.

The ideal near-term shape is:

```text
Human operator
  -> starts AgentCLI locally
  -> watches Web cockpit
  -> reviews logs, GOALS, config, prompts, and worktree diff
  -> applies or discards isolated output
  -> copies daily work summary
```

The system should optimize for:

- Fewer mistakes.
- Clear operator intent.
- Recoverable failures.
- Safe path boundaries.
- Review before merge.
- Practical daily use on a company PC.

The project should not optimize for:

- Multi-repo dashboard complexity.
- Remote unauthenticated control.
- Automatic merge.
- Team governance.
- Cloud control plane.

Those can come later, after the single-repo local operator experience is reliable.
