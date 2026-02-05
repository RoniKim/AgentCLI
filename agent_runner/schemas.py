from __future__ import annotations

from typing import List, Literal, Optional

try:
    from pydantic import BaseModel, Field
except Exception as ex:  # pragma: no cover
    raise ImportError(
        "Missing dependency: pydantic. Install: pip install -U pydantic"
    ) from ex


class BacklogTaskV2(BaseModel):
    """Atomic, single-iteration task for the Dev agent."""

    id: str = Field(..., description="Task ID like T01")
    title: str = Field(..., description="Short title")
    prompt: str = Field(..., description="Implementation instructions; must be executable")
    files: List[str] = Field(default_factory=list, description="Suggested files to touch (relative paths)")
    done_when: str = Field(..., description="Objective definition of done")
    skills: List[str] = Field(default_factory=list, description="Selected skill IDs for this task")
    skills_rationale: Optional[str] = Field(
        default=None, description="Why these skills were selected for the task"
    )


class PMOutputV2(BaseModel):
    """PM final output schema (structured)."""

    kind: Literal["bootstrap", "incremental", "refresh", "skip"] = Field(
        ..., description="PM run kind"
    )
    summary: str = Field(..., description="1-3 sentence summary of what changed and why")

    # Backlog (single source of truth in v2.0)
    tasks: List[BacklogTaskV2] = Field(
        default_factory=list,
        description="Ordered list of atomic remaining tasks (each should produce a git diff)",
    )

    # Optional run-local notes
    notes_md: Optional[str] = Field(
        default=None,
        description="Optional Markdown notes to write into run_dir/NOTES.md",
    )

    warnings: List[str] = Field(default_factory=list, description="Important warnings or risks")
    open_questions: List[str] = Field(
        default_factory=list, description="1-3 clarifying questions if requirements are ambiguous"
    )

    analysis_updated: bool = Field(
        default=False, description="Whether PROJECT_ANALYSIS.md was updated by PM"
    )
    analysis_path: Optional[str] = Field(
        default=None, description="Path (relative) to analysis file that was updated"
    )


class QAFollowupItem(BaseModel):
    title: str = Field(..., description="Short title for the QA follow-up")
    prompt: str = Field(..., max_length=1000, description="Executable QA follow-up prompt (<=1000 chars)")
    files: List[str] = Field(default_factory=list, description="Optional files to inspect/update")
    severity: Optional[str] = Field(default=None, description="Optional severity (low/medium/high)")


class QAFollowupsV1(BaseModel):
    kind: Literal["qa_followups_v1"] = Field("qa_followups_v1", description="QA followups schema version")
    cycle: Optional[int] = Field(default=None, description="Cycle index (optional)")
    followups: List[QAFollowupItem] = Field(default_factory=list, description="QA follow-up candidates")
    notes: Optional[str] = Field(default=None, description="Optional notes")


def pm_output_json_schema() -> dict:
    """Return a JSON Schema dict for OpenAI Structured Outputs (compatible shape).

    Note: Pydantic's schema is used for local validation. For API-level strictness,
    pass this schema via response_format={"type":"json_schema", ...}.
    """

    # Pydantic v2
    return PMOutputV2.model_json_schema()
