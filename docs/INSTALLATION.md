← [README로 돌아가기](../README.md)

# 요구사항 및 설치

## 공통
- **Python 3.10+**
- **Git**

## Codex backend 사용 시(기본)
- **Codex CLI** 설치: `npm install -g @openai/codex`
- **Codex 로그인**: `codex login` (ChatGPT 구독 기반 — API Key 불필요)

## Claude Code backend 사용 시
- `pip install -U claude-agent-sdk`
- **Claude Code 로그인**: `claude auth login` (API Key 불필요)

## (선택) 빌드/테스트 게이트
- .NET 프로젝트면 **.NET SDK**
- 비-.NET 프로젝트면 `--no-build` 권장 또는 `build_cmd/test_cmd` 설정

## 설치

```bash
pip install -U -r requirements.txt
```

> 양쪽 백엔드 모두 **CLI 로그인 기반** 인증을 사용합니다. `.env` 파일이나 API Key 설정은 필요하지 않습니다.

---

# 처음부터 실행까지 (Step-by-Step 세팅 가이드)

처음 사용하는 분을 위한 **전체 세팅 → 첫 실행 → 결과 확인** 가이드입니다.

## Step 1: 사전 준비

```bash
# 1-1. Python 3.10+ 확인
python --version   # Python 3.10 이상이어야 합니다

# 1-2. Git 확인
git --version

# 1-3. AgentCLI 의존성 설치
cd <AgentCLI 디렉토리>
pip install -U -r requirements.txt
```

## Step 2: 백엔드 설치 및 로그인

API Key는 불필요합니다. 각 백엔드의 CLI 로그인만 필요합니다.

**Codex 백엔드 (기본값):**
```bash
# Codex CLI 설치
npm install -g @openai/codex

# Codex 로그인 (ChatGPT 구독 계정)
codex login
```

**Claude 백엔드:**
```bash
# Claude Agent SDK 설치
pip install -U claude-agent-sdk

# Claude Code 로그인
claude auth login
```

> 양쪽 모두 **CLI 로그인 기반** 인증입니다. `.env` 파일이나 API Key 환경변수 설정은 필요하지 않습니다.

## Step 3: 환경 검증

```bash
python agent_cli.py --repo "<대상 프로젝트 경로>"
```

Shell이 열리면:

```text
> /doctor
```

`/doctor`가 모든 항목을 통과하면 준비 완료입니다. 실패 항목이 있으면 안내에 따라 해결하세요.

## Step 4: 첫 실행

**방법 1: Interactive Shell (권장 — 처음에는 이 방법을 추천)**

```bash
python agent_cli.py --repo "<대상 프로젝트 경로>"
```

```text
# 현재 설정 확인
> /config

# .NET이 아닌 프로젝트면 빌드 게이트 끄기
> /set no_build true

# 실행 시작 (PM이 백로그 생성 → Dev가 태스크 실행 → QA가 리뷰)
> /start --autopilot --continuous

# 실행 상태 확인
> /status

# 안전하게 중지 (현재 태스크 완료 후 종료)
> /stop --wait

# Shell 종료
> /exit
```

**방법 2: 무인 실행 (CI/CD, 밤새 운용)**

```bash
python agent_cli.py --run-now --repo "<대상 프로젝트 경로>" \
  --non-interactive --autopilot --continuous
```

## Step 5: 결과 확인

실행 후 대상 레포의 `.AgentCLI/agent_runs/<timestamp>/` 디렉토리에 산출물이 생성됩니다:

```
.AgentCLI/agent_runs/20260210-143000/
  ├─ BACKLOG.json          ← PM이 생성한 태스크 목록
  ├─ STATE.json            ← 완료/실패 태스크 기록
  ├─ SHUTDOWN_REPORT.md    ← 실행 종료 요약
  └─ dev_logs/             ← 태스크별 Dev 실행 로그
```

실제 코드 변경은 `git log`로 확인:

```bash
cd <대상 프로젝트 경로>
git log --oneline -10
```

## Step 6: 프롬프트 커스터마이징 (선택)

프로젝트에 맞게 PM/Dev/QA 프롬프트를 튜닝하려면:

```bash
# 기본 프롬프트 템플릿 생성
python agent_cli.py --run-now --repo "<경로>" --init-prompts
```

생성된 파일 위치: `<AgentCLI>/prompts/<repo-slug>-<hash>/`

| 파일 | 역할 |
|------|------|
| `pm_instructions.md` | PM 에이전트 지시문 (어떤 태스크를 만들지) |
| `dev_instructions.md` | Dev 에이전트 지시문 (어떻게 코딩할지) |
| `qa_instructions.md` | QA 에이전트 지시문 (어떻게 리뷰할지) |
| `pm_bootstrap_prompt.md` | PM 첫 실행 프롬프트 템플릿 |
| `pm_incremental_prompt.md` | PM 반복 실행 프롬프트 템플릿 |
| `dev_task_prompt.md` | Dev 태스크 프롬프트 템플릿 |
| `qa_prompt.md` | QA 프롬프트 템플릿 |

## 자주 쓰는 실행 시나리오

| 시나리오 | 명령 |
|----------|------|
| 백로그만 미리보기 (코드 변경 없음) | `python agent_cli.py --run-now --repo "<경로>" --non-interactive --autopilot` |
| 태스크 5개만 실행 | `--continuous --iterations 5` 추가 |
| Claude로 실행 | `--execution-backend claudecode` 추가 |
| 밤새 루프 | `--loop --loop-max-cycles 20 --loop-sleep-seconds 60` 추가 |
| Worktree 격리 (안전) | `--worktree-isolation` 추가 |
| 빌드 게이트 끄기 | `--no-build` 추가 |

## 최소 config JSON 예시

설정을 파일로 관리하려면 `<AgentCLI>/configs/<repo-slug>-<hash>.json`을 직접 만들거나, Shell에서 `/set` + `/save`를 사용합니다:

```json
{
  "config_version": 2,
  "repo": "C:/Dev/MyProject",
  "execution_backend": "claudecode",
  "no_build": true,
  "continuous": true,
  "autopilot": true,
  "claudecode_dev_model": "sonnet",
  "claudecode_dev_model_tier1": "opus",
  "claudecode_qa_model": "haiku"
}
```

> 전체 설정 변수 레퍼런스는 [`CONFIG_REFERENCE_KO.md`](CONFIG_REFERENCE_KO.md) 참고
