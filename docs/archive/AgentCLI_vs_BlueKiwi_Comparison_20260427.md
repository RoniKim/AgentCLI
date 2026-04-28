# AgentCLI Web Console vs BlueKiwi 비교 보고서

> 📦 **ARCHIVED — 2026-04-27 시점 비교 자료.**
> 기준 커밋(`170083c`)은 이미 5+커밋 뒤로 밀려있고, 인용된 라우트 수(27→28)·테스트 수(77→104)도 stale.
> 시점 자료로 보존하되 재인용 시 시점 명시 필수.

작성일: 2026-04-27  
AgentCLI 기준 커밋: `170083c` (`Stop PM and Dev promptly on manual stop`)  
비교 기준: AgentCLI 현재 로컬 저장소, `.doc/GOALS.md`, `docs/WEB_CONSOLE.md`, BlueKiwi 공개 GitHub/README

## 1. 결론 요약

BlueKiwi는 "팀이 재사용 가능한 AI 워크플로우를 웹에서 만들고, 여러 AI 런타임이 MCP로 실행하며, 실행 과정을 브라우저에서 관찰/승인하는" 쪽으로 제품화가 더 진행된 플랫폼이다. Docker Compose, PostgreSQL, Redis, RBAC, 초대/API 키, 워크플로우 빌더, MCP 도구군, 런타임 설치 CLI, 릴리즈 관리까지 이미 공개 제품 형태를 갖추고 있다.

AgentCLI는 방향이 다르다. AgentCLI는 "특정 코드 저장소에서 PM -> Dev -> QA 자동 개발 루프를 안전하게 오래 돌리고, 그 실행 상태/GOALS/로그/프롬프트/설정/worktree를 웹 UI에서도 다룰 수 있게 하는 개발 자동화 조종석"에 가깝다. 범용 워크플로우 플랫폼이라기보다 로컬 코드 작업, Git/worktree 격리, STOP 처리, run_dir 산출물, GOALS 기반 진행률에 깊게 붙어 있다.

현재 상태를 수치로 보면, AgentCLI의 Web Console 목표는 `.doc/GOALS.md` 기준 80/99 항목 완료 상태다. 웹 기능 뼈대는 상당히 올라왔지만, 엔터프라이즈 사용 관점에서는 인증/RBAC, worktree 안정성, redaction, 실행 상태와 프로젝트 완료 상태 분리, 신뢰 가능한 stop/merge/cleanup 운영성이 아직 핵심 잔여 리스크다.

## 2. 한눈에 보는 포지션

| 항목 | AgentCLI | BlueKiwi |
| --- | --- | --- |
| 핵심 목적 | 코드 저장소를 대상으로 configurable PM/Dev/QA/Security 개발 루프 실행 | AI agent용 재사용 워크플로우 엔진 |
| 주 사용자 | 개인/소규모 개발자가 로컬 repo 자동화에 사용 | 팀이 워크플로우를 공유하고 승인/관찰하며 사용 |
| 실행 단위 | backlog task, run_dir, branch/worktree, GOALS | workflow, task, step, node(Action/Gate/Loop) |
| Web UI 성격 | AgentCLI 실행 조종석, 상태/로그/GOALS/config/prompt/worktree 관리 | 워크플로우 빌더, live timeline, 승인/댓글/공유 |
| AI 런타임 연결 | Codex/Claude 백엔드 중심, repo 내부 실행 루프 | MCP 기반 다중 런타임 연결. README 기준 17개 런타임 지원 |
| Git/code 작업 깊이 | 강점. branch, worktree isolation, patch merge, STOP/process guard 중심 | 상대적으로 일반 workflow 엔진. repo scan은 있으나 Git 작업 조종석은 핵심이 아님 |
| 팀/권한 모델 | 아직 인증 없음. trusted network 전제 | RBAC, API key, folder sharing, invite/setup 모델 존재 |
| 배포/설치 | Python venv + repo-local 실행. Web은 FastAPI alpha | Docker Compose, PostgreSQL, Redis, CLI, local beta runtime |
| 현재 성숙도 | 기능 뼈대는 높음. 운영 안정화가 남음 | 공개 제품/플랫폼 형태가 더 완성됨 |

