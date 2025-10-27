from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:  # pragma: no cover - allow running as a script
    from .._datetime import normalize_published_datetime
except ImportError:  # pragma: no cover - fallback for direct execution
    import sys
    from pathlib import Path

    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from _datetime import normalize_published_datetime

SOURCE = "deepmind"
CATEGORY = "tech"

BASE_URL = "https://deepmind.google"
LIST_URL = f"{BASE_URL}/discover/blog/"
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
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def _to_iso8601(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return ""
        return normalize_published_datetime(dt, str(value))
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return normalize_published_datetime(dt, dt.isoformat())
    if isinstance(value, str):
        raw = value.strip()
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
    return normalize_published_datetime(None, str(value))


def _iter_dicts(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_dicts(item)


def _parse_json_ld(soup: BeautifulSoup) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for script in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except Exception:
            continue
        for obj in _iter_dicts(data):
            t = obj.get("@type") or obj.get("type")
            if isinstance(t, list):
                t = next((x for x in t if isinstance(x, str)), "")
            if str(t).lower() not in ("blogposting", "newsarticle", "article"):
                continue
            title = obj.get("headline") or obj.get("name") or obj.get("title")
            url = obj.get("url") or obj.get("mainEntityOfPage")
            published = obj.get("datePublished") or obj.get("dateCreated") or obj.get("dateModified")
            if isinstance(url, dict):
                url = url.get("@id") or url.get("url")
            if not isinstance(title, str) or not isinstance(url, str):
                continue
            title = title.strip()
            url = urljoin(BASE_URL, url.strip())
            pub = _to_iso8601(published)
            results.append({
                "title": title,
                "url": url,
                "published": pub,
                "source": SOURCE,
                "category": CATEGORY,
            })
    return results


def _parse_cards(soup: BeautifulSoup) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    candidates = soup.select(
        "article, .card, .post, li, .teaser, .listing-item, .gc-article"
    )
    for node in candidates:
        a = node.find("a", href=True)
        if not a:
            continue
        title = a.get_text(strip=True) or node.get_text(strip=True)
        if not title:
            continue
        href = a["href"].strip()
        # Build absolute URL and filter out non-article links (pagination/home etc.)
        abs_url = href
        if abs_url.startswith("//"):
            abs_url = "https:" + abs_url
        if abs_url.startswith("/"):
            abs_url = urljoin(BASE_URL, abs_url)
        parsed = urlparse(abs_url)
        if not parsed.netloc or parsed.netloc not in urlparse(BASE_URL).netloc:
            continue
        # Skip pagination and listing pages
        if parsed.query and "page=" in parsed.query:
            continue
        if parsed.path in ("/", "/discover/blog", "/discover/blog/"):
            continue
        if not parsed.path.startswith("/discover/blog/"):
            continue
        url = abs_url

        pub = ""
        # time tag
        t = node.find("time")
        if t and (t.get("datetime") or t.get_text(strip=True)):
            pub = _to_iso8601(t.get("datetime") or t.get_text(strip=True))
        if not pub:
            # data-attrs or meta
            meta = node.find("meta", attrs={"itemprop": "datePublished"}) or node.find(
                "meta", attrs={"property": "article:published_time"}
            )
            if meta and meta.get("content"):
                pub = _to_iso8601(meta["content"])  
        if not pub:
            # scan text for YYYY-MM-DD
            m = re.search(r"(\d{4}-\d{2}-\d{2})", node.get_text(" ", strip=True))
            if m:
                pub = _to_iso8601(m.group(1))

        results.append({
            "title": title,
            "url": url,
            "published": pub,
            "source": SOURCE,
            "category": CATEGORY,
        })
    return results


def parse_list(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    # Prefer structured data when available
    items.extend(_parse_json_ld(soup))
    if not items:
        items.extend(_parse_cards(soup))
    # Deduplicate by URL, keep first occurrence
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


# -----------------------
# Article detail fetching
# -----------------------


def _strip_noise(soup: BeautifulSoup) -> None:
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
        "button",
    ]):
        tag.decompose()
    for noisy in soup.select('[aria-label*="share" i], [class*="share" i], [class*="promo" i]'):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors: Iterable[str] = (
        "main article",
        "article [class*='content']",
        "article",
        "main",
        "[data-component='ArticleBody']",
        "#content",
    )
    for sel in selectors:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    _strip_noise(soup)
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


def collect_latest(limit: int = 20) -> List[dict]:
    html = fetch_list_page(LIST_URL)
    items = parse_list(html)
    return items[:limit]


def main() -> None:
    items = collect_latest()
    for it in items[:10]:
        print(it.get("published", ""), "-", it.get("title", ""), "-", it.get("url", ""))


if __name__ == "__main__":
    main()
