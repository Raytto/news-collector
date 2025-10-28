from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Iterable, List

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

RSS_URL = "https://stratechery.passport.online/feed/rss/CUXZnvi6sHPnV39z2Hje1"
SOURCE = "stratechery"
CATEGORY = "tech"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30


def _entry_get(entry: Any, key: str) -> Any:
    if isinstance(entry, dict):
        return entry.get(key)
    return getattr(entry, key, None)


def fetch_feed(url: str = RSS_URL) -> feedparser.FeedParserDict:
    """Fetch and parse the Stratechery Passport RSS feed."""

    headers = {"User-Agent": UA, "Accept": "application/rss+xml,application/xml"}
    response = requests.get(url, headers=headers, timeout=TIMEOUT)
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


def _parse_datetime(entry: Any) -> tuple[str, datetime | None]:
    raw_candidates = []
    for key in ("published", "updated", "created"):
        value = _entry_get(entry, key)
        if isinstance(value, str) and value.strip():
            raw_candidates.append(value.strip())
    raw = raw_candidates[0] if raw_candidates else ""

    for key in ("published_parsed", "updated_parsed", "created_parsed"):
        parsed = _entry_get(entry, key)
        dt = _parse_struct_time(parsed)
        if dt:
            dt = dt.astimezone(timezone.utc)
            normalized = normalize_published_datetime(dt, raw)
            try:
                parsed_dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except Exception:
                parsed_dt = dt
            return normalized, parsed_dt

    for key in ("published", "updated", "created"):
        raw_value = _entry_get(entry, key)
        if not isinstance(raw_value, str):
            continue
        text = raw_value.strip()
        if not text:
            continue
        for parser in (parsedate_to_datetime, datetime.fromisoformat):
            try:
                dt = parser(text)
            except Exception:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            dt = dt.astimezone(timezone.utc)
            normalized = normalize_published_datetime(dt, text)
            try:
                parsed_dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
            except Exception:
                parsed_dt = dt
            return normalized, parsed_dt

    normalized = normalize_published_datetime(None, raw)
    try:
        parsed_dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except Exception:
        parsed_dt = None
    return normalized, parsed_dt


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
        text = f"/{text}"
    return f"https://stratechery.com{text}"


def _extract_entry_html(entry: Any) -> str:
    content = _entry_get(entry, "content")
    if isinstance(content, list):
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

    for noisy in soup.select(
        "[class*='share' i], [class*='subscribe' i], [class*='promo' i], [aria-label*='share' i]"
    ):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup) -> BeautifulSoup:
    selectors: Iterable[str] = (
        "article",
        "main article",
        "main .entry-content",
        ".entry-content",
        "#content",
        "body",
    )
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    return soup


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r"(?<!\n)\n(?!\n)", " ", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


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
        title = (_entry_get(entry, "title") or "").strip()
        link = _normalize_url(_entry_get(entry, "link") or "")
        if not title or not link:
            continue

        published, sort_dt = _parse_datetime(entry)
        detail = _extract_entry_detail(entry)

        results.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
                "detail": detail,
                "_sort_key": sort_dt,
            }
        )

    def sort_key(item: dict) -> datetime:
        try:
            key = item.get("_sort_key")
            if isinstance(key, datetime):
                return key
            return datetime.fromisoformat((item.get("published") or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    results.sort(key=sort_key, reverse=True)
    for item in results:
        item.pop("_sort_key", None)
    return results


def collect_latest(limit: int = 20) -> List[dict]:
    feed = fetch_feed()
    entries = process_entries(feed)
    return entries[:limit]


def main() -> None:
    items = collect_latest()
    for item in items[:10]:
        print(item["published"], "-", item["title"], "-", item["url"])


if __name__ == "__main__":
    main()
