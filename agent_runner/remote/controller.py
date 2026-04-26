from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional

from ..cli import DEFAULTS
from ..config import AGENT_WORK_DIR, resolve_prompts_dir
from ..process_guard import register_pid, terminate_all_children, terminate_process_tree, unregister_pid_if_exited
from ..run_dir import find_latest_run_dir, make_run_dir
from ..runner_entry import run as run_runner
from ..utils import STOP_REASON_STOP_FILE, detect_stop_reason, rotate_log_file


class RunnerController:
    ALLOWED_TAIL_FILES = frozenset(
        {
            "cycle_summary.log",
            "metrics.jsonl",
            "run_summary.json",
            "last_run_summary.json",
            "STATE.json",
            "BACKLOG.md",
            "telegram_runner_subprocess.log",
        }
    )

    def __init__(self, *, repo: Path, base_args: argparse.Namespace, runner_mode: str = "thread") -> None:
        self.repo = repo.expanduser().resolve()
        self.base_args = base_args
        self.runner_mode = runner_mode if runner_mode in {"thread", "subprocess"} else "thread"

        self._runner_thread: Optional[threading.Thread] = None
        self._runner_process: Optional[subprocess.Popen[bytes]] = None
        self._runner_log_handle: Optional[Any] = None
        self._runner_exit_code: Optional[int] = None
        self._runner_started_at: Optional[float] = None
        self._start_lock = threading.Lock()
        self.run_dir: Optional[Path] = None
        self._on_done_callbacks: list[Callable[[int], None]] = []

        configured_run_dir = str(getattr(base_args, "run_dir", "") or "").strip()
        if configured_run_dir:
            self.run_dir = Path(configured_run_dir).expanduser().resolve()

    def register_on_done(self, callback: Callable[[int], None]) -> None:
        self._on_done_callbacks.append(callback)

    def _fire_on_done(self, rc: int) -> None:
        for cb in self._on_done_callbacks:
            try:
                cb(rc)
            except Exception:
                pass

    def _stop_file_name(self) -> str:
        raw = str(getattr(self.base_args, "stop_file", "STOP") or "STOP").strip()
        return raw or "STOP"

    def _config_path_name(self) -> str:
        raw = str(getattr(self.base_args, "config_path", getattr(self.base_args, "config", "")) or "").strip()
        if not raw:
            return ""
        try:
            return Path(raw).expanduser().as_posix()
        except Exception:
            return raw.replace("\\", "/")

    def _effective_dict(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        eff: dict[str, Any] = {}
        for key, default_value in DEFAULTS.items():
            eff[key] = getattr(self.base_args, key, default_value)
        if overrides:
            eff.update(overrides)
        eff["repo"] = self.repo.as_posix()
        try:
            eff["prompts_dir"] = resolve_prompts_dir(self.repo, str(eff.get("prompts_dir") or "")).as_posix()
        except Exception:
            pass
        return eff

    def _ensure_run_dir(self, eff: dict[str, Any]) -> Path:
        explicit = str(eff.get("run_dir") or "").strip()
        if explicit:
            return Path(explicit).expanduser().resolve()
        prefer_resume = bool(
            eff.get("resume_latest")
            or eff.get("loop")
            or eff.get("continuous")
            or eff.get("autopilot")
        )
        latest = find_latest_run_dir(self.repo) if prefer_resume else None
        return latest if latest is not None else make_run_dir(self.repo)

    def _refresh_process_state(self) -> None:
        if self._runner_process is None:
            return
        rc = self._runner_process.poll()
        if rc is None:
            return
        self._runner_exit_code = int(rc)
        try:
            if self._runner_process.pid:
                terminate_process_tree(int(self._runner_process.pid), include_root=False)
                unregister_pid_if_exited(int(self._runner_process.pid))
        except Exception:
            pass
        self._runner_process = None
        if self._runner_log_handle is not None:
            try:
                self._runner_log_handle.close()
            except Exception:
                pass
            self._runner_log_handle = None

    def _runner_is_alive(self) -> bool:
        if self.runner_mode == "subprocess":
            self._refresh_process_state()
            return bool(self._runner_process and self._runner_process.poll() is None)
        return bool(self._runner_thread and self._runner_thread.is_alive())

    def _start_thread(self, args: argparse.Namespace) -> None:
        def _target() -> None:
            self._runner_exit_code = None
            try:
                rc = run_runner(args)
            except Exception:
                rc = 1
            self._runner_exit_code = int(rc)
            self._fire_on_done(int(rc))

        self._runner_thread = threading.Thread(target=_target, name="agentcli-runner", daemon=True)
        self._runner_thread.start()

    def _start_subprocess(self, args: argparse.Namespace, run_dir: Path) -> None:
        payload_path = run_dir / ".telegram_runner_args.json"
        payload_path.write_text(
            json.dumps(vars(args), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            errors="replace",
        )
        log_path = run_dir / "telegram_runner_subprocess.log"
        try:
            rotate_log_file(log_path, max_bytes=20_000_000, backup_count=5, max_age_days=14)
            self._runner_log_handle = log_path.open("ab")
            cmd = [
                sys.executable,
                "-m",
                "agent_runner.remote.subprocess_runner",
                "--args-json",
                str(payload_path),
            ]
            self._runner_process = subprocess.Popen(
                cmd,
                cwd=str(self.repo),
                stdin=subprocess.DEVNULL,
                stdout=self._runner_log_handle,
                stderr=subprocess.STDOUT,
            )
            if self._runner_process.pid:
                try:
                    register_pid(int(self._runner_process.pid))
                except Exception:
                    pass
        except Exception:
            if self._runner_log_handle is not None:
                try:
                    self._runner_log_handle.close()
                except Exception:
                    pass
            self._runner_log_handle = None
            self._runner_process = None
            raise

    def start(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        context = {"repo": self.repo.as_posix(), "config_path": self._config_path_name()}
        if not self._start_lock.acquire(blocking=False):
            return {"ok": False, "message": "\ub7ec\ub108 \uc2dc\uc791\uc774 \uc774\ubbf8 \uc9c4\ud589 \uc911\uc785\ub2c8\ub2e4.", **context}

        try:
            if self._runner_is_alive():
                return {"ok": False, "message": "\ub7ec\ub108\uac00 \uc774\ubbf8 \uc2e4\ud589 \uc911\uc785\ub2c8\ub2e4.", **context}

            eff = self._effective_dict(overrides)
            run_dir = self._ensure_run_dir(eff)
            run_dir.mkdir(parents=True, exist_ok=True)
            self.run_dir = run_dir
            eff["run_dir"] = run_dir.as_posix()

            stop_paths = {run_dir / self._stop_file_name(), run_dir / "STOP"}
            for stop_path in stop_paths:
                try:
                    if stop_path.exists():
                        stop_path.unlink()
                except Exception:
                    pass

            args = argparse.Namespace(**eff)
            self._runner_exit_code = None
            self._runner_started_at = time.time()

            if self.runner_mode == "subprocess":
                self._start_subprocess(args, run_dir)
            else:
                self._start_thread(args)

            return {
                "ok": True,
                "message": "\ub7ec\ub108\uac00 \uc2dc\uc791\ub418\uc5c8\uc2b5\ub2c8\ub2e4.",
                "runner_mode": self.runner_mode,
                "run_dir": run_dir.as_posix(),
                **context,
            }
        finally:
            self._start_lock.release()

    def stop(self, *, wait: bool = False) -> dict[str, Any]:
        context = {"repo": self.repo.as_posix(), "config_path": self._config_path_name()}
        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return {"ok": False, "message": "\uc2e4\ud589 \ub514\ub809\ud1a0\ub9ac\ub97c \ucc3e\uc744 \uc218 \uc5c6\uc2b5\ub2c8\ub2e4.", **context}
        self.run_dir = run_dir

        stop_path = run_dir / self._stop_file_name()
        try:
            stop_path.write_text(STOP_REASON_STOP_FILE + "\n", encoding="utf-8", errors="replace")
        except Exception as ex:
            return {"ok": False, "message": f"\uc815\uc9c0 \ud30c\uc77c \uc0dd\uc131 \uc2e4\ud328: {ex}", **context}

        if self.runner_mode == "thread":
            try:
                terminate_all_children()
            except Exception:
                pass

            if wait and self._runner_thread and self._runner_thread.is_alive():
                self._runner_thread.join(timeout=60)
        else:
            self._refresh_process_state()
            if wait and self._runner_process and self._runner_process.poll() is None:
                try:
                    self._runner_process.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    try:
                        terminate_process_tree(int(self._runner_process.pid), include_root=True)
                        self._runner_process.wait(timeout=20)
                    except Exception:
                        try:
                            self._runner_process.kill()
                        except Exception:
                            pass
                self._refresh_process_state()

        return {
            "ok": True,
            "message": f"\uc911\uc9c0 \uc694\uccad\ub428: {stop_path.as_posix()}",
            "running": self._runner_is_alive(),
            "run_dir": run_dir.as_posix(),
            **context,
        }

    def _load_json(self, path: Path, fallback: Any) -> Any:
        try:
            if not path.exists():
                return fallback
            raw = path.read_text(encoding="utf-8", errors="replace").strip()
            if not raw:
                return fallback
            return json.loads(raw)
        except Exception:
            return fallback

    def _tail_lines(self, path: Path, lines: int) -> str:
        if not path.exists() or not path.is_file():
            return ""
        max_lines = max(1, int(lines))
        dq: deque[str] = deque(maxlen=max_lines)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    dq.append(line.rstrip("\n"))
        except Exception:
            return ""
        return "\n".join(dq).strip()

    def _latest_event(self, run_dir: Path) -> str:
        metrics_path = run_dir / "metrics.jsonl"
        tail = self._tail_lines(metrics_path, 5)
        if not tail:
            return ""
        events: list[dict[str, Any]] = []
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, dict):
                events.append(payload)
        if not events:
            return ""
        latest = events[-1]
        fields: list[str] = []
        ts = str(latest.get("ts") or "").strip()
        event_type = str(latest.get("type") or "").strip()
        if ts:
            fields.append(ts)
        if event_type:
            fields.append(event_type)
        task_id = str(latest.get("task_id") or "").strip()
        if task_id:
            fields.append(f"task={task_id}")
        reason = str(latest.get("reason") or "").strip()
        if reason:
            fields.append(f"reason={reason}")
        rc = latest.get("rc")
        if isinstance(rc, int):
            fields.append(f"rc={rc}")
        return " ".join(fields).strip()

    def _state_counts(self, run_dir: Path) -> dict[str, int]:
        payload = self._load_json(run_dir / "STATE.json", {})
        if not isinstance(payload, dict):
            payload = {}
        done = payload.get("done") if isinstance(payload.get("done"), list) else []
        failed = payload.get("failed") if isinstance(payload.get("failed"), list) else []
        warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
        return {
            "done": len(done),
            "failed": len(failed),
            "warnings": len(warnings),
        }

    def _stop_reason(self, run_dir: Path) -> str:
        stop_paths = [run_dir / self._stop_file_name(), run_dir / "STOP"]
        detected = detect_stop_reason(stop_paths)
        if detected:
            return detected
        summary_payload = self._load_json(run_dir / "run_summary.json", {})
        if isinstance(summary_payload, dict):
            final = summary_payload.get("final")
            if isinstance(final, dict):
                return str(final.get("reason") or "").strip()
        return ""

    def _runs_root(self) -> Path:
        root = self.repo / AGENT_WORK_DIR / "agent_runs"
        if root.exists():
            return root
        legacy = self.repo / ".doc" / "agent_runs"
        if legacy.exists():
            return legacy
        return root

    def _collect_run_info(self, run_dir: Path) -> dict[str, Any]:
        counts = self._state_counts(run_dir)
        cycle_tail = self._tail_lines(run_dir / "cycle_summary.log", 1)
        return {
            "run_id": run_dir.name,
            "run_dir": run_dir.as_posix(),
            "done": counts["done"],
            "failed": counts["failed"],
            "warnings": counts["warnings"],
            "reason": self._stop_reason(run_dir),
            "last_cycle": cycle_tail,
        }

    def list_runs(self, n: int = 10) -> list[dict[str, Any]]:
        root = self._runs_root()
        if not root.exists() or not root.is_dir():
            return []
        dirs = [p for p in root.iterdir() if p.is_dir()]
        dirs.sort(key=lambda p: p.name, reverse=True)
        out: list[dict[str, Any]] = []
        for run_dir in dirs[: max(1, int(n))]:
            out.append(self._collect_run_info(run_dir))
        return out

    def tail(self, *, name: str = "cycle_summary.log", lines: int = 50) -> str:
        file_name = str(name or "cycle_summary.log").strip()
        if file_name not in self.ALLOWED_TAIL_FILES:
            allowed = ", ".join(sorted(self.ALLOWED_TAIL_FILES))
            raise ValueError(f"\uc9c0\uc6d0\ub418\uc9c0 \uc54a\ub294 \ud30c\uc77c. \ud5c8\uc6a9: {allowed}")

        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return ""
        self.run_dir = run_dir
        return self._tail_lines(run_dir / file_name, max(1, int(lines)))

    def filter_metrics(self, *, event_type: str = "", errors_only: bool = False, limit: int = 50) -> str:
        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return ""
        self.run_dir = run_dir
        metrics_path = run_dir / "metrics.jsonl"
        if not metrics_path.exists() or not metrics_path.is_file():
            return ""

        event_key = str(event_type or "").strip().lower()
        max_lines = max(1, int(limit))
        dq: deque[str] = deque(maxlen=max_lines)

        try:
            with metrics_path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    raw = line.rstrip("\n")
                    if not raw.strip():
                        continue
                    try:
                        payload = json.loads(raw)
                    except Exception:
                        payload = {}

                    if not isinstance(payload, dict):
                        continue

                    ev = str(payload.get("event") or payload.get("type") or "").strip().lower()
                    level = str(payload.get("level") or "").strip().lower()
                    reason = str(payload.get("reason") or payload.get("message") or "").strip().lower()
                    rc = payload.get("rc")

                    if event_key and ev != event_key:
                        continue

                    if errors_only:
                        is_error = False
                        if level == "error":
                            is_error = True
                        if isinstance(rc, int) and rc != 0:
                            is_error = True
                        if any(tok in ev for tok in ("error", "fail", "exception", "violation", "exhausted")):
                            is_error = True
                        if any(tok in reason for tok in ("error", "fail", "exception", "violation", "exhausted")):
                            is_error = True
                        if not is_error:
                            continue

                    dq.append(raw)
        except Exception:
            return ""
        return "\n".join(dq).strip()

    def grep(self, *, pattern: str, name: str = "metrics.jsonl", lines: int = 50, ignore_case: bool = True) -> str:
        file_name = str(name or "metrics.jsonl").strip()
        if file_name not in self.ALLOWED_TAIL_FILES:
            allowed = ", ".join(sorted(self.ALLOWED_TAIL_FILES))
            raise ValueError(f"\uc9c0\uc6d0\ub418\uc9c0 \uc54a\ub294 \ud30c\uc77c. \ud5c8\uc6a9: {allowed}")

        needle = str(pattern or "").strip()
        if not needle:
            raise ValueError("\ud328\ud134\uc774 \ube44\uc5b4 \uc788\uc2b5\ub2c8\ub2e4")

        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        if run_dir is None:
            return ""
        self.run_dir = run_dir
        path = run_dir / file_name
        if not path.exists() or not path.is_file():
            return ""

        flags = re.IGNORECASE if ignore_case else 0
        try:
            rx = re.compile(needle, flags)
        except Exception as ex:
            raise ValueError(f"\uc798\ubabb\ub41c \uc815\uaddc\uc2dd: {ex}") from ex

        max_lines = max(1, int(lines))
        dq: deque[str] = deque(maxlen=max_lines)
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    raw = line.rstrip("\n")
                    if not raw:
                        continue
                    if rx.search(raw):
                        dq.append(raw)
        except Exception:
            return ""
        return "\n".join(dq).strip()

    def status(self) -> dict[str, Any]:
        running = self._runner_is_alive()
        run_dir = self.run_dir or find_latest_run_dir(self.repo)
        config_path = self._config_path_name()
        if run_dir is not None:
            self.run_dir = run_dir

        uptime_seconds = 0
        if self._runner_started_at:
            uptime_seconds = max(0, int(time.time() - self._runner_started_at))

        status: dict[str, Any] = {
            "running": running,
            "runner_mode": self.runner_mode,
            "repo": self.repo.as_posix(),
            "config_path": config_path,
            "run_dir": run_dir.as_posix() if run_dir else "",
            "uptime_seconds": uptime_seconds,
            "exit_code": self._runner_exit_code,
            "stop_file": self._stop_file_name(),
            "stop_file_exists": False,
            "done": 0,
            "failed": 0,
            "warnings": 0,
            "reason": "",
            "last_event": "",
        }
        if run_dir is None:
            return status

        stop_path = run_dir / self._stop_file_name()
        status["stop_file_exists"] = bool(stop_path.exists())
        counts = self._state_counts(run_dir)
        status["done"] = counts["done"]
        status["failed"] = counts["failed"]
        status["warnings"] = counts["warnings"]
        status["reason"] = self._stop_reason(run_dir)
        status["last_event"] = self._latest_event(run_dir)
        return status
