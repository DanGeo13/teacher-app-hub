-- Minimal Hermes session references. No message content, tokens or secrets are
-- stored: only the identifiers, negotiated protocol metadata and turn outcome
-- needed to reference and diagnose a session that lives in Hermes' own state.
CREATE TABLE IF NOT EXISTS hermes_sessions (
    id TEXT PRIMARY KEY,
    hermes_session_id TEXT,
    status TEXT NOT NULL CHECK (status IN ('connecting', 'active', 'failed')),
    transport TEXT NOT NULL,
    protocol_version INTEGER,
    agent_name TEXT,
    agent_version TEXT,
    model TEXT,
    provider TEXT,
    executable TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_stop_reason TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_hermes_sessions_created
    ON hermes_sessions (created_at DESC);
