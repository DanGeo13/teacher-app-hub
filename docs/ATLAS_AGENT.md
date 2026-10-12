# Atlas — Dan's personal operations agent

Atlas runs on **Hermes Agent** (profile `atlas`) on the dedicated Pixel 7 Pro
and is reached through Telegram, the Hub dashboard and the Pixel 9 Pro
companion app. This repository holds Atlas's persona, skills, tools, approval
engine and schedules. Hermes is the agent runtime; nothing here substitutes
for it.

> **Status:** built and tested in CI, **not yet verified on the Pixel 7 host**.
> See [IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## How the pieces fit

```text
 Telegram (Dan) ──► Hermes gateway, profile "atlas" ──► model
 Dashboard / Pixel 9 app ──► Hub (approvals, health, digest, audio upload, verify)
                         └─► Hermes API server (loopback, bearer key; used by
                             the verify button; chat proxy not built yet)
                                       │  tools allowed on remote platforms:
                                       │  memory, cronjob, atlas-hub, read-only mail
                                       ▼
                          atlas-hub MCP server (stdio)
                                       │
                                       ▼
                    Hub SQLite: records, evidence, approvals, checks, audit
                                       ▲
 Approve/Deny ── dashboard · companion app · Telegram approval bot (Dan only)
```

- **Persona:** `agent/atlas/persona/SOUL.md`, installed verbatim as the
  profile's `SOUL.md`.
- **Authority in code:** `agent/atlas/policy/tool-policy.json`, enforced by the
  atlas-hub tool table, `AtlasService` and the startup policy check.
- **Never used for Atlas:** the Hub's ACP chat (`hermes acp`). Hermes 0.19.0
  ignores the tool policy on ACP; see [AGENT_INTEGRATION.md](AGENT_INTEGRATION.md).

## Authority levels

| Level | What | Enforced by |
|---|---|---|
| Autonomous | read connectors and Hub data; create records labelled *proposed*; record evidence; internal status moves (for example due → payment_reported); reminders to Dan; scheduled checks; audit entries | atlas-hub tools; `state.py` marks which moves need no approval |
| Approval required | send any message or email; calendar changes; turning audio proposals into tasks; confirming paid; booked/completed/done; delete or merge; connector permission changes; purchases or bookings | `ApprovalService` + `execute_approved_action` (exact payload hash, expiry, single use, human channel only) |
| Forbidden | moving money or touching banking credentials; impersonating Dan; bypassing authentication; hidden recording; student/school data without a named purpose | never exposed; `FORBIDDEN_ACTIONS`; banking-detail refusal; startup check |

There is **no send broker yet**. An approved message is recorded as approved
and Atlas tells Dan to send it himself; nothing is sent.

## Approvals

1. Atlas proposes (for example `propose_status_change` to `confirmed_paid`).
   The Hub validates the transition and evidence, stores the exact payload,
   hashes it and sets an expiry (24 hours by default, 7 days maximum).
2. Dan sees action, target, full content, evidence and expiry on the
   dashboard, the companion app or a Telegram card.
3. He approves *that hash*. If the content, target or record changed, or the
   approval expired, it is invalidated or expired and must be requested again.
4. Execution consumes the approval once. Re-running returns the stored
   result; nothing happens twice. Every step is in the audit log.

Only `dashboard`, `companion` and `telegram` (Dan's user id in Dan's chat) can
decide. The agent's own actor and channel are refused by the engine.

## State machines

| Record | Path | Evidence needed |
|---|---|---|
| Bill | due → payment_reported → confirmed_paid | `payment_reported`/`payment_confirmed`; confirming needs `payment_confirmed` with matching amount or reference, and approval |
| Car service | due → booking_requested → booked → completed | booking → `booked` only; `completed` needs `service_completed` (dated record) and approval |
| Commitment | open → draft_ready → awaiting_approval → sent / done | `sent` only through an executed send (no broker yet); `done` needs `message_sent`/`task_done` and approval |
| Task | open → done | `task_done` and approval |

Evidence advances a record only when payee/person, action, amount/reference/
date and account all agree and the evidence is not attached elsewhere.
"Thanks", "done" or "confirmed" alone never qualifies. Anything uncertain is
left unchanged and put on the review list.

## Schedules (Australia/Sydney)

| Job | When | Skill |
|---|---|---|
| daily-bill-check | 07:05 daily | bill-and-payment-monitor |
| daily-car-check | 07:15 daily | car-maintenance-tracker |
| evidence-matching-pass | every 30 min (`checks.evidence_match.expectedEveryMinutes`) | message-evidence-monitor |
| audio-queue-worker | every 15 min | audio-action-capture |
| weekly-digest | wakes hourly 07:00–11:00 Sunday; Hub releases it once at 08:00 | weekly-what-matters-report |
| nightly-backup-and-health | 03:30 daily (outside the DST gap) | system-health-and-recovery |

Each job's prompt names its skill, allowed tools, data sources, destination
and failure behaviour (`agent/atlas/schedules.yaml`). Routine runs with no
change reply `[SILENT]`. Failed, stale and never-run checks always read
differently from "nothing changed". After downtime, `get_check_status` reports
the gap and asks for exactly one reconciliation; due dates are never shifted
and alerts are de-duplicated.

## Install on the Pixel 7 host (when Hermes runs there)

1. Install Hermes and create the profile: `hermes profile create atlas`.
2. Put secrets in `~/.hermes/profiles/atlas/.env` (mode 0600): Telegram bot
   token, `TELEGRAM_ALLOWED_USERS`, `API_SERVER_ENABLED=true`,
   `API_SERVER_HOST=127.0.0.1`, `API_SERVER_KEY`. Put the same API key in a
   separate 0600 file for the Hub and set `ATLAS_API_SERVER_KEY_FILE` to it.
3. Review the skills: `python agent/atlas/install/review_skills.py`, read each
   with `--show NAME`, then `--approve NAME --reviewer Dan`.
4. Dry run, then apply:
   `~/hermes-venv/bin/python agent/atlas/install/install_profile.py --hub-python <hub venv python> --hub-data-dir <HUB_DATA_DIR> --telegram-chat-id <id>`
   and repeat with `--apply`.
5. Start with the guard: `HERMES_PYTHON=~/hermes-venv/bin/python bash agent/atlas/install/start-atlas-gateway.sh`.
6. Start the approval bot: `python -m hermes_hub_backend.atlas.telegram_approvals`
   (needs `ATLAS_TELEGRAM_APPROVAL_TOKEN_FILE`, `ATLAS_TELEGRAM_DAN_USER_ID`,
   `ATLAS_TELEGRAM_CHAT_ID`, `HUB_DATA_DIR`).
7. Open the Hub → **Atlas** → **Verify Atlas on this host**.

Admin maintenance uses the separate default profile on the host's own
terminal (`hermes` without `-p atlas`). Never run `hermes profile use atlas`;
the startup check fails if Atlas is the sticky default.

## Editing the persona safely

1. Edit `agent/atlas/persona/SOUL.md` in a branch.
2. Run the evaluation suite with real Hermes
   (`ATLAS_TEST_HERMES_PYTHON=… scripts/run-atlas-evals.py`). It checks that
   Hermes' injection scanner does not block the file. A blocked SOUL.md is
   silently replaced by `[BLOCKED: …]`, which would strip the persona.
3. Merge, then re-run the installer (it copies SOUL.md) and the verify button.
   The persona never grants authority: changing it cannot widen tools or skip
   approvals.

## Adding a skill

1. Create `agent/atlas/skills/<name>/SKILL.md` following the Agent Skills
   standard: frontmatter `name` (lowercase, hyphens, same as the folder),
   `description` (what it does, "Use for/when …" triggers and "Not for …"),
   `metadata.version`, `metadata.atlas.triggers` and `not_for`. Put trigger
   context only in the description. Body sections: When not to use, Tools,
   Procedure (numbered), Verification, Output. Keep it under 500 lines.
2. Use only atlas-hub tools and read-only mail tools. Do not restate approval
   rules; call `propose_*` and let the Hub decide.
3. Add trigger/unrelated prompts to `tests/atlas/fixtures/skill_prompts.json`
   and success/failure tests to `tests/atlas/test_skills.py`.
4. `python agent/atlas/install/review_skills.py --stage`, then Dan approves it.
   Until then atlas-hub refuses it and the startup check fails.

## Running the evaluation suite

```bash
bash scripts/bootstrap-dev.sh
Apps/system/hermes-hub/backend/.venv/bin/python scripts/run-atlas-evals.py
# with real Hermes (otherwise those tests report BLOCKED):
ATLAS_TEST_HERMES_PYTHON=/path/to/hermes-venv/bin/python \
  Apps/system/hermes-hub/backend/.venv/bin/python scripts/run-atlas-evals.py --report atlas-eval.md
```
