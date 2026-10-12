# Atlas tools and skills

The model-facing surface is the atlas-hub tool table
(`mcp/atlas-hub/atlas_hub_mcp/tools.py`), checked against
`agent/atlas/policy/tool-policy.json` by tests. In Hermes the tools appear as
`mcp__atlas_hub__<name>`.

## atlas-hub tools

| Tool | Risk | Level | Who may call | Notes |
|---|---|---|---|---|
| list_obligations, get_record, search_vault | low (read) | autonomous | Atlas on any allowed platform | third-party text returned as `{untrusted: true}` |
| list_pending_approvals | low (read) | autonomous | Atlas | read-only; cannot decide |
| get_system_health, get_check_status | low (read) | autonomous | Atlas | check state, gaps, catch-up, digest gate |
| get_skill | low (read) | autonomous | Atlas | serves only skills Dan reviewed (hash-locked) |
| list_audio_queue | low (read) | autonomous | Atlas | merged transcripts, untrusted |
| propose_bill, propose_car_service, propose_task | low (internal write) | autonomous | Atlas | records labelled *proposed*; idempotent; refuse card/account numbers |
| record_odometer | low (internal write) | autonomous | Atlas | refuses decreasing readings |
| record_evidence | low (internal write) | autonomous | Atlas | stores evidence and candidate matches; never changes a record |
| propose_status_change | medium | autonomous request | Atlas | Hub validates transition and evidence; confirming moves become approval requests |
| propose_message_draft | medium | autonomous request | Atlas | creates an approval; nothing is sent |
| submit_audio_extraction | medium | autonomous request | Atlas | each proposal cites a timestamp and needs approval |
| log_check, collect_alerts, build_digest, log_suspicious_content, run_backup | low | autonomous | Atlas | `run_backup` is a local snapshot only |
| execute_approved_action | high | approval required | Atlas, after Dan approved | approved, unexpired, unchanged, single use; external actions fail closed (no broker) |

**Never exposed:** approve/deny/decide tools, raw SQL, delete, merge, forced
status changes, connector permission changes, money transfers. Decisions are
made only through the Hub API (dashboard, companion) or the Telegram
approval bot, by Dan.

## Hermes toolsets on remote platforms

Allowed: `memory`, `cronjob` (not on cron), `atlas-hub`, `gmail_personal`,
`gmail_work`. Disabled globally for the Atlas profile (`agent.disabled_toolsets`):
terminal, process, file, browser, code_execution, delegation, computer_use,
skills (it contains `skill_manage`), messaging, kanban, project,
homeassistant, debugging, coding. The startup check resolves the real
toolsets with Hermes' own code and fails on anything else.

## Adding an MCP server safely

1. Give it its own named entry under `mcp_servers` with an explicit
   `tools.include` allowlist of read-only tools. Never rely on defaults or
   `exclude`.
2. Set `tools.resources: false`, `tools.prompts: false`, `sampling.enabled: false`.
3. Pass credentials by **file path** (`*_FILE` env var to a 0600 file outside
   the repo). Inline tokens fail the startup check.
4. Add the server name to `hermes.allowedMcpServers` in the policy only after
   reviewing its tool list; mail-type servers must also pass
   `mailConnectors.readOnlyToolAllowlist`.
5. Run `check_policy.py` and the evaluation suite with real Hermes.

The Gmail entries ship **disabled** with a placeholder command. Dan picks and
reviews a Gmail MCP server, maps its read-only tool names into the allowlist,
then enables each account.

## Skills

| Skill | Purpose | Scheduled check |
|---|---|---|
| bill-and-payment-monitor | find bills, keep records current, due/overdue alerts | bill_check |
| car-maintenance-tracker | services by date and km; booking ≠ completion | car_check |
| payment-assistant | manual payment steps; record Dan's "I paid" | — |
| social-relationship-assistant | commitments to people; drafts for approval | — |
| message-evidence-monitor | store receipts/confirmations; match only with evidence | evidence_match |
| audio-action-capture | voice notes → proposed actions with timestamps | audio_queue |
| weekly-what-matters-report | Sunday 08:00 digest in fixed order | weekly_digest |
| system-health-and-recovery | nightly backup, failed/stale checks, recovery steps | nightly_backup |

## Reviewing a new or changed skill

Every skill, including those written by an agent (the builder included),
starts **pending**:

```bash
python agent/atlas/install/review_skills.py               # NEW / CHANGED / PENDING / reviewed
python agent/atlas/install/review_skills.py --show NAME   # read it in full
python agent/atlas/install/review_skills.py --approve NAME --reviewer Dan
```

Before approving, check: the trigger is narrow and "not for" is stated; it
uses only allowed tools; it does not restate or relax approval rules; it
never tells Atlas to send, pay, delete or change permissions; it has a
verification step and reports failures. Approval refuses skills that mention
the persona or policy files, approval bypasses or credential-shaped values.
Any later edit changes the hash and disables the skill again until reviewed.
