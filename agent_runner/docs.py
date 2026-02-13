from __future__ import annotations

from pathlib import Path
from typing import Optional, List, Dict

from .utils import eprint


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
