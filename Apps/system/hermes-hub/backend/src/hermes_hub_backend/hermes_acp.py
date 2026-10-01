"""Minimal Agent Client Protocol (ACP) client for the installed Hermes executable.

Speaks the documented ACP wire format (newline-delimited JSON-RPC 2.0 over stdio,
https://agentclientprotocol.com/protocol/v1/transports) to a ``hermes acp``
subprocess. The client is deliberately small: one session per process, one
prompt turn per request, and fail-closed semantics throughout:

- every agent ``session/request_permission`` is answered by selecting the
  agent's own offered ``reject_once``/``reject_always`` option (or, if none is
  offered, ``{"outcome": "cancelled"}``) so Hermes denies the tool call instead
  of the Hub approving external writes. There is no ``rejected`` value in the
  ACP v1 schema; see the module-level note on ``_handle_server_request`` for
  why. This stops the Hub from granting ACP-mediated approval, but it is not a
  sandbox: see docs/known-limitations.md for what it does and does not
  guarantee about tool side effects;
- any other server-to-client request is answered with JSON-RPC ``-32601`` so the
  agent fails fast instead of waiting out a timeout (the documented contract
  for hosts that do not implement a request method);
- process death, handshake failure and turn timeouts surface as
  :class:`HermesUnavailableError` carrying structured diagnostics (command,
  exit code, stderr tail) rather than a simulated success.

Only identifiers and metadata are surfaced. Message content is streamed to the
caller and never persisted by this module.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import __version__

#: ACP major protocol version this client speaks. The agent's negotiated
#: response is recorded in the session reference and never assumed to match.
ACP_PROTOCOL_VERSION = 1
_STDERR_TAIL_BYTES = 8_000
_NOTIFICATION_QUEUE_LIMIT = 4_096


@dataclass(frozen=True, slots=True)
class HermesDiagnostics:
    """Structured, secret-free explanation of why Hermes is unusable."""

    reason: str
    executable: str
    command: list[str] = field(default_factory=list)
    exit_code: int | None = None
    stderr_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "executable": self.executable,
            "command": self.command,
            "exitCode": self.exit_code,
            "stderrTail": self.stderr_tail,
        }


class HermesUnavailableError(RuntimeError):
    """Hermes could not be started, handshaked or kept alive."""

    def __init__(self, diagnostics: HermesDiagnostics) -> None:
        self.diagnostics = diagnostics
        super().__init__(f"Hermes is unavailable: {diagnostics.reason}")


class HermesProtocolError(RuntimeError):
    """The Hermes agent answered a request with a JSON-RPC error."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.data = data
        details = ""
        if isinstance(data, dict) and isinstance(data.get("details"), str):
            details = f" ({data['details']})"
        elif isinstance(data, str) and data:
            details = f" ({data})"
        super().__init__(f"Hermes ACP error {code}: {message}{details}")


@dataclass(frozen=True, slots=True)
class AgentInfo:
    protocol_version: int | None
    name: str | None
    version: str | None
    #: True when the agent's ``initialize`` response advertised the
    #: ``loadSession`` capability (``session/load``, full-history replay).
    #: The Hub never calls ``session/load`` today; this is recorded for
    #: visibility only.
    load_session: bool
    #: True when the agent's ``initialize`` response advertised
    #: ``agentCapabilities.sessionCapabilities.resume``. Per
    #: https://agentclientprotocol.com/protocol/v1/session-setup, a Client
    #: "MUST NOT attempt to call session/resume" unless this is present.
    #: ``resume_session`` refuses to call the agent at all when this is false.
    can_resume: bool


@dataclass(frozen=True, slots=True)
class AcpSessionInfo:
    session_id: str
    model: str | None
    provider: str | None


@dataclass(frozen=True, slots=True)
class TurnEvent:
    """Normalised streaming event forwarded to the caller."""

    type: str  # delta | tool | permission | usage | progress | done
    # Agent reasoning ("thought") chunks are intentionally not a distinct,
    # text-carrying event type: see _normalise_update's agent_thought_chunk
    # handling, which folds them into a content-free "progress" marker.
    data: dict[str, Any]


def _compact_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


