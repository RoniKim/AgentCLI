from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any

from .config import AGENT_WORK_DIR, ensure_work_dir
from .utils import atomic_write_json, now_iso


ACTIVE_GOAL_FILENAME = "ACTIVE_GOAL.json"
ACTIVE_GOAL_EVENTS_FILENAME = "events.jsonl"
ACTIVE_GOAL_GOALS_PROPOSAL_FILENAME = "GOALS_PROPOSAL.json"
ACTIVE_GOAL_CONTENT_MAX_CHARS = 20000
ACTIVE_GOAL_OBJECTIVE_MAX_CHARS = 4000
ACTIVE_GOAL_NOTE_MAX_CHARS = 4000
ACTIVE_GOAL_MODES = frozenset({"strict", "adaptive", "exploratory"})
ACTIVE_GOAL_STATUSES = frozenset({"active", "completed", "canceled"})
ACTIVE_GOAL_CHECKPOINT_STATUSES = frozenset({"pending", "active", "completed", "canceled"})
ACTIVE_GOAL_MODE_POLICIES: dict[str, dict[str, Any]] = {
    "strict": {
        "mode": "strict",
        "allow_bounded_discovery": False,
        "allowBroaderDiscovery": False,
        "planning_guidance": (
            "Plan only tasks directly necessary for the active objective and already admitted by GOALS.md."
        ),
    },
    "adaptive": {
        "mode": "adaptive",
        "allow_bounded_discovery": True,
        "allowBroaderDiscovery": False,
        "planning_guidance": (
            "Prefer implementation and validation tasks, with small discovery steps only when they unblock admitted work."
        ),
    },
    "exploratory": {
        "mode": "exploratory",
        "allow_bounded_discovery": True,
        "allowBroaderDiscovery": True,
        "planning_guidance": (
            "May propose bounded discovery, inventory, or spike tasks when tied to unchecked GOALS.md items "
            "or proposal-only GOALS updates."
        ),
    },
}
ACTIVE_GOAL_MODE_SAFETY_BOUNDARY = (
    "Mode never bypasses GOALS.md admission, validation gates, worktree policy, PR policy, network/LAN safety, "
    "or operator confirmation requirements."
)
ACTIVE_GOAL_COMPLETION_EVIDENCE_SOURCES = ("task_outcome", "validation_artifact", "operator_confirmation")
ACTIVE_GOAL_TEMPLATE_KEYS = (
    "bug_fix",
    "feature_build",
    "refactor",
    "test_hardening",
    "documentation",
    "release_prep",
    "exploratory_improvement",
)
ACTIVE_GOAL_AUTONOMY_PRESET_KEYS = ("one_shot", "overnight", "exploratory")
ACTIVE_GOAL_TEMPLATES: dict[str, dict[str, Any]] = {
    "bug_fix": {
        "key": "bug_fix",
        "label": "Bug fix",
        "mode": "strict",
        "preset": "one_shot",
        "objective_template": "Fix <bug> and verify the regression path.",
        "done_when": "The bug is fixed, the failing path is covered, and validation evidence is attached.",
        "checkpoint_titles": ["Reproduce or isolate", "Patch", "Validate regression"],
    },
    "feature_build": {
        "key": "feature_build",
        "label": "Feature build",
        "mode": "adaptive",
        "preset": "one_shot",
        "objective_template": "Build <feature> inside current GOALS.md scope.",
        "done_when": "The feature is implemented, reviewed in small tasks, and validation evidence is attached.",
        "checkpoint_titles": ["Plan slice", "Implement", "Validate and report"],
    },
    "refactor": {
        "key": "refactor",
        "label": "Refactor",
        "mode": "adaptive",
        "preset": "one_shot",
        "objective_template": "Refactor <area> without changing user-visible behavior.",
        "done_when": "The refactor is behavior-preserving and targeted tests or compile checks pass.",
        "checkpoint_titles": ["Inventory callers", "Refactor safely", "Run regression checks"],
    },
    "test_hardening": {
        "key": "test_hardening",
        "label": "Test hardening",
        "mode": "adaptive",
        "preset": "one_shot",
        "objective_template": "Harden tests for <risk area> and reduce validation gaps.",
        "done_when": "The test gap is covered and repeated flaky or skipped validation reasons are addressed.",
        "checkpoint_titles": ["Identify gap", "Add or stabilize tests", "Run focused validation"],
    },
    "documentation": {
        "key": "documentation",
        "label": "Documentation",
        "mode": "strict",
        "preset": "one_shot",
        "objective_template": "Document <operator workflow> with current contracts and examples.",
        "done_when": "Docs match live behavior and stale-contract tests protect the documented claims.",
        "checkpoint_titles": ["Confirm live contract", "Update docs", "Add stale-doc guard"],
    },
    "release_prep": {
        "key": "release_prep",
        "label": "Release prep",
        "mode": "adaptive",
        "preset": "overnight",
        "objective_template": "Prepare <release scope> for operator review.",
        "done_when": "Validation, PR queue, final report, and unresolved blockers are ready for operator review.",
        "checkpoint_titles": ["Collect blockers", "Run validation", "Summarize release readiness"],
    },
    "exploratory_improvement": {
        "key": "exploratory_improvement",
        "label": "Exploratory improvement",
        "mode": "exploratory",
        "preset": "exploratory",
        "objective_template": "Explore bounded improvements for <area> and propose GOALS changes if needed.",
        "done_when": "Findings are bounded, evidence is attached, and any GOALS change remains proposal-only.",
        "checkpoint_titles": ["Bound discovery", "Inspect evidence", "Propose next action"],
    },
}
ACTIVE_GOAL_AUTONOMY_PRESETS: dict[str, dict[str, Any]] = {
    "one_shot": {
        "key": "one_shot",
        "label": "One-shot work",
        "mode": "adaptive",
        "budgets": {"cycle_budget": 2, "time_budget_seconds": 0, "token_budget": 0},
        "execution": {"unattended": False, "loop": False, "loop_idle_exit_after": 0},
        "validation_strictness": "normal",
        "worktree": {"worktree_merge_mode": "manual"},
        "notifications": {"send_cycle_summary": True, "notify_events": ["run_stop", "task_failed", "error"]},
    },
    "overnight": {
        "key": "overnight",
        "label": "Overnight work",
        "mode": "adaptive",
        "budgets": {"cycle_budget": 6, "time_budget_seconds": 8 * 60 * 60, "token_budget": 0},
        "execution": {"unattended": True, "loop": True, "loop_idle_exit_after": 1800},
        "validation_strictness": "strict",
        "worktree": {"worktree_merge_mode": "manual"},
        "notifications": {
            "send_cycle_summary": True,
            "notify_events": ["run_start", "run_stop", "task_failed", "quota", "error", "stalled"],
        },
    },
    "exploratory": {
        "key": "exploratory",
        "label": "Exploratory improvement",
        "mode": "exploratory",
        "budgets": {"cycle_budget": 2, "time_budget_seconds": 60 * 60, "token_budget": 0},
        "execution": {"unattended": False, "loop": False, "loop_idle_exit_after": 0},
        "validation_strictness": "proposal_only",
        "worktree": {"worktree_merge_mode": "manual"},
        "notifications": {"send_cycle_summary": True, "notify_events": ["run_stop", "error"]},
    },
}
ACTIVE_GOAL_EXPORT_FILENAME = "ACTIVE_GOAL_EXPORT.json"
ACTIVE_GOAL_REASON_ACTIVE = "active_goal_active"
ACTIVE_GOAL_REASON_COMPLETED = "active_goal_completed"
ACTIVE_GOAL_REASON_CANCELED = "active_goal_canceled"
ACTIVE_GOAL_REASON_MISSING_OBJECTIVE = "active_goal_missing_objective"
ACTIVE_GOAL_REASON_TOKEN_BUDGET_EXHAUSTED = "active_goal_token_budget_exhausted"
ACTIVE_GOAL_REASON_CYCLE_BUDGET_EXHAUSTED = "active_goal_cycle_budget_exhausted"
ACTIVE_GOAL_REASON_TIME_BUDGET_EXPIRED = "active_goal_time_budget_expired"
ACTIVE_GOAL_REASON_ERROR = "active_goal_error"
ACTIVE_GOAL_BUDGET_STOP_REASONS = frozenset(
    {
        ACTIVE_GOAL_REASON_TOKEN_BUDGET_EXHAUSTED,
        ACTIVE_GOAL_REASON_CYCLE_BUDGET_EXHAUSTED,
        ACTIVE_GOAL_REASON_TIME_BUDGET_EXPIRED,
    }
)


class ActiveGoalError(ValueError):
    """Base error for active-goal mutations."""


class ActiveGoalConflict(ActiveGoalError):
    """Raised when an expected stale-write token no longer matches."""


def active_goal_dir(repo: Path) -> Path:
    return Path(repo).expanduser().resolve() / AGENT_WORK_DIR / "goals"


def active_goal_path(repo: Path) -> Path:
    return active_goal_dir(repo) / ACTIVE_GOAL_FILENAME


def active_goal_events_path(repo: Path) -> Path:
    return active_goal_dir(repo) / ACTIVE_GOAL_EVENTS_FILENAME


def active_goal_goals_proposal_path(repo: Path) -> Path:
    return active_goal_dir(repo) / ACTIVE_GOAL_GOALS_PROPOSAL_FILENAME


def active_goal_export_path(repo: Path) -> Path:
    return active_goal_dir(repo) / ACTIVE_GOAL_EXPORT_FILENAME


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _coerce_positive_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def normalize_active_goal_mode(value: Any, *, default: str = "adaptive") -> str:
    mode = str(value or "").strip().lower()
    if mode in ACTIVE_GOAL_MODES:
        return mode
    fallback = str(default or "adaptive").strip().lower()
    return fallback if fallback in ACTIVE_GOAL_MODES else "adaptive"


def normalize_active_goal_status(value: Any, *, default: str = "active") -> str:
    status = str(value or "").strip().lower()
    if status in ACTIVE_GOAL_STATUSES:
        return status
    fallback = str(default or "active").strip().lower()
    return fallback if fallback in ACTIVE_GOAL_STATUSES else "active"


def _slug(value: Any, *, default: str = "checkpoint") -> str:
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-._").lower()
    return text[:80] or default


