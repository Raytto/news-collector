from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List

import feedparser

RSS_URL = "https://www.chuapp.com/feed"
SOURCE = "chuapp"
CATEGORY = "game"


def fetch_feed(url: str = RSS_URL):
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", False):
        print("解析 RSS 时可能有问题:", getattr(feed, "bozo_exception", None))
    return feed


def _to_datetime(entry: Any) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        struct_time = getattr(entry, key, None)
        if struct_time:
            try:
                return datetime(*struct_time[:6], tzinfo=timezone.utc)
            except Exception:
                continue

    for key in ("published", "updated"):
        value = entry.get(key)
        if not value:
            continue
        try:
            dt = parsedate_to_datetime(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            try:
                dt = datetime.fromisoformat(value)
            except Exception:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
    return None


def collect_entries(feed: Any, limit: int = 10) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    sortable: List[tuple[datetime, Dict[str, str]]] = []
    for entry in getattr(feed, "entries", []):
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        dt = _to_datetime(entry)
        published = dt.isoformat() if dt else entry.get("published", entry.get("updated", ""))
        data = {"title": title, "url": link, "published": published, "source": SOURCE, "category": CATEGORY}
        sort_key = dt or datetime.min.replace(tzinfo=timezone.utc)
        sortable.append((sort_key, data))

    sortable.sort(key=lambda item: item[0], reverse=True)
    for _, data in sortable[:limit]:
        items.append(data)
    return items


def main(limit: int = 10) -> None:
    feed = fetch_feed(RSS_URL)
    for item in collect_entries(feed, limit=limit):
        print(f"{item['source']} - {item['published']} - {item['title']} - {item['url']}")


if __name__ == "__main__":
    main()
