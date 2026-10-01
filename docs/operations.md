# Operations

## Start and stop

Use `scripts/run-local.sh` for loopback development. Stop with `Ctrl+C`. Starting the process is idempotent: migrations are applied once and the server reopens the same `HUB_DATA_DIR/hub.db`. The script disables Uvicorn proxy-header processing.

## Health

- `GET /api/health` is unauthenticated and exposes only Hub version, schema version and SQLite integrity.
- `GET /api/runtime` is authenticated and runs bounded server-side probes.
- A 401/403 from Hermes is reported as `authentication_required`; authentication is not disabled.
- Capabilities remain `not_run` until a real operation has passed.

## Sessions and browser recovery

A missing, expired or invalid session returns HTTP 401 for authenticated API reads. The browser clears its in-memory CSRF token and returns to sign-in rather than continuing to render an authenticated shell. A failed logout remains visibly signed in and reports the failure; it never claims logout succeeded.

## Registry conflicts

Edits and reorders carry expected revisions. HTTP 409 means another write won. Reload the current record and review before retrying. The API never silently applies a stale edit. SQLite writes are serialised and concurrent stale updates are expected to produce one success and one conflict.

## Backups

Local snapshots are consistent SQLite copies in `HUB_DATA_DIR/backups`, with a 0700 directory and 0600 database/manifest files. Temporary files are removed on failure, and only the ten newest Hub snapshots are retained. This is workspace recovery evidence, not an external backup or disaster-recovery guarantee. Restore validation writes to a new destination and never overwrites the live database.

## Costs and availability

The Hub and local inference do not inherently create model API charges. A running Codespace consumes compute allowance; a stopped Codespace retains storage but serves no backend, agent or model. The PWA shell can explain disconnection, but cannot run Hermes or Qwen offline. No keep-alive mechanism is included.

## Disabled operations

Git commits/pushes, merges, clasp, deployments, external backups and live restore have no execution implementation. `/api/restricted-actions/*` returns `BROKER_NOT_IMPLEMENTED` even if an approval exists. Approval expiry and migration checksum enforcement are not implemented in this scope.