def _safe_public_text(value: Any, *, max_chars: int = ACTIVE_GOAL_NOTE_MAX_CHARS) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    secret_patterns = (
        r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?[^'\"\s]+",
        r"sk-[A-Za-z0-9_-]{12,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
    )
    for pattern in secret_patterns:
        text = re.sub(pattern, "[redacted-secret]", text)
    return text[:max_chars]


def _template_checkpoint_specs(template_key: str) -> list[dict[str, Any]]:
    template = active_goal_template(template_key)
    titles = template.get("checkpoint_titles") if isinstance(template.get("checkpoint_titles"), list) else []
    checkpoints: list[dict[str, Any]] = []
    for index, title in enumerate(titles, start=1):
        title_text = str(title or "").strip()
        if not title_text:
            continue
        checkpoints.append(
            {
                "id": f"cp-{index:02d}-{_slug(title_text)}",
                "title": title_text,
                "status": "active" if index == 1 else "pending",
                "evidence": [],
                "resume_point": {},
                "resumePoint": {},
            }
        )
    return checkpoints


def active_goal_template(key: Any) -> dict[str, Any]:
    normalized = str(key or "").strip().lower().replace("-", "_")
    if normalized not in ACTIVE_GOAL_TEMPLATES:
        raise ActiveGoalError(f"Unknown active-goal template: {key}")
    return dict(ACTIVE_GOAL_TEMPLATES[normalized])


def list_active_goal_templates() -> dict[str, Any]:
    templates = [dict(ACTIVE_GOAL_TEMPLATES[key]) for key in ACTIVE_GOAL_TEMPLATE_KEYS]
    return {
        "ok": True,
        "templates": templates,
        "items": templates,
        "keys": list(ACTIVE_GOAL_TEMPLATE_KEYS),
        "policy": "templates shape operator intent only; GOALS.md remains the admission authority",
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
    }


def active_goal_autonomy_preset(key: Any) -> dict[str, Any]:
    normalized = str(key or "").strip().lower().replace("-", "_")
    if normalized not in ACTIVE_GOAL_AUTONOMY_PRESETS:
        raise ActiveGoalError(f"Unknown active-goal autonomy preset: {key}")
    return dict(ACTIVE_GOAL_AUTONOMY_PRESETS[normalized])


def list_active_goal_autonomy_presets() -> dict[str, Any]:
    presets = [dict(ACTIVE_GOAL_AUTONOMY_PRESETS[key]) for key in ACTIVE_GOAL_AUTONOMY_PRESET_KEYS]
    return {
        "ok": True,
        "presets": presets,
        "items": presets,
        "keys": list(ACTIVE_GOAL_AUTONOMY_PRESET_KEYS),
        "policy": "presets bundle runtime defaults only and never weaken validation, PR, worktree, or LAN gates",
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
    }


def active_goal_mode_policy(mode: Any) -> dict[str, Any]:
    normalized_mode = normalize_active_goal_mode(mode)
    policy = dict(ACTIVE_GOAL_MODE_POLICIES.get(normalized_mode) or ACTIVE_GOAL_MODE_POLICIES["adaptive"])
    policy["safety_boundary"] = ACTIVE_GOAL_MODE_SAFETY_BOUNDARY
    policy["safetyBoundary"] = ACTIVE_GOAL_MODE_SAFETY_BOUNDARY
    policy["can_bypass_gates"] = False
    policy["canBypassGates"] = False
    return policy


def _normalize_evidence(value: Any, *, default_kind: str = "note") -> list[dict[str, Any]]:
    if value is None:
        return []
    raw_items = value if isinstance(value, list) else [value]
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, dict):
            kind = str(raw.get("kind") or raw.get("type") or default_kind).strip() or default_kind
            text = str(raw.get("text") or raw.get("message") or raw.get("summary") or "").strip()
            ref = str(raw.get("ref") or raw.get("path") or raw.get("url") or "").strip()
            source = str(raw.get("source") or raw.get("sourceKind") or "").strip()
            recorded_at = str(raw.get("recorded_at") or raw.get("recordedAt") or now_iso()).strip()
        else:
            kind = default_kind
            text = str(raw or "").strip()
            ref = ""
            source = ""
            recorded_at = now_iso()
        if not text and not ref:
            continue
        item = {
            "kind": kind[:80],
            "text": text[:ACTIVE_GOAL_NOTE_MAX_CHARS],
            "ref": ref[:1000],
            "recorded_at": recorded_at,
        }
        if source:
            item["source"] = source[:80]
            item["sourceKind"] = source[:80]
        items.append(item)
    return items


