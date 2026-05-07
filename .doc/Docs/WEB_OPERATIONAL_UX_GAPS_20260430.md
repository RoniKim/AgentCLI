# Web Operational UX Gaps - 2026-04-30

> Status: active follow-up.
> Evidence source: read-only Playwright pass against `http://127.0.0.1:8000/#dashboard` while run `20260430-113615` was active.
> Scope: observation and display quality only. Runner control, merge, discard, validate, start, stop, reload, and restart actions were not exercised.
> Current status (2026-05-07): This file preserves the 2026-04-30 UX audit. Several gaps were later promoted into `.doc/GOALS.md` P0-W/P1 and implemented or covered by tests. Treat the body as historical evidence unless a fresh browser pass reproduces the issue.

## Summary

The Web Console renders and updates, but it is not yet a reliable operator cockpit.

The current UI can show the active run, navigate primary routes, render GOALS, show backlog items, and update over time. However, several operator-facing states are ambiguous:

- The dashboard shows a stale snapshot badge even while `/api/status` continues to refresh.
- The current task title is missing on the dashboard even though backlog/task artifacts contain the task.
- Runner liveness text can show `running` and `Runner process: Stopped` at the same time.
- Logs route can show `0 lines` and `loading` while structured event data exists.
- Worktree Review surfaces an old finalized applied worktree as the main review state during a newer active run.
- Mobile navigation has off-screen internal nav overflow even when the document itself has no horizontal scroll.
- PR Queue data exists as local packets, but there is no clear user-facing PR Queue list/detail route yet.

## Observed Evidence

Read-only QA artifact:

```text
.AgentCLI/diagnostics/ui-qa-20260430-120353.json
```

Screenshots captured:

```text
.AgentCLI/diagnostics/ui-qa-20260430-120353-dashboard-desktop.jpg
.AgentCLI/diagnostics/ui-qa-20260430-120353-logs-desktop.jpg
.AgentCLI/diagnostics/ui-qa-20260430-120353-backlog-desktop.jpg
.AgentCLI/diagnostics/ui-qa-20260430-120353-goals-desktop.jpg
.AgentCLI/diagnostics/ui-qa-20260430-120353-worktree-desktop.jpg
.AgentCLI/diagnostics/ui-qa-20260430-120353-mobile.jpg
```

Pass/fail observations:

- Console errors: none.
- Page errors: none.
- Desktop routes checked: Dashboard, Pipeline, Logs, Backlog, Goals, History, Notifications, Worktree, Config, Prompts, Mobile.
- Desktop `1600x900`: no document-level horizontal overflow was detected on checked routes.
- Auto refresh: `/api/status` was called during the wait window and visible elapsed text changed.
- Mobile `390x844`: document-level horizontal overflow was false, but internal `.sidebar__inner` and nav groups extended to about 949px.

## Gap 1 - Stale Snapshot Semantics

Observed UI text:

```text
오래된 스냅샷
마지막 업데이트 ...
```

The page continued polling `/api/status`, and elapsed text changed over time. The stale badge therefore does not clearly mean "data is stale". It may mean one of:

- The run artifact is older than the live controller state.
- The controller says running but process liveness details disagree.
- The snapshot is older than a freshness threshold.
- The browser has not yet received a newer payload.

Target behavior:

- Show stale only when the latest payload age exceeds a configured freshness threshold.
- If liveness sources disagree, show `state mismatch` or `liveness mismatch`, not `stale snapshot`.
- Display the source of the badge: `browser age`, `run artifact age`, `controller mismatch`, or `process mismatch`.

## Gap 2 - Active Task Title

Observed dashboard text:

```text
task title unavailable
```

The current run had backlog and task artifacts for T1, and `/api/logs` showed `task_start task=T1`.

Target behavior:

- Dashboard should show current task id and title from the best available source.
- Source order should be deterministic:
  1. live runner event / active run payload
  2. current task artifact
  3. backlog item by current task id
  4. latest task_start event
- If the title is still unknown, show the missing source reason.

