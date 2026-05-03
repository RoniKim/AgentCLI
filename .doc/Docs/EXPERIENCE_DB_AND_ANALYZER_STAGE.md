# Experience DB And Analyzer Stage Design

> Status: design draft. Do not implement during an active long-running Runner unless the source repo merge plan is clear.
> Inspiration: ASI-Evolve's learn -> design -> experiment -> analyze loop, adapted for AgentCLI's personal unattended development workflow.

## 1. Purpose

AgentCLI should not only execute tasks. It should get better at choosing, sizing, validating, and preserving work across runs.

The target is not model weight training. The target is operational learning:

```text
GOALS / Docs / History / Failure Records
 -> PM backlog generation
 -> Dev implementation
 -> QA / validation
 -> Analyzer distills lessons
 -> Experience DB stores reusable signals
 -> next PM run uses those signals
```

This turns repeated overnight runs into a feedback system instead of isolated attempts.

## 2. ASI-Evolve Mapping

ASI-Evolve has three useful concepts for AgentCLI:

| ASI-Evolve concept | AgentCLI equivalent |
| --- | --- |
| Researcher | PM stage |
| Engineer | Dev stage |
| Analyzer | new Analyzer stage |
| Cognition Store | `.doc/Docs`, docs digest, stable runbook, design notes |
| Experiment Database | task history, validation records, PR queue, new experience DB |
| Candidate program | task branch / local PR packet |
| Evaluation script | build/test/fast regression/Playwright/policy gates |
| Sampling strategy | PM task selection and sizing policy |

ASI-Evolve is built for optimization loops with score-based experiments. AgentCLI is built for software development where feedback is more mixed: tests, diffs, screenshots, merge preflight, user approval, and failure classifications.

## 3. Problems To Solve

Current AgentCLI records many artifacts, but the learning signal is fragmented:

- `STATE.json` tells which tasks are done/failed but not why a pattern keeps recurring.
- `metrics.jsonl` is detailed but too raw for PM prompts.
- `task_history` records results but does not yet act as a strong decision memory.
- Validation failures can be environment, stale tests, real regressions, no tests, or oversized task scope.
- The PM can still generate work that is technically valid but operationally expensive.
- The user has to remember which patterns caused previous overnight failures.

The Analyzer stage should compress those artifacts into durable, reusable lessons.

## 4. Non-Goals

- Do not train or fine-tune an LLM.
- Do not automatically trust model-generated conclusions.
- Do not auto-merge based on learned confidence alone.
- Do not send private code, logs, prompts, or paths to external services beyond the already configured backend.
- Do not replace deterministic gates with heuristic scoring.

## 5. Target Loop

```text
Cycle N
  PM creates backlog from GOALS + docs + experience summary
  Dev implements task branch
  QA validates and writes notes
  Validation gates produce structured records
  Local PR queue preserves work
  Analyzer reads run artifacts
  Analyzer writes experience records

Cycle N+1
  PM receives a compact experience summary
  Backlog generation avoids known bad task shapes
  Validation selection uses historical file/test relationships
  User sees clearer review and merge guidance
```

## 6. Artifact Layout

Preferred storage:

```text
.AgentCLI/
  experience/
    experience.db
    latest_summary.md
    latest_summary.json
    patterns.jsonl
    schema_version
```

Run-local Analyzer output:

```text
.AgentCLI/agent_runs/<run_id>/
  ANALYZER_REPORT.md
  ANALYZER_SUMMARY.json
  EXPERIENCE_UPDATES.jsonl
```

The SQLite database is the source of truth. Markdown and JSON summaries are cache/readability artifacts.

## 7. Experience DB Schema

Minimum tables:

### `runs`

| column | meaning |
| --- | --- |
| `run_id` | AgentCLI run id |
| `started_at` | run start timestamp |
| `ended_at` | run end timestamp if known |
| `backend` | codex / claude |
| `source_head` | source repo head at run start |
| `stop_reason` | final stop reason |
| `summary` | short analyzer summary |

