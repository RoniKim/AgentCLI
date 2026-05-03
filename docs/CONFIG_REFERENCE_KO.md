# AgentCLI 설정(Config) 변수 레퍼런스

설정 파일 위치: `configs/<프로젝트명>.json`

> 최종 검증: 2026-04-28 (코드 기준 — `agent_runner/cli.py` DEFAULTS와 일치)

---

## 1. 코어 / 경로

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `repo` | string | `""` | 대상 저장소(repo)의 루트 경로. 필수값. |
| `config` | string | `""` | 설정 파일 경로. 비어있으면 `configs/<repo-slug>.json` 자동 사용. |
| `config_version` | int | `2` | 설정 파일 버전. 마이그레이션 로직에서 사용. 수동 변경 불필요. |
| `run_dir` | string | `""` | 실행 기록 디렉토리. 비어있으면 자동 생성. |
| `resume_latest` | bool | `false` | `true`면 가장 최근 run_dir을 이어서 실행. |

---

## 2. 실행 백엔드

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `execution_backend` | string | `"codex"` | 실행 엔진 선택. `"codex"` (OpenAI) 또는 `"claudecode"` (Anthropic Claude). |

---

## 3. Claude Code 백엔드 설정

### 3-1. 기본 옵션

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `claudecode_model` | string | `"sonnet"` | Claude 기본 모델. 역할별 모델이 비어있을 때 폴백으로 사용. |
| `claudecode_permission_mode` | string | `"acceptEdits"` | 도구 권한 모드. `default`, `acceptEdits`, `bypassPermissions`, `plan` 중 선택. |
| `claudecode_max_turns` | int | `32` | Claude 쿼리 1회당 최대 턴(대화 왕복) 수. |
| `claudecode_setting_sources` | string | `"project"` | 설정 소스. 쉼표 구분. `user`, `project`, `local` 조합 가능. |
| `claudecode_system_prompt_append` | string | `""` | Claude 시스템 프롬프트에 추가할 커스텀 지시문. |
| `claudecode_continue_conversation` | bool | `false` | `true`면 이전 대화를 이어서 진행. |
| `claudecode_resume` | string | `""` | 이전 세션 ID를 지정하여 해당 세션을 재개. |
| `claudecode_enable_file_checkpointing` | bool | `false` | 파일 체크포인팅 활성화. 롤백 시 파일 복원 지원. |

### 3-2. Claude Agent SDK 고급 옵션

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `claudecode_user` | string | `""` | 사용자 식별자 (선택). SDK에 전달. |
| `claudecode_include_partial_messages` | bool | `false` | 스트리밍 중 부분 메시지 이벤트 수신 여부. |
| `claudecode_fork_session` | bool | `false` | 세션 재개 시 새 세션 ID로 포크할지 여부. |
| `claudecode_max_thinking_tokens` | int | `0` | 최대 사고(thinking) 토큰 수. `0`이면 SDK 기본값 사용. |

### 3-3. 역할별 도구 허용/차단

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `claudecode_pm_allowed_tools` | string | `"Read,Grep,Glob,Write,Edit"` | PM 단계에서 허용할 도구 목록 (쉼표 구분). |
| `claudecode_pm_disallowed_tools` | string | `""` | PM 단계에서 차단할 도구 목록. |
| `claudecode_dev_allowed_tools` | string | `"Read,Write,Edit,Grep,Glob,Bash"` | Dev 단계에서 허용할 도구 목록. |
| `claudecode_dev_disallowed_tools` | string | `""` | Dev 단계에서 차단할 도구 목록. |
| `claudecode_qa_allowed_tools` | string | `"Read,Grep,Glob,Bash"` | QA 단계에서 허용할 도구 목록. |
| `claudecode_qa_disallowed_tools` | string | `""` | QA 단계에서 차단할 도구 목록. |

### 3-4. Claude Agent SDK 확장 기능

