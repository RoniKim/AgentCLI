from __future__ import annotations

import json
import re
from typing import Any, Optional, Type, TypeVar

from .utils import eprint

T = TypeVar("T")

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\{\[][\s\S]*?[\}\]])\s*```", re.IGNORECASE)


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

    if start < 0 or end <= start:
        start = s.find('[')
        end = s.rfind(']')
        if 0 <= start < end:
            return s[start:end + 1]

    # sometimes the whole output is already json
    if s.startswith("{") and s.endswith("}"):
        return s

    return None


def extract_pm_json_object(text: str) -> Optional[str]:
    """PM-specific JSON extractor using balanced brace counting.

    Scans all top-level ``{...}`` blocks with balanced braces and returns
    the **last** one that contains both ``"kind"`` and ``"tasks"`` keys —
    the typical PM output pattern.  Falls back to ``extract_json_object``
    if no PM-shaped block is found.
    """
    s = (text or "").strip()
    if not s:
        return None

    # Try fenced code blocks first (same as extract_json_object)
    m = _JSON_FENCE_RE.search(s)
    if m:
        inner = (m.group(1) or "").strip()
        if '"kind"' in inner and '"tasks"' in inner:
            return inner

    # Balanced-brace scan: collect all top-level {...} blocks
    candidates: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == '{':
            depth = 1
            start = i
            i += 1
            in_string = False
            escape = False
            while i < n and depth > 0:
                ch = s[i]
                if escape:
                    escape = False
                elif ch == '\\' and in_string:
                    escape = True
                elif ch == '"' and not escape:
                    in_string = not in_string
                elif not in_string:
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                i += 1
            if depth == 0:
                candidates.append(s[start:i])
        else:
            i += 1

    # Prefer the last block that looks like PM output
    pm_block: Optional[str] = None
    for block in reversed(candidates):
        if '"kind"' in block and '"tasks"' in block:
            pm_block = block
            break

    if pm_block is not None:
        return pm_block

    # Any candidate that parses as JSON
    for block in reversed(candidates):
        try:
            json.loads(block)
            return block
        except (json.JSONDecodeError, ValueError):
            continue

    # Fall back to original extractor
    return extract_json_object(text)


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
    except (json.JSONDecodeError, ValueError):
        pass

    # one repair attempt
    try:
        return json.loads(_loose_json_repairs(raw))
    except (json.JSONDecodeError, ValueError):
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


def parse_qa_followups(text: str) -> tuple[Optional["QAFollowupsV1"], Optional[str]]:
    """Parse QA followups JSON into schema, returning (model, error)."""
    from .schemas import QAFollowupsV1

    data = loads_json_object(text)
    if data is None:
        return None, "qa_followups_json_missing"
    try:
        return QAFollowupsV1.model_validate(data), None  # type: ignore[attr-defined]
    except Exception as ex:
        return None, str(ex)

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
            skills = _as_str_list(t.get("skills") or t.get("skill_ids") or t.get("skills_used") or t.get("skill"))
            skills_rationale = (
                t.get("skills_rationale")
                or t.get("skill_rationale")
                or t.get("skills_reason")
                or t.get("skill_reason")
            )
            tasks.append(
                {
                    "id": tid,
                    "title": title or tid,
                    "prompt": prompt or f"Implement: {title or tid}",
                    "files": _as_str_list(files),
                    "done_when": done_when,
                    "skills": skills,
                    "skills_rationale": None if skills_rationale is None else str(skills_rationale),
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

    Tries PM-specific extractor first, then strict PMOutputV2, then normalization.
    """
    from .schemas import PMOutputV2  # local import to avoid import cycles

    # Try PM-specific balanced-brace extractor first
    pm_raw = extract_pm_json_object(text)
    data = None
    if pm_raw:
        try:
            data = json.loads(pm_raw)
        except (json.JSONDecodeError, ValueError):
            try:
                data = json.loads(_loose_json_repairs(pm_raw))
            except (json.JSONDecodeError, ValueError):
                pass
    if data is None:
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


def summarize_validation_errors(err: Exception, *, max_items: int = 6) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    type_errors: list[str] = []
    details = []
    if hasattr(err, "errors"):
        try:
            details = err.errors()  # type: ignore[assignment]
        except Exception:
            details = []
    for item in details or []:
        loc = item.get("loc")
        if isinstance(loc, (list, tuple)):
            loc_str = ".".join(str(x) for x in loc if x is not None)
        else:
            loc_str = str(loc or "")
        msg = str(item.get("msg") or "")
        typ = str(item.get("type") or "")
        if "missing" in typ or "field required" in msg:
            if loc_str:
                missing.append(loc_str)
        elif msg:
            type_errors.append(f"{loc_str}: {msg}" if loc_str else msg)
        if len(missing) + len(type_errors) >= max_items:
            break
    return missing[:max_items], type_errors[:max_items]


def parse_pm_output_with_errors(text: str, *, kind_hint: str = "") -> tuple[Optional["PMOutputV2"], list[str], list[str]]:
    """Parse PM output and return validation error summaries.

    Returns (model, missing_fields, type_errors).
    """
    from .schemas import PMOutputV2  # local import to avoid import cycles

    # Try PM-specific balanced-brace extractor first
    pm_raw = extract_pm_json_object(text)
    data = None
    if pm_raw:
        try:
            data = json.loads(pm_raw)
        except (json.JSONDecodeError, ValueError):
            try:
                data = json.loads(_loose_json_repairs(pm_raw))
            except (json.JSONDecodeError, ValueError):
                pass
    if data is None:
        data = loads_json_object(text)
    if data is None:
        return None, ["<json_parse_failed>"], []
    try:
        return PMOutputV2.model_validate(data), [], []  # type: ignore[attr-defined]
    except Exception:
        pass

    norm = normalize_pm_output_dict(data, kind_hint=kind_hint)
    if norm is None:
        return None, ["<normalize_failed>"], []
    try:
        return PMOutputV2.model_validate(norm), [], []  # type: ignore[attr-defined]
    except Exception as ex:
        missing, type_errors = summarize_validation_errors(ex)
        return None, missing, type_errors
