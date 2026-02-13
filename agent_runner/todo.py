from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple


from .config import AGENT_WORK_DIR, ensure_work_dir

LAST_TODO_POINTER = "LAST_TODO.txt"


def _sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8", errors="replace")).hexdigest()


def todo_dir(repo: Path) -> Path:
    """Todo storage directory under the *target repo*.

    Stored in the repo's .AgentCLI tree so:
    - They are colocated with agent runs
    - They survive AgentCLI restarts
    """
    return repo / AGENT_WORK_DIR / "todo"


def last_pointer_path(repo: Path) -> Path:
    return todo_dir(repo) / LAST_TODO_POINTER


def _today_key(repo: Path) -> str:
    # Keep stable per-day per-repo without requiring git.
    day = datetime.now().strftime("%Y-%m-%d")
    base = f"{repo.as_posix()}|{day}"
    return _sha1_hex(base)[:10]


def today_todo_path(repo: Path) -> Path:
    return todo_dir(repo) / f"Today_{_today_key(repo)}.md"


DEFAULT_TODO_TEMPLATE = """# TODO (Today)

- created_at: {created_at}
- repo: {repo}

## Priorities

- [ ] (write the most important goal)

## Tasks

- [ ] 
- [ ] 

## Notes

- 
"""


def ensure_todo_file(repo: Path) -> Path:
    """Create today's todo file if missing, and update LAST_TODO pointer."""
    ensure_work_dir(repo)
    td = todo_dir(repo)
    td.mkdir(parents=True, exist_ok=True)

    p = today_todo_path(repo)
    if not p.exists():
        p.write_text(
            DEFAULT_TODO_TEMPLATE.format(created_at=datetime.now().isoformat(timespec="seconds"), repo=str(repo)),
            encoding="utf-8",
            errors="replace",
        )

    set_current_todo(repo, p)
    return p


def set_current_todo(repo: Path, todo_path: Path) -> None:
    try:
        rel = todo_path.relative_to(repo).as_posix() if repo in todo_path.parents else todo_path.as_posix()
        last_pointer_path(repo).write_text(rel + "\n", encoding="utf-8", errors="replace")
    except Exception:
        pass


def get_current_todo_path(repo: Path) -> Optional[Path]:
    """Return currently selected todo path.

    Selection order:
    1) .AgentCLI/todo/LAST_TODO.txt
    2) latest modified *.md under .AgentCLI/todo
    """
    try:
        ptr = last_pointer_path(repo)
        if ptr.exists():
            rel = (ptr.read_text(encoding="utf-8", errors="replace").strip() or "").strip()
            if rel:
                p = (repo / rel).resolve() if not Path(rel).is_absolute() else Path(rel).resolve()
                if p.exists() and p.is_file():
                    return p
    except Exception:
        pass

    td = todo_dir(repo)
    if not td.exists():
        return None
    mds = sorted(td.glob("*.md"), key=lambda x: x.stat().st_mtime)
    return mds[-1] if mds else None


def read_current_todo(repo: Path, max_chars: int = 12000) -> Tuple[Optional[Path], Optional[str]]:
    p = get_current_todo_path(repo)
    if not p:
        return None, None
    try:
        txt = p.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return p, None

    if max_chars and len(txt) > max_chars:
        txt = txt[:max_chars] + "\n\n...(truncated)"
    return p, txt


def open_path(p: Path) -> bool:
    """Best-effort open a file with the OS default app.

    This is used only from the interactive shell.
    """
    try:
        if os.name == "nt":
            os.startfile(str(p))  # type: ignore[attr-defined]
            return True
        # macOS
        if subprocess.call(["which", "open"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            subprocess.Popen(["open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        # Linux
        if subprocess.call(["which", "xdg-open"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0:
            subprocess.Popen(["xdg-open", str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    except Exception:
        return False
    return False


def format_todo_block(todo_path: Optional[Path], todo_text: Optional[str]) -> str:
    if not todo_path or not todo_text:
        return "(none)"
    # Keep it compact for token-saving.
    lines = todo_text.strip().splitlines()
    head = lines[:120]
    return f"# TODO SOURCE: {todo_path.as_posix()}\n" + "\n".join(head)
