# AgentCLI — CLI기반 Multi-Agent Runner (PM → Dev → QA)

개인 개발자가 **켜두고**, 나중에 **PR 수준의 변경(코드/테스트/문서)** 을 받는 것을 목표로 만든 **CLI 기반 멀티 에이전트 러너**입니다.

- 기본 파이프라인: **PM(백로그 생성) → Dev(구현) → QA(점검/피드백)**
- 실행 엔진(backend): **Codex(OpenAI)** 또는 **Claude Code(Anthropic)** 로 전환 가능
- 기본 UX: **Interactive Shell** (`/start`, `/stop`, `/config` …)
  + 무인 운용/스크립트용: `--run-now` (즉시 실행)
- **Runtime**: 프로젝트 내 가상환경 (Windows)

---

## 핵심 기능

- **Interactive Shell**: 실행 전 설정 확인/수정 후 `/start`로 러너 실행
- **Non-interactive 실행**: `--run-now --non-interactive`로 밤새 무인 운용
- **백엔드 전환** (양쪽 모두 CLI 로그인 기반 — API Key 불필요)
  - `execution_backend=codex` (기본): Codex CLI (`codex exec`) — Codex 크레딧(ChatGPT 구독) 사용
  - `execution_backend=claudecode`: Claude Agent SDK + Claude Code CLI — Claude 로그인 사용
- **PM 구조화 출력 강제**: PM 응답을 JSON 스키마로 검증 → 러너가 `BACKLOG.json|md`를 생성
- **안전한 Git 운용**
  - 기본은 **안전 모드** (파괴적 롤백 비활성)
  - 선택: `--worktree-isolation`로 격리 worktree에서 작업 후 패치로 반영
- **모델 에스컬레이션**: Dev 실패 시 저비용 → 고비용 모델로 자동 업그레이드
- **빌드/테스트 게이트(옵션)**: 커스텀 `build_cmd/test_cmd`도 지원
- **정책/시크릿 스캔(옵션)**: run_dir 산출물/코드에서 키/토큰 유출 방지 스캔
- **실행 아티팩트 관리**: `run_dir` 단위로 로그/상태/백로그/리포트 보존
- **예산 가드레일**: 에스컬레이션/continuation/재시도 횟수에 상한을 두어 비용 폭주 방지
- **쿼타 사용량 관리**: Claude OAuth 5h/7d 윈도우 사전 체크, 자동 대기/중단
- **GOALS 자동 갱신**: 프로젝트 목표 달성 시 LLM이 차세대 목표를 자동 생성 (`goals_auto_refresh`)
- **파이프라인 커스터마이징**
  - `roles="PM,Dev,QA,Security"`처럼 역할 순서/구성 변경
  - 플러그인 Stage(외부 모듈) 로드(Allowlist 기반)

---

## 아키텍처 개요

```
agent_cli.py (진입점)
  ├─ --run-now → agent_runner/main.py → 즉시 실행
  └─ (기본)   → agent_runner/shell.py → Interactive Shell → /start로 실행

agent_runner/runner_entry.py (async dispatch + failover + signal handling)
  ├─ codex     → cycle.py (~2550 lines) + codex_exec.py (서브프로세스 래퍼)
  └─ claudecode → backends/claudecode.py (~2900 lines) + claude_extensions.py (MCP/hooks/subagents)

파이프라인 오케스트레이션:
  pipeline/manager.py   (PipelineManager + _PROPAGATE_STOP_REASONS)
  pipeline/session.py   (PipelineSession — 백엔드 무관 컨텍스트)
  pipeline/stages/      (PM, Dev, QA, Security Stage 정의)

서브시스템:
  goals.py       (GOALS.md 추적 + 자동 갱신 + 체크박스 자동 업데이트)
  prompts.py     (PromptStore + append_pm_essential_context)
  task_history.py (SQLite 크로스-런 태스크 이력)
  skills/        (SKILL.md 인덱싱, 매칭, 발췌)
  utils.py       (Stop reasons 9개, 쿼타 사용량 체크, 예산 헬퍼)
```

