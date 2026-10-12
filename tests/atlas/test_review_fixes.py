"""Regression tests for issues found in the product-readiness review."""

from __future__ import annotations

import json
import os
import subprocess
import sys

from fastapi.testclient import TestClient

from atlas_helpers import approve
from hermes_hub_backend.api import create_app
from hermes_hub_backend.atlas.telegram_approvals import TelegramApprovalChannel
from hermes_hub_backend.settings import Settings
from atlas_hub_mcp.tools import TOOLS

ORIGIN = "http://testserver"
PASSWORD = "synthetic-test-password-1234"


def _settings(tmp_path):
    return Settings(data_dir=tmp_path / "runtime", admin_password=PASSWORD, cookie_secure=False,
                    allowed_origins=(ORIGIN,), hermes_dashboard_url="http://127.0.0.1:1",
                    hermes_executable="definitely-not-installed-hermes", ollama_base_url="http://127.0.0.1:1",
                    runtime_probe_timeout_seconds=0.2, frontend_dist=tmp_path / "missing-dist")


def _login(client):
    response = client.post("/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD})
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}


def test_obligations_are_paginated_and_filtered_in_sql(atlas):
    for day in range(1, 8):
        atlas.propose_bill(title=f"Bill {day}", payee=f"Payee {day}", due_date=f"2026-10-{day + 12:02d}",
                           amount=day, source_account="gmail_personal", source_type="email")
    first = atlas.list_obligations(kind="bill", limit=3)
    assert [r["title"] for r in first["records"]] == ["Bill 1", "Bill 2", "Bill 3"]
    assert first["nextOffset"] == 3
    last = atlas.list_obligations(kind="bill", limit=3, offset=6)
    assert [r["title"] for r in last["records"]] == ["Bill 7"] and last["nextOffset"] is None


def test_odometer_and_accept_do_not_invalidate_pending_approvals(atlas):
    car = atlas.propose_car_service(title="Logbook", vehicle="Mazda CX-5", due_date="30/11/2026",
                                    due_odometer_km=50000, current_odometer_km=47000,
                                    source_account="gmail_personal", source_type="email")["record"]
    ev = atlas.record_evidence(source_account="gmail_personal", source_type="email", external_ref="b1",
                               action="booking_confirmed", counterparty="Mazda CX-5",
                               occurred_on="05/11/2026")["evidence"]["id"]
    approval = atlas.propose_status_change(record_id=car["id"], to_status="booked", evidence_ids=[ev])["approval"]["id"]
    atlas.record_odometer(vehicle="Mazda CX-5", odometer_km=47250, source_account="telegram:dan")
    atlas.accept_record(car["id"], "dan")
    approve(atlas, approval)
    assert atlas.execute_approved_action(approval)["executed"] is True


def test_no_overdue_alert_for_a_bill_dan_reported_paid(atlas, clock):
    bill = atlas.propose_bill(title="Gas", payee="Jemena", due_date="13/10/2026", amount=64,
                              source_account="gmail_personal", source_type="email")["record"]
    ev = atlas.record_evidence(source_account="gmail_personal", source_type="manual", external_ref="dan",
                               action="payment_reported", counterparty="Jemena", amount=64)["evidence"]["id"]
    atlas.propose_status_change(record_id=bill["id"], to_status="payment_reported", evidence_ids=[ev])
    clock.advance(days=5)
    assert not [a for a in atlas.collect_alerts()["alerts"] if a["recordId"] == bill["id"]]
    action = next(s for s in atlas.build_digest()["sections"] if s["key"] == "action")
    assert any("payment reported" in i["text"] for g in action["groups"] for i in g["items"])


