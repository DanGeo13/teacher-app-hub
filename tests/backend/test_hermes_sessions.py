from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hermes_hub_backend.api import create_app
from hermes_hub_backend.errors import ConflictError
from hermes_hub_backend.hermes_sessions import HermesSessionService
from hermes_hub_backend.settings import Settings

ORIGIN = "http://testserver"
PASSWORD = "synthetic-test-password-1234"
FAKE_SOURCE = Path(__file__).with_name("fake_hermes_acp.py")
SECRET_MESSAGE = "secret-synthetic-message-do-not-store"


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
        hermes_startup_timeout_seconds=10.0,
        hermes_turn_timeout_seconds=10.0,
        ollama_base_url="http://127.0.0.1:1",
        runtime_probe_timeout_seconds=0.2,
        frontend_dist=tmp_path / "missing-dist",
    )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        if not lines:
            continue
        event = lines[0].removeprefix("event: ").strip()
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((event, data))
    return events


@pytest.fixture
def hermes_client(tmp_path: Path, fake_hermes: str) -> TestClient:
    with TestClient(create_app(hermes_settings(tmp_path, fake_hermes))) as client:
        yield client


@pytest.fixture
def hermes_authenticated(hermes_client: TestClient):
    response = hermes_client.post(
        "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
    )
    assert response.status_code == 200
    csrf = response.json()["csrfToken"]
    return hermes_client, {"Origin": ORIGIN, "X-CSRF-Token": csrf}


def test_hermes_session_streams_end_to_end(hermes_authenticated):
    client, headers = hermes_authenticated
    response = client.post(
        "/api/hermes/session", headers=headers, json={"message": "hello there"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(response.text)
    kinds = [kind for kind, _ in events]

    assert kinds[0] == "session"
    session_event = events[0][1]
    record = session_event["session"]
    assert record["status"] == "active"
    assert record["transport"] == "acp-stdio"
    assert record["agentName"] == "fake-hermes"
    assert record["agentVersion"] == "9.9.9"
    assert record["provider"] == "ollama"
    assert record["model"] == "qwen3.5:4b"
    assert record["messageCount"] == 0
    assert record["hermesSessionId"].startswith("sess_fake_")
    # Honest provider-side diagnostics travel with the session event.
    assert session_event["providerContext"]["ollama"]["status"] == "unavailable"

    # The agent's raw reasoning never reaches the stream as text, only as a
    # content-free progress marker (see hermes_acp._normalise_update).
    assert "thought" not in kinds
    thinking = next(data for kind, data in events if kind == "progress")
    assert thinking == {"kind": "thinking"}
    assert "permission" in kinds
    permission = next(data for kind, data in events if kind == "permission")
    # The ACP schema has no "rejected" outcome: the Hub selects the agent's
    # own reject_once/reject_always option instead (fail-closed, protocol-valid).
    assert permission["outcome"] == "selected:reject_once"
    deltas = "".join(data["text"] for kind, data in events if kind == "delta")
    assert "Hello from fake Hermes." in deltas
    assert "[permission selected:reject_once]" in deltas
    assert kinds[-1] == "done"
    assert events[-1][1]["stopReason"] == "end_turn"


def test_session_reference_is_persisted_and_listed(hermes_authenticated):
    client, headers = hermes_authenticated
    created = parse_sse(
        client.post("/api/hermes/session", headers=headers, json={"message": "hi"}).text
    )
    session_id = created[0][1]["session"]["id"]

    listing = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN})
    assert listing.status_code == 200
    records = listing.json()
    assert len(records) == 1
    assert records[0]["id"] == session_id
    assert records[0]["status"] == "active"
    assert records[0]["messageCount"] == 1
    assert records[0]["lastStopReason"] == "end_turn"


