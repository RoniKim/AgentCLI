← [README로 돌아가기](../README.md)

> 최종 검증: 2026-04-28 (코드 기준)

# 안전/운영 옵션 (Git, Stop, No-diff)

## Stop file로 안전 종료

- 기본 stop 파일: `STOP`
- `run_dir/STOP` 파일이 생기면 graceful stop

Shell:
```text
> /stop
> /stop --wait
```

**Stop 대기 타임아웃 (`stop_wait_timeout_seconds`)**

| 키 | 기본값 | 설명 |
|----|--------|------|
| `stop_wait_timeout_seconds` | `180` (3분) | `/stop --wait` 또는 web stop이 러너 종료를 기다리는 최대 시간. 러너 자체는 STOP 신호를 계속 인식하지만, 이 값은 **운영자(쉘/웹)** 측 대기 윈도우만 제어. 초과 시 "Runner is still alive after Ns stop wait timeout." 메시지로 보고하고 컨트롤을 반환. |

> 러너가 Reporter/디스크 플러시 등으로 길게 마무리하는 워크플로면 적당히 늘리는 것을 권장합니다 (예: 300-600). 강제 종료가 필요하면 `process_guard`가 별도로 처리합니다.

## "변경 없음(no diff)" 정책

기본값:
- 태스크 수행 후 `git diff`가 없으면 실패로 간주하고 중단(토큰 낭비 방지)

계속 진행하려면:
```bash
python agent_cli.py --run-now --repo <path> --non-interactive --autopilot --continuous --allow-no-diff
```

## Git 체크포인트 / 롤백

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

## Worktree 격리 모드 (권장: 안전하게 오래 돌릴 때)

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

## 파괴적 롤백(비권장, 명시적으로만)

```bash
python agent_cli.py --run-now --repo <path> --dangerous-git-rollback
```

---

# 예산 가드레일 (Budget Guardrails)

API 비용 폭주를 방지하기 위해 에스컬레이션/continuation/repair 횟수에 상한을 설정합니다.

## budgets 객체

```json
{
  "budgets": {
    "max_pm_structured_retries": 2,
    "max_dev_escalations_per_task": 2,
    "max_dev_continuations_per_task": 2,
    "max_total_escalations_per_run": 10,
    "max_total_continuations_per_run": 10,
    "max_total_repair_attempts_per_run": 5
  }
}
```

## 항목별 설명

| 항목 | 기본값 | 설명 |
|------|--------|------|
| `max_pm_structured_retries` | 2 | PM JSON 스키마 repair 최대 횟수 |
| `max_dev_escalations_per_task` | 2 | **태스크 1개**에서 모델 에스컬레이션 최대 횟수 |
| `max_dev_continuations_per_task` | 2 | **태스크 1개**에서 continuation(턴 초과 이어서 실행) 최대 횟수 |
| `max_total_escalations_per_run` | 10 | **실행 전체**에서 에스컬레이션 총 횟수 |
| `max_total_continuations_per_run` | 10 | **실행 전체**에서 continuation 총 횟수 |
| `max_total_repair_attempts_per_run` | 5 | **실행 전체**에서 PM repair 총 횟수 |

## 예산 초과 시 동작

```
예산 한도 도달
  │
  ├─ per_task 한도 → 해당 태스크만 실패 처리, 다음 태스크로 진행
  │
  └─ per_run 한도 → BudgetExceeded 예외 발생 → Reporter로 종료
```

## 비용 절감 프리셋

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

## 무제한 실행 프리셋 (주의)

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

# 빌드/테스트 게이트

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

# 정책/시크릿 스캔(옵션)

- `scan_scope="quick"` (기본)
- 상한: `scan_max_files`, `scan_timeout_seconds`, `scan_max_total_bytes`
- 제외: `scan_ignore_globs`, `scan_ignore_paths`

> 프로젝트가 커질수록 "quick → staged/full"은 신중히 올리는 것을 권장합니다.

---

# 산출물(Artifacts) 구조

## run_dir (실행 단위)

기본:
- `REPO/.AgentCLI/agent_runs/<YYYYMMDD-HHMMSS>/`

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

## STATE.json 구조

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

## PM_CACHE (지속 분석 아티팩트)

기본:
- `REPO/.AgentCLI/PM_CACHE/`

대표 파일:
- `PROJECT_ANALYSIS.md` — 프로젝트 구조/기술스택/현황 분석
- `REPO_INVENTORY.json|md` — 파일 목록/메타데이터
- `REPO_SNAPSHOT.json` — repo fingerprint

