# CLI-first Multi-Agent Runner 2.0 (PM → Dev → QA)

이 번들은 **CLI 기반**으로 동작하는 PM→Dev→QA 자동 개발 러너입니다.  
기본은 **Interactive Shell(명령어 기반)** 이고, 스크립트/CI/무인 운용을 위한 **즉시 실행(--run-now)** 도 지원합니다.

---

## 핵심 개념

- **PM(Project Manager)**: 레포 분석 → 구조화(JSON) 백로그 생성  
- **Dev(Developer)**: 백로그 태스크를 순서대로 수행(기본: task 당 max_turns 제한)  
- **QA**: 옵션/설정에 따라 점검 수행

### PM 백로그 정책(중요)

PM이 생성하는 BACKLOG의 태스크는 **오직 “개발 작업(기능/화면/UI/버그수정/테스트/필수 문서)”** 이어야 합니다.

- ✅ 허용: 기능 구현, 화면/컴포넌트/UI 구현, 버그 수정, 테스트 추가, (해당 변경을 설명하기 위한) README 등 in-repo 문서 업데이트
- ❌ 금지: 계획/분석/검토/트리아지/우선순위 정하기, REPO_INVENTORY/PROJECT_ANALYSIS/BACKLOG/리포트 생성, 프롬프트/런너 아티팩트(.doc/agent_runs, .doc/PM_CACHE 등) 정리

러너는 위 “금지 작업”을 태스크로 내놓는 경우를 자동 필터링하여 Dev에게 전달하지 않습니다.

2.0에서 바뀐 핵심:

- **PM 최종 응답을 JSON 스키마로 강제**(pydantic 검증 + 자동 리페어)  
  → 검증된 JSON으로 러너가 `BACKLOG.json|md`를 **직접 생성**
- Dev는 `apply_patch` 중심으로 작업하도록 프롬프트/런너가 유도 (불필요한 장황 출력/토큰 낭비 감소)
- (선택) **max turns** 등에 걸리면 **continuation 프롬프트로 이어서** 재시도(베스트-에포트)

---


### Max turns 초과(Dev/PM) 대응: Continuations

에이전트가 `MaxTurnsExceeded`로 중단될 때, 러너가 **짧은 CONTINUE 프롬프트로 이어서 재시도**할 수 있습니다.

- Dev: `--dev-max-turns-continuations` (기본 2)
- PM: `--pm-max-turns-continuations` (기본 1)

예시:

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --non-interactive --autopilot --continuous \
  --max-turns-per-task 10 --dev-max-turns-continuations 3
```

> 구버전 config가 `dev_max_turns_continuations=0`을 저장하고 있을 수 있습니다.  
> 이 경우 `/wizard`로 config를 다시 저장하거나, `/set dev_max_turns_continuations 2`로 올리면
> “밤새 돌다 max turns로 죽는” 케이스를 크게 줄일 수 있습니다.

---

## 파일 구조

```text
.
├─ agent_cli.py
├─ requirements.txt
├─ README.md
├─ DESIGN.md
├─ agent_runner/
│  ├─ cli.py                 # argparse + DEFAULTS + config merge + wizard/초기화 플래그
│  ├─ main.py                # --run-now(즉시 실행) 진입
│  ├─ shell.py               # Interactive Shell (/start, /stop, /config, /set, /save, /load ...)
│  ├─ cycle.py               # 러너 본체: PM→Dev→QA 사이클/루프/종료조건/게이트/산출물
│  ├─ config.py              # config 로드/저장(기본: AgentCLI/configs/<repo-hash>.json, legacy: REPO/.doc/agent_config.json)
│  ├─ prompts.py             # 프롬프트 템플릿 로딩/렌더링 + 기본 템플릿 생성
│  ├─ schemas.py             # PM 구조화 출력(pydantic 모델)
│  ├─ structured.py          # JSON 파싱/리페어/검증 유틸
│  ├─ state.py               # BACKLOG/STATE 저장·로드, 완료 처리
│  ├─ gates.py               # (옵션) dotnet build/test 게이트
│  ├─ gitops.py              # 변경 감지/체크포인트/워크트리(옵션: isolate_task/worktree_isolation)
│  ├─ policy.py              # 시크릿/키 유출 스캔(옵션)
│  ├─ docs.py                # .env 로딩 + docs digest 생성/읽기 유틸
│  ├─ run_dir.py             # run_dir 생성/최근 run 탐색
│  ├─ inventory.py           # git-tracked 파일 인벤토리 생성(REPO_INVENTORY.*)
│  ├─ analysis_cache.py      # PM_CACHE(분석 아티팩트) 유지/누적
│  ├─ metrics.py             # metrics.jsonl 이벤트 로그
│  ├─ tracing.py             # trace/span 유틸
│  ├─ utils.py               # subprocess/IO 등 공용 유틸
│  ├─ wizard.py              # config 생성/수정 마법사
│  └─ version.py
└─ templates/
   └─ agent_prompts/         # 프롬프트 템플릿 샘플
