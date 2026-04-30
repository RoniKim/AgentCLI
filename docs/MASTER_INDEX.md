# AgentCLI 문서 인덱스

> 마지막 갱신: 2026-04-30
> Web Operational UX QA 후속 문서 반영 + PM/Dev/QA 컨텍스트 갱신
> 모든 항목은 코드와 실제 파일 inventory + file:line 단위 대조 완료

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
| [CONFIGURATION.md](CONFIGURATION.md) | ⚠️ 업데이트 필요 | HIGH | Codex 모델 표·worktree 명령 갱신 필요 |
| [CONFIG_REFERENCE_KO.md](CONFIG_REFERENCE_KO.md) | ⚠️ 업데이트 필요 | HIGH | 모델 기본값 6개·failover·telegram 섹션 갱신 |
| [OPERATIONS.md](OPERATIONS.md) | ⚠️ 업데이트 필요 | MEDIUM | budgets 기본값·/doctor 항목 수 |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | ⚠️ 업데이트 필요 | MEDIUM | Stop Reason 표 9개로 확장 |
| [PIPELINE.md](PIPELINE.md) | ⚠️ 업데이트 필요 | HIGH | Enterprise 가드레일 정정 |
| [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) | ⚠️ 업데이트 필요 | HIGH | 매칭 임계값 80%·키 이름 정정 |
| [CUSTOMIZATION.md](CUSTOMIZATION.md) | ⚠️ 업데이트 필요 | MEDIUM | Skills 기본값 4건 |
| [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | ⚠️ 업데이트 필요 | MEDIUM | L5 watchdog·신규 모듈 반영 |
| [WEB_CONSOLE.md](WEB_CONSOLE.md) | ⚠️ 업데이트 필요 | HIGH | 이미 구현된 기능을 미구현으로 잘못 안내 |
| [TELEGRAM.md](TELEGRAM.md) | ✅ OK | MEDIUM | 13개 명령·11개 CLI 플래그 정합 |
| [WORKTREE_MERGE_FAILURE_20260428.md](WORKTREE_MERGE_FAILURE_20260428.md) | ✅ OK | LOW | 이미 코드화 반영됨 |

## 2. 미개발 기능 설계서 (docs/proposals/)

| 문서 | 크기 | 비고 |
|------|------|------|
| [PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md](proposals/PERSONAL_WORK_AUTOMATION_DESIGN_V2_EN.md) | 35KB | 거의 전체가 미구현 (INSTANCE_LOCK·WORK_SUMMARY·WEB_ACTION_AUDIT·WEB_SNAPSHOT 4개 신규 artifact + Runbook + Presets + Health + Retention + 5단계 로드맵) |

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
| `.doc/Docs/claude.md` | Claude backend 운영 가이드 |
| `UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md` | 무인운영 신뢰성 검증 결과 + 후속 작업 (T-A~T-I) |
| `LOCAL_PR_QUEUE_AND_DEFERRED_VALIDATION.md` | 로컬 PR 큐, 지연 검증, oversized GOALS task 분할 계획 |
| `EXPERIENCE_DB_AND_ANALYZER_STAGE.md` | Experience DB, Analyzer stage, lesson feedback loop 설계 |
| `LARGE_MODULE_DECOMPOSITION_PLAN.md` | `web.py`, `cycle.py`, `claudecode.py` 대형 모듈 분해 계획 |
| `WEB_OPERATIONAL_UX_GAPS_20260430.md` | Web Console 실사용 QA에서 확인된 운영 UX gap과 P0-W 후속 작업 |

## 5. 인시던트 (.doc/Docs/incidents/, 미해결)

| 문서 | 영향 | 상태 |
|------|------|------|
| `MEMORY_AND_HANDLE_LEAK_20260428.md` | HIGH (재부팅 전까지 회복 불가) | OPEN — Fix A/B/C/D/E 미적용 |

## 6. 디자인 소스 (docs/Design/, read-only)

| 항목 | 비고 |
|------|------|
| `Design/README.md` | Claude Design 핸드오프 번들 설명 |
| `Design/project/AgentCLI Web - A.html` | **canonical Direction A 디자인 소스** (모든 Web 문서가 참조) |
| `Design/project/directions/`, `Design/project/shared/` | 보조 자료 |

> ⚠️ `CONVENTIONS.md`가 명시적으로 "Do not edit `docs/Design/project/`"로 잠금. read-only.

---

## 카테고리별 우선순위

### 즉시 정합성 갱신이 필요한 문서 (HIGH)
- `CONFIG_REFERENCE_KO.md`, `CONFIGURATION.md`, `PIPELINE.md`, `ADVANCED_FEATURES.md`, `WEB_CONSOLE.md`

### 후속 갱신 (MEDIUM)
- `OPERATIONS.md`, `TROUBLESHOOTING.md`, `DEVELOPER_GUIDE.md`, `CUSTOMIZATION.md`

### 작업 트래커
- `.doc/Docs/UNATTENDED_OPS_AUDIT_AND_FOLLOWUP.md` (T-A~T-I)
- `.doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md` (Fix A~E)

---

## 검증 방법

본 인덱스의 각 문서 분류는 다음 방법으로 검증되었습니다:

```bash
# 코드 인벤토리 ↔ 문서 대조
grep -rn "DEFAULTS" agent_runner/cli.py
grep -rn "STOP_REASON_" agent_runner/utils.py
grep -rn "GOALS_REFRESH_RESCUABLE_REASONS" agent_runner/goals.py
grep -rn "skill_match_autofix_threshold" agent_runner/cli.py

# 모듈 누락 검사
ls agent_runner/  # 31 .py + backends/ + pipeline/ + skills/ + remote/

# 문서 작성일 확인
ls -la docs/  # Feb 20 다수 = stale 의심
```
