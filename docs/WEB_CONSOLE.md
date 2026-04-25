# AgentCLI Web Console

AgentCLI can serve a local FastAPI web console for browser-based monitoring. The console is repo-owned production code in `web_console/`; it does not run from the exported design prototype under `docs/Design/`.

## Install Dependencies

Use the project virtual environment:

```powershell
cd D:\999.AgentCLI
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m pip install -r requirements.txt
```

If `pip` is broken because the venv points to a missing base Python, recreate the venv or install the missing packages into `.venv\Lib\site-packages`. The web server requires `fastapi` and `uvicorn`.

## Localhost

```powershell
cd D:\999.AgentCLI
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m agent_runner.web `
  --repo "D:\999.AgentCLI" `
  --config-path "D:\999.AgentCLI\configs\AgentCLI-86741102.json" `
  --host 127.0.0.1 `
  --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## LAN Viewing

Bind to all interfaces only on a trusted network:

```powershell
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m agent_runner.web `
  --repo "D:\999.AgentCLI" `
  --config-path "D:\999.AgentCLI\configs\AgentCLI-86741102.json" `
  --host 0.0.0.0 `
  --port 8000
```

Open from another device:

```text
http://<notebook-ip>:8000
```

The current API redacts common secret keys and prompt bodies, but the web console is not authenticated yet. Do not expose it to an untrusted network.

## Runner Controls

Runner controls are disabled by default. Enable them explicitly:

```powershell
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m agent_runner.web `
  --repo "D:\999.AgentCLI" `
  --config-path "D:\999.AgentCLI\configs\AgentCLI-86741102.json" `
  --host 127.0.0.1 `
  --port 8000 `
  --enable-runner-controls
```

Mutating actions require confirmation phrases. Use localhost first before enabling controls on a LAN bind.

## Validation

```powershell
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m compileall agent_runner agent_cli.py
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_web_console*.py"
& "D:\999.AgentCLI\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_worktree*.py"
```

The web smoke path should cover desktop/mobile layout, navigation, command palette, stop confirmation, log filtering, and worktree review before marking the product complete.
