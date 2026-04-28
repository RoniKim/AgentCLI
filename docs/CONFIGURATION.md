← [README로 돌아가기](../README.md)

# 설정(Config) 관리

> 최종 검증: 2026-04-28 (코드 기준)

## config 저장 위치(기본)

기본적으로 config는 **레포 내부가 아니라 AgentCLI 쪽**에 저장됩니다:

- `{AgentCLI_HOME}/configs/<repo-slug>-<hash>.json`

환경변수로 홈 변경 가능:

- `AGENTCLI_HOME=<path>` 를 설정하면 `configs/`, `prompts/`의 기준 디렉토리가 바뀝니다.

> 레거시 호환: 레포에 `REPO/.AgentCLI/agent_config.json` 또는 `REPO/.doc/agent_config.json`이 있으면 **읽기용으로 폴백 로드**할 수 있으며, 이후 `/save`하면 새 경로로 마이그레이션됩니다.

## Shell에서 자주 쓰는 명령

| 명령 | 설명 |
|------|------|
| `/config` | 현재 적용 설정(기본값+config+오버라이드) 출력 |
| `/config --all` | 원본 JSON까지 포함한 전체 출력 |
| `/set <key> <value>` | 설정 오버라이드 (세션 한정, `/save`해야 영구) |
| `/add <key> <value>` | 리스트 설정에 값 추가 |
| `/load [path]` | config JSON 로드 |
| `/save [path]` | 현재 설정을 JSON으로 저장 |
| `/repo <path>` | repo 경로 변경 |
| `/todo` | 현재 TODO 확인 |
| `/todo --save` | 오늘 할 일 TODO 생성 후 에디터 열기 |
| `/start [--flags]` | 백그라운드로 러너 시작 |
| `/stop [--wait]` | STOP 파일 생성, 선택적 대기 |
| `/status` | 러너 상태/실행시간/종료코드 확인 |
| `/doctor` | 환경 진단 (Git, API 키, Backend, Skills, DB, Goals, Docs 등 15개 항목) |
| `/worktree` (alias: `/worktree-list`, `/worktree-doctor`) | 읽기 전용 워크트리 진단 — `.AgentCLI/agent_runs`, pending marker, patch path, cleanup-failed artifact, generated worktree 디렉토리 점검 |
| `/merge-worktree` | pending worktree 결과를 Y/N 확인 후 원본 repo에 적용 |
| `/discard-worktree` | pending worktree 결과를 Y/N 확인 후 폐기 |
| `/help` | 명령어 도움말 |
| `/exit` | Shell 종료 |

> 전체 설정 변수 레퍼런스는 [`CONFIG_REFERENCE_KO.md`](CONFIG_REFERENCE_KO.md) 참고

---

# 실행 엔진(Backend) 선택

## Codex backend (기본)

필수:
- **Codex CLI** 설치 및 로그인: `npm install -g @openai/codex && codex login`
- 과금: **Codex 크레딧** (ChatGPT 구독) — API per-token 과금 없음

동작 방식:
- `codex exec` 서브프로세스를 통해 선택된 역할을 실행
- JSONL 이벤트 스트리밍으로 결과 파싱

## Claude Code backend

필수:
- `pip install -U claude-agent-sdk`
- **Claude Code 로그인**: `claude auth login`

Shell에서 전환:
```text
> /set execution_backend claudecode
> /save
```

스모크 테스트(선택):
```bash
python -m agent_runner.backends.claude_smoke_test --prompt "hi"
```

---

# 역할별 모델 설정

## Codex 백엔드 (GPT 모델 — Codex 크레딧으로 실행)

| 설정 | 기본값 | 용도 |
|------|--------|------|
| `pm_model` | `gpt-5.5` | PM model |
| `dev_model` | `gpt-5.4-mini` | Dev fallback model |
| `dev_model_tier1` | `gpt-5.4` | Dev fallback tier 1 |
| `dev_model_tier2` | `gpt-5.5` | Dev fallback tier 2 |
| `qa_model` | `gpt-5.5` | QA model |
| `reporter_model` | `gpt-5.4-mini` | Reporter model |

Dev fallback ladder: `gpt-5.4-mini -> gpt-5.4 -> gpt-5.5`.

