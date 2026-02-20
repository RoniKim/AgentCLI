# Telegram Control Plane

## Overview

AgentCLI supports Telegram long-polling service mode for remote control:

- status monitoring (`/status`)
- stop (`/run_stop`)
- re-run (`/run_start`)
- run history (`/runs`)
- log tail (`/tail`)

No inbound port or webhook is required.

## Start

```bash
set AGENTCLI_TELEGRAM_BOT_TOKEN=123456:ABCDEF...
python agent_cli.py --telegram --repo "C:/Dev/YourRepo"
```

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
    "notify_events": ["run_start", "run_stop", "task_done", "task_failed", "quota", "error"],
    "send_cycle_summary": true,
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
- `/run_start [--autopilot --continuous --iterations 5 ...]`
- `/run_stop`
- `/runs [N]`
- `/tail [file] [lines]`

