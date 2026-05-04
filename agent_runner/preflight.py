from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .gitops import scan_worktree_diagnostics
from .stop_progress import FINAL_STOP_PHASES, STOP_RECONCILIATION_FILE, read_stop_progress
from .utils import now_iso, run_cmd


@dataclass(frozen=True)
class PreflightResult:
    backend: str
    ok: bool
    issues: list[str]


READINESS_SCHEMA_VERSION = 1
RUNNER_CONTROL_EVENT_FILE = "RUNNER_CONTROL.json"
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

    worktree_diagnostics = scan_worktree_diagnostics(repo_path, categories=["stale", "orphaned"])
    current_head = _git_head(repo_path) if git_rc == 0 else ""
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

    report = {
        "schema_version": READINESS_SCHEMA_VERSION,
        "checked_at": now_iso(),
        "repo": repo_path.as_posix(),
        "run_dir": run_dir_path.as_posix(),
        "stop_file": stop_file_name,
        "source_venv": source_venv,
        "git": git_info,
        "stop_artifacts": stop_artifacts,
        "worktree_diagnostics": {
            "status": str(worktree_diagnostics.get("status") or "ok"),
            "summary": dict(worktree_diagnostics.get("summary") or {}),
            "issues": [dict(item) for item in worktree_diagnostics.get("issues", []) if isinstance(item, dict)],
            "pending_markers": [dict(item) for item in worktree_diagnostics.get("pending_markers", []) if isinstance(item, dict)],
            "generated_worktrees": [dict(item) for item in worktree_diagnostics.get("generated_worktrees", []) if isinstance(item, dict)],
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