모두 opt-in이며 기본적으로 비활성입니다. `claude_extensions.py`에서 구현.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `claudecode_mcp_tools_enabled` | bool | `false` | 커스텀 MCP 도구 활성화 (`check_state`, `run_build` 등). |
| `claudecode_hooks_enabled` | bool | `false` | PreToolUse/PostToolUse 안전 훅 활성화. 위험 도구 차단, 경로 제한. |
| `claudecode_can_use_tool_enabled` | bool | `false` | 동적 도구 권한 제어 활성화. 역할별 도구 허용/차단 강제. |
| `claudecode_can_use_tool_strict_isolation` | bool | `false` | Dev 단계에서 태스크 대상 파일만 수정 허용 (엄격 격리). |

### 3-5. 서브에이전트 (Subagents)

서브에이전트 마스터 스위치(`claudecode_subagents_enabled`)가 `true`일 때만 동작.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `claudecode_subagents_enabled` | bool | `false` | 서브에이전트 시스템 전체 활성화. |
| `claudecode_subagent_reviewer_enabled` | bool | `true` | code-reviewer 서브에이전트 활성화. 마스터가 `true`일 때만 동작. |
| `claudecode_subagent_runner_enabled` | bool | `true` | test-runner 서브에이전트 활성화. |
| `claudecode_subagent_auditor_enabled` | bool | `true` | security-auditor 서브에이전트 활성화. |
| `claudecode_subagent_reviewer_model` | string | `""` | code-reviewer 전용 모델. 비어있으면 `claudecode_model` 폴백. |
| `claudecode_subagent_runner_model` | string | `""` | test-runner 전용 모델. |
| `claudecode_subagent_auditor_model` | string | `""` | security-auditor 전용 모델. |

### 3-6. 역할별 모델 오버라이드

비어있으면 `claudecode_model` 값으로 폴백.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `claudecode_pm_model` | string | `""` | PM 단계 전용 Claude 모델. (예: `sonnet`, `opus`, `haiku`) |
| `claudecode_dev_model` | string | `""` | Dev 단계 전용 Claude 모델. |
| `claudecode_dev_model_tier1` | string | `""` | Dev 에스컬레이션 1단계 모델. 비어있으면 에스컬레이션 없음. |
| `claudecode_dev_model_tier2` | string | `""` | Dev 에스컬레이션 2단계 모델. 비어있으면 추가 에스컬레이션 없음. |
| `claudecode_qa_model` | string | `""` | QA 단계 전용 Claude 모델. |
| `claudecode_reporter_model` | string | `""` | Reporter(종료 보고서) 전용 Claude 모델. |

**폴백 체인:**
```
역할별 모델 → claudecode_model → "sonnet" (최종 기본값)
```

---

## 4. 파이프라인 / 프로필

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `roles` | string | `"PM,Dev,QA"` | 실행할 파이프라인 단계. 쉼표 구분. 예: `"PM,Dev"` (QA 생략), `"PM,Security,Dev,QA"`. |
| `profile` | string | `"personal"` | 프로필 프리셋. `"personal"` 또는 `"enterprise"`. enterprise는 Security 단계 자동 추가, QA 항상 실행 등. |

---

## 5. 실행 동작 제어

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `autopilot` | bool | `false` | `true`면 사용자 확인 없이 자동 실행. |
| `loop` | bool | `false` | `true`면 사이클을 반복 실행 (데몬 모드). |
| `loop_sleep_seconds` | int | `60` | 루프 사이클 간 대기 시간 (초). |
| `loop_max_cycles` | int | `0` | 최대 루프 횟수. `0`이면 무제한. |
| `loop_idle_exit_after` | int | `0` | 할 일이 없을 때 자동 종료까지 대기 시간 (초). `0`이면 종료 안 함. |
| `max_consecutive_failed_cycles` | int | `3` | 연속 실패 사이클 허용 횟수. 초과 시 파이프라인 자동 중단. |
| `idle_exit_cycles` | int | `3` | 연속 무진전(delta=0) 사이클 허용 횟수. `0`이면 cycle-count 기반 idle 종료를 비활성화. |
| `budget_reset_per_cycle` | bool | `true` | `true`면 매 사이클 시작 시 예산 카운터(에스컬레이션, continuation 등)를 초기화. |
| `continuous` | bool | `false` | `true`면 한 사이클에서 여러 태스크를 연속 처리. |
| `iterations` | int | `30` | 한 사이클에서 처리할 최대 반복(태스크) 수. |
| `max_turns_per_task` | int | `12` | 태스크당 최대 LLM 턴 수. |
| `isolate_task` | bool | `false` | `true`면 각 태스크 시작 시 git 체크포인트를 만들어 실패 시 롤백. |
| `worktree_isolation` | bool | `false` | `true`면 git worktree에서 격리 실행. |

