---

# CLI-first Multi-Agent Runner 2.0 (PM → Dev → QA)

이 번들은 **CLI 기반**으로 동작하는 PM→Dev→QA 자동 개발 러너입니다.
기본은 **Interactive Shell**(Codex-CLI 스타일)이고, 스크립트/CI/무인 운용을 위한 **즉시 실행(`--run-now`)**도 지원합니다.

2.0에서 바뀐 핵심:

* **PM 최종 응답을 JSON 스키마로 강제** (pydantic 검증 + 자동 리페어) → 러너가 `BACKLOG.json|md`를 **권위 소스**로 생성
* Dev를 **패치(apply_patch) 중심**으로 유도해 불필요한 장황 출력/토큰 낭비를 줄임
* (선택) **max turns** 등으로 끊기면 **continuation**으로 이어서 재시도(베스트-에포트)

---

## 파일 구조 (신형 번들)

```text
.
├─ agent_cli.py                # 엔트리포인트: interactive shell(기본) / --run-now(즉시 실행)
├─ requirements.txt
├─ README.md
├─ DESIGN.md
├─ agent_runner/
│  ├─ cycle.py                 # 러너 본체: PM→Dev→QA 사이클/루프/종료조건/로그/게이트
│  ├─ shell.py                 # interactive CLI (/start, /stop, /config, /set ...)
│  ├─ cli.py                   # argparse + DEFAULTS + config merge + wizard/초기화 플래그
│  ├─ config.py                # config 로드/저장 (기본: REPO/.doc/agent_config.json)
│  ├─ prompts.py               # 프롬프트 템플릿 로딩(기본) + PromptStore
│  ├─ schemas.py               # PM 구조화 출력(pydantic 모델)
│  ├─ structured.py            # JSON 파싱/리페어/검증 유틸
│  ├─ state.py                 # BACKLOG/STATE 저장·로드, 완료 처리
│  ├─ gates.py                 # (옵션) dotnet build/test 게이트
│  ├─ gitops.py                # git 파일 목록/변경 감지/체크포인트(옵션)
│  ├─ inventory.py             # git-tracked 파일 인벤토리 생성(REPO_INVENTORY.*)
│  ├─ analysis_cache.py        # PM_CACHE(분석 아티팩트) 유지/누적
│  ├─ docs.py                  # .env 로딩 + docs digest 생성/읽기
│  ├─ run_dir.py               # run_dir 생성/최근 run 탐색
│  ├─ metrics.py               # metrics.jsonl 이벤트 로그
│  ├─ tracing.py               # trace/span 유틸
│  ├─ policy.py                # 시크릿/키 유출 스캔(옵션)
│  └─ main.py                  # --run-now 경로 진입
└─ templates/
   └─ agent_prompts/           # 프롬프트 템플릿 샘플
```

---

## Quick Start

### 0) 요구사항

* Python (권장: 3.10+)
* Node.js + npx (기본 MCP 모드가 `--mcp-mode npx` 이므로 필요)

  * `--codex-package @openai/codex@latest` 를 npx로 실행해 MCP 서버를 띄웁니다.
* (선택) dotnet SDK: `--no-build`를 끄고 기본 빌드 게이트를 사용할 경우 필요

### 1) 설치

```bash
pip install -U -r requirements.txt
```

### 2) 시크릿/환경변수

필수:

* `OPENAI_API_KEY`

권장:

* `.env` 를 **레포 루트**에 두거나, 실행 시 `--env-file`로 명시

```bash
# 예시: 레포 루트에 .env
OPENAI_API_KEY=xxxxx
```

### 3) 실행: interactive shell (기본)

```bash
python agent_cli.py --repo "C:/Dev/BudgetBook"
```

예시 세션:

```text
> /config
> /start --autopilot
> /status
> /stop --wait
> /exit
```

### 4) 실행: 즉시 실행(--run-now, 스크립트/CI/무인 운용)

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --autopilot --non-interactive
```

### Wizard로 config 생성

```bash
python agent_cli.py --repo "C:/Dev/BudgetBook" --wizard
```

* 기본 생성 경로: `REPO/.doc/agent_config.json`
* 프롬프트 템플릿 생성 경로(기본): `REPO/.doc/agent_prompts/`

---

## 실행 방법 상세

### 1) Interactive CLI 명령어 치트시트

Interactive 모드에서 다음 명령을 사용합니다:

* `/help` : 도움말
* `/repo <path>` : 레포 설정
* `/config` : 현재 적용될 설정(기본값+config+오버라이드) + env sanity 출력
* `/set <key> <value>` : 설정 오버라이드
* `/add <key> <value>` : 리스트 옵션에 값 추가 (예: policy_rule)
* `/load [path]` / `/save [path]` : config JSON 로드/저장
* `/start [--flags...]` : 백그라운드에서 러너 실행 (예: `/start --autopilot --loop`)
* `/stop [--wait]` : `run_dir/STOP` 파일 생성으로 graceful stop 요청
* `/status` : 러너 상태 출력
* `/exit` : 종료

> 참고: interactive 모드에서 러너는 **현재 터미널 프로세스의 백그라운드 스레드**로 실행됩니다.
> 터미널을 닫으면 같이 종료될 수 있으니, 밤새 무인 운용은 `--run-now`를 권장합니다.

### 2) Unattended Loop (밤새 무인 운용)

무인 운용은 보통 아래 조합을 씁니다:

* `--run-now --non-interactive` : 중간 질문(입력 대기) 방지
* `--loop` : PM→Dev→QA 사이클 반복
* `--loop-idle-exit-after` : 진행이 없으면 자동 종료(비용 방어)
* `--loop-max-cycles` : 최대 사이클 상한(비용 방어)

```bash
# 예시: 60초마다 재사이클, 1시간 진행 없으면 종료, 최대 20 사이클
python agent_cli.py --run-now --non-interactive --repo "C:/Dev/BudgetBook" \
  --autopilot --loop --loop-sleep-seconds 60 --loop-idle-exit-after 3600 --loop-max-cycles 20
