# Skills 시스템 확장 계획

> **작성일**: 2026-02-06
> **상태**: 미착수 (계획 완료)
> **관련 모듈**: `agent_runner/skills/`

---

## 1. 현행 시스템 진단

### 1.1 아키텍처 요약

AgentCLI의 Skills는 **파일시스템 기반 프롬프트 주입 메커니즘**이다.
`SKILL.md` 파일을 탐색/파싱하여 PM, Dev, QA 에이전트 프롬프트에 단계별로 삽입한다.

```
skills/
├── __init__.py      # 모듈 공개 API
├── parser.py        # YAML frontmatter + Markdown 파싱 → SkillMetadata
├── indexer.py       # 파일 탐색, SHA1 해싱, SkillRecord 생성, 스냅샷 저장
├── excerpt.py       # 본문 발췌 + 문자 제한 기반 컨텍스트 빌드
├── match.py         # difflib 기반 퍼지 매칭 (누락 skill_id 대응)
└── summary.py       # PM용 요약 (one-liner 리스트)
```

### 1.2 단계별 주입 방식 (현재)

| 단계 | 함수 | 주입 내용 | 문제점 |
|------|------|----------|--------|
| **PM** | `summarize_skills_index_capped()` | `skill_id \| name \| description` 한 줄 요약 | 없음 (적절) |
| **Dev** | `_format_skill_selection()` | **파일 경로만** (name, root, relative_path, resolved_path) | **본문 미전달** |
| **QA** | `build_skills_context()` | 발췌문 포함 가능 (`inline_mode`에 따라) | 없음 (적절) |

### 1.3 핵심 병목

**Dev 에이전트가 스킬 본문을 받지 못한다.**

`cycle.py:115-130`, `claudecode.py:114-129`의 `_format_skill_selection()`:

```python
# Dev가 실제로 받는 내용:
# - My Skill (domain/my_skill#abc123)
#   - root: /home/user/.agents/skills
#   - relative_path: domain/my_skill
#   - resolved_path: /home/user/.agents/skills/domain/my_skill
```

PM이 스킬을 선별해도 Dev에게는 파일 경로 힌트일 뿐이며,
Dev가 실제 내용을 얻으려면 추가 도구 호출(파일 읽기)이 필요하다.

### 1.4 `SkillMetadata` 현재 필드 (`parser.py:7-12`)

```python
@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    tags: list[str]
    body_lines: list[str]
```

Codex CLI와 Claude Code SDK의 확장 필드(`context`, `allowed-tools`, `disable-model-invocation` 등)를 인식하지 못한다.

---

## 2. 외부 시스템 비교

### 2.1 OpenAI Codex CLI Skills

| 항목 | 구현 |
|------|------|
| 파일 형식 | `SKILL.md` (YAML frontmatter + Markdown body) |
| 탐색 경로 | `$CWD/.agents/skills/` → 상위 → 레포루트 → `~/.agents/skills/` → `/etc/codex/skills/` |
| 호출 방식 | 명시적(`$skill-name`) 또는 암묵적(description 기반 자동 매칭) |
| Progressive disclosure | name/description만 로드, body는 호출 시 로드 |
| MCP 통합 | `~/.codex/config.toml`에서 MCP 서버 설정 |
| Always-on context | `AGENTS.md` 파일 |
| frontmatter 필드 | `name`(필수), `description`(필수) |

### 2.2 Claude Code SDK Skills

| 항목 | 구현 |
|------|------|
| 파일 형식 | `SKILL.md` + 8개 추가 frontmatter 필드 |
| 탐색 경로 | enterprise → `~/.claude/skills/` → `.claude/skills/` → plugin |
| 호출 방식 | 모델 자동 호출 + 사용자 명시(`/skill-name`) |
| 고유 기능 | `context: fork` (서브에이전트), `allowed-tools`, 동적 컨텍스트(`` !`command` ``) |
| 인자 전달 | `$ARGUMENTS`, `$ARGUMENTS[N]`, `$N` 치환 |
| 호출 제어 | `disable-model-invocation` + `user-invocable` 2축 매트릭스 |
| 확장 사고 | "ultrathink" 키워드로 extended thinking 활성화 |

