import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

RSS_URL = "https://www.gamedeveloper.com/rss.xml"
SOURCE = "gamedeveloper"
CATEGORY = "game"

def fetch_feed(url: str):
    d = feedparser.parse(url)
    if d.bozo:
        # bozo_exception 里通常是解析告警，不一定致命
        print("解析 RSS 时可能有问题:", getattr(d, "bozo_exception", None))
    return d

def parse_dt(entry):
    """
    依次尝试：
    - published_parsed / updated_parsed（feedparser已解析的struct_time）
    - published / updated（RFC 2822/1123或ISO8601样式，尝试用email.utils解析）
    - 失败则返回None
    """
    # 1) feedparser 自带的 *_parsed
    for key in ("published_parsed", "updated_parsed"):
        if getattr(entry, key, None):
            try:
                # struct_time -> datetime（UTC）
                return datetime(*entry[key][:6], tzinfo=timezone.utc)
            except Exception:
                pass

    # 2) 原始字符串字段
    for key in ("published", "updated"):
        val = entry.get(key)
        if val:
            try:
                dt = parsedate_to_datetime(val)  # 能解析RFC风格
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                # 有些源可能是ISO8601，必要时可再加一次fromisoformat尝试
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
        link  = e.get("link", "")
        dt    = parse_dt(e)
        # 若无时间，使用空字符串，或这里你也可以选择跳过
        published = dt.isoformat() if dt else e.get("published", e.get("updated", ""))
        results.append({
            "title": title,
            "url": link,
            "published": published,
            "source": SOURCE,
            "category": CATEGORY,
        })
    # 可选：按时间倒序
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
