from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List

from .utils import now_iso, atomic_write_json, atomic_write_text, eprint


@dataclass
class TaskItem:
    id: str
    title: str
    prompt: str
    files: list[str]
    done_when: str
    skills: list[str]
    skills_rationale: str | None
    depends_on: list[str]


def load_backlog_json(path: Path) -> list[TaskItem]:
    """Load backlog tasks from BACKLOG.json.

    Supported shapes:
    - {"generated_at":..., "tasks":[...]}
    - {"tasks":[...]}
    - [...] (list of tasks)

    This loader is intentionally tolerant of PM output drift:
    - task keys may be {id,title,prompt,files,done_when} or include {description,files_changed,definition_of_done,...}
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (json.JSONDecodeError, ValueError) as exc:
        eprint(f"[WARN] BACKLOG.json parse error: {exc}")
        return []

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

        skills_val = x.get("skills") or []
        if isinstance(skills_val, str):
            skills = [p.strip() for p in skills_val.split(",") if p.strip()]
        elif isinstance(skills_val, list):
            skills = [str(p).strip() for p in skills_val if str(p).strip()]
        else:
            skills = []

        skills_rationale = x.get("skills_rationale")
        if skills_rationale is not None:
            skills_rationale = str(skills_rationale)

        depends_on_val = x.get("depends_on") or []
        if isinstance(depends_on_val, list):
            depends_on = [str(d).strip() for d in depends_on_val if str(d).strip()]
        else:
            depends_on = []

        # drop empty id/title/prompt
        if tid and title and prompt:
            items.append(
                TaskItem(
                    id=tid,
                    title=title,
                    prompt=prompt,
                    files=files,
                    done_when=done_when,
                    skills=skills,
                    skills_rationale=skills_rationale,
                    depends_on=depends_on,
                )
            )

    return items


def write_backlog_files(run_dir: Path, tasks: List[dict[str, Any]]) -> tuple[Path, Path]:
    """Write BACKLOG.json and BACKLOG.md from a normalized task dict list."""
    backlog = {"generated_at": now_iso(), "tasks": tasks}
    run_dir.mkdir(parents=True, exist_ok=True)
    bj = run_dir / "BACKLOG.json"
    try:
        atomic_write_json(bj, backlog)
    except Exception as ex:
        eprint(f"[WARN] Failed to write BACKLOG.json atomically: {ex}")
        bj.write_text(json.dumps(backlog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")

    md_lines = ["# BACKLOG", ""]
    for t in tasks:
        md_lines.append(f"- [ ] {t.get('id','')} {t.get('title','')}")
    md_lines.append("")
    bm = run_dir / "BACKLOG.md"
    try:
        atomic_write_text(bm, "\n".join(md_lines) + "\n")
    except Exception as ex:
        eprint(f"[WARN] Failed to write BACKLOG.md atomically: {ex}")
        bm.write_text("\n".join(md_lines) + "\n", encoding="utf-8", errors="replace")
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
                skills=[],
                skills_rationale=None,
                depends_on=[],
            )
        )
    return items


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        except (json.JSONDecodeError, ValueError) as exc:
            eprint(f"[WARN] STATE.json is corrupt ({exc}); backing up and resetting.")
            try:
                corrupt_path = path.with_suffix(".json.corrupt")
                import shutil
                shutil.copy2(path, corrupt_path)
            except Exception:
                pass
    return {"done": [], "failed": []}


_MAX_FAILED_ENTRIES = 200


def save_state(path: Path, state: dict[str, Any]) -> None:
    # Cap failed list to prevent unbounded growth in long-running sessions
    state = dict(state)
    if len(state.get("failed", [])) > _MAX_FAILED_ENTRIES:
        state["failed"] = state["failed"][-_MAX_FAILED_ENTRIES:]
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(path, state)
    except Exception as ex:
        eprint(f"[WARN] Failed to write state atomically: {ex}")
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
        atomic_write_text(backlog_md, "\n".join(out) + "\n")


def write_default_p0_backlog(run_dir: Path) -> None:
    """Fallback backlog used if PM fails to generate one.

    IMPORTANT: this must be repository-agnostic. We avoid hardcoded language/framework
    tasks to prevent the Dev agent from doing irrelevant work.
    """
    backlog = {
        "generated_at": now_iso(),
        "tasks": [
            {
                "id": "T0",
                "title": "PM failed to generate BACKLOG: write a diagnostic report and stop",
                "prompt": (
                    "PM 단계가 BACKLOG를 생성하지 못했습니다. 레포의 실제 코드는 수정하지 마세요. "
                    "현재 run_dir(이번 실행 폴더)에 `PM_FAILURE.md`를 생성하고 다음 내용을 기록하세요: "
                    "(1) 어떤 에러/원인으로 PM이 실패했는지 추정, (2) 재현/재시도 방법(예: --debug, --pm-model, --pm-timeout-seconds), "
                    "(3) 사용자가 확인해야 할 설정(execution_backend, OPENAI_API_KEY/ANTHROPIC_API_KEY, docs_dir 등). "
                    "작성 후 작업을 종료(추가 코드 변경 없이)하세요."
                ),
                "files": ["(run_dir)/PM_FAILURE.md"],
                "done_when": "PM_FAILURE.md exists in run_dir and no product/source files were modified.",
                "skills": [],
                "skills_rationale": None,
                "depends_on": [],
            }
        ],
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_json(run_dir / "BACKLOG.json", backlog)
    except Exception as ex:
        eprint(f"[WARN] Failed to write default BACKLOG.json atomically: {ex}")
        (run_dir / "BACKLOG.json").write_text(json.dumps(backlog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", errors="replace")

    md_lines = ["# BACKLOG", ""]
    for t in backlog["tasks"]:
        md_lines.append(f"- [ ] {t['id']} {t['title']}")
    md_lines.append("")
    try:
        atomic_write_text(run_dir / "BACKLOG.md", "\n".join(md_lines) + "\n")
    except Exception as ex:
        eprint(f"[WARN] Failed to write default BACKLOG.md atomically: {ex}")
        (run_dir / "BACKLOG.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8", errors="replace")
