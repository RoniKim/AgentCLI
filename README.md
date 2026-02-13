# AgentCLI — CLI기반 Multi-Agent Runner (PM → Dev → QA)

개인 개발자가 **켜두고**, 나중에 **PR 수준의 변경(코드/테스트/문서)** 을 받는 것을 목표로 만든 **CLI 기반 멀티 에이전트 러너**입니다.

- 기본 파이프라인: **PM(백로그 생성) → Dev(구현) → QA(점검/피드백)**
- 실행 엔진(backend): **Codex(OpenAI)** 또는 **Claude Code(Anthropic)** 로 전환 가능
- 기본 UX: **Interactive Shell** (`/start`, `/stop`, `/config` …)
  + 무인 운용/스크립트용: `--run-now` (즉시 실행)

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
       ├─ codex     → agent_runner/cycle.py          (codex exec 서브프로세스)
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
| `codex_exec.py` | `codex exec` 서브프로세스 래퍼 (JSONL 파싱, 타임아웃, 프로세스 관리) |
| `cycle.py` | Codex 백엔드 전체 파이프라인 (PM→Dev→QA→Reporter) |
| `backends/claudecode.py` | Claude 백엔드 전체 파이프라인 |
| `exceptions.py` | 공유 예외 클래스 (`BudgetExceeded`, `StopRequested`) |
| `exc_detect.py` | 예외 감지 (`is_quota_exception`, `is_transient_exception` 등) |
| `backlog_utils.py` | 백로그 정규화/검증/컨텍스트 (`normalize_backlog_tasks`, `validate_skill_ids`) |
| `qa_utils.py` | QA followup 추출/병합 (`extract_qa_followups`, `merge_qa_followups`) |
| `state.py` | `BACKLOG.json`, `STATE.json` 읽기/쓰기, TaskItem 정의 |
| `utils.py` | Stop reason 상수, `has_quota_text()`, `budget_exceeded()`, 공용 헬퍼 |
| `gitops.py` | 체크포인트 생성/복원, worktree 격리, 변경 감지 |
| `gates.py` | 빌드/테스트 게이트 실행 |
| `prompts.py` | 프롬프트 템플릿 로딩, PM 출력 스키마 정의 |
| `structured.py` | PM JSON 파싱/검증, 에러 설명 생성 |
| `schemas.py` | `PMOutputV2` 스키마, JSON Schema 생성 |
| `pipeline/` | Stage 오케스트레이션, 플러그인 로딩 |
| `shell.py` | Interactive Shell (prompt_toolkit 기반) |

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

## ChangeLog

단위 테스트(있는 경우):
```bash
python -m pytest -q
```
