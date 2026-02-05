from __future__ import annotations

import argparse
import asyncio
import json
import os
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple
import inspect

from ..analysis_cache import merge_dev_hints_to_global_changelog
from ..docs import load_dotenv_best_effort, resolve_docs_dir, generate_docs_digest
from ..gates import run_build_gate_async, run_test_gate_async
from ..gitops import (
    git_head,
    git_changed_files,
    git_worktree_changed_files,
    git_porcelain,
    repo_fingerprint,
    create_checkpoint,
    restore_checkpoint,
    RepoCheckpoint,
    create_worktree,
    remove_worktree,
    handle_worktree_patch,
)
from ..inventory import build_repo_inventory, write_repo_inventory_files
from ..metrics import MetricsLogger
from ..pipeline import PipelineManager, make_stages
from ..pipeline.session import PipelineSession
from ..pipeline.stages.base import StageOutcome
from ..run_dir import make_run_dir, find_latest_run_dir
from ..schemas import pm_output_json_schema
from ..state import (
    load_backlog_json,
    parse_backlog_md,
    load_state,
    save_state,
    write_backlog_files,
    mark_backlog_done,
    write_default_p0_backlog,
    TaskItem,
)
from ..structured import parse_pm_output, dump_pretty, describe_parse_failure
from ..skills import (
    build_skills_context,
    build_skills_index,
    resolve_skills_roots,
    resolve_snapshot_dir,
    summarize_skills_index_capped,
    write_skills_snapshot,
)
from ..utils import force_utf8_stdio, eprint, now_iso, safe_write_text, has_quota_text


class StopRequested(Exception):
    pass

def _iter_exc_chain_quota(ex: BaseException):
    """Best-effort walk of exception cause/context chain."""
    seen: set[int] = set()
    cur: BaseException | None = ex
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        yield cur
        nxt = getattr(cur, "__cause__", None) or getattr(cur, "__context__", None)
        cur = nxt if isinstance(nxt, BaseException) else None


def _is_quota_error(ex: BaseException) -> bool:
    for e in _iter_exc_chain_quota(ex):
        try:
            msg = str(e)
        except Exception:
            msg = ""
        if has_quota_text(msg) or has_quota_text(repr(e)):
            return True
    return False



def _as_str_list(v: object) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        out: list[str] = []
        for it in v:
            s = str(it).strip()
            if s:
                out.append(s)
        return out
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        # allow comma-separated
        if "," in s:
            return [p.strip() for p in s.split(",") if p.strip()]
        return [p for p in s.split() if p]
    return [str(v).strip()] if str(v).strip() else []


def _inline_skills_for(role: str, inline_mode: str) -> bool:
    mode = str(inline_mode or "").strip().lower()
    if mode in ("none", ""):
        return False
    if mode == "both":
        return True
    return mode == role.lower()


def _format_skill_selection(skill_ids: list[str], skills_by_id: dict[str, Any]) -> str:
    if not skill_ids:
        return "(none)"
    lines: list[str] = []
    missing: list[str] = []
    for sid in skill_ids:
        rec = skills_by_id.get(sid)
        if rec is not None:
            try:
                resolved_path = rec.skill_path.resolve()
            except Exception:
                resolved_path = rec.skill_path
            lines.append(f"- {rec.name} ({sid})")
            lines.append(f"  - root: {rec.source_root}")
            lines.append(f"  - relative_path: {rec.relative_path}")
            lines.append(f"  - resolved_path: {resolved_path}")
        else:
            lines.append(f"- {sid} (missing)")
            missing.append(sid)
    if missing:
        lines.append("Missing skills: " + ", ".join(missing))
    return "\n".join(lines)


