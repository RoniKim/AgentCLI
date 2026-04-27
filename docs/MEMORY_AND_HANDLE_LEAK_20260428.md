# Windows 핸들/프로세스 누수로 인한 시스템 슬로우다운 문제

**작성일**: 2026-04-28
**상태**: 분석 완료, 수정 미적용
**영향**: HIGH — 재부팅 전까지 회복 불가, 사용자 워크플로우 차단

---

## 1. 증상 (Observed Symptoms)

AgentCLI Runner를 장시간 구동한 환경(집 노트북, Windows 11)에서 다음 증상이 보고됨:

1. **시스템 전반 슬로우다운**
   - 메모리/CPU 사용량은 작업관리자 기준 정상
   - 마우스/입력/앱 응답이 점진적으로 느려짐
   - **AgentCLI 종료만으로는 회복되지 않음 — 재부팅 필요**

2. **`error.log` 파일 커널 락**
   - 파일 삭제·이동·수정이 모두 거부됨
   - 재부팅 전까지 락 해제 불가

3. **Python 좀비 프로세스**
   - Python 프로세스가 종료됐는데 `tasklist` / 작업관리자에 살아있는 것으로 표시됨
   - 정상 kill로 정리되지 않음

### 환경 조건
- Windows 11 Pro
- 집 노트북 (회사 EDR/백업 에이전트 없음)
- Windows Defender Real-Time Protection: **비활성화 상태**
- AgentCLI: **CLI 전용** (web 대시보드 미사용), **단일 인스턴스**
- 사용자: `iskim@entecene.co.kr`

---

## 2. 진단 과정 — 기각된 가설들

다라운드 토론(4 Critical + 1 Neutral + 1 Synthesizer)을 거쳐 다음 가설들은 사용자 환경 정보로 **기각** 또는 **현재 무관**으로 판정:

| 기각된 가설 | 기각 근거 |
|------------|----------|
| Web 대시보드 폴링 + `build_snapshot()` 중복 I/O | CLI 전용 사용 |
| Multi-instance pythonw watchdog 누적 | 단일 인스턴스 운영 |
| 회사 EDR/백업 에이전트 간섭 | 집 노트북 |
| Defender real-time scan 폭주 | RTP 비활성화 |
| `agent_runs/` 디렉토리 누적 | 디렉토리 미생성 + FS 누적은 재부팅으로 회복되지 않음 |
| `task_history.db` 풀 스캔 | 0.01MB (사실상 빈 DB) |
| Logger rotation 부재로 인한 디스크 폭증 | 재부팅으로 회복되지 않으므로 현 증상과 무관 |

**핵심 신호**: "재부팅 전까지 회복 불가" = **OS 커널 레벨 자원 누수**의 직접 증거. user-mode 자원(메모리, 디스크, 파일)은 프로세스 종료 시 회복되지만 kernel object(Process, File handle, TCP socket, Pool memory)는 reference 해제 전까지 잔존.

---

## 3. 근본 원인 가설 — Process Object → File Handle 연쇄 누수

### Windows 커널 동작 원리

> 누군가 `OpenProcess()`로 프로세스 핸들을 가지고 있으면, 그 프로세스가 죽어도 **`Process Object`가 커널에 남는다(좀비 상태)**. 좀비 프로세스가 보유했던 일부 file handle은 외부 reference 사이에 즉시 해제되지 않을 수 있다.

사용자가 본 증상 ("좀비 Python" + "error.log 파일 락")은 **같은 메커니즘**으로 설명된다.

### 연쇄 시나리오

1. AgentCLI 시작 → process_guard L5: detached `pythonw.exe` watchdog spawn
2. Watchdog이 부모(AgentCLI Python)에 대해 `OpenProcess(SYNCHRONIZE)` → **process 평생 동안 핸들 보유**
3. AgentCLI가 비정상 종료 (Ctrl+C 후 hang, 강제 종료, 사용자 강제 kill, BSOD 직전 등)
4. 정상 경로: 부모 죽음 → watchdog `WaitForSingleObject` 깨어남 → `CloseHandle` → Process Object 해제 → 종료
5. **이상 경로**: watchdog 자체가 위 4단계 도중 hang/crash → `CloseHandle` 미실행 → **부모 Process Object가 영구 좀비**
6. 좀비가 보유했던 `logging.FileHandler` 파일 핸들(debug.log, error.log, run.log, events.jsonl)이 해제되지 않음
7. → 사용자가 `error.log` 파일 락 만남
8. → **재부팅으로 커널이 모든 reference 강제 해제 전까지 회복 불가**

---

## 4. 코드 레벨 root cause 후보

### 1순위: `process_guard._wait_for_parent_exit` 무기한 핸들 보유

**파일**: `agent_runner/process_guard.py:966-988`

```python
def _wait_for_parent_exit(parent_pid, parent_create_time=None):
    if sys.platform == "win32":
        handle = kernel32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION,
            False, int(parent_pid)
        )
        if handle:
            try:
                kernel32.WaitForSingleObject(handle, _INFINITE)  # 무기한 대기
                return
            finally:
                kernel32.CloseHandle(handle)  # 정상 경로에서만 호출됨
```

