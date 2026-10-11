"""Screening of untrusted text (email, notification, transcript, filename).

A match never blocks storage: the text stays data. It flags the item as
suspicious and writes a ``suspicious_content`` audit event, so the model is
told and Dan can see it. Patterns target instruction-shaped text aimed at an
agent, not ordinary English.
"""

from __future__ import annotations

import re
import unicodedata

_F = r"(?:\w+\s+){0,8}"
PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions", re.compile(rf"\b(ignore|disregard|forget|override)\s+{_F}(previous|prior|above|earlier|all|your|system)\s+{_F}(instructions|rules|prompt|guidelines|policy)", re.I)),
    ("role_hijack", re.compile(r"\b(you are now|act as|pretend to be|new instructions|system prompt|developer mode)\b", re.I)),
    ("approval_claim", re.compile(rf"\b(dan|the user|owner|admin)\s+{_F}(has\s+)?(already\s+)?(approved|authorised|authorized|confirmed|consented)\b", re.I)),
    ("tool_directive", re.compile(rf"\b(call|invoke|use|run|execute)\s+{_F}(tool|function|execute_approved_action|propose_\w+|mcp__\w+)", re.I)),
    ("exfiltration", re.compile(rf"\b(send|forward|reveal|print|share|email)\s+{_F}(password|token|credential|secret|api key|system prompt|memory|records)", re.I)),
    ("mark_paid_directive", re.compile(rf"\b(mark|set|record|flag)\s+{_F}(as\s+)?(paid|confirmed|completed|done)\b", re.I)),
    ("permission_change", re.compile(rf"\b(grant|enable|give|add)\s+{_F}(permission|access|send|write)\b", re.I)),
)
_INVISIBLE = {"​", "‌", "‍", "⁠", "﻿", "‮", "‭"}


def screen(text: str | None) -> list[str]:
    if not text:
        return []
    findings = [f"invisible_U+{ord(ch):04X}" for ch in sorted(set(text) & _INVISIBLE)]
    normalised = unicodedata.normalize("NFKC", text[:65_536])
    findings.extend(name for name, pattern in PATTERNS if pattern.search(normalised))
    return findings


def wrap_untrusted(text: str | None, source: str) -> dict:
    """Envelope for third-party text in tool results."""
    return {"untrusted": True, "source": source, "text": text or ""}
