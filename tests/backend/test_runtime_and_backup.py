import sqlite3
from pathlib import Path

from hermes_hub_backend.backup import BackupService
from hermes_hub_backend.database import Database


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
    manifest = snapshot.with_suffix(".manifest.json")
    assert snapshot.exists() and manifest.exists()
    assert settings.backup_dir.stat().st_mode & 0o777 == 0o700
    assert snapshot.stat().st_mode & 0o777 == 0o600
    assert manifest.stat().st_mode & 0o777 == 0o600

    restored = tmp_path / "restored.db"
    BackupService.restore_to_new_database(snapshot, restored)
    connection = sqlite3.connect(restored)
    try:
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("SELECT title FROM apps").fetchone()[0] == teaching_app["title"]
        assert connection.execute("SELECT COUNT(*) FROM backup_records").fetchone()[0] == 0
    finally:
        connection.close()
    assert restored.stat().st_mode & 0o777 == 0o600


def test_backup_cleanup_retains_only_recent_snapshots(settings):
    service = BackupService(Database(settings.database_path), settings.backup_dir)
    for _ in range(service.RETAINED_SNAPSHOTS + 2):
        service.create_snapshot()
    snapshots = list(settings.backup_dir.glob("hub-*.db"))
    manifests = list(settings.backup_dir.glob("hub-*.manifest.json"))
    assert len(snapshots) == service.RETAINED_SNAPSHOTS
    assert len(manifests) == service.RETAINED_SNAPSHOTS
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in [*snapshots, *manifests])


def test_backend_health_and_migrations(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["database"] == {"schemaVersion": 2, "integrity": "ok"}
