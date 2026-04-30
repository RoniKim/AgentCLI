# 무인운영 신뢰성 — 검증 결과와 후속 작업

> 작성일: 2026-04-28
> 검증 방식: 4개 병렬 코드 아키비스트 에이전트가 file:line 단위로 사실 확인 (`agent-debate` 스킬 + 후속 dispatch)
> 목적: "wait → 자동 이어가기"·"continuous 이어하기"·"mid-kill recovery"·"multi-window overnight" 기능의 실제 구현 상태를 확정하고, 남은 갭을 후속 작업 단위로 명세화

---

## TL;DR

- **이미 구현되어 작동하는 것**: quota wait → auto continue, `--loop` continuous mode, `--resume-latest` / `--run-dir` 명시적 재개, multi-window 5h sleep 견디기, goals auto-refresh rescue dispatch
- **남아있는 갭 (총 2-3일 작업 분량)**: stale STOP 자동 정리, sleep 중 HEARTBEAT 갱신, mid-merge pending marker 자동 정리, stale task branch 정리, `goals_auto_refresh` 기본값 정책 결정, Claude 백엔드 wait의 STOP 응답성 비대칭
- **잘못 알려진 것 (폐기)**: "sleep-and-resume이 미구현" — 실제로는 80% 이상 구현됨. "Codex thread_id 재활용" — codex_exec CLI에서 기술적 불가능. "Anthropic prompt caching" — ChatGPT 구독 모델에서 무관

---

## 1. 검증 결과 매트릭스

| 영역 | 상태 | 핵심 근거 |
|------|------|---------|
| **Codex quota wait → auto continue** | ✅ 완전 구현 | `cycle.py:2795-2864`. `sleep_or_stop` (`cycle.py:285-296`)이 1초 단위 polling. wait time cap 없음. STOP 파일 자동 unlink 후 sleep, `consecutive_failures=0` 리셋, `continue` |
| **Claude quota wait → auto continue** | 🟡 작동하지만 비대칭 | `claudecode.py:2921-2972`. mid-cycle path가 raw `await asyncio.sleep(wait_sec)` (`claudecode.py:2967`) — sleep 도중 STOP 파일 감지 안 됨. Codex와 비대칭 |
| **5h sleep 견디기** | ✅ 작동 | `seconds_until_unix_reset` (`utils.py:839-847`)이 cap 없이 +60s 버퍼만 추가해 그대로 전달. 18,000초 sleep을 1초 polling으로 chunk |
| **다음 cycle 자연스러운 재진입** | ✅ 작동 | sleep 후 `continue`로 outer loop의 `for cycle_idx`가 다음 iteration 진입. PM이 `incremental` 모드로 STATE/BACKLOG 그대로 이어감 |
| **`--loop` / `--continuous`** | ✅ 완전 구현 | `cli.py:126-130, 139`. `loop_max_cycles=0` → 1000 fallback (`cycle.py:2572-2574`). `continuous=False` 시 `prepared_only`로 PM만 돌고 종료 (`pipeline/manager.py:76-78`) |
| **`--resume-latest` / `--run-dir`** | ✅ 구현 | `cli.py:369-370`, `run_dir.py:30-45`. `find_latest_run_dir`은 lexicographic sort로 latest 선택 |
| **STATE.json → PM 컨텍스트 주입** | ✅ 구현 | `backlog_utils.py:276-315 load_backlog_context_for_pm` — done/failed/pending을 `[x]/[F]/[ ]` 체크리스트로 PM 프롬프트에 주입 |
| **Outer loop reason dispatch** | ✅ 완전 구현 | 11개 stop reason 모두 처리. `_PROPAGATE_STOP_REASONS`, `GOALS_REFRESH_RESCUABLE_REASONS` frozenset dispatch. `cycle.py:2795-2935`, `claudecode.py:2921-3047` |
| **Goals auto-refresh rescue** | 🟡 구현됐으나 default=False | `goals.py:308-336 GOALS_REFRESH_RESCUABLE_REASONS = {PROJECT_COMPLETE, NO_TASKS, PM_REFRESH_NO_BACKLOG}`. `should_attempt_goals_refresh()` 판정 5단계 모두 구현. 단 `goals_auto_refresh` 기본값 False (`cli.py:309`) |
| **SIGINT/SIGTERM 정상 종료 → 재개** | ✅ 작동 | `process_guard.py:662-687`이 STOP 파일 + 자식 정리. 다음 `--resume-latest`에서 STATE.json 기반 재개 |
| **Hard kill 후 직접 CLI 재개** | ❌ 갭 | stale STOP 파일이 즉시 종료 트리거. `/start` 인터랙티브 shell만 unlink (`shell.py:548-549`), `--run-now`/`--resume-latest` 직접 호출 시 unlink 없음 |
| **Mid-worktree-merge kill 후 재개** | ❌ 갭 | `WORKTREE_MERGE_PENDING.json`이 "pending"으로 남고, 다음 시도 시 validation 실패. `/merge-worktree` 또는 `/discard-worktree` 수동 호출 필요 |
| **Mid-task kill 후 재개** | 🟡 부분 작동 | STATE.json done/failed는 정확히 보존. 단 stale `task/<id>_<ts>` 브랜치 자동 정리 없음, `attempt_NN` 디렉토리 무한 누적 |
| **HEARTBEAT 기반 liveness 감지** | ❌ 없음 | `write_heartbeat`은 cycle 시작 시 1회만. 5h sleep 동안 stale. `metrics.jsonl`에 `quota_event=exhausted_wait/resumed` 기록은 됨 |
| **Backlog generation scoping** | 🟡 부분 구현 | `state.py:254-261 load_backlog_task_ids`, `:264-286 count_state_task_ids`가 현재 backlog ID로 필터. `done_set` membership test는 legacy ID 포함 가능. `pipeline/shared_runtime.py:350-383 detect_and_clear_recycled_ids`가 (title, prompt) 일치로 reconciliation |
| **Failover (codex ↔ claudecode)** | ✅ 구현 | `failover_enabled`, `failover_on={quota, quota_utilization, ...}`, `failover_max_switches`. `runner_entry.py:144-149`가 백엔드 전환 시 STOP 파일 정리 |

