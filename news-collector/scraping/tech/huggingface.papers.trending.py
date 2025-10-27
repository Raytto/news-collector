from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, List, Dict

import requests
from bs4 import BeautifulSoup

try:  # pragma: no cover - allow running as a script
    from .._datetime import normalize_published_datetime
except ImportError:  # pragma: no cover - fallback for direct execution
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from _datetime import normalize_published_datetime

SOURCE = "huggingface-papers"
CATEGORY = "tech"

BASE_URL = "https://huggingface.co"
LIST_URL = f"{BASE_URL}/blog"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.7,zh-CN;q=0.6",
    "Referer": BASE_URL,
}


def fetch_list_page(url: str = LIST_URL) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _clean_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    while "\n\n\n" in normalized:
        normalized = normalized.replace("\n\n\n", "\n\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized.strip()


def _to_iso8601(text: str) -> str:
    if not text:
        return ""
    raw = text.strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        dt = None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt:
        normalized = normalize_published_datetime(dt, raw)
        if normalized:
            return normalized
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
        try:
            dt = datetime.strptime(raw[:10], fmt).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        normalized = normalize_published_datetime(dt, raw)
        if normalized:
            return normalized
    return normalize_published_datetime(None, raw)


def parse_list(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, str]] = []

    # 1) Try structured data
    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        if not script.string:
            continue
        try:
            data = script.string
            # Some pages embed multiple JSON objects in one array
            import json as _json
            payload = _json.loads(data)
        except Exception:
            continue
        def iter_dicts(node: Any):
            if isinstance(node, dict):
                yield node
                for v in node.values():
                    yield from iter_dicts(v)
            elif isinstance(node, list):
                for it in node:
                    yield from iter_dicts(it)
        for obj in iter_dicts(payload):
            t = obj.get("@type") or obj.get("type")
            if isinstance(t, list):
                t = next((x for x in t if isinstance(x, str)), "")
            if str(t).lower() not in ("blogposting", "newsarticle", "article"):
                continue
            title = obj.get("headline") or obj.get("name") or obj.get("title")
            url = obj.get("url") or obj.get("mainEntityOfPage")
            if isinstance(url, dict):
                url = url.get("@id") or url.get("url")
            pub = obj.get("datePublished") or obj.get("dateCreated") or obj.get("dateModified")
            if isinstance(title, str) and isinstance(url, str):
                items.append({
                    "title": title.strip(),
                    "url": BASE_URL + url if url.startswith("/") else url,
                    "published": _to_iso8601(pub if isinstance(pub, str) else str(pub or "")),
                    "source": SOURCE,
                    "category": CATEGORY,
                })

    # 2) Fallback: parse visible cards
    if not items:
        cards = soup.select("article, .card, .post, li, .blog-post, .prose a[href*='/blog/']")
        for node in cards:
            a = node.find("a", href=True)
            if not a:
                continue
            href = a["href"].strip()
            if not href:
                continue
            title = a.get_text(strip=True) or node.get_text(strip=True)
            if not title:
                continue
            url = BASE_URL + href if href.startswith("/") else href
            pub = ""
            t = node.find("time")
            if t and (t.get("datetime") or t.get_text(strip=True)):
                pub = _to_iso8601(t.get("datetime") or t.get_text(strip=True))
            if not pub:
                meta = node.find("meta", attrs={"property": "article:published_time"})
                if meta and meta.get("content"):
                    pub = _to_iso8601(meta["content"])  
            items.append({
                "title": title,
                "url": url,
                "published": pub,
                "source": SOURCE,
                "category": CATEGORY,
            })

    # Deduplicate and sort
    seen = set()
    unique: List[Dict[str, str]] = []
    for it in items:
        u = it.get("url")
        if u and u not in seen:
            seen.add(u)
            unique.append(it)

    def sort_key(x: Dict[str, str]):
        try:
            return datetime.fromisoformat((x.get("published") or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    unique.sort(key=sort_key, reverse=True)
    return unique


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "article",
        "main article",
        "[data-testid='article-content']",
        "div[data-testid='markdown']",
        ".post-content",
        ".prose",
        "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup.find_all([
        "script",
        "style",
        "noscript",
        "svg",
        "img",
        "video",
        "figure",
        "iframe",
        "button",
        "form",
        "nav",
        "aside",
        "footer",
        "header",
    ]):
        tag.decompose()
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    cleaned = _clean_text(text)
    if cleaned:
        return cleaned
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        return og_desc["content"].strip()
    return ""


def collect_latest(limit: int = 20) -> List[Dict[str, str]]:
    html = fetch_list_page(LIST_URL)
    items = parse_list(html)
    return items[:limit]


if __name__ == "__main__":
    try:
        items = collect_latest()
    except Exception as exc:  # pragma: no cover - manual debug helper
        print(f"Failed to fetch HF blog: {exc}")
    else:
        for entry in items[:10]:
            print(entry.get("published", ""), "-", entry.get("title", ""), "-", entry.get("url", ""))
