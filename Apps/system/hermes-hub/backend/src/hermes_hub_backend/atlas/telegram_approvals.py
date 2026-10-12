"""Telegram Approve/Deny buttons for Atlas approvals.

Hermes' Telegram adapter only offers buttons for its own flows, so the Hub
runs a SEPARATE approval bot (its own token; two pollers on one token
conflict). Button presses are accepted only from Dan's Telegram user id in
Dan's chat, and are decided by the same ApprovalService as the dashboard and
companion app. The card shows action, target, full content, evidence and
expiry. The button carries the first 12 hex characters of the payload hash,
so a press can only approve the exact content the card showed.

Run on the host:  python -m hermes_hub_backend.atlas.telegram_approvals
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Protocol

from ..errors import ApprovalError, NotFoundError
from ..redaction import redact_text
from .service import AtlasError, AtlasService, INTERNAL_ACTIONS

CALLBACK_PREFIX = "atl"
HASH_PREFIX_LEN = 12
MAX_MESSAGE = 3900


class Transport(Protocol):
    def __call__(self, method: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def http_transport(token: str, timeout: float = 40.0) -> Transport:
    def call(method: str, payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(redact_text(str(error).replace(token, "[redacted]"))) from None
        if not body.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {redact_text(str(body.get('description')))}")
        return body.get("result", {})

    return call


def format_card(service: AtlasService, approval: dict[str, Any]) -> str:
    view = service.present_approval(approval)
    payload = view["payload"] or {}
    lines = [
        "Atlas needs your approval",
        f"Action: {view['action']}",
        f"Target: {view['target']}",
        f"Summary: {view['summary'] or '-'}",
        "Full content:",
        json.dumps(payload, ensure_ascii=False, indent=1),
    ]
    evidence = payload.get("evidenceIds") or []
    if evidence:
        lines.append("Evidence: " + ", ".join(evidence))
    lines.append(f"Expires: {view['expiresLocal']} (Sydney)")
    lines.append(f"Content hash: {view['contentHash'][:HASH_PREFIX_LEN]}")
    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE else text[:MAX_MESSAGE] + "\n…(open the dashboard for the full content)"


class TelegramApprovalChannel:
    def __init__(self, service: AtlasService, transport: Transport, *, dan_user_id: int, chat_id: int,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.service = service
        self.transport = transport
        self.dan_user_id = int(dan_user_id)
        self.chat_id = int(chat_id)
        self.offset = 0
        self.sleep = sleep

    # ------------------------------------------------------------ outbound

    def send_pending_cards(self) -> list[str]:
        """Send one card per pending approval that has not had one yet."""
        sent = []
        with self.service.database.read() as connection:
            rows = [dict(r) for r in connection.execute(
                "SELECT * FROM approvals WHERE status = 'pending' AND payload_json IS NOT NULL ORDER BY created_at"
            ).fetchall()]
            done = {r["dedupe_key"] for r in connection.execute(
                "SELECT dedupe_key FROM atlas_alerts WHERE kind = 'approval_card'"
            ).fetchall()}
        for row in rows:
            key = f"approval_card:{row['id']}"
            if key in done:
                continue
            prefix = row["content_hash"][:HASH_PREFIX_LEN]
            self.transport("sendMessage", {
                "chat_id": self.chat_id,
                "text": format_card(self.service, row),
                "reply_markup": {"inline_keyboard": [[
                    {"text": "✅ Approve", "callback_data": f"{CALLBACK_PREFIX}:a:{row['id']}:{prefix}"},
                    {"text": "❌ Deny", "callback_data": f"{CALLBACK_PREFIX}:d:{row['id']}:{prefix}"},
                ]]},
            })
            with self.service.database.write() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO atlas_alerts(dedupe_key, record_id, kind, severity, message, "
                    "first_raised_at, last_delivered_at, delivered_count) VALUES (?, NULL, 'approval_card', "
                    "'info', ?, datetime('now'), datetime('now'), 1)",
                    (key, row["summary"] or row["action"]),
                )
            sent.append(row["id"])
        return sent

    # ------------------------------------------------------------- inbound

    def handle_callback(self, callback: dict[str, Any]) -> str:
        """Decide one button press. Returns the text shown back to Dan."""
        data = str(callback.get("data", ""))
        sender = (callback.get("from") or {}).get("id")
        chat = ((callback.get("message") or {}).get("chat") or {}).get("id")
        outcome = self._decide(data, sender, chat)
        try:
            self.transport("answerCallbackQuery", {"callback_query_id": callback.get("id"), "text": outcome[:190]})
            message = callback.get("message") or {}
            if message.get("message_id") and sender == self.dan_user_id:
                self.transport("editMessageReplyMarkup", {"chat_id": chat, "message_id": message["message_id"],
                                                          "reply_markup": {"inline_keyboard": []}})
        except RuntimeError as error:
            print(f"telegram: could not acknowledge button press: {error}", file=sys.stderr)
        return outcome

    def _decide(self, data: str, sender: Any, chat: Any) -> str:
        if sender != self.dan_user_id or chat != self.chat_id:
            with self.service.database.write() as connection:
                self.service._audit(connection, "approval.telegram_rejected_sender", "telegram", str(sender),
                                    {"chat": chat}, actor="telegram")
            return "Not accepted: only Dan can decide Atlas approvals."
        parts = data.split(":")
        if len(parts) != 4 or parts[0] != CALLBACK_PREFIX or parts[1] not in ("a", "d", "v"):
            return "Not accepted: unknown button."
        _, verb, approval_id, prefix = parts
        if verb == "v":  # live-verification round trip; approves nothing
            with self.service.database.write() as connection:
                self.service._audit(connection, "atlas.verification.telegram_ack", "verification", approval_id,
                                    {"sender": sender}, actor=f"telegram:{sender}")
            return "Round trip confirmed. Thanks, Dan."
        try:
            approval = self.service.approvals.get(approval_id)
        except NotFoundError:
            return "That approval no longer exists."
        actor = f"telegram:{sender}"
        try:
            if verb == "d":
                self.service.approvals.reject(approval_id, actor, "telegram", "denied by button")
                return "Denied. Nothing was done."
            if approval.content_hash[:HASH_PREFIX_LEN] != prefix:
                return "Not accepted: this card does not match the current request. Check the dashboard."
            self.service.approvals.approve(approval_id, approval.action, approval.target, approval.content_hash,
                                           actor, "telegram")
        except ApprovalError as error:
            return f"Not accepted: {error}"
        if approval.action in INTERNAL_ACTIONS:
            try:
                self.service.execute_approved_action(approval_id, actor=actor)
                return "Approved and done."
            except AtlasError as error:
                return f"Approved, but it could not be applied: {error.message}"
        return "Approved. Atlas has no broker for this action yet, so nothing was sent; please do it yourself."

    def poll_once(self, timeout: int = 25) -> int:
        updates = self.transport("getUpdates", {"offset": self.offset, "timeout": timeout,
                                                "allowed_updates": ["callback_query"]})
        handled = 0
        for update in updates or []:
            self.offset = max(self.offset, int(update["update_id"]) + 1)
            if "callback_query" in update:
                self.handle_callback(update["callback_query"])
                handled += 1
        return handled


def main() -> int:  # pragma: no cover - host runner
    from pathlib import Path

    from ..database import Database
    from .host import AtlasHostSettings, read_secret_file

    host = AtlasHostSettings.from_env()
    if not (host.telegram_token_file and host.telegram_dan_user_id and host.telegram_chat_id):
        print("Set ATLAS_TELEGRAM_APPROVAL_TOKEN_FILE, ATLAS_TELEGRAM_DAN_USER_ID and ATLAS_TELEGRAM_CHAT_ID.",
              file=sys.stderr)
        return 2
    data_dir = Path(os.environ["HUB_DATA_DIR"]).expanduser()
    service = AtlasService(Database(data_dir / "hub.db", migrate=False), backup_dir=data_dir / "backups")
    channel = TelegramApprovalChannel(service, http_transport(read_secret_file(host.telegram_token_file)),
                                      dan_user_id=host.telegram_dan_user_id, chat_id=host.telegram_chat_id)
    while True:
        try:
            service.approvals.expire_due()
            channel.send_pending_cards()
            channel.poll_once()
        except RuntimeError as error:
            print(f"telegram approvals: {error}", file=sys.stderr)
            time.sleep(10)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
