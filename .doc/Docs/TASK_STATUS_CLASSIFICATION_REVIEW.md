# Task 결과 분류 통합 — 검증 결과와 후속 작업

> 작성일: 2026-04-29
> 검증 방식: 4관점 적대적 토론(`agent-debate`: 정합성 / 다국어 안정성 / 회귀성 / 운영가시성·보안) + Codex 독립 사실 검증(gpt-5.4 xhigh, read-only)
> 목적: 작업 트리 미커밋 변경분(`agent_runner/task_status.py` 신규 + `cycle.py` ~179줄 통합 + 다운스트림 5개 파일)이 사용자 의도("test 실패해도 무차별 삭제하지 않게 분류")를 어디까지 실현하고 어디서 멈췄는지 확정하고, 남은 갭을 후속 작업 단위로 명세화

---

## 0. 구현 업데이트 (2026-04-29)

이 문서는 최초 검토 시점의 갭 분석을 보존한다. 아래 항목은 이후 구현으로 닫힌 작업 상태다.

| Task | 상태 | 구현 요약 | 주요 파일 |
|---|---|---|---|
| T1~T7 | 완료 | `failure_policy` 도입, task_status enriched schema, backend 대칭화, 다국어 패턴 보강, task_history/task_results 반영, preserve dispatch 명시화 | `failure_policy.py`, `task_status.py`, `cycle.py`, `backends/claudecode.py`, `task_history.py` |
| T8 | 완료 | `blocked_env`만 발생한 cycle 실패는 consecutive failure stop 카운트에서 제외하고 `cycle_failure_not_counted` 이벤트를 남김 | `failure_policy.py`, `cycle.py`, `backends/claudecode.py` |
| T9 | 완료 | 실패 KPI를 `regression / review / blocked_env` 그룹으로 분리하고 report/web/backlog UI에 노출 | `reporting.py`, `web.py`, `web_console/app.js` |
| T10 | 완료 | `blocked_env` 실패에 대해 `DEPENDENCIES_NEEDED.md` 운영자 가이드를 기록하되, 기존 `needs_dependency` 중복 기록은 피함 | `cycle.py`, `backends/claudecode.py` |
| T11 | 완료 | persistent skip용 consecutive title failure 카운트에서 `blocked_env`와 `test_contract_changed`를 제외하도록 명시 | `cycle.py`, `backends/claudecode.py` |
| T12 | 완료 | validation text 입력을 64KB로 제한하고 task_status 정규식을 사전 컴파일해 대형 로그 선형 스캔 비용을 제한 | `task_status.py` |
| T13 | 완료 | 빈 reason은 기본적으로 `review_required`로 분류하고, success 의도는 `treat_empty_as_completed=True`로만 허용 | `task_status.py` |

검증:
- `.\.venv\Scripts\python.exe -m unittest tests.test_task_status tests.test_failure_policy tests.test_task_history_status tests.test_task_status_reporting` → 35 tests OK
- `.\.venv\Scripts\python.exe -m unittest tests.test_web_console_static` → 9 tests OK
- `.\.venv\Scripts\python.exe -m compileall agent_runner\task_status.py agent_runner\failure_policy.py agent_runner\task_history.py agent_runner\backlog_utils.py agent_runner\cycle.py agent_runner\backends\claudecode.py agent_runner\reporting.py agent_runner\web.py` → OK

---

## TL;DR

- **의도 절반만 실현**: 5단계 분류기(`completed / blocked_env / test_contract_changed / regression_failed / review_required`)와 다운스트림 라벨/번역/색상은 잘 짜였다. 그러나 **분류 결과가 실제 격리·재시도·집계 정책에 거의 도달하지 못한다.**
- **3개의 결정적 갭**:
  1. `_isolate_or_stop()` / `abandon_task_branch()`는 task_status 무관하게 reason만으로 호출 — 라벨링은 했지만 행동은 옛날 그대로
  2. `backends/claudecode.py`에 분류기 import 0건 — 백엔드 비대칭, failover 시 STATE.json이 사이클별로 다른 스키마
  3. Python/.NET/브라우저 패턴 편향 — Java/Go/Rust/C++/Kotlin/Swift/Maven/Gradle/NuGet/Cargo 미수록. AgentCLI는 모든 언어 프로젝트에서 사용되므로 가장 시급
- **올바르게 만들어진 것**: `task_status.py` 본체 분류 로직, `_task_failure_entry` helper, web/UI 라벨·색상·번역, 옛 STATE.json fallback 안전성
- **반박된 우려 (Codex 검증)**: `\blocator\b` camelCase 오매칭(틀림 — `\b`는 word boundary), ReDoS catastrophic backtracking(없음 — 입력 크기 캡 부재만), `_validation_text` PII 외부 LLM 노출(없음 — 분류기 내부 사용만)