### 설정 우선순위

```
CLI 인자 (--flag)  >  설정 파일 (JSON)  >  DEFAULTS (코드 내 기본값)
```

### 핵심 모듈 역할

| 모듈 | 역할 |
|------|------|
| `cli.py` | DEFAULTS 정의 (~125키), CLI 파싱, 설정 병합 (`_merge_effective`) |
| `codex_exec.py` | `codex exec` 서브프로세스 래퍼 (`CodexExecResult`, JSONL 파싱, 타임아웃) |
| `cycle.py` | Codex 백엔드 전체 파이프라인 (~2550줄, PM→Dev→QA→Reporter) |
| `backends/claudecode.py` | Claude 백엔드 전체 파이프라인 (~2900줄) |
| `backends/claude_extensions.py` | Claude SDK 확장 (MCP 도구, hooks, can_use_tool, subagents, ~616줄) |
| `exceptions.py` | 공유 예외 클래스 (`BudgetExceeded`, `StopRequested`) |
| `exc_detect.py` | 예외 감지 (`is_quota_exception`, `is_transient_exception` 등) |
| `backlog_utils.py` | 백로그 정규화/검증/컨텍스트 (`normalize_backlog_tasks`, `validate_skill_ids`) |
| `qa_utils.py` | QA followup 추출/병합 (`extract_qa_followups`, `merge_qa_followups`) |
| `state.py` | `BACKLOG.json`, `STATE.json` 읽기/쓰기, TaskItem 정의 |
| `utils.py` | Stop reason 상수 (9개), `has_quota_text()`, `budget_exceeded()`, 쿼타 사용량 체크, 공용 헬퍼 |
| `goals.py` | GOALS.md 완료 추적 (P0/P1), 자동 갱신 rescue, 체크박스 자동 업데이트 |
| `gitops.py` | 체크포인트 생성/복원, worktree 격리, 변경 감지 |
| `gates.py` | 빌드/테스트 게이트 실행 |
| `prompts.py` | 프롬프트 템플릿 로딩, PM 출력 스키마 정의 |
| `structured.py` | PM JSON 파싱/검증, 에러 설명 생성 |
| `schemas.py` | `PMOutputV2` 스키마, JSON Schema 생성 |
| `pipeline/` | Stage 오케스트레이션, `_PROPAGATE_STOP_REASONS`, 플러그인 로딩 |
| `skills/` | SKILL.md 인덱싱, 퍼지 매칭, 발췌문 생성 |
| `shell.py` | Interactive Shell (prompt_toolkit 기반) |
| `reporting.py` | Shutdown 보고서 생성 (~475줄) |
| `task_history.py` | SQLite 크로스-런 태스크 이력 DB |

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

## Claude Code 커스텀 커맨드

AgentCLI 프로젝트에는 **Claude Code CLI**에서 바로 사용할 수 있는 커스텀 커맨드(슬래시 커맨드)가 포함되어 있습니다.
이 커맨드들은 AgentCLI가 대상 프로젝트를 분석/운용하기 위해 필요한 **설계문서**와 **GOALS.md**를 생성합니다.

### 사용 가능한 커맨드

| 커맨드 | 설명 | 모드 |
|--------|------|------|
| `/design-doc <경로>` | 대상 프로젝트를 분석하여 설계문서 자동 생성 | 자동 (일괄) |
| `/generate-goals <경로>` | 대상 프로젝트의 GOALS.md 생성/보강 | 자동 (일괄) |
| `/design-workshop <경로>` | 사용자와 대화하며 설계문서를 함께 작성 | 대화형 (단계별) |

### 생성되는 산출물

```
{대상 프로젝트}/.doc/
  ├─ GOALS.md                  (/generate-goals, /design-workshop Phase 5)
  ├─ DOCS_DIGEST.md            (/design-doc, /design-workshop Phase 5)
  └─ Docs/
      ├─ ARCHITECTURE.md       (아키텍처 개요)
      ├─ CONVENTIONS.md        (코딩 규약)
      └─ CURRENT_STATE.md      (현재 상태 평가)
```

