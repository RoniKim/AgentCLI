"""Cycle-end progress report and token usage tracking.

Design principles:
- **never-raise**: every public function catches exceptions internally.
- **zero AI cost**: all formatting is local Python, no model calls.
- **standard library only**: no extra dependencies.
"""
from __future__ import annotations

from typing import Any, List, Optional

from .utils import eprint


# ---------------------------------------------------------------------------
# Duration / token formatting helpers
# ---------------------------------------------------------------------------

def _fmt_duration(seconds: float) -> str:
    """Format seconds as human-readable duration (e.g., '2m 30s')."""
    if seconds < 0:
        return "-"
    if seconds < 60:
        return f"{seconds:.0f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h = int(m // 60)
    return f"{h}h {m % 60:02d}m"


def _fmt_tokens(n: int) -> str:
    """Format token count compactly (e.g., '12.3k', '1.05M')."""
    if n <= 0:
        return "0"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.2f}M"


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------

class TokenTracker:
    """Lightweight per-run token usage accumulator. Never raises."""

    def __init__(self) -> None:
        self._stages: dict[str, dict[str, int]] = {}

    def add(self, stage: str, input_tokens: int = 0, output_tokens: int = 0) -> None:
        try:
            if stage not in self._stages:
                self._stages[stage] = {"input": 0, "output": 0}
            self._stages[stage]["input"] += max(0, int(input_tokens))
            self._stages[stage]["output"] += max(0, int(output_tokens))
        except Exception:
            pass

    def stage_total(self, stage: str) -> int:
        s = self._stages.get(stage, {})
        return s.get("input", 0) + s.get("output", 0)

    def grand_total(self) -> int:
        return sum(s.get("input", 0) + s.get("output", 0) for s in self._stages.values())

    def summary(self) -> dict[str, Any]:
        """Return per-stage breakdown + total for metrics/reporting."""
        result: dict[str, Any] = {}
        for stage, counts in self._stages.items():
            result[stage] = {
                "input": counts["input"],
                "output": counts["output"],
                "total": counts["input"] + counts["output"],
            }
        t_in = sum(s["input"] for s in self._stages.values())
        t_out = sum(s["output"] for s in self._stages.values())
        result["_total"] = {"input": t_in, "output": t_out, "total": t_in + t_out}
        return result

    def format_line(self) -> str:
        """Compact one-line summary for cycle report (e.g., 'Tokens: PM 2.1k + Dev 45.3k = 47.4k')."""
        parts = []
        for stage in sorted(self._stages.keys()):
            t = self.stage_total(stage)
            if t > 0:
                parts.append(f"{stage} {_fmt_tokens(t)}")
        if not parts:
            return ""
        return f"Tokens: {' + '.join(parts)} = {_fmt_tokens(self.grand_total())}"


# ---------------------------------------------------------------------------
# Token extraction helpers (SDK-specific, never-raise)
# ---------------------------------------------------------------------------

def extract_codex_tokens(result: Any) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from Codex RunResult.

    Tries multiple attribute patterns for forward compatibility.
    Returns (0, 0) if usage info is unavailable.
    """
    try:
        inp, out = 0, 0
        # Pattern 1: RunResult.raw_responses[].usage
        for resp in getattr(result, "raw_responses", []) or []:
            usage = getattr(resp, "usage", None)
            if usage:
                inp += int(getattr(usage, "input_tokens", 0) or 0)
                out += int(getattr(usage, "output_tokens", 0) or 0)
        # Pattern 2: aggregated usage on result object
        if inp == 0 and out == 0:
            usage = getattr(result, "usage", None)
            if usage:
                inp = int(getattr(usage, "input_tokens", 0) or 0)
                out = int(getattr(usage, "output_tokens", 0) or 0)
        return inp, out
    except Exception:
        return 0, 0


def extract_claude_tokens(structured: Any) -> tuple[int, int]:
    """Extract (input_tokens, output_tokens) from Claude SDK structured response.

    The Claude Agent SDK subprocess protocol may not expose token usage yet.
    Returns (0, 0) if unavailable.
    """
    try:
        if structured is None:
            return 0, 0
        if isinstance(structured, dict):
            usage = structured.get("usage") or {}
            inp = int(usage.get("input_tokens", 0) or 0)
            out = int(usage.get("output_tokens", 0) or 0)
            return inp, out
        usage = getattr(structured, "usage", None)
        if usage:
            inp = int(getattr(usage, "input_tokens", 0) or 0)
            out = int(getattr(usage, "output_tokens", 0) or 0)
            return inp, out
        return 0, 0
    except Exception:
        return 0, 0


# ---------------------------------------------------------------------------
# Cycle-end progress report
# ---------------------------------------------------------------------------

def print_cycle_report(
    cycle_idx: int,
    cycle_duration: float,
    task_results: List[dict[str, Any]],
    done_count: int,
    total_count: int,
    failed_count: int,
    skipped_count: int,
    token_tracker: Optional[TokenTracker] = None,
) -> None:
    """Print formatted cycle-end progress report to stderr. Never raises.

    task_results: list of dicts with keys:
        id, title, status (done/failed/skipped), reason, duration,
        attempt, max_attempts
    """
    try:
        w = 62
        hdr = f" Cycle {cycle_idx} Complete ({_fmt_duration(cycle_duration)}) "
        lines = [hdr.center(w, "=")]

        for r in task_results:
            tid = r.get("id", "?")
            title = r.get("title", "")
            status = r.get("status", "")
            reason = r.get("reason", "")
            dur = r.get("duration", -1)
            att = r.get("attempt", 0)
            max_att = r.get("max_attempts", 1)

            if len(title) > 32:
                title = title[:30] + ".."

            dur_str = _fmt_duration(dur) if dur >= 0 else "-"

            if status in {"done", "completed"}:
                mark = "+"
                detail = ""
            elif status in {"failed", "regression_failed"}:
                mark = "x"
                detail = f" [{reason or status}"
                if max_att > 1:
                    detail += f" {att}/{max_att}"
                detail += "]"
            elif status in {"review_required", "test_contract_changed", "blocked_env"}:
                mark = "!"
                detail = f" [{reason}"
                if status and status != reason:
                    detail += f" => {status}"
                if max_att > 1:
                    detail += f" {att}/{max_att}"
                detail += "]"
            else:  # skipped
                mark = "-"
                detail = f" [{reason}]" if reason else ""

            lines.append(f"  {mark} {tid:<5} {title}{detail}  {dur_str:>8}")

        lines.append("")
        parts = [f"{done_count}/{total_count} done"]
        if failed_count:
            parts.append(f"{failed_count} failed")
        if skipped_count:
            parts.append(f"{skipped_count} skipped")
        lines.append(f"  Progress: {' | '.join(parts)}")

        if token_tracker:
            tl = token_tracker.format_line()
            if tl:
                lines.append(f"  {tl}")

        lines.append("=" * w)
        eprint("\n".join(lines))
    except Exception:
        pass