---

## 1. 검증 결과 매트릭스

| 영역 | 상태 | 핵심 근거 |
|------|------|---------|
| **5단계 분류기 본체** | ✅ 합리적 설계 | `task_status.py:7-19, 110-150`. 패턴 우선순위 일관성 OK, MANUAL_REVIEW/AUTO_MERGE/AUTO_RETRY 헬퍼 분리 OK |
| **`_task_failure_entry` helper (Codex 백엔드)** | ✅ cycle.py 내부 통합 OK | `cycle.py:1909-1928`. 모든 fail 분기에서 일관 사용 |
| **분류 → 격리 dispatch** | ❌ 미연결 | `cycle.py:1931-2000` `_isolate_or_stop`은 reason만 받음. 9곳 호출처(`:2102, 2297, 2462, 2571, 2647, 2730, 2923, 2995, 3006`) 어디에서도 task_status를 분기 조건으로 안 씀. `blocked_env`/`review_required`/`test_contract_changed` 분류돼도 모두 abandon |
| **백엔드 대칭성** | ❌ Claude 백엔드 무분류 | `backends/claudecode.py:1-90` import 부재. `state["failed"].append({"task","reason"})` 옛 스키마 그대로 (`:1715, 1893, 2123, 2160, 2206, 2293, 2375` 등 10곳) |
| **다국어 패턴 커버리지** | ⚠️ Python/.NET 편향 | `task_status.py:31-78`. Java/Go/Rust/C++/Kotlin/Swift/Maven/Gradle/NuGet/Cargo 키워드 미수록. 의존성 해결 실패가 BLOCKED_ENV로 안 잡혀 `build_failed` reason fallback으로 REGRESSION_FAILED → max_attempts까지 retry 낭비 |
| **task_history DB 영속화** | ❌ subtype 미저장 | `task_history.py:26-48 _SCHEMA_SQL`, `:88-127 record_task` — task_status 컬럼/인자 부재. cross-run에서 분류 정보 유실 |
| **PM 컨텍스트 (같은 run 내)** | ✅ 정상 전달 | STATE.json 기반 `failed_tasks_block`이 `prompts.py:191-196` 통해 PM 프롬프트 주입. 분류 정보는 incremental PM에 도달 |
| **cross-run consecutive 카운터** | ❌ subtype 무시 | `task_history.py:679-682 count_consecutive_title_failures`는 status="failed"만 셈. blocked_env가 3회 누적되면 회귀와 동일하게 `persistent_failure` skip 처리 (`shared_runtime.py:542-579`) |
| **failed 카운터 (운영 KPI)** | ⚠️ 의미 회귀 | `reporting.py:516-520`, `web.py:4215-4221`. blocked_env / regression_failed / review_required / test_contract_changed 모두 `tasks_failed` 1개 카운터로 합산. 환경 문제와 진짜 회귀가 동일하게 빨갛게 표시 |
| **cycle stop (consecutive_failures)** | ⚠️ subtype 무시 | `cycle.py:3549-3557`. `cycle_failed=(rc != 0)` 만 보고 task_status 무관. blocked_env 3회면 전체 run stop |
| **needs_dependency / blocked_dependency 분기** | ⚠️ 부분 동작 | `cycle.py:2330-2362`(needs_dependency)는 `task_blocked=True`로 abandon 스킵 + DEPENDENCIES_NEEDED.md 기록. `:2392-2422`(blocked_dependency heuristic)는 abandon 스킵하나 DEPENDENCIES_NEEDED.md 미기록. 분류기에서 둘 다 first-class 매핑 부재 |
| **`task_results` vs `STATE.failed` 일관성** | ❌ 과소계상 | `cycle.py:2330-2362, 2394-2422, 2557-2579, 2633-2655, 2711-2738`. 다수 분기가 STATE.failed만 갱신, task_results 누락. `reporting.py:476-521`이 task_results만 집계 → SHUTDOWN_REPORT가 실패 수 과소계상 |
| **빈 reason → completed** | ⚠️ 호출자 실수 시 phantom success | `task_status.py:122-124`. `normalized_reason in {""}` → COMPLETED. 향후 호출자가 reason을 잊으면 fail이 success로 둔갑 |
| **Web 옛 STATE.json 호환** | ✅ 안전 | `web.py:4086-4110, 4200-4221`. status 키 부재 시 `"failed"` fallback. KeyError 0건 |
| **abandon_task_branch 비파괴성** | ✅ 브랜치 보존 | `gitops.py:819-870` 브랜치를 *삭제하지 않고* base로 checkout만. reflog/branch ref로 사후 복구 가능 (90일 git gc 제한) |
| **`_validation_text` PII 노출** | ❌ 우려 자체가 틀림 (Codex 반박) | 결과는 `task_status.py:85-149`와 `cycle.py:1619, 1907` 내부에서만 사용. 프롬프트 주입 경로 없음 |
| **`\blocator\b` camelCase 오매칭** | ❌ 우려 자체가 틀림 (Codex 반박) | Python `re`의 `\b`는 word boundary, `ServiceLocator`/`ResourceLocator` 매치 안 됨 |
| **ReDoS catastrophic backtracking** | ❌ 우려 자체가 틀림 (Codex 반박) | 패턴에 중첩 수량자/모호한 alternation 없음. 단 입력 크기 캡 부재 → 큰 로그 선형 비용은 사실 |

