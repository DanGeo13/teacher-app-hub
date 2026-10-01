from __future__ import annotations

import asyncio
import dataclasses
import shutil
import stat
from pathlib import Path

import pytest

from hermes_hub_backend.hermes_acp import (
    AcpHermesClient,
    HermesProtocolError,
    HermesUnavailableError,
    _subprocess_environment,
    parse_model_state,
    split_model_choice,
)

FAKE_SOURCE = Path(__file__).with_name("fake_hermes_acp.py")
WORKSPACE = Path("/tmp")


@pytest.fixture
def fake_hermes(tmp_path: Path) -> str:
    """Install the fake agent as an executable and return its path."""
    target = tmp_path / "fake-hermes"
    shutil.copy(FAKE_SOURCE, target)
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return str(target)


def make_client(executable: str, **overrides) -> AcpHermesClient:
    options = {
        "executable": executable,
        "workspace_dir": WORKSPACE,
        "startup_timeout_seconds": 10.0,
        "turn_timeout_seconds": 10.0,
    }
    options.update(overrides)
    return AcpHermesClient(**options)


async def collect_turn(client: AcpHermesClient, session_id: str, message: str):
    events = []
    async for event in client.stream_prompt(session_id, message):
        events.append(event)
    return events


def test_split_model_choice_shapes():
    assert split_model_choice("qwen3.5:4b-model") == ("qwen3.5", "4b-model")
    assert split_model_choice("bare-model") == (None, "bare-model")
    assert split_model_choice("custom:lab:lab-model") == ("custom:lab", "lab-model")
    assert split_model_choice("custom:lab:ns:model") == ("custom:lab", "ns:model")


def test_parse_model_state_is_absent_or_defensive():
    assert parse_model_state({}) == (None, None)
    assert parse_model_state({"models": "not-a-dict"}) == (None, None)
    assert parse_model_state({"models": {"models": []}}) == (None, None)
    assert parse_model_state({"models": {"currentId": "ollama:qwen3.5:4b"}}) == (
        "ollama",
        "qwen3.5:4b",
    )


def test_missing_executable_reports_path_diagnostics():
    client = make_client("definitely-not-installed-hermes")

    with pytest.raises(HermesUnavailableError) as raised:
        client.resolve_executable()
    diagnostics = raised.value.diagnostics.to_dict()
    assert "was not found on PATH" in diagnostics["reason"]
    assert diagnostics["executable"] == "definitely-not-installed-hermes"
    assert diagnostics["command"] == ["definitely-not-installed-hermes", "acp"]


def test_bad_executable_path_reports_diagnostics(tmp_path: Path):
    client = make_client(str(tmp_path / "not-there" / "hermes"))

    with pytest.raises(HermesUnavailableError) as raised:
        client.resolve_executable()
    assert "is not an executable file" in raised.value.diagnostics.reason


def test_handshake_and_session_new(fake_hermes: str):
    async def scenario():
        client = make_client(fake_hermes)
        try:
            agent = await client.start()
            session = await client.new_session()
            return agent, session
        finally:
            await client.aclose()

    agent, session = asyncio.run(scenario())
    assert agent.name == "fake-hermes"
    assert agent.version == "9.9.9"
    assert agent.protocol_version == 1
    assert agent.load_session is True
    assert agent.can_resume is True
    assert session.session_id.startswith("sess_fake_")
    assert session.provider == "ollama"
    assert session.model == "qwen3.5:4b"


def test_resume_session_refuses_when_agent_does_not_advertise_the_capability(
    fake_hermes: str,
):
    """ACP v1 says a Client MUST NOT call session/resume unless the agent's
    initialize response advertised sessionCapabilities.resume. The client
    must refuse locally instead of sending a request the agent never declared
    support for."""

    async def scenario():
        client = make_client(fake_hermes)
        try:
            await client.start()
            # Simulate an agent that only advertised loadSession, not resume.
            client.agent_info = dataclasses.replace(client.agent_info, can_resume=False)
            with pytest.raises(HermesProtocolError) as excinfo:
                await client.resume_session("sess_fake_does_not_matter")
            return excinfo.value
        finally:
            await client.aclose()

    error = asyncio.run(scenario())
    assert error.code == -32601
    assert "sessionCapabilities.resume" in str(error)


