from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from .pipeline.stage_registry import (
    builtin_role_specs as _builtin_role_specs,
    classify_role_spec as _classify_role_spec,
    normalize_role_specs as _normalize_role_specs,
)
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
            # Allow non-existing paths so first-run can create the directory.
            return Path(env).expanduser().resolve()
        except Exception:
            pass

    # Safer default for open-source usage: keep user-specific config/data
    # outside the repository working tree.
    try:
        return (Path.home() / ".agentcli").expanduser().resolve()
    except Exception:
        # Last-resort fallback for unusual environments.
        return Path(__file__).resolve().parents[1]


def _legacy_repo_home() -> Path:
    """Previous default app_home (repo root) for migration fallback reads."""
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


def _project_name_from_url(url: str) -> Optional[str]:
    """Extract project name from git remote URL.

    ``https://github.com/RoniKim/BudgetBook`` → ``BudgetBook``
    ``git@github.com:RoniKim/BudgetBook.git`` → ``BudgetBook``
    """
    # Strip trailing / and .git (already done in _git_remote_url, but be safe)
    u = url.rstrip("/")
    if u.endswith(".git"):
        u = u[:-4]
    # Last path segment is the project name
    last = u.rsplit("/", 1)[-1] if "/" in u else u.rsplit(":", 1)[-1] if ":" in u else u
    last = last.strip()
    return last if last else None


def _safe_name(repo: Path) -> str:
    """Human-readable prefix for the slug.

    Priority:
      1) Project name from git remote URL (folder-name independent)
      2) Local folder name (fallback for non-git repos)
    """
    url = _git_remote_url(repo)
    if url:
        name = _project_name_from_url(url)
        if name:
            return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "repo"
    name = repo.name or "repo"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "repo"


def _local_safe_name(repo: Path) -> str:
    """Safe name from local folder name only (for legacy fallback)."""
    name = repo.name or "repo"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_") or "repo"