---

## 6. Git 운영 (gitops)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `gitops.untracked_exclude_globs` | list | `[".doc/**", ".AgentCLI/**", ...]` | git untracked 파일 감지 시 제외할 glob 패턴. |
| `gitops.worktree_merge_mode` | string | `"manual"` | worktree 격리 종료 시 병합 방식. `"manual"`이면 자동 병합하지 않고 수동 처리 안내. |

---

## 7. 정책 스캔 (Policy Scan)

### 7-1. 스캔 기본 설정

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `no_policy_scan` | bool | `false` | `true`면 정책 스캔 비활성화. (`policy.enabled`의 역방향 호환.) |
| `policy_rules_file` | string | `""` | 정책 규칙 파일 경로. |
| `policy_rule` | list | `[]` | 인라인 정책 규칙 목록. |
| `scan_scope` | string | `"quick"` | 스캔 범위. `"quick"`, `"staged"`, `"full"` 중 선택. |
| `policy_scan_scope` | string | `""` | 정책 스캔 전용 범위. 비어있으면 `scan_scope` 사용. |
| `security_scan_scope` | string | `""` | 보안 스캔 전용 범위. 비어있으면 `scan_scope` 사용. |

### 7-2. 스캔 리소스 제한

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `scan_max_files` | int | `500` | 스캔 대상 최대 파일 수. |
| `scan_max_bytes_per_file` | int | `200000` | 파일당 최대 스캔 바이트 (~200KB). |
| `scan_max_total_bytes` | int | `20000000` | 전체 최대 스캔 바이트 (~20MB). |
| `scan_timeout_seconds` | int | `60` | 스캔 타임아웃 (초). |
| `scan_ignore_globs` | list | `[".doc/**", ".AgentCLI/**", ...]` | 스캔 시 무시할 glob 패턴. |
| `scan_ignore_paths` | list | `[]` | 스캔 시 무시할 경로 목록. |
| `scan_include_untracked_in_full` | bool | `false` | `full` 스캔 시 untracked 파일 포함 여부. |

### 7-3. 정책 (policy) 상세

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `policy.enabled` | bool/null | `null` | 정책 스캔 활성화. `null`이면 `no_policy_scan`의 역값 사용. |
| `policy.fail_severity` | string | `"high"` | 이 심각도 이상이면 파이프라인 실패 처리. |
| `policy.rules` | list | `[]` | 정책 규칙 목록. |
| `policy.ignore_paths` | list | `[]` | 정책 스캔에서 제외할 경로. |
| `policy.allow_patterns` | list | `[]` | 허용할 패턴 (화이트리스트). |

### 7-4. 보안 (security) 상세

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `security.enabled` | bool | `false` | 보안 스캔 단계 활성화. |
| `security.fail_severity` | string | `"high"` | 이 심각도 이상이면 파이프라인 실패 처리. |
| `security.rules_path` | string | `""` | 보안 규칙 파일 경로. |

---

## 8. 빌드/테스트 게이트

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `no_build` | bool | `false` | `true`면 빌드 게이트 비활성화. |
| `require_build` | bool | `false` | `true`면 `no_build`가 true여도 강제로 빌드 실행. |
| `run_tests` | bool | `false` | `true`면 테스트 게이트 활성화. |
| `dotnet_build_target` | string | `""` | dotnet 빌드 대상 (예: `.sln` 경로). 레거시 호환. |
| `dotnet_test_target` | string | `""` | dotnet 테스트 대상. 레거시 호환. |
| `dotnet_test_filter` | string | `""` | dotnet 테스트 필터 (`--filter`에 전달). |
| `build_cmd` | list | `[]` | 범용 빌드 명령어. 예: `["dotnet", "build", "My.sln"]`. 설정 시 dotnet_* 대체. |
| `test_cmd` | list | `[]` | 범용 테스트 명령어. 예: `["npm", "test"]`. 설정 시 dotnet_* 대체. |
| `build_timeout_seconds` | int | `1800` | 빌드 명령 타임아웃 (30분). |