```

중단(둘 중 하나):

* interactive shell에서 `/stop` 입력 (run_dir/STOP 생성)
* 또는 직접 `run_dir/STOP` 파일을 만들면 graceful stop

### 3) 이전 실행 이어서(resume)

최신 run_dir 재사용:

```bash
python agent_cli.py --run-now --non-interactive --repo "C:/Dev/BudgetBook" \
  --resume-latest --autopilot --loop
```

특정 run_dir 지정:

```bash
python agent_cli.py --run-now --non-interactive --repo "C:/Dev/BudgetBook" \
  --run-dir "C:/Dev/BudgetBook/.doc/agent_runs/20260202-123456" --autopilot
```

### 4) Windows에서 로그 파일로 저장 (권장)

PowerShell 예시:

```powershell
python agent_cli.py --run-now --non-interactive --repo "C:\Dev\BudgetBook" --autopilot --loop `
  2>&1 | Tee-Object -FilePath ".\logs\night_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
```

---

## Structured PM Output (2.0)

PM은 **반드시 JSON만** 출력해야 하며, 러너가 이를 검증합니다.

* 검증 성공: `run_dir/PM_OUTPUT_cycle_XXX.json` 저장 + `BACKLOG.json|md` 재생성
* 검증 실패: `--pm-structured-retries` 횟수만큼 리페어 재시도

관련 옵션:

* `--pm-structured-retries 2`
* `--pm-max-turns-continuations 1`
* `--dev-max-turns-continuations 2`

---

## Prompt Templates

아래 파일을 수정하면 에이전트 프롬프트를 교체할 수 있습니다:

* `pm_instructions.md`
* `dev_instructions.md`
* `qa_instructions.md`
* `pm_bootstrap_prompt.md`
* `pm_incremental_prompt.md`
* `dev_task_prompt.md`
* `qa_prompt.md`

샘플 템플릿은 `templates/agent_prompts/`에 포함되어 있습니다.

초기 템플릿을 레포에 생성:

```bash
python agent_cli.py --repo "C:/Dev/BudgetBook" --init-prompts
```

---

## Artifacts

### run_dir (실행 단위)

기본: `REPO/.doc/agent_runs/<YYYYMMDD-HHMMSS>/`

* `run_dir/metrics.jsonl` : JSONL 이벤트 로그
* `run_dir/STATE.json` : 완료/실패 태스크
* `run_dir/PM_OUTPUT_cycle_*.json` : PM 스키마-검증된 최종 JSON
* `run_dir/BACKLOG.json|md` : 러너가 생성한 백로그(권위 소스)
* `run_dir/NOTES_PM.md` : PM 메모(있을 때)
* `run_dir/tasks/` : 태스크별 로그/게이트 결과

### PM_CACHE (지속 분석 아티팩트)

기본: `REPO/.doc/PM_CACHE/`

* `PROJECT_ANALYSIS.md` : PM 전역 분석(누적)
* `REPO_INVENTORY.json|md` : git-tracked 파일 인벤토리
* `REPO_SNAPSHOT.json` : 이전 HEAD 추적(증분 분석 보조)

---

## Notes

* 이 툴은 실제 OpenAI 비용이 사용됩니다.
* 시크릿은 절대 config/prompt에 넣지 마세요. `.env` 또는 환경변수로만 주입하세요.

---

## 자주 겪는 문제 / 트러블슈팅

### 1) `ERROR: OPENAI_API_KEY is not set.`

레포 루트의 `.env`가 로드되는지 확인하거나, 명시적으로 `--env-file`을 사용하세요.

```bash
python agent_cli.py --run-now --non-interactive --repo "C:/Dev/BudgetBook" \
  --env-file "C:/Dev/BudgetBook/.env" --autopilot
```

### 2) `npx` 를 찾을 수 없음

* 기본 MCP 모드가 `--mcp-mode npx` 입니다. Node.js 설치 후 `npx -v`가 동작해야 합니다.
* 대안: 이미 `codex` CLI가 설치되어 있다면 `--mcp-mode codex`를 사용할 수 있습니다.

### 3) 무인 운용 중 멈춤(입력 대기)

* `--non-interactive`를 꼭 붙이세요. (config가 없을 때 wizard 선택을 요구할 수 있음)

### 4) “변경 없음(no diff)” 처리

* 기본 동작은 “no diff = 진행 없음”으로 판단해 stop/실패 처리 쪽으로 갑니다.
* “변경 없어도 계속 진행”을 원하면 `--allow-no-diff`를 사용하세요.
