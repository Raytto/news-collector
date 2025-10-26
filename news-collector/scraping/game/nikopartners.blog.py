from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Tuple

import feedparser

# WP Rocket 会对 /feed/ 做激进缓存（即便未提供条件请求头也直接返回 304），
# 所以追加一个无害的 query 参数来强制返回正文，避免脚本无法拿到 RSS。
RSS_URL = "https://nikopartners.com/feed/?nocache=1"
MAX_ITEMS = 10
SOURCE = "nikopartners"
CATEGORY = "game"
_MIN_SORT_KEY = datetime.min.replace(tzinfo=timezone.utc)


def fetch_feed(url: str = RSS_URL) -> feedparser.FeedParserDict:
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", False):
        exc = getattr(feed, "bozo_exception", "")
        print("解析 RSS 时出现警告:", exc)
    return feed


def _from_struct_time(struct_time) -> datetime | None:
    if not struct_time:
        return None
    try:
        return datetime(*struct_time[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_datetime(entry: Dict[str, Any]) -> Tuple[str, datetime | None]:
    for key in ("published_parsed", "updated_parsed"):
        dt = _from_struct_time(entry.get(key))
        if dt:
            return dt.astimezone(timezone.utc).isoformat(), dt

    for key in ("published", "updated"):
        raw = entry.get(key)
        if not raw:
            continue
        raw = raw.strip()
        for parser in (parsedate_to_datetime, datetime.fromisoformat):
            try:
                parsed = parser(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                parsed = parsed.astimezone(timezone.utc)
                return parsed.isoformat(), parsed
            except Exception:
                continue
        return raw, None

    return "", None


def collect_entries(feed: Any, limit: int = MAX_ITEMS) -> List[Dict[str, str]]:
    entries: List[Dict[str, Any]] = []
    for entry in getattr(feed, "entries", []):
        title = (entry.get("title") or "").strip()
        url = (entry.get("link") or "").strip()
        if not title or not url:
            continue
        published, dt = _parse_datetime(entry)
        entries.append(
            {
                "title": title,
                "url": url,
                "published": published,
                "source": SOURCE,
                "_sort_key": dt or _MIN_SORT_KEY,
            }
        )

    entries.sort(key=lambda item: item["_sort_key"], reverse=True)
    return [
        {"title": e["title"], "url": e["url"], "published": e["published"], "source": e["source"], "category": CATEGORY}
        for e in entries[:limit]
    ]


def main():
    feed = fetch_feed(RSS_URL)
    for entry in collect_entries(feed, MAX_ITEMS):
        print(entry["published"], "-", entry["title"], "-", entry["url"])


if __name__ == "__main__":
    main()
