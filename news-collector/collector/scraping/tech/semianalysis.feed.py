from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

import feedparser
import re
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


RSS_URL = "https://semianalysis.com/feed/"
SOURCE = "semianalysis"
CATEGORY = "tech"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


_DETAIL_CACHE: Dict[str, str] = {}


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all([
        "script",
        "style",
        "noscript",
        "svg",
        "img",
        "video",
        "figure",
        "iframe",
        "form",
        "header",
        "footer",
        "nav",
        "aside",
    ]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return _clean_text(text)


def fetch_feed(url: str = RSS_URL) -> feedparser.FeedParserDict:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def _parse_dt(entry: Any) -> Optional[datetime]:
    for key in ("published_parsed", "updated_parsed"):
        st = getattr(entry, key, None)
        if st:
            try:
                return datetime(*st[:6], tzinfo=timezone.utc)
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
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
    return None


def _extract_detail(entry: Any) -> str:
    content_list = entry.get("content")
    if isinstance(content_list, list) and content_list:
        html = content_list[0].get("value") or ""
        return _html_to_text(html)
    sd = entry.get("summary_detail") or {}
    if isinstance(sd, dict) and sd.get("value"):
        val = sd.get("value") or ""
        ctype = (sd.get("type") or "").lower()
        return _clean_text(val) if ctype.startswith("text/") else _html_to_text(val)
    summary = entry.get("summary")
    if isinstance(summary, str) and summary:
        return _html_to_text(summary)
    return ""


def collect_entries(feed: Any, limit: int = 20) -> List[Dict[str, str]]:
    sortable: List[tuple[datetime, Dict[str, str]]] = []
    _DETAIL_CACHE.clear()
    for entry in getattr(feed, "entries", []):
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        dt = _parse_dt(entry)
        raw = entry.get("published") or entry.get("updated") or ""
        published = normalize_published_datetime(dt, raw)
        detail = _extract_detail(entry)
        if detail:
            _DETAIL_CACHE[link] = detail
        payload = {
            "title": title,
            "url": link,
            "published": published,
            "source": SOURCE,
            "category": CATEGORY,
        }
        sort_key = dt or datetime.min.replace(tzinfo=timezone.utc)
        sortable.append((sort_key, payload))

    sortable.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in sortable[:limit]]


def process_entries(feed: Any) -> List[Dict[str, str]]:
    return collect_entries(feed, limit=50)


def fetch_article_detail(url: str) -> str:
    cached = _DETAIL_CACHE.get(url)
    if cached:
        return cached
    try:
        feed = fetch_feed(RSS_URL)
        for entry in getattr(feed, "entries", []):
            if (entry.get("link") or "").strip() == url:
                detail = _extract_detail(entry)
                if detail:
                    _DETAIL_CACHE[url] = detail
                    return detail
    except Exception:
        pass
    return ""


def main(limit: int = 10) -> None:
    feed = fetch_feed(RSS_URL)
    for item in collect_entries(feed, limit=limit):
        print(f"{item['published']} - {item['title']} - {item['url']}")


if __name__ == "__main__":
    main()