### 2.3 AgentCLI의 고유 강점

- **멀티에이전트 차등 주입**: PM→요약, Dev→(현재 경로만), QA→발췌문. Codex/Claude Code에 없는 기능.
- **퍼지 매칭** (`match.py`): 누락된 skill_id에 대해 자동 유사 스킬 제안.
- **SHA1 해싱 기반 중복 제거**: 동일 스킬이 여러 루트에 있을 때 자동 처리.

---

## 3. 확장 계획

### Phase 1: parser 확장 + Dev 본문 주입 (최우선)

**목표**: Skills 시스템의 실질적 가치를 Dev 단계에서 실현
**예상 작업량**: ~130줄 수정, 3-4시간
**변경 파일**: `parser.py`, `indexer.py`, `cycle.py`, `claudecode.py`, `cli.py`

#### 1-1. `parser.py` — SkillMetadata 확장

```python
@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    tags: list[str]
    body_lines: list[str]
    # ── Codex / Claude Code 호환 필드 ──
    context: str                     # "fork" | "" (서브에이전트 실행 여부)
    allowed_tools: list[str]         # ["Read", "Grep"] 등
    disable_model_invocation: bool   # True → PM 자동선택 제외
    user_invocable: bool             # False → 사용자 호출 불가, 모델만 호출
    argument_hint: str               # "[issue-number]" 등 힌트 텍스트
```

`parse_skill_text()` 수정사항:

```python
def parse_skill_text(text: str, *, fallback_name: str) -> SkillMetadata:
    # ... 기존 파싱 로직 유지 ...

    # 확장 필드 파싱
    context = str(frontmatter.get("context", "")).strip()

    allowed_tools_val = frontmatter.get("allowed-tools") or frontmatter.get("allowed_tools", "")
    if isinstance(allowed_tools_val, str):
        allowed_tools = [t.strip() for t in allowed_tools_val.split(",") if t.strip()]
    elif isinstance(allowed_tools_val, list):
        allowed_tools = [str(t).strip() for t in allowed_tools_val if str(t).strip()]
    else:
        allowed_tools = []

    disable_model = str(
        frontmatter.get("disable-model-invocation")
        or frontmatter.get("disable_model_invocation", "")
    ).strip().lower() in ("true", "yes", "1")

    user_invocable = str(
        frontmatter.get("user-invocable")
        or frontmatter.get("user_invocable", "true")
    ).strip().lower() not in ("false", "no", "0")

    argument_hint = str(
        frontmatter.get("argument-hint")
        or frontmatter.get("argument_hint", "")
    ).strip()

    return SkillMetadata(
        name=name,
        description=description,
        tags=tags,
        body_lines=body_lines,
        context=context,
        allowed_tools=allowed_tools,
        disable_model_invocation=disable_model,
        user_invocable=user_invocable,
        argument_hint=argument_hint,
    )
```

**하위 호환**: 기존 SKILL.md에 새 필드가 없으면 기본값 적용. 동작 변경 없음.

#### 1-2. `indexer.py` — SkillRecord 확장

```python
@dataclass(frozen=True)
class SkillRecord:
    skill_id: str
    name: str
    description: str
    tags: list[str]
    source_root: str
    relative_path: str
    skill_path: Path
    last_modified: str
    content_hash: str
    # ── 추가 ──
    context: str = ""
    allowed_tools: list[str] = ()      # frozen이므로 tuple 사용 고려
    disable_model_invocation: bool = False
    user_invocable: bool = True
    argument_hint: str = ""
```

`build_skills_index()` 내 `SkillRecord` 생성부에 새 필드 전달:

```python
records.append(
    SkillRecord(
        # ... 기존 필드 ...
        context=meta.context,
        allowed_tools=meta.allowed_tools,
        disable_model_invocation=meta.disable_model_invocation,
        user_invocable=meta.user_invocable,
        argument_hint=meta.argument_hint,
    )
)
```

#### 1-3. `_format_skill_selection()` 확장 — Dev 본문 주입

