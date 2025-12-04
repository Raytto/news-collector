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
from urllib.parse import parse_qs, urlencode, urlparse, unquote

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


CHANNEL_ID = "UCySabcVxWG9I18v0XI61MPg"
SOURCE = "youtube_cabogame"
CATEGORY = "game_yt"
TIMEOUT = 10
MAX_FEED_ENTRIES = 50  # allow more items; bounded by BUDGET_SEC
MAX_LINKS_PER_VIDEO = 8
BUDGET_SEC = 30.0  # keep margin under outer 40s timeout
IMG_MAX_SIDE = 900

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


def _parse_store_links(desc: str) -> List[Tuple[str, str]]:
    links: List[Tuple[str, str]] = []
    pat = re.compile(
        r"https?://(?:apps|itunes)\.apple\.com/[^\s)<>]+"
        r"|https?://play\.google\.com/store/apps/details\?id=[\w\.\-]+"
        r"|https?://www\.youtube\.com/redirect[^\s)<>]+",
        re.IGNORECASE,
    )
    gp_pat = re.compile(r"https?://play\.google\.com/store/apps/details\?id=[\w\.\-]+", re.IGNORECASE)
    for m in pat.finditer(desc):
        raw = m.group(0).strip()
        if raw.lower().startswith("https://www.youtube.com/redirect"):
            qs = parse_qs(urlparse(raw).query)
            target = (qs.get("q") or qs.get("url") or [None])[0]
            if not target:
                continue
            target = unquote(target).strip().rstrip(")")
            if gp_pat.match(target):
                links.append((target, "gp"))
            continue
        if "apple.com" in raw:
            links.append((raw.rstrip(")"), "itunes"))
        elif gp_pat.match(raw):
            links.append((raw.rstrip(")"), "gp"))
    seen = set()
    uniq: List[Tuple[str, str]] = []
    for url, kind in links:
        if url in seen:
            continue
        seen.add(url)
        uniq.append((url, kind))
    return uniq


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
                rows = conn.execute("SELECT store_link FROM info WHERE store_link IS NOT NULL").fetchall()
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
    m = re.search(r"[=/-]w(\d{2,4})-h(\d{2,4})", url)
    if m:
        try:
            return int(m.group(1)), int(m.group(2))
        except Exception:
            pass
    return 0, 0


def _normalize_shot_url(url: str) -> str:
    u = url.strip()
    u = u.split("?")[0].rstrip(");")
    if "{w}" in u and "{h}" in u:
        u = re.sub(r"\{w\}x\{h\}(?:\{c\})?(?:\.\{f\})?", "600x600bb.jpg", u)
    return u


def _shrink_img_url(url: str, max_side: int = IMG_MAX_SIDE) -> str:
    url = _normalize_shot_url(url)
    m_slash = re.search(r"/(\d{2,5})x(\d{2,5})([^/]*)$", url)
    m_gp = re.search(r"=w(\d{2,5})-h(\d{2,5})", url)
    if not m_slash and not m_gp:
        return url
    try:
        if m_slash:
            w = int(m_slash.group(1))
            h = int(m_slash.group(2))
        else:
            w = int(m_gp.group(1)) if m_gp else 0
            h = int(m_gp.group(2)) if m_gp else 0
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
    if m_slash:
        return url.replace(f"/{w}x{h}", f"/{new_w}x{new_h}")
    return re.sub(r"=w\d{2,5}-h\d{2,5}", f"=w{new_w}-h{new_h}", url)


def _pick_html_screenshots(html: str) -> List[str]:
    urls: List[str] = []
    pat = re.compile(
        r"https://is\d-ssl\.mzstatic\.com/image/[^\s\"']+|https://play-lh\.googleusercontent\.com/[^\s\"']+"
    )
    best: Dict[str, Tuple[int, int, int, str]] = {}
    for m in pat.finditer(html):
        raw_url = m.group(0)
        url = _normalize_shot_url(raw_url)
        lower = url.lower()
        if any(x in lower for x in ["appicon", "placeholder", ".svg", ".ico"]):
            continue
        msize = re.search(r"(.*)/(\d{2,5})x(\d{2,5})", url)
        base = msize.group(1) if msize else re.sub(r"=w\d{2,5}-h\d{2,5}.*", "", url)
        w_raw, h_raw = _extract_img_size_from_url(raw_url)
        w, h = _extract_img_size_from_url(url)
        w = w or w_raw
        h = h or h_raw
        if max(w, h) < 500:
            continue
        prev = best.get(base)
        area = w * h
        if not prev or area > prev[2]:
            best[base] = (w, h, area, url)
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
        gap = 2  # 2px white gap between images
        new_w = max(0, gap * (len(images) - 1))
        resized: List[Image.Image] = []
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
    creator: str = ""


