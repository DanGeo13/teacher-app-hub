"""Normalisation helpers shared by matching and de-duplication."""

from __future__ import annotations

import re

_SUFFIXES = re.compile(r"\b(pty|ltd|limited|inc|co|corp|corporation|plc|llc|the|australia|aust)\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_BARE_ACK = re.compile(
    r"^\s*(thanks|thank you|thx|ta|done|ok|okay|confirmed|cheers|received|got it|sorted|all good)"
    r"[\s.!]*$",
    re.IGNORECASE,
)
# Banking details Atlas must never store (persona: no banking details).
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_BSB_ACCOUNT = re.compile(r"\b\d{3}-?\d{3}\s*[/ ]\s*\d{6,10}\b")


def counterparty_key(name: str | None) -> str:
    if not name:
        return ""
    lowered = name.lower().replace("&", " and ")
    lowered = _NON_ALNUM.sub(" ", lowered)
    lowered = _SUFFIXES.sub(" ", lowered)
    return " ".join(lowered.split())


def is_bare_acknowledgement(text: str | None) -> bool:
    return bool(text) and bool(_BARE_ACK.match(text))


def contains_banking_details(text: str | None) -> bool:
    if not text:
        return False
    return bool(_CARD.search(text) or _BSB_ACCOUNT.search(text))


def normalise_sentence(text: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", text.lower()).split())
