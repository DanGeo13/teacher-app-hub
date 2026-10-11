"""Australia/Sydney time, DD/MM/YYYY dates and AUD amounts."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

SYDNEY = ZoneInfo("Australia/Sydney")

Clock = Callable[[], datetime]


def system_clock() -> datetime:
    return datetime.now(UTC)


def to_sydney(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; pass an aware datetime")
    return moment.astimezone(SYDNEY)


def sydney_today(clock: Clock = system_clock) -> date:
    return to_sydney(clock()).date()


def format_date(value: date | datetime | str | None) -> str:
    """DD/MM/YYYY (dates are local to Sydney)."""
    if value is None:
        return "no date"
    if isinstance(value, str):
        value = date.fromisoformat(value[:10])
    if isinstance(value, datetime):
        value = to_sydney(value).date()
    return value.strftime("%d/%m/%Y")


def parse_date(value: str) -> date:
    """Accept DD/MM/YYYY (Australian) or ISO YYYY-MM-DD; never MM/DD."""
    value = value.strip()
    if len(value) == 10 and value[2] == "/" and value[5] == "/":
        day, month, year = value.split("/")
        return date(int(year), int(month), int(day))
    return date.fromisoformat(value)


def format_aud(cents: int | None) -> str:
    if cents is None:
        return "amount unknown"
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}${cents // 100:,}.{cents % 100:02d} AUD"


def next_weekly_occurrence(after: datetime, weekday: int, hour: int, minute: int = 0) -> datetime:
    """Next Sydney wall-clock time (weekday 0=Mon … 6=Sun) strictly after ``after``.

    Built from the local date, so 08:00 stays 08:00 local across daylight
    saving changes (the UTC instant shifts by an hour instead).
    """
    local = to_sydney(after)
    days_ahead = (weekday - local.weekday()) % 7
    for offset in (days_ahead, days_ahead + 7):
        day = local.date() + timedelta(days=offset)
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=SYDNEY)
        if candidate > local:
            return candidate
    raise AssertionError("unreachable")