## 3. AgentCLI 현재 상태

AgentCLI는 README 기준 CLI 기반 multi-agent runner이며 기본 파이프라인은 PM -> Dev -> QA다. 다만 이 순서는 완전 고정이 아니라 `roles` 설정으로 조정 가능하다. 내장 Stage는 PM, Dev, QA, Security이고, `roles="PM,Dev"`처럼 QA를 생략하거나 `roles="PM,Security,Dev,QA"`처럼 Security를 추가할 수 있다. `plugins_enabled`, `plugins_allowlist`, `plugins_strict`를 통해 `pkg.module:ClassName` 형태의 외부 Stage도 allowlist 기반으로 로딩할 수 있다. Interactive Shell에서 `/start`, `/stop`, `/status`, `/doctor`로 운용하고, 산출물은 run_dir 단위로 남긴다. Codex와 Claude Code 백엔드 전환, PM 구조화 출력, 안전한 Git 운용, worktree isolation, 모델 에스컬레이션, 빌드/테스트 게이트, 정책/시크릿 스캔, 예산/쿼타 가드레일, GOALS 자동 갱신 같은 기능이 이미 존재한다.

Web Console 쪽은 `agent_runner.web`이 `web_console/` 정적 자산과 API를 제공한다. 현재 API 라우트는 `agent_runner/web.py` 기준 27개이며, 상태/진행률/로그/config/prompts/goals/history/worktree/runner controls를 다룬다. checked-in 웹 콘솔 테스트는 `test_web_console*.py` 기준 77개이며 별도 Playwright smoke도 있다.

`.doc/GOALS.md` 기준 현재 완료율은 80/99다. P0는 대부분 완료됐지만 다음 리스크가 남아 있다.

- Korean locale 전체 번역.
- 기존 isolated worktree 재사용 계약 검증.
- merge preflight에서 dirty source, base_ref 불일치, patch hash, `git apply --check` 차단.
- Windows locked path cleanup 재시도와 cleanup-failed 상태 유지.
- worktree doctor/list/prune 명령 또는 API.
- shell/web/controller 시작 시 fresh run_dir 기본화.
- `STATE.json` 진행률이 이전 backlog task id에 오염되지 않도록 scope 분리.
- 실행 성공과 프로젝트 완료 상태 분리.
- GOALS 필수 섹션 누락/손상 처리.
- `goals_completion_level` 일관성.
- self-development run의 빠른 web/worktree regression gate 강제.
- LAN 노출 전 logs/GOALS/task excerpts/config/prompts/runner args redaction 일관화.

즉 AgentCLI는 "기능 수"만 보면 많이 올라왔지만, 사용자가 실제로 밤새 돌리고 worktree를 merge/discard하는 운영 흐름에서는 아직 남은 P0들이 모두 중요하다.

## 4. BlueKiwi 공개 상태

BlueKiwi README는 자신을 "AI Agent Workflow Engine"으로 설명한다. 웹 UI에서 multi-step workflow를 설계하고, Claude Code, Codex CLI, Gemini CLI 등 연결된 agent가 MCP를 통해 시작/실행하며, 각 step이 live timeline에 기록되는 구조다.

주요 구성은 다음과 같다.

