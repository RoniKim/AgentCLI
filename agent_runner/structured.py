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

# --- PM output normalization (robust against prompt drift) ---

def _as_str_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out: list[str] = []
        for x in v:
            if isinstance(x, str):
                s = x.strip()
                if s:
                    out.append(s)
            else:
                s = str(x).strip()
                if s:
                    out.append(s)
        return out
    if isinstance(v, str):
        s = v.strip()
        return [s] if s else []
    return [str(v).strip()] if str(v).strip() else []


def normalize_pm_output_dict(data: Any, *, kind_hint: str = "") -> Optional[dict[str, Any]]:
    """Normalize common PM JSON variants into PMOutputV2-compatible dict."""
    if not isinstance(data, dict):
        return None

    out: dict[str, Any] = {}

    kind = str(data.get("kind", "")).strip()
    if kind not in ("bootstrap", "incremental", "refresh", "skip"):
        # common drift values
        if kind.lower() in ("pm_run_result", "pm_result", "pm", "result"):
            kind = kind_hint or "bootstrap"
        elif kind.lower() in ("initial", "first", "boot", "bootstrap_mode"):
            kind = "bootstrap"
        elif kind.lower() in ("delta", "incremental_mode", "inc"):
            kind = "incremental"
        else:
            kind = kind_hint or "bootstrap"
    out["kind"] = kind

    summary = data.get("summary") or data.get("message") or data.get("overview") or ""
    out["summary"] = str(summary).strip() or "PM run completed."

    # tasks
    raw_tasks = data.get("tasks") or data.get("backlog") or data.get("items") or []
    tasks: list[dict[str, Any]] = []
    if isinstance(raw_tasks, list):
        auto_i = 1
        for t in raw_tasks:
            if not isinstance(t, dict):
                continue
            tid = str(t.get("id") or t.get("task_id") or t.get("key") or "").strip()
            if not tid:
                tid = f"T{auto_i:02d}"
            auto_i += 1
            title = str(t.get("title") or t.get("name") or "").strip()
            prompt = str(t.get("prompt") or t.get("description") or t.get("details") or "").strip()
            files = t.get("files")
            if files is None:
                files = t.get("files_changed") or t.get("files_touched") or t.get("files_changed_or_created") or []
            done_when = str(
                t.get("done_when")
                or t.get("definition_of_done")
                or t.get("acceptance_criteria")
                or t.get("dod")
                or ""
            ).strip()
            if not done_when:
                done_when = "Git diff exists and build/tests gates pass; task acceptance criteria in prompt satisfied."
            tasks.append(
                {
                    "id": tid,
                    "title": title or tid,
                    "prompt": prompt or f"Implement: {title or tid}",
                    "files": _as_str_list(files),
                    "done_when": done_when,
                }
            )
    out["tasks"] = tasks

    # optional fields
    notes_md = data.get("notes_md") if "notes_md" in data else (data.get("notes") or data.get("notes_markdown"))
    out["notes_md"] = None if notes_md is None else str(notes_md)

    out["warnings"] = _as_str_list(data.get("warnings") or data.get("risks") or [])
    out["open_questions"] = _as_str_list(data.get("open_questions") or data.get("questions") or [])

    analysis_updated = data.get("analysis_updated")
    if isinstance(analysis_updated, bool):
        out["analysis_updated"] = analysis_updated
    else:
        out["analysis_updated"] = bool(data.get("analysis_path") or data.get("analysis_md") or data.get("analysis_file"))

    analysis_path = data.get("analysis_path") or data.get("analysis_md") or data.get("analysis_file")
    out["analysis_path"] = None if not analysis_path else str(analysis_path)

    return out


def parse_pm_output(text: str, *, kind_hint: str = "") -> Optional["PMOutputV2"]:
    """Parse and validate PM final output robustly.

    Tries strict PMOutputV2 first, then attempts normalization of common variants.
    """
    from .schemas import PMOutputV2  # local import to avoid import cycles

    data = loads_json_object(text)
    if data is None:
        return None
    try:
        return PMOutputV2.model_validate(data)  # type: ignore[attr-defined]
    except Exception:
        pass

    norm = normalize_pm_output_dict(data, kind_hint=kind_hint)
    if norm is None:
        return None
    try:
        return PMOutputV2.model_validate(norm)  # type: ignore[attr-defined]
    except Exception:
        return None
