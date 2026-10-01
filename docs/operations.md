# Operations

## Start and stop

Use `scripts/run-local.sh` for loopback development. Stop with `Ctrl+C`. Starting the process is idempotent: migrations are applied once and the server reopens the same `HUB_DATA_DIR/hub.db`.

## Health

- `GET /api/health` is unauthenticated and exposes only Hub version, schema version and SQLite integrity.
- `GET /api/runtime` is authenticated and runs bounded server-side probes.
- A 401/403 from Hermes is reported as `authentication_required`; authentication is not disabled.
- Capabilities remain `not_run` until a real operation has passed.

## Registry conflicts

Edits and reorders carry expected revisions. HTTP 409 means another write won. Reload the current record and review before retrying. The API never silently applies a stale edit.

## Costs and availability

The Hub and local inference do not inherently create model API charges. A running Codespace consumes compute allowance; a stopped Codespace retains storage but serves no backend, agent or model. The PWA shell can explain disconnection, but cannot run Hermes or Qwen offline. No keep-alive mechanism is included.

## Disabled operations

Git commits/pushes, merges, clasp, deployments, external backups and live restore have no execution implementation. `/api/restricted-actions/*` returns `BROKER_NOT_IMPLEMENTED` even if an approval exists.
