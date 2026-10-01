CREATE TABLE IF NOT EXISTS apps (
    id TEXT PRIMARY KEY,
    hub TEXT NOT NULL CHECK (hub IN ('teaching', 'personal')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    icon TEXT NOT NULL DEFAULT 'app',
    project_path TEXT,
    launch_url TEXT NOT NULL,
    development_url TEXT,
    integration_mode TEXT NOT NULL CHECK (
        integration_mode IN ('external_link', 'embedded_legacy', 'hub_aware')
    ),
    authentication_requirements TEXT NOT NULL DEFAULT '',
    theme_adapter_version TEXT,
    ai_adapter_support INTEGER NOT NULL DEFAULT 0 CHECK (ai_adapter_support IN (0, 1)),
    health_status TEXT NOT NULL DEFAULT 'unknown' CHECK (
        health_status IN ('unknown', 'healthy', 'degraded', 'unavailable')
    ),
    display_order INTEGER NOT NULL,
    script_id TEXT,
    deployment_id TEXT,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_apps_hub_order
    ON apps (hub, archived, display_order, title);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_created_at ON audit_events (created_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at);
