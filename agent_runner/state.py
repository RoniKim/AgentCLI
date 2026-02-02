from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import now_iso


@dataclass
class TaskItem:
    id: str
    title: str
    prompt: str
    files: list[str]
    done_when: str


def load_backlog_json(path: Path) -> list[TaskItem]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))

    # Allow both list[...] and {"tasks":[...]}
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
    for x in raw_tasks:
        if not isinstance(x, dict):
            continue
        items.append(
            TaskItem(
                id=str(x.get("id", "")).strip(),
                title=str(x.get("title", "")).strip(),
                prompt=str(x.get("prompt", "")).strip(),
                files=list(x.get("files", [])) if isinstance(x.get("files", []), list) else [],
                done_when=str(x.get("done_when", "")).strip(),
            )
        )
    return [t for t in items if t.id and t.title and t.prompt]


def parse_backlog_md(path: Path) -> list[TaskItem]:
    txt = path.read_text(encoding="utf-8", errors="replace")
    items: list[TaskItem] = []
    for line in txt.splitlines():
        m = re.match(r"^\s*-\s*\[\s*\]\s*(T\d+)\s+(.*)$", line)
        if not m:
            continue
        tid = m.group(1).strip()
        title = m.group(2).strip()
        items.append(TaskItem(id=tid, title=title, prompt=f"Implement {tid}: {title}", files=[], done_when="Git diff exists and build passes."))
    return items


def load_state(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    return {"done": [], "failed": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")


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
    """Fallback backlog used if PM fails to generate one."""
    backlog = {
        "generated_at": now_iso(),
        "tasks": [
            {
                "id": "T01",
                "title": "Startup minimum supported version gate",
                "prompt": "Implement startup flow: call rpc/get_current_app_version once, compare with current app version, and route to UpdateRequired page when below minimum. No secrets. Add safe error UI.",
                "files": ["MauiProgram.cs", "Components/Pages/UpdateRequired.razor", "Components/Pages/Home.razor", "Services/ApiService.cs"],
                "done_when": "Below-min app version shows UpdateRequired UX; normal path continues; build passes.",
            },
            {
                "id": "T02",
                "title": "Supabase API client wrapper (RPC/Views policy)",
                "prompt": "Create/adjust ApiService/Supabase wrapper enforcing: RPC for writes, Views/RPC for reads, retries/backoff for 429/5xx. Do not embed service-role/cron secrets.",
                "files": ["Services/ApiService.cs"],
                "done_when": "Reusable wrapper exists; no forbidden endpoints/keys; build passes.",
            },
            {
                "id": "T03",
                "title": "Auth-aware boot (session restore + token handling)",
                "prompt": "Implement session restore via SecureStorage. Handle access token expiry and refresh flow (if applicable), with safe retry/backoff. Route to sign-in when needed. Ensure no secrets stored in repo.",
                "files": ["Services/AuthService.cs", "Services/SecureStorageAdapter.cs"],
                "done_when": "Cold start restores session when available; invalid session routes to login; build passes.",
            },
            {
                "id": "T04",
                "title": "Dashboard P0 (get_dashboard)",
                "prompt": "Implement dashboard page that calls rpc/get_dashboard and renders KPI + recent transactions + sync status. Provide loading/error states. Refresh triggers re-call.",
                "files": ["Components/Pages/Home.razor", "Services/ApiService.cs"],
                "done_when": "Dashboard shows KPIs + recent tx + sync badge; errors handled; build passes.",
            },
            {
                "id": "T05",
                "title": "Transactions list (v_transactions_display)",
                "prompt": "Implement transactions list using GET v_transactions_display with filtering/paging. Add basic date range and type filter. Provide loading/error states.",
                "files": ["Components/Pages/Transactions/List.razor", "Services/ApiService.cs"],
                "done_when": "List loads and filters; paging works; build passes.",
            },
            {
                "id": "T06",
                "title": "Transaction detail (v_transactions_with_info)",
                "prompt": "Implement transaction detail view using v_transactions_with_info (by id). Include correction/void info. Add navigation from list.",
                "files": ["Components/Pages/Transactions/Detail.razor", "Services/ApiService.cs"],
                "done_when": "Detail loads by id; error handled; build passes.",
            },
            {
                "id": "T07",
                "title": "Transactions write (process/edit/reverse RPC)",
                "prompt": "Implement create/edit/reverse using RPCs: process_transaction_v2, edit_transaction_v2, reverse_transaction. Always send client_tx_id (idempotency). After success refresh dashboard.",
                "files": ["Components/Pages/Transactions/Edit.razor", "Services/ApiService.cs"],
                "done_when": "Create/edit/reverse works via RPC; no direct DML; build passes.",
            },
            {
                "id": "T08",
                "title": "Settings master data CRUD",
                "prompt": "Implement CRUD pages for accounts/cards/categories/fixed_expenses/liabilities using allowed method (direct table CRUD if docs allow, otherwise RPC). Never write forbidden core tables directly.",
                "files": ["Components/Pages/Settings/*.razor", "Services/ApiService.cs"],
                "done_when": "Settings CRUD works; no forbidden DML; build passes.",
            },
        ],
    }

    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "BACKLOG.json").write_text(json.dumps(backlog, ensure_ascii=False, indent=2), encoding="utf-8", errors="replace")

    md_lines = ["# BACKLOG", ""]
    for t in backlog["tasks"]:
        md_lines.append(f"- [ ] {t['id']} {t['title']}")
    md_lines.append("")
    (run_dir / "BACKLOG.md").write_text("\n".join(md_lines), encoding="utf-8", errors="replace")
