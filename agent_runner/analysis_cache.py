from __future__ import annotations

from pathlib import Path

from .utils import now_iso


MAX_ANALYSIS_CACHE_BYTES = 500_000
MAX_ANALYSIS_HINT_BYTES = 20_000


def _tail_by_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if max_bytes <= 0 or len(encoded) <= max_bytes:
        return text
    return encoded[-max_bytes:].decode("utf-8", errors="replace")


def _bounded_analysis_text(text: str, *, max_bytes: int = MAX_ANALYSIS_CACHE_BYTES) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if max_bytes <= 0 or len(encoded) <= max_bytes:
        return text
    marker = "\n\n---\n\n## ChangeLog (auto-appended)\n\n"
    prefix = "# PROJECT ANALYSIS\n\n...(truncated to analysis cache size cap)\n" + marker
    tail_budget = max(1, max_bytes - len(prefix.encode("utf-8")) - 1)
    return prefix + _tail_by_bytes(text, tail_budget).lstrip()


def append_analysis_changelog(analysis_md: Path, entry_md: str) -> None:
    """Append a short entry under a stable changelog section."""
    analysis_md.parent.mkdir(parents=True, exist_ok=True)
    marker = "\n\n---\n\n## ChangeLog (auto-appended)\n\n"
    if not analysis_md.exists():
        analysis_md.write_text("# PROJECT ANALYSIS\n\n" + marker, encoding="utf-8", errors="replace")

    txt = analysis_md.read_text(encoding="utf-8", errors="replace")
    if "## ChangeLog (auto-appended)" not in txt:
        txt = txt.strip() + marker
    txt = txt.rstrip() + "\n" + entry_md.strip() + "\n"
    txt = _bounded_analysis_text(txt)
    analysis_md.write_bytes((txt.rstrip() + "\n").encode("utf-8", errors="replace"))


def merge_dev_hints_to_global_changelog(pm_analysis_md: Path, dev_hints_dir: Path, curr_head: str) -> None:
    """Cheap merge: append dev hint excerpts into the global changelog."""
    if not dev_hints_dir.exists():
        return
    hint_files = sorted(dev_hints_dir.glob("*.md"), key=lambda x: x.stat().st_mtime)
    if not hint_files:
        return

    entry_lines: list[str] = []
    entry_lines.append(f"- [{now_iso()}] HEAD={curr_head}")
    for hf in hint_files[-50:]:
        try:
            rel = hf.as_posix()
            content = _tail_by_bytes(hf.read_text(encoding="utf-8", errors="replace").strip(), MAX_ANALYSIS_HINT_BYTES)
            entry_lines.append(f"  - hint: {rel}")
            if content:
                excerpt = "\n".join(content.splitlines()[:12])
                entry_lines.append("    ```")
                entry_lines.append(excerpt)
                entry_lines.append("    ```")
        except Exception:
            continue

    append_analysis_changelog(pm_analysis_md, "\n".join(entry_lines))
