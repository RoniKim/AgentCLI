from __future__ import annotations

from pathlib import Path

from .utils import run_cmd


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
        return [p for p in s.split() if p]
    return []


def run_build_gate(repo: Path, build_cmd: object, build_timeout_sec: int, legacy_build_target: str, log_path: Path) -> bool:
    """Run build gate.

    Priority:
      1) build_cmd (generic, preferred)
      2) legacy dotnet auto-detect (build_target + repo heuristics)
    """
    cmd = _norm_cmd(build_cmd)
    if not cmd:
        cmd = find_build_cmd(repo, legacy_build_target)
    timeout = int(build_timeout_sec or 1800)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


def run_test_gate(
    repo: Path,
    test_cmd: object,
    test_timeout_sec: int,
    legacy_test_target: str,
    legacy_test_filter: str,
    log_path: Path,
) -> bool:
    """Run test gate.

    Priority:
      1) test_cmd (generic, preferred)
      2) legacy dotnet test (target + filter)
    """
    cmd = _norm_cmd(test_cmd)
    if not cmd:
        cmd = find_test_cmd(repo, legacy_test_target, legacy_test_filter)
    timeout = int(test_timeout_sec or 3600)
    code, out = run_cmd(cmd, cwd=repo, timeout_sec=timeout)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(out + "\n", encoding="utf-8", errors="replace")
    return code == 0


def find_build_cmd(repo: Path, explicit: str) -> list[str]:
    if explicit:
        return ["dotnet", "build", explicit]
    root_csprojs = list(repo.glob("*.csproj"))
    if len(root_csprojs) == 1:
        return ["dotnet", "build", root_csprojs[0].name]
    return ["dotnet", "build"]


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
