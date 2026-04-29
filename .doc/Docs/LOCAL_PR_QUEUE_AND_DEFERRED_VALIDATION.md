# Local PR Queue And Deferred Validation Plan

> Status: planned. The backlog sizing guard is implemented first because it directly affects unattended overnight runs.

## Problem

The current runner can treat a task as either mergeable or failed too early in the pipeline. That is too coarse for personal unattended use:

- A task may produce useful code but fail a broad Playwright or integration gate.
- A repository may have no test suite yet, or the selected test command may be unavailable.
- A PM pass can bundle too many GOALS items into one large task, increasing retry cost and failure probability.
- The operator wants to review, validate, and merge after the runner has produced a queue of work.

## Target Flow

```text
PM
 -> Dev
 -> QA
 -> Local PR packet
 -> validation_pending
 -> user validates
 -> user approves
 -> merge
```

The runner should create reviewable work, not silently mutate source `main`.

## Backlog Sizing Guard

This guard is active before the PR queue exists:

- PM may still be custom or project-specific.
- Runtime GOALS gating must therefore enforce task size even when the prompt asks for broad work.
- A single generated task should normally map to one unchecked GOALS item.
- A generated task must not map to more than two unchecked GOALS items.
- If the GOALS gate sees more than two matching GOALS, it emits sibling tasks with narrowed prompts and preserved `goal_trace`.

This keeps overnight runs moving through smaller reviewable units instead of retrying one oversized task for hours.

## Local PR Packet

Each completed task or run should produce a local packet under an AgentCLI-owned queue:

```text
.AgentCLI/pr_queue/<pr_id>.json
```

Minimum fields:

- `id`
- `source_repo`
- `run_id`
- `task_ids`
- `base_ref`
- `head_ref`
- `branch`
- `commits`
- `changed_files`
- `goal_trace`
- `qa_notes`
- `validation_status`
- `validation_artifacts`
- `merge_preflight`
- `created_at`
- `updated_at`

## Status Model

- `dev_completed`: code exists on a task branch or integration branch.
- `qa_reviewed`: QA notes were generated.
- `pr_queued`: local PR packet exists.
- `validation_pending`: tests were intentionally deferred.
- `tests_skipped`: configured policy skipped tests.
- `no_tests_found`: a test command ran but found no tests.
- `validation_running`: validation is active.
- `validation_passed`: validation passed.
- `validation_failed`: validation failed.
- `blocked_env`: dependencies, browser, permission, or toolchain issue.
- `test_contract_changed`: product behavior changed and tests likely need update.
- `review_required`: useful output exists but automatic merge is unsafe.
- `approved`: operator approved merge.
- `merged`: source repo received the change.
- `discarded`: operator discarded the packet.

## Merge Policy

Automatic merge should be conservative:

- Only `validation_passed` plus explicit operator approval can merge.
- `tests_skipped`, `no_tests_found`, `review_required`, `blocked_env`, and `test_contract_changed` must preserve work for review.
- Validation should run in an isolated temporary worktree.
- Merge preflight must check source `HEAD`, conflicts, stale patch/branch metadata, and dirty source files.

## Shell And Web Commands

Planned shell commands:

```text
/prs
/pr <id>
/validate-pr <id>
/validate-pr <id> --full
/merge-pr <id>
/discard-pr <id>
/rebase-pr <id>
```

The Web Console should expose the same queue with:

- PR list and status chips.
- GOALS trace and QA notes.
- Per-file diff.
- Validation logs.
- Merge blocking reasons.
- Confirmed validate, merge, discard, and rebase actions.

## Implementation Order

1. Backlog sizing guard for oversized GOALS bundles.
2. Local PR packet schema and writer.
3. Shell read-only list/detail commands.
4. Isolated validation command.
5. Merge command requiring validation and approval.
6. Web PR Queue view.
7. Telegram PR list/status notifications.
8. Optional GitHub PR export.