````

---

## 요구사항

* **Python 3.10+** 권장
* **Node.js + npx** (기본 MCP 모드가 `--mcp-mode npx` 이므로 필요)
* (선택) **.NET SDK**

  * 기본 build/test 게이트는 dotnet 기반입니다(레포 루트의 *.csproj 감지 등).
  * 레포가 .NET이 아니면 `--no-build`를 쓰거나, config에서 `build_cmd`/`test_cmd`로 게이트 명령을 지정하세요.

---

## 설치

```bash
pip install -U -r requirements.txt
```

---

## 환경변수 / .env

### Execution Backend (codex / claudecode)

AgentCLI는 설정(`execution_backend`)에 따라 실행 엔진을 교체할 수 있습니다.

* `codex` (기본): OpenAI Agents + Codex MCP 기반
* `claudecode`: Claude Agent SDK 기반(Claude Code CLI 포함)

> Interactive Shell에서 `/set execution_backend claudecode` 후 `/save` 하면 config에 저장됩니다.

필수(backend에 따라):

* `codex`: `OPENAI_API_KEY`
* `claudecode`: `ANTHROPIC_API_KEY` (+ `pip install claude-agent-sdk` 필요)

`.env` 로딩은 “베스트-에포트”로 동작합니다.

* `--env-file`을 지정하면 그 파일을 우선 로딩합니다.
* 그 외에도 현재 작업 디렉토리, 패키지 위치, repo 상위/하위에서 `.env`를 탐색합니다.

예시(.env):

```bash
OPENAI_API_KEY=xxxxx
```

---

## 실행 방법

### 1) Interactive Shell (기본)

가장 많이 쓰는 흐름입니다.
`--repo`를 주면 셸에 repo가 미리 세팅됩니다(권장). 안 줘도 `/repo`로 나중에 지정 가능합니다.

```bash
python agent_cli.py --repo "C:/Dev/BudgetBook"
```

셸에서:

```text
> /help
> /config
> /start --autopilot --continuous
> /status
> /stop --wait
> /exit
```

### 안전 옵션 (rollback/worktree)

기본값은 **안전 모드**입니다. 자동 롤백이 필요하면 명시적으로 허용해야 합니다.

```bash
# 파괴적 롤백 허용 (git reset/clean + patch apply)
python agent_cli.py --run-now --repo <path> --dangerous-git-rollback

# 안전한 격리 실행 (git worktree에서 작업 후 성공 시 patch 적용)
python agent_cli.py --run-now --repo <path> --worktree-isolation
```

> worktree 모드는 실패/중단 시 원 repo를 보존하고 worktree만 삭제합니다.

### /doctor (환경/설정 점검)

셸에서 `/doctor`를 실행하면 환경 진단 보고서가 `DOCTOR.md`로 저장됩니다.

#### Interactive 명령어 치트시트

* `/help` : 도움말
* `/repo <path>` : repo 지정
* `/config` : 현재 적용 설정(기본값+config+오버라이드) + env sanity 출력
* `/set <key> <value>` : 설정 오버라이드 (타입은 DEFAULTS 기준으로 추론)
* `/add <key> <value>` : 리스트 설정에 append (예: `policy_rule`)
* `/load [path]` / `/save [path]` : config JSON 로드/저장

  * 기본 경로: `AgentCLI/configs/<repo-hash>.json`
  * 레거시 호환: `REPO/.doc/agent_config.json` (존재할 때만 자동 로드)
* `/start [--flags...]` : 러너를 **백그라운드 스레드로 실행**
* `/todo --save` : `REPO/.doc/todo/Today_<hash>.md` 생성 후 선택 + 열기
* `/todo --load latest|<path>` : 기존 TODO 선택 + 열기 (PM은 TODO를 최우선 반영하여 BACKLOG 생성)
* `/stop [--wait]` : `run_dir/<STOP_FILE>` 생성으로 graceful stop 요청
* `/status` : 러너 상태
* `/exit` : 종료

