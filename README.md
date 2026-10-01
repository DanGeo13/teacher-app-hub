# teacher-app-hub

Hermes Hub is an authenticated browser control panel for persistent teaching and personal application registries. Milestone 0.1 provides a durable disconnected foundation: it reports unavailable runtimes honestly and does not simulate Hermes or model behaviour.

## Current milestone

- React/TypeScript responsive frontend and conservative PWA shell.
- FastAPI/Pydantic backend with server-side sessions and CSRF protection.
- SQLite migrations, revision-safe registry writes, audit events and local snapshots.
- Teacher Hub and Lifestyle Hub add/edit/reorder/archive workflows.
- Server-side Hermes and Ollama availability probes with unverified capabilities kept separate.
- Minimal authentic Hermes sessions: one user message per request over the installed Hermes executable (ACP v1 stdio), streamed SSE progress, persisted session references with no message content — see [docs/hermes-sessions.md](docs/hermes-sessions.md).
- Approval records bound to action, target and content hash; external writes fail closed.
- Hardening coverage for exact Origins, expired sessions, safe snapshots and a four-scenario Chromium browser suite.

The hardening validation remains review-only: it does not enable external-write brokers, approval expiry or migration checksums, and it does not rebuild or alter an existing Codespace automatically.

Start with [docs/setup.md](docs/setup.md). Read [docs/evidence.md](docs/evidence.md) and [docs/known-limitations.md](docs/known-limitations.md) before enabling any integration.

No repository commit, GitHub push, Apps Script write, model download or public-port configuration is performed by the application.