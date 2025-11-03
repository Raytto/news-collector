from __future__ import annotations

from datetime import datetime
import os
from typing import Any, Iterable, Optional

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def normalize(days: list[int] | None) -> list[int] | None:
    """Normalize a weekday list to sorted unique ints in 1..7.

    - None -> None (unrestricted)
    - []   -> []   (never run)
    - [..] -> sorted unique subset of 1..7
    """
    if days is None:
        return None
    try:
        xs = {int(x) for x in days if 1 <= int(x) <= 7}
    except Exception:
        return []
    return sorted(xs)


def parse(value: Any) -> list[int] | None:
    """Strict parser: accept only list/tuple of numbers or None.

    Any other type raises ValueError to enforce API contracts.
    """
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        return normalize([int(x) for x in value]) or []
    raise ValueError("weekdays_json must be array or null")


def coerce(value: Any) -> list[int] | None:
    """Lenient parser for transition period.

    Accepts: list/tuple, JSON-like strings ("[2,3]"), CSV ("2,3,4"), single number, bytes.
    Returns normalized list[int] or None.
    """
    if value is None:
        return None
    v = value
    if isinstance(v, (bytes, bytearray)):
        try:
            v = v.decode("utf-8", errors="ignore")
        except Exception:
            return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        # Try JSON array first
        try:
            import json as _json

            parsed = _json.loads(s)
        except Exception:
            # CSV fallback
            parts = [p.strip() for p in s.split(",") if p.strip()]
            try:
                vals = [int(p) for p in parts]
            except Exception:
                return None
            return normalize(vals) or []
        else:
            if isinstance(parsed, (list, tuple)):
                try:
                    vals = [int(x) for x in parsed]
                except Exception:
                    return []
                return normalize(vals) or []
            if isinstance(parsed, (int, float)):
                try:
                    pi = int(parsed)
                except Exception:
                    return None
                return normalize([pi]) or []
            return None
    if isinstance(v, (list, tuple)):
        try:
            vals = [int(x) for x in v]
        except Exception:
            return []
        return normalize(vals) or []
    if isinstance(v, (int, float)):
        try:
            return normalize([int(v)]) or []
        except Exception:
            return None
    return None


def is_allowed(days: list[int] | None, dt: Optional[datetime] = None, tz: str = "Asia/Shanghai") -> bool:
    """Return whether a run is allowed for the given day set.

    - None -> True (unrestricted)
    - []   -> False (never)
    - else -> today in days
    """
    if days is None:
        return True
    if not days:
        return False
    if dt is None:
        if ZoneInfo is not None:
            try:
                dt = datetime.now(ZoneInfo(tz))
            except Exception:
                dt = datetime.now()
        else:
            dt = datetime.now()
    return dt.isoweekday() in days


def to_tag(days: list[int] | None) -> str:
    """Convert weekday set to human-readable tag."""
    if days is None:
        return "不限制"
    if not days:
        return "不按星期"
    xs = normalize(days) or []
    if xs == [1, 2, 3, 4, 5, 6, 7]:
        return "每天"
    if xs == [1, 2, 3, 4, 5]:
        return "工作日"
    if xs == [6, 7]:
        return "周末"
    return "自定义"


def to_mask(days: list[int] | None) -> int:
    """Bitmask representation where bit (day-1) is set if allowed.

    None (unrestricted) encodes as 0x7F (all days) for convenience.
    """
    if days is None:
        days = [1, 2, 3, 4, 5, 6, 7]
    xs = normalize(days) or []
    mask = 0
    for d in xs:
        mask |= (1 << (d - 1))
    return mask


def from_mask(mask: int) -> list[int]:
    """Decode bitmask back to weekday list."""
    try:
        m = int(mask)
    except Exception:
        return []
    days: list[int] = []
    for d in range(1, 8):
        if m & (1 << (d - 1)):
            days.append(d)
    return days

