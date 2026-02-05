# AgentCLI Optimization Review

## 🐛 Critical Issues

### 1. Max Turns + No Diff = Premature Failure
**Location:** `cycle.py:1730-1751`

**Problem:**
- When dev agent hits max_turns, it's logged as warning and continues
- Immediately checks for no_diff and FAILS the task
- But if max_turns was hit, agent didn't finish coding → no_diff is expected!

**Evidence from BudgetBook:**
- T1: 38 minutes, 18 turns, 3 continuations
- Result: max_turns exceeded + files_changed_count=0
- Status: Marked as "done" (because allow_no_diff=true)
- **This is a logic bug that wastes compute**

**Fix:**
```python
# After line 1733
if dev_exc and dev_is_max_turns:
    state.setdefault("warnings", []).append(...)
    save_state(state_path, state)
    metrics.event("task_warn", ...)

    # NEW: Set flag to skip no_diff check or auto-retry
    max_turns_no_diff_auto_retry = True

# At line 1739
if stop_on_no_diff and (not changed):
    # NEW: If max_turns caused no_diff, don't fail immediately
    if dev_is_max_turns and max_turns_no_diff_auto_retry:
        if dev_auto_escalate and (attempt + 1) < max_attempts:
            eprint(f"[INFO] Max turns hit with no diff for {next_task.id}, retrying with escalation...")
            metrics.event("dev_attempt_retry", ..., reason="max_turns_no_diff")
            continue

    # Original no_diff failure logic
    if dev_auto_escalate and (attempt + 1) < max_attempts and "no_diff" in dev_escalate_on:
        ...
```

**Impact:** Prevents wasting 30+ minutes on tasks that hit max_turns without completing


## ⚡ Performance Optimizations

### 2. Continuation Prompt Accumulation
**Location:** `cycle.py:681-688`

**Problem:**
```python
prompt = (
    prompt
    + f"\n\n[CONTINUE] You hit a turn limit previously..."
)
```
- Each continuation appends to prompt
- After 10 continuations: prompt has 10x "[CONTINUE]..." messages
- Wastes tokens

**Fix:**
```python
# Replace instead of append
continuation_msg = (
    "\n\n[CONTINUE] You hit a turn limit previously while running '{label}'. "
    "Continue EXACTLY from where you left off.\n"
    "- Do NOT restate a plan.\n"
    "- Do NOT summarize.\n"
    "- Apply changes now (call tools / edit files).\n"
    "- End with only the required output."
)

if "[CONTINUE]" in prompt:
    # Replace existing continuation message
    prompt = prompt.split("[CONTINUE]")[0] + continuation_msg
else:
    # First continuation
    prompt = prompt + continuation_msg
```


### 3. Unnecessary Git Operations
**Location:** Multiple places

**Current:**
- `git_porcelain()` called before/after each dev attempt
- `git_head()` called multiple times per cycle
- Each git call spawns subprocess

**Optimization:**
- Cache git_head result within a cycle (head rarely changes during execution)
- Only call git_porcelain when needed (not on every exception path)


### 4. Exception Traceback Always Formatted
**Location:** `cycle.py:1684`

**Current:**
```python
exc_traceback = traceback.format_exc()
dev_log += f"\n[EXCEPTION]\n{exc_header}\n\nTraceback:\n{exc_traceback}\n"
```

**Problem:**
- `traceback.format_exc()` is expensive (stack inspection)
- Called even when dev_exc is None or when debug=False

**Fix:**
```python
if dev_exc:
    exc_header = f"{type(dev_exc).__name__}: {str(dev_exc)}" if str(dev_exc) else type(dev_exc).__name__

    # Only format full traceback if debug mode or if needed
    if bool(getattr(args, "debug", False)):
        exc_traceback = traceback.format_exc()
        dev_log += f"\n[EXCEPTION]\n{exc_header}\n\nTraceback:\n{exc_traceback}\n"
    else:
        # Just save exception type and message
        dev_log += f"\n[EXCEPTION]\n{exc_header}\n"
```


## 📝 Dev Prompt Improvements

### 5. Dev Task Prompt Too Generic
**Location:** `prompts/BudgetBook-69084820/dev_task_prompt.md`

**Current Issues:**
- No specific guidance on code style
- No hints on where to look first
- No examples of good micro-task implementations

**Improvements:**

#### Add Implementation Strategy Section:
```markdown
**Implementation Strategy (follow this order):**
1. Read ONLY the files listed in "Files to touch" section
2. Understand the change scope (< 50 lines ideal)
3. Make targeted edits using Edit tool (not Write)
4. Verify compilation safety mentally before editing
5. Update {run_dir}/NOTES.md with validation steps

**Code Style (MAUI Blazor):**
- Use @code blocks for component logic
- Prefer EventCallback<T> over Action<T>
- Use @bind-Value for two-way binding
- Follow existing naming conventions in the file
- Keep methods under 20 lines

**Token Optimization:**
- DO NOT read files not in "Files to touch" unless truly necessary
- Use grep/glob with specific paths, not broad searches
- Read targeted line ranges when possible
```

#### Add File Context Hints:
Instead of just listing files, provide context:
```markdown
Files to modify:
{files_with_context}

Example format:
- Pages/Dashboard.razor (lines 140-160: sync button handler)
- wwwroot/css/app.css (add .loading-spinner styles)
```


### 6. Missing "Definition of Done" Checklist
**Add to dev_task_prompt.md:**

```markdown
**Before Finishing - Checklist:**
- [ ] Code compiles (mental check)
- [ ] Changes are < 100 lines total
- [ ] No hardcoded secrets or API keys
- [ ] Updated {run_dir}/NOTES.md with:
  - Files changed (list)
  - Why changed (1 sentence)
  - How to validate (1-2 steps)
- [ ] Analysis hint written to {analysis_hint_out}
```


## 🔧 Recommended Changes Priority

**P0 (Immediate):**
1. ✅ Fix datetime import (done)
2. ✅ Fix exception logging (done)
3. ✅ Fix allow_no_diff setting (done)
4. ⏳ **Fix max_turns + no_diff logic** (prevents 30-min waste)

**P1 (High Impact):**
5. Improve dev_task_prompt with strategy section
6. Add file context hints to task prompts
7. Fix continuation prompt accumulation

**P2 (Nice to Have):**
8. Cache git operations within cycle
9. Conditional traceback formatting
10. Add checklist to dev prompt

## 📊 Expected Impact

**Current State (BudgetBook):**
- T1: 38 minutes, 18 turns → no diff → marked "done" (wrong)
- Result: 0 completed tasks, 1 false positive

**After Fixes:**
- P0 fixes: Tasks with max_turns auto-retry instead of failing
- P1 fixes: Dev agent uses fewer turns (better guidance)
- Expected: 2-3x more tasks completed per run

**Token Savings:**
- Continuation prompt fix: ~500 tokens saved per continuation
- Better dev prompt: ~2000 tokens saved per task (less exploration)
- Git caching: Minimal, but cleaner code
