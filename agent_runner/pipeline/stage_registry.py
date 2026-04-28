from __future__ import annotations

import importlib
import re
from fnmatch import fnmatch
from typing import Any, List, Type

from ..runtime_contract import (
    BUILTIN_ROLE_SPECS,
    DEFAULT_ROLE_SPECS,
    ROLE_SPEC_CANONICALS,
)
from .stages.base import Stage
from .stages.pm_stage import PMStage
from .stages.dev_stage import DevStage
from .stages.qa_stage import QAStage
from .stages.security_stage import SecurityStage


_BUILTIN: dict[str, Type[Stage]] = {
    "PM": PMStage,
    "Dev": DevStage,
    "QA": QAStage,
    "Security": SecurityStage,
}

_PLUGIN_SPEC_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):(?P<class>[A-Za-z_][A-Za-z0-9_]*)$"
)


def builtin_role_specs() -> List[str]:
    return list(BUILTIN_ROLE_SPECS)


def normalize_role_spec(spec: Any) -> str:
    text = str(spec or "").strip()
    if not text:
        return ""
    return ROLE_SPEC_CANONICALS.get(text.lower(), text)


def is_plugin_role_spec(spec: Any) -> bool:
    text = str(spec or "").strip()
    if not text:
        return False
    return bool(_PLUGIN_SPEC_RE.match(text))


def classify_role_spec(spec: Any) -> str:
    text = normalize_role_spec(spec)
    if not text:
        return "empty"
    if text in _BUILTIN:
        return "builtin"
    if is_plugin_role_spec(text):
        return "plugin"
    return "invalid"


def normalize_role_specs(raw: Any, *, default: List[str] | None = None) -> List[str]:
    if raw is None:
        return list(default) if default is not None else []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return list(default) if default is not None else []
        parts = [part.strip() for part in re.split(r"[\s,;]+", text) if part and part.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        parts = [raw]

    out: List[str] = []
    for part in parts:
        normalized = normalize_role_spec(part)
        if normalized:
            out.append(normalized)
    if not out and default is not None and not isinstance(raw, (list, tuple)):
        return list(default)
    return out


def parse_roles(raw: str | None) -> List[str]:
    """Parse roles string into ordered role specs.

    Supports:
      - Comma/space/semicolon separated roles: "PM,Dev,QA"
      - External plugin stage specs: "pkg.mod:ClassName"

    Returns normalized role specs in input order. Builtins are canonicalized
    and plugin/unknown specs are preserved verbatim.
    """
    return normalize_role_specs(raw, default=list(DEFAULT_ROLE_SPECS))


def _is_allowed(spec: str, allowlist: list[str]) -> bool:
    if not allowlist:
        return False
    mod, _cls = spec.split(":", 1)
    for pat in allowlist:
        pat = pat.strip()
        if not pat:
            continue
        if ":" in pat:
            if fnmatch(spec, pat):
                return True
        elif fnmatch(mod, pat):
            return True
    return False


def _load_plugin(spec: str) -> Type[Stage]:
    mod, cls = spec.split(":", 1)
    m = importlib.import_module(mod)
    c = getattr(m, cls)
    if not isinstance(c, type) or not issubclass(c, Stage):
        raise TypeError(f"Plugin stage must subclass Stage: {spec}")
    return c


def make_stages(
    raw_roles: str | None,
    *,
    plugins_enabled: bool,
    plugins_allowlist: list[str],
    plugins_strict: bool,
) -> List[Stage]:
    roles = parse_roles(raw_roles)
    stages: List[Stage] = []
    for r in roles:
        role_kind = classify_role_spec(r)
        if role_kind == "builtin":
            cls = _BUILTIN.get(r)
            if cls is not None:
                stages.append(cls())
            continue
        if role_kind == "plugin":
            if not plugins_enabled:
                raise ValueError(f"Plugin stages are disabled: {r}")
            if not _is_allowed(r, plugins_allowlist):
                raise ValueError(f"Plugin stage not allowed by allowlist: {r}")
            try:
                stages.append(_load_plugin(r)())
            except Exception:
                if plugins_strict:
                    raise
            continue
        raise ValueError(f"Invalid role spec: {r}")
    return stages
