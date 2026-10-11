"""Weekly "what matters" digest.

Section order is fixed: 🔴 Urgent/overdue, 🟠 Action required, 🟡 Upcoming,
🟢 Completed with evidence, then System health. Within a section, items are
grouped by kind and sorted by due date.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from .locale import format_aud, format_date
from .state import TERMINAL

if TYPE_CHECKING:
    from .service import AtlasService

SECTIONS = (
    ("urgent", "🔴 Urgent/overdue"),
    ("action", "🟠 Action required"),
    ("upcoming", "🟡 Upcoming"),
    ("completed", "🟢 Completed with evidence"),
    ("health", "System health"),
)
GROUP_ORDER = ("Bills", "Car", "Commitments", "Tasks", "Approvals", "Review")
KIND_GROUP = {"bill": "Bills", "car_service": "Car", "commitment": "Commitments", "task": "Tasks"}
UPCOMING_DAYS = 14
COMPLETED_DAYS = 7


def _line(record: dict[str, Any]) -> str:
    parts = [record["title"]]
    if record["review_state"] == "proposed":
        parts.append("(proposed)")
    if record["amount_cents"] is not None:
        parts.append(format_aud(record["amount_cents"]))
    if record["due_date"]:
        parts.append(f"due {format_date(record['due_date'])}")
    if record["due_odometer_km"] is not None:
        parts.append(f"at {record['due_odometer_km']:,} km")
    parts.append(f"[{record['source_account']}]")
    return " ".join(parts)


def build_digest(service: "AtlasService", channel: str = "telegram") -> dict[str, Any]:
    if channel not in ("telegram", "voice", "dashboard"):
        raise ValueError("channel must be telegram, voice or dashboard")
    today = service._today()
    alerts_cfg = service.config["alerts"]
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {key: {} for key, _ in SECTIONS}

    def add(section: str, group: str, item: dict[str, Any]) -> None:
        buckets[section].setdefault(group, []).append(item)

    with service.database.read() as connection:
        records = [service._record_dict(r) for r in connection.execute(
            "SELECT * FROM atlas_records WHERE review_state != 'archived'"
        ).fetchall()]
        approvals = [dict(r) for r in connection.execute(
            "SELECT * FROM approvals WHERE status = 'pending' ORDER BY expires_at"
        ).fetchall()]
        review = [dict(r) for r in connection.execute(
            "SELECT * FROM atlas_review_items WHERE resolved_at IS NULL ORDER BY id"
        ).fetchall()]
        completed_rows = connection.execute(
            "SELECT h.record_id, h.to_status, h.evidence_ids_json, h.created_at FROM atlas_record_history h "
            "WHERE h.created_at >= ? ORDER BY h.id",
            ((today - timedelta(days=COMPLETED_DAYS)).isoformat(),),
        ).fetchall()

    by_id = {r["id"]: r for r in records}
    for r in sorted(records, key=lambda x: (x["due_date"] or "9999-12-31", x["title"])):
        group = KIND_GROUP[r["kind"]]
        if r["status"] in TERMINAL[r["kind"]]:
            continue
        days = (date.fromisoformat(r["due_date"]) - today).days if r["due_date"] else None
        item = {"recordId": r["id"], "text": _line(r), "dueDate": r["due_date"], "sourceAccount": r["source_account"]}
        km_overdue = (r["kind"] == "car_service" and r["due_odometer_km"] is not None
                      and r["current_odometer_km"] is not None and r["current_odometer_km"] >= r["due_odometer_km"])
        km_soon = (r["kind"] == "car_service" and r["due_odometer_km"] is not None
                   and r["current_odometer_km"] is not None
                   and r["due_odometer_km"] - r["current_odometer_km"] <= alerts_cfg["carServiceDueWithinKm"])
        if (days is not None and days < 0) or km_overdue:
            add("urgent", group, {**item, "text": "OVERDUE: " + item["text"]})
        elif r["kind"] == "bill" and days is not None and days * 24 <= alerts_cfg["billDueWithinHours"] and r["status"] == "due":
            add("urgent", group, item)
        elif r["status"] in ("payment_reported", "booking_requested") or r["review_state"] == "proposed":
            reason = {"payment_reported": "payment reported, needs confirming evidence",
                      "booking_requested": "booking requested, not confirmed"}.get(r["status"], "proposed, needs Dan's review")
            add("action", group, {**item, "text": f"{item['text']} — {reason}"})
        elif (days is not None and days <= UPCOMING_DAYS) or km_soon:
            add("upcoming", group, item)
    for a in approvals:
        add("action", "Approvals", {"approvalId": a["id"], "text": f"Approve? {a['summary'] or a['action']}",
                                    "expiresAt": a["expires_at"]})
    for item in review:
        add("action", "Review", {"reviewId": item["id"], "recordId": item["record_id"],
                                 "text": f"Check evidence for {by_id.get(item['record_id'], {}).get('title', item['record_id'])}: {item['reason']}"})
    for row in completed_rows:
        record = by_id.get(row["record_id"])
        evidence_ids = json.loads(row["evidence_ids_json"])
        if record and row["to_status"] in TERMINAL[record["kind"]] and evidence_ids:
            add("completed", KIND_GROUP[record["kind"]],
                {"recordId": record["id"], "text": f"{record['title']} — {row['to_status'].replace('_', ' ')}",
                 "evidenceIds": evidence_ids})

    health = service.get_system_health()
    for check in health["checks"]:
        add("health", "Checks", {"text": check["text"], "state": check["state"]})
    for problem in health["problems"]:
        if not problem.startswith(tuple(c["check"] for c in health["checks"])):
            add("health", "Other", {"text": problem})

    sections = []
    for key, title in SECTIONS:
        groups = buckets[key]
        ordered = sorted(groups.items(), key=lambda kv: GROUP_ORDER.index(kv[0]) if kv[0] in GROUP_ORDER else 99)
        sections.append({"key": key, "title": title,
                         "groups": [{"name": name, "items": items} for name, items in ordered]})
    return {"generatedFor": format_date(today), "channel": channel, "sections": sections,
            "text": render(sections, channel, today)}


def render(sections: list[dict[str, Any]], channel: str, today: date) -> str:
    if channel == "voice":
        sentences = [f"Here is your week from {format_date(today)}."]
        for section in sections:
            count = sum(len(g["items"]) for g in section["groups"])
            label = section["title"].split(" ", 1)[-1] if section["key"] != "health" else "System health"
            if not count:
                sentences.append(f"{label}: nothing.")
                continue
            sentences.append(f"{label}: {count} item{'s' if count != 1 else ''}.")
            for group in section["groups"]:
                for item in group["items"][:5]:
                    sentences.append(item["text"].replace("[", "from ").replace("]", "") + ".")
        return " ".join(sentences)
    lines = [f"Atlas weekly — {format_date(today)}"]
    for section in sections:
        lines.append("")
        lines.append(section["title"])
        if not any(g["items"] for g in section["groups"]):
            lines.append("  nothing")
            continue
        for group in section["groups"]:
            lines.append(f"  {group['name']}")
            for item in group["items"]:
                lines.append(f"   • {item['text']}")
    return "\n".join(lines)
