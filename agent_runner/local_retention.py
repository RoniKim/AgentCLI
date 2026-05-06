from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any, Sequence

from .utils import atomic_write_json


LOCAL_RETENTION_DRY_RUN = "LOCAL_RETENTION_DRY_RUN.json"
DEFAULT_LOCAL_RETENTION_DAYS = 30
DEFAULT_LOCAL_RETENTION_MAX_RUN_DIRS = 50
DEFAULT_LOCAL_RETENTION_LOG_MB = 100

_PENDING_WORKTREE_MARKERS = {
    "WORKTREE_MERGE_PENDING.json",
    "WORKTREE_MERGE_PENDING.md",
}
_CLEANUP_FAILED_MARKERS = {
    "WORKTREE_MERGE_APPLIED_CLEANUP_FAILED.json",
    "WORKTREE_MERGE_DISCARD_CLEANUP_FAILED.json",
}
_FINAL_PR_QUEUE_STATUSES = {"approved", "discarded", "merged", "closed"}
_LOG_SUFFIXES = {".log", ".jsonl", ".txt"}
_BACKUP_NEEDLES = (".bak", ".backup", "~")


@dataclass(frozen=True)
class LocalRetentionConfig:
    enabled: bool = True
    max_days: int = DEFAULT_LOCAL_RETENTION_DAYS
    max_run_dirs: int = DEFAULT_LOCAL_RETENTION_MAX_RUN_DIRS
    keep_failed_runs: bool = True
    keep_pending_worktree_runs: bool = True
    prune_logs_over_mb: int = DEFAULT_LOCAL_RETENTION_LOG_MB
    include_pm_cache: bool = True
    include_logs: bool = True
    include_diagnostics: bool = True
    include_backups: bool = True


def _coerce_bool(value: Any, default: bool) -> bool:
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _coerce_int(value: Any, default: int, *, minimum: int = 0) -> int:
    try:
        return max(int(minimum), int(value))
    except Exception:
        return max(int(minimum), int(default))


def _get_arg_value(args: Any, key: str, default: Any = None) -> Any:
    if isinstance(args, dict):
        if key in args:
            return args.get(key)
        retention = args.get("retention")
        if isinstance(retention, dict) and key in retention:
            return retention.get(key)
        return default
    if hasattr(args, key):
        return getattr(args, key)
    retention = getattr(args, "retention", None)
    if isinstance(retention, dict) and key in retention:
        return retention.get(key)
    return default


def local_retention_config_from_args(args: Any = None) -> LocalRetentionConfig:
    return LocalRetentionConfig(
        enabled=_coerce_bool(_get_arg_value(args, "enabled"), True),
        max_days=_coerce_int(_get_arg_value(args, "max_days"), DEFAULT_LOCAL_RETENTION_DAYS),
        max_run_dirs=_coerce_int(
            _get_arg_value(args, "max_run_dirs"),
            DEFAULT_LOCAL_RETENTION_MAX_RUN_DIRS,
        ),
        keep_failed_runs=_coerce_bool(_get_arg_value(args, "keep_failed_runs"), True),
        keep_pending_worktree_runs=_coerce_bool(_get_arg_value(args, "keep_pending_worktree_runs"), True),
        prune_logs_over_mb=_coerce_int(
            _get_arg_value(args, "prune_logs_over_mb"),
            DEFAULT_LOCAL_RETENTION_LOG_MB,
        ),
        include_pm_cache=_coerce_bool(_get_arg_value(args, "include_pm_cache"), True),
        include_logs=_coerce_bool(_get_arg_value(args, "include_logs"), True),
        include_diagnostics=_coerce_bool(_get_arg_value(args, "include_diagnostics"), True),
        include_backups=_coerce_bool(_get_arg_value(args, "include_backups"), True),
    )