def test_follow_up_message_continues_session(hermes_authenticated):
    client, headers = hermes_authenticated
    created = parse_sse(
        client.post("/api/hermes/session", headers=headers, json={"message": "hi"}).text
    )
    session_id = created[0][1]["session"]["id"]

    follow_up = client.post(
        f"/api/hermes/session/{session_id}/message",
        headers=headers,
        json={"message": "and again"},
    )
    assert follow_up.status_code == 200
    events = parse_sse(follow_up.text)
    assert events[0][0] == "session"
    assert events[-1][0] == "done"
    assert events[-1][1]["stopReason"] == "end_turn"

    records = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN}).json()
    assert records[0]["messageCount"] == 2


def test_unavailable_hermes_is_an_honest_503(tmp_path: Path):
    settings = hermes_settings(tmp_path, "definitely-not-installed-hermes")
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}
        response = client.post(
            "/api/hermes/session", headers=headers, json={"message": "hello"}
        )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == "HERMES_UNAVAILABLE"
        assert "was not found on PATH" in detail["diagnostics"]["reason"]

        # The failed attempt is still recorded as an honest session reference.
        records = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN}).json()
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert "not found on PATH" in records[0]["lastError"]


def test_hermes_integration_disabled_by_default_refuses_without_starting_anything(
    tmp_path: Path, fake_hermes: str
):
    """HUB_HERMES_ENABLED defaults to False: launching Hermes is opt-in.

    This uses a *working* fake agent (not a missing executable) to prove the
    distinction from HERMES_UNAVAILABLE — the request is refused by policy
    before any subprocess is attempted, and no session reference is created
    at all (an unavailable attempt still creates a failed reference; a
    disabled attempt does not, because nothing was attempted).
    """
    import dataclasses

    settings = dataclasses.replace(hermes_settings(tmp_path, fake_hermes), hermes_enabled=False)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrfToken"]}
        response = client.post(
            "/api/hermes/session", headers=headers, json={"message": "hello"}
        )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == "HERMES_INTEGRATION_DISABLED"
        assert "HUB_HERMES_ENABLED" in detail["message"]

        # Nothing was attempted, so there is no session reference at all —
        # not even a "failed" one.
        records = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN}).json()
        assert records == []

        # The follow-up-message endpoint is refused the same way.
        response = client.post(
            "/api/hermes/session/does-not-matter/message",
            headers=headers,
            json={"message": "hello"},
        )
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "HERMES_INTEGRATION_DISABLED"


def test_default_settings_disable_hermes_integration():
    """HUB_HERMES_ENABLED is not set by Settings' own dataclass default."""
    assert Settings.__dataclass_fields__["hermes_enabled"].default is False


def test_hermes_endpoints_require_authentication_and_csrf(hermes_client: TestClient):
    # Unauthenticated reads are refused before any login cookie exists.
    response = hermes_client.get("/api/hermes/sessions")
    assert response.status_code == 401
    # Unauthenticated mutations are refused by the CSRF/Origin gate first.
    response = hermes_client.post("/api/hermes/session", json={"message": "hello"})
    assert response.status_code == 403
    login = hermes_client.post(
        "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
    )
    assert login.status_code == 200
    csrf = login.json()["csrfToken"]
    response = hermes_client.post(
        "/api/hermes/session",
        headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong-token"},
        json={"message": "hello"},
    )
    assert response.status_code == 403
    response = hermes_client.post(
        "/api/hermes/session",
        headers={"Origin": "https://evil.example", "X-CSRF-Token": csrf},
        json={"message": "hello"},
    )
    assert response.status_code == 403



def test_provider_refusal_is_an_honest_503(tmp_path: Path, fake_hermes: str, monkeypatch):
    """A real Hermes without a configured provider refuses session creation.

    The Hub must surface the agent's own remediation and record a failed
    session reference instead of simulating a session.
    """
    monkeypatch.setenv("FAKE_HERMES_REFUSE_SESSION", "1")
    settings = hermes_settings(tmp_path, fake_hermes)
    with TestClient(create_app(settings)) as client:
        login = client.post(
            "/api/auth/login", headers={"Origin": ORIGIN}, json={"password": PASSWORD}
        )
        headers = {"Origin": ORIGIN, "X-CSRF-Token": login.json()["csrfToken"]}
        response = client.post(
            "/api/hermes/session", headers=headers, json={"message": "hello"}
        )
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["code"] == "HERMES_SESSION_REFUSED"
        assert "No LLM provider configured" in detail["message"]
        assert detail["agentError"]["code"] == -32603

        records = client.get("/api/hermes/sessions", headers={"Origin": ORIGIN}).json()
        assert len(records) == 1
        assert records[0]["status"] == "failed"
        assert "No LLM provider configured" in records[0]["lastError"]


