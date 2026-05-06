"""Stage-based pipeline orchestration.

This package introduces a pluggable stage architecture:
- Built-in stages: PM, Dev, QA, Security
- External plugin stages: "pkg.module:ClassName" (must subclass Stage)

The goal is to decouple orchestration from any specific execution backend.
"""

from .manager import PipelineManager
from .stage_registry import (
    PluginStageLoadError,
    build_plugin_stage_diagnostics_payload,
    coerce_plugin_bool,
    format_plugin_stage_diagnostics_markdown,
    make_stages,
    normalize_plugin_allowlist,
    parse_roles,
)

__all__ = [
    "PipelineManager",
    "PluginStageLoadError",
    "build_plugin_stage_diagnostics_payload",
    "coerce_plugin_bool",
    "format_plugin_stage_diagnostics_markdown",
    "make_stages",
    "normalize_plugin_allowlist",
    "parse_roles",
]