#: Environment variable name prefixes/names that are safe, expected inputs for
#: an unrelated OS process (interpreter discovery, locale, proxying). Every
#: other inherited variable is dropped before the Hermes subprocess is
#: spawned, specifically so Hub secrets (HUB_ADMIN_PASSWORD and friends) are
#: never visible inside Hermes' process environment, its own crash
#: diagnostics, or anything a Hermes tool call might choose to print.
_SUBPROCESS_ENV_ALLOW_NAMES = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "PYTHONIOENCODING",
}
_SUBPROCESS_ENV_ALLOW_PREFIXES = (
    "HERMES_",
    # Lets tests/backend/fake_hermes_acp.py (a synthetic ACP agent used only by
    # the test suite) be steered without special-casing tests in the client
    # that drives the real executable. Real Hermes never defines or reads
    # these names, so this adds no production exposure.
    "FAKE_HERMES_",
)


def _subprocess_environment() -> dict[str, str]:
    """Build a minimal environment for the Hermes subprocess.

    Hub configuration (``HUB_*``, including ``HUB_ADMIN_PASSWORD``) and any
    other ambient secret the Hub process happens to have inherited are
    deliberately excluded. Only an allow-listed set of OS/runtime variables
    plus anything Hermes itself defines (``HERMES_*``, e.g.
    ``HERMES_EXECUTABLE`` downstream tooling) is passed through.
    """
    return {
        name: value
        for name, value in os.environ.items()
        if name in _SUBPROCESS_ENV_ALLOW_NAMES or name.startswith(_SUBPROCESS_ENV_ALLOW_PREFIXES)
    }


def split_model_choice(choice: str) -> tuple[str | None, str | None]:
    """Split a Hermes ``provider:model`` choice id into ``(provider, model)``.

    Custom endpoints use ``custom:<name>:<model>``; bare ids carry no provider.
    """
    parts = choice.split(":")
    if len(parts) == 1:
        return None, parts[0]
    if parts[0] == "custom" and len(parts) >= 3:
        return ":".join(parts[:2]), ":".join(parts[2:])
    return parts[0], ":".join(parts[1:])


def parse_model_state(result: dict[str, Any]) -> tuple[str | None, str | None]:
    """Best-effort extraction of the current model from a session response.

    Hermes returns the ACP model state (``models``) on ``session/new``. The
    shape is parsed defensively; when the agent reports nothing the caller
    records an honest null instead of guessing.
    """
    state = result.get("models") if isinstance(result, dict) else None
    if not isinstance(state, dict):
        return None, None
    current = state.get("currentId") or state.get("current_id")
    if not isinstance(current, str) or not current.strip():
        return None, None
    return split_model_choice(current.strip())


