# Current State - AgentCLI Web Console Work

## Repository State

- AgentCLI already has a mature Python CLI runner with Codex and Claude Code backends.
- The default prompt templates in `templates/agent_prompts/` are currently written for a MAUI/PAD-style project and are not appropriate for this repository's web-console goal.
- A Claude Design export exists under `docs/Design/`.
- The primary design file is `docs/Design/project/AgentCLI Web - A.html`.
- There is no production web app/package at the repo root yet.
- `docs/Design/` is currently untracked in git status, so PM should include working-tree context when planning this work.

## Created Setup

- `.doc/GOALS.md` defines the AgentCLI web console completion target.
- `.doc/Docs/` provides stable architecture, conventions, current-state, and design-target context.
- `.doc/DOCS_DIGEST.md` summarizes the setup documents for token-saving PM reads.
- `configs/AgentCLI-86741102.json` configures this repo for Codex-only AgentCLI runs.
- `prompts/AgentCLI-86741102/` contains project-specific prompt overrides.

## Important Risks

- The local machine used for this setup did not have `codex` or plain `python` on PATH during verification. AgentCLI runs will require Codex CLI installation/login, and validation should use `.venv/Scripts/python.exe`.
- The configured model names follow the requested intent (`pm_model=gpt-5.5`, Dev/QA/Reporter on `gpt-5.4-codex-mini`) but could not be validated locally because Codex CLI is unavailable.
- No web build system exists yet. The first Dev task should either create a dependency-free page or add a manifest and stop before installing dependencies.
- The design prototype uses React, Babel, and CDN scripts for export convenience. Production code should not copy that runtime shape directly.

## Immediate Next Work

1. Read `docs/Design/README.md` and `docs/Design/project/AgentCLI Web - A.html`.
2. Follow the imports listed by the primary design file.
3. Create a production web page surface.
4. Implement the desktop shell and core screens before adding backend control APIs.
5. Add a minimal documented validation command.
