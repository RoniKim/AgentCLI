from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

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


def _git_remote_url(repo: Path) -> Optional[str]:
    """Return normalised git remote origin URL, or None.

    Used to generate a **portable** slug that stays the same across PCs
    as long as the repo is cloned from the same remote.
    """
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            url = r.stdout.strip()
            if url:
                # Normalise: strip trailing '/' and '.git' so
                # https://github.com/foo/bar.git == https://github.com/foo/bar
                url = url.rstrip("/")
                if url.endswith(".git"):
                    url = url[:-4]
                return url
    except Exception:
        pass
    return None


def _repo_identity(repo: Path) -> str:
    """git remote URL (portable) or local path (fallback)."""
    return _git_remote_url(repo) or str(repo)


def _safe_name(repo: Path) -> str:
    name = repo.name or "repo"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "repo"


def _repo_slug(repo: Path) -> str:
    """Portable slug: ``{folder_name}-{hash}``.

    Hash source priority:
      1) git remote origin URL → same hash on any PC
      2) local path (non-git repos or no remote)
    """
    safe = _safe_name(repo)
    h = hashlib.sha1(_repo_identity(repo).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{safe}-{h}"


def _path_based_slug(repo: Path) -> str:
    """Legacy slug using local path only (for migration fallback)."""
    safe = _safe_name(repo)
    h = hashlib.sha1(str(repo).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{safe}-{h}"


def _find_legacy_slug_file(directory: Path, safe: str, suffix: str) -> Optional[Path]:
    """Search *directory* for a file/dir matching ``{safe}-*.{suffix}`` pattern.

    Returns the match if **exactly one** candidate is found; None otherwise.
    This enables automatic discovery of config/db files created with the
    old path-based slug when the new remote-URL-based slug differs.
    """
    if not directory.exists():
        return None
    pattern = f"{safe}-*{suffix}"
    candidates: List[Path] = list(directory.glob(pattern))
    if len(candidates) == 1:
        return candidates[0]
    return None


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
    """Config JSON path with migration fallback.

    1) ``configs/{new_slug}.json`` exists → use it
    2) ``configs/{old_path_slug}.json`` exists → use it (legacy migration)
    3) Prefix scan: exactly 1 match for ``{safe}-*.json`` → use it
    4) Otherwise → new slug path (will be created on first /save)
    """
    home = app_home()
    slug = _repo_slug(repo)
    primary = (home / "configs" / f"{slug}.json").resolve()
    if primary.exists():
        return primary
    # Legacy path-based slug fallback
    old_slug = _path_based_slug(repo)
    if old_slug != slug:
        old = (home / "configs" / f"{old_slug}.json").resolve()
        if old.exists():
            return old
    # Prefix scan fallback (config from another PC with different path)
    found = _find_legacy_slug_file(home / "configs", _safe_name(repo), ".json")
    if found:
        return found.resolve()
    return primary


def default_prompts_dir(repo: Path) -> Path:
    """Prompts directory with migration fallback."""
    home = app_home()
    slug = _repo_slug(repo)
    primary = (home / "prompts" / slug).resolve()
    if primary.exists():
        return primary
    old_slug = _path_based_slug(repo)
    if old_slug != slug:
        old = (home / "prompts" / old_slug).resolve()
        if old.exists():
            return old
    # Prefix scan: look for directory
    prompts_root = home / "prompts"
    if prompts_root.exists():
        safe = _safe_name(repo)
        candidates = [d for d in prompts_root.iterdir()
                      if d.is_dir() and d.name.startswith(f"{safe}-")]
        if len(candidates) == 1:
            return candidates[0].resolve()
    return primary


def default_database_path(repo: Path) -> Path:
    """Database path with migration fallback."""
    home = app_home()
    slug = _repo_slug(repo)
    primary = (home / "databases" / f"{slug}.db").resolve()
    if primary.exists():
        return primary
    old_slug = _path_based_slug(repo)
    if old_slug != slug:
        old = (home / "databases" / f"{old_slug}.db").resolve()
        if old.exists():
            return old
    found = _find_legacy_slug_file(home / "databases", _safe_name(repo), ".db")
    if found:
        return found.resolve()
    return primary


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