### `task_experiences`

| column | meaning |
| --- | --- |
| `task_id` | task id within run |
| `run_id` | owning run |
| `title` | task title |
| `goal_refs` | JSON list |
| `files` | JSON list |
| `status` | completed / review_required / blocked_env / etc. |
| `reason` | raw stop/failure reason |
| `task_status` | classified status |
| `attempts` | attempt count |
| `validation_status` | passed / failed / skipped / no_tests_found |
| `branch` | task or PR branch |
| `pr_id` | local PR packet id if any |
| `lesson` | compact reusable lesson |

### `validation_experiences`

| column | meaning |
| --- | --- |
| `id` | primary key |
| `run_id` | run id |
| `task_id` | task id |
| `gate` | build / test / fast_regression / playwright / policy |
| `cmd_hash` | normalized command hash |
| `rc` | return code |
| `status` | passed / failed / skipped / timeout / stopped |
| `classification` | regression_failed / blocked_env / test_contract_changed / no_tests_found |
| `summary` | normalized failure summary |
| `artifact_path` | log path |

### `file_patterns`

| column | meaning |
| --- | --- |
| `path` | repo-relative path |
| `touch_count` | times touched by tasks |
| `success_count` | successful task count |
| `failure_count` | failed task count |
| `common_gates` | JSON list of useful validation gates |
| `risk_notes` | compact notes |

### `lessons`

| column | meaning |
| --- | --- |
| `id` | primary key |
| `kind` | task_sizing / validation / merge / env / ui / docs |
| `severity` | low / medium / high |
| `confidence` | 0.0-1.0 |
| `trigger` | when PM/Dev should apply it |
| `lesson` | concise reusable instruction |
| `evidence` | JSON pointers to runs/tasks/logs |
| `created_at` | timestamp |
| `last_seen_at` | timestamp |
| `seen_count` | recurrence count |

## 8. Analyzer Stage Responsibilities

The Analyzer runs after task completion, task failure, cycle end, and run shutdown.

It should:

- Read `STATE.json`, `BACKLOG.json`, `metrics.jsonl`, validation artifacts, PR queue packets, and shutdown reports.
- Classify recurring failure modes.
- Detect oversized tasks and recommend smaller GOALS grouping.
- Detect missing or weak tests.
- Link changed files to gates that caught failures.
- Summarize which failures are useful work requiring review versus work to discard.
- Write compact lessons for the next PM prompt.
- Never mutate source code.
- Never mark GOALS complete by itself.
- Never approve merge by itself.

## 9. Analyzer Output Contract

`ANALYZER_SUMMARY.json`:

```json
{
  "schema_version": 1,
  "run_id": "20260429-150347",
  "summary": "Short human-readable summary.",
  "task_lessons": [
    {
      "task_id": "T2",
      "kind": "task_sizing",
      "severity": "medium",
      "confidence": 0.82,
      "lesson": "Split keyboard navigation and accessibility into separate tasks when Playwright is required.",
      "evidence": ["tasks/c000_s001_T2/attempt_00/test.txt"]
    }
  ],
  "validation_lessons": [],
  "pm_hints": [
    "For web_console/app.js + web_console/styles.css changes, include tests/web_console_playwright_smoke.py only at PR validation time unless the task directly targets layout or accessibility."
  ],
  "merge_hints": [],
  "operator_actions": []
}
```

`latest_summary.md` should be short enough to inject into a PM prompt.

## 10. PM Integration

PM prompt injection should include a bounded block:

```text
<pm_experience_summary>
Recent durable lessons:
- Split high-risk UI work by route or workflow; avoid combining keyboard, accessibility, and screenshot gates.
- If a task has no tests, record validation_pending/no_tests_found and preserve for review.
- web_console/app.js changes usually require tests.test_web_console_static and Playwright smoke before merge.
</pm_experience_summary>
```

Rules:

- Limit injected lessons to the most relevant 10-20 items.
- Prefer lessons that match current unmet GOALS, touched files, or failed tasks.
- Include confidence and evidence count only if useful.
- Expire stale lessons or reduce confidence when repeated evidence stops appearing.

## 10A. Token-Bounded Experience Injection Contract

Experience data must never become raw log reinjection. The database may store many records, but the PM prompt receives only a small ranked summary.

Hard limits:

```json
{
  "experience_prompt_max_items": 12,
  "experience_prompt_max_chars": 4000,
  "experience_lesson_max_chars": 240,
  "experience_evidence_max_items": 3,
  "experience_raw_log_excerpt_chars": 0
}
```

Rules:

- Do not inject `metrics.jsonl`, `run.log`, `error.log`, `test.txt`, backend transcripts, full diffs, raw prompt text, or raw model output.
- Store raw artifacts by path only, preferably repo-relative or run-relative paths.
- Inject only distilled lessons with short evidence pointers.
- If the rendered block exceeds `experience_prompt_max_chars`, drop the lowest-ranked lessons until it fits.
- If a single lesson exceeds `experience_lesson_max_chars`, summarize it again or exclude it.
- The block is advisory. It must not mark GOALS complete, approve merges, override user instructions, or override deterministic validation gates.

Rendered prompt shape:

```text
<pm_experience_summary version="1" items="3" max_items="12" max_chars="4000" authority="advisory">
- [task_sizing high conf=0.86 evidence=3] Split keyboard navigation and accessibility into separate tasks when Playwright is required.
- [validation medium conf=0.72 evidence=2] web_console/app.js changes should run static web console tests and Playwright smoke before merge validation.
- [env high conf=0.91 evidence=1] Windows nested worktree tests must use short temp paths to avoid GIT_DIR too big.
</pm_experience_summary>
```

This block should be appended by the same marker-based essential-context mechanism used for GOALS, done tasks, failed tasks, and output contracts, so custom PM prompts still receive it without requiring a `{pm_experience_summary}` template variable.

Current repo contract for the deterministic Analyzer path:

- Run-local output writes `ANALYZER_SUMMARY.json` plus `EXPERIENCE_UPDATES.jsonl`.
- Durable lesson storage writes repo-local `.AgentCLI/experience/experience.db`.
- Each emitted lesson record must include `kind`, `normalized_trigger`, `goal_refs`, `file_globs`, `gate`, `task_status`, evidence pointers, confidence, created/updated timestamps, and `last_applied` metadata.
- Dedupe uses `kind + normalized_trigger`; equivalent triggers should normalize to the same stable key.
- Store only sanitized lesson text plus artifact pointers. Never persist raw logs, raw diffs, raw prompts, or backend transcripts.

## 10B. Lesson Relevance Ranking

Lesson selection is separate from task sampling. The Analyzer may store many lessons, but PM injection uses a relevance score.

```text
lesson_relevance_score =
  direct_goal_match * 5
  + file_path_match * 4
  + validation_gate_match * 3
  + recent_failure_match * 3
  + task_status_match * 2
  + user_decision_match * 4
  + severity_weight
  + confidence * 2
  + log1p(seen_count)
  - age_decay
  - repeated_nonuse_penalty
  - operator_suppressed_penalty
```

Recommended matching inputs:

- current unmet GOALS refs and GOALS text
- proposed PM task titles/prompts if available
- failed task titles and reasons
- changed files from the latest cycle
- local PR packet changed files
- validation gates touched by the task type
- task status classifications such as `blocked_env`, `review_required`, `test_contract_changed`, `regression_failed`
- validation status classifications such as `validation_pending`, `tests_skipped`, `no_tests_found`, `validation_failed`

Required `lessons` metadata:

| field | meaning |
| --- | --- |
| `applies_to_goal_refs` | GOALS ids or empty list |
| `applies_to_file_globs` | repo-relative glob patterns |
| `applies_to_gates` | build/test/playwright/policy/etc. |
| `applies_to_statuses` | task status classifications |
| `applies_to_validation_statuses` | validation status classifications |
| `negative_patterns` | conditions where the lesson should not apply |
| `last_applied_at` | last PM injection timestamp |
| `last_helpful_at` | last time the lesson correlated with better outcome |
| `suppressed_until` | operator or decay-based suppression timestamp |

