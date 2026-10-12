---
name: car-maintenance-tracker
description: Track Dan's car services by date and odometer, record booking and completion evidence, and alert when a service is due or overdue. Use for the daily car check or when Dan asks about servicing, bookings or kilometres. Not for bills, registration payments, buying parts, or making bookings on his behalf.
metadata:
  version: "1.0.0"
  hermes:
    tags: [atlas, car]
    related_skills: [message-evidence-monitor]
  atlas:
    triggers: [car service, service due, logbook service, odometer, kilometres, km, oil change, tyre rotation, mechanic, service booking, car booked]
    not_for: [pay rego, electricity, book it for me, buy tyres, insurance claim]
    check: car_check
---

# Car maintenance tracker

A booking email means **booked**, never **completed**. Completion needs a
dated service record such as an invoice or logbook entry.

## When not to use

- Paying registration or any bill → `bill-and-payment-monitor` / `payment-assistant`.
- Asking Atlas to make the booking itself: Atlas cannot book anything; offer a booking-request draft for Dan instead.

## Tools

- `mcp__atlas_hub__get_check_status`, `mcp__atlas_hub__list_obligations`, `mcp__atlas_hub__get_record`
- `mcp__atlas_hub__propose_car_service`, `mcp__atlas_hub__record_odometer`, `mcp__atlas_hub__record_evidence`, `mcp__atlas_hub__propose_status_change`
- `mcp__atlas_hub__propose_message_draft` (booking request text for Dan to send)
- `mcp__atlas_hub__collect_alerts`, `mcp__atlas_hub__log_check`
- Read-only mail search on each configured account.

## Procedure

1. Call `get_check_status` with `car_check`; if `catchUpRequired`, this run covers the whole gap with original due dates kept.
2. Call `list_obligations` with `kind: car_service`.
3. If Dan gave an odometer reading, call `record_odometer` with the vehicle and kilometres. If no service record exists yet for a service he mentions, use `propose_car_service`.
4. Search mail for workshop messages since the last successful check. On any connector error, remember the failure; never report "no change" for an account you could not read.
5. Classify each workshop message and call `record_evidence`:
   - "we have received your request" → `booking_requested`
   - "your booking is confirmed for …" → `booking_confirmed`
   - invoice / "your vehicle is ready" with a date → `service_completed`
6. For each candidate with `matched: true`, call `propose_status_change`. Expect `booked` and `completed` to come back as approval requests.
7. If a service is due and nothing is booked, offer a booking-request draft via `propose_message_draft` only when Dan asked for one.
8. Call `collect_alerts`, then `log_check` (`failed` / `ok` / `no_change`, `catch_up` as needed).

## Verification

- Re-read the affected record with `get_record`: its status must equal what the Hub returned, and no record may show `completed` without a `service_completed` evidence item.

## Output

- Nothing new and no failures → `[SILENT]`.
- Otherwise: alerts first ("Service overdue by kilometres: …"), then changes with source account, then failures stated plainly.
