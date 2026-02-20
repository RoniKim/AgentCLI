from __future__ import annotations

import argparse
import os
import re
import shlex
import threading
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


class TelegramControlService:
    def __init__(self, args: argparse.Namespace) -> None:
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

        self.allowed_chat_ids: set[int] = set()
        for value in telegram_cfg.get("allowed_chat_ids") or []:
            try:
                self.allowed_chat_ids.add(int(str(value).strip()))
            except Exception:
                continue

        runner_mode = str(telegram_cfg.get("runner_mode") or "thread").strip().lower()
        self.controller = RunnerController(repo=self.repo, base_args=args, runner_mode=runner_mode)
        self._config_lock = threading.Lock()

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
            f"- repo: {self.repo}",
            f"- authorized: {str(authorized).lower()}",
            f"- paired_chat_count: {len(self.allowed_chat_ids)}",
            "Commands: /whoami /pair /status /run_start /run_stop /runs /tail",
        ]
        if not authorized:
            if self.pairing_code:
                lines.append("Pairing required: /pair <code>")
            else:
                lines.append("Pairing is disabled. Set telegram.pairing_code or allowed_chat_ids in config.")
        if update.message:
            await update.message.reply_text(_safe_text("\n".join(lines)))

    async def cmd_whoami(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        text = f"chat_id: {chat_id}" if chat_id else "chat_id: (unknown)"
        if update.message:
            await update.message.reply_text(text)

    async def cmd_pair(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        chat = update.effective_chat
        chat_id = int(chat.id) if chat else 0
        if not chat_id:
            if update.message:
                await update.message.reply_text("Pairing failed: chat_id is unavailable.")
            return

        if self._is_allowed(chat_id):
            if update.message:
                await update.message.reply_text("Already paired.")
            return

        if not self.pairing_code:
            if update.message:
                await update.message.reply_text("Pairing is disabled: telegram.pairing_code is empty.")
            return

        provided = " ".join(context.args or []).strip()
        if not provided:
            if update.message:
                await update.message.reply_text("Usage: /pair <code>")
            return

        if provided != self.pairing_code:
            if update.message:
                await update.message.reply_text("Pairing failed: invalid code.")
            return

        try:
            self._persist_allowlist(chat_id)
        except Exception as ex:
            if update.message:
                await update.message.reply_text(f"Pairing failed: {ex}")
            return

        if update.message:
            await update.message.reply_text(f"Pairing successful. allowlisted chat_id={chat_id}")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        status = self.controller.status()
        if update.message:
            await update.message.reply_text(_safe_text(self._format_status(status)))

    async def cmd_run_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._require_auth(update):
            return
        overrides, ignored = self._parse_run_start_overrides(context.args or [])
        try:
            result = self.controller.start(overrides=overrides)
        except Exception as ex:
            if update.message:
                await update.message.reply_text(_safe_text(f"Failed to start runner: {ex}"))
            return
        lines = [result.get("message") or "Failed to start runner."]
        if result.get("run_dir"):
            lines.append(f"run_dir: {result['run_dir']}")
        if result.get("runner_mode"):
            lines.append(f"mode: {result['runner_mode']}")
        if ignored:
            lines.append(f"ignored options: {', '.join(ignored)}")
        if update.message:
            await update.message.reply_text(_safe_text("\n".join(lines)))

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
            if update.message:
                await update.message.reply_text("No run history found.")
            return
        lines = ["[Recent Runs]"]
        for item in runs:
            lines.append(
                f"- {item.get('run_id')}: done={item.get('done', 0)} failed={item.get('failed', 0)} "
                f"warn={item.get('warnings', 0)} reason={item.get('reason') or '-'}"
            )
        if update.message:
            await update.message.reply_text(_safe_text("\n".join(lines)))

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
            if update.message:
                await update.message.reply_text(f"tail failed: {ex}")
            return
        payload = tail_text.strip() or "(empty)"
        response = f"[tail] {file_name} (last {lines})\n{payload}"
        if update.message:
            await update.message.reply_text(_safe_text(response))

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
        app.add_handler(CommandHandler("run_start", self.cmd_run_start))
        app.add_handler(CommandHandler("run_stop", self.cmd_run_stop))
        app.add_handler(CommandHandler("runs", self.cmd_runs))
        app.add_handler(CommandHandler("tail", self.cmd_tail))
        app.add_handler(CallbackQueryHandler(self.on_stop_confirm, pattern=r"^confirm_run_stop$"))

        print(f"[TELEGRAM] Control plane started. repo={self.repo} mode={self.controller.runner_mode}")
        app.run_polling(
            poll_interval=1.0,
            timeout=max(1, int(self.poll_timeout_seconds)),
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=False,
        )
        return 0


def telegram_main(argv: list[str] | None = None) -> int:
    if _TELEGRAM_IMPORT_ERROR is not None:
        print("[ERR] Telegram mode requires python-telegram-bot.")
        print(f"[ERR] import error: {_TELEGRAM_IMPORT_ERROR}")
        return 2

    args = parse_args(argv)
    telegram_cfg = getattr(args, "telegram", {})
    if not isinstance(telegram_cfg, dict):
        telegram_cfg = {}

    # --telegram flag should force service mode on even if config says disabled.
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

    service = TelegramControlService(args)
    return service.run()