### 사용 예시

```bash
# Claude Code CLI에서 실행
> /design-doc D:\000.Work\MyProject
> /generate-goals D:\000.Work\MyProject
> /design-workshop D:\000.Work\MyProject
```

- **`/design-doc`**: 코드베이스를 분석하여 ARCHITECTURE.md, CONVENTIONS.md, CURRENT_STATE.md, DOCS_DIGEST.md를 한 번에 생성합니다. AgentCLI config 권장 설정도 함께 제안합니다.
- **`/generate-goals`**: 프로젝트의 미완성 기능을 분석하여 P0(Must-Have) / P1(Should-Have) 항목을 생성합니다. 기존 GOALS.md가 있으면 체크 상태를 보존하면서 보강합니다.
- **`/design-workshop`**: 5단계 대화형 워크숍으로, 각 Phase마다 사용자 확인을 받으며 진행합니다. 한꺼번에 생성하지 않고, 사용자 피드백을 반영하여 문서 품질을 높입니다.

> **주의**: 세 커맨드 모두 **대상 프로젝트 경로**가 필수입니다. 경로 없이 실행하면 오류 메시지와 함께 중단됩니다.

### 다른 PC에서 사용하기

커맨드 파일은 `.claude/commands/*.md`에 위치하며, `.gitignore` 예외 설정을 통해 **git으로 공유**됩니다:

```gitignore
# .claude 디렉토리 내용 무시 (하위 예외 허용을 위해 /* 사용)
.claude/*
!.claude/commands/
!.claude/commands/*.md
```

다른 PC에서 repo를 `git clone`/`git pull`하면 커맨드가 자동으로 포함됩니다.
Claude Code CLI가 프로젝트 루트에서 실행되면 `.claude/commands/` 내의 커맨드를 자동 인식합니다.

---

## 문서 안내

| 문서 | 내용 |
|------|------|
| [설치 가이드](docs/INSTALLATION.md) | 요구사항, Step-by-Step 세팅, 환경 검증 |
| [설정 관리](docs/CONFIGURATION.md) | Config 관리, 백엔드 선택, 모델 설정, Claude 고급 |
| [파이프라인](docs/PIPELINE.md) | PM→Dev→QA 로직, 커스터마이징, Enterprise |
| [운영 가이드](docs/OPERATIONS.md) | Git 안전, 예산, 빌드 게이트, Preflight, 산출물 |
| [트러블슈팅](docs/TROUBLESHOOTING.md) | 22개 문제 상황별 해결법 |
| [프롬프트 & 스킬](docs/CUSTOMIZATION.md) | 프롬프트 오버라이드, 스킬 작성법 |
| [고급 기능](docs/ADVANCED_FEATURES.md) | TODO, GOALS, 태스크 히스토리, QA 후속, Shutdown |
| [개발자 가이드](docs/DEVELOPER_GUIDE.md) | Stage/Backend 확장, 메트릭스, 프로세스 안전 |
| [설정 레퍼런스](docs/CONFIG_REFERENCE_KO.md) | 전체 설정 변수 상세 (23개 섹션) |

---

## 의존성(Dependencies)

```
openai>=1.0.0
openai-agents>=0.0.0
claude-agent-sdk>=0.1.0
pydantic>=2.0.0
prompt_toolkit>=3.0.0
```

## 변경 이력(ChangeLog)

단위 테스트(있는 경우):
```bash
python -m pytest -q
```

---

## Telegram 원격 제어 (하이브리드, Long Polling)

AgentCLI는 원격 모니터링/제어를 위해 Telegram 하이브리드 모드로 실행할 수 있습니다.

### 0) Dependency Install (Conda/Windows)

If `pip.exe` is blocked, install with `python -m pip`:

```bash
'C:\ProgramData\Anaconda3\python.exe' -m pip install --user python-telegram-bot
```

Conda users can also install from conda-forge:

