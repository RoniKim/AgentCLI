# CLI-first Multi-Agent Runner 설계 문서

> 기준: `agent_cli_bundle.zip` 내부 소스(=본 번들) 기준  
> 목적: **CLI 기반**(Codex CLI 스타일) 초기 설정 + 무인 운용(Loop) + 토큰/현금 최적화 + 유지보수성(모듈 분리)

---

## 0) 한 줄 요약

이 러너는 **PM(분석/백로그) → Dev(태스크 구현+게이트) → QA(요약 점검)** 을 “사이클(cycle)”로 실행하며,  
- **config가 없으면 시작 시 Wizard로 생성**(인터랙티브 CLI)  
- 프롬프트는 **파일로 로드**하되 **없거나 비어있으면 코드 기본값으로 폴백**  
- `--run-dir` 고정 재사용으로 **중단 후 재시작 시 이어서 진행**  
을 핵심 UX/운영 철학으로 삼습니다.

---

## 1) 프로젝트 구조(파일/폴더 맵)

### 1.1 엔트리포인트

- `agent_cli.py`  
  - **최상위 CLI 엔트리포인트**  
  - 실질 실행은 `agent_runner.main:main()`로 위임

- `agent_runner/main.py`  
  - `parse_args()`로 args 확정 후 `cycle.run()` 실행

---

### 1.2 핵심 런타임 모듈(수정 지점 찾기용)

- `agent_runner/cli.py`  
  - **CLI 스펙 정의(argparse)**  
  - config 로드/병합(우선순위: args > config > defaults)  
  - **config 없을 때 인터랙티브 메뉴로 wizard 선택**  
  - `--init-prompts`로 프롬프트 템플릿 생성 후 종료  
  - 수정 포인트
    - 옵션 추가/기본값 변경: `DEFAULTS`, `_build_parser()`
    - “config 없을 때 동작”: `parse_args()`의 interactive 분기

- `agent_runner/wizard.py`  
  - **초기 설정 Wizard**(Codex CLI 느낌)  
  - 질문/기본값/검증(정수 범위 등) 담당  
  - Wizard가 생성한 dict는 `agent_runner/config.py`로 저장됨
  - 수정 포인트
    - 질문 항목 추가/삭제/기본값 조정: `run_wizard()`

- `agent_runner/config.py`  
  - config 파일 로드/저장 및 경로 결정  
  - 기본 경로:
    - config: `REPO/.doc/agent_config.json`
    - prompts dir: `REPO/.doc/agent_prompts/`
  - 수정 포인트
    - config 스키마 확장/검증 강화: `load_config()`, `save_config()`

- `agent_runner/prompts.py`  
  - **프롬프트 로딩/렌더링 + 폴백 로직**  
  - 규칙:
    - `<name>.md` 파일이 **없거나 / 비어있으면(whitespace 포함)** → 코드 기본값 사용
  - `PromptStore.get(name, default)` : 파일 or default
  - `PromptStore.render(name, default, ctx)` : `{var}` 템플릿 치환(안전 포맷)
  - 템플릿 파일 자동 생성: `ensure_default_prompt_files(prompts_dir)`
  - 수정 포인트
    - 기본 프롬프트 수정: `*_DEFAULT` 상수
    - 템플릿 변수 추가/명세: `_safe_format()`, render 호출부(=cycle.py)

- `agent_runner/cycle.py`  
  - **전체 오케스트레이션 “본체”**  
  - 기능:
    - .env 로드(베스트 에포트)
    - run_dir 결정(새로 생성 or `--run-dir` 재사용)
    - PM 캐시(.doc/PM_CACHE) 갱신/사용
    - PM 실행 조건(bootstrap/incremental/refresh + working tree 옵션)
    - Dev 태스크 루프(체크포인트/롤백, build/test/policy 게이트)
    - QA 조건부 실행
    - `--loop` 반복 운용(Stop/Idle/Max cycles)
    - metrics/summary 기록
  - 수정 포인트(가장 자주 찾게 됨)
    - loop 정책: `main_async()`의 cycle loop
    - PM 실행 조건/토큰 절약 정책: `run_pm_if_needed()`
    - Dev 완료 판정/게이트: `run_task_once()` 주변(파일 내 함수들)
    - QA 조건: `qa_always` 및 “progress 있을 때만” 분기