---

## 2. 의도-실현 갭

| 분류 | 사용자 기대 | 실제 동작 | 갭 |
|---|---|---|---|
| `blocked_env` | 작업 보존, 가이드 노출, retry 카운트 제외 | abandon 실행, consecutive_failures 카운트, DEPENDENCIES_NEEDED.md 미작성 | **Critical** |
| `test_contract_changed` | 작업 보존, 사람이 계약 검토 | 동일하게 abandon. retry 안 됨. cherry-pick 안내 부재 | **Medium** (브랜치는 살아있음) |
| `review_required` | 작업 보존, 사람 판단 큐 | 동일 abandon. exhausted_attempts 분기는 별도 직접 abandon | **Small** |
| `regression_failed` | 자동 재시도 / 실패 시 격리 | `is_auto_retry_allowed=True`로 retry, escalate, 최종 abandon | **None** (설계대로) |

분류 라벨은 UI/State에 도달하나 **실제 격리 정책에는 도달 못 함**. 이것이 사용자 의도와 가장 큰 갭이다.

---

## 3. 다국어 매트릭스

| 언어/툴체인 | env_pattern | regression_pattern | test_contract_pattern | 잘못 분류 시 영향 |
|---|---|---|---|---|
| Python (pip/pytest) | ✓ | ✓ | 부분 (`to_be`만) | - |
| .NET (MSBuild/xUnit) | ✗ NuGet `NU1101` | ✓ (`cs\d{4}`) | ✗ | NuGet 실패 → max_attempts retry |
| Java (Maven/Gradle/JUnit) | ✗ | ✗ | ✗ | 컴파일 회귀 review_required, 의존성 실패 max_attempts retry |
| Kotlin/Android | ✗ | ✗ | ✗ | 빌드 회귀 자동 재시도 손실 |
| Go | ✗ | ✗ | ✗ | panic 회귀 매뉴얼 격리 |
| Rust (cargo) | ✗ | ✗ | ✗ | 컴파일 회귀 review_required |
| Swift/iOS | ✗ | ✗ | ✗ | UI/컴파일 모두 매뉴얼 적재 |
| Node (npm/Jest/Cypress) | 부분 | ✗ | 부분 (Jest OK, Cypress 누락) | npm 실패 max_attempts retry |
| C/C++ | 부분 (`no such file`) | ✗ | ✗ | 링크/컴파일 회귀 review_required |
| Docker | ✗ | ✗ | ✗ | 레지스트리 접근 실패 max_attempts retry |

**누락 키워드 사례**:
- `_STRONG_REGRESSION_PATTERNS`에 추가 필요:
  - Java/Kotlin: `cannot find symbol`, `';' expected`, `package X does not exist`, `NullPointerException`, `ClassCastException`, `KotlinNullPointerException`
  - Go: `panic:`, `undefined:`, `runtime error: invalid memory address`, `cannot use ... as ...`
  - Rust: `error\[E\d+\]:`, `mismatched types`, `panicked at`
  - C/C++: `undefined reference to`, `error: expected ';'`, `Segmentation fault`, `error: no matching function`
  - Android: `> Task ... FAILED`, `FATAL EXCEPTION:`
  - Swift: `error: cannot find 'X'`, `Fatal error:`
  - Node: `Error: Cannot find module`, `UnhandledPromiseRejection`
- `_ENV_PATTERNS`에 추가 필요:
  - Maven `[ERROR] Could not resolve dependencies`
  - Gradle `Could not resolve dependency`, `> Could not find`
  - NuGet `error NU1101: Unable to find package`
  - Cargo `failed to download`, `failed to resolve crate`
  - Go modules `cannot find module providing package`
  - Docker `pull access denied`, `manifest unknown`

---

## 4. 후속 작업 (우선순위 + 추정 공수)

> 각 작업 단위는 한 AgentCLI task 브랜치로 진행 가능한 크기로 분해됨. T1~T7은 즉시(Hot-fix), T8~T13은 계획적(Roadmap).

### Tier 1 — 분류 의도를 실현하는 핵심 (Hot-fix, 1주 분량)

