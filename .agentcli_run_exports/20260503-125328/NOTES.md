# T34 Notes

## Files changed
- agent_runner/stop_progress.py
- agent_runner/shell.py
- agent_runner/remote/controller.py
- agent_runner/cycle.py
- agent_runner/backends/claudecode.py
- tests/test_stop_progress.py

## Why
- Centralized stop-progress and backend stop-snapshot artifact writing in `agent_runner.stop_progress` so shell, controller, Codex, and Claude stop paths no longer build those payloads independently.
- Preserved existing stop artifact filenames and stop-progress schema while repairing malformed or partial pre-existing stop-progress payloads during subsequent writes.
- Added Claude backend stop checkpoint parity for dev/build/test/fast-regression stop paths.

## How to validate
- `python -m py_compile agent_runner/stop_progress.py agent_runner/shell.py agent_runner/remote/controller.py agent_runner/cycle.py agent_runner/backends/claudecode.py tests/test_stop_progress.py`
- `python -m unittest tests.test_stop_progress`
