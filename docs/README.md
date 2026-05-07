# AgentCLI Documentation

This directory contains current operator guides, implementation plans, design references, proposals, and historical records. Do not treat every file under `docs/` as implemented product scope.

## Source Of Truth

| Need | Use |
|---|---|
| Current completion status | [../.doc/GOALS.md](../.doc/GOALS.md) |
| Current user/operator docs | This file plus [MASTER_INDEX.md](MASTER_INDEX.md) |
| Web console runtime contract | [WEB_CONSOLE.md](WEB_CONSOLE.md) |
| Full config reference | [CONFIG_REFERENCE_KO.md](CONFIG_REFERENCE_KO.md) |
| PM/Dev/QA persistent context | [../.doc/Docs](../.doc/Docs) and [../.doc/DOCS_DIGEST.md](../.doc/DOCS_DIGEST.md) |

## Read In This Order

### 1. Start And Configure

| Document | Purpose |
|---|---|
| [INSTALLATION.md](INSTALLATION.md) | Requirements, virtualenv setup, first run checks |
| [CONFIGURATION.md](CONFIGURATION.md) | Config files, backend selection, model settings |
| [CONFIG_REFERENCE_KO.md](CONFIG_REFERENCE_KO.md) | Complete Korean config reference |

### 2. Operate Runs

| Document | Purpose |
|---|---|
| [OPERATIONS.md](OPERATIONS.md) | Daily operation, Git safety, budgets, build/test gates, artifacts |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Stop reasons, failures, recovery procedures |
| [TELEGRAM.md](TELEGRAM.md) | Telegram control plane and notifications |

### 3. Understand And Extend

| Document | Purpose |
|---|---|
| [PIPELINE.md](PIPELINE.md) | PM/Dev/QA/Security/PL pipeline and role behavior |
| [ADVANCED_FEATURES.md](ADVANCED_FEATURES.md) | TODO, GOALS, task history, shutdown reports, advanced workflows |
| [CUSTOMIZATION.md](CUSTOMIZATION.md) | Prompt overrides and Skills behavior |
| [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | Backend/stage extension, metrics, process safety |

### 4. Web Console

| Document | Purpose |
|---|---|
| [WEB_CONSOLE.md](WEB_CONSOLE.md) | Current web console implementation and validation contract |
| [AUTHENTICATION_PLAN.md](AUTHENTICATION_PLAN.md) | Future authentication plan; not an active auth layer |
| [Design/README.md](Design/README.md) | Read-only Direction A design handoff |

## Non-Current Material

| Area | Meaning |
|---|---|
| [proposals/](proposals/) | Future design and gap maps. Some items may already be implemented through GOALS; do not use as active backlog without revalidation. |
| [archive/](archive/) | Historical records, snapshots, old task lists, and incident writeups. These are not current implementation status. |
| [Design/project/](Design/project/) | Read-only exported prototype assets. Production code lives in `web_console/`, not here. |

## Agent Context

The `.doc/Docs` directory is the PM/Dev/QA context set used by AgentCLI runs. The compact digest at [../.doc/DOCS_DIGEST.md](../.doc/DOCS_DIGEST.md) is generated from that inventory.

Key current context files:

| Document | Purpose |
|---|---|
| [../.doc/Docs/ARCHITECTURE.md](../.doc/Docs/ARCHITECTURE.md) | Product architecture and web-console integration state |
| [../.doc/Docs/CONVENTIONS.md](../.doc/Docs/CONVENTIONS.md) | Implementation and validation conventions |
| [../.doc/Docs/WEB_CONSOLE_TARGET.md](../.doc/Docs/WEB_CONSOLE_TARGET.md) | Direction A visual/workflow target and current implementation baseline |
| [../.doc/Docs/DOC_PROJECT_CONSISTENCY_AUDIT_20260507.md](../.doc/Docs/DOC_PROJECT_CONSISTENCY_AUDIT_20260507.md) | 2026-05-07 doc/project consistency audit |

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m unittest tests.test_docs_validation
$env:PYTHONPYCACHEPREFIX = ".test-scratch\pycache-validation"
.\.venv\Scripts\python.exe -B -m compileall -q agent_runner tests
```

Run Playwright proof for web-facing claims when browser/runtime support is available:

```powershell
.\.venv\Scripts\python.exe -B .\tests\web_console_playwright_smoke.py -v
```
