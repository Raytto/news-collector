from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import List
from urllib.parse import parse_qs, urlparse

import feedparser

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


RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id=UC33VP_gIBzCVcEvaGKBocZA"
SOURCE = "youtube-luo-yonghao-crossroads"
CATEGORY = "general"
SOURCE_LABEL = "YouTube: Crossroads with Luo Yonghao"
SOURCE_LABEL_ZH = "YouTube：罗永浩的十字路口"

# Cache description by video ID so fetch_article_detail can return from feed.
_DESC_BY_ID: dict[str, str] = {}


def fetch_feed(url: str = RSS_URL):
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", False):
        print(f"解析 RSS 时可能有问题: {url} ({SOURCE}) ->", getattr(feed, "bozo_exception", None))
    return feed


def _normalize_datetime(entry) -> str:
    dt = None
    for key in ("published_parsed", "updated_parsed"):
        val = entry.get(key)
        if val:
            try:
                dt = datetime(*val[:6], tzinfo=timezone.utc)
                break
            except Exception:
                continue
    raw = entry.get("published") or entry.get("updated") or ""
    return normalize_published_datetime(dt, raw)


def _extract_description(entry) -> str:
    desc_raw = (
        entry.get("media_description")
        or entry.get("summary_detail", {}).get("value")
        or entry.get("summary")
        or ""
    )
    return _clean_description(desc_raw)


def collect_entries(feed, limit: int | None = None) -> List[dict]:
    items: List[dict] = []
    for entry in getattr(feed, "entries", []):
        url = entry.get("link") or ""
        vid = entry.get("yt_videoid") or _extract_video_id(url)
        if not url and vid:
            url = f"https://www.youtube.com/watch?v={vid}"

        desc = _extract_description(entry)
        if vid and desc:
            _DESC_BY_ID[vid] = desc
        items.append(
            {
                "title": entry.get("title", ""),
                "url": url,
                "published": _normalize_datetime(entry),
                "source": SOURCE,
                "category": CATEGORY,
            }
        )
    items.sort(key=_sort_key, reverse=True)
    return items[:limit] if limit else items


def _sort_key(item: dict):
    try:
        return datetime.fromisoformat(item.get("published") or "").astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def _extract_video_id(url: str) -> str:
    # Common patterns: watch?v=, youtu.be/, shorts/, embed/
    patterns = [
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"/shorts/([A-Za-z0-9_-]{11})",
        r"/embed/([A-Za-z0-9_-]{11})",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)

    parsed = urlparse(url)
    if parsed.query:
        qs = parse_qs(parsed.query)
        vals = qs.get("v") or []
        if vals:
            candidate = vals[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
                return candidate

    parts = [p for p in (parsed.path or "").split("/") if p]
    if parts:
        candidate = parts[-1]
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate):
            return candidate
    return ""


def _clean_description(text: str) -> str:
    if not text:
        return ""
    s = html.unescape(text)
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def fetch_article_detail(url: str) -> str:
    video_id = _extract_video_id(url)
    if video_id and video_id in _DESC_BY_ID:
        return _DESC_BY_ID[video_id]
    if not video_id:
        return ""

    try:
        feed = fetch_feed(RSS_URL)
    except Exception:
        feed = None

    if feed:
        for entry in getattr(feed, "entries", []):
            vid = entry.get("yt_videoid") or _extract_video_id(entry.get("link") or "")
            if vid != video_id:
                continue
            desc = _extract_description(entry)
            if desc:
                _DESC_BY_ID[vid] = desc
                return desc
    return ""


if __name__ == "__main__":  # pragma: no cover - manual sanity check
    feed = fetch_feed(RSS_URL)
    items = collect_entries(feed, limit=5)
    for it in items:
        print(it["published"], "-", it["title"], "-", it["url"])
    if items:
        print("\n示例描述抓取...")
        print(fetch_article_detail(items[0]["url"])[:500])
