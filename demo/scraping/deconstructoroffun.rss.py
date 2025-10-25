import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

RSS_URL = "https://www.deconstructoroffun.com/blog?format=rss"


def fetch_feed(url: str):
    d = feedparser.parse(url)
    if d.bozo:
        print("解析 RSS 时可能有问题:", getattr(d, "bozo_exception", None))
    return d


def parse_dt(entry):
    for key in ("published_parsed", "updated_parsed"):
        if getattr(entry, key, None):
            try:
                return datetime(*entry[key][:6], tzinfo=timezone.utc)
            except Exception:
                pass

    for key in ("published", "updated"):
        val = entry.get(key)
        if val:
            try:
                dt = parsedate_to_datetime(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                try:
                    dt = datetime.fromisoformat(val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except Exception:
                    continue
    return None


def process_entries(feed):
    results = []
    for e in feed.entries:
        title = e.get("title", "")
        link = e.get("link", "")
        dt = parse_dt(e)
        published = dt.isoformat() if dt else e.get("published", e.get("updated", ""))
        results.append(
            {
                "title": title,
                "url": link,
                "published": published,
            }
        )

    def sort_key(x):
        try:
            return datetime.fromisoformat(x["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    results.sort(key=sort_key, reverse=True)
    return results


if __name__ == "__main__":
    feed = fetch_feed(RSS_URL)
    items = process_entries(feed)
    for it in items[:10]:
        print(it["published"], "-", it["title"], "-", it["url"])
