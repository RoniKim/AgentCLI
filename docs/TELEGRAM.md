# Telegram Hybrid Mode

## Overview

AgentCLI supports Telegram long-polling hybrid mode:

- local interactive shell and Telegram run together in one process
- no inbound port or webhook required

- status monitoring (`/status`)
- stop (`/run_stop`)
- re-run (`/run_start`)
- run history (`/runs`)
- log tail (`/tail`)
- detailed combined logs (`/detail`)
- filtered errors/events/grep (`/errors`, `/events`, `/grep`)
- automatic push notifications (run/task/quota/error/stalled)

## Start

```bash
set AGENTCLI_TELEGRAM_BOT_TOKEN=123456:ABCDEF...
python agent_cli.py --telegram --repo "C:/Dev/YourRepo"
```

This starts both local shell and Telegram control-plane.

## Security

- Use `allowed_chat_ids` allowlist.
- Prefer pairing flow with `pairing_code`.
- Keep token in env var (`AGENTCLI_TELEGRAM_BOT_TOKEN`) instead of config when possible.
- Config/data default path is `%USERPROFILE%\\.agentcli` (or `~/.agentcli`).

## Config keys

```json
{
  "telegram": {
    "enabled": false,
    "bot_token": "",
    "allowed_chat_ids": [],
    "pairing_code": "",
    "instance_name": "home-pc-main",
    "notify_events": ["run_start", "run_stop", "task_done", "task_failed", "quota", "error", "stalled"],
    "send_cycle_summary": true,
    "notify_poll_interval_seconds": 8,
    "stalled_seconds": 600,
    "tail_lines_default": 50,
    "runner_mode": "thread",
    "poll_timeout_seconds": 30
  }
}
```

## Commands

- `/start`
- `/whoami`
- `/pair <code>`
- `/status`
- `/detail [lines]`
- `/errors [lines]`
- `/events <event_name> [lines]`
- `/grep <pattern> [file] [lines]`
- `/run_start [--autopilot --continuous --iterations 5 ...]`
- `/run_stop`
- `/runs [N]`
- `/tail [file] [lines]`
- `/notify`

## Push notifications

- Push is enabled automatically when `notify_events` is not empty or `send_cycle_summary=true`.
- Messages are broadcast to all `allowed_chat_ids`.
- Each message includes `instance_name` so multiple instances can be distinguished.
- `stalled` alert triggers when `metrics.jsonl` has no update for `stalled_seconds` (default: 600s).

## Multiple instances

- Use a different bot token per AgentCLI process for stable long polling.
- If multiple processes share one token, Telegram update polling can conflict.
- If multiple instances target the same chat, you receive separate notifications from each instance.
- If multiple instances point to the same repo/run_dir, stop/status/log files can interfere.

## Log language policy

- Keep artifact logs (`metrics.jsonl`, `run_summary.json`, etc.) in English.
- Use Telegram as a viewing layer; avoid runtime LLM translation for logs.
