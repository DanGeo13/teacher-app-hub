---
name: weekly-what-matters-report
description: Produce Dan's Sunday 8:00 AM weekly digest from the Hub, in the fixed section order, for Telegram, voice or the dashboard. Use for the weekly digest schedule or when Dan asks for his week at a glance. Not for single-bill questions, sending the digest to anyone else, or adding items that are not in the Hub.
metadata:
  version: "1.0.0"
  hermes:
    tags: [atlas, digest]
    related_skills: [system-health-and-recovery]
  atlas:
    triggers: [weekly digest, weekly summary, what matters this week, week ahead, sunday report, my week]
    not_for: [send the digest to, forward my week]
    check: weekly_digest
---

# Weekly what-matters report

## When not to use

- A question about one bill or service → that domain's skill.
- Sending the digest to another person: Atlas only reports to Dan.

## Tools

- `mcp__atlas_hub__get_check_status`, `mcp__atlas_hub__build_digest`, `mcp__atlas_hub__log_check`

## Procedure

1. Call `get_check_status` with `weekly_digest`. If `catchUpRequired`, say at the top that the previous digest was missed and how long Atlas was down.
2. Call `build_digest` with `channel` set to where the reply goes (`telegram` for scheduled runs, `voice` if Dan asked by voice).
3. Use the returned `text` as the body. Keep the section order exactly: 🔴 Urgent/overdue, 🟠 Action required, 🟡 Upcoming, 🟢 Completed with evidence, System health. Do not add items that are not in the result, and do not drop the System health section even when everything is fine.
4. Call `log_check` with `ok` (a digest was produced) or `failed` with the reason if `build_digest` failed.

## Verification

- The reply contains all five section titles in order.

## Failure handling

- If `build_digest` failed, send "The weekly digest could not be built: <reason>". Never send an empty or "no change" digest instead.

## Output

- The digest text. For voice, the spoken-friendly version with no tables.