def _fetch_itunes_meta(store_url: str) -> Optional[StoreMeta]:
    app_id = ""
    m = re.search(r"id(\d+)", store_url)
    if m:
        app_id = m.group(1)
    title = ""
    description = ""
    screenshots: List[str] = []
    creator = ""
    if app_id:
        try:
            r = requests.get(
                "https://itunes.apple.com/lookup",
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
                    creator = str(res.get("sellerName") or res.get("artistName") or creator)
                    screenshots = (
                        list(res.get("screenshotUrls") or [])
                        or list(res.get("ipadScreenshotUrls") or [])
                    )
        except Exception:
            pass
    if not screenshots or not title or not description:
        try:
            html_txt = requests.get(store_url, headers={"User-Agent": UA}, timeout=TIMEOUT).text
            if not title:
                mt = re.search(r"<title>(.*?)</title>", html_txt, re.IGNORECASE | re.DOTALL)
                if mt:
                    title = re.sub(r"\s+", " ", mt.group(1)).strip()
            if not description:
                md = re.search(r'"description"\s*:\s*"([^"]+)"', html_txt)
                if md:
                    description = md.group(1)
            if not creator:
                mc = re.search(r'"sellerName"\s*:\s*"([^"]+)"', html_txt)
                if mc:
                    creator = mc.group(1)
            if not screenshots:
                screenshots = _pick_html_screenshots(html_txt)
        except Exception:
            pass
    title = title.strip()
    description = description.strip()
    creator = creator.strip()
    if not title:
        return None
    return StoreMeta(title=title, description=description, screenshots=screenshots, creator=creator)


def _fetch_gp_meta(store_url: str) -> Optional[StoreMeta]:
    title = ""
    description = ""
    screenshots: List[str] = []
    creator = ""
    try:
        html_txt = requests.get(store_url, headers={"User-Agent": UA}, timeout=TIMEOUT).text
        m_title = re.search(r'<h1[^>]*><span[^>]*>([^<]+)</span>', html_txt)
        if m_title:
            title = m_title.group(1).strip()
        md = re.search(r'"description":"(.*?)"', html_txt, re.S)
        if md:
            desc_raw = md.group(1).replace("\\n", "\n")
            try:
                description = json.loads(f'"{desc_raw}"')
            except Exception:
                description = desc_raw
        m_creator = re.search(r'"developerName"\s*:\s*"([^"]+)"', html_txt)
        if m_creator:
            creator = m_creator.group(1)
        if not creator:
            alt_creator = re.search(r'"name"\s*:\s*"([^"]+)"\s*,\s*"@type"\s*:\s*"Organization"', html_txt)
            if alt_creator:
                creator = alt_creator.group(1)
        if not creator:
            link_creator = re.search(r'developer[^>]*>\s*<span>([^<]+)</span>', html_txt, re.IGNORECASE)
            if link_creator:
                creator = link_creator.group(1)
        if not description:
            m_alt = re.search(r'itemprop="description"[^>]*>(.*?)</div>', html_txt, re.S)
            if m_alt:
                description = re.sub("<[^>]+>", "", m_alt.group(1))
        screenshots = _pick_html_screenshots(html_txt)
    except Exception:
        return None
    title = title.strip()
    description = _clean_description(description)
    creator = creator.strip()
    if not title:
        return None
    return StoreMeta(title=title, description=description, screenshots=screenshots, creator=creator)


def _fetch_store_meta(store_url: str, platform: str) -> Optional[StoreMeta]:
    key = f"{platform}:{store_url}"
    if key in _STORE_CACHE:
        meta = _STORE_CACHE[key]
        return StoreMeta(
            meta.get("title", ""),
            meta.get("description", ""),
            meta.get("screenshots", []),
            meta.get("creator", ""),
        )
    meta: Optional[StoreMeta] = None
    if platform == "gp":
        meta = _fetch_gp_meta(store_url)
    else:
        meta = _fetch_itunes_meta(store_url)
    if meta:
        _STORE_CACHE[key] = {
            "title": meta.title,
            "description": meta.description,
            "screenshots": meta.screenshots,
            "creator": meta.creator,
        }
    return meta


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
        out_path = TEMP_DIR / f"{base_name}-h.jpg"
        first.convert("RGB").save(out_path, format="JPEG", quality=92)
        return str(out_path)
    stitched = _concat_vertical(images[:3])
    if stitched:
        out_path = TEMP_DIR / f"{base_name}-v-stitched.jpg"
        stitched.convert("RGB").save(out_path, format="JPEG", quality=92)
        return str(out_path)
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
        store_links = _parse_store_links(desc)
        chapters = _parse_chapters(desc)
        published = _normalize_datetime(entry)

        if not store_links:
            thumb = _extract_thumbnail(entry, vid)
            items.append(
                {
                    "title": entry.get("title", "") or "",
                    "url": url,
                    "published": published,
                    "img": thumb,
                    "source": SOURCE,
                    "category": CATEGORY,
                    "store_link": "",
                    "detail": desc,
                }
            )
            if url and desc:
                _DETAIL_CACHE[url] = desc
            processed += 1
            continue

        for idx, (store_url, platform) in enumerate(store_links[:MAX_LINKS_PER_VIDEO], start=1):
            if (time.time() - _RUN_START_TS) >= BUDGET_SEC:
                break
            if store_url in existing_store_links:
                continue
            meta = _fetch_store_meta(store_url, platform)
            if not meta:
                continue
            timestamp = chapters[idx - 1][0] if idx - 1 < len(chapters) else None
            article_link = _build_video_link(url, timestamp, idx)
            track_id_match = re.search(r"id=([\w\.]+)", store_url) if platform == "gp" else re.search(r"id(\d+)", store_url)
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
                    "creator": meta.creator or "",
                    "url": article_link,
                    "published": published,
                    "img": img_path or _extract_thumbnail(entry, vid),
                    "source": SOURCE,
                    "category": CATEGORY,
                    "store_link": store_url,
                    "detail": detail,
                }
            )
        processed += 1

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
    try:
        with sqlite3.connect(str(DB_PATH)) as conn:
            row = conn.execute("SELECT store_link FROM info WHERE link=?", (url,)).fetchone()
            if row and row[0]:
                store = str(row[0])
                platform = "gp" if "play.google.com" in store else "itunes"
                meta = _fetch_store_meta(store, platform)
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