def _utc_now(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _path_is_within(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except Exception:
        return False


def _safe_resolve(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except Exception:
        return path.expanduser()


def _safe_size(path: Path) -> int:
    try:
        if path.is_dir():
            total = 0
            for item in path.rglob("*"):
                try:
                    if item.is_file():
                        total += int(item.stat().st_size)
                except Exception:
                    continue
            return total
        if path.is_file():
            return int(path.stat().st_size)
    except Exception:
        pass
    return 0


def _safe_mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except Exception:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def _age_fields(path: Path, now: datetime) -> dict[str, Any]:
    mtime = _safe_mtime(path)
    age_seconds = max(0, int((now - mtime).total_seconds()))
    return {
        "mtime": mtime.isoformat(),
        "age_seconds": age_seconds,
        "age_days": round(age_seconds / 86_400, 3),
    }


def _relative_path(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except Exception:
        return path.as_posix()


def _candidate_id(category: str, path: Path) -> str:
    seed = f"{category}:{path.as_posix()}"
    import hashlib

    return hashlib.sha256(seed.encode("utf-8", errors="replace")).hexdigest()[:16]


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _pending_review_state_evidence(run_dir: Path) -> list[dict[str, Any]]:
    state = _read_json_object(run_dir / "STATE.json")
    pending = state.get("pending_review") if isinstance(state.get("pending_review"), list) else []
    if not pending:
        return []
    state_path = run_dir / "STATE.json"
    evidence: list[dict[str, Any]] = []
    for row in pending:
        if not isinstance(row, dict):
            continue
        evidence.append(
            {
                "kind": "pending_review_state",
                "path": state_path.as_posix(),
                "task_id": str(row.get("task") or row.get("task_id") or row.get("taskId") or "").strip(),
                "status": str(row.get("task_status") or row.get("status") or "").strip(),
                "branch": str(row.get("branch") or "").strip(),
                "validation_artifact": str(row.get("validation_artifact") or row.get("validationArtifact") or "").strip(),
            }
        )
    return evidence


def _pending_review_packets_by_run(repo: Path) -> dict[str, list[dict[str, Any]]]:
    queue_root = repo / ".AgentCLI" / "pr_queue"
    by_run: dict[str, list[dict[str, Any]]] = {}
    if not queue_root.exists() or not queue_root.is_dir():
        return by_run
    for packet_path in sorted(queue_root.glob("*.json")):
        packet = _read_json_object(packet_path)
        if not packet:
            continue
        run_id = str(packet.get("run_id") or packet.get("runId") or "").strip()
        if not run_id:
            continue
        status = str(packet.get("status") or "").strip().lower()
        if status in _FINAL_PR_QUEUE_STATUSES:
            continue
        packet_id = str(packet.get("id") or packet_path.stem).strip()
        by_run.setdefault(run_id, []).append(
            {
                "kind": "review_packet",
                "path": packet_path.as_posix(),
                "packet_id": packet_id,
                "status": status or "pr_queued",
            }
        )
    return by_run


def _run_dir_protections(run_dir: Path, review_packets_by_run: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    evidence.extend(_pending_review_state_evidence(run_dir))
    for marker in sorted(_PENDING_WORKTREE_MARKERS):
        marker_path = run_dir / marker
        if marker_path.exists():
            evidence.append(
                {
                    "kind": "pending_worktree_review",
                    "path": marker_path.as_posix(),
                    "detail": "pending worktree review marker exists",
                }
            )
    for marker in sorted(_CLEANUP_FAILED_MARKERS):
        marker_path = run_dir / marker
        if marker_path.exists():
            evidence.append(
                {
                    "kind": "cleanup_failed_artifact",
                    "path": marker_path.as_posix(),
                    "detail": "cleanup-failed worktree evidence exists",
                }
            )
    evidence.extend(review_packets_by_run.get(run_dir.name, []))
    return evidence


def _run_state_is_failed(run_dir: Path) -> bool:
    state = _read_json_object(run_dir / "STATE.json")
    haystack = " ".join(
        str(state.get(key) or "")
        for key in (
            "status",
            "run_status",
            "final_status",
            "final_reason",
            "stop_reason",
            "last_error",
        )
    ).lower()
    return any(token in haystack for token in ("failed", "error", "blocked_env", "regression"))


def _candidate_entry(
    repo: Path,
    path: Path,
    *,
    category: str,
    kind: str,
    reason: str,
    action: str,
    now: datetime,
    protected: bool = False,
    pending_review_evidence: Sequence[dict[str, Any]] | None = None,
    covered_by: str = "",
) -> dict[str, Any]:
    resolved = _safe_resolve(path)
    return {
        "candidate_id": _candidate_id(category, resolved),
        "category": category,
        "kind": kind,
        "path": resolved.as_posix(),
        "relative_path": _relative_path(repo, resolved),
        "bytes": _safe_size(resolved),
        "reason": reason,
        "action": action,
        "protected": bool(protected),
        "pending_review_evidence": [dict(item) for item in pending_review_evidence or []],
        "covered_by": covered_by,
        **_age_fields(resolved, now),
    }


def _iter_child_dirs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return sorted([item for item in root.iterdir() if item.is_dir()], key=lambda item: item.name)


def _stale_by_age(path: Path, cfg: LocalRetentionConfig, now: datetime) -> bool:
    if cfg.max_days <= 0:
        return False
    return _safe_mtime(path) < (now - timedelta(days=cfg.max_days))


def _action_for_run(
    run_dir: Path,
    *,
    cfg: LocalRetentionConfig,
    active_run_dirs: set[str],
    protections: list[dict[str, Any]],
) -> tuple[str, bool, str]:
    resolved = _safe_resolve(run_dir).as_posix()
    if resolved in active_run_dirs:
        return "preserve_active_run", True, "active run directory is never pruned"
    if protections and cfg.keep_pending_worktree_runs:
        return "preserve_pending_review_evidence", True, "pending worktree or PR queue review evidence is present"
    if cfg.keep_failed_runs and _run_state_is_failed(run_dir):
        return "preserve_failed_run", True, "failed or blocked run retention is enabled"
    return "delete_candidate", False, "run directory exceeds local retention settings"


def _collect_run_dir_candidates(
    repo: Path,
    cfg: LocalRetentionConfig,
    now: datetime,
    *,
    active_run_dirs: set[str],
    review_packets_by_run: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[Path]]:
    runs_root = repo / ".AgentCLI" / "agent_runs"
    run_dirs = _iter_child_dirs(runs_root)
    newest_first = sorted(run_dirs, key=lambda item: (_safe_mtime(item), item.name), reverse=True)
    keep_names = {item.name for item in newest_first[: cfg.max_run_dirs]} if cfg.max_run_dirs > 0 else set()
    candidates: list[dict[str, Any]] = []
    by_run_path: dict[str, dict[str, Any]] = {}
    protected_roots: list[Path] = []

    for run_dir in run_dirs:
        stale_age = _stale_by_age(run_dir, cfg, now)
        stale_count = cfg.max_run_dirs > 0 and run_dir.name not in keep_names
        protections = _run_dir_protections(run_dir, review_packets_by_run)
        active = _safe_resolve(run_dir).as_posix() in active_run_dirs
        if not (stale_age or stale_count or protections or active):
            continue
        action, protected, default_reason = _action_for_run(
            run_dir,
            cfg=cfg,
            active_run_dirs=active_run_dirs,
            protections=protections,
        )
        if active:
            reason = default_reason
        elif protections:
            reason = default_reason
        elif stale_age and stale_count:
            reason = f"older than {cfg.max_days} days and outside newest {cfg.max_run_dirs} run directories"
        elif stale_age:
            reason = f"older than {cfg.max_days} days"
        elif stale_count:
            reason = f"outside newest {cfg.max_run_dirs} run directories"
        else:
            reason = default_reason
        entry = _candidate_entry(
            repo,
            run_dir,
            category="agent_runs",
            kind="run_directory",
            reason=reason,
            action=action,
            now=now,
            protected=protected,
            pending_review_evidence=protections,
        )
        candidates.append(entry)
        by_run_path[_safe_resolve(run_dir).as_posix()] = entry
        if protected:
            protected_roots.append(_safe_resolve(run_dir))
    return candidates, by_run_path, protected_roots


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    if root.is_file():
        return [root]
    if not root.is_dir():
        return []
    files: list[Path] = []
    for item in root.rglob("*"):
        try:
            if item.is_file():
                files.append(item)
        except Exception:
            continue
    return sorted(files, key=lambda item: item.as_posix())


def _run_parent_candidate(path: Path, run_candidates: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    resolved = _safe_resolve(path)
    for run_path, candidate in run_candidates.items():
        run_root = Path(run_path)
        if resolved == run_root or _path_is_within(resolved, run_root):
            return candidate
    return None


def _file_action(
    path: Path,
    *,
    run_candidates: dict[str, dict[str, Any]],
) -> tuple[str, bool, str, list[dict[str, Any]], str]:
    parent = _run_parent_candidate(path, run_candidates)
    if not parent:
        return "delete_candidate", False, "", [], ""
    parent_action = str(parent.get("action") or "")
    evidence = parent.get("pending_review_evidence") if isinstance(parent.get("pending_review_evidence"), list) else []
    if bool(parent.get("protected")):
        return parent_action or "preserve_pending_review_evidence", True, str(parent.get("reason") or ""), [dict(item) for item in evidence], str(parent.get("candidate_id") or "")
    if parent_action == "delete_candidate":
        return "remove_with_run_directory", False, "covered by stale run directory candidate", [], str(parent.get("candidate_id") or "")
    return "delete_candidate", False, "", [], str(parent.get("candidate_id") or "")


def _add_file_candidate(
    candidates: list[dict[str, Any]],
    seen: set[str],
    repo: Path,
    path: Path,
    *,
    cfg: LocalRetentionConfig,
    now: datetime,
    category: str,
    kind: str,
    reason: str,
    run_candidates: dict[str, dict[str, Any]],
    stale_override: bool = False,
) -> None:
    resolved = _safe_resolve(path)
    key = resolved.as_posix()
    if key in seen:
        return
    seen.add(key)
    if not stale_override and not _stale_by_age(resolved, cfg, now):
        return
    action, protected, parent_reason, evidence, covered_by = _file_action(resolved, run_candidates=run_candidates)
    candidates.append(
        _candidate_entry(
            repo,
            resolved,
            category=category,
            kind=kind,
            reason=parent_reason or reason,
            action=action,
            now=now,
            protected=protected,
            pending_review_evidence=evidence,
            covered_by=covered_by,
        )
    )


def _collect_pm_cache_candidates(
    repo: Path,
    cfg: LocalRetentionConfig,
    now: datetime,
    run_candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not cfg.include_pm_cache:
        return []
    root = repo / ".AgentCLI" / "PM_CACHE"
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _iter_files(root):
        _add_file_candidate(
            candidates,
            seen,
            repo,
            item,
            cfg=cfg,
            now=now,
            category="pm_cache",
            kind="pm_cache_file",
            reason=f"PM cache file older than {cfg.max_days} days",
            run_candidates=run_candidates,
        )
    return candidates


def _collect_log_candidates(
    repo: Path,
    cfg: LocalRetentionConfig,
    now: datetime,
    run_candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not cfg.include_logs:
        return []
    roots: list[Path] = [repo / ".AgentCLI" / "logs"]
    runs_root = repo / ".AgentCLI" / "agent_runs"
    for run_dir in _iter_child_dirs(runs_root):
        roots.extend([run_dir / "logs", run_dir / "dev_logs"])
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    max_bytes = cfg.prune_logs_over_mb * 1_000_000 if cfg.prune_logs_over_mb > 0 else 0
    for root in roots:
        for item in _iter_files(root):
            if item.suffix.lower() not in _LOG_SUFFIXES:
                continue
            size = _safe_size(item)
            oversize = bool(max_bytes and size > max_bytes)
            stale = _stale_by_age(item, cfg, now)
            if not (stale or oversize):
                continue
            reason = f"log file older than {cfg.max_days} days"
            if oversize and stale:
                reason = f"log file older than {cfg.max_days} days and larger than {cfg.prune_logs_over_mb} MB"
            elif oversize:
                reason = f"log file larger than {cfg.prune_logs_over_mb} MB"
            _add_file_candidate(
                candidates,
                seen,
                repo,
                item,
                cfg=cfg,
                now=now,
                category="logs",
                kind="log_file",
                reason=reason,
                run_candidates=run_candidates,
                stale_override=True,
            )
    return candidates


def _collect_diagnostic_candidates(
    repo: Path,
    cfg: LocalRetentionConfig,
    now: datetime,
    run_candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not cfg.include_diagnostics:
        return []
    roots: list[Path] = [
        repo / ".AgentCLI" / "diagnostics",
        repo / ".AgentCLI" / "DOCTOR.md",
    ]
    runs_root = repo / ".AgentCLI" / "agent_runs"
    for run_dir in _iter_child_dirs(runs_root):
        roots.append(run_dir / "diagnostics")
        for item in run_dir.glob("*diagnostic*"):
            roots.append(item)
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for item in _iter_files(root):
            _add_file_candidate(
                candidates,
                seen,
                repo,
                item,
                cfg=cfg,
                now=now,
                category="diagnostics",
                kind="diagnostic_file",
                reason=f"diagnostic artifact older than {cfg.max_days} days",
                run_candidates=run_candidates,
            )
    return candidates


def _is_backup_path(path: Path) -> bool:
    name = path.name.lower()
    return any(needle in name for needle in _BACKUP_NEEDLES)


def _collect_backup_candidates(
    repo: Path,
    cfg: LocalRetentionConfig,
    now: datetime,
    run_candidates: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    if not cfg.include_backups:
        return []
    roots = [repo / ".doc", repo / ".AgentCLI", repo / "config", repo / "prompts"]
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for root in roots:
        for item in _iter_files(root):
            if not _is_backup_path(item):
                continue
            _add_file_candidate(
                candidates,
                seen,
                repo,
                item,
                cfg=cfg,
                now=now,
                category="backups",
                kind="backup_file",
                reason=f"backup artifact older than {cfg.max_days} days",
                run_candidates=run_candidates,
            )
    return candidates


def _summary(candidates: Sequence[dict[str, Any]]) -> dict[str, Any]:
    categories: dict[str, int] = {}
    actions: dict[str, int] = {}
    bytes_by_category: dict[str, int] = {}
    delete_bytes = 0
    protected_bytes = 0
    for candidate in candidates:
        category = str(candidate.get("category") or "unknown")
        action = str(candidate.get("action") or "unknown")
        size = int(candidate.get("bytes") or 0)
        categories[category] = categories.get(category, 0) + 1
        actions[action] = actions.get(action, 0) + 1
        bytes_by_category[category] = bytes_by_category.get(category, 0) + size
        if bool(candidate.get("protected")):
            protected_bytes += size
        elif action in {"delete_candidate", "remove_with_run_directory"}:
            delete_bytes += size
    protected = sum(1 for item in candidates if bool(item.get("protected")))
    delete_candidates = sum(
        1
        for item in candidates
        if not bool(item.get("protected")) and str(item.get("action") or "") in {"delete_candidate", "remove_with_run_directory"}
    )
    return {
        "total": len(list(candidates)),
        "delete_candidates": delete_candidates,
        "protected": protected,
        "categories": categories,
        "actions": actions,
        "bytes_by_category": bytes_by_category,
        "delete_bytes": delete_bytes,
        "protected_bytes": protected_bytes,
    }


def build_local_retention_dry_run(
    repo: Path,
    *,
    cfg: LocalRetentionConfig | dict[str, Any] | Any | None = None,
    run_dir: Path | str | None = None,
    active_run_dirs: Sequence[Path | str] | None = None,
    now: datetime | None = None,
    write_artifact: bool = False,
) -> dict[str, Any]:
    repo_root = _safe_resolve(Path(repo))
    retention_cfg = cfg if isinstance(cfg, LocalRetentionConfig) else local_retention_config_from_args(cfg)
    current_time = _utc_now(now)
    active_paths: set[str] = set()
    for value in active_run_dirs or []:
        if value in (None, "", False):
            continue
        active_paths.add(_safe_resolve(Path(value)).as_posix())
    if run_dir not in (None, "", False):
        active_paths.add(_safe_resolve(Path(str(run_dir))).as_posix())

    review_packets_by_run = _pending_review_packets_by_run(repo_root)
    candidates: list[dict[str, Any]] = []
    run_candidates: dict[str, dict[str, Any]] = {}
    protected_roots: list[Path] = []

    if retention_cfg.enabled:
        run_items, run_candidates, protected_roots = _collect_run_dir_candidates(
            repo_root,
            retention_cfg,
            current_time,
            active_run_dirs=active_paths,
            review_packets_by_run=review_packets_by_run,
        )
        candidates.extend(run_items)
        candidates.extend(_collect_pm_cache_candidates(repo_root, retention_cfg, current_time, run_candidates))
        candidates.extend(_collect_log_candidates(repo_root, retention_cfg, current_time, run_candidates))
        candidates.extend(_collect_diagnostic_candidates(repo_root, retention_cfg, current_time, run_candidates))
        candidates.extend(_collect_backup_candidates(repo_root, retention_cfg, current_time, run_candidates))

    payload = {
        "schema_version": 1,
        "schemaVersion": 1,
        "dry_run": True,
        "dryRun": True,
        "status": "ready" if retention_cfg.enabled else "disabled",
        "repo_root": repo_root.as_posix(),
        "repoRoot": repo_root.as_posix(),
        "generated_at": current_time.isoformat(),
        "generatedAt": current_time.isoformat(),
        "retention": asdict(retention_cfg),
        "roots": {
            "agent_runs": (repo_root / ".AgentCLI" / "agent_runs").as_posix(),
            "pm_cache": (repo_root / ".AgentCLI" / "PM_CACHE").as_posix(),
            "logs": (repo_root / ".AgentCLI" / "logs").as_posix(),
            "diagnostics": (repo_root / ".AgentCLI" / "diagnostics").as_posix(),
            "backups": [
                (repo_root / ".doc").as_posix(),
                (repo_root / ".AgentCLI").as_posix(),
                (repo_root / "config").as_posix(),
                (repo_root / "prompts").as_posix(),
            ],
        },
        "active_run_dirs": sorted(active_paths),
        "activeRunDirs": sorted(active_paths),
        "protected_roots": [path.as_posix() for path in protected_roots],
        "protectedRoots": [path.as_posix() for path in protected_roots],
        "summary": _summary(candidates),
        "candidates": candidates,
        "artifact_path": "",
        "artifactPath": "",
    }

    if write_artifact and run_dir not in (None, "", False):
        artifact_path = _safe_resolve(Path(str(run_dir))) / LOCAL_RETENTION_DRY_RUN
        payload["artifact_path"] = artifact_path.as_posix()
        payload["artifactPath"] = artifact_path.as_posix()
        atomic_write_json(artifact_path, payload)

    return payload
