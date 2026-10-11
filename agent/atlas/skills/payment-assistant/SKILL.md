---
name: payment-assistant
description: Help Dan pay a bill himself by laying out the exact payee, amount, reference and due date, and record his report that he has paid. Use when Dan asks how to pay a bill, for payment details, or says he has paid. Not for moving money, logging into banking, storing card or account numbers, or confirming a bill as paid.
version: 1.0.0
metadata:
  hermes:
    tags: [atlas, payments]
    related_skills: [bill-and-payment-monitor, message-evidence-monitor]
  atlas:
    triggers: [how do i pay, payment details, bpay, pay it for me, pay this, i paid, i have paid, paid it, transfer details]
    not_for: [what bills are due, is it paid, car service, bank password, card number]
---

# Payment assistant (manual transfer helper)

Atlas has no banking access. Dan makes every payment himself; this skill
gives him correct details and records what he reports.

## When to use

- "How do I pay the water bill?", "What's the BPAY reference?"
- "Pay it for me": explain that Atlas cannot pay, then give the manual steps.
- "I've paid the electricity": record his report.

## When not to use

- Listing bills or due dates → `bill-and-payment-monitor`.
- Confirming a bill as paid: that needs the biller's receipt via `message-evidence-monitor`.

## Tools

- `mcp__atlas_hub__list_obligations`, `mcp__atlas_hub__get_record`
- `mcp__atlas_hub__record_evidence`, `mcp__atlas_hub__propose_status_change`

## Procedure

1. Identify the bill with `list_obligations` (`kind: bill`). If two bills could match, or two accounts hold a bill with the same name, ask Dan which one and name the source accounts.
2. Read it with `get_record`.
3. Give the manual steps as a short numbered list: payee, amount in AUD, reference, due date (DD/MM/YYYY), and which account the bill came from. Use only details stored on the record; if the reference is missing, say so rather than guessing.
4. Never ask for, repeat or store card numbers, BSB/account numbers, passwords or one-time codes. If Dan sends one, tell him it was not stored.
5. When Dan says he paid: call `record_evidence` with `source_type: manual`, `source_account: telegram:dan` (or the channel he used), `action: payment_reported`, the payee, amount and today's date, and `external_ref` `dan-report:<record id>:<date>`.
6. Call `propose_status_change` to `payment_reported` with that evidence id.

## Verification

- `get_record` shows status `payment_reported` with Dan's report attached. If the Hub refused the change, tell Dan exactly why (for example the amount differs).

## Failure handling

- If any tool call failed, say "I couldn't record that: <reason>". Never reply as if nothing needed doing; a failed call is not "no change".

## Output

- Steps or a one-line confirmation: "Recorded your payment report for Electricity (gmail_personal). It becomes confirmed paid when Origin's receipt arrives and you approve it."
