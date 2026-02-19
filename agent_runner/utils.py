from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Sequence, Tuple, Any, Optional, Iterable


STOP_REASON_QUOTA_UTILIZATION = "quota_utilization"
STOP_REASON_QUOTA = "quota_exhausted"
STOP_REASON_STOP_FILE = "stop_file"
STOP_REASON_ALL_TASKS_DONE = "all_tasks_done"
STOP_REASON_PROJECT_COMPLETE = "project_complete"
STOP_REASON_ALL_TASKS_ATTEMPTED = "all_tasks_attempted"
STOP_REASON_PREPARED_ONLY = "prepared_only"
STOP_REASON_NO_TASKS = "no_tasks"
STOP_REASON_PM_REFRESH_NO_BACKLOG = "pm_refresh_no_backlog"
STOP_REASON_IDLE_EXIT = "idle_exit"
STOP_REASON_OK = "ok"

STOP_REASON_PRIORITY: list[str] = [
    STOP_REASON_QUOTA,
    STOP_REASON_QUOTA_UTILIZATION,
    STOP_REASON_STOP_FILE,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_ALL_TASKS_DONE,
    STOP_REASON_ALL_TASKS_ATTEMPTED,
    STOP_REASON_PREPARED_ONLY,
    STOP_REASON_IDLE_EXIT,
    STOP_REASON_OK,
]


def choose_stop_reason(reasons: Iterable[str]) -> str:
    candidates = [str(r).strip() for r in reasons if str(r).strip()]
    if not candidates:
        return ""
    priority = {reason: idx for idx, reason in enumerate(STOP_REASON_PRIORITY)}
    best = candidates[0]
    best_rank = priority.get(best, len(priority))
    for r in candidates[1:]:
        rank = priority.get(r, len(priority))
        if rank < best_rank:
            best = r
            best_rank = rank
    return best


