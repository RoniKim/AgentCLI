from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import threading
import time
from pathlib import Path
from typing import Any

from ..cli import DEFAULTS, parse_args
from ..config import load_config, save_config
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

    async def _reply(self, update: Update, text: str) -> None:
        if not update.message:
            return
        for chunk in _chunk_text(text, limit=3500):
            await update.message.reply_text(chunk or " ")

    def _is_allowed(self, chat_id: int) -> bool:
        return chat_id in self.allowed_chat_ids

    def _auth_failed_message(self) -> str:
        if not self.allowed_chat_ids:
            if self.pairing_code:
                return "Access denied: no paired chat. Use /pair <code> first."
            return "Access denied: allowlist empty. Configure telegram.pairing_code or allowed_chat_ids."
        return "Access denied: this chat_id is not allowlisted."

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

    def _format_status(self, data: dict[str, Any]) -> str:
        lines = [
            "[AgentCLI Status]",
            f"- instance: {self.instance_name}",
            f"- running: {str(bool(data.get('running'))).lower()}",
            f"- mode: {data.get('runner_mode') or 'thread'}",
            f"- repo: {data.get('repo') or self.repo}",
            f"- run_dir: {data.get('run_dir') or '(none)'}",
            f"- uptime: {int(data.get('uptime_seconds') or 0)}s",
            f"- exit_code: {data.get('exit_code') if data.get('exit_code') is not None else '(running/unknown)'}",
            f"- progress: done={int(data.get('done') or 0)} failed={int(data.get('failed') or 0)} warnings={int(data.get('warnings') or 0)}",
            f"- reason: {data.get('reason') or '(none)'}",
            f"- stop_file: {'present' if data.get('stop_file_exists') else 'absent'} ({data.get('stop_file') or 'STOP'})",
        ]
        last_event = str(data.get("last_event") or "").strip()
        if last_event:
            lines.append(f"- last_event: {last_event}")
        return "\n".join(lines)

    def _build_detail_text(self, *, lines: int = 80) -> str:
        n = max(10, min(400, int(lines)))
        status = self.controller.status()
        run_dir = str(status.get("run_dir") or "").strip()

        out: list[str] = [
            "[AgentCLI Detail]",
            f"- instance: {self.instance_name}",
            f"- running: {str(bool(status.get('running'))).lower()}",
            f"- mode: {status.get('runner_mode') or 'thread'}",
            f"- repo: {status.get('repo') or self.repo}",
            f"- run_dir: {run_dir or '(none)'}",
            f"- progress: done={int(status.get('done') or 0)} failed={int(status.get('failed') or 0)} warnings={int(status.get('warnings') or 0)}",
            f"- reason: {status.get('reason') or '(none)'}",
            "",
        ]

        if not run_dir:
            out.append("No run_dir found. Run /run_start first.")
            return "\n".join(out)

        def _append_section(name: str, content: str) -> None:
            out.append(f"[{name}] last {n}")
            out.append(content.strip() or "(empty)")
            out.append("")

        try:
            _append_section("cycle_summary.log", self.controller.tail(name="cycle_summary.log", lines=n))
        except Exception as ex:
            _append_section("cycle_summary.log", f"(error) {ex}")

        try:
            _append_section("metrics.jsonl", self.controller.tail(name="metrics.jsonl", lines=n))
        except Exception as ex:
            _append_section("metrics.jsonl", f"(error) {ex}")

        try:
            _append_section("run_summary.json", self.controller.tail(name="run_summary.json", lines=max(20, n // 2)))
        except Exception as ex:
            _append_section("run_summary.json", f"(error) {ex}")

        if str(status.get("runner_mode") or "").strip().lower() == "subprocess":
            try:
                _append_section(
                    "telegram_runner_subprocess.log",
                    self.controller.tail(name="telegram_runner_subprocess.log", lines=n),
                )
            except Exception as ex:
                _append_section("telegram_runner_subprocess.log", f"(error) {ex}")

        out.append("Tip: use /tail <file> <lines> for focused view.")
        return "\n".join(out).strip()

    def _notify_enabled(self, event: str) -> bool:
        return event in self.notify_events

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

        out: list[str] = []
        if quota_message:
            out.append(f"[quota] {quota_message}")
        if error_message:
            out.append(f"[error] {error_message}")
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
                    f"[run_start] instance={self.instance_name} run_id={self._short_run_id(run_dir)} mode={runner_mode}"
                )

            if self._notify_enabled("run_stop") and (not running) and prev_running:
                final_reason = reason or str(prev.get("reason") or "").strip() or "(none)"
                stop_run_id = self._short_run_id(prev_run_dir or run_dir)
                messages.append(
                    f"[run_stop] instance={self.instance_name} run_id={stop_run_id} "
                    f"reason={final_reason} done={done} failed={failed} warnings={warnings}"
                )

            if self._notify_enabled("task_done") and done > prev_done:
                messages.append(
                    f"[task_done] instance={self.instance_name} +{done - prev_done} "
                    f"total={done} failed={failed} warnings={warnings}"
                )

            if self._notify_enabled("task_failed") and failed > prev_failed:
                messages.append(
                    f"[task_failed] instance={self.instance_name} +{failed - prev_failed} "
                    f"total={failed} reason={reason or '(none)'}"
                )

            if run_dir and self.send_cycle_summary:
                cycle_lines = self._read_new_lines(
                    Path(run_dir) / "cycle_summary.log",
                    max_lines=4,
                    max_bytes=20_000,
                )
                if cycle_lines:
                    for line in cycle_lines[-2:]:
                        messages.append(f"[cycle] {line}")
                    if len(cycle_lines) > 2:
                        messages.append(f"[cycle] ... ({len(cycle_lines) - 2} more lines)")

            messages.extend(self._collect_metric_push_messages(run_dir))

            now_ts = time.time()
            if metrics_mtime > prev_metrics_mtime:
                stalled_notified = False
            if running and self._notify_enabled("stalled"):
                if metrics_mtime > 0:
                    idle_seconds = max(0, int(now_ts - metrics_mtime))
                    if idle_seconds >= int(self.stalled_seconds) and not stalled_notified:
                        messages.append(
                            f"[stalled] instance={self.instance_name} run_id={self._short_run_id(run_dir)} "
                            f"idle={idle_seconds}s threshold={int(self.stalled_seconds)}s"
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
        lines = [f"[AgentCLI Notify] instance={self.instance_name}"]
        lines.extend([f"- {line}" for line in limited])
        if extra > 0:
            lines.append(f"- ... ({extra} more events)")
        payload = _safe_text("\n".join(lines))

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
            "AgentCLI Telegram control plane",
            f"- instance: {self.instance_name}",
            f"- repo: {self.repo}",
            f"- authorized: {str(authorized).lower()}",
            f"- paired_chat_count: {len(self.allowed_chat_ids)}",
            "Commands: /whoami /pair /status /detail /errors /events /grep /run_start /run_stop /runs /tail /notify",
        ]
        if not authorized:
            if self.pairing_code:
                lines.append("Pairing required: /pair <code>")
            else:
                lines.append("Pairing is disabled. Set telegram.pairing_code or allowed_chat_ids in config.")
        await self._reply(update, "\n".join(lines))

    async def cmd_notify(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        events = sorted(self.notify_events)
        lines = [
            "[Notify Settings]",
            f"- instance: {self.instance_name}",
            f"- enabled_events: {events if events else '(none)'}",
            f"- send_cycle_summary: {str(self.send_cycle_summary).lower()}",
            f"- poll_interval_seconds: {self.notify_poll_interval_seconds}",
            f"- stalled_seconds: {self.stalled_seconds}",
            f"- paired_chat_count: {len(self.allowed_chat_ids)}",
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
            await self._reply(update, f"errors failed: {ex}")
            return
        payload = body.strip() or "(empty)"
        await self._reply(update, f"[errors] last {lines}\n{payload}")

    async def cmd_events(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        args = context.args or []
        if not args:
            await self._reply(update, "Usage: /events <event_name> [lines]")
            return
        event_name = str(args[0] or "").strip().lower()
        if not event_name:
            await self._reply(update, "Usage: /events <event_name> [lines]")
            return
        lines = self.tail_lines_default
        if len(args) > 1:
            raw = str(args[1]).strip()
            if raw.isdigit():
                lines = max(1, min(400, int(raw)))
        try:
            body = self.controller.filter_metrics(event_type=event_name, limit=lines)
        except Exception as ex:
            await self._reply(update, f"events failed: {ex}")
            return
        payload = body.strip() or "(empty)"
        await self._reply(update, f"[events] {event_name} last {lines}\n{payload}")

    async def cmd_grep(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        args = context.args or []
        if not args:
            await self._reply(update, "Usage: /grep <pattern> [file] [lines]")
            return
        pattern = str(args[0] or "").strip()
        if not pattern:
            await self._reply(update, "Usage: /grep <pattern> [file] [lines]")
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
            await self._reply(update, f"grep failed: {ex}")
            return
        payload = body.strip() or "(empty)"
        await self._reply(update, f"[grep] {file_name} /{pattern}/ last {lines}\n{payload}")

    async def cmd_whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        text = f"chat_id: {chat_id}" if chat_id else "chat_id: (unknown)"
        await self._reply(update, text)

    async def cmd_pair(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        if not chat_id:
            await self._reply(update, "Pairing failed: chat_id is unavailable.")
            return

        if self._is_allowed(chat_id):
            await self._reply(update, "Already paired.")
            return

        if not self.pairing_code:
            await self._reply(update, "Pairing is disabled: telegram.pairing_code is empty.")
            return

        provided = " ".join(context.args or []).strip()
        if not provided:
            await self._reply(update, "Usage: /pair <code>")
            return

        if provided != self.pairing_code:
            await self._reply(update, "Pairing failed: invalid code.")
            return

        try:
            self._persist_allowlist(chat_id)
        except Exception as ex:
            await self._reply(update, f"Pairing failed: {ex}")
            return

        await self._reply(update, f"Pairing successful. allowlisted chat_id={chat_id}")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        status = self.controller.status()
        await self._reply(update, self._format_status(status))

    async def cmd_run_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        overrides, ignored = self._parse_run_start_overrides(context.args or [])
        try:
            result = self.controller.start(overrides=overrides)
        except Exception as ex:
            await self._reply(update, f"Failed to start runner: {ex}")
            return
        lines = [result.get("message") or "Failed to start runner."]
        if result.get("run_dir"):
            lines.append(f"run_dir: {result['run_dir']}")
        if result.get("runner_mode"):
            lines.append(f"mode: {result['runner_mode']}")
        if ignored:
            lines.append(f"ignored options: {', '.join(ignored)}")
        await self._reply(update, "\n".join(lines))

    async def cmd_run_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        if not update.message:
            return
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Confirm Stop", callback_data="confirm_run_stop")]]
        )
        await update.message.reply_text("Stop runner now?", reply_markup=keyboard)

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
            await self._reply(update, "No run history found.")
            return
        lines = ["[Recent Runs]"]
        for item in runs:
            lines.append(
                f"- {item.get('run_id')}: done={item.get('done', 0)} failed={item.get('failed', 0)} "
                f"warn={item.get('warnings', 0)} reason={item.get('reason') or '-'}"
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
            await self._reply(update, f"tail failed: {ex}")
            return
        payload = tail_text.strip() or "(empty)"
        response = f"[tail] {file_name} (last {lines})\n{payload}"
        await self._reply(update, response)

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
        await query.answer("Stop requested.")
        await query.edit_message_text(_safe_text(result.get("message") or "Stop requested."))

    def run(self) -> int:
        app = ApplicationBuilder().token(self.bot_token).build()
        app.add_handler(CommandHandler("start", self.cmd_start))
        app.add_handler(CommandHandler("whoami", self.cmd_whoami))
        app.add_handler(CommandHandler("pair", self.cmd_pair))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("detail", self.cmd_detail))
        app.add_handler(CommandHandler("errors", self.cmd_errors))
        app.add_handler(CommandHandler("events", self.cmd_events))
        app.add_handler(CommandHandler("grep", self.cmd_grep))
        app.add_handler(CommandHandler("run_start", self.cmd_run_start))
        app.add_handler(CommandHandler("run_stop", self.cmd_run_stop))
        app.add_handler(CommandHandler("runs", self.cmd_runs))
        app.add_handler(CommandHandler("tail", self.cmd_tail))
        app.add_handler(CommandHandler("notify", self.cmd_notify))
        app.add_handler(CallbackQueryHandler(self.on_stop_confirm, pattern=r"^confirm_run_stop$"))

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
                    f"instance={self.instance_name} interval={self.notify_poll_interval_seconds}s "
                    f"events={sorted(self.notify_events)} cycle_summary={self.send_cycle_summary} "
                    f"stalled_after={self.stalled_seconds}s"
                )

        print(
            f"[TELEGRAM] Control plane started. "
            f"instance={self.instance_name} repo={self.repo} mode={self.controller.runner_mode}"
        )
        app.run_polling(
            poll_interval=1.0,
            timeout=max(1, int(self.poll_timeout_seconds)),
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
        return 0


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

    t = threading.Thread(target=service.run, name="agentcli-telegram", daemon=True)
    t.start()
    print("[HYBRID] Telegram control plane started in background.")
    print("[HYBRID] Local shell is available. Use /help for commands.")

    from ..shell import RunnerShell, shell_main

    shell = RunnerShell(initial_argv=argv or [], controller=controller)
    return shell_main(argv, shell_instance=shell)
