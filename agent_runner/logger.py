"""Enhanced structured logging system for AgentCLI with error context tracking."""

from __future__ import annotations

import atexit
import json
import logging
import sys
import threading
import traceback
import weakref
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import now_iso


_ACTIVE_LOGGERS: "weakref.WeakSet[StructuredLogger]" = weakref.WeakSet()
_ACTIVE_LOGGERS_LOCK = threading.RLock()
_STRUCTURED_LOGGER_CLEANUP_REGISTERED = False


class _ProcessGuardFilter(logging.Filter):
    """Filter out noisy DEBUG-level messages from process_guard."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "agent_runner.process_guard" and record.levelno <= logging.DEBUG:
            return False
        return True


class StructuredLogger:
    """
    Enhanced logger that writes structured logs to multiple destinations:
    - Console (stderr) for real-time monitoring
    - debug.log for all debug information
    - error.log for errors with full context
    - events.jsonl for structured event data
    """

    def __init__(self, run_dir: Path, debug: bool = False):
        self.run_dir = run_dir
        self.debug_enabled = debug
        self._closed = False

        # Create log directory
        self.log_dir = run_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Log files
        self.debug_log = self.log_dir / "debug.log"
        self.error_log = self.log_dir / "error.log"
        self.events_log = self.log_dir / "events.jsonl"

        # Python logger setup
        self.logger = logging.getLogger("agent_runner")
        self.logger.setLevel(logging.DEBUG)
        for _h in self.logger.handlers[:]:
            try:
                _h.close()
            except Exception:
                pass
        self.logger.handlers.clear()

        # Console handler (stderr)
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # Debug file handler
        if debug:
            debug_handler = logging.FileHandler(self.debug_log, encoding="utf-8")
            debug_handler.setLevel(logging.DEBUG)
            debug_handler.addFilter(_ProcessGuardFilter())
            debug_formatter = logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S"
            )
            debug_handler.setFormatter(debug_formatter)
            self.logger.addHandler(debug_handler)

        # Error file handler
        error_handler = logging.FileHandler(self.error_log, encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        error_handler.setFormatter(error_formatter)
        self.logger.addHandler(error_handler)

        # Run log handler (always created, INFO+)
        self.run_log = self.log_dir / "run.log"
        run_handler = logging.FileHandler(self.run_log, encoding="utf-8")
        run_handler.setLevel(logging.INFO)
        run_handler.addFilter(_ProcessGuardFilter())
        run_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        run_handler.setFormatter(run_formatter)
        self.logger.addHandler(run_handler)

        # Current context (for error tracing)
        self.context: Dict[str, Any] = {}

        # Cached file handle for events.jsonl
        self._events_fh: Optional[Any] = None
        with _ACTIVE_LOGGERS_LOCK:
            _ACTIVE_LOGGERS.add(self)

    def set_context(self, **kwargs: Any) -> None:
        """Set context information for error tracking."""
        self.context.update(kwargs)

    def clear_context(self, *keys: str) -> None:
        """Clear specific context keys."""
        for key in keys:
            self.context.pop(key, None)

    def debug(self, msg: str, **extra: Any) -> None:
        """Log debug message."""
        if self.debug_enabled:
            self.logger.debug(msg)
            self._write_event("debug", msg=msg, **extra)

    def info(self, msg: str, **extra: Any) -> None:
        """Log info message."""
        self.logger.info(msg)
        self._write_event("info", msg=msg, **extra)

    def warning(self, msg: str, **extra: Any) -> None:
        """Log warning message."""
        self.logger.warning(msg)
        self._write_event("warning", msg=msg, **extra)

    def error(
        self,
        msg: str,
        exc: Optional[Exception] = None,
        include_traceback: bool = True,
        **extra: Any
    ) -> None:
        """
        Log error with full context and optional traceback.

        Args:
            msg: Error message
            exc: Exception object (optional)
            include_traceback: Whether to include full traceback
            **extra: Additional context fields
        """
        # Build error context
        error_context = {
            "msg": msg,
            "timestamp": now_iso(),
            "context": dict(self.context),
            **extra
        }

        if exc:
            error_context["exception"] = {
                "type": type(exc).__name__,
                "message": str(exc),
                "repr": repr(exc)
            }

            if include_traceback:
                error_context["traceback"] = traceback.format_exc()

        # Log to console and error.log
        error_msg = msg
        if exc:
            error_msg += f" | {type(exc).__name__}: {str(exc)}"
        self.logger.error(error_msg)

        # Write detailed error to error.log
        self._write_error_detail(error_context)

        # Write structured event
        self._write_event("error", **error_context)

    def task_start(self, task_id: str, task_title: str, attempt: int = 0, **extra: Any) -> None:
        """Log task start with context."""
        self.set_context(task_id=task_id, task_title=task_title, attempt=attempt)
        self.info(f"Task started: {task_id} - {task_title} (attempt {attempt})")
        self._write_event(
            "task_start",
            task_id=task_id,
            task_title=task_title,
            attempt=attempt,
            **extra
        )

    def task_end(self, task_id: str, success: bool, reason: str = "", **extra: Any) -> None:
        """Log task completion."""
        level = "info" if success else "error"
        msg = f"Task {'completed' if success else 'failed'}: {task_id}"
        if reason:
            msg += f" ({reason})"

        getattr(self, level)(msg)
        self._write_event(
            "task_end",
            task_id=task_id,
            success=success,
            reason=reason,
            **extra
        )
        self.clear_context("task_id", "task_title", "attempt")

    def timing(self, operation: str, duration_sec: float, **extra: Any) -> None:
        """Log timing information."""
        self.debug(f"Timing: {operation} took {duration_sec:.2f}s")
        self._write_event(
            "timing",
            operation=operation,
            duration_sec=duration_sec,
            **extra
        )

    # ------------------------------------------------------------------
    # Domain-specific structured logging methods
    # ------------------------------------------------------------------

    def cycle_start(self, cycle_idx: int, **extra: Any) -> None:
        """Log cycle start."""
        self.logger.info(f"Cycle {cycle_idx} started")
        self._write_event("cycle_start", cycle=cycle_idx, **extra)

    def cycle_end(
        self, cycle_idx: int, rc: int, reason: str,
        done: int = 0, total: int = 0, duration_sec: float = 0.0, **extra: Any,
    ) -> None:
        """Log cycle end with summary."""
        self.logger.info(
            f"Cycle {cycle_idx} ended: rc={rc} reason={reason} "
            f"done={done}/{total} duration={duration_sec:.1f}s"
        )
        self._write_event(
            "cycle_end", cycle=cycle_idx, rc=rc, reason=reason,
            done=done, total=total, duration_sec=duration_sec, **extra,
        )

    def stage_event(self, stage: str, event: str, cycle: int = 0, **extra: Any) -> None:
        """Log PM/QA/Security stage events."""
        msg = f"[{stage.upper()}] {event}"
        if extra:
            detail_parts = [f"{k}={v}" for k, v in extra.items()]
            msg += " " + " ".join(detail_parts)
        level = logging.WARNING if event in ("error", "quota_detected", "quota_exhausted") else logging.INFO
        self.logger.log(level, msg)
        self._write_event("stage_event", stage=stage, event=event, cycle=cycle, **extra)

    def quota_event(
        self, action: str, five_hour: Any = None, seven_day: Any = None,
        resets_at: Any = None, wait_seconds: Any = None, **extra: Any,
    ) -> None:
        """Log quota check results."""
        parts = [f"action={action}"]
        if five_hour is not None:
            parts.append(f"5h={five_hour}%")
        if seven_day is not None:
            parts.append(f"7d={seven_day}%")
        if wait_seconds is not None:
            parts.append(f"wait={wait_seconds}s")
        msg = "[QUOTA] " + " ".join(parts)
        level = logging.WARNING if action in ("wait", "stop", "imminent") else logging.INFO
        self.logger.log(level, msg)
        self._write_event(
            "quota_event", action=action, five_hour=five_hour,
            seven_day=seven_day, resets_at=resets_at,
            wait_seconds=wait_seconds, **extra,
        )

    def budget_event(self, event: str, **extra: Any) -> None:
        """Log budget reset/exceeded events."""
        parts = [f"event={event}"]
        for k, v in extra.items():
            parts.append(f"{k}={v}")
        msg = "[BUDGET] " + " ".join(parts)
        self.logger.info(msg)
        self._write_event("budget_event", event=event, **extra)

    def skip_event(self, task_id: str, reason: str, **extra: Any) -> None:
        """Log task skip with reason."""
        msg = f"[SKIP] {task_id}: {reason}"
        self.logger.warning(msg)
        self._write_event("skip_event", task_id=task_id, reason=reason, **extra)

    def stop_event(self, reason: str, **extra: Any) -> None:
        """Log run stop with reason."""
        msg = f"[STOP] {reason}"
        self.logger.warning(msg)
        self._write_event("stop_event", reason=reason, **extra)

    def retry_event(self, stage: str, task_id: str, attempt: int = 0, reason: str = "", **extra: Any) -> None:
        """Log retry/escalation events."""
        msg = f"[RETRY] {stage} {task_id} attempt={attempt} reason={reason}"
        self.logger.info(msg)
        self._write_event("retry_event", stage=stage, task_id=task_id, attempt=attempt, reason=reason, **extra)

    def gate_event(self, gate: str, task_id: str, passed: bool, **extra: Any) -> None:
        """Log build/test gate results."""
        status = "passed" if passed else "FAILED"
        msg = f"[{gate.upper()}] {task_id}: {status}"
        level = logging.INFO if passed else logging.WARNING
        self.logger.log(level, msg)
        self._write_event("gate_event", gate=gate, task_id=task_id, passed=passed, **extra)

    def _write_event(self, event_type: str, **fields: Any) -> None:
        """Write structured event to events.jsonl."""
        if self._closed:
            return
        event = {
            "ts": now_iso(),
            "type": event_type,
            **fields
        }
        try:
            if self._events_fh is None or self._events_fh.closed:
                self._events_fh = self.events_log.open("a", encoding="utf-8", errors="replace")
            self._events_fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            self._events_fh.flush()
        except Exception:
            # Don't break on logging failures
            pass

    def close(self) -> None:
        """Close cached file handles and detach logging handlers."""
        self._closed = True
        if self._events_fh is not None and not self._events_fh.closed:
            try:
                self._events_fh.close()
            except Exception:
                pass
            self._events_fh = None
        for handler in self.logger.handlers[:]:
            try:
                handler.flush()
            except Exception:
                pass
            try:
                handler.close()
            except Exception:
                pass
            try:
                self.logger.removeHandler(handler)
            except Exception:
                pass
        with _ACTIVE_LOGGERS_LOCK:
            try:
                _ACTIVE_LOGGERS.discard(self)
            except Exception:
                pass

    def _write_error_detail(self, error_context: Dict[str, Any]) -> None:
        """Write detailed error information to error.log."""
        try:
            with self.error_log.open("a", encoding="utf-8", errors="replace") as f:
                f.write("\n" + "=" * 80 + "\n")
                f.write(f"ERROR at {error_context['timestamp']}\n")
                f.write("=" * 80 + "\n")
                f.write(f"Message: {error_context['msg']}\n\n")

                if "exception" in error_context:
                    exc = error_context["exception"]
                    f.write(f"Exception: {exc['type']}: {exc['message']}\n\n")

                if error_context.get("context"):
                    f.write("Context:\n")
                    for key, value in error_context["context"].items():
                        f.write(f"  {key}: {value}\n")
                    f.write("\n")

                if "traceback" in error_context:
                    f.write("Traceback:\n")
                    f.write(error_context["traceback"])
                    f.write("\n")

                f.write("=" * 80 + "\n\n")
        except Exception:
            # Don't break on logging failures
            pass


def create_logger(run_dir: Path, debug: bool = False) -> StructuredLogger:
    """Factory function to create a structured logger."""
    close_all_loggers()
    return StructuredLogger(run_dir, debug)


def register_structured_logger_cleanup() -> bool:
    """Register structured logger cleanup with ``atexit`` once per process."""
    global _STRUCTURED_LOGGER_CLEANUP_REGISTERED
    with _ACTIVE_LOGGERS_LOCK:
        if _STRUCTURED_LOGGER_CLEANUP_REGISTERED:
            return False
        try:
            atexit.register(close_all_loggers)
        except Exception:
            return False
        _STRUCTURED_LOGGER_CLEANUP_REGISTERED = True
        return True


def close_all_loggers() -> None:
    """Close every active AgentCLI structured logger in this process."""
    with _ACTIVE_LOGGERS_LOCK:
        loggers = list(_ACTIVE_LOGGERS)
    for logger in loggers:
        try:
            logger.close()
        except Exception:
            pass
