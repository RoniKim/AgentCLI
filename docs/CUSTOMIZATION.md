← [README로 돌아가기](../README.md)

> 최종 검증: 2026-04-28 (코드 기준)

# 프롬프트/문서/스킬

## 프롬프트 템플릿 커스터마이징

기본 프롬프트는 **Python-side prompts_dir**에 저장됩니다(레포 내부가 기본이 아님).

- 기본 prompts_dir: `AGENTCLI_HOME/prompts/<repo-slug>-<hash>/`

템플릿 생성(1회):
```bash
python agent_cli.py --run-now --repo "<path>" --init-prompts
```

> 생성 후에는 prompts_dir의 `pm_instructions.md`, `dev_instructions.md` 등을 수정해 튜닝할 수 있습니다.

## Docs 읽기(Digest) — 토큰 절약

기본값:
- `docs_read_mode="digest"`
- `docs_dir=".doc/Docs"`
- `docs_digest_file=".doc/DOCS_DIGEST.md"`

Digest 생성/갱신(로컬 작업, 토큰 사용 없음):
```bash
python agent_cli.py --run-now --repo "<path>" --generate-digest
```

## Skills 시스템 (Codex/Claude 공통)

AgentCLI는 스킬 폴더를 스캔해 `SKILLS_INDEX` 요약을 만들어 **PM/QA에 인라인(발췌)** 할 수 있습니다.
Dev에는 스킬 본문을 길게 인라인하지 않는 방향으로 설계되어 있습니다(토큰 방어).

config 예시(핵심):
```json
{
  "skills": {
    "enabled": true,
    "roots": [
      "~/.codex/skills",
      "~/.agents/skills",
      "~/.claude/skills"
    ],
    "snapshot_dir": ".AgentCLI/skills",
    "inline_mode": "qa",
    "max_excerpt_lines": 12
  }
}
```

**기본 roots (cli.py DEFAULTS)**: 위 3개 경로(`~/.codex/skills`, `~/.agents/skills`, `~/.claude/skills`)가 디폴트입니다. `{repo}/Skills` 같은 프로젝트 내부 스킬 폴더는 디폴트가 아니며, 필요시 사용자가 직접 `roots`에 추가해야 합니다.

**`inline_mode` 입력 정규화**: 사용자가 `"off"`로 입력하면 자동으로 `"none"`으로 정규화됩니다 (cli.py `_validate_skills_config()`). 허용 값은 `qa` | `pm` | `both` | `none`이며, 그 외 값은 모두 `"qa"`로 강제됩니다.

---

# 외부 프롬프트 작성 가이드

AgentCLI의 프롬프트는 **기본 내장 템플릿**과 **프로젝트별 외부 오버라이드**로 구성됩니다. 이 섹션은 외부 프롬프트를 작성하는 방법을 상세히 설명합니다.

## PromptStore 동작 원리

```
PromptStore.render(name, default, ctx)
  │
  ├─ 1) prompts_dir/{name}.md 파일이 존재하고 비어있지 않으면 → 파일 내용 사용
  │
  └─ 2) 없으면 → default (코드 내장 템플릿) 사용
       │
       ▼
  format_map(ctx) → {variable} 치환
       │
       ▼
  append_pm_output_contract() → JSON 스키마 계약 자동 추가 (PM만)
       │
       ▼
  append_pm_essential_context() → 필수 런타임 블록 자동 추가 (PM만)
```

**핵심 규칙:**
- 외부 파일은 **완전 대체** — 기본 템플릿과 합치지(merge) 않음
- 누락된 `{variable}`은 경고만 출력하고 리터럴 그대로 남음 (`_SafeDict`)
- 필수 런타임 블록(done/failed tasks, goals, build warnings)은 **프로그래밍적으로 자동 주입** — 외부 템플릿에서 빼먹어도 러너가 자동으로 붙여줌

## 오버라이드 가능 파일 목록

| 파일명 | 역할 | 적용 대상 |
|--------|------|-----------|
| `pm_instructions.md` | PM 에이전트 시스템 지시문 | 모든 PM 호출 |
| `pm_bootstrap_prompt.md` | PM 첫 실행 프롬프트 | Bootstrap 모드 |
| `pm_incremental_prompt.md` | PM 반복 실행 프롬프트 | Incremental/Refresh 모드 |
| `dev_instructions.md` | Dev 에이전트 시스템 지시문 | 모든 Dev 호출 |
| `dev_task_prompt.md` | Dev 태스크 실행 프롬프트 | 태스크별 |
| `qa_instructions.md` | QA 에이전트 시스템 지시문 | 모든 QA 호출 |
| `qa_prompt.md` | QA 검증 프롬프트 | Cycle별 |

