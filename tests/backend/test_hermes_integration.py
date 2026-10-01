"""Integration test against a real Hermes installation.

This test runs only when a real Hermes executable is available, and marks
itself BLOCKED with an explicit reason otherwise. It never simulates a
session: a real ``hermes acp`` subprocess is started and driven over stdio.

Set ``HUB_TEST_HERMES_EXECUTABLE`` to point at a specific Hermes binary
(for example a virtualenv's ``bin/hermes``). Turn assertions accept both a
completed turn and an honest provider-side failure, because a Hermes install
without a configured model provider is a legitimate, expected state.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import pytest

from hermes_hub_backend.database import Database
from hermes_hub_backend.hermes_acp import HermesProtocolError, HermesUnavailableError
from hermes_hub_backend.hermes_sessions import HermesSessionService
from hermes_hub_backend.settings import Settings

PASSWORD = "synthetic-test-password-1234"


def _hermes_executable() -> str | None:
    override = os.getenv("HUB_TEST_HERMES_EXECUTABLE", "").strip()
    if override:
        return override
    return shutil.which("hermes")


pytestmark = pytest.mark.skipif(
    _hermes_executable() is None,
    reason=(
        "BLOCKED: no Hermes executable is available on this host. Install Hermes "
        "(https://hermes-agent.nousresearch.com) or set HUB_TEST_HERMES_EXECUTABLE; "
        "see docs/hermes-sessions.md for the expected setup. No session is simulated."
    ),
)


def _settings(tmp_path: Path, executable: str) -> Settings:
    return Settings(
        data_dir=tmp_path / "runtime",
        admin_password=PASSWORD,
        cookie_secure=False,
        allowed_origins=("http://127.0.0.1:9121",),
        hermes_executable=executable,
        hermes_workspace_dir=tmp_path / "workspace",
        # A real agent build is slow; give it room but keep it bounded.
        hermes_startup_timeout_seconds=float(os.getenv("HUB_TEST_HERMES_STARTUP_TIMEOUT", "120")),
        hermes_turn_timeout_seconds=float(os.getenv("HUB_TEST_HERMES_TURN_TIMEOUT", "180")),
        ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        runtime_probe_timeout_seconds=2.0,
        frontend_dist=tmp_path / "missing-dist",
    )


def test_real_hermes_acp_session_streams_and_persists(tmp_path: Path):
    executable = _hermes_executable()
    assert executable is not None
    settings = _settings(tmp_path, executable)
    database = Database(settings.database_path)
    service = HermesSessionService(database, settings)

    async def scenario():
        try:
            conversation = await service.open_new(
                "Reply with the single word: ready.", "admin"
            )
        except HermesUnavailableError as error:
            return "unavailable", error.diagnostics.to_dict(), []
        except HermesProtocolError as error:
            return "refused", {"code": error.code, "message": str(error)}, []
        events = []
        async for chunk in service.stream_events(conversation):
            events.append(chunk)
        return "streamed", None, events

    outcome, diagnostics, events = asyncio.run(scenario())

    if outcome == "unavailable":
        pytest.skip(
            "BLOCKED: the installed Hermes could not complete the ACP handshake: "
            + str(diagnostics)
        )
    if outcome == "refused":
        # An authentic refusal (typically: no model provider is configured).
        # The honest-state contract still has to hold: a failed reference with
        # the agent's own remediation, never a simulated session.
        records = service.list()
        assert len(records) == 1
        assert records[0].status == "failed"
        assert records[0].last_error
        assert "No LLM provider configured" in records[0].last_error
        pytest.skip(
            "BLOCKED: Hermes refused the session (no model provider configured): "
            + str(diagnostics)
        )

    kinds = [chunk.splitlines()[0].removeprefix("event: ") for chunk in events]
    assert kinds[0] == "session"
    assert kinds[-1] in {"done", "error"}
    # The persisted reference records real agent metadata, never content.
    records = service.list()
    assert len(records) == 1
    record = records[0]
    assert record.hermes_session_id
    assert record.status in {"active", "failed"}
    if record.status == "active":
        assert record.message_count == 1
        assert record.last_stop_reason in {"end_turn", "refusal", "max_tokens", "cancelled"}
    else:
        assert record.last_error


def test_real_hermes_follow_up_message(tmp_path: Path):
    executable = _hermes_executable()
    assert executable is not None
    settings = _settings(tmp_path, executable)
    database = Database(settings.database_path)
    service = HermesSessionService(database, settings)

    async def scenario():
        conversation = await service.open_new("Reply with: one.", "admin")
        async for _chunk in service.stream_events(conversation):
            pass
        second = await service.open_existing(
            conversation.hub_session_id, "Reply with: two.", "admin"
        )
        async for _chunk in service.stream_events(second):
            pass
        return service.list()

    try:
        records = asyncio.run(scenario())
    except HermesUnavailableError as error:
        pytest.skip(f"BLOCKED: the installed Hermes could not be driven: {error}")
    except HermesProtocolError as error:
        pytest.skip(
            "BLOCKED: Hermes refused the session before the follow-up could run: "
            f"{error}"
        )

    assert len(records) == 1
    assert records[0].message_count == 2
