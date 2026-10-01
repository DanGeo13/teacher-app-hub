from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

from .audit import AuditService
from .database import Database
from .models import BackupRecord
from .time import iso_now


class BackupService:
    """Creates local consistent snapshots. This is not an external/disaster backup."""

    def __init__(self, database: Database, backup_dir: Path) -> None:
        self.database = database
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def create_snapshot(self) -> BackupRecord:
        backup_id = str(uuid.uuid4())
        timestamp = iso_now().replace(":", "").replace("-", "")
        filename = f"hub-{timestamp}-{backup_id[:8]}.db"
        destination = self.backup_dir / filename

        source = self.database.connect()
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"snapshot integrity check failed: {check}")
        finally:
            target.close()
            source.close()

        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        record = BackupRecord(
            id=backup_id,
            filename=filename,
            sha256=digest,
            schema_version=self.database.schema_version(),
            size_bytes=destination.stat().st_size,
            created_at=iso_now(),
            status="verified",
        )
        manifest = destination.with_suffix(".manifest.json")
        manifest.write_text(
            json.dumps(record.model_dump(mode="json", by_alias=True), indent=2) + "\n",
            encoding="utf-8",
        )
        manifest.chmod(0o600)
        destination.chmod(0o600)

        with self.database.write() as connection:
            connection.execute(
                """
                INSERT INTO backup_records(
                    id, filename, sha256, schema_version, size_bytes, created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.filename,
                    record.sha256,
                    record.schema_version,
                    record.size_bytes,
                    record.created_at.isoformat(),
                    record.status,
                ),
            )
            AuditService.record(
                connection,
                "backup.local_snapshot_created",
                "admin",
                "backup",
                backup_id,
                {"filename": filename, "sha256": digest},
            )
        return record

    @staticmethod
    def restore_to_new_database(snapshot: Path, destination: Path) -> None:
        """Restore into a new path for verification; never overwrites a live database."""
        if destination.exists():
            raise FileExistsError("restore destination already exists")
        source = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
        target = sqlite3.connect(destination)
        try:
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"restored database integrity check failed: {check}")
        finally:
            target.close()
            source.close()
