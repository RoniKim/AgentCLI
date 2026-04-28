← [README로 돌아가기](../README.md)

> 최종 검증: 2026-04-28 (코드 기준)

# 트러블슈팅 (문제 상황 및 해결)

## 1. 인증/로그인 문제

### `codex CLI not found in PATH`

**원인:** Codex CLI가 설치되지 않았거나 PATH에 없음
**해결:**
```bash
npm install -g @openai/codex
codex login   # ChatGPT 구독 계정으로 로그인
```

### Claude 인증 실패

**원인:** `claude-agent-sdk` 미설치 또는 로그인 만료
**해결:**
```bash
pip install -U claude-agent-sdk
claude auth login
```

## 2. 할당량 소진 (Quota Exhausted)

**증상:** 실행 중 갑자기 중단, `SHUTDOWN_REPORT.md`에 `quota_exhausted` 기록
**감지 키워드 (대표):** `insufficient_quota`, `quota_exhausted`, `exceeded your current quota`, `usage limit`, `billing hard limit`, `you've hit your usage limit`, `purchase more credits`, `upgrade to pro`, `credit balance is too low`, `usage cap`, `token limit exceeded`, `api key limit`, `limit resets` (전체 목록은 `OPERATIONS.md` 또는 `agent_runner/utils.py:_has_quota_text` 참조)

**해결:**
```
방법 1: Codex 크레딧 충전 확인 (ChatGPT 구독 상태 점검)
방법 2: 페일오버 설정으로 자동 백엔드 전환
```
```json
{
  "failover_enabled": true,
  "failover_backends": ["codex", "claudecode"],
  "failover_on": ["quota_exhausted"],
  "failover_max_switches": 1
}
```

## 3. PM 구조화 출력 파싱 실패

**증상:** PM이 JSON 대신 일반 텍스트를 반환, `[PM] Structured parse failed` 로그

**자동 복구 흐름:**
```
1차: repair 프롬프트로 재시도 (최대 pm_structured_retries회)
2차: BACKLOG.json 파일이 이미 있으면 파일에서 로드
3차: P0 폴백 백로그 생성 ("PM_FAILURE.md 작성" 단일 태스크)
```

**수동 해결:**
- `pm_structured_retries` 값 증가 (기본 2)
- 프롬프트 튜닝: `prompts_dir/pm_instructions.md` 수정
- `pm_bootstrap_max_turns` / `pm_incremental_max_turns` 증가 (PM에게 더 많은 턴 허용)

## 4. BACKLOG가 비어있어서 중단 (`no_tasks`)

**증상:** Dev 단계에서 `no_tasks`로 즉시 종료
**원인:** PM이 빈 태스크 목록을 생성했거나 모든 태스크가 이미 완료

**해결:**
- 레포에 목표/할 일을 더 명확히 기술 (README, 이슈 등)
- `/todo --save`로 오늘 할 일을 TODO로 만들어 PM에게 전달
- `pm_refresh_backlog=true`로 매 사이클 백로그 재생성 강제

## 5. Dev가 코드를 변경하지 않음 (no_diff)

**증상:** `[Dev] No diff detected` 후 태스크 실패 처리

**자동 복구:**
- `dev_escalate_on`에 `no_diff` 포함 시 → 상위 모델로 에스컬레이션
- `dev_auto_escalate=true` + `dev_max_escalations > 0` 필요

**수동 해결:**
```bash
# 방법 1: no_diff를 허용
--allow-no-diff

# 방법 2: 에스컬레이션 설정
{
  "dev_auto_escalate": true,
  "dev_max_escalations": 2,
  "dev_escalate_on": ["no_diff", "build_failed", "test_failed", "fast_regression_failed", "no_commits"]
}
```

## 6. 빌드/테스트 실패 후 무한 루프

**증상:** 에스컬레이션 반복 후에도 계속 빌드 실패

