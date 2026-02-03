from __future__ import annotations

"""
State and backlog utilities for the agent runner.

This module defines the TaskItem dataclass and helper functions for
loading and saving state, parsing and writing backlog files, and
constructing a generic fallback backlog when the project manager fails
to produce one.  The default tasks generated here are intended to be
repository-agnostic so they can apply to any codebase.  In contrast
with the original AgentCLI implementation, the fallback tasks avoid
hard‑coded C# project specifics and instead focus on documentation,
inventory, and test scaffolding that are universally helpful.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from .utils import now_iso


@dataclass
class TaskItem:
    """Simple representation of a backlog task.

    The Agent runner represents tasks using this dataclass.  Each task
    has an ID (e.g. ``T1``), a title, a prompt describing the work to
    perform, an optional list of file paths relevant to the task, and a
    ``done_when`` string that indicates the acceptance criteria.
    """

    id: str
    title: str
    prompt: str
    files: list[str]
    done_when: str


def load_backlog_json(path: Path) -> list[TaskItem]:
    """Load backlog tasks from a JSON file.

    Supported shapes:

    - ``{"generated_at": ..., "tasks": [...]}``
    - ``{"tasks": [...]}``
    - ``[...]`` (a list of task dictionaries)

    The loader tolerates drift in PM output by accepting various
    synonymous keys such as ``description`` for ``prompt`` and
    ``definition_of_done`` for ``done_when``.  Missing IDs are
    auto‑assigned sequentially starting at ``T01``.

    Args:
        path: Path to the JSON file.

    Returns:
        A list of ``TaskItem`` instances.
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

        # drop empty id/title/prompt
        if tid and title and prompt:
            items.append(TaskItem(id=tid, title=title, prompt=prompt, files=files, done_when=done_when))

    return items


def write_backlog_files(run_dir: Path, tasks: List[dict[str, Any]]) -> tuple[Path, Path]:
    """Write ``BACKLOG.json`` and ``BACKLOG.md`` from a normalized task list.

    Args:
        run_dir: The run directory where backlog files will be stored.
        tasks: A list of task dictionaries as produced by the PM or
            normalized via ``_normalize_backlog_tasks``.

    Returns:
        A tuple of paths ``(BACKLOG.json, BACKLOG.md)``.
    """
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
    """Legacy fallback: parse a simple checklist backlog from a markdown file."""
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
    """Load task progress state from ``STATE.json``.

    The state file tracks which tasks have been completed or failed.  If
    the file does not exist, a default structure with empty lists is
    returned.
    """
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    return {"done": [], "failed": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Persist the state dictionary to ``STATE.json``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")


def mark_backlog_done(backlog_md: Path, task_id: str) -> None:
    """Mark a task as completed in ``BACKLOG.md`` by replacing ``[ ]`` with ``[x]``."""
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
    """Write a generic fallback backlog if the PM fails to produce one.

    In situations where the project manager cannot generate a backlog
    (for example, due to an LLM error or invalid response), this
    function constructs a minimal set of universally applicable tasks to
    bootstrap the development cycle.  The tasks are intentionally
    language‑agnostic and avoid references to specific frameworks or
    languages so they can be applied to any repository.

    The default backlog contains the following tasks:

    - **T1**: Create or update the project README with a clear
      description, installation instructions, and usage examples.
    - **T2**: Produce a file inventory listing all files and
      directories in the repository along with a brief description of
      their purpose.  Save this as ``FILE_INVENTORY.md``.
    - **T3**: Introduce a basic unit test suite for key functions or
      classes in the project.  Use an appropriate test framework
      depending on the project's language (e.g. pytest, unittest, jest).

    Args:
        run_dir: The run directory where backlog files will be written.
    """
    tasks = [
        {
            "id": "T1",
            "title": "Create or update README",
            "prompt": (
                "Write or update a README.md file in the repository root that describes what the project does, "
                "how to set it up, and how to use it. If a README already exists, ensure it is comprehensive "
                "and up to date."
            ),
            "files": ["README.md"],
            "done_when": "A detailed README.md exists and includes description, setup, and usage."
        },
        {
            "id": "T2",
            "title": "Generate repository file inventory",
            "prompt": (
                "List all files and directories in the repository and write a brief description of each. "
                "Save this information to a FILE_INVENTORY.md file at the root of the repository."
            ),
            "files": ["FILE_INVENTORY.md"],
            "done_when": "FILE_INVENTORY.md exists and lists files and directories with descriptions."
        },
        {
            "id": "T3",
            "title": "Add a basic unit test suite",
            "prompt": (
                "Identify core functions or classes in the repository and create a basic set of unit tests. "
                "Choose a standard test framework appropriate for the project's language (e.g., pytest for Python, "
                "unittest, jest for JavaScript). Ensure the tests run without errors."
            ),
            "files": [],
            "done_when": "A test suite exists and passes when executed."
        },
    ]
    run_dir.mkdir(parents=True, exist_ok=True)
    backlog = {"generated_at": now_iso(), "tasks": tasks}
    (run_dir / "BACKLOG.json").write_text(json.dumps(backlog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")
    md_lines = ["# BACKLOG", ""]
    for t in tasks:
        md_lines.append(f"- [ ] {t['id']} {t['title']}")
    md_lines.append("")
    (run_dir / "BACKLOG.md").write_text("\n".join(md_lines), encoding="utf-8", errors="replace")