> 주의: interactive 모드에서 러너는 **현재 터미널 프로세스에 종속**됩니다.
> 터미널을 닫으면 같이 종료될 수 있으므로 “밤새 무인 운용”은 `--run-now`를 권장합니다.

추가 팁:
* interactive에서 `run_dir`을 직접 지정하지 않았고, `--loop/--continuous/--autopilot`로 시작하면
  기본적으로 **가장 최신 run_dir을 이어서(resume) 사용**합니다(백로그/상태 중복 생성 방지).

---

### 2) 즉시 실행(--run-now) — 스크립트/CI/무인 운용용

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --autopilot --continuous --non-interactive
```

* `--non-interactive`: 중간에 입력을 요구하는 프롬프트가 뜨는 것을 방지(무인 운용 필수)
* `--continuous`: 백로그 생성 후 **태스크 실행까지 자동 진행**

  * `--continuous`가 없으면 PM/백로그 준비만 하고 종료합니다.

---

## 추천 실행 프리셋 (예시)

### 비용 절감 모델 프리셋

기본값은 비용을 크게 줄이기 위해 아래처럼 설정되어 있습니다.

- PM: `gpt-5-mini`
- Dev(기본): `gpt-5.1-codex-mini`
- Dev(상향): `gpt-5.1-codex` → `gpt-5.2-codex` (실패 시 자동 상향 재시도)
- QA: `gpt-5-mini`
- Reporter(종료 보고서): `gpt-5-nano`

config 예시(핵심만):

```json
{
  "pm_model": "gpt-5-mini",
  "dev_model": "gpt-5.1-codex-mini",
  "dev_auto_escalate": true,
  "dev_max_escalations": 2,
  "dev_model_tier1": "gpt-5.1-codex",
  "dev_model_tier2": "gpt-5.2-codex",
  "dev_escalate_on": ["no_diff", "build_failed", "test_failed"],
  "qa_model": "gpt-5-mini",
  "reporter_model": "gpt-5-nano",
  "report_max_turns": 8
}
```

설명:
- Dev가 `no_diff/build_failed/test_failed`로 멈출 상황이면, 동일 태스크를 상위 모델로 **최대 2번까지** 재시도합니다.
- base 모델이 존재하지 않거나(예: model not found) 호출이 실패하면, 자동으로 tier1/tier2로 폴백합니다.
- Codex 사용량 제한(usage limit) 발생 시에는 즉시 중단하고 `SHUTDOWN_REPORT.md`를 남깁니다.


### A) 스모크 테스트(백로그만 준비)

토큰 사용을 최소화하고 “환경/산출물”만 확인할 때:

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --non-interactive --autopilot
```

결과:

* `run_dir/BACKLOG.json|md` 생성(및 PM 산출물 로그)

### B) 태스크 실행(최대 5개만)

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --non-interactive --autopilot --continuous --iterations 5
```

### C) 밤새 무인(루프)

* `--loop`: PM→Dev→QA 사이클을 반복
* `--loop-idle-exit-after`: 진행이 없으면 자동 종료(비용 방어)
* `--loop-max-cycles`: 최대 사이클 상한(비용 방어)

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --non-interactive --autopilot \
  --loop --loop-sleep-seconds 60 --loop-idle-exit-after 3600 --loop-max-cycles 20
```

---

## 안전 종료(Stop File)

기본 stop 파일명은 `STOP`이며, `run_dir/STOP` 파일이 생기면 러너가 graceful stop 합니다.

* interactive: `/stop` 또는 `/stop --wait`
* 수동: `run_dir/STOP` 파일 생성
* 변경: `--stop-file <NAME>`

---

## “변경 없음(no diff)” 처리

