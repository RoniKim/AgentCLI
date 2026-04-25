# AgentCLI Implementation Conventions

## General

- Keep changes scoped to the current task and the active GOALS item.
- Prefer existing AgentCLI modules and config keys over new parallel concepts.
- Do not commit secrets, service keys, chat tokens, or local credentials.
- Do not mutate `.AgentCLI/agent_runs/` artifacts except as runtime output from AgentCLI.
- Do not edit `docs/Design/project/` as production implementation source. Use it as reference.

## Python

- Use `from __future__ import annotations` in new Python modules.
- Prefer type hints for public functions and dataclasses.
- Keep subprocess calls argv-list based.
- Preserve Windows support.
- Validate config and filesystem paths defensively.
- Run `.venv/Scripts/python.exe -m compileall agent_runner agent_cli.py` after Python changes on this Windows repo.

## Web Console

- Match `docs/Design/project/AgentCLI Web - A.html` before adding unrelated UI.
- Preserve the Direction A visual system:
  - Background near `#0a0c0a`.
  - Surface colors near `#10130f` and `#161a14`.
  - Accent green near `#7ee38a`.
  - Warning, error, and info colors remain visually distinct.
  - Compact terminal-style density and thin borders.
- Use stable dimensions for shell chrome, nav, cards, charts, and log rows so hover/live updates do not shift layout.
- Use responsive constraints for mobile. Text must not overlap or overflow controls.
- Avoid marketing-page structure for the console. The first screen should be the usable application surface.
- If iconography is added through a dependency, document the dependency. Otherwise keep simple inline symbols consistent and accessible.
- Production web code should not rely on Babel-in-browser compilation.
- Avoid CDN-only runtime app logic. If external fonts are optional, provide system fallbacks.

## AgentCLI PM/Dev/QA Behavior

- PM tasks must be implementation work that produces a git diff.
- PM must not create tasks only for analysis, backlog generation, prompt editing, or run artifact cleanup.
- Dev must not install packages. If a package is necessary, write `DEPENDENCY_REQUIRED.md` in the run directory and stop.
- QA should verify both the web surface and Python CLI compile safety using the repo venv Python.
- Prefer small cohesive implementation slices over broad rewrites.
