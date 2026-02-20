from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

from .utils import now_iso, rotate_log_file


class MetricsLogger:
    """Append-only JSONL event logger for unattended operations."""

    def __init__(
        self,
        path: Path,
        *,
        instance_name: str = "",
        max_bytes: int = 5_000_000,
        backup_count: int = 5,
        retention_days: int = 14,
    ) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = self.path.parent.name
        self.instance_name = str(instance_name or os.getenv("AGENTCLI_INSTANCE_NAME") or "").strip()
        self.max_bytes = max(1_024, int(max_bytes))
        self.backup_count = max(1, int(backup_count))
        self.retention_days = max(0, int(retention_days))
        self._seq = 0
        self._lock = threading.Lock()

    def _infer_stage(self, event_type: str, fields: Dict[str, Any]) -> str:
        stage = str(fields.get("stage") or "").strip()
        if stage:
            return stage
        ev = str(event_type or "").strip().lower()
        if ev.startswith("pm_"):
            return "pm"
        if ev.startswith("qa_"):
            return "qa"
        if ev.startswith("security_"):
            return "security"
        if ev.startswith("dev_") or ev.startswith("build_") or ev.startswith("test_"):
            return "dev"
        if ev.startswith("task_"):
            return "task"
        return ""

    def _infer_level(self, event_type: str, fields: Dict[str, Any]) -> str:
        explicit = str(fields.get("level") or "").strip().lower()
        if explicit in {"debug", "info", "warning", "error"}:
            return explicit
        rc = fields.get("rc")
        if isinstance(rc, int) and rc != 0:
            return "error"
        ev = str(event_type or "").lower()
        if any(t in ev for t in ("error", "failed", "violation", "exhausted", "exception")):
            return "error"
        if any(t in ev for t in ("warn", "retry", "skip", "stop")):
            return "warning"
        return "info"

    def _build_message(self, event_type: str, fields: Dict[str, Any]) -> str:
        raw_msg = str(fields.get("message") or "").strip()
        if raw_msg:
            return raw_msg
        reason = str(fields.get("reason") or fields.get("error") or "").strip()
        task_id = str(fields.get("task_id") or "").strip()
        if reason and task_id:
            return f"{event_type} task={task_id} reason={reason}"
        if reason:
            return f"{event_type} reason={reason}"
        if task_id:
            return f"{event_type} task={task_id}"
        return str(event_type or "").strip()

    def event(self, event_type: str, **fields: Any) -> None:
        raw_fields: Dict[str, Any] = dict(fields)
        stage = self._infer_stage(event_type, raw_fields)
        task_id = str(raw_fields.get("task_id") or "").strip()
        level = self._infer_level(event_type, raw_fields)
        message = self._build_message(event_type, raw_fields)

        payload: Dict[str, Any] = dict(raw_fields)
        nested_payload = payload.get("payload")
        if isinstance(nested_payload, dict):
            merged_payload = dict(nested_payload)
            payload.pop("payload", None)
            merged_payload.update(payload)
            payload = merged_payload

        rec: Dict[str, Any] = dict(raw_fields)
        rec.update(
            {
                "ts": now_iso(),
                "seq": 0,  # set under lock
                "level": level,
                "event": event_type,
                "type": event_type,  # legacy compatibility
                "run_id": self.run_id,
                "instance": self.instance_name,
                "stage": stage,
                "task_id": task_id,
                "message": message,
                "payload": payload,
            }
        )
        try:
            with self._lock:
                rec["seq"] = int(self._seq)
                self._seq += 1
                rotate_log_file(
                    self.path,
                    max_bytes=self.max_bytes,
                    backup_count=self.backup_count,
                    max_age_days=self.retention_days,
                )
                with self.path.open("a", encoding="utf-8", errors="replace") as f:
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        except Exception:
            # Avoid breaking the runner on observability failures.
            pass
