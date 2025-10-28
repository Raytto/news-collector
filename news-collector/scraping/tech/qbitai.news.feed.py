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
    import importlib.util
    import sys
    from pathlib import Path

    helper_path = Path(__file__).resolve().parents[1] / "_datetime.py"
    module_name = "scraping_datetime_helper"
    helper = sys.modules.get(module_name)
    if helper is None:
        spec = importlib.util.spec_from_file_location(module_name, helper_path)
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        sys.modules[module_name] = helper
    normalize_published_datetime = helper.normalize_published_datetime

SOURCE = "qbitai-news"
CATEGORY = "tech"

FEED_URL = "https://www.qbitai.com/category/资讯/feed"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 30


def fetch_feed(url: str = FEED_URL) -> feedparser.FeedParserDict:
    """Fetch the RSS feed for QbitAI 资讯."""

    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_datetime(entry) -> datetime | None:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
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
        try:
            return _ensure_utc(dt)
        except Exception:
            continue

    return None


def _normalize_url(link: str) -> str:
    if not link:
        return ""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    if not link.startswith("/"):
        link = "/" + link
    return f"https://www.qbitai.com{link}"


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


def _strip_noise(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "img",
            "video",
            "iframe",
            "figure",
            "header",
            "footer",
            "nav",
            "form",
            "aside",
        ]
    ):
        tag.decompose()

    for noisy in soup.select('[class*="share" i], [id*="share" i], .meta, .post-meta'):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "article .entry-content",
        "article .content",
        "article",
        "div.entry-content",
        "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    _strip_noise(soup)
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


if __name__ == "__main__":
    articles = collect_latest(5)
    for item in articles:
        print(f"- {item['title']} ({item['published']}) -> {item['url']}")