- Docker Compose 기반 self-hosting: Next.js 앱, PostgreSQL 16, Redis 7.
- 첫 접속 `/setup`에서 superuser 생성.
- `bluekiwi accept`, `bluekiwi init` CLI로 팀 초대/API key 연결 및 agent runtime 설정.
- local quick start runtime은 SQLite 기반 beta로 제공.
- Slash command: `/bk-start`, `/bk-design`, `/bk-approve`, `/bk-improve`, `/bk-report`, `/bk-status`, `/bk-rewind`, `/bk-credential`, `/bk-scan`, `/bk-share` 등.
- Workflow Builder: Action, Gate, Loop node와 HITL approval.
- MCP tools: workflow execution, visual selection, approval, task data, workflow CRUD, attachments, instruction/credential store, folders/sharing, compliance scan.
- README 기준 17개 AI runtime 설치/연결 지원.
- Security/RBAC: superuser/admin/editor/viewer 4-tier role, hashed API key with expiry/revocation, personal/group/public folders, group sharing, REST API 경유 MCP.
- GitHub 공개 페이지 기준 386 commits, 29 releases, latest `v1.2.3` dated 2026-04-19.

라이선스는 주의가 필요하다. README 하단은 Sustainable Use License로 개인/내부 비즈니스 사용은 무료지만 상업적 재배포나 SaaS hosting은 별도 계약이 필요하다고 설명한다. GitHub 우측 메타데이터에는 MIT와 Unknown license가 함께 감지된다. 실제 사용 전 license 파일을 직접 확인해야 한다.

## 5. 기능별 상세 비교

### 5.1 Web UI와 UX

BlueKiwi의 UI는 workflow builder와 live timeline이 제품의 중심이다. 팀 사용자가 workflow를 만들고, 단계별 실행 상태를 보고, gate/HITL에서 승인하는 흐름에 맞춰져 있다.

AgentCLI Web Console은 현재 Dashboard, Pipeline, Logs, Backlog, Goals, Config, Prompts, Run History, Notifications, Worktree Review 같은 운영 화면을 갖고 있다. 이는 workflow builder라기보다 "이미 존재하는 AgentCLI runner를 웹에서 안전하게 관찰하고 조작하는 cockpit"이다.

판단:

- UI 제품성은 BlueKiwi가 앞선다.
- 코드 자동화 운영 화면의 깊이는 AgentCLI가 더 특화되어 있다.
- AgentCLI가 BlueKiwi를 그대로 따라갈 필요는 없다. 대신 Logs, Worktree Review, STOP progress, GOALS progress가 BlueKiwi보다 더 신뢰 가능해야 한다.

### 5.2 Workflow 모델

BlueKiwi는 Action/Gate/Loop로 workflow를 구성하고 version, folder, sharing, rewind, HITL, visual selection까지 제공한다. 이는 비개발 작업과 개발 작업 모두에 확장하기 좋다.

AgentCLI는 PM이 backlog를 만들고 Dev가 task branch에서 구현하며 QA/reporter가 검토하는 개발 pipeline에 가깝다. 하지만 stage ordering 자체는 `roles`로 구성할 수 있고, Security 같은 내장 Stage와 외부 plugin Stage를 끼워 넣을 수 있다. 즉 BlueKiwi처럼 웹에서 노드를 조립하는 범용 workflow builder는 아니지만, 코드 변경을 산출물로 만드는 데 필요한 model fallback, build/test gate, GOALS, task history, worktree isolation을 가진 configurable development pipeline이다.

판단:

- 범용성과 팀 재사용성은 BlueKiwi 우세.
- 코드 저장소 자동 개발 루프의 실행 깊이는 AgentCLI 우세.
- AgentCLI는 이미 `roles`/plugin Stage 확장 지점이 있으므로, 단기에는 웹 workflow builder보다 이 확장 지점을 안전하게 노출하고 검증하는 쪽이 맞다.

### 5.3 런타임/모델 통합

BlueKiwi는 MCP를 통해 다양한 AI runtime에 붙는다. README 기준 Codex CLI, Claude Code, Gemini CLI, Cursor, Windsurf, VS Code, JetBrains 등 17개 runtime을 다룬다.

AgentCLI는 Codex/Claude 백엔드가 핵심이며, 이 repo 설정에서는 Claude fallback을 막고 Codex 모델 tiering을 사용하는 방향으로 맞춰져 있다. PM은 `gpt-5.5`, Dev는 `gpt-5.4-mini -> gpt-5.4 -> gpt-5.5`, QA는 `gpt-5.5`, Reporter는 `gpt-5.4-mini`라는 운영 모델이 이 작업 맥락에 맞다.

