from __future__ import annotations

import json
import re
from typing import Any, Optional, Type, TypeVar

from .utils import eprint

T = TypeVar("T")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", re.IGNORECASE)


def extract_json_object(text: str) -> Optional[str]:
    """Best-effort extraction of a JSON object from free-form text."""
    s = (text or "").strip()
    if not s:
        return None

    m = _JSON_FENCE_RE.search(s)
    if m:
        return (m.group(1) or "").strip()

    start = s.find("{")
    end = s.rfind("}")
    if start != -1 and end != -1 and end > start:
        return s[start : end + 1]

    # sometimes the whole output is already json
    if s.startswith("{") and s.endswith("}"):
        return s

    return None


def _loose_json_repairs(raw: str) -> str:
    """Small, safe repairs for common JSON drift.

    We keep this conservative to avoid mangling valid JSON.
    """

    s = raw.strip()
    # smart quotes
    s = s.replace("\u201c", '"').replace("\u201d", '"')
    s = s.replace("\u2018", "'").replace("\u2019", "'")

    # remove trailing commas (very common)
    s = re.sub(r",\s*([}\]])", r"\1", s)

    return s


def loads_json_object(text: str) -> Optional[Any]:
    """Parse JSON object from text, with minimal repair."""

    raw = extract_json_object(text) or (text or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except Exception:
        pass

    # one repair attempt
    try:
        return json.loads(_loose_json_repairs(raw))
    except Exception:
        return None


def parse_as_model(text: str, model_cls: Type[T]) -> Optional[T]:
    """Parse text as JSON and validate via pydantic model."""

    data = loads_json_object(text)
    if data is None:
        return None
    try:
        # pydantic v2
        return model_cls.model_validate(data)  # type: ignore[attr-defined]
    except Exception:
        return None


def dump_pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def describe_parse_failure(label: str, text: str, max_preview: int = 1200) -> None:
    prev = (text or "")[:max_preview]
    eprint(f"[{label}] Failed to parse/validate JSON. Preview:\n{prev}\n")
