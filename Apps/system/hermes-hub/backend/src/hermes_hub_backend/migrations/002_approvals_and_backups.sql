CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'approved', 'consumed', 'invalidated', 'rejected')
    ),
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    consumed_at TEXT,
    invalidated_at TEXT,
    invalidation_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_approvals_status_created
    ON approvals (status, created_at DESC);

CREATE TABLE IF NOT EXISTS backup_records (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL UNIQUE,
    sha256 TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('created', 'verified', 'failed'))
);