---

## 9. 모델 설정 (Codex 백엔드용)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `pm_model` | string | `"gpt-5.5"` | PM 단계 GPT 모델. |
| `dev_model` | string | `"gpt-5.4-mini"` | Dev 단계 GPT 모델 (기본 티어). |
| `qa_model` | string | `"gpt-5.5"` | QA 단계 GPT 모델. |
| `codex_reasoning_effort` | string | `""` | `codex exec`에 전달할 `model_reasoning_effort` 오버라이드. 비어있으면 Codex CLI/글로벌 설정 기본값 사용. |
| `qa_always` | bool | `true` | `true`면 코드 변경 없어도 항상 QA 실행. enterprise 프로필에서는 항상 `true` 강제. |
| `qa_to_backlog` | bool | `false` | `true`면 QA 결과를 백로그에 후속 태스크로 추가. |
| `max_qa_followups` | int | `5` | QA에서 백로그에 추가할 최대 후속 태스크 수. |
| `reporter_model` | string | `"gpt-5.4-mini"` | Reporter(종료 보고서) GPT 모델. |
| `report_max_turns` | int | `8` | Reporter 최대 턴 수. |

---

## 10. Dev 에스컬레이션 (모델 티어링)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `dev_auto_escalate` | bool | `true` | `true`면 빌드/테스트 실패 시 상위 모델로 자동 에스컬레이션. |
| `dev_max_escalations` | int | `2` | 태스크당 최대 에스컬레이션 횟수. |
| `dev_model_tier1` | string | `"gpt-5.4"` | 1차 에스컬레이션 GPT 모델. |
| `dev_model_tier2` | string | `"gpt-5.5"` | 2차 에스컬레이션 GPT 모델. |
| `dev_escalate_on` | list | `["no_diff", "build_failed", "test_failed", "fast_regression_failed", "no_commits"]` | 에스컬레이션 트리거 조건. |

**에스컬레이션 흐름:**
```
dev_model → dev_model_tier1 → dev_model_tier2
(실패 시)      (재실패 시)
```

---

## 11. 타임아웃

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `pm_timeout_seconds` | int | `900` | PM 단계 타임아웃 (15분). |
| `dev_timeout_seconds` | int | `900` | Dev 단계 타임아웃 (15분). |
| `mcp_timeout_seconds` | int | `120` | MCP 서버 통신 타임아웃 (2분). |
| `test_timeout_seconds` | int | `3600` | 테스트 실행 타임아웃 (1시간). |
| `stop_wait_timeout_seconds` | int | `180` | STOP 파일 감지 후 graceful 종료 대기 시간 (3분). 초과 시 강제 종료. |

---

## 12. PM 튜닝

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `pm_structured_retries` | int | `2` | PM 구조화 출력(JSON) 파싱 실패 시 재시도 횟수. |
| `pm_max_turns_continuations` | int | `1` | PM이 max_turns 초과 시 이어서 실행할 횟수. |
| `pm_bootstrap_max_turns` | int | `28` | 첫 번째 사이클(부트스트랩) PM 최대 턴 수. |
| `pm_incremental_max_turns` | int | `18` | 이후 사이클(증분) PM 최대 턴 수. |
| `pm_refresh_backlog` | bool | `false` | `true`면 매 사이클마다 백로그를 갱신(재분석). |
| `pm_refresh_every_cycles` | int | `0` | N 사이클마다 백로그 갱신. `0`이면 비활성화. `1`이면 매 사이클. |
| `pm_include_working_tree` | bool | `false` | `true`면 PM 프롬프트에 현재 워킹 트리 변경사항 포함. |

---

## 13. Dev 튜닝

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `dev_max_turns_continuations` | int | `2` | Dev가 max_turns 초과 시 이어서 실행할 횟수. |

---

## 14. 예산 가드레일 (budgets)