**문제점**:
- `_INFINITE` 대기 + watchdog process가 hang/crash하면 `CloseHandle` 미실행
- fast path는 `parent_create_time`을 검증하지 않음 → PID 재사용 시 다른 프로세스를 기다림 (별도 위험)

### 2순위: subprocess PIPE 핸들 누수

**파일**: `agent_runner/utils.py:633` (`_CodexAppServerClient.__init__`)
**파일**: `agent_runner/utils.py:127` (`run_cmd_capture`)
**파일**: `agent_runner/codex_exec.py:272` (`codex_exec`)

```python
self._proc = subprocess.Popen(
    [resolved, "app-server", ...],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    ...  # close_fds 명시 없음, PIPE handle inheritable
)
```

`_CodexAppServerClient.close()` (`utils.py:677-696`):
- `proc.terminate()` + `wait(timeout=2)` 만 수행
- `self._reader.join()` **누락** — daemon thread가 stdout EOF 못 받으면 PIPE handle 영구 보유
- `self._proc.stdout.close()` / `self._proc.stdin.close()` 명시적 호출 없음

자식이 비정상 종료(SIGKILL/`TerminateProcess`) → child의 PIPE write 끝은 닫히지만 parent의 read 끝이 열린 채 잔존 가능.

### 3순위: Logger `FileHandler`의 무한 보유 + cleanup 미보장

**파일**: `agent_runner/logger.py:76, 87, 98`

```python
debug_handler = logging.FileHandler(self.debug_log, encoding="utf-8")
error_handler = logging.FileHandler(self.error_log, encoding="utf-8")
run_handler   = logging.FileHandler(self.run_log,   encoding="utf-8")
```

- `FileHandler`는 logger 객체 lifetime 동안 파일을 계속 열어둠
- `close()` 메서드(`logger.py:333`)와 `close_all_loggers()`(`logger.py:397`)가 정의돼 있으나 **`atexit.register()`에 등록되어 있는지 확인 필요**
- `os._exit()` 등 강제 종료 경로에서는 호출되지 않음
- 1순위 가설(좀비 Process Object)와 결합 시 → 파일 락 영속화

### 4순위 (안전성): PID 재사용으로 무관 프로세스 kill 위험

**파일**: `agent_runner/process_guard.py:974-980`

`_wait_for_parent_exit` fast path가 `parent_create_time` 미검증. Windows는 PID 재사용이 빠름 → 부모 종료 직후 OS가 같은 PID를 다른 프로세스에 재할당하면 watchdog가 그 무관 프로세스를 추적하다 cleanup 단계에서 종료할 위험. **현 증상의 직접 원인은 아니지만 같은 모듈에서 함께 수정 권장**.

---

## 5. 제안 수정 — 우선순위 순

### Fix A (최우선): `_wait_for_parent_exit`를 timeout 기반 polling으로 변경

**파일**: `agent_runner/process_guard.py:966-988`

`OpenProcess` 핸들을 평생 보유하지 않고, `_pid_alive()`(매 호출마다 OpenProcess+CloseHandle 짝지음)을 폴링:

```python
def _wait_for_parent_exit(parent_pid, parent_create_time=None):
    if parent_pid <= 0 or parent_pid == os.getpid():
        return
    while _pid_alive(parent_pid):
        # PID 재사용 검증 (Fix D 통합)
        if parent_create_time is not None:
            actual = _pid_create_time_ticks(parent_pid)
            if actual is not None and actual != parent_create_time:
                return
        time.sleep(2.0)
```

**효과**: watchdog가 부모 Process Object의 reference를 영구 보유하지 않게 됨 → **좀비 발생 차단**.

**비용**: 2초 폴링 (watchdog는 sleep 상태이므로 CPU 무시 가능 수준).

### Fix B: `_CodexAppServerClient.close()` 핸들 cleanup 강화

**파일**: `agent_runner/utils.py:677-696`

```python
def close(self):
    try:
        self._proc.terminate()
        self._proc.wait(timeout=2)
    except Exception:
        terminate_process_tree(self._proc.pid)
    finally:
        # PIPE 명시적 close — Windows에서 reader thread가 EOF 받게 보장
        try:
            if self._proc.stdout: self._proc.stdout.close()
            if self._proc.stdin:  self._proc.stdin.close()
        except Exception:
            pass
        # daemon thread join — 1초 timeout
        if self._reader.is_alive():
            self._reader.join(timeout=1.0)
        if self._registered_pid:
            unregister_pid_if_exited(self._registered_pid)
```

### Fix C: Logger cleanup을 atexit에 등록

**파일**: `agent_cli.py` 또는 `agent_runner/main.py` 진입점

```python
import atexit
from agent_runner.logger import close_all_loggers
atexit.register(close_all_loggers)
```

