# AgentCLI Web Authentication Plan

> Last verified: 2026-05-06. This is an implementation plan, not an active authentication layer.

AgentCLI Web is a local operator cockpit for one active repository. Until this plan is implemented, non-loopback binds must stay read-only/redacted and mutating routes must remain blocked by LAN safety.

## Current Boundary

- Loopback (`127.0.0.1`, `localhost`, `::1`) is the only supported surface for raw artifact opening, raw prompt reads, and mutating controls.
- Non-loopback binds are treated as LAN exposure. They must redact sensitive payloads and block raw artifact/prompt reads plus mutating actions.
- Confirmation phrases are UX friction only. They are not authentication and must not be used as an authorization boundary.
- `--trusted-network` documents operator intent for a private network; it does not turn on authenticated write access.

## Assets

- Repository source files and generated worktrees.
- `.AgentCLI` run artifacts, logs, reports, prompts, config backups, and audit files.
- Config secrets such as Telegram tokens and backend credentials exposed through local files or environment-derived config.
- Runner process control: start, stop, reload, restart, PR queue approval, worktree merge/discard, config/prompt/goals writes.

## Threats

- A LAN client reads raw logs, prompts, config, or artifacts that normal API redaction would hide.
- A LAN client triggers mutating web actions through direct HTTP calls or CSRF.
- A stale browser tab or duplicate web instance performs operations against the wrong repo/run.
- Tokens or session material are written into run artifacts, web snapshots, logs, or audit records.

## Target Design

1. **Local-only default remains unchanged.** Loopback may continue to use explicit operator confirmation phrases for dangerous actions.
2. **LAN requires an authenticated session.** A server-generated operator token must be required before any non-loopback raw read or mutation is allowed.
3. **Session tokens are ephemeral by default.** The token should be generated at web startup, printed only to the local terminal, and stored in process memory unless the operator explicitly opts into a local file.
4. **CSRF is blocked.** Mutating requests must require both an authenticated session and an `X-AgentCLI-CSRF` value bound to that session.
5. **Origin checks are enforced.** Non-loopback mutating requests must reject unexpected `Origin`/`Referer` hosts unless explicitly disabled for tests.
6. **Capabilities are explicit.** At minimum, separate read, raw-read, and mutate capabilities so future profiles can enable read-only authenticated LAN use without granting write access.
7. **Audit records stay redacted.** Authentication success/failure and authorization denial events must be recorded without raw tokens, prompts, logs, config values, or diffs.
8. **Duplicate-instance protection remains authoritative.** Authenticated sessions do not bypass repo web instance lock read-only states.

## MVP Acceptance Gates

- Non-loopback raw artifact reads and raw prompt reads return 403 without a valid session.
- Non-loopback mutating routes return 403 without a valid session and CSRF token.
- Authenticated LAN read-only mode can load status, history, logs metadata, and redacted summaries without raw payload leakage.
- Authenticated LAN mutation mode is opt-in and still requires action-specific confirmation phrases.
- Session tokens never appear in `/api/status`, `/api/health`, `WEB_ACTION_AUDIT.jsonl`, logs, config payloads, or run artifacts.
- Tests cover missing token, bad token, expired token, missing CSRF, bad CSRF, duplicate-instance read-only state, and loopback backward compatibility.

## Non-Goals

- Multi-user RBAC and shared team administration.
- Cloud-hosted AgentCLI Web.
- Persistent accounts or password reset flows.
- Using Telegram pairing codes or runner confirmation phrases as web authentication.

## Implementation Order

1. Add config schema fields for auth mode, session TTL, and capability defaults.
2. Add a small auth/session module with token generation, hashing, expiry, and CSRF binding.
3. Add FastAPI dependency helpers for raw-read and mutate route gates.
4. Wire raw prompt/artifact routes first, then config/prompt/goals/runner/worktree/PR queue mutation routes.
5. Surface auth state in `/api/health` and `/api/status` without exposing secrets.
6. Add docs and operator startup copy showing how to connect from a LAN device.
7. Add LAN/auth regression tests and keep unauthenticated LAN safety tests.
