# AgentCLI — CLI-first Multi-Agent Runner (PM → Dev → QA)

개인 개발자가 **켜두고**, 나중에 **PR 수준의 변경(코드/테스트/문서)** 을 받는 것을 목표로 만든 **CLI 기반 멀티 에이전트 러너**입니다.

- 기본 파이프라인: **PM(백로그 생성) → Dev(구현) → QA(점검/피드백)**
- 실행 엔진(backend): **Codex(OpenAI)** 또는 **Claude Code(Anthropic)** 로 전환 가능
- 기본 UX: **Interactive Shell** (`/start`, `/stop`, `/config` …)  
  + 무인 운용/스크립트용: `--run-now` (즉시 실행)

---

## 핵심 기능

- **Interactive Shell**: 실행 전 설정 확인/수정 후 `/start`로 러너 실행
- **Non-interactive 실행**: `--run-now --non-interactive`로 밤새 무인 운용
- **백엔드 전환**
  - `execution_backend=codex` (기본): OpenAI Agents + Codex MCP
  - `execution_backend=claudecode`: Claude Agent SDK + Claude Code CLI
- **PM 구조화 출력 강제**: PM 응답을 JSON 스키마로 검증 → 러너가 `BACKLOG.json|md`를 생성
- **안전한 Git 운용**
  - 기본은 **안전 모드** (파괴적 롤백 비활성)
  - 선택: `--worktree-isolation`로 격리 worktree에서 작업 후 패치로 반영
- **빌드/테스트 게이트(옵션)**: 기본은 .NET(dotnet) 중심, 커스텀 `build_cmd/test_cmd`도 지원
- **정책/시크릿 스캔(옵션)**: run_dir 산출물/코드에서 키/토큰 유출 방지 스캔
- **실행 아티팩트 관리**: `run_dir` 단위로 로그/상태/백로그/리포트 보존
- **파이프라인 커스터마이징**
  - `roles="PM,Dev,QA,Security"`처럼 역할 순서/구성 변경
  - 플러그인 Stage(외부 모듈) 로드(Allowlist 기반)

---

## 요구사항

### 공통
- **Python 3.10+**
- **Git**

### Codex backend 사용 시(기본)
- **Node.js + npx** (기본 MCP 모드가 `npx`)

### Claude Code backend 사용 시
- `pip install -U claude-agent-sdk`
- **Claude Code 인증**(로그인) 또는 `ANTHROPIC_API_KEY`

### (선택) 빌드/테스트 게이트
- .NET 프로젝트면 **.NET SDK**
- 비-.NET 프로젝트면 `--no-build` 권장 또는 `build_cmd/test_cmd` 설정

---

## 설치

```bash
pip install -U -r requirements.txt
```

> `claude-agent-sdk`는 기본 requirements에 포함되어 있지 않습니다(선택 의존성).  
> Claude backend를 쓸 때만 별도 설치하세요.

---

## 빠른 시작

### 1) Interactive Shell (권장: 설정 확인 후 시작)

```bash
python agent_cli.py --repo "C:/Dev/BudgetBook"
```

Shell에서:

```text
> /config
> /start --autopilot --continuous
> /status
> /stop --wait
> /exit
```

### 2) 무인 운용 / 스크립트 실행 (--run-now)

```bash
python agent_cli.py --run-now --repo "C:/Dev/BudgetBook" --non-interactive --autopilot --continuous
```

- `--non-interactive`: 중간 입력 요구를 최대한 방지(무인 운용 필수)
- `--continuous`: 백로그 생성 후 Dev 태스크 실행까지 자동 진행  
  (`--continuous`가 없으면 PM/백로그 준비만 하고 종료)

---

## 설정(Config) 관리

### config 저장 위치(기본)

기본적으로 config는 **레포 내부가 아니라 AgentCLI 쪽**에 저장됩니다:

- `{AgentCLI_HOME}/configs/<repo-slug>-<hash>.json`

환경변수로 홈 변경 가능:

- `AGENTCLI_HOME=<path>` 를 설정하면 `configs/`, `prompts/`의 기준 디렉토리가 바뀝니다.

> 레거시 호환: 레포에 `REPO/.doc/agent_config.json`이 있으면 **읽기용으로 폴백 로드**할 수 있으며, 이후 `/save`하면 새 경로로 마이그레이션됩니다.

### Shell에서 자주 쓰는 명령

- `/config` : 현재 적용 설정(기본값+config+오버라이드) 출력
- `/set <key> <value>` : 설정 오버라이드
- `/add <key> <value>` : 리스트 설정에 추가
- `/load [path]` / `/save [path]` : config 로드/저장
- `/repo <path>` : repo 지정

---

