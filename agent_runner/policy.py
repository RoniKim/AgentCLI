from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


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
                            rules.append({
                                "id": str(item.get("id") or f"custom_{i}"),
                                "severity": str(item.get("severity") or "medium"),
                                "regex": str(item["regex"]),
                            })
                    return rules
            except Exception:
                pass
            # newline-separated regex rules
            for i, line in enumerate(raw.splitlines()):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                rules.append({"id": f"file_{i}", "severity": "medium", "regex": line})
    for i, r in enumerate(extra_rules or []):
        if r and isinstance(r, str):
            rules.append({"id": f"cli_{i}", "severity": "medium", "regex": r})
    return rules


def policy_scan_text(text: str, rules: list[dict[str, str]], max_hits_per_rule: int = 10) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
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
            snippet = text[max(0, m.start() - 30): min(len(text), m.end() + 30)]
            violations.append({
                "rule_id": rid,
                "severity": sev,
                "match": m.group(0)[:80],
                "snippet": snippet.replace("\n", "\\n")[:160],
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
