# Hermes sessions (minimal authentic integration)

This milestone adds the smallest honest Hermes integration that is still real: one
user message per HTTP request, streamed progress events over Server-Sent Events,
and a persisted session reference. No orchestration, no multi-agent hand-off, no
new UI controls beyond a read-only session list on the Maintenance page.

Every session attempt runs against the **installed Hermes executable** over the
documented [Agent Client Protocol](https://agentclientprotocol.com) (ACP) v1
stdio transport. Nothing about a Hermes session is ever simulated: if Hermes, the
model provider or the ACP handshake is unavailable, the Hub reports that state
with diagnostics and records a failed session reference.

## Architecture position

```text
FastAPI Hub
   |-- POST /api/hermes/session            (CSRF, actor-authenticated)
   |-- POST /api/hermes/session/{id}/message
   |-- GET  /api/hermes/sessions           (read-only references)
   |
   |-- hermes_sessions.py  HermesSessionService / HermesSessionStore
   |       |                 session references, per-session turn lock, audit
   |       `-- hermes_acp.py  AcpHermesClient (ACP v1 over stdio)
   |               `-- hermes acp subprocess  (stdout = JSON-RPC, stderr = logs)
   `-- OllamaAdapter (existing probe, read-only) --> Ollama loopback
```

- `hermes_acp.py` owns the wire protocol only: `initialize`, `session/new`,
  `session/prompt`, `session/resume`, notification parsing and honest
  diagnostics when the process dies, refuses or times out.
- `hermes_sessions.py` owns persistence and policy: one turn at a time per
  session (409 `HERMES_SESSION_BUSY` otherwise), `hermes_sessions` table rows,
  audit events, and the SSE mapping.
- Hermes-owned state (its own `~/.hermes/state.db`, checkpoints, secrets) is
  **never opened** by the Hub. The Hub stores only references and metadata.
- The Hermes subprocess is launched with a **curated environment**, not the
  Hub's own `os.environ`: only `HERMES_*`-prefixed variables and an explicit
  allow-list of ordinary process variables (`PATH`, `HOME`, `LANG`, `TMPDIR`,
  and similar) are passed through. This keeps Hub-only secrets such as
  `HUB_ADMIN_PASSWORD` and session/CSRF signing material out of the agent
  process's environment, even though that process runs under the same OS user
  as the Hub.
- The ACP session `cwd` defaults to a directory dedicated to Hermes
  (`$HUB_DATA_DIR/hermes-workspace`), never the Hub's own data directory, so a
  misbehaving or compromised Hermes tool call cannot read the Hub's database,
  config or secrets merely by resolving a relative path. This is a
  best-effort default, not a sandbox — see
  [known-limitations.md](known-limitations.md).

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `HERMES_EXECUTABLE` | `hermes` | Hermes executable to launch for `hermes acp`. An absolute path is recommended when Hermes lives in a virtual environment. |
| `HUB_HERMES_WORKSPACE_DIR` | `$HUB_DATA_DIR/hermes-workspace` | Working directory passed as the ACP session `cwd`. |
| `HUB_HERMES_STARTUP_TIMEOUT_SECONDS` | `30` | Handshake budget for `initialize`. Real Hermes can take over a minute on a cold start — raise this on slow hosts. |
| `HUB_HERMES_TURN_TIMEOUT_SECONDS` | `900` | Budget for one streamed turn before the Hub reports an honest timeout. |

Model provider configuration is **Hermes' own**, not the Hub's: the ACP server
uses whatever provider/model Hermes is configured with (`hermes model`,
`hermes setup`, or a `custom_providers` entry pointing at a local endpoint such
as Ollama). The Hub additionally probes the configured Ollama endpoint
(`OLLAMA_BASE_URL`, default `http://127.0.0.1:11434`) read-only and reports the
result in the first SSE event as `providerContext`, so an operator can see both
halves of the chain. The probe never launches or configures anything.

## API surface

### `POST /api/hermes/session` (CSRF required)

Body: `{"message": "…"}` (1–8000 characters). Starts a **new** Hermes ACP
session and streams the first turn as SSE.

### `POST /api/hermes/session/{id}/message` (CSRF required)

Same body; resumes the referenced Hermes session (ACP `session/resume`) and
streams the next turn. Returns **409 `HERMES_SESSION_BUSY`** if a turn is
already streaming for that session, **404** if the reference does not exist.

Per the [ACP v1 session-setup spec](https://agentclientprotocol.com/protocol/v1/session-setup),
a client must not call `session/resume` unless the agent's `initialize`
response advertised `agentCapabilities.sessionCapabilities.resume`. The Hub
checks this after every fresh handshake and refuses to send `session/resume`
at all when the capability is absent, surfacing that as an honest
**503 `HERMES_SESSION_REFUSED`** instead of sending a request the agent never
declared support for.

### `GET /api/hermes/sessions`

Read-only list of session references (most recent first, capped at 200). No
message content is stored anywhere, so nothing of the conversation can leak
through this endpoint.

### Error states (never simulated)

| Response | Code | When |
|---|---|---|
| 503 | `HERMES_UNAVAILABLE` | executable missing/not executable, handshake timeout, or the process died before/during the turn. `diagnostics` carries executable, argv, exit code and stderr tail. |
| 503 | `HERMES_SESSION_REFUSED` | Hermes itself answered with a JSON-RPC error (typically: **no model provider configured**). `agentError` carries the agent's own error code and data — including Hermes' remediation message. |
| 409 | `HERMES_SESSION_BUSY` | a turn is already active for this session. |

Failed attempts still create a session reference with `status: "failed"` and a
`lastError` string, so the record is honest about what was attempted.

### SSE event stream

The first event is `session` (the persisted reference plus `providerContext`
with the Ollama probe result), then mapped turn events, then exactly one
terminal event:

| SSE event | Payload | Meaning |
|---|---|---|
| `session` | `{session: HermesSessionRecord, providerContext}` | session reference + provider probe |
| `delta` | `{text}` | assistant message chunk |
| `tool` | `{toolCallId, kind, title, content?}` | tool-call lifecycle (`pending`/`completed`/`failed`) |
| `permission` | `{options, outcome}` | tool-permission request; **the Hub always answers it itself** (see below) |
| `usage` | `{used, size}` or usage totals | context/usage update |
| `progress` | `{kind}` | observable progress only — `{"kind": "thinking"}` for agent reasoning and any other agent-reported progress `kind` |
| `done` | `{stopReason, usage}` | turn finished normally |
| `error` | `{message, diagnostics?}` | turn failed; stream ends after this event |

**Raw agent reasoning is never forwarded to the client.** Hermes' ACP
`agent_thought_chunk` notifications carry the agent's free-text chain-of-thought;
the Hub deliberately does not stream that text to the browser (it may be
verbose, speculative or simply not meant for an end user). Instead every such
chunk is collapsed into the same observable `progress` event,
`{"kind": "thinking"}`, so the UI can show "thinking…" without leaking raw
reasoning content. Other ACP progress notifications pass their `kind` through
unchanged.

**Permission requests are answered by the Hub, not the user, and always deny.**
The ACP v1 `RequestPermissionResponse` schema has exactly two valid shapes —
`{"outcome": {"outcome": "selected", "optionId": "<id>"}}` or
`{"outcome": {"outcome": "cancelled"}}` — there is no `"rejected"` value. When
Hermes asks for permission the Hub looks for an offered option whose `kind` is
`reject_once` or `reject_always` and selects it; if the agent offered no reject
option at all, the Hub answers `cancelled`. The `permission` SSE event reports
which of those happened as `outcome: "selected:<optionId>"` or
`outcome: "cancelled"`. This stops the Hub from granting ACP-mediated
*approval*, but answering "no" over ACP does not prevent a tool process from
having already produced a side effect before asking, nor does it sandbox what
the agent can do in its working directory — see
[known-limitations.md](known-limitations.md) for the exact boundary this
integration does and does not provide.

If the stream ends without a stop reason the Hub emits an explicit `error`
event (`turn stream ended before a stop reason was reported`) rather than
pretending the turn completed.

## What is persisted

`hermes_sessions` table (migration `003_hermes_sessions.sql`): Hub session id,
Hermes ACP session id, status (`connecting`/`active`/`failed`), transport,
protocol version, agent name/version, model, provider, executable,
message count, timestamps, last stop reason and last error. **No message
content, transcript or prompt is stored** — a dedicated test asserts the
schema has no such column and that message text never reaches any table,
including the audit log. Audit events: `hermes.session.created`,
`hermes.turn.completed`, `hermes.turn.failed`.

## Running a local Hermes-backed session

1. Install Hermes with the ACP extra and check it:

   ```bash
   python3 -m venv /path/to/hermes-venv
   /path/to/hermes-venv/bin/pip install "hermes-agent[acp]==0.19.0"
   /path/to/hermes-venv/bin/hermes --version
   /path/to/hermes-venv/bin/hermes acp --check   # verifies ACP dependencies
   ```

2. Configure a model provider in Hermes itself (this is Hermes config, not Hub
   config). With a local Ollama serving Qwen:

   ```bash
   ollama pull qwen3.5:4b   # or whichever local model you use
   /path/to/hermes-venv/bin/hermes model           # interactive provider/model pick
   ```

   Or hand-write `~/.hermes/config.yaml` with a `custom_providers` entry whose
   `base_url` points at your Ollama/OpenAI-compatible endpoint.

3. Point the Hub at the executable and start it:

   ```bash
   export HERMES_EXECUTABLE=/path/to/hermes-venv/bin/hermes
   export OLLAMA_BASE_URL=http://127.0.0.1:11434   # read-only providerContext probe
   cd Apps/system/hermes-hub/backend
   .venv/bin/uvicorn hermes_hub_backend.api:app --port 9120
   ```

4. Log in through the frontend, obtain the CSRF token, and stream one session:

   ```bash
   curl -c jar.txt -o login.json -X POST http://127.0.0.1:9120/api/auth/login \
     -H 'Origin: http://127.0.0.1:9120' \
     -H 'Content-Type: application/json' \
     -d '{"password": "<admin password>"}'
   CSRF=$(python3 -c 'import json;print(json.load(open("login.json"))["csrfToken"])')
   curl -b jar.txt -N -X POST http://127.0.0.1:9120/api/hermes/session \
     -H 'Origin: http://127.0.0.1:9120' -H "X-CSRF-Token: $CSRF" \
     -H 'Content-Type: application/json' \
     -d '{"message": "Reply with the single word: ready."}'
   ```

   The response is the SSE stream from the table above; `GET /api/hermes/sessions`
   (or the Maintenance page's read-only "Hermes sessions" card) shows the
   persisted reference afterwards.

### The honest unavailable state

With no Hermes on `PATH`, no provider configured in Hermes, or no reachable
model endpoint you get, respectively:

- `503 HERMES_UNAVAILABLE` with `reason: "Hermes executable '…' was not found
  on PATH; install Hermes or set HERMES_EXECUTABLE"` — plus the failed session
  reference.
- `503 HERMES_SESSION_REFUSED` with the agent's own message, e.g.
  `No LLM provider configured. Run \`hermes model\` to select a provider, or
  run \`hermes setup\` for first-time configuration.`
- an SSE `error` event mid-turn with the agent's failure text or process
  diagnostics (exit code, stderr tail).

None of these are ever dressed up as success.

## Tests

- `tests/backend/test_hermes_acp.py` — adapter unit tests against a synthetic
  ACP agent (`tests/backend/fake_hermes_acp.py`): handshake, session, streamed
  turn, permission rejection, protocol errors, process death, handshake
  timeout, unknown-method answers.
- `tests/backend/test_hermes_sessions.py` — service/HTTP tests: end-to-end SSE
  with the fake agent, follow-up turns, busy-session 409, unavailable 503,
  provider-refusal 503, auth/CSRF, 404, and the no-message-content persistence
  guarantee.
- `tests/backend/test_hermes_integration.py` — real-Hermes integration tests.
  They run only when `HUB_TEST_HERMES_EXECUTABLE` is set (or `hermes` is on
  `PATH`) and otherwise **skip as BLOCKED with the exact reason and how to fix
  it — no session is simulated**. If real Hermes is present but refuses (for
  example no provider configured), the test first asserts the honest failed
  session reference and then skips BLOCKED with the agent's remediation text.

Local full-configuration run used during development (real Hermes 0.19.0 plus a
synthetic local model endpoint standing in for Ollama):

```bash
HUB_TEST_HERMES_EXECUTABLE=/path/to/hermes-venv/bin/hermes \
OLLAMA_BASE_URL=http://127.0.0.1:4567 \
  .venv/bin/pytest tests/backend -q   # 50 passed, including both real-Hermes tests
```
