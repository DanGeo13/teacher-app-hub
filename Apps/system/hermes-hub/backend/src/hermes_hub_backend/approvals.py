from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .audit import AuditService
from .database import Database
from .errors import ApprovalError, NotFoundError
from .models import ApprovalRecord
from .time import iso_now


def canonical_content_hash(content: Any) -> str:
    encoded = json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > 65_536:
        raise ApprovalError("approval content must be 64 KiB or less")
    return hashlib.sha256(encoded).hexdigest()


class ApprovalService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _record(row) -> ApprovalRecord:
        return ApprovalRecord.model_validate(dict(row))

    def create(self, action: str, target: str, content: Any, actor: str) -> ApprovalRecord:
        approval_id = str(uuid.uuid4())
        content_hash = canonical_content_hash(content)
        with self.database.write() as connection:
            connection.execute(
                """
                INSERT INTO approvals(
                    id, action, target, content_hash, status, requested_by, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (approval_id, action, target, content_hash, actor, iso_now()),
            )
            AuditService.record(
                connection,
                "approval.requested",
                actor,
                "approval",
                approval_id,
                {"action": action, "target": target, "contentHash": content_hash},
            )
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        return self._record(row)

    def approve(
        self,
        approval_id: str,
        action: str,
        target: str,
        content_hash: str,
        actor: str,
    ) -> ApprovalRecord:
        mismatch = False
        with self.database.write() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("approval not found")
            if row["status"] != "pending":
                raise ApprovalError(f"approval is already {row['status']}")
            mismatch = (
                row["action"] != action
                or row["target"] != target
                or row["content_hash"] != content_hash
            )
            if mismatch:
                self._invalidate(connection, approval_id, "approval target or content changed", actor)
            else:
                now = iso_now()
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = 'approved', approved_by = ?, approved_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (actor, now, approval_id),
                )
                AuditService.record(
                    connection,
                    "approval.approved",
                    actor,
                    "approval",
                    approval_id,
                    {"action": action, "target": target, "contentHash": content_hash},
                )
            updated = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        if mismatch:
            raise ApprovalError("approval target or content changed; approval invalidated")
        return self._record(updated)

    def consume(
        self, approval_id: str, action: str, target: str, content: Any, actor: str = "broker"
    ) -> ApprovalRecord:
        """Atomically consume one matching approval. Future write brokers must call this."""
        content_hash = canonical_content_hash(content)
        mismatch = False
        with self.database.write() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("approval not found")
            if row["status"] != "approved":
                raise ApprovalError(f"approval cannot be consumed from status {row['status']}")
            mismatch = (
                row["action"] != action
                or row["target"] != target
                or row["content_hash"] != content_hash
            )
            if mismatch:
                self._invalidate(connection, approval_id, "approved target or content changed", actor)
            else:
                now = iso_now()
                cursor = connection.execute(
                    """
                    UPDATE approvals SET status = 'consumed', consumed_at = ?
                    WHERE id = ? AND status = 'approved'
                    """,
                    (now, approval_id),
                )
                if cursor.rowcount != 1:
                    raise ApprovalError("approval was already consumed")
                AuditService.record(
                    connection,
                    "approval.consumed",
                    actor,
                    "approval",
                    approval_id,
                    {"action": action, "target": target, "contentHash": content_hash},
                )
            updated = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        if mismatch:
            raise ApprovalError("approved target or content changed; approval invalidated")
        return self._record(updated)

    def _invalidate(self, connection, approval_id: str, reason: str, actor: str) -> None:
        connection.execute(
            """
            UPDATE approvals
            SET status = 'invalidated', invalidated_at = ?, invalidation_reason = ?
            WHERE id = ? AND status IN ('pending', 'approved')
            """,
            (iso_now(), reason, approval_id),
        )
        AuditService.record(
            connection,
            "approval.invalidated",
            actor,
            "approval",
            approval_id,
            {"reason": reason},
        )

    def list(self) -> list[ApprovalRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        return [self._record(row) for row in rows]