Selection algorithm:

1. Query candidate lessons by GOALS refs, file globs, failed task signatures, validation gates, and pending PR queue items.
2. Score candidates with `lesson_relevance_score`.
3. Deduplicate near-identical lessons by normalized lesson text and trigger fingerprint.
4. Prefer high-confidence lessons with recent evidence, but reserve 1-2 slots for new recurring failures.
5. Render at most `experience_prompt_max_items`.
6. Enforce `experience_prompt_max_chars` after rendering.
7. Write omitted counts to `ANALYZER_SUMMARY.json` so the operator can see when useful lessons were excluded by budget.

## 10C. Token-Bounded Lesson Confidence Updates

A lesson must never be promoted only because it exists. It needs recurring support, low contradiction, and recent relevance.

### Lesson Identity

Lessons are deduplicated by a stable key:

```text
lesson_key =
  kind
  + normalized_trigger
  + file_pattern
  + goal_refs
  + gate
  + task_status
```

Examples:

- `task_sizing:web_ui_keyboard_accessibility:playwright`
- `validation:web_console/app.js:playwright_smoke`
- `env:playwright_browser_missing`
- `merge:worktree_cleanup_failed:windows`

### Evidence Weights

Recommended default support weights:

| event | effect |
| --- | --- |
| `regression_failed` with strong build/test evidence | `+0.18 support` |
| `test_contract_changed` | `+0.14 support` for contract/update lesson |
| `review_required` | `+0.10 support` |
| `blocked_env` | `+0.12 support` for env/setup lesson |
| same failure repeats with same trigger | `+0.08 recurrence support` |
| validation passes after applying recommended lesson | `+0.10 support` |
| user approves/merges PR | `+0.25 support` for positive task-shape/validation lesson |
| user discards PR | `+0.35 support` for avoid/split/discard lesson |
| same trigger later passes without issue | `+0.12 contradiction` against avoid/risk lesson |
| user merges despite failed gate as expected behavior | `+0.20 support` for `test_contract_changed`, `+0.15 contradiction` against `regression_failed` |

### Confidence Formula

Store support and contradiction separately:

```text
effective_support = support_weight * recency_factor
effective_contradiction = contradiction_weight * recency_factor

confidence =
  clamp(
    0.05,
    0.95,
    (1.0 + effective_support) /
    (2.0 + effective_support + effective_contradiction)
  )
```

Do not delete low-confidence lessons immediately. Hide them from PM injection when:

```text
confidence < experience_lesson_min_confidence
or evidence_count < 2 and no user decision exists
```

### Stale Evidence Decay

Time decay lowers ranking before it lowers truth.

```text
recency_factor = 0.5 ** (age_days / half_life_days)
```

Suggested half-life:

| lesson kind | half-life |
| --- | --- |
| `env` | 14 days |
| `validation` | 30 days |
| `task_sizing` | 45 days |
| `merge` | 60 days |
| `user_decision` | 90 days |

User merge/discard evidence should decay slowly because it reflects operator preference.

### Pass And Merge Semantics

A passed validation can mean two different things:

- If the lesson recommended a gate and that gate passed, strengthen the gate-selection lesson.
- If the lesson warned against a task shape and the same shape repeatedly passes, add contradiction to the risk lesson.

A user merge means the work was acceptable. A user discard means the work shape, implementation, or validation result was unacceptable. Discard reasons should be captured as fixed choices plus optional text.

## 10D. Privacy, Redaction, And Prompt-Injection Safety

Experience summaries are prompt inputs, so every stored lesson and rendered PM block must treat logs, test output, commit messages, branch names, and user free text as untrusted data.

Rules:

