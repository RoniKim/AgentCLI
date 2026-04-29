from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Sequence

from .utils import now_iso, run_cmd, run_cmd_async


FAST_WEB_WORKTREE_REGRESSION_TEST_FILES: tuple[str, ...] = (
    "tests/test_web_console_readonly.py",
    "tests/test_web_console_safety.py",
    "tests/test_web_console_static.py",
    "tests/test_web_console_worktree.py",
    "tests/test_worktree_isolation.py",
    "tests/test_worktree_manual_merge.py",
)

FAST_WEB_WORKTREE_REGRESSION_SCOPE_FILES: tuple[str, ...] = (
    ".doc/goals.md",
)


def _tail_text(text: str, max_chars: int) -> str:
    limit = max(1, int(max_chars))
    if len(text) <= limit:
        return text
    return text[-limit:]


def summarize_fast_web_worktree_regression_failure(
    result: dict[str, object],
    log_path: Path | None = None,
    *,
    max_chars: int = 4000,
) -> str:
    """Build a compact retry prompt excerpt for a failed fast regression gate."""
    failed_command = result.get("failed_command")
    failed = failed_command if isinstance(failed_command, dict) else {}
    name = str(failed.get("name") or failed.get("test_file") or "fast_regression")
    test_file = str(failed.get("test_file") or "").strip()
    rc = failed.get("rc")
    cmd = failed.get("cmd")
    summary = str(failed.get("summary") or "").strip()
    command_log = str(failed.get("log_path") or "").strip()

    parts = [f"fast_web_worktree_regression failed: {name}"]
    if test_file:
        parts.append(f"test_file: {test_file}")
    if rc is not None:
        parts.append(f"return_code: {rc}")
    if isinstance(cmd, list) and cmd:
        parts.append("command: " + " ".join(str(part) for part in cmd))
    elif cmd:
        parts.append(f"command: {cmd}")
    if summary:
        parts.append("summary:\n" + _tail_text(summary, min(2000, max_chars)))

    log_candidate = Path(command_log) if command_log else None
    if log_candidate and log_candidate.exists():
        try:
            parts.append("log_tail:\n" + _tail_text(log_candidate.read_text(encoding="utf-8", errors="replace"), 2000))
        except Exception:
            pass
    elif log_path:
        parts.append(f"log_path: {log_path}")

    return _tail_text("\n".join(parts), max_chars)


def should_retry_fast_web_worktree_regression_failure(
    dev_auto_escalate: bool,
    attempt: int,
    max_attempts: int,
    dev_escalate_on: Sequence[object] | set[object] | None,
) -> bool:
    """Return True when a fast regression failure should feed the next Dev attempt."""
    if not dev_auto_escalate or (int(attempt) + 1) >= max(1, int(max_attempts)):
        return False
    reasons = {str(item) for item in (dev_escalate_on or [])}
    return "fast_regression_failed" in reasons or "test_failed" in reasons


def _norm_cmd(v: object) -> list[str]:
    """Best-effort normalize a command spec into argv list."""
    if v is None:
        return []
    if isinstance(v, list):
        out: list[str] = []
        for it in v:
            s = str(it).strip()
            if s:
                out.append(s)
        return out
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        try:
            return shlex.split(s, posix=(os.name != 'nt'))
        except ValueError:
            return s.split()  # fallback
    return []


_REPO_VENV_PYTHON_RE = re.compile(
    r"(?i)(?<![\w:])(?:\.?[\\/])?\.venv[\\/]+(?:scripts[\\/]+python(?:\.exe)?|bin[\\/]+python3?)"
)


def _repo_venv_python_path(command_repo: Path) -> Path:
    repo = command_repo.expanduser().resolve()
    windows_python = repo / ".venv" / "Scripts" / "python.exe"
    posix_python = repo / ".venv" / "bin" / "python"
    if os.name == "nt":
        return windows_python
    return posix_python if posix_python.exists() else windows_python


def _is_repo_venv_python_token(value: object) -> bool:
    text = str(value or "").strip().strip("\"'")
    if not text:
        return False
    normalized = text.replace("\\", "/").lower()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized in {
        ".venv/scripts/python.exe",
        ".venv/scripts/python",
        ".venv/bin/python",
        ".venv/bin/python3",
    }


def _quote_for_embedded_command(path: Path) -> str:
    text = str(path)
    escaped = text.replace('"', '`"') if os.name == "nt" else text.replace('"', '\\"')
    return f'"{escaped}"'