기본 동작은 **태스크 수행 후 git diff가 없으면 실패로 간주하고 중단**합니다(토큰 낭비 방지).
계속 진행시키려면:

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --non-interactive --autopilot --continuous --allow-no-diff
```

`--stop-if-no-diff`는 구버전 호환용(deprecated)입니다.

---

## 빌드/테스트 게이트(.NET)

기본: **태스크마다 `dotnet build` 수행**

* 끄기: `--no-build`
* 빌드 타깃 지정: `--dotnet-build-target <csproj|sln|path>`
* 테스트 켜기: `--run-tests`

  * 타깃: `--dotnet-test-target <csproj|sln|path>`
  * 필터: `--dotnet-test-filter "<expr>"`
  * 타임아웃: `--test-timeout-seconds 3600`

> 레포가 .NET이 아니라면 우선 `--no-build`로 운용하고,
> 필요한 경우 `agent_runner/gates.py`를 프로젝트에 맞게 수정하세요.

---

## PM 구조화 출력(2.0)

PM은 **반드시 JSON만** 출력해야 하며, 러너가 이를 검증합니다.

* 성공: `run_dir/PM_OUTPUT_cycle_XXX.json` 저장 + `BACKLOG.json|md` 재생성
* 실패: `--pm-structured-retries` 횟수만큼 리페어/재검증

관련 옵션 예시:

```bash
--pm-structured-retries 2
--pm-max-turns-continuations 1
--dev-max-turns-continuations 2
--max-turns-per-task 12
```

---

## 프롬프트 템플릿 커스터마이징

샘플 템플릿은 `templates/agent_prompts/`에 포함되어 있습니다.

레포에 기본 템플릿 생성:

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --init-prompts
```

기본 경로:

* `REPO/.doc/agent_prompts/`

대표 파일:

* `pm_instructions.md`
* `dev_instructions.md`
* `qa_instructions.md`
* `pm_bootstrap_prompt.md`
* `pm_incremental_prompt.md`
* `dev_task_prompt.md`
* `qa_prompt.md`

---

## 문서(Docs) 읽기 / Digest

기본값: `--docs-read-mode digest` (토큰 절약 권장)

* `digest`: 헤딩 인덱스만 읽음
* `full`: 문서 원문 열람 가능
* `none`: docs 무시

Digest 생성/갱신(로컬 작업, 토큰 사용 없음):

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --generate-digest
```

---

## 산출물(Artifacts)

### run_dir (실행 단위)

기본: `REPO/.doc/agent_runs/<YYYYMMDD-HHMMSS>/`

* `metrics.jsonl` : 이벤트 로그(JSONL)
* `STATE.json` : 완료/실패 태스크 기록
* `PM_OUTPUT_cycle_*.json` : PM 스키마-검증된 JSON
* `BACKLOG.json|md` : 러너가 생성한 백로그(권위 소스)
* `dev_logs/` : Dev 출력 로그(누적)
* `tasks/` : 태스크별 로그/빌드/테스트 결과

### PM_CACHE (지속 분석 아티팩트)

기본: `REPO/.doc/PM_CACHE/`

* `PROJECT_ANALYSIS.md` : 전역 분석(누적)
* `REPO_INVENTORY.json|md` : git-tracked 파일 인벤토리
* `REPO_SNAPSHOT.json` : HEAD 추적(증분 분석 보조)

---

## Windows 로그 저장 예시(PowerShell)

```powershell
mkdir logs -Force
python agent_cli.py --run-now --repo "C:\Dev\BudgetBook" --non-interactive --autopilot --loop `
  2>&1 | Tee-Object -FilePath ".\logs\night_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"
```

---

## 트러블슈팅

### 1) `OPENAI_API_KEY is not set.`

* `.env` 위치가 애매하면 `--env-file`로 명시하세요.

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --env-file "C:/Dev/BudgetBook/.env" --non-interactive --autopilot
```

### 2) `npx` 를 찾을 수 없음

* Node.js 설치 후 `npx -v` 확인
* 대안: `--mcp-mode codex` (codex CLI가 설치되어 있을 때)

### 3) .NET 빌드가 계속 실패함

* 레포가 .NET이 아니라면 `--no-build`로 끄세요.
* .NET인데도 실패하면 `--dotnet-build-target`로 타깃을 정확히 지정하세요.

### 4) “아무것도 안 하는 것처럼 보임”

* `--continuous` 없이 실행하면 **백로그 준비만 하고 종료**합니다.
* 태스크까지 자동 실행하려면 `--continuous` 또는 `--loop`가 필요합니다.

### 5) no diff로 중단됨

* 기본 정책입니다(토큰 방어). 계속 진행시키려면 `--allow-no-diff`.

---

## Notes

* 이 툴은 실제 OpenAI 비용이 사용됩니다.
* 시크릿은 절대 config/prompt에 넣지 마세요. `.env` 또는 환경변수로만 주입하세요.
