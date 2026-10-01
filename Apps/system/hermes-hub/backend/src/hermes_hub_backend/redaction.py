"""Best-effort redaction of credentials/tokens from Hermes diagnostic text.

Hermes session diagnostics (subprocess stderr, ACP protocol error messages and
their ``data`` payloads) originate from a process the Hub does not control:
the installed Hermes executable, its configured model provider, or any tool
Hermes runs. None of that text is trusted. Before any of it reaches an HTTP
response, an SSE event, the Hub's own database, or the audit log, it is
passed through :func:`redact_text` / :func:`redact_json`.

This is pattern matching against known credential/token/password *shapes*
(API key prefixes, ``Authorization`` headers, ``key=value`` assignments,
credentials embedded in a URL). It is a safety net, not a guarantee: a secret
that does not match one of these shapes will not be caught. The only real
protection is to never let a Hermes provider print live credentials to
stderr in the first place (see docs/known-limitations.md).
"""

from __future__ import annotations

import re
from typing import Any

#: Placeholder written over anything a pattern below matches.
REDACTED_PLACEHOLDER = "[redacted]"

#: Hard cap applied by callers after redaction, kept here for a single
#: source of truth across stderr tails, persisted errors and audit entries.
MAX_DIAGNOSTIC_TEXT_LENGTH = 4_000

# Deliberately conservative, named patterns only — matching a known
# credential/token/password *shape*, not a generic "looks random" heuristic.
# A generic entropy/length heuristic would also eat ordinary identifiers
# (session ids, model names, hashes) that are useful for debugging and are
# not secrets.
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)(api[_-]?key|apikey|secret|client[_-]?secret|password|passwd|pwd|"
    r"token|access[_-]?token|refresh[_-]?token|bearer)\b\s*[:=]\s*"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;\"'}\]]+)"
)
_AUTH_HEADER_PATTERN = re.compile(r"(?i)\bauthorization\s*:\s*\S+(?:\s+\S+)?")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}")
# Common provider/cloud key prefixes seen in LLM-provider error text.
_PROVIDER_KEY_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),  # OpenAI-style
    re.compile(r"\bAIza[0-9A-Za-z_-]{10,}\b"),  # Google API key
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub tokens
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),  # JWT
)
# scheme://user:pass@host -> scheme://[redacted]@host
_URL_CREDENTIAL_PATTERN = re.compile(r"([a-zA-Z][a-zA-Z0-9+.\-]*://)[^\s/@]+:[^\s/@]+@")

_ALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    _KEY_VALUE_PATTERN,
    _AUTH_HEADER_PATTERN,
    _BEARER_PATTERN,
    *_PROVIDER_KEY_PATTERNS,
)


def redact_text(text: str | None, *, max_length: int = MAX_DIAGNOSTIC_TEXT_LENGTH) -> str | None:
    """Scrub known credential/token patterns from ``text`` and cap its length.

    Returns ``None`` unchanged (nothing to redact). Redaction always runs
    before truncation so a secret is not left half-matched at the cut point.
    """
    if text is None:
        return None
    redacted = text
    for pattern in _ALL_PATTERNS:
        redacted = pattern.sub(REDACTED_PLACEHOLDER, redacted)
    redacted = _URL_CREDENTIAL_PATTERN.sub(rf"\1{REDACTED_PLACEHOLDER}@", redacted)
    if max_length and len(redacted) > max_length:
        redacted = redacted[:max_length] + "…[truncated]"
    return redacted


def redact_json(value: Any, *, max_length: int = MAX_DIAGNOSTIC_TEXT_LENGTH) -> Any:
    """Recursively apply :func:`redact_text` to every string leaf.

    Used for structures the Hub does not control the shape of, such as a
    JSON-RPC error ``data`` payload an agent chooses to send.
    """
    if isinstance(value, str):
        return redact_text(value, max_length=max_length)
    if isinstance(value, dict):
        return {key: redact_json(item, max_length=max_length) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_json(item, max_length=max_length) for item in value]
    return value
