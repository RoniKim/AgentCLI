from __future__ import annotations

import asyncio
import atexit
import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from ..cli import DEFAULTS, parse_args
from ..config import AGENT_WORK_DIR
from ..config import load_config, save_config
from ..active_goal import (
    ActiveGoalError,
    build_active_goal_status,
    cancel_active_goal,
    clear_active_goal,
    complete_active_goal,
    create_active_goal,
    update_active_goal,
)
from ..pr_queue import build_telegram_pr_queue_detail, build_telegram_pr_queue_summary
from ..shell import _parse_kv_tokens
from .controller import RunnerController

_TELEGRAM_IMPORT_ERROR: Exception | None = None
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, ContextTypes
except Exception as _ex:  # pragma: no cover - import-time fallback
    _TELEGRAM_IMPORT_ERROR = _ex
    InlineKeyboardButton = None  # type: ignore[assignment]
    InlineKeyboardMarkup = None  # type: ignore[assignment]
    Update = None  # type: ignore[assignment]
    ApplicationBuilder = None  # type: ignore[assignment]
    CallbackQueryHandler = None  # type: ignore[assignment]
    CommandHandler = None  # type: ignore[assignment]
    ContextTypes = None  # type: ignore[assignment]


_RUN_START_ALLOWED_KEYS = {
    "autopilot",
    "loop",
    "continuous",
    "iterations",
    "max_turns_per_task",
    "worktree_isolation",
    "run_tests",
    "no_build",
    "debug",
    "resume_latest",
    "execution_backend",
    "roles",
    "qa_always",
    "profile",
    "pm_model",
    "dev_model",
    "qa_model",
    "reporter_model",
    "failover_enabled",
    "failover_backends",
    "failover_on",
    "failover_max_switches",
    "claudecode_model",
    "claudecode_pm_model",
    "claudecode_dev_model",
    "claudecode_qa_model",
    "claudecode_reporter_model",
}

_NOTIFY_EVENT_ALLOWED = {
    "run_start",
    "run_stop",
    "task_done",
    "task_failed",
    "quota",
    "error",
    "stalled",
    "backend_failover",
    "goals_refresh",
    "project_complete",
    "escalation",
    "phantom",
    "persistent_skip",
    "pm_garbage",
    "goals_updated",
}


def _mask_sensitive(text: str) -> str:
    if not text:
        return text
    out = text
    out = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_BOT_TOKEN]", out)
    out = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "[REDACTED_API_KEY]", out)
    out = re.sub(
        r"(?i)\b(token|password|secret)\b\s*[:=]\s*([^\s]+)",
        lambda m: f"{m.group(1)}=[REDACTED]",
        out,
    )
    return out


def _truncate(text: str, limit: int = 3500) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...(truncated)"


def _safe_text(text: str, limit: int = 3500) -> str:
    return _truncate(_mask_sensitive(text), limit=limit)


_EMOJI: dict[str, str] = {
    "run_start": "\U0001f7e2",      # 🟢
    "run_stop": "\U0001f534",       # 🔴
    "task_done": "\u2705",          # ✅
    "task_failed": "\u274c",        # ❌
    "quota": "\u26a0\ufe0f",        # ⚠️
    "error": "\U0001f6a8",          # 🚨
    "stalled": "\U0001f4a4",        # 💤
    "cycle": "\U0001f504",          # 🔄
    "info": "\U0001f4ca",           # 📊
    "detail": "\U0001f4cb",         # 📋
    "log": "\U0001f4dd",            # 📝
    "network_error": "\u26a1",      # ⚡
    "backend_failover": "\U0001f500",   # 🔀
    "goals_refresh": "\U0001f31f",      # 🌟
    "project_complete": "\U0001f3c6",   # 🏆
    "escalation": "\u2b06\ufe0f",       # ⬆️
    "phantom": "\U0001f47b",            # 👻
    "persistent_skip": "\u23e9",        # ⏩
    "pm_garbage": "\U0001f5d1\ufe0f",   # 🗑️
    "goals_updated": "\U0001f4c8",      # 📈
}

_REASON_KR: dict[str, str] = {
    "quota_exhausted": "\ucffc\ud0c0 \uc18c\uc9c4",                # 쿼타 소진
    "quota_utilization": "\ucffc\ud0c0 \uc0ac\uc6a9\ub7c9 \ucd08\uacfc",  # 쿼타 사용량 초과
    "stop_file": "\uc815\uc9c0 \ud30c\uc77c \uac10\uc9c0",          # 정지 파일 감지
    "all_tasks_done": "\ubaa8\ub4e0 \ud0dc\uc2a4\ud06c \uc644\ub8cc",      # 모든 태스크 완료
    "all_tasks_attempted": "\ubaa8\ub4e0 \ud0dc\uc2a4\ud06c \uc2dc\ub3c4 \uc644\ub8cc",  # 모든 태스크 시도 완료
    "project_complete": "\ud504\ub85c\uc81d\ud2b8 \uc644\ub8cc",    # 프로젝트 완료
    "no_tasks": "\ud0dc\uc2a4\ud06c \uc5c6\uc74c",                  # 태스크 없음
    "pm_refresh_no_backlog": "PM \uac31\uc2e0 \ud6c4 \ubc31\ub85c\uadf8 \uc5c6\uc74c",  # PM 갱신 후 백로그 없음
    "prepared_only": "\uc900\ube44\ub9cc \uc644\ub8cc",              # 준비만 완료
    "idle_exit": "\uc720\ud734 \uc885\ub8cc",                       # 유휴 종료
    "ok": "\uc815\uc0c1 \uc885\ub8cc",                               # 정상 종료
}


def _fmt_uptime(seconds: int | float) -> str:
    total = max(0, int(seconds))
    if total < 60:
        return f"{total}\ucd08"  # N초
    m, s = divmod(total, 60)
    if m < 60:
        return f"{m}\ubd84 {s}\ucd08"  # N분 N초
    h, m = divmod(m, 60)
    return f"{h}\uc2dc\uac04 {m}\ubd84 {s}\ucd08"  # N시간 N분 N초


def _fmt_reason_kr(reason: str) -> str:
    text = str(reason or "").strip()
    if not text:
        return "(\uc5c6\uc74c)"  # (없음)
    return _REASON_KR.get(text, text)


def _fmt_progress(done: int, failed: int, warnings: int) -> str:
    return (
        f"{_EMOJI['task_done']} \uc644\ub8cc {done} / "   # ✅ 완료
        f"{_EMOJI['task_failed']} \uc2e4\ud328 {failed} / "  # ❌ 실패
        f"{_EMOJI['quota']} \uacbd\uace0 {warnings}"       # ⚠️ 경고
    )


def _fmt_running_status(running: bool) -> str:
    if running:
        return f"{_EMOJI['run_start']} \uc2e4\ud589 \uc911"  # 🟢 실행 중
    return f"{_EMOJI['run_stop']} \uc911\uc9c0\ub428"          # 🔴 중지됨


def _chunk_text(text: str, limit: int = 3500) -> list[str]:
    raw = _mask_sensitive(text or "")
    if len(raw) <= limit:
        return [raw]
    chunks: list[str] = []
    current = ""
    for line in raw.splitlines():
        piece = line + "\n"
        if len(piece) > limit:
            if current:
                chunks.append(current.rstrip("\n"))
                current = ""
            start = 0
            while start < len(piece):
                chunks.append(piece[start : start + limit].rstrip("\n"))
                start += limit
            continue
        if len(current) + len(piece) > limit:
            chunks.append(current.rstrip("\n"))
            current = piece
        else:
            current += piece
    if current:
        chunks.append(current.rstrip("\n"))
    return chunks if chunks else [""]

def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
        if not raw:
            return {}
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return dict(payload)
    except Exception:
        pass
    return {}


def _redact_path_like_text(text: str) -> str:
    out = re.sub(r"(?i)[A-Z]:\[^\s]+", "[path]", text)
    out = re.sub(r"(?<!\w)/(?:[^/\s]+/)+[^/\s]+", "[path]", out)
    return out


def _looks_like_raw_summary_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lowered = raw.lower()
    markers = (
        "system prompt",
        "user prompt",
        "assistant prompt",
        "ignore previous",
        "backend transcript",
        "raw transcript",
        "prompt injection",
        "diff --git",
        "traceback (most recent call last)",
        "stack trace",
        "<assistant",
        "<system",
        "<user",
    )
    if any(marker in lowered for marker in markers):
        return True
    if re.search(r"(?m)^\s*@@\s", raw):
        return True
    if re.search(r"(?m)^\d{4}-\d{2}-\d{2}[ T].*(?:ERROR|WARN|INFO|DEBUG)", raw):
        return True
    return False


def _sanitize_summary_text(value: object, *, limit: int = 160) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    masked = _mask_sensitive(_redact_path_like_text(raw))
    normalized = re.sub(r"\s+", " ", masked).strip(" -:")
    if not normalized:
        return ""
    if _looks_like_raw_summary_text(raw) or _looks_like_raw_summary_text(normalized):
        return "[redacted]"
    if len(normalized) > limit:
        return normalized[: max(1, limit - 3)].rstrip() + "..."
    return normalized


def _append_summary_value(values: list[str], value: str) -> None:
    text = str(value or "").strip()
    if not text or text in values:
        return
    values.append(text)


def _safe_summary_ref(value: object, *, label: str = "") -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if label == "artifact":
        name = Path(raw).name.strip()
        return f"artifact:{name}" if name else ""
    text = _sanitize_summary_text(raw, limit=48)
    if text == "[redacted]":
        return text
    return f"{label}:{text}" if label else text