실행 비용을 제어하는 상한선. 초과 시 해당 동작을 중단.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `budgets.max_pm_structured_retries` | int | `2` | PM 구조화 출력 재시도 최대 횟수 상한. |
| `budgets.max_dev_escalations_per_task` | int | `2` | 태스크당 Dev 에스컬레이션 최대 횟수 상한. |
| `budgets.max_dev_continuations_per_task` | int | `2` | 태스크당 Dev continuation 최대 횟수 상한. |
| `budgets.max_total_escalations_per_run` | int | `10` | 전체 실행(run)에서 에스컬레이션 총 횟수 상한. |
| `budgets.max_total_continuations_per_run` | int | `10` | 전체 실행에서 continuation 총 횟수 상한. |
| `budgets.max_total_repair_attempts_per_run` | int | `5` | 전체 실행에서 복구 시도 총 횟수 상한. |

---

## 15. MCP (Model Context Protocol)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `mcp_mode` | string | `"npx"` | MCP 실행 모드. `"npx"`, `"codex"`, `"disabled"` 중 선택. |
| `codex_package` | string | `"@openai/codex@latest"` | Codex NPM 패키지 지정. |

---

## 16. 문서 (Docs)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `docs_read_mode` | string | `"digest"` | 문서 읽기 모드. `"digest"` (요약), `"full"` (전체), `"none"` (비활성). |
| `docs_dir` | string | `".doc/Docs"` | 프로젝트 문서 디렉토리 경로. |
| `docs_digest_file` | string | `".doc/DOCS_DIGEST.md"` | 문서 다이제스트 출력 파일 경로. |
| `generate_digest` | bool | `false` | `true`면 문서 다이제스트를 강제 재생성. |
| `prompts_dir` | string | `""` | 프롬프트 템플릿 디렉토리. 비어있으면 `prompts/<repo-slug>/` 자동 사용. |

---

## 17. 스킬 시스템 (skills)

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `skills.enabled` | bool | `false` | 스킬 시스템 활성화. |
| `skills.roots` | list | `["~/.codex/skills", ...]` | 스킬 파일 검색 루트 디렉토리 목록. |
| `skills.snapshot_dir` | string | `""` | 스킬 스냅샷 저장 디렉토리. |
| `skills.inline_mode` | string | `"qa"` | 스킬 인라인 모드. `"qa"`, `"pm"`, `"both"`, `"none"` 중 선택. |
| `skills.max_excerpt_lines` | int | `12` | 스킬 발췌문 최대 줄 수. |
| `skills.pm_summary_max_items` | int | `120` | PM 요약에 포함할 최대 스킬 항목 수. |
| `skills.pm_summary_max_chars` | int | `8000` | PM 요약 최대 문자 수. |
| `skills.qa_max_total_chars` | int | `8000` | QA 스킬 컨텍스트 최대 문자 수. |
| `skills.skill_match_autofix` | bool | `false` | 스킬 매칭 자동 수정 활성화. |
| `skills.skill_match_autofix_threshold` | float | `0.9` | 자동 수정 임계값 (0.0~1.0). |

---

## 18. 기타 / 디버그

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `debug` | bool | `false` | `true`면 상세 디버그 로그 출력. |
| `stop_file` | string | `"STOP"` | 이 이름의 파일이 repo에 생성되면 실행 즉시 중단. |
| `allow_no_diff` | bool | `false` | `true`면 Dev가 코드 변경 없이 완료해도 성공으로 처리. |
| `stop_if_no_diff` | bool | `false` | `true`면 코드 변경 없을 때 실행 중단. (`allow_no_diff`의 반대 개념.) |
| `dangerous_git_rollback` | bool | `false` | `true`면 파괴적인 git 롤백(hard reset 등) 허용. 주의 필요. |

---

## 19. 페일오버 (Failover)

백엔드 장애 시 다른 백엔드로 자동 전환.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `failover_enabled` | bool | `false` | 페일오버 활성화. |
| `failover_backends` | list | `["codex", "claudecode"]` | 페일오버 체인. codex 실패 시 claudecode로 전환. |
| `failover_on` | list | `["quota_exhausted", "quota_utilization"]` | 페일오버 트리거 조건. `"quota_exhausted"` = API 할당량 소진, `"quota_utilization"` = 사용률 한도(`quota_*_max_utilization`) 초과 시. |
| `failover_max_switches` | int | `0` | 한 실행에서 최대 백엔드 전환 횟수. `0`이면 무제한. |