def normalize_gate_command(
    cmd: Sequence[object],
    *,
    repo: Path,
    command_repo: Path | None = None,
) -> list[str]:
    """Normalize gate commands for an execution repo and a source command repo.

    Worktree isolation runs tests from the generated worktree, but repo-local
    toolchain paths such as .venv/Scripts/python.exe live in the source repo.
    """
    command_root = (command_repo or repo).expanduser().resolve()
    repo_python = _repo_venv_python_path(command_root)
    normalized: list[str] = []
    for part in cmd:
        text = str(part)
        if _is_repo_venv_python_token(text):
            normalized.append(str(repo_python))
            continue
        if ".venv" in text.lower():
            text = _REPO_VENV_PYTHON_RE.sub(lambda _match: _quote_for_embedded_command(repo_python), text)
        normalized.append(text)
    return normalized


def _validation_record(
    *,
    name: str,
    kind: str,
    gate: str,
    cmd: Sequence[object],
    rc: int,
    artifact_path: Path,
    summary: str,
    started_at: str = "",
    ended_at: str = "",
    elapsed_sec: float | int | None = None,
    status: str = "",
    extra: dict[str, object] | None = None,
) -> dict[str, object]:
    validation_status = str(status or "").strip().lower()
    if not validation_status:
        validation_status = "stopped" if str(summary or "").strip().lower() == "stopped" else ("passed" if int(rc) == 0 else "failed")
    failure_summary = summary if validation_status == "failed" else ""
    elapsed_value = round(float(elapsed_sec), 3) if elapsed_sec is not None else None
    record: dict[str, object] = {
        "name": str(name),
        "kind": str(kind),
        "gate": str(gate),
        "cmd": [str(part) for part in cmd],
        "rc": int(rc),
        "ok": validation_status == "passed",
        "status": validation_status,
        "started_at": str(started_at or ""),
        "startedAt": str(started_at or ""),
        "ended_at": str(ended_at or ""),
        "endedAt": str(ended_at or ""),
        "elapsed_sec": elapsed_value,
        "elapsedSec": elapsed_value,
        "artifact_path": artifact_path.as_posix(),
        "artifactPath": artifact_path.as_posix(),
        "log_path": artifact_path.as_posix(),
        "logPath": artifact_path.as_posix(),
        "summary": summary,
        "failure_summary": failure_summary,
        "failureSummary": failure_summary,
    }
    if extra:
        record.update(extra)
    return record


_NO_TESTS_FOUND_PHRASES: tuple[str, ...] = (
    "no tests found",
    "no test found",
    "no tests were found",
    "no test is available",
    "no tests are available",
    "no matching tests",
    "collected 0 items",
    "0 tests collected",
    "ran 0 tests",
    "0 tests run",
    "0 tests passed",
)


