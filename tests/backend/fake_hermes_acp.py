#!/usr/bin/env python3
"""Fake Hermes ACP agent for Hub tests.

Speaks enough of the Agent Client Protocol (newline-delimited JSON-RPC 2.0 on
stdio) to exercise the Hub's ACP client: initialize handshake, session/new with
model state, session/resume, streamed session/update notifications, a
permission request that must be rejected, an unsupported server request that
must fail fast with -32601, and failure modes selected by prompt text.

Prompt-text behaviour:
  "die"          exit(3) mid-turn (process death diagnostics)
  "error-turn"   answer session/prompt with a JSON-RPC error
  "slow-turn"    sleep 10s before finishing (turn timeout)
  "unsupported"  send an fs/read_text_file request before finishing
  anything else  normal turn including a permission request
"""

from __future__ import annotations

import json
import os
import sys
import time

AGENT_NAME = "fake-hermes"
AGENT_VERSION = "9.9.9"


def send(frame: dict) -> None:
    sys.stdout.write(json.dumps(frame, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    send({"jsonrpc": "2.0", "method": method, "params": params})


def update(session_id: str, payload: dict) -> None:
    notify("session/update", {"sessionId": session_id, "update": payload})


def chunk(session_id: str, kind: str, text: str) -> None:
    update(
        session_id,
        {
            "sessionUpdate": kind,
            "messageId": "msg_agent_1",
            "content": {"type": "text", "text": text},
        },
    )


def finish_turn(rid: str, stop_reason: str = "end_turn") -> None:
    send(
        {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "stopReason": stop_reason,
                "usage": {"inputTokens": 12, "outputTokens": 34, "totalTokens": 46},
            },
        }
    )


def main() -> None:
    if os.environ.get("FAKE_HERMES_SILENT") == "1":
        # Stay alive but never speak: exercises the Hub's handshake timeout.
        # FAKE_HERMES_STDERR_CANARY lets a test assert that whatever this
        # process writes to stderr (which the Hub captures verbatim as
        # stderr_tail) is redacted before it reaches an HTTP/SSE response.
        canary = os.environ.get("FAKE_HERMES_STDERR_CANARY")
        if canary:
            sys.stderr.write(f"fake-hermes stderr: silent mode; leaked={canary}\n")
        else:
            sys.stderr.write("fake-hermes stderr: silent mode\n")
        sys.stderr.flush()
        time.sleep(3600)
        return
    sys.stderr.write("fake-hermes stderr: started\n")
    sys.stderr.flush()
    session_id = "sess_fake_1"
    pending_prompt_rid = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(frame, dict):
            continue

        if "method" not in frame:
            # A response to a server-to-client request this fake sent earlier.
            rid = frame.get("id")
            if rid == "perm-1":
                result = frame.get("result") or {}
                outcome_obj = result.get("outcome") or {}
                outcome = outcome_obj.get("outcome", "<no outcome>")
                option_id = outcome_obj.get("optionId")
                label = f"{outcome}:{option_id}" if option_id else outcome
                chunk(session_id, "agent_message_chunk", f"[permission {label}]")
                update(
                    session_id,
                    {"sessionUpdate": "tool_call", "toolCallId": "call_1",
                     "title": "Read notes", "kind": "read", "status": "pending"},
                )
                update(
                    session_id,
                    {"sessionUpdate": "tool_call_update", "toolCallId": "call_1",
                     "status": "completed"},
                )
                notify(
                    "session/update",
                    {
                        "sessionId": session_id,
                        "update": {"sessionUpdate": "usage_update", "used": 100, "size": 2000},
                    },
                )
                finish_turn(pending_prompt_rid)
                pending_prompt_rid = None
            elif rid == "unsupported-1":
                code = (frame.get("error") or {}).get("code")
                chunk(session_id, "agent_message_chunk", f"[unsupported {code}]")
                finish_turn(pending_prompt_rid)
                pending_prompt_rid = None
            continue

        method = frame["method"]
        params = frame.get("params") or {}
        rid = frame.get("id")
        if method == "initialize":
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": 1,
                        "agentInfo": {"name": AGENT_NAME, "version": AGENT_VERSION},
                        "agentCapabilities": {
                            "loadSession": True,
                            "sessionCapabilities": {"resume": {}},
                        },
                        "authMethods": [],
                    },
                }
            )
        elif method == "session/new":
            if os.environ.get("FAKE_HERMES_SILENT_SESSION_NEW") == "1":
                # Handshake completes normally, then this agent goes quiet
                # instead of answering session/new: exercises the Hub's
                # session/new timeout path (distinct from the handshake
                # timeout, which FAKE_HERMES_SILENT covers).
                time.sleep(3600)
                continue
            if os.environ.get("FAKE_HERMES_REFUSE_SESSION") == "1":
                # FAKE_HERMES_REFUSE_SESSION_SECRET lets a test assert that a
                # credential-shaped string embedded in the agent's own error
                # "data.details" (as a misbehaving provider might produce) is
                # redacted before it reaches the agentError field of the
                # HERMES_SESSION_REFUSED response.
                details = os.environ.get("FAKE_HERMES_REFUSE_SESSION_SECRET") or (
                    "No LLM provider configured. Run `hermes model` "
                    "to select a provider, or run `hermes setup` "
                    "for first-time configuration."
                )
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {
                            "code": -32603,
                            "message": "Internal error",
                            "data": {"details": details},
                        },
                    }
                )
                continue
            session_id = "sess_fake_" + str(abs(hash(params.get("cwd", ""))) % 10000)
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "sessionId": session_id,
                        "models": {
                            "currentId": "ollama:qwen3.5:4b",
                            "models": [{"id": "ollama:qwen3.5:4b", "name": "qwen3.5:4b"}],
                        },
                    },
                }
            )
        elif method == "session/resume":
            if os.environ.get("FAKE_HERMES_SILENT_SESSION_RESUME") == "1":
                # Mirrors FAKE_HERMES_SILENT_SESSION_NEW for the resume path:
                # exercises the Hub's session/resume timeout handling.
                time.sleep(3600)
                continue
            session_id = str(params.get("sessionId") or "sess_fake_1")
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "models": {"currentId": "custom:lab:lab-model"},
                    },
                }
            )
        elif method == "session/prompt":
            prompt = params.get("prompt") or [{}]
            text = prompt[0].get("text", "") if isinstance(prompt[0], dict) else ""
            session_id = str(params.get("sessionId") or session_id)
            if text == "die":
                chunk(session_id, "agent_message_chunk", "about to die")
                sys.stderr.write("fake-hermes stderr: dying\n")
                sys.stderr.flush()
                sys.exit(3)
            if text == "error-turn":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32000, "message": "provider exploded"},
                    }
                )
                continue
            if text == "leak-secret":
                # Lets a test assert that a credential-shaped string embedded
                # in a mid-turn provider error is redacted before it reaches
                # the SSE "error" event, the persisted last_error column, and
                # the audit log entry.
                secret = os.environ.get("FAKE_HERMES_TURN_SECRET", "token=CanaryTurnSecret111222333")
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32000, "message": f"provider exploded: {secret}"},
                    }
                )
                continue
            if text == "slow-turn":
                time.sleep(10)
            chunk(session_id, "agent_thought_chunk", "thinking...")
            chunk(session_id, "agent_message_chunk", "Hello ")
            if text == "unsupported":
                pending_prompt_rid = rid
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": "unsupported-1",
                        "method": "fs/read_text_file",
                        "params": {"sessionId": session_id, "path": "/etc/hostname"},
                    }
                )
                continue
            if text == "no-permission":
                chunk(session_id, "agent_message_chunk", "plain turn.")
                finish_turn(rid)
                continue
            chunk(session_id, "agent_message_chunk", "from fake Hermes.")
            pending_prompt_rid = rid
            send(
                {
                    "jsonrpc": "2.0",
                    "id": "perm-1",
                    "method": "session/request_permission",
                    "params": {
                        "sessionId": session_id,
                        "options": [
                            {"optionId": "allow_once", "name": "Allow once", "kind": "allow_once"},
                            {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
                        ],
                    },
                }
            )
        else:
            send(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": f"fake has no method {method}"},
                }
            )
    sys.stderr.write("fake-hermes stderr: stdin closed\n")


if __name__ == "__main__":
    main()