## 실행 엔진(backend) 선택

### Codex backend (기본)

필수:
- `OPENAI_API_KEY`

예시(.env):
```bash
OPENAI_API_KEY=xxxxx
```

Codex MCP 모드(기본값):
- `mcp_mode="npx"`
- `codex_package="@openai/codex@latest"`

### Claude Code backend

필수(둘 중 하나):
- Claude Code 로그인(예: `claude auth login`) **또는**
- `ANTHROPIC_API_KEY`

설치:
```bash
pip install -U claude-agent-sdk
```

Shell에서 전환:
```text
> /set execution_backend claudecode
> /save
```

스모크 테스트(선택):
```bash
python -m agent_runner.backends.claude_smoke_test --prompt "hi"
```

#### Claude backend에서 자주 막히는 포인트
- `ClaudeSDKClient does not provide a message stream` 같은 오류가 나면,
  보통 `claude-agent-sdk` 버전 불일치/구버전일 가능성이 큽니다.  
  우선 아래로 업그레이드 후 재시도하세요:
  ```bash
  pip install -U claude-agent-sdk
  ```

---

## 파이프라인(roles) 커스터마이징

기본:
- `roles="PM,Dev,QA"`

내장 Stage:
- `PM`, `Dev`, `QA`, `Security`

예시) QA를 끄고 PM→Dev만:
```text
> /set roles PM,Dev
> /save
```

예시) Security Stage까지 포함:
```text
> /set roles PM,Dev,QA,Security
> /save
```

### 플러그인 Stage 로드(고급)

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

## 안전/운영 옵션 (Git, Stop, No-diff)

### Stop file로 안전 종료

- 기본 stop 파일: `STOP`
- `run_dir/STOP` 파일이 생기면 graceful stop

Shell:
```text
> /stop
> /stop --wait
```

### “변경 없음(no diff)” 정책

기본값:
- 태스크 수행 후 `git diff`가 없으면 실패로 간주하고 중단(토큰 낭비 방지)

계속 진행하려면:
```bash
python agent_cli.py --run-now --repo <path> --non-interactive --autopilot --continuous --allow-no-diff
```

### Worktree 격리 모드 (권장: 안전하게 오래 돌릴 때)

```bash
python agent_cli.py --run-now --repo <path> --worktree-isolation --non-interactive --autopilot --continuous
```

- 성공 시: 패치가 원 repo에 적용
- 실패/중단 시: 원 repo는 보존되고, `run_dir/worktree.patch`로 변경사항 복구 가능

수동 복구:
```bash
git apply --binary --whitespace=nowarn <run_dir>/worktree.patch
# 충돌 시:
git apply --reject --whitespace=nowarn <run_dir>/worktree.patch
```

### 파괴적 롤백(비권장, 명시적으로만)

```bash
python agent_cli.py --run-now --repo <path> --dangerous-git-rollback
```

---

## 빌드/테스트 게이트

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

---

## 정책/시크릿 스캔(옵션)

- `scan_scope="quick"` (기본)
- 상한: `scan_max_files`, `scan_timeout_seconds`, `scan_max_total_bytes`
- 제외: `scan_ignore_globs`, `scan_ignore_paths`

> 프로젝트가 커질수록 “quick → staged/full”은 신중히 올리는 것을 권장합니다.

---

## 산출물(Artifacts) 구조

### run_dir (실행 단위)

기본:
- `REPO/.doc/agent_runs/<YYYYMMDD-HHMMSS>/`

대표 파일:
- `BACKLOG.json`, `BACKLOG.md` : 러너가 생성한 백로그(권위 소스)
- `STATE.json` : 완료/실패 태스크 기록
- `PM_OUTPUT_cycle_*.json` : 스키마 검증된 PM 출력
- `metrics.jsonl` : 이벤트 로그(JSONL)
- `tasks/` : 태스크별 로그/빌드/테스트 결과
- `dev_logs/` : Dev 로그 누적
- `SHUTDOWN_REPORT.md` : 종료 요약(조건에 따라)

### PM_CACHE (지속 분석 아티팩트)

기본:
- `REPO/.doc/PM_CACHE/`

대표 파일:
- `PROJECT_ANALYSIS.md`
- `REPO_INVENTORY.json|md`
- `REPO_SNAPSHOT.json`

---

## 트러블슈팅

### 1) `OPENAI_API_KEY is not set.`
- `.env` 위치가 애매하면 `--env-file`로 명시:
```bash
python agent_cli.py --run-now --repo <path> --env-file "<path>/.env" --non-interactive --autopilot
```

### 2) `npx` 를 찾을 수 없음
- Node.js 설치 후 `npx -v` 확인

