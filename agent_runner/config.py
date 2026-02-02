from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def default_config_path(repo: Path) -> Path:
    return (repo / ".doc" / "agent_config.json").resolve()


def default_prompts_dir(repo: Path) -> Path:
    return (repo / ".doc" / "agent_prompts").resolve()


def load_config(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        raise ValueError("Config must be a JSON object.")
    return data


def save_config(path: Path, cfg: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")


def resolve_config_path(repo: Path, explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        return p.resolve() if p.is_absolute() else (repo / p).resolve()
    return default_config_path(repo)
