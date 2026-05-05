from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .config import default_config_path, legacy_default_config_path, load_config
from .gitops import (
    collect_worktree_cleanup_candidates,
    reconcile_stale_pending_worktree_markers,
    scan_worktree_diagnostics,
)
from .process_guard import _pid_alive, _pid_create_time_ticks, _pid_executable_path
from .stop_progress import FINAL_STOP_PHASES, STOP_RECONCILIATION_FILE, read_stop_progress
from .utils import now_iso, run_cmd


@dataclass(frozen=True)
class PreflightResult:
    backend: str
    ok: bool
    issues: list[str]


READINESS_SCHEMA_VERSION = 1
RUNNER_CONTROL_EVENT_FILE = "RUNNER_CONTROL.json"
STALE_LOCK_AGE_SECONDS = 300
TELEGRAM_TOKEN_LOCK_GLOB = "agentcli_tg_*.lock"
RUNNER_WAIT_BLOCKING_PHASES = frozenset(
    {
        "request",
        "stop_file_write",
        "child_termination",
        "runner_wait",
        "final_artifact_collection",
        "timeout",
    }
)


def _path_text(value: Path | str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return Path(raw).expanduser().resolve().as_posix()
    except Exception:
        try:
            return Path(raw).expanduser().as_posix()
        except Exception:
            return raw.replace("\\", "/")


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or not path.is_file():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_runner_control_event_file(run_dir: Path) -> dict[str, Any]:
    payload = _read_json_file(run_dir / RUNNER_CONTROL_EVENT_FILE)
    current = payload.get("current_event")
    if isinstance(current, dict):
        return current
    current = payload.get("currentEvent")
    if isinstance(current, dict):
        return current
    return payload


def _readiness_issue(
    code: str,
    message: str,
    *,
    severity: str,
    path: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue = {
        "code": str(code or "").strip(),
        "message": str(message or "").strip(),
        "severity": str(severity or "").strip().lower() or "warning",
    }
    path_text = _path_text(path)
    if path_text:
        issue["path"] = path_text
    if isinstance(details, dict) and details:
        issue["details"] = dict(details)
    else:
        issue["details"] = {}
    return issue


def _coerce_optional_int(value: object) -> int | None:
    try:
        if value in (None, "", False):
            return None
        return int(str(value).strip())
    except Exception:
        return None


def _coerce_optional_float(value: object) -> float | None:
    try:
        if value in (None, "", False):
            return None
        return float(str(value).strip())
    except Exception:
        return None


def _path_age_seconds(path: Path) -> int | None:
    try:
        return max(0, int(time.time() - path.stat().st_mtime))
    except Exception:
        return None


def _normalize_lock_executable(value: object) -> str:
    text = _path_text(value)
    if not text:
        return ""
    return text.lower() if os.name == "nt" else text


def _git_dir_hint(raw: str, repo: Path) -> Path:
    text = str(raw or "").strip()
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = (repo / candidate).resolve()
    return candidate


def _resolve_git_admin_paths(repo: Path) -> tuple[Path, Path]:
    dot_git = (repo / ".git").expanduser()
    git_dir = dot_git.resolve() if dot_git.exists() else dot_git
    if dot_git.is_file():
        try:
            match = re.match(
                r"gitdir:\s*(.+)",
                dot_git.read_text(encoding="utf-8", errors="replace").strip(),
                flags=re.IGNORECASE,
            )
        except Exception:
            match = None
        if match:
            git_dir = _git_dir_hint(match.group(1), repo)
    common_dir = git_dir
    common_dir_file = git_dir / "commondir"
    if common_dir_file.exists():
        try:
            raw_common = common_dir_file.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            raw_common = ""
        if raw_common:
            common_dir = _git_dir_hint(raw_common, git_dir)
    return git_dir, common_dir


def _lock_owner_evidence(
    *,
    pid: int | None = None,
    hostname: object = "",
    executable: object = "",
    instance_name: object = "",
    token_fingerprint: object = "",
) -> dict[str, Any]:
    owner: dict[str, Any] = {}
    if pid is not None and pid > 0:
        owner["pid"] = int(pid)
    hostname_text = str(hostname or "").strip()
    if hostname_text:
        owner["hostname"] = hostname_text
    executable_text = _path_text(executable)
    if executable_text:
        owner["process_executable"] = executable_text
        owner["processExecutable"] = executable_text
    instance_text = str(instance_name or "").strip()
    if instance_text:
        owner["instance"] = instance_text
    fingerprint_text = str(token_fingerprint or "").strip()
    if fingerprint_text:
        owner["token_fingerprint"] = fingerprint_text
        owner["tokenFingerprint"] = fingerprint_text
    return owner


def _lock_liveness(
    *,
    pid: int | None,
    recorded_create_time: int | None = None,
    recorded_executable: str = "",
) -> dict[str, Any]:
    safe_pid = int(pid or 0)

    def _result(
        *,
        live: bool,
        deterministic: bool,
        reason: str,
        live_create_time: int | None = None,
        live_executable: str = "",
    ) -> dict[str, Any]:
        return {
            "live": bool(live),
            "deterministic": bool(deterministic),
            "reason": str(reason or "").strip(),
            "pid": safe_pid,
            "pid_create_time": recorded_create_time,
            "pidCreateTime": recorded_create_time,
            "live_pid_create_time": live_create_time,
            "livePidCreateTime": live_create_time,
            "process_executable": recorded_executable,
            "processExecutable": recorded_executable,
            "live_process_executable": live_executable,
            "liveProcessExecutable": live_executable,
        }

    if safe_pid <= 0:
        return _result(live=False, deterministic=False, reason="missing_pid")
    if not _pid_alive(safe_pid):
        return _result(live=False, deterministic=True, reason="pid_not_alive")
    live_create_time = _pid_create_time_ticks(safe_pid)
    live_executable = _normalize_lock_executable(_pid_executable_path(safe_pid))
    if recorded_create_time is not None and live_create_time is not None and recorded_create_time != live_create_time:
        return _result(
            live=False,
            deterministic=True,
            reason="pid_reused",
            live_create_time=live_create_time,
            live_executable=live_executable,
        )
    if recorded_executable and live_executable and recorded_executable != live_executable:
        return _result(
            live=False,
            deterministic=True,
            reason="process_executable_mismatch",
            live_create_time=live_create_time,
            live_executable=live_executable,
        )
    deterministic = bool(
        (recorded_create_time is not None and live_create_time is not None)
        or (recorded_executable and live_executable)
    )
    return _result(
        live=True,
        deterministic=deterministic,
        reason="pid_alive_signature_match" if deterministic else "pid_alive_signature_unavailable",
        live_create_time=live_create_time,
        live_executable=live_executable,
    )


def _lock_diagnostic(
    *,
    code: str,
    kind: str,
    state: str,
    path: Path,
    message: str,
    owner: dict[str, Any] | None = None,
    liveness: dict[str, Any] | None = None,
    guidance: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    age_seconds = _path_age_seconds(path)
    payload = {
        "code": str(code or "").strip(),
        "kind": str(kind or "").strip(),
        "state": str(state or "").strip() or "unknown",
        "path": _path_text(path),
        "age_seconds": age_seconds,
        "ageSeconds": age_seconds,
        "message": str(message or "").strip(),
        "owner": dict(owner) if isinstance(owner, dict) else {},
        "liveness": dict(liveness) if isinstance(liveness, dict) else {},
        "guidance": [str(item).strip() for item in guidance or [] if str(item).strip()],
        "details": dict(details) if isinstance(details, dict) else {},
    }
    return payload


def _git_lock_diagnostics(repo: Path) -> list[dict[str, Any]]:
    git_dir, common_dir = _resolve_git_admin_paths(repo)
    candidates: list[tuple[str, Path, dict[str, Any]]] = [
        (
            "git_index_lock",
            git_dir / "index.lock",
            {"git_dir": _path_text(git_dir), "git_common_dir": _path_text(common_dir)},
        )
    ]
    worktrees_dir = common_dir / "worktrees"
    if worktrees_dir.exists() and worktrees_dir.is_dir():
        for worktree_dir in sorted((item for item in worktrees_dir.iterdir() if item.is_dir()), key=lambda item: item.name.lower()):
            candidates.append(
                (
                    "git_worktree_head_lock",
                    worktree_dir / "HEAD.lock",
                    {
                        "git_dir": _path_text(git_dir),
                        "git_common_dir": _path_text(common_dir),
                        "worktree_name": worktree_dir.name,
                    },
                )
            )

    diagnostics: list[dict[str, Any]] = []
    for kind, lock_path, details in candidates:
        if not lock_path.exists() or not lock_path.is_file():
            continue
        age_seconds = _path_age_seconds(lock_path)
        state = "unknown"
        code = f"unknown_{kind}"
        message = "Git lock exists but its age could not be verified."
        if age_seconds is not None and age_seconds >= STALE_LOCK_AGE_SECONDS:
            state = "stale"
            code = f"stale_{kind}"
            message = (
                "Git lock looks stale; confirm no Git operation is still running before manual cleanup."
            )
        elif age_seconds is not None:
            state = "active"
            code = f"active_{kind}"
            message = "Git lock appears recent; leave it in place while the owning Git operation is active."
        diagnostics.append(
            _lock_diagnostic(
                code=code,
                kind=kind,
                state=state,
                path=lock_path,
                message=message,
                guidance=[
                    "Confirm no Git process is still operating in this repository or linked worktree.",
                    "If the recorded lock is stale, remove only the specific lock file manually before retrying.",
                    "Do not delete a recent Git lock owned by a live operation.",
                ],
                details=details,
            )
        )
    return diagnostics


def _telegram_token_fingerprint(token: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        return "none"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _repo_telegram_fingerprints(repo: Path) -> set[str]:
    fingerprints: set[str] = set()
    candidates = [default_config_path(repo), legacy_default_config_path(repo)]
    for candidate in candidates:
        if candidate is None or not candidate.exists():
            continue
        try:
            cfg = load_config(candidate)
        except Exception:
            continue
        telegram_cfg = cfg.get("telegram") if isinstance(cfg.get("telegram"), dict) else {}
        token = str(telegram_cfg.get("bot_token") or "").strip()
        if token:
            fingerprints.add(_telegram_token_fingerprint(token))
    return fingerprints


def _telegram_lock_diagnostics(repo: Path) -> list[dict[str, Any]]:
    repo_text = repo.resolve().as_posix()
    configured_fingerprints = _repo_telegram_fingerprints(repo)
    temp_dir = Path(tempfile.gettempdir())
    diagnostics: list[dict[str, Any]] = []
    for lock_path in sorted(temp_dir.glob(TELEGRAM_TOKEN_LOCK_GLOB), key=lambda item: item.name.lower()):
        payload = _read_json_file(lock_path)
        path_fingerprint = lock_path.stem.removeprefix("agentcli_tg_").strip()
        repo_match = _path_text(payload.get("repo")) == repo_text if payload else False
        fingerprint = str(payload.get("token_fingerprint") or path_fingerprint or "").strip()
        fingerprint_match = bool(fingerprint and fingerprint in configured_fingerprints)
        if not repo_match and not fingerprint_match:
            continue
        pid = _coerce_optional_int(payload.get("pid"))
        recorded_executable = _normalize_lock_executable(
            payload.get("process_executable")
            if payload.get("process_executable") not in (None, "", False)
            else payload.get("processExecutable")
        )
        liveness = _lock_liveness(
            pid=pid,
            recorded_create_time=None,
            recorded_executable=recorded_executable,
        )
        state = "active" if liveness.get("live") else "unknown"
        code = "active_telegram_token_lock"
        message = "Telegram token lock belongs to a live control-plane process."
        if not liveness.get("live"):
            if bool(liveness.get("deterministic")):
                state = "stale"
                code = "stale_telegram_token_lock"
                message = "Telegram token lock looks stale; the recorded control-plane owner is no longer live."
            elif (_path_age_seconds(lock_path) or 0) >= STALE_LOCK_AGE_SECONDS:
                state = "unknown"
                code = "unknown_telegram_token_lock"
                message = "Telegram token lock could not be verified and should be inspected before unattended start."
        owner = _lock_owner_evidence(
            pid=pid,
            executable=liveness.get("live_process_executable") or recorded_executable,
            instance_name=payload.get("instance"),
            token_fingerprint=fingerprint,
        )
        diagnostics.append(
            _lock_diagnostic(
                code=code,
                kind="telegram_token_lock",
                state=state,
                path=lock_path,
                message=message,
                owner=owner,
                liveness=liveness,
                guidance=[
                    "Confirm no Telegram control-plane process is still using this token fingerprint.",
                    "If the owner is gone, remove only the matching temp lock file and restart Telegram control explicitly.",
                    "Do not rotate or expose the full bot token while diagnosing the lock.",
                ],
                details={
                    "repo": _path_text(payload.get("repo") or repo_text),
                    "instance": str(payload.get("instance") or "").strip(),
                    "started_unix": _coerce_optional_float(payload.get("started_unix")),
                    "token_fingerprint": fingerprint,
                    "tokenFingerprint": fingerprint,
                },
            )
        )
    return diagnostics


def _web_instance_lock_diagnostic(repo: Path) -> dict[str, Any] | None:
    lock_path = repo / ".AgentCLI" / "web_console.lock.json"
    if not lock_path.exists() or not lock_path.is_file():
        return None
    payload = _read_json_file(lock_path)
    pid = _coerce_optional_int(payload.get("pid"))
    recorded_create_time = _coerce_optional_int(
        payload.get("pid_create_time")
        if payload.get("pid_create_time") is not None
        else payload.get("pidCreateTime")
    )
    recorded_executable = _normalize_lock_executable(
        payload.get("process_executable")
        if payload.get("process_executable") not in (None, "", False)
        else payload.get("processExecutable")
    )
    liveness = _lock_liveness(
        pid=pid,
        recorded_create_time=recorded_create_time,
        recorded_executable=recorded_executable,
    )
    state = "active" if liveness.get("live") else "unknown"
    code = "active_web_instance_lock"
    message = "Repo web instance lock belongs to a live web console owner."
    if not liveness.get("live"):
        if bool(liveness.get("deterministic")):
            state = "stale"
            code = "stale_web_instance_lock"
            message = "Repo web instance lock looks stale; the recorded web owner is no longer live."
        elif (_path_age_seconds(lock_path) or 0) >= STALE_LOCK_AGE_SECONDS:
            state = "unknown"
            code = "unknown_web_instance_lock"
            message = "Repo web instance lock could not be verified and should be inspected before unattended start."
    owner = _lock_owner_evidence(
        pid=pid,
        hostname=payload.get("hostname"),
        executable=liveness.get("live_process_executable") or recorded_executable,
    )
    host_text = str(payload.get("host") or "").strip()
    port_value = _coerce_optional_int(payload.get("port"))
    if host_text:
        owner["host"] = host_text
    if port_value is not None and port_value > 0:
        owner["port"] = port_value
    return _lock_diagnostic(
        code=code,
        kind="web_instance_lock",
        state=state,
        path=lock_path,
        message=message,
        owner=owner,
        liveness=liveness,
        guidance=[
            "Confirm no repo web console is still running for this lock owner before deleting the file.",
            "If the owner is gone, remove only this repo web lock JSON and restart the console if operator access is required.",
            "Do not kill unrelated processes or delete a lock owned by a live web console.",
        ],
        details={
            "created_at": str(payload.get("created_at") or payload.get("createdAt") or "").strip(),
            "runner_control_state": str(payload.get("runner_control_state") or payload.get("runnerControlState") or "").strip(),
            "runner_control_enabled": bool(payload.get("runner_control_enabled") or payload.get("runnerControlEnabled")),
            "runner_control_requested": bool(payload.get("runner_control_requested") or payload.get("runnerControlRequested")),
            "hostname": str(payload.get("hostname") or "").strip(),
        },
    )


def _collect_lock_diagnostics(repo: Path) -> dict[str, Any]:
    items = _git_lock_diagnostics(repo)
    web_lock = _web_instance_lock_diagnostic(repo)
    if web_lock:
        items.append(web_lock)
    items.extend(_telegram_lock_diagnostics(repo))

    summary = {
        "total": len(items),
        "active": len([item for item in items if item.get("state") == "active"]),
        "stale": len([item for item in items if item.get("state") == "stale"]),
        "unknown": len([item for item in items if item.get("state") == "unknown"]),
    }
    if summary["stale"] > 0:
        status = "blocker"
    elif summary["unknown"] > 0:
        status = "warning"
    else:
        status = "ok"
    return {
        "status": status,
        "stale_age_seconds": STALE_LOCK_AGE_SECONDS,
        "staleAgeSeconds": STALE_LOCK_AGE_SECONDS,
        "summary": summary,
        "items": items,
    }


def _append_lock_readiness_issues(
    diagnostics: dict[str, Any],
    *,
    blockers: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
) -> None:
    for item in diagnostics.get("items", []):
        if not isinstance(item, dict):
            continue
        state = str(item.get("state") or "").strip().lower()
        if state == "stale":
            blockers.append(
                _readiness_issue(
                    str(item.get("code") or "stale_lock"),
                    str(item.get("message") or "Stale lock requires operator review."),
                    severity="blocker",
                    path=str(item.get("path") or ""),
                    details=dict(item),
                )
            )
        elif state == "unknown":
            warnings.append(
                _readiness_issue(
                    str(item.get("code") or "unknown_lock"),
                    str(item.get("message") or "Lock ownership could not be verified."),
                    severity="warning",
                    path=str(item.get("path") or ""),
                    details=dict(item),
                )
            )


def _issue_lines(items: list[dict[str, Any]], *, prefix: str) -> list[str]:
    lines: list[str] = []
    for item in items:
        path_text = str(item.get("path") or "").strip()
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        rendered_details = json.dumps(details, ensure_ascii=False, sort_keys=True) if details else ""
        suffix = ""
        if path_text:
            suffix += f" path={path_text}"
        if rendered_details:
            suffix += f" details={rendered_details}"
        lines.append(f"[{prefix}] {item.get('code')}: {item.get('message')}{suffix}".rstrip())
    return lines


def format_runner_start_readiness(report: dict[str, Any] | None, *, include_ok: bool = False) -> list[str]:
    if not isinstance(report, dict) or not report:
        return []
    blockers = [item for item in report.get("blockers", []) if isinstance(item, dict)]
    warnings = [item for item in report.get("warnings", []) if isinstance(item, dict)]
    if not blockers and not warnings and not include_ok:
        return []

    lines: list[str] = []
    if blockers:
        lines.append(
            f"[ERR] Runner start blocked by readiness checks. (blockers={len(blockers)}, warnings={len(warnings)})."
        )
    elif warnings:
        lines.append(
            f"[WARN] Runner start readiness warnings detected. (blockers=0, warnings={len(warnings)})."
        )
    else:
        lines.append("[OK] Runner start readiness checks passed.")
    lines.extend(_issue_lines(blockers, prefix="ERR"))
    lines.extend(_issue_lines(warnings, prefix="WARN"))
    return lines


def _git_status_output_lines(output: str) -> list[str]:
    out = str(output or "")
    return [line.strip() for line in out.splitlines() if line.strip()]


def _git_last_data_line(output: str) -> str:
    lines = _git_status_output_lines(output)
    return lines[-1] if lines else ""


def _git_safe_directory_hint(output: str) -> str:
    text = str(output or "")
    match = re.search(r"safe\.directory\s+([^\r\n]+)", text, flags=re.IGNORECASE)
    return str(match.group(1) if match else "").strip()


def _git_head(repo: Path) -> str:
    rc, out = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo, timeout_sec=60)
    return _git_last_data_line(out) if rc == 0 else ""


def _git_is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    if not ancestor or not descendant:
        return False
    rc, _ = run_cmd(["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=repo, timeout_sec=60)
    return rc == 0


def _source_venv_details(repo: Path) -> dict[str, Any]:
    venv_path = (repo / ".venv").resolve()
    python_rel = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    python_path = (venv_path / python_rel).resolve()
    return {
        "path": venv_path.as_posix(),
        "exists": venv_path.exists(),
        "is_dir": venv_path.is_dir(),
        "python_path": python_path.as_posix(),
        "python_exists": python_path.exists(),
    }


def _stop_artifact_details(run_dir: Path, stop_file: str) -> dict[str, Any]:
    stop_path = (run_dir / stop_file).resolve()
    stop_progress = read_stop_progress(run_dir)
    runner_control = _read_runner_control_event_file(run_dir)
    stop_reconciliation_path = (run_dir / STOP_RECONCILIATION_FILE).resolve()
    current_phase = str(stop_progress.get("phase") or "").strip().lower()
    control_phase = str(
        runner_control.get("phase")
        or runner_control.get("status")
        or ""
    ).strip().lower()
    return {
        "run_dir": run_dir.resolve().as_posix(),
        "stop_file": stop_path.as_posix(),
        "stop_file_exists": stop_path.exists(),
        "stop_progress_path": (run_dir / "STOP_PROGRESS.json").resolve().as_posix(),
        "stop_progress_phase": current_phase,
        "stop_progress_active": bool(current_phase and current_phase not in FINAL_STOP_PHASES),
        "stop_reconciliation_path": stop_reconciliation_path.as_posix(),
        "stop_reconciliation": _read_json_file(stop_reconciliation_path),
        "runner_control_path": (run_dir / RUNNER_CONTROL_EVENT_FILE).resolve().as_posix(),
        "runner_control_phase": control_phase,
        "runner_control_event": runner_control,
    }


def check_runner_start_readiness(repo: Path | str, run_dir: Path | str, *, stop_file: str = "STOP") -> dict[str, Any]:
    repo_path = Path(repo).expanduser().resolve()
    run_dir_path = Path(run_dir).expanduser().resolve()
    stop_file_name = str(stop_file or "STOP").strip() or "STOP"

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    source_venv = _source_venv_details(repo_path)
    if not source_venv["exists"] or not source_venv["is_dir"] or not source_venv["python_exists"]:
        blockers.append(
            _readiness_issue(
                "missing_source_venv",
                "Source .venv is missing.",
                severity="blocker",
                path=str(source_venv.get("path") or ""),
                details=source_venv,
            )
        )

    lock_diagnostics = _collect_lock_diagnostics(repo_path)
    _append_lock_readiness_issues(
        lock_diagnostics,
        blockers=blockers,
        warnings=warnings,
    )

    git_info: dict[str, Any] = {"repo": repo_path.as_posix()}
    git_rc, git_out = run_cmd(["git", "status", "--porcelain"], cwd=repo_path, timeout_sec=60)
    git_info["rc"] = int(git_rc)
    git_info["output"] = str(git_out or "").strip()
    git_info["ok"] = git_rc == 0
    if git_rc != 0:
        output_text = str(git_out or "").strip()
        lowered = output_text.lower()
        safe_directory_hint = _git_safe_directory_hint(output_text)
        if "dubious ownership" in lowered or "safe.directory" in lowered:
            details = dict(git_info)
            if safe_directory_hint:
                details["safe_directory_hint"] = safe_directory_hint
            blockers.append(
                _readiness_issue(
                    "git_safe_directory_required",
                    "Git safe-directory or ownership checks failed before the runner could start.",
                    severity="blocker",
                    path=repo_path.as_posix(),
                    details=details,
                )
            )
        else:
            blockers.append(
                _readiness_issue(
                    "git_status_failed",
                    "Git status failed before the runner could start.",
                    severity="blocker",
                    path=repo_path.as_posix(),
                    details=git_info,
                )
            )

    stop_artifacts = _stop_artifact_details(run_dir_path, stop_file_name)
    stop_phase = str(stop_artifacts.get("stop_progress_phase") or "").strip().lower()
    control_phase = str(stop_artifacts.get("runner_control_phase") or "").strip().lower()
    if bool(stop_artifacts.get("stop_file_exists")):
        blockers.append(
            _readiness_issue(
                "stale_stop_artifact",
                "Target run dir contains a STOP file; remove it or wait for stale STOP reconciliation before launching.",
                severity="blocker",
                path=str(stop_artifacts.get("stop_file") or ""),
                details=stop_artifacts,
            )
        )
    if stop_phase in RUNNER_WAIT_BLOCKING_PHASES:
        blockers.append(
            _readiness_issue(
                "stale_runner_wait_artifact",
                "Target run dir still records stop-progress wait state from a previous run.",
                severity="blocker",
                path=str(stop_artifacts.get("stop_progress_path") or ""),
                details=stop_artifacts,
            )
        )
    elif control_phase in RUNNER_WAIT_BLOCKING_PHASES:
        blockers.append(
            _readiness_issue(
                "stale_runner_wait_artifact",
                "Target run dir still records runner-control wait state from a previous run.",
                severity="blocker",
                path=str(stop_artifacts.get("runner_control_path") or ""),
                details=stop_artifacts,
            )
        )

    current_head = _git_head(repo_path) if git_rc == 0 else ""
    worktree_diagnostics = scan_worktree_diagnostics(repo_path)
    pending_marker_reconciliations = reconcile_stale_pending_worktree_markers(
        repo_path,
        diagnostics=worktree_diagnostics,
        source_head=current_head,
    )
    if pending_marker_reconciliations:
        worktree_diagnostics = scan_worktree_diagnostics(repo_path)
    merged_warning_paths: set[str] = set()
    for worktree in worktree_diagnostics.get("generated_worktrees", []):
        if not isinstance(worktree, dict) or not bool(worktree.get("orphaned")):
            continue
        contract_path_text = _path_text(worktree.get("contract_path"))
        if not contract_path_text or not current_head:
            continue
        contract = _read_json_file(Path(contract_path_text))
        if not contract:
            continue
        worktree_path = Path(_path_text(worktree.get("path")))
        live_head = _git_head(worktree_path) if worktree_path.exists() else ""
        head_ref = str(
            live_head
            or contract.get("head_ref")
            or contract.get("headRef")
            or ""
        ).strip()
        expected_head = str(
            contract.get("expected_head")
            or contract.get("expectedHead")
            or contract.get("base_ref")
            or contract.get("baseRef")
            or ""
        ).strip()
        if not head_ref or head_ref == expected_head:
            continue
        if head_ref == current_head or _git_is_ancestor(repo_path, head_ref, current_head):
            warning_path = _path_text(worktree.get("path"))
            merged_warning_paths.add(warning_path)
            warnings.append(
                _readiness_issue(
                    "generated_worktree_already_merged",
                    "Generated worktree appears already merged into the source repository.",
                    severity="warning",
                    path=warning_path,
                    details={
                        "worktree": dict(worktree),
                        "contract_path": contract_path_text,
                        "live_head": live_head,
                        "head_ref": head_ref,
                        "expected_head": expected_head,
                        "source_head": current_head,
                    },
                )
            )

    for issue in worktree_diagnostics.get("issues", []):
        if not isinstance(issue, dict):
            continue
        kind = str(issue.get("kind") or "").strip().lower()
        path_text = _path_text(issue.get("path"))
        if kind == "stale_pending_marker":
            warnings.append(
                _readiness_issue(
                    "stale_generated_worktree_marker",
                    str(issue.get("message") or "Pending worktree marker appears stale.").strip(),
                    severity="warning",
                    path=path_text,
                    details={"issue": dict(issue)},
                )
            )
        elif kind == "orphaned_worktree" and path_text not in merged_warning_paths:
            warnings.append(
                _readiness_issue(
                    "orphaned_generated_worktree",
                    str(issue.get("message") or "Generated worktree appears stale.").strip(),
                    severity="warning",
                    path=path_text,
                    details={"issue": dict(issue)},
                )
            )
        elif kind == "stale_task_branch":
            details = issue.get("details") if isinstance(issue.get("details"), dict) else {}
            warnings.append(
                _readiness_issue(
                    "stale_task_branch",
                    (
                        "Stale task branch listed before cleanup: "
                        f"age={details.get('age') or '0s'} "
                        f"status={details.get('status') or 'unknown'} "
                        f"reason={details.get('reason') or 'unknown'} "
                        f"owning_run={details.get('owning_run') or 'unknown'}"
                    ),
                    severity="warning",
                    details={"issue": dict(issue), "branch": details.get("branch")},
                )
            )
        elif kind == "interrupted_attempt_directory":
            details = issue.get("details") if isinstance(issue.get("details"), dict) else {}
            warnings.append(
                _readiness_issue(
                    "interrupted_attempt_directory",
                    (
                        "Interrupted attempt directory listed before cleanup: "
                        f"age={details.get('age') or '0s'} "
                        f"status={details.get('status') or 'unknown'} "
                        f"reason={details.get('reason') or 'unknown'} "
                        f"owning_run={details.get('owning_run') or 'unknown'}"
                    ),
                    severity="warning",
                    path=path_text,
                    details={"issue": dict(issue)},
                )
            )

    cleanup_candidates = collect_worktree_cleanup_candidates(repo_path, run_dir=run_dir_path)

    report = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "checked_at": now_iso(),
        "repo": repo_path.as_posix(),
        "run_dir": run_dir_path.as_posix(),
        "stop_file": stop_file_name,
        "source_venv": source_venv,
        "lock_diagnostics": lock_diagnostics,
        "git": git_info,
        "stop_artifacts": stop_artifacts,
        "worktree_diagnostics": {
            "status": str(worktree_diagnostics.get("status") or "ok"),
            "summary": dict(worktree_diagnostics.get("summary") or {}),
            "issues": [dict(item) for item in worktree_diagnostics.get("issues", []) if isinstance(item, dict)],
            "pending_markers": [dict(item) for item in worktree_diagnostics.get("pending_markers", []) if isinstance(item, dict)],
            "generated_worktrees": [dict(item) for item in worktree_diagnostics.get("generated_worktrees", []) if isinstance(item, dict)],
            "cleanup_failed": [dict(item) for item in worktree_diagnostics.get("cleanup_failed", []) if isinstance(item, dict)],
            "stale_task_branches": [dict(item) for item in worktree_diagnostics.get("stale_task_branches", []) if isinstance(item, dict)],
            "interrupted_attempts": [dict(item) for item in worktree_diagnostics.get("interrupted_attempts", []) if isinstance(item, dict)],
            "pending_marker_reconciliations": [dict(item) for item in pending_marker_reconciliations if isinstance(item, dict)],
            "cleanup_candidates": [dict(item) for item in cleanup_candidates if isinstance(item, dict)],
        },
        "blockers": blockers,
        "warnings": warnings,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
    }
    report["ok"] = not blockers
    report["message"] = (
        "Runner start blocked by readiness checks."
        if blockers
        else "Runner start readiness checks passed."
    )
    return report


def _normalize_backend_list(backends: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in backends:
        val = str(raw or "").strip().lower()
        if not val or val in seen:
            continue
        seen.add(val)
        out.append(val)
    return out


def _check_import(module_name: str, label: str, issues: list[str]) -> None:
    try:
        importlib.import_module(module_name)
    except Exception:
        issues.append(f"missing dependency: {label}")


def _check_command(cmd: str, issues: list[str]) -> None:
    if shutil.which(cmd) is None:
        issues.append(f"missing executable in PATH: {cmd}")


def preflight_codex(args: argparse.Namespace) -> PreflightResult:
    issues: list[str] = []
    # codex exec uses Codex credits (ChatGPT subscription) — no OPENAI_API_KEY needed.
    _check_command("codex", issues)

    return PreflightResult(backend="codex", ok=not issues, issues=issues)


def preflight_claudecode(args: argparse.Namespace) -> PreflightResult:
    issues: list[str] = []
    _check_import("claude_agent_sdk", "claude-agent-sdk", issues)

    return PreflightResult(backend="claudecode", ok=not issues, issues=issues)


def run_preflight(args: argparse.Namespace, backends: Iterable[str]) -> list[PreflightResult]:
    results: list[PreflightResult] = []
    for backend in _normalize_backend_list(backends):
        if backend == "codex":
            results.append(preflight_codex(args))
        elif backend == "claudecode":
            results.append(preflight_claudecode(args))
        else:
            results.append(PreflightResult(backend=backend, ok=False, issues=[f"unknown backend: {backend}"]))
    return results
