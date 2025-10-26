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


# -----------------------
# Article detail fetching
# -----------------------
import re
import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "div.entry-content",
        "article .entry-content",
        "article",
        "main .content",
        ".content",
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://nikopartners.com/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    html: str
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        # Fallback via readability proxy to bypass 403/WAF
        jurl = f"https://r.jina.ai/{url}"
        jresp = requests.get(jurl, headers={"User-Agent": UA}, timeout=30)
        jresp.raise_for_status()
        md = jresp.text
        md = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", md)
        md = re.sub(r"(^|\s)[#*_`]+|[#*_`]+($|\s)", " ", md)
        md = re.sub(r"\r\n?", "\n", md)
        md = re.sub(r"\n{3,}", "\n\n", md)
        return md.strip()
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all([
        "script",
        "style",
        "noscript",
        "svg",
        "img",
        "video",
        "figure",
        "iframe",
        "header",
        "footer",
        "nav",
        "aside",
        "form",
    ]):
        tag.decompose()
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


if __name__ == "__main__":
    main()