#### T1. `failure_policy.py` 신설 — 분류 vs 행동 분리 — 4-6시간

**증상**: 분류기는 라벨만 만들고, 격리 정책은 옛 reason만 본다. 두 백엔드가 같은 dispatch 모듈을 공유하지 못해 비대칭이 생긴다.

**현재 구현**:
- `task_status.py` — classification만 (`completed / blocked_env / test_contract_changed / regression_failed / review_required`)
- `cycle.py:1909-1928 _task_failure_entry / _task_failure_status` — closure에서 사용
- `cycle.py:1931-2000 _isolate_or_stop` — reason만 받음
- `claudecode.py` — 둘 다 미적용

**제안**:
- 새 모듈 `agent_runner/failure_policy.py`:
  ```python
  @dataclass(frozen=True)
  class FailureOutcome:
      reason: str
      task_status: str
      action: Literal["retry", "skip_next", "abandon_branch", "restore_checkpoint", "preserve_for_review", "stop_run"]
      retry_budget_consumed: bool
      guide_message: Optional[str]

  def decide_failure_disposition(
      reason: str,
      task_status: str,
      *,
      attempt: int,
      max_attempts: int,
      has_tb: bool,
      has_cp: bool,
      continuous: bool,
      escalate_on: list[str],
      auto_escalate: bool,
  ) -> FailureOutcome: ...

  def build_failure_entry(task_id: str, reason: str, *, validations=None, detail="", **extra) -> dict: ...
  ```
- 정책:
  - `blocked_env` → `preserve_for_review` + `retry_budget_consumed=False` + `guide_message` 채움
  - `test_contract_changed` / `review_required` → `preserve_for_review` (브랜치 유지, pending review 큐)
  - `regression_failed` → `retry`(budget 내) → 소진 시 `abandon_branch`
  - `exhausted_attempts` → `abandon_branch` + pending_review 동시 등록
- `cycle.py`의 `_task_failure_entry / _task_failure_status` 제거하고 `build_failure_entry` import
- `cycle.py:1604` import 갱신

**테스트 케이스**:
- `tests/test_failure_policy.py` 신설 — 각 (reason, task_status) 조합에 대해 기대 action 검증
- 기존 `tests/test_task_status.py`는 classification만 검증 (변경 없음)

**영향 받는 파일**:
- 신규: `agent_runner/failure_policy.py`, `tests/test_failure_policy.py`
- 수정: `agent_runner/cycle.py`, `agent_runner/backends/claudecode.py`(T2/T3과 결합)

---

#### T2. `_isolate_or_stop` task_status 인지 — 격리 dispatch 연결 — 2-3시간

**증상**: blocked_env / test_contract_changed / review_required 분류돼도 abandon이 그대로 실행됨. 사용자 의도("작업 보존") 미실현.

**현재 구현**:
- `cycle.py:1931-2000 _isolate_or_stop(reason)` — reason만 받음
- `cycle.py:2102, 2297, 2462, 2571, 2647, 2730, 2923, 2995, 3006` — 9곳 호출. 각자 `_isolate_or_stop("retry"|"exception"|"build_failed"|"test_failed"|"policy_violation"|"fast_regression_failed")`

**제안**:
- T1의 `FailureOutcome`을 받도록 시그니처 확장:
  ```python
  def _isolate_or_stop(reason: str, outcome: FailureOutcome) -> tuple[bool, str]:
      if outcome.action == "preserve_for_review":
          # branch 유지, base checkout 안 함, pending_review 큐에 등록
          state.setdefault("pending_review", []).append({"task": tb.task_id, "branch": tb.branch_name, "reason": reason, "task_status": outcome.task_status})
          return True, ""
      # 기존 abandon_task_branch / restore_checkpoint 흐름
  ```
- 9곳 호출처에서 `decide_failure_disposition()` 결과를 전달
- web.py에 `state["pending_review"]` 노출용 endpoint 추가 (별도 task로 분해 가능)

**테스트 케이스**:
- `tests/test_cycle_isolation.py` (또는 통합 테스트) — blocked_env로 분류된 fail이 abandon 안 되고 pending_review에 들어가는지 확인
- 기존 regression_failed 경로는 변경 없음 검증

**영향 받는 파일**:
- `agent_runner/cycle.py` (9곳 호출 + 시그니처)
- `agent_runner/state.py` (pending_review 키 추가, save/load 호환)

---

#### T3. Claude 백엔드 분류기 적용 — 백엔드 대칭성 — 4-6시간

**증상**: failover 운영 시 동일 STATE.json이 사이클별로 다른 스키마. CLAUDE.md "대칭 구현" 원칙 위반.