## Gap 3 - Runner Liveness Wording

Observed dashboard can show:

```text
실행 중
작업 백엔드 Alive
Runner process: Stopped
```

This is technically possible if the shell runner process and backend task process are distinct, but the wording is not operator-safe. The user reads it as a contradiction.

Target behavior:

- Use separate labels:
  - Shell runner
  - Task backend
  - Tracked children
  - Artifact writer
- Avoid a global `running` label next to a `Runner process: Stopped` row unless the relationship is explained.
- Topbar, Dashboard, Runner Controls, and Mobile should use the same liveness vocabulary.

## Gap 4 - Logs Route Loading State

Observed Logs route text:

```text
0줄 표시됨
불러오는 중
run.log
```

At the same time, `/api/logs` had structured entries and `/api/logs/tail` had two `run.log` lines.

Target behavior:

- The route should render `/api/logs` structured entries immediately.
- The live tail pane should leave `loading` once the current cursor reaches EOF.
- Sparse `run.log` should not make the whole Logs route look empty.
- Structured events and raw log tail should be visually separated.
- Show "last log line" and "no output for N minutes" from the correct source.

## Gap 5 - Worktree Review Historical State

Observed Worktree Review surfaced:

```text
Patch applied
run: 20260429-133223
```

The active run was:

```text
20260430-113615
```

This makes the page look like the current run has a patch applied even though the artifact is historical.

Target behavior:

- Current run pending worktree state should be primary.
- Historical finalized artifacts should be shown under history or "recent finalized worktree", not as the primary review state.
- If there is no current pending worktree, the main state should say that clearly.

## Gap 6 - Mobile Nav Overflow

Observed mobile numeric check:

```text
viewport: 390x844
document horizontal scroll: false
.sidebar__inner width: about 949px
```

The nav may intentionally scroll horizontally, but there is no clear affordance and automated overflow detection flags it as off-screen content.

Target behavior:

- Keep horizontal nav inside an explicit scroll container.
- Add visual affordance or edge fade when horizontal scroll is intentional.
- Ensure fixed shell regions do not push required controls off-screen.

## Gap 7 - PR Queue Visibility

Local PR queue packets are useful only if the operator can review them without reading JSON files.

Target behavior:

- Add a dedicated PR Queue route or a clear PR Queue panel.
- List queued packets with task id, GOALS refs, branch, base/head refs, changed file count, validation status, and merge preflight status.
- Detail view should show per-file diff, QA notes, validation logs, blockers, and dependency details.
- Validate, merge, discard, and rebase actions must be separated from read-only review and require explicit confirmation.

## Gap 8 - Snapshot Payload Size

Observed `/api/status` was about 6 MB during the QA pass.

This may be acceptable for local-only early development, but it is expensive for polling and can make the UI feel stale or delayed.

Target behavior:

- Keep `/api/status` as the compact route snapshot.
- Move heavy raw GOALS text, full history reports, full diff hunks, and long logs behind route-specific endpoints.
- Route views should fetch heavy data only when that route is active.

## Implementation Order

1. Fix Dashboard stale badge and active task title.
2. Normalize runner liveness wording across topbar, dashboard, runner controls, and mobile.
3. Fix Logs route source separation and loading state.
4. Fix Worktree Review current-run vs historical-finalized state.
5. Fix mobile nav overflow affordance.
6. Add PR Queue read-only list/detail route.
7. Add PR Queue mutating actions only after read-only review is useful.
8. Split heavy `/api/status` payload into compact status plus route-specific payloads.

## Verification

Minimum verification for each UI fix:

- Playwright desktop `1600x900`.
- Playwright mobile `390x844`.
- Console errors must be empty.
- Page errors must be empty.
- No document-level horizontal overflow.
- For mobile horizontal nav, either no off-screen internal nav or an explicitly tested scroll container.
- `/api/status` refresh must update visible elapsed/state text without showing a misleading stale badge.
- No Runner control POST endpoints are used during read-only QA.
