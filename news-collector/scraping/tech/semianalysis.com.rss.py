from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List

import feedparser
import requests
from bs4 import BeautifulSoup

SOURCE = "semianalysis"
CATEGORY = "tech"

FEED_URL = "https://semianalysis.com/feed/"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30

_FEED_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
}
_ARTICLE_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def fetch_feed(url: str = FEED_URL) -> feedparser.FeedParserDict:
    """Fetch the RSS feed for SemiAnalysis."""

    resp = requests.get(url, headers=_FEED_HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _parse_datetime(entry) -> datetime | None:
    """Parse a datetime from a feed entry, normalising to UTC."""

    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except Exception:
                continue

    for key in ("published", "updated", "created", "pubDate"):
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
    return f"https://semianalysis.com{link}"


def process_entries(feed: feedparser.FeedParserDict) -> List[dict]:
    results: List[dict] = []
    for entry in getattr(feed, "entries", []):
        title = entry.get("title", "").strip()
        link = _normalize_url(entry.get("link", ""))
        dt = _parse_datetime(entry)
        published = dt.isoformat() if dt else entry.get("published", "")

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

    for noisy in soup.select('[aria-label*="share" i], [class*="share" i], [class*="subscribe" i]'):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "article .body",
        "article .body.markup",
        "article [data-testid='post-body']",
        "article",
        "main article",
        "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers=_ARTICLE_HEADERS, timeout=TIMEOUT)
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