---

### 1.3 운영 보조 모듈(게이트/로그/정책/깃/상태)

- `agent_runner/state.py`  
  - run_dir의 **STATE/BACKLOG** 읽기/쓰기  
  - 산출물:
    - `STATE.json` (done set 등)
    - `BACKLOG.json`, `BACKLOG.md`
  - 수정 포인트
    - 상태 스키마 변경: `load_state()`, `save_state()`
    - 백로그 파싱/마킹: `parse_backlog_md()`, `mark_backlog_done()`
    - fallback 백로그: `write_default_p0_backlog()`

- `agent_runner/gitops.py`  
  - Git 유틸:
    - HEAD 조회, 변경 파일 추출, `git status --porcelain`
    - repo fingerprint(working tree 포함 “지문”)
    - untracked 목록
    - **체크포인트/복구(=isolate_task 롤백의 핵심)**
  - 수정 포인트
    - isolate 전략 변경(현재는 체크포인트 방식): `create_checkpoint()`, `restore_checkpoint()`

- `agent_runner/gates.py`  
  - **dotnet build / dotnet test 게이트**
  - `find_build_cmd()` / `find_test_cmd()`로 커맨드 결정
  - 수정 포인트
    - 빌드/테스트 커맨드 규칙 변경
    - timeout 기본값(테스트) 변경

- `agent_runner/policy.py`  
  - **정책/시크릿 스캔(정규식 룰)**
  - 기본 룰: OpenAI 키/GitHub 토큰/AWS 키/Private key block/password assignment 등
  - 룰 소스:
    - `--policy-rules-file`(JSON)
    - `--policy-rule`(추가 regex 문자열)
  - 수정 포인트
    - 기본 룰 추가/오탐 조정: `DEFAULT_POLICY_RULES`
    - 최대 hit 제한/출력 포맷: `policy_scan_text()`

- `agent_runner/metrics.py`  
  - **metrics.jsonl** JSON Lines 로 이벤트 기록(append-only)
  - 런타임 안정성을 위해 관측 실패는 무시(에러로 runner 중단 방지)

- `agent_runner/docs.py`  
  - `.env` 탐색/로드, Docs 폴더 digest 생성(토큰 절약용)
  - 수정 포인트
    - digest 생성 규칙(헤딩 추출 등): `generate_docs_digest()`

- `agent_runner/inventory.py`  
  - `git ls-files` 기반 레포 인벤토리 생성
  - PM 입력용:
    - `REPO_INVENTORY.json/md` 같은 인덱스 산출(저장 위치는 PM_CACHE)
  - 수정 포인트
    - 제외 규칙/파일 크기 제한: `build_repo_inventory()`

- `agent_runner/analysis_cache.py`  
  - Dev가 남긴 “힌트/메모”를 PM 전역 분석 문서에 “changelog 형태”로 합쳐주는 보조 기능  
  - 수정 포인트
    - changelog 포맷/합치기 규칙: `merge_dev_hints_to_global_changelog()`

- `agent_runner/run_dir.py`  
  - 기본 run_dir 생성 로직(타임스탬프 폴더)
  - 재개/고정은 `--run-dir`로 제어(=cli/cycle에서)

- `agent_runner/utils.py`  
  - 공통 유틸(프로세스 실행, 안전 파일 읽기/쓰기, UTF-8 STDIO 강제 등)

---

## 2) 실행 흐름(구동 방식)

### 2.1 시작: CLI-first + config/wizard

1) `python agent_cli.py --repo <path>` 실행  
2) `agent_runner/cli.py:parse_args()`에서 아래 순서로 설정 확정:
- `--config` 경로 결정(기본: `REPO/.doc/agent_config.json`)
- config 존재 → 로드
- config 없음 → (TTY & not `--non-interactive`)면 **메뉴로 wizard 선택**  
- config를 로드/생성한 뒤, **args > config > defaults**로 최종 args 생성
- `--init-prompts`면 프롬프트 템플릿을 prompts_dir에 생성하고 종료

3) `agent_runner/cycle.py:run()` → `main_async()`로 전체 실행