def _experience_evidence_refs(item: dict[str, Any], *, run_dir: Path | None) -> list[str]:
    refs: list[str] = []
    task_id = _safe_summary_ref(item.get("task_id") or item.get("taskId"), label="task")
    if task_id:
        _append_summary_value(refs, task_id)
    run_id = _safe_summary_ref(item.get("run_id") or item.get("runId"), label="run")
    if run_id:
        _append_summary_value(refs, run_id)
    pr_id = _safe_summary_ref(item.get("pr_id") or item.get("prId") or item.get("packet_id") or item.get("packetId"), label="pr")
    if pr_id:
        _append_summary_value(refs, pr_id)
    goal_trace = item.get("goal_trace") if isinstance(item.get("goal_trace"), list) else item.get("goalTrace")
    if isinstance(goal_trace, list):
        for raw_goal in goal_trace[:2]:
            if not isinstance(raw_goal, dict):
                continue
            goal_ref = _safe_summary_ref(raw_goal.get("goal_ref") or raw_goal.get("goalRef"), label="goal")
            if goal_ref:
                _append_summary_value(refs, goal_ref)
    evidence = item.get("evidence")
    if isinstance(evidence, list):
        for raw_ref in evidence[:2]:
            if isinstance(raw_ref, dict):
                candidate = (
                    _safe_summary_ref(raw_ref.get("task_id") or raw_ref.get("taskId"), label="task")
                    or _safe_summary_ref(raw_ref.get("run_id") or raw_ref.get("runId"), label="run")
                    or _safe_summary_ref(raw_ref.get("packet_id") or raw_ref.get("packetId"), label="pr")
                    or _safe_summary_ref(raw_ref.get("goal_ref") or raw_ref.get("goalRef"), label="goal")
                    or _safe_summary_ref(raw_ref.get("artifact_path") or raw_ref.get("artifactPath") or raw_ref.get("path"), label="artifact")
                )
            else:
                raw_text = str(raw_ref or "").strip()
                candidate = _safe_summary_ref(raw_ref, label="artifact" if any(sep in raw_text for sep in ('/', '\\')) or raw_text.endswith('.json') else "")
            if candidate:
                _append_summary_value(refs, candidate)
    if run_dir is not None and len(refs) < 2:
        run_ref = _safe_summary_ref(run_dir.name, label="run")
        if run_ref:
            _append_summary_value(refs, run_ref)
    return refs[:3]


def _experience_source_priority(source_key: str) -> int:
    order = {
        "operator_actions": 0,
        "validation_lessons": 1,
        "merge_hints": 2,
        "task_lessons": 3,
        "lessons": 4,
        "items": 5,
    }
    return order.get(source_key, 99)


def _load_telegram_experience_blockers(repo: Path, *, run_dir: Path | None, limit: int = 3) -> dict[str, Any]:
    candidates: list[Path] = []
    if run_dir is not None:
        candidates.append(run_dir / "ANALYZER_SUMMARY.json")
    candidates.append(repo / AGENT_WORK_DIR / "experience" / "latest_summary.json")

    payload: dict[str, Any] = {}
    source_path = ""
    for candidate in candidates:
        payload = _load_json_object(candidate)
        if payload:
            source_path = candidate.as_posix()
            break
    if not payload:
        return {"source_path": "", "total": 0, "items": []}

    entries: list[tuple[str, dict[str, Any]]] = []
    for key in ("operator_actions", "validation_lessons", "merge_hints", "task_lessons", "lessons", "items"):
        raw_items = payload.get(key)
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                entries.append((key, dict(raw_item)))
            elif raw_item not in (None, "", False):
                entries.append((key, {"lesson": str(raw_item)}))

    normalized: list[dict[str, Any]] = []
    for source_key, raw_item in entries:
        text = _sanitize_summary_text(
            raw_item.get("lesson")
            or raw_item.get("summary")
            or raw_item.get("action")
            or raw_item.get("title")
            or raw_item.get("message")
            or raw_item.get("detail")
            or raw_item.get("text")
        )
        if not text:
            continue
        severity = str(raw_item.get("severity") or raw_item.get("status") or "").strip().lower()
        normalized.append(
            {
                "source_key": source_key,
                "priority": _experience_source_priority(source_key),
                "severity": severity,
                "text": text,
                "kind": _sanitize_summary_text(raw_item.get("kind") or source_key.replace("_", " "), limit=32) or source_key,
                "evidence_refs": _experience_evidence_refs(raw_item, run_dir=run_dir),
            }
        )
    normalized.sort(key=lambda item: (int(item.get("priority", 99)), 0 if item.get("severity") == "high" else 1, str(item.get("kind") or "")))
    return {
        "source_path": source_path,
        "total": len(normalized),
        "items": normalized[: max(1, int(limit))],
    }


def _humanize_summary_label(value: object) -> str:
    text = str(value or "").strip().replace("_", " ")
    return text or "-"


def _token_fingerprint(token: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        return "none"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]


def _pid_is_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    try:
        if os.name == "nt":
            try:
                import ctypes  # local import for Windows-only check

                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
                if int(handle) != 0:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            except Exception:
                proc = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {int(pid)}", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=4,
                )
                out = str(proc.stdout or "").strip()
                return bool(out) and ("No tasks are running" not in out)
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


class _TokenInstanceLock:
    def __init__(self, *, token_fingerprint: str, instance_name: str, repo: Path) -> None:
        self.token_fingerprint = str(token_fingerprint or "none")
        self.instance_name = str(instance_name or "").strip() or "default"
        self.repo = str(repo)
        self.path = Path(tempfile.gettempdir()) / f"agentcli_tg_{self.token_fingerprint}.lock"
        self._held = False

    def _read_existing(self) -> dict[str, Any]:
        try:
            raw = self.path.read_text(encoding="utf-8", errors="replace").strip()
            obj = json.loads(raw) if raw else {}
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        return {}

    def acquire(self) -> tuple[bool, str]:
        payload = {
            "pid": int(os.getpid()),
            "instance": self.instance_name,
            "repo": self.repo,
            "started_unix": float(time.time()),
            "token_fingerprint": self.token_fingerprint,
        }

        for _ in range(2):
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
                self._held = True
                return True, ""
            except FileExistsError:
                existing = self._read_existing()
                old_pid = int(existing.get("pid") or 0)
                if old_pid and not _pid_is_alive(old_pid):
                    try:
                        self.path.unlink(missing_ok=True)
                        continue
                    except Exception:
                        pass
                detail = (
                    f"lock={self.path} token_fp={self.token_fingerprint} "
                    f"pid={old_pid or 'unknown'} instance={existing.get('instance') or 'unknown'}"
                )
                return False, detail
            except Exception as ex:
                return False, f"lock_error: {ex}"

        return False, f"lock={self.path} token_fp={self.token_fingerprint}"

    def release(self) -> None:
        if not self._held:
            return
        try:
            self.path.unlink(missing_ok=True)
        except Exception:
            pass
        self._held = False