def test_unknown_session_returns_404(hermes_authenticated):
    client, headers = hermes_authenticated
    response = client.post(
        "/api/hermes/session/00000000-0000-0000-0000-000000000000/message",
        headers=headers,
        json={"message": "hello"},
    )
    assert response.status_code == 404


def test_concurrent_turn_on_one_session_conflicts(tmp_path: Path, fake_hermes: str):
    settings = hermes_settings(tmp_path, fake_hermes)
    from hermes_hub_backend.database import Database

    database = Database(settings.database_path)
    service = HermesSessionService(database, settings)

    async def scenario():
        conversation = await service.open_new("first", "admin")
        generator = service.stream_events(conversation)
        await generator.__anext__()  # session event; the turn lock is now held
        try:
            await service.open_existing(conversation.hub_session_id, "second", "admin")
            return "no-conflict"
        except ConflictError:
            return "conflict"
        finally:
            await generator.aclose()

    assert asyncio.run(scenario()) == "conflict"


def test_turn_lock_is_held_before_any_subprocess_starts(tmp_path: Path, fake_hermes: str):
    """The turn lock must be reserved before open_existing starts a second ACP
    subprocess, not only once stream_events begins consuming events: two
    concurrent open_existing calls for the same session must never both reach
    client.start()/resume_session (that would run two Hermes processes
    against the same Hermes-side session at once).
    """
    settings = hermes_settings(tmp_path, fake_hermes)
    from hermes_hub_backend.database import Database

    database = Database(settings.database_path)
    service = HermesSessionService(database, settings)

    async def scenario():
        conversation = await service.open_new("first", "admin")
        # Simulate the first turn having already completed (as stream_events'
        # finally block would): release the lock and close that process.
        await conversation.client.aclose()
        conversation.lock.release()

        outcomes = await asyncio.gather(
            service.open_existing(conversation.hub_session_id, "a", "admin"),
            service.open_existing(conversation.hub_session_id, "b", "admin"),
            return_exceptions=True,
        )
        # Clean up whichever conversation actually acquired the lock.
        for outcome in outcomes:
            if not isinstance(outcome, BaseException):
                await outcome.client.aclose()
                outcome.lock.release()
        return outcomes

    outcomes = asyncio.run(scenario())
    successes = [item for item in outcomes if not isinstance(item, BaseException)]
    conflicts = [item for item in outcomes if isinstance(item, ConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1


def test_session_new_timeout_releases_lock_and_closes_process(
    tmp_path: Path, fake_hermes: str, monkeypatch
):
    """A session/new timeout must be cleaned up like any other open_new()
    failure: release the turn lock and terminate the subprocess. Before the
    hardening in hermes_acp.py/hermes_sessions.py, a bare asyncio.TimeoutError
    from session/new was not one of the two exception types open_new() caught,
    so it propagated past the lock-release/aclose() cleanup entirely.
    """
    import dataclasses

    from hermes_hub_backend.database import Database
    from hermes_hub_backend.hermes_acp import HermesUnavailableError

    monkeypatch.setenv("FAKE_HERMES_SILENT_SESSION_NEW", "1")
    settings = dataclasses.replace(
        hermes_settings(tmp_path, fake_hermes), hermes_startup_timeout_seconds=0.5
    )
    database = Database(settings.database_path)
    service = HermesSessionService(database, settings)

    async def scenario():
        with pytest.raises(HermesUnavailableError) as excinfo:
            await service.open_new("hello", "admin")
        return excinfo.value

    error = asyncio.run(scenario())
    assert "session/new" in error.diagnostics.reason

    records = service.store.list()
    assert len(records) == 1
    assert records[0].status == "failed"
    assert "session/new" in (records[0].last_error or "")

    # The lock for that session id must not still be held.
    lock = service._turn_locks[records[0].id]
    assert not lock.locked()

    # And the turn slot is free, not stuck "busy": a follow-up attempt against
    # the same row fails for a different, expected reason (session/new never
    # produced a Hermes session id to resume) rather than ConflictError's
    # "a message turn is already streaming for this session".
    async def retry():
        with pytest.raises(ConflictError, match="no Hermes reference"):
            await service.open_existing(records[0].id, "hello again", "admin")

    asyncio.run(retry())


def test_cancelled_open_new_releases_lock_and_closes_process(
    tmp_path: Path, fake_hermes: str, monkeypatch
):
    """A browser disconnect (request task cancellation) mid-handshake must
    also release the turn lock and terminate the subprocess, not just the two
    anticipated Hermes* exception types.
    """
    import dataclasses

    from hermes_hub_backend.database import Database

    # Silent agent: client.start()'s initialize request is left pending long
    # enough for this test to cancel the task while it is awaiting that call.
    monkeypatch.setenv("FAKE_HERMES_SILENT", "1")
    settings = dataclasses.replace(
        hermes_settings(tmp_path, fake_hermes), hermes_startup_timeout_seconds=10.0
    )
    database = Database(settings.database_path)
    service = HermesSessionService(database, settings)

    async def scenario():
        task = asyncio.ensure_future(service.open_new("hello", "admin"))
        await asyncio.sleep(0.2)  # let client.start() spawn the process and block on initialize
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    records = service.store.list()
    assert len(records) == 1
    assert records[0].status == "failed"

    lock = service._turn_locks[records[0].id]
    assert not lock.locked()


def test_browser_disconnect_mid_stream_releases_lock_and_closes_process(
    tmp_path: Path, fake_hermes: str
):
    """Simulates a browser disconnect: FastAPI's StreamingResponse closes the
    stream_events async generator (GeneratorExit) when the HTTP client goes
    away mid-turn. The already-held turn lock must be released and the Hermes
    subprocess terminated, not left running/locked until the fake agent
    eventually idles out on its own.
    """
    from hermes_hub_backend.database import Database

    settings = hermes_settings(tmp_path, fake_hermes)
    database = Database(settings.database_path)
    service = HermesSessionService(database, settings)

    async def scenario():
        conversation = await service.open_new("hello", "admin")
        generator = service.stream_events(conversation)
        await generator.__anext__()  # the "session" SSE event; lock is held
        assert conversation.lock.locked()
        process = conversation.client._process
        assert process is not None and process.returncode is None

        # FastAPI calls this when the client disconnects mid-stream.
        await generator.aclose()
        return conversation, process

    conversation, process = asyncio.run(scenario())
    assert not conversation.lock.locked()
    assert process.returncode is not None

    records = service.store.list()
    assert len(records) == 1
    assert records[0].status == "failed"
    assert "ended before a stop reason" in (records[0].last_error or "")


def test_no_message_content_is_persisted(hermes_authenticated, tmp_path: Path):
    client, headers = hermes_authenticated
    response = client.post(
        "/api/hermes/session", headers=headers, json={"message": SECRET_MESSAGE}
    )
    assert response.status_code == 200

    database_path = tmp_path / "runtime" / "hub.db"
    connection = sqlite3.connect(database_path)
    try:
        session_rows = connection.execute("SELECT * FROM hermes_sessions").fetchall()
        audit_rows = connection.execute("SELECT * FROM audit_events").fetchall()
        schema = [description[0] for description in connection.execute(
            "SELECT * FROM hermes_sessions LIMIT 1"
        ).description]
    finally:
        connection.close()
    assert len(session_rows) == 1
    forbidden_columns = {"message", "messages", "content", "transcript", "prompt"}
    assert forbidden_columns.isdisjoint(set(schema))
    for row in session_rows + audit_rows:
        for value in row:
            assert SECRET_MESSAGE not in str(value)
