# Doc/Project Consistency Audit - 2026-05-07

## Purpose

This audit reconciles the documents under `docs/` and `.doc/Docs/` with the current project state. The main risk is not broken links; it is PM/Dev/QA treating historical gap notes as active backlog after the corresponding GOALS items have already been promoted or implemented.

## Task Split

| Task | Scope | Check Method | Result |
|---|---|---|---|
| T1. Inventory and validators | `docs/`, `.doc/Docs/`, `.doc/DOCS_DIGEST.md`, `docs/MASTER_INDEX.md` | Run docs validation and markdown link inventory | Automated docs validation passes; no missing markdown links found |
| T2. Canonical user docs | `docs/WEB_CONSOLE.md`, `CONFIG_REFERENCE_KO.md`, `OPERATIONS.md`, `ADVANCED_FEATURES.md` | Compare route/config/TODO claims against code and tests | Current user docs are the source of truth for web console and TODO behavior |
| T3. Agent context docs | `.doc/Docs/ARCHITECTURE.md`, `WEB_CONSOLE_TARGET.md`, `CONVENTIONS.md` | Compare PM/Dev/QA context against current implementation paths and validation commands | These docs needed updates because they still described pre-implementation web-console targets |
| T4. Historical gap notes | 2026-04-28 through 2026-04-30 audit/follow-up docs | Check whether unchecked candidate lists have been superseded by `.doc/GOALS.md` and current tests | Historical bodies are preserved, but top notes now prevent stale backlog interpretation |
| T5. Digest/index output | `.doc/DOCS_DIGEST.md`, `docs/MASTER_INDEX.md` | Regenerate digest and update index status | New audit document is indexed for PM/Dev/QA context |

## Findings

1. Link and docs validators did not find structural breakage. The consistency issue is semantic drift in active context documents.
2. `.doc/Docs/ARCHITECTURE.md` and `.doc/Docs/WEB_CONSOLE_TARGET.md` still described the web console as a future/static/mock-backed target. The current project has a production web surface under `web_console/`, served by `agent_runner.web`, with guarded local mutation endpoints and route/test coverage.
3. Several 2026-04-28 to 2026-04-30 audit notes contain unchecked candidate bullets for TODO, Claude advanced controls, MCP, plugin loading, enterprise profile checks, Command Palette, Instance Health, and unattended-operation follow-ups. Those notes are valuable historical evidence, but `.doc/GOALS.md` and current tests are the active implementation source of truth.
4. The memory/handle leak incident remains an incident record, not a closed bug report. Related process-guard, subprocess cleanup, diagnostics, and logger-hardening work was later implemented through GOALS follow-ups, but the original incident should be re-audited before changing its final status to closed.

## Corrections Applied

- Updated `.doc/Docs/ARCHITECTURE.md` to describe the current `web_console/` + `agent_runner.web` implementation and current data/API boundaries.
- Updated `.doc/Docs/WEB_CONSOLE_TARGET.md` with a current implementation baseline while preserving Direction A as the visual and workflow reference.
- Updated `.doc/Docs/CONVENTIONS.md` so Python validation uses the repo `.venv` and `PYTHONPYCACHEPREFIX=.test-scratch\pycache-validation`.
- Added current-status notes to historical gap/audit documents so old unchecked lists are not treated as new PM backlog without revalidation.
- Updated `docs/MASTER_INDEX.md` and regenerated `.doc/DOCS_DIGEST.md`.

## Remaining Guidance

- Use `.doc/GOALS.md`, `docs/WEB_CONSOLE.md`, and the checked-in tests as the current source of truth for implementation status.
- Use 2026-04-28 through 2026-04-30 audit documents as historical evidence and regression context unless their top status note says otherwise.
- Before closing `.doc/Docs/incidents/MEMORY_AND_HANDLE_LEAK_20260428.md`, run a focused Windows handle/process re-audit and confirm no remaining reproducible leak path.

## Validation Commands

Run these from the repository root:

```powershell
$env:PYTHONPYCACHEPREFIX = ".test-scratch\pycache-validation"
.\.venv\Scripts\python.exe -B -m compileall -q agent_runner tests
.\.venv\Scripts\python.exe -B -m unittest tests.test_docs_validation
.\.venv\Scripts\python.exe -B .\tests\web_console_playwright_smoke.py -v
```

The Playwright command is browser-render proof only when it runs tests instead of skipping because of local browser/runtime constraints.
