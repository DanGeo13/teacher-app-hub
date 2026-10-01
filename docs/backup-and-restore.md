# Backup and restore

## Implemented: local consistent snapshot

The authenticated Maintenance page calls `POST /api/backups/local`. The backend uses SQLite's online backup API, verifies `PRAGMA integrity_check`, hashes the snapshot and writes a manifest under `HUB_DATA_DIR/backups`.

This is a **local snapshot**, not an off-workspace backup. It will not survive deletion of the machine/Codespace if the directory is deleted with it.

## Restore safety

The tested restore helper restores into a new, non-existent database and verifies integrity. It refuses to overwrite a destination. Live in-place restore is disabled because it is destructive and needs a scoped approval, stopped writers, backup selection and rollback procedure.

## Not yet implemented

- Encryption and key custody.
- User-controlled Drive/removable-storage destination.
- Retention scheduling.
- Fresh-Codespace restore.
- Automatic runtime restart.

After any future restore, interrupted external-write stages must enter `needs-review`; approvals and deployments must not replay automatically.
