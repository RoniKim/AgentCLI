from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatch
from typing import Any

_RAW_LOG_NAMES = {
    "build.txt",
    "dev_output.txt",
    "error.log",
    "metrics.jsonl",
    "run.log",
    "test.txt",
    "validation.txt",
}
_RAW_DIFF_SUFFIXES = {".diff", ".patch"}
_RAW_DIFF_NAMES = {"patch.diff"}
_PROMPT_NAMES = {"dev_task_prompt.md", "pm_bootstrap_prompt.md", "pm_incremental_prompt.md"}
_TRANSCRIPT_NAMES = {"telegram_runner_subprocess.log"}
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|ghp|xoxb)-[A-Za-z0-9._-]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._-]{12,}\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ExperienceSummaryConfig:
    max_items: int = 12
    max_chars: int = 4000
    lesson_max_chars: int = 240
    evidence_max_items: int = 3


@dataclass(frozen=True)
class ExperienceRenderContext:
    goal_refs: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    validation_gates: tuple[str, ...] = ()
    task_statuses: tuple[str, ...] = ()
    validation_statuses: tuple[str, ...] = ()

    @classmethod
    def from_values(
        cls,
        *,
        goal_refs: Iterable[str] = (),
        changed_files: Iterable[str] = (),
        validation_gates: Iterable[str] = (),
        task_statuses: Iterable[str] = (),
        validation_statuses: Iterable[str] = (),
    ) -> ExperienceRenderContext:
        return cls(
            goal_refs=_normalize_string_tuple(goal_refs),
            changed_files=_normalize_path_tuple(changed_files),
            validation_gates=_normalize_string_tuple(validation_gates),
            task_statuses=_normalize_string_tuple(task_statuses),
            validation_statuses=_normalize_string_tuple(validation_statuses),
        )


@dataclass(frozen=True)
class _PreparedLesson:
    score: float
    line: str


def render_experience_summary(
    lessons: Iterable[Mapping[str, Any]],
    *,
    config: ExperienceSummaryConfig | None = None,
    context: ExperienceRenderContext | None = None,
) -> str:
    cfg = config or ExperienceSummaryConfig()
    if cfg.max_items <= 0 or cfg.max_chars <= 0:
        return ""

    render_context = context or ExperienceRenderContext()
    prepared = _prepare_lessons(lessons, config=cfg, context=render_context)
    if not prepared:
        return ""

    selected: list[str] = []
    for lesson in prepared:
        if len(selected) >= cfg.max_items:
            break
        candidate = [*selected, lesson.line]
        omitted = max(0, len(prepared) - len(candidate))
        block = _format_summary_block(candidate, omitted=omitted, config=cfg)
        if len(block) <= cfg.max_chars:
            selected = candidate
        else:
            break

    omitted = max(0, len(prepared) - len(selected))
    block = _format_summary_block(selected, omitted=omitted, config=cfg)
    while selected and len(block) > cfg.max_chars:
        selected.pop()
        omitted = max(0, len(prepared) - len(selected))
        block = _format_summary_block(selected, omitted=omitted, config=cfg)

    return block if len(block) <= cfg.max_chars else ""


def _prepare_lessons(
    lessons: Iterable[Mapping[str, Any]],
    *,
    config: ExperienceSummaryConfig,
    context: ExperienceRenderContext,
) -> list[_PreparedLesson]:
    prepared: list[_PreparedLesson] = []
    for lesson in lessons:
        summary = _sanitize_summary(_first_text(lesson, "summary", "lesson", "text"), max_chars=config.lesson_max_chars)
        if not summary:
            continue
        evidence = _normalize_evidence_list(lesson, max_items=config.evidence_max_items)
        evidence_count = len(evidence) or _coerce_int(lesson.get("evidence_count")) or _coerce_len(lesson.get("evidence"))
        line = _render_lesson_line(
            kind=_first_text(lesson, "kind", default="general") or "general",
            summary=summary,
            confidence=_coerce_float(lesson.get("confidence")),
            evidence=evidence,
            evidence_count=evidence_count,
        )
        prepared.append(
            _PreparedLesson(
                score=_lesson_score(lesson, context=context, evidence_count=evidence_count),
                line=line,
            )
        )
    prepared.sort(key=lambda item: item.score, reverse=True)
    return prepared


