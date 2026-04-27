# Worktree Merge Failure - 2026-04-28

## Cause Analysis

`/merge-worktree` failed because the exported patch did not pass the preflight command:

```powershell
git apply --check --binary --whitespace=nowarn .AgentCLI\agent_runs\20260427-180031\worktree.patch
```

The direct failure was a context mismatch in `tests/test_web_console_readonly.py`:

```text
error: patch failed: tests/test_web_console_readonly.py:1886
error: tests/test_web_console_readonly.py: patch does not apply
```

The pending worktree contained two kinds of work at shutdown:

- committed task-branch history from T43 through T56, ending at `9513c3e`
- uncommitted T57 changes in `agent_runner/web.py`, `tests/test_web_console_static.py`, `web_console/app.js`, and `web_console/styles.css`

The generated `worktree.patch` was a single large cumulative patch from base commit `b0636bda` to the dirty worktree. That made the merge brittle: one stale hunk in one test file caused the whole patch preflight to fail, even though the committed worktree branch was fast-forwardable and most of the patch content was valid.

## Resolution

The merge was completed by splitting the worktree result into its stable parts instead of applying the monolithic patch:

1. Verified the source repo was clean and still at the expected base commit `b0636bda`.
2. Fast-forwarded `main` to the committed worktree branch `task/T57_2026-04-28T06-06-47`, which brought in T43 through T56.
3. Exported only the remaining dirty T57 diff from the worktree:

```powershell
git -c safe.directory=D:/.agentcli_worktrees/999.AgentCLI/20260427-180031 `
  -C D:\.agentcli_worktrees\999.AgentCLI\20260427-180031 `
  diff --binary --output=D:\999.AgentCLI\.AgentCLI\agent_runs\20260427-180031\worktree_dirty_uncommitted.patch `
  -- agent_runner/web.py tests/test_web_console_static.py web_console/app.js web_console/styles.css
```

4. Checked and applied that smaller patch to the source repo.
5. Fixed the merged JavaScript syntax error caused by mixing `??` and `||` without parentheses.
6. Validated the result with:

```powershell
node --check web_console\app.js
python -B -m py_compile agent_runner\web.py tests\web_console_playwright_smoke.py
```

7. Committed and pushed the final state as `e99e05d [T57] Surface stale live-run snapshot state`.
8. Converted pending markers to `WORKTREE_MERGE_APPLIED.json` so AgentCLI no longer prompts for the same merge.

## Prevention

- Prefer merging the committed worktree branch first when `head_ref` is a descendant of the source `HEAD`.
- Export and apply dirty worktree changes as a second, smaller patch.
- Keep `/merge-worktree` from relying only on one monolithic cumulative patch when a fast-forward path exists.
- After a manual merge, write an applied marker and remove the pending marker so the shell does not offer the stale merge again.
