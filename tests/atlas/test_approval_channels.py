"""Step 4 / 10: one approval engine behind the dashboard, companion app and
Telegram buttons; only authenticated humans can approve; live verification."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas_helpers import AGENT_DIR, approve
from hermes_hub_backend.api import create_app
from hermes_hub_backend.atlas.host import AtlasHostSettings, SecretFileError, read_secret_file
from hermes_hub_backend.atlas.service import AtlasError
from hermes_hub_backend.atlas.telegram_approvals import TelegramApprovalChannel
from hermes_hub_backend.atlas.verification import VerificationRunner
from hermes_hub_backend.errors import ApprovalError
from hermes_hub_backend.settings import Settings

ORIGIN = "http://testserver"
PASSWORD = "synthetic-test-password-1234"
DAN = 111222333
CHAT = 111222333


@pytest.fixture
def hub(tmp_path):
    settings = Settings(data_dir=tmp_path / "runtime", admin_password=PASSWORD, cookie_secure=False,
                        allowed_origins=(ORIGIN,), hermes_dashboard_url="http://127.0.0.1:1",
                        hermes_executable="definitely-not-installed-hermes", ollama_base_url="http://127.0.0.1:1",
                        runtime_probe_timeout_seconds=0.2, frontend_dist=tmp_path / "missing-dist")
    with TestClient(create_app(settings)) as client:
        login = client.post("/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD})
        yield client, {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrfToken"]}, client.app.state.atlas


def _pending_bill_confirmation(atlas):
    bill = atlas.propose_bill(title="Gas", payee="Jemena", due_date="25/10/2026", amount="64.00",
                              reference="G-1", source_account="gmail_personal", source_type="email")["record"]
    ev = atlas.record_evidence(source_account="gmail_personal", source_type="email", external_ref="r-1",
                               action="payment_confirmed", counterparty="Jemena", amount="64.00",
                               occurred_on="20/10/2026", reference="G-1")["evidence"]["id"]
    return bill, atlas.propose_status_change(record_id=bill["id"], to_status="confirmed_paid", evidence_ids=[ev])["approval"]


# --------------------------------------------------------------- dashboard


def test_dashboard_approval_executes_exact_payload(hub):
    client, headers, atlas = hub
    bill, approval = _pending_bill_confirmation(atlas)
    pending = client.get("/api/atlas/approvals").json()["approvals"]
    shown = next(a for a in pending if a["id"] == approval["id"])
    assert shown["payload"]["toStatus"] == "confirmed_paid" and shown["expiresLocal"]
    result = client.post(f"/api/atlas/approvals/{approval['id']}/decide", headers=headers,
                         json={"decision": "approve", "contentHash": shown["contentHash"]})
    assert result.status_code == 200, result.text
    assert result.json()["executed"] is True
    assert client.get(f"/api/atlas/records/{bill['id']}").json()["record"]["status"] == "confirmed_paid"
    again = client.post(f"/api/atlas/approvals/{approval['id']}/decide", headers=headers,
                        json={"decision": "approve", "contentHash": shown["contentHash"]})
    assert again.status_code == 409, "double use is rejected"


def test_dashboard_rejects_changed_payload_and_requires_auth(hub):
    client, headers, atlas = hub
    _, approval = _pending_bill_confirmation(atlas)
    wrong = client.post(f"/api/atlas/approvals/{approval['id']}/decide", headers=headers,
                        json={"decision": "approve", "contentHash": "0" * 64})
    assert wrong.status_code == 409
    assert atlas.approvals.get(approval["id"]).status == "invalidated"
    no_csrf = client.post(f"/api/atlas/approvals/{approval['id']}/decide", headers={"Origin": ORIGIN},
                          json={"decision": "approve", "contentHash": "0" * 64})
    assert no_csrf.status_code == 403
    client.cookies.clear()
    assert client.get("/api/atlas/approvals").status_code == 401


def test_companion_channel_and_rejection(hub):
    client, headers, atlas = hub
    _, approval = _pending_bill_confirmation(atlas)
    denied = client.post(f"/api/atlas/approvals/{approval['id']}/decide", headers=headers,
                         json={"decision": "reject", "contentHash": approval["contentHash"], "channel": "companion"})
    assert denied.status_code == 200 and denied.json()["approval"]["status"] == "rejected"
    bad_channel = client.post(f"/api/atlas/approvals/{approval['id']}/decide", headers=headers,
                              json={"decision": "approve", "contentHash": approval["contentHash"], "channel": "agent"})
    assert bad_channel.status_code == 422


def test_engine_refuses_agent_and_non_human_channels(atlas, new_bill):
    _, approval = _pending_bill_confirmation(atlas)
    record = atlas.approvals.get(approval["id"])
    with pytest.raises(ApprovalError):
        atlas.approvals.approve(record.id, record.action, record.target, record.content_hash, "atlas-agent", "telegram")
    with pytest.raises(ApprovalError):
        atlas.approvals.approve(record.id, record.action, record.target, record.content_hash, "dan", "agent")
    assert atlas.approvals.get(record.id).status == "pending"


def test_companion_audio_upload_and_health_digest_endpoints(hub):
    client, headers, _ = hub
    upload = client.post("/api/atlas/audio", headers=headers, json={
        "sourceDevice": "pixel-9-pro", "recordedAt": "2026-10-12T07:40:00+11:00",
        "chunks": [{"offsetSeconds": 0, "segments": [{"start": 1, "end": 3, "text": "call the plumber", "confidence": 0.9}]}]})
    assert upload.status_code == 201 and upload.json()["status"] == "queued"
    assert client.get("/api/atlas/health").json()["checks"]
    digest = client.get("/api/atlas/digest?channel=voice").json()
    assert "|" not in digest["text"], "voice output has no tables"


# ---------------------------------------------------------------- telegram


class FakeTelegram:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        return {"message_id": len(self.calls)} if method == "sendMessage" else True


def _press(channel, data, user=DAN, chat=CHAT):
    return channel.handle_callback({"id": "cb", "data": data, "from": {"id": user},
                                    "message": {"message_id": 1, "chat": {"id": chat}}})


def test_telegram_card_shows_full_content_and_buttons(atlas):
    _, approval = _pending_bill_confirmation(atlas)
    fake = FakeTelegram()
    channel = TelegramApprovalChannel(atlas, fake, dan_user_id=DAN, chat_id=CHAT)
    assert channel.send_pending_cards() == [approval["id"]]
    assert channel.send_pending_cards() == [], "one card per approval"
    method, payload = fake.calls[0]
    assert method == "sendMessage"
    for needle in ("Action: confirm_paid", "Target: atlas:", "Full content", "confirmed_paid", "Expires:", "Evidence:"):
        assert needle in payload["text"]
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in buttons] == ["✅ Approve", "❌ Deny"]
    assert all(len(b["callback_data"].encode()) <= 64 for b in buttons)


def test_telegram_only_dan_can_approve(atlas):
    bill, approval = _pending_bill_confirmation(atlas)
    channel = TelegramApprovalChannel(atlas, FakeTelegram(), dan_user_id=DAN, chat_id=CHAT)
    prefix = approval["contentHash"][:12]
    assert "only Dan" in _press(channel, f"atl:a:{approval['id']}:{prefix}", user=999)
    assert "only Dan" in _press(channel, f"atl:a:{approval['id']}:{prefix}", chat=-100)
    assert atlas.approvals.get(approval["id"]).status == "pending"
    assert "does not match" in _press(channel, f"atl:a:{approval['id']}:{'f' * 12}")
    assert "Approved and done" in _press(channel, f"atl:a:{approval['id']}:{prefix}")
    assert atlas.get_record(bill["id"])["record"]["status"] == "confirmed_paid"
    assert "Not accepted" in _press(channel, f"atl:a:{approval['id']}:{prefix}"), "double use rejected"


def test_telegram_deny_and_expired(atlas, clock):
    _, first = _pending_bill_confirmation(atlas)
    channel = TelegramApprovalChannel(atlas, FakeTelegram(), dan_user_id=DAN, chat_id=CHAT)
    assert "Denied" in _press(channel, f"atl:d:{first['id']}:{first['contentHash'][:12]}")
    assert atlas.approvals.get(first["id"]).status == "rejected"
    bill = atlas.propose_bill(title="Phone", payee="Telstra", due_date="28/10/2026", amount="70",
                              source_account="gmail_personal", source_type="email")["record"]
    ev = atlas.record_evidence(source_account="gmail_personal", source_type="email", external_ref="t-1",
                               action="payment_confirmed", counterparty="Telstra", amount="70",
                               occurred_on="27/10/2026")["evidence"]["id"]
    second = atlas.propose_status_change(record_id=bill["id"], to_status="confirmed_paid", evidence_ids=[ev])["approval"]
    clock.advance(hours=25)
    assert "expired" in _press(channel, f"atl:a:{second['id']}:{second['contentHash'][:12]}")
    assert atlas.approvals.get(second["id"]).status == "expired"


# ------------------------------------------------------------ verification


def _host(tmp_path, **overrides):
    values = dict(hermes_python=None, hermes_executable=None, hermes_root=tmp_path / ".hermes",
                  api_server_url="http://127.0.0.1:8642", api_server_key_file=None, telegram_token_file=None,
                  telegram_dan_user_id=None, telegram_chat_id=None)
    values.update(overrides)
    return AtlasHostSettings(**values)


def test_verification_reports_blocked_honestly_off_device(atlas, tmp_path):
    result = VerificationRunner(atlas, _host(tmp_path)).run("t1")
    by_name = {s["name"]: s["status"] for s in result["steps"]}
    assert by_name["Hermes responds (Atlas API server)"] == "BLOCKED"
    assert by_name["Persona loaded"] == "BLOCKED"
    assert by_name["Telegram round trip"] == "BLOCKED"
    assert all(v == "BLOCKED" for k, v in by_name.items() if k.startswith("Skill discoverable"))
    assert len([k for k in by_name if k.startswith("Skill discoverable")]) == 8
    assert by_name["Hub database writable"] == "PASS"
    assert by_name["Approval round trip (synthetic)"] == "PASS"
    assert by_name["Weekly digest dry run (synthetic)"] == "PASS"
    assert result["atlasOperationalOnThisHost"] is False


def test_verification_telegram_round_trip_and_api_failure(atlas, tmp_path):
    token = tmp_path / "token"
    token.write_text("123:synthetic")
    token.chmod(0o600)
    key = tmp_path / "key"
    key.write_text("synthetic-key")
    key.chmod(0o600)
    fake = FakeTelegram()
    channel = TelegramApprovalChannel(atlas, fake, dan_user_id=DAN, chat_id=CHAT)

    def http(method, url, **kwargs):
        raise OSError("connection refused")

    def wait(_seconds):
        _press(channel, "atl:v:t2:x")  # Dan taps Confirm; the bot runner records it

    runner = VerificationRunner(atlas, _host(tmp_path, telegram_token_file=str(token), telegram_dan_user_id=DAN,
                                             telegram_chat_id=CHAT, api_server_key_file=str(key)),
                                http=http, telegram_send=fake, wait=wait)
    by_name = {s["name"]: s for s in runner.run("t2")["steps"]}
    assert by_name["Telegram round trip"]["status"] == "PASS"
    assert by_name["Hermes responds (Atlas API server)"]["status"] == "FAIL"
    assert "synthetic-key" not in json.dumps(by_name)


def test_secret_files_must_be_private(tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("value")
    secret.chmod(0o644)
    with pytest.raises(SecretFileError):
        read_secret_file(secret)
    secret.chmod(0o600)
    assert read_secret_file(secret) == "value"
    with pytest.raises(SecretFileError):
        read_secret_file(tmp_path / "missing")


def test_verification_button_endpoint(hub):
    client, headers, _ = hub
    started = client.post("/api/atlas/verification/run", headers=headers)
    assert started.status_code == 202
    for _ in range(100):
        latest = client.get("/api/atlas/verification/latest").json()
        if latest["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert latest["status"] == "finished"
    assert latest["host"]["android"] is False
    assert latest["verdict"].startswith("Not on-device proof")