def _repo_slug(repo: Path) -> str:
    """Fully portable slug: ``{project_name}-{hash}``.

    Both prefix and hash are derived from git remote URL when available,
    making the slug completely independent of local folder name.
    """
    safe = _safe_name(repo)
    h = hashlib.sha1(_repo_identity(repo).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{safe}-{h}"


def _path_based_slug(repo: Path) -> str:
    """Legacy slug using local folder name + path hash (for migration)."""
    safe = _local_safe_name(repo)
    h = hashlib.sha1(str(repo).encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{safe}-{h}"


def _find_legacy_slug_file(directory: Path, prefixes: List[str], suffix: str) -> Optional[Path]:
    """Search *directory* for a file/dir matching any of ``{prefix}-*{suffix}``.

    *prefixes* should include both the remote-derived name and the local
    folder name so that renames (``006. Budgetbook`` → ``BudgetBook``) are
    still discovered.

    Returns the match if **exactly one** candidate is found across all
    prefixes combined; None otherwise (ambiguous or missing).
    """
    if not directory.exists():
        return None
    seen: dict[Path, None] = {}
    for prefix in prefixes:
        for p in directory.glob(f"{prefix}-*{suffix}"):
            seen[p] = None
    candidates = list(seen)
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

def _fallback_prefixes(repo: Path) -> List[str]:
    """Return deduplicated list of prefixes for legacy file discovery.

    Includes both remote-derived name and local folder name so that
    folder renames (e.g. ``006. Budgetbook`` → ``BudgetBook``) are handled.
    """
    remote_safe = _safe_name(repo)
    local_safe = _local_safe_name(repo)
    seen: list[str] = [remote_safe]
    if local_safe != remote_safe:
        seen.append(local_safe)
    return seen


def default_config_path(repo: Path) -> Path:
    """Config JSON path with migration fallback.

    1) ``configs/{new_slug}.json`` exists → use it
    2) ``configs/{old_path_slug}.json`` exists → use it (legacy migration)
    3) Prefix scan: exactly 1 match across all known prefixes → use it
    4) Otherwise → new slug path (will be created on first /save)
    """
    home = app_home()
    slug = _repo_slug(repo)
    primary = (home / "configs" / f"{slug}.json").resolve()

    old_slug = _path_based_slug(repo)
    prefixes = _fallback_prefixes(repo)
    candidate = (home / "configs" / f"{slug}.json").resolve()
    if candidate.exists():
        return candidate

    if old_slug != slug:
        old = (home / "configs" / f"{old_slug}.json").resolve()
        if old.exists():
            return old

    found = _find_legacy_slug_file(home / "configs", prefixes, ".json")
    if found:
        return found.resolve()

    return primary


def legacy_default_config_path(repo: Path) -> Optional[Path]:
    """Find legacy config under previous repo-root app_home.

    This is read-only fallback discovery; callers should keep writing to
    ``default_config_path(repo)`` so config migrates out of the repository.
    """
    home = app_home()
    legacy_home = _legacy_repo_home()
    if legacy_home == home:
        return None

    slug = _repo_slug(repo)
    candidate = (legacy_home / "configs" / f"{slug}.json").resolve()
    if candidate.exists():
        return candidate

    old_slug = _path_based_slug(repo)
    if old_slug != slug:
        old = (legacy_home / "configs" / f"{old_slug}.json").resolve()
        if old.exists():
            return old

    found = _find_legacy_slug_file(legacy_home / "configs", _fallback_prefixes(repo), ".json")
    return found.resolve() if found else None


def default_prompts_dir(repo: Path) -> Path:
    """Prompts directory with migration fallback."""
    home = app_home()
    legacy_home = _legacy_repo_home()
    slug = _repo_slug(repo)
    primary = (home / "prompts" / slug).resolve()

    roots = [home]
    if legacy_home != home:
        roots.append(legacy_home)

    old_slug = _path_based_slug(repo)
    prefixes = _fallback_prefixes(repo)
    for root in roots:
        candidate = (root / "prompts" / slug).resolve()
        if candidate.exists():
            return candidate

        if old_slug != slug:
            old = (root / "prompts" / old_slug).resolve()
            if old.exists():
                return old

        prompts_root = root / "prompts"
        if not prompts_root.exists():
            continue
        candidates = [
            d
            for d in prompts_root.iterdir()
            if d.is_dir() and any(d.name.startswith(f"{pfx}-") for pfx in prefixes)
        ]
        if len(candidates) == 1:
            return candidates[0].resolve()

    return primary


def default_database_path(repo: Path) -> Path:
    """Database path with migration fallback."""
    home = app_home()
    legacy_home = _legacy_repo_home()
    slug = _repo_slug(repo)
    primary = (home / "databases" / f"{slug}.db").resolve()

    roots = [home]
    if legacy_home != home:
        roots.append(legacy_home)

    old_slug = _path_based_slug(repo)
    prefixes = _fallback_prefixes(repo)
    for root in roots:
        candidate = (root / "databases" / f"{slug}.db").resolve()
        if candidate.exists():
            return candidate

        if old_slug != slug:
            old = (root / "databases" / f"{old_slug}.db").resolve()
            if old.exists():
                return old

        found = _find_legacy_slug_file(root / "databases", prefixes, ".db")
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


def normalize_roles_value(value: Any, *, default: List[str] | None = None) -> List[str]:
    return _normalize_role_specs(value, default=default)


def validate_roles_value(value: Any, *, default: List[str] | None = None) -> tuple[List[str], List[str]]:
    items = _normalize_role_specs(value, default=default)
    invalid = [item for item in items if _classify_role_spec(item) == "invalid"]
    return items, invalid


def normalize_config_list_value(value: Any, *, item_kind: str = "text") -> List[Any]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, tuple):
        raw_items = list(value)
    elif isinstance(value, str):
        raw_items = [item.strip() for item in re.split(r"[,\n]", value) if item.strip()]
    elif value in (None, ""):
        raw_items = []
    else:
        raw_items = [value]

    items: List[Any] = []
    for item in raw_items:
        if item_kind in {"int", "number"}:
            try:
                items.append(int(str(item).strip()))
                continue
            except Exception:
                pass
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def normalize_config_value(value: Any, schema: Dict[str, Any], path: str = "") -> Any:
    kind = str(schema.get("kind") or "text")
    if kind == "bool":
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on", "enabled"}:
                return True
            if normalized in {"0", "false", "no", "off", "disabled"}:
                return False
        return bool(value)

    if kind == "number":
        if value in (None, ""):
            return None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number: int | float = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                number = int(text)
            except Exception:
                try:
                    number = float(text)
                except Exception:
                    return value
        else:
            return value
        if isinstance(number, float) and number.is_integer():
            return int(number)
        return number

    if kind == "multienum":
        if path == "roles":
            return normalize_roles_value(value)
        return normalize_config_list_value(value, item_kind="text")

    if kind == "list":
        item_kind = str(schema.get("item_kind") or schema.get("itemKind") or "text")
        items = normalize_config_list_value(value, item_kind=item_kind)
        if item_kind in {"int", "number"}:
            normalized_items: list[Any] = []
            for item in items:
                if isinstance(item, bool):
                    normalized_items.append(int(item))
                    continue
                if isinstance(item, (int, float)):
                    number = int(item) if float(item).is_integer() else item
                elif isinstance(item, str):
                    text = item.strip()
                    if not text:
                        continue
                    try:
                        number = int(text)
                    except Exception:
                        try:
                            number = float(text)
                        except Exception:
                            normalized_items.append(item)
                            continue
                else:
                    normalized_items.append(item)
                    continue
                if isinstance(number, float) and number.is_integer():
                    number = int(number)
                normalized_items.append(number)
            return normalized_items
        return items

    if kind in {"enum", "text"}:
        if value is None:
            return ""
        return str(value)

    return value


def builtin_roles() -> List[str]:
    return _builtin_role_specs()


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