판단:

- runtime ecosystem 확장성은 BlueKiwi 우세.
- 특정 Codex 개발 루프 최적화와 stop/worktree/process guard는 AgentCLI 우세.

### 5.4 Git/worktree/code execution 안전성

이 영역은 AgentCLI의 핵심 경쟁력이어야 한다. 이미 worktree isolation, pending merge/discard, branch/task commit, patch handling, process guard, stop progress가 존재한다. 다만 실제 사용자 흐름에서 merge patch 충돌, run_dir 재사용, stop timeout, stale marker, Windows locked path 문제가 확인됐고, GOALS에도 관련 P0가 남아 있다.

BlueKiwi는 workflow platform이므로 Git 작업의 세부 안전장치가 제품 핵심으로 보이지 않는다. 대신 team workflow, approval, logs, artifacts, MCP tool contracts가 중심이다.

판단:

- AgentCLI는 이 영역에서 이겨야 한다.
- 남은 P0를 끝내지 않으면 AgentCLI의 차별점이 오히려 리스크가 된다.

### 5.5 보안/권한/조직 사용

BlueKiwi는 공개 README 기준 RBAC, API key hashing/expiry/revocation, group sharing, no default credentials, REST API 경유 MCP를 갖고 있다. 엔터프라이즈 도입 검토에서 기본 질문에 답할 수 있는 구조다.

AgentCLI는 현재 인증 layer가 없고, docs에서도 LAN bind는 trusted-network-only로 다룬다. runner controls는 opt-in/confirmation phrase로 막지만, confirmation phrase는 인증이 아니다. LAN/팀 환경에서는 logs, GOALS raw text, task output, config, prompts, runner args redaction이 끝나기 전까지 제한적으로 써야 한다.

판단:

- 보안/권한/팀 사용은 BlueKiwi가 명확히 앞선다.
- AgentCLI는 개인 로컬 사용에서는 충분하지만, 엔터프라이즈 사용은 authentication/RBAC/audit/redaction이 필요하다.

### 5.6 배포와 운영

BlueKiwi는 Docker Compose로 앱/DB/cache를 올리는 self-hosting model을 제시하고, one-click deploy 템플릿과 migration 자동 실행을 문서화한다. 공개 제품으로 설치 경로가 명확하다.

AgentCLI는 Python venv와 repo-local 실행이 중심이다. 사용자가 직접 config/prompt/profile/run_dir를 잡아야 하고, Web은 FastAPI alpha이다. 다만 로컬 개발자가 본인 repo에서 바로 쓰기에는 가볍고, run_dir 산출물이 파일로 남는 점은 디버깅에 강하다.

판단:

- 팀 배포/설치/업그레이드 문서는 BlueKiwi 우세.
- 개인 로컬 코드 자동화와 산출물 추적은 AgentCLI가 더 직접적이다.

## 6. 엔터프라이즈 관점 성숙도 추정

아래 수치는 코드 품질의 절대 점수가 아니라 "엔터프라이즈에서 실제 운영하려고 할 때 준비된 정도"에 대한 현재 기준 추정이다.

| 평가 축 | AgentCLI | BlueKiwi |
| --- | ---: | ---: |
| 핵심 기능 구현 | 80-85% | 85-90% |
| Web 제품 완성도 | 70-75% | 85-90% |
| 장시간 실행/운영 안정성 | 65-75% | 75-85% |
| 코드 저장소 자동 개발 특화 | 85-90% | 55-65% |
| 팀/RBAC/권한/공유 | 25-35% | 80-90% |
| 설치/배포/업그레이드 | 55-65% | 80-90% |
| 보안/감사/노출 안전성 | 45-55% | 75-85% |
| 전체 엔터프라이즈 준비도 | 65-70% | 80-90% |

