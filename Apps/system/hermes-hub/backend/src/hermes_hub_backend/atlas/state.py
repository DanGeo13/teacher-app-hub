"""Record state machines. The only place transitions are defined.

Each transition names the evidence actions that can justify it and whether
it needs Dan's approval. Anything not listed is illegal.
"""

from __future__ import annotations

from dataclasses import dataclass

INITIAL_STATUS = {
    "bill": "due",
    "car_service": "due",
    "commitment": "open",
    "task": "open",
}

TERMINAL = {
    "bill": {"confirmed_paid"},
    "car_service": {"completed"},
    "commitment": {"sent", "done"},
    "task": {"done"},
}


@dataclass(frozen=True, slots=True)
class Transition:
    kind: str
    from_status: str
    to_status: str
    #: Evidence actions that can justify the move; empty = no evidence needed.
    evidence_actions: frozenset[str]
    requires_approval: bool
    #: Only reachable through an executed external action (no broker yet).
    external_only: bool = False


def _t(kind, src, dst, actions=(), approval=False, external=False) -> Transition:
    return Transition(kind, src, dst, frozenset(actions), approval, external)


TRANSITIONS: tuple[Transition, ...] = (
    # Bills: due -> payment_reported -> confirmed_paid (needs matching evidence)
    _t("bill", "due", "payment_reported", {"payment_reported", "payment_confirmed"}),
    _t("bill", "payment_reported", "confirmed_paid", {"payment_confirmed"}, approval=True),
    _t("bill", "due", "confirmed_paid", {"payment_confirmed"}, approval=True),
    # Car: due -> booking_requested -> booked -> completed.
    # A booking confirmation can reach "booked" but never "completed".
    _t("car_service", "due", "booking_requested", {"booking_requested"}),
    _t("car_service", "booking_requested", "booked", {"booking_confirmed"}, approval=True),
    _t("car_service", "due", "booked", {"booking_confirmed"}, approval=True),
    _t("car_service", "booked", "completed", {"service_completed"}, approval=True),
    # Commitments: open -> draft_ready -> awaiting_approval -> sent / done
    _t("commitment", "open", "draft_ready"),
    _t("commitment", "draft_ready", "awaiting_approval"),
    _t("commitment", "awaiting_approval", "draft_ready"),
    _t("commitment", "awaiting_approval", "sent", {"message_sent"}, approval=True, external=True),
    _t("commitment", "open", "done", {"message_sent", "task_done"}, approval=True),
    _t("commitment", "draft_ready", "done", {"message_sent", "task_done"}, approval=True),
    _t("commitment", "awaiting_approval", "done", {"message_sent", "task_done"}, approval=True),
    # Tasks
    _t("task", "open", "done", {"task_done"}, approval=True),
)

_INDEX = {(t.kind, t.from_status, t.to_status): t for t in TRANSITIONS}


class TransitionError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def get_transition(kind: str, from_status: str, to_status: str) -> Transition:
    transition = _INDEX.get((kind, from_status, to_status))
    if transition is None:
        if from_status in TERMINAL.get(kind, set()):
            raise TransitionError("TERMINAL_STATE", f"{kind} is already {from_status}")
        raise TransitionError(
            "ILLEGAL_TRANSITION", f"{kind} cannot move from {from_status} to {to_status}"
        )
    return transition


def statuses(kind: str) -> set[str]:
    found = {INITIAL_STATUS[kind]}
    for transition in TRANSITIONS:
        if transition.kind == kind:
            found |= {transition.from_status, transition.to_status}
    return found
