# CLI-first Multi-Agent Runner (PM → Dev → QA)

이 번들은 **CLI 기반**으로 동작하는 Codex/Agents SDK 러너입니다.  
토큰/현금 비용 최적화를 최우선으로 설계되어 있으며, **설정 파일이 없으면 시작 시 Wizard로 생성**할 수 있습니다.

## Quick Start

```bash
# 1) 의존성 설치
pip install -U openai openai-agents python-dotenv

# 2) 실행 (repo 경로만 주면, config 없을 때 Wizard 선택 가능)
python agent_cli.py --repo "C:/Dev/BudgetBook"
```

### Wizard로 config 생성
```bash
python agent_cli.py --repo "C:/Dev/BudgetBook" --wizard
```

- 기본 생성 경로: `REPO/.doc/agent_config.json`
- 프롬프트 템플릿 생성 경로(기본): `REPO/.doc/agent_prompts/`
- 프롬프트 파일이 **없거나 비어있으면** 코드 내부 기본 프롬프트를 사용합니다.

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

## Token Optimization Tips (권장 기본)

- `docs_read_mode=digest` + `00_DOCS_DIGEST.md` 사용
- `--loop` + **고정 run_dir 재사용** (Wizard 기본)
- PM은 기본적으로 변경이 없으면 **스킵**하도록 설계됨  
  (`--pm-include-working-tree`는 토큰 증가 가능성 있음)

## Unattended Loop

```bash
# STOP 파일을 만들어 안전하게 중단
# (run_dir은 config에서 고정 재사용 권장)
python agent_cli.py --repo "C:/Dev/BudgetBook" --loop --autopilot
```

중단:
- `run_dir/STOP` 파일이 존재하면 graceful stop
- `--loop-idle-exit-after`로 공회전 종료

## Artifacts

- `run_dir/metrics.jsonl` : JSONL 이벤트 로그
- `run_dir/STATE.json` : 완료/실패 태스크
- `run_dir/BACKLOG.json|md` : PM 생성 백로그
- `run_dir/tasks/` : 태스크별 로그/게이트 결과

## Notes

- 이 툴은 실제 OpenAI 비용이 사용됩니다. Wizard 기본값은 토큰 절약 우선입니다.
- 시크릿은 절대 config/prompt에 넣지 마세요. `.env` 또는 환경변수로만 주입하세요.
