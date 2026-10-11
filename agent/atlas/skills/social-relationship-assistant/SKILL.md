---
name: social-relationship-assistant
description: Keep track of Dan's commitments to people (replies owed, catch-ups, birthdays) and prepare message drafts for his approval. Use when Dan mentions owing someone a reply, a promise to a person, or an upcoming birthday. Not for sending anything, bills, car services, or any student or school data.
version: 1.0.0
metadata:
  hermes:
    tags: [atlas, people]
    related_skills: [weekly-what-matters-report]
  atlas:
    triggers: [birthday, catch up with, follow up with, reply to, get back to, i promised, anniversary, owe a reply, message sam, draft a message]
    not_for: [student, parent of, school report, year 9, class, bill, car service]
---

# Social and relationship assistant

## When to use

- "I need to reply to Sam about Saturday", "Remind me Mum's birthday is 03/11".
- "Draft a message to Priya saying I'll be late."

## When not to use

- Anything about students, parents of students or school records: Atlas does not process school data unless Dan has enabled it for a named purpose.
- Bills and car services → their own skills.

## Tools

- `mcp__atlas_hub__list_obligations`, `mcp__atlas_hub__get_record`, `mcp__atlas_hub__search_vault`
- `mcp__atlas_hub__propose_task`, `mcp__atlas_hub__propose_message_draft`, `mcp__atlas_hub__propose_status_change`

## Procedure

1. Check for an existing commitment with `list_obligations` (`kind: commitment`) or `search_vault` for the person's name, so you do not create a duplicate.
2. If none exists, call `propose_task` with `kind: commitment`, the person, the due date if Dan gave one, `source_type: manual` and the channel Dan used as `source_account`.
3. If Dan wants a message, write it in Dan's plain Australian English, short and ready to send, then call `propose_message_draft` with recipient, channel, body and the account it would be sent from. Show Dan the exact text.
4. Tell Dan the draft is waiting for his approval and that Atlas will not send it; with no send broker installed he sends it himself.
5. When Dan says he has done it, record his statement with `record_evidence` (`action: task_done` or `message_sent`, `source_type: manual`) and call `propose_status_change` to `done`.

## Verification

- `get_record` shows the commitment with the expected status and, for drafts, a pending approval.

## Failure handling

- If a tool call failed, tell Dan plainly what was not saved. A failure is never reported as "no change".

## Output

- One or two lines plus the draft text when there is one.
