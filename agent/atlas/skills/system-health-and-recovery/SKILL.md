---
name: system-health-and-recovery
description: Nightly backup and health check for Atlas itself, reporting failed or stale checks, expired authorisation, unprocessed audio and failed backups, with plain recovery steps. Use for the nightly schedule or when Dan asks whether Atlas is working. Not for Dan's own health, the car's condition, or fixing connectors by changing permissions.
version: 1.0.0
metadata:
  hermes:
    tags: [atlas, health]
    related_skills: [weekly-what-matters-report]
  atlas:
    triggers: [system health, is atlas working, did the checks run, backup, atlas status, health check, why no digest]
    not_for: [my health, doctor, blood pressure, car health, grant access]
    check: nightly_backup
---

# System health and recovery

"Nothing changed" and "the check did not run" must never read the same.

## When to use

- The nightly backup and health schedule.
- "Is Atlas working?", "Did the bill check run?"

## When not to use

- Personal health questions, or the car's mechanical condition.
- Changing connector permissions: Atlas reports the problem; Dan fixes it locally.

## Tools

- `mcp__atlas_hub__get_check_status`, `mcp__atlas_hub__run_backup`, `mcp__atlas_hub__get_system_health`
- `mcp__atlas_hub__list_pending_approvals`, `mcp__atlas_hub__collect_alerts`, `mcp__atlas_hub__log_check`

## Procedure

1. Call `get_check_status` with `nightly_backup`.
2. Call `run_backup`. A failure is logged by the Hub; keep its message for the report.
3. Call `get_system_health`. For every check read its `state`: `ok` and `no_change` are healthy; `failed`, `stale` and `never_run` are problems.
4. Call `list_pending_approvals` and note any that expire within the next day.
5. Call `collect_alerts`.
6. For each problem give one recovery step Dan can take, for example: "Bill check stale since 06:00 10/10/2026 — check the Atlas gateway is running on the Pixel 7 host." or "Work mail authorisation expired — re-authorise gmail_work on the host."
7. Call `log_check` for `nightly_backup` only if `run_backup` did not already log it (it logs `ok` or `failed` itself).

## Verification

- The backup result names a snapshot file, or the report says the backup failed.

## Output

- Backup ok, every check healthy, no alerts → `[SILENT]`.
- Otherwise a short list: failures first, then stale or never-run checks, unprocessed audio, then expiring approvals, each with its recovery step.
