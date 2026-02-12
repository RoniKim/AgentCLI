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
5. [처음부터 실행까지 (Step-by-Step)](#처음부터-실행까지-step-by-step-세팅-가이드)
6. [빠른 시작 (요약)](#빠른-시작-요약)
7. [설정(Config) 관리](#설정config-관리)
8. [실행 엔진(Backend) 선택](#실행-엔진backend-선택)
9. [역할별 모델 설정](#역할별-모델-설정)
10. [Claude 백엔드 고급 설정](#claude-백엔드-고급-설정)
11. [파이프라인 커스터마이징](#파이프라인roles-커스터마이징)
12. [Enterprise 프로필](#enterprise-프로필)
13. [안전/운영 옵션](#안전운영-옵션-git-stop-no-diff)
14. [예산 가드레일](#예산-가드레일-budget-guardrails)
15. [빌드/테스트 게이트](#빌드테스트-게이트)
16. [정책/시크릿 스캔](#정책시크릿-스캔옵션)
17. [산출물 구조](#산출물artifacts-구조)
18. [트러블슈팅 (문제 상황 및 해결)](#트러블슈팅-문제-상황-및-해결)
19. [추천 운용 프리셋](#추천-운용-프리셋)
20. [프롬프트/문서/스킬](#프롬프트문서스킬)
21. [TODO 기능 (사용자 우선순위 주입)](#todo-기능-사용자-우선순위-주입)
22. [GOALS 기능 (프로젝트 완성 기준)](#goals-기능-프로젝트-완성-기준)
23. [Preflight 체크 & 환경 검증](#preflight-체크--환경-검증)
24. [보안 메모](#보안-메모)
25. [외부 프롬프트 작성 가이드](#외부-프롬프트-작성-가이드)
26. [스킬 파일 작성법](#스킬-파일-작성법)
27. [태스크 히스토리 (Cross-Run)](#태스크-히스토리-cross-run)
28. [메트릭스 & 로깅](#메트릭스--로깅)
29. [프로세스 안전 (Process Guard)](#프로세스-안전-process-guard)
30. [QA 후속 태스크 시스템](#qa-후속-태스크-시스템)
31. [Shutdown Report 시스템](#shutdown-report-시스템)
32. [개발자 가이드 (확장)](#개발자-가이드-확장)

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

**테스트 태스크 검증 (필수):**
- PM이 유닛 테스트 태스크를 생성할 때, 테스트 프로젝트의 타겟 프레임워크/패키지 참조를 확인
- 테스트에서 참조하는 타입이 테스트 프로젝트에서 접근 가능한지 검증
- 플랫폼 API(MAUI Connectivity 등)에 의존하는 서비스는 플랫폼 독립적 로직만 테스트하도록 안내
- 모킹 프레임워크(Moq 등) 설치 여부를 가정하지 않고 .csproj 확인

**백로그 정규화:**
- 메타 위임 방지: "백로그 생성", "분석 작성" 같은 PM 자기참조 태스크 자동 필터링
- ID 안정성: `T1`, `T2`, ... 형식 강제
- 스킬 검증: `SKILLS_INDEX`와 대조, 없는 스킬 ID 경고

### Dev 단계 (태스크 실행)

**Dev 에이전트 핵심 규칙:**
- **API pre-read (필수)**: 기존 메서드/속성/컴포넌트를 사용하기 전 반드시 정의를 읽어 시그니처를 확인. 이름, 파라미터 순서, 반환 타입을 가정하지 않음.
- **Tooling**: `apply_patch` 우선, 타겟 검색(`rg`/`git ls-files`) 사용, 광범위 스캔 금지.
- **Dependency**: 패키지 설치 금지 — 필요 시 `DEPENDENCY_REQUIRED.md` 작성 후 중단.

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
- 기본: 매 Cycle 실행 (`qa_always=true`가 기본값)
- `qa_always=false`로 설정 시, Dev가 코드를 변경한 Cycle에서만 실행

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

## 처음부터 실행까지 (Step-by-Step 세팅 가이드)

처음 사용하는 분을 위한 **전체 세팅 → 첫 실행 → 결과 확인** 가이드입니다.

### Step 1: 사전 준비

```bash
# 1-1. Python 3.10+ 확인
python --version   # Python 3.10 이상이어야 합니다

# 1-2. Git 확인
git --version

# 1-3. AgentCLI 의존성 설치
cd <AgentCLI 디렉토리>
pip install -U -r requirements.txt
```

### Step 2: API 키 설정

사용할 백엔드에 맞는 API 키를 준비합니다.

**방법 A: `.env` 파일 생성 (권장)**

AgentCLI 디렉토리(또는 `AGENTCLI_HOME`)에 `.env` 파일을 만듭니다:

```env
# Codex 백엔드 (OpenAI) 사용 시
OPENAI_API_KEY=sk-xxxxxxxxxxxx

# Claude 백엔드 사용 시 (Codex 대신 또는 failover용)
ANTHROPIC_API_KEY=sk-ant-xxxxxxxxxxxx
```

**방법 B: 환경변수 직접 설정**

```bash
# Windows
set OPENAI_API_KEY=sk-xxxxxxxxxxxx

# Linux/Mac
export OPENAI_API_KEY=sk-xxxxxxxxxxxx
```

> `.env` 파일은 **AgentCLI 홈 디렉토리**에 위치해야 합니다. 대상 레포 안의 `.env`는 보안상 의도적으로 로드하지 않습니다.

### Step 3: 백엔드별 추가 설치

**Codex 백엔드 (기본값):**
```bash
# Node.js + npx 필요 (MCP 모드)
node --version   # 확인
npx --version    # 확인
```

**Claude 백엔드:**
```bash
pip install -U claude-agent-sdk

# 인증 (API 키 없이 사용할 경우)
claude auth login
```

### Step 4: 환경 검증

```bash
python agent_cli.py --repo "<대상 프로젝트 경로>"
```

Shell이 열리면:

```text
> /doctor
```

`/doctor`가 모든 항목을 통과하면 준비 완료입니다. 실패 항목이 있으면 안내에 따라 해결하세요.

### Step 5: 첫 실행

**방법 1: Interactive Shell (권장 — 처음에는 이 방법을 추천)**

```bash
python agent_cli.py --repo "<대상 프로젝트 경로>"
```

```text
# 현재 설정 확인
> /config

# .NET이 아닌 프로젝트면 빌드 게이트 끄기
> /set no_build true

# 실행 시작 (PM이 백로그 생성 → Dev가 태스크 실행 → QA가 리뷰)
> /start --autopilot --continuous

# 실행 상태 확인
> /status

# 안전하게 중지 (현재 태스크 완료 후 종료)
> /stop --wait

# Shell 종료
> /exit
```

**방법 2: 무인 실행 (CI/CD, 밤새 운용)**

```bash
python agent_cli.py --run-now --repo "<대상 프로젝트 경로>" \
  --non-interactive --autopilot --continuous
```

### Step 6: 결과 확인

실행 후 대상 레포의 `.doc/agent_runs/<timestamp>/` 디렉토리에 산출물이 생성됩니다:

```
.doc/agent_runs/20260210-143000/
  ├─ BACKLOG.json          ← PM이 생성한 태스크 목록
  ├─ STATE.json            ← 완료/실패 태스크 기록
  ├─ SHUTDOWN_REPORT.md    ← 실행 종료 요약
  └─ dev_logs/             ← 태스크별 Dev 실행 로그
```

실제 코드 변경은 `git log`로 확인:

```bash
cd <대상 프로젝트 경로>
git log --oneline -10
```

### Step 7: 프롬프트 커스터마이징 (선택)

프로젝트에 맞게 PM/Dev/QA 프롬프트를 튜닝하려면:

```bash
# 기본 프롬프트 템플릿 생성
python agent_cli.py --run-now --repo "<경로>" --init-prompts
```

생성된 파일 위치: `<AgentCLI>/prompts/<repo-slug>-<hash>/`

| 파일 | 역할 |
|------|------|
| `pm_instructions.md` | PM 에이전트 지시문 (어떤 태스크를 만들지) |
| `dev_instructions.md` | Dev 에이전트 지시문 (어떻게 코딩할지) |
| `qa_instructions.md` | QA 에이전트 지시문 (어떻게 리뷰할지) |
| `pm_bootstrap_prompt.md` | PM 첫 실행 프롬프트 템플릿 |
| `pm_incremental_prompt.md` | PM 반복 실행 프롬프트 템플릿 |
| `dev_task_prompt.md` | Dev 태스크 프롬프트 템플릿 |
| `qa_prompt.md` | QA 프롬프트 템플릿 |

### 자주 쓰는 실행 시나리오

| 시나리오 | 명령 |
|----------|------|
| 백로그만 미리보기 (코드 변경 없음) | `python agent_cli.py --run-now --repo "<경로>" --non-interactive --autopilot` |
| 태스크 5개만 실행 | `--continuous --iterations 5` 추가 |
| Claude로 실행 | `--execution-backend claudecode` 추가 |
| 밤새 루프 | `--loop --loop-max-cycles 20 --loop-sleep-seconds 60` 추가 |
| Worktree 격리 (안전) | `--worktree-isolation` 추가 |
| 빌드 게이트 끄기 | `--no-build` 추가 |

### 최소 config JSON 예시

설정을 파일로 관리하려면 `<AgentCLI>/configs/<repo-slug>-<hash>.json`을 직접 만들거나, Shell에서 `/set` + `/save`를 사용합니다:

```json
{
  "config_version": 2,
  "repo": "C:/Dev/MyProject",
  "execution_backend": "claudecode",
  "no_build": true,
  "continuous": true,
  "autopilot": true,
  "claudecode_dev_model": "sonnet",
  "claudecode_dev_model_tier1": "opus",
  "claudecode_qa_model": "haiku"
}
```

> 전체 설정 변수 레퍼런스는 [`docs/CONFIG_REFERENCE_KO.md`](docs/CONFIG_REFERENCE_KO.md) 참고

---

## 빠른 시작 (요약)

### 1) Interactive Shell (권장: 설정 확인 후 시작)

```bash
python agent_cli.py --repo "C:/Dev/BudgetBook"
```

Shell에서:

```text
> /doctor                            # 환경 점검
> /config                            # 설정 확인
> /start --autopilot --continuous    # 실행
> /status                            # 상태 확인
> /stop --wait                       # 안전 중지
> /exit                              # 종료
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
| `/doctor` | 환경 진단 (Git, API 키, Backend, Skills, DB, Goals, Docs 등 15개 항목) |
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
| **QA 항상 실행** | `qa_always=true` | `qa_always=true` |
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

## TODO 기능 (사용자 우선순위 주입)

### 개요

TODO는 사용자가 작성한 **오늘의 우선순위/작업 목록**을 PM 에이전트에 **최우선 컨텍스트**로 주입하는 기능입니다.
PM이 백로그를 생성할 때 TODO 내용을 가장 먼저 반영하므로, "이번 Cycle에서 꼭 이것만 해줘"를 지정할 수 있습니다.

### 저장 위치

```
REPO/.doc/todo/
  ├─ LAST_TODO.txt            # 현재 활성 TODO 포인터
  ├─ Today_<hash>.md          # 오늘 날짜 기반 TODO 파일 (날짜+repo 해시)
  └─ (이전 TODO 파일들...)
```

- 파일명: `Today_<sha1(repo+날짜)[:10]>.md` — 동일 repo에서 하루 1개
- `LAST_TODO.txt`가 현재 활성 TODO를 가리킴 (상대 경로)
- 없으면 `.doc/todo/*.md` 중 최신 수정 파일을 자동 선택

### Shell 명령어

| 명령어 | 설명 |
|--------|------|
| `/todo` | 현재 TODO 미리보기 (상위 40줄) |
| `/todo --save` | 오늘의 TODO 생성 + 활성화 + OS 기본 에디터로 열기 |
| `/todo --load <path>` | 특정 TODO 파일을 활성화 |
| `/todo --load latest` | 가장 최근 수정된 TODO를 활성화 |

### 사용 흐름

```
1. /todo --save              ← 오늘의 TODO 파일 생성, 에디터 열림
2. (에디터에서 우선순위/작업 작성 후 저장)
3. /start                    ← 러너 실행 → PM이 TODO를 최우선으로 반영
```

### TODO 파일 기본 템플릿

```markdown
# TODO (Today)

- created_at: 2026-02-10T15:00:00
- repo: D:\MyProject

## Priorities

- [ ] (write the most important goal)

## Tasks

- [ ]
- [ ]

## Notes

-
```

### PM 프롬프트 주입 방식

TODO 내용은 PM의 백로그 생성 프롬프트에 다음과 같이 주입됩니다:

```
User TODO (highest priority; if present, reflect into backlog tasks):
{todo_block}
```

- **"highest priority"** — PM은 TODO 항목을 다른 소스(repo 분석, 이전 백로그 등)보다 우선 반영
- TODO가 없으면 `(none)`이 삽입되어 PM이 자체 판단으로 백로그 생성
- 최대 12,000자 / 120줄까지 전달 (초과 시 자동 truncate)
- Codex, Claude 양쪽 백엔드 모두 동일하게 지원

### TODO만 돌릴 때 Config 설정

"TODO에 적은 작업만 빠르게 처리"하려면 iterations를 낮추고, TODO에 집중 지시를 적습니다:

```json
{
  "repo": "D:\\MyProject",
  "execution_backend": "claudecode",
  "continuous": true,
  "iterations": 3,
  "pm_refresh_interval": 1,
  "qa_always": true
}
```

| 키 | 권장값 | 이유 |
|----|--------|------|
| `iterations` | `1`~`5` | TODO 항목 수에 맞게 최소 Cycle만 실행 |
| `continuous` | `true` | iterations 횟수만큼 자동 반복 |
| `pm_refresh_interval` | `1` | 매 Cycle마다 PM이 TODO를 다시 읽어 반영 |
| `qa_always` | `true` | 매 Cycle QA 검증 실행 |

**실행 예시:**

```bash
# 1. TODO 작성
python agent_cli.py
> /repo D:\MyProject
> /todo --save
# (에디터에서 작업 목록 작성)

# 2. iterations=3으로 짧게 실행
> /set iterations 3
> /set continuous true
> /start
```

또는 CLI에서 직접:

```bash
python agent_cli.py --run-now --repo D:\MyProject --iterations 3 --continuous
```

> **Tip**: TODO에 `## Priorities` 섹션에 "이 작업만 처리하고 종료"처럼 명시하면 PM이 해당 작업만 백로그로 생성합니다.

---

## GOALS 기능 (프로젝트 완성 기준)

### 개요

GOALS는 **"프로젝트가 언제 완성인지"를 정의하는 수렴 조건**입니다.
TODO가 "오늘 뭐 해"라면, GOALS는 "이것들이 되면 완성이다"입니다.

| | TODO | GOALS |
|--|------|-------|
| 성격 | 일일 작업 지시 | 프로젝트 완성 기준 |
| 수명 | 하루 | 프로젝트 전체 |
| 없을 때 | PM이 자체 판단 | PM이 초안 자동 생성 |
| 체크박스 | 사용자가 수동 관리 | 시스템이 자동 체크 |

### 저장 위치

```
REPO/.doc/GOALS.md
```

### GOALS.md 형식

```markdown
# Project Goals

> Auto-generated by AgentCLI PM.
> 사용자 검토 후 수정하세요. 이후 Cycle은 이 파일을 기준으로 완성도를 평가합니다.

## P0 (Must-Have)
- [ ] 가계부 입출금 CRUD 동작
- [ ] 월별 대시보드 정상 렌더링
- [ ] 빌드 성공 (Android + Windows)
- [ ] 런타임 크래시 없음

## P1 (Should-Have)
- [ ] 다크 모드
- [ ] 카테고리별 분석 차트

## Completion Criteria
- 모든 P0 항목 [x] 완료
- 빌드 게이트 통과
- 실패 후 미처리 태스크 0개
```

**핵심 규칙:**
- **P0 (Must-Have)**: 전부 체크 완료 = 프로젝트 완성
- **P1 (Should-Have)**: 있으면 좋지만 완성 판단에 영향 없음
- `## Completion Criteria`: 사용자 정의 추가 조건 (참고용)

### 동작 흐름

```
[GOALS.md 없이 시작]
  Cycle 1 → PM이 repo 분석 후 GOALS.md 초안 자동 생성
  → "GOALS.md 초안을 생성했습니다. 검토/수정 후 재실행하세요."

[GOALS.md 있을 때]
  매 Cycle:
    1. PM이 GOALS.md 읽고, 미완료 P0 항목을 태스크로 변환
    2. Dev가 태스크 실행
    3. 태스크 완료 시 → GOALS.md 체크박스 자동 [x] 업데이트
    4. Completion Evaluator:
       - P0 전부 [x] + 실패 미재시도 0개 → project_complete 신호 → 자동 종료
       - 아직 미완료 → 다음 Cycle 계속
```

### 자동 체크박스 업데이트

태스크가 완료되면 시스템이 GOALS.md의 관련 항목을 자동으로 `[x]`로 체크합니다.

```
태스크 완료: "가계부 입출금 CRUD 구현"
  → GOALS.md에서 "가계부 입출금 CRUD 동작" 항목 매칭
  → - [x] 가계부 입출금 CRUD 동작  (자동 체크)
```

매칭 방식: 태스크 제목/설명의 키워드와 GOALS 항목의 키워드를 비교 (60% 이상 일치 시 체크)

### 완성 판단 (`project_complete`)

```
project_complete = (P0 전부 [x]) AND (실패 후 미재시도 태스크 == 0)
```

- `project_complete` 신호 발생 시 → loop 모드여도 **자동 종료**
- `COMPLETION_STATUS.json`이 run_dir에 생성됨:

```json
{
  "generated_at": "2026-02-10T15:00:00",
  "project_complete": true,
  "goals": {
    "p0_total": 4, "p0_done": 4,
    "p1_total": 2, "p1_done": 1,
    "unmet_p0": [], "unmet_p1": ["카테고리별 분석 차트"]
  },
  "failed_tasks_unresolved": 0
}
```

### 실패 태스크 강제 재시도

GOALS와 함께 도입된 **실패 태스크 강제 재시도** 메커니즘:

PM 프롬프트에서 실패 태스크가 완료 태스크와 분리되어 별도 섹션으로 전달됩니다:

```
Completed tasks (do NOT re-create):
- [DONE] T01: CRUD 구현 (2026-02-10)

FAILED TASKS — MANDATORY RETRY (MUST address each one):
- [FAIL/build_failed 3/3] T03: validation 추가 (2026-02-10)
  failure_detail: CS1061 - AccountService.GetAll() 존재하지 않음
```

PM은 실패 태스크를 **반드시** 다른 접근법으로 재생성하거나, 불가능한 경우 `open_questions`에 사유를 기재해야 합니다.

### Config 설정

```json
{
  "goals_enabled": true,
  "goals_auto_generate": true,
  "goals_auto_check": true
}
```

| 키 | 기본값 | 설명 |
|----|--------|------|
| `goals_enabled` | `true` | GOALS 기능 전체 활성화 |
| `goals_auto_generate` | `true` | GOALS.md 없을 때 PM이 초안 자동 생성 |
| `goals_auto_check` | `true` | 태스크 완료 시 GOALS.md 체크박스 자동 업데이트 |

### Stop Reason 추가

| stop_reason | 의미 |
|-------------|------|
| `project_complete` | P0 전부 달성 + 실패 미재시도 0개 → 프로젝트 완성 |
| `all_tasks_done` | 현재 백로그 태스크 전부 완료 (프로젝트 완성과는 별개) |

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
- `usage limit`, `plan and billing`, `spend limit`, `monthly spend limit`
- Claude 특화: `usage cap`, `reached your`, `token limit exceeded`, `account limit`

> Failover는 **환경이 사전에 준비**되어야 성공합니다. 양쪽 백엔드 모두 `/doctor`로 점검하세요.

### /doctor (환경 진단)

Shell에서 `/doctor`를 실행하면 run_dir에 진단 보고서(`DOCTOR.md`)가 생성됩니다.

```text
> /doctor
```

**진단 항목 (14개):**

| # | 카테고리 | 검사 내용 | 상세 |
|---|----------|-----------|------|
| 1 | **Git** | `git --version`, 레포 `is-inside-work-tree` | 버전 출력, git 레포 여부 |
| 2 | **Config** | config JSON 로드 | 경로 + 파싱 성공 여부 |
| 3 | **run_dir** | 쓰기 테스트 (임시파일 생성→삭제) | 경로 + 쓰기 가능 여부 |
| 4 | **API 인증** | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` 환경변수 | set 여부 (True/False) |
| 5 | **프로필/정책** | profile, policy enabled, security enabled | 현재 설정값 |
| 6 | **Backend Preflight** | 각 백엔드별 `run_preflight()` | OK/FAIL + 이슈 상세 |
| 7 | **빌드 도구** | `build_cmd`, `test_cmd` 첫 실행파일 | `shutil.which()` 검증 |
| 8 | **Prompts 디렉토리** | `resolve_prompts_dir()` 경로 존재 여부 | override .md 파일 개수 |
| 9 | **Skills 시스템** | `skills.enabled` 시 roots 존재, SKILL.md 발견 수 | 경고: enabled인데 0개 |
| 10 | **Task History** | `task_history_enabled` 시 SQLite DB 접근 | `query_history()` 호출 |
| 11 | **Goals** | `goals_enabled` 시 GOALS.md 존재/파싱 | P0/P1 완료 현황 (done/total) |
| 12 | **TODO** | `.doc/todo` 디렉토리 + 오늘 TODO 내용 | has content / empty |
| 13 | **Docs Digest** | `docs_read_mode`, docs_dir .md 파일 수 | digest 파일 존재/크기 |
| 14 | **Process Guard** | Windows Job Object 활성 상태 | 초기화 여부 (비-Windows: N/A) |
| 15 | **Claude SDK** | `claudecode` 백엔드 사용 시 import 검증 | `claude_code_sdk` 버전 |

> **참고**: 항목 9-15는 해당 기능이 활성화되었거나 관련 백엔드를 사용할 때만 표시됩니다.

**출력 예시:**

```text
# Doctor report

- git version: git version 2.43.0.windows.1
- repo is git: True
- config load: OK (C:\Users\USER\.agentcli\configs\MyProject-a1b2c3d4.json)
- run_dir writable: OK (D:\MyProject\runs\20260212-143000)
- OPENAI_API_KEY set: True
- ANTHROPIC_API_KEY set: True
- profile: personal
- policy enabled: False
- security enabled: False
- backend preflight:
  - codex: OK
  - claudecode: OK
- build command executable: dotnet -> True
- test command executable: dotnet -> True
- prompts_dir: OK (C:\Users\USER\.agentcli\prompts\MyProject-a1b2c3d4, 3 overrides)
- skills.enabled: True
  - roots configured: 3, existing: 1
  - skills discovered: 12
- task_history_enabled: True
  - db query: OK (history accessible)
- goals_enabled: True
  - GOALS.md: found (P0: 3/5, P1: 2/8)
- todo: OK (has content)
- docs_read_mode: digest
  - docs_dir: OK (D:\MyProject\.doc\Docs, 7 .md files)
  - digest file: OK (DOCS_DIGEST.md, 4521 bytes)
- process_guard: Job Object active
- claude_code_sdk: OK (v0.1.12)
```

보고서는 `{run_dir}/DOCTOR.md`에도 저장됩니다.

---

## 보안 메모

- 시크릿/토큰은 절대 README/config/prompt에 하드코딩하지 말고 **환경변수 또는 .env**로만 주입하세요.
- worktree 패치(`worktree.patch`)는 변경 내용을 포함합니다. 외부 공유 전 민감정보 포함 여부를 점검하세요.

---

## 외부 프롬프트 작성 가이드

AgentCLI의 프롬프트는 **기본 내장 템플릿**과 **프로젝트별 외부 오버라이드**로 구성됩니다. 이 섹션은 외부 프롬프트를 작성하는 방법을 상세히 설명합니다.

### PromptStore 동작 원리

```
PromptStore.render(name, default, ctx)
  │
  ├─ 1) prompts_dir/{name}.md 파일이 존재하고 비어있지 않으면 → 파일 내용 사용
  │
  └─ 2) 없으면 → default (코드 내장 템플릿) 사용
       │
       ▼
  format_map(ctx) → {variable} 치환
       │
       ▼
  append_pm_output_contract() → JSON 스키마 계약 자동 추가 (PM만)
       │
       ▼
  append_pm_essential_context() → 필수 런타임 블록 자동 추가 (PM만)
```

**핵심 규칙:**
- 외부 파일은 **완전 대체** — 기본 템플릿과 합치지(merge) 않음
- 누락된 `{variable}`은 경고만 출력하고 리터럴 그대로 남음 (`_SafeDict`)
- 필수 런타임 블록(done/failed tasks, goals, build warnings)은 **프로그래밍적으로 자동 주입** — 외부 템플릿에서 빼먹어도 러너가 자동으로 붙여줌

### 오버라이드 가능 파일 목록

| 파일명 | 역할 | 적용 대상 |
|--------|------|-----------|
| `pm_instructions.md` | PM 에이전트 시스템 지시문 | 모든 PM 호출 |
| `pm_bootstrap_prompt.md` | PM 첫 실행 프롬프트 | Bootstrap 모드 |
| `pm_incremental_prompt.md` | PM 반복 실행 프롬프트 | Incremental/Refresh 모드 |
| `dev_instructions.md` | Dev 에이전트 시스템 지시문 | 모든 Dev 호출 |
| `dev_task_prompt.md` | Dev 태스크 실행 프롬프트 | 태스크별 |
| `qa_instructions.md` | QA 에이전트 시스템 지시문 | 모든 QA 호출 |
| `qa_prompt.md` | QA 검증 프롬프트 | Cycle별 |

### 외부 프롬프트 생성/위치

```bash
# 기본 템플릿을 외부 파일로 복사 (1회)
python agent_cli.py --run-now --repo "<경로>" --init-prompts
```

생성 위치: `{AGENTCLI_HOME}/prompts/<repo-slug>-<hash>/`

```
prompts/
├── BudgetBook-69084820/
│   ├── pm_instructions.md
│   ├── pm_bootstrap_prompt.md
│   ├── pm_incremental_prompt.md
│   ├── dev_instructions.md
│   ├── dev_task_prompt.md
│   ├── qa_instructions.md
│   └── qa_prompt.md
└── argos_ai-cdc5165b/
    └── ...
```

### 템플릿 변수 레퍼런스

#### PM Bootstrap 프롬프트 변수

| 변수 | 설명 | 예시 값 |
|------|------|---------|
| `{analysis_md}` | PROJECT_ANALYSIS.md 경로 | `.doc/PM_CACHE/PROJECT_ANALYSIS.md` |
| `{inv_md}` | REPO_INVENTORY.md 경로 | `.doc/PM_CACHE/REPO_INVENTORY.md` |
| `{repo}` | 레포 루트 경로 | `D:\Dev\BudgetBook` |
| `{run_dir}` | 실행 산출물 폴더 경로 | `.doc/agent_runs/20260212-140000` |
| `{todo_block}` | 사용자 TODO 내용 | `## Priorities\n- 로그인 구현` 또는 `(none)` |
| `{docs_dir}` | Docs 폴더 경로 | `.doc/Docs` 또는 `(none)` |
| `{docs_read_mode}` | Docs 읽기 모드 | `digest` |
| `{digest_rel}` | Docs 다이제스트 상대 경로 | `.doc/DOCS_DIGEST.md` |
| `{skills_index_summary}` | 스킬 인덱스 요약 | `- blazor_ui: Blazor UI 패턴 [blazor, ui]` |
| `{codex_call_hint}` | Codex MCP 호출 힌트 | `{"approval_policy": "..."}` |
| `{task_history_block}` | 이전 실행 태스크 이력 | `- [DONE] T01: CRUD 구현 (2026-02-10)` |

#### PM Incremental 프롬프트 변수 (Bootstrap 변수 + 아래 추가)

| 변수 | 설명 |
|------|------|
| `{prev_head}` | 이전 Git HEAD SHA |
| `{curr_head}` | 현재 Git HEAD SHA |
| `{changed_files_block}` | 변경된 파일 목록 (`git diff --name-only`) |
| `{current_backlog_block}` | 현재 백로그 상태 (`[x]=완료, [ ]=대기, [F]=실패`) |
| `{hint_block}` | Dev 분석 힌트 (dev_hints/*.md 내용) |

#### Dev 태스크 프롬프트 변수

| 변수 | 설명 |
|------|------|
| `{repo}` | 레포 루트 경로 |
| `{run_dir}` | 실행 산출물 폴더 경로 |
| `{task_id}` | 태스크 ID (T1, T2, ...) |
| `{task_title}` | 태스크 제목 |
| `{task_prompt}` | 태스크 구현 지침 |
| `{files_hint}` | 변경 대상 파일 목록 (시작점) |
| `{skills_context}` | 선택된 스킬 발췌문 |
| `{done_when}` | 완료 조건 |
| `{docs_read_mode}` | Docs 읽기 모드 |
| `{digest_rel}` | Docs 다이제스트 경로 |
| `{analysis_hint_out}` | 분석 힌트 출력 경로 |
| `{codex_call_hint}` | Codex MCP 호출 힌트 |

#### QA 프롬프트 변수

| 변수 | 설명 |
|------|------|
| `{repo}` | 레포 루트 경로 |
| `{run_dir}` | 실행 산출물 폴더 경로 |
| `{skills_context}` | 태스크별 스킬 컨텍스트 |

### 자동 주입 블록 (프로그래밍적)

외부 템플릿에 아래 블록이 없어도 러너가 **자동으로 프롬프트 끝에 추가**합니다. 중복 방지를 위해 HTML 마커로 체크합니다.

| 블록 | 마커 | 대상 | 설명 |
|------|------|------|------|
| **턴 예산 경고** | `<turn_budget_warning>` | PM | PM이 JSON 출력 없이 턴을 소진하지 않도록 경고 |
| **프로젝트 Goals** | `<pm_goals>` | PM | GOALS.md 내용 + 완성 기준 평가 지침 |
| **완료 태스크** | `<pm_done_tasks>` | PM | 이미 완료된 태스크 목록 (중복 생성 방지) |
| **빌드 경고** | `<pm_build_warnings>` | PM | 최신 빌드 경고 (CS8602, CS4014 등) |
| **실패 태스크** | `<pm_failed_tasks>` | PM | MANDATORY RETRY — 실패 태스크 + Dev 로그 tail |
| **출력 계약** | `<pm_output_contract>` | PM | JSON 스키마 계약 (PMOutputV2) |
| **태스크 사이징** | `<pm_task_sizing_rules>` | PM | 3-7 태스크 규칙, 번들링 지침 |

**중요**: 이 블록들은 ctx dict에 넣는 템플릿 변수(`{variable}`)가 아닙니다. 러너가 템플릿 렌더링 **이후에** 프로그래밍적으로 append합니다. 외부 프롬프트에서 해당 내용을 직접 작성할 필요가 없습니다.

### 실전 예제: 프로젝트별 PM 프롬프트

**`pm_incremental_prompt.md` 예시 (BudgetBook 프로젝트):**

```markdown
You are Planner/PM.

INCREMENTAL MODE (token-saving):
- Global analysis already exists at: {analysis_md}
- Do NOT redo full analysis.

Reference file list: {inv_md}

Git:
- prev_head: {prev_head}
- curr_head: {curr_head}
- changed files: {changed_files_block}

Current backlog: {current_backlog_block}

Dev hints: {hint_block}

SKILLS_INDEX summary:
{skills_index_summary}

## 프로젝트 특화 지침

- BudgetBook은 .NET MAUI Blazor Hybrid 앱입니다
- Razor 컴포넌트의 CancellationToken 패턴을 항상 확인하세요
- 안드로이드/Windows 동시 빌드 호환성 유지

User TODO:
{todo_block}

Rules:
- FILE PATHS: REPO_INVENTORY.md의 전체 경로만 사용
- 태스크는 atomic하게, 각각 git diff를 생산해야 함
- 질문이 있으면 open_questions에 기재

When editing files, call Codex MCP with {codex_call_hint}.

Now execute: update PROJECT_ANALYSIS.md, then respond ONLY with the JSON schema object.
```

**주의사항:**
- `{goals_block}`, `{done_tasks_block}`, `{failed_tasks_block}`, `{turn_budget_warning}`, `{build_warnings_block}` — 이 변수들은 **사용하지 마세요**. ctx에 포함되지 않으며 자동 주입됩니다.
- `{codex_call_hint}`는 Codex 백엔드에서 JSON 승인 정책을 주입합니다. Claude 백엔드에서는 자동으로 Claude 도구 지침으로 대체됩니다.

### 프롬프트 작성 시 주의사항

1. **ctx에 없는 변수를 쓰면**: 리터럴 그대로 남음 (예: `{my_custom_var}` → 출력에 `{my_custom_var}` 표시). `_SafeDict`가 stderr에 경고만 출력.

2. **JSON 중괄호 이스케이프**: 프롬프트에 JSON 예시를 넣을 때 `{{`, `}}`로 이스케이프해야 합니다.
   ```
   올바른 예: {{"kind": "pm_output_v2", "tasks": [...]}}
   잘못된 예: {"kind": "pm_output_v2"} → kind를 변수로 인식 시도
   ```

3. **외부 프롬프트는 완전 대체**: 기본 템플릿의 일부만 수정하려면, `--init-prompts`로 전체 복사 후 원하는 부분만 수정하세요.

4. **PM 출력 스키마는 자동 보장**: `pm_instructions.md`에 JSON 스키마를 직접 넣지 않아도, `ensure_pm_instructions_have_output_schema()`가 자동으로 추가합니다.

5. **Claude 백엔드 차이**: Claude 백엔드에서는 `_patch_prompt_for_claude()` 함수가 Codex 특화 지시문(예: `apply_patch` 참조)을 Claude 도구 참조(Read, Write, Edit, Grep, Glob, Bash)로 자동 변환합니다.

---

## 스킬 파일 작성법

### 스킬 파일 구조

스킬은 **Markdown 파일 (`SKILL.md`)**로 작성합니다. 파일명은 반드시 `SKILL.md`여야 합니다.

```
~/.agents/skills/
├── blazor/
│   └── SKILL.md        ← blazor/SKILL.md
├── dotnet-test/
│   └── SKILL.md        ← dotnet-test/SKILL.md
└── react/
    ├── hooks/
    │   └── SKILL.md    ← react/hooks/SKILL.md
    └── SKILL.md        ← react/SKILL.md
```

### Frontmatter 형식

```markdown
---
name: Blazor State Management
description: Blazor Hybrid 앱의 상태 관리 패턴
tags: [blazor, state, maui, dependency-injection]
---

# Blazor State Management

## 핵심 패턴

1. Scoped Service 사용
...
```

**지원 필드:**

| 필드 | 필수 | 설명 | 폴백 |
|------|------|------|------|
| `name` (또는 `title`) | 아니오 | 스킬 이름 | 디렉토리명 사용 |
| `description` (또는 `desc`) | 아니오 | 한 줄 설명 | 첫 번째 heading 또는 첫 비어있지 않은 줄 |
| `tags` | 아니오 | 태그 배열 | 빈 배열 |

**태그 형식 (두 가지 모두 지원):**
```yaml
# 인라인
tags: [blazor, state, maui]

# 리스트
tags:
  - blazor
  - state
  - maui
```

### 스킬 ID 생성 규칙

```
skill_id = "{relative_path}#{sha1(source_root::relative_path)[:10]}"
```

예: `blazor/SKILL.md#a1b2c3d4e5`

### 인덱싱 및 PM 연동

1. **인덱싱**: 러너 시작 시 `skills_index.json` 자동 생성
2. **PM 요약**: PM 프롬프트에 `{skills_index_summary}` 변수로 요약 전달
3. **PM 선택**: PM이 태스크별로 `skills` 필드에 skill_id를 지정
4. **Dev/QA 발췌**: 선택된 스킬의 본문을 발췌(최대 `max_excerpt_lines`줄)하여 프롬프트에 인라인

### 퍼지 매칭 (Auto-fix)

PM이 존재하지 않는 skill_id를 참조하면:
- `difflib.SequenceMatcher`로 유사도 비교 (skill_id, name, path 3가지 대상)
- 상위 3개 후보를 자동 제안
- `skill_match_autofix=true` + `skill_match_autofix_threshold` 초과 시 자동 교정

### Config 참조

```json
{
  "skills": {
    "enabled": true,
    "roots": ["~/.agents/skills", "{repo}/Skills"],
    "snapshot_dir": ".doc/skills",
    "inline_mode": "qa",
    "max_excerpt_lines": 12,
    "pm_summary_max_items": 30,
    "pm_summary_max_chars": 4000,
    "qa_max_total_chars": 8000,
    "skill_match_autofix": true,
    "skill_match_autofix_threshold": 0.5
  }
}
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `inline_mode` | `"qa"` | 스킬 발췌 인라인 대상: `qa`, `pm`, `both`, `none` |
| `max_excerpt_lines` | 12 | 스킬 발췌 최대 줄 수 |
| `qa_max_total_chars` | 8000 | QA 스킬 컨텍스트 총 글자 수 상한 |

---

## 태스크 히스토리 (Cross-Run)

### 개요

태스크 히스토리는 **실행(run)을 넘어 SQLite로 영구 보존**되는 태스크 결과 기록입니다. PM이 이전 실행에서의 성공/실패를 참고하여 더 나은 백로그를 생성할 수 있게 합니다.

### 저장 위치

```
{AGENTCLI_HOME}/databases/{repo-slug}.db
```

SQLite WAL 모드, `busy_timeout=5000ms`.

### 스키마

```sql
CREATE TABLE task_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id      TEXT NOT NULL,       -- T1, T2, ...
    title        TEXT NOT NULL,       -- 태스크 제목
    status       TEXT NOT NULL,       -- DONE / FAIL
    reason       TEXT DEFAULT '',     -- 실패 사유 (no_diff, build_failed, ...)
    detail       TEXT DEFAULT '',     -- 상세 (최대 500자)
    files        TEXT DEFAULT '[]',   -- 관련 파일 JSON 배열
    cycle_idx    INTEGER DEFAULT 0,   -- 사이클 번호
    attempt      INTEGER DEFAULT 0,   -- 시도 번호
    max_attempts INTEGER DEFAULT 1,   -- 최대 시도 횟수
    run_id       TEXT DEFAULT '',     -- 실행 ID (타임스탬프)
    backend      TEXT DEFAULT '',     -- codex / claudecode
    recorded_at  TEXT NOT NULL        -- ISO 기록 시각
);
```

### PM 프롬프트 주입

히스토리는 두 형태로 PM에 전달됩니다:

**1) `{task_history_block}` (통합 이력):**
```
- [DONE] T01: CRUD 구현 (2026-02-10)
- [FAIL/build_failed 2/3] T03: validation 추가 (2026-02-10) — CS1061 에러
```

**2) 분리 블록 (자동 주입):**
- `<pm_done_tasks>` — 완료 태스크만
- `<pm_failed_tasks>` — 실패 태스크 + 마지막 Dev 로그 tail (8줄)

### Config

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `task_history_enabled` | `true` | 히스토리 기능 활성화 |
| `task_history_max_items` | `50` | PM에 전달할 최대 항목 수 |

---

## 메트릭스 & 로깅

### 로그 파일 구조

```
run_dir/
└── logs/
    ├── run.log           # INFO+ 메시지 (항상 생성)
    ├── debug.log         # DEBUG+ 메시지 (debug=true일 때)
    ├── error.log         # ERROR만
    └── events.jsonl      # 구조화 이벤트 (JSONL)
```

### events.jsonl 형식

```json
{"ts": "2026-02-12T14:00:00.000Z", "type": "cycle_start", "cycle": 1}
{"ts": "2026-02-12T14:00:05.000Z", "type": "pm_start", "cycle": 1, "kind": "bootstrap"}
{"ts": "2026-02-12T14:01:00.000Z", "type": "pm_end", "cycle": 1, "kind": "bootstrap", "rc": 0}
{"ts": "2026-02-12T14:01:01.000Z", "type": "task_start", "task_id": "T1", "attempt": 0}
{"ts": "2026-02-12T14:03:00.000Z", "type": "gate_result", "gate": "build", "task_id": "T1", "passed": true}
{"ts": "2026-02-12T14:03:30.000Z", "type": "task_end", "task_id": "T1", "success": true}
{"ts": "2026-02-12T14:03:31.000Z", "type": "phantom_completion_detected", "task_id": "T2"}
{"ts": "2026-02-12T14:05:00.000Z", "type": "runner_stop", "reason": "all_tasks_done"}
```

### metrics.jsonl 형식

```
run_dir/metrics.jsonl
```

별도의 메트릭스 파일로, 이벤트 단위로 기록:

```json
{"ts": "...", "type": "pm_start", "cycle": 1, "kind": "bootstrap"}
{"ts": "...", "type": "pm_end", "cycle": 1, "rc": 0}
{"ts": "...", "type": "dev_attempt", "task_id": "T1", "attempt": 0, "model": "sonnet"}
{"ts": "...", "type": "escalation", "task_id": "T1", "from": "sonnet", "to": "opus"}
```

### StructuredLogger 주요 메서드

| 메서드 | 설명 |
|--------|------|
| `info(msg)` | 정보 로그 |
| `error(msg, exc=, context=)` | 에러 로그 (traceback 포함) |
| `task_start(task_id, title, attempt)` | 태스크 시작 이벤트 |
| `task_end(task_id, success, reason)` | 태스크 종료 이벤트 |
| `cycle_start(cycle_idx)` | 사이클 시작 |
| `cycle_end(cycle_idx, rc, reason, done, total)` | 사이클 종료 |
| `stage_event(stage, event, cycle)` | PM/Dev/QA 단계 이벤트 |
| `gate_event(gate, task_id, passed)` | 빌드/테스트 게이트 결과 |
| `budget_event(event)` | 예산 이벤트 |
| `quota_event(action)` | 할당량 이벤트 |

---

## 프로세스 안전 (Process Guard)

### 4-Layer 보호 체계

AgentCLI는 자식 프로세스(Codex CLI, Claude Code CLI 등)가 **부모 종료 후에도 남아있는 문제(orphan process)**를 방지하기 위해 4층 보호 체계를 사용합니다.

```
┌─ Layer 1: Windows Job Object (KILL_ON_JOB_CLOSE) ─────────┐
│  OS 레벨 자동 정리. 부모 프로세스 종료 시 모든 자식 즉시 종료 │
│  Job 핸들은 프로세스 수명 동안 유지                          │
└────────────────────────────────────────────────────────────┘
┌─ Layer 2: PID 추적 + atexit ──────────────────────────────┐
│  정상 종료/미처리 예외 시 graceful cleanup                  │
│  _tracked_pids (set) + RLock 보호                          │
└────────────────────────────────────────────────────────────┘
┌─ Layer 3: Signal Handlers ─────────────────────────────────┐
│  SIGINT / SIGTERM / SIGBREAK 수신 시 자식 프로세스 종료     │
│  terminate_all_children() 호출                              │
└────────────────────────────────────────────────────────────┘
┌─ Layer 4: Startup Orphan Cleanup ──────────────────────────┐
│  이전 실행에서 남은 고아 프로세스 감지/정리                  │
│  tasklist 기반 (signal handler에서는 호출되지 않음)          │
└────────────────────────────────────────────────────────────┘
```

### 주요 함수

| 함수 | 설명 |
|------|------|
| `init_process_guard()` | Layer 1~4 초기화 (runner_entry.py에서 호출) |
| `register_pid(pid)` | 자식 프로세스 PID 등록 |
| `unregister_pid(pid)` | 자식 프로세스 PID 해제 |
| `terminate_all_children()` | 등록된 모든 자식 프로세스 종료 |

### 스레드 안전성

- 모든 변경 가능 상태는 `RLock`으로 보호 (재진입 안전)
- Signal handler에서도 `terminate_all_children()` 안전 호출 가능
- Job Object 핸들은 의도적으로 프로세스 수명 동안 열려있음 (조기 닫힘 방지)

---

## QA 후속 태스크 시스템

### 개요

QA가 코드 리뷰 후 발견한 문제를 **자동으로 백로그 태스크로 변환**하는 기능입니다.

### 활성화

```json
{
  "qa_to_backlog": true,
  "max_qa_followups": 5
}
```

### 동작 흐름

```
QA 에이전트 실행
  │
  ├─ 리뷰 결과 텍스트 생성
  │
  └─ qa_to_backlog=true 일 때:
       │
       ├─ QA_FOLLOWUPS_OUTPUT_CONTRACT 스키마에 따라 JSON 출력 요구
       │
       ├─ 파싱 성공:
       │    followups → 백로그에 QA-FU-{hash} ID로 병합
       │    (중복 방지: 동일 title + files 조합 → 기존 항목 유지)
       │
       └─ 파싱 실패:
            텍스트 리뷰만 저장, 백로그 변경 없음
```

### QA 후속 태스크 스키마

```json
{
  "kind": "qa_followups_v1",
  "cycle": 3,
  "followups": [
    {
      "title": "TransactionEntry null check 추가",
      "prompt": "SaveAsync()에서 SelectedAccount null 체크 추가 (line 234)",
      "files": ["Pages/TransactionEntry.razor"],
      "severity": "high"
    }
  ],
  "notes": "전반적으로 안정적이나 null-safety 보강 필요"
}
```

### PM과의 연동

QA 후속 태스크는 다음 사이클의 PM이 백로그를 생성할 때 자동으로 포함됩니다. PM이 새 태스크 목록을 생성해도 기존 `QA-FU-*` 태스크는 완료되지 않은 한 유지됩니다.

---

## Shutdown Report 시스템

### 보고서 생성 흐름

```
파이프라인 종료 감지
  │
  ├─ 1) SHUTDOWN_CONTEXT 수집 (collect_shutdown_context)
  │    └─ repo 상태, 백로그 진행률, 마지막 태스크, 로그 tail 등
  │
  ├─ 2) 로컬 폴백 보고서 생성 (build_local_shutdown_report)
  │    └─ 토큰 소비 없이 즉시 생성 (항상 성공)
  │    └─ SHUTDOWN_REPORT.md에 기록
  │
  ├─ 3) Reporter 에이전트로 보고서 작성 시도 (best-effort)
  │    └─ 성공 시: 폴백 보고서 덮어쓰기
  │    └─ 실패 시: 폴백 보고서 유지 (토큰 부족 등)
  │
  └─ 4) 중복 감지 (Fix 4)
       └─ PM이 보고서를 반복 생성하는 경우, half-content 비교로 중복 제거
```

### SHUTDOWN_CONTEXT 수집 항목

| 항목 | 설명 |
|------|------|
| `git_head` | 현재 HEAD SHA |
| `git_porcelain` | `git status --porcelain` 출력 |
| `state` | STATE.json (done/failed/warnings) |
| `tasks_total` / `tasks_done` | 태스크 진행률 |
| `backlog_lines` | 백로그 미리보기 ([x]/[ ] 표시) |
| `latest_dev_log_tail` | 마지막 Dev 로그 120줄 |
| `build_log_tail` | 마지막 빌드 로그 120줄 |
| `test_log_tail` | 마지막 테스트 로그 120줄 |
| `policy_scan_summary` | 정책 스캔 결과 요약 |
| `todo_text` | 현재 TODO 내용 |

### 비상 보고서 (Emergency)

예기치 않은 예외로 정상 종료가 불가능한 경우:

```
EMERGENCY_SHUTDOWN.md 생성
  ├─ 기존 SHUTDOWN_REPORT.md가 있으면 → 건너뜀
  └─ 없으면 → 최소한의 상태 정보로 보고서 생성
```

---

## 개발자 가이드 (확장)

### 커스텀 Stage 추가

1. `Stage` ABC를 상속:

```python
from agent_runner.pipeline.stages.base import Stage, StageOutcome

class MyCustomStage(Stage):
    name = "MyCustom"

    async def run(self, session, cycle_idx: int) -> StageOutcome:
        # session.repo, session.run_dir, session.args 접근 가능
        # session.data (dict) 로 다른 Stage와 데이터 공유

        try:
            # 커스텀 로직
            result = await my_custom_logic(session.repo)

            if result.success:
                return StageOutcome.ok("custom_done")
            else:
                return StageOutcome.fail("custom_failed", rc=1, detail=str(result.error))

        except Exception as ex:
            return StageOutcome.fail("custom_error", rc=1, detail=str(ex))
```

2. Config에 등록:

```json
{
  "plugins_enabled": true,
  "plugins_allowlist": ["my_stages.*"],
  "roles": "PM,Dev,my_stages:MyCustomStage,QA"
}
```

### StageOutcome 반환값

| 메서드 | 의미 | 파이프라인 동작 |
|--------|------|----------------|
| `StageOutcome.ok(reason)` | 성공 | 다음 Stage 진행 |
| `StageOutcome.skip(reason)` | 건너뜀 | 다음 Stage 진행 |
| `StageOutcome.stop(reason, rc)` | 즉시 중단 | 파이프라인 종료 |
| `StageOutcome.fail(reason, rc)` | 실패 | 파이프라인 종료 |

> `STOP_REASON_ALL_TASKS_DONE`, `STOP_REASON_PROJECT_COMPLETE`는 `StageOutcome.ok()`로 반환하여 QA Stage까지 실행 후 종료합니다. `STOP_REASON_QUOTA`, `STOP_REASON_STOP_FILE`은 `StageOutcome.stop()`으로 즉시 종료합니다.

### 커스텀 Backend 추가

1. `AbstractAgentRunner` 상속:

```python
from agent_runner.backends.base import AbstractAgentRunner

class MyRunner(AbstractAgentRunner):
    name = "my_backend"

    async def run(self, args, repo) -> int:
        # 0: 성공, 1: 실패
        ...
        return 0
```

2. `backends/factory.py`에 등록:

```python
def get_runner(backend: str) -> AbstractAgentRunner:
    if backend == "my_backend":
        from .my_runner import MyRunner
        return MyRunner()
    ...
```

### PM 분석 캐시

**위치**: `REPO/.doc/PM_CACHE/`

| 파일 | 설명 |
|------|------|
| `PROJECT_ANALYSIS.md` | 프로젝트 구조/기술스택 분석 (PM이 유지) |
| `REPO_INVENTORY.json` | 파일 목록 메타데이터 |
| `REPO_INVENTORY.md` | 사람이 읽을 수 있는 파일 트리 |
| `REPO_SNAPSHOT.json` | repo fingerprint (변경 감지용) |

**변경 감지 (fingerprint)**:
```
repo_fingerprint = git_head + working_tree_hash
                    │
├─ fingerprint 동일 → PM Skip (백로그 재사용)
├─ fingerprint 다름 → PM Incremental (변경분만 업데이트)
└─ PROJECT_ANALYSIS.md 없음 → PM Bootstrap (전체 분석)
```

**ChangeLog 자동 축적**:

Dev가 태스크를 완료하면 분석 힌트(`dev_hints/*.md`)가 생성됩니다. 이 힌트들은 `PROJECT_ANALYSIS.md`의 `## ChangeLog` 섹션에 자동으로 merge됩니다:

```markdown
## ChangeLog (auto-appended)

- [2026-02-12T14:00:00] HEAD=abc1234
  - hint: dev_hints/T1_crud_impl.md
    ```
    Added CRUD operations to TransactionService.cs
    New files: Pages/TransactionEntry.razor
    ```
```

---

## 개발/테스트

단위 테스트(있는 경우):
```bash
python -m pytest -q
```
