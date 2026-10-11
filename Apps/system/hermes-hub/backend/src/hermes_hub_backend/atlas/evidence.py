"""Evidence matching: close or advance a record only when ALL rules hold.

1. same payee / person / organisation
2. same action (the evidence action justifies the requested transition)
3. consistent date, amount or reference where relevant
4. evidence not already attached to another record
Plus account provenance: evidence from a different account than the record is
never matched automatically. A bare "thanks" / "done" / "confirmed" is never
enough. If any rule fails the record stays unchanged and goes to review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

from .state import Transition
from .text import is_bare_acknowledgement

DATE_WINDOW_DAYS = 45
EVIDENCE_ACTIONS = frozenset(
    {
        "payment_reported",
        "payment_confirmed",
        "booking_requested",
        "booking_confirmed",
        "service_completed",
        "message_sent",
        "task_done",
        "acknowledgement",
        "other",
    }
)
# Transitions that settle money or a service need a hard identifier, not just
# a name: an amount or a reference must be present on both sides and agree.
NEEDS_IDENTIFIER = {"confirmed_paid", "completed"}


@dataclass(slots=True)
class MatchResult:
    matched: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"matched": self.matched, "reasons": list(self.reasons)}


def evaluate(
    record: Mapping[str, Any], evidence: Mapping[str, Any], transition: Transition
) -> MatchResult:
    reasons: list[str] = []

    if evidence.get("attached_record_id") and evidence["attached_record_id"] != record["id"]:
        reasons.append("evidence is already attached to another record")

    if evidence["source_account"] != record["source_account"]:
        reasons.append(
            f"evidence came from account '{evidence['source_account']}' but the record is from "
            f"'{record['source_account']}'; cross-account matches need Dan's confirmation"
        )

    if evidence["action"] == "acknowledgement" or (
        is_bare_acknowledgement(evidence.get("summary")) and not evidence.get("reference")
        and evidence.get("amount_cents") is None
    ):
        reasons.append("a bare acknowledgement ('thanks', 'done', 'confirmed') is never enough")

    if not evidence.get("counterparty_key") or evidence["counterparty_key"] != record["counterparty_key"]:
        reasons.append(
            f"payee/person differs: evidence '{evidence.get('counterparty') or 'unknown'}' vs "
            f"record '{record['counterparty']}'"
        )

    if transition.evidence_actions and evidence["action"] not in transition.evidence_actions:
        reasons.append(
            f"evidence action '{evidence['action']}' cannot justify {transition.from_status} -> "
            f"{transition.to_status} (needs one of {sorted(transition.evidence_actions)})"
        )

    record_amount = record.get("amount_cents")
    evidence_amount = evidence.get("amount_cents")
    if record_amount is not None and evidence_amount is not None and record_amount != evidence_amount:
        reasons.append(f"amount differs: evidence {evidence_amount} vs record {record_amount} cents")

    record_ref = (record.get("reference") or "").strip().lower()
    evidence_ref = (evidence.get("reference") or "").strip().lower()
    if record_ref and evidence_ref and record_ref != evidence_ref:
        reasons.append("reference differs")

    if transition.to_status in NEEDS_IDENTIFIER:
        amount_agrees = record_amount is not None and evidence_amount == record_amount
        reference_agrees = bool(record_ref) and evidence_ref == record_ref
        if transition.kind == "bill" and not (amount_agrees or reference_agrees):
            reasons.append("confirming payment needs a matching amount or reference")
        if transition.kind == "car_service" and not (reference_agrees or evidence.get("occurred_on")):
            reasons.append("completing a service needs a dated service record or matching reference")

    due = record.get("due_date")
    occurred = evidence.get("occurred_on")
    if due and occurred:
        gap = abs((date.fromisoformat(occurred) - date.fromisoformat(due)).days)
        if gap > DATE_WINDOW_DAYS:
            reasons.append(f"evidence date is {gap} days from the due date")

    return MatchResult(matched=not reasons, reasons=reasons)