**`cycle.py`와 `claudecode.py` 모두 수정** (동일 함수 중복):

```python
def _format_skill_selection(
    skill_ids: list[str],
    skills_by_id: dict[str, Any],
    *,
    include_body: bool = False,
    max_body_lines: int = 30,
) -> str:
    if not skill_ids:
        return "(none)"
    lines: list[str] = []
    missing: list[str] = []
    for sid in skill_ids:
        rec = skills_by_id.get(sid)
        if rec is not None:
            lines.append(f"- {rec.name} ({sid})")
            if include_body and max_body_lines > 0:
                text, status = read_text_robust(rec.skill_path)
                if status == "ok":
                    meta = parse_skill_text(text, fallback_name=rec.name)
                    body = meta.body_lines[:max_body_lines]
                    if body:
                        lines.append("  - skill content:")
                        lines.extend([f"    {l}" for l in body])
            else:
                try:
                    resolved_path = rec.skill_path.resolve()
                except Exception:
                    resolved_path = rec.skill_path
                lines.append(f"  - resolved_path: {resolved_path}")
        else:
            lines.append(f"- {sid} (missing)")
            missing.append(sid)
    # ... 기존 missing 처리 유지 ...
    return "\n".join(lines)
```

호출부 수정 (`cycle.py:1642`, `claudecode.py:991`):

```python
include_body = _inline_skills_for("dev", skills_cfg.get("inline_mode", ""))
skills_context = _format_skill_selection(
    next_task.skills or [],
    skills_by_id,
    include_body=include_body,
    max_body_lines=int(skills_cfg.get("dev_max_body_lines", 30)),
)
```

#### 1-4. `cli.py` — 설정 추가

```python
"skills": {
    "enabled": False,
    "roots": [ ... ],           # 기존 유지
    "snapshot_dir": "",
    "inline_mode": "both",      # 기존 "qa" → "both"로 기본값 변경
    "max_excerpt_lines": 12,
    "dev_max_body_lines": 30,   # 추가: Dev용 본문 라인 제한
    "dev_max_total_chars": 12000, # 추가: Dev용 전체 문자 제한
    "pm_summary_max_items": 120,
    "pm_summary_max_chars": 8000,
    "qa_max_total_chars": 8000,
    "skill_match_autofix": False,
    "skill_match_autofix_threshold": 0.9,
},
```

#### Phase 1 검증 방법

1. 기존 SKILL.md로 테스트 → 확장 필드 없이도 정상 동작 확인 (하위 호환)
2. Claude Code 형식 SKILL.md 생성 → 확장 필드 파싱 확인
3. `inline_mode: "both"`로 실행 → Dev 프롬프트에 본문 포함 확인
4. 대형 스킬 테스트 → `dev_max_body_lines` 절삭 확인

---

### Phase 2: 동적 컨텍스트 `!command` 지원 (중기)

**목표**: 스킬을 "정적 문서"에서 "동적 컨텍스트 생성기"로 진화
**예상 작업량**: ~60줄 추가, 2-3시간
**변경 파일**: `excerpt.py` (+ 선택적으로 새 파일 `skills/dynamic.py`)

#### 2-1. 동적 라인 해석 함수

`excerpt.py` 또는 새 파일 `skills/dynamic.py`에 추가:

```python
import subprocess
from pathlib import Path

_DYNAMIC_TIMEOUT = 10  # 초
_DYNAMIC_MAX_OUTPUT_LINES = 50


def resolve_dynamic_lines(
    lines: list[str],
    cwd: Path,
    *,
    enabled: bool = True,
) -> list[str]:
    """SKILL.md 본문의 !`command` 패턴을 셸 실행 결과로 치환한다."""
    if not enabled:
        return lines

    resolved: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("!`") and stripped.endswith("`"):
            cmd = stripped[2:-1].strip()
            if not cmd:
                resolved.append(line)
                continue
            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=_DYNAMIC_TIMEOUT,
                    cwd=str(cwd),
                )
                resolved.append(f"<!-- !`{cmd}` -->")
                output_lines = result.stdout.splitlines()[:_DYNAMIC_MAX_OUTPUT_LINES]
                resolved.extend(output_lines)
                if result.returncode != 0 and result.stderr.strip():
                    resolved.append(f"<!-- stderr: {result.stderr.strip()[:200]} -->")
            except subprocess.TimeoutExpired:
                resolved.append(f"<!-- !`{cmd}` timed out ({_DYNAMIC_TIMEOUT}s) -->")
            except Exception as e:
                resolved.append(f"<!-- !`{cmd}` failed: {e} -->")
        else:
            resolved.append(line)
    return resolved
```