## Claude 백엔드 (Claude 모델)

| 설정 | 기본값 | 권장값 | 용도 |
|------|--------|--------|------|
| `claudecode_model` | `sonnet` | — | 전체 폴백 모델 |
| `claudecode_pm_model` | `""` | `sonnet` | PM 백로그 생성 |
| `claudecode_dev_model` | `""` | `sonnet` | Dev 코딩 (기본 티어) |
| `claudecode_dev_model_tier1` | `""` | `opus` | Dev 에스컬레이션 1단계 |
| `claudecode_dev_model_tier2` | `""` | — | Dev 에스컬레이션 2단계 |
| `claudecode_qa_model` | `""` | `haiku` | QA 리뷰 (비용 절감) |
| `claudecode_reporter_model` | `""` | `haiku` | 종료 보고서 (비용 절감) |

**폴백 체인:**
```
역할별 모델 (비어있으면) → claudecode_model (비어있으면) → "sonnet"
```

**비용 최적화 예시:**
```json
{
  "claudecode_pm_model": "sonnet",
  "claudecode_dev_model": "sonnet",
  "claudecode_dev_model_tier1": "opus",
  "claudecode_qa_model": "haiku",
  "claudecode_reporter_model": "haiku"
}
```
- PM/Dev: sonnet으로 균형 잡힌 품질
- Dev 에스컬레이션: opus로 어려운 작업 처리
- QA/Reporter: haiku로 비용 절감 (읽기/요약 위주)

---

# Claude 백엔드 고급 설정

Claude Code 백엔드를 사용할 때 추가로 설정할 수 있는 고급 옵션들입니다.

## 역할별 도구(Tool) 제한

각 Stage에서 Claude가 사용할 수 있는 도구를 제한하여 안전성을 높일 수 있습니다:

```json
{
  "claudecode_pm_allowed_tools": "Read,Grep,Glob,Write,Edit",
  "claudecode_pm_disallowed_tools": "",
  "claudecode_dev_allowed_tools": "Read,Write,Edit,Grep,Glob,Bash",
  "claudecode_dev_disallowed_tools": "",
  "claudecode_qa_allowed_tools": "Read,Grep,Glob,Bash",
  "claudecode_qa_disallowed_tools": ""
}
```

| 역할 | 기본 허용 도구 | 설명 |
|------|----------------|------|
| **PM** | Read, Grep, Glob, Write, Edit | 분석 + 백로그 작성 |
| **Dev** | Read, Write, Edit, Grep, Glob, Bash | 코딩 + 쉘 실행 |
| **QA** | Read, Grep, Glob, Bash | 읽기 전용 리뷰 |

> `disallowed_tools`를 설정하면 `allowed_tools`에 있더라도 해당 도구가 차단됩니다.

## Extended Thinking (확장 사고)

Claude 모델의 내부 추론(thinking) 토큰 예산을 설정합니다:

```json
{
  "claudecode_max_thinking_tokens": 0
}
```

- `0` (기본): SDK 기본값 사용
- 양수 값: 지정된 토큰 수까지 thinking 허용
- thinking 지원 모델(opus 등)에서만 효과 있음

## 세션 관리

```json
{
  "claudecode_user": "",
  "claudecode_fork_session": false,
  "claudecode_include_partial_messages": false,
  "claudecode_setting_sources": "project"
}
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `claudecode_user` | `""` | Claude Code 사용자 식별자 |
| `claudecode_fork_session` | `false` | resume 시 새 세션 ID로 포크 (best-effort) |
| `claudecode_include_partial_messages` | `false` | 스트리밍 중간 메시지 이벤트 활성화 |
| `claudecode_setting_sources` | `"project"` | Claude Code 설정 읽기 소스 (예: `"project,user,local"`로 확장 가능) |

## 시스템 프롬프트 확장

Claude의 기본 시스템 프롬프트에 커스텀 지침을 추가합니다:

```json
{
  "claudecode_system_prompt_append": "항상 한국어로 커밋 메시지를 작성하세요."
}
```

## 파일 체크포인팅 (Beta)

```json
{
  "claudecode_enable_file_checkpointing": false
}
```

> 실험적 기능. Claude Code SDK의 파일 체크포인팅을 활성화합니다.
