from __future__ import annotations

import json
import re
import hashlib
from pathlib import Path
from typing import Any, Iterable


DEFAULT_POLICY_RULES: list[dict[str, str]] = [
    {"id": "openai_key", "severity": "high", "regex": r"\bsk-[A-Za-z0-9]{20,}\b"},
    {"id": "github_token_ghp", "severity": "high", "regex": r"\bghp_[A-Za-z0-9]{30,}\b"},
    {"id": "github_pat", "severity": "high", "regex": r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"},
    {"id": "aws_access_key", "severity": "high", "regex": r"\bAKIA[0-9A-Z]{16}\b"},
    {"id": "private_key_block", "severity": "high", "regex": r"-----BEGIN (?:RSA|OPENSSH|EC|DSA) PRIVATE KEY-----"},
    {"id": "password_assignment", "severity": "medium", "regex": r"(?i)\b(password|passwd|pwd)\b\s*[:=]\s*['\"][^'\"]{6,}['\"]"},
]


def load_policy_rules(rules_file: str, extra_rules: list[str]) -> list[dict[str, str]]:
    rules: list[dict[str, str]] = list(DEFAULT_POLICY_RULES)
    if rules_file:
        p = Path(rules_file).expanduser()
        if p.exists():
            raw = p.read_text(encoding="utf-8", errors="replace")
            # JSON list?
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    for i, item in enumerate(parsed):
                        if isinstance(item, dict) and "regex" in item:
                            rule = {
                                "id": str(item.get("id") or f"custom_{i}"),
                                "severity": str(item.get("severity") or "medium"),
                                "regex": str(item["regex"]),
                            }
                            try:
                                re.compile(rule["regex"])
                            except re.error as e:
                                from .utils import eprint
                                eprint(f"[WARN] Invalid policy regex '{rule['regex'][:60]}': {e}")
                                continue
                            rules.append(rule)
                    return rules
            except Exception:
                pass
            # newline-separated regex rules
            for i, line in enumerate(raw.splitlines()):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                rule = {"id": f"file_{i}", "severity": "medium", "regex": line}
                try:
                    re.compile(rule["regex"])
                except re.error as e:
                    from .utils import eprint
                    eprint(f"[WARN] Invalid policy regex '{rule['regex'][:60]}': {e}")
                    continue
                rules.append(rule)
    for i, r in enumerate(extra_rules or []):
        if r and isinstance(r, str):
            rule = {"id": f"cli_{i}", "severity": "medium", "regex": r}
            try:
                re.compile(rule["regex"])
            except re.error as e:
                from .utils import eprint
                eprint(f"[WARN] Invalid policy regex '{rule['regex'][:60]}': {e}")
                continue
            rules.append(rule)
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


def _is_allowed(match_text: str, allow_patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(match_text) for p in allow_patterns)


def _hash_match(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _match_preview(text: str, keep: int = 3) -> str:
    if not text:
        return ""
    if len(text) <= keep * 2:
        if len(text) <= 2:
            return f"{text[:1]}..." if len(text) == 1 else f"{text[:1]}...{text[-1:]}"
        return f"{text[:1]}...{text[-1:]}"
    return f"{text[:keep]}...{text[-keep:]}"


def _match_location(text: str, start: int, path: str | None = None) -> dict[str, Any]:
    line = text.count("\n", 0, start) + 1
    location: dict[str, Any] = {"line": line, "byte_offset": start}
    if path:
        location["path"] = path
    return location


def policy_scan_text(
    text: str,
    rules: list[dict[str, str]],
    *,
    max_hits_per_rule: int = 10,
    allow_patterns: Iterable[str] = (),
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    allow_pats = _compile_allow_patterns(allow_patterns)
    for rule in rules:
        rid = rule.get("id", "rule")
        sev = rule.get("severity", "medium")
        regex = rule.get("regex", "")
        try:
            pat = re.compile(regex)
        except re.error:
            continue
        hits = 0
        for m in pat.finditer(text):
            if allow_pats and _is_allowed(m.group(0), allow_pats):
                continue
            violations.append({
                "rule_id": rid,
                "severity": sev,
                "match_sha256": _hash_match(m.group(0)),
                "match_preview": _match_preview(m.group(0)),
                "location": _match_location(text, m.start()),
            })
            hits += 1
            if hits >= max_hits_per_rule:
                break
    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "rules_count": len(rules),
        "scanned_bytes": len(text.encode("utf-8", errors="replace")),
    }


def policy_scan_files(
    files: Iterable[tuple[str, str]],
    rules: list[dict[str, str]],
    *,
    max_hits_per_rule: int = 10,
    allow_patterns: Iterable[str] = (),
    ignore_paths: Iterable[str] = (),
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    allow_pats = _compile_allow_patterns(allow_patterns)
    ignore = {str(p).strip() for p in ignore_paths if str(p).strip()}

    def _ignored(path: str) -> bool:
        return any(path == prefix or path.startswith(prefix + "/") or path.startswith(prefix + "\\") for prefix in ignore)

    compiled_rules = []
    for rule in rules:
        try:
            compiled_rules.append((rule, re.compile(rule.get("regex", ""))))
        except re.error:
            continue

    for path, text in files:
        if not path or _ignored(path):
            continue
        for rule, pat in compiled_rules:
            rid = rule.get("id", "rule")
            sev = rule.get("severity", "medium")
            hits = 0
            for m in pat.finditer(text):
                if allow_pats and _is_allowed(m.group(0), allow_pats):
                    continue
                violations.append({
                    "rule_id": rid,
                    "severity": sev,
                    "path": path,
                    "match_sha256": _hash_match(m.group(0)),
                    "match_preview": _match_preview(m.group(0)),
                    "location": _match_location(text, m.start(), path),
                })
                hits += 1
                if hits >= max_hits_per_rule:
                    break

    return {
        "ok": len(violations) == 0,
        "violations": violations,
        "rules_count": len(rules),
    }
