# AgentCLI 문서 인덱스

> 마지막 갱신: 2026-05-07
> 전체 문서 재정렬 반영: 현재 운영 문서, 구현 계획, proposal, archive, design, agent context를 분리
> 모든 링크는 실제 파일 inventory + case-sensitive path 검증 대상이며, `⚠️ 업데이트 필요` 상태는 validator가 실패로 처리

---

## 디렉토리 역할

| 위치 | 역할 | 현재 구현 완료 판단에 사용? |
|---|---|---|
| `docs/` 루트 | 현재 사용자/운영/개발자 가이드와 구현 계획 | 예. 단, `AUTHENTICATION_PLAN.md`는 계획서 |
| `docs/proposals/` | Future design과 gap map | 아니오. 재검증 후 GOALS 승격 필요 |
| `docs/archive/` | 과거 시점 자료, 작업 로그, 해결된 인시던트 | 아니오. 이력 참고 |
| `docs/Design/` | read-only 디자인 핸드오프와 prototype assets | 아니오. 시각/워크플로우 기준 |
| `.doc/Docs/` | PM/Dev/QA가 읽는 안정 컨텍스트 | 예. 단, 상단 status note 우선 |
| `.doc/Docs/incidents/` | 미해결 또는 재감사 필요 인시던트 | 부분. 작업 트래킹 |

---

## 1. 문서 진입점 (docs/)

| 문서 | 상태 | 비고 |
|---|---|---|
| [README.md](README.md) | ✅ OK | `docs/` 내비게이션 허브 |
| [MASTER_INDEX.md](MASTER_INDEX.md) | ✅ OK | 전체 inventory, 분류, 검증 방법 |

## 2. 시작 및 설정

| 문서 | 상태 | 우선순위 | 비고 |
|---|---|---|---|
| [INSTALLATION.md](INSTALLATION.md) | ✅ OK | HIGH | 신규 사용자 진입점 |
| [CONFIGURATION.md](CONFIGURATION.md) | ✅ OK | HIGH | Config 관리, 백엔드 선택, 모델 설정 |
| [CONFIG_REFERENCE_KO.md](CONFIG_REFERENCE_KO.md) | ✅ OK | HIGH | 전체 설정 변수 레퍼런스 |

## 3. 운영 및 문제 해결

| 문서 | 상태 | 우선순위 | 비고 |
|---|---|---|---|
| [OPERATIONS.md](OPERATIONS.md) | ✅ OK | MEDIUM | budgets 기본값, `/doctor`, worktree 운영 계약 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | ✅ OK | MEDIUM | Stop Reason, failover, budget 대응표 |
| [TELEGRAM.md](TELEGRAM.md) | ✅ OK | MEDIUM | 13개 명령, 11개 CLI 플래그 정합 |

## 4. 파이프라인, 확장, 고급 기능

