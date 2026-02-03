"""Agent Runner package.

This package contains utilities and simplified scaffolding for running agents.
It provides asynchronous command execution helpers, fallback backlog generation,
and gating helpers for build and test steps. Many of the original AgentCLI
modules are not included here; instead, this package focuses on the core
abstractions necessary to demonstrate improved responsiveness and safer
fallback behavior. Stub modules are provided to satisfy imports from the
original cycle implementation.

"""

# Expose key helpers at the package level for convenience
from .utils import run_cmd, run_cmd_async, force_utf8_stdio, eprint, now_iso  # noqa: F401
from .state import write_default_p0_backlog, TaskItem, load_backlog_json  # noqa: F401
from .gates import run_build_gate, run_test_gate, run_build_gate_async, run_test_gate_async  # noqa: F401