**현재 구현**:
- `backends/claudecode.py:1-90` — task_status import 0건
- `:1715, 1893-1899, 2123, 2160, 2206, 2293-2295, 2375, 2389-2399` — `state["failed"].append({"task": id, "reason": str})` 옛 스키마

**제안**:
- T1의 `failure_policy` import
- 모든 `state["failed"].append({...})` 호출을 `build_failure_entry(...)` 로 교체
- `_isolate_or_stop` 동등 헬퍼가 claudecode.py에 있다면 동일하게 `FailureOutcome` 받게 수정
- task_results.append에도 task_status 키 추가
- metrics.event / logger.task_end 호출에도 task_status= 인자 추가

**테스트 케이스**:
- `tests/test_claudecode_failure_schema.py` 신설 — Mock으로 claudecode.py의 fail 분기 호출 후 STATE.json이 cycle.py와 동일 스키마인지 비교
- 기존 Claude smoke test 영향 없음

**영향 받는 파일**:
- `agent_runner/backends/claudecode.py` (10+곳 수정)

---

#### T4. 다국어 패턴 보강 — Java/Go/Rust/C++/Kotlin/Swift/Maven/Gradle/NuGet/Cargo — 3-4시간

**증상**: AgentCLI는 모든 언어 프로젝트에서 사용되어야 하나, 현재 패턴은 Python/.NET/브라우저 편향. 의존성 해결 실패가 build_failed로 떨어져 max_attempts까지 retry 낭비. 진짜 회귀(`cannot find symbol`, `panic:`, `error[E0XXX]:`)가 review_required로 떨어져 auto_retry/escalate 손실.

**현재 구현**: `task_status.py:31-78` — 3개 패턴 그룹 총 ~42개

**제안**:
- `_STRONG_REGRESSION_PATTERNS`에 다음 추가:
  ```python
  r"\bcannot\s+find\s+symbol\b",                # Java
  r"\bpackage\s+\S+\s+does\s+not\s+exist\b",    # Java
  r"\bnullpointerexception\b",                   # Java
  r"\bclasscastexception\b",                     # Java
  r"\bkotlinnullpointerexception\b",             # Kotlin
  r"\b';'\s+expected\b",                         # Java
  r"\bpanic:\s*runtime\s+error\b",               # Go
  r"\bpanic:\s*",                                 # Go
  r"\bgoroutine\s+\d+\s+\[",                     # Go
  r"\berror\s*\[E\d+\]:",                        # Rust
  r"\bmismatched\s+types\b",                     # Rust
  r"\bpanicked\s+at\b",                           # Rust/Swift
  r"\bundefined\s+reference\s+to\b",             # C/C++
  r"\bsegmentation\s+fault\b",                   # C/C++
  r"\bno\s+matching\s+function\b",               # C/C++
  r"\bfatal\s+exception\b",                       # Android
  r"\b>\s*Task\s+\S+\s+FAILED\b",                # Gradle
  r"\bfatal\s+error:\s*",                         # Swift/clang
  r"\bcannot\s+find\s+'?[A-Za-z_]+'?\b",         # Swift
  r"\bunhandledpromiserejection\b",              # Node
  r"\bcannot\s+find\s+module\s",                 # Node
  ```
- `_ENV_PATTERNS`에 다음 추가:
  ```python
  r"\bcould\s+not\s+resolve\s+dependencies\b",   # Maven
  r"\bcould\s+not\s+resolve\s+dependency\b",     # Gradle
  r"\bcould\s+not\s+find\s+\S+\.\S+",            # Gradle (module:artifact:version)
  r"\bnu1101\b",                                  # NuGet
  r"\bunable\s+to\s+find\s+package\b",           # NuGet/Cargo
  r"\bfailed\s+to\s+resolve\s+crate\b",          # Cargo
  r"\bcannot\s+find\s+module\s+providing\s+package\b", # Go modules
  r"\bgo:\s+module\s+not\s+found\b",             # Go modules
  r"\bpull\s+access\s+denied\b",                  # Docker
  r"\bmanifest\s+unknown\b",                     # Docker
  r"\bartifact\s+\S+\s+not\s+found\b",           # Maven/Gradle generic
  ```

**테스트 케이스**: `tests/test_task_status.py`에 다음 케이스 추가 (10+개):
- Maven `[ERROR] Could not resolve dependencies` → BLOCKED_ENV
- Gradle `> Could not find org.example:lib:1.0` → BLOCKED_ENV
- NuGet `error NU1101: Unable to find package X` → BLOCKED_ENV
- Cargo `error: failed to download serde v1.0.0` → BLOCKED_ENV
- Go `panic: runtime error: index out of range` → REGRESSION_FAILED
- Rust `error[E0308]: mismatched types` → REGRESSION_FAILED
- C++ `undefined reference to 'foo'` → REGRESSION_FAILED
- Java `error: cannot find symbol: class Locator` (Locator 우연 매칭 방어 — strong이 contract보다 우선) → REGRESSION_FAILED
- Android `> Task :app:compileDebugJavaWithJavac FAILED` → REGRESSION_FAILED
- Node `Error: Cannot find module 'express'` → BLOCKED_ENV (npm 미설치 패키지)

