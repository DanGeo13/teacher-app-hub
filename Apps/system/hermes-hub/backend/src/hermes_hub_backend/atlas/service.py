"""AtlasService: the server-side authority behind every Atlas tool.

All state-machine rules, evidence validation, payload hashing, expiry,
idempotency and audit logging happen here. The MCP server and the Hub API are
thin adapters. Errors are raised as :class:`AtlasError` with a stable code;
nothing fails silently.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..approvals import ApprovalService, canonical_content_hash
from ..audit import AuditService
from ..database import Database
from ..errors import ApprovalError, NotFoundError
from ..redaction import redact_json, redact_text
from . import audio as audio_mod
from . import evidence as evidence_mod
from . import injection
from .locale import Clock, format_aud, format_date, parse_date, sydney_today, system_clock, to_sydney
from .state import INITIAL_STATUS, TERMINAL, TransitionError, get_transition
from .text import contains_banking_details, counterparty_key

AGENT_ACTOR = "atlas-agent"
REPO_ROOT = Path(__file__).resolve().parents[7]
SOURCE_TYPES = frozenset(
    {"email", "notification", "sms", "transcript", "manual", "calendar", "invoice", "statement", "other"}
)
CHECK_OUTCOMES = frozenset({"ok", "no_change", "failed", "stale"})
MESSAGE_CHANNELS = frozenset({"email", "sms", "telegram", "whatsapp", "other"})
# Actions an approval can authorise, and whether a local executor exists.
INTERNAL_ACTIONS = frozenset({"confirm_status", "confirm_paid", "convert_audio_proposal", "atlas_verification_noop"})
EXTERNAL_ACTIONS = frozenset(
    {"send_message", "send_email", "calendar_create", "calendar_edit", "external_purchase", "external_booking"}
)
FORBIDDEN_ACTIONS = frozenset(
    {
        "move_money",
        "access_banking_credentials",
        "impersonate_dan",
        "bypass_authentication",
        "enable_hidden_recording",
        "process_student_data",
        "external_transfer",
    }
)
ID_PREFIX = {"bill": "BIL", "car_service": "CAR", "commitment": "COM", "task": "TSK"}
KIND_LABEL = {"bill": "Bills", "car_service": "Car", "commitment": "Commitments", "task": "Tasks"}


class AtlasError(Exception):
    """Structured, model-visible error. ``code`` is stable."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message, **({"details": self.details} if self.details else {})}}


def resolve_agent_dir() -> Path:
    configured = os.environ.get("ATLAS_AGENT_DIR")
    return Path(configured).expanduser().resolve() if configured else REPO_ROOT / "agent" / "atlas"


def _iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_cents(amount: Any) -> int | None:
    if amount is None or amount == "":
        return None
    try:
        value = round(float(str(amount).replace("$", "").replace(",", "").replace("AUD", "").strip()) * 100)
    except ValueError as error:
        raise AtlasError("INVALID_AMOUNT", f"amount {amount!r} is not a number") from error
    if value < 0:
        raise AtlasError("INVALID_AMOUNT", "amount must not be negative")
    return value


def _date_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return parse_date(str(value)).isoformat()
    except ValueError as error:
        raise AtlasError("INVALID_DATE", f"date {value!r} must be DD/MM/YYYY or YYYY-MM-DD") from error


def _require(value: Any, name: str, max_length: int = 500) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AtlasError("MISSING_FIELD", f"{name} is required")
    if len(value) > max_length:
        raise AtlasError("FIELD_TOO_LONG", f"{name} must be {max_length} characters or fewer")
    return value.strip()