def force_utf8_stdio() -> None:
    """Best-effort UTF-8 IO for Windows/CI."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("LANG", "ko_KR.UTF-8")
    os.environ.setdefault("LC_ALL", "ko_KR.UTF-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def eprint(msg: str) -> None:
    print(msg, file=sys.stderr)


def run_cmd(cmd: Sequence[str], cwd: Path, timeout_sec: int = 600) -> Tuple[int, str]:
    """Run a subprocess and capture output (stdout+stderr)."""
    if not cmd:
        return (1, "empty command")
    try:
        r = subprocess.run(
            list(cmd),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        out = (r.stdout or "") + ("\n" + r.stderr if r.stderr else "")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, f"TIMEOUT: {' '.join(cmd)}"
    except (OSError, FileNotFoundError) as e:
        return (127, str(e))


async def run_cmd_async(
    cmd: Sequence[str],
    cwd: Path,
    log_path: Path,
    *,
    timeout_sec: int = 600,
    stop_path: Optional[Path] = None,
    max_output_bytes: int = 10_000_000,
) -> tuple[int, str]:
    """Run a subprocess asynchronously, streaming output to log_path.

    Returns (returncode, summary). Output is streamed to disk with a hard cap; excess output is discarded
    and a TRUNCATED marker is appended once.
    """
    if not cmd:
        return (1, "empty command")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *list(cmd),
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except (OSError, FileNotFoundError) as e:
        return (127, str(e))
    truncated = False
    written = 0
    log_fh = log_path.open("ab")
    reader_tasks: list[asyncio.Task] = []

    try:
        async def _reader(stream: asyncio.StreamReader, label: str) -> None:
            nonlocal written, truncated
            if stream is None:
                return
            while True:
                chunk = await stream.read(8192)
                if not chunk:
                    break
                if written >= max_output_bytes:
                    if truncated:
                        return
                    log_fh.write(b"\n[TRUNCATED OUTPUT]\n")
                    truncated = True
                    continue
                remaining = max_output_bytes - written
                data = chunk[:remaining]
                log_fh.write(data)
                written += len(data)
                if len(chunk) > remaining and not truncated:
                    log_fh.write(b"\n[TRUNCATED OUTPUT]\n")
                    truncated = True

        reader_tasks = [
            asyncio.create_task(_reader(proc.stdout, "stdout")),
            asyncio.create_task(_reader(proc.stderr, "stderr")),
        ]

        summary = ""
        while True:
            if stop_path is not None and stop_path.exists():
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                summary = "stopped"
                break
            if timeout_sec and (time.monotonic() - start) > timeout_sec:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                summary = "timeout"
                break
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.2)
        rc = await proc.wait()
    finally:
        # Ensure subprocess is terminated on any exit path (CancelledError, etc.)
        if proc.returncode is None:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
        await asyncio.gather(*reader_tasks, return_exceptions=True)
        log_fh.close()

    if truncated:
        summary = (summary + " " if summary else "") + "truncated"
    if not summary:
        summary = "ok"
    return rc, summary


def read_text_robust(path: Path) -> tuple[str, str]:
    """Return (text, status). status is ok|binary|missing|error."""
    if not path.exists():
        return "", "missing"
    try:
        data = path.read_bytes()
        if b"\x00" in data[:4096]:
            return "", "binary"
        return data.decode("utf-8", errors="replace"), "ok"
    except Exception:
        return "", "error"


def load_json_if_exists(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            import json
            raw = path.read_text(encoding="utf-8", errors="replace") or ""
            return json.loads(raw) if raw.strip() else default
    except Exception:
        pass
    return default


def ensure_relative_to_repo(repo: Path, maybe_rel: str) -> Path:
    p = Path(maybe_rel)
    resolved = p.resolve() if p.is_absolute() else (repo / p).resolve()
    try:
        resolved.relative_to(repo.resolve())
    except Exception as ex:
        raise ValueError(f"Path escapes repo: {maybe_rel}") from ex
    return resolved


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", errors="replace", delete=False, dir=str(path.parent)) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    try:
        os.replace(tmp_path, path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(path, data)


def safe_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", errors="replace")


def _has_quota_text(text: str) -> bool:
    """Canonical quota/billing/rate-limit needle list.

    This is the **single source of truth** — backends should call
    ``has_quota_text`` instead of maintaining their own needle lists.
    """
    s = (text or "").lower()
    if not s:
        return False
    needles = (
        # OpenAI / generic billing
        "insufficient_quota",
        "quota exceeded",
        "exceeded your current quota",
        "quota exhausted",
        "billing hard limit",
        "hard limit",
        "plan and billing",
        "plans & billing",
        "payment required",
        "budgetexceeded",
        # Usage-limit strings (Codex / CLI)
        "you've hit your usage limit",
        "you've hit your limit",
        "hit your limit",
        "purchase more credits",
        "upgrade to pro",
        "codex/settings/usage",
        "usage limit",
        "user limit",
        "user_limit",
        "credit balance is too low",
        "insufficient credits",
        "purchase credits",
        "spend limit",
        "monthly spend limit",
        # Claude Code CLI rate-limit patterns
        "usage cap",
        "reached your",
        "token limit exceeded",
        "account limit",
        "api key limit",
        "limit resets",
    )
    return any(n in s for n in needles)




# Public alias (used by backends) for quota/credits text detection
has_quota_text = _has_quota_text


# ---------------------------------------------------------------------------
# OAuth usage / quota check (unofficial endpoint)
# ---------------------------------------------------------------------------

def _load_oauth_token() -> Optional[str]:
    """Load Claude Code OAuth access token from credentials file.

    Looks for ``~/.claude/.credentials.json`` on all platforms.
    Returns the access token string, or None if unavailable.
    """
    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.exists():
        return None
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        return data.get("claudeAiOauth", {}).get("accessToken")
    except Exception:
        return None


def fetch_quota_usage() -> Optional[dict]:
    """Fetch current quota utilization from the Claude OAuth usage endpoint.

    Returns a dict like::

        {
            "five_hour": {"utilization": 30.0, "resets_at": "2026-02-11T08:59:59+00:00"},
            "seven_day": {"utilization": 39.0, "resets_at": "2026-02-13T06:59:59+00:00"},
            ...
        }

    Returns None on any failure (no token, network error, etc.). Never raises.
    """
    token = _load_oauth_token()
    if not token:
        return None
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.anthropic.com/api/oauth/usage",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        hint = ""
        if "401" in str(exc):
            hint = " (OAuth 토큰이 만료되었을 수 있습니다. Claude Code를 실행하면 토큰이 자동 갱신됩니다)"
        eprint(f"[WARN] fetch_quota_usage failed: {exc}{hint}")
        return None


def check_quota_utilization(
    *,
    five_hour_max: float = 95.0,
    seven_day_max: float = 95.0,
) -> tuple[str, dict, Optional[str]]:
    """Check quota and return action recommendation.

    Returns ``(action, usage_info, resets_at)`` where:

    - *action*: ``"ok"`` | ``"wait"`` | ``"stop"`` | ``"skip"``
    - *usage_info*: ``{"five_hour": float, "seven_day": float}``
    - *resets_at*: ISO 8601 timestamp (for ``"wait"`` / ``"stop"``) or None
    """
    usage = fetch_quota_usage()
    if usage is None:
        return ("skip", {}, None)

    info: dict[str, float] = {}
    five_hour = usage.get("five_hour")
    seven_day = usage.get("seven_day")
    if five_hour and five_hour.get("utilization") is not None:
        info["five_hour"] = float(five_hour["utilization"])
    if seven_day and seven_day.get("utilization") is not None:
        info["seven_day"] = float(seven_day["utilization"])

    # Check 7-day first (harder limit — stopping is the only option)
    if info.get("seven_day", 0) >= seven_day_max:
        return ("stop", info, (seven_day or {}).get("resets_at"))

    # Check 5-hour (soft limit — can wait for reset)
    if info.get("five_hour", 0) >= five_hour_max:
        return ("wait", info, (five_hour or {}).get("resets_at"))

    return ("ok", info, None)


def seconds_until_reset(resets_at: Optional[str]) -> int:
    """Calculate seconds from now until the given ISO 8601 reset timestamp.

    Returns 0 if *resets_at* is None, unparseable, or already in the past.
    """
    if not resets_at:
        return 0
    try:
        from datetime import timezone
        reset_dt: Optional[datetime] = None
        # Try standard fromisoformat (Python 3.11+ handles timezone offsets)
        try:
            reset_dt = datetime.fromisoformat(resets_at)
        except ValueError:
            pass
        # Fallback for Python 3.10: strip timezone and parse manually
        if reset_dt is None:
            from datetime import timedelta
            clean = resets_at.replace("Z", "+00:00")
            # Split off timezone offset (e.g. "+09:00", "-05:00")
            tz_str = ""
            for sep in ("+", "-"):
                if sep in clean[19:]:
                    sep_idx = clean.rindex(sep)
                    dt_part = clean[:sep_idx]
                    tz_str = clean[sep_idx:]
                    break
            else:
                dt_part = clean
            # Try with microseconds, then without
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    reset_dt = datetime.strptime(dt_part, fmt)
                    break
                except ValueError:
                    continue
            # Apply parsed timezone offset (default to UTC)
            if reset_dt is not None:
                offset = timezone.utc
                if tz_str:
                    try:
                        sign = 1 if tz_str[0] == "+" else -1
                        hm = tz_str[1:].split(":")
                        offset = timezone(timedelta(
                            hours=sign * int(hm[0]),
                            minutes=sign * int(hm[1]) if len(hm) > 1 else 0,
                        ))
                    except (ValueError, IndexError):
                        pass
                reset_dt = reset_dt.replace(tzinfo=offset)
        if reset_dt is None:
            return 0
        now = datetime.now(timezone.utc)
        diff = (reset_dt - now).total_seconds()
        return max(0, int(diff) + 60)  # +60s buffer
    except Exception:
        return 0


class CodexAppServerError(RuntimeError):
    """Raised when codex app-server JSON-RPC requests fail."""


@dataclass(frozen=True)
class CodexRateLimitWindow:
    """Normalized codex rate-limit window."""

    limit_id: str
    used_percent: float
    window_minutes: int
    resets_at_unix: int
    window_type: str = "primary"  # "primary" (5h) | "secondary" (7d)

    @property
    def remaining_percent(self) -> float:
        return max(0.0, 100.0 - float(self.used_percent))


class _CodexAppServerClient:
    """Minimal JSON-RPC client for ``codex app-server`` over stdio JSONL.

    Not thread-safe — all public methods must be called from a single thread.
    Use as a context manager (``with _CodexAppServerClient() as client:``)
    or call :meth:`close` explicitly in a ``finally`` block.
    """

    def __init__(self, codex_path: str = "codex", timeout_s: float = 10.0):
        # Resolve bare name to full path — Windows can't execute .cmd shims
        # via CreateProcess without the full path.
        resolved = shutil.which(codex_path) or codex_path
        self._proc = subprocess.Popen(
            [resolved, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        if self._proc.stdin is None or self._proc.stdout is None:
            raise CodexAppServerError("Failed to start codex app-server (stdio unavailable)")

        # Register with process_guard for orphan cleanup
        try:
            from .process_guard import register_pid
            if self._proc.pid:
                register_pid(self._proc.pid)
        except Exception:
            pass

        self._queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        self._next_id = 1  # monotonic, single-thread only
        self._reader = threading.Thread(
            target=self._reader_loop,
            daemon=True,
            name="codex-app-server-reader",
        )
        self._reader.start()

        self._initialize(timeout_s=timeout_s)

    def __enter__(self) -> "_CodexAppServerClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._proc.poll() is None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

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
    raw: dict[str, Any], limit_id: str, window_key: str,
) -> Optional[CodexRateLimitWindow]:
    """Parse a single primary/secondary window entry from codex rate-limit data."""
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
    """Normalize codex app-server rate-limit response into windows.

    Keys are ``"{limit_id}:primary"`` and ``"{limit_id}:secondary"`` to
    distinguish 5-hour vs 7-day windows.
    """
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


def seconds_until_unix_reset(resets_at_unix: Optional[int]) -> int:
    """Calculate seconds until a Unix reset timestamp."""
    if resets_at_unix is None:
        return 0
    try:
        diff = int(resets_at_unix) - int(time.time())
        return max(0, diff + 60)  # +60s safety buffer
    except Exception:
        return 0


def check_codex_quota_utilization(
    *,
    five_hour_max: float = 95.0,
    seven_day_max: float = 95.0,
    codex_path: str = "codex",
) -> tuple[str, dict[str, Any], Optional[int]]:
    """Check codex app-server quota and return (action, info, resets_at_unix).

    Mirrors :func:`check_quota_utilization` (Claude backend) behaviour:

    - 7-day window exceeds *seven_day_max* → ``"stop"`` (hard limit)
    - 5-hour window exceeds *five_hour_max* → ``"wait"`` (soft limit)
    - Both under threshold → ``"ok"``
    """
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

            info["used_percent_by_limit_id"] = {
                k: round(v.used_percent, 3) for k, v in windows.items()
            }

            # Separate primary (5h) and secondary (7d) windows
            primary_wins = [w for w in windows.values() if w.window_type == "primary"]
            secondary_wins = [w for w in windows.values() if w.window_type == "secondary"]

            hottest_5h = max(primary_wins, key=lambda w: w.used_percent) if primary_wins else None
            hottest_7d = max(secondary_wins, key=lambda w: w.used_percent) if secondary_wins else None

            if hottest_5h:
                info["five_hour"] = round(hottest_5h.used_percent, 3)
            if hottest_7d:
                info["seven_day"] = round(hottest_7d.used_percent, 3)

            # Legacy compat keys
            hottest_all = max(windows.values(), key=lambda w: w.used_percent)
            info["max_used_limit_id"] = hottest_all.limit_id
            info["max_used_percent"] = round(hottest_all.used_percent, 3)
            info["remaining_percent"] = round(hottest_all.remaining_percent, 3)

            # 7d 초과 → stop (hard limit, Claude 백엔드와 동일)
            if hottest_7d and hottest_7d.used_percent >= _7d:
                return ("stop", info, int(hottest_7d.resets_at_unix))

            # 5h 초과 → wait (soft limit)
            if hottest_5h and hottest_5h.used_percent >= _5h:
                return ("wait", info, int(hottest_5h.resets_at_unix))

            return ("ok", info, None)
    except Exception as exc:
        eprint(f"[WARN] check_codex_quota_utilization failed: {exc}")
        return ("skip", {}, None)


def write_heartbeat(run_dir: Path) -> None:
    """Write a HEARTBEAT file for external monitoring."""
    try:
        (run_dir / "HEARTBEAT").write_text(now_iso() + "\n", encoding="utf-8")
    except Exception:
        pass


def severity_at_or_above(found: str, threshold: str) -> bool:
    """Return True if *found* severity is at or above *threshold*."""
    order = {"low": 0, "medium": 1, "high": 2}
    return order.get(found, 1) >= order.get(threshold, 1)


def budget_exceeded(key: str, current: int, limit: int) -> bool:
    """Return True if *current* has reached or exceeded *limit* (0 means unlimited)."""
    if limit <= 0:
        return False
    return current >= limit


def is_unsafe_path(raw: str) -> bool:
    """Return True if *raw* contains path-traversal patterns."""
    try:
        return ".." in Path(raw).parts
    except Exception:
        return True


def hash_prompt(text: str) -> str:
    """Return a short SHA-256 digest of *text* (10 hex chars)."""
    import hashlib
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:10]


# All known stop reason constant values for direct STOP-file matching
_KNOWN_STOP_REASONS: frozenset[str] = frozenset({
    STOP_REASON_QUOTA,
    STOP_REASON_QUOTA_UTILIZATION,
    STOP_REASON_STOP_FILE,
    STOP_REASON_ALL_TASKS_DONE,
    STOP_REASON_PROJECT_COMPLETE,
    STOP_REASON_ALL_TASKS_ATTEMPTED,
    STOP_REASON_PREPARED_ONLY,
    STOP_REASON_NO_TASKS,
    STOP_REASON_PM_REFRESH_NO_BACKLOG,
    STOP_REASON_IDLE_EXIT,
    STOP_REASON_OK,
})


def detect_stop_reason(stop_paths: Sequence[Path]) -> str:
    """Detect stop reason from one of the provided stop files.

    If the file content exactly matches a known stop reason constant,
    return that constant directly.  Otherwise fall back to quota-text
    heuristic or generic ``STOP_REASON_STOP_FILE``.
    """
    for path in stop_paths:
        try:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            normalized = text.strip().lower()
            if normalized in _KNOWN_STOP_REASONS:
                return normalized
            if _has_quota_text(text):
                return STOP_REASON_QUOTA
            return STOP_REASON_STOP_FILE
        except Exception:
            continue
    return ""
