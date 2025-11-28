from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Dict, List
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
        spec.loader.exec_module(helper)  # type: ignore[attr-defined]
        sys.modules[module_name] = helper
    normalize_published_datetime = helper.normalize_published_datetime  # type: ignore[attr-defined]


def _clean_description(text: str) -> str:
    if not text:
        return ""
    s = html.unescape(text)
    s = re.sub(r"\r\n?", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def _extract_video_id(url: str) -> str:
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

    parsed = urlparse(url or "")
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


def _extract_thumbnail(entry, video_id: str = "") -> str:
    thumbs = entry.get("media_thumbnail")
    if isinstance(thumbs, list) and thumbs:
        cand = thumbs[0]
        if isinstance(cand, dict):
            url = cand.get("url") or cand.get("href")
            if url:
                return str(url).strip()
    if not video_id:
        video_id = entry.get("yt_videoid") or _extract_video_id(entry.get("link") or "")
    if video_id:
        return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    return ""


def _sort_key(item: dict) -> datetime:
    try:
        return datetime.fromisoformat(str(item.get("published") or "").replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def fetch_feed(channel_id: str):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", False):
        exc = getattr(feed, "bozo_exception", None)
        try:
            print(f"解析 RSS 时可能有问题: {url} -> {exc}")
        except Exception:
            pass
    return feed


def collect_entries(
    channel_id: str,
    source_key: str,
    category: str,
    desc_cache: Dict[str, str],
) -> List[Dict[str, str]]:
    cache = desc_cache if desc_cache is not None else {}
    feed = fetch_feed(channel_id)
    items: List[Dict[str, str]] = []

    for entry in getattr(feed, "entries", []):
        url = entry.get("link") or ""
        vid = entry.get("yt_videoid") or _extract_video_id(url)
        if not url and vid:
            url = f"https://www.youtube.com/watch?v={vid}"

        desc = _extract_description(entry)
        if vid and desc:
            cache[vid] = desc

        items.append(
            {
                "title": entry.get("title", "") or "",
                "url": url,
                "published": _normalize_datetime(entry),
                "img": _extract_thumbnail(entry, vid),
                "source": source_key,
                "category": category,
            }
        )

    items.sort(key=_sort_key, reverse=True)
    return items


def fetch_detail(channel_id: str, url: str, desc_cache: Dict[str, str]) -> str:
    cache = desc_cache if desc_cache is not None else {}
    vid = _extract_video_id(url)
    if vid and vid in cache:
        return cache[vid]
    if not vid:
        return ""

    feed = fetch_feed(channel_id)
    for entry in getattr(feed, "entries", []):
        entry_vid = entry.get("yt_videoid") or _extract_video_id(entry.get("link") or "")
        if entry_vid != vid:
            continue
        desc = _extract_description(entry)
        if desc:
            cache[vid] = desc
            return desc
        break
    return ""
