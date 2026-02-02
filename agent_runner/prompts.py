from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


def codex_call_hint(autopilot: bool) -> str:
    if autopilot:
        return '{"approval-policy":"never","sandbox":"workspace-write","cwd":"."}'
    return '{"approval-policy":"on-request","sandbox":"workspace-write","cwd":"."}'


PM_INSTRUCTIONS_DEFAULT = (
    "You are a practical PM for a MAUI Blazor Hybrid app.\n"
    "Token-saving is critical: avoid broad scans, use the inventory/digest.\n"
    "You MUST write required files; avoid analysis paralysis.\n"
    "When asked to cover all files, you must not omit any file entry.\n"
)

DEV_INSTRUCTIONS_DEFAULT = (
    "You implement MAUI Blazor Hybrid frontend changes in the repo.\n"
    "Token-saving is critical: use targeted searches; don't refactor widely.\n"
    "You MUST produce compilation-safe diffs.\n"
)

QA_INSTRUCTIONS_DEFAULT = (
    "You produce a short actionable QA plan and build checks.\n"
    "Token-saving: keep it brief and concrete.\n"
)

PM_BOOTSTRAP_TEMPLATE_DEFAULT = """You are Planner/PM.

BOOTSTRAP MODE (first-time, expensive but must be done):
- You MUST create/overwrite the GLOBAL analysis file at:
  {analysis_md}
- It MUST cover EVERY git-tracked file listed in:
  {inv_md}
  Even if a file is binary/too large, it must be listed with \"skipped_reason\" and a short note.

What to write in PROJECT_ANALYSIS.md (required structure):
1) Executive summary (P0 readiness, biggest risks, immediate priorities)
2) Repo architecture map (folders/modules, where MAUI/Blazor pages/services/models live)
3) Supabase policy constraints (RPC for writes, Views/RPC for reads, no secrets in client)
4) File-by-file analysis (MANDATORY):
   - For each file path in REPO_INVENTORY.md, include:
     - Purpose (1-2 lines)
     - P0 relevance (P0/P1/Ignore)
     - Risks/Issues (if any)
     - Suggested actions (if any)
   - Keep each file entry short (3-8 lines). Do NOT omit any file.
5) P0 gap list (what is missing vs docs)
6) \"Next backlog\" section: must be actionable.

Then, generate run-local deliverables into this run folder:
- {run_dir}/REQUIREMENTS.md
- {run_dir}/AGENT_TASKS.md
- {run_dir}/BACKLOG.md
- {run_dir}/BACKLOG.json
- {run_dir}/NOTES.md

Repo root: {repo}
Run artifacts folder: {run_dir}
Docs folder: {docs_dir}
Docs read mode: {docs_read_mode}
Digest file (preferred): {digest_rel}

Hard rules:
- TOKEN SAVING: Prefer digest. Only open full docs if absolutely needed.
- Avoid broad repo scans: use REPO_INVENTORY.md as the file list; use targeted reads for critical files.
- Backlog tasks MUST be atomic and implementable within one Dev iteration.
- Each backlog task MUST be expected to produce a git diff.
- No questions. No waiting. Produce the files.

When editing/creating files, call Codex MCP with {codex_call_hint}.
"""

PM_INCREMENTAL_TEMPLATE_DEFAULT = """You are Planner/PM.

INCREMENTAL MODE (token-saving):
- Global analysis already exists at:
  {analysis_md}
- Do NOT redo full analysis.
- Update PROJECT_ANALYSIS.md by appending a Delta section for this run, and updating only impacted file entries.

Reference file list:
- {inv_md}

Git:
- prev_head: {prev_head}
- curr_head: {curr_head}
- changed files (name-only):
{changed_files_block}

Dev change-hints (optional, run-local; use as clues, not source-of-truth):
{hint_block}

Deliverables into run folder:
- {run_dir}/BACKLOG.md
- {run_dir}/BACKLOG.json
- {run_dir}/NOTES.md  (what changed, why, next)
(If REQUIREMENTS/AGENT_TASKS need updates, update them too.)

Rules:
- Keep backlog atomic; each task must create git diff.
- Avoid broad scans. Only inspect changed files + their direct dependencies.
- No questions. Output files and stop.

When editing/creating files, call Codex MCP with {codex_call_hint}.
"""