---

### 2.2 런타임 저장소: “전역 캐시” vs “run_dir”

- 전역(Repo 단위, 계속 재사용): `REPO/.doc/PM_CACHE/`
  - `PROJECT_ANALYSIS.md` (PM 전역 분석)
  - `REPO_SNAPSHOT.json` (이전 HEAD 등)
  - `PM_LAST_FINGERPRINT.json` (working tree 포함 옵션의 공회전 방지용)
  - 인벤토리 파일들

- 실행(run) 단위, 기본은 매번 새로 생성(재개는 `--run-dir`):  
  - `REPO/.doc/agent_runs/<timestamp>/...`
  - `STATE.json`, `BACKLOG.json`, `BACKLOG.md`
  - `metrics.jsonl`, `last_run_summary.json`, `cycle_summary.log`
  - `tasks/<cycle>_<task>/...` (태스크별 산출물)

> **중단 후 이어서 하려면** 반드시 `--run-dir`로 같은 run_dir를 재사용하세요.

---

### 2.3 사이클(cycle) 단위 실행(PM → Dev → QA)

- PM 단계
  - 최초: `PROJECT_ANALYSIS.md`가 없으면 bootstrap PM 실행(비용 큼, 1회)
  - 변경 감지 시: incremental PM 실행
  - 옵션:
    - `--pm-include-working-tree` : working tree 변화도 반영(토큰 증가 가능)
    - `--pm-refresh-backlog` / `--pm-refresh-every-cycles N` : backlog 주기 갱신

- Dev 단계
  - `BACKLOG.json`의 미완료 태스크를 최대 `iterations`만큼 수행
  - 기본 정책(토큰 절약/안전):
    - 변경(diff) 없으면 실패 처리(공회전 방지)
    - 빌드 기본 ON(`--no-build`로만 끔)
    - 테스트 옵션(`--run-tests`)
    - 정책 스캔 기본 ON(`--no-policy-scan`으로만 끔)
    - `--isolate-task`면 체크포인트 생성 후 실패 시 자동 롤백

- QA 단계
  - 기본은 “진행(progress)이 있을 때만” QA 실행(토큰 절약)
  - `--qa-always`면 매 사이클 QA 실행

---

### 2.4 무인 운용 Loop

- `--loop` 켜면 cycle을 반복 수행
- 종료 조건:
  - run_dir 내 `STOP` 파일 존재(기본 이름 `STOP`, `--stop-file`로 변경 가능)
  - `--loop-max-cycles` 도달(0이면 사실상 무제한)
  - `--loop-idle-exit-after` 초과(진행이 없으면 idle 누적 → 종료)
- 사이클 요약 로그(최소 1줄/사이클):
  - stdout: `[CYCLE] ts idx=... rc=... reason=... progress_delta=...`
  - `cycle_summary.log`에도 append

---

## 3) 프롬프트 파일 로딩 규칙(요청사항 반영)

### 3.1 파일 위치
- 기본 prompts_dir: `REPO/.doc/agent_prompts/`
- 변경: `--prompts-dir <path>` 또는 config의 `prompts_dir`

### 3.2 파일 명 규칙(=name.md)
`agent_runner/prompts.py:PromptStore`는 아래 이름을 그대로 사용합니다.

- `pm_instructions.md`
- `dev_instructions.md`
- `qa_instructions.md`
- `pm_bootstrap_prompt.md`
- `pm_incremental_prompt.md`
- `dev_task_prompt.md`
- `qa_prompt.md`

### 3.3 폴백 규칙(중요)
- 파일이 **없으면** → 코드 내부 기본값 사용
- 파일이 있어도 내용이 **공백이면** → 코드 내부 기본값 사용
- 정상 내용이 있으면 → 파일 내용을 사용
- 템플릿 `{변수}`는 안전 포맷으로 치환(모르는 키는 `{key}`로 남김)

### 3.4 템플릿 생성
- `--init-prompts` 실행 시 prompts_dir에 위 파일들을 생성(기존 파일은 덮어쓰지 않음)
- wizard에서도 prompts_dir 생성/템플릿 생성이 수행됨

---

