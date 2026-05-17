"""Time-zone-aware helpers. All trading times are America/New_York."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
PRE_MARKET_OPEN = time(4, 0)


def ny_now() -> datetime:
    """Current wall-clock time in America/New_York."""
    return datetime.now(tz=NY_TZ)


def ensure_ny(ts: datetime) -> datetime:
    """Coerce a datetime to America/New_York. Naive datetimes are rejected."""
    if ts.tzinfo is None:
        raise ValueError(f"naive datetime not allowed: {ts!r}")
    return ts.astimezone(NY_TZ)


def to_ny(ts: datetime | str) -> datetime:
    """Parse and convert any tz-aware datetime (or ISO string) to NY tz."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts)
    if ts.tzinfo is None:
        # FMP intraday timestamps are NY-local without tz; treat as such.
        ts = ts.replace(tzinfo=NY_TZ)
    return ts.astimezone(NY_TZ)


def at_ny(d: date, t: time) -> datetime:
    """Build a NY-tz datetime from a date and time."""
    return datetime.combine(d, t, tzinfo=NY_TZ)


def trading_day_bounds(d: date) -> tuple[datetime, datetime]:
    """Return (regular-open, regular-close) for the given date in NY tz."""
    return at_ny(d, REGULAR_OPEN), at_ny(d, REGULAR_CLOSE)


def premarket_bounds(d: date) -> tuple[datetime, datetime]:
    """Return (04:00, 09:30) NY tz for the given date."""
    return at_ny(d, PRE_MARKET_OPEN), at_ny(d, REGULAR_OPEN)


def previous_business_day(d: date) -> date:
    """Return the previous Mon-Fri date (does NOT account for market holidays)."""
    delta = 3 if d.weekday() == 0 else 1
    return d - timedelta(days=delta)
