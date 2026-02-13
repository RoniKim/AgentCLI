from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import atomic_write_json, eprint

# Runtime artifacts directory name (under target repo root).
# Design documents stay in ".doc/"; runtime outputs go here.
AGENT_WORK_DIR = ".AgentCLI"

def app_home() -> Path:
    """
    AgentCLI 홈 디렉토리(파이썬쪽 저장 기준점).

    우선순위:
      1) AGENTCLI_HOME 환경변수 (지정 시 그 위치)
      2) agent_runner 패키지의 상위 디렉토리 (일반적으로 AgentCLI 프로젝트 루트)
    """
    env = (os.getenv("AGENTCLI_HOME") or "").strip()
    if env:
        try:
            p = Path(env).expanduser().resolve()
            if p.exists() and p.is_dir():
                return p
        except Exception:
            pass
    return Path(__file__).resolve().parents[1]


def _repo_slug(repo: Path) -> str:
    """
    repo 경로 기반으로 충돌 적은 slug를 만든다.
    같은 폴더명이라도 경로가 다르면 해시로 구분됨.
    """
    name = repo.name or "repo"
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "repo"
    h = hashlib.sha1(str(repo).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{safe}-{h}"


# ---- legacy paths (호환용: repo 내부) ----

def legacy_config_path(repo: Path) -> Path:
    # Prefer .AgentCLI; fall back to old .doc location for existing repos.
    new = repo / AGENT_WORK_DIR / "agent_config.json"
    if new.exists():
        return new.resolve()
    old = repo / ".doc" / "agent_config.json"
    if old.exists():
        return old.resolve()
    return new.resolve()  # default to new location


def legacy_prompts_dir(repo: Path) -> Path:
    # Prefer .AgentCLI; fall back to old .doc location for existing repos.
    new = repo / AGENT_WORK_DIR / "agent_prompts"
    if new.exists():
        return new.resolve()
    old = repo / ".doc" / "agent_prompts"
    if old.exists():
        return old.resolve()
    return new.resolve()  # default to new location


# ---- new defaults (python-side: AgentCLI 내부) ----

def default_config_path(repo: Path) -> Path:
    return (app_home() / "configs" / f"{_repo_slug(repo)}.json").resolve()


def default_prompts_dir(repo: Path) -> Path:
    return (app_home() / "prompts" / _repo_slug(repo)).resolve()


def default_database_path(repo: Path) -> Path:
    return (app_home() / "databases" / f"{_repo_slug(repo)}.db").resolve()


# ---- config io ----

def load_config(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Config root must be a JSON object")
    return data


def save_config(path: Path, cfg: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(path, cfg)
    except Exception as ex:
        eprint(f"[WARN] Failed to write config atomically: {ex}")
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")


# ---- path resolution ----

def resolve_config_path(repo: Path, explicit: Optional[str]) -> Path:
    """
    - absolute: 그대로 사용
    - relative: repo 기준이 아니라 AgentCLI 홈(app_home) 기준
    - empty: python-side default_config_path(repo)
    """
    if explicit and str(explicit).strip():
        p = Path(str(explicit)).expanduser()
        return p.resolve() if p.is_absolute() else (app_home() / p).resolve()
    return default_config_path(repo)


def resolve_prompts_dir(repo: Path, explicit: Optional[str]) -> Path:
    """
    - absolute: 그대로 사용
    - relative: AgentCLI 홈(app_home) 기준
    - empty: python-side default_prompts_dir(repo)
    - legacy 값 ".doc/agent_prompts"는 python-side default로 간주
      (실수로 AgentCLI\\.doc\\agent_prompts 만들지 않게)
    """
    if explicit and str(explicit).strip():
        s = str(explicit).strip()
        norm = s.replace("\\", "/")
        if norm in (".doc/agent_prompts", f"{AGENT_WORK_DIR}/agent_prompts"):
            return default_prompts_dir(repo)
        p = Path(s).expanduser()
        return p.resolve() if p.is_absolute() else (app_home() / p).resolve()
    return default_prompts_dir(repo)


def ensure_gitignore_entry(repo: Path, entry: str = AGENT_WORK_DIR) -> None:
    """Ensure *entry* is listed in repo/.gitignore (idempotent, best-effort).

    Called once when the runtime directory is first created so that the
    user doesn't accidentally commit runtime artifacts.
    """
    gi = repo / ".gitignore"
    try:
        if gi.exists():
            text = gi.read_text(encoding="utf-8", errors="replace")
            # Check if the entry already appears on its own line.
            for line in text.splitlines():
                stripped = line.strip()
                if stripped == entry or stripped == f"/{entry}" or stripped == f"{entry}/":
                    return  # already present
            # Append at the end with a blank separator line.
            if not text.endswith("\n"):
                text += "\n"
            text += f"\n# AgentCLI runtime artifacts\n{entry}\n"
            gi.write_text(text, encoding="utf-8", errors="replace")
        else:
            # Create a minimal .gitignore with the entry.
            gi.write_text(f"# AgentCLI runtime artifacts\n{entry}\n", encoding="utf-8", errors="replace")
    except Exception:
        pass  # best-effort; never fail the run because of this


def ensure_work_dir(repo: Path) -> Path:
    """Create *repo/.AgentCLI/* if needed and ensure .gitignore entry.

    All code that writes under .AgentCLI/ should call this first.
    Idempotent and cheap on subsequent calls.
    """
    work_root = repo / AGENT_WORK_DIR
    first_time = not work_root.exists()
    work_root.mkdir(parents=True, exist_ok=True)
    if first_time:
        ensure_gitignore_entry(repo)
    return work_root


def resolve_env_file(explicit: Optional[str]) -> Optional[Path]:
    """
    env_file도 python-side 기준으로 해석한다.
    - absolute: 그대로
    - relative: app_home 기준
    - empty: None
    """
    if explicit and str(explicit).strip():
        p = Path(str(explicit)).expanduser()
        return p.resolve() if p.is_absolute() else (app_home() / p).resolve()
    return None
