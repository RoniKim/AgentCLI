from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from .utils import now_iso


@dataclass
class TaskItem:
    id: str
    title: str
    prompt: str
    files: list[str]
    done_when: str


def load_backlog_json(path: Path) -> list[TaskItem]:
    """Load backlog tasks from BACKLOG.json.

    Supported shapes:
    - {"generated_at":..., "tasks":[...]}
    - {"tasks":[...]}
    - [...] (list of tasks)

    This loader is intentionally tolerant of PM output drift:
    - task keys may be {id,title,prompt,files,done_when} or include {description,files_changed,definition_of_done,...}
    """
    data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))

    if isinstance(data, list):
        raw_tasks = data
    elif isinstance(data, dict):
        raw_tasks = data.get("tasks")
        if raw_tasks is None:
            raw_tasks = data.get("items") or data.get("backlog") or []
        if not isinstance(raw_tasks, list):
            raw_tasks = []
    else:
        raw_tasks = []

    items: list[TaskItem] = []
    auto_i = 1
    for x in raw_tasks:
        if not isinstance(x, dict):
            continue

        tid = str(x.get("id") or x.get("task_id") or x.get("key") or "").strip()
        if not tid:
            tid = f"T{auto_i:02d}"
        auto_i += 1

        title = str(x.get("title") or x.get("name") or "").strip() or tid
        prompt = str(x.get("prompt") or x.get("description") or x.get("details") or "").strip()
        if not prompt:
            prompt = f"Implement {tid}: {title}"

        files_val = x.get("files")
        if not isinstance(files_val, list):
            files_val = x.get("files_changed") if isinstance(x.get("files_changed"), list) else []
        files = [str(p).strip() for p in (files_val or []) if str(p).strip()]

        done_when = str(
            x.get("done_when")
            or x.get("definition_of_done")
            or x.get("acceptance_criteria")
            or x.get("dod")
            or ""
        ).strip()
        if not done_when:
            done_when = "Git diff exists and build passes."

        if tid and title and prompt:
            items.append(TaskItem(id=tid, title=title, prompt=prompt, files=files, done_when=done_when))

    return items


def write_backlog_files(run_dir: Path, tasks: List[dict[str, Any]]) -> tuple[Path, Path]:
    """Write BACKLOG.json and BACKLOG.md from a normalized task dict list."""
    backlog = {"generated_at": now_iso(), "tasks": tasks}
    run_dir.mkdir(parents=True, exist_ok=True)
    bj = run_dir / "BACKLOG.json"
    bj.write_text(json.dumps(backlog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")

    md_lines = ["# BACKLOG", ""]
    for t in tasks:
        md_lines.append(f"- [ ] {t.get('id','')} {t.get('title','')}")
    md_lines.append("")
    bm = run_dir / "BACKLOG.md"
    bm.write_text("\n".join(md_lines), encoding="utf-8", errors="replace")
    return bj, bm


def parse_backlog_md(path: Path) -> list[TaskItem]:
    """Legacy fallback: parse simple checklist backlog."""
    txt = path.read_text(encoding="utf-8-sig", errors="replace")
    items: list[TaskItem] = []
    for line in txt.splitlines():
        m = re.match(r"^\s*-\s*\[\s*\]\s*(T\d+)\s+(.*)$", line)
        if not m:
            continue
        tid = m.group(1).strip()
        title = m.group(2).strip()
        items.append(
            TaskItem(
                id=tid,
                title=title,
                prompt=f"Implement {tid}: {title}",
                files=[],
                done_when="Git diff exists and build passes.",
            )
        )
    return items


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    return {"done": [], "failed": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")


def mark_backlog_done(backlog_md: Path, task_id: str) -> None:
    if not backlog_md.exists():
        return
    txt = backlog_md.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[str] = []
    changed = False
    for line in txt:
        if re.match(rf"^\s*-\s*\[\s*\]\s*{re.escape(task_id)}\b", line):
            out.append(re.sub(r"^\s*-\s*\[\s*\]", "- [x]", line))
            changed = True
        else:
            out.append(line)
    if changed:
        backlog_md.write_text("\n".join(out) + "\n", encoding="utf-8", errors="replace")


def write_default_p0_backlog(run_dir: Path) -> None:
    """Fallback backlog used if PM fails to generate one.

    IMPORTANT: These tasks are intentionally repo-agnostic and low-risk.
    They avoid language/framework-specific changes so the Dev agent does not
    start editing unrelated code when PM output is missing.
    """

    backlog = {
        "generated_at": now_iso(),
        "pm_failed": True,
        "tasks": [
            {
                "id": "T1",
                "title": "Record PM failure and recovery steps",
                "prompt": (
                    "PM failed to generate a backlog. Create a documentation file `.doc/PM_FAILURE.md` in the target repo "
                    "that records: (1) timestamp, (2) where to find AgentCLI logs in the run directory, "
                    "(3) recommended next steps (re-run with --debug, verify OPENAI_API_KEY, try smaller max_turns), "
                    "and (4) what was attempted. Do NOT modify application code."
                ),
                "files": [".doc/PM_FAILURE.md"],
                "done_when": "`.doc/PM_FAILURE.md` exists with recovery steps; no secrets; git diff exists.",
            },
            {
                "id": "T2",
                "title": "Generate a lightweight repository inventory",
                "prompt": (
                    "Create or update `.doc/REPO_INVENTORY.md` listing key directories and files in the repo. "
                    "Keep it brief and structured (tree-style + notes for important files). "
                    "Do NOT include secrets."
                ),
                "files": [".doc/REPO_INVENTORY.md"],
                "done_when": "`.doc/REPO_INVENTORY.md` exists and is readable; no secrets; git diff exists.",
            },
            {
                "id": "T3",
                "title": "Add a minimal verification checklist",
                "prompt": (
                    "Create `.doc/VERIFY.md` containing a small checklist of how to build/test/lint the repo locally. "
                    "If commands are unknown, add placeholders and instructions to fill them in. "
                    "Do NOT modify application code."
                ),
                "files": [".doc/VERIFY.md"],
                "done_when": "`.doc/VERIFY.md` exists with a clear checklist; no secrets; git diff exists.",
            },
        ],
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "BACKLOG.json").write_text(
        json.dumps(backlog, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        errors="replace",
    )

    md_lines = ["# BACKLOG", "", "⚠️ PM failed to generate a backlog. The tasks below are safe defaults.", ""]
    for t in backlog["tasks"]:
        md_lines.append(f"- [ ] {t['id']} {t['title']}")
    md_lines.append("")
    (run_dir / "BACKLOG.md").write_text("\n".join(md_lines), encoding="utf-8", errors="replace")