| 문서 | 상태 | 우선순위 | 비고 |
|---|---|---|---|
| [PIPELINE.md](PIPELINE.md) | ✅ OK | HIGH | Enterprise 가드레일과 역할 순서 |
| [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) | ✅ OK | HIGH | TODO, GOALS, task history, shutdown report |
| [CUSTOMIZATION.md](CUSTOMIZATION.md) | ✅ OK | MEDIUM | prompt override, Skills 설정 |
| [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | ✅ OK | MEDIUM | watchdog, 신규 모듈 구조, 확장 포인트 |

## 5. Web Console

| 문서 | 상태 | 우선순위 | 비고 |
|---|---|---|---|
| [WEB_CONSOLE.md](WEB_CONSOLE.md) | ✅ OK | HIGH | 현재 구현, FastAPI route inventory, 검증 명령 |
| [AUTHENTICATION_PLAN.md](AUTHENTICATION_PLAN.md) | ✅ PLAN | HIGH | 인증 구현 계획. 현재 인증 레이어가 아님 |
| [Design/README.md](Design/README.md) | ✅ REFERENCE | MEDIUM | Direction A 디자인 핸드오프 |

## 6. Proposal / Future Design

| 문서 | 상태 | 비고 |
|---|---|---|
| [PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md](proposals/PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md) | PROPOSAL | 일부 항목은 GOALS로 승격·구현됨. 남은 항목은 future scope unless revalidated |

## 7. Archive

| 문서 | 일자 | 분류 |
|---|---|---|
| [TELEGRAM_EVENING_TASKLIST.md](archive/TELEGRAM_EVENING_TASKLIST.md) | 2026-02-20 | 일회성 검증 체크리스트 |
| [TELEGRAM_WORKLOG_2026-02-20.md](archive/TELEGRAM_WORKLOG_2026-02-20.md) | 2026-02-20 | 작업 일지 |
| [AgentCLI_vs_BlueKiwi_Comparison_20260427.md](archive/AgentCLI_vs_BlueKiwi_Comparison_20260427.md) | 2026-04-27 | 시점 비교 (커밋 170083c 기준) |
| [WEB_CONSOLE_STATUS.md](archive/WEB_CONSOLE_STATUS.md) | 2026-04-26 | Web Console 리뷰 노트 (superseded) |
| [CURRENT_STATE.md](archive/CURRENT_STATE.md) | 2026-04-27 | Web Console 초기 셋업 스냅샷 (superseded by GOALS.md) |
| [WORKTREE_MERGE_FAILURE_20260428.md](archive/WORKTREE_MERGE_FAILURE_20260428.md) | 2026-04-28 | 해결된 worktree merge failure 인시던트 |

## 8. PM/Dev/QA 컨텍스트 (.doc/Docs/)

| 문서 | 용도 |
|---|---|
| `.doc/Docs/ARCHITECTURE.md` | 전체 아키텍처 + Web Console 현재 구현 상태 |
| `.doc/Docs/CONVENTIONS.md` | self-development 시 PM/Dev/QA 행동 계약 |
| `.doc/Docs/WEB_CONSOLE_TARGET.md` | Direction A 시각/구조/데이터 계약 + 현재 baseline |
| `.doc/Docs/DOC_PROJECT_CONSISTENCY_AUDIT_20260507.md` | `docs/`/`.doc/Docs`와 현재 구현 정합성 감사 |
| `.doc/Docs/claude.md` | Claude backend 운영 가이드 |
| `.doc/Docs/LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md` | 로컬 PR 큐, 지연 검증, oversized GOALS task 분할 계획 |
| `.doc/Docs/EXPERIENCE_DB_AND_ANALYZER_STAGE.md` | Experience DB, Analyzer stage, lesson feedback loop 설계 |
| `.doc/Docs/LARGE_MODULE_DECOMPOSITION_PLAN.md` | `web.py`, `cycle.py`, `claudecode.py` 대형 모듈 분해 계획 |
| `.doc/Docs/UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md` | 무인운영 신뢰성 검증 결과와 P0-X 후속 이력 |
| `.doc/Docs/WEB_OPERATIONAL_UX_GAPS_20260430.md` | Web Console UX gap 감사와 P0-W 후속 이력 |

## 9. Open / Re-Audit Incidents (.doc/Docs/incidents/)

| 문서 | 영향 | 상태 |
|---|---|---|
| `.doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md` | HIGH | OPEN / RE-AUDIT REQUIRED. 관련 GOALS 후속 구현 후 Windows handle/process 재검증 필요 |

---

## 읽는 기준

- 현재 기능 완료 판단은 `.doc/GOALS.md`와 테스트 결과를 기준으로 한다.
- `docs/proposals/`, `docs/archive/`, `docs/Design/`의 unchecked 항목은 현재 backlog가 아니다.
- 인증은 아직 구현된 기능이 아니다. `AUTHENTICATION_PLAN.md`는 구현 전 안전 기준선이다.
- `docs/Design/project/`는 read-only prototype이다. production Web Console은 `web_console/`와 `agent_runner.web`다.

## 검증 방법

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
