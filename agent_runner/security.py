from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .policy import DEFAULT_POLICY_RULES


DEFAULT_SECURITY_RULES: list[dict[str, str]] = [
    *DEFAULT_POLICY_RULES,
    {"id": "generic_private_key", "severity": "high", "regex": r"-----BEGIN PRIVATE KEY-----"},
]


def _load_rules_from_path(path: str) -> list[dict[str, str]]:
    if not path:
        return []
    p = Path(path).expanduser()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(data, list):
        out: list[dict[str, str]] = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            if "pattern" in item or "regex" in item:
                out.append(
                    {
                        "id": str(item.get("id") or f"custom_{i}"),
                        "severity": str(item.get("severity") or "medium"),
                        "regex": str(item.get("pattern") or item.get("regex")),
                        "message": str(item.get("description") or ""),
                    }
                )
        return out
    return []


def load_security_rules(rules_path: str = "") -> list[dict[str, str]]:
    rules = list(DEFAULT_SECURITY_RULES)
    rules.extend(_load_rules_from_path(rules_path))
    return rules


def _compile_allow_patterns(allow_patterns: Iterable[str]) -> list[re.Pattern[str]]:
    patterns: list[re.Pattern[str]] = []
    for raw in allow_patterns:
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            patterns.append(re.compile(s))
        except re.error:
            continue
    return patterns


def security_scan_files(
    files: Iterable[tuple[str, str]],
    rules: list[dict[str, str]],
    *,
    allow_patterns: Iterable[str] = (),
    ignore_paths: Iterable[str] = (),
    max_hits_per_rule: int = 10,
) -> dict[str, Any]:
    findings: list[dict[str, Any]] = []
    allow_pats = _compile_allow_patterns(allow_patterns)
    ignore = {str(p).strip() for p in ignore_paths if str(p).strip()}

    def _ignored(path: str) -> bool:
        return any(path.startswith(prefix) for prefix in ignore)

    files_scanned = 0
    bytes_scanned = 0

    for path, text in files:
        if not path or _ignored(path):
            continue
        files_scanned += 1
        bytes_scanned += len(text.encode("utf-8", errors="replace"))
        for rule in rules:
            rid = rule.get("id", "rule")
            sev = rule.get("severity", "medium")
            regex = rule.get("regex", "")
            message = rule.get("message") or rule.get("description") or ""
            try:
                pat = re.compile(regex)
            except re.error:
                continue
            hits = 0
            for m in pat.finditer(text):
                if allow_pats and any(p.search(m.group(0)) for p in allow_pats):
                    continue
                findings.append(
                    {
                        "rule_id": rid,
                        "severity": sev,
                        "path": path,
                        "match": m.group(0)[:80],
                        "message": message or "Security rule matched.",
                    }
                )
                hits += 1
                if hits >= max_hits_per_rule:
                    break

    return {
        "findings": findings,
        "stats": {"files_scanned": files_scanned, "bytes_scanned": bytes_scanned},
    }
