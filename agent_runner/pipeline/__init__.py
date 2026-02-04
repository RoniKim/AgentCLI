"""Stage-based pipeline orchestration.

This package introduces a pluggable stage architecture:
- Built-in stages: PM, Dev, QA, Security
- External plugin stages: "pkg.module:ClassName" (must subclass Stage)

The goal is to decouple orchestration from any specific execution backend.
"""

from .manager import PipelineManager
from .stage_registry import make_stages, parse_roles

__all__ = ["PipelineManager", "make_stages", "parse_roles"]