DEV_TASK_TEMPLATE_DEFAULT = """You are the Frontend Developer (MAUI Blazor Hybrid).

Implement ONLY this task now.

Task:
- ID: {task_id}
- Title: {task_title}

Implementation instructions:
{task_prompt}

Files to touch (keep minimal):
{files_hint}

Constraints (non-negotiable):
- No secrets in client. Never embed SERVICE_ROLE_KEY or CRON_SECRET.
- For PAD: writes MUST use RPC/Edge. Reads use Views/RPC. Do NOT direct-write forbidden tables.
- Use idempotency keys where required (client_tx_id).
- Keep changes incremental and compilation-safe.
- Avoid broad repo scan; use targeted rg/git ls-files.

Docs read mode: {docs_read_mode}
Digest file (preferred): {digest_rel}

Definition of done:
- {done_when}
- MUST produce a real git diff in the repo.
- Update {run_dir}/NOTES.md with: files changed, why, how to validate.

IMPORTANT (analysis update safety):
- Do NOT edit the global analysis file directly.
- Instead, write a short \"analysis hint\" markdown to:
  {analysis_hint_out}
  Include:
  - changed files (list)
  - what you changed and why (brief)
  - any new gaps discovered (brief)
This will be merged by PM incrementally later.

When editing files, call Codex MCP with {codex_call_hint}.
Repo root: {repo}
"""

QA_TEMPLATE_DEFAULT = """You are QA/Tester.
- Read {run_dir}/TEST.md and NOTES.md (if exists).
- Create:
  - {run_dir}/qa/TEST_PLAN.md
  - {run_dir}/qa/BUILD_CHECKS.md
- Keep it short and actionable (Windows + Android).
Repo: {repo}
"""


@dataclass(frozen=True)
class PromptStore:
    prompts_dir: Path

    def _read_if_nonempty(self, filename: str) -> Optional[str]:
        p = self.prompts_dir / filename
        try:
            if p.exists() and p.is_file():
                txt = p.read_text(encoding="utf-8", errors="replace")
                if txt.strip():
                    return txt
        except Exception:
            pass
        return None

    def get(self, name: str, default: str) -> str:
        # map name -> file
        filename = f"{name}.md"
        return self._read_if_nonempty(filename) or default

    def render(self, name: str, default: str, ctx: Dict[str, Any]) -> str:
        tmpl = self.get(name, default)
        return _safe_format(tmpl, ctx)


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _safe_format(tmpl: str, ctx: Dict[str, Any]) -> str:
    try:
        return tmpl.format_map(_SafeDict(ctx))
    except Exception:
        return tmpl


def ensure_default_prompt_files(prompts_dir: Path) -> None:
    """Create prompt files if they do not exist. Never overwrite existing files."""
    prompts_dir.mkdir(parents=True, exist_ok=True)
    defaults = {
        "pm_instructions.md": PM_INSTRUCTIONS_DEFAULT,
        "dev_instructions.md": DEV_INSTRUCTIONS_DEFAULT,
        "qa_instructions.md": QA_INSTRUCTIONS_DEFAULT,
        "pm_bootstrap_prompt.md": PM_BOOTSTRAP_TEMPLATE_DEFAULT,
        "pm_incremental_prompt.md": PM_INCREMENTAL_TEMPLATE_DEFAULT,
        "dev_task_prompt.md": DEV_TASK_TEMPLATE_DEFAULT,
        "qa_prompt.md": QA_TEMPLATE_DEFAULT,
    }
    for fn, content in defaults.items():
        p = prompts_dir / fn
        if not p.exists():
            p.write_text(content.strip() + "\n", encoding="utf-8", errors="replace")
