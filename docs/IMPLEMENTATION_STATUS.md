# Atlas implementation status

Last updated 12/10/2026. Branch `claude/atlas-operations-agent-7debvp`.

**Atlas is not yet operational.** Nothing has run on the Pixel 7 Pro host.
Everything runnable in CI is built and tested, including against the real
hermes-agent 0.19.0 package in a Linux sandbox. That sandbox is not the
device and does not count as live verification.

Legend: **DONE** (built and tested), **BLOCKED** (cannot be done without the
device or Dan), **NOT BUILT** (deliberately out of scope so far).

## By step

| Step | Item | Status | Evidence |
|---|---|---|---|
| 1 | Hermes mechanisms verified from 0.19.0 source and execution | DONE | [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md) |
| 1 | Termux status rechecked | DONE — **still broken** for a clean PyPI install (Python 3.14 vs `<3.14`; update path issues open) | AGENT_INTEGRATION.md §7 |
| 2 | Persona, config (Australia/Sydney), three-level tool manifest, injection policy | DONE | `agent/atlas/`, `tests/atlas/test_policy.py` |
| 2 | Test fails if a forbidden tool is callable | DONE | `test_no_forbidden_tool_is_callable`, `test_tool_list_contains_no_approval_tool`, Hermes MCP discovery test |
| 3 | atlas-hub stdio MCP server on the Hub database | DONE | `mcp/atlas-hub/`, `tests/atlas/test_mcp_server.py` |
| 3 | No approve/deny, SQL, delete or bypass tool | DONE | tool table test + real Hermes discovery |
| 4 | Approval engine: payload hash, expiry, single use, human-only, audit | DONE | `approvals.py`, `test_eval_core.py`, `test_approval_channels.py` |
| 4 | Dashboard and companion API decide through the engine | DONE | `/api/atlas/approvals/{id}/decide` |
| 4 | Telegram Approve/Deny | DONE in code, **BLOCKED live** | separate approval bot; needs a bot token and Dan's ids on the host |
| 5 | mcp_servers entries with explicit allowlists; credentials by file | DONE (Gmail entries disabled until Dan picks a server) | `atlas_policy.build_hermes_config` |
| 5 | Remote platforms restricted; startup check fails loudly | DONE | `check_policy.py` using Hermes' resolver; `start-atlas-gateway.sh` |
| 5 | Admin-only local profile | DONE (default profile, host terminal only) | ATLAS_AGENT.md |
| 6 | Eight skills with tests | DONE — all **pending Dan's review** | `agent/atlas/skills/`, `skills.lock.json` |
| 7 | Six schedules, catch-up, DST-safe digest | DONE | `schedules.yaml`, `test_schedules.py`, real-Hermes DST simulation |
| 8 | Quiet logging; no change vs failed vs stale; immediate alerts | DONE | `log_check`, `get_check_status`, `collect_alerts` |
| 9 | Evaluation suite in CI with PASS/FAIL/BLOCKED report | DONE | `scripts/run-atlas-evals.py`; CI jobs `test` and `atlas-hermes-compat` |
| 10 | Guided verify button | DONE in code; **live run BLOCKED** | Hub → Atlas → Verify Atlas on this host |
| 11 | Docs | DONE | ATLAS_AGENT.md, TOOLS_AND_SKILLS.md, this file |

## Test results (sandbox, 12/10/2026)

- Backend + Atlas pytest: 236 passed, 9 skipped (skips are the real-Hermes
  and existing integration tests, reported BLOCKED without Hermes).
- Atlas evaluation with real hermes-agent 0.19.0: **PASS 172 · FAIL 0 · BLOCKED 0**.
- Atlas evaluation without Hermes: PASS 165 · FAIL 0 · BLOCKED 7.
- Frontend: unit tests, typecheck and build pass; Playwright e2e 5 passed
  (run against the sandbox's Chromium; the pinned Playwright browser is not
  installed in the sandbox, CI installs it).
- Secret scan passes.

## Live verification on the Pixel 7 host

| Check | Status | Why |
|---|---|---|
| Hermes responds | BLOCKED | Hermes not installed on the device (Termux install broken upstream) |
| Persona loaded | BLOCKED | no Atlas profile on the device |
| Each skill discoverable | BLOCKED | same; skills also await Dan's review |
| Database writable | BLOCKED on device (PASS in sandbox) | Hub not yet running on the device |
| Telegram round trip | BLOCKED | approval bot token and Dan's Telegram ids not configured |
| Approval round trip | BLOCKED on device (PASS in sandbox, synthetic) | — |
| Dry-run weekly digest | BLOCKED on device (PASS in sandbox, synthetic) | — |

## Defects and findings recorded during the build

- Hermes 0.19.0 ACP ignores `platform_toolsets` and `agent.disabled_toolsets`
  (verified by execution). ACP must never run against the Atlas profile.
- Hermes 0.19.0 cron shifts the first run after a DST change by an hour;
  worked around in the Hub (weekly digest gate).
- Fixed during evaluation: hyphenated tokens evaded the "mark as paid"
  injection pattern; audio proposals from flagged segments were not flagged;
  a failed execution rolled back its own approval invalidation.

## Not built yet

- A send/calendar broker. Approved messages are recorded and Dan sends them.
- A chat proxy from the dashboard/companion app to the Atlas API server.
- Speech-to-text. The companion app must upload transcript chunks
  (`POST /api/atlas/audio`).
- OS-level isolation between Hermes and the Hub database (same Android user).
  The tool policy keeps terminal, file and code tools away from Atlas, but
  this is not a sandbox.
