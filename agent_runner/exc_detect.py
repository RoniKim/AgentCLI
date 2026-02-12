"""Exception detection helpers shared by Codex and Claude Code backends.

These pure functions inspect exception chains to classify errors into
actionable categories (quota, transient, max-turns, invalid model).
"""
from __future__ import annotations

from typing import Generator

from .utils import has_quota_text


def iter_exc_chain(ex: BaseException, max_depth: int = 6) -> Generator[BaseException, None, None]:
    """Yield exception + its causes/contexts (best-effort)."""
    cur: BaseException | None = ex
    seen: set[int] = set()
    for _ in range(max_depth):
        if cur is None or id(cur) in seen:
            break
        seen.add(id(cur))
        yield cur
        nxt = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        if nxt is None or not isinstance(nxt, BaseException):
            break
        cur = nxt


def is_max_turns_exception(ex: BaseException) -> bool:
    for e in iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        name = type(e).__name__.lower()
        rep = (repr(e) or "").lower()
        if (
            "max turns" in msg
            or "max_turn" in msg
            or "maxturn" in msg
            or "maxturn" in name
            or "max_turn" in name
            or ("turn" in name and "max" in name)
            or "maxturnsexceeded" in rep
        ):
            return True
    return False


def is_quota_exception(ex: BaseException) -> bool:
    """Detect OpenAI/Anthropic quota/billing exhaustion.

    Delegates to the canonical ``has_quota_text`` in utils.py.
    """
    for e in iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        rep = (repr(e) or "").lower()
        if has_quota_text(msg) or has_quota_text(rep):
            return True
    return False


def is_model_invalid_exception(ex: BaseException) -> bool:
    """Detect invalid/unknown model errors and allow escalation fallback."""
    needles = (
        "model_not_found",
        "model not found",
        "does not exist",
        "unknown model",
        "invalid model",
        "is not available",
    )
    for e in iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        rep = (repr(e) or "").lower()
        if ("model" in msg or "model" in rep) and (
            any(n in msg for n in needles) or any(n in rep for n in needles)
        ):
            return True
    return False


def is_transient_exception(ex: BaseException) -> bool:
    """Detect transient / retryable API errors (rate-limit, 5xx, timeout).

    NOTE: "500" removed from needles to avoid false positives on port numbers
    (e.g. "localhost:5000"). HTTP 500 is caught by the status_code check below.
    """
    needles = (
        "rate_limit", " 429", " 503", " 502",
        "overloaded", "connection", "timeout", "timed out",
        "internal server error",
    )
    for e in iter_exc_chain(ex):
        try:
            msg = (str(e) or "").lower()
        except Exception:
            msg = ""
        rep = (repr(e) or "").lower()
        if any(n in msg for n in needles) or any(n in rep for n in needles):
            return True
        status = getattr(e, "status_code", None) or getattr(e, "status", None)
        if status is not None:
            try:
                if int(status) in (429, 500, 502, 503):
                    return True
            except (ValueError, TypeError):
                pass
    return False