## 4) “무엇을 고치려면 어디를 봐야 하나” 빠른 인덱스

| 하고 싶은 변경 | 파일 | 핵심 함수/위치 |
|---|---|---|
| CLI 옵션 추가/기본값 변경 | `agent_runner/cli.py` | `DEFAULTS`, `_build_parser()` |
| config 없을 때 wizard/메뉴 동작 변경 | `agent_runner/cli.py` | `parse_args()`의 interactive 분기 |
| wizard 질문 항목/기본값 수정 | `agent_runner/wizard.py` | `run_wizard()` |
| config 스키마/경로 정책 변경 | `agent_runner/config.py` | `default_config_path()`, `load_config()` |
| 프롬프트 기본값 수정 | `agent_runner/prompts.py` | `*_DEFAULT` 상수 |
| 프롬프트 파일명/로딩 규칙 변경 | `agent_runner/prompts.py` | `PromptStore.get()` |
| 프롬프트 템플릿 변수 추가 | `agent_runner/cycle.py` | `store.render(..., ctx=...)` ctx 확장 |
| loop 종료 조건/idle 정책 변경 | `agent_runner/cycle.py` | `main_async()` cycle 루프 |
| PM 실행 조건(bootstrap/incremental/refresh) 변경 | `agent_runner/cycle.py` | `run_pm_if_needed()` |
| Dev 완료 판정(no-diff 처리) 변경 | `agent_runner/cycle.py` | 태스크 실행 후 diff 판정 로직 |
| 빌드 기본/대상 변경 | `agent_runner/gates.py` | `find_build_cmd()`, `dotnet_build()` |
| 테스트 커맨드/필터/타임아웃 변경 | `agent_runner/gates.py` | `find_test_cmd()`, `dotnet_test()` |
| 정책 스캔 룰 추가/오탐 조정 | `agent_runner/policy.py` | `DEFAULT_POLICY_RULES` |
| 정책 스캔을 “변경된 파일만”으로 좁히기 | `agent_runner/cycle.py` | 정책 스캔 입력(diff 수집) 부분 |
| isolate(롤백) 전략을 worktree로 바꾸기 | `agent_runner/gitops.py` + `cycle.py` | `create_checkpoint()/restore_checkpoint()` 교체 |
| metrics 이벤트/스키마 확장 | `agent_runner/metrics.py` + `cycle.py` | `MetricsLogger.event(...)` 호출부 |
| docs digest 생성 규칙 변경 | `agent_runner/docs.py` | `generate_docs_digest()` |
| 레포 인벤토리 제외 규칙/용량 제한 | `agent_runner/inventory.py` | `build_repo_inventory()` |
| Dev 힌트 merge 규칙 변경 | `agent_runner/analysis_cache.py` | `merge_dev_hints_to_global_changelog()` |

---

## 5) 운영 체크리스트(중단/재시작/토큰 최적화)

- **재시작 시 run_dir 고정**
  - `--run-dir REPO/.doc/agent_runs/night_run` 추천
- STOP 파일 기반 중단
  - `run_dir/STOP` 생성 → 다음 cycle에서 정상 종료
- 공회전 방지
  - `--loop-idle-exit-after` 설정(기본 3600초)
  - no-diff는 기본 실패 처리(진행 없는 비용 낭비 차단)
- 토큰 절약 기본값 유지
  - docs는 `digest` 중심
  - QA는 progress 있을 때만(기본)

---

## 6) 확장 설계(다음 리팩터링 후보)

- `agent_runner/cycle.py`가 가장 크므로, 다음 단계에서는 아래로 분리하는 게 좋습니다.
  - `stages/pm.py`, `stages/dev.py`, `stages/qa.py` 로 stage 함수 분리
  - `loop.py`로 loop 정책 분리
  - `artifacts.py`로 run_dir 산출물 경로/이름 통합

---

## 7) 문서 유지 방침

- 이 문서는 “소스 구조의 인덱스” 역할이므로,
  - 파일/함수명이 바뀌면 이 문서의 표(인덱스)도 함께 갱신하세요.
- 추천 위치:
  - 번들 루트: `DESIGN.md`
  - 혹은 `docs/DESIGN.md` (프로젝트 정책에 맞게)
