"""Local-time helpers: bare dates are local days in ``settings.tz`` (Europe/Budapest).

Storage stays UTC ISO-8601 (``…+00:00``) so string comparison in SQLite orders correctly.
Every boundary is computed in the local zone and converted to UTC; DST is handled by zoneinfo.
"""

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import settings

_BARE_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InvalidDateError(ValueError):
    """A query parameter is neither ``YYYY-MM-DD`` nor an ISO-8601 datetime."""


def local_tz() -> ZoneInfo:
    return ZoneInfo(settings.tz)


def now_utc() -> datetime:
    """Current time; tests monkeypatch this to freeze the clock."""
    return datetime.now(UTC)


def local_today(now: datetime | None = None) -> date:
    return (now or now_utc()).astimezone(local_tz()).date()


def local_midnight_utc(day: date) -> datetime:
    """Start of the local day ``day`` as an aware UTC datetime."""
    return datetime.combine(day, time(0), tzinfo=local_tz()).astimezone(UTC)


def local_noon_utc(day: date) -> datetime:
    return datetime.combine(day, time(12), tzinfo=local_tz()).astimezone(UTC)


def local_month_start(day: date) -> date:
    return day.replace(day=1)


def to_utc_iso(value: datetime) -> str:
    """Normalise an aware datetime to the storage format (UTC, ``+00:00``)."""
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).isoformat()


def is_bare_date(value: str) -> bool:
    return bool(_BARE_DATE.match(value))


def _parse(value: str) -> datetime | date:
    try:
        if is_bare_date(value):
            return date.fromisoformat(value)
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InvalidDateError(f"Некорректная дата: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=local_tz())
    return dt


def parse_lower_bound(value: str | datetime) -> datetime:
    """``date_from``: a bare date means the start of that local day (inclusive)."""
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    parsed = _parse(value)
    if isinstance(parsed, datetime):
        return parsed.astimezone(UTC)
    return local_midnight_utc(parsed)


def parse_upper_bound(value: str | datetime) -> datetime:
    """``date_to``: a bare date means the whole local day (inclusive, last microsecond)."""
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    parsed = _parse(value)
    if isinstance(parsed, datetime):
        return parsed.astimezone(UTC)
    return local_midnight_utc(parsed + timedelta(days=1)) - timedelta(microseconds=1)


def to_local_date(value: str) -> date:
    """Local calendar date of a stored UTC ISO timestamp."""
    return datetime.fromisoformat(value).astimezone(local_tz()).date()
