from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..quota_utils import seconds_until_unix_reset
from ..utils import eprint, subprocess_close_fds_kwargs


class CodexAppServerError(RuntimeError):
    """Raised when codex app-server JSON-RPC requests fail."""


@dataclass(frozen=True)
class CodexRateLimitWindow:
    """Normalized Codex rate-limit window."""

    limit_id: str
    used_percent: float
    window_minutes: int
    resets_at_unix: int
    window_type: str = "primary"

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - float(self.used_percent))


class _CodexAppServerClient:
    """Minimal JSON-RPC client for ``codex app-server`` over stdio JSONL."""

    def __init__(self, codex_path: str = "codex", timeout_s: float = 10.0):
        resolved = shutil.which(codex_path) or codex_path
        self._registered_pid: int | None = None
        self._closed = False
        self._proc = subprocess.Popen(
            [resolved, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **subprocess_close_fds_kwargs(),
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise CodexAppServerError("Failed to start codex app-server (stdio unavailable)")

        try:
            from ..process_guard import register_pid

            if self._proc.pid:
                self._registered_pid = int(self._proc.pid)
                register_pid(self._proc.pid)
        except Exception:
            pass

        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._next_id = 1
        self._reader = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="codex-app-server-reader",
        )
        self._reader.start()

        try:
            self._initialize(timeout_s=timeout_s)
        except Exception:
            self.close()
            raise

    def __enter__(self) -> "_CodexAppServerClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        pid = self._registered_pid or (int(self._proc.pid) if self._proc.pid else None)
        try:
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                try:
                    self._proc.wait(timeout=2)
                except Exception:
                    try:
                        self._proc.kill()
                    except Exception:
                        pass
                    try:
                        self._proc.wait(timeout=2)
                    except Exception:
                        pass
        finally:
            for stream in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
                if stream is None:
                    continue
                try:
                    stream.close()
                except Exception:
                    pass

            if self._reader.is_alive() and threading.current_thread() is not self._reader:
                try:
                    self._reader.join(timeout=2)
                except Exception:
                    pass

            if pid is not None:
                try:
                    from ..process_guard import terminate_process_tree, unregister_pid_if_exited

                    terminate_process_tree(pid, include_root=self._proc.poll() is None, wait=True)
                    unregister_pid_if_exited(pid)
                except Exception:
                    pass
                self._registered_pid = None

    def _reader_loop(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except Exception:
                continue
            if isinstance(msg, dict):
                self._queue.put(msg)

    def _send(self, payload: dict[str, Any]) -> None:
        if self._proc.stdin is None:
            raise CodexAppServerError("codex app-server stdin closed")
        try:
            self._proc.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except Exception as exc:
            raise CodexAppServerError(f"Failed to write to codex app-server: {exc}") from exc

    def _rpc(self, method: str, *, params: Optional[dict[str, Any]] = None, timeout_s: float = 10.0) -> dict[str, Any]:
        req_id = self._next_id
        self._next_id += 1

        payload: dict[str, Any] = {"method": method, "id": req_id}
        if params is not None:
            payload["params"] = params
        self._send(payload)

        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                msg = self._queue.get(timeout=remaining)
            except queue.Empty:
                break

            if msg.get("id") != req_id:
                continue
            if msg.get("error"):
                raise CodexAppServerError(f"{method} failed: {msg.get('error')}")

            result = msg.get("result")
            if isinstance(result, dict):
                return result
            return {}

        raise CodexAppServerError(f"Timeout waiting for response: {method}")

    def _initialize(self, *, timeout_s: float = 10.0) -> None:
        self._rpc(
            "initialize",
            params={
                "clientInfo": {
                    "name": "agent_runner",
                    "title": "AgentCLI",
                    "version": "0.1.0",
                }
            },
            timeout_s=timeout_s,
        )
        self._send({"method": "initialized", "params": {}})

    def account_read(self, *, refresh_token: bool = False) -> dict[str, Any]:
        return self._rpc("account/read", params={"refreshToken": bool(refresh_token)})

    def rate_limits_read(self) -> dict[str, Any]:
        return self._rpc("account/rateLimits/read")


def _to_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return None


def _to_int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except Exception:
        return None


def _parse_window_entry(
    raw: dict[str, Any],
    limit_id: str,
    window_key: str,
) -> Optional[CodexRateLimitWindow]:
    entry = raw.get(window_key) if isinstance(raw.get(window_key), dict) else {}
    used = _to_float(entry.get("usedPercent"))
    mins = _to_int(entry.get("windowDurationMins"))
    reset_unix = _to_int(entry.get("resetsAt"))
    if used is None or mins is None or reset_unix is None:
        return None
    lid = str(limit_id or "codex").strip() or "codex"
    return CodexRateLimitWindow(
        limit_id=lid,
        used_percent=used,
        window_minutes=mins,
        resets_at_unix=reset_unix,
        window_type=window_key,
    )


def parse_codex_rate_limit_windows(rate_limits_result: dict[str, Any]) -> dict[str, CodexRateLimitWindow]:
    """Normalize Codex app-server rate-limit response into windows."""

    windows: dict[str, CodexRateLimitWindow] = {}
    if not isinstance(rate_limits_result, dict):
        return windows

    by_id = rate_limits_result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict) and by_id:
        for limit_id, raw in by_id.items():
            if not isinstance(raw, dict):
                continue
            for wkey in ("primary", "secondary"):
                win = _parse_window_entry(raw, limit_id, wkey)
                if win is not None:
                    windows[f"{win.limit_id}:{wkey}"] = win
        if windows:
            return windows

    single = rate_limits_result.get("rateLimits")
    if isinstance(single, dict):
        lid = str(single.get("limitId") or "codex").strip() or "codex"
        for wkey in ("primary", "secondary"):
            win = _parse_window_entry(single, lid, wkey)
            if win is not None:
                windows[f"{win.limit_id}:{wkey}"] = win

    return windows


def check_codex_quota_utilization(
    *,
    five_hour_max: float = 95.0,
    seven_day_max: float = 95.0,
    codex_path: str = "codex",
) -> tuple[str, dict[str, Any], Optional[int]]:
    """Check Codex app-server quota and return an action recommendation."""

    if codex_path == "codex" and shutil.which("codex") is None:
        return ("skip", {}, None)

    try:
        _5h = float(five_hour_max)
    except Exception:
        _5h = 95.0
    if _5h <= 0:
        _5h = 95.0

    try:
        _7d = float(seven_day_max)
    except Exception:
        _7d = 95.0
    if _7d <= 0:
        _7d = 95.0

    try:
        with _CodexAppServerClient(codex_path=codex_path, timeout_s=10.0) as client:
            account = client.account_read(refresh_token=False)
            acct = account.get("account") if isinstance(account, dict) and isinstance(account.get("account"), dict) else {}

            account_type = str(acct.get("type") or account.get("type") or "").strip()
            plan_type = str(acct.get("planType") or account.get("planType") or "").strip()

            limits = client.rate_limits_read()
            windows = parse_codex_rate_limit_windows(limits)

            info: dict[str, Any] = {
                "account_type": account_type,
                "plan_type": plan_type,
            }
            if not windows:
                return ("skip", info, None)

            info["used_percent_by_limit_id"] = {k: round(v.used_percent, 3) for k, v in windows.items()}

            primary_wins = [w for w in windows.values() if w.window_type == "primary"]
            secondary_wins = [w for w in windows.values() if w.window_type == "secondary"]

            hottest_5h = max(primary_wins, key=lambda w: w.used_percent) if primary_wins else None
            hottest_7d = max(secondary_wins, key=lambda w: w.used_percent) if secondary_wins else None

            if hottest_5h:
                info["five_hour"] = round(hottest_5h.used_percent, 3)
            if hottest_7d:
                info["seven_day"] = round(hottest_7d.used_percent, 3)

            hottest_all = max(windows.values(), key=lambda w: w.used_percent)
            info["max_used_limit_id"] = hottest_all.limit_id
            info["max_used_percent"] = round(hottest_all.used_percent, 3)
            info["remaining_percent"] = round(hottest_all.remaining_percent, 3)

            if hottest_7d and hottest_7d.used_percent >= _7d:
                return ("stop", info, int(hottest_7d.resets_at_unix))
            if hottest_5h and hottest_5h.used_percent >= _5h:
                return ("wait", info, int(hottest_5h.resets_at_unix))
            return ("ok", info, None)
    except Exception as exc:
        eprint(f"[WARN] check_codex_quota_utilization failed: {exc}")
        return ("skip", {}, None)
