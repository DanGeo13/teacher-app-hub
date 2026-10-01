"""Persisted Hermes session references and the minimal session service.

The store keeps only references and metadata (Hub id, Hermes ACP session id,
negotiated protocol version, agent name/version, model/provider, turn outcome).
Message content is streamed to the caller and never written to the Hub
database. Every turn runs in a fresh ``hermes acp`` subprocess; sessions are
continued through Hermes' own persisted session state.

Fail-closed behaviour: the service never approves agent permission requests
(the ACP client rejects them), never enables external writes, and records
honest failure states with diagnostics instead of simulated success.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator

from .audit import AuditService
from .database import Database
from .errors import ConflictError, NotFoundError
from .hermes_acp import (
    AcpHermesClient,
    HermesProtocolError,
    HermesUnavailableError,
    parse_model_state,
)
from .models import HermesSessionRecord
from .runtime import OllamaAdapter
from .settings import Settings
from .time import iso_now

HERMES_TRANSPORT = "acp-stdio"
_ERROR_SNIPPET = 500


def _sse(event: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


class HermesSessionStore:
    """SQLite persistence for Hermes session references."""

    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _record(row) -> HermesSessionRecord:
        return HermesSessionRecord.model_validate(dict(row))

    def create(self, executable: str) -> HermesSessionRecord:
        session_id = str(uuid.uuid4())
        now = iso_now()
        with self.database.write() as connection:
            connection.execute(
                """
                INSERT INTO hermes_sessions(
                    id, hermes_session_id, status, transport, executable,
                    message_count, created_at, updated_at
                ) VALUES (?, NULL, 'connecting', ?, ?, 0, ?, ?)
                """,
                (session_id, HERMES_TRANSPORT, executable, now, now),
            )
            row = connection.execute(
                "SELECT * FROM hermes_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._record(row)

    def get(self, session_id: str) -> HermesSessionRecord:
        with self.database.read() as connection:
            row = connection.execute(
                "SELECT * FROM hermes_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        if row is None:
            raise NotFoundError("Hermes session not found")
        return self._record(row)

    def list(self, limit: int = 50) -> list[HermesSessionRecord]:
        with self.database.read() as connection:
            rows = connection.execute(
                "SELECT * FROM hermes_sessions ORDER BY created_at DESC LIMIT ?",
                (min(limit, 200),),
            ).fetchall()
        return [self._record(row) for row in rows]

    def mark_active(
        self,
        session_id: str,
        *,
        hermes_session_id: str | None = None,
        protocol_version: int | None = None,
        agent_name: str | None = None,
        agent_version: str | None = None,
        model: str | None = None,
        provider: str | None = None,
    ) -> None:
        with self.database.write() as connection:
            connection.execute(
                """
                UPDATE hermes_sessions
                SET status = 'active',
                    hermes_session_id = COALESCE(?, hermes_session_id),
                    protocol_version = COALESCE(?, protocol_version),
                    agent_name = COALESCE(?, agent_name),
                    agent_version = COALESCE(?, agent_version),
                    model = COALESCE(?, model),
                    provider = COALESCE(?, provider),
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    hermes_session_id,
                    protocol_version,
                    agent_name,
                    agent_version,
                    model,
                    provider,
                    iso_now(),
                    session_id,
                ),
            )

    def mark_failed(self, session_id: str, error: str) -> None:
        with self.database.write() as connection:
            connection.execute(
                """
                UPDATE hermes_sessions
                SET status = 'failed', last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (error[:_ERROR_SNIPPET], iso_now(), session_id),
            )

    def record_turn(
        self, session_id: str, *, stop_reason: str | None, error: str | None
    ) -> None:
        now = iso_now()
        with self.database.write() as connection:
            if error is None:
                connection.execute(
                    """
                    UPDATE hermes_sessions
                    SET message_count = message_count + 1,
                        last_stop_reason = ?, last_error = NULL,
                        status = 'active', updated_at = ?
                    WHERE id = ?
                    """,
                    (stop_reason, now, session_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE hermes_sessions
                    SET message_count = message_count + 1,
                        last_stop_reason = ?, last_error = ?,
                        status = 'failed', updated_at = ?
                    WHERE id = ?
                    """,
                    (stop_reason, error[:_ERROR_SNIPPET], now, session_id),
                )


