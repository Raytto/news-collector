from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qs, urlencode, urlparse

import feedparser
import requests

try:
    from PIL import Image  # type: ignore
except Exception as exc:  # pragma: no cover
    raise SystemExit("缺少 Pillow 依赖，请先安装：pip install Pillow") from exc

try:
    from .._datetime import normalize_published_datetime
except ImportError:  # pragma: no cover - fallback for direct execution
    import importlib.util
    import sys

    helper_path = Path(__file__).resolve().parents[1] / "_datetime.py"
    module_name = "scraping_datetime_helper"
    helper = sys.modules.get(module_name)
    if helper is None:
        spec = importlib.util.spec_from_file_location(module_name, str(helper_path))
        if spec and spec.loader:
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)  # type: ignore[attr-defined]
            sys.modules[module_name] = helper
        else:  # pragma: no cover
            raise ImportError("cannot load _datetime helper")
    normalize_published_datetime = helper.normalize_published_datetime  # type: ignore[attr-defined]


CHANNEL_ID = "UCxqmlztVA1plOoXqYigJy9g"
SOURCE = "new_games_daily"
CATEGORY = "game_yt"
TIMEOUT = 10
MAX_FEED_ENTRIES = 50  # allow more items; bounded by BUDGET_SEC
MAX_LINKS_PER_VIDEO = 8
BUDGET_SEC = 30.0  # keep margin under outer 40s timeout

ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = ROOT / "data"
TEMP_DIR = DATA_DIR / "temp"
DB_PATH = DATA_DIR / "info.db"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

_DESC_CACHE: Dict[str, str] = {}
_DETAIL_CACHE: Dict[str, str] = {}
_STORE_CACHE: Dict[str, Dict[str, str]] = {}
_EXISTING_STORE_LINKS: Optional[set[str]] = None
IMG_MAX_SIDE = 900  # shrink screenshot requests to keep size low
_RUN_START_TS = 0.0


