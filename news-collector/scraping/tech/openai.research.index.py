from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Dict, List

import feedparser
import requests
from bs4 import BeautifulSoup

SOURCE = "openai.research"
CATEGORY = "tech"
RSS_URL = "https://openai.com/blog/rss.xml"
REQUEST_TIMEOUT = 30
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# Cache summaries for use when full article fetching is blocked.
SUMMARY_CACHE: Dict[str, str] = {}


def _to_iso8601(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return raw


def _clean_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)
    return unescape(text).strip()


def fetch_list_page(url: str = RSS_URL) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_list(xml_text: str) -> List[Dict[str, str]]:
    feed = feedparser.parse(xml_text)
    items: List[Dict[str, str]] = []
    for entry in getattr(feed, "entries", []):
        link = (entry.get("link") or "").strip()
        # Only keep research/index style posts (skip generic news landing page, etc.)
        if not link or "/index/" not in link:
            continue
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        published_raw = entry.get("published") or entry.get("updated") or ""
        published = _to_iso8601(published_raw)
        summary_html = entry.get("summary") or entry.get("description") or ""
        summary_text = _clean_html(summary_html)
        if summary_text:
            SUMMARY_CACHE[link] = summary_text
        items.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )

    def sort_key(item: Dict[str, str]) -> datetime:
        try:
            return datetime.fromisoformat((item.get("published") or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    items.sort(key=sort_key, reverse=True)
    return items


def fetch_article_detail(url: str) -> str:
    # Attempt to fetch via readability proxy first.
    proxy = f"https://r.jina.ai/{url}"
    try:
        resp = requests.get(proxy, headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
        if resp.status_code < 400:
            text = resp.text.strip()
            if text and "Just a moment" not in text[:40]:
                return text
    except Exception:
        pass
    # Fall back to cached RSS summary if available.
    return SUMMARY_CACHE.get(url, "")


def collect_latest(limit: int = 20) -> List[Dict[str, str]]:
    xml = fetch_list_page()
    items = parse_list(xml)
    return items[:limit]


if __name__ == "__main__":  # pragma: no cover - manual verification helper
    for entry in collect_latest(10):
        print(entry.get("published", ""), "-", entry.get("title", ""), "-", entry.get("url", ""))
