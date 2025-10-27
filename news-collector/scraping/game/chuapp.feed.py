from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List

import feedparser
import requests
import re

RSS_URL = "https://www.chuapp.com/feed"
SOURCE = "chuapp"
CATEGORY = "game"


def _sanitize_html_entities(xml_text: str) -> str:
    # Replace common HTML entities that are undefined in XML with safe equivalents
    replacements = {
        "&nbsp;": " ",
        "&ensp;": " ",
        "&emsp;": " ",
        "&ndash;": "-",
        "&mdash;": "-",
        "&lsquo;": "'",
        "&rsquo;": "'",
        "&ldquo;": '"',
        "&rdquo;": '"',
        "&hellip;": "...",
    }
    # Fast path for direct replacements
    for k, v in replacements.items():
        if k in xml_text:
            xml_text = xml_text.replace(k, v)
    # Collapse stray unescaped ampersands in text nodes like 'AT&T' -> 'AT&amp;T'
    # Heuristic: replace '&' not followed by a known entity pattern
    xml_text = re.sub(r"&(?![a-zA-Z#][a-zA-Z0-9]+;)", "&amp;", xml_text)
    return xml_text


def fetch_feed(url: str = RSS_URL):
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
        }, timeout=20)
        resp.raise_for_status()
        text = resp.text
        text = _sanitize_html_entities(text)
        feed = feedparser.parse(text)
    except Exception:
        # Fallback to feedparser's internal fetch if requests fails
        feed = feedparser.parse(url)

    if getattr(feed, "bozo", False):
        # Only log truly unexpected parse errors; ignore undefined entity noise we already sanitized
        exc = getattr(feed, "bozo_exception", None)
        if exc and "undefined entity" not in str(exc).lower():
            print("解析 RSS 时可能有问题:", exc)
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


# -----------------------
# Article detail fetching
# -----------------------
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
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
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