def looks_like_no_tests_found(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    if not normalized:
        return False
    return any(phrase in normalized for phrase in _NO_TESTS_FOUND_PHRASES)


def _normalize_validation_status(value: object) -> str:
    status = str(value or "").strip().lower()
    if not status:
        return ""
    if status in {"passed", "pass", "success", "completed", "ok", "validation_passed"}:
        return "passed"
    if status in {"failed", "fail", "error", "validation_failed"}:
        return "failed"
    if status in {"stopped", "stop"}:
        return "stopped"
    if status in {"skipped", "tests_skipped"}:
        return "tests_skipped"
    if status in {"validation_pending", "no_tests_found"}:
        return status
    return status


def classify_task_validation_status(
    *,
    run_tests: bool,
    fast_regression_triggered: bool,
    test_validation: dict[str, object] | None = None,
    validation_records: Sequence[dict[str, object]] | None = None,
) -> str:
    """Classify the overall task validation state without marking skipped tests as success."""

    def _record_status(record: dict[str, object]) -> str:
        return _normalize_validation_status(
            record.get("status")
            or record.get("validation_status")
            or record.get("validationStatus")
        )

    for record in validation_records or []:
        if not isinstance(record, dict):
            continue
        status = _record_status(record)
        if status in {"validation_pending", "tests_skipped", "no_tests_found"}:
            return status

    if test_validation and isinstance(test_validation, dict):
        status = _record_status(test_validation)
        if status in {"validation_pending", "tests_skipped", "no_tests_found"}:
            return status
        summary_text = str(
            test_validation.get("summary")
            or test_validation.get("failure_summary")
            or test_validation.get("failureSummary")
            or ""
        )
        if looks_like_no_tests_found(summary_text):
            return "no_tests_found"

    if not run_tests:
        return "validation_pending"
    if not fast_regression_triggered:
        return "tests_skipped"
    return "passed"


def repo_has_web_worktree_markers(repo: Path) -> bool:
    """Return True when the repository looks like the AgentCLI web/worktree repo."""
    try:
        return (
            (repo / "agent_runner").is_dir()
            and (repo / "web_console").is_dir()
            and (repo / ".doc" / "GOALS.md").is_file()
        )
    except Exception:
        return False


def _normalized_task_path(repo: Path, value: object) -> tuple[str, tuple[str, ...]]:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return "", ()
    try:
        path = Path(text)
        if path.is_absolute():
            repo_root = repo.expanduser().resolve()
            try:
                text = path.resolve().relative_to(repo_root).as_posix()
            except Exception:
                text = path.as_posix().replace("\\", "/")
        else:
            text = Path(text).as_posix()
    except Exception:
        text = text.replace("\\", "/")
    normalized = text.lower()
    parts = tuple(part.lower() for part in Path(text).parts if part not in ("", "."))
    return normalized, parts


def should_run_fast_web_worktree_regression(
    repo: Path,
    task_files: Sequence[str] | None,
    *extra_task_files: Sequence[str] | None,
) -> bool:
    """Return True when a task should pay the fast web/worktree regression cost."""
    if not repo_has_web_worktree_markers(repo):
        return False
    candidate_files: list[str] = []
    for file_group in (task_files, *extra_task_files):
        if not file_group:
            continue
        for raw in file_group:
            text = str(raw or "").strip()
            if text:
                candidate_files.append(text)
    if not candidate_files:
        return False
    for raw in candidate_files:
        normalized, parts = _normalized_task_path(repo, raw)
        if not normalized:
            continue
        if normalized in FAST_WEB_WORKTREE_REGRESSION_SCOPE_FILES:
            return True
        if "web_console" in parts or "agent_runner" in parts:
            return True
        if len(parts) >= 2 and parts[0] == "tests" and (
            parts[1].startswith("test_web_console_") or parts[1].startswith("test_worktree_")
        ):
            return True
    return False


def _fast_web_worktree_regression_commands() -> list[dict[str, object]]:
    python = sys.executable or "python"
    commands: list[dict[str, object]] = []
    for test_file in FAST_WEB_WORKTREE_REGRESSION_TEST_FILES:
        commands.append(
            {
                "name": Path(test_file).stem,
                "test_file": test_file,
                "cmd": [
                    python,
                    "-B",
                    "-m",
                    "unittest",
                    "discover",
                    "-s",
                    "tests",
                    "-p",
                    Path(test_file).name,
                ],
            }
        )
    return commands


async def run_fast_web_worktree_regression_async(
    repo: Path,
    log_path: Path,
    *,
    stop_path: Path | None = None,
    max_output_bytes: int = 10_000_000,
    trigger_files: Sequence[str] | None = None,
) -> dict[str, object]:
    """Run the fast web/worktree regression command set and persist a summary."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = log_path.parent / log_path.stem
    log_dir.mkdir(parents=True, exist_ok=True)

    started_at = now_iso()
    suite_started_monotonic = time.monotonic()
    command_specs = _fast_web_worktree_regression_commands()
    records: list[dict[str, object]] = []
    result: dict[str, object] = {
        "schema_version": 1,
        "gate": "fast_web_worktree_regression",
        "repo": str(repo),
        "started_at": started_at,
        "ended_at": "",
        "elapsed_sec": None,
        "elapsedSec": None,
        "ok": False,
        "commands": records,
        "log_path": log_path.as_posix(),
        "artifact_path": log_path.as_posix(),
        "artifactPath": log_path.as_posix(),
        "log_dir": log_dir.as_posix(),
        "failed_command": None,
        "failure_summary": "",
        "failureSummary": "",
        "trigger_files": [str(item) for item in dict.fromkeys(str(file).replace("\\", "/") for file in (trigger_files or []) if str(file).strip())],
        "triggerFiles": [str(item) for item in dict.fromkeys(str(file).replace("\\", "/") for file in (trigger_files or []) if str(file).strip())],
        "suite_files": list(FAST_WEB_WORKTREE_REGRESSION_TEST_FILES),
        "suiteFiles": list(FAST_WEB_WORKTREE_REGRESSION_TEST_FILES),
    }

    try:
        for index, spec in enumerate(command_specs, start=1):
            cmd = [str(part) for part in spec["cmd"]]  # type: ignore[index]
            test_file = str(spec["test_file"])  # type: ignore[index]
            name = str(spec["name"])  # type: ignore[index]
            cmd_log_path = log_dir / f"{index:02d}_{name}.txt"
            started_at = now_iso()
            started_monotonic = time.monotonic()
            try:
                rc, summary = await run_cmd_async(
                    cmd,
                    cwd=repo,
                    log_path=cmd_log_path,
                    timeout_sec=1800,
                    stop_path=stop_path,
                    max_output_bytes=max_output_bytes,
                )
            except Exception as ex:
                rc = 1
                summary = f"{type(ex).__name__}: {str(ex).strip() or type(ex).__name__}"
                try:
                    cmd_log_path.write_text(summary + "\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass
            ended_at = now_iso()
            elapsed_sec = round(max(0.0, time.monotonic() - started_monotonic), 3)
            status = "stopped" if str(summary or "").strip().lower() == "stopped" else ("passed" if rc == 0 else "failed")
            record = {
                "index": index,
                "name": name,
                "test_file": test_file,
                "cmd": cmd,
                "rc": rc,
                "status": status,
                "started_at": started_at,
                "startedAt": started_at,
                "ended_at": ended_at,
                "endedAt": ended_at,
                "elapsed_sec": elapsed_sec,
                "elapsedSec": elapsed_sec,
                "summary": summary,
                "log_path": cmd_log_path.as_posix(),
                "artifact_path": cmd_log_path.as_posix(),
                "artifactPath": cmd_log_path.as_posix(),
                "failure_summary": summary if status == "failed" else "",
                "failureSummary": summary if status == "failed" else "",
            }
            records.append(record)
            if status != "passed":
                result["failed_command"] = record
                break
        result["ok"] = bool(records) and all(str(item.get("status") or "").lower() == "passed" for item in records)
        if not result["ok"]:
            try:
                failure_summary = summarize_fast_web_worktree_regression_failure(result, log_path)
            except Exception:
                failure_summary = ""
            result["failure_summary"] = failure_summary
            result["failureSummary"] = failure_summary
        result["ended_at"] = now_iso()
        elapsed_sec = round(max(0.0, time.monotonic() - suite_started_monotonic), 3)
        result["elapsed_sec"] = elapsed_sec
        result["elapsedSec"] = elapsed_sec
    finally:
        try:
            log_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8", errors="replace")
        except Exception:
            pass

    return result


def run_build_gate(repo: Path, build_cmd: object, build_timeout_sec: int, legacy_build_target: str, log_path: Path) -> bool:
    """Run build gate.

    Priority:
      1) build_cmd (generic, preferred)
      2) legacy dotnet auto-detect (build_target + repo heuristics)
    """
    cmd = _norm_cmd(build_cmd)
    if not cmd:
        cmd = find_build_cmd(repo, legacy_build_target)
    cmd = normalize_gate_command(cmd, repo=repo)
    timeout = int(build_timeout_sec or 1800)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


async def run_build_gate_async(
    repo: Path,
    build_cmd: object,
    build_timeout_sec: int,
    legacy_build_target: str,
    log_path: Path,
    *,
    stop_path: Path | None = None,
    max_output_bytes: int = 10_000_000,
    command_repo: Path | None = None,
) -> bool:
    result = await run_build_validation_async(
        repo,
        build_cmd,
        build_timeout_sec,
        legacy_build_target,
        log_path,
        stop_path=stop_path,
        max_output_bytes=max_output_bytes,
        command_repo=command_repo,
    )
    return bool(result.get("ok", False))


def run_test_gate(
    repo: Path,
    test_cmd: object,
    test_timeout_sec: int,
    legacy_test_target: str,
    legacy_test_filter: str,
    log_path: Path,
    *,
    command_repo: Path | None = None,
) -> bool:
    """Run test gate.

    Priority:
      1) test_cmd (generic, preferred)
      2) legacy dotnet test (target + filter)
    """
    cmd = _norm_cmd(test_cmd)
    if not cmd:
        cmd = find_test_cmd(repo, legacy_test_target, legacy_test_filter)
    cmd = normalize_gate_command(cmd, repo=repo, command_repo=command_repo)
    timeout = int(test_timeout_sec or 3600)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


async def run_test_gate_async(
    repo: Path,
    test_cmd: object,
    test_timeout_sec: int,
    legacy_test_target: str,
    legacy_test_filter: str,
    log_path: Path,
    *,
    stop_path: Path | None = None,
    max_output_bytes: int = 10_000_000,
    command_repo: Path | None = None,
) -> bool:
    result = await run_test_validation_async(
        repo,
        test_cmd,
        test_timeout_sec,
        legacy_test_target,
        legacy_test_filter,
        log_path,
        stop_path=stop_path,
        max_output_bytes=max_output_bytes,
        command_repo=command_repo,
    )
    return bool(result.get("ok", False))


async def run_build_validation_async(
    repo: Path,
    build_cmd: object,
    build_timeout_sec: int,
    legacy_build_target: str,
    log_path: Path,
    *,
    stop_path: Path | None = None,
    max_output_bytes: int = 10_000_000,
    command_repo: Path | None = None,
) -> dict[str, object]:
    cmd = _norm_cmd(build_cmd)
    if not cmd:
        cmd = find_build_cmd(repo, legacy_build_target)
    cmd = normalize_gate_command(cmd, repo=repo, command_repo=command_repo)
    timeout = int(build_timeout_sec or 1800)
    started_at = now_iso()
    started_monotonic = time.monotonic()
    code, summary = await run_cmd_async(
        cmd,
        cwd=repo,
        log_path=log_path,
        timeout_sec=timeout,
        stop_path=stop_path,
        max_output_bytes=max_output_bytes,
    )
    ended_at = now_iso()
    elapsed_sec = round(max(0.0, time.monotonic() - started_monotonic), 3)
    return _validation_record(
        name="build",
        kind="compile",
        gate="build",
        cmd=cmd,
        rc=code,
        artifact_path=log_path,
        summary=summary,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_sec=elapsed_sec,
    )


async def run_test_validation_async(
    repo: Path,
    test_cmd: object,
    test_timeout_sec: int,
    legacy_test_target: str,
    legacy_test_filter: str,
    log_path: Path,
    *,
    stop_path: Path | None = None,
    max_output_bytes: int = 10_000_000,
    command_repo: Path | None = None,
) -> dict[str, object]:
    cmd = _norm_cmd(test_cmd)
    if not cmd:
        cmd = find_test_cmd(repo, legacy_test_target, legacy_test_filter)
    cmd = normalize_gate_command(cmd, repo=repo, command_repo=command_repo)
    timeout = int(test_timeout_sec or 3600)
    started_at = now_iso()
    started_monotonic = time.monotonic()
    code, summary = await run_cmd_async(
        cmd,
        cwd=repo,
        log_path=log_path,
        timeout_sec=timeout,
        stop_path=stop_path,
        max_output_bytes=max_output_bytes,
    )
    ended_at = now_iso()
    elapsed_sec = round(max(0.0, time.monotonic() - started_monotonic), 3)
    validation_status = ""
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    except Exception:
        log_text = ""
    if looks_like_no_tests_found(summary) or looks_like_no_tests_found(log_text):
        validation_status = "no_tests_found"
    return _validation_record(
        name="test",
        kind="test",
        gate="test",
        cmd=cmd,
        rc=code,
        artifact_path=log_path,
        summary=summary,
        started_at=started_at,
        ended_at=ended_at,
        elapsed_sec=elapsed_sec,
        status=validation_status,
    )


def find_build_cmd(repo: Path, explicit: str) -> list[str]:
    if explicit:
        return ["dotnet", "build", explicit]
    root_csprojs = list(repo.glob("*.csproj"))
    if len(root_csprojs) == 1:
        return ["dotnet", "build", root_csprojs[0].name]
    return []


def dotnet_build(repo: Path, build_target: str, log_path: Path) -> bool:
    cmd = find_build_cmd(repo, build_target)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=1800)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


def find_test_cmd(repo: Path, explicit: str, test_filter: str) -> list[str]:
    cmd = ["dotnet", "test"]
    if explicit:
        cmd.append(explicit)
    if test_filter:
        cmd.extend(["--filter", test_filter])
    return cmd


def dotnet_test(repo: Path, test_target: str, test_filter: str, log_path: Path, timeout_sec: int) -> bool:
    cmd = find_test_cmd(repo, test_target, test_filter)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout_sec)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


def extract_build_warnings(log_path: Path, max_warnings: int = 20) -> list[str]:
    """Extract compiler warning lines from build output.

    Deduplicates by (file, warning code) to avoid noise from multi-TFM builds.
    """
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    pattern = re.compile(r"^.*:\s*warning\s+(CS|RZ|BL|NU)\d{4}:.*$", re.MULTILINE)
    seen: set[tuple[str, str]] = set()
    warnings: list[str] = []
    for m in pattern.finditer(text):
        line = m.group(0).strip()
        key_match = re.search(r"([\w.]+)\((\d+),\d+\):\s*warning\s+(\w+\d+)", line)
        if key_match:
            key = (key_match.group(1), key_match.group(3))
            if key in seen:
                continue
            seen.add(key)
        warnings.append(line)
        if len(warnings) >= max_warnings:
            break
    return warnings