def test_prompt_streams_deltas_rejects_permission_and_completes(fake_hermes: str):
    async def scenario():
        client = make_client(fake_hermes)
        try:
            await client.start()
            session = await client.new_session()
            events = await collect_turn(client, session.session_id, "hello there")
            return events
        finally:
            await client.aclose()

    events = asyncio.run(scenario())
    types = [event.type for event in events]
    # The agent's raw reasoning ("agent_thought_chunk") is never forwarded as
    # text: it becomes a content-free progress marker instead.
    assert types[0] == "progress"
    assert events[0].data == {"kind": "thinking"}
    deltas = [event.data["text"] for event in events if event.type == "delta"]
    assert "Hello " in deltas
    assert "from fake Hermes." in deltas
    # The fake only continues after the Hub answers the permission request, so
    # this delta proves the Hub selected the agent's own "reject_once" option
    # (the ACP schema has no bare "rejected" outcome value).
    assert "[permission selected:reject_once]" in deltas
    permissions = [event for event in events if event.type == "permission"]
    assert len(permissions) == 1
    assert permissions[0].data["outcome"] == "selected:reject_once"
    assert permissions[0].data["options"] == ["Allow once", "Reject"]
    tools = [event.data for event in events if event.type == "tool"]
    assert tools[0]["toolCallId"] == "call_1"
    assert tools[0]["status"] == "pending"
    assert tools[1]["status"] == "completed"
    usage = [event.data for event in events if event.type == "usage"]
    assert usage[0]["used"] == 100
    done = events[-1]
    assert done.type == "done"
    assert done.data["stopReason"] == "end_turn"
    assert done.data["usage"]["totalTokens"] == 46


def test_unsupported_server_request_fails_fast_with_method_not_found(fake_hermes: str):
    async def scenario():
        client = make_client(fake_hermes)
        try:
            await client.start()
            session = await client.new_session()
            return await collect_turn(client, session.session_id, "unsupported")
        finally:
            await client.aclose()

    events = asyncio.run(scenario())
    deltas = [event.data["text"] for event in events if event.type == "delta"]
    assert "[unsupported -32601]" in deltas
    assert events[-1].type == "done"


def test_process_death_surfaces_structured_diagnostics(fake_hermes: str):
    async def scenario():
        client = make_client(fake_hermes)
        try:
            await client.start()
            session = await client.new_session()
            await collect_turn(client, session.session_id, "die")
        except HermesUnavailableError as error:
            return error.diagnostics
        finally:
            await client.aclose()

    diagnostics = asyncio.run(scenario())
    assert diagnostics.exit_code == 3
    assert "closed the ACP stream" in diagnostics.reason
    assert "fake-hermes stderr: dying" in diagnostics.stderr_tail


def test_protocol_error_is_surfaced(fake_hermes: str):
    async def scenario():
        client = make_client(fake_hermes)
        try:
            await client.start()
            session = await client.new_session()
            await collect_turn(client, session.session_id, "error-turn")
        except HermesProtocolError as error:
            return error
        finally:
            await client.aclose()

    error = asyncio.run(scenario())
    assert error.code == -32000
    assert "provider exploded" in str(error)


def test_turn_timeout_cancels_and_reports(fake_hermes: str):
    async def scenario():
        client = make_client(fake_hermes, turn_timeout_seconds=1.0)
        try:
            await client.start()
            session = await client.new_session()
            await collect_turn(client, session.session_id, "slow-turn")
        except HermesUnavailableError as error:
            return error.diagnostics
        finally:
            await client.aclose()

    diagnostics = asyncio.run(scenario())
    assert "timed out after 1s" in diagnostics.reason


