from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Dict

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

SOURCE = "ruanyifeng"
SOURCE_LABEL_ZH = "阮一峰的网络日志"
CATEGORY = "tech"

PRIMARY_FEED_URL = "https://www.ruanyifeng.com/blog/atom.xml"
FALLBACK_FEED_URL = "https://feeds.feedburner.com/ruanyifeng"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 20

_DETAIL_CACHE: Dict[str, str] = {}


def _normalize_url(link: str) -> str:
    url = (link or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://") :]
    if url.startswith("https://"):
        return url
    if not url.startswith("/"):
        url = "/" + url
    return f"https://www.ruanyifeng.com{url}"


def _parse_dt(entry) -> datetime | None:
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
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                continue
        try:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue

    return None


def _clean_text(text: str) -> str:
    s = text.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def _html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
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
            "form",
            "header",
            "footer",
            "nav",
            "aside",
        ]
    ):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    return _clean_text(text)


def _extract_detail(entry) -> str:
    content_list = entry.get("content")
    if isinstance(content_list, list) and content_list:
        html = content_list[0].get("value") or ""
        ctype = (content_list[0].get("type") or "").lower()
        if ctype.startswith("text/plain"):
            return _clean_text(html)
        return _html_to_text(html)

    sd = entry.get("summary_detail")
    if isinstance(sd, dict) and sd.get("value"):
        val = sd.get("value") or ""
        ctype = (sd.get("type") or "").lower()
        if ctype.startswith("text/plain"):
            return _clean_text(val)
        return _html_to_text(val)

    summary = entry.get("summary")
    if isinstance(summary, str) and summary:
        return _html_to_text(summary)

    desc = entry.get("description")
    if isinstance(desc, str) and desc:
        return _html_to_text(desc)

    return ""


def _fetch_content(url: str) -> bytes:
    headers = {
        "User-Agent": UA,
        "Accept": "application/atom+xml,application/rss+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    resp = requests.get(url, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.content


def fetch_feed(url: str = PRIMARY_FEED_URL) -> feedparser.FeedParserDict:
    """Fetch the RSS/Atom feed. Try the primary link first, fall back when blocked."""

    urls = [url]
    if FALLBACK_FEED_URL not in urls:
        urls.append(FALLBACK_FEED_URL)

    last_err: Exception | None = None
    for target in urls:
        try:
            content = _fetch_content(target)
            return feedparser.parse(content)
        except Exception as exc:
            last_err = exc
            continue

    if last_err:
        raise last_err
    raise RuntimeError("无法获取阮一峰博客的订阅源")


def process_entries(feed: feedparser.FeedParserDict) -> List[dict]:
    _DETAIL_CACHE.clear()
    results: List[dict] = []
    for entry in getattr(feed, "entries", []):
        title = (entry.get("title") or "").strip()
        link = _normalize_url(entry.get("link", ""))
        if not (title and link):
            continue

        dt = _parse_dt(entry)
        raw_time = entry.get("published") or entry.get("updated") or entry.get("created") or ""
        published = normalize_published_datetime(dt, raw_time)
        detail = _extract_detail(entry)
        if detail:
            _DETAIL_CACHE[link] = detail

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
    return process_entries(feed)[:limit]


def fetch_article_detail(url: str) -> str:
    cached = _DETAIL_CACHE.get(url)
    if cached:
        return cached

    try:
        feed = fetch_feed()
        for entry in getattr(feed, "entries", []):
            link = _normalize_url(entry.get("link", ""))
            if link == url:
                detail = _extract_detail(entry)
                if detail:
                    _DETAIL_CACHE[link] = detail
                    return detail
    except Exception:
        return ""
    return ""


if __name__ == "__main__":
    for item in collect_latest(5):
        print(f"{item['published']} - {item['title']} -> {item['url']}")
