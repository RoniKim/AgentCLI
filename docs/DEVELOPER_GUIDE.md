← [README로 돌아가기](../README.md)

> 최종 검증: 2026-04-28 (코드 기준)

# 개발자 가이드 (확장)

## 커스텀 Stage 추가

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

## StageOutcome 반환값

| 메서드 | 의미 | 파이프라인 동작 |
|--------|------|----------------|
| `StageOutcome.ok(reason)` | 성공 | 다음 Stage 진행 |
| `StageOutcome.skip(reason)` | 건너뜀 | 다음 Stage 진행 |
| `StageOutcome.stop(reason, rc)` | 즉시 중단 | 파이프라인 종료 |
| `StageOutcome.fail(reason, rc)` | 실패 | 파이프라인 종료 |

> `STOP_REASON_ALL_TASKS_DONE`, `STOP_REASON_PROJECT_COMPLETE`는 `StageOutcome.ok()`로 반환하여 QA Stage까지 실행 후 종료합니다. `STOP_REASON_QUOTA`, `STOP_REASON_STOP_FILE`은 `StageOutcome.stop()`으로 즉시 종료합니다.

## 커스텀 Backend 추가

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

## PM 분석 캐시

**위치**: `REPO/.AgentCLI/PM_CACHE/`

| 파일 | 설명 |
|------|------|
| `PROJECT_ANALYSIS.md` | 프로젝트 구조/기술스택 분석 (PM이 유지) |
| `REPO_INVENTORY.json` | 파일 목록 메타데이터 |
| `REPO_INVENTORY.md` | 사람이 읽을 수 있는 파일 트리 |
| `REPO_SNAPSHOT.json` | repo fingerprint (변경 감지용) |

**PM 모드 결정 (cycle.py `run_pm_if_needed()`)**:

PM 출력 스키마 `PMOutputV2.kind`는 다음 4개 값 중 하나입니다:
- `bootstrap` — PROJECT_ANALYSIS.md 없음 → 전체 분석 + 초기 백로그 생성
- `incremental` — 변경 파일 또는 working tree fingerprint 변동 감지 → 변경분만 업데이트
- `refresh` — `pm_refresh_backlog=true` + `pm_refresh_every_cycles` 주기 도달 → 백로그 재구성
- `skip` — 변경 없음 + refresh 주기 미도달 → 기존 백로그 재사용

```
need_bootstrap   = not analysis_md.exists()
need_incremental = (changed_files != []) or
                   (pm_include_working_tree and repo_fp != last_pm_fp)
force_refresh    = pm_refresh_backlog and (cycle_idx % pm_refresh_every_cycles == 0)

         ┌─ analysis_md 없음 ───────────────► kind="bootstrap"
         │
run_pm ──┼─ need_incremental ───────────────► kind="incremental"
         │
         ├─ force_refresh ──────────────────► kind="refresh"
         │
         └─ 그 외 ──────────────────────────► kind="skip"  (백로그 재사용)
```

**fingerprint 구성 요소** (`gitops.repo_fingerprint`):
- `git_head` (HEAD SHA)
- working tree hash (변경된 파일의 해시 합산, `pm_include_working_tree=true`일 때만)

`REPO_SNAPSHOT.json`에 `{"fingerprint": "...", "updated_at": "..."}` 형태로 저장되어 다음 사이클의 변경 감지에 사용됩니다.

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

# 메트릭스 & 로깅

## 로그 파일 구조

```
run_dir/
└── logs/
    ├── run.log           # INFO+ 메시지 (항상 생성)
    ├── debug.log         # DEBUG+ 메시지 (debug=true일 때)
    ├── error.log         # ERROR만
    └── events.jsonl      # 구조화 이벤트 (JSONL)
```

## events.jsonl 형식

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

## metrics.jsonl 형식

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

## StructuredLogger 주요 메서드

| 메서드 | 설명 |
|--------|------|
| `debug(msg)` | 디버그 로그 (`debug_enabled=true`일 때만 기록) |
| `info(msg)` | 정보 로그 |
| `warning(msg)` | 경고 로그 (events.jsonl에 `level=warning`으로 기록) |
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

# 프로세스 안전 (Process Guard)

## 5-Layer 보호 체계

AgentCLI는 자식 프로세스(Codex CLI, Claude Code CLI 등)가 **부모 종료 후에도 남아있는 문제(orphan process)**를 방지하기 위해 5층 보호 체계를 사용합니다.

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
┌─ Layer 5: Parent Watchdog (detached pythonw.exe) ──────────┐
│  부모가 SIGKILL/크래시로 L1~L3를 우회해 죽어도 살아남는      │
│  분리(detach)된 헬퍼 프로세스. Windows에서 pythonw.exe로     │
│  실행되어 콘솔 없이 동작하며, 부모 PID + create_time을 추적. │
│  부모 종료 감지 시 세션 PID 파일 기반으로 자식 정리.         │
│  CREATE_BREAKAWAY_FROM_JOB 플래그로 부모 Job Object와 분리. │
└────────────────────────────────────────────────────────────┘
```

## 주요 함수

| 함수 | 설명 |
|------|------|
| `init_process_guard()` | Layer 1~5 초기화 (runner_entry.py에서 호출) |
| `register_pid(pid)` | 자식 프로세스 PID 등록 |
| `unregister_pid(pid)` | 자식 프로세스 PID 해제 |
| `terminate_all_children()` | 등록된 모든 자식 프로세스 종료 |
| `_start_parent_watchdog(session_dir)` | L5 watchdog 헬퍼 프로세스 분리 기동 (Windows 한정) |

## 스레드 안전성

- 모든 변경 가능 상태는 `RLock`으로 보호 (재진입 안전)
- Signal handler에서도 `terminate_all_children()` 안전 호출 가능
- Job Object 핸들은 의도적으로 프로세스 수명 동안 열려있음 (조기 닫힘 방지)
- L5 watchdog은 별도 프로세스(독립 메모리/시그널)로 동작하므로 부모 충돌과 무관하게 자식 정리 가능

---

# 추가 서브시스템 모듈

CLAUDE.md에는 등재되어 있으나 본 가이드에 별도 항목으로 다루지 않은 모듈들입니다.

| 모듈 | 라인 수 | 역할 |
|------|---------|------|
| `agent_runner/web.py` | ~8300 | FastAPI 기반 Web Console 진입점 (run 모니터링/제어, SSE 이벤트 스트리밍) |
| `agent_runner/remote/controller.py` | ~1850 | HTTP 원격 제어 엔드포인트 (외부 트리거/상태 조회, 토큰 인증) |
| `agent_runner/remote/telegram_service.py` | ~1490 | Telegram bot 연동 — 알림 송신 + 명령 수신 |
| `agent_runner/stop_progress.py` | ~580 | Stop signal 단계별 진행 추적 (request → ack → drain → terminate) |
| `agent_runner/pipeline/shared_runtime.py` | ~920 | 백엔드-비종속 Dev 루프 헬퍼 (Codex/Claude 양쪽에서 공유) |

이 모듈들은 옵트인 기능이며, 기본 CLI 실행 경로(`--run-now` / interactive shell)는 모두 없이도 동작합니다.

---

# 개발/테스트

단위 테스트(있는 경우):
```bash
python -m pytest -q
```
