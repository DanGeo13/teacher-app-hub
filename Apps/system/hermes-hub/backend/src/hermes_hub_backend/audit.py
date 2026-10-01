from __future__ import annotations

import json
from typing import Any

from .database import Database
from .time import iso_now


class AuditService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def record(
        connection,
        event_type: str,
        actor: str,
        target_type: str,
        target_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events(
                event_type, actor, target_type, target_id, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                actor,
                target_type,
                target_id,
                json.dumps(details or {}, sort_keys=True, separators=(",", ":")),
                iso_now(),
            ),
        )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (min(limit, 200),)
            ).fetchall()
        return [
            {
                "id": row["id"],
                "eventType": row["event_type"],
                "actor": row["actor"],
                "targetType": row["target_type"],
                "targetId": row["target_id"],
                "details": json.loads(row["details_json"]),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]