def test_matching_only_considers_the_same_counterparty(atlas):
    atlas.propose_bill(title="Water", payee="Sydney Water", due_date="20/10/2026", amount=90,
                       source_account="gmail_personal", source_type="email")
    other = atlas.propose_bill(title="Gas", payee="Jemena", due_date="20/10/2026", amount=90,
                               source_account="gmail_personal", source_type="email")["record"]
    out = atlas.record_evidence(source_account="gmail_personal", source_type="email", external_ref="j",
                                action="payment_confirmed", counterparty="Jemena", amount=90)
    assert {c["recordId"] for c in out["candidates"]} == {other["id"]}
    nameless = atlas.record_evidence(source_account="gmail_personal", source_type="email", external_ref="n",
                                     action="payment_confirmed", amount=90)
    assert nameless["candidates"] == []


def test_telegram_card_bookkeeping_uses_iso_utc_timestamps(atlas):
    bill = atlas.propose_bill(title="Gas", payee="Jemena", due_date="20/10/2026", amount=64,
                              source_account="gmail_personal", source_type="email")["record"]
    ev = atlas.record_evidence(source_account="gmail_personal", source_type="email", external_ref="r",
                               action="payment_confirmed", counterparty="Jemena", amount=64)["evidence"]["id"]
    atlas.propose_status_change(record_id=bill["id"], to_status="confirmed_paid", evidence_ids=[ev])
    TelegramApprovalChannel(atlas, lambda m, p: {}, dan_user_id=1, chat_id=1).send_pending_cards()
    with atlas.database.read() as c:
        stamp = c.execute("SELECT first_raised_at FROM atlas_alerts WHERE kind = 'approval_card'").fetchone()[0]
    assert stamp.endswith("Z") and "T" in stamp


def test_http_status_codes_match_the_error(tmp_path):
    with TestClient(create_app(_settings(tmp_path))) as client:
        headers = _login(client)
        assert client.get("/api/atlas/obligations?kind=boat").status_code == 400
        assert client.get("/api/atlas/records/BIL-NOPE").status_code == 404
        assert client.post("/api/atlas/records/BIL-NOPE/accept", headers=headers).status_code == 404
        atlas = client.app.state.atlas
        bill = atlas.propose_bill(title="Gas", payee="Jemena", due_date="20/10/2026", amount=64,
                                  source_account="gmail_personal", source_type="email")["record"]
        client.post(f"/api/atlas/records/{bill['id']}/accept", headers=headers)
        assert client.post(f"/api/atlas/records/{bill['id']}/accept", headers=headers).status_code == 409


def test_hub_starts_without_atlas_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_AGENT_DIR", str(tmp_path / "no-such-agent-dir"))
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/health").status_code == 200
        _login(client)
        assert client.get("/api/apps").status_code == 200, "the rest of the Hub keeps working"
        response = client.get("/api/atlas/health")
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "ATLAS_NOT_CONFIGURED"


def test_mcp_server_explains_missing_agent_dir(tmp_path):
    from hermes_hub_backend.database import Database

    Database(tmp_path / "hub.db")
    env = {"PATH": os.environ.get("PATH", ""), "HUB_DATA_DIR": str(tmp_path),
           "ATLAS_AGENT_DIR": str(tmp_path / "missing")}
    proc = subprocess.run([sys.executable, "-m", "atlas_hub_mcp"], input="", capture_output=True, text=True,
                          env=env, timeout=20)
    assert proc.returncode != 0 and "set ATLAS_AGENT_DIR" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_tool_schemas_use_one_json_type_per_property():
    def walk(schema):
        if isinstance(schema, dict):
            assert not isinstance(schema.get("type"), list), schema
            for value in schema.values():
                walk(value)
        elif isinstance(schema, list):
            for value in schema:
                walk(value)

    for tool in TOOLS:
        walk(tool.schema()["inputSchema"])
    json.dumps([tool.schema() for tool in TOOLS])


def test_frontend_unit_script_quotes_its_exclude_glob():
    """Unquoted, the shell expanded e2e/** into several files and Vitest
    treated all but the first as filters, silently skipping every unit test."""
    from pathlib import Path

    package = json.loads((Path(__file__).resolve().parents[2] / "Apps/system/hermes-hub/frontend/package.json").read_text())
    assert package["scripts"]["test"] == "vitest run --exclude 'e2e/**'"
