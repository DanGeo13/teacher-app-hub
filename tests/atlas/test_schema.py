"""Database best practices (adapted from supabase/agent-skills'
supabase-postgres-best-practices to SQLite): indexed foreign keys, indexed
hot queries, and constraints that reject invalid rows at the database."""

from __future__ import annotations

import sqlite3

import pytest

HOT_QUERIES = {
    "approvals for a record": "SELECT id FROM approvals WHERE target = 'x' ORDER BY created_at DESC",
    "evidence for a record": "SELECT * FROM atlas_evidence WHERE attached_record_id = 'x' ORDER BY created_at",
    "records by counterparty": "SELECT * FROM atlas_records WHERE counterparty_key = 'x'",
    "verification ack poll": "SELECT 1 FROM audit_events WHERE event_type = 'a' AND target_id = 'x'",
    "suspicious content count": "SELECT count(*) FROM audit_events WHERE event_type = 'a' AND created_at >= 'x'",
    "audio queue": "SELECT * FROM atlas_audio_items WHERE status = 'queued' ORDER BY received_at",
    "approval cards": "SELECT dedupe_key FROM atlas_alerts WHERE kind = 'approval_card'",
    "pending approvals": "SELECT * FROM approvals WHERE status = 'pending'",
}


def test_every_foreign_key_is_indexed(database):
    connection = database.connect()
    missing = []
    tables = [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    for table in tables:
        leading = set()
        for index in connection.execute(f"PRAGMA index_list('{table}')"):
            columns = [r[2] for r in connection.execute(f"PRAGMA index_info('{index[1]}')")]
            if columns:
                leading.add(columns[0])
        missing += [(table, fk[3]) for fk in connection.execute(f"PRAGMA foreign_key_list('{table}')")
                    if fk[3] not in leading]
    assert missing == []


@pytest.mark.parametrize("name", sorted(HOT_QUERIES))
def test_hot_queries_use_an_index(database, name):
    plan = " ".join(r[3] for r in database.connect().execute("EXPLAIN QUERY PLAN " + HOT_QUERIES[name]))
    assert "USING" in plan and "INDEX" in plan, plan


@pytest.mark.parametrize("column, value", [
    ("status", "'paid_maybe'"),
    ("amount_cents", "-1"),
    ("currency", "'USD'"),
    ("due_date", "'20/10/2026'"),
    ("source_type", "'carrier_pigeon'"),
    ("current_odometer_km", "-5"),
])
def test_records_table_rejects_invalid_values(database, column, value):
    values = {"id": "'BIL-1'", "kind": "'bill'", "status": "'due'", "review_state": "'proposed'", "title": "'t'",
              "counterparty": "'c'", "counterparty_key": "'c'", "source_account": "'a'", "source_type": "'email'",
              "idempotency_key": "'k'", "created_at": "'2026-10-12T00:00:00Z'", "updated_at": "'2026-10-12T00:00:00Z'"}
    values[column] = value
    sql = f"INSERT INTO atlas_records({', '.join(values)}) VALUES ({', '.join(values.values())})"
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(sql)


def test_evidence_table_rejects_unknown_action(database):
    with pytest.raises(sqlite3.IntegrityError):
        database.connect().execute(
            "INSERT INTO atlas_evidence(id, source_account, source_type, external_ref, action, created_at) "
            "VALUES ('E', 'a', 'email', 'r', 'mark_everything_paid', '2026-10-12T00:00:00Z')"
        )
