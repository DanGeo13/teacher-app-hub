import sqlite3
from pathlib import Path

from hermes_hub_backend.backup import BackupService


def test_missing_hermes_and_ollama_are_explicitly_unavailable(authenticated):
    client, _headers = authenticated
    response = client.get("/api/runtime")
    assert response.status_code == 200
    payload = response.json()
    assert payload["hermes"]["status"] == "unavailable"
    assert payload["qwen"]["status"] == "unavailable"
    assert payload["hermes"]["capabilities"]["tools"]["state"] == "not_run"
    assert payload["qwen"]["capabilities"]["vision"]["state"] == "not_run"


def test_consistent_snapshot_and_restore(authenticated, teaching_app, settings, tmp_path: Path):
    client, headers = authenticated
    created = client.post("/api/apps", headers=headers, json=teaching_app)
    assert created.status_code == 201

    snapshot_response = client.post("/api/backups/local", headers=headers)
    assert snapshot_response.status_code == 201
    snapshot = settings.backup_dir / snapshot_response.json()["filename"]
    assert snapshot.exists()

    restored = tmp_path / "restored.db"
    BackupService.restore_to_new_database(snapshot, restored)
    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT title FROM apps").fetchone()[0] == teaching_app["title"]
        assert connection.execute("SELECT COUNT(*) FROM backup_records").fetchone()[0] == 0
    finally:
        connection.close()


def test_backend_health_and_migrations(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == {"schemaVersion": 2, "integrity": "ok"}