**영향 받는 파일**:
- `agent_runner/task_status.py` (패턴 추가)
- `tests/test_task_status.py` (케이스 10개 추가)

---

#### T5. `task_history` SQLite `task_status` 컬럼 — 4-6시간

**증상**: cross-run에서 분류 정보가 사라짐. 새 run 시작 시 PM 프롬프트가 옛 reason만 봄. 분류 도입의 가장 큰 가치(PM이 환경 문제와 회귀를 구분 처리)가 run 경계를 못 넘음.

**현재 구현**:
- `task_history.py:26-48 _SCHEMA_SQL` — task_status 컬럼 없음
- `:88-127 record_task` — task_status 인자 없음
- `:679-682 count_consecutive_title_failures` — status="failed"만 셈
- `:535-577 _build_failed_task_item` — `_text(row.get("status") or row.get("task_status") ...)` fallback만 있음 (DB에 없으니 항상 "failed")

**제안**:
- `_SCHEMA_SQL`에 컬럼 추가: `task_status TEXT DEFAULT ''`
- `_MIGRATIONS`에 한 줄: `"ALTER TABLE task_history ADD COLUMN task_status TEXT DEFAULT ''"`
- `record_task(...)` 시그니처에 `task_status: str = ""` 추가
- `cycle.py` / `claudecode.py`의 `_record_history(...)` 호출처에서 task_status 전달
- `count_consecutive_title_failures(title, *, exclude_task_statuses=("blocked_env",))` 옵션 인자 추가
- `format_history_block` / `_build_failed_task_item`이 task_status를 PM 프롬프트로 노출 (`[FAIL/{reason}/{task_status}]` 형식)

**테스트 케이스**:
- `tests/test_task_history.py`에 마이그레이션 케이스 추가 — 옛 DB 열어서 ALTER 적용 → record_task로 task_status 저장 → query 시 반환
- consecutive_failures가 task_status="blocked_env" 행을 제외하는지 검증

**영향 받는 파일**:
- `agent_runner/task_history.py`
- `agent_runner/cycle.py` (`_record_history` 호출처)
- `agent_runner/backends/claudecode.py` (T3 결합)
- `tests/test_task_history.py`

---

#### T6. `needs_dependency` / `blocked_dependency`를 분류기 first-class로 매핑 — 1시간

**증상**: 명시적 의존성 시그널 reason인데 분류기에서 매핑 부재. 텍스트 매칭 안 하면 review_required로 떨어짐.

**현재 구현**: `task_status.py:126-150` — 두 reason은 fallback `review_required`

**제안**:
```python
# task_status.py classify_task_failure() 안:
if normalized_reason in {"needs_dependency", "blocked_dependency"}:
    return TASK_STATUS_BLOCKED_ENV
```
이 한 줄을 `_ENV_PATTERNS` 검사 직전에 배치.

**테스트 케이스**:
- 기존 `tests/test_task_status.py`에 2개 추가:
  - `classify_task_failure("needs_dependency")` → `BLOCKED_ENV`
  - `classify_task_failure("blocked_dependency")` → `BLOCKED_ENV`

**영향 받는 파일**:
- `agent_runner/task_status.py`
- `tests/test_task_status.py`

---

#### T7. STATE.failed / task_results 일관 갱신 — reporting 과소계상 해결 — 2-3시간

**증상**: `cycle.py`의 다수 fail 분기(needs_dependency, blocked_dependency, build_failed, test_failed, policy_violation)가 STATE.failed만 갱신하고 task_results는 누락. `reporting.py:476-521`이 task_results만 집계 → SHUTDOWN_REPORT가 실패 수 과소계상.

**현재 구현**:
- 갱신 누락 분기: `cycle.py:2330-2362, 2394-2422, 2557-2579, 2633-2655, 2711-2738`
- `reporting.py:516-520 build_cycle_change_summary` — task_results만 카운트
- 일부 분기(`fast_regression_failed`, `exhausted_attempts`, `no_commits`, 정상 완료)만 task_results.append 호출

**제안**:
- 옵션 A: 모든 fail 분기에서 `task_results.append({...})` 일관 호출 (반복 코드 증가)
- 옵션 B: `reporting.py`를 task_results 대신 `state.failed` 기반으로 전환 (단일 정보원)
- **권장**: 옵션 B + helper `state_to_task_result(failed_item) -> dict` 추가

