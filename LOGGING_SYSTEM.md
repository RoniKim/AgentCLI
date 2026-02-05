# Enhanced Logging System

AgentCLI now includes a comprehensive structured logging system for better error tracking and debugging.

## Log Files Location

All logs are stored in `<run_dir>/logs/`:

```
.doc/agent_runs/YYYYMMDD-HHMMSS/
├── logs/
│   ├── debug.log          # Detailed debug information (only in debug mode)
│   ├── error.log          # Error details with full context
│   └── events.jsonl       # Structured event data (JSON Lines format)
├── metrics.jsonl          # Existing metrics (preserved)
└── dev_logs/              # Existing dev logs (preserved)
```

## Log Files Overview

### 1. **debug.log** (Debug Mode Only)
Contains detailed execution traces for debugging:
- All operations and their results
- Timing information
- Function calls and returns
- Only enabled when `debug: true` in config

**Example:**
```
2026-02-06 05:30:15 [INFO] Task started: T1a - Create ConfirmDialog.razor (attempt 0)
2026-02-06 05:30:45 [DEBUG] Timing: dev_task_execution took 30.25s
2026-02-06 05:30:45 [INFO] Task completed: T1a (completed)
```

### 2. **error.log**
Dedicated error log with full context for troubleshooting:
- Error message and exception type
- Full stack trace
- Context information (task, cycle, model, etc.)
- Timestamp for correlation

**Example:**
```
================================================================================
ERROR at 2026-02-06T05:35:12Z
================================================================================
Message: Dev task execution failed: T2b

Exception: TimeoutError:

Context:
  cycle: 0
  step: 1
  task_id: T2b
  task_title: Add CSS styling to Dashboard
  model: gpt-5.1-codex-mini
  attempt: 0
  duration_sec: 3602.5
  is_max_turns: False
  is_quota_exhausted: False

Traceback:
Traceback (most recent call last):
  File "agent_runner/cycle.py", line 1667, in main_async
    dev_result = await _run_with_continuations(...)
  ...
TimeoutError

================================================================================
```

### 3. **events.jsonl**
Structured event log in JSON Lines format for programmatic analysis:
- One JSON object per line
- Easy to parse with `jq`, Python, or other tools
- All events include timestamp and type

**Example:**
```json
{"ts": "2026-02-06T05:30:15Z", "type": "task_start", "task_id": "T1a", "task_title": "Create ConfirmDialog.razor", "attempt": 0, "files": ["Shared/ConfirmDialog.razor"]}
{"ts": "2026-02-06T05:30:45Z", "type": "timing", "operation": "dev_task_execution", "duration_sec": 30.25, "task_id": "T1a", "attempt": 0}
{"ts": "2026-02-06T05:30:45Z", "type": "task_end", "task_id": "T1a", "success": true, "reason": "completed", "attempt": 0}
{"ts": "2026-02-06T05:35:12Z", "type": "error", "msg": "Dev task execution failed: T2b", "exception": {"type": "TimeoutError", "message": "", "repr": "TimeoutError()"}, "traceback": "...", "context": {...}}
```

## Event Types

### Task Events
- `task_start`: Task execution begins
- `task_end`: Task completes (success or failure)
- `timing`: Operation duration tracking

### Error Events
- `error`: Error occurred with full context
- `warning`: Warning message
- `info`: General information
- `debug`: Debug-level information (debug mode only)

## Analyzing Logs

### Find All Errors
```bash
# Using error.log
grep "ERROR at" <run_dir>/logs/error.log

# Using events.jsonl
grep '"type":"error"' <run_dir>/logs/events.jsonl | jq .
```

### Check Task Performance
```bash
# Find slow tasks (> 60 seconds)
grep '"type":"timing"' <run_dir>/logs/events.jsonl | jq 'select(.duration_sec > 60)'
```

### Trace Task Execution
```bash
# Get all events for a specific task
grep '"task_id":"T1a"' <run_dir>/logs/events.jsonl | jq .
```

### Find Timeout Errors
```bash
# Find all timeout-related errors
grep -i "timeout" <run_dir>/logs/error.log -A 20
```

## Context Tracking

The logging system automatically tracks execution context:

**Set at task start:**
- `cycle`: Current cycle number
- `step`: Step within cycle
- `task_id`: Task identifier
- `task_title`: Human-readable task name
- `model`: Model being used
- `attempt`: Attempt number (for retries)
- `max_turns`: Max turns configured
- `timeout_sec`: Timeout in seconds

**Included in error logs:**
- All context from task start
- Exception type and message
- Full stack trace
- Additional error-specific context

## Configuration

Enable debug logging in config:
```json
{
  "debug": true
}
```

When enabled:
- `debug.log` is created with detailed traces
- More verbose console output
- All debug-level messages are logged

## Integration with Existing Metrics

The new logging system **complements** the existing `metrics.jsonl`:

**metrics.jsonl** (preserved):
- High-level events (cycle_start, cycle_end, etc.)
- Performance metrics
- Budget tracking

**logs/events.jsonl** (new):
- Detailed task-level events
- Error context
- Timing information

Both can be used together for comprehensive analysis.

## Best Practices

### 1. Always Check error.log First
When debugging failures, start with `error.log` for:
- Human-readable error messages
- Full stack traces
- Context information

### 2. Use events.jsonl for Patterns
For analyzing trends or patterns:
```bash
# Find most common error types
grep '"type":"error"' logs/events.jsonl | jq -r '.exception.type' | sort | uniq -c | sort -rn
```

### 3. Correlate with metrics.jsonl
Use timestamps to correlate between logs:
```bash
# Get metrics around an error time
grep "2026-02-06T05:35" metrics.jsonl
grep "2026-02-06T05:35" logs/events.jsonl
```

### 4. Monitor Timing
Track performance over time:
```bash
# Average task duration by task_id
grep '"type":"timing"' logs/events.jsonl | jq -r '[.task_id, .duration_sec] | @tsv' | awk '{sum[$1]+=$2; count[$1]++} END {for (task in sum) print task, sum[task]/count[task]}'
```

## Example: Debugging a Failed Task

```bash
# 1. Find the error
grep "T2b" logs/error.log

# 2. Get full task trace
grep "T2b" logs/events.jsonl | jq .

# 3. Check timing
grep "T2b" logs/events.jsonl | grep timing | jq .duration_sec

# 4. Look at context
grep "T2b" logs/error.log -A 30 | grep "Context:" -A 15
```

## Future Enhancements

Potential additions:
- Log rotation for long-running sessions
- Automatic error aggregation and reporting
- Performance regression detection
- Integration with external monitoring tools
