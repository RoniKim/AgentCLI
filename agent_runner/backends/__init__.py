"""agent_runner.backends

Execution backends (runner engines).

This project supports multiple execution engines via ``execution_backend``.
The default is **codex**, preserving historical behavior.

Backends:
- ``codex``: OpenAI/Codex flow implemented in :mod:`agent_runner.cycle`.
- ``claudecode``: Claude Agent SDK runtime (currently a scaffold).

Use :func:`agent_runner.backends.factory.get_runner` to resolve a backend.
"""

from .factory import get_runner  # noqa: F401