#### 2-2. 통합 지점

`excerpt.py`의 `_excerpt_lines()`에서 호출:

```python
def _excerpt_lines(text: str, max_lines: int, *, cwd: Path = None) -> list[str]:
    lines = text.splitlines()
    body = _strip_frontmatter(lines)
    if cwd:
        body = resolve_dynamic_lines(body, cwd)
    return body[:max_lines]
```

`_format_skill_selection()`에서도 `include_body=True`일 때 동일하게 적용.

#### 2-3. 설정 추가

```python
"skills": {
    ...
    "dynamic_context_enabled": True,   # !`command` 실행 허용
    "dynamic_context_timeout": 10,     # 초
    "dynamic_context_max_lines": 50,   # 명령 출력 최대 라인
},
```

#### 2-4. 보안 고려사항

- `cwd`를 레포 루트로 제한 (임의 경로 실행 방지)
- `timeout` 필수 (무한 대기 방지)
- 선택적 명령 화이트리스트: `dynamic_context_allowed_commands: ["git", "dotnet", "npm"]`
- `shell=True`의 injection 위험: SKILL.md는 프로젝트 소유자가 작성하므로 수용 가능 (AGENTS.md/CLAUDE.md와 동일 신뢰 수준)

#### Phase 2 검증 방법

1. `!`git log --oneline -5`` 포함 SKILL.md → 최근 커밋 5건 주입 확인
2. 존재하지 않는 명령 → 에러 코멘트로 대체 확인
3. 10초 초과 명령 → timeout 처리 확인
4. `dynamic_context_enabled: false` → 원문 그대로 출력 확인

---

### Phase 3: 명시적 호출 + `$ARGUMENTS` 치환 (장기)

**목표**: Dev가 런타임에 스킬을 매개변수화하여 호출
**예상 작업량**: ~150줄, 반나절
**변경 파일**: `parser.py`, `cycle.py`, `claudecode.py`

#### 3-1. `$ARGUMENTS` 치환 함수

`parser.py`에 추가:

```python
def resolve_arguments(body_lines: list[str], arguments: str) -> list[str]:
    """$ARGUMENTS, $ARGUMENTS[N], $N 패턴을 실제 인자로 치환한다."""
    if not arguments:
        return body_lines
    args_list = arguments.split()
    resolved: list[str] = []
    for line in body_lines:
        result = line.replace("$ARGUMENTS", arguments)
        for i, arg in enumerate(args_list):
            result = result.replace(f"$ARGUMENTS[{i}]", arg)
            result = result.replace(f"${i}", arg)
        resolved.append(result)
    return resolved
```

#### 3-2. Dev 프롬프트 내 명시적 호출 패턴

Dev task prompt에 스킬 호출 지시 추가:

```
Available skills (invoke by name if needed during implementation):
{skills_catalog}

To use a skill with arguments, reference: @skill-name arg1 arg2
```

런타임에 Dev가 `@database-patterns users transactions`를 출력하면,
러너가 이를 감지하여 해당 스킬의 본문을 `$ARGUMENTS = "users transactions"`로 치환 후 다음 턴에 주입.

#### 3-3. `disable-model-invocation` 활용

`build_skills_index()` 후 PM에 전달하는 요약에서 `disable_model_invocation=True`인 스킬 제외:

```python
def summarize_skills_index_capped(records, *, max_items, max_chars):
    # PM 자동선택 제외 스킬 필터링
    eligible = [r for r in records if not r.disable_model_invocation]
    # ... 기존 로직 ...
```

