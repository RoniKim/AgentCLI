from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .metrics import MetricsLogger


@dataclass
class TraceCtx:
    trace_id: str
    parent_span_id: Optional[str] = None


class Span:
    def __init__(self, metrics: MetricsLogger, ctx: TraceCtx, name: str, **metadata: Any) -> None:
        self.metrics = metrics
        self.ctx = ctx
        self.name = name
        self.metadata: Dict[str, Any] = metadata
        self.span_id = uuid.uuid4().hex

    def __enter__(self) -> "Span":
        self.metrics.event(
            "span_start",
            trace_id=self.ctx.trace_id,
            span_id=self.span_id,
            parent_span_id=self.ctx.parent_span_id,
            name=self.name,
            **self.metadata,
        )
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.metrics.event(
            "span_end",
            trace_id=self.ctx.trace_id,
            span_id=self.span_id,
            parent_span_id=self.ctx.parent_span_id,
            name=self.name,
            ok=exc is None,
            error=str(exc)[:500] if exc else None,
        )


def new_trace_id() -> str:
    # Prefer OpenAI trace id if available via env; otherwise generate.
    tid = os.getenv("OPENAI_TRACE_ID", "").strip()
    return tid or uuid.uuid4().hex


def trace_ctx(parent: Optional[TraceCtx] = None) -> TraceCtx:
    if parent:
        return TraceCtx(trace_id=parent.trace_id, parent_span_id=parent.parent_span_id)
    return TraceCtx(trace_id=new_trace_id(), parent_span_id=None)
