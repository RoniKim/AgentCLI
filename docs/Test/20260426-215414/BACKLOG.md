# BACKLOG

- [x] T42 Add a locale state model with `en` and `ko` options and persisted browser preference — shell-wide i18n slice
- [ ] T43 Playwright smoke covers Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, Worktree Review, and mobile width — checked-in smoke harness and validation docs  (depends_on: ['T42'])
- [ ] T44 Worktree merge preflight blocks unsafe apply when the source repo is dirty, source `HEAD` differs from `base_ref`, the patch hash does not match metadata, or `git apply --check` fails.
- [ ] T45 Worktree cleanup on Windows retries locked-path removal, records exact permission failures, and keeps cleanup-failed states visible until reconciled.  (depends_on: ['T44'])
- [ ] T46 Shell, web, and remote-controller starts create a new `run_dir` by default; latest run reuse requires explicit resume or `--run-dir` intent.
- [ ] T47 `STATE.json` done/failed counts are scoped to the current backlog generation so stale task ids from previous backlogs cannot inflate progress or history.
- [ ] T48 Goals parsing treats missing or malformed required priority sections as invalid/incomplete instead of silently completing P0/P1 modes.  (depends_on: ['T47'])

