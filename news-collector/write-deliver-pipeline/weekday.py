from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def normalize(days: list[int] | None) -> list[int] | None:
    if days is None:
        return None
    try:
        xs = {int(x) for x in days if 1 <= int(x) <= 7}
    except Exception:
        return []
    return sorted(xs)


def coerce(value: Any) -> list[int] | None:
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
        try:
            import json as _json

            parsed = _json.loads(s)
        except Exception:
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