def _normalize_resume_point(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        text = str(value or "").strip()
        return {"note": text[:ACTIVE_GOAL_NOTE_MAX_CHARS]} if text else {}
    resume: dict[str, Any] = {}
    for key in ("task_id", "taskId", "run_id", "runId", "path", "artifact", "note"):
        raw = value.get(key)
        text = str(raw or "").strip()
        if text:
            resume[key] = text[:1000]
    return resume


def _normalize_checkpoint(value: Any, *, index: int = 0) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {"title": str(value or "").strip()}
    title = str(raw.get("title") or raw.get("name") or raw.get("objective") or f"Checkpoint {index or 1}").strip()
    checkpoint_id = str(raw.get("id") or raw.get("checkpoint_id") or raw.get("checkpointId") or "").strip()
    if not checkpoint_id:
        checkpoint_id = f"cp-{index + 1:02d}-{_slug(title)}"
    status = str(raw.get("status") or raw.get("state") or "pending").strip().lower()
    if status not in ACTIVE_GOAL_CHECKPOINT_STATUSES:
        status = "pending"
    evidence = raw.get("evidence")
    if evidence is None:
        evidence = raw.get("completion_evidence")
    if evidence is None:
        evidence = raw.get("completionEvidence")
    resume_point = raw.get("resume_point") if isinstance(raw.get("resume_point"), dict) else raw.get("resumePoint")
    normalized = {
        "id": checkpoint_id[:120],
        "title": title[:300],
        "status": status,
        "created_at": str(raw.get("created_at") or raw.get("createdAt") or "").strip(),
        "updated_at": str(raw.get("updated_at") or raw.get("updatedAt") or "").strip(),
        "completed_at": str(raw.get("completed_at") or raw.get("completedAt") or "").strip(),
        "evidence": _normalize_evidence(evidence, default_kind="checkpoint_evidence"),
        "resume_point": _normalize_resume_point(resume_point),
        "resumePoint": _normalize_resume_point(resume_point),
    }
    if not normalized["created_at"]:
        normalized["created_at"] = now_iso()
    if not normalized["updated_at"]:
        normalized["updated_at"] = normalized["created_at"]
    return normalized


def _normalize_checkpoints(value: Any) -> list[dict[str, Any]]:
    raw_items = value if isinstance(value, list) else []
    checkpoints = [_normalize_checkpoint(item, index=index) for index, item in enumerate(raw_items)]
    if not any(item["status"] == "active" for item in checkpoints):
        for item in checkpoints:
            if item["status"] == "pending":
                item["status"] = "active"
                item["updated_at"] = now_iso()
                break
    return checkpoints


def _checkpoint_progress(checkpoints: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(checkpoints)
    completed = len([item for item in checkpoints if item.get("status") == "completed"])
    active = next((item for item in checkpoints if item.get("status") == "active"), None)
    return {
        "total": total,
        "completed": completed,
        "remaining": max(0, total - completed),
        "percent": min(100.0, round((completed / total) * 100.0, 2)) if total else None,
        "active_checkpoint_id": str((active or {}).get("id") or ""),
        "activeCheckpointId": str((active or {}).get("id") or ""),
        "active_checkpoint_title": str((active or {}).get("title") or ""),
        "activeCheckpointTitle": str((active or {}).get("title") or ""),
    }


def _completion_policy() -> dict[str, Any]:
    return {
        "requires_evidence": True,
        "requiresEvidence": True,
        "allowed_evidence_sources": list(ACTIVE_GOAL_COMPLETION_EVIDENCE_SOURCES),
        "allowedEvidenceSources": list(ACTIVE_GOAL_COMPLETION_EVIDENCE_SOURCES),
        "does_not_mark_goals_complete": True,
        "doesNotMarkGoalsComplete": True,
        "does_not_approve_pr_merge": True,
        "doesNotApprovePrMerge": True,
        "does_not_count_as_merge_readiness": True,
        "doesNotCountAsMergeReadiness": True,
        "summary": (
            "Active-goal completion requires task outcome, validation artifact, or explicit operator-confirmation "
            "evidence and never marks GOALS.md complete or approves PR merge readiness."
        ),
    }


def _completion_evidence_source(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "").strip().lower().replace("-", "_")
    ref = str(item.get("ref") or "").strip().lower().replace("\\", "/")
    if kind in {"task", "task_outcome", "task_result", "task_completion", "task_status"}:
        return "task_outcome"
    if kind in {"validation", "validation_artifact", "validation_result", "validation_record", "artifact"}:
        return "validation_artifact"
    if kind in {"operator", "operator_confirmation", "confirmation", "manual_confirmation", "note"}:
        return "operator_confirmation"
    if ref.endswith("validation.json") or "validation" in ref:
        return "validation_artifact"
    if "state.json" in ref or "/tasks/" in ref:
        return "task_outcome"
    return "operator_confirmation"


def _normalize_completion_evidence(value: Any) -> list[dict[str, Any]]:
    items = _normalize_evidence(value, default_kind="operator_confirmation")
    if not items:
        raise ActiveGoalError(
            "Active goal completion requires task outcome, validation artifact, or explicit operator confirmation evidence."
        )
    normalized: list[dict[str, Any]] = []
    for item in items:
        next_item = dict(item)
        source = _completion_evidence_source(next_item)
        next_item["source"] = source
        next_item["sourceKind"] = source
        normalized.append(next_item)
    if not any(str(item.get("source") or "") in ACTIVE_GOAL_COMPLETION_EVIDENCE_SOURCES for item in normalized):
        raise ActiveGoalError(
            "Active goal completion evidence must come from task outcomes, validation artifacts, or operator confirmation."
        )
    return normalized


def _normalize_goal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    goal_id = str(payload.get("id") or payload.get("goal_id") or "").strip()
    objective = str(payload.get("objective") or "").strip()
    status = normalize_active_goal_status(payload.get("status"))
    mode = normalize_active_goal_mode(payload.get("mode"))
    created_at = str(payload.get("created_at") or payload.get("createdAt") or now_iso()).strip()
    updated_at = str(payload.get("updated_at") or payload.get("updatedAt") or created_at or now_iso()).strip()
    budgets_raw = payload.get("budgets") if isinstance(payload.get("budgets"), dict) else {}
    usage_raw = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    source_raw = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    template_raw = payload.get("template") if isinstance(payload.get("template"), dict) else {}
    preset_raw = payload.get("autonomy_preset") if isinstance(payload.get("autonomy_preset"), dict) else payload.get("autonomyPreset")
    preset_raw = preset_raw if isinstance(preset_raw, dict) else {}
    evidence_raw = payload.get("completion_evidence")
    if evidence_raw is None:
        evidence_raw = payload.get("completionEvidence")
    if evidence_raw is None:
        evidence_raw = payload.get("evidence")
    revision = _coerce_positive_int(payload.get("revision"))
    if revision <= 0:
        revision = 1

    normalized = {
        "schema_version": 1,
        "id": goal_id,
        "objective": objective[:ACTIVE_GOAL_OBJECTIVE_MAX_CHARS],
        "status": status,
        "mode": mode,
        "created_at": created_at,
        "updated_at": updated_at,
        "completed_at": str(payload.get("completed_at") or payload.get("completedAt") or "").strip(),
        "canceled_at": str(payload.get("canceled_at") or payload.get("canceledAt") or "").strip(),
        "source": {
            "kind": str(source_raw.get("kind") or payload.get("source_kind") or "operator").strip()[:80],
            "actor": str(source_raw.get("actor") or payload.get("source_actor") or "").strip()[:200],
            "surface": str(source_raw.get("surface") or payload.get("source_surface") or "").strip()[:80],
        },
        "budgets": {
            "token_budget": _coerce_positive_int(budgets_raw.get("token_budget", payload.get("token_budget"))),
            "time_budget_seconds": _coerce_positive_int(
                budgets_raw.get("time_budget_seconds", payload.get("time_budget_seconds"))
            ),
            "cycle_budget": _coerce_positive_int(budgets_raw.get("cycle_budget", payload.get("cycle_budget"))),
        },
        "usage": {
            "tokens_used": _coerce_positive_int(usage_raw.get("tokens_used", payload.get("tokens_used"))),
            "time_used_seconds": _coerce_positive_int(
                usage_raw.get("time_used_seconds", payload.get("time_used_seconds"))
            ),
            "cycles_used": _coerce_positive_int(usage_raw.get("cycles_used", payload.get("cycles_used"))),
        },
        "completion_evidence": _normalize_evidence(evidence_raw),
        "template": {
            "key": str(template_raw.get("key") or payload.get("template_key") or payload.get("templateKey") or "").strip()[:80],
            "label": str(template_raw.get("label") or "").strip()[:120],
        },
        "autonomy_preset": {
            "key": str(preset_raw.get("key") or payload.get("autonomy_preset_key") or payload.get("autonomyPresetKey") or "").strip()[:80],
            "label": str(preset_raw.get("label") or "").strip()[:120],
        },
        "autonomyPreset": {
            "key": str(preset_raw.get("key") or payload.get("autonomy_preset_key") or payload.get("autonomyPresetKey") or "").strip()[:80],
            "label": str(preset_raw.get("label") or "").strip()[:120],
        },
        "checkpoints": _normalize_checkpoints(payload.get("checkpoints")),
        "notes": str(payload.get("notes") or "").strip()[:ACTIVE_GOAL_NOTE_MAX_CHARS],
        "revision": revision,
    }
    if not normalized["id"]:
        normalized["id"] = "active-" + _sha256_text(
            f"{normalized['created_at']}|{normalized['objective']}"
        )[:12]
    return normalized


def _read_active_goal_snapshot(repo: Path) -> dict[str, Any] | None:
    path = active_goal_path(repo)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return {
                "path": path,
                "text": "",
                "payload": None,
                "etag": "",
                "read_error": True,
            }
    if len(text) > ACTIVE_GOAL_CONTENT_MAX_CHARS:
        return {
            "path": path,
            "text": text,
            "payload": None,
            "etag": _sha256_text(text),
            "read_error": True,
            "error": "active goal file is too large",
        }
    try:
        raw = json.loads(text) if text.strip() else {}
    except Exception as exc:
        return {
            "path": path,
            "text": text,
            "payload": None,
            "etag": _sha256_text(text),
            "read_error": True,
            "error": str(exc),
        }
    payload = _normalize_goal_payload(raw if isinstance(raw, dict) else {})
    return {
        "path": path,
        "text": text,
        "payload": payload,
        "etag": _sha256_text(text),
        "read_error": False,
    }


def _payload_etag(payload: dict[str, Any]) -> str:
    return _sha256_text(_canonical_json(payload))


def _write_active_goal(repo: Path, payload: dict[str, Any], *, action: str) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    ensure_work_dir(repo_root)
    path = active_goal_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_goal_payload(payload)
    atomic_write_json(path, normalized)
    written_snapshot = _read_active_goal_snapshot(repo_root)
    written = dict((written_snapshot or {}).get("payload") or normalized)
    etag = str((written_snapshot or {}).get("etag") or _payload_etag(written))
    _append_active_goal_event(repo_root, action=action, goal=written, etag=etag)
    return _status_from_snapshot(repo_root, written_snapshot)


def _append_active_goal_event(repo: Path, *, action: str, goal: dict[str, Any], etag: str = "") -> None:
    path = active_goal_events_path(repo)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        budgets = goal.get("budgets") if isinstance(goal.get("budgets"), dict) else {}
        usage = goal.get("usage") if isinstance(goal.get("usage"), dict) else {}
        checkpoints = goal.get("checkpoints") if isinstance(goal.get("checkpoints"), list) else []
        checkpoint_progress = _checkpoint_progress([item for item in checkpoints if isinstance(item, dict)])
        objective = str(goal.get("objective") or "")
        event = {
            "ts": now_iso(),
            "action": str(action or "").strip()[:80],
            "goal_id": str(goal.get("id") or ""),
            "status": str(goal.get("status") or ""),
            "mode": str(goal.get("mode") or ""),
            "revision": int(goal.get("revision") or 0),
            "etag": etag,
            "objective": objective[:500],
            "objective_hash": _sha256_text(objective) if objective else "",
            "template_key": str((goal.get("template") if isinstance(goal.get("template"), dict) else {}).get("key") or ""),
            "templateKey": str((goal.get("template") if isinstance(goal.get("template"), dict) else {}).get("key") or ""),
            "autonomy_preset_key": str(
                (goal.get("autonomy_preset") if isinstance(goal.get("autonomy_preset"), dict) else {}).get("key") or ""
            ),
            "autonomyPresetKey": str(
                (goal.get("autonomy_preset") if isinstance(goal.get("autonomy_preset"), dict) else {}).get("key") or ""
            ),
            "budgets": {
                "token_budget": _coerce_positive_int(budgets.get("token_budget")),
                "time_budget_seconds": _coerce_positive_int(budgets.get("time_budget_seconds")),
                "cycle_budget": _coerce_positive_int(budgets.get("cycle_budget")),
            },
            "usage": {
                "tokens_used": _coerce_positive_int(usage.get("tokens_used")),
                "time_used_seconds": _coerce_positive_int(usage.get("time_used_seconds")),
                "cycles_used": _coerce_positive_int(usage.get("cycles_used")),
            },
            "checkpoint_progress": checkpoint_progress,
            "checkpointProgress": checkpoint_progress,
            "completion_evidence_count": len(goal.get("completion_evidence") or [])
            if isinstance(goal.get("completion_evidence"), list)
            else 0,
            "completionEvidenceCount": len(goal.get("completion_evidence") or [])
            if isinstance(goal.get("completion_evidence"), list)
            else 0,
        }
        with path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        pass


def propose_goals_from_active_goal(
    repo: Path,
    *,
    level: str = "P0",
    rationale: str = "",
) -> dict[str, Any]:
    """Create a proposal artifact for adding GOALS.md items from active goal.

    This deliberately does not mutate .doc/GOALS.md. Applying a proposal is an
    operator-confirmed workflow outside this deterministic bridge.
    """
    repo_root = Path(repo).expanduser().resolve()
    status = build_active_goal_status(repo_root)
    if not status.get("active"):
        raise ActiveGoalError("No active goal exists for GOALS proposal.")
    goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
    objective = str(goal.get("objective") or "").strip()
    if not objective:
        raise ActiveGoalError("Active goal objective is required for GOALS proposal.")
    normalized_level = str(level or "P0").strip().upper()
    if normalized_level not in {"P0", "P1"}:
        normalized_level = "P0"
    mode = normalize_active_goal_mode(goal.get("mode"))
    proposal = {
        "level": normalized_level,
        "text": objective,
        "source": "active_goal",
        "active_goal_id": str(goal.get("id") or ""),
        "activeGoalId": str(goal.get("id") or ""),
        "mode": mode,
        "requires_operator_confirmation": True,
        "requiresOperatorConfirmation": True,
        "allowed_actions_after_confirmation": ["add"],
        "allowedActionsAfterConfirmation": ["add"],
        "forbidden_without_confirmation": ["add", "downgrade", "delete", "complete"],
        "forbiddenWithoutConfirmation": ["add", "downgrade", "delete", "complete"],
    }
    if rationale:
        proposal["rationale"] = str(rationale or "").strip()[:ACTIVE_GOAL_NOTE_MAX_CHARS]
    payload = {
        "schema_version": 1,
        "kind": "active_goal_goals_proposal",
        "generated_at": now_iso(),
        "active_goal_id": str(goal.get("id") or ""),
        "activeGoalId": str(goal.get("id") or ""),
        "active_goal_objective": objective,
        "activeGoalObjective": objective,
        "policy": "proposal_only_operator_confirmation_required",
        "does_not_mutate_goals_md": True,
        "doesNotMutateGoalsMd": True,
        "requires_operator_confirmation": True,
        "requiresOperatorConfirmation": True,
        "goals_path": (repo_root / ".doc" / "GOALS.md").as_posix(),
        "goalsPath": (repo_root / ".doc" / "GOALS.md").as_posix(),
        "proposal_path": active_goal_goals_proposal_path(repo_root).as_posix(),
        "proposalPath": active_goal_goals_proposal_path(repo_root).as_posix(),
        "proposals": [proposal],
    }
    ensure_work_dir(repo_root)
    path = active_goal_goals_proposal_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    _append_active_goal_event(repo_root, action="goals_proposal", goal=goal, etag=str(status.get("etag") or ""))
    return payload


def _assert_etag(snapshot: dict[str, Any] | None, expected_etag: str | None) -> None:
    expected = str(expected_etag or "").strip()
    if not expected:
        return
    current = str((snapshot or {}).get("etag") or "").strip()
    if current != expected:
        raise ActiveGoalConflict("Active goal changed after it was read; reload and retry.")


def _status_from_snapshot(repo: Path, snapshot: dict[str, Any] | None, *, include_raw: bool = False) -> dict[str, Any]:
    path = active_goal_path(repo)
    if snapshot is None:
        namespaced_status = _active_goal_namespaced_status(None, state="missing")
        progress = _active_goal_progress({}, namespaced_status)
        return {
            "ok": True,
            "exists": False,
            "active": False,
            "state": "missing",
            "message": "No active goal is set.",
            "terminal_reason": namespaced_status["terminal_reason"],
            "terminalReason": namespaced_status["terminalReason"],
            "active_goal_status": namespaced_status,
            "activeGoalStatus": namespaced_status,
            "path": path.as_posix(),
            "etag": "",
            "revision": 0,
            "goal": {},
            "progress": progress,
            "active_goal_progress": progress,
            "activeGoalProgress": progress,
            "completion_policy": _completion_policy(),
            "completionPolicy": _completion_policy(),
            "templates": list_active_goal_templates(),
            "activeGoalTemplates": list_active_goal_templates(),
            "autonomy_presets": list_active_goal_autonomy_presets(),
            "autonomyPresets": list_active_goal_autonomy_presets(),
            "pm_injection": _pm_injection_status(False),
            "pmInjection": _pm_injection_status(False),
        }
    if snapshot.get("read_error"):
        namespaced_status = _active_goal_namespaced_status(None, state="error")
        progress = _active_goal_progress({}, namespaced_status)
        return {
            "ok": False,
            "exists": True,
            "active": False,
            "state": "error",
            "message": str(snapshot.get("error") or "Active goal could not be read."),
            "terminal_reason": namespaced_status["terminal_reason"],
            "terminalReason": namespaced_status["terminalReason"],
            "active_goal_status": namespaced_status,
            "activeGoalStatus": namespaced_status,
            "path": path.as_posix(),
            "etag": str(snapshot.get("etag") or ""),
            "revision": 0,
            "goal": {},
            "progress": progress,
            "active_goal_progress": progress,
            "activeGoalProgress": progress,
            "completion_policy": _completion_policy(),
            "completionPolicy": _completion_policy(),
            "templates": list_active_goal_templates(),
            "activeGoalTemplates": list_active_goal_templates(),
            "autonomy_presets": list_active_goal_autonomy_presets(),
            "autonomyPresets": list_active_goal_autonomy_presets(),
            "pm_injection": _pm_injection_status(False),
            "pmInjection": _pm_injection_status(False),
        }
    goal = dict(snapshot.get("payload") or {})
    active = str(goal.get("status") or "") == "active" and bool(str(goal.get("objective") or "").strip())
    state = str(goal.get("status") or "missing").strip() or "missing"
    namespaced_status = _active_goal_namespaced_status(goal, state=state)
    progress = _active_goal_progress(goal, namespaced_status)
    status = {
        "ok": True,
        "exists": True,
        "active": active,
        "state": state,
        "message": "Active goal is ready for subordinate PM context injection." if active else f"Active goal is {state}.",
        "terminal_reason": namespaced_status["terminal_reason"],
        "terminalReason": namespaced_status["terminalReason"],
        "active_goal_status": namespaced_status,
        "activeGoalStatus": namespaced_status,
        "path": path.as_posix(),
        "etag": str(snapshot.get("etag") or _payload_etag(goal)),
        "revision": int(goal.get("revision") or 0),
        "goal": goal,
        "progress": progress,
        "active_goal_progress": progress,
        "activeGoalProgress": progress,
        "completion_policy": _completion_policy(),
        "completionPolicy": _completion_policy(),
        "templates": list_active_goal_templates(),
        "activeGoalTemplates": list_active_goal_templates(),
        "autonomy_presets": list_active_goal_autonomy_presets(),
        "autonomyPresets": list_active_goal_autonomy_presets(),
        "pm_injection": _pm_injection_status(active),
        "pmInjection": _pm_injection_status(active),
    }
    if include_raw:
        status["raw_text"] = str(snapshot.get("text") or "")
        status["rawText"] = str(snapshot.get("text") or "")
    return status


def _budget_pair(goal: dict[str, Any], budget_key: str, usage_key: str) -> tuple[int, int, bool]:
    budgets = goal.get("budgets") if isinstance(goal.get("budgets"), dict) else {}
    usage = goal.get("usage") if isinstance(goal.get("usage"), dict) else {}
    budget = _coerce_positive_int(budgets.get(budget_key))
    used = _coerce_positive_int(usage.get(usage_key))
    return budget, used, bool(budget > 0 and used >= budget)


def _progress_item(budget: int, used: int, *, unit: str) -> dict[str, Any]:
    exhausted = bool(budget > 0 and used >= budget)
    remaining = max(0, budget - used) if budget > 0 else 0
    percent = min(100.0, round((used / budget) * 100.0, 2)) if budget > 0 else None
    return {
        "unit": unit,
        "budget": budget,
        "used": used,
        "remaining": remaining,
        "percent": percent,
        "exhausted": exhausted,
        "bounded": bool(budget > 0),
    }


def _active_goal_progress(goal: dict[str, Any], namespaced_status: dict[str, Any]) -> dict[str, Any]:
    budget_status = (
        namespaced_status.get("budget_status")
        if isinstance(namespaced_status.get("budget_status"), dict)
        else namespaced_status.get("budgetStatus")
    )
    budget_status = budget_status if isinstance(budget_status, dict) else {}
    token = _progress_item(
        _coerce_positive_int(budget_status.get("token_budget") or budget_status.get("tokenBudget")),
        _coerce_positive_int(budget_status.get("tokens_used") or budget_status.get("tokensUsed")),
        unit="tokens",
    )
    time_used = _coerce_positive_int(budget_status.get("time_used_seconds") or budget_status.get("timeUsedSeconds"))
    time_item = _progress_item(
        _coerce_positive_int(budget_status.get("time_budget_seconds") or budget_status.get("timeBudgetSeconds")),
        time_used,
        unit="seconds",
    )
    cycle = _progress_item(
        _coerce_positive_int(budget_status.get("cycle_budget") or budget_status.get("cycleBudget")),
        _coerce_positive_int(budget_status.get("cycles_used") or budget_status.get("cyclesUsed")),
        unit="cycles",
    )
    evidence = goal.get("completion_evidence") if isinstance(goal.get("completion_evidence"), list) else goal.get("completionEvidence")
    evidence_count = len(evidence) if isinstance(evidence, list) else 0
    checkpoints = goal.get("checkpoints") if isinstance(goal.get("checkpoints"), list) else []
    checkpoint_progress = _checkpoint_progress([item for item in checkpoints if isinstance(item, dict)])
    state = str(goal.get("status") or namespaced_status.get("state") or "missing").strip() or "missing"
    mode = normalize_active_goal_mode(goal.get("mode"))
    cycle_label = f"{cycle['used']}/{cycle['budget']}" if cycle["bounded"] else f"{cycle['used']}/unbounded"
    token_label = f"{token['used']}/{token['budget']}" if token["bounded"] else f"{token['used']}/unbounded"
    time_label = f"{time_item['used']}/{time_item['budget']}s" if time_item["bounded"] else f"{time_item['used']}s/unbounded"
    checkpoint_label = (
        f" checkpoints={checkpoint_progress['completed']}/{checkpoint_progress['total']}"
        if checkpoint_progress["total"]
        else ""
    )
    summary = (
        f"state={state} mode={mode} cycles={cycle_label} tokens={token_label} time={time_label} "
        f"evidence={evidence_count}{checkpoint_label}"
    )
    return {
        "state": state,
        "mode": mode,
        "summary": summary,
        "cycle": cycle,
        "cycles": cycle,
        "token": token,
        "tokens": token,
        "time": time_item,
        "time_seconds": time_item,
        "timeSeconds": time_item,
        "completion_evidence_count": evidence_count,
        "completionEvidenceCount": evidence_count,
        "checkpoint_progress": checkpoint_progress,
        "checkpointProgress": checkpoint_progress,
        "budget_exhausted": bool(token["exhausted"] or cycle["exhausted"] or time_item["exhausted"]),
        "budgetExhausted": bool(token["exhausted"] or cycle["exhausted"] or time_item["exhausted"]),
        "terminal_reason": str(namespaced_status.get("terminal_reason") or ""),
        "terminalReason": str(namespaced_status.get("terminalReason") or namespaced_status.get("terminal_reason") or ""),
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
    }


def _active_goal_namespaced_status(goal: dict[str, Any] | None, *, state: str) -> dict[str, Any]:
    goal_payload = dict(goal or {})
    objective = str(goal_payload.get("objective") or "").strip()
    status = str(goal_payload.get("status") or state or "missing").strip().lower()
    mode = normalize_active_goal_mode(goal_payload.get("mode"))
    mode_policy = active_goal_mode_policy(mode)
    token_budget, tokens_used, token_exhausted = _budget_pair(goal_payload, "token_budget", "tokens_used")
    time_budget, time_used, time_expired = _budget_pair(goal_payload, "time_budget_seconds", "time_used_seconds")
    cycle_budget, cycles_used, cycle_exhausted = _budget_pair(goal_payload, "cycle_budget", "cycles_used")

    terminal_reason = ""
    if state == "error":
        terminal_reason = ACTIVE_GOAL_REASON_ERROR
    elif status == "completed":
        terminal_reason = ACTIVE_GOAL_REASON_COMPLETED
    elif status == "canceled":
        terminal_reason = ACTIVE_GOAL_REASON_CANCELED
    elif not objective:
        terminal_reason = ACTIVE_GOAL_REASON_MISSING_OBJECTIVE
    elif time_expired:
        terminal_reason = ACTIVE_GOAL_REASON_TIME_BUDGET_EXPIRED
    elif token_exhausted:
        terminal_reason = ACTIVE_GOAL_REASON_TOKEN_BUDGET_EXHAUSTED
    elif cycle_exhausted:
        terminal_reason = ACTIVE_GOAL_REASON_CYCLE_BUDGET_EXHAUSTED

    active = bool(status == "active" and objective)
    terminal = bool(terminal_reason and terminal_reason != ACTIVE_GOAL_REASON_ACTIVE)
    budget_status = {
        "token_budget": token_budget,
        "tokenBudget": token_budget,
        "tokens_used": tokens_used,
        "tokensUsed": tokens_used,
        "token_budget_exhausted": token_exhausted,
        "tokenBudgetExhausted": token_exhausted,
        "time_budget_seconds": time_budget,
        "timeBudgetSeconds": time_budget,
        "time_used_seconds": time_used,
        "timeUsedSeconds": time_used,
        "time_budget_expired": time_expired,
        "timeBudgetExpired": time_expired,
        "cycle_budget": cycle_budget,
        "cycleBudget": cycle_budget,
        "cycles_used": cycles_used,
        "cyclesUsed": cycles_used,
        "cycle_budget_exhausted": cycle_exhausted,
        "cycleBudgetExhausted": cycle_exhausted,
        "budget_exhausted": bool(token_exhausted or time_expired or cycle_exhausted),
        "budgetExhausted": bool(token_exhausted or time_expired or cycle_exhausted),
    }
    return {
        "namespace": "active_goal",
        "state": status if status else state,
        "mode": mode,
        "mode_policy": mode_policy,
        "modePolicy": mode_policy,
        "active": active,
        "terminal": terminal,
        "terminal_reason": terminal_reason,
        "terminalReason": terminal_reason,
        "budget_status": budget_status,
        "budgetStatus": budget_status,
        "stop_priority_unchanged": True,
        "stopPriorityUnchanged": True,
    }


def _pm_injection_status(enabled: bool) -> dict[str, Any]:
    return {
        "enabled": bool(enabled),
        "state": "ready" if enabled else "missing",
        "template_field": "active_goal_block",
        "templateField": "active_goal_block",
        "priority_policy": "goals_first",
        "priorityPolicy": "goals_first",
        "does_not_override_goals": True,
        "doesNotOverrideGoals": True,
        "summary": "Active goal is runtime operator intent only; it cannot override GOALS.md gating.",
    }


def build_active_goal_status(repo: Path, *, include_raw: bool = False) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    return _status_from_snapshot(repo_root, _read_active_goal_snapshot(repo_root), include_raw=include_raw)


def active_goal_budget_stop_reason(status: dict[str, Any] | None) -> str:
    if not isinstance(status, dict):
        return ""
    reason = str(status.get("terminal_reason") or status.get("terminalReason") or "").strip()
    return reason if reason in ACTIVE_GOAL_BUDGET_STOP_REASONS else ""


def build_active_goal_budget_stop_reason(repo: Path) -> str:
    return active_goal_budget_stop_reason(build_active_goal_status(repo))


def read_active_goal(repo: Path) -> dict[str, Any] | None:
    snapshot = _read_active_goal_snapshot(Path(repo).expanduser().resolve())
    if not snapshot or snapshot.get("read_error"):
        return None
    return dict(snapshot.get("payload") or {})


def _read_json_dict(path: Path, *, max_chars: int = 1_000_000) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if len(text) > max_chars:
            return {}
        payload = json.loads(text) if text.strip() else {}
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_active_goal_events(repo: Path, *, limit: int = 500) -> list[dict[str, Any]]:
    path = active_goal_events_path(repo)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    for line in lines[-max(1, int(limit)):]:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _goal_id_from_context(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get("active_goal_id") or value.get("activeGoalId") or value.get("goal_id") or value.get("goalId") or "").strip()


def _active_goal_id_for_intelligence(repo: Path, explicit_goal_id: str = "") -> str:
    if str(explicit_goal_id or "").strip():
        return str(explicit_goal_id or "").strip()
    status = build_active_goal_status(repo)
    goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
    goal_id = str(goal.get("id") or "").strip()
    if goal_id:
        return goal_id
    for event in reversed(_read_active_goal_events(repo, limit=200)):
        goal_id = str(event.get("goal_id") or event.get("goalId") or "").strip()
        if goal_id:
            return goal_id
    return ""


def _recommendation(
    *,
    source: str,
    objective: str,
    reason: str,
    score: float,
    template_key: str = "",
    preset_key: str = "one_shot",
    mode: str = "adaptive",
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    objective_text = _safe_public_text(objective, max_chars=ACTIVE_GOAL_OBJECTIVE_MAX_CHARS)
    recommendation_id = f"rec-{_slug(source)}-{_sha256_text(source + '|' + objective_text)[:10]}"
    return {
        "id": recommendation_id,
        "source": source,
        "source_kind": source,
        "sourceKind": source,
        "objective": objective_text,
        "reason": _safe_public_text(reason, max_chars=1000),
        "score": round(float(score), 3),
        "mode": normalize_active_goal_mode(mode),
        "template_key": template_key,
        "templateKey": template_key,
        "autonomy_preset_key": preset_key,
        "autonomyPresetKey": preset_key,
        "evidence": list(evidence or []),
        "requires_operator_confirmation": True,
        "requiresOperatorConfirmation": True,
        "does_not_mutate_goals_md": True,
        "doesNotMutateGoalsMd": True,
        "policy": "recommendation_only_operator_confirmation_required",
    }


def recommend_next_active_goals(repo: Path, *, limit: int = 5) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    recommendations: list[dict[str, Any]] = []

    def add(item: dict[str, Any]) -> None:
        objective = str(item.get("objective") or "").strip().lower()
        if not objective:
            return
        if any(str(existing.get("objective") or "").strip().lower() == objective for existing in recommendations):
            return
        recommendations.append(item)

    try:
        from .goals import parse_goals_completion, read_goals

        goal_path, goals_text = read_goals(repo_root)
        completion = parse_goals_completion(goals_text, completion_level="all")
        for priority, score in (("p0", 0.95), ("p1", 0.75)):
            items = completion.get(f"unmet_{priority}_items") if isinstance(completion.get(f"unmet_{priority}_items"), list) else []
            if not items:
                continue
            first = items[0] if isinstance(items[0], dict) else {}
            goal_text = str(first.get("goal_text") or first.get("text") or "").strip()
            if goal_text:
                add(
                    _recommendation(
                        source=f"unmet_{priority}_goals",
                        objective=goal_text,
                        reason=f"Next unchecked {priority.upper()} GOALS.md item.",
                        score=score,
                        template_key="feature_build" if priority == "p0" else "exploratory_improvement",
                        preset_key="one_shot",
                        mode="adaptive",
                        evidence=[
                            {
                                "kind": "goals_md",
                                "ref": goal_path.as_posix() if goal_path else ".doc/GOALS.md",
                                "goal_ref": str(first.get("goal_ref") or ""),
                            }
                        ],
                    )
                )
    except Exception:
        pass

    try:
        pr_root = repo_root / AGENT_WORK_DIR / "pr_queue"
        for path in sorted(pr_root.glob("*.json"))[:100]:
            packet = _read_json_dict(path)
            if not packet:
                continue
            packet_id = str(packet.get("id") or path.stem).strip()
            status = str(packet.get("validation_status") or packet.get("validationStatus") or packet.get("status") or "").strip()
            need = str(packet.get("need") or packet.get("need_label") or packet.get("needLabel") or "").strip()
            if status not in {"validation_failed", "blocked_env", "validation_pending"} and "validation" not in need.lower():
                continue
            task_ids = packet.get("task_ids") if isinstance(packet.get("task_ids"), list) else packet.get("taskIds")
            task_label = ", ".join(str(item) for item in task_ids[:3]) if isinstance(task_ids, list) else packet_id
            add(
                _recommendation(
                    source="pr_queue_blocker",
                    objective=f"Resolve PR queue blocker for {task_label}",
                    reason=f"Queued PR packet {packet_id} needs validation or operator attention.",
                    score=0.9,
                    template_key="test_hardening",
                    preset_key="one_shot",
                    mode="strict",
                    evidence=[{"kind": "pr_packet", "ref": path.relative_to(repo_root).as_posix(), "packet_id": packet_id}],
                )
            )
    except Exception:
        pass

    try:
        for path in _recent_validation_paths(repo_root, limit=100):
            payload = _read_json_dict(path)
            status = str(payload.get("status") or payload.get("validation_status") or payload.get("validationStatus") or "").strip()
            if status not in {"failed", "validation_failed", "blocked_env"}:
                continue
            task_id = str(payload.get("task_id") or payload.get("taskId") or "").strip() or path.parent.parent.name
            reason = str(payload.get("reason") or payload.get("validation_reason") or payload.get("validationReason") or status).strip()
            add(
                _recommendation(
                    source="failing_validation",
                    objective=f"Fix failing validation for {task_id}",
                    reason=reason,
                    score=0.86,
                    template_key="bug_fix",
                    preset_key="one_shot",
                    mode="strict",
                    evidence=[{"kind": "validation_artifact", "ref": path.relative_to(repo_root).as_posix()}],
                )
            )
    except Exception:
        pass

    try:
        from .experience import list_lessons

        for lesson in list_lessons(repo_root)[:50]:
            kind = str(lesson.get("kind") or "").strip()
            lesson_text = str(lesson.get("lesson") or "").strip()
            if not lesson_text:
                continue
            if kind.startswith("active_goal") or "validation" in kind or "budget" in kind:
                add(
                    _recommendation(
                        source="experience_db_lesson",
                        objective=f"Apply Experience DB lesson: {lesson_text[:160]}",
                        reason=f"Lesson kind={kind} confidence={lesson.get('confidence') or 0}",
                        score=0.72,
                        template_key="test_hardening" if "validation" in kind else "refactor",
                        preset_key="one_shot",
                        mode="adaptive",
                        evidence=[{"kind": "experience_lesson", "lesson_id": str(lesson.get("id") or "")}],
                    )
                )
    except Exception:
        pass

    try:
        from .todo import build_todo_status

        todo_status = build_todo_status(repo_root, include_preview=True)
        if str(todo_status.get("freshness") or "") == "stale":
            preview = todo_status.get("preview") if isinstance(todo_status.get("preview"), dict) else {}
            lines = preview.get("lines") if isinstance(preview.get("lines"), list) else []
            first_priority = next((str(line).strip("- []\t ") for line in lines if str(line).strip().startswith("-")), "")
            add(
                _recommendation(
                    source="stale_todo_priority",
                    objective=f"Refresh stale TODO priority{': ' + first_priority if first_priority else ''}",
                    reason="The active TODO is stale and should be reconciled before more autonomy.",
                    score=0.68,
                    template_key="documentation",
                    preset_key="one_shot",
                    mode="strict",
                    evidence=[{"kind": "todo", "ref": str(todo_status.get("active_relative_path") or "")}],
                )
            )
    except Exception:
        pass

    ranked = sorted(recommendations, key=lambda item: -float(item.get("score") or 0.0))[: max(1, int(limit))]
    return {
        "ok": True,
        "generated_at": now_iso(),
        "recommendations": ranked,
        "items": ranked,
        "policy": "recommendation_only_operator_confirmation_required",
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
    }


def _recent_validation_paths(repo: Path, *, limit: int = 200) -> list[Path]:
    roots = [repo / AGENT_WORK_DIR / "agent_runs", repo / ".doc" / "agent_runs"]
    paths: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        try:
            paths.extend(path for path in root.rglob("validation.json") if path.is_file())
        except Exception:
            continue
    paths.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0, reverse=True)
    return paths[: max(1, int(limit))]


def build_active_goal_timeline(repo: Path, *, goal_id: str = "", limit: int = 200) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    selected_goal_id = _active_goal_id_for_intelligence(repo_root, goal_id)
    items: list[dict[str, Any]] = []

    for event in _read_active_goal_events(repo_root, limit=limit):
        event_goal_id = str(event.get("goal_id") or event.get("goalId") or "").strip()
        if selected_goal_id and event_goal_id and event_goal_id != selected_goal_id:
            continue
        items.append(
            {
                "ts": str(event.get("ts") or ""),
                "kind": "goal_event",
                "label": str(event.get("action") or ""),
                "goal_id": event_goal_id,
                "goalId": event_goal_id,
                "objective": _safe_public_text(event.get("objective"), max_chars=500),
                "status": str(event.get("status") or ""),
                "mode": str(event.get("mode") or ""),
                "revision": int(event.get("revision") or 0),
                "usage": dict(event.get("usage") or {}) if isinstance(event.get("usage"), dict) else {},
                "budgets": dict(event.get("budgets") or {}) if isinstance(event.get("budgets"), dict) else {},
                "checkpoint_progress": dict(event.get("checkpoint_progress") or {})
                if isinstance(event.get("checkpoint_progress"), dict)
                else {},
                "checkpointProgress": dict(event.get("checkpointProgress") or {})
                if isinstance(event.get("checkpointProgress"), dict)
                else {},
            }
        )

    try:
        from .task_history import query_history

        for row in query_history(repo_root, max_items=limit):
            row_goal_id = str(row.get("active_goal_id") or "").strip()
            if selected_goal_id and row_goal_id != selected_goal_id:
                continue
            items.append(
                {
                    "ts": str(row.get("recorded_at") or ""),
                    "kind": "task_decomposition",
                    "label": str(row.get("task_id") or ""),
                    "goal_id": row_goal_id,
                    "goalId": row_goal_id,
                    "task_id": str(row.get("task_id") or ""),
                    "taskId": str(row.get("task_id") or ""),
                    "title": _safe_public_text(row.get("title"), max_chars=300),
                    "status": str(row.get("status") or row.get("task_status") or ""),
                    "run_id": str(row.get("run_id") or ""),
                    "runId": str(row.get("run_id") or ""),
                }
            )
    except Exception:
        pass

    for path in _recent_validation_paths(repo_root, limit=limit):
        payload = _read_json_dict(path)
        context = payload.get("active_goal_context") if isinstance(payload.get("active_goal_context"), dict) else payload.get("activeGoalContext")
        payload_goal_id = _goal_id_from_context(context)
        if selected_goal_id and payload_goal_id != selected_goal_id:
            continue
        if not payload_goal_id:
            continue
        items.append(
            {
                "ts": str(payload.get("recorded_at") or payload.get("updated_at") or payload.get("created_at") or ""),
                "kind": "validation_evidence",
                "label": str(payload.get("status") or payload.get("validation_status") or payload.get("validationStatus") or ""),
                "goal_id": payload_goal_id,
                "goalId": payload_goal_id,
                "task_id": str(payload.get("task_id") or payload.get("taskId") or ""),
                "taskId": str(payload.get("task_id") or payload.get("taskId") or ""),
                "status": str(payload.get("status") or payload.get("validation_status") or payload.get("validationStatus") or ""),
                "artifact": path.relative_to(repo_root).as_posix(),
            }
        )

    pr_root = repo_root / AGENT_WORK_DIR / "pr_queue"
    for path in sorted(pr_root.glob("*.json"))[: max(1, int(limit))] if pr_root.exists() else []:
        packet = _read_json_dict(path)
        context = packet.get("active_goal_context") if isinstance(packet.get("active_goal_context"), dict) else packet.get("activeGoalContext")
        packet_goal_id = _goal_id_from_context(context)
        if selected_goal_id and packet_goal_id != selected_goal_id:
            continue
        if not packet_goal_id:
            continue
        items.append(
            {
                "ts": str(packet.get("updated_at") or packet.get("updatedAt") or packet.get("created_at") or packet.get("createdAt") or ""),
                "kind": "pr_packet",
                "label": str(packet.get("id") or path.stem),
                "goal_id": packet_goal_id,
                "goalId": packet_goal_id,
                "packet_id": str(packet.get("id") or path.stem),
                "packetId": str(packet.get("id") or path.stem),
                "status": str(packet.get("status") or ""),
                "validation_status": str(packet.get("validation_status") or packet.get("validationStatus") or ""),
                "validationStatus": str(packet.get("validation_status") or packet.get("validationStatus") or ""),
            }
        )

    items.sort(key=lambda item: str(item.get("ts") or ""))
    if limit > 0:
        items = items[-int(limit):]
    return {
        "ok": True,
        "goal_id": selected_goal_id,
        "goalId": selected_goal_id,
        "items": items,
        "count": len(items),
        "policy": "timeline is evidence for operator review, not merge approval or GOALS completion",
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
    }


def build_active_goal_analytics(repo: Path) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    events = _read_active_goal_events(repo_root, limit=2000)
    by_goal: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        goal_id = str(event.get("goal_id") or event.get("goalId") or "").strip()
        if not goal_id:
            continue
        by_goal.setdefault(goal_id, []).append(event)
    completed = 0
    canceled = 0
    exhausted = 0
    cycles_to_completion: list[int] = []
    validation_failures: dict[str, int] = {}
    manual_intervention = 0
    for goal_events in by_goal.values():
        actions = {str(event.get("action") or "") for event in goal_events}
        terminal = next((event for event in reversed(goal_events) if str(event.get("status") or "") in {"completed", "canceled"}), goal_events[-1])
        status = str(terminal.get("status") or "")
        if status == "completed":
            completed += 1
            usage = terminal.get("usage") if isinstance(terminal.get("usage"), dict) else {}
            cycles_to_completion.append(_coerce_positive_int(usage.get("cycles_used")))
        elif status == "canceled":
            canceled += 1
        if any(str(event.get("action") or "") in {"complete", "cancel", "checkpoint_complete", "import"} for event in goal_events):
            manual_intervention += 1
        for event in goal_events:
            usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
            budgets = event.get("budgets") if isinstance(event.get("budgets"), dict) else {}
            if _coerce_positive_int(budgets.get("cycle_budget")) and _coerce_positive_int(usage.get("cycles_used")) >= _coerce_positive_int(budgets.get("cycle_budget")):
                exhausted += 1
                break
        if "complete" not in actions and "cancel" not in actions:
            continue
    for path in _recent_validation_paths(repo_root, limit=500):
        payload = _read_json_dict(path)
        status = str(payload.get("status") or payload.get("validation_status") or payload.get("validationStatus") or "").strip()
        if status in {"failed", "validation_failed", "blocked_env", "no_tests_found", "tests_skipped"}:
            reason = str(payload.get("reason") or payload.get("validation_reason") or payload.get("validationReason") or status).strip()
            key = reason[:80] or status
            validation_failures[key] = validation_failures.get(key, 0) + 1
    total_terminal = completed + canceled
    success_rate = round(completed / total_terminal, 3) if total_terminal else None
    median_cycles = statistics.median(cycles_to_completion) if cycles_to_completion else None
    return {
        "ok": True,
        "generated_at": now_iso(),
        "goal_count": len(by_goal),
        "goalCount": len(by_goal),
        "completed": completed,
        "canceled": canceled,
        "success_rate": success_rate,
        "successRate": success_rate,
        "median_cycles_to_completion": median_cycles,
        "medianCyclesToCompletion": median_cycles,
        "validation_failure_reasons": validation_failures,
        "validationFailureReasons": validation_failures,
        "budget_exhaustion_count": exhausted,
        "budgetExhaustionCount": exhausted,
        "manual_intervention_count": manual_intervention,
        "manualInterventionCount": manual_intervention,
        "policy": "analytics are retrospective signals only and do not mark GOALS complete or approve merges",
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
    }


def _sanitize_goal_for_export(goal: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(goal, ensure_ascii=False))
    sanitized["objective"] = _safe_public_text(sanitized.get("objective"), max_chars=ACTIVE_GOAL_OBJECTIVE_MAX_CHARS)
    sanitized["notes"] = _safe_public_text(sanitized.get("notes"), max_chars=ACTIVE_GOAL_NOTE_MAX_CHARS)
    source = sanitized.get("source")
    if isinstance(source, dict):
        for key in ("actor", "prompt", "raw_prompt", "rawPrompt", "text", "detail"):
            if source.get(key) not in (None, "", False):
                source[key] = "[redacted]"
    for item in sanitized.get("completion_evidence") or []:
        if isinstance(item, dict):
            item["text"] = _safe_public_text(item.get("text"), max_chars=ACTIVE_GOAL_NOTE_MAX_CHARS)
            item["ref"] = _safe_public_text(item.get("ref"), max_chars=1000)
    for checkpoint in sanitized.get("checkpoints") or []:
        if not isinstance(checkpoint, dict):
            continue
        for evidence in checkpoint.get("evidence") or []:
            if isinstance(evidence, dict):
                evidence["text"] = _safe_public_text(evidence.get("text"), max_chars=ACTIVE_GOAL_NOTE_MAX_CHARS)
                evidence["ref"] = _safe_public_text(evidence.get("ref"), max_chars=1000)
    return sanitized


def export_active_goal_state(repo: Path, *, include_timeline: bool = True, include_analytics: bool = True) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    status = build_active_goal_status(repo_root)
    goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "active_goal_export",
        "exported_at": now_iso(),
        "goal": _sanitize_goal_for_export(goal),
        "status": {
            "state": str(status.get("state") or ""),
            "revision": int(status.get("revision") or 0),
            "terminal_reason": str(status.get("terminal_reason") or ""),
            "terminalReason": str(status.get("terminalReason") or ""),
            "progress": dict(status.get("progress") or {}) if isinstance(status.get("progress"), dict) else {},
        },
        "redaction_policy": {
            "raw_prompts_excluded": True,
            "rawPromptsExcluded": True,
            "raw_logs_excluded": True,
            "rawLogsExcluded": True,
            "source_actor_redacted": True,
            "sourceActorRedacted": True,
            "secret_patterns_redacted": True,
            "secretPatternsRedacted": True,
        },
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
    }
    if include_timeline:
        payload["timeline"] = build_active_goal_timeline(repo_root)
    if include_analytics:
        payload["analytics"] = build_active_goal_analytics(repo_root)
    return payload


def write_active_goal_export(repo: Path, *, include_timeline: bool = True, include_analytics: bool = True) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    payload = export_active_goal_state(repo_root, include_timeline=include_timeline, include_analytics=include_analytics)
    ensure_work_dir(repo_root)
    path = active_goal_export_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, payload)
    payload["path"] = path.as_posix()
    return payload