**보호 장치 (이미 내장):**
```
태스크당 최대 에스컬레이션: budgets.max_dev_escalations_per_task (기본 2)
전체 실행 에스컬레이션 총량: budgets.max_total_escalations_per_run (기본 10)
전체 실행 continuation 총량: budgets.max_total_continuations_per_run (기본 10)
```

**해결:**
- 빌드 명령이 올바른지 확인: `build_cmd`, `test_cmd` 점검
- 빌드 게이트 자체를 끄고 진행: `--no-build`
- 예산 한도 조정:
```json
{
  "budgets": {
    "max_dev_escalations_per_task": 1,
    "max_total_escalations_per_run": 3
  }
}
```

## 7. MaxTurnsExceeded (턴 초과)

**증상:** `[Dev] Max turns exceeded` — 에이전트가 제한 턴 안에 작업 미완료

**자동 복구:** Continuation으로 이어서 실행 (최대 `dev_max_turns_continuations`회)

**수동 해결:**
```json
{
  "max_turns_per_task": 20,
  "dev_max_turns_continuations": 3,
  "claudecode_max_turns": 48
}
```

## 8. 롤백 차단 (Rollback Blocked)

**증상:** `ROLLBACK_BLOCKED.md` 파일 생성, `[STOP] Rollback blocked` 로그

**원인:** `dangerous_git_rollback=false` (기본 안전 모드)에서 롤백 시도

**해결:**
```
방법 1 (권장): worktree 격리 모드 사용 — 원본 repo 보호
  --worktree-isolation

방법 2 (주의): 파괴적 롤백 허용
  --dangerous-git-rollback
```

## 9. `npx`를 찾을 수 없음

**원인:** Node.js 미설치
**해결:** Node.js 설치 후 `npx -v` 확인

## 10. Claude SDK 스트림 오류

**증상:** `ClaudeSDKClient does not provide a message stream`

**원인:** `claude-agent-sdk` 버전 불일치/구버전
**해결:**
```bash
pip install -U claude-agent-sdk
```

## 11. 타임아웃

**증상:** 특정 단계에서 시간 초과 후 종료

**관련 설정 및 기본값:**

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `pm_timeout_seconds` | 900 (15분) | PM 단계 |
| `dev_timeout_seconds` | 900 (15분) | Dev 단계 |
| `mcp_timeout_seconds` | 120 (2분) | MCP 서버 통신 |
| `test_timeout_seconds` | 3600 (1시간) | 테스트 실행 |
| `build_timeout_seconds` | 1800 (30분) | 빌드 실행 |

**해결:** 필요에 따라 타임아웃 값 증가

## 12. 모델 인식 실패 (model_not_found)

**증상:** `model_not_found`, `does not exist`, `unknown model` 에러

**원인:** 설정한 모델 이름이 API에서 지원하지 않는 이름
**해결:**
- Codex (현행 DEFAULTS, `cli.py`): `pm_model=gpt-5.5`, `dev_model=gpt-5.4-mini -> gpt-5.4 -> gpt-5.5`, `qa_model=gpt-5.5`, `reporter_model=gpt-5.4-mini`
- Claude: `sonnet`, `opus`, `haiku` 중 선택
- 에스컬레이션이 활성화되어 있으면 `dev_model` → `dev_model_tier1` → `dev_model_tier2` 순서로 자동 승격

## 13. 예산 초과 (BudgetExceeded)

**증상:** `[STOP] BudgetExceeded` 로그, 남은 태스크가 있지만 실행 중단

**원인:** 에스컬레이션/continuation/repair 횟수가 `budgets` 한도를 초과

**자동 동작:**
```
per_task 한도 초과 → 해당 태스크만 실패, 다음 태스크 진행
per_run 한도 초과  → BudgetExceeded 예외 → Reporter 종료
```

**해결:**
```json
{
  "budgets": {
    "max_dev_escalations_per_task": 3,
    "max_total_escalations_per_run": 15,
    "max_total_continuations_per_run": 15
  }
}
```