- Do not store raw prompt content, raw backend transcripts, raw diffs, raw stack traces, or long test output in the Experience DB by default.
- Store artifact pointers, hashes, normalized summaries, and fixed-choice categories instead.
- Prefer repo-relative paths. If absolute paths are needed for local convenience, keep them out of PM injection.
- Run a redaction pass before writing global summaries or rendering `pm_experience_summary`.
- Strip command-like text, instruction-like text such as "ignore previous instructions", secrets, tokens, and long raw excerpts from lessons.
- Keep Experience DB redaction separate from Web/LAN redaction. Web redaction does not automatically make PM injection safe.
- Default `experience_redact_paths` should be `true`.

## 10E. Analyzer Authority Matrix

Analyzer output is advisory and evidence-backed. It can influence PM task selection, but it must never become an unchecked authority.

| Analyzer may | Analyzer must not |
| --- | --- |
| recommend task sizing | edit source files |
| recommend validation gates | mark GOALS complete |
| recommend retry avoidance | approve or perform merge |
| mark lessons candidate/active/retired | override user prompts |
| summarize repeated failures | override deterministic validation gates |
| suggest PR review priorities | convert skipped/no-test state to success |

PM prompt injection should label experience lessons as advisory hints. If a PM ignores a high-confidence lesson and the same failure repeats, record that as evidence for ranking, not as a hard policy violation.

## 10F. Analyzer Failure Isolation

Analyzer failure must not make the Runner fail.

Rules:

- Deterministic Analyzer runs first; model-based Analyzer is optional and disabled by default.
- Analyzer has a bounded timeout.
- Analyzer DB write failure produces a warning artifact and falls back to run-local JSONL.
- SQLite locks, schema migration errors, or redaction failures must not block shutdown.
- During `/stop --wait`, Analyzer work is best-effort and should prefer writing a minimal warning over delaying runner finalization.
- Analyzer subprocesses, DB handles, file handles, and loggers must be closed explicitly.

## 11. Local PR Queue Integration

Each local PR packet is an experiment node:

```text
motivation = task prompt + GOALS trace
candidate = branch commits + diff
evaluation = validation artifacts + QA notes + user decision
analysis = Analyzer lesson
```

The PR queue should feed the Experience DB after:

- PR packet creation.
- Validation pass/fail.
- User merge.
- User discard.
- Rebase or conflict recovery.

User decisions are high-value signals. A manually discarded PR should produce a lesson if the user provides or selects a reason.

## 12. Sampling And Prioritization

AgentCLI does not need full evolutionary search immediately. A conservative scoring model is enough:

```text
task_score =
  goal_priority
  + stale_goal_bonus
  + historical_success_bonus
  - repeated_failure_penalty
  - oversized_scope_penalty
  - missing_validation_penalty
```

Possible sampling modes:

- `greedy`: choose highest score.
- `risk_balanced`: mix easy wins and hard blockers.
- `retry_limited`: avoid repeating the same failed title/file pattern too soon.
- `explore`: occasionally try a low-confidence task to gather evidence.

Default should be `risk_balanced`.

## 12A. Task Status And Validation Status Mapping

`task_status` and `validation_status` are different signals.

| field | purpose | examples |
| --- | --- | --- |
| `task_status` | retry/review/merge disposition | `completed`, `review_required`, `blocked_env`, `test_contract_changed`, `regression_failed` |
| `validation_status` | what happened to a concrete gate | `validation_pending`, `tests_skipped`, `no_tests_found`, `validation_passed`, `validation_failed`, `timeout`, `stopped` |

Rules:

- `validation_pending`, `tests_skipped`, and `no_tests_found` are not success.
- They should map to `review_required` or a preserved local PR state unless the user explicitly approves a build-only merge.
- `blocked_env` should preserve work and create an environment/setup lesson.
- `test_contract_changed` should preserve work and create a contract-update lesson.
- `regression_failed` should create a regression lesson, but user merge/discard can later add support or contradiction.

## 13. Web Console

Add an Experience view or panel:

- Recent lessons.
- Repeated failure patterns.
- File risk map.
- Validation coverage gaps.
- Suggested next actions.
- "Why did PM choose this task?" trace.
- "Why is this PR blocked from merge?" trace.

The page should be read-only at first.

## 14. Telegram

Minimal commands:

```text
/experience
/experience failures
/experience pr <id>
```

Short responses only:

- latest run summary
- top 3 blockers
- queued PRs needing validation or approval

## 15. Safety And Trust

- Analyzer output is advisory.
- Human approval remains required for merge.
- Validation gates remain deterministic.
- Lessons must include evidence pointers.
- Sensitive raw logs should not be copied into global summaries.
- LAN mode should redact prompt/log content according to existing policy.

## 16. Configuration

Proposed config keys:

```json
{
  "experience_db_enabled": true,
  "analyzer_stage_enabled": true,
  "experience_prompt_max_items": 12,
  "experience_prompt_max_chars": 4000,
  "experience_lesson_max_chars": 240,
  "experience_evidence_max_items": 3,
  "experience_raw_log_excerpt_chars": 0,
  "experience_lesson_min_confidence": 0.55,
  "experience_retention_days": 90,
  "experience_redact_paths": true,
  "pm_use_experience_summary": true
}
```

## 17. Implementation Plan

### Phase 1: Read-only Experience Capture

- Create `agent_runner/experience.py`.
- Create SQLite schema and migrations.
- Ingest run/task/validation records after cycle end.
- Write `ANALYZER_SUMMARY.json` from deterministic rules only.
- No model call required.

### Phase 2: Analyzer Stage

- Add optional `Analyzer` stage after QA or shutdown.
- Produce `ANALYZER_REPORT.md`.
- Record durable lessons with evidence.
- Add tests for classification and retention.

### Phase 3: PM Prompt Injection

- Inject bounded `pm_experience_summary`.
- Match lessons to current GOALS and failed tasks.
- Add tests proving custom PM prompts still receive the injected experience block.

### Phase 4: PR Queue Feedback

- Link local PR packets to experience nodes.
- Record user merge/discard decisions.
- Use validation outcomes to recommend future gates.

### Phase 5: Web And Telegram

- Add read-only Experience panel.
- Add Telegram summary commands.
- Add retention and export tools.

## 18. Proposed GOALS Items

These should be added after the active long-running Runner finishes, to avoid source HEAD drift during worktree merge.

```md
### P0-U. Experience DB And Analyzer Stage

- [ ] AgentCLI writes a durable Experience DB that links runs, tasks, GOALS refs, changed files, validation records, branches, and local PR packets.
- [ ] Analyzer artifacts summarize successful work, failed work, oversized tasks, blocked environments, missing tests, and merge risks with evidence pointers.
- [ ] PM receives a bounded experience summary even when a custom PM prompt is configured.
- [ ] Experience lessons can recommend task sizing, validation selection, and retry avoidance without mutating source code or marking GOALS complete.
- [ ] Local PR queue validation and user merge/discard decisions are recorded as high-value experience signals.
- [ ] Web Console shows recent lessons, repeated failure patterns, validation gaps, and merge blockers.
- [ ] Telegram can summarize latest experience blockers and queued PR validation needs.
- [ ] Experience retention and redaction settings prevent stale or sensitive raw logs from leaking into future prompts.
```

## 19. Validation Plan

- Unit tests for schema migration and inserts.
- Unit tests for deterministic Analyzer rules.
- Tests for custom PM prompt injection.
- Tests for redaction and retention.
- Integration test with a fake run_dir containing:
  - one completed task
  - one `blocked_env`
  - one `test_contract_changed`
  - one `no_tests_found`
  - one local PR packet

## 20. Open Questions

- Should Analyzer run after every task or only after cycle/run end?
- Should lessons be global per repo or per source branch?
- Should user merge/discard reasons be free text, fixed choices, or both?
- Should Experience DB be copied into run archives or only stored under `.AgentCLI/experience`?