### 3) Claude backend 오류(스트림 관련)
- `claude-agent-sdk` 업그레이드:
```bash
pip install -U claude-agent-sdk
```
- Claude Code 로그인 상태 확인(또는 `ANTHROPIC_API_KEY` 설정)

### 4) BACKLOG가 비어있어서 중단(`no_tasks`)
- PM이 빈 태스크 목록을 만든 경우 Dev 단계에서 `no_tasks`로 종료될 수 있습니다.
- 해결:
  - 레포 목표/할 일을 더 명확히 주거나
  - `/todo --save`로 오늘 할 일을 TODO로 만들고, PM이 이를 기준으로 백로그를 만들게 하세요.

### 5) Codex 사용량 제한(usage limit)으로 중단
- run_dir의 종료 리포트를 확인하고, 필요하면 `failover_enabled`와 `failover_backends`로 백엔드 체인을 구성하세요.

---

## 추천 운용 프리셋

### A) 백로그만 준비(최소 비용 스모크)
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot
```

### B) 태스크 실행(최대 5개)
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot --continuous --iterations 5
```

### C) 밤새 루프(아이들 타임아웃 포함)
```bash
python agent_cli.py --run-now --repo "<path>" --non-interactive --autopilot \
  --loop --loop-sleep-seconds 60 --loop-idle-exit-after 3600 --loop-max-cycles 20
```

---

## 보안 메모

- 시크릿/토큰은 절대 README/config/prompt에 하드코딩하지 말고 **환경변수 또는 .env**로만 주입하세요.
- worktree 패치(`worktree.patch`)는 변경 내용을 포함합니다. 외부 공유 전 민감정보 포함 여부를 점검하세요.


---

## 프롬프트 템플릿 커스터마이징

기본 프롬프트는 **Python-side prompts_dir**에 저장됩니다(레포 내부가 기본이 아님).

- 기본 prompts_dir: `AGENTCLI_HOME/prompts/<repo-slug>-<hash>/`
- 레거시(.doc/agent_prompts)는 읽기 폴백용으로만 취급될 수 있습니다.

템플릿 생성(1회):
```bash
python agent_cli.py --run-now --repo "<path>" --init-prompts
```

> 생성 후에는 prompts_dir의 `pm_instructions.md`, `dev_instructions.md` 등을 수정해 튜닝할 수 있습니다.

---

## Docs 읽기(Digest) — 토큰 절약

기본값:
- `docs_read_mode="digest"`
- `docs_dir=".doc/Docs"`
- `docs_digest_file=".doc/DOCS_DIGEST.md"`

Digest 생성/갱신(로컬 작업, 토큰 사용 없음):
```bash
python agent_cli.py --run-now --repo "<path>" --generate-digest
```

---

## Skills 시스템 (Codex/Claude 공통)

AgentCLI는 스킬 폴더를 스캔해 `SKILLS_INDEX` 요약을 만들어 **PM/QA에 인라인(발췌)** 할 수 있습니다.  
Dev에는 스킬 본문을 길게 인라인하지 않는 방향으로 설계되어 있습니다(토큰 방어).

config 예시(핵심):
```json
{
  "skills": {
    "enabled": true,
    "roots": [
      "~/.agents/skills",
      "~/.claude/skills",
      "{repo}/Skills"
    ],
    "snapshot_dir": ".doc/skills",
    "inline_mode": "qa",
    "max_excerpt_lines": 12
  }
}
```

- `roots`에서 `{repo}`는 현재 repo 경로로 치환됩니다.
- `snapshot_dir`는 run_dir 기준 상대경로(또는 절대경로)로 해석됩니다.

---

## Failover (backend 체인) — 고급

Codex 사용량 제한(quota/usage limit) 등 특정 사유로 중단될 때, 다른 backend로 자동 전환할 수 있습니다.

관련 옵션(요약):
- `failover_enabled=true`
- `failover_backends=["codex","claudecode"]`
- `failover_on=["quota_exhausted"]`
- `failover_max_switches=1`

> 실제로는 환경(키/로그인/설치)까지 만족해야 전환이 성공합니다. `/doctor`로 사전 점검을 권장합니다.

---

## /doctor (환경 진단)

Shell에서 `/doctor`를 실행하면 run_dir에 진단 보고서(`DOCTOR.md`)가 생성됩니다.

```text
> /doctor
```

진단 내용(요약):
- Python/Node/npx/.NET/환경변수 존재 여부
- repo/config/prompts_dir/run_dir 경로 정리
- backend별 필수 조건(OPENAI_API_KEY, ANTHROPIC_API_KEY, SDK 설치 등) 힌트

---

## 개발/테스트

단위 테스트(있는 경우):
```bash
python -m pytest -q
```