> Enterprise 프로필에서는 per_run 한도에 최소 상한이 강제됩니다. 이 경우 프로필을 `personal`로 변경하거나 한도 내에서 운영하세요.

## 14. Continuation 소진 (턴 초과 반복)

**증상:** `[Dev] MaxTurnsExceeded` 반복 후 태스크 실패, continuation이 남아있지 않음

**원인:** 태스크가 너무 크거나, max_turns가 너무 작음

**자동 복구 흐름:**
```
MaxTurnsExceeded 발생
  │
  ├─ per_task continuations 남아있음
  │    → "[CONTINUE]" 프롬프트로 이어서 실행
  │    → 부분 진행 보존 (git diff 유지)
  │
  ├─ per_task continuations 소진 + per_run 한도 내
  │    → 현재까지 변경사항으로 게이트 진행
  │
  └─ per_run continuations 한도 초과
       → BudgetExceeded → 실행 중단
```

**해결:**
```json
{
  "max_turns_per_task": 30,
  "dev_max_turns_continuations": 5,
  "claudecode_max_turns": 64,
  "budgets": {
    "max_dev_continuations_per_task": 5,
    "max_total_continuations_per_run": 20
  }
}
```

> 태스크를 작게 분할하는 것이 턴/continuation을 늘리는 것보다 효과적입니다.

## 15. Failover 전환 실패

**증상:** Failover가 활성화되어 있지만 백엔드 전환이 일어나지 않음

**원인 및 해결:**

| 원인 | 해결 |
|------|------|
| stop_reason이 `failover_on`에 없음 | `failover_on` 목록에 해당 사유 추가 |
| `failover_max_switches` 소진 | 값 증가 (기본 1) |
| 다음 백엔드 Preflight 실패 | 대상 백엔드의 API 키/SDK 사전 설치 |
| 이미 마지막 백엔드 | `failover_backends`에 백엔드 추가 |

```json
{
  "failover_enabled": true,
  "failover_backends": ["codex", "claudecode"],
  "failover_on": ["quota_exhausted"],
  "failover_max_switches": 2
}
```

> `/doctor`로 모든 백엔드의 환경을 사전 점검하세요.

## 16. Plugin Stage 로딩 실패

**증상:** `[Pipeline] Plugin blocked` 또는 `[Pipeline] Plugin load failed`

**원인:** 플러그인이 allowlist에 없거나, 모듈을 찾을 수 없음

**해결:**
```json
{
  "plugins_enabled": true,
  "plugins_allowlist": ["my_pkg.*", "my_pkg.stages:MyStage"],
  "plugins_strict": true
}
```

| 체크 포인트 | 설명 |
|-------------|------|
| `plugins_enabled` | `true`여야 플러그인 로드 시도 |
| `plugins_allowlist` | 패턴이 모듈 경로와 일치해야 함 |
| `plugins_strict` | `true`면 차단 시 즉시 실패, `false`면 경고만 |
| 모듈 경로 | Python import path 형식 (`pkg.module:ClassName`) |

## 17. Worktree 패치 충돌

**증상:** `[Worktree] Patch apply failed` — worktree에서 작업 성공했지만 원본 repo에 패치 적용 실패

**원인:** worktree 작업 중 원본 repo에 다른 변경이 생김

**해결:**
```bash
# 패치 파일 위치 확인
ls <run_dir>/worktree.patch

# 방법 1: 강제 적용 (reject 파일 생성)
git apply --reject --whitespace=nowarn <run_dir>/worktree.patch

# 방법 2: 3-way 머지 시도
git apply --3way --whitespace=nowarn <run_dir>/worktree.patch

# 방법 3: 수동 적용
# .rej 파일들을 확인하고 수동으로 충돌 해결
```

> Worktree 패치는 `<run_dir>/worktree.patch`에 항상 보존됩니다. 원본 repo는 무손실입니다.

## 18. Preflight 체크 실패

**증상:** 실행 시작 전 에러 메시지와 함께 즉시 종료

