from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import (
    latest_experience_summary_path,
    resolve_experience_redaction_settings,
)

REDACTED_EXPERIENCE_VALUE = "[redacted]"
OMITTED_TEST_OUTPUT_VALUE = "[test output omitted]"
OMITTED_TRANSCRIPT_VALUE = "[backend transcript omitted]"
OMITTED_PROMPT_VALUE = "[raw prompt omitted]"
OMITTED_INJECTION_VALUE = "[prompt-injection content omitted]"

_SECRET_TOKEN_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bsk_[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(token|secret|password|api[_-]?key|service_role_key|cron_secret)\b\s*[:=]\s*(['\"]?)([^\s,'\";]+)\2"
)
_WINDOWS_ABSOLUTE_PATH_PATTERN = re.compile(r"\b[A-Za-z]:\\(?:[^\\\r\n]+\\)*[^\\\r\n]*")
_UNIX_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])/(?:Users|home|var|tmp|opt|srv|etc|mnt|private|Volumes)(?:/[^\s`'\"]+)+"
)
_PROMPT_SECTION_MARKERS = (
    "task:",
    "implementation instructions:",
    "files to touch",
    "selected skills",
    "constraints (non-negotiable):",
    "definition of done:",
    "repo root:",
    "docs read mode:",
    "digest file (preferred):",
    "important (analysis update safety):",
    "when editing files, call codex mcp",
)
_TRANSCRIPT_MARKERS = (
    "assistant:",
    "user:",
    "system:",
    "developer:",
    "tool:",
    "backend transcript",
    "[agentcli_continuation_marker]",
)
_INJECTION_MARKERS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard previous instructions",
    "developer message",
    "system prompt",
    "follow these instructions instead",
    "override your instructions",
    "jailbreak",
)
_LONG_OUTPUT_MARKERS = (
    "test output",
    "build output",
    "stdout",
    "stderr",
    "traceback",
    "stack trace",
)


@dataclass(frozen=True)
class ExperienceRedactionSettings:
    enabled: bool
    prompt_max_items: int
    prompt_max_chars: int
    lesson_max_chars: int
    evidence_max_items: int
    raw_log_excerpt_chars: int
    redact_paths: bool
    redact_secrets: bool
    redact_backend_transcripts: bool
    redact_prompt_text: bool
    redact_prompt_injection: bool
    redact_test_output: bool

    @classmethod
    def from_source(cls, source: Any = None) -> "ExperienceRedactionSettings":
        cfg = resolve_experience_redaction_settings(source)
        return cls(
            enabled=bool(cfg.get("pm_use_experience_summary", True)),
            prompt_max_items=max(0, int(cfg.get("experience_prompt_max_items", 12) or 0)),
            prompt_max_chars=max(0, int(cfg.get("experience_prompt_max_chars", 4000) or 0)),
            lesson_max_chars=max(0, int(cfg.get("experience_lesson_max_chars", 240) or 0)),
            evidence_max_items=max(0, int(cfg.get("experience_evidence_max_items", 3) or 0)),
            raw_log_excerpt_chars=max(0, int(cfg.get("experience_raw_log_excerpt_chars", 0) or 0)),
            redact_paths=bool(cfg.get("experience_redact_paths", True)),
            redact_secrets=bool(cfg.get("experience_redact_secrets", True)),
            redact_backend_transcripts=bool(cfg.get("experience_redact_backend_transcripts", True)),
            redact_prompt_text=bool(cfg.get("experience_redact_prompt_text", True)),
            redact_prompt_injection=bool(cfg.get("experience_redact_prompt_injection", True)),
            redact_test_output=bool(cfg.get("experience_redact_test_output", True)),
        )


def sanitize_experience_lesson(
    lesson: Any,
    *,
    settings: ExperienceRedactionSettings | None = None,
    repo_root: Path | None = None,
    run_dir: Path | None = None,
) -> str:
    redaction = settings or ExperienceRedactionSettings.from_source()
    text = str(lesson or "").strip()
    if not text:
        return ""

    text = _strip_markdown_fences(text)

    if redaction.redact_secrets:
        text = _redact_secret_text(text)
    if redaction.redact_paths:
        text = _redact_path_text(text)
    if redaction.redact_test_output:
        text = _omit_long_test_output(text)

    lines: list[str] = []
    prompt_omitted = False
    transcript_omitted = False
    injection_omitted = False

    for raw_line in text.splitlines():
        line = " ".join(raw_line.strip().split())
        if not line:
            continue
        lower = line.lower()

        if redaction.redact_prompt_injection and any(marker in lower for marker in _INJECTION_MARKERS):
            injection_omitted = True
            continue
        if redaction.redact_backend_transcripts and any(lower.startswith(marker) or marker in lower for marker in _TRANSCRIPT_MARKERS):
            transcript_omitted = True
            continue
        if redaction.redact_prompt_text and any(lower.startswith(marker) for marker in _PROMPT_SECTION_MARKERS):
            prompt_omitted = True
            continue
        if OMITTED_TEST_OUTPUT_VALUE in line:
            lines.append(line)
            continue
        if redaction.redact_test_output and _line_looks_like_output_dump(lower):
            transcript_omitted = True
            continue

        lines.append(line)

    if prompt_omitted:
        lines.append(OMITTED_PROMPT_VALUE)
    if transcript_omitted:
        lines.append(OMITTED_TRANSCRIPT_VALUE)
    if injection_omitted:
        lines.append(OMITTED_INJECTION_VALUE)

    collapsed = " ".join(lines).strip()
    collapsed = re.sub(r"\s+", " ", collapsed).strip()
    collapsed = _dedupe_omission_markers(collapsed)
    if redaction.lesson_max_chars and len(collapsed) > redaction.lesson_max_chars:
        collapsed = collapsed[: max(0, redaction.lesson_max_chars - 3)].rstrip() + "..."
    return collapsed


def sanitize_experience_evidence(
    evidence: Any,
    *,
    settings: ExperienceRedactionSettings | None = None,
    repo_root: Path | None = None,
    run_dir: Path | None = None,
) -> list[str]:
    redaction = settings or ExperienceRedactionSettings.from_source()
    items = evidence if isinstance(evidence, list) else [evidence] if evidence not in (None, "") else []
    pointers: list[str] = []
    seen: set[str] = set()

    for item in items:
        pointer = _sanitize_evidence_pointer(
            item,
            settings=redaction,
            repo_root=repo_root,
            run_dir=run_dir,
        )
        if not pointer or pointer in seen:
            continue
        seen.add(pointer)
        pointers.append(pointer)
        if redaction.evidence_max_items and len(pointers) >= redaction.evidence_max_items:
            break

    return pointers


def render_pm_experience_summary(
    lessons: Sequence[Mapping[str, Any] | str],
    *,
    settings: ExperienceRedactionSettings | None = None,
    repo_root: Path | None = None,
    run_dir: Path | None = None,
) -> str:
    redaction = settings or ExperienceRedactionSettings.from_source()
    if not redaction.enabled:
        return "(disabled)"

    lines: list[str] = []
    for item in lessons:
        lesson_text, meta_prefix, evidence = _normalize_lesson_item(
            item,
            settings=redaction,
            repo_root=repo_root,
            run_dir=run_dir,
        )
        if not lesson_text:
            continue
        line = f"- {meta_prefix}{lesson_text}" if meta_prefix else f"- {lesson_text}"
        if evidence:
            line += f" | evidence: {', '.join(evidence)}"
        lines.append(line)
        if redaction.prompt_max_items and len(lines) >= redaction.prompt_max_items:
            break

    if not lines:
        return "(none)"

    open_tag = (
        f'<pm_experience_summary version="1" items="{len(lines)}" '
        f'max_items="{redaction.prompt_max_items}" max_chars="{redaction.prompt_max_chars}" '
        'authority="advisory">'
    )
    close_tag = "</pm_experience_summary>"

    while lines:
        block = "\n".join([open_tag, *lines, close_tag])
        if not redaction.prompt_max_chars or len(block) <= redaction.prompt_max_chars:
            return block
        lines.pop()

    return "(none)"


def render_pm_experience_summary_from_run(
    run_dir: Path,
    *,
    settings: ExperienceRedactionSettings | None = None,
    repo_root: Path | None = None,
) -> str:
    redaction = settings or ExperienceRedactionSettings.from_source()
    repo = repo_root or _repo_root_from_run_dir(run_dir)
    lessons = load_experience_lessons(run_dir, repo_root=repo)
    return render_pm_experience_summary(
        lessons,
        settings=redaction,
        repo_root=repo,
        run_dir=run_dir,
    )


def load_experience_lessons(
    run_dir: Path,
    *,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    repo = repo_root or _repo_root_from_run_dir(run_dir)
    lessons: list[dict[str, Any]] = []

    for path in _experience_summary_candidates(run_dir, repo):
        payload = _load_json_file(path)
        if not isinstance(payload, dict):
            continue
        lessons.extend(_lessons_from_summary_payload(payload))
        if lessons:
            return lessons

    hints_dir = run_dir / "analysis_hints"
    if not hints_dir.exists() or not hints_dir.is_dir():
        return lessons

    hint_files = sorted(hints_dir.glob("*.md"), key=lambda item: item.stat().st_mtime)[-12:]
    for hint_path in hint_files:
        try:
            raw_text = hint_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        evidence = [f"artifact:{hint_path.relative_to(run_dir).as_posix()}"]
        for lesson_text in _lessons_from_hint_markdown(raw_text):
            lessons.append(
                {
                    "kind": "hint",
                    "lesson": lesson_text,
                    "evidence": evidence,
                }
            )
    return lessons


def _experience_summary_candidates(run_dir: Path, repo_root: Path | None) -> Iterable[Path]:
    yield run_dir / "ANALYZER_SUMMARY.json"
    if repo_root is not None:
        yield latest_experience_summary_path(repo_root)


def _load_json_file(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _lessons_from_summary_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    lessons: list[dict[str, Any]] = []
    for key in ("task_lessons", "validation_lessons", "lessons", "merge_hints", "pm_hints", "operator_actions"):
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            continue
        for raw in raw_items:
            if isinstance(raw, dict):
                text = raw.get("lesson") or raw.get("summary") or raw.get("text")
                if not text:
                    continue
                item = dict(raw)
                item.setdefault("kind", key.rstrip("s"))
                item["lesson"] = str(text)
                lessons.append(item)
            else:
                text = str(raw or "").strip()
                if not text:
                    continue
                lessons.append({"kind": key.rstrip("s"), "lesson": text, "evidence": []})
    return lessons


def _lessons_from_hint_markdown(text: str) -> list[str]:
    lessons: list[str] = []
    active_section = ""

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        lower = stripped.rstrip(":").lower()
        if lower.startswith("#"):
            continue
        if lower in {"what changed and why", "- what changed and why"}:
            active_section = "lessons"
            continue
        if lower in {"new gaps", "- new gaps"}:
            active_section = "gaps"
            continue
        if lower in {"changed files", "- changed files"}:
            active_section = ""
            continue
        if active_section in {"lessons", "gaps"}:
            if stripped.startswith("- "):
                lessons.append(stripped[2:].strip())
            elif lessons:
                lessons[-1] = f"{lessons[-1]} {stripped}".strip()

    if lessons:
        return lessons

    fallback: list[str] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.lower().startswith("changed files"):
            continue
        fallback.append(stripped.lstrip("- ").strip())
    return fallback[:6]


def _normalize_lesson_item(
    item: Mapping[str, Any] | str,
    *,
    settings: ExperienceRedactionSettings,
    repo_root: Path | None,
    run_dir: Path | None,
) -> tuple[str, str, list[str]]:
    if isinstance(item, Mapping):
        lesson_text = sanitize_experience_lesson(
            item.get("lesson") or item.get("summary") or item.get("text") or "",
            settings=settings,
            repo_root=repo_root,
            run_dir=run_dir,
        )
        meta_parts: list[str] = []
        kind = str(item.get("kind") or "").strip()
        severity = str(item.get("severity") or "").strip()
        confidence = item.get("confidence")
        if kind:
            meta_parts.append(kind)
        if severity:
            meta_parts.append(severity)
        if confidence not in (None, ""):
            try:
                meta_parts.append(f"conf={float(confidence):.2f}")
            except Exception:
                meta_parts.append(f"conf={str(confidence).strip()}")
        evidence = sanitize_experience_evidence(
            item.get("evidence") or item.get("evidence_pointers") or item.get("artifacts") or [],
            settings=settings,
            repo_root=repo_root,
            run_dir=run_dir,
        )
        if evidence:
            meta_parts.append(f"evidence={len(evidence)}")
        prefix = f"[{' '.join(meta_parts)}] " if meta_parts else ""
        return lesson_text, prefix, evidence

    lesson_text = sanitize_experience_lesson(
        item,
        settings=settings,
        repo_root=repo_root,
        run_dir=run_dir,
    )
    return lesson_text, "", []


def _sanitize_evidence_pointer(
    item: Any,
    *,
    settings: ExperienceRedactionSettings,
    repo_root: Path | None,
    run_dir: Path | None,
) -> str:
    if isinstance(item, Mapping):
        value = item.get("path") or item.get("artifact_path") or item.get("artifactPath") or item.get("pointer") or item.get("label")
    else:
        value = item
    text = str(value or "").strip()
    if not text:
        return ""

    if settings.redact_secrets:
        text = _redact_secret_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if settings.redact_paths:
        normalized = _normalize_pointer_path(text, repo_root=repo_root, run_dir=run_dir)
        return normalized
    if len(text) > 160:
        return text[:157].rstrip() + "..."
    return text


def _normalize_pointer_path(text: str, *, repo_root: Path | None, run_dir: Path | None) -> str:
    if re.match(r"^[a-z]+:", text, re.IGNORECASE) and not re.match(r"^[A-Za-z]:\\", text):
        return text if len(text) <= 160 else text[:157].rstrip() + "..."
    candidate = Path(text)
    roots = [root for root in (run_dir, repo_root) if root is not None]
    for root in roots:
        try:
            rel = candidate.resolve().relative_to(root.resolve()).as_posix()
            return f"artifact:{rel}"
        except Exception:
            continue
    if candidate.is_absolute():
        name = candidate.name.strip()
        return f"artifact:{name}" if name else REDACTED_EXPERIENCE_VALUE
    normalized = candidate.as_posix().strip()
    return f"artifact:{normalized}" if normalized else REDACTED_EXPERIENCE_VALUE


def _strip_markdown_fences(text: str) -> str:
    return re.sub(r"```(?:[\w+-]+)?", "", text)


def _redact_secret_text(text: str) -> str:
    redacted = text
    for pattern in _SECRET_TOKEN_PATTERNS:
        redacted = pattern.sub(REDACTED_EXPERIENCE_VALUE, redacted)
    redacted = _SECRET_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}={REDACTED_EXPERIENCE_VALUE}", redacted)
    return redacted


def _redact_path_text(text: str) -> str:
    redacted = _WINDOWS_ABSOLUTE_PATH_PATTERN.sub(REDACTED_EXPERIENCE_VALUE, text)
    redacted = _UNIX_ABSOLUTE_PATH_PATTERN.sub(REDACTED_EXPERIENCE_VALUE, redacted)
    return redacted


def _omit_long_test_output(text: str) -> str:
    lower = text.lower()
    for marker in _LONG_OUTPUT_MARKERS:
        index = lower.find(marker)
        if index >= 0 and len(text[index:]) > 180:
            prefix = text[:index].strip()
            return f"{prefix} {OMITTED_TEST_OUTPUT_VALUE}".strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    noisy_lines = sum(1 for line in lines if _line_looks_like_output_dump(line.lower()))
    if len(lines) >= 8 and noisy_lines >= 4:
        return OMITTED_TEST_OUTPUT_VALUE
    return text


def _line_looks_like_output_dump(lower: str) -> bool:
    if any(marker in lower for marker in _LONG_OUTPUT_MARKERS):
        return True
    if lower.startswith("at ") or lower.startswith("traceback") or lower.startswith("assertion failed"):
        return True
    if "expected:" in lower or "actual:" in lower:
        return True
    return False


def _dedupe_omission_markers(text: str) -> str:
    if not text:
        return text
    pieces: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"(?<=\])\s+", text):
        normalized = part.strip()
        if normalized in {
            OMITTED_PROMPT_VALUE,
            OMITTED_TRANSCRIPT_VALUE,
            OMITTED_INJECTION_VALUE,
        }:
            if normalized in seen:
                continue
            seen.add(normalized)
        if normalized:
            pieces.append(normalized)
    return " ".join(pieces)


def _repo_root_from_run_dir(run_dir: Path) -> Path:
    current = Path(run_dir).resolve()
    for parent in [current, *current.parents]:
        if parent.name == ".AgentCLI":
            return parent.parent
    try:
        return current.parents[2]
    except Exception:
        return current
