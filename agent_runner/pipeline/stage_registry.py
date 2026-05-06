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
from .stages.backlog_refiner_stage import BacklogRefinerStage
from .stages.pm_stage import PMStage
from .stages.dev_stage import DevStage
from .stages.qa_stage import QAStage
from .stages.security_stage import SecurityStage


_BUILTIN: dict[str, Type[Stage]] = {
    "PM": PMStage,
    "PL": BacklogRefinerStage,
    "Dev": DevStage,
    "QA": QAStage,
    "Security": SecurityStage,
}

_PLUGIN_SPEC_RE = re.compile(
    r"^(?P<module>[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*):(?P<class>[A-Za-z_][A-Za-z0-9_]*)$"
)
_BOOL_TRUE = {"1", "true", "yes", "on", "enabled"}
_BOOL_FALSE = {"0", "false", "no", "off", "disabled"}


class PluginStageLoadError(RuntimeError):
    def __init__(self, message: str, diagnostics: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = list(diagnostics or [])


def coerce_plugin_bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _BOOL_TRUE:
            return True
        if text in _BOOL_FALSE:
            return False
    return bool(value)


def normalize_plugin_allowlist(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() for part in value if str(part).strip()]
    if value in (None, ""):
        return []
    return [str(value).strip()]


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


def _plugin_diagnostic(
    spec: str,
    *,
    status: str,
    reason: str,
    enabled: bool,
    allowed: bool,
    strict: bool,
    action: str,
    stage_name: str = "",
    error: BaseException | None = None,
) -> dict[str, Any]:
    module, _, class_name = spec.partition(":")
    payload: dict[str, Any] = {
        "spec": spec,
        "module": module,
        "class": class_name,
        "status": status,
        "reason": reason,
        "enabled": bool(enabled),
        "allowed": bool(allowed),
        "strict": bool(strict),
        "action": action,
        "stage_name": stage_name,
        "stageName": stage_name,
    }
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["errorType"] = type(error).__name__
        payload["error"] = str(error)
    return payload


def _append_plugin_diagnostic(target: list[dict[str, Any]] | None, diagnostic: dict[str, Any]) -> None:
    if target is not None:
        target.append(diagnostic)


def _plugin_load_status(ex: BaseException) -> str:
    if isinstance(ex, (ModuleNotFoundError, AttributeError)):
        return "missing"
    return "load_error"


def build_plugin_stage_diagnostics_payload(
    diagnostics: list[dict[str, Any]] | None,
    *,
    strict: bool,
    error: BaseException | None = None,
) -> dict[str, Any]:
    items = [dict(item) for item in diagnostics or [] if isinstance(item, dict)]
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    failed = bool(error) or any(str(item.get("action") or "") == "failed" for item in items)
    skipped = any(str(item.get("action") or "") == "skipped" for item in items)
    status = "failed" if failed else "partial" if skipped else "ok"
    payload = {
        "status": status,
        "strict": bool(strict),
        "failed": failed,
        "skipped": skipped,
        "counts": counts,
        "items": items,
        "error": str(error) if error is not None else "",
        "error_type": type(error).__name__ if error is not None else "",
        "errorType": type(error).__name__ if error is not None else "",
    }
    return payload


def format_plugin_stage_diagnostics_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Plugin stage diagnostics",
        "",
        f"- status: {payload.get('status') or 'unknown'}",
        f"- strict: {bool(payload.get('strict', False))}",
        f"- failed: {bool(payload.get('failed', False))}",
        f"- skipped: {bool(payload.get('skipped', False))}",
    ]
    error = str(payload.get("error") or "").strip()
    if error:
        lines.append(f"- error: {error}")
    lines.extend(["", "## Items", ""])
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    if not items:
        lines.append("- none")
    for item in items:
        if not isinstance(item, dict):
            continue
        detail = str(item.get("error") or item.get("stage_name") or item.get("stageName") or "").strip()
        suffix = f" ({detail})" if detail else ""
        lines.append(
            "- "
            f"{item.get('spec') or '(unknown)'}: "
            f"status={item.get('status') or 'unknown'} "
            f"reason={item.get('reason') or 'unknown'} "
            f"action={item.get('action') or 'unknown'}"
            f"{suffix}"
        )
    return "\n".join(lines) + "\n"


def make_stages(
    raw_roles: str | None,
    *,
    plugins_enabled: Any,
    plugins_allowlist: Any,
    plugins_strict: Any,
    plugin_diagnostics: list[dict[str, Any]] | None = None,
) -> List[Stage]:
    roles = parse_roles(raw_roles)
    plugins_enabled_bool = coerce_plugin_bool(plugins_enabled, default=False)
    plugins_strict_bool = coerce_plugin_bool(plugins_strict, default=True)
    plugin_allowlist_items = normalize_plugin_allowlist(plugins_allowlist)
    stages: List[Stage] = []
    for r in roles:
        role_kind = classify_role_spec(r)
        if role_kind == "builtin":
            cls = _BUILTIN.get(r)
            if cls is not None:
                stages.append(cls())
            continue
        if role_kind == "plugin":
            if not plugins_enabled_bool:
                diagnostic = _plugin_diagnostic(
                    r,
                    status="blocked",
                    reason="plugins_disabled",
                    enabled=False,
                    allowed=False,
                    strict=plugins_strict_bool,
                    action="failed" if plugins_strict_bool else "skipped",
                )
                _append_plugin_diagnostic(plugin_diagnostics, diagnostic)
                if plugins_strict_bool:
                    raise PluginStageLoadError(f"Plugin stages are disabled: {r}", plugin_diagnostics)
                continue
            if not _is_allowed(r, plugin_allowlist_items):
                diagnostic = _plugin_diagnostic(
                    r,
                    status="blocked",
                    reason="allowlist_blocked",
                    enabled=True,
                    allowed=False,
                    strict=plugins_strict_bool,
                    action="failed" if plugins_strict_bool else "skipped",
                )
                _append_plugin_diagnostic(plugin_diagnostics, diagnostic)
                if plugins_strict_bool:
                    raise PluginStageLoadError(f"Plugin stage not allowed by allowlist: {r}", plugin_diagnostics)
                continue
            try:
                plugin_stage = _load_plugin(r)()
            except Exception as ex:
                status = _plugin_load_status(ex)
                diagnostic = _plugin_diagnostic(
                    r,
                    status=status,
                    reason=status,
                    enabled=True,
                    allowed=True,
                    strict=plugins_strict_bool,
                    action="failed" if plugins_strict_bool else "skipped",
                    error=ex,
                )
                _append_plugin_diagnostic(plugin_diagnostics, diagnostic)
                if plugins_strict_bool:
                    raise PluginStageLoadError(f"Plugin stage load failed: {r}: {ex}", plugin_diagnostics) from ex
                continue
            stages.append(plugin_stage)
            _append_plugin_diagnostic(
                plugin_diagnostics,
                _plugin_diagnostic(
                    r,
                    status="loaded",
                    reason="loaded",
                    enabled=True,
                    allowed=True,
                    strict=plugins_strict_bool,
                    action="loaded",
                    stage_name=str(getattr(plugin_stage, "name", "") or r),
                ),
            )
            continue
        raise ValueError(f"Invalid role spec: {r}")
    return stages