def _load_json_if_exists(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except Exception:
        return default
    return default


def _parse_setting_sources(v: object) -> list[str]:
    # Claude Agent SDK expects a list like ["user", "project", "local"].
    out: list[str] = []
    for s in _as_str_list(v):
        low = s.strip().lower()
        if not low:
            continue
        if low == "global":
            low = "user"
        if low in {"user", "project", "local"}:
            out.append(low)
    if not out:
        out = ["project"]
    return out


@dataclass(frozen=True)
class ClaudeCodeConfig:
    model: str
    permission_mode: str
    max_turns: int
    setting_sources: list[str]
    system_prompt_append: str
    continue_conversation: bool
    resume: str
    enable_file_checkpointing: bool

    # Advanced SDK toggles (best-effort: ignored when SDK doesn't support)
    user: str
    include_partial_messages: bool
    fork_session: bool
    max_thinking_tokens: Optional[int]

    pm_allowed_tools: list[str]
    pm_disallowed_tools: list[str]
    dev_allowed_tools: list[str]
    dev_disallowed_tools: list[str]
    qa_allowed_tools: list[str]
    qa_disallowed_tools: list[str]


def _load_claudecode_cfg(args: argparse.Namespace) -> ClaudeCodeConfig:
    return ClaudeCodeConfig(
        model=str(getattr(args, "claudecode_model", "sonnet") or "sonnet"),
        permission_mode=str(getattr(args, "claudecode_permission_mode", "acceptEdits") or "acceptEdits"),
        max_turns=int(getattr(args, "claudecode_max_turns", 32) or 32),
        setting_sources=_parse_setting_sources(getattr(args, "claudecode_setting_sources", "project")),
        system_prompt_append=str(getattr(args, "claudecode_system_prompt_append", "") or ""),
        continue_conversation=bool(getattr(args, "claudecode_continue_conversation", False)),
        resume=str(getattr(args, "claudecode_resume", "") or ""),
        enable_file_checkpointing=bool(getattr(args, "claudecode_enable_file_checkpointing", False)),

        user=str(getattr(args, "claudecode_user", "") or ""),
        include_partial_messages=bool(getattr(args, "claudecode_include_partial_messages", False)),
        fork_session=bool(getattr(args, "claudecode_fork_session", False)),
        max_thinking_tokens=(
            int(getattr(args, "claudecode_max_thinking_tokens", 0) or 0)
            if int(getattr(args, "claudecode_max_thinking_tokens", 0) or 0) > 0
            else None
        ),
        pm_allowed_tools=_as_str_list(getattr(args, "claudecode_pm_allowed_tools", "Read,Grep,Glob,Write,Edit")),
        pm_disallowed_tools=_as_str_list(getattr(args, "claudecode_pm_disallowed_tools", "")),
        dev_allowed_tools=_as_str_list(getattr(args, "claudecode_dev_allowed_tools", "Read,Write,Edit,Grep,Glob,Bash")),
        dev_disallowed_tools=_as_str_list(getattr(args, "claudecode_dev_disallowed_tools", "")),
        qa_allowed_tools=_as_str_list(getattr(args, "claudecode_qa_allowed_tools", "Read,Grep,Glob,Bash")),
        qa_disallowed_tools=_as_str_list(getattr(args, "claudecode_qa_disallowed_tools", "")),
    )


def _filter_kwargs_for_ctor(cls: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    """Filter kwargs to parameters actually supported by cls.__init__.

    The Claude Agent SDK evolves quickly; this keeps us compatible with
    older/newer versions by ignoring unknown constructor args.
    """
    try:
        sig = inspect.signature(cls)
        allowed = set(sig.parameters.keys())
        # dataclass-like constructors often include **kwargs; if so, keep all.
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            return kwargs
        return {k: v for k, v in kwargs.items() if k in allowed}
    except Exception:
        return kwargs


def _pick_run_dir(repo: Path, args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "run_dir", "") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    # resume latest
    if bool(getattr(args, "resume_latest", False)):
        latest = find_latest_run_dir(repo)
        if latest:
            return latest

    return make_run_dir(repo)


def _rel(repo: Path, p: Path) -> str:
    try:
        return p.resolve().relative_to(repo.resolve()).as_posix()
    except Exception:
        return p.as_posix()


def _pm_prompt(
    *,
    repo: Path,
    run_dir: Path,
    inventory_md: str,
    analysis_md: str,
    kind: str,
    changed_files: list[str],
    digest_rel: str,
    docs_read_mode: str,
    skills_index_summary: str,
) -> str:
    changed_block = "\n".join([f"- {p}" for p in changed_files[:200]]) if changed_files else "(none)"

    docs_hint = ""
    if docs_read_mode == "digest" and digest_rel:
        docs_hint = (
            "\n\n[DOCS]\n"
            f"- You may read the pre-generated docs digest at: `{digest_rel}`\n"
            "- Use it for high-level architecture/usage context; do not copy large sections verbatim.\n"
        )

    if kind == "bootstrap":
        mode_hint = (
            "You are running in BOOTSTRAP mode.\n"
            "- Create or update the global analysis markdown at `analysis_md` with a structured overview of the repo.\n"
            "- Cover key modules, critical flows, risks, and how to run/build/test.\n"
            "- Keep it concise and stable; prefer headings + bullet points.\n"
        )
    elif kind in {"incremental", "refresh"}:
        mode_hint = (
            f"You are running in {kind.upper()} mode.\n"
            "- Update the existing global analysis markdown (do NOT rewrite everything).\n"
            "- Append a short delta entry under the existing 'ChangeLog (auto-appended)' section if present, else add it.\n"
            "- Focus primarily on changed files and new/remaining tasks.\n"
        )
    else:
        mode_hint = "You may skip if nothing changed, but still ensure BACKLOG exists.\n"

    schema_hint = "\n".join(
        [
            "{",
            "  \"kind\": \"bootstrap|incremental|refresh|skip\",",
            "  \"summary\": \"...\",",
            "  \"tasks\": [ { \"id\": \"T01\", \"title\": \"...\", \"prompt\": \"...\", \"files\": [\"...\"], \"done_when\": \"...\", \"skills\": [\"...\"], \"skills_rationale\": \"...\" } ],",
            "  \"notes_md\": \"(optional markdown)\",",
            "  \"warnings\": [\"...\"],",
            "  \"open_questions\": [\"...\"],",
            "  \"analysis_updated\": true,",
            "  \"analysis_path\": \".doc/PM_CACHE/PROJECT_ANALYSIS.md\"",
            "}",
        ]
    )

    return f"""You are the PM (project manager) agent for AgentCLI.

[GOALS]
1) Ensure the global project analysis markdown exists and is up-to-date:
   - Path: `{analysis_md}`
2) Generate a practical development backlog (tasks) for the Dev agent.

[CONTEXT]
- Repo root: `{_rel(repo, repo)}`
- Run directory: `{_rel(repo, run_dir)}`
- Repo inventory (all files, with size/binary hints): `{inventory_md}`
- Recently changed files (HEAD/worktree):
{changed_block}
 - SKILLS_INDEX summary (select skill_id per task; do NOT inline full skill text):
{skills_index_summary}
{docs_hint}
{mode_hint}
[IMPORTANT RULES]
- You may use tools to read/search files. Prefer Grep/Glob + targeted reads.
- Avoid re-reading huge files unless necessary.
- DO NOT modify product/source code in PM stage.
- You MAY edit/create `{analysis_md}` only.
- If a SKILLS_INDEX summary is provided, include skills and skills_rationale per task.

[OUTPUT CONTRACT]
- Your FINAL message MUST be valid JSON that matches this schema:
{schema_hint}
- Do NOT wrap JSON in markdown fences.
- `tasks` should be concrete and actionable. Each task MUST include `prompt` and `done_when`.
- Set `analysis_updated` true if you edited `{analysis_md}`. Set `analysis_path` to `{analysis_md}`.
"""


def _dev_prompt(repo: Path, run_dir: Path, task: TaskItem, skills_context: str) -> str:
    files_hint = "\n".join([f"- {f}" for f in (task.files or [])[:50]]) if task.files else "(not specified)"
    return f"""You are the Dev agent.

[ONE TASK ONLY]
- Implement exactly this task: {task.id} {task.title}

[TASK PROMPT]
{task.prompt}

[FILES HINT]
{files_hint}

[SELECTED SKILLS]
{skills_context}

[ACCEPTANCE]
- done_when: {task.done_when}
- You MUST produce a meaningful git diff for the repository (unless the task explicitly says no code changes).
- Keep changes minimal and consistent with existing style.

[WORKDIR]
- Repo root: `{_rel(repo, repo)}`
- Run dir: `{_rel(repo, run_dir)}` (you may write logs/reports here)

[IMPORTANT]
- If you need to create an operational note for PM, write a short markdown hint into:
  `{_rel(repo, run_dir / 'analysis_hints' / (task.id + '.md'))}`
"""


def _qa_prompt(repo: Path, run_dir: Path, recent_done_ids: list[str], skills_context: str) -> str:
    done_block = "\n".join([f"- {x}" for x in recent_done_ids]) if recent_done_ids else "(none)"
    return f"""You are the QA agent.

[CONTEXT]
- Repo root: `{_rel(repo, repo)}`
- Run dir: `{_rel(repo, run_dir)}`

[RECENT COMPLETED TASKS]
{done_block}

[SKILLS CONTEXT]
{skills_context}

[GOAL]
- Review recent changes. Look for obvious bugs, missing edge cases, and regression risks.
- If tests/build are available, suggest how to run them.

[OUTPUT]
- Write a concise markdown QA report.
"""


async def _collect_messages(stream: Any, *, stop_path: Path, debug: bool) -> Tuple[str, Optional[Any]]:
    """Drain Claude Agent SDK messages.

    Returns: (assistant_text, structured_output_if_any)
    """

    text_parts: list[str] = []
    structured: Any = None

    # We avoid importing message classes eagerly; the SDK package may not exist.
    if hasattr(stream, "__aiter__"):
        iterator = stream
    elif hasattr(stream, "__iter__"):
        async def _sync_iter():
            for msg in stream:
                yield msg
        iterator = _sync_iter()
    else:
        raise RuntimeError("ClaudeSDKClient did not provide a message stream")

    async for msg in iterator:
        if stop_path.exists():
            raise StopRequested()

        msg_name = msg.__class__.__name__
        msg_type = getattr(msg, "type", None)

        if msg_name in {"AssistantMessage", "TextMessage"} or msg_type == "assistant":
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for blk in content:
                    t = getattr(blk, "text", None)
                    if isinstance(t, str) and t.strip():
                        text_parts.append(t)

        if msg_name in {"ResultMessage", "ResponseMessage"} or msg_type == "result":
            result = getattr(msg, "result", None)
            if isinstance(result, dict):
                structured = result
            elif isinstance(result, str) and result.strip():
                text_parts.append(result)
            so = getattr(msg, "structured_output", None)
            if so is not None:
                structured = so
            content = getattr(msg, "content", None)
            if isinstance(content, list):
                for blk in content:
                    t = getattr(blk, "text", None)
                    if isinstance(t, str) and t.strip():
                        text_parts.append(t)

        if debug:
            try:
                if msg_name in {"AssistantMessage", "ResultMessage", "ResponseMessage"}:
                    pass
            except Exception:
                pass

    return ("\n".join(text_parts).strip(), structured)


async def _start_query(client: Any, prompt: str) -> None:
    """Start a Claude SDK query. Message retrieval is handled separately."""

    try:
        result = client.query(prompt)
    except TypeError:
        result = client.query(prompt=prompt)

    if inspect.isawaitable(result):
        await result


async def _receive_messages(client: Any, *, stop_path: Path, debug: bool) -> Tuple[str, Optional[Any]]:
    """Receive messages from the Claude SDK client."""

    if hasattr(client, "receive_response"):
        stream = client.receive_response()
        if inspect.isawaitable(stream):
            stream = await stream
        return await _collect_messages(stream, stop_path=stop_path, debug=debug)

    if hasattr(client, "receive_messages"):
        stream = client.receive_messages()
        if inspect.isawaitable(stream):
            stream = await stream
        return await _collect_messages(stream, stop_path=stop_path, debug=debug)

    def _coerce_stream(candidate: Any) -> Any | None:
        if hasattr(candidate, "__aiter__") or hasattr(candidate, "__iter__"):
            return candidate
        for attr_name in ("stream", "messages", "iter_messages"):
            if hasattr(candidate, attr_name):
                obj = getattr(candidate, attr_name)
                try:
                    stream = obj() if callable(obj) else obj
                except TypeError:
                    continue
                if hasattr(stream, "__aiter__") or hasattr(stream, "__iter__"):
                    return stream
        return None

    stream = _coerce_stream(client)
    if stream is not None:
        return await _collect_messages(stream, stop_path=stop_path, debug=debug)

    try:
        import claude_agent_sdk
    except Exception:
        claude_agent_sdk = None
    if claude_agent_sdk is not None:
        version = getattr(claude_agent_sdk, "__version__", None)
        if version:
            eprint(f"Claude Agent SDK version detected: {version}")

    raise RuntimeError("ClaudeSDKClient does not provide a message stream")


def _build_options(cfg: ClaudeCodeConfig, *, repo: Path, stage: str) -> Any:
    """Build Claude Agent SDK options for a stage."""

    try:
        from claude_agent_sdk import ClaudeAgentOptions
    except Exception as ex:
        raise RuntimeError(
            "claude_agent_sdk is not installed. Install it first (see: https://platform.claude.com/docs/ko/agent-sdk/python). "
            f"Original error: {ex}"
        )

    stage_low = (stage or "").strip().lower()
    if stage_low == "pm":
        # PM may need to update `.doc/PM_CACHE/PROJECT_ANALYSIS.md`.
        # Even if a user config predates this change (missing Write/Edit), we
        # add them here to preserve "works by default" behavior for ClaudeCode.
        allowed = list(cfg.pm_allowed_tools)
        for t in ("Write", "Edit"):
            if t not in allowed:
                allowed.append(t)
        disallowed = cfg.pm_disallowed_tools
        output_format = {"type": "json_schema", "schema": pm_output_json_schema()}
    elif stage_low == "dev":
        allowed = cfg.dev_allowed_tools
        disallowed = cfg.dev_disallowed_tools
        output_format = None
    else:
        allowed = cfg.qa_allowed_tools
        disallowed = cfg.qa_disallowed_tools
        output_format = None

    system_prompt = "".join(
        [
            "You are running inside AgentCLI. Follow the stage instructions exactly.\n",
            cfg.system_prompt_append.strip() + "\n" if cfg.system_prompt_append.strip() else "",
        ]
    )

    # NOTE: permission_mode default is 'acceptEdits' (safe for PM analysis edits and Dev code edits).
    kwargs: dict[str, Any] = {
        "model": cfg.model,
        "permission_mode": cfg.permission_mode,
        "max_turns": int(cfg.max_turns),
        "setting_sources": cfg.setting_sources,
        "allowed_tools": allowed,
        "disallowed_tools": disallowed,
        "output_format": output_format,
        "system_prompt": system_prompt,
        "continue_conversation": cfg.continue_conversation,
        "resume": cfg.resume or None,
        "enable_file_checkpointing": cfg.enable_file_checkpointing,

        # advanced toggles (best-effort)
        "user": cfg.user or None,
        "include_partial_messages": bool(cfg.include_partial_messages),
        "fork_session": bool(cfg.fork_session),
        "max_thinking_tokens": cfg.max_thinking_tokens,
    }
    kwargs = _filter_kwargs_for_ctor(ClaudeAgentOptions, kwargs)
    return ClaudeAgentOptions(**kwargs)


async def main_async_claudecode(args: argparse.Namespace, repo: Path) -> int:
    """Claude Code backend main.

    This backend aims to match Codex backend behavior:
    - same PM cache paths (.doc/PM_CACHE/PROJECT_ANALYSIS.md)
    - fingerprint-based incremental PM
    - stage pipeline with --roles
    """

    force_utf8_stdio()

    repo = repo.expanduser().resolve()
    if not repo.exists():
        eprint(f"[ERR] repo not found: {repo}")
        return 2

    # Load .env files (repo/.env and AgentCLI env_file, best-effort)
    load_dotenv_best_effort(repo, getattr(args, "env_file", ""))

    if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        eprint(
            "[WARN] ANTHROPIC_API_KEY is not set. "
            "Claude Agent SDK will use Claude Code authentication if available."
        )

    cfg = _load_claudecode_cfg(args)

    run_dir = _pick_run_dir(repo, args)
    run_dir.mkdir(parents=True, exist_ok=True)

    def _is_unsafe_path(raw: str) -> bool:
        try:
            return ".." in Path(raw).parts
        except Exception:
            return True

    for _name, _value in (("env_file", getattr(args, "env_file", "") or ""), ("prompts_dir", getattr(args, "prompts_dir", "") or "")):
        if str(_value).strip() and _is_unsafe_path(str(_value)):
            msg = (
                "# Validation failure\n\n"
                f"Blocked unsafe path for `{_name}`: `{_value}`\n\n"
                "Path traversal patterns like `..` are not allowed. Use an absolute path or a safe relative path.\n"
            )
            safe_write_text(run_dir / "VALIDATION_FAILURE.md", msg)
            eprint(f"[STOP] Validation failure for {_name}: {_value}")
            return 2

    source_repo = repo
    worktree_dir: Optional[Path] = None
    if bool(getattr(args, "worktree_isolation", False)):
        worktree_dir = run_dir / "worktree"
        try:
            create_worktree(source_repo, worktree_dir)
        except Exception as ex:
            eprint(f"[STOP] Failed to create worktree: {ex}")
            return 2
        repo = worktree_dir

    # Ensure we operate within repo root
    try:
        os.chdir(repo)
    except Exception:
        pass

    # STOP file
    stop_path = run_dir / "STOP"

    metrics = MetricsLogger(run_dir)

    # Global PM cache (shared across runs)
    pm_cache_dir = repo / ".doc" / "PM_CACHE"
    pm_cache_dir.mkdir(parents=True, exist_ok=True)
    analysis_md = pm_cache_dir / "PROJECT_ANALYSIS.md"

    # Drift guard for PM incremental
    pm_fp_path = pm_cache_dir / "PM_LAST_FINGERPRINT.json"
    pm_fp_obj = _load_json_if_exists(pm_fp_path, default={"fingerprint": "", "updated_at": ""})
    last_pm_fp = str(pm_fp_obj.get("fingerprint") or "")

    # Snapshot for HEAD tracking
    snapshot_json = pm_cache_dir / "REPO_SNAPSHOT.json"
    snapshot = _load_json_if_exists(snapshot_json, default={"head": "", "updated_at": ""})
    prev_head = str(snapshot.get("head") or "").strip()

    # Docs digest (optional)
    docs_dir = resolve_docs_dir(repo, getattr(args, "docs_dir", ""))
    digest_path = (repo / Path(getattr(args, "docs_digest_file", ".doc/Docs/DIGEST.md"))).resolve()
    digest_rel = _rel(repo, digest_path)
    docs_read_mode = str(getattr(args, "docs_read_mode", "off") or "off")

    if docs_read_mode == "digest" and bool(getattr(args, "generate_digest", False)) and docs_dir:
        try:
            generate_docs_digest(repo, docs_dir, digest_path)
        except Exception as ex:
            eprint(f"[WARN] docs digest generation failed: {ex}")

    skills_cfg = getattr(args, "skills", {}) if isinstance(getattr(args, "skills", {}), dict) else {}
    skills_enabled = bool(skills_cfg.get("enabled", False))
    skills_records = []
    skills_index_summary = "(skills disabled)"
    skills_by_id: dict[str, Any] = {}
    if skills_enabled:
        roots = resolve_skills_roots(repo, skills_cfg.get("roots", []))
        skills_records = build_skills_index(roots)
        skills_by_id = {r.skill_id: r for r in skills_records}
        snapshot_dir = resolve_snapshot_dir(run_dir, skills_cfg.get("snapshot_dir", ""))
        write_skills_snapshot(skills_records, snapshot_dir)
        skills_index_summary = summarize_skills_index_capped(
            skills_records,
            max_items=int(skills_cfg.get("pm_summary_max_items", 0) or 0),
            max_chars=int(skills_cfg.get("pm_summary_max_chars", 0) or 0),
        )

    # Run-local state
    backlog_json_path = run_dir / "BACKLOG.json"
    backlog_md_path = run_dir / "BACKLOG.md"
    state_path = run_dir / "STATE.json"

    dev_hints_dir = run_dir / "analysis_hints"
    dev_hints_dir.mkdir(parents=True, exist_ok=True)

    # Behavior flags
    build_enabled = (not bool(getattr(args, "no_build", False))) or bool(getattr(args, "require_build", False))
    run_tests = bool(getattr(args, "run_tests", False))
    stop_on_no_diff = (not bool(getattr(args, "allow_no_diff", False))) or bool(getattr(args, "stop_if_no_diff", False))

    build_cmd = getattr(args, "build_cmd", [])
    test_cmd = getattr(args, "test_cmd", [])

    continuous = bool(getattr(args, "continuous", False) or getattr(args, "loop", False))

    roles_raw = str(getattr(args, "roles", "PM,Dev,QA") or "PM,Dev,QA")
    plugins_allowlist = getattr(args, "plugins_allowlist", []) or []
    if isinstance(plugins_allowlist, str):
        plugins_allowlist = [p.strip() for p in plugins_allowlist.split(",") if p.strip()]

    try:
        stages = make_stages(
            roles_raw,
            plugins_enabled=bool(getattr(args, "plugins_enabled", False)),
            plugins_allowlist=list(plugins_allowlist),
            plugins_strict=bool(getattr(args, "plugins_strict", True)),
        )
    except Exception as ex:
        safe_write_text(run_dir / "PLUGIN_LOAD_FAILURE.md", f"# Plugin load failure\n\n{ex}\n")
        eprint(f"[STOP] Plugin load failure: {ex}")
        if worktree_dir is not None:
            try:
                remove_worktree(source_repo, worktree_dir)
            except Exception as rm_ex:
                eprint(f"[WARN] Failed to remove worktree: {rm_ex}")
        return 1
    pipeline_mgr = PipelineManager(stages)

    # --- Sync helpers required by PipelineSession ---

    def ensure_backlog() -> bool:
        if backlog_json_path.exists() or backlog_md_path.exists():
            return True
        return False

    def load_tasks() -> list[TaskItem]:
        if backlog_json_path.exists():
            return load_backlog_json(backlog_json_path)
        if backlog_md_path.exists():
            return parse_backlog_md(backlog_md_path)
        return []

    # --- Stage implementations ---

    async def pm_phase(cycle_idx: int) -> StageOutcome:
        # Skip PM if disabled in roles
        if not any((getattr(s, "name", "") or "").strip().lower() == "pm" for s in stages):
            return StageOutcome.skip("pm_disabled")

        # Check stop early
        if stop_path.exists():
            return StageOutcome.stop("stop_file", rc=0)

        curr_head = git_head(repo).strip()

        # Merge dev hints into global changelog (cheap context merge)
        try:
            merge_dev_hints_to_global_changelog(analysis_md, dev_hints_dir, curr_head)
        except Exception:
            pass

        # Inventory (always available for PM)
        try:
            inv = build_repo_inventory(repo)
        except Exception as ex:
            eprint(f"[PM] inventory build failed: {ex}")
            inv = []
        _, inv_md = write_repo_inventory_files(repo, pm_cache_dir, inv)

        # Change detection
        head_changed_files = git_changed_files(repo, prev_head, curr_head)
        wt_changed_files: list[str] = []
        if bool(getattr(args, "pm_include_working_tree", False)):
            try:
                wt_changed_files = git_worktree_changed_files(repo)
            except Exception as ex:
                eprint(f"[WARN] working-tree change detection failed: {ex}")
                wt_changed_files = []
        changed_files = sorted(set([*head_changed_files, *wt_changed_files]))

        repo_fp = repo_fingerprint(repo)

        pm_refresh = bool(getattr(args, "pm_refresh_backlog", False))

        if (not pm_refresh) and last_pm_fp and (repo_fp == last_pm_fp) and ensure_backlog():
            return StageOutcome.skip("pm_skip_fingerprint")

        kind = "bootstrap"
        if pm_refresh:
            kind = "refresh"
        elif analysis_md.exists() and last_pm_fp and repo_fp != last_pm_fp:
            kind = "incremental"
        elif analysis_md.exists() and last_pm_fp and repo_fp == last_pm_fp:
            kind = "skip"

        # Prompt
        prompt = _pm_prompt(
            repo=repo,
            run_dir=run_dir,
            inventory_md=_rel(repo, inv_md),
            analysis_md=_rel(repo, analysis_md),
            kind=kind,
            changed_files=changed_files,
            digest_rel=digest_rel,
            docs_read_mode=docs_read_mode,
            skills_index_summary=skills_index_summary,
        )

        options = _build_options(cfg, repo=repo, stage="PM")

        try:
            from claude_agent_sdk import ClaudeSDKClient
        except Exception as ex:
            return StageOutcome.fail("claude_agent_sdk_missing", rc=2, detail=str(ex))

        try:
            async with ClaudeSDKClient(options=options) as client:
                await _start_query(client, prompt)
                text, structured = await _receive_messages(
                    client,
                    stop_path=stop_path,
                    debug=bool(getattr(args, "debug", False)),
                )
        except StopRequested:
            return StageOutcome.stop("stop_requested", rc=130)
        except Exception as ex:
            if _is_quota_error(ex):
                # Quota/credits exhausted: stop gracefully so failover (if enabled) can take over.
                try:
                    stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass
                metrics.event("runner_stop", stage="pm", reason="quota_exhausted")
                return StageOutcome.stop("quota_exhausted", rc=0)

            eprint(f"[PM] Claude error: {ex}")
            if bool(getattr(args, "debug", False)):
                eprint(traceback.format_exc())
            # PM failure => fallback backlog
            write_default_p0_backlog(run_dir)
            return StageOutcome.ok("pm_failed_fallback_backlog")

        # Parse structured output
        pm_text = ""
        if structured is not None:
            try:
                pm_text = json.dumps(structured, ensure_ascii=False)
            except Exception:
                pm_text = str(structured)
        else:
            pm_text = text

        pm_out = parse_pm_output(pm_text, kind_hint=kind)
        if pm_out is None:
            describe_parse_failure("PM", pm_text)
            write_default_p0_backlog(run_dir)
            return StageOutcome.ok("pm_parse_failed_fallback_backlog")

        # Write backlog files
        try:
            tasks_dicts = [t.model_dump() for t in (pm_out.tasks or [])]  # pydantic v2
        except Exception:
            tasks_dicts = []
            for t in (pm_out.tasks or []):
                try:
                    tasks_dicts.append({
                        "id": getattr(t, "id", ""),
                        "title": getattr(t, "title", ""),
                        "prompt": getattr(t, "prompt", ""),
                        "files": getattr(t, "files", []) or [],
                        "done_when": getattr(t, "done_when", ""),
                        "skills": getattr(t, "skills", []) or [],
                        "skills_rationale": getattr(t, "skills_rationale", None),
                    })
                except Exception:
                    continue

        write_backlog_files(run_dir, tasks_dicts)

        # Update PM fingerprint
        try:
            pm_fp_path.write_text(
                json.dumps({"fingerprint": repo_fp, "updated_at": now_iso(), "head": curr_head}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            pass

        # Update snapshot head
        try:
            snapshot_json.write_text(
                json.dumps({"head": curr_head, "updated_at": now_iso()}, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            pass

        # Human-friendly PM summary
        try:
            (run_dir / "PM_SUMMARY.txt").write_text(
                dump_pretty({
                    "kind": pm_out.kind,
                    "summary": pm_out.summary,
                    "warnings": pm_out.warnings,
                    "open_questions": pm_out.open_questions,
                    "analysis_path": pm_out.analysis_path,
                })
                + "\n",
                encoding="utf-8",
                errors="replace",
            )
        except Exception:
            pass

        return StageOutcome.ok("pm_ok")

    async def dev_phase(cycle_idx: int) -> StageOutcome:
        # Do nothing if no tasks
        if stop_path.exists():
            return StageOutcome.stop("stop_file", rc=0)

        # Ensure tasks are loaded (PipelineManager already did this in continuous mode)
        tasks: list[TaskItem] = list(getattr(session, "tasks", []) or [])  # type: ignore[name-defined]

        state = load_state(state_path)
        done_set = set([str(x) for x in (state.get("done") or [])])

        before_done_count = len(done_set)
        completed_this_cycle: list[str] = []

        # Config
        autopilot = bool(getattr(args, "autopilot", False))
        max_turns_per_task = int(getattr(args, "max_turns_per_task", 12) or 12)
        max_tasks_per_cycle = int(getattr(args, "max_tasks_per_cycle", 1) or 1)

        # Find next pending tasks
        pending = [t for t in tasks if t.id not in done_set]
        if not pending:
            return StageOutcome.skip("dev_no_pending")

        # Dev options
        options = _build_options(cfg, repo=repo, stage="Dev")

        try:
            from claude_agent_sdk import ClaudeSDKClient
        except Exception as ex:
            return StageOutcome.fail("claude_agent_sdk_missing", rc=2, detail=str(ex))

        executed = 0

        for task in pending:
            if stop_path.exists():
                return StageOutcome.stop("stop_file", rc=0)
            if executed >= max_tasks_per_cycle:
                break

            executed += 1
            skills_context = _format_skill_selection(task.skills or [], skills_by_id)
            prompt = _dev_prompt(repo, run_dir, task, skills_context)

            # checkpoint before edits
            cp: Optional[RepoCheckpoint] = None
            try:
                cp = create_checkpoint(repo, run_dir / "checkpoints" / task.id)
            except Exception:
                cp = None

            def _restore_or_stop(reason: str, before_porcelain: str) -> tuple[bool, str]:
                if not cp:
                    return True, ""
                current_porcelain = git_porcelain(repo)
                if before_porcelain == current_porcelain:
                    return True, ""
                try:
                    restore_checkpoint(
                        repo,
                        cp,
                        dangerous=bool(getattr(args, "dangerous_git_rollback", False)),
                        run_dir=run_dir,
                        stop_path=stop_path,
                    )
                    metrics.event("rollback", task=task.id, reason=reason)
                    return True, ""
                except Exception as ex:
                    detail = str(ex)
                    blocked = "blocked" in detail.lower()
                    fail_reason = "rollback_blocked" if blocked else "rollback_failed"
                    state.setdefault("failed", []).append({"task": task.id, "reason": fail_reason, "detail": detail})
                    save_state(state_path, state)
                    metrics.event("rollback_failed", task=task.id, reason=reason, detail=detail)
                    eprint(f"[STOP] Rollback {fail_reason}: {detail}")
                    return False, fail_reason

            before = git_porcelain(repo)

            # Run the agent
            try:
                async with ClaudeSDKClient(options=options) as client:
                    await _start_query(client, prompt)
                    _text, _structured = await _receive_messages(
                        client,
                        stop_path=stop_path,
                        debug=bool(getattr(args, "debug", False)),
                    )
            except StopRequested:
                if cp:
                    ok, fail_reason = _restore_or_stop("stop_requested", before)
                    if not ok:
                        return StageOutcome.fail(fail_reason, rc=1)
                return StageOutcome.stop("stop_requested", rc=130)
            except Exception as ex:
                if _is_quota_error(ex):
                    # Quota/credits exhausted: rollback and stop gracefully.
                    if cp:
                        ok, fail_reason = _restore_or_stop("quota_exhausted", before)
                        if not ok:
                            return StageOutcome.fail(fail_reason, rc=1)
                    state.setdefault("warnings", []).append({"task": task.id, "reason": "quota_exhausted", "detail": str(ex)})
                    save_state(state_path, state)
                    try:
                        stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                    except Exception:
                        pass
                    metrics.event("runner_stop", stage="dev", task=task.id, reason="quota_exhausted")
                    return StageOutcome.stop("quota_exhausted", rc=0)

                eprint(f"[DEV] Claude error: {ex}")
                if bool(getattr(args, "debug", False)):
                    eprint(traceback.format_exc())
                if cp:
                    ok, fail_reason = _restore_or_stop("exception", before)
                    if not ok:
                        return StageOutcome.fail(fail_reason, rc=1)
                state.setdefault("failed", []).append({"task": task.id, "reason": "exception", "detail": str(ex)})
                save_state(state_path, state)
                return StageOutcome.fail("dev_exception", rc=1, detail=str(ex))

            after = git_porcelain(repo)
            changed = (before != after)

            if stop_on_no_diff and (not changed):
                eprint(f"[STOP] No diff produced for {task.id}.")
                if cp:
                    ok, fail_reason = _restore_or_stop("no_diff", before)
                    if not ok:
                        return StageOutcome.fail(fail_reason, rc=1)
                state.setdefault("failed", []).append({"task": task.id, "reason": "no_diff"})
                save_state(state_path, state)
                return StageOutcome.fail("no_diff", rc=1)

            # Gates (run in thread so event loop stays responsive)
            if build_enabled:
                ok = await run_build_gate_async(
                    repo=repo,
                    build_cmd=build_cmd,
                    build_timeout_sec=int(getattr(args, "build_timeout_seconds", 1800) or 1800),
                    legacy_build_target=str(getattr(args, "dotnet_build_target", "") or ""),
                    log_path=(run_dir / "attempts" / task.id / "build.txt"),
                    stop_path=stop_path,
                )
                if not ok:
                    eprint(f"[STOP] Build failed after {task.id}.")
                    if cp:
                        ok_restore, fail_reason = _restore_or_stop("build_failed")
                        if not ok_restore:
                            return StageOutcome.fail(fail_reason, rc=1)
                    state.setdefault("failed", []).append({"task": task.id, "reason": "build_failed"})
                    save_state(state_path, state)
                    return StageOutcome.fail("build_failed", rc=1)

            if run_tests:
                ok = await run_test_gate_async(
                    repo=repo,
                    test_cmd=test_cmd,
                    test_timeout_sec=int(getattr(args, "test_timeout_seconds", 3600) or 3600),
                    legacy_test_target=str(getattr(args, "dotnet_test_target", "") or ""),
                    legacy_test_filter=str(getattr(args, "dotnet_test_filter", "") or ""),
                    log_path=(run_dir / "attempts" / task.id / "test.txt"),
                    stop_path=stop_path,
                )
                if not ok:
                    eprint(f"[STOP] Tests failed after {task.id}.")
                    if cp:
                        ok_restore, fail_reason = _restore_or_stop("test_failed")
                        if not ok_restore:
                            return StageOutcome.fail(fail_reason, rc=1)
                    state.setdefault("failed", []).append({"task": task.id, "reason": "test_failed"})
                    save_state(state_path, state)
                    return StageOutcome.fail("test_failed", rc=1)

            # Mark done
            done_set.add(task.id)
            state.setdefault("done", []).append(task.id)
            save_state(state_path, state)

            try:
                mark_backlog_done(backlog_md_path, task.id)
            except Exception:
                pass

            completed_this_cycle.append(task.id)

        # Update session stats
        delta = len(done_set) - before_done_count
        session.ran_tasks = bool(delta > 0)  # type: ignore[name-defined]
        session.done_delta = int(delta)  # type: ignore[name-defined]

        if delta > 0:
            return StageOutcome.ok("dev_tasks_completed")
        return StageOutcome.skip("dev_no_progress")

    async def qa_phase(cycle_idx: int) -> StageOutcome:
        if stop_path.exists():
            return StageOutcome.stop("stop_file", rc=0)

        qa_always = bool(getattr(args, "qa_always", False))

        state = load_state(state_path)
        done_ids: list[str] = []
        try:
            done_ids = list(state.get("done", []))
        except Exception:
            done_ids = []

        if (not qa_always) and (not getattr(session, "ran_tasks", False)):  # type: ignore[name-defined]
            return StageOutcome.skip("qa_skip_no_tasks")

        skills_context = "(skills disabled)"
        if skills_enabled:
            skill_ids: list[str] = []
            for t in load_tasks():
                skill_ids.extend(t.skills or [])
            deduped = list(dict.fromkeys([s for s in skill_ids if s]))
            selected_records = [skills_by_id[sid] for sid in deduped if sid in skills_by_id]
            include_excerpts = _inline_skills_for("qa", skills_cfg.get("inline_mode", ""))
            skills_context = build_skills_context(
                selected_records,
                max_excerpt_lines=int(skills_cfg.get("max_excerpt_lines", 0) or 0),
                total_char_cap=int(skills_cfg.get("qa_max_total_chars", 0) or 0),
                include_excerpts=include_excerpts,
            )
            missing = [sid for sid in deduped if sid not in skills_by_id]
            if missing:
                skills_context += "\nMissing skills: " + ", ".join(missing)

        prompt = _qa_prompt(repo, run_dir, done_ids[-10:], skills_context)
        options = _build_options(cfg, repo=repo, stage="QA")

        try:
            from claude_agent_sdk import ClaudeSDKClient
        except Exception as ex:
            return StageOutcome.fail("claude_agent_sdk_missing", rc=2, detail=str(ex))

        try:
            async with ClaudeSDKClient(options=options) as client:
                await _start_query(client, prompt)
                md, _structured = await _receive_messages(
                    client,
                    stop_path=stop_path,
                    debug=bool(getattr(args, "debug", False)),
                )
        except StopRequested:
            return StageOutcome.stop("stop_requested", rc=130)
        except Exception as ex:
            if _is_quota_error(ex):
                try:
                    stop_path.write_text("quota exhausted\n", encoding="utf-8", errors="replace")
                except Exception:
                    pass
                metrics.event("runner_stop", stage="qa", reason="quota_exhausted")
                return StageOutcome.stop("quota_exhausted", rc=0)

            eprint(f"[QA] Claude error: {ex}")
            if bool(getattr(args, "debug", False)):
                eprint(traceback.format_exc())
            return StageOutcome.ok("qa_failed")

        out_path = run_dir / "QA_REPORT.md"
        out_path.write_text((md or "") + "\n", encoding="utf-8", errors="replace")
        return StageOutcome.ok("qa_ok")

    # Wire the session (stages call session.pm_phase/dev_phase/qa_phase)
    session = PipelineSession(
        args=args,
        repo=repo,
        run_dir=run_dir,
        stop_path=stop_path,
        ensure_backlog=ensure_backlog,
        load_tasks=load_tasks,
        pm_phase=pm_phase,
        dev_phase=dev_phase,
        qa_phase=qa_phase,
    )

    # Execution modes
    loop = bool(getattr(args, "loop", False))
    sleep_s = int(getattr(args, "loop_sleep_seconds", 60) or 60)
    max_cycles = int(getattr(args, "loop_max_cycles", 0) or 0)

    cycle_idx = 0

    exit_code = 0
    while True:
        if stop_path.exists():
            metrics.event("stop_file")
            exit_code = 0
            break

        metrics.event("cycle_start", cycle=cycle_idx)

        try:
            res = await pipeline_mgr.run_cycle(session, cycle_idx=cycle_idx, continuous=continuous)
        except Exception as ex:
            eprint(f"[ERR] pipeline cycle exception: {ex}")
            if bool(getattr(args, "debug", False)):
                eprint(traceback.format_exc())
            exit_code = 1
            break

        metrics.event("cycle_end", cycle=cycle_idx, rc=res.rc, reason=res.reason, done_delta=res.done_delta)

        if not loop and not continuous:
            exit_code = 0
            break

        if res.rc != 0:
            exit_code = int(res.rc)
            break

        cycle_idx += 1

        if (max_cycles > 0) and (cycle_idx >= max_cycles):
            exit_code = 0
            break

        if not (loop or continuous):
            break

        await asyncio.sleep(max(1, sleep_s))

    if worktree_dir is not None:
        gitops_cfg = getattr(args, "gitops", {}) if isinstance(getattr(args, "gitops", {}), dict) else {}
        exclude_globs = gitops_cfg.get("untracked_exclude_globs", []) or []
        exit_code = handle_worktree_patch(
            repo,
            source_repo,
            run_dir,
            exit_code,
            exclude_globs=exclude_globs,
        )
        try:
            remove_worktree(source_repo, worktree_dir)
        except Exception as ex:
            eprint(f"[WARN] Failed to remove worktree: {ex}")

    return exit_code
