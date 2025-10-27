from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, List

import feedparser
import requests
from bs4 import BeautifulSoup

SOURCE = "jiqizhixin"
CATEGORY = "tech"

BASE_URL = "https://www.jiqizhixin.com"
FEED_URL = f"{BASE_URL}/rss"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30


def _entry_get(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def fetch_feed(url: str = FEED_URL) -> feedparser.FeedParserDict:
    """Fetch the RSS feed for 机器之心."""

    response = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    response.raise_for_status()
    return feedparser.parse(response.content)


def _parse_struct_time(value: Any) -> datetime | None:
    if value is None:
        return None
    if hasattr(value, "tm_year"):
        try:
            return datetime(
                value.tm_year,
                value.tm_mon,
                value.tm_mday,
                value.tm_hour,
                value.tm_min,
                value.tm_sec,
                tzinfo=timezone.utc,
            )
        except Exception:
            return None
    if isinstance(value, (tuple, list)) and len(value) >= 6:
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _parse_datetime(entry: Any) -> datetime | None:
    """Parse the published datetime from a feed entry."""

    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        candidate = _entry_get(entry, key)
        dt = _parse_struct_time(candidate)
        if dt:
            return dt

    for key in ("published", "updated", "created"):
        raw = _entry_get(entry, key)
        if not raw or not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text:
            continue
        try:
            dt = parsedate_to_datetime(text)
        except Exception:
            try:
                dt = datetime.fromisoformat(text)
            except Exception:
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    return None


def _normalize_url(link: str) -> str:
    if not link:
        return ""
    text = link.strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if not text.startswith("/"):
        text = "/" + text
    return f"{BASE_URL}{text}"


def _extract_entry_html(entry: Any) -> str:
    content = _entry_get(entry, "content")
    if isinstance(content, (list, tuple)):
        for item in content:
            if isinstance(item, dict):
                html = item.get("value")
            else:
                html = getattr(item, "value", None)
            if html:
                return str(html)

    summary_detail = _entry_get(entry, "summary_detail")
    if summary_detail:
        html = _entry_get(summary_detail, "value")
        if html:
            return str(html)

    summary = _entry_get(entry, "summary")
    if isinstance(summary, str) and summary.strip():
        return summary

    return ""


def _extract_entry_detail(entry: Any) -> str:
    html = _extract_entry_html(entry)
    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")
    _strip_noise(soup)
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


def process_entries(feed: feedparser.FeedParserDict) -> List[dict]:
    results: List[dict] = []

    for entry in getattr(feed, "entries", []):
        title = _entry_get(entry, "title") or ""
        link = _entry_get(entry, "link") or ""
        published_dt = _parse_datetime(entry)
        published = published_dt.isoformat() if published_dt else ""
        detail = _extract_entry_detail(entry)

        title = title.strip()
        link = _normalize_url(link)

        if not title or not link:
            continue

        results.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
                "detail": detail,
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
    s = re.sub(r"\t", " ", s)
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    lines = [line.rstrip() for line in s.splitlines()]
    return "\n".join(lines).strip()


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
            "button",
        ]
    ):
        tag.decompose()

    for noisy in soup.select(
        "[aria-label*='share' i], [class*='share' i], [class*='social' i], [class*='related' i]"
    ):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "article .article-content",
        "article .post-content",
        "article .entry-content",
        "article .content",
        "div.article-content",
        "div.post-content",
        "div.entry-content",
        "article",
        "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    if soup.body and soup.body.get_text(strip=True):
        return soup.body
    return soup


def fetch_article_detail(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
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