```bash
conda config --add channels conda-forge
conda config --set channel_priority strict
conda install python-telegram-bot
```

### 1) Telegram 설정

환경변수로 bot token을 설정(권장):

```bash
set AGENTCLI_TELEGRAM_BOT_TOKEN=123456:ABCDEF...
```


또는 config JSON의 `telegram.bot_token`에 설정할 수 있습니다(기본 저장 경로: `%USERPROFILE%\.agentcli\configs`).

```json
{
  "telegram": {
    "enabled": true,
    "runner_mode": "thread",
    "instance_name": "home-pc-main",
    "allowed_chat_ids": [],
    "pairing_code": "CHANGE-ME",
    "notify_events": ["run_start", "run_stop", "task_done", "task_failed", "quota", "error", "stalled"],
    "send_cycle_summary": true,
    "notify_poll_interval_seconds": 8,
    "stalled_seconds": 600
  }
}
```
### 2) 하이브리드 모드 시작

```bash
python agent_cli.py --telegram --repo "C:/Dev/YourRepo"
```


`--telegram`은 다음 두 가지를 함께 시작합니다:
- 로컬 인터랙티브 셸 (`/start`, `/stop`, `/config`, ...)
- Telegram 컨트롤 플레인 (`/status`, `/run_start`, `/run_stop`, ...)

### 3) Telegram 명령어

- `/whoami` : 현재 `chat_id` 출력
- `/pair <code>` : 현재 채팅을 allowlist에 등록(페어링)
- `/status` : 실행 상태/진행 요약 표시
- `/detail [lines]` : 상세 통합 뷰 (`cycle_summary.log`, `metrics.jsonl`, `run_summary.json`, subprocess 로그 등)
- `/errors [lines]` : 에러 성격의 metrics 이벤트만 tail
- `/events <event_name> [lines]` : 이벤트 타입별 metrics 필터
- `/grep <pattern> [file] [lines]` : run 산출물에서 정규식 검색
- `/run_start [--flags...]` : 러너 시작
- `/run_stop` : 확인 버튼(Confirm) 포함 중지 요청
- `/runs` : 최근 실행(run) 목록
- `/tail [file] [lines]` : run 아티팩트 tail (`cycle_summary.log`, `metrics.jsonl`, `telegram_runner_subprocess.log`, ...)
- `/notify` : 푸시 알림 설정 조회

### 4) 자동 푸시 알림

- `notify_events`가 비어있지 않거나 `send_cycle_summary=true`이면 푸시가 자동으로 활성화됩니다.
- 알림은 allowlist에 등록된 모든 채팅으로 전송됩니다.
- 기본 이벤트: `run_start`, `run_stop`, `task_done`, `task_failed`, `quota`, `error`, `stalled`.
- `stalled`는 `stalled_seconds` 동안 `metrics.jsonl` 갱신이 없을 때 발생합니다(기본 600초 / 10분).
- 각 푸시 메시지에는 인스턴스 구분을 위한 `instance_name`이 포함됩니다.

### 5) 여러 AgentCLI 인스턴스

- 권장: AgentCLI 인스턴스(프로세스)마다 bot token을 1개씩 사용
- Same-PC duplicate startup with the same bot token is blocked by local token-lock.
- 여러 프로세스가 같은 token으로 long polling을 하면 Telegram 업데이트 처리 충돌이 날 수 있습니다.
- 여러 인스턴스가 같은 `chat_id`로 알림을 보내면, 인스턴스별로 별도의 푸시 스트림(메시지)을 받게 됩니다.
- 여러 인스턴스가 동일 repo/run_dir을 제어하면 stop/status/log 아티팩트가 서로 간섭할 수 있습니다. 가능하면 repo당 1개 인스턴스를 사용하세요.

### 6) 로그 언어 권장 사항

- 파서/LLM 호환성을 위해 run 산출물/로그 파일은 영어로 유지하는 것을 권장합니다.
- Telegram 명령어(`/status`, `/detail`, `/tail`)는 사람이 읽기 좋은 조회 레이어로 사용하세요.
