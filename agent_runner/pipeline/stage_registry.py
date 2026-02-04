from __future__ import annotations

import importlib
import re
from typing import List, Type

from .stages.base import Stage
from .stages.pm_stage import PMStage
from .stages.dev_stage import DevStage
from .stages.qa_stage import QAStage
from .stages.security_stage import SecurityStage


_CANON = {
    "pm": "PM",
    "dev": "Dev",
    "qa": "QA",
    "security": "Security",
}

_BUILTIN: dict[str, Type[Stage]] = {
    "PM": PMStage,
    "Dev": DevStage,
    "QA": QAStage,
    "Security": SecurityStage,
}


def parse_roles(raw: str | None) -> List[str]:
    """Parse roles string into ordered role specs.

    Supports:
      - Comma/space/semicolon separated roles: "PM,Dev,QA"
      - External plugin stage specs: "pkg.mod:ClassName"

    Returns role specs with stable de-duplication, preserving order.
    """
    if not raw:
        return ["PM", "Dev", "QA"]

    parts = [p.strip() for p in re.split(r"[\s,;]+", raw) if p and p.strip()]
    out: List[str] = []
    for p in parts:
        if ":" in p:
            out.append(p)
            continue
        out.append(_CANON.get(p.lower(), p))

    seen = set()
    deduped: List[str] = []
    for r in out:
        k = r.lower()
        if k in seen:
            continue
        seen.add(k)
        deduped.append(r)

    return deduped


def _load_plugin(spec: str) -> Type[Stage]:
    mod, cls = spec.split(":", 1)
    m = importlib.import_module(mod)
    c = getattr(m, cls)
    if not isinstance(c, type) or not issubclass(c, Stage):
        raise TypeError(f"Plugin stage must subclass Stage: {spec}")
    return c


def make_stages(raw_roles: str | None) -> List[Stage]:
    roles = parse_roles(raw_roles)
    stages: List[Stage] = []
    for r in roles:
        if ":" in r:
            try:
                stages.append(_load_plugin(r)())
            except Exception:
                # keep robust: plugin load failure should not crash the runner
                continue
            continue
        cls = _BUILTIN.get(r)
        if cls is not None:
            stages.append(cls())
    return stages
