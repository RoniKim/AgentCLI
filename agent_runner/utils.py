from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
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


def detect_stop_reason(stop_paths: Sequence[Path]) -> str:
    """Detect stop reason from one of the provided stop files."""
    for path in stop_paths:
        try:
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if _has_quota_text(text):
                return STOP_REASON_QUOTA
            return STOP_REASON_STOP_FILE
        except Exception:
            continue
    return ""
