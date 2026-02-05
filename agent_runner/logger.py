"""Enhanced structured logging system for AgentCLI with error context tracking."""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import now_iso


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

        # Create log directory
        self.log_dir = run_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Log files
        self.debug_log = self.log_dir / "debug.log"
        self.error_log = self.log_dir / "error.log"
        self.events_log = self.log_dir / "events.jsonl"

        # Python logger setup
        self.logger = logging.getLogger("agent_runner")
        self.logger.setLevel(logging.DEBUG if debug else logging.INFO)
        self.logger.handlers.clear()

        # Console handler (stderr)
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter(
            "[%(levelname)s] %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        self.logger.addHandler(console_handler)

        # Debug file handler
        if debug:
            debug_handler = logging.FileHandler(self.debug_log, encoding="utf-8")
            debug_handler.setLevel(logging.DEBUG)
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
            "%(asctime)s [%(levelname)s] %(message)s\n%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        error_handler.setFormatter(error_formatter)
        self.logger.addHandler(error_handler)

        # Current context (for error tracing)
        self.context: Dict[str, Any] = {}

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

    def _write_event(self, event_type: str, **fields: Any) -> None:
        """Write structured event to events.jsonl."""
        event = {
            "ts": now_iso(),
            "type": event_type,
            **fields
        }
        try:
            with self.events_log.open("a", encoding="utf-8", errors="replace") as f:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # Don't break on logging failures
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
    return StructuredLogger(run_dir, debug)
