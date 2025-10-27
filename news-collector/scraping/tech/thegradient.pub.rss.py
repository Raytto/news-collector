from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List

import feedparser
import requests
from bs4 import BeautifulSoup

try:  # pragma: no cover - allow running as a script
    from .._datetime import normalize_published_datetime
except ImportError:  # pragma: no cover - fallback for direct execution
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from _datetime import normalize_published_datetime

SOURCE = "thegradient"
CATEGORY = "tech"

FEED_URL = "https://thegradient.pub/rss/"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30


def fetch_feed(url: str = FEED_URL) -> feedparser.FeedParserDict:
    """Fetch the RSS feed for The Gradient."""

    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _parse_datetime(entry) -> datetime | None:
    """Parse the published datetime from a feed entry."""

    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except Exception:
                continue

    for key in ("published", "updated", "created"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw)
            except Exception:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    return None


def _normalize_url(link: str) -> str:
    if not link:
        return ""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    if not link.startswith("/"):
        link = "/" + link
    return f"https://thegradient.pub{link}"


def process_entries(feed: feedparser.FeedParserDict) -> List[dict]:
    results: List[dict] = []
    for entry in getattr(feed, "entries", []):
        title = entry.get("title", "").strip()
        link = _normalize_url(entry.get("link", ""))
        dt = _parse_datetime(entry)
        raw_time = entry.get("published") or entry.get("updated") or entry.get("created") or ""
        published = normalize_published_datetime(dt, raw_time)

        if not title or not link:
            continue

        results.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )

    def sort_key(item: dict) -> datetime:
        try:
            return datetime.fromisoformat(item["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    results.sort(key=sort_key, reverse=True)
    return results


def collect_latest(limit: int = 20) -> List[dict]:
    feed = fetch_feed()
    entries = process_entries(feed)
    return entries[:limit]


# -----------------------
# Article detail fetching
# -----------------------


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(
        [
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
        ]
    ):
        tag.decompose()

    for noisy in soup.select('[aria-label*="share" i], [class*="share" i]'):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "article .post-full-content",
        "article [class*='content']",
        "main article",
        "article",
        "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    _strip_noise(soup)
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


def main() -> None:
    items = collect_latest()
    for item in items[:10]:
        print(item["published"], "-", item["title"], "-", item["url"])


if __name__ == "__main__":
    main()