#### Phase 3 검증 방법

1. `$ARGUMENTS` 포함 SKILL.md + 인자 전달 → 치환 결과 확인
2. `disable-model-invocation: true` 스킬 → PM 요약에서 제외 확인
3. `user-invocable: false` 스킬 → Dev 카탈로그에서 제외 확인

---

## 4. 우선순위 매트릭스

| Phase | 변경 규모 | 효과 | ROI | 의존성 |
|-------|----------|------|-----|--------|
| **1-1** parser 확장 | ~50줄 | 중 (호환성 기반) | 높음 | 없음 |
| **1-2** Dev 본문 주입 | ~80줄 | **최고** | **최고** | Phase 1-1 |
| **2** 동적 컨텍스트 | ~60줄 | 높음 | 높음 | Phase 1-1 (선택) |
| **3** 명시적 호출 | ~150줄 | 중 | 중 | Phase 1 |

**권장 실행 순서**: Phase 1-1 → Phase 1-2 → Phase 2 → Phase 3
Phase 1-1과 Phase 2는 병렬 작업 가능.

---

## 5. 호환성 정리

### SKILL.md 크로스 플랫폼 호환 매트릭스

작성 예시:

```markdown
---
name: "Supabase RPC Patterns"
description: "RPC 기반 DB 쿼리 패턴 가이드"
tags: ["database", "supabase"]
context: fork
allowed-tools: Read, Grep
disable-model-invocation: false
argument-hint: "[table-name]"
---

## 기본 규칙
- 쓰기는 반드시 RPC 사용
- 읽기는 View 또는 RPC
- 클라이언트에 SERVICE_ROLE_KEY 금지

## 현재 RPC 목록
!`grep -r "create.*function" supabase/migrations/ --include="*.sql" -l`
```

| 필드 | AgentCLI (Phase 1 후) | Codex CLI | Claude Code SDK |
|------|----------------------|-----------|-----------------|
| `name` | O | O | O |
| `description` | O | O | O |
| `tags` | O | X (무시) | X (무시) |
| `context` | O (파싱, 향후 활용) | X (무시) | O |
| `allowed-tools` | O (파싱, 향후 활용) | X (무시) | O |
| `disable-model-invocation` | O | X (무시) | O |
| `argument-hint` | O | X (무시) | O |
| `!command` (Phase 2 후) | O | X (원문 유지) | O |
| `$ARGUMENTS` (Phase 3 후) | O | X (원문 유지) | O |

**핵심**: 각 시스템이 모르는 필드를 무시하므로, 하나의 SKILL.md로 3개 시스템 동시 사용 가능.

---

## 6. 리팩터링 참고: `_format_skill_selection` 중복 제거

현재 `_format_skill_selection()`과 `_inline_skills_for()`가 `cycle.py`와 `claudecode.py`에 **완전히 중복**되어 있다.

Phase 1 작업 시 이 함수들을 `skills/` 모듈로 이동하는 것을 권장:

```
skills/
├── __init__.py
├── parser.py
├── indexer.py
├── excerpt.py
├── match.py
├── summary.py
└── formatter.py     # ← _format_skill_selection, _inline_skills_for 이동
```

`cycle.py`와 `claudecode.py`는 `from ..skills.formatter import ...`로 임포트.

---

## 7. 참고 자료

- [OpenAI Codex Agent Skills](https://developers.openai.com/codex/skills/)
- [OpenAI Codex - Create Skills](https://developers.openai.com/codex/skills/create-skill/)
- [Claude Code - Extend Claude with Skills](https://code.claude.com/docs/en/skills)
- [Claude Agent SDK - Agent Skills](https://platform.claude.com/docs/en/agent-sdk/skills)
- [Agent Skills Open Standard](https://agentskills.io)
- [Claude Skills vs MCP: Technical Comparison](https://intuitionlabs.ai/articles/claude-skills-vs-mcp)
- [Progressive Disclosure for AI Coding Tools](https://alexop.dev/posts/stop-bloating-your-claude-md-progressive-disclosure-ai-coding-tools/)
