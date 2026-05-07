# AgentCLI 문서 인덱스

> 마지막 갱신: 2026-05-07
> `.doc/Docs`와 현재 프로젝트 구현의 정합성 감사 결과 반영
> 모든 링크는 실제 파일 inventory + case-sensitive path 검증 대상이며, `⚠️ 업데이트 필요` 상태는 validator가 실패로 처리

---

## 디렉토리 구조

| 디렉토리 | 용도 | 대상 독자 |
|---------|------|---------|
| `docs/` | 사용자/개발자용 영구 가이드 | 사람 |
| `docs/proposals/` | 미개발 기능 설계서 (Future design) | 사람 (의사결정용) |
| `docs/archive/` | 일회성/시점 자료 (워크로그·비교·snapshot) | 사람 (이력 참조) |
| `docs/Design/` | Read-only 디자인 소스 (HTML/JSX) | Direction A 시각 기준 |
| `.doc/Docs/` | PM/Dev/QA가 매 사이클 컨텍스트로 읽는 안정 문서 | Agent (자동 digest) |
| `.doc/Docs/incidents/` | 미해결 인시던트 분석 보고서 | 사람 (작업 트래킹) |

---

## 1. 운영 가이드 (docs/, 영구)

| 문서 | 상태 | 우선순위 | 비고 |
|------|------|---------|------|
| [INSTALLATION.md](INSTALLATION.md) | ✅ OK | HIGH | 신규 사용자 진입점 |
| [CONFIGURATION.md](CONFIGURATION.md) | ✅ OK | HIGH | Codex 모델 표·worktree 명령 검증 대상 |
| [CONFIG_REFERENCE_KO.md](CONFIG_REFERENCE_KO.md) | ✅ OK | HIGH | 모델 기본값·failover·telegram 섹션 갱신됨 |
| [OPERATIONS.md](OPERATIONS.md) | ✅ OK | MEDIUM | budgets 기본값·/doctor·worktree 운영 계약 검증 대상 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | ✅ OK | MEDIUM | Stop Reason·failover·budget 대응표 갱신됨 |
| [PIPELINE.md](PIPELINE.md) | ✅ OK | HIGH | Enterprise 가드레일과 역할 순서 갱신됨 |
| [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) | ✅ OK | HIGH | 매칭 임계값·shutdown report 키 이름 검증 대상 |
| [CUSTOMIZATION.md](CUSTOMIZATION.md) | ✅ OK | MEDIUM | Skills 설정 섹션 갱신됨 |
| [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | ✅ OK | MEDIUM | watchdog·신규 모듈 구조 반영됨 |
| [WEB_CONSOLE.md](WEB_CONSOLE.md) | ✅ OK | HIGH | FastAPI route inventory + runtime role order 검증 |
| [AUTHENTICATION_PLAN.md](AUTHENTICATION_PLAN.md) | ✅ OK | HIGH | LAN/외부 노출 전 인증 설계 기준선 |
| [TELEGRAM.md](TELEGRAM.md) | ✅ OK | MEDIUM | 13개 명령·11개 CLI 플래그 정합 |
| [WORKTREE_MERGE_FAILURE_20260428.md](WORKTREE_MERGE_FAILURE_20260428.md) | ✅ OK | LOW | 이미 코드화 반영됨 |

## 2. 미개발 기능 설계서 (docs/proposals/)

| 문서 | 크기 | 비고 |
|------|------|------|
| [PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md](proposals/PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md) | 35KB | 일부 구현됨 (Runbook·WORK_SUMMARY·WEB_ACTION_AUDIT 반영, INSTANCE_LOCK·WEB_SNAPSHOT·Retention 등은 GOALS P1 후속) |

## 3. 아카이브 (docs/archive/, 시점 자료)

| 문서 | 일자 | 분류 |
|------|------|------|
| [TELEGRAM_EVENING_TASKLIST.md](archive/TELEGRAM_EVENING_TASKLIST.md) | 2026-02-20 | 일회성 검증 체크리스트 |
| [TELEGRAM_WORKLOG_2026-02-20.md](archive/TELEGRAM_WORKLOG_2026-02-20.md) | 2026-02-20 | 작업 일지 |
| [AgentCLI_vs_BlueKiwi_Comparison_20260427.md](archive/AgentCLI_vs_BlueKiwi_Comparison_20260427.md) | 2026-04-27 | 시점 비교 (커밋 170083c 기준) |
| [WEB_CONSOLE_STATUS.md](archive/WEB_CONSOLE_STATUS.md) | 2026-04-26 | Web Console 리뷰 노트 (superseded) |
| [CURRENT_STATE.md](archive/CURRENT_STATE.md) | 2026-04-27 | Web Console 초기 셋업 스냅샷 (superseded by GOALS.md) |

## 4. PM/Dev/QA 컨텍스트 (.doc/Docs/, 자동 digest)

| 문서 | 용도 |
|------|------|
| `ARCHITECTURE.md` | 전체 아키텍처 + Web Console 타겟 통합 노트 |
| `CONVENTIONS.md` | self-development 시 PM/Dev/QA 행동 계약 |
| `WEB_CONSOLE_TARGET.md` | Web Console 시각/구조/데이터 계약 명세 (Direction A) |
| `DOC_PROJECT_CONSISTENCY_AUDIT_20260507.md` | `docs/`/`.doc/Docs`와 현재 구현 정합성 감사, Task 분해, 보완사항 |
| `.doc/Docs/claude.md` | Claude backend 운영 가이드 |
| `UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md` | 무인운영 신뢰성 검증 결과 + 후속 작업 (T-A~T-I) |
| `LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md` | 로컬 PR 큐, 지연 검증, oversized GOALS task 분할 계획 |
| `EXPERIENCE_DB_AND_ANALYZER_STAGE.md` | Experience DB, Analyzer stage, lesson feedback loop 설계 |
| `LARGE_MODULE_DECOMPOSITION_PLAN.md` | `web.py`, `cycle.py`, `claudecode.py` 대형 모듈 분해 계획 |
| `WEB_OPERATIONAL_UX_GAPS_20260430.md` | Web Console 실사용 QA에서 확인된 운영 UX gap과 P0-W 후속 작업 |

## 5. 인시던트 (.doc/Docs/incidents/, 미해결)

| 문서 | 영향 | 상태 |
|------|------|------|
| `MEMORY_AND_HANDLE_LEAK_20260428.md` | HIGH (재부팅 전까지 회복 불가) | OPEN / RE-AUDIT REQUIRED — 관련 GOALS 후속 구현 후 Windows handle/process 재검증 필요 |

## 6. 디자인 소스 (docs/Design/, read-only)

| 항목 | 비고 |
|------|------|
| `Design/README.md` | Claude Design 핸드오프 번들 설명 |
| `Design/project/AgentCLI Web - A.html` | **canonical Direction A 디자인 소스** (모든 Web 문서가 참조) |
| `Design/project/directions/`, `Design/project/shared/` | 보조 자료 |

> ⚠️ `CONVENTIONS.md`가 명시적으로 "Do not edit `docs/Design/project/`"로 잠금. read-only.

---

## 카테고리별 우선순위

### 현재 정합성 유지 대상 (HIGH)
- `CONFIG_REFERENCE_KO.md`, `CONFIGURATION.md`, `PIPELINE.md`, `ADVANCED_FEATURES.md`, `WEB_CONSOLE.md`
- `.doc/Docs/ARCHITECTURE.md`, `.doc/Docs/WEB_CONSOLE_TARGET.md`, `.doc/Docs/CONVENTIONS.md`

### 후속 갱신 (MEDIUM)
- `OPERATIONS.md`, `TROUBLESHOOTING.md`, `DEVELOPER_GUIDE.md`, `CUSTOMIZATION.md`

### 작업 트래커
- `.doc/Docs/UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md` (T-A~T-I)
- `.doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md` (Fix A~E)

---

## 검증 방법

본 인덱스의 각 문서 분류는 다음 방법으로 검증되었습니다:

```powershell
# docs validator
.\.venv\Scripts\python.exe -B -m unittest tests.test_docs_validation

# Python compile safety for doc-adjacent validation helpers
$env:PYTHONPYCACHEPREFIX = ".test-scratch\pycache-validation"
.\.venv\Scripts\python.exe -B -m compileall -q agent_runner tests

# Web route and browser proof when web docs/UX claims change
.\.venv\Scripts\python.exe -B .\tests\web_console_playwright_smoke.py -v
```

Playwright smoke is browser-render proof only when it runs tests instead of skipping because of local browser/runtime constraints.
