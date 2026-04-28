← [README로 돌아가기](../README.md)

# 파이프라인 상세 로직

> 최종 검증: 2026-04-28 (코드 기준)

## 실행 디스패치 흐름 (Preflight → Backend)

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
  │     ├─ codex: codex CLI 설치 (로그인 필요)       │
  │     └─ claudecode: claude-agent-sdk (로그인 필요) │
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
  │  ├─ codex     → cycle.py (codex exec 서브프로세스)│
  │  └─ claudecode → claudecode.py (Claude SDK)     │
  └──────────────────────────────────────────────────┘
```

## 전체 사이클 흐름

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

## 예산 추적 상태 (Budget State)

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

## PM 단계 (백로그 생성)

**동작 모드:**

| 모드 | 조건 | 설명 |
|------|------|------|
| **Bootstrap** | 첫 실행 (PROJECT_ANALYSIS.md 없음) | 프로젝트 분석 + 초기 백로그 생성 |
| **Incremental** | HEAD 변경 또는 워킹 트리 dirty | 변경사항 반영한 백로그 업데이트 |
| **Refresh** | `pm_refresh_backlog=true` AND `pm_refresh_every_cycles>0` AND `cycle_idx % pm_refresh_every_cycles == 0` | 주기적 강제 재분석 |
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
      "skills_rationale": "UI 작업",
      "depends_on": []
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

## Dev 단계 (태스크 실행)

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
  (gpt-5.4-mini → gpt-5.4 → gpt-5.5)

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

## QA 단계 (리뷰/피드백)

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

## Reporter 단계 (종료 보고서)

**트리거 조건 (`utils.py`의 11개 stop_reason 어떤 것이든 가능):**
- 할당량 소진 (`quota_exhausted`)
- 할당량 활용률 한도 초과 (`quota_utilization`)
- 모든 태스크 완료 (`all_tasks_done`)
- 모든 태스크 시도 (`all_tasks_attempted`)
- 프로젝트 완성 (`project_complete`)
- 백로그 없음 (`no_tasks`)
- PM refresh 후 백로그 없음 (`pm_refresh_no_backlog`)
- prepared-only 모드 (`prepared_only`)
- Idle exit (`idle_exit`)
- STOP 파일 생성 (`stop_file`)
- 정상 종료 (`ok`) 또는 치명적 에러 발생

**보고서 생성 흐름:**
```
1. SHUTDOWN_CONTEXT.json 수집 (repo 상태, 백로그 진행률, 마지막 태스크)
2. SHUTDOWN_REPORT.md 로컬 폴백 작성 (항상, 토큰 무관)
3. Reporter 에이전트로 보고서 작성 시도 (best-effort)
   └─ 성공 시 폴백 덮어쓰기
   └─ 실패해도 로컬 폴백이 남아있으므로 안전
```

---

# 파이프라인(roles) 커스터마이징

기본:
- `roles="PM,Dev,QA"`

내장 Stage:
- `PM`, `Security`, `Dev`, `QA`

예시) QA를 끄고 PM→Dev만:
```text
> /set roles PM,Dev
> /save
```

예시) Security Stage까지 포함:
```text
> /set roles PM,Security,Dev,QA
> /save
```

## 플러그인 Stage 로드(고급)

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

# Enterprise 프로필

`profile` 설정으로 **personal**(기본) 또는 **enterprise** 모드를 선택할 수 있습니다.

```bash
# CLI
python agent_cli.py --run-now --repo <path> --profile enterprise

# Shell
> /set profile enterprise
> /save
```

## Enterprise 자동 적용 사항

| 항목 | personal (기본) | enterprise |
|------|-----------------|------------|
| **roles** | `PM,Dev,QA` | `PM,Security,Dev,QA` (Security 자동 추가) |
| **정책 스캔** | 비활성 | **자동 활성** |
| **보안 스캔** | 비활성 | **자동 활성** |
| **QA 항상 실행** | `qa_always=true` | `qa_always=true` |
| **예산 가드레일** | 사용자 설정값 | **최소값 강제 적용** (아래 참고) |

## Enterprise 예산 가드레일 강제

Enterprise 모드에서는 안정적 운영을 보장하기 위해 예산 한도에 **최소값(floor)** 이 강제됩니다. 사용자가 더 낮은 값으로 설정하더라도 다음 최소값으로 끌어올려집니다 (`cli.py`):

```
max_total_escalations_per_run    → max(사용자값, 5)
max_total_continuations_per_run  → max(사용자값, 5)
max_total_repair_attempts_per_run → max(사용자값, 3)
```

> 사용자가 더 높은 값을 설정하면 그대로 사용되며, 더 낮게 설정한 경우에만 위 최소값으로 보정됩니다. (Enterprise 프로필에서 PM/Dev 작업이 예산 부족으로 조기 중단되는 것을 방지하기 위함.)

## 사용 시나리오

- **팀 프로젝트**: Security Stage로 보안 취약점 자동 스캔
- **CI/CD 통합**: 정책/보안 스캔 필수화로 배포 전 품질 보장
- **안정적 운영**: 강제 최소값 가드레일로 PM/Dev가 예산 부족으로 조기 중단되는 것을 방지