**테스트 케이스**:
- `tests/test_reporting.py`에 케이스 추가 — STATE.failed에 5개, task_results에 0개일 때 SHUTDOWN_REPORT가 5개로 카운트하는지

**영향 받는 파일**:
- `agent_runner/reporting.py`
- 또는 `agent_runner/cycle.py` (옵션 A 선택 시)

---

### Tier 2 — 운영 KPI / 안정성 보강 (Roadmap, 1주 분량)

> 구현 상태(2026-04-29): T8~T13 완료. 아래 제안 본문은 최초 검토 당시의 작업 명세로 보존하며, 실제 구현 요약은 0장을 기준으로 본다.

#### T8. `cycle_failed`에 task_status 반영 — blocked_env로 stop 안 되게 — 2시간

**증상**: `cycle.py:3549-3557 cycle_failed=(rc != 0)` 만 보고 task_status 무관. blocked_env 3회 = 전체 run stop.

**제안**: cycle_failed 판정에서 `state.failed` 마지막 N개 항목의 task_status를 보고, 모두 `blocked_env`이면 stop 카운트 제외. 또는 cycle_failed 자체를 `outcome.action == "stop_run"` 로 전환 (T1과 결합).

**영향 받는 파일**: `agent_runner/cycle.py`, `agent_runner/backends/claudecode.py`

---

#### T9. failed 카운터 3그룹 분리 — 운영 KPI 시그널 정상화 — 2-3시간

**증상**: SHUTDOWN_REPORT와 web 카드가 환경 문제(blocked_env)와 진짜 회귀를 동일하게 빨갛게 표시. 운영자가 git revert 충동.

**제안**:
- `reporting.py:516-520`과 `web.py:4215-4221`에서 카운터를 3개로 분리:
  - `tasks_regressed`: `failed`, `regression_failed`
  - `tasks_review`: `review_required`, `test_contract_changed`
  - `tasks_blocked_env`: `blocked_env`
- web 대시보드 카드 3개로 분리, 색상도 분리(빨강/주황/파랑)
- SHUTDOWN_REPORT 텍스트 템플릿 갱신

**영향 받는 파일**:
- `agent_runner/reporting.py`
- `agent_runner/web.py`
- `web_console/app.js` (대시보드 카드 / 색상)

---

#### T10. blocked_env 분기에서 `DEPENDENCIES_NEEDED.md` 기록 — 운영자 가이드 — 1-2시간

**증상**: blocked_env로 분류돼도 무엇을 설치해야 하는지 운영자가 알 수 없음. Web UI는 `failure.detail` 한 줄만 표시.

**제안**:
- `cycle.py:2326`의 needs_dependency 분기와 동일한 형식으로 blocked_env 분기에서도 부록 추가:
  ```
  ## {task.id}: {title}
  Detected env signals: {matched ENV pattern keywords}
  Validation log: {attempt_dir}/build.txt or test.txt
  Branch: {tb.branch_name}
  Suggested install: {heuristic guess based on matched pattern}
  ```
- web `failure.detail`에 `dependencies_guide_path` 키 추가

**영향 받는 파일**: `agent_runner/cycle.py`, `agent_runner/backends/claudecode.py`(T3 결합), `agent_runner/web.py`

---

#### T11. `count_consecutive_title_failures`에 task_status 필터 — persistent skip 오염 방지 — 1시간

**증상**: T5 의 컬럼 추가 후, `task_history.py:679-682`가 task_status="blocked_env" 행도 카운트해서 환경 문제로 영구 격리되는 태스크 발생.

**제안**: T5에서 추가한 `exclude_task_statuses=("blocked_env",)` 인자를 `shared_runtime.py:542-579 select_next_task_with_dependency_checks` 호출에서 사용.

**영향 받는 파일**: `agent_runner/pipeline/shared_runtime.py`

---

#### T12. ReDoS 완화 — text 입력 캡 + 정규식 사전 컴파일 — 2시간

**증상**: catastrophic backtracking은 없지만, 큰 stderr(수 MB Playwright trace 또는 C++ template)에서 ~42개 패턴 × IGNORECASE+DOTALL 선형 스캔 → 누적 비용 증가. retry 루프와 결합 시 한 cycle 멈춤 가능.

**제안**:
- `task_status.py:_validation_text` 진입에서 `text = "\n".join(parts)[:65536]` 캡 적용
- 모든 패턴을 모듈 레벨 `_compiled = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in patterns]`로 사전 컴파일
- `_matches_any`도 컴파일된 패턴 받게 변경

**테스트 케이스**: `tests/test_task_status.py`에 큰 입력(1MB) 케이스 — 분류 정확도 유지 + latency 측정 (skip if slow)

**영향 받는 파일**: `agent_runner/task_status.py`

---

