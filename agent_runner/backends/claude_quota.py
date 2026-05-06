from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..utils import eprint


def _load_oauth_token() -> Optional[str]:
    """Load Claude Code OAuth access token from credentials file."""

    cred_path = Path.home() / ".claude" / ".credentials.json"
    if not cred_path.exists():
        return None
    try:
        data = json.loads(cred_path.read_text(encoding="utf-8"))
        return data.get("claudeAiOauth", {}).get("accessToken")
    except Exception:
        return None


def fetch_quota_usage() -> Optional[dict]:
    """Fetch current Claude OAuth quota utilization."""

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
    """Check Claude quota and return an action recommendation."""

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

    if info.get("seven_day", 0) >= seven_day_max:
        return ("stop", info, (seven_day or {}).get("resets_at"))
    if info.get("five_hour", 0) >= five_hour_max:
        return ("wait", info, (five_hour or {}).get("resets_at"))
    return ("ok", info, None)


def seconds_until_reset(resets_at: Optional[str]) -> int:
    """Calculate seconds from now until a Claude ISO 8601 reset timestamp."""

    if not resets_at:
        return 0
    try:
        reset_dt: Optional[datetime] = None
        try:
            reset_dt = datetime.fromisoformat(resets_at)
        except ValueError:
            pass
        if reset_dt is None:
            from datetime import timedelta

            clean = resets_at.replace("Z", "+00:00")
            tz_str = ""
            for sep in ("+", "-"):
                if sep in clean[19:]:
                    sep_idx = clean.rindex(sep)
                    dt_part = clean[:sep_idx]
                    tz_str = clean[sep_idx:]
                    break
            else:
                dt_part = clean
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    reset_dt = datetime.strptime(dt_part, fmt)
                    break
                except ValueError:
                    continue
            if reset_dt is not None:
                offset = timezone.utc
                if tz_str:
                    try:
                        sign = 1 if tz_str[0] == "+" else -1
                        hm = tz_str[1:].split(":")
                        offset = timezone(
                            timedelta(
                                hours=sign * int(hm[0]),
                                minutes=sign * int(hm[1]) if len(hm) > 1 else 0,
                            )
                        )
                    except (ValueError, IndexError):
                        pass
                reset_dt = reset_dt.replace(tzinfo=offset)
        if reset_dt is None:
            return 0
        now = datetime.now(timezone.utc)
        diff = (reset_dt - now).total_seconds()
        return max(0, int(diff) + 60)
    except Exception:
        return 0
