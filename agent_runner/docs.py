from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple, List, Dict

from .utils import eprint
from .config import app_home


def resolve_docs_dir(repo: Path, docs_dir_arg: str) -> Optional[Path]:
    p = Path(docs_dir_arg).expanduser()
    cand = p if p.is_absolute() else (repo / p)
    if cand.exists() and cand.is_dir():
        return cand.resolve()
    for fb in [repo / ".doc" / "Docs", repo / ".doc" / "docs", repo / "Docs", repo / "docs"]:
        if fb.exists() and fb.is_dir():
            return fb.resolve()
    return None


def read_text_robust(path: Path) -> tuple[str, str]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    for enc in ("cp949", "euc-kr"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def _parse_env_kv(line: str) -> Optional[Tuple[str, str]]:
    s = line.strip()
    if not s or s.startswith("#") or "=" not in s:
        return None
    k, v = s.split("=", 1)
    k = k.strip()
    v = v.strip()
    if not k:
        return None
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        v = v[1:-1]
    return k, v


def find_dotenv_upwards(start: Path, filename: str = ".env", max_levels: int = 10) -> Optional[Path]:
    cur = start.resolve()
    for _ in range(max_levels):
        cand = cur / filename
        if cand.exists() and cand.is_file():
            return cand
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


def load_env_file(path: Path, override: bool = False) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        txt, _enc = read_text_robust(path)
        for line in txt.splitlines():
            kv = _parse_env_kv(line)
            if not kv:
                continue
            k, v = kv
            if not override and os.getenv(k):
                continue
            if v:
                os.environ[k] = v
        return True
    except Exception:
        return False


def load_dotenv_best_effort(repo: Path, explicit_env_file: str = "", override: bool = True) -> dict[str, list[str]]:
    """Best-effort .env loading across working-directory changes."""
    tried: list[str] = []
    loaded: list[str] = []

    def try_load(p: Optional[Path]) -> None:
        if not p:
            return
        sp = str(p)
        tried.append(sp)
        if load_env_file(p, override=override):
            loaded.append(sp)

    if explicit_env_file:
        try_load(Path(explicit_env_file).expanduser())

    # Prefer AgentCLI-side .env (outside the target repo).
    try_load(app_home() / ".env")

    try_load(find_dotenv_upwards(Path.cwd()))

    try:
        # (Legacy) search near this module as well.
        try_load(find_dotenv_upwards(Path(__file__).resolve().parent))
    except Exception:
        pass

    try_load(find_dotenv_upwards(repo))

    # Optional python-dotenv support
    try:
        from dotenv import load_dotenv  # type: ignore
        dot: Optional[Path] = None
        if loaded:
            for s in reversed(loaded):
                if not s.startswith("python-dotenv:"):
                    dot = Path(s)
                    break
        if not dot:
            dot = find_dotenv_upwards(Path.cwd()) or find_dotenv_upwards(repo)

        if dot and dot.exists():
            tried.append(f"python-dotenv:{dot}")
            load_dotenv(dotenv_path=str(dot), override=override)
            loaded.append(f"python-dotenv:{dot}")
        else:
            tried.append("python-dotenv:default")
            load_dotenv(override=override)
            loaded.append("python-dotenv:default")
    except Exception:
        pass

    return {"tried": tried, "loaded": loaded}


def extract_headings(md: str, max_headings: int = 80) -> list[str]:
    out: list[str] = []
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("#"):
            continue
        lvl = len(s) - len(s.lstrip("#"))
        if 1 <= lvl <= 3:
            out.append(s)
            if len(out) >= max_headings:
                break
    return out


def list_md_files(docs_dir: Path) -> list[Path]:
    return sorted([p for p in docs_dir.glob("*.md") if p.is_file()], key=lambda x: x.name.lower())


def generate_docs_digest(repo: Path, docs_dir: Path, digest_path: Path) -> None:
    docs = list_md_files(docs_dir)
    lines: list[str] = []
    lines.append("# DOCS DIGEST")
    lines.append("")
    lines.append("이 파일은 `.doc/Docs` 문서들의 **헤딩 인덱스**만 로컬에서 추출한 디제스트입니다.")
    lines.append("토큰 절약을 위해 에이전트는 기본적으로 이 디제스트만 읽고 진행합니다.")
    lines.append("")
    lines.append("## Inventory")
    for f in docs:
        rel = f.relative_to(repo).as_posix() if repo in f.parents else f.as_posix()
        lines.append(f"- {rel}")
    lines.append("")
    for f in docs:
        text, used = read_text_robust(f)
        rel = f.relative_to(repo).as_posix() if repo in f.parents else f.as_posix()
        lines.append(f"## {f.name}")
        lines.append(f"- path: `{rel}`")
        lines.append(f"- decoded_as: `{used}`")
        heads = extract_headings(text)
        if heads:
            lines.append("- headings:")
            for h in heads:
                lines.append(f"  - {h}")
        else:
            lines.append("- headings: (none found)")
        lines.append("")
    digest_path.parent.mkdir(parents=True, exist_ok=True)
    digest_path.write_text("\n".join(lines), encoding="utf-8", errors="strict", newline="\n")