class TelegramControlService:
    def __init__(self, args: argparse.Namespace, controller: RunnerController | None = None) -> None:
        self.args = args
        self.repo = Path(str(args.repo)).expanduser().resolve()
        self.config_path = Path(str(getattr(args, "config", ""))).expanduser().resolve()

        telegram_cfg = getattr(args, "telegram", {})
        if not isinstance(telegram_cfg, dict):
            telegram_cfg = {}
        self.telegram_cfg = telegram_cfg

        self.bot_token = str(telegram_cfg.get("bot_token") or "").strip()
        self.token_fingerprint = _token_fingerprint(self.bot_token)
        self.pairing_code = str(telegram_cfg.get("pairing_code") or "").strip()
        self.tail_lines_default = int(telegram_cfg.get("tail_lines_default") or 50)
        self.poll_timeout_seconds = int(telegram_cfg.get("poll_timeout_seconds") or 30)
        self.instance_name = str(telegram_cfg.get("instance_name") or self.repo.name).strip() or self.repo.name

        raw_notify_events = telegram_cfg.get("notify_events") or []
        if isinstance(raw_notify_events, str):
            notify_values = [p.strip() for p in raw_notify_events.split(",") if p.strip()]
        elif isinstance(raw_notify_events, list):
            notify_values = [str(v).strip() for v in raw_notify_events if str(v).strip()]
        else:
            notify_values = []
        self.notify_events = {
            str(v).strip().lower()
            for v in notify_values
            if str(v).strip().lower() in _NOTIFY_EVENT_ALLOWED
        }
        self.send_cycle_summary = bool(telegram_cfg.get("send_cycle_summary", True))
        try:
            self.notify_poll_interval_seconds = max(2, int(telegram_cfg.get("notify_poll_interval_seconds") or 8))
        except Exception:
            self.notify_poll_interval_seconds = 8
        try:
            self.stalled_seconds = max(60, int(telegram_cfg.get("stalled_seconds") or 600))
        except Exception:
            self.stalled_seconds = 600

        self.allowed_chat_ids: set[int] = set()
        for value in telegram_cfg.get("allowed_chat_ids") or []:
            try:
                self.allowed_chat_ids.add(int(str(value).strip()))
            except Exception:
                continue

        runner_mode = str(telegram_cfg.get("runner_mode") or "thread").strip().lower()
        self.controller = controller or RunnerController(repo=self.repo, base_args=args, runner_mode=runner_mode)
        self._config_lock = threading.Lock()
        self._push_lock = threading.Lock()
        self._push_initialized = False
        self._push_state: dict[str, Any] = {
            "running": False,
            "run_dir": "",
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "reason": "",
            "last_metrics_mtime": 0.0,
            "stalled_notified": False,
        }
        self._file_offsets: dict[str, int] = {}
        self._cursor_run_dir: str = ""
        self._token_lock = _TokenInstanceLock(
            token_fingerprint=self.token_fingerprint,
            instance_name=self.instance_name,
            repo=self.repo,
        )
        self._token_lock_held = False
        self._atexit_registered = False
        self.controller.register_on_done(self._on_runner_done)

    async def _reply(self, update: Update, text: str) -> None:
        if not update.message:
            return
        for chunk in _chunk_text(text, limit=3500):
            await update.message.reply_text(chunk or " ")

    def acquire_token_lock(self) -> tuple[bool, str]:
        if self._token_lock_held:
            return True, ""
        ok, detail = self._token_lock.acquire()
        if ok:
            self._token_lock_held = True
            if not self._atexit_registered:
                try:
                    atexit.register(self.release_token_lock)
                    self._atexit_registered = True
                except Exception:
                    pass
        return ok, detail

    def release_token_lock(self) -> None:
        if not self._token_lock_held:
            return
        self._token_lock.release()
        self._token_lock_held = False

    def _is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chat_ids

    def _auth_failed_message(self) -> str:
        if not self.allowed_chat_ids:
            if self.pairing_code:
                return "\uc811\uadfc \uac70\ubd80: \uc5f0\uacb0\ub41c \ucc44\ud305\uc774 \uc5c6\uc2b5\ub2c8\ub2e4. /pair <\ucf54\ub4dc>\ub97c \uba3c\uc800 \uc2e4\ud589\ud558\uc138\uc694."  # 접근 거부: 연결된 채팅이 없습니다...
            return "\uc811\uadfc \uac70\ubd80: \ud5c8\uc6a9 \ubaa9\ub85d\uc774 \ube44\uc5b4 \uc788\uc2b5\ub2c8\ub2e4. telegram.pairing_code \ub610\ub294 allowed_chat_ids\ub97c \uc124\uc815\ud558\uc138\uc694."  # 접근 거부: 허용 목록이 비어 있습니다...
        return "\uc811\uadfc \uac70\ubd80: \uc774 chat_id\ub294 \ud5c8\uc6a9 \ubaa9\ub85d\uc5d0 \uc5c6\uc2b5\ub2c8\ub2e4."  # 접근 거부: 이 chat_id는 허용 목록에 없습니다.

    async def _require_auth(self, update: Update) -> bool:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        if chat_id and self._is_allowed(chat_id):
            return True
        if update.message:
            await update.message.reply_text(self._auth_failed_message())
        elif update.callback_query:
            await update.callback_query.answer(self._auth_failed_message(), show_alert=True)
        return False

    def _persist_allowlist(self, chat_id: int) -> None:
        with self._config_lock:
            cfg: dict[str, Any] = {}
            try:
                if self.config_path.exists():
                    loaded = load_config(self.config_path)
                    if isinstance(loaded, dict):
                        cfg = loaded
            except Exception:
                cfg = {}

            telegram_cfg = cfg.get("telegram")
            if not isinstance(telegram_cfg, dict):
                telegram_cfg = {}

            merged_ids: list[int] = []
            seen: set[int] = set()
            existing = telegram_cfg.get("allowed_chat_ids") or []
            if isinstance(existing, str):
                existing = [p.strip() for p in existing.split(",") if p.strip()]
            if not isinstance(existing, list):
                existing = []
            for value in existing:
                try:
                    normalized = int(str(value).strip())
                except Exception:
                    continue
                if normalized in seen:
                    continue
                seen.add(normalized)
                merged_ids.append(normalized)
            if chat_id not in seen:
                merged_ids.append(chat_id)

            telegram_cfg["allowed_chat_ids"] = merged_ids
            cfg["telegram"] = telegram_cfg
            save_config(self.config_path, cfg)

        self.allowed_chat_ids.add(chat_id)

    def _active_goal_summary_line(self) -> str:
        try:
            status = build_active_goal_status(self.repo)
        except Exception as ex:
            return f"active goal: ERROR ({ex})"
        goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
        progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
        if not goal:
            return f"active goal: {status.get('state') or 'missing'}"
        objective = _sanitize_summary_text(goal.get("objective"), limit=80)
        return (
            f"active goal: {progress.get('summary') or status.get('state') or 'missing'}"
            + (f" | {objective}" if objective else "")
        )

    def _format_status(self, data: dict[str, Any]) -> str:
        running = bool(data.get("running"))
        done = int(data.get("done") or 0)
        failed = int(data.get("failed") or 0)
        warnings = int(data.get("warnings") or 0)
        uptime = int(data.get("uptime_seconds") or 0)
        reason = str(data.get("reason") or "").strip()
        stop_file_exists = bool(data.get("stop_file_exists"))

        lines = [
            f"{_EMOJI['info']} {self.instance_name} \uc0c1\ud0dc",  # 📊 {instance} 상태
            f"  \uc0c1\ud0dc: {_fmt_running_status(running)}",       # 상태: 🟢/🔴
            f"  \ubaa8\ub4dc: {data.get('runner_mode') or 'thread'} | "
            f"\uacbd\ub85c: {data.get('repo') or self.repo} | "
            f"\uac00\ub3d9 \uc2dc\uac04: {_fmt_uptime(uptime)}",     # 모드/경로/가동 시간
            f"  \uc9c4\ud589: {_fmt_progress(done, failed, warnings)}",  # 진행: ✅ 완료 ...
            f"  {self._active_goal_summary_line()}",
            f"  \uc911\uc9c0 \uc0ac\uc720: {_fmt_reason_kr(reason)} | "
            f"STOP \ud30c\uc77c: {'\uc788\uc74c' if stop_file_exists else '\uc5c6\uc74c'}",  # 중지 사유/STOP 파일
        ]
        last_event = str(data.get("last_event") or "").strip()
        if last_event:
            lines.append(f"  \ub9c8\uc9c0\ub9c9 \uc774\ubca4\ud2b8: {last_event}")  # 마지막 이벤트
        return "\n".join(lines)

    def _parse_goal_options(self, raw_args: list[str]) -> tuple[dict[str, Any], list[str]]:
        opts: dict[str, Any] = {}
        positional: list[str] = []
        idx = 0
        while idx < len(raw_args):
            item = str(raw_args[idx] or "").strip()
            if not item:
                idx += 1
                continue
            if item in {"--replace", "-r"}:
                opts["replace"] = True
                idx += 1
                continue
            if item in {"--mode", "--etag", "--token-budget", "--time-budget", "--cycle-budget"}:
                if idx + 1 < len(raw_args):
                    opts[item.lstrip("-").replace("-", "_")] = raw_args[idx + 1]
                    idx += 2
                    continue
                idx += 1
                continue
            positional.append(item)
            idx += 1
        return opts, positional

    def _format_active_goal_status(self) -> str:
        status = build_active_goal_status(self.repo)
        goal = status.get("goal") if isinstance(status.get("goal"), dict) else {}
        namespaced = status.get("active_goal_status") if isinstance(status.get("active_goal_status"), dict) else {}
        lines = [
            "Active goal",
            f"state: {status.get('state') or 'missing'}",
            f"terminal: {namespaced.get('terminalReason') or namespaced.get('terminal_reason') or '-'}",
        ]
        if not goal:
            return "\n".join(lines)
        budgets = goal.get("budgets") if isinstance(goal.get("budgets"), dict) else {}
        usage = goal.get("usage") if isinstance(goal.get("usage"), dict) else {}
        progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
        lines.extend(
            [
                f"id: {goal.get('id') or ''}",
                f"mode: {goal.get('mode') or 'adaptive'}",
                f"revision: {goal.get('revision') or 0}",
                f"objective: {goal.get('objective') or ''}",
                f"progress: {progress.get('summary') or ''}",
                (
                    "budget: "
                    f"tokens={budgets.get('token_budget') or 0} "
                    f"time={budgets.get('time_budget_seconds') or 0}s "
                    f"cycles={budgets.get('cycle_budget') or 0}"
                ),
                (
                    "usage: "
                    f"tokens={usage.get('tokens_used') or 0} "
                    f"time={usage.get('time_used_seconds') or 0}s "
                    f"cycles={usage.get('cycles_used') or 0}"
                ),
            ]
        )
        return "\n".join(lines)

    def _build_detail_text(self, *, lines: int = 80) -> str:
        n = max(10, min(400, int(lines)))
        status = self.controller.status()
        run_dir = str(status.get("run_dir") or "").strip()
        running = bool(status.get("running"))
        done = int(status.get("done") or 0)
        failed = int(status.get("failed") or 0)
        warnings = int(status.get("warnings") or 0)
        reason = str(status.get("reason") or "").strip()

        out: list[str] = [
            f"{_EMOJI['detail']} {self.instance_name} \uc0c1\uc138",  # 📋 {instance} 상세
            f"  \uc0c1\ud0dc: {_fmt_running_status(running)}",
            f"  \ubaa8\ub4dc: {status.get('runner_mode') or 'thread'} | "
            f"\uacbd\ub85c: {status.get('repo') or self.repo}",
            f"  \uc9c4\ud589: {_fmt_progress(done, failed, warnings)}",
            f"  {self._active_goal_summary_line()}",
            f"  \uc911\uc9c0 \uc0ac\uc720: {_fmt_reason_kr(reason)}",
            "",
        ]

        if not run_dir:
            out.append("\uc2e4\ud589 \ub514\ub809\ud1a0\ub9ac \uc5c6\uc74c. /run_start\ub97c \uba3c\uc800 \uc2e4\ud589\ud558\uc138\uc694.")  # 실행 디렉토리 없음...
            return "\n".join(out)

        def _append_section(name: str, content: str) -> None:
            out.append(f"{_EMOJI['log']} {name} (\ucd5c\uadfc {n}\uc904)")  # 📝 name (최근 N줄)
            out.append(content.strip() or "(\ube44\uc5b4 \uc788\uc74c)")     # (비어 있음)
            out.append("")

        try:
            _append_section("cycle_summary.log", self.controller.tail(name="cycle_summary.log", lines=n))
        except Exception as ex:
            _append_section("cycle_summary.log", f"(\uc624\ub958) {ex}")

        try:
            _append_section("metrics.jsonl", self.controller.tail(name="metrics.jsonl", lines=n))
        except Exception as ex:
            _append_section("metrics.jsonl", f"(\uc624\ub958) {ex}")

        try:
            _append_section("run_summary.json", self.controller.tail(name="run_summary.json", lines=max(20, n // 2)))
        except Exception as ex:
            _append_section("run_summary.json", f"(\uc624\ub958) {ex}")

        if str(status.get("runner_mode") or "").strip().lower() == "subprocess":
            try:
                _append_section(
                    "telegram_runner_subprocess.log",
                    self.controller.tail(name="telegram_runner_subprocess.log", lines=n),
                )
            except Exception as ex:
                _append_section("telegram_runner_subprocess.log", f"(\uc624\ub958) {ex}")

        out.append("\ud301: /tail <\ud30c\uc77c\uba85> <\uc904\uc218> \ub85c \ud2b9\uc815 \ud30c\uc77c\uc744 \ud655\uc778\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.")  # 팁: /tail ...
        return "\n".join(out).strip()

    def _notify_enabled(self, event: str) -> bool:
        return event in self.notify_events

    def _send_push_sync(self, messages: list[str]) -> None:
        """Send push messages synchronously via raw HTTP (thread-safe, no PTB dependency)."""
        if not messages or not self.allowed_chat_ids or not self.bot_token:
            return
        import urllib.request

        limited = messages[:6]
        extra = len(messages) - len(limited)
        lines = [f"{_EMOJI['info']} {self.instance_name} \uc54c\ub9bc"]  # 📊 {instance} 알림
        lines.extend(limited)
        if extra > 0:
            lines.append(f"... (\uc678 {extra}\uac74)")  # ... (외 N건)
        payload = _safe_text("\n\n".join(lines))

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        for chat_id in sorted(self.allowed_chat_ids):
            try:
                data = json.dumps({"chat_id": chat_id, "text": payload}).encode("utf-8")
                req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=10)
            except Exception as ex:
                print(f"[TELEGRAM][WARN] Sync push failed chat_id={chat_id}: {ex}")

    def _on_runner_done(self, exit_code: int) -> None:
        """Callback invoked from runner thread when runner finishes. Sends immediate notification."""
        try:
            messages = self._collect_push_messages()
        except Exception:
            messages = []
        if messages:
            self._send_push_sync(messages)

    def _atexit_shutdown(self) -> None:
        """atexit handler: send final run_stop if runner was still tracked as running."""
        try:
            if not self._push_initialized:
                return
            with self._push_lock:
                was_running = bool(self._push_state.get("running", False))
            if not was_running:
                return
            messages = self._collect_push_messages()
            if messages:
                self._send_push_sync(messages)
        except Exception:
            pass

    def _short_run_id(self, run_dir: str) -> str:
        text = str(run_dir or "").strip()
        if not text:
            return "-"
        try:
            return Path(text).name or text
        except Exception:
            return text

    def _metrics_mtime(self, run_dir: str) -> float:
        text = str(run_dir or "").strip()
        if not text:
            return 0.0
        path = Path(text) / "metrics.jsonl"
        try:
            return float(path.stat().st_mtime)
        except Exception:
            return 0.0

    def _cursor_path(self, run_dir: str) -> Path:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", self.instance_name).strip("._")
        if not safe:
            safe = "default"
        return Path(run_dir) / f".telegram_notify_cursor_{safe}.json"

    def _load_cursor_for_run(self, run_dir: str, *, status: dict[str, Any], apply_state: bool = True) -> None:
        run_text = str(run_dir or "").strip()
        if not run_text:
            return
        if self._cursor_run_dir == run_text:
            return

        self._file_offsets = {}
        self._cursor_run_dir = run_text
        cursor_path = self._cursor_path(run_text)
        loaded_state: dict[str, Any] = {}
        loaded_offsets = False

        try:
            if cursor_path.exists():
                raw = cursor_path.read_text(encoding="utf-8", errors="replace").strip()
                payload = json.loads(raw) if raw else {}
                if isinstance(payload, dict):
                    offsets = payload.get("offsets")
                    if isinstance(offsets, dict):
                        for name, value in offsets.items():
                            rel = str(name or "").strip()
                            if not rel:
                                continue
                            try:
                                pos = max(0, int(value))
                            except Exception:
                                continue
                            key = str(Path(run_text) / rel)
                            self._file_offsets[key] = pos
                            loaded_offsets = True
                    state = payload.get("state")
                    if isinstance(state, dict):
                        loaded_state = state
        except Exception:
            loaded_state = {}

        if not loaded_offsets:
            self._prime_offsets_for_run(run_text)

        if not apply_state:
            return

        running = bool(status.get("running"))
        done = int(status.get("done") or 0)
        failed = int(status.get("failed") or 0)
        warnings = int(status.get("warnings") or 0)
        reason = str(status.get("reason") or "").strip()
        metrics_mtime = self._metrics_mtime(run_text)

        self._push_state = {
            "running": bool(loaded_state.get("running", running)),
            "run_dir": str(loaded_state.get("run_dir") or run_text),
            "done": int(loaded_state.get("done") or done),
            "failed": int(loaded_state.get("failed") or failed),
            "warnings": int(loaded_state.get("warnings") or warnings),
            "reason": str(loaded_state.get("reason") or reason),
            "last_metrics_mtime": float(loaded_state.get("last_metrics_mtime") or metrics_mtime),
            "stalled_notified": bool(loaded_state.get("stalled_notified", False)),
        }
        self._push_initialized = True

    def _save_cursor_for_run(self, run_dir: str) -> None:
        run_text = str(run_dir or "").strip()
        if not run_text:
            return
        base = Path(run_text)
        offsets: dict[str, int] = {}
        for name in ("metrics.jsonl", "cycle_summary.log"):
            key = str(base / name)
            offsets[name] = max(0, int(self._file_offsets.get(key, 0)))

        payload = {
            "version": 1,
            "instance": self.instance_name,
            "run_dir": run_text,
            "updated_unix": time.time(),
            "offsets": offsets,
            "state": dict(self._push_state),
        }
        cursor_path = self._cursor_path(run_text)
        tmp_path = cursor_path.with_name(cursor_path.name + ".tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                encoding="utf-8",
                errors="replace",
            )
            tmp_path.replace(cursor_path)
        except Exception:
            try:
                if tmp_path.exists():
                    tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _prime_offsets_for_run(self, run_dir: str) -> None:
        text = str(run_dir or "").strip()
        if not text:
            return
        base = Path(text)
        for name in ("metrics.jsonl", "cycle_summary.log"):
            path = base / name
            key = str(path)
            try:
                self._file_offsets[key] = max(0, int(path.stat().st_size))
            except Exception:
                self._file_offsets[key] = 0

    def _read_new_lines(self, path: Path, *, max_lines: int, max_bytes: int) -> list[str]:
        key = str(path)
        try:
            size = int(path.stat().st_size)
        except Exception:
            return []

        offset = int(self._file_offsets.get(key, 0))
        if offset < 0 or offset > size:
            offset = 0

        if size - offset > max_bytes:
            offset = max(0, size - max_bytes)

        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(offset)
                chunk = handle.read()
                self._file_offsets[key] = int(handle.tell())
        except Exception:
            return []

        if not chunk:
            return []
        lines = [line.rstrip("\n") for line in chunk.splitlines() if line.strip()]
        if len(lines) > max_lines:
            lines = lines[-max_lines:]
        return lines

    def _format_metric_event(self, payload: dict[str, Any]) -> str:
        fields: list[str] = []
        ts = str(payload.get("ts") or "").strip()
        event_type = str(payload.get("event") or payload.get("type") or "").strip()
        task_id = str(payload.get("task_id") or "").strip()
        reason = str(payload.get("reason") or payload.get("message") or "").strip()
        rc = payload.get("rc")
        level = str(payload.get("level") or "").strip()

        if ts:
            fields.append(ts)
        if event_type:
            fields.append(event_type)
        if level:
            fields.append(f"level={level}")
        if task_id:
            fields.append(f"task={task_id}")
        if reason:
            fields.append(f"reason={reason}")
        if isinstance(rc, int):
            fields.append(f"rc={rc}")
        return " ".join(fields).strip()

    def _collect_metric_push_messages(self, run_dir: str) -> list[str]:
        if not run_dir:
            return []
        metrics_path = Path(run_dir) / "metrics.jsonl"
        lines = self._read_new_lines(metrics_path, max_lines=120, max_bytes=180_000)
        if not lines:
            return []

        quota_message = ""
        error_message = ""
        # New event accumulators (last-one-wins per category)
        failover_payload: dict[str, Any] | None = None
        goals_refresh_payload: dict[str, Any] | None = None
        project_complete_payload: dict[str, Any] | None = None
        escalation_payload: dict[str, Any] | None = None
        phantom_payload: dict[str, Any] | None = None
        persistent_skip_payload: dict[str, Any] | None = None
        pm_garbage_payload: dict[str, Any] | None = None
        goals_updated_payload: dict[str, Any] | None = None

        for raw in lines:
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            event_type = str(payload.get("event") or payload.get("type") or "").strip().lower()
            reason = str(payload.get("reason") or payload.get("message") or "").strip().lower()
            level = str(payload.get("level") or "").strip().lower()
            merged = f"{event_type} {reason}"
            formatted = self._format_metric_event(payload) or line

            if self._notify_enabled("quota") and "quota" in merged:
                quota_message = formatted

            if self._notify_enabled("error"):
                rc = payload.get("rc")
                is_error = ("error" in event_type) or ("exception" in event_type)
                if level == "error":
                    is_error = True
                if isinstance(rc, int) and rc != 0:
                    is_error = True
                if is_error:
                    error_message = formatted

            # --- New event detection (exact match) ---
            if self._notify_enabled("backend_failover") and event_type == "backend_failover":
                failover_payload = payload
            if self._notify_enabled("goals_refresh") and event_type == "goals_refresh_ok":
                goals_refresh_payload = payload
            if self._notify_enabled("project_complete") and event_type == "project_complete":
                project_complete_payload = payload
            if self._notify_enabled("escalation") and event_type == "escalate_attempt":
                escalation_payload = payload
            if self._notify_enabled("phantom") and event_type == "phantom_completion_detected":
                phantom_payload = payload
            if self._notify_enabled("persistent_skip") and event_type == "task_persistent_skip":
                persistent_skip_payload = payload
            if self._notify_enabled("pm_garbage") and event_type == "pm_garbage_detected":
                kind = str(payload.get("kind") or "").strip().lower()
                if kind != "quota":
                    pm_garbage_payload = payload
            if self._notify_enabled("goals_updated") and event_type == "goals_updated":
                goals_updated_payload = payload

        out: list[str] = []
        if quota_message:
            out.append(f"{_EMOJI['quota']} \ucffc\ud0c0 \uacbd\uace0\n  {quota_message}")   # ⚠️ 쿼타 경고
        if error_message:
            out.append(f"{_EMOJI['error']} \uc5d0\ub7ec \ubc1c\uc0dd\n  {error_message}")     # 🚨 에러 발생

        # --- New event messages ---
        if failover_payload is not None:
            fb = str(failover_payload.get("from_backend") or "?")
            tb = str(failover_payload.get("to_backend") or "?")
            fr = str(failover_payload.get("reason") or "")
            out.append(
                f"{_EMOJI['backend_failover']} \ubc31\uc5d4\ub4dc \uc804\ud658\n"   # 🔀 백엔드 전환
                f"  {fb} \u2192 {tb} ({fr})"                                          #   codex → claudecode (reason)
            )
        if project_complete_payload is not None:
            goals = project_complete_payload.get("goals") or {}
            p0 = goals.get("p0") or ""
            out.append(
                f"{_EMOJI['project_complete']} \ud504\ub85c\uc81d\ud2b8 \uc644\ub8cc!\n"  # 🏆 프로젝트 완료!
                f"  P0: {p0}"
            )
        if goals_refresh_payload is not None:
            p0 = goals_refresh_payload.get("p0") or ""
            p1 = goals_refresh_payload.get("p1") or ""
            rn = goals_refresh_payload.get("refresh_n") or ""
            out.append(
                f"{_EMOJI['goals_refresh']} Goals \uac31\uc2e0\n"                   # 🌟 Goals 갱신
                f"  P0: {p0}, P1: {p1} (#{rn})"
            )
        if escalation_payload is not None:
            tid = str(escalation_payload.get("task_id") or "?")
            att = str(escalation_payload.get("attempt") or "?")
            out.append(
                f"{_EMOJI['escalation']} Dev \uc5d0\uc2a4\uceec\ub808\uc774\uc158\n"  # ⬆️ Dev 에스컬레이션
                f"  \ud0dc\uc2a4\ud06c: {tid} | \uc2dc\ub3c4: {att}"                   #   태스크: T03 | 시도: 2
            )
        if phantom_payload is not None:
            tid = str(phantom_payload.get("task_id") or "?")
            out.append(
                f"{_EMOJI['phantom']} \ud32c\ud140 \uc644\ub8cc \uac10\uc9c0\n"  # 👻 팬텀 완료 감지
                f"  \ud0dc\uc2a4\ud06c: {tid}"                                     #   태스크: T02
            )
        if persistent_skip_payload is not None:
            tid = str(persistent_skip_payload.get("task_id") or "?")
            consec = str(persistent_skip_payload.get("consecutive_failures") or "?")
            out.append(
                f"{_EMOJI['persistent_skip']} \ud0dc\uc2a4\ud06c \uc601\uad6c \uac74\ub108\ub700\n"  # ⏩ 태스크 영구 건너뜀
                f"  \ud0dc\uc2a4\ud06c: {tid} | \uc5f0\uc18d \uc2e4\ud328: {consec}"                   #   태스크: T04 | 연속 실패: 3
            )
        if pm_garbage_payload is not None:
            kind = str(pm_garbage_payload.get("kind") or "?")
            out.append(
                f"{_EMOJI['pm_garbage']} PM \uac00\ube44\uc9c0 \uac10\uc9c0\n"  # 🗑️ PM 가비지 감지
                f"  \uc720\ud615: {kind}"                                          #   유형: repetitive
            )
        if goals_updated_payload is not None:
            cnt = str(goals_updated_payload.get("checked_count") or "0")
            out.append(
                f"{_EMOJI['goals_updated']} Goals \uccb4\ud06c \uc5c5\ub370\uc774\ud2b8\n"  # 📈 Goals 체크 업데이트
                f"  +{cnt}\uac74 \uccb4\ud06c\ub428"                                          #   +3건 체크됨
            )

        return out

    def _collect_push_messages(self) -> list[str]:
        status = self.controller.status()
        running = bool(status.get("running"))
        run_dir = str(status.get("run_dir") or "").strip()
        done = int(status.get("done") or 0)
        failed = int(status.get("failed") or 0)
        warnings = int(status.get("warnings") or 0)
        reason = str(status.get("reason") or "").strip()
        runner_mode = str(status.get("runner_mode") or "thread").strip() or "thread"

        messages: list[str] = []
        with self._push_lock:
            prev = dict(self._push_state)
            if not self._push_initialized:
                if run_dir:
                    self._load_cursor_for_run(run_dir, status=status, apply_state=True)
                    prev = dict(self._push_state)
                if self._push_initialized:
                    if run_dir:
                        self._save_cursor_for_run(run_dir)
                    return []

                metrics_mtime = self._metrics_mtime(run_dir)
                self._push_state = {
                    "running": running,
                    "run_dir": run_dir,
                    "done": done,
                    "failed": failed,
                    "warnings": warnings,
                    "reason": reason,
                    "last_metrics_mtime": metrics_mtime,
                    "stalled_notified": False,
                }
                self._push_initialized = True
                if run_dir:
                    self._prime_offsets_for_run(run_dir)
                    self._save_cursor_for_run(run_dir)
                return []

            prev_running = bool(prev.get("running", False))
            prev_run_dir = str(prev.get("run_dir") or "").strip()
            prev_done = int(prev.get("done") or 0)
            prev_failed = int(prev.get("failed") or 0)
            run_changed = bool(run_dir and run_dir != prev_run_dir)
            prev_metrics_mtime = float(prev.get("last_metrics_mtime") or 0.0)
            stalled_notified = bool(prev.get("stalled_notified", False))
            metrics_mtime = self._metrics_mtime(run_dir)

            if run_changed:
                self._load_cursor_for_run(run_dir, status=status, apply_state=False)
                metrics_mtime = self._metrics_mtime(run_dir)
                stalled_notified = False

            if self._notify_enabled("run_start") and running and (not prev_running or run_changed):
                messages.append(
                    f"{_EMOJI['run_start']} \ub7ec\ub108 \uc2dc\uc791\ub428\n"   # 🟢 러너 시작됨
                    f"  \uc2e4\ud589 ID: {self._short_run_id(run_dir)} | "        # 실행 ID
                    f"\ubaa8\ub4dc: {runner_mode}\n"                                # 모드
                    f"  {self._active_goal_summary_line()}"
                )

            if self._notify_enabled("run_stop") and (not running) and prev_running:
                final_reason = reason or str(prev.get("reason") or "").strip()
                stop_run_id = self._short_run_id(prev_run_dir or run_dir)
                messages.append(
                    f"{_EMOJI['run_stop']} \ub7ec\ub108 \uc911\uc9c0\ub428\n"          # 🔴 러너 중지됨
                    f"  \uc2e4\ud589 ID: {stop_run_id}\n"                                # 실행 ID
                    f"  \uc774\uc720: {_fmt_reason_kr(final_reason)} | "                  # 이유
                    f"{_fmt_progress(done, failed, warnings)}\n"                           # ✅ 완료 ...
                    f"  {self._active_goal_summary_line()}"
                )

            if self._notify_enabled("task_done") and done > prev_done:
                messages.append(
                    f"{_EMOJI['task_done']} \ud0dc\uc2a4\ud06c \uc644\ub8cc (+{done - prev_done})\n"  # ✅ 태스크 완료 (+N)
                    f"  \uc9c4\ud589: {_fmt_progress(done, failed, warnings)}"                          # 진행: ...
                )

            if self._notify_enabled("task_failed") and failed > prev_failed:
                messages.append(
                    f"{_EMOJI['task_failed']} \ud0dc\uc2a4\ud06c \uc2e4\ud328 (+{failed - prev_failed})\n"  # ❌ 태스크 실패 (+N)
                    f"  \ucd1d \uc2e4\ud328: {failed} | \uc774\uc720: {_fmt_reason_kr(reason)}"              # 총 실패: N | 이유: ...
                )

            if run_dir and self.send_cycle_summary:
                cycle_lines = self._read_new_lines(
                    Path(run_dir) / "cycle_summary.log",
                    max_lines=4,
                    max_bytes=20_000,
                )
                if cycle_lines:
                    for line in cycle_lines[-2:]:
                        messages.append(f"{_EMOJI['cycle']} \uc0ac\uc774\ud074: {line}")  # 🔄 사이클: {line}
                    if len(cycle_lines) > 2:
                        messages.append(f"{_EMOJI['cycle']} ... (\uc678 {len(cycle_lines) - 2}\uac74)")  # 🔄 ... (외 N건)

            messages.extend(self._collect_metric_push_messages(run_dir))

            now_ts = time.time()
            if metrics_mtime > prev_metrics_mtime:
                stalled_notified = False
            if running and self._notify_enabled("stalled"):
                if metrics_mtime > 0:
                    idle_seconds = max(0, int(now_ts - metrics_mtime))
                    if idle_seconds >= int(self.stalled_seconds) and not stalled_notified:
                        messages.append(
                            f"{_EMOJI['stalled']} \uc751\ub2f5 \uc5c6\uc74c (\uba48\ucda4 \uac10\uc9c0)\n"  # 💤 응답 없음 (멈춤 감지)
                            f"  \uc720\ud734: {_fmt_uptime(idle_seconds)} | "                                  # 유휴: ...
                            f"\uc784\uacc4\uac12: {_fmt_uptime(int(self.stalled_seconds))}"                    # 임계값: ...
                        )
                        stalled_notified = True
            if not running:
                stalled_notified = False

            self._push_state = {
                "running": running,
                "run_dir": run_dir,
                "done": done,
                "failed": failed,
                "warnings": warnings,
                "reason": reason,
                "last_metrics_mtime": metrics_mtime,
                "stalled_notified": stalled_notified,
            }
            if run_dir:
                self._save_cursor_for_run(run_dir)

        return messages

    async def _push_tick(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.allowed_chat_ids:
            return
        if not (self.notify_events or self.send_cycle_summary):
            return

        try:
            messages = self._collect_push_messages()
        except Exception as ex:
            print(f"[TELEGRAM][WARN] Push poll failed: {ex}")
            return
        if not messages:
            return

        limited = messages[:6]
        extra = len(messages) - len(limited)
        lines = [f"{_EMOJI['info']} {self.instance_name} \uc54c\ub9bc"]  # 📊 {instance} 알림
        lines.extend(limited)
        if extra > 0:
            lines.append(f"... (\uc678 {extra}\uac74)")  # ... (외 N건)
        payload = _safe_text("\n\n".join(lines))

        chat_ids = sorted(self.allowed_chat_ids)
        for chat_id in chat_ids:
            try:
                await context.bot.send_message(chat_id=chat_id, text=payload)
            except Exception as ex:
                print(f"[TELEGRAM][WARN] Push send failed chat_id={chat_id}: {ex}")

    def _parse_run_start_overrides(self, raw_args: list[str]) -> tuple[dict[str, Any], list[str]]:
        if not raw_args:
            return {}, []
        # Respect shell-like quoting to keep behavior consistent with shell mode.
        text = " ".join(raw_args)
        try:
            tokens = shlex.split(text)
        except Exception:
            tokens = raw_args
        parsed = _parse_kv_tokens(tokens, DEFAULTS)
        overrides = {k: v for k, v in parsed.items() if k in _RUN_START_ALLOWED_KEYS}
        ignored = sorted(set(parsed.keys()) - set(overrides.keys()))
        return overrides, ignored

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        authorized = self._is_allowed(chat_id) if chat_id else False
        lines = [
            f"{_EMOJI['info']} AgentCLI \ud154\ub808\uadf8\ub7a8 \uc81c\uc5b4",  # 📊 AgentCLI 텔레그램 제어
            f"  \uc778\uc2a4\ud134\uc2a4: {self.instance_name}",      # 인스턴스
            f"  \ud1a0\ud070 \uc9c0\ubb38: {self.token_fingerprint}",  # 토큰 지문
            f"  \uc800\uc7a5\uc18c: {self.repo}",                      # 저장소
            f"  \uc778\uc99d: {'\uc644\ub8cc' if authorized else '\ubbf8\uc778\uc99d'}",  # 인증: 완료/미인증
            f"  \uc5f0\uacb0\ub41c \ucc44\ud305: {len(self.allowed_chat_ids)}\uac1c",    # 연결된 채팅: N개
            "\uba85\ub839\uc5b4: /whoami /pair /status /goal /detail /experience /errors /events /grep /run_start /run_stop /runs /tail /notify",  # 명령어
        ]
        lines.append("PR queue: /prs /pr <id>")
        if not authorized:
            if self.pairing_code:
                lines.append("\ud398\uc5b4\ub9c1 \ud544\uc694: /pair <\ucf54\ub4dc>")  # 페어링 필요
            else:
                lines.append("\ud398\uc5b4\ub9c1 \ube44\ud65c\uc131. telegram.pairing_code \ub610\ub294 allowed_chat_ids\ub97c \uc124\uc815\ud558\uc138\uc694.")  # 페어링 비활성...
        await self._reply(update, "\n".join(lines))


    def _append_pr_queue_summary_lines(self, lines: list[str], *, limit: int) -> None:
        pr_queue = build_telegram_pr_queue_summary(self.repo, limit=limit)
        queue_items = pr_queue.get("items") if isinstance(pr_queue.get("items"), list) else []
        if queue_items:
            lines.append(
                "PR queue: "
                f"{int(pr_queue.get('total') or 0)} queued | "
                f"{int(pr_queue.get('needs_validation') or 0)} need validation | "
                f"{int(pr_queue.get('needs_approval') or 0)} need approval."
            )
            for item in queue_items:
                if not isinstance(item, dict):
                    continue
                evidence = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
                evidence_text = ", ".join(str(entry) for entry in evidence[:3] if str(entry).strip()) or "packet-only"
                task_ids = item.get("task_ids")
                task_text = ",".join(str(task_id) for task_id in task_ids[:2]) if isinstance(task_ids, list) and task_ids else "-"
                packet_id = _sanitize_summary_text(item.get("id"), limit=48) or "-"
                branch = _sanitize_summary_text(item.get("branch"), limit=32) or "-"
                validation_status = _humanize_summary_label(item.get("validation_status"))
                merge_status = _humanize_summary_label(item.get("merge_label") or item.get("merge_status"))
                lines.append(
                    f"- {packet_id} | validation={validation_status} | merge={merge_status} | branch={branch} | task={task_text} | evidence={evidence_text}"
                )
        else:
            lines.append("PR queue: no queued PRs need validation or approval.")

    def _build_pr_queue_summary_text(self) -> str:
        lines = [f"{_EMOJI['info']} {self.instance_name} PR queue"]
        self._append_pr_queue_summary_lines(lines, limit=6)
        return "\n".join(lines)

    def _build_pr_queue_detail_text(self, packet_id: str) -> str:
        packet_id_text = str(packet_id or "").strip()
        if not packet_id_text:
            return "Usage: /pr <id>"
        try:
            detail = build_telegram_pr_queue_detail(self.repo, packet_id_text)
        except FileNotFoundError:
            return f"PR packet not found: {packet_id_text}"

        lines = [f"{_EMOJI['detail']} {self.instance_name} PR {detail.get('id') or packet_id_text}"]
        lines.append(f"status: {_humanize_summary_label(detail.get('status'))}")
        lines.append(f"validation: {_humanize_summary_label(detail.get('validation_status'))}")
        lines.append(f"merge: {_humanize_summary_label(detail.get('merge_label') or detail.get('merge_status'))}")
        approval_status = str(detail.get("approval_status") or "").strip()
        if approval_status:
            lines.append(f"approval: {_humanize_summary_label(approval_status)}")
        lines.append(f"branch: {_sanitize_summary_text(detail.get('branch'), limit=48) or '-'}")
        lines.append(f"tasks: {', '.join(detail.get('task_ids') or []) or '-'}")
        lines.append(f"run_id: {detail.get('run_id') or '-'}")
        lines.append(f"updated: {detail.get('updated_at') or '-'}")

        validation_reason = str(detail.get("validation_reason") or "").strip()
        validation_detail = str(detail.get("validation_detail") or "").strip()
        if validation_reason:
            lines.append(f"validation reason: {validation_reason}")
        if validation_detail and validation_detail != validation_reason:
            lines.append(f"validation detail: {validation_detail}")

        qa_notes = detail.get("qa_notes") if isinstance(detail.get("qa_notes"), list) else []
        if qa_notes:
            lines.append(f"qa: {' | '.join(str(note) for note in qa_notes if str(note).strip())}")

        artifacts = detail.get("validation_artifacts") if isinstance(detail.get("validation_artifacts"), list) else []
        if artifacts:
            lines.append(f"artifacts: {', '.join(str(artifact) for artifact in artifacts if str(artifact).strip())}")

        evidence = detail.get("evidence_refs") if isinstance(detail.get("evidence_refs"), list) else []
        if evidence:
            lines.append(f"evidence: {', '.join(str(ref) for ref in evidence if str(ref).strip())}")

        return "\n".join(lines)

    def _build_experience_summary_text(self) -> str:
        status = self.controller.status()
        run_dir_text = str(status.get("run_dir") or "").strip()
        run_dir = Path(run_dir_text) if run_dir_text else None
        blockers = _load_telegram_experience_blockers(self.repo, run_dir=run_dir, limit=3)

        lines = [f"{_EMOJI['info']} {self.instance_name} experience summary"]
        blocker_items = blockers.get("items") if isinstance(blockers.get("items"), list) else []
        if blocker_items:
            lines.append(f"Blockers ({len(blocker_items)}/{int(blockers.get('total') or 0)}):")
            for item in blocker_items:
                if not isinstance(item, dict):
                    continue
                evidence = item.get("evidence_refs") if isinstance(item.get("evidence_refs"), list) else []
                evidence_text = ", ".join(str(entry) for entry in evidence[:3] if str(entry).strip()) or "summary-only"
                kind_text = _humanize_summary_label(item.get("kind"))
                lines.append(f"- [{kind_text}] {item.get('text') or '[redacted]'} | evidence={evidence_text}")
        else:
            lines.append("Blockers: none from the latest experience summary.")

        self._append_pr_queue_summary_lines(lines, limit=4)
        return "\n".join(lines)


    async def cmd_notify(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        events = sorted(self.notify_events)
        available_inactive = sorted(_NOTIFY_EVENT_ALLOWED - self.notify_events)
        lines = [
            f"{_EMOJI['info']} {self.instance_name} \uc54c\ub9bc \uc124\uc815",    # 📊 {instance} 알림 설정
            f"  \ud65c\uc131 \uc774\ubca4\ud2b8: {events if events else '(\uc5c6\uc74c)'}",  # 활성 이벤트
            f"  \uc0ac\uc6a9 \uac00\ub2a5: {available_inactive if available_inactive else '(\uc5c6\uc74c)'}",  # 사용 가능: [...]
            f"  \uc0ac\uc774\ud074 \uc694\uc57d \uc804\uc1a1: {'\uc608' if self.send_cycle_summary else '\uc544\ub2c8\uc624'}",  # 사이클 요약 전송: 예/아니오
            f"  \ud3f4\ub9c1 \uac04\uaca9: {self.notify_poll_interval_seconds}\ucd08",  # 폴링 간격: N초
            f"  \uba48\ucda4 \uac10\uc9c0: {self.stalled_seconds}\ucd08",               # 멈춤 감지: N초
            f"  \uc5f0\uacb0\ub41c \ucc44\ud305: {len(self.allowed_chat_ids)}\uac1c",   # 연결된 채팅: N개
        ]
        await self._reply(update, "\n".join(lines))

    async def cmd_detail(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        lines = self.tail_lines_default
        if context.args:
            raw = str(context.args[0]).strip()
            if raw.isdigit():
                lines = max(10, min(400, int(raw)))
        detail = self._build_detail_text(lines=lines)
        await self._reply(update, detail)


    async def cmd_experience(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._reply(update, self._build_experience_summary_text())

    async def cmd_prs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        await self._reply(update, self._build_pr_queue_summary_text())

    async def cmd_pr(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        packet_id = str(context.args[0]).strip() if context.args else ""
        await self._reply(update, self._build_pr_queue_detail_text(packet_id))


    async def cmd_errors(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        lines = self.tail_lines_default
        if context.args:
            raw = str(context.args[0]).strip()
            if raw.isdigit():
                lines = max(1, min(400, int(raw)))
        try:
            body = self.controller.filter_metrics(errors_only=True, limit=lines)
        except Exception as ex:
            await self._reply(update, f"\uc5d0\ub7ec \uc870\ud68c \uc2e4\ud328: {ex}")
            return
        payload = body.strip() or "(\ube44\uc5b4 \uc788\uc74c)"  # (비어 있음)
        await self._reply(update, f"{_EMOJI['error']} \uc5d0\ub7ec (\ucd5c\uadfc {lines}\uac74)\n{payload}")  # 🚨 에러 (최근 N건)

    async def cmd_events(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        args = context.args or []
        if not args:
            await self._reply(update, "\uc0ac\uc6a9\ubc95: /events <\uc774\ubca4\ud2b8\uba85> [\uc904\uc218]")
            return
        event_name = str(args[0] or "").strip().lower()
        if not event_name:
            await self._reply(update, "\uc0ac\uc6a9\ubc95: /events <\uc774\ubca4\ud2b8\uba85> [\uc904\uc218]")
            return
        lines = self.tail_lines_default
        if len(args) > 1:
            raw = str(args[1]).strip()
            if raw.isdigit():
                lines = max(1, min(400, int(raw)))
        try:
            body = self.controller.filter_metrics(event_type=event_name, limit=lines)
        except Exception as ex:
            await self._reply(update, f"\uc774\ubca4\ud2b8 \uc870\ud68c \uc2e4\ud328: {ex}")
            return
        payload = body.strip() or "(\ube44\uc5b4 \uc788\uc74c)"
        await self._reply(update, f"{_EMOJI['info']} \uc774\ubca4\ud2b8: {event_name} (\ucd5c\uadfc {lines}\uac74)\n{payload}")  # 📊 이벤트: name (최근 N건)

    async def cmd_grep(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        args = context.args or []
        if not args:
            await self._reply(update, "\uc0ac\uc6a9\ubc95: /grep <\ud328\ud134> [\ud30c\uc77c] [\uc904\uc218]")
            return
        pattern = str(args[0] or "").strip()
        if not pattern:
            await self._reply(update, "\uc0ac\uc6a9\ubc95: /grep <\ud328\ud134> [\ud30c\uc77c] [\uc904\uc218]")
            return

        file_name = "metrics.jsonl"
        lines = self.tail_lines_default
        if len(args) > 1:
            second = str(args[1] or "").strip()
            if second.isdigit():
                lines = max(1, min(400, int(second)))
            elif second:
                file_name = second
        if len(args) > 2:
            third = str(args[2] or "").strip()
            if third.isdigit():
                lines = max(1, min(400, int(third)))

        try:
            body = self.controller.grep(pattern=pattern, name=file_name, lines=lines, ignore_case=True)
        except Exception as ex:
            await self._reply(update, f"\uac80\uc0c9 \uc2e4\ud328: {ex}")
            return
        payload = body.strip() or "(\ube44\uc5b4 \uc788\uc74c)"
        await self._reply(update, f"\uac80\uc0c9: {file_name} /{pattern}/ (\ucd5c\uadfc {lines}\uac74)\n{payload}")  # 검색: file /pat/ (최근 N건)

    async def cmd_whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        allowed = self._is_allowed(chat_id) if chat_id else False
        text = f"chat_id: {chat_id}" if chat_id else "chat_id: (\uc54c \uc218 \uc5c6\uc74c)"  # (알 수 없음)
        lines = [
            text,
            f"authorized: {'yes' if allowed else 'no'}",
            "protected commands: /status /detail /experience /prs /pr <id>",
        ]
        if not allowed:
            if self.pairing_code:
                lines.append("next: /pair <code>")
            else:
                lines.append("next: ask the operator to allow this chat_id.")
        await self._reply(update, "\n".join(lines))

    async def cmd_pair(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        if not chat_id:
            await self._reply(update, "\ud398\uc5b4\ub9c1 \uc2e4\ud328: chat_id\ub97c \ud655\uc778\ud560 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.")  # 페어링 실패: chat_id를 확인할 수 없습니다.
            return

        if self._is_allowed(chat_id):
            await self._reply(update, "\uc774\ubbf8 \ud398\uc5b4\ub9c1\ub418\uc5b4 \uc788\uc2b5\ub2c8\ub2e4.")  # 이미 페어링되어 있습니다.
            return

        if not self.pairing_code:
            await self._reply(update, "\ud398\uc5b4\ub9c1 \ube44\ud65c\uc131: telegram.pairing_code\uac00 \uc124\uc815\ub418\uc9c0 \uc54a\uc558\uc2b5\ub2c8\ub2e4.")  # 페어링 비활성: ...
            return

        provided = " ".join(context.args or []).strip()
        if not provided:
            await self._reply(update, "\uc0ac\uc6a9\ubc95: /pair <\ucf54\ub4dc>")  # 사용법: /pair <코드>
            return

        if provided != self.pairing_code:
            await self._reply(update, "\ud398\uc5b4\ub9c1 \uc2e4\ud328: \uc798\ubabb\ub41c \ucf54\ub4dc\uc785\ub2c8\ub2e4.")  # 페어링 실패: 잘못된 코드입니다.
            return

        try:
            self._persist_allowlist(chat_id)
        except Exception as ex:
            await self._reply(update, f"\ud398\uc5b4\ub9c1 \uc2e4\ud328: {ex}")  # 페어링 실패: {ex}
            return

        await self._reply(update, f"\ud398\uc5b4\ub9c1 \uc131\uacf5. \ud5c8\uc6a9\ub41c chat_id={chat_id}")  # 페어링 성공. 허용된 chat_id=...

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        status = self.controller.status()
        await self._reply(update, self._format_status(status))

    async def cmd_goal(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        raw_args = [str(item or "") for item in (context.args or [])]
        action = (raw_args[0].strip().lower() if raw_args else "status") or "status"
        rest = raw_args[1:] if raw_args else []
        try:
            if action in {"status", "show"}:
                await self._reply(update, self._format_active_goal_status())
                return
            opts, positional = self._parse_goal_options(rest)
            if action == "create":
                objective = " ".join(positional).strip()
                if not objective:
                    await self._reply(update, "Usage: /goal create <objective> [--mode strict|adaptive|exploratory]")
                    return
                status = create_active_goal(
                    self.repo,
                    objective,
                    mode=str(opts.get("mode") or "adaptive"),
                    token_budget=int(opts.get("token_budget") or 0),
                    time_budget_seconds=int(opts.get("time_budget") or 0),
                    cycle_budget=int(opts.get("cycle_budget") or 0),
                    source={"kind": "operator", "surface": "telegram"},
                    replace=bool(opts.get("replace")),
                    expected_etag=str(opts.get("etag") or ""),
                )
                await self._reply(update, f"Active goal created: {status['goal'].get('id')}\n{self._format_active_goal_status()}")
                return
            if action == "update":
                update_kwargs: dict[str, Any] = {"expected_etag": str(opts.get("etag") or "")}
                objective = " ".join(positional).strip()
                if objective:
                    update_kwargs["objective"] = objective
                if opts.get("mode"):
                    update_kwargs["mode"] = str(opts.get("mode") or "")
                if opts.get("token_budget") is not None:
                    update_kwargs["token_budget"] = int(opts.get("token_budget") or 0)
                if opts.get("time_budget") is not None:
                    update_kwargs["time_budget_seconds"] = int(opts.get("time_budget") or 0)
                if opts.get("cycle_budget") is not None:
                    update_kwargs["cycle_budget"] = int(opts.get("cycle_budget") or 0)
                if len(update_kwargs) <= 1:
                    await self._reply(update, "Usage: /goal update [objective] [--mode ...] [--token-budget N] [--time-budget N] [--cycle-budget N]")
                    return
                status = update_active_goal(self.repo, **update_kwargs)
                await self._reply(update, f"Active goal updated: revision={status.get('revision')}\n{self._format_active_goal_status()}")
                return
            if action == "complete":
                evidence = " ".join(positional).strip()
                if not evidence:
                    await self._reply(update, "Usage: /goal complete <evidence>")
                    return
                status = complete_active_goal(self.repo, evidence=evidence, expected_etag=str(opts.get("etag") or ""))
                await self._reply(update, f"Active goal completed: {status['goal'].get('id')}")
                return
            if action == "cancel":
                reason = " ".join(positional).strip()
                status = cancel_active_goal(self.repo, reason=reason, expected_etag=str(opts.get("etag") or ""))
                await self._reply(update, f"Active goal canceled: {status['goal'].get('id')}")
                return
            if action == "clear":
                clear_active_goal(self.repo, expected_etag=str(opts.get("etag") or ""))
                await self._reply(update, "Active goal cleared.")
                return
        except (ActiveGoalError, ValueError) as ex:
            await self._reply(update, f"Active goal command failed: {ex}")
            return
        await self._reply(update, "Usage: /goal [status] | create | update | complete | cancel | clear")

    async def cmd_run_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        overrides, ignored = self._parse_run_start_overrides(context.args or [])
        try:
            result = self.controller.start(overrides=overrides)
        except Exception as ex:
            await self._reply(update, f"\ub7ec\ub108 \uc2dc\uc791 \uc2e4\ud328: {ex}")  # 러너 시작 실패
            return
        lines = [result.get("message") or "\ub7ec\ub108 \uc2dc\uc791 \uc2e4\ud328."]  # 러너 시작 실패.
        if result.get("run_dir"):
            lines.append(f"\uc2e4\ud589 \uacbd\ub85c: {result['run_dir']}")  # 실행 경로
        if result.get("runner_mode"):
            lines.append(f"\ubaa8\ub4dc: {result['runner_mode']}")  # 모드
        if ignored:
            lines.append(f"\ubb34\uc2dc\ub41c \uc635\uc158: {', '.join(ignored)}")  # 무시된 옵션
        await self._reply(update, "\n".join(lines))

    async def cmd_run_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        if not update.message:
            return
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("\uc911\uc9c0 \ud655\uc778", callback_data="confirm_run_stop")]]  # 중지 확인
        )
        await update.message.reply_text("\ub7ec\ub108\ub97c \uc9c0\uae08 \uc911\uc9c0\ud558\uc2dc\uaca0\uc2b5\ub2c8\uae4c?", reply_markup=keyboard)  # 러너를 지금 중지하시겠습니까?

    async def cmd_runs(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        count = 10
        if context.args:
            try:
                count = max(1, min(30, int(context.args[0])))
            except Exception:
                count = 10
        runs = self.controller.list_runs(n=count)
        if not runs:
            await self._reply(update, "\uc2e4\ud589 \uc774\ub825\uc774 \uc5c6\uc2b5\ub2c8\ub2e4.")  # 실행 이력이 없습니다.
            return
        lines = [f"{_EMOJI['info']} \ucd5c\uadfc \uc2e4\ud589 \uc774\ub825"]  # 📊 최근 실행 이력
        for item in runs:
            r_done = int(item.get("done") or 0)
            r_failed = int(item.get("failed") or 0)
            r_warn = int(item.get("warnings") or 0)
            r_reason = str(item.get("reason") or "").strip()
            lines.append(
                f"  {item.get('run_id')}: {_fmt_progress(r_done, r_failed, r_warn)}"
                f" | \uc0ac\uc720: {_fmt_reason_kr(r_reason)}"  # 사유
            )
        await self._reply(update, "\n".join(lines))

    async def cmd_tail(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        file_name = "cycle_summary.log"
        lines = self.tail_lines_default
        args = context.args or []
        if args:
            first = args[0].strip()
            if first.isdigit():
                lines = max(1, min(400, int(first)))
            else:
                file_name = first
        if len(args) > 1:
            second = args[1].strip()
            if second.isdigit():
                lines = max(1, min(400, int(second)))
        try:
            tail_text = self.controller.tail(name=file_name, lines=lines)
        except Exception as ex:
            await self._reply(update, f"\ud14c\uc77c \uc2e4\ud328: {ex}")
            return
        payload = tail_text.strip() or "(\ube44\uc5b4 \uc788\uc74c)"
        response = f"{_EMOJI['log']} {file_name} (\ucd5c\uadfc {lines}\uc904)\n{payload}"  # 📝 file (최근 N줄)
        await self._reply(update, response)

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle PTB errors gracefully instead of dumping full tracebacks."""
        err = context.error
        if err is None:
            return
        # Network errors during polling are transient — log one line, PTB retries automatically.
        err_name = type(err).__name__
        cause = type(err.__cause__).__name__ if err.__cause__ else ""
        label = f"{err_name}({cause})" if cause else err_name
        print(f"[TELEGRAM][WARN] {label}: {err}")

    async def on_stop_confirm(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if query is None:
            return
        chat = query.message.chat if query.message else None
        chat_id = int(chat.id) if chat else 0
        if not chat_id or not self._is_allowed(chat_id):
            await query.answer(self._auth_failed_message(), show_alert=True)
            return

        result = self.controller.stop(wait=False)
        await query.answer("\uc911\uc9c0 \uc694\uccad\ub428.")  # 중지 요청됨.
        await query.edit_message_text(_safe_text(result.get("message") or "\uc911\uc9c0 \uc694\uccad\ub428."))

    def run(self) -> int:
        ok, detail = self.acquire_token_lock()
        if not ok:
            print(
                "[TELEGRAM][ERR] Another Telegram control-plane appears to be using the same bot token. "
                "Use a different bot token per instance."
            )
            print(f"[TELEGRAM][ERR] {detail}")
            return 2

        created_loop: asyncio.AbstractEventLoop | None = None
        try:
            # PTB run_polling expects a current event loop on the running thread.
            # In hybrid mode this service runs in a background thread.
            try:
                asyncio.get_event_loop()
            except RuntimeError:
                created_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(created_loop)

            app = ApplicationBuilder().token(self.bot_token).build()
            app.add_handler(CommandHandler("start", self.cmd_start))
            app.add_handler(CommandHandler("whoami", self.cmd_whoami))
            app.add_handler(CommandHandler("pair", self.cmd_pair))
            app.add_handler(CommandHandler("status", self.cmd_status))
            app.add_handler(CommandHandler("goal", self.cmd_goal))
            app.add_handler(CommandHandler("detail", self.cmd_detail))
            app.add_handler(CommandHandler("experience", self.cmd_experience))
            app.add_handler(CommandHandler("prs", self.cmd_prs))
            app.add_handler(CommandHandler("pr", self.cmd_pr))
            app.add_handler(CommandHandler("errors", self.cmd_errors))
            app.add_handler(CommandHandler("events", self.cmd_events))
            app.add_handler(CommandHandler("grep", self.cmd_grep))
            app.add_handler(CommandHandler("run_start", self.cmd_run_start))
            app.add_handler(CommandHandler("run_stop", self.cmd_run_stop))
            app.add_handler(CommandHandler("runs", self.cmd_runs))
            app.add_handler(CommandHandler("tail", self.cmd_tail))
            app.add_handler(CommandHandler("notify", self.cmd_notify))
            app.add_handler(CallbackQueryHandler(self.on_stop_confirm, pattern=r"^confirm_run_stop$"))
            app.add_error_handler(self._error_handler)

            if self.notify_events or self.send_cycle_summary:
                if app.job_queue is None:
                    print("[TELEGRAM][WARN] Push notifications disabled: job_queue is unavailable.")
                else:
                    app.job_queue.run_repeating(
                        self._push_tick,
                        interval=float(self.notify_poll_interval_seconds),
                        first=2.0,
                        name="agentcli-push",
                    )
                    print(
                        "[TELEGRAM] Push notifications enabled. "
                        f"instance={self.instance_name} token_fp={self.token_fingerprint} "
                        f"interval={self.notify_poll_interval_seconds}s "
                        f"events={sorted(self.notify_events)} cycle_summary={self.send_cycle_summary} "
                        f"stalled_after={self.stalled_seconds}s"
                    )

            try:
                atexit.register(self._atexit_shutdown)
            except Exception:
                pass

            print(
                f"[TELEGRAM] Control plane started. "
                f"instance={self.instance_name} token_fp={self.token_fingerprint} "
                f"repo={self.repo} mode={self.controller.runner_mode}"
            )
            app.run_polling(
                poll_interval=1.0,
                timeout=max(1, int(self.poll_timeout_seconds)),
                allowed_updates=Update.ALL_TYPES,
                # Always ignore backlog updates on startup to prevent replay bursts.
                drop_pending_updates=True,
            )
            return 0
        finally:
            if created_loop is not None:
                try:
                    if not created_loop.is_closed():
                        created_loop.close()
                except Exception:
                    pass
            self.release_token_lock()


def telegram_hybrid_main(argv: list[str] | None = None) -> int:
    """Run hybrid mode: local shell + Telegram control plane (shared controller)."""
    if _TELEGRAM_IMPORT_ERROR is not None:
        print("[ERR] Telegram mode requires python-telegram-bot.")
        print(f"[ERR] import error: {_TELEGRAM_IMPORT_ERROR}")
        return 2

    args = parse_args(argv)
    telegram_cfg = getattr(args, "telegram", {})
    if not isinstance(telegram_cfg, dict):
        telegram_cfg = {}

    if bool(getattr(args, "telegram_service", False)):
        telegram_cfg["enabled"] = True

    token_env = (os.getenv("AGENTCLI_TELEGRAM_BOT_TOKEN") or "").strip()
    token_cfg = str(telegram_cfg.get("bot_token") or "").strip()
    bot_token = token_env or token_cfg
    if not bot_token:
        print("[ERR] Telegram bot token is missing.")
        print("[ERR] Set AGENTCLI_TELEGRAM_BOT_TOKEN or telegram.bot_token in config.")
        return 2

    telegram_cfg["bot_token"] = bot_token
    setattr(args, "telegram", telegram_cfg)

    runner_mode = str(telegram_cfg.get("runner_mode") or "thread").strip().lower()
    controller = RunnerController(
        repo=Path(str(args.repo)).expanduser().resolve(),
        base_args=args,
        runner_mode=runner_mode,
    )
    service = TelegramControlService(args, controller=controller)

    ok, detail = service.acquire_token_lock()
    if not ok:
        print(
            "[ERR] Telegram control plane lock conflict. "
            "Use a different bot token per instance."
        )
        print(f"[ERR] {detail}")
        return 2

    t = threading.Thread(target=service.run, name="agentcli-telegram", daemon=True)
    try:
        t.start()
    except Exception:
        service.release_token_lock()
        raise
    print("[HYBRID] Telegram control plane started in background.")
    print("[HYBRID] Local shell is available. Use /help for commands.")

    from ..shell import RunnerShell, shell_main

    shell = RunnerShell(initial_argv=argv or [], controller=controller)
    return shell_main(argv, shell_instance=shell)