def test_resume_session_reports_model_state(fake_hermes: str):
    async def scenario():
        client = make_client(fake_hermes)
        try:
            await client.start()
            session = await client.new_session()
            provider, model = await client.resume_session(session.session_id)
            return session, provider, model
        finally:
            await client.aclose()

    session, provider, model = asyncio.run(scenario())
    assert provider == "custom:lab"
    assert model == "lab-model"


def test_subprocess_environment_excludes_hub_secrets(monkeypatch):
    """Hub secrets must never be visible inside the Hermes process environment,
    its crash diagnostics, or anything a Hermes tool call might print.
    """
    monkeypatch.setenv("HUB_ADMIN_PASSWORD", "super-secret-should-not-leak")
    monkeypatch.setenv("SOME_AMBIENT_API_KEY", "also-should-not-leak")
    monkeypatch.setenv("HERMES_CUSTOM_CONFIG", "keep-me")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = _subprocess_environment()

    assert "HUB_ADMIN_PASSWORD" not in env
    assert "SOME_AMBIENT_API_KEY" not in env
    assert env["HERMES_CUSTOM_CONFIG"] == "keep-me"
    assert env["PATH"] == "/usr/bin:/bin"


def test_permission_request_selects_the_agents_own_reject_option():
    """The ACP schema has no bare "rejected" outcome: selecting the agent's
    own reject_once/reject_always option is how the Hub denies fail-closed.
    """
    client = make_client("unused-in-this-test")
    written: list[dict] = []
    client._write_frame = written.append  # type: ignore[method-assign]

    asyncio.run(
        client._handle_server_request(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "s1",
                    "options": [
                        {"optionId": "allow_once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
                    ],
                },
            }
        )
    )

    assert written == [
        {
            "jsonrpc": "2.0",
            "id": 7,
            "result": {"outcome": {"outcome": "selected", "optionId": "reject_once"}},
        }
    ]


def test_permission_request_without_a_reject_option_cancels():
    """When the agent offers no reject-shaped option, cancelling the turn is
    the only standard way left to decline every option on the table.
    """
    client = make_client("unused-in-this-test")
    written: list[dict] = []
    client._write_frame = written.append  # type: ignore[method-assign]

    asyncio.run(
        client._handle_server_request(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "session/request_permission",
                "params": {
                    "sessionId": "s1",
                    "options": [
                        {"optionId": "allow_once", "name": "Allow", "kind": "allow_once"},
                        {"optionId": "allow_always", "name": "Always allow", "kind": "allow_always"},
                    ],
                },
            }
        )
    )

    assert written == [
        {"jsonrpc": "2.0", "id": 9, "result": {"outcome": {"outcome": "cancelled"}}}
    ]


def test_unsupported_server_request_returns_method_not_found():
    client = make_client("unused-in-this-test")
    written: list[dict] = []
    client._write_frame = written.append  # type: ignore[method-assign]

    asyncio.run(
        client._handle_server_request(
            {"jsonrpc": "2.0", "id": 3, "method": "fs/read_text_file", "params": {}}
        )
    )

    assert written == [
        {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {
                "code": -32601,
                "message": "Hermes Hub does not implement 'fs/read_text_file' requests",
            },
        }
    ]


def test_handshake_timeout_closes_process(fake_hermes: str, monkeypatch):
    """A silent agent must produce a handshake timeout, not a hang or a leak."""
    monkeypatch.setenv("FAKE_HERMES_SILENT", "1")

    async def scenario():
        client = make_client(fake_hermes, startup_timeout_seconds=0.5)
        try:
            await client.start()
        except HermesUnavailableError as error:
            return error.diagnostics, client
        finally:
            await client.aclose()

    diagnostics, client = asyncio.run(scenario())
    assert "handshake" in diagnostics.reason
    assert client._process is None or client._process.returncode is not None