class AcpHermesClient:
    """One ``hermes acp`` subprocess speaking ACP over stdio."""

    def __init__(
        self,
        *,
        executable: str,
        workspace_dir: Path,
        startup_timeout_seconds: float,
        turn_timeout_seconds: float,
    ) -> None:
        self._executable = executable
        self._workspace_dir = workspace_dir
        self._startup_timeout = startup_timeout_seconds
        self._turn_timeout = turn_timeout_seconds
        self._process: asyncio.subprocess.Process | None = None
        self._stdout_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._notifications: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            _NOTIFICATION_QUEUE_LIMIT
        )
        self._stderr_tail: deque[str] = deque()
        self._stderr_bytes = 0
        self._next_id = 0
        self._closed = False
        self._death: HermesDiagnostics | None = None
        self.agent_info: AgentInfo | None = None

    # -- lifecycle ---------------------------------------------------------

    def resolve_executable(self) -> str:
        """Resolve the configured executable or raise with honest diagnostics."""
        candidate = self._executable
        if os.sep in candidate or (os.altsep and os.altsep in candidate):
            path = Path(candidate)
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
            raise HermesUnavailableError(
                HermesDiagnostics(
                    reason=(
                        f"configured Hermes executable '{candidate}' is not an executable file"
                    ),
                    executable=candidate,
                    command=[candidate, "acp"],
                )
            )
        resolved = shutil.which(candidate)
        if resolved is None:
            raise HermesUnavailableError(
                HermesDiagnostics(
                    reason=(
                        f"Hermes executable '{candidate}' was not found on PATH; "
                        "install Hermes or set HERMES_EXECUTABLE"
                    ),
                    executable=candidate,
                    command=[candidate, "acp"],
                )
            )
        return resolved

    async def start(self) -> AgentInfo:
        """Spawn the subprocess and complete the ACP initialize handshake."""
        resolved = self.resolve_executable()
        command = [resolved, "acp"]
        try:
            self._process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=_subprocess_environment(),
            )
        except OSError as error:
            raise HermesUnavailableError(
                HermesDiagnostics(
                    reason=f"could not start Hermes: {error}",
                    executable=self._executable,
                    command=command,
                )
            ) from error
        assert self._process.stdout is not None and self._process.stderr is not None
        self._stdout_task = asyncio.create_task(
            self._read_stdout(self._process.stdout), name="hermes-acp-stdout"
        )
        self._stderr_task = asyncio.create_task(
            self._read_stderr(self._process.stderr), name="hermes-acp-stderr"
        )
        try:
            result = await self._request(
                "initialize",
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {},
                    "clientInfo": {"name": "hermes-hub", "version": __version__},
                },
                timeout=self._startup_timeout,
            )
        except HermesProtocolError as error:
            await self.aclose()
            raise HermesUnavailableError(
                HermesDiagnostics(
                    reason=f"Hermes rejected the ACP handshake: {error}",
                    executable=self._executable,
                    command=command,
                    stderr_tail=self.stderr_tail(),
                )
            ) from error
        except (asyncio.TimeoutError, HermesUnavailableError) as error:
            await self.aclose()
            reason = (
                f"Hermes did not complete the ACP handshake within {self._startup_timeout:g}s"
                if isinstance(error, asyncio.TimeoutError)
                else str(error)
            )
            raise HermesUnavailableError(
                HermesDiagnostics(
                    reason=reason,
                    executable=self._executable,
                    command=command,
                    exit_code=self._process.returncode if self._process else None,
                    stderr_tail=self.stderr_tail(),
                )
            ) from error
        capabilities = result.get("agentCapabilities") or {}
        agent = result.get("agentInfo") or {}
        protocol_version = result.get("protocolVersion")
        session_capabilities = capabilities.get("sessionCapabilities")
        self.agent_info = AgentInfo(
            protocol_version=protocol_version if isinstance(protocol_version, int) else None,
            name=agent.get("name") if isinstance(agent.get("name"), str) else None,
            version=agent.get("version") if isinstance(agent.get("version"), str) else None,
            load_session=bool(capabilities.get("loadSession")),
            can_resume=isinstance(session_capabilities, dict) and "resume" in session_capabilities,
        )
        return self.agent_info

    async def aclose(self) -> None:
        """Terminate the subprocess and stop reader tasks."""
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is not None and process.returncode is None:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()
        for task in (self._stdout_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    # -- ACP methods -------------------------------------------------------

    async def new_session(self) -> AcpSessionInfo:
        result = await self._request(
            "session/new",
            {"cwd": str(self._workspace_dir), "mcpServers": []},
            timeout=self._startup_timeout,
        )
        session_id = result.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise HermesProtocolError(-32603, "agent returned no sessionId for session/new")
        provider, model = parse_model_state(result)
        return AcpSessionInfo(session_id=session_id, model=model, provider=provider)

    async def resume_session(self, session_id: str) -> tuple[str | None, str | None]:
        """Reattach to a persisted Hermes session without replaying history.

        Hermes' documented resume behaviour creates a fresh session when the
        stored id is gone; the Hub records that limitation honestly instead of
        claiming continuity. Returns the ``(provider, model)`` the resumed
        session reports, when available.

        Per https://agentclientprotocol.com/protocol/v1/session-setup, a
        Client "MUST NOT attempt to call session/resume" unless the agent's
        ``initialize`` response advertised
        ``agentCapabilities.sessionCapabilities.resume``. ``start()`` must be
        awaited first so that capability is known; if it was not advertised
        this raises :class:`HermesProtocolError` instead of sending a request
        the agent never declared support for.
        """
        if self.agent_info is None or not self.agent_info.can_resume:
            raise HermesProtocolError(
                -32601,
                "agent does not advertise sessionCapabilities.resume; "
                "refusing to call session/resume",
            )
        result = await self._request(
            "session/resume",
            {"sessionId": session_id, "cwd": str(self._workspace_dir), "mcpServers": []},
            timeout=self._startup_timeout,
        )
        self._drain_notifications()
        return parse_model_state(result)

    async def stream_prompt(self, session_id: str, message: str):
        """Run one prompt turn, yielding :class:`TurnEvent` objects until done.

        The prompt response is funnelled through the same notification queue as
        the streamed updates, so a turn that produces no notifications at all
        still terminates deterministically.
        """
        response_future = self._send_request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": message}]},
        )
        watcher = asyncio.create_task(self._watch_turn(response_future))
        try:
            async with asyncio.timeout(self._turn_timeout):
                while True:
                    item = await self._notifications.get()
                    if item is None:
                        raise HermesUnavailableError(
                            self._death
                            or HermesDiagnostics(
                                reason="Hermes process ended during the turn",
                                executable=self._executable,
                            )
                        )
                    if "__turn_result" in item:
                        outcome = item["__turn_result"]
                        break
                    for event in self._normalise_update(item):
                        yield event
        except asyncio.TimeoutError as error:
            self._send_notification("session/cancel", {"sessionId": session_id})
            await self.aclose()
            raise HermesUnavailableError(
                HermesDiagnostics(
                    reason=f"Hermes turn timed out after {self._turn_timeout:g}s",
                    executable=self._executable,
                    command=[self._executable, "acp"],
                    stderr_tail=self.stderr_tail(),
                )
            ) from error
        finally:
            watcher.cancel()
        if "__acp_error" in outcome:
            failure = outcome["__acp_error"]
            raise HermesProtocolError(
                failure.get("code", -32603),
                str(failure.get("message", "prompt turn failed")),
                failure.get("data"),
            )
        yield TurnEvent(
            "done",
            {
                "stopReason": outcome.get("stopReason"),
                "usage": outcome.get("usage") if isinstance(outcome.get("usage"), dict) else None,
            },
        )

    # -- internals ---------------------------------------------------------

    async def _watch_turn(self, future: asyncio.Future) -> None:
        """Forward the prompt response into the notification queue."""
        try:
            result = await future
        except asyncio.CancelledError:
            return
        except HermesProtocolError as error:
            payload = {
                "__acp_error": {"code": error.code, "message": str(error), "data": error.data}
            }
        except Exception as error:  # process death or transport failure
            payload = {"__acp_error": {"code": -32603, "message": str(error)}}
        else:
            payload = result if isinstance(result, dict) else {}
        self._enqueue_notification({"__turn_result": payload})

    def _send_request(self, method: str, params: dict[str, Any]) -> asyncio.Future:
        self._next_id += 1
        request_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future
        self._write_frame(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )
        return future

    async def _request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        future = self._send_request(method, params)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            future.cancel()
            raise

    def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        self._write_frame({"jsonrpc": "2.0", "method": method, "params": params})

    def _write_frame(self, frame: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.returncode is not None:
            raise HermesUnavailableError(
                self._death
                or HermesDiagnostics(
                    reason="Hermes process is not running", executable=self._executable
                )
            )
        payload = _compact_json(frame)
        process.stdin.write(payload.encode("utf-8") + b"\n")

    async def _read_stdout(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(frame, dict):
                continue
            try:
                await self._handle_frame(frame)
            except HermesUnavailableError:
                break
        await self._mark_death("Hermes process closed the ACP stream")

    async def _handle_frame(self, frame: dict[str, Any]) -> None:
        if "method" in frame:
            if "id" in frame:
                await self._handle_server_request(frame)
            else:
                self._enqueue_notification(frame.get("params") or {})
            return
        request_id = frame.get("id")
        future = self._pending.pop(request_id, None) if isinstance(request_id, int) else None
        if future is None or future.done():
            return
        if "error" in frame:
            error = frame["error"] or {}
            future.set_exception(
                HermesProtocolError(
                    error.get("code", -32603),
                    str(error.get("message", "unknown ACP error")),
                    error.get("data"),
                )
            )
        else:
            future.set_result(frame.get("result") or {})

    async def _handle_server_request(self, frame: dict[str, Any]) -> None:
        """Answer agent questions. Approvals are rejected; the rest fail fast.

        The ACP schema (https://agentclientprotocol.com/protocol/v1/schema
        RequestPermissionOutcome) only defines two outcomes: ``selected``
        (carrying one of the agent's own ``optionId`` values) and
        ``cancelled``. There is no ``rejected`` outcome value; sending one
        would be an invalid response a conformant agent could reject or
        misinterpret. To deny fail-closed we therefore select whichever
        offered option is tagged ``reject_once``/``reject_always`` (the
        agent's own explicit "do not do this" choice), falling back to
        ``cancelled`` only if the agent offered no reject-shaped option.
        """
        method = str(frame.get("method") or "")
        request_id = frame.get("id")
        params = frame.get("params") or {}
        if method == "session/request_permission":
            options = params.get("options") if isinstance(params.get("options"), list) else []
            valid_options = [option for option in options if isinstance(option, dict)]
            reject_option = next(
                (
                    option
                    for option in valid_options
                    if option.get("kind") in {"reject_once", "reject_always"}
                ),
                None,
            )
            if reject_option is not None and isinstance(reject_option.get("optionId"), str):
                outcome: dict[str, Any] = {
                    "outcome": "selected",
                    "optionId": reject_option["optionId"],
                }
                outcome_label = "selected:" + reject_option["optionId"]
            else:
                # No reject-shaped option was offered; cancelling the turn is
                # the only other standard way to decline every option.
                outcome = {"outcome": "cancelled"}
                outcome_label = "cancelled"
            self._enqueue_notification(
                {"permissionRequest": valid_options, "permissionOutcome": outcome_label}
            )
            result: dict[str, Any] | None = {"outcome": outcome}
        else:
            result = None
        response: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id}
        if result is None:
            response["error"] = {
                "code": -32601,
                "message": f"Hermes Hub does not implement '{method}' requests",
            }
        else:
            response["result"] = result
        self._write_frame(response)

    def _enqueue_notification(self, params: dict[str, Any]) -> None:
        try:
            self._notifications.put_nowait(params)
        except asyncio.QueueFull:
            self._notifications.get_nowait()
            self._notifications.put_nowait(params)

    def _drain_notifications(self) -> None:
        while not self._notifications.empty():
            if self._notifications.get_nowait() is None:
                self._notifications.put_nowait(None)
                break

    def _normalise_update(self, params: dict[str, Any]) -> list[TurnEvent]:
        if "permissionRequest" in params:
            options = params["permissionRequest"]
            names = [
                str(option.get("name") or option.get("optionId") or "option")
                for option in options
                if isinstance(option, dict)
            ]
            outcome = str(params.get("permissionOutcome") or "cancelled")
            return [TurnEvent("permission", {"options": names, "outcome": outcome})]
        update = params.get("update")
        if not isinstance(update, dict):
            return []
        kind = str(update.get("sessionUpdate") or "")
        content = update.get("content")
        text = content.get("text") if isinstance(content, dict) else None
        if kind == "agent_message_chunk" and isinstance(text, str):
            return [TurnEvent("delta", {"text": text})]
        if kind == "agent_thought_chunk" and isinstance(text, str):
            # The agent's raw chain-of-thought is not forwarded verbatim to the
            # caller: only an observable "the agent is thinking" progress
            # marker is. This avoids leaking a model's internal reasoning
            # (which providers may consider unsuitable for direct display and
            # which may itself contain sensitive intermediate content) through
            # the Hub's user-facing stream.
            return [TurnEvent("progress", {"kind": "thinking"})]
        if kind in {"tool_call", "tool_call_update"}:
            return [
                TurnEvent(
                    "tool",
                    {
                        "toolCallId": update.get("toolCallId"),
                        "title": update.get("title"),
                        "kind": update.get("kind"),
                        "status": update.get("status"),
                    },
                )
            ]
        if kind == "usage_update":
            usage = {key: update.get(key) for key in ("used", "size", "cost") if key in update}
            return [TurnEvent("usage", usage)]
        if not kind:
            return []
        return [TurnEvent("progress", {"kind": kind})]

    async def _read_stderr(self, stream: asyncio.StreamReader) -> None:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").rstrip("\n")
            self._stderr_bytes += len(text)
            self._stderr_tail.append(text)
            while self._stderr_bytes > _STDERR_TAIL_BYTES and len(self._stderr_tail) > 1:
                dropped = self._stderr_tail.popleft()
                self._stderr_bytes -= len(dropped)

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)[-_STDERR_TAIL_BYTES:]

    async def _mark_death(self, reason: str) -> None:
        if self._death is not None:
            return
        exit_code = self._process.returncode if self._process is not None else None
        if exit_code is None and self._process is not None:
            # stdout EOF usually precedes SIGCHLD reaping; wait briefly so the
            # diagnostics can report the real exit code.
            try:
                exit_code = await asyncio.wait_for(self._process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        self._death = HermesDiagnostics(
            reason=reason,
            executable=self._executable,
            command=[self._executable, "acp"],
            exit_code=exit_code,
            stderr_tail=self.stderr_tail(),
        )
        for future in self._pending.values():
            if not future.done():
                future.set_exception(HermesUnavailableError(self._death))
        self._pending.clear()
        self._enqueue_notification(None)
