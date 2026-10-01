"""Synthetic-canary coverage for Hermes diagnostic redaction.

These canaries are deliberately credential-*shaped* (so the redaction
patterns under test actually fire) but sized to stay under
scripts/scan-secrets.py's higher thresholds, so this file does not trip the
repository's own secret scanner. None of these values are real credentials.

Each test proves a canary that enters the system through a specific channel
(subprocess stderr, a JSON-RPC protocol error, a mid-turn provider error)
never survives into a specific externally-observable surface: the HTTP JSON
response, the SSE stream body, the persisted `last_error` column, or the
audit log.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hermes_hub_backend.api import create_app
from hermes_hub_backend.hermes_acp import HermesDiagnostics, HermesProtocolError
from hermes_hub_backend.redaction import redact_json, redact_text
from hermes_hub_backend.settings import Settings

ORIGIN = "http://testserver"
PASSWORD = "synthetic-test-password-1234"
FAKE_SOURCE = Path(__file__).with_name("fake_hermes_acp.py")

# Credential-shaped synthetic canaries, sized to match this module's own
# redaction patterns (>=10/20 chars after the prefix) while staying under
# scripts/scan-secrets.py's thresholds (20-30+ chars after the prefix).
CANARY_PASSWORD_ASSIGNMENT = "HUB_ADMIN_PASSWORD=CanaryAdminPasswordValue987"
CANARY_BEARER = "Authorization: Bearer canary.turn.tokenvalue123456"
CANARY_OPENAI_STYLE = "sk-CANARYKEY12"
CANARY_URL_CREDENTIAL = "postgres://user:CanaryDbPass1@db.example.com:5432/app"


@pytest.fixture
def fake_hermes(tmp_path: Path) -> str:
    target = tmp_path / "fake-hermes"
    shutil.copy(FAKE_SOURCE, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(target)


def hermes_settings(tmp_path: Path, executable: str) -> Settings:
    return Settings(
        data_dir=tmp_path / "runtime",
        admin_password=PASSWORD,
        cookie_secure=False,
        allowed_origins=(ORIGIN,),
        hermes_enabled=True,
        hermes_executable=executable,
        hermes_workspace_dir=tmp_path / "workspace",
        hermes_startup_timeout_seconds=1.0,
        hermes_turn_timeout_seconds=10.0,
        ollama_base_url="http://127.0.0.1:1",
        runtime_probe_timeout_seconds=0.2,
        frontend_dist=tmp_path / "missing-dist",
    )


def login(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
    )
    assert response.status_code == 200
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}


def dump_database(database_path: Path) -> str:
    """Every row of every table, stringified, for a single canary-absence check."""
    connection = sqlite3.connect(database_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        blob = []
        for table in tables:
            for row in connection.execute(f"SELECT * FROM {table}").fetchall():
                blob.append(str(row))
        return "\n".join(blob)
    finally:
        connection.close()


# -- Unit coverage for the shared redaction helpers -------------------------


def test_redact_text_scrubs_known_credential_shapes_but_keeps_ordinary_text():
    assert "CanaryAdminPasswordValue987" not in redact_text(CANARY_PASSWORD_ASSIGNMENT)
    assert "canary.turn.tokenvalue123456" not in redact_text(CANARY_BEARER)
    assert "CANARYKEY12" not in redact_text(CANARY_OPENAI_STYLE)
    redacted_url = redact_text(CANARY_URL_CREDENTIAL)
    assert "CanaryDbPass1" not in redacted_url
    assert "db.example.com" in redacted_url  # host is not a secret

    ordinary = "model qwen3.5:4b on provider ollama, session sess_fake_42"
    assert redact_text(ordinary) == ordinary


def test_redact_text_handles_none_and_empty():
    assert redact_text(None) is None
    assert redact_text("") == ""


def test_redact_json_walks_nested_structures():
    payload = {
        "details": CANARY_PASSWORD_ASSIGNMENT,
        "nested": {"token": CANARY_BEARER},
        "list": [CANARY_OPENAI_STYLE, "harmless"],
    }
    redacted = redact_json(payload)
    blob = json.dumps(redacted)
    assert "CanaryAdminPasswordValue987" not in blob
    assert "canary.turn.tokenvalue123456" not in blob
    assert "CANARYKEY12" not in blob
    assert "harmless" in blob


def test_hermes_diagnostics_redacts_at_construction_not_only_at_serialisation():
    diagnostics = HermesDiagnostics(
        reason=f"could not start Hermes: {CANARY_PASSWORD_ASSIGNMENT}",
        executable="hermes",
        stderr_tail=CANARY_BEARER,
    )
    # The attributes themselves are already sanitised, not just to_dict().
    assert "CanaryAdminPasswordValue987" not in diagnostics.reason
    assert "canary.turn.tokenvalue123456" not in diagnostics.stderr_tail
    assert "CanaryAdminPasswordValue987" not in json.dumps(diagnostics.to_dict())


def test_hermes_protocol_error_redacts_message_and_data_at_construction():
    error = HermesProtocolError(
        -32603,
        f"refused: {CANARY_BEARER}",
        data={"details": CANARY_PASSWORD_ASSIGNMENT},
    )
    assert "canary.turn.tokenvalue123456" not in str(error)
    assert "CanaryAdminPasswordValue987" not in json.dumps(error.data)


# -- End-to-end coverage: canary never reaches HTTP/SSE/DB/audit ------------


def test_handshake_stderr_canary_is_redacted_from_the_503_response(
    tmp_path: Path, fake_hermes: str, monkeypatch
):
    """A silent agent's stderr is captured as stderr_tail; it must be
    redacted before it reaches the HERMES_UNAVAILABLE response body."""
    monkeypatch.setenv("FAKE_HERMES_SILENT", "1")
    monkeypatch.setenv("FAKE_HERMES_STDERR_CANARY", CANARY_PASSWORD_ASSIGNMENT)
    settings = hermes_settings(tmp_path, fake_hermes)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/hermes/session", headers=headers, json={"message": "hello"}
        )
        assert response.status_code == 503
        body_text = response.text
        assert "CanaryAdminPasswordValue987" not in body_text
        detail = response.json()["detail"]
        assert detail["code"] == "HERMES_UNAVAILABLE"
        # The diagnostic channel still carries a (redacted) stderr tail, so
        # this is proving redaction, not proving the field was removed.
        assert "leaked=" in detail["diagnostics"]["stderrTail"]

        records = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN}).json()
        assert len(records) == 1
        assert "CanaryAdminPasswordValue987" not in json.dumps(records)

    database_path = tmp_path / "runtime" / "hub.db"
    assert "CanaryAdminPasswordValue987" not in dump_database(database_path)


def test_session_refusal_secret_is_redacted_from_agent_error_and_persistence(
    tmp_path: Path, fake_hermes: str, monkeypatch
):
    """A misbehaving provider's refusal message is agent-controlled text; a
    credential-shaped string inside it must not reach agentError, the
    persisted lastError, or the audit log."""
    monkeypatch.setenv("FAKE_HERMES_REFUSE_SESSION", "1")
    monkeypatch.setenv("FAKE_HERMES_REFUSE_SESSION_SECRET", CANARY_BEARER)
    settings = hermes_settings(tmp_path, fake_hermes)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/hermes/session", headers=headers, json={"message": "hello"}
        )
        assert response.status_code == 503
        assert "canary.turn.tokenvalue123456" not in response.text
        detail = response.json()["detail"]
        assert detail["code"] == "HERMES_SESSION_REFUSED"
        assert "canary.turn.tokenvalue123456" not in json.dumps(detail["agentError"])

        records = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN}).json()
        assert records[0]["status"] == "failed"
        assert "canary.turn.tokenvalue123456" not in json.dumps(records)

    database_path = tmp_path / "runtime" / "hub.db"
    assert "canary.turn.tokenvalue123456" not in dump_database(database_path)


def test_mid_turn_provider_secret_is_redacted_from_sse_db_and_audit(
    tmp_path: Path, fake_hermes: str, monkeypatch
):
    """A canary embedded in a mid-turn JSON-RPC error (as a provider leaking
    a credential into an error message might produce) must not reach the SSE
    error event, the persisted lastError, or the audit event."""
    monkeypatch.setenv("FAKE_HERMES_TURN_SECRET", CANARY_OPENAI_STYLE)
    settings = hermes_settings(tmp_path, fake_hermes)
    with TestClient(create_app(settings)) as client:
        headers = login(client)
        response = client.post(
            "/api/hermes/session", headers=headers, json={"message": "leak-secret"}
        )
        assert response.status_code == 200
        assert "CANARYKEY12" not in response.text

        records = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN}).json()
        assert records[0]["status"] == "failed"
        assert "CANARYKEY12" not in json.dumps(records)

    database_path = tmp_path / "runtime" / "hub.db"
    assert "CANARYKEY12" not in dump_database(database_path)
