from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import AGENT_WORK_DIR

DEFAULT_EXPERIENCE_PROMPT_MAX_ITEMS = 12
DEFAULT_EXPERIENCE_PROMPT_MAX_CHARS = 4000
DEFAULT_EXPERIENCE_LESSON_MAX_CHARS = 240
DEFAULT_EXPERIENCE_EVIDENCE_MAX_ITEMS = 3

_RAW_ARTIFACT_TOKENS = (
    "metrics.jsonl",
    "run.log",
    "error.log",
    "test.txt",
    "diff --git",
    "@@ ",
    "+++ ",
    "--- ",
    "ignore previous instructions",
    "system prompt",
    "assistant:",
    "user:",
    "<pm_output_contract>",
)
_MARKDOWN_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")


@dataclass(frozen=True)
class ExperiencePromptConfig:
    enabled: bool = True
    max_items: int = DEFAULT_EXPERIENCE_PROMPT_MAX_ITEMS
    max_chars: int = DEFAULT_EXPERIENCE_PROMPT_MAX_CHARS
    lesson_max_chars: int = DEFAULT_EXPERIENCE_LESSON_MAX_CHARS
    evidence_max_items: int = DEFAULT_EXPERIENCE_EVIDENCE_MAX_ITEMS
    redact_paths: bool = True


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _coerce_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(value))
    except Exception:
        return max(minimum, int(default))


def experience_prompt_config_from_args(args: Any) -> ExperiencePromptConfig:
    raw_cfg = getattr(args, "experience", {})
    cfg = raw_cfg if isinstance(raw_cfg, dict) else {}

    enabled = _coerce_bool(
        cfg.get("pm_use_experience_summary", getattr(args, "pm_use_experience_summary", None)),
        True,
    )
    db_enabled = _coerce_bool(
        cfg.get("experience_db_enabled", getattr(args, "experience_db_enabled", None)),
        True,
    )
    return ExperiencePromptConfig(
        enabled=enabled and db_enabled,
        max_items=_coerce_int(
            cfg.get("experience_prompt_max_items", getattr(args, "experience_prompt_max_items", None)),
            DEFAULT_EXPERIENCE_PROMPT_MAX_ITEMS,
            minimum=0,
        ),
        max_chars=_coerce_int(
            cfg.get("experience_prompt_max_chars", getattr(args, "experience_prompt_max_chars", None)),
            DEFAULT_EXPERIENCE_PROMPT_MAX_CHARS,
            minimum=0,
        ),
        lesson_max_chars=_coerce_int(
            cfg.get("experience_lesson_max_chars", getattr(args, "experience_lesson_max_chars", None)),
            DEFAULT_EXPERIENCE_LESSON_MAX_CHARS,
            minimum=0,
        ),
        evidence_max_items=_coerce_int(
            cfg.get("experience_evidence_max_items", getattr(args, "experience_evidence_max_items", None)),
            DEFAULT_EXPERIENCE_EVIDENCE_MAX_ITEMS,
            minimum=0,
        ),
        redact_paths=_coerce_bool(
            cfg.get("experience_redact_paths", getattr(args, "experience_redact_paths", None)),
            True,
        ),
    )