class AtlasService:
    def __init__(
        self,
        database: Database,
        *,
        clock: Clock = system_clock,
        agent_dir: Path | None = None,
        backup_dir: Path | None = None,
    ) -> None:
        self.database = database
        self.clock = clock
        self.approvals = ApprovalService(database, clock=clock)
        self.agent_dir = agent_dir or resolve_agent_dir()
        self.config = json.loads((self.agent_dir / "atlas.config.json").read_text(encoding="utf-8"))
        self.backup_dir = backup_dir

    # ------------------------------------------------------------------ util

    def _now(self) -> datetime:
        return self.clock()

    def _today(self) -> date:
        return sydney_today(self.clock)

    def _audit(self, connection, event: str, target_type: str, target_id: str, details: dict | None = None, actor: str = AGENT_ACTOR) -> None:
        AuditService.record(connection, event, actor, target_type, target_id, redact_json(details or {}))

    @staticmethod
    def _record_dict(row) -> dict[str, Any]:
        data = dict(row)
        data["details"] = json.loads(data.pop("details_json") or "{}")
        data["suspicious"] = bool(data["suspicious"])
        return data

    def _present_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        today = self._today()
        out = {
            "id": record["id"],
            "kind": record["kind"],
            "status": record["status"],
            "label": "proposed" if record["review_state"] == "proposed" else record["review_state"],
            "title": record["title"],
            "counterparty": record["counterparty"],
            "amount": format_aud(record["amount_cents"]) if record["amount_cents"] is not None else None,
            "amountCents": record["amount_cents"],
            "dueDate": format_date(record["due_date"]) if record["due_date"] else None,
            "dueDateIso": record["due_date"],
            "dueOdometerKm": record["due_odometer_km"],
            "currentOdometerKm": record["current_odometer_km"],
            "reference": record["reference"],
            "sourceAccount": record["source_account"],
            "sourceType": record["source_type"],
            "suspicious": record["suspicious"],
            "revision": record["revision"],
            "createdAt": record["created_at"],
            "updatedAt": record["updated_at"],
        }
        if record["due_date"] and record["status"] not in TERMINAL[record["kind"]]:
            days = (date.fromisoformat(record["due_date"]) - today).days
            out["daysUntilDue"] = days
            out["overdue"] = days < 0
        return out

    def _load_record(self, connection, record_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM atlas_records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            raise AtlasError("NOT_FOUND", f"record {record_id} not found")
        return self._record_dict(row)

    @staticmethod
    def _load_evidence(connection, evidence_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT * FROM atlas_evidence WHERE id = ?", (evidence_id,)).fetchone()
        if row is None:
            raise AtlasError("NOT_FOUND", f"evidence {evidence_id} not found")
        return dict(row)

    @staticmethod
    def _present_evidence(evidence: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "id": evidence["id"],
            "sourceAccount": evidence["source_account"],
            "sourceType": evidence["source_type"],
            "externalRef": evidence["external_ref"],
            "counterparty": evidence["counterparty"],
            "action": evidence["action"],
            "amount": format_aud(evidence["amount_cents"]) if evidence["amount_cents"] is not None else None,
            "occurredOn": format_date(evidence["occurred_on"]) if evidence["occurred_on"] else None,
            "reference": evidence["reference"],
            "summary": injection.wrap_untrusted(evidence["summary"], evidence["source_account"]),
            "link": evidence["link"],
            "suspicious": bool(evidence["suspicious"]),
            "attachedRecordId": evidence["attached_record_id"],
        }

    # ------------------------------------------------------------ read tools

    def list_obligations(
        self, kind: str | None = None, include_closed: bool = False, source_account: str | None = None
    ) -> dict[str, Any]:
        if kind is not None and kind not in INITIAL_STATUS:
            raise AtlasError("INVALID_KIND", f"kind must be one of {sorted(INITIAL_STATUS)}")
        query = "SELECT * FROM atlas_records WHERE review_state != 'archived'"
        params: list[Any] = []
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        if source_account:
            query += " AND source_account = ?"
            params.append(source_account)
        with self.database.read() as connection:
            rows = [self._record_dict(r) for r in connection.execute(query, params).fetchall()]
        if not include_closed:
            rows = [r for r in rows if r["status"] not in TERMINAL[r["kind"]]]
        rows.sort(key=lambda r: (r["due_date"] or "9999-12-31", r["title"]))
        return {"today": format_date(self._today()), "records": [self._present_record(r) for r in rows]}

    def get_record(self, record_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            record = self._load_record(connection, record_id)
            history = [dict(r) for r in connection.execute(
                "SELECT from_status, to_status, actor, via, evidence_ids_json, note, created_at "
                "FROM atlas_record_history WHERE record_id = ? ORDER BY id", (record_id,)
            ).fetchall()]
            evidence = [dict(r) for r in connection.execute(
                "SELECT * FROM atlas_evidence WHERE attached_record_id = ? ORDER BY created_at", (record_id,)
            ).fetchall()]
            approvals = [dict(r) for r in connection.execute(
                "SELECT id, action, status, expires_at, summary FROM approvals WHERE target = ? ORDER BY created_at DESC",
                (f"atlas:{record_id}",),
            ).fetchall()]
        for item in history:
            item["evidenceIds"] = json.loads(item.pop("evidence_ids_json"))
        return {
            "record": self._present_record(record),
            "details": injection.wrap_untrusted(json.dumps(record["details"], ensure_ascii=False), record["source_account"]),
            "history": history,
            "evidence": [self._present_evidence(e) for e in evidence],
            "approvals": approvals,
        }

    def search_vault(self, query: str, limit: int = 20) -> dict[str, Any]:
        query = _require(query, "query", 200)
        like = f"%{query.lower()}%"
        limit = max(1, min(int(limit), 50))
        with self.database.read() as connection:
            records = connection.execute(
                "SELECT * FROM atlas_records WHERE lower(title) LIKE ? OR lower(counterparty) LIKE ? "
                "OR lower(coalesce(reference,'')) LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
            evidence = connection.execute(
                "SELECT * FROM atlas_evidence WHERE lower(summary) LIKE ? OR lower(coalesce(counterparty,'')) LIKE ? "
                "OR lower(coalesce(reference,'')) LIKE ? ORDER BY created_at DESC LIMIT ?",
                (like, like, like, limit),
            ).fetchall()
        return {
            "scope": "Hub database records and evidence only (no mailbox or file search)",
            "records": [self._present_record(self._record_dict(r)) for r in records],
            "evidence": [self._present_evidence(dict(e)) for e in evidence],
        }

    def list_pending_approvals(self) -> dict[str, Any]:
        self.approvals.expire_due(actor=AGENT_ACTOR)
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM approvals WHERE status IN ('pending', 'approved') ORDER BY expires_at"
            ).fetchall()
        return {"approvals": [self.present_approval(dict(r)) for r in rows]}

    def approval_view(self, approval_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if row is None:
            raise AtlasError("NOT_FOUND", "approval not found")
        return self.present_approval(dict(row))

    def present_approval(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.loads(row["payload_json"]) if row.get("payload_json") else None
        expires = row.get("expires_at")
        return {
            "id": row["id"],
            "action": row["action"],
            "target": row["target"],
            "status": row["status"],
            "summary": row.get("summary"),
            "payload": payload,
            "contentHash": row["content_hash"],
            "requestedBy": row["requested_by"],
            "expiresAt": expires,
            "expiresLocal": to_sydney(_parse_iso(expires)).strftime("%d/%m/%Y %H:%M") if expires else None,
            "executionStatus": row.get("execution_status"),
        }

    # ------------------------------------------------------------- proposals

    def _insert_record(self, connection, *, kind: str, title: str, counterparty: str, source_account: str,
                       source_type: str, idempotency_key: str, amount_cents: int | None = None,
                       due_date: str | None = None, reference: str | None = None,
                       due_odometer_km: int | None = None, current_odometer_km: int | None = None,
                       details: dict | None = None, status: str | None = None,
                       review_state: str = "proposed") -> tuple[dict[str, Any], bool]:
        existing = connection.execute(
            "SELECT * FROM atlas_records WHERE kind = ? AND source_account = ? AND idempotency_key = ?",
            (kind, source_account, idempotency_key),
        ).fetchone()
        if existing is not None:
            return self._record_dict(existing), False
        record_id = f"{ID_PREFIX[kind]}-{uuid.uuid4().hex[:10].upper()}"
        now = _iso(self._now())
        text_blob = " ".join([title, counterparty, json.dumps(details or {}, ensure_ascii=False)])
        if contains_banking_details(text_blob) or contains_banking_details(reference):
            raise AtlasError(
                "BANKING_DETAILS_REFUSED",
                "Atlas does not store card or bank account numbers; keep a payment reference only",
            )
        findings = injection.screen(text_blob)
        status = status or INITIAL_STATUS[kind]
        connection.execute(
            """
            INSERT INTO atlas_records(
                id, kind, status, review_state, title, counterparty, counterparty_key, amount_cents,
                currency, due_date, due_odometer_km, current_odometer_km, reference, source_account,
                source_type, details_json, idempotency_key, suspicious, revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'AUD', ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                record_id, kind, status, review_state, title, counterparty, counterparty_key(counterparty),
                amount_cents, due_date, due_odometer_km, current_odometer_km, reference, source_account,
                source_type, json.dumps(details or {}, ensure_ascii=False), idempotency_key,
                1 if findings else 0, now, now,
            ),
        )
        connection.execute(
            "INSERT INTO atlas_record_history(record_id, from_status, to_status, actor, via, note, created_at) "
            "VALUES (?, NULL, ?, ?, 'proposal', ?, ?)",
            (record_id, status, AGENT_ACTOR, f"created as {review_state}", now),
        )
        self._audit(connection, "atlas.record.proposed", "atlas_record", record_id,
                    {"kind": kind, "sourceAccount": source_account, "reviewState": review_state})
        if findings:
            self._audit(connection, "suspicious_content", "atlas_record", record_id,
                        {"source": source_account, "findings": findings})
        return self._load_record(connection, record_id), True

    def _check_source(self, source_account: Any, source_type: Any) -> tuple[str, str]:
        account = _require(source_account, "source_account", 120)
        kind = _require(source_type, "source_type", 40)
        if kind not in SOURCE_TYPES:
            raise AtlasError("INVALID_SOURCE_TYPE", f"source_type must be one of {sorted(SOURCE_TYPES)}")
        return account, kind

    def propose_bill(self, *, title: str, payee: str, due_date: str, source_account: str, source_type: str,
                     amount: Any = None, reference: str | None = None, idempotency_key: str | None = None,
                     notes: str | None = None) -> dict[str, Any]:
        account, stype = self._check_source(source_account, source_type)
        title, payee = _require(title, "title", 200), _require(payee, "payee", 200)
        due = _date_or_none(due_date)
        if due is None:
            raise AtlasError("MISSING_FIELD", "due_date is required for a bill")
        cents = _to_cents(amount)
        key = idempotency_key or f"{counterparty_key(payee)}|{due}|{cents}|{reference or ''}"
        with self.database.write() as connection:
            record, created = self._insert_record(
                connection, kind="bill", title=title, counterparty=payee, source_account=account,
                source_type=stype, idempotency_key=key, amount_cents=cents, due_date=due,
                reference=(reference or None), details={"notes": notes} if notes else {},
            )
        return {"created": created, "record": self._present_record(record)}

    def propose_car_service(self, *, title: str, vehicle: str, source_account: str, source_type: str,
                            due_date: str | None = None, due_odometer_km: int | None = None,
                            current_odometer_km: int | None = None, provider: str | None = None,
                            reference: str | None = None, idempotency_key: str | None = None) -> dict[str, Any]:
        account, stype = self._check_source(source_account, source_type)
        title, vehicle = _require(title, "title", 200), _require(vehicle, "vehicle", 120)
        due = _date_or_none(due_date)
        if due is None and due_odometer_km is None:
            raise AtlasError("MISSING_FIELD", "a car service needs a due date or a due odometer reading")
        key = idempotency_key or f"{counterparty_key(vehicle)}|{title.lower()}|{due}|{due_odometer_km}"
        with self.database.write() as connection:
            record, created = self._insert_record(
                connection, kind="car_service", title=title, counterparty=vehicle, source_account=account,
                source_type=stype, idempotency_key=key, due_date=due, reference=reference,
                due_odometer_km=int(due_odometer_km) if due_odometer_km is not None else None,
                current_odometer_km=int(current_odometer_km) if current_odometer_km is not None else None,
                details={"provider": provider} if provider else {},
            )
        return {"created": created, "record": self._present_record(record)}

    def record_odometer(self, *, vehicle: str, odometer_km: int, source_account: str) -> dict[str, Any]:
        """Record a stated odometer reading on the vehicle's open service records."""
        vehicle = _require(vehicle, "vehicle", 120)
        account = _require(source_account, "source_account", 120)
        if not isinstance(odometer_km, int) or not 0 <= odometer_km <= 2_000_000:
            raise AtlasError("INVALID_ODOMETER", "odometer_km must be a whole number of kilometres")
        key = counterparty_key(vehicle)
        updated = []
        with self.database.write() as connection:
            rows = [self._record_dict(r) for r in connection.execute(
                "SELECT * FROM atlas_records WHERE kind = 'car_service' AND counterparty_key = ?", (key,)
            ).fetchall()]
            open_rows = [r for r in rows if r["status"] not in TERMINAL["car_service"]]
            if not open_rows:
                raise AtlasError("NOT_FOUND", f"no open service records for {vehicle}")
            for r in open_rows:
                if r["current_odometer_km"] is not None and odometer_km < r["current_odometer_km"]:
                    raise AtlasError("ODOMETER_WENT_BACKWARDS",
                                     f"{odometer_km} km is lower than the recorded {r['current_odometer_km']} km")
                connection.execute(
                    "UPDATE atlas_records SET current_odometer_km = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                    (odometer_km, _iso(self._now()), r["id"]),
                )
                self._audit(connection, "atlas.odometer.recorded", "atlas_record", r["id"],
                            {"odometerKm": odometer_km, "sourceAccount": account})
                updated.append(r["id"])
            records = [self._present_record(self._load_record(connection, rid)) for rid in updated]
        return {"updated": records}

    def propose_task(self, *, title: str, source_account: str, source_type: str, person: str | None = None,
                     due_date: str | None = None, kind: str = "task", audio_id: str | None = None,
                     audio_timestamp: str | None = None, confidence: float | None = None,
                     idempotency_key: str | None = None) -> dict[str, Any]:
        account, stype = self._check_source(source_account, source_type)
        title = _require(title, "title", 300)
        if kind not in ("task", "commitment"):
            raise AtlasError("INVALID_KIND", "kind must be task or commitment")
        due = _date_or_none(due_date)
        from_audio = stype == "transcript"
        if from_audio and not (audio_id and audio_timestamp):
            raise AtlasError("AUDIO_TIMESTAMP_REQUIRED", "audio-derived proposals must cite audio_id and audio_timestamp")
        details: dict[str, Any] = {}
        if from_audio:
            details = {"audioId": audio_id, "audioTimestamp": audio_timestamp, "confidence": confidence,
                       "lowConfidence": confidence is not None and confidence < self.config["audio"]["lowConfidenceBelow"]}
        key = idempotency_key or f"{title.lower()}|{due}|{audio_id or ''}|{audio_timestamp or ''}"
        with self.database.write() as connection:
            record, created = self._insert_record(
                connection, kind=kind, title=title, counterparty=person or "Dan", source_account=account,
                source_type=stype, idempotency_key=key, due_date=due, details=details,
            )
            approval = None
            if from_audio and created:
                payload = {"recordId": record["id"], "title": title, "dueDate": due, "audioId": audio_id,
                           "audioTimestamp": audio_timestamp, "confidence": confidence,
                           "recordRevision": record["revision"]}
                approval = self.approvals.create(
                    "convert_audio_proposal", f"atlas:{record['id']}", payload, AGENT_ACTOR,
                    expires_in=self._expiry(), store_payload=True, requester_channel="agent",
                    summary=f"Turn voice note ({audio_timestamp}) into {kind}: {title}"
                    + (" [LOW CONFIDENCE]" if details.get("lowConfidence") else ""),
                    connection=connection,
                )
        out = {"created": created, "record": self._present_record(record)}
        if approval is not None:
            out["approval"] = {"id": approval.id, "status": approval.status, "expiresAt": approval.expires_at.isoformat()}
        return out

    def _expiry(self, hours: float | None = None) -> timedelta:
        cfg = self.config["approvals"]
        hours = cfg["defaultExpiryHours"] if hours is None else hours
        if not 0 < hours <= cfg["maxExpiryHours"]:
            raise AtlasError("INVALID_EXPIRY", f"expiry must be between 0 and {cfg['maxExpiryHours']} hours")
        return timedelta(hours=hours)

    def _validate_transition(self, connection, record: dict[str, Any], to_status: str,
                             evidence_ids: list[str]) -> tuple[Any, list[dict[str, Any]], list[dict]]:
        try:
            transition = get_transition(record["kind"], record["status"], to_status)
        except TransitionError as error:
            raise AtlasError(error.code, str(error)) from error
        if transition.external_only:
            raise AtlasError("EXTERNAL_ONLY", f"'{to_status}' is only reached by an executed external action")
        if transition.evidence_actions and not evidence_ids:
            raise AtlasError("EVIDENCE_REQUIRED", f"{record['status']} -> {to_status} needs matching evidence")
        evidence_rows = [self._load_evidence(connection, eid) for eid in evidence_ids]
        results = [evidence_mod.evaluate(record, ev, transition) for ev in evidence_rows]
        return transition, evidence_rows, [r.to_dict() for r in results]

    def propose_status_change(self, *, record_id: str, to_status: str, evidence_ids: list[str] | None = None,
                              note: str | None = None) -> dict[str, Any]:
        evidence_ids = list(dict.fromkeys(evidence_ids or []))
        with self.database.write() as connection:
            record = self._load_record(connection, record_id)
            transition, evidence_rows, results = self._validate_transition(connection, record, to_status, evidence_ids)
            failing = [(ev, r) for ev, r in zip(evidence_rows, results) if not r["matched"]]
            if failing:
                for ev, r in failing:
                    connection.execute(
                        "INSERT INTO atlas_review_items(record_id, evidence_id, reason, created_at) VALUES (?, ?, ?, ?)",
                        (record_id, ev["id"], "; ".join(r["reasons"]), _iso(self._now())),
                    )
                self._audit(connection, "atlas.evidence.rejected", "atlas_record", record_id,
                            {"toStatus": to_status, "evidenceIds": [ev["id"] for ev, _ in failing]})
                return {
                    "changed": False,
                    "reviewRequired": True,
                    "record": self._present_record(record),
                    "evidenceResults": dict(zip(evidence_ids, results)),
                    "message": "Evidence did not match; record left unchanged and added to the review list.",
                }
            if not transition.requires_approval:
                self._apply_transition(connection, record, to_status, evidence_ids, via="autonomous", note=note)
                return {"changed": True, "record": self._present_record(self._load_record(connection, record_id))}
            action = "confirm_paid" if to_status == "confirmed_paid" else "confirm_status"
            payload = {
                "recordId": record_id,
                "kind": record["kind"],
                "title": record["title"],
                "counterparty": record["counterparty"],
                "sourceAccount": record["source_account"],
                "fromStatus": record["status"],
                "toStatus": to_status,
                "evidenceIds": evidence_ids,
                "recordRevision": record["revision"],
            }
            approval = self.approvals.create(
                action, f"atlas:{record_id}", payload, AGENT_ACTOR, expires_in=self._expiry(),
                store_payload=True, requester_channel="agent",
                summary=f"{record['title']} ({record['source_account']}): {record['status']} -> {to_status}",
                connection=connection,
            )
            self._audit(connection, "atlas.status.proposed", "atlas_record", record_id,
                        {"toStatus": to_status, "approvalId": approval.id})
        return {
            "changed": False,
            "approvalRequired": True,
            "approval": {"id": approval.id, "action": action, "expiresAt": approval.expires_at.isoformat(),
                         "contentHash": approval.content_hash},
            "record": self._present_record(record),
            "message": "Waiting for Dan's approval of this exact change.",
        }

    def _apply_transition(self, connection, record: dict[str, Any], to_status: str, evidence_ids: list[str],
                          *, via: str, note: str | None = None, actor: str = AGENT_ACTOR) -> None:
        now = _iso(self._now())
        cursor = connection.execute(
            "UPDATE atlas_records SET status = ?, revision = revision + 1, updated_at = ? "
            "WHERE id = ? AND revision = ? AND status = ?",
            (to_status, now, record["id"], record["revision"], record["status"]),
        )
        if cursor.rowcount != 1:
            raise AtlasError("RECORD_CHANGED", "the record changed while this change was being applied")
        for evidence_id in evidence_ids:
            claimed = connection.execute(
                "UPDATE atlas_evidence SET attached_record_id = ? WHERE id = ? "
                "AND (attached_record_id IS NULL OR attached_record_id = ?)",
                (record["id"], evidence_id, record["id"]),
            )
            if claimed.rowcount != 1:
                raise AtlasError("EVIDENCE_ALREADY_USED", f"evidence {evidence_id} is attached to another record")
        connection.execute(
            "INSERT INTO atlas_record_history(record_id, from_status, to_status, actor, via, evidence_ids_json, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (record["id"], record["status"], to_status, actor, via, json.dumps(evidence_ids), (note or "")[:500], now),
        )
        self._audit(connection, "atlas.status.changed", "atlas_record", record["id"],
                    {"from": record["status"], "to": to_status, "via": via, "evidenceIds": evidence_ids}, actor=actor)

    def propose_message_draft(self, *, recipient: str, channel: str, body: str, source_account: str,
                              record_id: str | None = None, subject: str | None = None,
                              title: str | None = None, due_date: str | None = None) -> dict[str, Any]:
        recipient = _require(recipient, "recipient", 200)
        body = _require(body, "body", 4000)
        if channel not in MESSAGE_CHANNELS:
            raise AtlasError("INVALID_CHANNEL", f"channel must be one of {sorted(MESSAGE_CHANNELS)}")
        account = _require(source_account, "source_account", 120)
        findings = injection.screen(body)
        with self.database.write() as connection:
            if record_id:
                record = self._load_record(connection, record_id)
                if record["kind"] != "commitment":
                    raise AtlasError("INVALID_KIND", "message drafts attach to commitments")
            else:
                record, _ = self._insert_record(
                    connection, kind="commitment", title=title or f"Message {recipient}", counterparty=recipient,
                    source_account=account, source_type="manual",
                    idempotency_key=hashlib.sha256(f"{recipient}|{body}".encode()).hexdigest()[:32],
                    due_date=_date_or_none(due_date),
                )
            if record["status"] == "open":
                self._apply_transition(connection, record, "draft_ready", [], via="autonomous", note="draft created")
                record = self._load_record(connection, record["id"])
            if record["status"] != "draft_ready":
                raise AtlasError("ILLEGAL_TRANSITION", f"commitment is {record['status']}; a new draft needs draft_ready")
            payload = {"recordId": record["id"], "channel": channel, "recipient": recipient, "subject": subject,
                       "body": body, "fromAccount": account, "recordRevision": record["revision"] + 1}
            action = "send_email" if channel == "email" else "send_message"
            self._apply_transition(connection, record, "awaiting_approval", [], via="autonomous", note="draft submitted")
            approval = self.approvals.create(
                action, f"atlas:{record['id']}", payload, AGENT_ACTOR, expires_in=self._expiry(), store_payload=True,
                requester_channel="agent", summary=f"{action} to {recipient} via {channel} from {account}",
                connection=connection,
            )
            if findings:
                self._audit(connection, "suspicious_content", "approval", approval.id, {"findings": findings})
            record = self._load_record(connection, record["id"])
        return {
            "record": self._present_record(record),
            "approval": {"id": approval.id, "action": action, "expiresAt": approval.expires_at.isoformat()},
            "suspicious": findings,
            "note": "No send broker is installed yet: even after approval, sending fails closed and Dan sends it himself.",
        }

    # -------------------------------------------------------------- evidence

    def record_evidence(self, *, source_account: str, source_type: str, external_ref: str, action: str,
                        summary: str = "", counterparty: str | None = None, amount: Any = None,
                        occurred_on: str | None = None, reference: str | None = None,
                        link: str | None = None) -> dict[str, Any]:
        account, stype = self._check_source(source_account, source_type)
        external_ref = _require(external_ref, "external_ref", 300)
        if action not in evidence_mod.EVIDENCE_ACTIONS:
            raise AtlasError("INVALID_ACTION", f"action must be one of {sorted(evidence_mod.EVIDENCE_ACTIONS)}")
        summary = redact_text(summary or "", max_length=2000) or ""
        if contains_banking_details(summary):
            raise AtlasError("BANKING_DETAILS_REFUSED", "summarise without card or account numbers")
        findings = injection.screen(summary) + injection.screen(external_ref)
        with self.database.write() as connection:
            existing = connection.execute(
                "SELECT * FROM atlas_evidence WHERE source_account = ? AND external_ref = ?", (account, external_ref)
            ).fetchone()
            if existing is not None:
                evidence = dict(existing)
                created = False
            else:
                evidence_id = f"EVD-{uuid.uuid4().hex[:10].upper()}"
                connection.execute(
                    """
                    INSERT INTO atlas_evidence(id, source_account, source_type, external_ref, counterparty,
                        counterparty_key, action, amount_cents, occurred_on, reference, summary, link,
                        suspicious, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (evidence_id, account, stype, external_ref, counterparty, counterparty_key(counterparty), action,
                     _to_cents(amount), _date_or_none(occurred_on), reference, summary, link,
                     1 if findings else 0, _iso(self._now())),
                )
                self._audit(connection, "atlas.evidence.recorded", "atlas_evidence", evidence_id,
                            {"sourceAccount": account, "action": action})
                if findings:
                    self._audit(connection, "suspicious_content", "atlas_evidence", evidence_id,
                                {"source": account, "findings": findings})
                evidence = self._load_evidence(connection, evidence_id)
                created = True
            candidates = self._match_candidates(connection, evidence)
        return {"created": created, "evidence": self._present_evidence(evidence),
                "suspicious": findings, "candidates": candidates}

    def _match_candidates(self, connection, evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
        from .state import TRANSITIONS

        out = []
        rows = connection.execute(
            "SELECT * FROM atlas_records WHERE review_state != 'archived'"
        ).fetchall()
        for row in rows:
            record = self._record_dict(row)
            for transition in TRANSITIONS:
                if (transition.kind, transition.from_status) != (record["kind"], record["status"]):
                    continue
                if transition.external_only or evidence["action"] not in transition.evidence_actions:
                    continue
                result = evidence_mod.evaluate(record, evidence, transition)
                if result.matched or record["counterparty_key"] == evidence.get("counterparty_key"):
                    out.append({"recordId": record["id"], "title": record["title"], "toStatus": transition.to_status,
                                "requiresApproval": transition.requires_approval, **result.to_dict()})
        return out

    def log_suspicious_content(self, *, source: str, quote: str, record_id: str | None = None) -> dict[str, Any]:
        source = _require(source, "source", 200)
        quote = redact_text(_require(quote, "quote", 1000), max_length=500)
        with self.database.write() as connection:
            if record_id:
                self._load_record(connection, record_id)
                connection.execute("UPDATE atlas_records SET suspicious = 1 WHERE id = ?", (record_id,))
            self._audit(connection, "suspicious_content", "atlas_record" if record_id else "source",
                        record_id or source, {"source": source, "quote": quote, "reportedBy": "model"})
        return {"logged": True}

    # ------------------------------------------------------------- execution

    def execute_approved_action(self, approval_id: str, actor: str = AGENT_ACTOR) -> dict[str, Any]:
        with self.database.write() as connection:
            row = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
            if row is None:
                raise AtlasError("NOT_FOUND", "approval not found")
            row = dict(row)
            if row["status"] == "consumed" and row["execution_status"] == "succeeded":
                return {"executed": True, "idempotentReplay": True,
                        "result": json.loads(row["execution_result_json"] or "{}")}
            if row["action"] in FORBIDDEN_ACTIONS:
                raise AtlasError("FORBIDDEN_ACTION", f"'{row['action']}' is never executed")
            if row["status"] == "pending":
                raise AtlasError("NOT_APPROVED", "Dan has not approved this action yet")
            if row["status"] != "approved":
                raise AtlasError("APPROVAL_NOT_USABLE", f"approval is {row['status']}")
            if row["payload_json"] is None:
                raise AtlasError("NO_PAYLOAD", "this approval has no stored payload and cannot be executed by Atlas")
            if self.approvals.is_expired(row, self._now()):
                self.approvals.expire_in(connection, row, AGENT_ACTOR)
                return_error = AtlasError("APPROVAL_EXPIRED", "approval expired before execution; request a new one")
            else:
                return_error = None
        if return_error is not None:
            raise return_error

        payload = json.loads(row["payload_json"])
        if canonical_content_hash(payload) != row["content_hash"]:
            with self.database.write() as connection:
                self.approvals.invalidate_in(connection, approval_id, "stored payload no longer matches its hash", AGENT_ACTOR)
            raise AtlasError("PAYLOAD_CHANGED", "the approved payload changed; approval invalidated")
        if row["action"] in EXTERNAL_ACTIONS:
            raise AtlasError(
                "BROKER_NOT_AVAILABLE",
                f"No broker is installed for '{row['action']}'. Nothing was sent; the approval is still unused.",
            )
        if row["action"] not in INTERNAL_ACTIONS:
            raise AtlasError("UNKNOWN_ACTION", f"no executor for '{row['action']}'")

        with self.database.write() as connection:
            fresh = dict(connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone())
            if fresh["status"] != "approved":
                raise AtlasError("APPROVAL_NOT_USABLE", f"approval is {fresh['status']}")
            result = self._run_internal(connection, fresh, payload)
            self.approvals.consume_in(connection, fresh, actor)
            connection.execute(
                "UPDATE approvals SET execution_status = 'succeeded', execution_result_json = ?, executed_at = ? WHERE id = ?",
                (json.dumps(result), _iso(self._now()), approval_id),
            )
            self._audit(connection, "atlas.action.executed", "approval", approval_id,
                        {"action": fresh["action"], "result": result})
        return {"executed": True, "idempotentReplay": False, "result": result}

    def _run_internal(self, connection, approval: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
        action = approval["action"]
        if action == "atlas_verification_noop":
            return {"verification": True}
        record = self._load_record(connection, payload["recordId"])
        if approval["target"] != f"atlas:{record['id']}":
            raise AtlasError("TARGET_MISMATCH", "approval target does not match the record")
        if record["revision"] != payload["recordRevision"]:
            self.approvals.invalidate_in(connection, approval["id"], "record changed after approval was requested", AGENT_ACTOR)
            raise AtlasError("RECORD_CHANGED", "the record changed after approval; approval invalidated, ask again")
        if action == "convert_audio_proposal":
            connection.execute(
                "UPDATE atlas_records SET review_state = 'active', revision = revision + 1, updated_at = ? WHERE id = ?",
                (_iso(self._now()), record["id"]),
            )
            self._audit(connection, "atlas.record.accepted", "atlas_record", record["id"], {"via": f"approval:{approval['id']}"})
            return {"recordId": record["id"], "reviewState": "active"}
        # confirm_status / confirm_paid
        if record["status"] != payload["fromStatus"]:
            raise AtlasError("RECORD_CHANGED", "record status changed after approval")
        transition, evidence_rows, results = self._validate_transition(
            connection, record, payload["toStatus"], list(payload["evidenceIds"])
        )
        if not all(r["matched"] for r in results):
            raise AtlasError("EVIDENCE_NO_LONGER_MATCHES", "evidence no longer matches", results=results)
        self._apply_transition(connection, record, payload["toStatus"], list(payload["evidenceIds"]),
                               via=f"approval:{approval['id']}", actor=AGENT_ACTOR)
        return {"recordId": record["id"], "status": payload["toStatus"]}

    # ------------------------------------------------------- checks / health

    def _check_cfg(self, check_name: str) -> dict[str, Any]:
        cfg = self.config["checks"].get(check_name)
        if cfg is None:
            raise AtlasError("UNKNOWN_CHECK", f"unknown check '{check_name}'; known: {sorted(self.config['checks'])}")
        return cfg

    def get_check_status(self, check_name: str) -> dict[str, Any]:
        cfg = self._check_cfg(check_name)
        with self.database.read() as connection:
            last = connection.execute(
                "SELECT * FROM atlas_checks WHERE check_name = ? ORDER BY id DESC LIMIT 1", (check_name,)
            ).fetchone()
            last_ok = connection.execute(
                "SELECT * FROM atlas_checks WHERE check_name = ? AND outcome IN ('ok','no_change') ORDER BY id DESC LIMIT 1",
                (check_name,),
            ).fetchone()
        now = self._now()
        if last is None:
            extra = self.weekly_digest_window() if check_name == "weekly_digest" else {}
            return {"check": check_name, "state": "never_run", "catchUpRequired": False,
                    "text": f"{check_name} has never run.", "expectedEveryMinutes": cfg["expectedEveryMinutes"], **extra}
        gap = int((now - _parse_iso(last["created_at"])).total_seconds() // 60)
        state = last["outcome"]
        if gap > cfg["staleAfterMinutes"]:
            state = "stale"
        catch_up = gap > 2 * cfg["expectedEveryMinutes"]
        when = to_sydney(_parse_iso(last["created_at"])).strftime("%H:%M %d/%m/%Y")
        extra = self.weekly_digest_window() if check_name == "weekly_digest" else {}
        text = {
            "ok": f"{check_name} ran at {when} and found changes.",
            "no_change": f"{check_name} ran at {when}: nothing changed.",
            "failed": f"{check_name} FAILED at {when}: {last['detail']}",
            "stale": f"{check_name} has NOT run since {when} ({gap} minutes); data may be out of date.",
        }[state]
        return {
            "check": check_name,
            "state": state,
            "lastRunAt": last["created_at"],
            "lastOutcome": last["outcome"],
            "lastSuccessAt": last_ok["created_at"] if last_ok else None,
            "gapMinutes": gap,
            "catchUpRequired": catch_up,
            "catchUpInstruction": (
                f"Run ONE reconciliation now covering the {gap}-minute gap. Keep original due dates. "
                "Alerts are de-duplicated server-side. Log it with catch_up=true."
            ) if catch_up else None,
            "expectedEveryMinutes": cfg["expectedEveryMinutes"],
            "text": text,
            **extra,
        }

    def weekly_digest_window(self) -> dict[str, Any]:
        """Is the Sunday 08:00 Australia/Sydney digest due right now?

        Hermes 0.19.0 anchors cron on the last run's fixed UTC offset, so the
        first run after a daylight-saving change lands an hour early or late.
        The weekly job therefore fires hourly on Sunday mornings and the Hub
        decides: due from 08:00 local until noon, once per week.
        """
        local = to_sydney(self._now())
        if local.weekday() != 6 or not 8 <= local.hour < 12:
            return {"digestDueNow": False, "reason": "outside Sunday 08:00-12:00 Sydney",
                    "localTime": local.strftime("%a %d/%m/%Y %H:%M")}
        slot_start = local.replace(hour=8, minute=0, second=0, microsecond=0)
        with self.database.read() as connection:
            sent = connection.execute(
                "SELECT created_at FROM atlas_checks WHERE check_name = 'weekly_digest' AND outcome = 'ok' "
                "AND created_at >= ? ORDER BY id DESC LIMIT 1",
                (_iso(slot_start.astimezone(UTC)),),
            ).fetchone()
        return {"digestDueNow": sent is None,
                "reason": "already sent this week" if sent else "Sunday digest slot",
                "localTime": local.strftime("%a %d/%m/%Y %H:%M")}

    def log_check(self, *, check_name: str, outcome: str, detail: str = "", source_account: str | None = None,
                  catch_up: bool = False) -> dict[str, Any]:
        self._check_cfg(check_name)
        if outcome not in CHECK_OUTCOMES:
            raise AtlasError("INVALID_OUTCOME", f"outcome must be one of {sorted(CHECK_OUTCOMES)}")
        if outcome in ("failed", "stale") and not (detail or "").strip():
            raise AtlasError("MISSING_FIELD", "a failed or stale check needs a detail explaining why")
        gap = None
        if catch_up:
            previous = self.get_check_status(check_name)
            gap = previous.get("gapMinutes")
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO atlas_checks(check_name, outcome, detail, source_account, catch_up, gap_minutes, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (check_name, outcome, redact_text(detail, max_length=500) or "", source_account,
                 1 if catch_up else 0, gap, _iso(self._now())),
            )
            self._audit(connection, f"atlas.check.{outcome}", "atlas_check", check_name,
                        {"sourceAccount": source_account, "catchUp": catch_up, "gapMinutes": gap})
        response = {"logged": True, "check": check_name, "outcome": outcome}
        if catch_up and gap is not None:
            response["gapReport"] = f"Atlas was not running checks for {gap} minutes; one reconciliation covered the gap."
        return response

    def get_system_health(self) -> dict[str, Any]:
        checks = [self.get_check_status(name) for name in self.config["checks"]]
        now = self._now()
        audio_cutoff = _iso(now - timedelta(minutes=self.config["audio"]["unprocessedAfterMinutes"]))
        with self.database.read() as connection:
            unprocessed_audio = connection.execute(
                "SELECT count(*) FROM atlas_audio_items WHERE status = 'queued' AND received_at <= ?", (audio_cutoff,)
            ).fetchone()[0]
            failed_audio = connection.execute("SELECT count(*) FROM atlas_audio_items WHERE status = 'failed'").fetchone()[0]
            failed_backups = connection.execute("SELECT count(*) FROM backup_records WHERE status = 'failed'").fetchone()[0]
            last_backup = connection.execute(
                "SELECT created_at FROM backup_records WHERE status != 'failed' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            review = connection.execute("SELECT count(*) FROM atlas_review_items WHERE resolved_at IS NULL").fetchone()[0]
            suspicious = connection.execute(
                "SELECT count(*) FROM audit_events WHERE event_type = 'suspicious_content' AND created_at >= ?",
                (_iso(now - timedelta(days=7)),),
            ).fetchone()[0]
        problems = [c["text"] for c in checks if c["state"] in ("failed", "stale")]
        if unprocessed_audio:
            problems.append(f"{unprocessed_audio} voice note(s) waiting more than {self.config['audio']['unprocessedAfterMinutes']} minutes")
        if failed_audio:
            problems.append(f"{failed_audio} voice note(s) failed processing")
        if failed_backups:
            problems.append(f"{failed_backups} backup(s) failed")
        return {
            "overall": "attention" if problems else "ok",
            "problems": problems,
            "checks": checks,
            "unprocessedAudio": unprocessed_audio,
            "failedAudio": failed_audio,
            "failedBackups": failed_backups,
            "lastBackupAt": last_backup[0] if last_backup else None,
            "openReviewItems": review,
            "suspiciousContentLast7Days": suspicious,
        }

    def run_backup(self) -> dict[str, Any]:
        from ..backup import BackupService

        if self.backup_dir is None:
            raise AtlasError("BACKUP_NOT_CONFIGURED", "no backup directory is configured")
        try:
            record = BackupService(self.database, self.backup_dir).create_snapshot()
        except Exception as error:  # reported, never swallowed
            self.log_check(check_name="nightly_backup", outcome="failed", detail=f"backup failed: {error}")
            raise AtlasError("BACKUP_FAILED", f"backup failed: {error}") from error
        self.log_check(check_name="nightly_backup", outcome="ok", detail=f"snapshot {record.filename}")
        return {"backup": record.model_dump(by_alias=True, mode="json"),
                "note": "Local snapshot only; this is not an off-device backup."}

    # ------------------------------------------------------------------ alerts

    def compute_alerts(self) -> list[dict[str, Any]]:
        today = self._today()
        now = self._now()
        alerts_cfg = self.config["alerts"]
        out: list[dict[str, Any]] = []
        with self.database.read() as connection:
            rows = [self._record_dict(r) for r in connection.execute(
                "SELECT * FROM atlas_records WHERE review_state != 'archived'"
            ).fetchall()]
            approvals = connection.execute(
                "SELECT * FROM approvals WHERE status = 'pending' AND expires_at IS NOT NULL"
            ).fetchall()
            audio = connection.execute(
                "SELECT id, received_at FROM atlas_audio_items WHERE status IN ('queued','failed')"
            ).fetchall()
        for r in rows:
            if r["status"] in TERMINAL[r["kind"]]:
                continue
            label = " (proposed)" if r["review_state"] == "proposed" else ""
            where = f" [{r['source_account']}]"
            if r["kind"] == "bill" and r["due_date"]:
                days = (date.fromisoformat(r["due_date"]) - today).days
                if days < 0:
                    out.append({"key": f"bill_overdue:{r['id']}:{r['due_date']}", "recordId": r["id"], "kind": "bill_overdue",
                                "severity": "urgent", "message": f"🔴 OVERDUE: {r['title']}{label} {format_aud(r['amount_cents'])} was due {format_date(r['due_date'])}{where}"})
                elif days * 24 <= alerts_cfg["billDueWithinHours"] and r["status"] == "due":
                    out.append({"key": f"bill_due_soon:{r['id']}:{r['due_date']}", "recordId": r["id"], "kind": "bill_due_soon",
                                "severity": "urgent", "message": f"🔴 Due {format_date(r['due_date'])}: {r['title']}{label} {format_aud(r['amount_cents'])}{where}"})
            if r["kind"] == "car_service":
                by_date = r["due_date"] and date.fromisoformat(r["due_date"]) < today
                by_km = (r["due_odometer_km"] is not None and r["current_odometer_km"] is not None
                         and r["current_odometer_km"] >= r["due_odometer_km"])
                if (by_date or by_km) and r["status"] in ("due", "booking_requested"):
                    reason = "date" if by_date else "kilometres"
                    out.append({"key": f"service_overdue:{r['id']}:{r['due_date']}:{r['due_odometer_km']}", "recordId": r["id"],
                                "kind": "service_overdue", "severity": "urgent",
                                "message": f"🔴 Service overdue by {reason}: {r['title']}{label} ({r['counterparty']}){where}"})
        soon = now + timedelta(hours=self.config["approvals"]["expiringSoonHours"])
        for a in approvals:
            expires = _parse_iso(a["expires_at"])
            if now < expires <= soon:
                out.append({"key": f"approval_expiring:{a['id']}", "recordId": None, "kind": "approval_expiring",
                            "severity": "urgent",
                            "message": f"🟠 Approval expires {to_sydney(expires).strftime('%H:%M %d/%m/%Y')}: {a['summary'] or a['action']}"})
        for name in self.config["checks"]:
            status = self.get_check_status(name)
            if status["state"] == "failed":
                out.append({"key": f"check_failed:{name}:{status['lastRunAt']}", "recordId": None, "kind": "check_failed",
                            "severity": "system", "message": f"⚠️ {status['text']}"})
            elif status["state"] == "stale":
                out.append({"key": f"check_stale:{name}:{status['lastRunAt']}", "recordId": None, "kind": "check_stale",
                            "severity": "system", "message": f"⚠️ {status['text']}"})
        cutoff = now - timedelta(minutes=self.config["audio"]["unprocessedAfterMinutes"])
        for item in audio:
            if _parse_iso(item["received_at"]) <= cutoff:
                out.append({"key": f"audio_unprocessed:{item['id']}", "recordId": None, "kind": "audio_unprocessed",
                            "severity": "system", "message": f"⚠️ Voice note {item['id']} is still unprocessed"})
        return out

    def collect_alerts(self, run_ref: str = "") -> dict[str, Any]:
        """New immediate alerts not yet handed to delivery (de-duplicated)."""
        alerts = self.compute_alerts()
        fresh = []
        now = _iso(self._now())
        with self.database.write() as connection:
            for alert in alerts:
                row = connection.execute("SELECT * FROM atlas_alerts WHERE dedupe_key = ?", (alert["key"],)).fetchone()
                if row is not None and row["delivered_count"] > 0:
                    continue
                if row is None:
                    connection.execute(
                        "INSERT INTO atlas_alerts(dedupe_key, record_id, kind, severity, message, first_raised_at, "
                        "last_delivered_at, delivered_count) VALUES (?, ?, ?, ?, ?, ?, ?, 1)",
                        (alert["key"], alert["recordId"], alert["kind"], alert["severity"], alert["message"], now, now),
                    )
                else:
                    connection.execute(
                        "UPDATE atlas_alerts SET delivered_count = delivered_count + 1, last_delivered_at = ? WHERE dedupe_key = ?",
                        (now, alert["key"]),
                    )
                fresh.append(alert)
            if fresh:
                self._audit(connection, "atlas.alerts.collected", "atlas_alerts", run_ref or "run",
                            {"count": len(fresh), "keys": [a["key"] for a in fresh]})
        return {"alerts": fresh, "suppressedDuplicates": len(alerts) - len(fresh),
                "deliver": bool(fresh)}

    # ------------------------------------------------------------------ digest

    def build_digest(self, channel: str = "telegram") -> dict[str, Any]:
        from .digest import build_digest

        return build_digest(self, channel)

    # ------------------------------------------------------------------- audio

    def enqueue_audio(self, *, source_device: str, recorded_at: str, chunks: list[dict[str, Any]],
                      actor: str = "admin") -> dict[str, Any]:
        source_device = _require(source_device, "source_device", 120)
        audio_mod.merge_chunks(chunks)  # validates offsets and segments
        audio_id = f"AUD-{uuid.uuid4().hex[:10].upper()}"
        with self.database.write() as connection:
            connection.execute(
                "INSERT INTO atlas_audio_items(id, source_device, recorded_at, status, chunks_json, received_at) "
                "VALUES (?, ?, ?, 'queued', ?, ?)",
                (audio_id, source_device, recorded_at, json.dumps(chunks, ensure_ascii=False), _iso(self._now())),
            )
            self._audit(connection, "atlas.audio.queued", "atlas_audio", audio_id, {"device": source_device}, actor=actor)
        return {"id": audio_id, "status": "queued"}

    def list_audio_queue(self, limit: int = 5) -> dict[str, Any]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM atlas_audio_items WHERE status = 'queued' ORDER BY received_at LIMIT ?",
                (max(1, min(int(limit), 20)),),
            ).fetchall()
        items = []
        for row in rows:
            segments = audio_mod.merge_chunks(json.loads(row["chunks_json"]))
            low = self.config["audio"]["lowConfidenceBelow"]
            items.append({
                "id": row["id"],
                "sourceDevice": row["source_device"],
                "recordedAt": row["recorded_at"],
                "segments": [
                    {**s.to_dict(), "lowConfidence": s.confidence < low,
                     "text": injection.wrap_untrusted(s.text, f"audio:{row['id']}"),
                     "suspicious": injection.screen(s.text)}
                    for s in segments
                ],
            })
        return {"items": items}

    def submit_audio_extraction(self, *, audio_id: str, proposals: list[dict[str, Any]] | None = None,
                                failed: bool = False, error: str | None = None) -> dict[str, Any]:
        with self.database.read() as connection:
            row = connection.execute("SELECT * FROM atlas_audio_items WHERE id = ?", (audio_id,)).fetchone()
        if row is None:
            raise AtlasError("NOT_FOUND", f"audio item {audio_id} not found")
        if row["status"] != "queued":
            raise AtlasError("ALREADY_PROCESSED", f"audio item is {row['status']}")
        if failed:
            with self.database.write() as connection:
                connection.execute(
                    "UPDATE atlas_audio_items SET status = 'failed', processed_at = ?, error = ? WHERE id = ?",
                    (_iso(self._now()), redact_text(error or "unspecified", max_length=300), audio_id),
                )
                self._audit(connection, "atlas.audio.failed", "atlas_audio", audio_id, {"error": error})
            return {"status": "failed"}
        segments = audio_mod.merge_chunks(json.loads(row["chunks_json"]))
        valid_stamps = {s.to_dict()["timestamp"] for s in segments}
        results = []
        for proposal in proposals or []:
            stamp = proposal.get("audio_timestamp")
            if stamp not in valid_stamps:
                raise AtlasError("AUDIO_TIMESTAMP_INVALID",
                                 f"timestamp {stamp!r} does not match any merged segment start {sorted(valid_stamps)}")
            results.append(self.propose_task(
                title=proposal["title"], source_account=f"audio:{row['source_device']}", source_type="transcript",
                person=proposal.get("person"), due_date=proposal.get("due_date"), kind=proposal.get("kind", "task"),
                audio_id=audio_id, audio_timestamp=stamp, confidence=proposal.get("confidence"),
            ))
        with self.database.write() as connection:
            connection.execute(
                "UPDATE atlas_audio_items SET status = 'processed', processed_at = ? WHERE id = ?",
                (_iso(self._now()), audio_id),
            )
            self._audit(connection, "atlas.audio.processed", "atlas_audio", audio_id, {"proposals": len(results)})
        return {"status": "processed", "proposals": results}

    # ------------------------------------------------------------------ skills

    def get_skill(self, name: str) -> dict[str, Any]:
        name = _require(name, "name", 64)
        if "/" in name or name.startswith("."):
            raise AtlasError("INVALID_SKILL", "skill names are plain folder names")
        lock = json.loads((self.agent_dir / "skills.lock.json").read_text(encoding="utf-8"))
        entry = lock["skills"].get(name)
        path = self.agent_dir / "skills" / name / "SKILL.md"
        if entry is None or not path.is_file():
            raise AtlasError("SKILL_NOT_FOUND", f"no reviewed skill named {name}")
        content = path.read_bytes()
        if not entry.get("reviewedBy"):
            raise AtlasError("SKILL_NOT_REVIEWED", f"{name} is waiting for Dan's review and cannot be used yet")
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            raise AtlasError("SKILL_NOT_REVIEWED",
                             f"{name} changed since Dan reviewed it; it stays disabled until he re-approves it")
        return {"name": name, "reviewedSha256": entry["sha256"], "content": content.decode("utf-8")}

    # ------------------------------------------------------------- human side

    def accept_record(self, record_id: str, actor: str) -> dict[str, Any]:
        with self.database.write() as connection:
            record = self._load_record(connection, record_id)
            if record["review_state"] != "proposed":
                raise AtlasError("NOT_PROPOSED", f"record is {record['review_state']}")
            if record["source_type"] == "transcript":
                raise AtlasError("NEEDS_APPROVAL", "audio-derived records are accepted through their approval")
            connection.execute(
                "UPDATE atlas_records SET review_state = 'active', revision = revision + 1, updated_at = ? WHERE id = ?",
                (_iso(self._now()), record_id),
            )
            self._audit(connection, "atlas.record.accepted", "atlas_record", record_id, {"via": "dashboard"}, actor=actor)
        return {"record": self._present_record(self._load_record_ro(record_id))}

    def _load_record_ro(self, record_id: str) -> dict[str, Any]:
        with self.database.read() as connection:
            return self._load_record(connection, record_id)

    def list_review_items(self) -> list[dict[str, Any]]:
        with self.database.read() as connection:
            return [dict(r) for r in connection.execute(
                "SELECT * FROM atlas_review_items WHERE resolved_at IS NULL ORDER BY id DESC LIMIT 100"
            ).fetchall()]