## 외부 프롬프트 생성/위치

```bash
# 기본 템플릿을 외부 파일로 복사 (1회)
python agent_cli.py --run-now --repo "<경로>" --init-prompts
```

생성 위치: `{AGENTCLI_HOME}/prompts/<repo-slug>-<hash>/`

```
prompts/
├── BudgetBook-69084820/
│   ├── pm_instructions.md
│   ├── pm_bootstrap_prompt.md
│   ├── pm_incremental_prompt.md
│   ├── dev_instructions.md
│   ├── dev_task_prompt.md
│   ├── qa_instructions.md
│   └── qa_prompt.md
└── argos_ai-cdc5165b/
    └── ...
```

## 템플릿 변수 레퍼런스

### PM Bootstrap 프롬프트 변수

| 변수 | 설명 | 예시 값 |
|------|------|---------|
| `{analysis_md}` | PROJECT_ANALYSIS.md 경로 | `.AgentCLI/PM_CACHE/PROJECT_ANALYSIS.md` |
| `{inv_md}` | REPO_INVENTORY.md 경로 | `.AgentCLI/PM_CACHE/REPO_INVENTORY.md` |
| `{repo}` | 레포 루트 경로 | `D:\Dev\BudgetBook` |
| `{run_dir}` | 실행 산출물 폴더 경로 | `.AgentCLI/agent_runs/20260212-140000` |
| `{todo_block}` | 사용자 TODO 내용 | `## Priorities\n- 로그인 구현` 또는 `(none)` |
| `{docs_dir}` | Docs 폴더 경로 | `.doc/Docs` 또는 `(none)` |
| `{docs_read_mode}` | Docs 읽기 모드 | `digest` |
| `{digest_rel}` | Docs 다이제스트 상대 경로 | `.doc/DOCS_DIGEST.md` |
| `{skills_index_summary}` | 스킬 인덱스 요약 | `- blazor_ui: Blazor UI 패턴 [blazor, ui]` |
| `{codex_call_hint}` | Codex MCP 호출 힌트 | `{"approval_policy": "..."}` |
| `{task_history_block}` | 이전 실행 태스크 이력 | `- [DONE] T01: CRUD 구현 (2026-02-10)` |

### PM Incremental 프롬프트 변수 (Bootstrap 변수 + 아래 추가)