def _format_summary_block(lines: list[str], *, omitted: int, config: ExperienceSummaryConfig) -> str:
    attrs = (
        f'version="1" items="{len(lines)}" omitted="{max(0, omitted)}" '
        f'max_items="{config.max_items}" max_chars="{config.max_chars}" authority="advisory"'
    )
    if lines:
        body = "\n".join(lines)
        return f"<pm_experience_summary {attrs}>\n{body}\n</pm_experience_summary>"
    return f"<pm_experience_summary {attrs}>\n</pm_experience_summary>"


def _render_lesson_line(
    *,
    kind: str,
    summary: str,
    confidence: float | None,
    evidence: list[str],
    evidence_count: int,
) -> str:
    parts = [kind.lower()]
    if confidence is not None:
        parts.append(_confidence_band(confidence))
        parts.append(f"conf={confidence:.2f}")
    if evidence_count > 0:
        parts.append(f"evidence={evidence_count}")
    line = f"- [{' '.join(parts)}] {summary}"
    if evidence:
        line += " | evidence: " + "; ".join(evidence)
    return line


def _lesson_score(
    lesson: Mapping[str, Any],
    *,
    context: ExperienceRenderContext,
    evidence_count: int,
) -> float:
    explicit = _coerce_float(lesson.get("relevance_score"))
    if explicit is None:
        explicit = _coerce_float(lesson.get("score"))
    if explicit is not None:
        return explicit

    score = 0.0
    goal_refs = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_goal_refs"))))
    file_globs = _normalize_path_tuple(_coerce_iterable(lesson.get("applies_to_file_globs")))
    gates = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_gates"))))
    statuses = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_statuses"))))
    validation_statuses = set(_normalize_string_tuple(_coerce_iterable(lesson.get("applies_to_validation_statuses"))))

    if goal_refs and goal_refs.intersection(context.goal_refs):
        score += 5.0
    if file_globs and any(fnmatch(changed_file, pattern) for pattern in file_globs for changed_file in context.changed_files):
        score += 4.0
    if gates and gates.intersection(context.validation_gates):
        score += 3.0
    if validation_statuses and validation_statuses.intersection(context.validation_statuses):
        score += 3.0
    if statuses and statuses.intersection(context.task_statuses):
        score += 2.0

    confidence = _coerce_float(lesson.get("confidence"))
    if confidence is not None:
        score += max(0.0, min(confidence, 1.0)) * 2.0

    if evidence_count > 0:
        score += math.log1p(evidence_count)
    return score


def _normalize_evidence_list(lesson: Mapping[str, Any], *, max_items: int) -> list[str]:
    if max_items <= 0:
        return []

    evidence = lesson.get("evidence_pointers", lesson.get("evidence"))
    if evidence is None:
        return []

    pointers: list[str] = []
    for item in _coerce_iterable(evidence):
        pointer = _normalize_evidence_pointer(item)
        if not pointer or pointer in pointers:
            continue
        pointers.append(pointer)
        if len(pointers) >= max_items:
            break
    return pointers


