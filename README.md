# AgentCLI — CLI기반 Multi-Agent Runner (PM → Dev → QA)

개인 개발자가 **켜두고**, 나중에 **PR 수준의 변경(코드/테스트/문서)** 을 받는 것을 목표로 만든 **CLI 기반 멀티 에이전트 러너**입니다.

- 기본 파이프라인: **PM(백로그 생성) → Dev(구현) → QA(점검/피드백)**
- 실행 엔진(backend): **Codex(OpenAI)** 또는 **Claude Code(Anthropic)** 로 전환 가능
- 기본 UX: **Interactive Shell** (`/start`, `/stop`, `/config` …)
  + 무인 운용/스크립트용: `--run-now` (즉시 실행)

---

## 목차

1. [핵심 기능](#핵심-기능)
2. [아키텍처 개요](#아키텍처-개요)
3. [파이프라인 상세 로직](#파이프라인-상세-로직)
4. [요구사항 및 설치](#요구사항-및-설치)
5. [빠른 시작](#빠른-시작)
6. [설정(Config) 관리](#설정config-관리)
7. [실행 엔진(Backend) 선택](#실행-엔진backend-선택)
8. [역할별 모델 설정](#역할별-모델-설정)
9. [Claude 백엔드 고급 설정](#claude-백엔드-고급-설정)
10. [파이프라인 커스터마이징](#파이프라인roles-커스터마이징)
11. [Enterprise 프로필](#enterprise-프로필)
12. [안전/운영 옵션](#안전운영-옵션-git-stop-no-diff)
13. [예산 가드레일](#예산-가드레일-budget-guardrails)
14. [빌드/테스트 게이트](#빌드테스트-게이트)
15. [정책/시크릿 스캔](#정책시크릿-스캔옵션)
16. [산출물 구조](#산출물artifacts-구조)
17. [트러블슈팅 (문제 상황 및 해결)](#트러블슈팅-문제-상황-및-해결)
18. [추천 운용 프리셋](#추천-운용-프리셋)
19. [프롬프트/문서/스킬](#프롬프트문서스킬)
20. [Preflight 체크 & 환경 검증](#preflight-체크--환경-검증)
21. [보안 메모](#보안-메모)

---

## 핵심 기능

- **Interactive Shell**: 실행 전 설정 확인/수정 후 `/start`로 러너 실행
- **Non-interactive 실행**: `--run-now --non-interactive`로 밤새 무인 운용
- **백엔드 전환**
  - `execution_backend=codex` (기본): OpenAI Agents + Codex MCP
  - `execution_backend=claudecode`: Claude Agent SDK + Claude Code CLI
- **PM 구조화 출력 강제**: PM 응답을 JSON 스키마로 검증 → 러너가 `BACKLOG.json|md`를 생성
- **안전한 Git 운용**
  - 기본은 **안전 모드** (파괴적 롤백 비활성)
  - 선택: `--worktree-isolation`로 격리 worktree에서 작업 후 패치로 반영
- **모델 에스컬레이션**: Dev 실패 시 저비용 → 고비용 모델로 자동 업그레이드
- **빌드/테스트 게이트(옵션)**: 커스텀 `build_cmd/test_cmd`도 지원
- **정책/시크릿 스캔(옵션)**: run_dir 산출물/코드에서 키/토큰 유출 방지 스캔
- **실행 아티팩트 관리**: `run_dir` 단위로 로그/상태/백로그/리포트 보존
- **예산 가드레일**: 에스컬레이션/continuation/재시도 횟수에 상한을 두어 비용 폭주 방지
- **파이프라인 커스터마이징**
  - `roles="PM,Dev,QA,Security"`처럼 역할 순서/구성 변경
  - 플러그인 Stage(외부 모듈) 로드(Allowlist 기반)

---

## 아키텍처 개요

```
agent_cli.py (진입점)
  ├─ --run-now → agent_runner/main.py → 즉시 실행
  └─ (기본)   → agent_runner/shell.py → Interactive Shell → /start로 실행

agent_runner/main.py
  └─ parse_args() → backend 분기
       ├─ codex     → agent_runner/cycle.py          (OpenAI Agents SDK)
       └─ claudecode → agent_runner/backends/claudecode.py (Claude Agent SDK)

파이프라인 오케스트레이션:
  agent_runner/pipeline/manager.py   (PipelineManager)
  agent_runner/pipeline/session.py   (PipelineSession — 백엔드 무관 컨텍스트)
  agent_runner/pipeline/stages/      (PM, Dev, QA, Security Stage 정의)
```

### 설정 우선순위

```
CLI 인자 (--flag)  >  설정 파일 (JSON)  >  DEFAULTS (코드 내 기본값)
```

### 핵심 모듈 역할

| 모듈 | 역할 |
|------|------|
| `cli.py` | DEFAULTS 정의, CLI 파싱, 설정 병합 (`_merge_effective`) |
| `cycle.py` | Codex 백엔드 전체 파이프라인 (PM→Dev→QA→Reporter) |
| `backends/claudecode.py` | Claude 백엔드 전체 파이프라인 |
| `state.py` | `BACKLOG.json`, `STATE.json` 읽기/쓰기, TaskItem 정의 |
| `gitops.py` | 체크포인트 생성/복원, worktree 격리, 변경 감지 |
| `gates.py` | 빌드/테스트 게이트 실행 |
| `prompts.py` | 프롬프트 템플릿 로딩, PM 출력 스키마 정의 |
| `structured.py` | PM JSON 파싱/검증, 에러 설명 생성 |
| `schemas.py` | `PMOutputV2` 스키마, JSON Schema 생성 |
| `pipeline/` | Stage 오케스트레이션, 플러그인 로딩 |
| `shell.py` | Interactive Shell (prompt_toolkit 기반) |

---

## 파이프라인 상세 로직

### 실행 디스패치 흐름 (Preflight → Backend)

```
python agent_cli.py [--flags]
  │
  ├─ --wizard     → 대화형 설정 마법사
  ├─ --init-prompts → 프롬프트 템플릿 생성 후 종료
  ├─ --generate-digest → Docs 다이제스트 생성 후 종료
  ├─ --run-now    → 즉시 실행 (아래 흐름)
  └─ (기본)       → Interactive Shell → /start로 실행
         │
         ▼
  ┌─ Preflight 체크 ─────────────────────────────────┐
  │  1. repo 경로 존재 및 git 초기화 확인             │
  │  2. 백엔드별 필수 조건 검증                       │
  │     ├─ codex: OPENAI_API_KEY, npx 설치           │
  │     └─ claudecode: claude-agent-sdk, 인증         │
  │  3. 빌드 도구 존재 확인 (no_build 아닐 때)        │
  │  4. run_dir 생성/resume 판단                      │
  └──────────────────────────────────────────────────┘
         │
         ▼
  ┌─ Backend Dispatch ───────────────────────────────┐
  │  failover_enabled?                               │
  │  ├─ YES → failover_backends 순서대로 시도        │
  │  │        실패 사유가 failover_on에 해당하면      │
  │  │        다음 백엔드로 전환 (max_switches까지)   │
  │  └─ NO  → 단일 백엔드 실행                       │
  │                                                  │
  │  backend 분기:                                   │
  │  ├─ codex     → cycle.py (OpenAI Agents SDK)    │
  │  └─ claudecode → claudecode.py (Claude SDK)     │
  └──────────────────────────────────────────────────┘
```

### 전체 사이클 흐름

```
┌─────────────────────────────────────────────────────────────┐
│                      1 Cycle                                │
│                                                             │
│  ┌──────┐    ┌──────┐    ┌──────┐    ┌──────────┐          │
│  │  PM  │ →  │ Dev  │ →  │  QA  │ →  │ Reporter │          │
│  └──┬───┘    └──┬───┘    └──┬───┘    └────┬─────┘          │
│     │           │           │              │                │
│  BACKLOG     코드 변경    리뷰/피드백    종료 보고서        │
│  .json/.md   + 빌드/테스트              (조건부)            │
└─────────────────────────────────────────────────────────────┘
       │
       ▼  (loop=true 시 반복)
  다음 Cycle...
```

### 예산 추적 상태 (Budget State)

사이클 실행 중 아래 카운터가 실시간 추적되며, 한도 초과 시 `BudgetExceeded` 예외가 발생합니다:

```
budget_state = {
  "total_escalations": 0,           # 전체 에스컬레이션 횟수
  "total_continuations": 0,         # 전체 continuation 횟수
  "total_repairs": 0,               # PM JSON repair 횟수
  "per_task_escalations": {},       # 태스크별 에스컬레이션: {"T1": 1, "T2": 0}
  "per_task_continuations": {},     # 태스크별 continuation: {"T1": 2}
}

제한 (budgets 객체):
  max_dev_escalations_per_task   → per_task_escalations[task_id] 대조
  max_dev_continuations_per_task → per_task_continuations[task_id] 대조
  max_total_escalations_per_run  → total_escalations 대조
  max_total_continuations_per_run → total_continuations 대조
  max_total_repair_attempts_per_run → total_repairs 대조
```

### PM 단계 (백로그 생성)

**동작 모드:**

| 모드 | 조건 | 설명 |
|------|------|------|
| **Bootstrap** | 첫 실행 (PROJECT_ANALYSIS.md 없음) | 프로젝트 분석 + 초기 백로그 생성 |
| **Incremental** | HEAD 변경 또는 워킹 트리 dirty | 변경사항 반영한 백로그 업데이트 |
| **Refresh** | `pm_refresh_every_cycles` 조건 충족 | 주기적 강제 재분석 |
| **Skip** | repo fingerprint 동일 | 변경 없으면 기존 백로그 재사용 |

**구조화 출력 강제:**

```
PM 호출 → JSON 응답 → parse_pm_output_with_errors() → 스키마 검증
                                                         │
                                 ┌─────────────────────────┤
                                 │                         │
                             성공: PMOutputV2            실패: repair 프롬프트 생성
                             → BACKLOG.json 작성         → 재시도 (최대 pm_structured_retries회)
                                                         → 전부 실패 시 P0 폴백 백로그
```

**PM 출력 스키마 (`PMOutputV2`):**
```json
{
  "kind": "pm_output_v2",
  "summary": "프로젝트 상태 요약",
  "tasks": [
    {
      "id": "T1",
      "title": "로그인 UI 구현",
      "prompt": "LoginPage.razor 생성...",
      "files": ["Pages/LoginPage.razor"],
      "done_when": "빌드 통과, 수동 테스트 가능",
      "skills": ["skill_blazor_ui"],
      "skills_rationale": "UI 작업"
    }
  ],
  "notes_md": "참고사항...",
  "warnings": [],
  "open_questions": []
}
```

**백로그 정규화:**
- 메타 위임 방지: "백로그 생성", "분석 작성" 같은 PM 자기참조 태스크 자동 필터링
- ID 안정성: `T1`, `T2`, ... 형식 강제
- 스킬 검증: `SKILLS_INDEX`와 대조, 없는 스킬 ID 경고

### Dev 단계 (태스크 실행)

**태스크 실행 흐름:**

```
태스크 선택 (BACKLOG에서 미완료 순서대로)
  │
  ├─ 체크포인트 생성 (isolate_task 또는 에스컬레이션 활성 시)
  │
  ▼
┌─────────────────────────────────────────────┐
│  시도 루프 (attempt 0 ~ max_attempts-1)      │
│                                              │
│  1. Dev 에이전트에 프롬프트 전달              │
│  2. Continuation 지원                        │
│     └─ MaxTurnsExceeded 시 [CONTINUE] 추가   │
│        └─ 최대 dev_max_turns_continuations회  │
│  3. 결과 확인                                │
│     ├─ git diff 없음 (no_diff)              │
│     ├─ 빌드 실패 (build_failed)             │
│     └─ 테스트 실패 (test_failed)             │
│  4. 에스컬레이션 판단                        │
│     ├─ 조건 충족 + 상위 모델 있음 → 롤백 후 재시도 │
│     └─ 조건 불충족 또는 상위 모델 없음 → 종료     │
└─────────────────────────────────────────────┘
  │
  ▼
태스크 완료 → STATE.json에 "done" 추가
  또는
태스크 실패 → STATE.json에 "failed" 추가
```

**모델 에스컬레이션 체인:**

```
Codex 백엔드:
  dev_model → dev_model_tier1 → dev_model_tier2
  (gpt-5.1-codex-mini → gpt-5.1-codex → gpt-5.2-codex)

Claude 백엔드:
  claudecode_dev_model → claudecode_dev_model_tier1 → claudecode_dev_model_tier2
  (sonnet → opus → 비워두면 에스컬레이션 없음)
```

**에스컬레이션 트리거 (`dev_escalate_on`):**

| 트리거 | 설명 |
|--------|------|
| `no_diff` | Dev가 코드를 전혀 변경하지 않음 |
| `build_failed` | 빌드 게이트 실패 |
| `test_failed` | 테스트 게이트 실패 |

**Continuation (턴 초과 시 이어서 실행):**

```
Dev 실행 → MaxTurnsExceeded 예외 발생
  │
  ├─ continuations 남아있음 (dev_max_turns_continuations > 0)
  │    → "[CONTINUE] 턴 제한에 도달. 중단한 곳부터 이어서 진행..." 프롬프트 추가
  │    → 재실행 (같은 모델)
  │
  └─ continuations 소진
       → 현재까지 변경사항으로 게이트 진행 (부분 진행 보존)
```

### QA 단계 (리뷰/피드백)

**실행 조건:**
- `qa_always=true` **또는** Dev가 코드를 변경한 경우

**QA 흐름:**
1. Dev가 처리한 태스크들의 스킬 컨텍스트 구성
2. QA 에이전트 실행 (읽기 전용 도구만 허용)
3. 결과를 `qa_final_output_cycle_NNN.txt`에 저장
4. (선택) `qa_to_backlog=true` 시:
   - QA 출력에서 후속 태스크 추출
   - 백로그에 `QA-FU-{hash}` ID로 병합 (중복 방지)
   - 최대 `max_qa_followups`개

### Reporter 단계 (종료 보고서)

**트리거 조건:**
- 할당량 소진 (`quota_exhausted`)
- 모든 태스크 완료 (`all_tasks_done`)
- STOP 파일 생성 (`stop_file`)
- 치명적 에러 발생

**보고서 생성 흐름:**
```
1. SHUTDOWN_CONTEXT.json 수집 (repo 상태, 백로그 진행률, 마지막 태스크)
2. SHUTDOWN_REPORT.md 로컬 폴백 작성 (항상, 토큰 무관)
3. Reporter 에이전트로 보고서 작성 시도 (best-effort)
   └─ 성공 시 폴백 덮어쓰기
   └─ 실패해도 로컬 폴백이 남아있으므로 안전
```

---

## 요구사항 및 설치

### 공통
- **Python 3.10+**
- **Git**

### Codex backend 사용 시(기본)
- **Node.js + npx** (기본 MCP 모드가 `npx`)
- `OPENAI_API_KEY` 환경변수

### Claude Code backend 사용 시
- `pip install -U claude-agent-sdk`
- **Claude Code 인증**(로그인) 또는 `ANTHROPIC_API_KEY`

### (선택) 빌드/테스트 게이트
- .NET 프로젝트면 **.NET SDK**
- 비-.NET 프로젝트면 `--no-build` 권장 또는 `build_cmd/test_cmd` 설정

### 설치

```bash
pip install -U -r requirements.txt
```

> `claude-agent-sdk`는 기본 requirements에 포함되어 있지 않습니다(선택 의존성).
> Claude backend를 쓸 때만 별도 설치하세요.

---

## 빠른 시작

### 1) Interactive Shell (권장: 설정 확인 후 시작)

```bash
python agent_cli.py --repo "C:/Dev/BudgetBook"
```

Shell에서:

```text
> /config
> /start --autopilot --continuous
> /status
> /stop --wait
> /exit
```

### 2) 무인 운용 / 스크립트 실행 (--run-now)

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --non-interactive --autopilot --continuous
```

- `--non-interactive`: 중간 입력 요구를 최대한 방지(무인 운용 필수)
- `--continuous`: 백로그 생성 후 Dev 태스크 실행까지 자동 진행
  (`--continuous`가 없으면 PM/백로그 준비만 하고 종료)

---

## 설정(Config) 관리

### config 저장 위치(기본)

기본적으로 config는 **레포 내부가 아니라 AgentCLI 쪽**에 저장됩니다:

- `{AgentCLI_HOME}/configs/<repo-slug>-<hash>.json`

환경변수로 홈 변경 가능:

- `AGENTCLI_HOME=<path>` 를 설정하면 `configs/`, `prompts/`의 기준 디렉토리가 바뀝니다.

> 레거시 호환: 레포에 `REPO/.doc/agent_config.json`이 있으면 **읽기용으로 폴백 로드**할 수 있으며, 이후 `/save`하면 새 경로로 마이그레이션됩니다.

### Shell에서 자주 쓰는 명령

| 명령 | 설명 |
|------|------|
| `/config` | 현재 적용 설정(기본값+config+오버라이드) 출력 |
| `/config --all` | 원본 JSON까지 포함한 전체 출력 |
| `/set <key> <value>` | 설정 오버라이드 (세션 한정, `/save`해야 영구) |
| `/add <key> <value>` | 리스트 설정에 값 추가 |
| `/load [path]` | config JSON 로드 |
| `/save [path]` | 현재 설정을 JSON으로 저장 |
| `/repo <path>` | repo 경로 변경 |
| `/todo` | 현재 TODO 확인 |
| `/todo --save` | 오늘 할 일 TODO 생성 후 에디터 열기 |
| `/start [--flags]` | 백그라운드로 러너 시작 |
| `/stop [--wait]` | STOP 파일 생성, 선택적 대기 |
| `/status` | 러너 상태/실행시간/종료코드 확인 |
| `/doctor` | 환경 진단 (API 키, SDK, 빌드 도구 등) |
| `/help` | 명령어 도움말 |
| `/exit` | Shell 종료 |

> 전체 설정 변수 레퍼런스는 [`docs/CONFIG_REFERENCE_KO.md`](docs/CONFIG_REFERENCE_KO.md) 참고

---

## 실행 엔진(Backend) 선택

### Codex backend (기본)

필수:
- `OPENAI_API_KEY`

예시(.env):
```bash
OPENAI_API_KEY=xxxxx
```

Codex MCP 모드(기본값):
- `mcp_mode="npx"`
- `codex_package="@openai/codex@latest"`

### Claude Code backend

필수(둘 중 하나):
- Claude Code 로그인(예: `claude auth login`) **또는**
- `ANTHROPIC_API_KEY`

설치:
```bash
pip install -U claude-agent-sdk
```

Shell에서 전환:
```text
> /set execution_backend claudecode
> /save
```

스모크 테스트(선택):
```bash
python -m agent_runner.backends.claude_smoke_test --prompt "hi"
```

---

## 역할별 모델 설정

### Codex 백엔드 (GPT 모델)

| 설정 | 기본값 | 용도 |
|------|--------|------|
| `pm_model` | `gpt-5-mini` | PM 백로그 생성 |
| `dev_model` | `gpt-5.1-codex-mini` | Dev 코딩 (기본 티어) |
| `dev_model_tier1` | `gpt-5.1-codex` | Dev 에스컬레이션 1단계 |
| `dev_model_tier2` | `gpt-5.2-codex` | Dev 에스컬레이션 2단계 |
| `qa_model` | `gpt-5-mini` | QA 리뷰 |
| `reporter_model` | `gpt-5-nano` | 종료 보고서 |

### Claude 백엔드 (Claude 모델)

| 설정 | 기본값 | 권장값 | 용도 |
|------|--------|--------|------|
| `claudecode_model` | `sonnet` | — | 전체 폴백 모델 |
| `claudecode_pm_model` | `""` | `sonnet` | PM 백로그 생성 |
| `claudecode_dev_model` | `""` | `sonnet` | Dev 코딩 (기본 티어) |
| `claudecode_dev_model_tier1` | `""` | `opus` | Dev 에스컬레이션 1단계 |
| `claudecode_dev_model_tier2` | `""` | — | Dev 에스컬레이션 2단계 |
| `claudecode_qa_model` | `""` | `haiku` | QA 리뷰 (비용 절감) |
| `claudecode_reporter_model` | `""` | `haiku` | 종료 보고서 (비용 절감) |

**폴백 체인:**
```
역할별 모델 (비어있으면) → claudecode_model (비어있으면) → "sonnet"
```

**비용 최적화 예시:**
```json
{
  "claudecode_pm_model": "sonnet",
  "claudecode_dev_model": "sonnet",
  "claudecode_dev_model_tier1": "opus",
  "claudecode_qa_model": "haiku",
  "claudecode_reporter_model": "haiku"
}
```
- PM/Dev: sonnet으로 균형 잡힌 품질
- Dev 에스컬레이션: opus로 어려운 작업 처리
- QA/Reporter: haiku로 비용 절감 (읽기/요약 위주)

---

## Claude 백엔드 고급 설정

Claude Code 백엔드를 사용할 때 추가로 설정할 수 있는 고급 옵션들입니다.

### 역할별 도구(Tool) 제한

각 Stage에서 Claude가 사용할 수 있는 도구를 제한하여 안전성을 높일 수 있습니다:

```json
{
  "claudecode_pm_allowed_tools": "Read,Grep,Glob,Write,Edit",
  "claudecode_pm_disallowed_tools": "",
  "claudecode_dev_allowed_tools": "Read,Write,Edit,Grep,Glob,Bash",
  "claudecode_dev_disallowed_tools": "",
  "claudecode_qa_allowed_tools": "Read,Grep,Glob,Bash",
  "claudecode_qa_disallowed_tools": ""
}
```

| 역할 | 기본 허용 도구 | 설명 |
|------|----------------|------|
| **PM** | Read, Grep, Glob, Write, Edit | 분석 + 백로그 작성 |
| **Dev** | Read, Write, Edit, Grep, Glob, Bash | 코딩 + 쉘 실행 |
| **QA** | Read, Grep, Glob, Bash | 읽기 전용 리뷰 |

> `disallowed_tools`를 설정하면 `allowed_tools`에 있더라도 해당 도구가 차단됩니다.

### Extended Thinking (확장 사고)

Claude 모델의 내부 추론(thinking) 토큰 예산을 설정합니다:

```json
{
  "claudecode_max_thinking_tokens": 0
}
```

- `0` (기본): SDK 기본값 사용
- 양수 값: 지정된 토큰 수까지 thinking 허용
- thinking 지원 모델(opus 등)에서만 효과 있음

### 세션 관리

```json
{
  "claudecode_user": "",
  "claudecode_fork_session": false,
  "claudecode_include_partial_messages": false,
  "claudecode_setting_sources": "project,user,local"
}
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `claudecode_user` | `""` | Claude Code 사용자 식별자 |
| `claudecode_fork_session` | `false` | resume 시 새 세션 ID로 포크 (best-effort) |
| `claudecode_include_partial_messages` | `false` | 스트리밍 중간 메시지 이벤트 활성화 |
| `claudecode_setting_sources` | `"project,user,local"` | Claude Code 설정 읽기 소스 |

### 시스템 프롬프트 확장

Claude의 기본 시스템 프롬프트에 커스텀 지침을 추가합니다:

```json
{
  "claudecode_system_prompt_append": "항상 한국어로 커밋 메시지를 작성하세요."
}
```

### 파일 체크포인팅 (Beta)

```json
{
  "claudecode_enable_file_checkpointing": false
}
```

> 실험적 기능. Claude Code SDK의 파일 체크포인팅을 활성화합니다.

---

## 파이프라인(roles) 커스터마이징

기본:
- `roles="PM,Dev,QA"`

내장 Stage:
- `PM`, `Dev`, `QA`, `Security`

예시) QA를 끄고 PM→Dev만:
```text
> /set roles PM,Dev
> /save
```

예시) Security Stage까지 포함:
```text
> /set roles PM,Dev,QA,Security
> /save
```

### 플러그인 Stage 로드(고급)

`roles`에 `pkg.module:ClassName` 형태로 Stage를 추가할 수 있습니다.

보안상 기본은 차단이며, 아래 설정이 필요합니다:

- `plugins_enabled=true`
- `plugins_allowlist`에 허용 패턴 추가
- `plugins_strict=true`면 allowlist에 없으면 즉시 실패

예시(config 일부):
```json
{
  "plugins_enabled": true,
  "plugins_allowlist": ["my_pkg.*", "my_pkg.stages:MyStage"],
  "plugins_strict": true,
  "roles": "PM,Dev,my_pkg.stages:MyStage,QA"
}
```

---

## Enterprise 프로필

`profile` 설정으로 **personal**(기본) 또는 **enterprise** 모드를 선택할 수 있습니다.

```bash
# CLI
python agent_cli.py --run-now --repo <path> --profile enterprise

# Shell
> /set profile enterprise
> /save
```

### Enterprise 자동 적용 사항

| 항목 | personal (기본) | enterprise |
|------|-----------------|------------|
| **roles** | `PM,Dev,QA` | `PM,Security,Dev,QA` (Security 자동 추가) |
| **정책 스캔** | 비활성 | **자동 활성** |
| **보안 스캔** | 비활성 | **자동 활성** |
| **QA 항상 실행** | `qa_always=false` | `qa_always=true` |
| **예산 가드레일** | 사용자 설정값 | 최소값 강제 적용 (아래 참고) |

### Enterprise 예산 가드레일 강제

Enterprise 모드에서는 비용 폭주 방지를 위해 예산 한도에 **최소 상한**이 적용됩니다:

```
max_total_escalations_per_run    → min(사용자값, 5)
max_total_continuations_per_run  → min(사용자값, 5)
max_total_repair_attempts_per_run → min(사용자값, 3)
```

> 사용자가 더 높은 값을 설정하더라도 Enterprise 모드에서는 위 상한을 초과할 수 없습니다.

### 사용 시나리오

- **팀 프로젝트**: Security Stage로 보안 취약점 자동 스캔
- **CI/CD 통합**: 정책/보안 스캔 필수화로 배포 전 품질 보장
- **비용 관리**: 강제 가드레일로 예상치 못한 API 비용 방지

---

## 안전/운영 옵션 (Git, Stop, No-diff)

### Stop file로 안전 종료

- 기본 stop 파일: `STOP`
- `run_dir/STOP` 파일이 생기면 graceful stop

Shell:
```text
> /stop
> /stop --wait
```

### "변경 없음(no diff)" 정책

기본값:
- 태스크 수행 후 `git diff`가 없으면 실패로 간주하고 중단(토큰 낭비 방지)

계속 진행하려면:
```bash
python agent_cli.py --run-now --repo <path> --non-interactive --autopilot --continuous --allow-no-diff
```

### Git 체크포인트 / 롤백

**체크포인트 생성 (자동):**
- `isolate_task=true` 시 또는 에스컬레이션 활성 시
- tracked 변경사항 (`git diff HEAD --binary`) + untracked 파일 스냅샷 저장

**롤백 흐름:**
```
에스컬레이션 또는 태스크 실패 시:
  1. 구조 체크포인트(rescue) 생성 (안전망)
  2. git apply --check 로 패치 검증
  3. git reset --hard (run_dir 제외)
  4. git clean -fd (run_dir/rescue 제외)
  5. 원본 패치 적용
  6. untracked 파일 복원
```

> 기본은 **안전 모드** — 롤백 대신 `ROLLBACK_BLOCKED.md`를 남기고 중단합니다.

### Worktree 격리 모드 (권장: 안전하게 오래 돌릴 때)

```bash
python agent_cli.py --run-now --repo <path> --worktree-isolation --non-interactive --autopilot --continuous
```

```
1. git worktree add --detach (격리 복제)
2. 에이전트가 worktree 안에서만 작업
3. 성공 시: worktree.patch → 원본 repo에 적용
4. 실패/중단 시: 원본 repo 무손실, 패치만 보존
```

수동 복구:
```bash
git apply --binary --whitespace=nowarn <run_dir>/worktree.patch
# 충돌 시:
git apply --reject --whitespace=nowarn <run_dir>/worktree.patch
```

### 파괴적 롤백(비권장, 명시적으로만)

```bash
python agent_cli.py --run-now --repo <path> --dangerous-git-rollback
```

---

## 예산 가드레일 (Budget Guardrails)

API 비용 폭주를 방지하기 위해 에스컬레이션/continuation/repair 횟수에 상한을 설정합니다.

### budgets 객체

```json
{
  "budgets": {
    "max_pm_structured_retries": 3,
    "max_dev_escalations_per_task": 2,
    "max_dev_continuations_per_task": 3,
    "max_total_escalations_per_run": 10,
    "max_total_continuations_per_run": 10,
    "max_total_repair_attempts_per_run": 6
  }
}
```

### 항목별 설명

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `max_pm_structured_retries` | 3 | PM JSON 스키마 repair 최대 횟수 |
| `max_dev_escalations_per_task` | 2 | **태스크 1개**에서 모델 에스컬레이션 최대 횟수 |
| `max_dev_continuations_per_task` | 3 | **태스크 1개**에서 continuation(턴 초과 이어서 실행) 최대 횟수 |
| `max_total_escalations_per_run` | 10 | **실행 전체**에서 에스컬레이션 총 횟수 |
| `max_total_continuations_per_run` | 10 | **실행 전체**에서 continuation 총 횟수 |
| `max_total_repair_attempts_per_run` | 6 | **실행 전체**에서 PM repair 총 횟수 |

### 예산 초과 시 동작

```
예산 한도 도달
  │
  ├─ per_task 한도 → 해당 태스크만 실패 처리, 다음 태스크로 진행
  │
  └─ per_run 한도 → BudgetExceeded 예외 발생 → Reporter로 종료
```

### 비용 절감 프리셋

```json
{
  "budgets": {
    "max_dev_escalations_per_task": 1,
    "max_dev_continuations_per_task": 1,
    "max_total_escalations_per_run": 3,
    "max_total_continuations_per_run": 5,
    "max_total_repair_attempts_per_run": 2
  }
}
```

### 무제한 실행 프리셋 (주의)

```json
{
  "budgets": {
    "max_dev_escalations_per_task": 5,
    "max_dev_continuations_per_task": 5,
    "max_total_escalations_per_run": 50,
    "max_total_continuations_per_run": 50,
    "max_total_repair_attempts_per_run": 10
  }
}
```

> Enterprise 프로필에서는 per_run 한도에 최소 상한이 강제됩니다.

---

## 빌드/테스트 게이트

기본은 .NET 기준 게이트를 포함합니다.

- 끄기: `--no-build`
- 테스트 실행: `--run-tests`
- 타깃 지정:
  - `--dotnet-build-target <csproj|sln|path>`
  - `--dotnet-test-target <csproj|sln|path>`
  - `--dotnet-test-filter "<expr>"`

비-.NET 프로젝트라면:
- 우선 `--no-build`로 운용하고,
- 필요 시 config의 `build_cmd` / `test_cmd`로 커스텀 명령을 지정하세요.

```json
{
  "build_cmd": ["npm", "run", "build"],
  "test_cmd": ["npm", "test"],
  "build_timeout_seconds": 300
}
```

**게이트 실행 순서:**
```
Dev 완료 → git diff 확인 → 빌드 게이트 → 테스트 게이트 → 정책 스캔 → 보안 스캔
                 │              │              │
              no_diff?     build_failed?   test_failed?
              → 에스컬      → 에스컬        → 에스컬레이션
                레이션        레이션           또는 실패
```

---

## 정책/시크릿 스캔(옵션)

- `scan_scope="quick"` (기본)
- 상한: `scan_max_files`, `scan_timeout_seconds`, `scan_max_total_bytes`
- 제외: `scan_ignore_globs`, `scan_ignore_paths`

> 프로젝트가 커질수록 "quick → staged/full"은 신중히 올리는 것을 권장합니다.

---

## 산출물(Artifacts) 구조

### run_dir (실행 단위)

기본:
- `REPO/.doc/agent_runs/<YYYYMMDD-HHMMSS>/`

```
run_dir/
  ├─ BACKLOG.json          # PM이 생성한 태스크 목록 (권위 소스)
  ├─ BACKLOG.md            # 사람이 읽을 수 있는 체크리스트
  ├─ STATE.json            # 완료(done)/실패(failed)/경고(warnings) 기록
  ├─ PM_OUTPUT_cycle_*.json # 스키마 검증된 PM 원본 출력
  ├─ NOTES_PM.md           # PM 참고사항/경고
  ├─ metrics.jsonl         # 이벤트 로그 (JSONL)
  ├─ SHUTDOWN_REPORT.md    # 종료 요약 보고서
  ├─ SHUTDOWN_CONTEXT.json # 종료 시 컨텍스트
  ├─ tasks/                # 태스크별 디렉토리
  │   └─ T1/
  │       ├─ attempt_00/   # 시도별 로그
  │       ├─ checkpoint/   # git 체크포인트
  │       ├─ build.log     # 빌드 결과
  │       └─ test.log      # 테스트 결과
  ├─ dev_logs/             # Dev 로그 누적
  ├─ dev_hints/            # Dev 분석 힌트 (글로벌 changelog)
  └─ qa_final_output_*.txt # QA 결과
```

### STATE.json 구조

```json
{
  "done": ["T1", "T2"],
  "failed": [
    {"task": "T3", "reason": "build_failed", "detail": "CS1234 에러..."}
  ],
  "warnings": [
    {"task": "T4", "reason": "max_turns_exceeded", "detail": "..."}
  ]
}
```

### PM_CACHE (지속 분석 아티팩트)

기본:
- `REPO/.doc/PM_CACHE/`

대표 파일:
- `PROJECT_ANALYSIS.md` — 프로젝트 구조/기술스택/현황 분석
- `REPO_INVENTORY.json|md` — 파일 목록/메타데이터
- `REPO_SNAPSHOT.json` — repo fingerprint

---

## 트러블슈팅 (문제 상황 및 해결)

### 1. API 키/인증 문제

#### `OPENAI_API_KEY is not set.`

**원인:** `.env` 파일 미발견 또는 환경변수 미설정
**해결:**
```bash
# 방법 1: .env 경로 명시
python agent_cli.py --run-now --repo <path> --env-file "/path/to/.env"

# 방법 2: 환경변수 직접 설정
export OPENAI_API_KEY=sk-xxxxx  # Linux/Mac
set OPENAI_API_KEY=sk-xxxxx     # Windows
```

#### Claude 인증 실패

**원인:** `claude-agent-sdk` 미설치, 로그인 만료, API 키 미설정
**해결:**
```bash
pip install -U claude-agent-sdk
claude auth login                 # 또는
export ANTHROPIC_API_KEY=sk-ant-xxxxx
```

### 2. 할당량 소진 (Quota Exhausted)

**증상:** 실행 중 갑자기 중단, `SHUTDOWN_REPORT.md`에 `quota_exhausted` 기록
**감지 키워드:** `insufficient_quota`, `exceeded your current quota`, `usage limit`, `billing hard limit`

**해결:**
```
방법 1: API 과금 플랜 확인 및 한도 증대
방법 2: 페일오버 설정으로 자동 백엔드 전환
```
```json
{
  "failover_enabled": true,
  "failover_backends": ["codex", "claudecode"],
  "failover_on": ["quota_exhausted"],
  "failover_max_switches": 1
}
```

### 3. PM 구조화 출력 파싱 실패

**증상:** PM이 JSON 대신 일반 텍스트를 반환, `[PM] Structured parse failed` 로그

**자동 복구 흐름:**
```
1차: repair 프롬프트로 재시도 (최대 pm_structured_retries회)
2차: BACKLOG.json 파일이 이미 있으면 파일에서 로드
3차: P0 폴백 백로그 생성 ("PM_FAILURE.md 작성" 단일 태스크)
```

**수동 해결:**
- `pm_structured_retries` 값 증가 (기본 2)
- 프롬프트 튜닝: `prompts_dir/pm_instructions.md` 수정
- `pm_bootstrap_max_turns` / `pm_incremental_max_turns` 증가 (PM에게 더 많은 턴 허용)

### 4. BACKLOG가 비어있어서 중단 (`no_tasks`)

**증상:** Dev 단계에서 `no_tasks`로 즉시 종료
**원인:** PM이 빈 태스크 목록을 생성했거나 모든 태스크가 이미 완료

**해결:**
- 레포에 목표/할 일을 더 명확히 기술 (README, 이슈 등)
- `/todo --save`로 오늘 할 일을 TODO로 만들어 PM에게 전달
- `pm_refresh_backlog=true`로 매 사이클 백로그 재생성 강제

### 5. Dev가 코드를 변경하지 않음 (no_diff)

**증상:** `[Dev] No diff detected` 후 태스크 실패 처리

**자동 복구:**
- `dev_escalate_on`에 `no_diff` 포함 시 → 상위 모델로 에스컬레이션
- `dev_auto_escalate=true` + `dev_max_escalations > 0` 필요

**수동 해결:**
```bash
# 방법 1: no_diff를 허용
--allow-no-diff

# 방법 2: 에스컬레이션 설정
{
  "dev_auto_escalate": true,
  "dev_max_escalations": 2,
  "dev_escalate_on": ["no_diff", "build_failed", "test_failed"]
}
```

### 6. 빌드/테스트 실패 후 무한 루프

**증상:** 에스컬레이션 반복 후에도 계속 빌드 실패

**보호 장치 (이미 내장):**
```
태스크당 최대 에스컬레이션: budgets.max_dev_escalations_per_task (기본 2)
전체 실행 에스컬레이션 총량: budgets.max_total_escalations_per_run (기본 10)
전체 실행 continuation 총량: budgets.max_total_continuations_per_run (기본 10)
```

**해결:**
- 빌드 명령이 올바른지 확인: `build_cmd`, `test_cmd` 점검
- 빌드 게이트 자체를 끄고 진행: `--no-build`
- 예산 한도 조정:
```json
{
  "budgets": {
    "max_dev_escalations_per_task": 1,
    "max_total_escalations_per_run": 3
  }
}
```

### 7. MaxTurnsExceeded (턴 초과)

**증상:** `[Dev] Max turns exceeded` — 에이전트가 제한 턴 안에 작업 미완료

**자동 복구:** Continuation으로 이어서 실행 (최대 `dev_max_turns_continuations`회)

**수동 해결:**
```json
{
  "max_turns_per_task": 20,
  "dev_max_turns_continuations": 3,
  "claudecode_max_turns": 48
}
```

### 8. 롤백 차단 (Rollback Blocked)

**증상:** `ROLLBACK_BLOCKED.md` 파일 생성, `[STOP] Rollback blocked` 로그

**원인:** `dangerous_git_rollback=false` (기본 안전 모드)에서 롤백 시도

**해결:**
```
방법 1 (권장): worktree 격리 모드 사용 — 원본 repo 보호
  --worktree-isolation

방법 2 (주의): 파괴적 롤백 허용
  --dangerous-git-rollback
```

### 9. `npx`를 찾을 수 없음

**원인:** Node.js 미설치
**해결:** Node.js 설치 후 `npx -v` 확인

### 10. Claude SDK 스트림 오류

**증상:** `ClaudeSDKClient does not provide a message stream`

**원인:** `claude-agent-sdk` 버전 불일치/구버전
**해결:**
```bash
pip install -U claude-agent-sdk
```

### 11. 타임아웃

**증상:** 특정 단계에서 시간 초과 후 종료

**관련 설정 및 기본값:**

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `pm_timeout_seconds` | 900 (15분) | PM 단계 |
| `dev_timeout_seconds` | 900 (15분) | Dev 단계 |
| `mcp_timeout_seconds` | 120 (2분) | MCP 서버 통신 |
| `test_timeout_seconds` | 3600 (1시간) | 테스트 실행 |
| `build_timeout_seconds` | 1800 (30분) | 빌드 실행 |

**해결:** 필요에 따라 타임아웃 값 증가

### 12. 모델 인식 실패 (model_not_found)

**증상:** `model_not_found`, `does not exist`, `unknown model` 에러

**원인:** 설정한 모델 이름이 API에서 지원하지 않는 이름
**해결:**
- Codex: `gpt-5-mini`, `gpt-5.1-codex-mini`, `gpt-5.1-codex`, `gpt-5.2-codex` 등 확인
- Claude: `sonnet`, `opus`, `haiku` 중 선택
- 에스컬레이션이 활성화되어 있으면 다음 티어 모델로 자동 시도

### 13. 예산 초과 (BudgetExceeded)

**증상:** `[STOP] BudgetExceeded` 로그, 남은 태스크가 있지만 실행 중단

**원인:** 에스컬레이션/continuation/repair 횟수가 `budgets` 한도를 초과

**자동 동작:**
```
per_task 한도 초과 → 해당 태스크만 실패, 다음 태스크 진행
per_run 한도 초과  → BudgetExceeded 예외 → Reporter 종료
```

**해결:**
```json
{
  "budgets": {
    "max_dev_escalations_per_task": 3,
    "max_total_escalations_per_run": 15,
    "max_total_continuations_per_run": 15
  }
}
```

> Enterprise 프로필에서는 per_run 한도에 최소 상한이 강제됩니다. 이 경우 프로필을 `personal`로 변경하거나 한도 내에서 운영하세요.

### 14. Continuation 소진 (턴 초과 반복)

**증상:** `[Dev] MaxTurnsExceeded` 반복 후 태스크 실패, continuation이 남아있지 않음

**원인:** 태스크가 너무 크거나, max_turns가 너무 작음

**자동 복구 흐름:**
```
MaxTurnsExceeded 발생
  │
  ├─ per_task continuations 남아있음
  │    → "[CONTINUE]" 프롬프트로 이어서 실행
  │    → 부분 진행 보존 (git diff 유지)
  │
  ├─ per_task continuations 소진 + per_run 한도 내
  │    → 현재까지 변경사항으로 게이트 진행
  │
  └─ per_run continuations 한도 초과
       → BudgetExceeded → 실행 중단
```

**해결:**
```json
{
  "max_turns_per_task": 30,
  "dev_max_turns_continuations": 5,
  "claudecode_max_turns": 64,
  "budgets": {
    "max_dev_continuations_per_task": 5,
    "max_total_continuations_per_run": 20
  }
}
```

> 태스크를 작게 분할하는 것이 턴/continuation을 늘리는 것보다 효과적입니다.

### 15. Failover 전환 실패

**증상:** Failover가 활성화되어 있지만 백엔드 전환이 일어나지 않음

**원인 및 해결:**

| 원인 | 해결 |
|------|------|
| stop_reason이 `failover_on`에 없음 | `failover_on` 목록에 해당 사유 추가 |
| `failover_max_switches` 소진 | 값 증가 (기본 1) |
| 다음 백엔드 Preflight 실패 | 대상 백엔드의 API 키/SDK 사전 설치 |
| 이미 마지막 백엔드 | `failover_backends`에 백엔드 추가 |

```json
{
  "failover_enabled": true,
  "failover_backends": ["codex", "claudecode"],
  "failover_on": ["quota_exhausted"],
  "failover_max_switches": 2
}
```

> `/doctor`로 모든 백엔드의 환경을 사전 점검하세요.

### 16. Plugin Stage 로딩 실패

**증상:** `[Pipeline] Plugin blocked` 또는 `[Pipeline] Plugin load failed`

**원인:** 플러그인이 allowlist에 없거나, 모듈을 찾을 수 없음

**해결:**
```json
{
  "plugins_enabled": true,
  "plugins_allowlist": ["my_pkg.*", "my_pkg.stages:MyStage"],
  "plugins_strict": true
}
```

| 체크 포인트 | 설명 |
|-------------|------|
| `plugins_enabled` | `true`여야 플러그인 로드 시도 |
| `plugins_allowlist` | 패턴이 모듈 경로와 일치해야 함 |
| `plugins_strict` | `true`면 차단 시 즉시 실패, `false`면 경고만 |
| 모듈 경로 | Python import path 형식 (`pkg.module:ClassName`) |

### 17. Worktree 패치 충돌

**증상:** `[Worktree] Patch apply failed` — worktree에서 작업 성공했지만 원본 repo에 패치 적용 실패

**원인:** worktree 작업 중 원본 repo에 다른 변경이 생김

**해결:**
```bash
# 패치 파일 위치 확인
ls <run_dir>/worktree.patch

# 방법 1: 강제 적용 (reject 파일 생성)
git apply --reject --whitespace=nowarn <run_dir>/worktree.patch

# 방법 2: 3-way 머지 시도
git apply --3way --whitespace=nowarn <run_dir>/worktree.patch

# 방법 3: 수동 적용
# .rej 파일들을 확인하고 수동으로 충돌 해결
```

> Worktree 패치는 `<run_dir>/worktree.patch`에 항상 보존됩니다. 원본 repo는 무손실입니다.

### 18. Preflight 체크 실패

**증상:** 실행 시작 전 에러 메시지와 함께 즉시 종료

**원인별 해결:**

| 에러 | 원인 | 해결 |
|------|------|------|
| `repo path does not exist` | 잘못된 경로 | `--repo` 경로 확인 |
| `not a git repository` | git 미초기화 | `git init` 실행 |
| `OPENAI_API_KEY is not set` | 환경변수 없음 | `.env` 또는 환경변수 설정 |
| `npx not found` | Node.js 미설치 | Node.js 설치 |
| `claude-agent-sdk not installed` | SDK 미설치 | `pip install -U claude-agent-sdk` |
| `build tool not found` | .NET/커스텀 도구 없음 | 도구 설치 또는 `--no-build` |

```bash
# 전체 환경 진단
python agent_cli.py --repo <path>
> /doctor
```

### 19. Config 버전 마이그레이션

**증상:** 이전 버전 config 로드 시 일부 설정이 기본값으로 리셋됨

**원인:** `config_version=1` → `2` 자동 마이그레이션 시 다음 값이 변경될 수 있음:
- `dev_max_turns_continuations`: `0` → `2` (기본값)
- `pm_max_turns_continuations`: `0` → `1` (기본값)

**해결:**
```bash
# config 파일 확인
> /config --all

# 필요 시 명시적으로 값 재설정
> /set dev_max_turns_continuations 0
> /set pm_max_turns_continuations 0
> /save
```

> `config_version` 값을 직접 수정하지 마세요. 마이그레이션은 자동으로 처리됩니다.

### 20. 스캔 제한 초과

**증상:** `[Scan] Skipped: max files/bytes exceeded` — 정책/보안 스캔이 부분적으로만 실행됨

**원인:** 프로젝트 크기가 스캔 한도를 초과

**관련 설정:**

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `scan_scope` | `"quick"` | 스캔 범위: quick/staged/full |
| `scan_max_files` | (코드 내 기본) | 최대 스캔 파일 수 |
| `scan_max_total_bytes` | (코드 내 기본) | 최대 스캔 총 바이트 |
| `scan_timeout_seconds` | (코드 내 기본) | 스캔 타임아웃 |
| `scan_ignore_globs` | `[".doc/**", "*.log"]` | 제외 패턴 |

**해결:**
```json
{
  "scan_scope": "quick",
  "scan_ignore_globs": [".doc/**", "*.log", "node_modules/**", "dist/**"],
  "policy_scan_scope": "staged",
  "security_scan_scope": "quick"
}
```

> `full` 스캔은 대규모 프로젝트에서 매우 느릴 수 있습니다. `quick` → `staged` → `full` 순서로 점진적으로 올리세요.

### 21. PM이 자기참조 태스크 생성

**증상:** 백로그에 "백로그 작성", "프로젝트 분석" 같은 메타 태스크가 포함됨

**원인:** PM 에이전트가 자기 역할을 태스크로 위임하려 함

**자동 복구:**
- 내장 필터가 "백로그 생성", "분석 작성" 등의 자기참조 키워드를 자동 제거
- 태스크 ID 정규화: `T1`, `T2`, ... 형식 강제
- 스킬 ID 검증: `SKILLS_INDEX`와 대조, 없는 스킬 경고

**수동 해결:**
- PM 프롬프트 튜닝: `prompts_dir/pm_instructions.md`에 "실제 코딩 태스크만 출력" 지침 강화
- `pm_structured_retries` 증가 (repair 프롬프트가 자기참조도 교정 시도)

### 22. .env 파일 로딩 문제

**증상:** 환경변수가 인식되지 않음

**원인:** AgentCLI는 **레포 내 `.env`를 의도적으로 로드하지 않음** (보안상 설계)

**.env 로딩 우선순위:**
```
1. --env-file <경로>     (명시적 지정, 최우선)
2. AGENTCLI_HOME/.env    (AgentCLI 홈 디렉토리)
3. 시스템 환경변수        (OS 레벨)
```

**해결:**
```bash
# 방법 1: 명시적 경로
python agent_cli.py --run-now --repo <path> --env-file "C:/secrets/.env"

# 방법 2: AgentCLI 홈에 .env 배치
# (AGENTCLI_HOME 또는 AgentCLI 설치 디렉토리)
```

### 종료 사유 (Stop Reason) 우선순위

| 우선순위 | 사유 | 설명 |
|----------|------|------|
| 1 | `quota_exhausted` | API 할당량 소진 |
| 2 | `stop_file` | STOP 파일 감지 |
| 3 | `all_tasks_done` | 모든 백로그 태스크 완료 |
| 4 | `prepared_only` | continuous 미설정, 백로그만 준비 |
| 5 | `idle_exit` | loop 모드에서 유휴 타임아웃 |
| 6 | `ok` | 정상 종료 |

---

## 추천 운용 프리셋

### A) 백로그만 준비(최소 비용 스모크)
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot
```

### B) 태스크 실행(최대 5개)
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot --continuous --iterations 5
```

### C) 밤새 루프(아이들 타임아웃 포함)
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot \
  --loop --loop-sleep-seconds 60 --loop-idle-exit-after 3600 --loop-max-cycles 20
```

### D) Claude 백엔드 + 비용 최적화
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot --continuous \
  --execution-backend claudecode \
  --claudecode-dev-model sonnet \
  --claudecode-dev-model-tier1 opus \
  --claudecode-qa-model haiku \
  --claudecode-reporter-model haiku
```

### E) 안전 최우선 (worktree 격리 + 빌드 게이트)
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot --continuous \
  --worktree-isolation --run-tests \
  --build-cmd "npm,run,build" --test-cmd "npm,test"
```

---

## 프롬프트/문서/스킬

### 프롬프트 템플릿 커스터마이징

기본 프롬프트는 **Python-side prompts_dir**에 저장됩니다(레포 내부가 기본이 아님).

- 기본 prompts_dir: `AGENTCLI_HOME/prompts/<repo-slug>-<hash>/`

템플릿 생성(1회):
```bash
python agent_cli.py --run-now --repo "<path>" --init-prompts
```

> 생성 후에는 prompts_dir의 `pm_instructions.md`, `dev_instructions.md` 등을 수정해 튜닝할 수 있습니다.

### Docs 읽기(Digest) — 토큰 절약

기본값:
- `docs_read_mode="digest"`
- `docs_dir=".doc/Docs"`
- `docs_digest_file=".doc/DOCS_DIGEST.md"`

Digest 생성/갱신(로컬 작업, 토큰 사용 없음):
```bash
python agent_cli.py --run-now --repo "<path>" --generate-digest
```

### Skills 시스템 (Codex/Claude 공통)

AgentCLI는 스킬 폴더를 스캔해 `SKILLS_INDEX` 요약을 만들어 **PM/QA에 인라인(발췌)** 할 수 있습니다.
Dev에는 스킬 본문을 길게 인라인하지 않는 방향으로 설계되어 있습니다(토큰 방어).

config 예시(핵심):
```json
{
  "skills": {
    "enabled": true,
    "roots": [
      "~/.agents/skills",
      "~/.claude/skills",
      "{repo}/Skills"
    ],
    "snapshot_dir": ".doc/skills",
    "inline_mode": "qa",
    "max_excerpt_lines": 12
  }
}
```

---

## Preflight 체크 & 환경 검증

### Preflight 체크 (자동 실행)

러너가 실행되기 전, 아래 항목을 자동으로 검증합니다:

```
┌─ Preflight Checks ────────────────────────────────────────┐
│                                                           │
│  1. repo 경로                                             │
│     ├─ 디렉토리 존재 여부                                 │
│     └─ git 저장소 초기화 (.git 존재)                      │
│                                                           │
│  2. 백엔드별 필수 조건                                    │
│     ├─ Codex:                                             │
│     │   ├─ OPENAI_API_KEY 설정 여부                       │
│     │   ├─ npx 실행 가능 여부 (mcp_mode=npx일 때)         │
│     │   └─ openai-agents 패키지 설치 여부                 │
│     └─ Claude:                                            │
│         ├─ claude-agent-sdk 설치 여부                     │
│         └─ ANTHROPIC_API_KEY 또는 claude 로그인 여부       │
│                                                           │
│  3. 빌드/테스트 도구 (no_build=false일 때)                 │
│     ├─ .NET SDK (기본) 또는 커스텀 build_cmd 실행 가능    │
│     └─ test_cmd 실행 가능 (run_tests=true일 때)           │
│                                                           │
│  4. run_dir 준비                                          │
│     ├─ 신규 생성: REPO/.doc/agent_runs/<timestamp>/       │
│     └─ resume_latest: 가장 최근 run_dir 재사용            │
│                                                           │
│  5. .env 로딩                                             │
│     ├─ --env-file 경로 (명시적)                           │
│     └─ AGENTCLI_HOME/.env (폴백)                          │
│     ※ 레포 내 .env는 의도적으로 로드하지 않음             │
└───────────────────────────────────────────────────────────┘
```

> Preflight 실패 시 에러 메시지와 함께 즉시 종료됩니다. `/doctor`로 사전 점검을 권장합니다.

### Failover (backend 체인)

Codex 사용량 제한(quota/usage limit) 등 특정 사유로 중단될 때, 다른 backend로 자동 전환할 수 있습니다.

```json
{
  "failover_enabled": true,
  "failover_backends": ["codex", "claudecode"],
  "failover_on": ["quota_exhausted"],
  "failover_max_switches": 1
}
```

**Failover 동작 흐름:**

```
Backend #1 (codex) 실행
  │
  ├─ 정상 종료 → 끝
  │
  └─ stop_reason이 failover_on에 해당
       │
       ├─ failover_max_switches 남아있음
       │    → Backend #2 (claudecode)로 전환
       │    → Preflight 재검증 (API 키, SDK 등)
       │    → 성공 시 실행 계속
       │    → 실패 시 최종 종료
       │
       └─ max_switches 소진
            → 최종 종료
```

**할당량 소진 감지 키워드:**
- `insufficient_quota`, `quota exceeded`, `exceeded your current quota`
- `billing hard limit`, `hard limit`, `payment required`
- `usage limit`, `plan and billing`

> Failover는 **환경이 사전에 준비**되어야 성공합니다. 양쪽 백엔드 모두 `/doctor`로 점검하세요.

### /doctor (환경 진단)

Shell에서 `/doctor`를 실행하면 run_dir에 진단 보고서(`DOCTOR.md`)가 생성됩니다.

```text
> /doctor
```

**진단 항목:**

| 카테고리 | 검사 내용 |
|----------|-----------|
| **런타임** | Python 버전, Node.js/npx 설치, .NET SDK |
| **API 인증** | OPENAI_API_KEY, ANTHROPIC_API_KEY, claude 로그인 |
| **SDK** | openai-agents, claude-agent-sdk 설치 여부 |
| **경로** | repo, config, prompts_dir, run_dir 유효성 |
| **빌드 도구** | build_cmd/test_cmd 실행 가능 여부 |
| **Git** | 저장소 상태, worktree 지원 여부 |

---

## 보안 메모

- 시크릿/토큰은 절대 README/config/prompt에 하드코딩하지 말고 **환경변수 또는 .env**로만 주입하세요.
- worktree 패치(`worktree.patch`)는 변경 내용을 포함합니다. 외부 공유 전 민감정보 포함 여부를 점검하세요.

---

## 개발/테스트

단위 테스트(있는 경우):
```bash
python -m pytest -q
```