---

## 2. 후속 작업 (우선순위 + 추정 공수)

### Tier 1 — 무인운영 안정성 (필수)

#### T-A. Stale STOP 파일 자동 정리 — 1-2시간
**증상**: SIGKILL/handle leak로 강제 재부팅 후 `python agent_cli.py --run-now --resume-latest` 실행하면 stale STOP 파일이 남아있어 즉시 종료.

**현재 구현**:
- `shell.py:548-549` — `/start` 시 unlink (있음)
- `remote/controller.py:1268-1274` — 원격 시작 시 unlink (있음)
- `runner_entry.py` — direct invocation 시 unlink **없음**
- `cycle.py:2620-2622`, `claudecode.py:2812-2814` — STOP 존재 시 즉시 break

**제안**:
- `runner_entry.run` 직전, 또는 `cycle.py` / `claudecode.py` 진입부에서 STOP 파일 mtime 검사
- 옵션 A: `--ignore-existing-stop` CLI 플래그 추가 (사용자 명시적 의사)
- 옵션 B: STOP 파일 mtime이 일정 시간(예: 1시간) 이상 오래됐으면 자동 unlink + 경고 로그
- 옵션 C: HEARTBEAT 검사와 결합 — HEARTBEAT이 stale이면 STOP도 stale로 간주

**테스트 케이스**:
- STOP 파일 미리 생성 → `--resume-latest` 호출 → 자동 정리 후 정상 시작 확인
- 새로 생성한 STOP 파일은 그대로 유지되는지 (race 방지) 확인

---

#### T-B. Sleep 중 HEARTBEAT 갱신 — 1시간
**증상**: 5h quota wait 동안 HEARTBEAT 파일 mtime이 5시간 stale → Web Console / 외부 모니터가 runner 죽음으로 오인.

