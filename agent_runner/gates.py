from __future__ import annotations

from pathlib import Path

from .utils import run_cmd


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
