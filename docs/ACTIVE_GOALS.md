# Active Goals

Active goals are repo-local runtime operator intent. They help steer the next run, but they are not a second project authority.

The project contract remains [../.doc/GOALS.md](../.doc/GOALS.md). Active goals can shape planning, task metadata, progress reporting, and budget stops, but they cannot mark GOALS complete, approve a PR merge, bypass validation, bypass worktree policy, or weaken Web/LAN mutation safety.

## Runtime Model

The active-goal artifact lives under:

```text
.AgentCLI/goals/ACTIVE_GOAL.json
```

It stores:

- objective, status, mode, timestamps, source, revision, and stale-write token
- token, time, and cycle budgets
- usage counters updated by Codex and Claude runs
- completion evidence
- progress and terminal reason metadata

The shell, non-interactive CLI, Web Console, Telegram, PM/PL/Dev/QA/reporter/analyzer prompts, task history, validation artifacts, PR packets, run reports, and Experience DB all treat this metadata as subordinate to GOALS.md.

## Modes

| Mode | Use For | Behavior |
|---|---|---|
| `strict` | Narrow known work | Plan only tasks directly necessary for the active objective and already admitted by GOALS.md. |
| `adaptive` | Normal implementation | Prefer implementation and validation tasks, with small discovery steps only when they unblock admitted work. |
| `exploratory` | Bounded investigation | May propose discovery or spike tasks when tied to unchecked GOALS.md items or proposal-only GOALS updates. |

Mode never bypasses GOALS.md admission, validation gates, worktree policy, PR policy, network/LAN safety, or operator confirmation requirements.

## Recommended Presets

| Workflow | Mode | Budgets | Runner Settings | Worktree Merge |
|---|---|---|---|---|
| One-shot work | `strict` or `adaptive` | `--active-goal-cycle-budget 1` to `3` | no loop, normal manual supervision | `manual` |
| Overnight work | `adaptive` | token/time/cycle budget required | `--unattended`, loop enabled, idle exit enabled | `manual` |
| Exploratory improvement | `exploratory` | small cycle or time budget required | loop only when bounded | `manual` |

Readiness checks warn when active-goal mode, `goals_completion_level`, loop/unattended settings, or worktree merge mode conflict. In particular, exploratory unattended runs should have an active-goal budget, and active-goal runs should keep `gitops.worktree_merge_mode=manual`.

## Commands

```powershell
# Inspect
python agent_cli.py --repo . --active-goal-status
python agent_cli.py --repo . --active-goal-templates
python agent_cli.py --repo . --active-goal-presets
python agent_cli.py --repo . --active-goal-recommend
python agent_cli.py --repo . --active-goal-timeline
python agent_cli.py --repo . --active-goal-analytics
python agent_cli.py --repo . --active-goal-export

# Create
python agent_cli.py --repo . --active-goal-objective "Fix flaky web status tests" --active-goal-template bug_fix --active-goal-preset one_shot

# Shell
/goal status
/goal templates
/goal presets
/goal recommend
/goal timeline
/goal analytics
/goal export
/goal create Fix flaky web status tests --template bug_fix --preset one_shot
/goal update Add Web dashboard progress --mode strict --etag <etag>
/goal checkpoint set "Reproduce" "Patch" "Validate"
/goal checkpoint complete cp-01-reproduce "task_outcome: failing path isolated"
/goal complete "operator_confirmation: tests passed and final report reviewed"
/goal cancel "superseded by higher priority issue"
/goal clear

# Proposal-only GOALS bridge
/goal propose P1
```

`/goal propose` writes a proposal artifact only. It does not mutate `.doc/GOALS.md`; the operator must explicitly confirm any GOALS change.

## Goal Intelligence

Active goals expose seven workflow templates:

- `bug_fix`
- `feature_build`
- `refactor`
- `test_hardening`
- `documentation`
- `release_prep`
- `exploratory_improvement`

Autonomy presets are operator-selectable bundles:

- `one_shot`: bounded supervised work, no loop, manual worktree merge
- `overnight`: bounded unattended loop, idle exit, strict validation posture, manual worktree merge
- `exploratory`: small discovery budget, proposal-only posture, manual worktree merge

The recommendation view is proposal-only. It ranks candidate next active goals from unchecked GOALS items, Experience DB lessons, PR queue blockers, failing validation artifacts, and stale TODO priority signals. Selecting a recommendation still requires an operator create/update action.

Long goals can carry checkpoints. Each checkpoint has status, evidence, and a resume point. Completing a checkpoint advances the next pending checkpoint, but it does not complete the active goal by itself.

The timeline view combines objective and budget events, task-history decomposition, validation evidence, PR packets, and final disposition. It is review evidence only.

The export/import flow writes a redacted `ACTIVE_GOAL_EXPORT.json` payload. It excludes raw prompts and raw logs, redacts source actor details and secret-like tokens, and keeps the imported goal subordinate to GOALS.md.

Analytics report success rate, median cycles to completion, validation failure reasons, budget exhaustion count, and manual-intervention count. Analytics are retrospective signals only.

## Completion Evidence

Completing an active goal requires at least one evidence source:

- `task_outcome`: task state or task-history evidence
- `validation_artifact`: validation JSON/log evidence
- `operator_confirmation`: explicit operator statement

Active-goal completion is status/report evidence only. It never means:

- `.doc/GOALS.md` project completion
- PR validation passed
- PR merge approval
- worktree merge readiness
- permission to bypass Web/LAN controls

## Examples

### One-Shot Bug Fix

```powershell
python agent_cli.py --repo . --active-goal-objective "Fix failing /api/status active-goal progress contract" --active-goal-mode strict --active-goal-cycle-budget 2
python agent_cli.py --repo . --run-now
```

Use this when the files and expected tests are known. Complete the goal only after the relevant task outcome or validation artifact exists.

### Overnight Run

```powershell
python agent_cli.py --repo . --active-goal-objective "Harden active-goal Web and Telegram surfaces" --active-goal-mode adaptive --active-goal-cycle-budget 6
python agent_cli.py --repo . --unattended --loop --loop-idle-exit-after 1800 --run-now
```

Keep `goals_completion_level=all` for self-development runs and keep worktree merge manual. Review run history, validation artifacts, PR queue, and final report before completing the goal.

### Exploratory Improvement

```powershell
python agent_cli.py --repo . --active-goal-objective "Explore next active-goal usability improvements" --active-goal-mode exploratory --active-goal-cycle-budget 2
python agent_cli.py --repo . --run-now
```

Exploratory mode is for bounded discovery. It may produce a GOALS proposal, but it must not silently add, downgrade, delete, or complete GOALS items.
