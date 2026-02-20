# Telegram 하이브리드 모드

## 개요

AgentCLI는 Telegram Long Polling 기반 하이브리드 모드를 지원합니다:

- 로컬 인터랙티브 셸과 Telegram이 하나의 프로세스에서 함께 동작
- 인바운드 포트(inbound port)나 웹훅(webhook) 불필요

- 상태 모니터링 (`/status`)
- 중지 (`/run_stop`)
- 재실행 (`/run_start`)
- 실행 이력 (`/runs`)
- 로그 tail (`/tail`)
- 상세 통합 로그 (`/detail`)
- 에러/이벤트/grep 필터 (`/errors`, `/events`, `/grep`)
- 자동 푸시 알림 (run/task/quota/error/stalled)

## 시작

```bash
set AGENTCLI_TELEGRAM_BOT_TOKEN=123456:ABCDEF...
python agent_cli.py --telegram --repo "C:/Dev/YourRepo"
```


이 명령은 로컬 셸과 Telegram 컨트롤 플레인(control-plane)을 함께 시작합니다.

## 보안

- `allowed_chat_ids` allowlist를 사용하세요.
- `pairing_code` 기반 페어링(pairing) 흐름을 권장합니다.
- 가능하면 토큰은 config보다 환경변수(`AGENTCLI_TELEGRAM_BOT_TOKEN`)로 관리하세요.
- config/data 기본 경로: `%USERPROFILE%\.agentcli` (또는 `~/.agentcli`).

## 설정 키

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


## 명령어

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

## 푸시 알림

- `notify_events`가 비어있지 않거나 `send_cycle_summary=true`이면 푸시가 자동으로 활성화됩니다.
- 메시지는 모든 `allowed_chat_ids`로 브로드캐스트(broadcast)됩니다.
- 각 메시지에 `instance_name`이 포함되어 여러 인스턴스를 구분할 수 있습니다.
- `stalled_seconds` 동안 `metrics.jsonl` 업데이트가 없으면 `stalled` 알림이 트리거됩니다(기본 600초).

## 여러 인스턴스

- 안정적인 Long Polling을 위해 AgentCLI 프로세스마다 서로 다른 bot token 사용을 권장합니다.
- 여러 프로세스가 하나의 token을 공유하면 Telegram 업데이트 폴링이 충돌할 수 있습니다.
- 여러 인스턴스가 같은 chat을 대상으로 하면, 인스턴스별로 별도 알림을 받게 됩니다.
- 여러 인스턴스가 동일 repo/run_dir을 가리키면 stop/status/log 파일이 서로 간섭할 수 있습니다.

## 로그 언어 정책

- 아티팩트 로그(`metrics.jsonl`, `run_summary.json` 등)는 영어로 유지하는 것을 권장합니다.
- Telegram은 조회(view) 레이어로 사용하고, 런타임에 로그를 LLM으로 번역하는 방식은 피하세요.
