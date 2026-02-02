from __future__ import annotations

from pathlib import Path

from .utils import now_iso


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
    analysis_md.write_text(txt + "\n", encoding="utf-8", errors="replace")


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
            content = hf.read_text(encoding="utf-8", errors="replace").strip()
            entry_lines.append(f"  - hint: {rel}")
            if content:
                excerpt = "\n".join(content.splitlines()[:12])
                entry_lines.append("    ```")
                entry_lines.append(excerpt)
                entry_lines.append("    ```")
        except Exception:
            continue

    append_analysis_changelog(pm_analysis_md, "\n".join(entry_lines))
