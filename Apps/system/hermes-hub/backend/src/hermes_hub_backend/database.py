from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class Database:
    """Small SQLite boundary with explicit migrations and serialised writes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._write_lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path.parent.chmod(0o700)
        self._migrate()
        self.path.chmod(0o600)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=5,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            connection.close()
            raise RuntimeError("SQLite WAL mode could not be enabled on the configured filesystem")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _migrate(self) -> None:
        migrations_dir = Path(__file__).with_name("migrations")
        migrations = sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))
        if not migrations:
            raise RuntimeError(f"No database migrations found in {migrations_dir}")

        with self._write_lock:
            connection = self.connect()
            try:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS schema_migrations (
                        version INTEGER PRIMARY KEY,
                        name TEXT NOT NULL,
                        applied_at TEXT NOT NULL
                    )
                    """
                )
                applied = {
                    int(row[0])
                    for row in connection.execute("SELECT version FROM schema_migrations")
                }
                for migration in migrations:
                    version = int(migration.name.split("_", 1)[0])
                    if version in applied:
                        continue
                    sql = migration.read_text(encoding="utf-8")
                    escaped_name = migration.name.replace("'", "''")
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + sql
                        + "\nINSERT INTO schema_migrations(version, name, applied_at) "
                        + f"VALUES ({version}, '{escaped_name}', strftime('%Y-%m-%dT%H:%M:%fZ','now'));\n"
                        + "COMMIT;"
                    )
            finally:
                connection.close()

    def schema_version(self) -> int:
        with self.read() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations"
            ).fetchone()
            return int(row["version"])

    def integrity_check(self) -> str:
        with self.read() as connection:
            return str(connection.execute("PRAGMA quick_check").fetchone()[0])