---

## 20. 플러그인

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `plugins_enabled` | bool | `false` | 플러그인 스테이지 로딩 활성화. |
| `plugins_allowlist` | list | `[]` | 허용할 플러그인 패턴 목록. 비어있으면 모두 차단. |
| `plugins_strict` | bool | `true` | `true`면 플러그인 로드 실패/차단 시 파이프라인 중단. |

---

## 21. 할당량 관리 (Quota)

API 사용량 제한으로 인한 장애를 선제적으로 방지합니다.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `quota_check_enabled` | bool | `true` | API 할당량 사전 체크 활성화. 태스크 실행 전 남은 할당량을 확인. |
| `quota_five_hour_max_utilization` | int | `95` | 5시간 롤링 윈도우 최대 사용률 (%). 초과 시 실행 보류 또는 페일오버. |
| `quota_seven_day_max_utilization` | int | `95` | 7일 롤링 윈도우 최대 사용률 (%). |
| `quota_wait_for_reset` | bool | `true` | `true`면 할당량 초과 시 리셋까지 대기. `false`면 즉시 실패 처리. |

---

## 22. 태스크 이력 (Task History)

SQLite 기반 크로스-런 태스크 이력 추적. PM이 이전 실행에서의 완료/실패 이력을 참조하여 중복 작업을 방지합니다.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `task_history_enabled` | bool | `true` | 태스크 이력 DB 활성화. |
| `task_history_max_items` | int | `15` | PM 프롬프트에 주입할 최대 이력 항목 수. |
| `max_consecutive_task_failures` | int | `3` | 연속 태스크 실패 허용 횟수. 초과 시 사이클 중단. |

**DB 위치**: `{AGENTCLI_HOME}/databases/{repo-slug}.db`

**스키마 주요 컬럼**: `task_id`, `title`, `status`, `reason`, `detail`, `files`, `cycle_idx`, `attempt`, `max_attempts`, `run_id`, `backend`, `recorded_at`

---

## 23. 프로젝트 목표 (Goals)

GOALS.md 기반 프로젝트 완료 추적 시스템. P0(필수)와 P1(선택) 체크박스로 완료 여부를 판단합니다.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `goals_enabled` | bool | `true` | Goals 시스템 활성화. |
| `goals_auto_generate` | bool | `true` | `true`면 GOALS.md가 없을 때 PM이 첫 사이클에서 자동 생성. |
| `goals_auto_check` | bool | `true` | `true`면 매 사이클마다 목표 달성률을 자동 검사하여 프로젝트 완료 여부 판단. |
| `goals_completion_level` | string | `"all"` | 프로젝트 완료 판정 기준. `"p0"` (P0만), `"p1"` (P0+P1), `"all"` (전체 체크박스). |
| `goals_auto_refresh` | bool | `false` | `true`면 GOALS 전체 완료/빈 백로그 시 LLM이 차세대 목표를 GOALS.md에 자동 추가. |
| `goals_refresh_max_per_run` | int | `3` | 런 당 GOALS auto-refresh 최대 횟수. 무한 루프 방지. |

**파일 위치**: `{repo}/.doc/GOALS.md`

**완료 판정 로직** (`goals_completion_level` 기준):
- `"p0"`: P0 항목이 모두 체크(`[x]`)되면 `project_complete = true`
- `"p1"`: P0 + P1 항목 모두 체크 시 `project_complete = true`
- `"all"` (기본): 파일 내 모든 체크박스 완료 시 `project_complete = true`

**자동 갱신 (auto-refresh)**:
- `goals_auto_refresh=true` 시, 다음 상황에서 LLM이 새 P0/P1 항목을 GOALS.md에 자동 추가:
  - `project_complete` — GOALS 전체 달성 후
  - `no_tasks` — PM이 태스크 0개 생성 (빈 백로그)
  - `pm_refresh_no_backlog` — PM refresh 후에도 백로그 없음
- 안전장치: `goals_refresh_max_per_run` (기본 3) 초과 시 강제 중단, GOALS 미완료 시 시도 안 함

---

## 24. 원격 제어 평면 (Telegram)

