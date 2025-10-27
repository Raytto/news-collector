from __future__ import annotations

from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import re
from typing import Optional, Set, Tuple

FALLBACK_FILL_VALUE = 11


ISO_PATTERN = re.compile(
    r"""
    ^\s*
    (?P<year>\d{4})
    (?:[-/年](?P<month>\d{1,2})
        (?:[-/月](?P<day>\d{1,2})
            (?:[T\s日](?P<hour>\d{1,2})
                (?::(?P<minute>\d{1,2})
                    (?::(?P<second>\d{1,2}))?
                )?
            )?
        )?
    )?
    (?:\s*(?P<tz>Z|[+-]\d{2}:?\d{2}))?
    \s*$
    """,
    re.VERBOSE,
)


def _parse_timezone(offset: Optional[str]) -> timezone:
    if not offset or offset == "Z":
        return timezone.utc
    sign = 1 if offset[0] == "+" else -1
    digits = offset[1:].replace(":", "")
    hours = int(digits[:2])
    minutes = int(digits[2:]) if len(digits) > 2 else 0
    delta = timedelta(hours=hours, minutes=minutes) * sign
    return timezone(delta)


def _parse_iso_like(raw: str) -> Tuple[Optional[datetime], Set[str]]:
    match = ISO_PATTERN.match(raw)
    if not match:
        return None, set()

    parts: dict[str, int] = {}
    provided: Set[str] = set()
    for key in ("year", "month", "day", "hour", "minute", "second"):
        value = match.group(key)
        if value is not None:
            provided.add(key)
            parts[key] = int(value)

    tz = _parse_timezone(match.group("tz"))

    year = parts.get("year", datetime.now(timezone.utc).year)
    month = parts.get("month", 1)
    day = parts.get("day", 1)
    hour = parts.get("hour", 0)
    minute = parts.get("minute", 0)
    second = parts.get("second", 0)

    try:
        dt = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except ValueError:
        return None, set()

    return dt.astimezone(timezone.utc), provided


def _detect_components_from_raw(raw: str) -> Set[str]:
    raw = raw.strip()
    if not raw:
        return set()

    _, provided = _parse_iso_like(raw)
    if provided:
        return provided

    try:
        parsedate_to_datetime(raw)
    except Exception:
        return set()
    return {"year", "month", "day", "hour", "minute", "second"}


def _ensure_datetime(value: Optional[datetime], raw: str) -> Tuple[Optional[datetime], Set[str]]:
    if value is not None:
        provided = _detect_components_from_raw(raw)
        if not provided:
            provided = {"year", "month", "day", "hour", "minute", "second"}
        return _to_utc(value), provided

    raw = raw.strip()
    if not raw:
        return None, set()

    dt, provided = _parse_iso_like(raw)
    if dt is not None:
        return dt, provided

    try:
        dt = parsedate_to_datetime(raw)
    except Exception:
        return None, set()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc), {"year", "month", "day", "hour", "minute", "second"}


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fill_missing(
    dt: datetime,
    provided: Set[str],
    now: datetime,
) -> datetime:
    base = dt
    matches_now = bool(provided.intersection({"year", "month", "day", "hour"}))
    if matches_now:
        for field in ("year", "month", "day", "hour"):
            if field in provided and getattr(base, field) != getattr(now, field):
                matches_now = False
                break

    fallback_source = now if matches_now else None

    year = base.year
    month = base.month
    day = base.day
    hour = base.hour
    minute = 0 if "minute" in provided else base.minute
    second = 0 if "second" in provided else base.second

    if "year" not in provided:
        year = fallback_source.year if fallback_source else year
    if "month" not in provided:
        month = fallback_source.month if fallback_source else FALLBACK_FILL_VALUE
    if "day" not in provided:
        day = fallback_source.day if fallback_source else FALLBACK_FILL_VALUE
    if "hour" not in provided:
        hour = fallback_source.hour if fallback_source else FALLBACK_FILL_VALUE
    if "minute" not in provided:
        minute = fallback_source.minute if fallback_source else FALLBACK_FILL_VALUE
    if "second" not in provided:
        second = fallback_source.second if fallback_source else FALLBACK_FILL_VALUE

    try:
        normalized = base.replace(
            year=year,
            month=month,
            day=day,
            hour=hour,
            minute=minute,
            second=second,
            microsecond=0,
        )
    except ValueError:
        # Fallback to safe defaults if replacement fails.
        normalized = base.replace(
            year=year,
            month=max(1, min(12, month)),
            day=min(28, day),
            hour=hour % 24,
            minute=minute % 60,
            second=second % 60,
            microsecond=0,
        )

    return normalized


def normalize_published_datetime(
    value: Optional[datetime] = None,
    raw: str | None = None,
    *,
    now: Optional[datetime] = None,
) -> str:
    """Normalize published datetimes across scrapers.

    Parameters
    ----------
    value:
        A parsed :class:`datetime.datetime` object, if available.
    raw:
        The original string value from the source.
    now:
        Reference "current" time. Defaults to ``datetime.now(timezone.utc)``.

    Returns
    -------
    str
        ISO-8601 formatted string after normalization, or ``""`` when parsing fails.
    """

    raw_text = (raw or "").strip()
    dt, provided = _ensure_datetime(value, raw_text)
    if dt is None:
        return ""

    reference = now or datetime.now(timezone.utc)
    normalized = _fill_missing(dt, provided, reference)
    return normalized.astimezone(timezone.utc).isoformat()