def _normalize_evidence_pointer(value: Any) -> str:
    if isinstance(value, Mapping):
        text = _first_value_text(value, "path", "pointer", "artifact_path", "artifactPath", "file", "label")
        kind_hint = _first_value_text(value, "kind", "type", "label")
    else:
        text = str(value or "").strip()
        kind_hint = ""
    if not text:
        return ""

    normalized = text.replace("\\", "/").strip().strip("`")
    line_suffix = ""
    match = re.match(r"^(.*?)(:\d+)?$", normalized)
    if match:
        normalized = match.group(1) or normalized
        line_suffix = match.group(2) or ""

    segments = [segment for segment in normalized.split("/") if segment not in {"", "."}]
    if not segments:
        return ""

    prefix = "repo"
    if "agent_runs" in segments:
        start = segments.index("agent_runs")
        segments = segments[start + 1 :]
        prefix = "run"
    elif re.match(r"^[A-Za-z]:$", segments[0]) or normalized.startswith("/"):
        prefix = "path"
        segments = segments[-4:]

    if not segments:
        return ""

    artifact_token = _artifact_placeholder(segments[-1], kind_hint)
    if artifact_token:
        segments[-1] = artifact_token
    elif prefix == "path" and len(segments) > 3:
        segments = ["..."] + segments[-3:]

    pointer = f"{prefix}:{'/'.join(segments)}{line_suffix}"
    return _truncate_middle(pointer, 96)


def _artifact_placeholder(file_name: str, kind_hint: str) -> str:
    name = file_name.strip().lower()
    kind = (kind_hint or "").strip().lower()
    if name in _RAW_LOG_NAMES or "log" in kind:
        return "[log]"
    if any(name.endswith(suffix) for suffix in _RAW_DIFF_SUFFIXES) or name in _RAW_DIFF_NAMES or "diff" in kind or "patch" in kind:
        return "[diff]"
    if name in _PROMPT_NAMES or "prompt" in kind:
        return "[prompt]"
    if name in _TRANSCRIPT_NAMES or "transcript" in kind:
        return "[transcript]"
    return ""


def _sanitize_summary(text: str, *, max_chars: int) -> str:
    raw = str(text or "").strip()
    if not raw or _is_excluded_summary(raw):
        return ""
    sanitized = _redact_secret_tokens(raw)
    sanitized = re.sub(r"\s+", " ", sanitized).strip(" -")
    if not sanitized:
        return ""
    return _truncate_text(sanitized, max_chars)


def _is_excluded_summary(text: str) -> bool:
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    lowered = text.lower()

    if "```" in text:
        return True
    if "diff --git " in lowered or "\n@@ " in text or "\n+++ " in text or "\n--- " in text:
        return True
    if "traceback (most recent call last)" in lowered:
        return True
    if "ignore previous instructions" in lowered:
        return True
    if lowered.startswith("you are ") and (
        "implementation instructions:" in lowered
        or "when editing files" in lowered
        or "selected skills" in lowered
    ):
        return True
    if len(lines) >= 8:
        return True

    transcript_markers = sum(1 for line in lines if re.match(r"^(assistant|user|system|tool):", line.strip(), re.IGNORECASE))
    if transcript_markers >= 2:
        return True

    shell_like_lines = sum(
        1
        for line in lines
        if re.match(r"^(PS>|>>>|\$|INFO\b|WARN\b|ERROR\b|\d{4}-\d{2}-\d{2}[ T])", line.strip(), re.IGNORECASE)
    )
    if len(lines) >= 4 and shell_like_lines >= max(2, len(lines) // 2):
        return True

    return False


def _redact_secret_tokens(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[redacted]", redacted)
    return redacted


def _truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."


def _truncate_middle(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 5:
        return text[:max_chars]
    keep = (max_chars - 3) // 2
    tail = max_chars - 3 - keep
    return text[:keep] + "..." + text[-tail:]


def _confidence_band(confidence: float) -> str:
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.55:
        return "medium"
    return "low"


def _first_text(source: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return default


def _first_value_text(source: Mapping[str, Any], *keys: str) -> str:
    return _first_text(source, *keys, default="")


def _coerce_iterable(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, (str, bytes)):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(value)
    return (value,)


def _normalize_string_tuple(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if text:
            normalized.append(text)
    return tuple(normalized)


def _normalize_path_tuple(values: Iterable[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        text = str(value or "").strip().replace("\\", "/")
        if not text:
            continue
        normalized.append(text.lstrip("./").lower())
    return tuple(normalized)


def _coerce_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _coerce_len(value: Any) -> int:
    items = _coerce_iterable(value)
    return len(items)