**현재 구현**:
- `cycle.py:2625`, `claudecode.py:2817` — cycle 시작 시 1회 호출
- `utils.py:935-940 write_heartbeat` — 단순 timestamp 쓰기
- `sleep_or_stop` (`cycle.py:285-296`) — 1초 polling 루프 내부에 heartbeat 갱신 없음

**제안**:
- `sleep_or_stop` 내부 polling 루프에서 N초마다 (예: 30s) `write_heartbeat(run_dir)` 호출
- 또는 별도 `sleep_with_heartbeat(run_dir, sec, stop_path, hb_interval=30)` 헬퍼 추가
- Claude 백엔드의 raw `asyncio.sleep` (`claudecode.py:2967`)도 동일 헬퍼로 교체 (T-D와 결합)

**테스트 케이스**:
- 60초 sleep 시뮬레이션 중 HEARTBEAT mtime이 30초 이내로 유지되는지

---

#### T-C. Stale `WORKTREE_MERGE_PENDING.json` 자동 진단/정리 — 반나절
**증상**: mid-merge kill 후 marker가 "pending"으로 남고, 다음 attempt가 validation 실패. P0-L 항목 중 "stale 마커 검출"은 검출만 하고 자동 정리 없음.

**현재 구현**:
- `gitops.py:1245+ scan_worktree_diagnostics` — 보고만
- `gitops.py:2011-2179 apply_pending_worktree_merge` — atomic이 아님 (apply_patch → remove_worktree → status write 사이에 kill 시 stale)
- `/merge-worktree`, `/discard-worktree` shell command 만 정리 가능

**제안**:
- 시작 시 `pending` marker 검사 → 다음 중 하나면 자동 discard + 로그:
  1. `patch_path` 파일 부재
  2. 현재 source HEAD가 `base_ref`와 다르고 `expected_head`도 아님 (이미 적용됐을 가능성)
  3. `current_patch_hash != patch_hash` (patch 변경됨)
- 자동 정리 정책은 config로 토글 가능하게 (`worktree_auto_reconcile_stale_pending: True/False`)
- 자동 정리 시 `WORKTREE_MERGE_DISCARDED.json` artifact 작성해 사후 추적

**테스트 케이스**:
- pending marker + patch 파일 삭제 시나리오 → 자동 discard 확인
- pending marker + 정상 base_ref + 정상 patch hash → 그대로 유지 확인

---

#### T-D. Claude 백엔드 wait의 STOP 응답성 (대칭성 회복) — 1시간
**증상**: Claude 백엔드 mid-cycle quota wait 도중 STOP 파일 / SIGINT 보내도 sleep 끝까지 안 깨어남.

**현재 구현**:
- Codex `cycle.py:2856` — `sleep_or_stop` (1초 polling, STOP-aware)
- Claude `claudecode.py:2967` — raw `asyncio.sleep(wait_sec)` (응답성 없음)

**제안**:
- `sleep_or_stop` 헬퍼를 `claudecode.py`에서도 사용 (또는 공통 모듈로 추출)
- T-B의 `sleep_with_heartbeat`로 통합 가능

**참고**: Codex 메인 사용 시 직접적 영향 없음. Failover로 Claude 진입 시에만 노출되므로 우선순위 낮음.

---

### Tier 2 — 운영 정책 결정

#### T-E. `goals_auto_refresh` 기본값 정책 — 정책 결정 + 1-2시간 적용
**증상**: 5h reset 후 빈 backlog 시 자동 GOALS 갱신으로 이어가려면 `goals_auto_refresh=True` 필요. 현재 기본 False.

**현재 구현**:
- `cli.py:309` — `goals_auto_refresh: False` 기본
- `goals.py:308-347 should_attempt_goals_refresh` — 5단계 판정 모두 구현
- `_try_goals_refresh` async closure — Codex/Claude 양쪽 대칭 구현

