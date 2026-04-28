from __future__ import annotations

from typing import Any


PIPELINE_STAGE_ORDER: tuple[str, ...] = ("PM", "Security", "Dev", "QA", "Reporter")
BUILTIN_ROLE_SPECS: tuple[str, ...] = tuple(stage for stage in PIPELINE_STAGE_ORDER if stage != "Reporter")
DEFAULT_ROLE_SPECS: tuple[str, ...] = ("PM", "Dev", "QA")
ENTERPRISE_ROLE_SPECS: tuple[str, ...] = ("PM", "Security", "Dev", "QA")

ROLE_SPEC_CANONICALS: dict[str, str] = {
    "pm": "PM",
    "security": "Security",
    "dev": "Dev",
    "qa": "QA",
}

PIPELINE_ROLE_HINT = "Built-in order: PM, Security, Dev, QA. Plugin specs like pkg.mod:Class are preserved."

CODEX_DEV_MODEL_LADDER: tuple[str, str, str] = ("gpt-5.4-mini", "gpt-5.4", "gpt-5.5")
CODEX_MODEL_DEFAULTS: dict[str, str] = {
    "pm_model": "gpt-5.5",
    "dev_model": CODEX_DEV_MODEL_LADDER[0],
    "dev_model_tier1": CODEX_DEV_MODEL_LADDER[1],
    "dev_model_tier2": CODEX_DEV_MODEL_LADDER[2],
    "qa_model": "gpt-5.5",
    "reporter_model": "gpt-5.4-mini",
}

PIPELINE_ROLE_FIELD_SPEC: dict[str, Any] = {
    "path": "roles",
    "group": "project",
    "kind": "multienum",
    "label": "Pipeline roles",
    "options": BUILTIN_ROLE_SPECS,
    "allow_empty": False,
    "desc": "Stages enabled in the pipeline.",
    "hint": PIPELINE_ROLE_HINT,
}

CODEX_MODEL_FIELD_SPECS: tuple[dict[str, Any], ...] = (
    {
        "path": "pm_model",
        "group": "codex_models",
        "kind": "text",
        "label": "PM model",
        "allow_empty": False,
        "desc": "Model used for PM planning and backlog generation.",
        "hint": "Approved Codex default: gpt-5.5.",
    },
    {
        "path": "dev_model",
        "group": "codex_models",
        "kind": "text",
        "label": "Dev fallback model",
        "allow_empty": False,
        "desc": "First model in the Dev fallback ladder.",
        "hint": f"Approved ladder: {' -> '.join(CODEX_DEV_MODEL_LADDER)}.",
    },
    {
        "path": "dev_model_tier1",
        "group": "codex_models",
        "kind": "text",
        "label": "Dev fallback tier 1",
        "allow_empty": False,
        "desc": "Second model in the Dev fallback ladder.",
        "hint": "Escalates to gpt-5.4 when the base model is not enough.",
    },
    {
        "path": "dev_model_tier2",
        "group": "codex_models",
        "kind": "text",
        "label": "Dev fallback tier 2",
        "allow_empty": False,
        "desc": "Final model in the Dev fallback ladder.",
        "hint": "Escalates to gpt-5.5 as the last approved Codex tier.",
    },
    {
        "path": "qa_model",
        "group": "codex_models",
        "kind": "text",
        "label": "QA model",
        "allow_empty": False,
        "desc": "Model used for QA verification.",
        "hint": "Approved Codex default: gpt-5.5.",
    },
    {
        "path": "reporter_model",
        "group": "codex_models",
        "kind": "text",
        "label": "Reporter model",
        "allow_empty": False,
        "desc": "Model used for close-out reporting.",
        "hint": "Approved Codex default: gpt-5.4-mini.",
    },
)


def default_role_string() -> str:
    return ",".join(DEFAULT_ROLE_SPECS)


def enterprise_role_string() -> str:
    return ",".join(ENTERPRISE_ROLE_SPECS)