이미 `close_all_loggers()`가 정의돼 있으므로(`logger.py:397`) atexit 등록만 추가.

### Fix D: PID 재사용 검증

Fix A에 통합되어 함께 적용됨 (`parent_create_time` 비교).

### Fix E (선택): Logger FileHandler를 non-inheritable로 명시

**파일**: `agent_runner/logger.py`

Windows에서 `SetHandleInformation`으로 `HANDLE_FLAG_INHERIT` 끄기. Fix A·B·C 적용 후 재현 여부 보고 결정.

---

## 6. 사용자 진단 명령

다음 슬로우다운 발생 시 **재부팅 전에** 한 번 실행:

```powershell
# 1. 좀비 Python 프로세스 카운트
Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Select-Object ProcessId, ParentProcessId, Name, CreationDate, WorkingSetSize, HandleCount |
  Format-Table

# 2. error.log 파일 핸들 보유자 (Sysinternals handle.exe 필요)
# https://learn.microsoft.com/en-us/sysinternals/downloads/handle
handle.exe -accepteula -nobanner error.log

# 3. 시스템 전체 kernel pool / handle 카운트
Get-Counter '\Memory\Pool Nonpaged Bytes',
            '\Memory\Pool Paged Bytes',
            '\Process(_Total)\Handle Count'

# 4. TIME_WAIT 소켓 (보조 가설 검증)
(netstat -an | Select-String "TIME_WAIT").Count
```

**판정**:
- `handle.exe error.log` 결과에 **이미 종료된 PID** 또는 살아있는 `pythonw.exe`가 잡고 있음 → 1순위 가설 **확정**
- `Pool Nonpaged Bytes` 가 1GB 이상 → 커널 메모리 누수 추가 의심
- `HandleCount` 가 재부팅 직후 대비 2배 이상 → 시스템 전역 핸들 누수

---

## 7. 검증 절차

수정 적용 후:

1. AgentCLI를 baseline 상태로 시작 → 핸들/프로세스 카운트 측정
2. 1시간 운영 → 재측정
3. AgentCLI 강제 종료(Ctrl+C 다중, 작업관리자 kill) → `error.log` 즉시 삭제 가능 여부 확인
4. 재시작 후 좀비 Python 프로세스 잔존 여부 확인

**성공 기준**:
- 강제 종료 후 즉시 `error.log` 파일 삭제 가능
- 작업관리자에 좀비 Python 프로세스 0개
- 1시간 운영 후 시스템 전역 핸들 카운트 증가량 < 100

---

## 8. 관련 파일 인덱스

| 파일 | 라인 | 관련 항목 |
|------|------|----------|
| `agent_runner/process_guard.py` | 966-988 | Fix A — `_wait_for_parent_exit` |
| `agent_runner/process_guard.py` | 974-980 | Fix D — PID 재사용 검증 |
| `agent_runner/process_guard.py` | 1018-1066 | watchdog spawn 경로 |
| `agent_runner/utils.py` | 633-663 | `_CodexAppServerClient.__init__` |
| `agent_runner/utils.py` | 677-696 | Fix B — `_CodexAppServerClient.close` |
| `agent_runner/utils.py` | 127 | `run_cmd_capture` subprocess |
| `agent_runner/codex_exec.py` | 272 | codex subprocess spawn |
| `agent_runner/logger.py` | 76, 87, 98 | Fix E 후보 — `FileHandler` 생성 |
| `agent_runner/logger.py` | 333, 397 | Fix C 호출 대상 (`close`, `close_all_loggers`) |
| `agent_cli.py` | 진입점 | Fix C 등록 위치 |

---

## 9. 추가 분석 자료

본 문서는 다라운드 에이전트 토론(2 round, 5 agent 병렬 dispatch) 결과를 종합한 것이며, 다음 분석 결과들이 반영됨:

- Critical-1 (Windows handle/process): L5 watchdog `pythonw` 누적, PID 재사용 취약점, `_CodexAppServerClient` reader join 누락
- Critical-2 (I/O saturation): logger rotation 부재 + per-event flush + atomic_write fsync (현 증상과 무관, latent risk)
- Critical-3 (background services): web.py `build_snapshot` 중복 I/O, Telegram daemon thread 정리 누락 (CLI 전용 환경에서 무관)
- Critical-4 (filesystem accumulation): `agent_runs/` retention 부재, task_history 인덱스 부재 (현 증상과 무관)
- Neutral-1 (architecture): 가설들 간 상관관계 매트릭스, 진단 우선순위

---

## 10. 후속 조치

- [ ] Fix A 구현 (process_guard.py)
- [ ] Fix B 구현 (utils.py)
- [ ] Fix C 구현 (agent_cli.py atexit 등록)
- [ ] Fix D 통합 (Fix A에 포함)
- [ ] 검증 절차 수행
- [ ] 결과에 따라 Fix E 추가 검토
- [ ] Latent risk fix (logger rotation, agent_runs retention, task_history index, analysis_cache cap) 별도 PR로 분리