AgentCLI의 숫자가 낮게 잡히는 핵심 이유는 기능이 없어서가 아니라 "운영 중 실패했을 때 사용자가 안전하게 판단하고 복구할 수 있는가"와 "팀/네트워크 환경에서 노출해도 되는가"가 아직 완전히 닫히지 않았기 때문이다.

## 7. AgentCLI가 가져가야 할 방향

AgentCLI가 BlueKiwi를 그대로 복제하는 것은 비효율적이다. BlueKiwi의 강점은 범용 워크플로우 플랫폼이고, AgentCLI의 강점은 코드 저장소 자동 개발 작업을 안전하게 끝까지 굴리는 것이다.

AgentCLI는 다음 순서로 차별점을 완성하는 편이 좋다.

1. Worktree 안전성 P0 완료
   - reuse contract, merge preflight, patch hash, dirty source 차단, cleanup-failed 가시화, doctor/list/prune.

2. Runner lifecycle 신뢰성 완료
   - fresh run_dir 기본화, STOP snapshot, stop wait progress, PM/Dev 즉시 중단, process guard 고아 프로세스 방지.

3. Web 상태 모델 정리
   - execution status와 project completion status 분리.
   - GOALS/backlog/STATE progress 오염 방지.
   - malformed GOALS를 명확한 incomplete/error로 표시.

4. LAN/팀 사용 최소 보안선
   - authentication 전까지는 trusted-network gate를 유지.
   - logs/GOALS/task excerpts/config/prompts/args redaction 완성.
   - 이후 API key 또는 local auth, audit log, 역할 구분을 설계.

5. 제품화 문서
   - "AgentCLI는 BlueKiwi 같은 범용 workflow builder가 아니라 repo-local AI dev runner cockpit"이라는 포지션을 문서화.
   - 설치, doctor, start/stop, merge/discard, recovery guide를 단일 quickstart로 정리.

## 8. 최종 판단

BlueKiwi는 이미 "여러 AI agent가 같은 workflow engine을 공유하는 팀 플랫폼"으로 보기에 더 완성도가 높다. 특히 RBAC, workflow builder, MCP tool surface, Docker 배포, runtime installer는 AgentCLI보다 앞선다.

AgentCLI는 "코드 작업을 실제로 바꾸고, 테스트하고, worktree로 격리하고, stop/merge/recover하는 로컬 개발 자동화"에서 더 깊다. 이 차별점은 충분히 가치가 있지만, 남은 P0가 바로 그 차별점의 신뢰성을 좌우한다.

따라서 현재 전략은 BlueKiwi와 기능 수 경쟁을 하는 것이 아니라, AgentCLI의 Web Console을 "AI coding run cockpit"으로 완성하는 것이다. 특히 worktree/STOP/redaction/fresh run_dir/GOALS 상태 모델을 끝내면, 개인 및 소규모 팀의 코드 자동화 도구로는 BlueKiwi보다 더 직접적인 장점이 생긴다.

## 9. 참고 자료

### AgentCLI 로컬 자료

- `README.md`
- `docs/WEB_CONSOLE.md`
- `docs/WEB_CONSOLE_STATUS.md`
- `.doc/GOALS.md`
- `agent_runner/web.py`
- `web_console/app.js`
- `tests/test_web_console_*.py`
- `tests/web_console_playwright_smoke.py`

### BlueKiwi 공개 자료

- GitHub repository: https://github.com/dandacompany/bluekiwi
- README 주요 확인 내용:
  - self-hosted workflow engine, web UI, MCP, live timeline.
  - Docker Compose stack: Next.js app, PostgreSQL 16, Redis 7.
  - CLI install/init/accept and runtime injection.
  - Workflow Builder: Action, Gate, Loop, HITL.
  - MCP tools: workflow execution, task data, workflow CRUD, attachments, instructions, credentials, folders/sharing, compliance.
  - Security/RBAC: 4-tier roles, hashed API keys, group sharing, REST API path.
  - GitHub page metadata: 386 commits, 29 releases, latest `v1.2.3` on 2026-04-19.