def _clean_description(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return "\n".join(line.rstrip() for line in s.splitlines()).strip()


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


def _parse_timecode_to_seconds(code: str) -> Optional[int]:
    parts = code.strip().split(":")
    if not parts or not all(p.isdigit() for p in parts):
        return None
    parts = [int(p) for p in parts]
    if len(parts) == 2:
        m, s = parts
        return m * 60 + s
    if len(parts) == 3:
        h, m, s = parts
        return h * 3600 + m * 60 + s
    return None


def _parse_chapters(desc: str) -> List[Tuple[int, str]]:
    chapters: List[Tuple[int, str]] = []
    for line in desc.splitlines():
        m = re.match(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s+(.+)$", line.strip())
        if not m:
            continue
        sec = _parse_timecode_to_seconds(m.group(1))
        if sec is None:
            continue
        chapters.append((sec, m.group(2).strip()))
    return chapters


def _parse_itunes_links(desc: str) -> List[str]:
    links: List[str] = []
    pattern = re.compile(r"https?://(?:apps|itunes)\.apple\.com/[^\s)<>]+", re.IGNORECASE)
    for m in pattern.finditer(desc):
        url = m.group(0).strip().rstrip(")")
        if url not in links:
            links.append(url)
    return links


def _append_param(url: str, key: str, value: str) -> str:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs[key] = [value]
    new_query = urlencode(qs, doseq=True)
    return parsed._replace(query=new_query).geturl()


def _build_video_link(base_link: str, timestamp: Optional[int], index: int) -> str:
    if timestamp is not None:
        return _append_param(base_link, "t", f"{max(0, timestamp)}s")
    return _append_param(base_link, "index", str(index))


def _load_existing_store_links() -> set[str]:
    global _EXISTING_STORE_LINKS
    if _EXISTING_STORE_LINKS is not None:
        return _EXISTING_STORE_LINKS
    links: set[str] = set()
    if DB_PATH.exists():
        try:
            with sqlite3.connect(str(DB_PATH)) as conn:
                rows = conn.execute(
                    "SELECT store_link FROM info WHERE store_link IS NOT NULL"
                ).fetchall()
                for (link,) in rows:
                    if link:
                        links.add(str(link))
        except Exception:
            links = set()
    _EXISTING_STORE_LINKS = links
    return links


def _extract_img_size_from_url(url: str) -> Tuple[int, int]:
    m = re.search(r"/(\d{2,5})x(\d{2,5})", url)
    if m:
        try:
            return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
    return 0, 0


def _shrink_img_url(url: str, max_side: int = IMG_MAX_SIDE) -> str:
    url = _normalize_shot_url(url)
    m = re.search(r"/(\d{2,5})x(\d{2,5})([^/]*)$", url)
    if not m:
        return url
    try:
        w = int(m.group(1))
        h = int(m.group(2))
    except Exception:
        return url
    if max(w, h) <= max_side or w <= 0 or h <= 0:
        return url
    if w >= h:
        new_w = max_side
        new_h = max(1, int(h * new_w / w))
    else:
        new_h = max_side
        new_w = max(1, int(w * new_h / h))
    return url.replace(f"/{w}x{h}", f"/{new_w}x{new_h}")


def _normalize_shot_url(url: str) -> str:
    # Normalize apple placeholder URLs that end with {w}x{h}{c}.{f}
    u = url.strip()
    u = u.split("?")[0].rstrip(");")
    if "{w}" in u and "{h}" in u:
        u = re.sub(r"\{w\}x\{h\}(?:\{c\})?(?:\.\{f\})?", "600x600bb.jpg", u)
    return u


def _pick_html_screenshots(html: str) -> List[str]:
    urls: List[str] = []
    pat = re.compile(r"https://is\d-ssl\.mzstatic\.com/image/[^\s\"']+")
    best: Dict[str, Tuple[int, int, int, str]] = {}
    for m in pat.finditer(html):
        raw_url = m.group(0)
        url = _normalize_shot_url(raw_url)
        lower = url.lower()
        if any(x in lower for x in ["appicon", "placeholder", ".svg", ".ico"]):
            continue
        msize = re.search(r"(.*)/(\d{2,5})x(\d{2,5})", url)
        base = msize.group(1) if msize else url
        w, h = _extract_img_size_from_url(url)
        if max(w, h) < 500:  # ignore tiny thumbs
            continue
        prev = best.get(base)
        area = w * h
        if not prev or area > prev[2]:
            best[base] = (w, h, area, url)
    # Sort by area desc to prioritize real screenshots
    for _, _, _, u in sorted(best.values(), key=lambda x: x[2], reverse=True):
        urls.append(u)
    return urls


def _download_image(url: str) -> Optional[Image.Image]:
    try:
        resp = requests.get(_normalize_shot_url(url), headers={"User-Agent": UA}, timeout=TIMEOUT)
        resp.raise_for_status()
        return Image.open(BytesIO(resp.content))
    except Exception:
        return None


def _concat_vertical(images: Sequence[Image.Image]) -> Optional[Image.Image]:
    if not images:
        return None
    try:
        heights = [im.height for im in images if im]
        if not heights:
            return None
        target_h = min(heights)
        resized: List[Image.Image] = []
        gap = 2  # 2px white gap between images
        new_w = max(0, gap * (len(images) - 1))
        for im in images:
            if not im:
                continue
            if im.height != target_h:
                scale = target_h / im.height
                w = max(1, int(im.width * scale))
                resized.append(im.resize((w, target_h)))
                new_w += w
            else:
                resized.append(im)
                new_w += im.width
        canvas = Image.new("RGB", (new_w, target_h), (255, 255, 255))
        x = 0
        for im in resized:
            canvas.paste(im.convert("RGB"), (x, 0))
            x += im.width
            x += gap
        return canvas
    except Exception:
        return None


@dataclass
class StoreMeta:
    title: str
    description: str
    screenshots: List[str]


def _fetch_store_meta(store_url: str) -> Optional[StoreMeta]:
    if store_url in _STORE_CACHE:
        meta = _STORE_CACHE[store_url]
        return StoreMeta(meta.get("title", ""), meta.get("description", ""), meta.get("screenshots", []))

    app_id = ""
    m = re.search(r"id(\d+)", store_url)
    if m:
        app_id = m.group(1)

    title = ""
    description = ""
    screenshots: List[str] = []

    if app_id:
        try:
            r = requests.get(
                f"https://itunes.apple.com/lookup",
                params={"id": app_id, "country": "us", "entity": "software"},
                timeout=TIMEOUT,
                headers={"User-Agent": UA},
            )
            if r.ok:
                data = r.json()
                if data.get("resultCount", 0) > 0:
                    res = data["results"][0]
                    title = str(res.get("trackName") or title)
                    description = str(res.get("description") or description)
                    screenshots = (
                        list(res.get("screenshotUrls") or [])
                        or list(res.get("ipadScreenshotUrls") or [])
                    )
        except Exception:
            pass

    allow_html = _RUN_START_TS and (time.time() - _RUN_START_TS) < (BUDGET_SEC * 0.9)
    if allow_html and (not screenshots or not title or not description):
        try:
            html = requests.get(store_url, headers={"User-Agent": UA}, timeout=TIMEOUT).text
            if not title:
                mt = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
                if mt:
                    title = re.sub(r"\s+", " ", mt.group(1)).strip()
            if not description:
                md = re.search(r'"description"\s*:\s*"([^"]+)"', html)
                if md:
                    description = md.group(1)
            if not screenshots:
                screenshots = _pick_html_screenshots(html)
        except Exception:
            pass

    title = title.strip()
    description = description.strip()
    _STORE_CACHE[store_url] = {"title": title, "description": description, "screenshots": screenshots}
    if not title:
        return None
    return StoreMeta(title=title, description=description, screenshots=screenshots)


def _save_screenshot(track_id: str, video_id: str, idx: int, shots: List[str]) -> str:
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    images: List[Image.Image] = []
    for url in shots[:3]:
        im = _download_image(_shrink_img_url(url))
        if im:
            images.append(im)
    if not images:
        return ""
    first = images[0]
    base_name = f"{track_id or video_id}-{idx}"
    if first.width >= first.height:
        out_path = TEMP_DIR / f"{base_name}-h.jpg"  # h = horizontal
        first.convert("RGB").save(out_path, format="JPEG", quality=92)
        return str(out_path)
    # vertical: stitch up to 3
    stitched = _concat_vertical(images[:3])
    if stitched:
        out_path = TEMP_DIR / f"{base_name}-v-stitched.jpg"
        stitched.convert("RGB").save(out_path, format="JPEG", quality=92)
        return str(out_path)
    # fallback to first even if vertical
    out_path = TEMP_DIR / f"{base_name}-v.jpg"
    first.convert("RGB").save(out_path, format="JPEG", quality=92)
    return str(out_path)


def fetch_feed():
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    feed = feedparser.parse(url)
    if getattr(feed, "bozo", False):
        try:
            print(f"解析 RSS 时可能有问题: {url} -> {getattr(feed, 'bozo_exception', None)}")
        except Exception:
            pass
    return feed


def collect_latest() -> List[Dict[str, str]]:
    global _RUN_START_TS
    _RUN_START_TS = time.time()
    feed = fetch_feed()
    items: List[Dict[str, str]] = []
    existing_store_links = _load_existing_store_links()
    processed = 0
    for entry in getattr(feed, "entries", []):
        if processed >= MAX_FEED_ENTRIES or (time.time() - _RUN_START_TS) >= BUDGET_SEC:
            break
        url = entry.get("link") or ""
        vid = entry.get("yt_videoid") or _extract_video_id(url)
        if not url and vid:
            url = f"https://www.youtube.com/watch?v={vid}"
        desc = _extract_description(entry)
        if vid and desc:
            _DESC_CACHE[vid] = desc
        itunes_links = _parse_itunes_links(desc)
        if not itunes_links:
            continue
        processed += 1
        chapters = _parse_chapters(desc)
        published = _normalize_datetime(entry)

        for idx, store_url in enumerate(itunes_links[:MAX_LINKS_PER_VIDEO], start=1):
            if (time.time() - _RUN_START_TS) >= BUDGET_SEC:
                break
            if store_url in existing_store_links:
                continue
            meta = _fetch_store_meta(store_url)
            if not meta:
                continue
            timestamp = chapters[idx - 1][0] if idx - 1 < len(chapters) else None
            article_link = _build_video_link(url, timestamp, idx)
            track_id_match = re.search(r"id(\d+)", store_url)
            track_id = track_id_match.group(1) if track_id_match else vid
            img_path = ""
            if meta.screenshots:
                img_path = _save_screenshot(track_id, vid, idx, meta.screenshots)
            detail = meta.description
            _DETAIL_CACHE[article_link] = detail
            existing_store_links.add(store_url)
            items.append(
                {
                    "title": meta.title or entry.get("title", "") or "",
                    "url": article_link,
                    "published": published,
                    "img": img_path,
                    "source": SOURCE,
                    "category": CATEGORY,
                    "store_link": store_url,
                    "detail": detail,
                }
            )
    # newest first
    def sort_key(x):
        try:
            return datetime.fromisoformat(x.get("published", "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    items.sort(key=sort_key, reverse=True)
    return items


def fetch_article_detail(url: str) -> str:
    if url in _DETAIL_CACHE:
        return _DETAIL_CACHE[url]
    # fallback: find store_link via DB
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            row = conn.execute("SELECT store_link FROM info WHERE link=?", (url,)).fetchone()
            if row and row[0]:
                meta = _fetch_store_meta(str(row[0]))
                if meta:
                    _DETAIL_CACHE[url] = meta.description
                    return meta.description
    except Exception:
        pass
    vid = _extract_video_id(url)
    if vid and vid in _DESC_CACHE:
        desc = _DESC_CACHE[vid]
        _DETAIL_CACHE[url] = desc
        return desc
    return ""


if __name__ == "__main__":  # pragma: no cover
    items = collect_latest()[:5]
    for item in items:
        print(json.dumps(item, ensure_ascii=False, indent=2))
    if items:
        print(fetch_article_detail(items[0]["url"])[:400])
