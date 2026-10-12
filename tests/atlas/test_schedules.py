"""Step 7: self-contained cron prompts, DST-safe times, and catch-up after downtime."""

from __future__ import annotations

import copy
from datetime import UTC, datetime

import pytest

import atlas_policy
from hermes_hub_backend.atlas.locale import SYDNEY, next_weekly_occurrence

JOBS = atlas_policy.load_schedules()
BY_NAME = {job["name"]: job for job in JOBS}


def test_all_required_schedules_are_declared():
    assert set(BY_NAME) == {"daily-bill-check", "daily-car-check", "evidence-matching-pass",
                            "audio-queue-worker", "weekly-digest", "nightly-backup-and-health"}
    assert atlas_policy.check_schedules(JOBS) == []


@pytest.mark.parametrize("name", sorted(BY_NAME))
def test_prompt_is_self_contained(name):
    job = BY_NAME[name]
    prompt = job["prompt"]
    assert job["skills"] and all(skill in prompt for skill in job["skills"])
    for part in atlas_policy.REQUIRED_PROMPT_PARTS:
        assert part in prompt
    assert "untrusted" in prompt or name in {"weekly-digest", "nightly-backup-and-health"}
    assert "never" in prompt.lower() or "always" in prompt.lower()


def test_evidence_interval_defaults_to_30_minutes_and_is_configurable():
    assert BY_NAME["evidence-matching-pass"]["schedule"] == "every 30m"
    config = copy.deepcopy(atlas_policy.load_config())
    config["checks"]["evidence_match"]["expectedEveryMinutes"] = 15
    jobs = {j["name"]: j for j in atlas_policy.load_schedules(config=config)}
    assert jobs["evidence-matching-pass"]["schedule"] == "every 15m"


def test_weekly_digest_fires_on_sunday_mornings_sydney():
    assert BY_NAME["weekly-digest"]["schedule"] == "0 7-11 * * 0"
    assert "digestDueNow" in BY_NAME["weekly-digest"]["prompt"]
    assert atlas_policy.load_config()["locale"]["timezone"] == "Australia/Sydney"


@pytest.mark.parametrize(
    "utc, due",
    [
        (datetime(2026, 10, 3, 20, 0, tzinfo=UTC), False),  # Sun 07:00 AEDT: too early
        (datetime(2026, 10, 3, 21, 0, tzinfo=UTC), True),   # Sun 08:00 AEDT
        (datetime(2026, 10, 4, 21, 0, tzinfo=UTC), False),  # Mon 08:00 AEDT
        (datetime(2027, 4, 3, 21, 0, tzinfo=UTC), False),   # Sun 07:00 AEST after DST ends
        (datetime(2027, 4, 3, 22, 0, tzinfo=UTC), True),    # Sun 08:00 AEST
    ],
)
def test_weekly_digest_gate_uses_sydney_wall_clock(atlas, clock, utc, due):
    clock.moment = utc
    assert atlas.get_check_status("weekly_digest")["digestDueNow"] is due


def test_weekly_digest_gate_releases_once_per_sunday(atlas, clock):
    clock.moment = datetime(2026, 10, 10, 21, 0, tzinfo=UTC)  # Sun 11/10 08:00 AEDT
    assert atlas.get_check_status("weekly_digest")["digestDueNow"] is True
    atlas.log_check(check_name="weekly_digest", outcome="ok")
    clock.advance(hours=1)
    assert atlas.get_check_status("weekly_digest")["digestDueNow"] is False
    clock.advance(days=7)
    assert atlas.get_check_status("weekly_digest")["digestDueNow"] is True


@pytest.mark.parametrize(
    "after, expected_utc",
    [
        # DST starts 02:00 Sun 04/10/2026: 08:00 that morning is AEDT (UTC+11).
        (datetime(2026, 10, 1, 0, 0, tzinfo=UTC), datetime(2026, 10, 3, 21, 0, tzinfo=UTC)),
        # The Sunday before is still AEST (UTC+10).
        (datetime(2026, 9, 24, 0, 0, tzinfo=UTC), datetime(2026, 9, 26, 22, 0, tzinfo=UTC)),
        # DST ends 03:00 Sun 04/04/2027: 08:00 that morning is AEST again.
        (datetime(2027, 4, 1, 0, 0, tzinfo=UTC), datetime(2027, 4, 3, 22, 0, tzinfo=UTC)),
        # Exactly at 08:00 Sydney the next run is a week later.
        (datetime(2026, 10, 10, 21, 0, tzinfo=UTC), datetime(2026, 10, 17, 21, 0, tzinfo=UTC)),
    ],
)
def test_sunday_0800_across_daylight_saving(after, expected_utc):
    nxt = next_weekly_occurrence(after, weekday=6, hour=8)
    assert nxt.astimezone(UTC) == expected_utc
    assert nxt.astimezone(SYDNEY).strftime("%a %H:%M") == "Sun 08:00"


def test_no_job_in_the_daylight_saving_gap():
    bad = [dict(BY_NAME["nightly-backup-and-health"], schedule="30 2 * * *")]
    assert [v.code for v in atlas_policy.check_schedules(bad)] == ["JOB_IN_DST_GAP"]


def test_job_cannot_widen_toolsets():
    bad = [dict(BY_NAME["weekly-digest"], toolsets=["memory", "atlas-hub", "terminal"])]
    assert "JOB_TOOLSET_NOT_ALLOWED" in {v.code for v in atlas_policy.check_schedules(bad)}


# ------------------------------------------------------------ catch-up logic


def test_catch_up_after_downtime_runs_one_reconciliation(atlas, clock, new_bill):
    bill = new_bill(due_date="14/10/2026")
    atlas.log_check(check_name="bill_check", outcome="no_change")
    first_alerts = atlas.collect_alerts("before-downtime")["alerts"]
    assert any(a["kind"] == "bill_due_soon" for a in first_alerts)

    clock.advance(days=3)  # the Pixel was off for three days
    status = atlas.get_check_status("bill_check")
    assert status["catchUpRequired"] is True and status["state"] == "stale"
    assert status["gapMinutes"] == 3 * 24 * 60

    after = atlas.collect_alerts("catch-up")["alerts"]
    kinds = [a["kind"] for a in after]
    assert "bill_overdue" in kinds, "an escalation to overdue is a new alert"
    assert "bill_due_soon" not in kinds, "the earlier due-soon alert is not repeated"
    assert atlas.get_record(bill["id"])["record"]["dueDateIso"] == "2026-10-14", "original due date preserved"

    logged = atlas.log_check(check_name="bill_check", outcome="ok", catch_up=True)
    assert "4320 minutes" in logged["gapReport"]
    assert atlas.get_check_status("bill_check")["catchUpRequired"] is False
    assert atlas.collect_alerts("next-run")["alerts"] == []