def _read_json_if_exists(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return {}
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_markdown_bullets(path: Path) -> list[str]:
    try:
        if not path.exists() or not path.is_file():
            return []
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    bullets: list[str] = []
    for line in raw.splitlines():
        match = _MARKDOWN_BULLET_RE.match(line)
        if match:
            bullets.append(match.group("text"))
    return bullets


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _looks_like_raw_artifact(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    if "```" in text:
        return True
    if any(token in lowered for token in _RAW_ARTIFACT_TOKENS):
        return True
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("diff --git", "@@", "+++", "---", "index ", "$ ", "> ")):
            return True
    return False


def _sanitize_lesson_text(text: Any, *, max_chars: int) -> str:
    normalized = _normalize_whitespace(str(text or ""))
    if not normalized or _looks_like_raw_artifact(normalized):
        return ""
    if max_chars > 0 and len(normalized) > max_chars:
        normalized = normalized[: max_chars - 3].rstrip() + "..."
    return normalized


def _coerce_evidence_count(value: Any) -> int:
    if isinstance(value, list):
        return len([item for item in value if str(item).strip()])
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _coerce_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except Exception:
        return None
    return confidence if confidence >= 0 else None


def _build_candidate(
    raw: Any,
    *,
    kind_hint: str,
    lesson_max_chars: int,
    source_priority: int,
    ordinal: int,
) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        lesson_text = (
            raw.get("lesson")
            or raw.get("text")
            or raw.get("summary")
            or raw.get("hint")
            or raw.get("title")
            or raw.get("message")
            or ""
        )
        kind = _normalize_whitespace(str(raw.get("kind") or kind_hint or "hint")).lower() or "hint"
        severity = _normalize_whitespace(str(raw.get("severity") or "")).lower()
        confidence = _coerce_confidence(raw.get("confidence"))
        evidence_count = _coerce_evidence_count(raw.get("evidence_count"))
        if evidence_count <= 0:
            evidence_count = _coerce_evidence_count(raw.get("evidence"))
        score = _coerce_confidence(raw.get("relevance_score"))
        if score is None:
            score = _coerce_confidence(raw.get("score"))
        if score is None:
            score = confidence if confidence is not None else 0.0
    else:
        lesson_text = raw
        kind = _normalize_whitespace(kind_hint or "hint").lower() or "hint"
        severity = ""
        confidence = None
        evidence_count = 0
        score = 0.0

    sanitized_text = _sanitize_lesson_text(lesson_text, max_chars=lesson_max_chars)
    if not sanitized_text:
        return None

    return {
        "kind": kind,
        "severity": severity,
        "confidence": confidence,
        "evidence_count": evidence_count,
        "score": score if score is not None else 0.0,
        "text": sanitized_text,
        "source_priority": source_priority,
        "ordinal": ordinal,
    }


def _collect_candidates_from_payload(
    payload: dict[str, Any],
    *,
    lesson_max_chars: int,
    source_priority: int,
    ordinal_start: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ordinal = ordinal_start

    list_sections = (
        ("task_lessons", "task_sizing"),
        ("validation_lessons", "validation"),
        ("lessons", "lesson"),
        ("items", "lesson"),
        ("pm_hints", "pm_hint"),
        ("merge_hints", "merge_hint"),
        ("operator_actions", "operator_action"),
    )
    for key, kind_hint in list_sections:
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            candidate = _build_candidate(
                raw_item,
                kind_hint=kind_hint,
                lesson_max_chars=lesson_max_chars,
                source_priority=source_priority,
                ordinal=ordinal,
            )
            ordinal += 1
            if candidate is not None:
                candidates.append(candidate)

    if candidates:
        return candidates

    summary_text = payload.get("summary")
    candidate = _build_candidate(
        summary_text,
        kind_hint="summary",
        lesson_max_chars=lesson_max_chars,
        source_priority=source_priority,
        ordinal=ordinal,
    )
    if candidate is not None:
        candidates.append(candidate)
    return candidates


def _collect_candidates_from_markdown(
    items: list[str],
    *,
    lesson_max_chars: int,
    source_priority: int,
    ordinal_start: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    ordinal = ordinal_start
    for item in items:
        candidate = _build_candidate(
            item,
            kind_hint="summary",
            lesson_max_chars=lesson_max_chars,
            source_priority=source_priority,
            ordinal=ordinal,
        )
        ordinal += 1
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _normalize_whitespace(candidate.get("text", "")).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _format_candidate_line(candidate: dict[str, Any], *, evidence_max_items: int) -> str:
    meta: list[str] = []
    kind = _normalize_whitespace(str(candidate.get("kind") or "")).lower()
    severity = _normalize_whitespace(str(candidate.get("severity") or "")).lower()
    confidence = candidate.get("confidence")
    evidence_count = int(candidate.get("evidence_count") or 0)

    if kind:
        meta.append(kind)
    if severity:
        meta.append(severity)
    if confidence is not None:
        meta.append(f"conf={confidence:.2f}")
    if evidence_count > 0:
        bounded_count = evidence_count if evidence_max_items <= 0 else min(evidence_count, evidence_max_items)
        meta.append(f"evidence={bounded_count}")

    text = str(candidate.get("text") or "").strip()
    if meta:
        return f"- [{' '.join(meta)}] {text}"
    return f"- {text}"


def _compose_block(lines: list[str], *, cfg: ExperiencePromptConfig) -> str:
    opening = (
        f'<pm_experience_summary version="1" items="{len(lines)}" '
        f'max_items="{cfg.max_items}" max_chars="{cfg.max_chars}" authority="advisory">'
    )
    return "\n".join([opening, *lines, "</pm_experience_summary>"])


def load_pm_experience_summary(repo: Path, run_dir: Path, *, args: Any | None = None) -> str:
    cfg = experience_prompt_config_from_args(args)
    if not cfg.enabled or cfg.max_items <= 0 or cfg.max_chars <= 0 or cfg.lesson_max_chars <= 0:
        return ""

    experience_root = repo / AGENT_WORK_DIR / "experience"
    sources: list[tuple[int, dict[str, Any], list[str]]] = [
        (
            2,
            _read_json_if_exists(run_dir / "ANALYZER_SUMMARY.json"),
            [],
        ),
        (
            1,
            _read_json_if_exists(experience_root / "latest_summary.json"),
            _read_markdown_bullets(experience_root / "latest_summary.md"),
        ),
    ]

    candidates: list[dict[str, Any]] = []
    ordinal = 0
    for source_priority, payload, markdown_items in sources:
        if payload:
            found = _collect_candidates_from_payload(
                payload,
                lesson_max_chars=cfg.lesson_max_chars,
                source_priority=source_priority,
                ordinal_start=ordinal,
            )
            if found:
                ordinal += len(found)
                candidates.extend(found)
                continue
        if markdown_items:
            found = _collect_candidates_from_markdown(
                markdown_items,
                lesson_max_chars=cfg.lesson_max_chars,
                source_priority=source_priority,
                ordinal_start=ordinal,
            )
            ordinal += len(found)
            candidates.extend(found)

    candidates = _dedupe_candidates(candidates)
    if not candidates:
        return ""

    ranked = sorted(
        candidates,
        key=lambda item: (
            -float(item.get("score") or 0.0),
            -int(item.get("source_priority") or 0),
            int(item.get("ordinal") or 0),
        ),
    )

    rendered_lines: list[str] = []
    for candidate in ranked:
        if cfg.max_items > 0 and len(rendered_lines) >= cfg.max_items:
            break
        line = _format_candidate_line(candidate, evidence_max_items=cfg.evidence_max_items)
        tentative = rendered_lines + [line]
        block = _compose_block(tentative, cfg=cfg)
        if len(block) > cfg.max_chars:
            continue
        rendered_lines.append(line)

    if not rendered_lines:
        return ""
    return _compose_block(rendered_lines, cfg=cfg)
