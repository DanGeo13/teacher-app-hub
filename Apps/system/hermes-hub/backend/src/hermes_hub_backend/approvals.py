from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from .audit import AuditService
from .database import Database
from .errors import ApprovalError, NotFoundError
from .models import ApprovalRecord
from .time import utc_now

#: Channels on which a human can decide an approval. The agent's own channel
#: ("agent") is deliberately absent: nothing the model calls can approve.
HUMAN_CHANNELS = frozenset({"dashboard", "companion", "telegram"})
#: Actors that may never decide an approval, whatever channel they claim.
AGENT_ACTORS = frozenset({"atlas-agent", "agent", "broker"})
DEFAULT_EXPIRY = timedelta(hours=24)
MAX_EXPIRY = timedelta(days=7)


class ApprovalExpiredError(ApprovalError):
    pass


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


def _canonical_json(content: Any) -> str:
    return json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class ApprovalService:
    """The single approval engine used by the dashboard, companion app,
    Telegram buttons and the atlas-hub executor."""

    def __init__(self, database: Database, clock=utc_now) -> None:
        self.database = database
        self.clock = clock

    def _now_iso(self) -> str:
        return _iso(self.clock())

    @staticmethod
    def _record(row) -> ApprovalRecord:
        return ApprovalRecord.model_validate(dict(row))

    def create(
        self,
        action: str,
        target: str,
        content: Any,
        actor: str,
        *,
        expires_in: timedelta = DEFAULT_EXPIRY,
        store_payload: bool = False,
        summary: str | None = None,
        requester_channel: str | None = None,
        connection=None,
    ) -> ApprovalRecord:
        if expires_in <= timedelta(0) or expires_in > MAX_EXPIRY:
            raise ApprovalError("approval expiry must be between 0 and 7 days")
        approval_id = str(uuid.uuid4())
        content_hash = canonical_content_hash(content)
        now = self.clock()
        expires_at = _iso(now + expires_in)

        def insert(conn) -> ApprovalRecord:
            conn.execute(
                """
                INSERT INTO approvals(
                    id, action, target, content_hash, status, requested_by, created_at,
                    expires_at, payload_json, summary, requester_channel
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
                """,
                (
                    approval_id,
                    action,
                    target,
                    content_hash,
                    actor,
                    _iso(now),
                    expires_at,
                    _canonical_json(content) if store_payload else None,
                    summary,
                    requester_channel,
                ),
            )
            AuditService.record(
                conn,
                "approval.requested",
                actor,
                "approval",
                approval_id,
                {
                    "action": action,
                    "target": target,
                    "contentHash": content_hash,
                    "expiresAt": expires_at,
                },
            )
            row = conn.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            return self._record(row)

        if connection is not None:
            return insert(connection)
        with self.database.write() as conn:
            return insert(conn)

    def is_expired(self, row, now: datetime | None = None) -> bool:
        expires_at = row["expires_at"]
        if not expires_at:
            return False
        moment = now or self.clock()
        return datetime.fromisoformat(expires_at.replace("Z", "+00:00")) <= moment

    def expire_in(self, connection, row, actor: str) -> None:
        connection.execute(
            """
            UPDATE approvals SET status = 'expired', expired_at = ?
            WHERE id = ? AND status IN ('pending', 'approved')
            """,
            (self._now_iso(), row["id"]),
        )
        AuditService.record(
            connection, "approval.expired", actor, "approval", row["id"], {"expiresAt": row["expires_at"]}
        )

    @staticmethod
    def _require_human(actor: str, channel: str) -> None:
        if channel not in HUMAN_CHANNELS:
            raise ApprovalError(f"approvals can only be decided on a human channel, not '{channel}'")
        if actor in AGENT_ACTORS:
            raise ApprovalError("an agent cannot decide an approval")

    def approve(
        self,
        approval_id: str,
        action: str,
        target: str,
        content_hash: str,
        actor: str,
        channel: str = "dashboard",
    ) -> ApprovalRecord:
        self._require_human(actor, channel)
        outcome = "approved"
        with self.database.write() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("approval not found")
            if row["status"] != "pending":
                raise ApprovalError(f"approval is already {row['status']}")
            if self.is_expired(row):
                self.expire_in(connection, row, actor)
                outcome = "expired"
            elif (
                row["action"] != action
                or row["target"] != target
                or row["content_hash"] != content_hash
            ):
                self._invalidate(connection, approval_id, "approval target or content changed", actor)
                outcome = "mismatch"
            else:
                connection.execute(
                    """
                    UPDATE approvals
                    SET status = 'approved', approved_by = ?, approved_at = ?, decided_channel = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (actor, self._now_iso(), channel, approval_id),
                )
                AuditService.record(
                    connection,
                    "approval.approved",
                    actor,
                    "approval",
                    approval_id,
                    {"action": action, "target": target, "contentHash": content_hash, "channel": channel},
                )
            updated = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        if outcome == "expired":
            raise ApprovalExpiredError("approval has expired; request a new one")
        if outcome == "mismatch":
            raise ApprovalError("approval target or content changed; approval invalidated")
        return self._record(updated)

    def reject(
        self, approval_id: str, actor: str, channel: str = "dashboard", reason: str = ""
    ) -> ApprovalRecord:
        self._require_human(actor, channel)
        with self.database.write() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise NotFoundError("approval not found")
            if row["status"] != "pending":
                raise ApprovalError(f"approval is already {row['status']}")
            connection.execute(
                """
                UPDATE approvals
                SET status = 'rejected', rejected_by = ?, rejected_at = ?, decided_channel = ?
                WHERE id = ? AND status = 'pending'
                """,
                (actor, self._now_iso(), channel, approval_id),
            )
            AuditService.record(
                connection,
                "approval.rejected",
                actor,
                "approval",
                approval_id,
                {"channel": channel, "reason": reason[:200]},
            )
            updated = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        return self._record(updated)

    def consume(
        self, approval_id: str, action: str, target: str, content: Any, actor: str = "broker"
    ) -> ApprovalRecord:
        """Atomically consume one matching approval. Future write brokers must call this."""
        content_hash = canonical_content_hash(content)
        outcome = "consumed"
        with self.database.write() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise NotFoundError("approval not found")
            if row["status"] != "approved":
                raise ApprovalError(f"approval cannot be consumed from status {row['status']}")
            if self.is_expired(row):
                self.expire_in(connection, row, actor)
                outcome = "expired"
            elif (
                row["action"] != action
                or row["target"] != target
                or row["content_hash"] != content_hash
            ):
                self._invalidate(connection, approval_id, "approved target or content changed", actor)
                outcome = "mismatch"
            else:
                self.consume_in(connection, row, actor)
            updated = connection.execute(
                "SELECT * FROM approvals WHERE id = ?", (approval_id,)
            ).fetchone()
        if outcome == "expired":
            raise ApprovalExpiredError("approval has expired; request a new one")
        if outcome == "mismatch":
            raise ApprovalError("approved target or content changed; approval invalidated")
        return self._record(updated)

    def consume_in(self, connection, row, actor: str) -> None:
        """Single-use consumption inside a caller's write transaction."""
        cursor = connection.execute(
            """
            UPDATE approvals SET status = 'consumed', consumed_at = ?
            WHERE id = ? AND status = 'approved'
            """,
            (self._now_iso(), row["id"]),
        )
        if cursor.rowcount != 1:
            raise ApprovalError("approval was already consumed")
        AuditService.record(
            connection,
            "approval.consumed",
            actor,
            "approval",
            row["id"],
            {"action": row["action"], "target": row["target"], "contentHash": row["content_hash"]},
        )

    def _invalidate(self, connection, approval_id: str, reason: str, actor: str) -> None:
        connection.execute(
            """
            UPDATE approvals
            SET status = 'invalidated', invalidated_at = ?, invalidation_reason = ?
            WHERE id = ? AND status IN ('pending', 'approved')
            """,
            (self._now_iso(), reason, approval_id),
        )
        AuditService.record(
            connection,
            "approval.invalidated",
            actor,
            "approval",
            approval_id,
            {"reason": reason},
        )

    def invalidate_in(self, connection, approval_id: str, reason: str, actor: str) -> None:
        self._invalidate(connection, approval_id, reason, actor)

    def expire_due(self, actor: str = "scheduler") -> int:
        """Mark every pending/approved approval past its expiry as expired."""
        count = 0
        with self.database.write() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE status IN ('pending', 'approved') AND expires_at IS NOT NULL"
            ).fetchall()
            for row in rows:
                if self.is_expired(row):
                    self.expire_in(connection, row, actor)
                    count += 1
        return count

    def get(self, approval_id: str) -> ApprovalRecord:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise NotFoundError("approval not found")
        return self._record(row)

    def list(self) -> list[ApprovalRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals ORDER BY created_at DESC LIMIT 200"
            ).fetchall()
        return [self._record(row) for row in rows]