#### T13. 빈 reason → completed 방어 — `treat_empty_as_completed` 인자 — 30분

**증상**: `task_status.py:122-124`. `normalized_reason in {""}` → COMPLETED. 향후 호출자가 reason을 잊으면 phantom success.

**제안**:
```python
def classify_task_failure(
    reason: str,
    *,
    validations: Sequence[dict[str, Any]] | None = None,
    detail: str = "",
    treat_empty_as_completed: bool = False,
) -> str:
    normalized_reason = str(reason or "").strip().lower()
    if treat_empty_as_completed and normalized_reason == "":
        return TASK_STATUS_COMPLETED
    if normalized_reason in {"ok", "done", "passed", "success", TASK_STATUS_COMPLETED}:
        return TASK_STATUS_COMPLETED
    if normalized_reason == "":
        return TASK_STATUS_REVIEW_REQUIRED  # safe default
    # ...
```
호출자 중 명시적으로 success를 의도하는 곳만 `treat_empty_as_completed=True` 사용.

**영향 받는 파일**: `agent_runner/task_status.py`, `agent_runner/cycle.py`(`_write_task_validation_artifact` 호출 검토), `tests/test_task_status.py`

---

## 5. GOALS.md 추가 권장 (선택)

이 작업들을 AgentCLI가 자동 진행하게 하려면 `.doc/GOALS.md`에 다음을 P0 묶음으로 추가하면 된다 (사용자 승인 전 미적용):

```markdown
### P0-N. Task Result Classification — Disposition Dispatch

- [ ] A `failure_policy` module decides task disposition (retry / preserve_for_review / abandon_branch / restore_checkpoint / stop_run) from (reason, task_status, attempt budget) so codex and claude backends share one policy.
- [ ] `_isolate_or_stop` consumes a `FailureOutcome` so `blocked_env` and `test_contract_changed` tasks are preserved for human review instead of abandoned.
- [ ] `backends/claudecode.py` records failures with the same `task_status` enriched schema as `cycle.py` so failover does not produce mixed STATE.json schemas.
- [ ] `task_status.py` classifier covers Java/Go/Rust/C/C++/Kotlin/Swift/Maven/Gradle/NuGet/Cargo regression and dependency-resolution patterns; tests cover at least ten multi-language cases.
- [ ] `task_history` SQLite stores `task_status` so PM's next-run failed-tasks block distinguishes environment-blocked tasks from real regressions; consecutive-failure skip excludes `blocked_env`.
- [ ] `needs_dependency` and `blocked_dependency` reasons are first-class `BLOCKED_ENV` mappings in `classify_task_failure` (no text-pattern reliance).
- [ ] SHUTDOWN_REPORT and Web Console split task counters into regression / review-needed / blocked-env groups so environment failures do not look like code regressions.
```

위 7개 P0 항목은 T1~T7과 1:1 대응되며, 각각 한 task 브랜치 분량이다. T8~T13은 P1 묶음으로 추가 가능.

---

## 6. 검증 못한 항목

- `pytest tests/test_task_status.py` 실제 실행 (Codex sandbox 정책상 차단 — 환경 사용자 측 검증 필요)
- 정규식 4MB 로그 처리 시 실측 latency
- `web_console/app.js`의 `BACKLOG_BUCKETS` 정의 위치 (상수명 부재, 로컬 buckets 배열 구현만 확인)
- `claudecode.py`가 `pipeline/shared_runtime.py` 통해 간접적으로 분류를 받는지 (shared_runtime.py 일부만 확인)
- `failover_enabled=true` 환경에서 Codex↔Claude 전환 시 STATE.json 실제 손상 시나리오 (시뮬레이션 미수행)

---

## 7. 검토 메타정보

| 항목 | 값 |
|---|---|
| Reviewer A (정합성) | Critical 4 / Warning 6 / Info 4 |
| Reviewer B (다국어 안정성) | Critical 4 / Warning 4 / Info 3 + 매트릭스 10행 |
| Reviewer C (회귀성) | Critical 4 / Warning 4 / Info 3 |
| Reviewer D (운영가시성+보안) | Critical 4 / Warning 4 / Info 3 |
| Codex (gpt-5.4 xhigh, read-only) | 합의점 11개 검증 (✅5/⚠️3/❌3), 추가 발견 6개, 과대평가 반박 3개 |
| 결과 산출물 (raw) | `~/.claude/codex-results/2026-04-29-pipeline-review.md` |

**최종 합의**: 분류기 자체는 잘 만들어졌다. 분류 결과를 받는 쪽(격리 / 카운터 / 백엔드 / 이력 DB)이 거의 모두 옛 reason만 본다 — 라벨링은 했지만 행동은 그대로다. T1~T7 Hot-fix를 순서대로 적용하면 사용자 의도가 실현된다.
