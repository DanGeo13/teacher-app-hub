-- Atlas operations agent: approval expiry/rejection/execution plus the
-- record, evidence, check, alert and audio tables. The approvals table is
-- rebuilt because SQLite cannot widen a CHECK constraint in place; every
-- existing row and column is copied unchanged.
CREATE TABLE approvals_v4 (
    id TEXT PRIMARY KEY,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'approved', 'consumed', 'invalidated', 'rejected', 'expired')
    ),
    requested_by TEXT NOT NULL,
    approved_by TEXT,
    created_at TEXT NOT NULL,
    approved_at TEXT,
    consumed_at TEXT,
    invalidated_at TEXT,
    invalidation_reason TEXT,
    expires_at TEXT,
    payload_json TEXT,
    summary TEXT,
    requester_channel TEXT,
    decided_channel TEXT,
    rejected_by TEXT,
    rejected_at TEXT,
    expired_at TEXT,
    execution_status TEXT CHECK (execution_status IN ('succeeded', 'failed')),
    execution_result_json TEXT,
    executed_at TEXT
);

INSERT INTO approvals_v4 (
    id, action, target, content_hash, status, requested_by, approved_by, created_at,
    approved_at, consumed_at, invalidated_at, invalidation_reason
)
SELECT
    id, action, target, content_hash, status, requested_by, approved_by, created_at,
    approved_at, consumed_at, invalidated_at, invalidation_reason
FROM approvals;

DROP TABLE approvals;
ALTER TABLE approvals_v4 RENAME TO approvals;

CREATE INDEX IF NOT EXISTS idx_approvals_status_created
    ON approvals (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_approvals_expires
    ON approvals (status, expires_at);

CREATE TABLE IF NOT EXISTS atlas_records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('bill', 'car_service', 'commitment', 'task')),
    status TEXT NOT NULL CHECK (status IN (
        'due', 'payment_reported', 'confirmed_paid', 'booking_requested', 'booked', 'completed',
        'open', 'draft_ready', 'awaiting_approval', 'sent', 'done'
    )),
    review_state TEXT NOT NULL CHECK (review_state IN ('proposed', 'active', 'archived')),
    title TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    counterparty_key TEXT NOT NULL,
    amount_cents INTEGER CHECK (amount_cents IS NULL OR amount_cents >= 0),
    currency TEXT NOT NULL DEFAULT 'AUD' CHECK (currency = 'AUD'),
    due_date TEXT CHECK (due_date IS NULL OR date(due_date) IS due_date),
    due_odometer_km INTEGER CHECK (due_odometer_km IS NULL OR due_odometer_km >= 0),
    current_odometer_km INTEGER CHECK (current_odometer_km IS NULL OR current_odometer_km >= 0),
    reference TEXT,
    source_account TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN (
        'email', 'notification', 'sms', 'transcript', 'manual', 'calendar', 'invoice', 'statement', 'other'
    )),
    details_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT NOT NULL,
    suspicious INTEGER NOT NULL DEFAULT 0 CHECK (suspicious IN (0, 1)),
    revision INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (kind, source_account, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_atlas_records_kind_status
    ON atlas_records (kind, status, due_date);
CREATE INDEX IF NOT EXISTS idx_atlas_records_counterparty
    ON atlas_records (counterparty_key, kind);

CREATE TABLE IF NOT EXISTS atlas_record_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL REFERENCES atlas_records(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    actor TEXT NOT NULL,
    via TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_atlas_history_record
    ON atlas_record_history (record_id, id);

CREATE TABLE IF NOT EXISTS atlas_evidence (
    id TEXT PRIMARY KEY,
    source_account TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN (
        'email', 'notification', 'sms', 'transcript', 'manual', 'calendar', 'invoice', 'statement', 'other'
    )),
    external_ref TEXT NOT NULL,
    counterparty TEXT,
    counterparty_key TEXT,
    action TEXT NOT NULL CHECK (action IN (
        'payment_reported', 'payment_confirmed', 'booking_requested', 'booking_confirmed',
        'service_completed', 'message_sent', 'task_done', 'acknowledgement', 'other'
    )),
    amount_cents INTEGER CHECK (amount_cents IS NULL OR amount_cents >= 0),
    occurred_on TEXT CHECK (occurred_on IS NULL OR date(occurred_on) IS occurred_on),
    reference TEXT,
    summary TEXT NOT NULL DEFAULT '',
    link TEXT,
    suspicious INTEGER NOT NULL DEFAULT 0 CHECK (suspicious IN (0, 1)),
    attached_record_id TEXT REFERENCES atlas_records(id),
    created_at TEXT NOT NULL,
    UNIQUE (source_account, external_ref)
);

-- Foreign-key and lookup indexes (get_record, matching, review list).
CREATE INDEX IF NOT EXISTS idx_atlas_evidence_attached
    ON atlas_evidence (attached_record_id, created_at);

CREATE TABLE IF NOT EXISTS atlas_review_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT REFERENCES atlas_records(id),
    evidence_id TEXT REFERENCES atlas_evidence(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_atlas_review_record ON atlas_review_items (record_id);
CREATE INDEX IF NOT EXISTS idx_atlas_review_evidence ON atlas_review_items (evidence_id);
CREATE INDEX IF NOT EXISTS idx_atlas_review_open ON atlas_review_items (id) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS atlas_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_name TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('ok', 'no_change', 'failed', 'stale')),
    detail TEXT NOT NULL DEFAULT '',
    source_account TEXT,
    catch_up INTEGER NOT NULL DEFAULT 0 CHECK (catch_up IN (0, 1)),
    gap_minutes INTEGER,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_atlas_checks_name
    ON atlas_checks (check_name, id DESC);

CREATE TABLE IF NOT EXISTS atlas_alerts (
    dedupe_key TEXT PRIMARY KEY,
    record_id TEXT,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    first_raised_at TEXT NOT NULL,
    last_delivered_at TEXT,
    delivered_count INTEGER NOT NULL DEFAULT 0 CHECK (delivered_count >= 0)
);

CREATE INDEX IF NOT EXISTS idx_atlas_alerts_kind ON atlas_alerts (kind);

CREATE TABLE IF NOT EXISTS atlas_audio_items (
    id TEXT PRIMARY KEY,
    source_device TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'processed', 'failed')),
    chunks_json TEXT NOT NULL,
    received_at TEXT NOT NULL,
    processed_at TEXT,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_atlas_audio_status ON atlas_audio_items (status, received_at);

-- Approvals are looked up by target (a record's approvals) and audit events
-- by type and target (verification acknowledgement polling, suspicious
-- content counts); both tables grow without bound.
CREATE INDEX IF NOT EXISTS idx_approvals_target ON approvals (target, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_type_target ON audit_events (event_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_type_created ON audit_events (event_type, created_at);
