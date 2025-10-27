from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List

import feedparser
import requests
from bs4 import BeautifulSoup

API_URL = (
    "https://export.arxiv.org/api/query?search_query=cat:cs.AI&"
    "sortBy=submittedDate&sortOrder=descending&max_results=50"
)
SOURCE = "arxiv.cs_ai"
CATEGORY = "tech"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def fetch_feed(url: str = API_URL) -> feedparser.FeedParserDict:
    """Fetch the Atom feed for the latest cs.AI submissions."""
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    return feedparser.parse(resp.text)


def _normalize_iso(dt_str: str) -> str:
    if not dt_str:
        return ""
    cleaned = dt_str.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return cleaned


def parse_datetime(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except Exception:
                continue

    for key in ("published", "updated"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            try:
                normalized = _normalize_iso(raw)
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
    return None


def process_entries(feed: feedparser.FeedParserDict) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for entry in feed.entries:
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip() or next(
            (l.get("href") for l in entry.get("links", []) if l.get("rel") == "alternate"),
            "",
        )
        dt = parse_datetime(entry)
        published = dt.isoformat() if dt else _normalize_iso(
            entry.get("published") or entry.get("updated") or ""
        )
        results.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )

    def sort_key(item: Dict[str, str]) -> datetime:
        raw = item.get("published", "")
        if not raw:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            normalized = _normalize_iso(raw)
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    results.sort(key=sort_key, reverse=True)
    return results


def _clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\u00a0", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup.find_all("span", class_="descriptor"):
        tag.decompose()

    abstract_node = soup.select_one("blockquote.abstract")
    if abstract_node and abstract_node.get_text(strip=True):
        text = abstract_node.get_text("\n", strip=True)
        return _clean_text(text)

    main = soup.find("article") or soup.body or soup
    return _clean_text(main.get_text("\n", strip=True))


if __name__ == "__main__":
    feed = fetch_feed()
    items = process_entries(feed)
    for item in items[:10]:
        print(item["published"], "-", item["title"], "-", item["url"])
