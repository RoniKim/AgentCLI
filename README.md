# CLI-first Multi-Agent Runner 2.0 (PM → Dev → QA)

이 번들은 **CLI 기반**으로 동작하는 Codex/Agents SDK 러너입니다.

2.0에서 바뀐 핵심:
- **PM 최종 응답을 JSON 스키마로 강제**하고(pydantic 검증 + 자동 리페어),
  그 JSON으로 `BACKLOG.json|md`를 러너가 **직접 생성**합니다.
- Dev 프롬프트를 개선해 **사전 '플랜 장황 출력'을 줄이고, `apply_patch` 중심**으로 작업하도록 강제합니다.
- (선택) **max turns**에 걸리면 **continuation 프롬프트로 이어서** 재시도할 수 있습니다.

## Quick Start

```bash
# 1) 의존성 설치
pip install -U -r requirements.txt

# 2) 실행 (interactive shell, 기본)
python agent_cli.py --repo "C:/Dev/BudgetBook"
# then:
# > /config
# > /start --autopilot
# > /status
# > /stop

# 2-b) 즉시 실행 (legacy)
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --autopilot
```

### Wizard로 config 생성
```bash
python agent_cli.py --repo "C:/Dev/BudgetBook" --wizard
```

- 기본 생성 경로: `REPO/.doc/agent_config.json`
- 프롬프트 템플릿 생성 경로(기본): `REPO/.doc/agent_prompts/`

## Unattended Loop

```bash
# STOP 파일로 안전하게 종료
python agent_cli.py --repo "C:/Dev/BudgetBook"
# > /start --loop --autopilot
```

중단:
- interactive shell에서 `/stop` 입력 (run_dir/STOP 생성)
- 또는 `run_dir/STOP` 파일이 존재하면 graceful stop
- `--loop-idle-exit-after`로 공회전 종료

## Structured PM Output (2.0)

PM은 **반드시 JSON만** 출력해야 하며, 러너가 이를 검증합니다.
- 검증 성공: `run_dir/PM_OUTPUT_cycle_XXX.json` 저장 + `BACKLOG.json|md` 재생성
- 검증 실패: `--pm-structured-retries` 횟수만큼 리페어 재시도

관련 옵션:
- `--pm-structured-retries 2`
- `--pm-max-turns-continuations 1`
- `--dev-max-turns-continuations 2`

## Prompt Templates

아래 파일을 수정하면 에이전트 프롬프트를 교체할 수 있습니다:

- `pm_instructions.md`
- `dev_instructions.md`
- `qa_instructions.md`
- `pm_bootstrap_prompt.md`
- `pm_incremental_prompt.md`
- `dev_task_prompt.md`
- `qa_prompt.md`

샘플 템플릿은 `templates/agent_prompts/`에 포함되어 있습니다.

## Artifacts

- `run_dir/metrics.jsonl` : JSONL 이벤트 로그
- `run_dir/STATE.json` : 완료/실패 태스크
- `run_dir/PM_OUTPUT_cycle_*.json` : PM 스키마-검증된 최종 JSON
- `run_dir/BACKLOG.json|md` : 러너가 생성한 백로그(권위 소스)
- `run_dir/NOTES_PM.md` : PM 메모(있을 때)
- `run_dir/tasks/` : 태스크별 로그/게이트 결과

## Notes

- 이 툴은 실제 OpenAI 비용이 사용됩니다.
- 시크릿은 절대 config/prompt에 넣지 마세요. `.env` 또는 환경변수로만 주입하세요.