def import_active_goal_state(
    repo: Path,
    payload: dict[str, Any],
    *,
    replace: bool = False,
    expected_etag: str | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ActiveGoalError("Active goal import payload must be a JSON object.")
    raw_goal = payload.get("goal") if isinstance(payload.get("goal"), dict) else payload
    serialized = json.dumps(raw_goal, ensure_ascii=False).lower()
    if any(token in serialized for token in ("raw_prompt", "system_prompt", "transcript", "api_key", "password")):
        raise ActiveGoalError("Active goal import payload contains raw prompt or secret-like fields.")
    repo_root = Path(repo).expanduser().resolve()
    snapshot = _read_active_goal_snapshot(repo_root)
    _assert_etag(snapshot, expected_etag)
    current = dict((snapshot or {}).get("payload") or {})
    if current.get("status") == "active" and not replace:
        raise ActiveGoalConflict("An active goal already exists; pass replace=True to import over it.")
    goal = _normalize_goal_payload(dict(raw_goal))
    goal["source"] = {"kind": "import", "surface": "active_goal_import"}
    goal["updated_at"] = now_iso()
    goal["revision"] = _coerce_positive_int(goal.get("revision")) + 1
    return _write_active_goal(repo_root, goal, action="import")


def create_active_goal(
    repo: Path,
    objective: str,
    *,
    mode: str = "adaptive",
    token_budget: int = 0,
    time_budget_seconds: int = 0,
    cycle_budget: int = 0,
    template_key: str = "",
    autonomy_preset_key: str = "",
    checkpoints: list[dict[str, Any]] | None = None,
    source: dict[str, Any] | None = None,
    replace: bool = False,
    expected_etag: str | None = None,
) -> dict[str, Any]:
    template: dict[str, Any] = {}
    preset: dict[str, Any] = {}
    if str(template_key or "").strip():
        template = active_goal_template(template_key)
    if str(autonomy_preset_key or "").strip():
        preset = active_goal_autonomy_preset(autonomy_preset_key)
    if template and not str(autonomy_preset_key or "").strip():
        preset = active_goal_autonomy_preset(template.get("preset") or "one_shot")
    objective_text = str(objective or "").strip()
    if not objective_text and template:
        objective_text = str(template.get("objective_template") or "").strip()
    if not objective_text:
        raise ActiveGoalError("Active goal objective is required.")
    if len(objective_text) > ACTIVE_GOAL_OBJECTIVE_MAX_CHARS:
        raise ActiveGoalError(f"Active goal objective exceeds {ACTIVE_GOAL_OBJECTIVE_MAX_CHARS} characters.")
    repo_root = Path(repo).expanduser().resolve()
    snapshot = _read_active_goal_snapshot(repo_root)
    _assert_etag(snapshot, expected_etag)
    current = dict((snapshot or {}).get("payload") or {})
    if current.get("status") == "active" and not replace:
        raise ActiveGoalConflict("An active goal already exists; complete, cancel, clear, or replace it first.")
    now = now_iso()
    preset_budgets = preset.get("budgets") if isinstance(preset.get("budgets"), dict) else {}
    resolved_mode = normalize_active_goal_mode(
        template.get("mode") if template and str(mode or "").strip().lower() in {"", "adaptive"} else mode
    )
    if preset and not template and str(mode or "").strip().lower() in {"", "adaptive"}:
        resolved_mode = normalize_active_goal_mode(preset.get("mode"))
    checkpoint_payload = checkpoints if checkpoints is not None else (_template_checkpoint_specs(str(template.get("key") or "")) if template else [])
    payload = {
        "objective": objective_text,
        "status": "active",
        "mode": resolved_mode,
        "created_at": now,
        "updated_at": now,
        "source": source or {"kind": "operator", "surface": "unknown"},
        "budgets": {
            "token_budget": token_budget if token_budget else _coerce_positive_int(preset_budgets.get("token_budget")),
            "time_budget_seconds": time_budget_seconds
            if time_budget_seconds
            else _coerce_positive_int(preset_budgets.get("time_budget_seconds")),
            "cycle_budget": cycle_budget if cycle_budget else _coerce_positive_int(preset_budgets.get("cycle_budget")),
        },
        "usage": {},
        "completion_evidence": [],
        "template": {
            "key": str(template.get("key") or "").strip(),
            "label": str(template.get("label") or "").strip(),
        },
        "autonomy_preset": {
            "key": str(preset.get("key") or "").strip(),
            "label": str(preset.get("label") or "").strip(),
        },
        "checkpoints": checkpoint_payload,
        "revision": 1,
    }
    return _write_active_goal(repo_root, payload, action="create")


def update_active_goal(
    repo: Path,
    *,
    objective: str | None = None,
    mode: str | None = None,
    token_budget: int | None = None,
    time_budget_seconds: int | None = None,
    cycle_budget: int | None = None,
    notes: str | None = None,
    template_key: str | None = None,
    autonomy_preset_key: str | None = None,
    checkpoints: list[dict[str, Any]] | None = None,
    usage_delta: dict[str, Any] | None = None,
    expected_etag: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    snapshot = _read_active_goal_snapshot(repo_root)
    if not snapshot or snapshot.get("read_error"):
        raise ActiveGoalError("No readable active goal exists.")
    _assert_etag(snapshot, expected_etag)
    payload = dict(snapshot.get("payload") or {})
    if objective is not None:
        objective_text = str(objective or "").strip()
        if not objective_text:
            raise ActiveGoalError("Active goal objective cannot be empty.")
        payload["objective"] = objective_text[:ACTIVE_GOAL_OBJECTIVE_MAX_CHARS]
    if mode is not None:
        payload["mode"] = normalize_active_goal_mode(mode)
    if template_key is not None:
        template = active_goal_template(template_key) if str(template_key or "").strip() else {}
        payload["template"] = {
            "key": str(template.get("key") or "").strip(),
            "label": str(template.get("label") or "").strip(),
        }
        if template and checkpoints is None:
            payload["checkpoints"] = _template_checkpoint_specs(str(template.get("key") or ""))
    preset: dict[str, Any] = {}
    if autonomy_preset_key is not None:
        preset = active_goal_autonomy_preset(autonomy_preset_key) if str(autonomy_preset_key or "").strip() else {}
        payload["autonomy_preset"] = {
            "key": str(preset.get("key") or "").strip(),
            "label": str(preset.get("label") or "").strip(),
        }
        payload["autonomyPreset"] = dict(payload["autonomy_preset"])
        if preset and mode is None:
            payload["mode"] = normalize_active_goal_mode(preset.get("mode"))
    budgets = dict(payload.get("budgets") or {})
    preset_budgets = preset.get("budgets") if isinstance(preset.get("budgets"), dict) else {}
    if token_budget is not None:
        budgets["token_budget"] = _coerce_positive_int(token_budget)
    elif preset_budgets and not _coerce_positive_int(budgets.get("token_budget")):
        budgets["token_budget"] = _coerce_positive_int(preset_budgets.get("token_budget"))
    if time_budget_seconds is not None:
        budgets["time_budget_seconds"] = _coerce_positive_int(time_budget_seconds)
    elif preset_budgets and not _coerce_positive_int(budgets.get("time_budget_seconds")):
        budgets["time_budget_seconds"] = _coerce_positive_int(preset_budgets.get("time_budget_seconds"))
    if cycle_budget is not None:
        budgets["cycle_budget"] = _coerce_positive_int(cycle_budget)
    elif preset_budgets and not _coerce_positive_int(budgets.get("cycle_budget")):
        budgets["cycle_budget"] = _coerce_positive_int(preset_budgets.get("cycle_budget"))
    payload["budgets"] = budgets
    if checkpoints is not None:
        payload["checkpoints"] = checkpoints
    if notes is not None:
        payload["notes"] = str(notes or "").strip()[:ACTIVE_GOAL_NOTE_MAX_CHARS]
    if isinstance(usage_delta, dict):
        usage = dict(payload.get("usage") or {})
        for key in ("tokens_used", "time_used_seconds", "cycles_used"):
            if key in usage_delta:
                usage[key] = _coerce_positive_int(usage.get(key)) + _coerce_positive_int(usage_delta.get(key))
        payload["usage"] = usage
    payload["updated_at"] = now_iso()
    payload["revision"] = _coerce_positive_int(payload.get("revision")) + 1
    return _write_active_goal(repo_root, payload, action="update")


def increment_active_goal_usage(
    repo: Path,
    *,
    tokens_used: int = 0,
    time_used_seconds: int = 0,
    cycles_used: int = 0,
) -> dict[str, Any]:
    """Best-effort usage counter update for the current active goal."""
    try:
        delta = {
            "tokens_used": _coerce_positive_int(tokens_used),
            "time_used_seconds": _coerce_positive_int(time_used_seconds),
            "cycles_used": _coerce_positive_int(cycles_used),
        }
        if not any(delta.values()):
            return build_active_goal_status(repo)
        status = build_active_goal_status(repo)
        if not status.get("active"):
            return status
        return update_active_goal(repo, usage_delta=delta)
    except Exception:
        return build_active_goal_status(repo)


def complete_active_goal(
    repo: Path,
    *,
    evidence: Any = None,
    expected_etag: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    snapshot = _read_active_goal_snapshot(repo_root)
    if not snapshot or snapshot.get("read_error"):
        raise ActiveGoalError("No readable active goal exists.")
    _assert_etag(snapshot, expected_etag)
    payload = dict(snapshot.get("payload") or {})
    completion_evidence = _normalize_completion_evidence(evidence)
    payload["status"] = "completed"
    payload["completed_at"] = now_iso()
    payload["updated_at"] = payload["completed_at"]
    payload["completion_evidence"] = list(payload.get("completion_evidence") or []) + completion_evidence
    payload["revision"] = _coerce_positive_int(payload.get("revision")) + 1
    return _write_active_goal(repo_root, payload, action="complete")


def cancel_active_goal(
    repo: Path,
    *,
    reason: str = "",
    expected_etag: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    snapshot = _read_active_goal_snapshot(repo_root)
    if not snapshot or snapshot.get("read_error"):
        raise ActiveGoalError("No readable active goal exists.")
    _assert_etag(snapshot, expected_etag)
    payload = dict(snapshot.get("payload") or {})
    payload["status"] = "canceled"
    payload["canceled_at"] = now_iso()
    payload["updated_at"] = payload["canceled_at"]
    if reason:
        payload["completion_evidence"] = list(payload.get("completion_evidence") or []) + _normalize_evidence(
            {"kind": "cancel_reason", "text": reason}
        )
    payload["revision"] = _coerce_positive_int(payload.get("revision")) + 1
    return _write_active_goal(repo_root, payload, action="cancel")


def set_active_goal_checkpoints(
    repo: Path,
    checkpoints: list[dict[str, Any]],
    *,
    expected_etag: str | None = None,
) -> dict[str, Any]:
    if not isinstance(checkpoints, list) or not checkpoints:
        raise ActiveGoalError("At least one active-goal checkpoint is required.")
    return update_active_goal(repo, checkpoints=checkpoints, expected_etag=expected_etag)


def complete_active_goal_checkpoint(
    repo: Path,
    checkpoint_id: str,
    *,
    evidence: Any,
    resume_point: Any = None,
    expected_etag: str | None = None,
) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    snapshot = _read_active_goal_snapshot(repo_root)
    if not snapshot or snapshot.get("read_error"):
        raise ActiveGoalError("No readable active goal exists.")
    _assert_etag(snapshot, expected_etag)
    payload = dict(snapshot.get("payload") or {})
    checkpoints = payload.get("checkpoints") if isinstance(payload.get("checkpoints"), list) else []
    normalized_id = str(checkpoint_id or "").strip()
    if not normalized_id:
        raise ActiveGoalError("Active-goal checkpoint id is required.")
    evidence_items = _normalize_completion_evidence(evidence)
    found = False
    next_checkpoints: list[dict[str, Any]] = []
    activate_next = False
    now = now_iso()
    for raw in checkpoints:
        item = _normalize_checkpoint(raw, index=len(next_checkpoints))
        if item["id"] == normalized_id:
            found = True
            item["status"] = "completed"
            item["completed_at"] = now
            item["updated_at"] = now
            item["evidence"] = list(item.get("evidence") or []) + evidence_items
            item["resume_point"] = _normalize_resume_point(resume_point)
            item["resumePoint"] = _normalize_resume_point(resume_point)
            activate_next = True
        elif activate_next and item["status"] == "pending":
            item["status"] = "active"
            item["updated_at"] = now
            activate_next = False
        next_checkpoints.append(item)
    if not found:
        raise ActiveGoalError(f"Active-goal checkpoint not found: {checkpoint_id}")
    payload["checkpoints"] = next_checkpoints
    payload["updated_at"] = now
    payload["revision"] = _coerce_positive_int(payload.get("revision")) + 1
    return _write_active_goal(repo_root, payload, action="checkpoint_complete")


def clear_active_goal(repo: Path, *, expected_etag: str | None = None) -> dict[str, Any]:
    repo_root = Path(repo).expanduser().resolve()
    snapshot = _read_active_goal_snapshot(repo_root)
    _assert_etag(snapshot, expected_etag)
    goal = dict((snapshot or {}).get("payload") or {})
    path = active_goal_path(repo_root)
    if path.exists():
        path.unlink()
    _append_active_goal_event(repo_root, action="clear", goal=goal, etag=str((snapshot or {}).get("etag") or ""))
    return build_active_goal_status(repo_root)


def format_active_goal_block(status: dict[str, Any] | None) -> str:
    """Format active goal for prompt injection.

    The block is advisory and explicitly subordinate to GOALS.md.
    """
    if not isinstance(status, dict) or not status.get("active"):
        return "(none)"
    goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
    budgets = goal.get("budgets") if isinstance(goal.get("budgets"), dict) else {}
    usage = goal.get("usage") if isinstance(goal.get("usage"), dict) else {}
    template = goal.get("template") if isinstance(goal.get("template"), dict) else {}
    preset = goal.get("autonomy_preset") if isinstance(goal.get("autonomy_preset"), dict) else {}
    checkpoint_progress = (
        (status.get("progress") if isinstance(status.get("progress"), dict) else {}).get("checkpoint_progress")
        or (status.get("progress") if isinstance(status.get("progress"), dict) else {}).get("checkpointProgress")
        or {}
    )
    mode_policy = active_goal_mode_policy(goal.get("mode") or "adaptive")
    lines = [
        f"# ACTIVE GOAL SOURCE: {status.get('path') or ''}",
        "# ACTIVE GOAL POLICY: Runtime operator intent only; do not override GOALS.md, validation, worktree, PR, policy, or LAN safety gates.",
        f"- id: {goal.get('id') or ''}",
        f"- status: {goal.get('status') or ''}",
        f"- mode: {goal.get('mode') or 'adaptive'}",
        f"- template_key: {template.get('key') or ''}",
        f"- autonomy_preset_key: {preset.get('key') or ''}",
        f"- revision: {goal.get('revision') or 0}",
        f"- token_budget: {budgets.get('token_budget') or 0}",
        f"- time_budget_seconds: {budgets.get('time_budget_seconds') or 0}",
        f"- cycle_budget: {budgets.get('cycle_budget') or 0}",
        f"- tokens_used: {usage.get('tokens_used') or 0}",
        f"- time_used_seconds: {usage.get('time_used_seconds') or 0}",
        f"- cycles_used: {usage.get('cycles_used') or 0}",
        f"- checkpoint_progress: {checkpoint_progress.get('completed') or 0}/{checkpoint_progress.get('total') or 0}",
        f"- mode_policy: {mode_policy.get('planning_guidance') or ''}",
        f"- mode_safety_boundary: {mode_policy.get('safety_boundary') or ''}",
        "",
        "## Objective",
        str(goal.get("objective") or "").strip(),
    ]
    checkpoints = goal.get("checkpoints") if isinstance(goal.get("checkpoints"), list) else []
    if checkpoints:
        lines.extend(["", "## Checkpoints"])
        for checkpoint in checkpoints[:10]:
            if not isinstance(checkpoint, dict):
                continue
            lines.append(
                f"- {checkpoint.get('id') or ''}: {checkpoint.get('status') or 'pending'} - "
                f"{checkpoint.get('title') or ''}"
            )
    notes = str(goal.get("notes") or "").strip()
    if notes:
        lines.extend(["", "## Notes", notes])
    return "\n".join(lines).strip()


def active_goal_task_metadata(status: dict[str, Any] | None) -> dict[str, Any]:
    """Return task metadata for the current active goal.

    This metadata is parallel to GOALS `goal_trace`; it is not task-admission
    authority and does not mark project goals complete.
    """
    if not isinstance(status, dict) or not status.get("active"):
        return {}
    goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
    goal_id = str(goal.get("id") or "").strip()
    objective = str(goal.get("objective") or "").strip()
    if not goal_id or not objective:
        return {}
    budgets = goal.get("budgets") if isinstance(goal.get("budgets"), dict) else {}
    snapshot = {
        "id": goal_id,
        "objective": objective,
        "status": str(goal.get("status") or "active"),
        "mode": str(goal.get("mode") or "adaptive"),
        "mode_policy": active_goal_mode_policy(goal.get("mode") or "adaptive"),
        "modePolicy": active_goal_mode_policy(goal.get("mode") or "adaptive"),
        "revision": int(goal.get("revision") or 0),
        "template": dict(goal.get("template") or {}) if isinstance(goal.get("template"), dict) else {},
        "autonomy_preset": dict(goal.get("autonomy_preset") or {})
        if isinstance(goal.get("autonomy_preset"), dict)
        else {},
        "autonomyPreset": dict(goal.get("autonomyPreset") or {})
        if isinstance(goal.get("autonomyPreset"), dict)
        else {},
        "checkpoint_progress": dict((status.get("progress") or {}).get("checkpoint_progress") or {})
        if isinstance(status.get("progress"), dict)
        else {},
        "checkpointProgress": dict((status.get("progress") or {}).get("checkpointProgress") or {})
        if isinstance(status.get("progress"), dict)
        else {},
        "budgets": {
            "token_budget": _coerce_positive_int(budgets.get("token_budget")),
            "time_budget_seconds": _coerce_positive_int(budgets.get("time_budget_seconds")),
            "cycle_budget": _coerce_positive_int(budgets.get("cycle_budget")),
        },
    }
    return {
        "active_goal_id": goal_id,
        "activeGoalId": goal_id,
        "active_goal": snapshot,
        "activeGoal": snapshot,
    }


def active_goal_role_context(status: dict[str, Any] | None, *, role: str) -> dict[str, Any]:
    """Return role-scoped active-goal context for prompts and artifacts."""
    if not isinstance(status, dict) or not status.get("active"):
        namespaced_status = status.get("active_goal_status") if isinstance(status, dict) else {}
        return {
            "role": str(role or "").strip(),
            "active": False,
            "state": str((status or {}).get("state") or "missing") if isinstance(status, dict) else "missing",
            "terminal_reason": str((namespaced_status or {}).get("terminal_reason") or ""),
            "terminalReason": str((namespaced_status or {}).get("terminalReason") or ""),
            "active_goal_status": dict(namespaced_status) if isinstance(namespaced_status, dict) else {},
            "activeGoalStatus": dict(namespaced_status) if isinstance(namespaced_status, dict) else {},
            "progress": dict((status or {}).get("progress") or {}) if isinstance(status, dict) else {},
            "active_goal_progress": dict((status or {}).get("active_goal_progress") or {}) if isinstance(status, dict) else {},
            "activeGoalProgress": dict((status or {}).get("activeGoalProgress") or {}) if isinstance(status, dict) else {},
            "completion_policy": _completion_policy(),
            "completionPolicy": _completion_policy(),
            "policy": "goals_first",
            "subordinate_to_goals_md": True,
            "subordinateToGoalsMd": True,
        }
    goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
    metadata = active_goal_task_metadata(status)
    snapshot = dict(metadata.get("active_goal") or {})
    namespaced_status = status.get("active_goal_status") if isinstance(status.get("active_goal_status"), dict) else {}
    return {
        "role": str(role or "").strip(),
        "active": True,
        "state": str(status.get("state") or "active"),
        "active_goal_id": str(metadata.get("active_goal_id") or ""),
        "activeGoalId": str(metadata.get("activeGoalId") or metadata.get("active_goal_id") or ""),
        "active_goal": snapshot,
        "activeGoal": snapshot,
        "etag": str(status.get("etag") or ""),
        "source_path": str(status.get("path") or ""),
        "sourcePath": str(status.get("path") or ""),
        "policy": "goals_first",
        "priority_policy": "goals_first",
        "priorityPolicy": "goals_first",
        "subordinate_to_goals_md": True,
        "subordinateToGoalsMd": True,
        "does_not_mark_goals_complete": True,
        "doesNotMarkGoalsComplete": True,
        "does_not_approve_pr_merge": True,
        "doesNotApprovePrMerge": True,
        "does_not_count_as_merge_readiness": True,
        "doesNotCountAsMergeReadiness": True,
        "prompt_policy": (
            "Runtime operator intent only; do not override GOALS.md, validation, "
            "worktree, PR, policy, or LAN safety gates."
        ),
        "promptPolicy": (
            "Runtime operator intent only; do not override GOALS.md, validation, "
            "worktree, PR, policy, or LAN safety gates."
        ),
        "mode": str(goal.get("mode") or "adaptive"),
        "status": str(goal.get("status") or "active"),
        "revision": int(goal.get("revision") or 0),
        "mode_policy": active_goal_mode_policy(goal.get("mode") or "adaptive"),
        "modePolicy": active_goal_mode_policy(goal.get("mode") or "adaptive"),
        "terminal_reason": str(namespaced_status.get("terminal_reason") or ""),
        "terminalReason": str(namespaced_status.get("terminalReason") or ""),
        "active_goal_status": dict(namespaced_status),
        "activeGoalStatus": dict(namespaced_status),
        "progress": dict(status.get("progress") or {}),
        "active_goal_progress": dict(status.get("active_goal_progress") or status.get("progress") or {}),
        "activeGoalProgress": dict(status.get("activeGoalProgress") or status.get("progress") or {}),
        "completion_policy": _completion_policy(),
        "completionPolicy": _completion_policy(),
    }


def active_goal_role_context_from_task_snapshot(
    active_goal: dict[str, Any] | None,
    *,
    role: str,
    fallback_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return role context from a task-bound active-goal snapshot when present."""
    if isinstance(active_goal, dict):
        goal_id = str(active_goal.get("id") or "").strip()
        objective = str(active_goal.get("objective") or "").strip()
        if goal_id and objective:
            status = {
                "active": True,
                "state": str(active_goal.get("status") or "active"),
                "goal": dict(active_goal),
                "progress": {
                    "subordinate_to_goals_md": True,
                    "subordinateToGoalsMd": True,
                },
                "active_goal_progress": {
                    "subordinate_to_goals_md": True,
                    "subordinateToGoalsMd": True,
                },
                "activeGoalProgress": {
                    "subordinate_to_goals_md": True,
                    "subordinateToGoalsMd": True,
                },
                "active_goal_status": {
                    "namespace": "active_goal",
                    "state": str(active_goal.get("status") or "active"),
                    "mode": str(active_goal.get("mode") or "adaptive"),
                    "active": True,
                    "terminal": False,
                    "stop_priority_unchanged": True,
                    "stopPriorityUnchanged": True,
                },
            }
            return active_goal_role_context(status, role=role)
    return active_goal_role_context(fallback_status, role=role)


def format_active_goal_block_from_task_snapshot(
    active_goal: dict[str, Any] | None,
    *,
    fallback_status: dict[str, Any] | None = None,
) -> str:
    """Format prompt context from a task-bound active-goal snapshot when present."""
    if isinstance(active_goal, dict) and str(active_goal.get("id") or "").strip() and str(active_goal.get("objective") or "").strip():
        return format_active_goal_block({"active": True, "goal": dict(active_goal)})
    return format_active_goal_block(fallback_status)


def attach_active_goal_to_tasks(tasks: list[dict[str, Any]], status: dict[str, Any] | None) -> list[dict[str, Any]]:
    metadata = active_goal_task_metadata(status)
    if not metadata:
        return [dict(task) for task in tasks]
    out: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        next_task = dict(task)
        for key, value in metadata.items():
            next_task[key] = dict(value) if isinstance(value, dict) else value
        out.append(next_task)
    return out