**원인별 해결:**

| 에러 | 원인 | 해결 |
|------|------|------|
| `repo path does not exist` | 잘못된 경로 | `--repo` 경로 확인 |
| `not a git repository` | git 미초기화 | `git init` 실행 |
| `codex not found` | Codex CLI 미설치 | `npm install -g @openai/codex` 후 `codex login` |
| `claude-agent-sdk not installed` | SDK 미설치 | `pip install -U claude-agent-sdk` |
| `build tool not found` | .NET/커스텀 도구 없음 | 도구 설치 또는 `--no-build` |

```bash
# 전체 환경 진단
python agent_cli.py --repo <path>
> /doctor
```

## 19. Config 버전 마이그레이션

**증상:** 이전 버전 config 로드 시 일부 설정이 기본값으로 리셋됨

**원인:** `config_version=1` → `2` 자동 마이그레이션 시 다음 값이 변경될 수 있음:
- `dev_max_turns_continuations`: `0` → `2` (기본값)
- `pm_max_turns_continuations`: `0` → `1` (기본값)

**해결:**
```bash
# config 파일 확인
> /config --all

# 필요 시 명시적으로 값 재설정
> /set dev_max_turns_continuations 0
> /set pm_max_turns_continuations 0
> /save
```

> `config_version` 값을 직접 수정하지 마세요. 마이그레이션은 자동으로 처리됩니다.

## 20. 스캔 제한 초과

**증상:** `[Scan] Skipped: max files/bytes exceeded` — 정책/보안 스캔이 부분적으로만 실행됨

**원인:** 프로젝트 크기가 스캔 한도를 초과

**관련 설정:**

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `scan_scope` | `"quick"` | 스캔 범위: quick/staged/full |
| `scan_max_files` | (코드 내 기본) | 최대 스캔 파일 수 |
| `scan_max_total_bytes` | (코드 내 기본) | 최대 스캔 총 바이트 |
| `scan_timeout_seconds` | (코드 내 기본) | 스캔 타임아웃 |
| `scan_ignore_globs` | `[".doc/**", "*.log"]` | 제외 패턴 |

**해결:**
```json
{
  "scan_scope": "quick",
  "scan_ignore_globs": [".doc/**", "*.log", "node_modules/**", "dist/**"],
  "policy_scan_scope": "staged",
  "security_scan_scope": "quick"
}
```

> `full` 스캔은 대규모 프로젝트에서 매우 느릴 수 있습니다. `quick` → `staged` → `full` 순서로 점진적으로 올리세요.

## 21. PM이 자기참조 태스크 생성

**증상:** 백로그에 "백로그 작성", "프로젝트 분석" 같은 메타 태스크가 포함됨

**원인:** PM 에이전트가 자기 역할을 태스크로 위임하려 함

**자동 복구:**
- 내장 필터가 "백로그 생성", "분석 작성" 등의 자기참조 키워드를 자동 제거
- 태스크 ID 정규화: `T1`, `T2`, ... 형식 강제
- 스킬 ID 검증: `SKILLS_INDEX`와 대조, 없는 스킬 경고

**수동 해결:**
- PM 프롬프트 튜닝: `prompts_dir/pm_instructions.md`에 "실제 코딩 태스크만 출력" 지침 강화
- `pm_structured_retries` 증가 (repair 프롬프트가 자기참조도 교정 시도)

## 22. CLI 로그인 만료

**증상:** 실행 중 인증 실패 에러

**원인:** Codex 또는 Claude CLI 로그인 세션이 만료됨

**해결:**
```bash
# Codex 백엔드
codex login

# Claude 백엔드
claude auth login
```

> AgentCLI는 `.env` 파일이나 API Key를 사용하지 않습니다. 모든 인증은 CLI 로그인 기반입니다.

## 종료 사유 (Stop Reason) 우선순위

`utils.py:STOP_REASON_PRIORITY` 기준 (낮을수록 우선):

