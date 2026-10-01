# Hermes Hub architecture

## Milestone 0.1 boundary

Hermes Hub is a same-origin React/FastAPI application. The foundation implements an authenticated, persistent application registry, honest runtime probes, local SQLite snapshots and an enforced fail-closed approval boundary. It does not simulate Hermes sessions or model responses.

```text
Browser/PWA
   | same-origin cookie + CSRF
   v
FastAPI Hub (default 9120)
   |-- registry / audit / approvals ---> Hub SQLite (HUB_DATA_DIR)
   |-- local snapshot -----------------> HUB_DATA_DIR/backups
   |-- Hermes adapter --read only------> official dashboard (default 9119)
   `-- Ollama adapter --read only------> Ollama loopback (default 11434)

Official Hermes dashboard remains separate. Hermes-owned state is not opened by the Hub.
```

## Trust boundaries

- Browser input is untrusted and validated with Pydantic.
- App launch URLs are display/launch data only and never become backend fetch targets.
- Runtime endpoints come only from server environment configuration and default to loopback.
- Session tokens are random, stored only as a digest server-side and delivered in an HttpOnly cookie.
- CSRF tokens are session-bound and required with a matching Origin for mutations.
- The approval service atomically consumes one matching action/target/content hash.
- No external-write broker exists; every restricted-action endpoint fails closed.
- The service worker never handles `/api/` through Cache Storage.

## Persistence

Versioned source/configuration remains in Git. Runtime state remains under `HUB_DATA_DIR`. In a Codespace the supplied configuration uses `/workspaces/.hermes-hub`; in this sandbox the operator must explicitly choose another writable directory outside the repository. These locations are not equivalent: this host has no Codespaces persistence guarantee.

SQLite uses WAL on a same-host filesystem, numbered migrations and serialised `BEGIN IMMEDIATE` writes. App edits include an expected revision. A stale edit or reorder returns HTTP 409 rather than overwriting newer state.

## Future components

The persistent orchestrator, authentic Hermes session adapter, specialist agents, constrained Git/clasp broker, GAS message adapter and app inference gateway are deliberately absent. Their schemas are versioned so future work has a validated boundary, but no UI control claims those operations currently work.