| 변수 | 설명 |
|------|------|
| `{prev_head}` | 이전 Git HEAD SHA |
| `{curr_head}` | 현재 Git HEAD SHA |
| `{changed_files_block}` | 변경된 파일 목록 (`git diff --name-only`) |
| `{current_backlog_block}` | 현재 백로그 상태 (`[x]=완료, [ ]=대기, [F]=실패`) |
| `{hint_block}` | Dev 분석 힌트 (dev_hints/*.md 내용) |

### Dev 태스크 프롬프트 변수

| 변수 | 설명 |
|------|------|
| `{repo}` | 레포 루트 경로 |
| `{run_dir}` | 실행 산출물 폴더 경로 |
| `{task_id}` | 태스크 ID (T1, T2, ...) |
| `{task_title}` | 태스크 제목 |
| `{task_prompt}` | 태스크 구현 지침 |
| `{files_hint}` | 변경 대상 파일 목록 (시작점) |
| `{skills_context}` | 선택된 스킬 발췌문 |
| `{done_when}` | 완료 조건 |
| `{docs_read_mode}` | Docs 읽기 모드 |
| `{digest_rel}` | Docs 다이제스트 경로 |
| `{analysis_hint_out}` | 분석 힌트 출력 경로 |
| `{codex_call_hint}` | Codex MCP 호출 힌트 |

### QA 프롬프트 변수

| 변수 | 설명 |
|------|------|
| `{repo}` | 레포 루트 경로 |
| `{run_dir}` | 실행 산출물 폴더 경로 |
| `{skills_context}` | 태스크별 스킬 컨텍스트 |

## 자동 주입 블록 (프로그래밍적)

외부 템플릿에 아래 블록이 없어도 러너가 **자동으로 프롬프트 끝에 추가**합니다. 중복 방지를 위해 HTML 마커로 체크합니다.

| 블록 | 마커 | 대상 | 설명 |
|------|------|------|------|
| **턴 예산 경고** | `<turn_budget_warning>` | PM | PM이 JSON 출력 없이 턴을 소진하지 않도록 경고 |
| **프로젝트 Goals** | `<pm_goals>` | PM | GOALS.md 내용 + 완성 기준 평가 지침 |
| **완료 태스크** | `<pm_done_tasks>` | PM | 이미 완료된 태스크 목록 (중복 생성 방지) |
| **빌드 경고** | `<pm_build_warnings>` | PM | 최신 빌드 경고 (CS8602, CS4014 등) |
| **실패 태스크** | `<pm_failed_tasks>` | PM | MANDATORY RETRY — 실패 태스크 + Dev 로그 tail |
| **출력 계약** | `<pm_output_contract>` | PM | JSON 스키마 계약 (PMOutputV2) |
| **태스크 사이징** | `<pm_task_sizing_rules>` | PM | 3-7 태스크 규칙, 번들링 지침 |

**중요**: 이 블록들은 ctx dict에 넣는 템플릿 변수(`{variable}`)가 아닙니다. 러너가 템플릿 렌더링 **이후에** 프로그래밍적으로 append합니다. 외부 프롬프트에서 해당 내용을 직접 작성할 필요가 없습니다.

## 실전 예제: 프로젝트별 PM 프롬프트

**`pm_incremental_prompt.md` 예시 (BudgetBook 프로젝트):**

```markdown
You are Planner/PM.

INCREMENTAL MODE (token-saving):
- Global analysis already exists at: {analysis_md}
- Do NOT redo full analysis.

Reference file list: {inv_md}

Git:
- prev_head: {prev_head}
- curr_head: {curr_head}
- changed files: {changed_files_block}

Current backlog: {current_backlog_block}

Dev hints: {hint_block}

SKILLS_INDEX summary:
{skills_index_summary}

## 프로젝트 특화 지침

- BudgetBook은 .NET MAUI Blazor Hybrid 앱입니다
- Razor 컴포넌트의 CancellationToken 패턴을 항상 확인하세요
- 안드로이드/Windows 동시 빌드 호환성 유지

User TODO:
{todo_block}

Rules:
- FILE PATHS: REPO_INVENTORY.md의 전체 경로만 사용
- 태스크는 atomic하게, 각각 git diff를 생산해야 함
- 질문이 있으면 open_questions에 기재

When editing files, call Codex MCP with {codex_call_hint}.

Now execute: update PROJECT_ANALYSIS.md, then respond ONLY with the JSON schema object.
```

**주의사항:**
- `{goals_block}`, `{done_tasks_block}`, `{failed_tasks_block}`, `{turn_budget_warning}`, `{build_warnings_block}` — 이 변수들은 **사용하지 마세요**. ctx에 포함되지 않으며 자동 주입됩니다.
- `{codex_call_hint}`는 Codex 백엔드에서 JSON 승인 정책을 주입합니다. Claude 백엔드에서는 자동으로 Claude 도구 지침으로 대체됩니다.

## 프롬프트 작성 시 주의사항

1. **ctx에 없는 변수를 쓰면**: 리터럴 그대로 남음 (예: `{my_custom_var}` → 출력에 `{my_custom_var}` 표시). `_SafeDict`가 stderr에 경고만 출력.

2. **JSON 중괄호 이스케이프**: 프롬프트에 JSON 예시를 넣을 때 `{{`, `}}`로 이스케이프해야 합니다.
   ```
   올바른 예: {{"kind": "pm_output_v2", "tasks": [...]}}
   잘못된 예: {"kind": "pm_output_v2"} → kind를 변수로 인식 시도
   ```

3. **외부 프롬프트는 완전 대체**: 기본 템플릿의 일부만 수정하려면, `--init-prompts`로 전체 복사 후 원하는 부분만 수정하세요.

4. **PM 출력 스키마는 자동 보장**: `pm_instructions.md`에 JSON 스키마를 직접 넣지 않아도, `ensure_pm_instructions_have_output_schema()`가 자동으로 추가합니다.

5. **Claude 백엔드 차이**: Claude 백엔드에서는 `_patch_prompt_for_claude()` 함수가 Codex 특화 지시문(예: `apply_patch` 참조)을 Claude 도구 참조(Read, Write, Edit, Grep, Glob, Bash)로 자동 변환합니다.

---

# 스킬 파일 작성법

## 스킬 파일 구조

스킬은 **Markdown 파일 (`SKILL.md`)**로 작성합니다. 파일명은 반드시 `SKILL.md`여야 합니다.

```
~/.agents/skills/
├── blazor/
│   └── SKILL.md        ← blazor/SKILL.md
├── dotnet-test/
│   └── SKILL.md        ← dotnet-test/SKILL.md
└── react/
    ├── hooks/
    │   └── SKILL.md    ← react/hooks/SKILL.md
    └── SKILL.md        ← react/SKILL.md
```

## Frontmatter 형식

```markdown
---
name: Blazor State Management
description: Blazor Hybrid 앱의 상태 관리 패턴
tags: [blazor, state, maui, dependency-injection]
---

# Blazor State Management

## 핵심 패턴

1. Scoped Service 사용
...
```

**지원 필드:**

| 필드 | 필수 | 설명 | 폴백 |
|------|------|------|------|
| `name` (또는 `title`) | 아니오 | 스킬 이름 | 디렉토리명 사용 |
| `description` (또는 `desc`) | 아니오 | 한 줄 설명 | 첫 번째 heading 또는 첫 비어있지 않은 줄 |
| `tags` | 아니오 | 태그 배열 | 빈 배열 |

**태그 형식 (두 가지 모두 지원):**
```yaml
# 인라인
tags: [blazor, state, maui]

# 리스트
tags:
  - blazor
  - state
  - maui
```

## 스킬 ID 생성 규칙

```
skill_id = "{relative_path}#{sha1(source_root::relative_path)[:10]}"
```

예: `blazor/SKILL.md#a1b2c3d4e5`

## 인덱싱 및 PM 연동

1. **인덱싱**: 러너 시작 시 `skills_index.json` 자동 생성
2. **PM 요약**: PM 프롬프트에 `{skills_index_summary}` 변수로 요약 전달
3. **PM 선택**: PM이 태스크별로 `skills` 필드에 skill_id를 지정
4. **Dev/QA 발췌**: 선택된 스킬의 본문을 발췌(최대 `max_excerpt_lines`줄)하여 프롬프트에 인라인

## 퍼지 매칭 (Auto-fix)

PM이 존재하지 않는 skill_id를 참조하면:
- `difflib.SequenceMatcher`로 유사도 비교 (skill_id, name, path 3가지 대상)
- 상위 3개 후보를 자동 제안
- `skill_match_autofix=true` + `skill_match_autofix_threshold` 초과 시 자동 교정 (기본은 비활성)

## Config 참조

```json
{
  "skills": {
    "enabled": true,
    "roots": [
      "~/.codex/skills",
      "~/.agents/skills",
      "~/.claude/skills"
    ],
    "snapshot_dir": ".AgentCLI/skills",
    "inline_mode": "qa",
    "max_excerpt_lines": 12,
    "pm_summary_max_items": 120,
    "pm_summary_max_chars": 8000,
    "qa_max_total_chars": 8000,
    "skill_match_autofix": false,
    "skill_match_autofix_threshold": 0.9
  }
}
```

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `enabled` | `false` | 스킬 시스템 활성화 |
| `roots` | `["~/.codex/skills", "~/.agents/skills", "~/.claude/skills"]` | 스킬 검색 루트 (`{repo}/Skills`는 디폴트가 아님) |
| `snapshot_dir` | `""` (비활성) | 스킬 스냅샷 저장 경로 |
| `inline_mode` | `"qa"` | 스킬 발췌 인라인 대상: `qa`, `pm`, `both`, `none` (`"off"` → `"none"`으로 정규화) |
| `max_excerpt_lines` | `12` | 스킬 발췌 최대 줄 수 |
| `pm_summary_max_items` | `120` | PM 인덱스 요약 최대 항목 수 |
| `pm_summary_max_chars` | `8000` | PM 인덱스 요약 총 글자 수 상한 |
| `qa_max_total_chars` | `8000` | QA 스킬 컨텍스트 총 글자 수 상한 |
| `skill_match_autofix` | `false` | 퍼지 매칭 자동 교정 활성화 (기본 비활성 — 제안만 출력) |
| `skill_match_autofix_threshold` | `0.9` | 자동 교정 최소 유사도 임계값 |