Telegram 봇을 이용한 원격 모니터링/제어 평면. 로컬 셸과 병행하여 작동하는 하이브리드 모드를 지원합니다.
자세한 운영 가이드는 `docs/TELEGRAM.md` 참조.

| 변수 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `telegram.enabled` | bool | `false` | Telegram 통합 활성화. `true`면 봇 폴링/푸시 시작. |
| `telegram.bot_token` | string | `""` | Telegram 봇 토큰. 환경변수 `AGENTCLI_TELEGRAM_BOT_TOKEN`이 있으면 우선 사용. |
| `telegram.allowed_chat_ids` | list | `[]` | 허용된 chat_id 목록 (정수). 비어있으면 페어링 코드만으로 등록. |
| `telegram.pairing_code` | string | `""` | `/pair` 명령으로 chat_id를 등록할 때 쓰는 일회성 페어링 코드. |
| `telegram.instance_name` | string | `""` | 알림 메시지에 표시되는 인스턴스 라벨. 다중 호스트 운영 시 식별용. |
| `telegram.notify_events` | list | `["run_start", "run_stop", "task_done", "task_failed", "quota", "error", "stalled", "project_complete", "backend_failover"]` | 푸시할 이벤트 목록. |
| `telegram.send_cycle_summary` | bool | `true` | `cycle_summary.log`의 신규 라인을 푸시할지 여부. |
| `telegram.notify_poll_interval_seconds` | int | `8` | 푸시 폴링 주기 (초). |
| `telegram.stalled_seconds` | int | `600` | stall 감지 임계 시간 (초). HEARTBEAT 갱신이 이 시간 동안 없으면 `stalled` 이벤트 발송. |
| `telegram.tail_lines_default` | int | `50` | `/tail` 명령 기본 출력 라인 수. |
| `telegram.runner_mode` | string | `"thread"` | 러너 실행 모드. `"thread"` (같은 프로세스) 또는 `"subprocess"` (별도 프로세스). |
| `telegram.poll_timeout_seconds` | int | `30` | Telegram long-poll 타임아웃 (초). |

### 24-1. CLI 플래그

설정 파일 값을 CLI에서 일시 오버라이드할 수 있습니다.

| 플래그 | 대응 키 / 동작 |
|--------|---------------|
| `--telegram` | 하이브리드 모드 활성화 (로컬 셸 + Telegram 제어 평면). |
| `--telegram-bot-token <TOKEN>` | `telegram.bot_token` 오버라이드. |
| `--telegram-pairing-code <CODE>` | `telegram.pairing_code` 오버라이드. |
| `--telegram-runner-mode {thread,subprocess}` | `telegram.runner_mode` 오버라이드. |
| `--telegram-allowed-chat-id <ID>` | `telegram.allowed_chat_ids`에 추가 (반복 가능). |
| `--telegram-instance-name <NAME>` | `telegram.instance_name` 오버라이드. |
| `--telegram-notify-events <CSV>` | `telegram.notify_events` 오버라이드. 쉼표 구분 (예: `run_start,task_done,quota`). |
| `--telegram-send-cycle-summary / --no-telegram-send-cycle-summary` | `telegram.send_cycle_summary` 토글. |
| `--telegram-notify-interval <SEC>` | `telegram.notify_poll_interval_seconds` 오버라이드. |
| `--telegram-stalled-seconds <SEC>` | `telegram.stalled_seconds` 오버라이드. |
| `--telegram-tail-lines <N>` | `telegram.tail_lines_default` 오버라이드. |
| `--telegram-poll-timeout <SEC>` | `telegram.poll_timeout_seconds` 오버라이드. |

### 24-2. 환경변수

| 변수 | 우선순위 / 용도 |
|------|---------------|
| `AGENTCLI_TELEGRAM_BOT_TOKEN` | `telegram.bot_token`보다 우선 적용. 토큰을 설정 파일에 기록하지 않고 안전하게 주입할 때 사용. |

---

## 설정 우선순위

```
CLI 인자 (--flag)  >  설정 파일 (JSON)  >  DEFAULTS (코드 내 기본값)
```

CLI에서 `--flag`를 명시하면 설정 파일보다 우선. 설정 파일에 없는 항목은 DEFAULTS 폴백.