| 우선순위 | 사유 | 설명 |
|----------|------|------|
| 1 | `quota_exhausted` | API 할당량 소진 (failover 트리거) |
| 2 | `quota_utilization` | OAuth 사용량 임계치 초과 (선제적 wait/stop) |
| 3 | `stop_file` | STOP 파일 감지 (graceful stop) |
| 4 | `project_complete` | GOALS.md P0(+P1) 모두 완료 |
| 5 | `all_tasks_done` | 백로그 태스크 전부 done |
| 6 | `all_tasks_attempted` | 모든 태스크 시도 완료 (실패 포함) |
| 7 | `prepared_only` | continuous 미설정 — 백로그만 준비하고 종료 |
| 8 | `idle_exit` | loop 모드 유휴 타임아웃 (`loop_idle_exit_after`) |
| 9 | `ok` | 정상 종료 |

**우선순위 표에 없는 보조 reason** (반환되지만 priority dict에는 없음 → `len(priority)`로 후순위 처리, 외부 루프에서 별도 dispatch):

| 사유 | 처리 |
|------|------|
| `no_tasks` | `GOALS_REFRESH_RESCUABLE_REASONS`에 포함 — goals auto-refresh 시도 후 재진입 |
| `pm_refresh_no_backlog` | 동일 — refresh 미가용/실패 시 idle/exit 폴백 |

> `STOP_REASON_PRIORITY`에 직접 등장하지 않는 사유라도 `choose_stop_reason()` 호출 시 후순위로 평가됩니다. 새 reason 추가 시 `_PROPAGATE_STOP_REASONS` (manager.py)와 `GOALS_REFRESH_RESCUABLE_REASONS` (goals.py) 분류도 함께 검토하세요.

## 23. Windows 핸들/메모리 누수 (재부팅 전까지 회복 불가)

**증상:**
- 장시간 운영 후 `agent_cli.py` 종료 시 `error.log` 파일 락이 풀리지 않음
- Python 자식 프로세스가 좀비로 남아 작업 관리자에 누적
- 재시작 시 로그 디렉토리 쓰기 실패 / `WinError 32: process cannot access the file`
- 메모리 사용량이 비정상적으로 누적

**원인 (확인된 사례):**
- Windows에서 `process_guard.py` Job Object가 종료 경로를 모두 회수하지 못함
- `_CodexAppServerClient`(또는 SDK 채널) 등 일부 구독이 명시적 `close()` 없이 GC에 위임됨
- `StructuredLogger`의 파일 핸들이 닫히지 않은 채 프로세스가 종료
- 한 번 누수가 발생하면 OS 레벨 핸들이 잠겨, **현 세션에서는 회복 불가능**

**참조 인시던트:** `.doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md`

**임시 완화책 (재부팅 전까지의 차선책):**

```text
1. /stop --wait 후 충분한 wait 부여 (stop_wait_timeout_seconds=300+ 권장)
2. process_guard Job Object 재초기화 — agent_cli.py 재진입 시 새 Job 생성
3. _CodexAppServerClient.close() 명시 호출 — 종료 hook에서 정리 보장
4. close_all_loggers() — StructuredLogger 핸들 일괄 close
5. Codex CLI / Claude CLI 자식 프로세스 작업 관리자에서 수동 종료 (taskkill /F /IM codex.exe /T)
```

**근본 해결:**
- **재부팅이 유일한 회복 경로**입니다. OS가 모든 좀비 핸들을 해제할 때까지 동일 디렉토리에서 재실행해도 락이 풀리지 않습니다.
- 재부팅 후 `.AgentCLI/agent_runs/<old>/logs/` 잔존 핸들 정리 후 새 run_dir 시작을 권장합니다.

> 장기 실행 환경에서는 `worktree-isolation` + 작은 `loop_max_cycles`로 분할 운영해 누수 노출을 줄이세요. 인시던트 재발 시 `.doc/Docs/incidents/`에 새 보고서를 추가하세요.
