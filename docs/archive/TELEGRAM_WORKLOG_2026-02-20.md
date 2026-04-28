# Telegram Integration Worklog (2026-02-20)

> 📦 **ARCHIVED — 2026-02-20 시점 작업 일지.**
> 영구 가이드는 `docs/TELEGRAM.md` 참조. 본 문서는 당시 구현 작업 기록으로 보존.

## Scope
- Goal: Remote monitor/control of AgentCLI via Telegram (status, stop, restart) with safe per-user token/chat registration.
- Mode: Hybrid only (`--telegram`), local shell + Telegram control plane.

## Implemented Changes

### 1) Telegram control-plane and hybrid execution
- Added/extended hybrid entry path in `agent_cli.py` and Telegram service flow in `agent_runner/remote/telegram_service.py`.
- Long polling architecture retained (no webhook required).
- Local shell and Telegram control run together.

### 2) Security and access control
- Allowlist authorization via `telegram.allowed_chat_ids`.
- Pairing flow via `/pair <code>` using `telegram.pairing_code`.
- Token source priority: `AGENTCLI_TELEGRAM_BOT_TOKEN` over config token.
- Sensitive masking for outbound text payloads.

### 3) Remote commands
- Existing control commands verified:
  - `/status`, `/run_start`, `/run_stop`, `/runs`, `/tail`, `/detail`, `/notify`
- Added detailed filtering/search commands:
  - `/errors [lines]`
  - `/events <event_name> [lines]`
  - `/grep <pattern> [file] [lines]`

### 4) Push notifications
- Automatic push enabled via `notify_events` and `send_cycle_summary`.
- Added/validated `stalled` event with threshold:
  - `telegram.stalled_seconds` (default 600s / 10 min)
- Push cursor persistence added:
  - restart-safe offsets/state per run to avoid replay spam.

### 5) Log system improvements
- Metrics schema normalization in `agent_runner/metrics.py`:
  - `ts, seq, level, event, type(compat), run_id, instance, stage, task_id, message, payload`
- Rotation utility in `agent_runner/utils.py`:
  - size-based rotate + backup retention + age pruning.
- Rotation applied to:
  - `metrics.jsonl`
  - `cycle_summary.log` (both codex/claudecode pipelines)
  - `telegram_runner_subprocess.log`

### 6) Multi-instance safety (same PC)
- Added token fingerprint and local token lock:
  - blocks duplicate startup with same bot token on same machine.
- Added visibility:
  - `token_fingerprint` included in `/start`, `/notify`, startup logs.

### 7) Runtime stability fixes
- Fixed Telegram background-thread event loop issue in hybrid mode:
  - create/set event loop in Telegram thread when missing.
- Fixed stale lock issue on process exit:
  - lock release guaranteed via `atexit` registration.

### 8) Dependency updates
- Added scheduler dependency for PTB job queue push:
  - `APScheduler>=3.10,<4.0` in `requirements.txt`

## Files Touched (major)
- `agent_runner/remote/telegram_service.py`
- `agent_runner/remote/controller.py`
- `agent_runner/metrics.py`
- `agent_runner/utils.py`
- `agent_runner/cycle.py`
- `agent_runner/backends/claudecode.py`
- `agent_runner/cli.py`
- `agent_runner/shell.py`
- `agent_cli.py`
- `requirements.txt`
- `README.md`
- `docs/TELEGRAM.md`
- `docs/TELEGRAM_EVENING_TASKLIST.md`

## Verification Summary

### Static/logic verification
- `py_compile`/`compileall` checks passed on modified Python modules.
- Command parser and Telegram config normalization paths validated.

### Functional smoke verification
- Metrics schema/sequence (`seq`) validation passed.
- `/errors`, `/events`, `/grep` behavior validated.
- `stalled` detection (600s) validated (single notify, no immediate spam).
- Cursor persistence validated (no old replay after restart, new events detected).
- Rotation behavior validated for key log files.
- Token-lock behavior validated:
  - same token blocked, different token allowed.
- Lock cleanup fix validated:
  - lock released on process exit.

### Telegram API/E2E verification
- Bot API connectivity verified (`getMe` success).
- Real message send verified (`sendMessage` success).
- Korean encoding verification passed after Unicode-safe send path.

## Operational Notes
- First DM is not possible until user starts chat with bot (Telegram platform rule).
- Keep logs/artifacts in English for parser/LLM compatibility.
- Recommended deployment policy:
  - one bot token per AgentCLI instance.

## Pending/Recommended Follow-ups
1. Revoke/rotate any token exposed in shared logs/chat history.
2. Finalize `allowed_chat_ids` with real operator chat IDs and clear pairing code after setup.
3. Pre-open-source cleanup:
   - remove tracked personal configs/db/prompts and keep templates only.
4. Optional next phase:
   - SOLID refactor for large duplicated logic in `cycle.py` and `claudecode.py`.