**제안**:
- 옵션 A: 단순 default=True 변경 (가장 무인운영 친화적)
- 옵션 B: `--unattended` 프리셋 도입 — `goals_auto_refresh`, `quota_wait_for_reset`, `loop`, `loop_idle_exit_after`를 묶어 one-liner로 활성화
- 옵션 C: 그대로 유지하고 docs/CONFIG_REFERENCE_KO.md에 무인운영 권장 설정 명시

**의사결정 필요**: 현재 사용자 워크플로우(인터랙티브 vs 무인) 비중

---

#### T-F. Stale task branch / attempt_NN 정리 정책 — 반나절
**증상**: kill 후 남은 `task/<id>_<oldts>` 브랜치, `attempt_NN/` 디렉토리가 무한 누적.

**현재 구현**:
- `gitops.py` — `merge_task_branch`, `abandon_task_branch` 존재하나 정상 종료 경로에서만 호출
- attempt 디렉토리 — 재실행 시 `attempt_(N+1)`로 신규 생성

**제안**:
- 시작 시 (또는 `/doctor` 명령) `task/` 브랜치 중 done/failed 어디에도 없고 N일 이상 오래된 것 정리 후보로 표시
- 자동 abandon은 위험하니 default off, `--cleanup-stale-branches` 명시적 옵션
- attempt 디렉토리는 단순 archive (압축) 또는 mtime 기반 prune

**우선순위**: 즉시 영향 없음. 디스크 누적 + 시각적 노이즈 문제만.

---

### Tier 3 — 별개 트랙 (앞서 합의된 다른 우선순위)

#### T-G. Phantom completion 강화 — 2-3일
**별도 작업**. Sleep-and-resume과 무관. 본 문서 범위 외.

위치: `gitops.py:218 has_new_commits` (현재 단순 `before_head != current` 6줄). `gitops.py:233 git_changed_files` 활용해 task.files vs git diff actuals cross-check 추가.

---

#### T-H. P0-Q items 3, 7 — 각 2-3일
**별도 작업**. GOALS.md `P0-Q-3` ("self-runs cannot stop early with reason=ok while goals incomplete"), `P0-Q-7` ("failed tasks carried into next PM prompt via structured block").

---

#### T-I. Topological scheduler + budget caps — 1주
**별도 작업**. `schemas.BacklogTaskV2`에 `effort: Literal["S","M","L"]` 추가. `backlog_utils.normalize_backlog_tasks`에 `depends_on` 위상정렬 + 윈도우 잔여시간 기반 cutoff. **단순히 effort 필드만 추가하는 게 아니라** 스케줄러 로직까지 포함.

---

## 3. 폐기된 제안 (이전 분석 정정)

### 3-1. "Codex thread_id 재활용" — 폐기
- `CodexExecResult.thread_id`는 `codex_exec.py:49,95,421`에서 파싱·할당만 됨
- `codex_exec()` 함수 시그니처에 thread_id 입력 파라미터 없음
- Grep 전수조사 결과 다운스트림 read 사이트 0건
- Codex CLI 자체가 stateless subprocess라 thread 재개 불가능 가능성 높음
- **재검토하려면**: Codex CLI 공식 문서에서 `--resume`/`--thread-id` 플래그 존재 여부 1시간 spike 후

### 3-2. "Anthropic prompt caching 적용" — Codex 메인 환경에서 N/A
- ChatGPT 구독 = 메시지 카운트 기반 quota 모델 (토큰 단가 청구 아님)
- OpenAI prompt caching의 자동 50% 할인은 API key 청구에만 적용
- 코드베이스 grep 결과 `cache_control`/`ephemeral` 0건 (Anthropic 미사용 확인)

### 3-3. "Sleep-and-resume에 1주 투자" — 정정
- 검증 결과 80% 이상 구현됨
- 남은 건 본 문서 T-A ~ T-D (총 2-3일 분량)
- 1주 단위 신규 기능이 아니라 마무리 갭 메우기

---

## 4. 부록: 검증 방법 재현

본 문서의 사실 주장은 모두 file:line 단위 코드 검증 기반. 재검증 시:

```bash
# Quota wait 흐름 — Codex
grep -n "quota_wait_for_reset\|sleep_or_stop\|seconds_until_unix_reset" agent_runner/cycle.py

# Quota wait 흐름 — Claude
grep -n "quota_wait_for_reset\|asyncio.sleep" agent_runner/backends/claudecode.py

# Outer loop reason dispatch
grep -n "GOALS_REFRESH_RESCUABLE_REASONS\|_PROPAGATE_STOP_REASONS\|STOP_REASON_" \
  agent_runner/cycle.py agent_runner/backends/claudecode.py agent_runner/pipeline/manager.py agent_runner/goals.py

# Resume capability
grep -n "resume_latest\|find_latest_run_dir\|--run-dir" agent_runner/cli.py agent_runner/run_dir.py

# STOP 파일 lifecycle
grep -n "stop_path\|STOP_FILE\|stop_path.unlink" \
  agent_runner/shell.py agent_runner/cycle.py agent_runner/backends/claudecode.py \
  agent_runner/runner_entry.py agent_runner/remote/controller.py

# HEARTBEAT
grep -n "write_heartbeat\|HEARTBEAT" agent_runner/

# Worktree merge atomicity
grep -n "apply_pending_worktree_merge\|WORKTREE_MERGE_PENDING\|_write_pending_status" \
  agent_runner/gitops.py
```

---

## 5. 변경 이력

| 날짜 | 변경 | 근거 |
|------|------|------|
| 2026-04-28 | 최초 작성 | 4개 병렬 코드 검증 + agent-debate 합성 결과 |
| 2026-04-30 | P0-X GOALS 매핑 추가 | 장시간 무인운영 후 stale STOP, heartbeat, Claude wait, stale cleanup, scheduler, diagnostics 후속 작업을 실행 단위로 분리 |

---

## 6. GOALS 매핑

본 문서의 후속 작업은 `.doc/GOALS.md`의 `P0-X. Unattended Operations Follow-Up`에 실행 단위로 반영한다.

| 문서 항목 | GOALS 매핑 |
|-----------|------------|
| T-A. Stale STOP 파일 자동 정리 | `P0-X` direct runner/resume stale STOP reconciliation |
| T-B. Sleep 중 HEARTBEAT 갱신 | `P0-X` long sleeps/quota waits/loop idle heartbeat refresh |
| T-C. Stale `WORKTREE_MERGE_PENDING.json` 자동 진단/정리 | `P0-X` startup stale pending marker reconciliation |
| T-D. Claude 백엔드 wait의 STOP 응답성 | `P0-X` Claude shared STOP-aware sleep helper |
| T-E. `goals_auto_refresh` 기본값 정책 | `P0-X` unattended preset for goals refresh, quota wait, loop, diagnostics, cleanup defaults |
| T-F. Stale task branch / attempt_NN 정리 정책 | `P0-X` stale branch/attempt doctor listing and explicit cleanup artifacts |
| T-G. Phantom completion 강화 | partially covered by `P0-S`; future changes should stay branch/ref aware |
| T-H. P0-Q items 3, 7 | already covered by completed `P0-Q` items |
| T-I. Topological scheduler + budget caps | `P0-X` backlog scheduling effort/priority/touches/dependencies and remaining-window budget caps |
| Windows CMD/Explorer instability diagnostics | `P0-X` Windows handle/process diagnostic linkage and anomaly flags |

`P0-X` is intentionally separate from completed `P0-S` items. `P0-S` captures fixes already implemented or documented; `P0-X` captures follow-up work still needed for long unattended operation.

---

## 관련 문서

- `.doc/GOALS.md` — P0-Q (Safe Self-Development Automation) 8개 항목, 본 문서 T-G/T-H와 직접 관련
- `.doc/Docs/ARCHITECTURE.md` — 전체 아키텍처
- `CLAUDE.md` (project root) — Stop reason 상수, frozenset dispatch 패턴, outer loop reason handling 흐름
- `docs/CONFIG_REFERENCE_KO.md` — `quota_*`, `loop_*`, `goals_*`, `failover_*` 설정 키 레퍼런스