---

# Preflight 체크 & 환경 검증

## Preflight 체크 (자동 실행)

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
│     │   └─ codex CLI 설치 및 로그인 (codex login)         │
│     └─ Claude:                                            │
│         ├─ claude-agent-sdk 설치 여부                     │
│         └─ claude CLI 로그인 (claude auth login)          │
│                                                           │
│  3. 빌드/테스트 도구 (no_build=false일 때)                 │
│     ├─ .NET SDK (기본) 또는 커스텀 build_cmd 실행 가능    │
│     └─ test_cmd 실행 가능 (run_tests=true일 때)           │
│                                                           │
│  4. run_dir 준비                                          │
│     ├─ 신규 생성: REPO/.AgentCLI/agent_runs/<timestamp>/   │
│     └─ resume_latest: 가장 최근 run_dir 재사용            │
│                                                           │
└───────────────────────────────────────────────────────────┘
```

> Preflight 실패 시 에러 메시지와 함께 즉시 종료됩니다. `/doctor`로 사전 점검을 권장합니다.

## Failover (backend 체인)

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
       │    → Preflight 재검증 (CLI 로그인, SDK 등)
       │    → 성공 시 실행 계속
       │    → 실패 시 최종 종료
       │
       └─ max_switches 소진
            → 최종 종료
```

**할당량 소진 감지 키워드** (`utils.py:_has_quota_text` 단일 진실 공급원):
- OpenAI / 일반 결제: `insufficient_quota`, `quota exceeded`, `exceeded your current quota`, `quota_exhausted`, `billing hard limit`, `hard limit`, `plan and billing`, `plans & billing`, `payment required`, `budgetexceeded`
- Codex CLI 사용량 한도: `you've hit your usage limit`, `you've hit your limit`, `hit your limit`, `purchase more credits`, `upgrade to pro`, `codex/settings/usage`, `usage limit`, `user limit`, `user_limit`, `credit balance is too low`, `insufficient credits`, `purchase credits`, `spend limit`, `monthly spend limit`
- Claude Code CLI 한도: `usage cap`, `reached your`, `token limit exceeded`, `account limit`, `api key limit`, `limit resets`

> Failover는 **환경이 사전에 준비**되어야 성공합니다. 양쪽 백엔드 모두 `/doctor`로 점검하세요.

## /doctor (환경 진단)

Shell에서 `/doctor`를 실행하면 run_dir에 진단 보고서(`DOCTOR.md`)가 생성됩니다.

```text
> /doctor
```

**진단 항목 (15개):**

| # | 카테고리 | 검사 내용 | 상세 |
|---|----------|-----------|------|
| 1 | **Git** | `git --version`, 레포 `is-inside-work-tree` | 버전 출력, git 레포 여부 |
| 2 | **Config** | config JSON 로드 | 경로 + 파싱 성공 여부 |
| 3 | **run_dir** | 쓰기 테스트 (임시파일 생성→삭제) | 경로 + 쓰기 가능 여부 |
| 4 | **CLI 인증** | `codex` CLI, `claude` CLI 설치 여부 | found / NOT found |
| 5 | **프로필/정책** | profile, policy enabled, security enabled | 현재 설정값 |
| 6 | **Backend Preflight** | 각 백엔드별 `run_preflight()` | OK/FAIL + 이슈 상세 |
| 7 | **빌드 도구** | `build_cmd`, `test_cmd` 첫 실행파일 | `shutil.which()` 검증 |
| 8 | **Prompts 디렉토리** | `resolve_prompts_dir()` 경로 존재 여부 | override .md 파일 개수 |
| 9 | **Skills 시스템** | `skills.enabled` 시 roots 존재, SKILL.md 발견 수 | 경고: enabled인데 0개 |
| 10 | **Task History** | `task_history_enabled` 시 SQLite DB 접근 | `query_history()` 호출 |
| 11 | **Goals** | `goals_enabled` 시 GOALS.md 존재/파싱 | P0/P1 완료 현황 (done/total) |
| 12 | **TODO** | `.AgentCLI/todo` 디렉토리 + 오늘 TODO 내용 | has content / empty |
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
- codex CLI: found
- claude CLI: found
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

# 보안 메모

- 시크릿/토큰은 절대 README/config/prompt에 하드코딩하지 마세요. 인증은 **CLI 로그인 기반**(`codex login` / `claude auth login`)으로만 관리합니다.
- worktree 패치(`worktree.patch`)는 변경 내용을 포함합니다. 외부 공유 전 민감정보 포함 여부를 점검하세요.