@dataclass
class HermesConversation:
    """A live ACP client bound to one pending user message.

    ``lock`` is already acquired by the time the conversation is constructed
    (see ``HermesSessionService._acquire_turn_lock``); ``stream_events`` is
    responsible for releasing it exactly once, in a ``finally`` block.
    """

    client: AcpHermesClient
    hub_session_id: str
    hermes_session_id: str | None
    message: str
    provider_context: dict[str, Any]
    lock: asyncio.Lock


class HermesSessionService:
    """One real user message per request, streamed, with a persisted reference."""

    def __init__(self, database: Database, settings: Settings) -> None:
        self.settings = settings
        self.store = HermesSessionStore(database)
        self._turn_locks: dict[str, asyncio.Lock] = {}
        # Created eagerly and kept separate from HUB_DATA_DIR's hub.db/backups
        # (see Settings.hermes_workspace): the ACP cwd is only a hint to the
        # agent, not a sandbox, so it must never be the Hub's own secrets/data
        # directory.
        self.settings.hermes_workspace.mkdir(parents=True, exist_ok=True, mode=0o700)

    async def _acquire_turn_lock(self, hub_session_id: str) -> asyncio.Lock:
        """Reserve the single in-flight turn slot for a session, or refuse.

        ``locked()`` and ``acquire()`` run back-to-back with no other
        ``await`` between them, so no other coroutine can observe the lock as
        free and acquire it in between (asyncio only switches tasks at an
        ``await`` point, and ``acquire()`` on an uncontended lock returns
        without suspending). Acquiring here — before any subprocess is
        started — closes a race where two concurrent requests for the same
        session could both pass a "not locked yet" check and run
        ``session/resume``/``session/prompt`` against the same Hermes-side
        session at once.
        """
        lock = self._turn_locks.setdefault(hub_session_id, asyncio.Lock())
        if lock.locked():
            raise ConflictError("a message turn is already streaming for this session")
        await lock.acquire()
        return lock

    def _new_client(self) -> AcpHermesClient:
        return AcpHermesClient(
            executable=self.settings.hermes_executable,
            workspace_dir=self.settings.hermes_workspace,
            startup_timeout_seconds=self.settings.hermes_startup_timeout_seconds,
            turn_timeout_seconds=self.settings.hermes_turn_timeout_seconds,
        )

    async def _provider_context(self) -> dict[str, Any]:
        """Honest provider-side diagnostics; never a substitute for a real turn."""
        probe = await asyncio.to_thread(OllamaAdapter(self.settings).probe)
        return {
            "ollama": {
                "status": probe.status,
                "endpoint": probe.endpoint,
                "version": probe.version,
                "detail": probe.detail,
            }
        }

    async def open_new(self, message: str, actor: str) -> HermesConversation:
        """Create a session reference and connect a fresh Hermes ACP process."""
        record = self.store.create(self.settings.hermes_executable)
        # The id is freshly minted (uuid4) so this never contends, but
        # reserving it the same way as open_existing keeps exactly one release
        # path (stream_events' finally) for every conversation.
        lock = await self._acquire_turn_lock(record.id)
        client = self._new_client()
        try:
            agent, context = await asyncio.gather(
                client.start(), self._provider_context()
            )
            session = await client.new_session()
        except (HermesUnavailableError, HermesProtocolError) as error:
            await client.aclose()
            self.store.mark_failed(record.id, str(error))
            lock.release()
            raise
        self.store.mark_active(
            record.id,
            hermes_session_id=session.session_id,
            protocol_version=agent.protocol_version if agent else None,
            agent_name=agent.name if agent else None,
            agent_version=agent.version if agent else None,
            model=session.model,
            provider=session.provider,
        )
        with self.store.database.write() as connection:
            AuditService.record(
                connection,
                "hermes.session.created",
                actor,
                "hermes_session",
                record.id,
                {
                    "transport": HERMES_TRANSPORT,
                    "protocolVersion": agent.protocol_version if agent else None,
                    "agent": agent.name if agent else None,
                    "model": session.model,
                    "provider": session.provider,
                },
            )
        return HermesConversation(
            client=client,
            hub_session_id=record.id,
            hermes_session_id=session.session_id,
            message=message,
            provider_context=context,
            lock=lock,
        )

    async def open_existing(
        self, hub_session_id: str, message: str, actor: str
    ) -> HermesConversation:
        """Resume a persisted Hermes session in a fresh ACP process."""
        record = self.store.get(hub_session_id)
        if record.hermes_session_id is None:
            raise ConflictError("session has no Hermes reference; create a new session")
        lock = await self._acquire_turn_lock(record.id)
        client = self._new_client()
        try:
            agent, context = await asyncio.gather(
                client.start(), self._provider_context()
            )
            provider, model = await client.resume_session(record.hermes_session_id)
        except (HermesUnavailableError, HermesProtocolError) as error:
            await client.aclose()
            self.store.mark_failed(record.id, str(error))
            lock.release()
            raise
        self.store.mark_active(
            record.id,
            protocol_version=agent.protocol_version if agent else None,
            agent_name=agent.name if agent else None,
            agent_version=agent.version if agent else None,
            model=model,
            provider=provider,
        )
        return HermesConversation(
            client=client,
            hub_session_id=record.id,
            hermes_session_id=record.hermes_session_id,
            message=message,
            provider_context=context,
            lock=lock,
        )

    async def stream_events(self, conversation: HermesConversation) -> AsyncIterator[str]:
        """Stream one prompt turn as Server-Sent Events and persist the outcome.

        ``conversation.lock`` is already held (acquired by
        ``open_new``/``open_existing`` before any subprocess was started) and
        is released exactly once here, including when the caller abandons the
        stream early (FastAPI closes the async generator with
        ``GeneratorExit``, which still runs this ``finally``).
        """
        record = self.store.get(conversation.hub_session_id)
        try:
            yield _sse(
                "session",
                {
                    "session": record.model_dump(by_alias=True),
                    "providerContext": conversation.provider_context,
                },
            )
            stop_reason: str | None = None
            error_text: str | None = None
            try:
                assert conversation.hermes_session_id is not None
                async for event in conversation.client.stream_prompt(
                    conversation.hermes_session_id, conversation.message
                ):
                    yield _sse(event.type, event.data)
                    if event.type == "done":
                        stop_reason = event.data.get("stopReason")
            except HermesUnavailableError as error:
                error_text = str(error)
                yield _sse(
                    "error",
                    {
                        "message": error_text,
                        "diagnostics": error.diagnostics.to_dict(),
                    },
                )
            except Exception as error:  # ACP protocol failure or transport error
                error_text = str(error)
                yield _sse("error", {"message": error_text})
            finally:
                if error_text is None and stop_reason is None:
                    error_text = "turn stream ended before a stop reason was reported"
                self.store.record_turn(
                    conversation.hub_session_id,
                    stop_reason=stop_reason,
                    error=error_text,
                )
                with self.store.database.write() as connection:
                    AuditService.record(
                        connection,
                        "hermes.turn.failed"
                        if error_text is not None
                        else "hermes.turn.completed",
                        "admin",
                        "hermes_session",
                        conversation.hub_session_id,
                        {
                            "stopReason": stop_reason,
                            "error": error_text[:_ERROR_SNIPPET] if error_text else None,
                        },
                    )
                await conversation.client.aclose()
        finally:
            conversation.lock.release()

    def list(self) -> list[HermesSessionRecord]:
        return self.store.list()
