from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, List, Dict, Iterable

import requests
from bs4 import BeautifulSoup, Tag

SOURCE = "jiqizhixin"
CATEGORY = "tech"

BASE_URL = "https://www.jiqizhixin.com"
LIST_URL = f"{BASE_URL}/"
API_URL = f"{BASE_URL}/api/article_library/articles.json"
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Referer": BASE_URL,
}


def fetch_list_page(page: int | str = 1, per: int = 30, url: str | None = None):
    """Fetch the latest article list via public JSON API, fallback to HTML.

    Compatibility notes:
    - Older callers may pass the list URL as the first positional argument.
    - `per` controls page size for the JSON API (defaults to 30).
    """

    # Backwards compatibility: allow first positional argument to be a URL string.
    if isinstance(page, str) and not page.isdigit():
        url = page
        page = 1

    if url:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code < 400 and resp.text:
                return resp.text
        except Exception:
            pass
    else:
        params = {"sort": "time", "page": int(page), "per": int(per)}
        try:
            resp = requests.get(
                API_URL,
                params=params,
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Referer": LIST_URL,
                },
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and data.get("articles"):
                return data
        except Exception:
            pass

    # JSON API unavailable, fall back to homepage HTML or readability proxy
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code < 400 and resp.text:
            return resp.text
    except Exception:
        pass
    try:
        r = requests.get(f"https://r.jina.ai/{LIST_URL}", headers={"User-Agent": UA}, timeout=TIMEOUT)
        if r.status_code < 400 and r.text:
            return r.text
    except Exception:
        pass
    raise RuntimeError("无法获取 机器之心 列表页")


def _to_iso8601(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except Exception:
            return ""
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y.%m.%d %H:%M", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                dt = dt.replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                continue
        m = re.search(r"(\d{4}-\d{1,2}-\d{1,2})", raw)
        if m:
            try:
                dt = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except Exception:
                pass
    return ""


def _normalize_url(href: str) -> str:
    if not href:
        return ""
    text = href.strip()
    if not text:
        return ""
    if text.startswith("//"):
        return f"https:{text}"
    if text.startswith("http://") or text.startswith("https://"):
        return text
    if not text.startswith("/"):
        text = "/" + text
    return f"{BASE_URL}{text}"


def _parse_json_entries(data: Dict[str, Any]) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for entry in data.get("articles") or []:
        title = str(entry.get("title") or "").strip()
        slug = str(entry.get("slug") or "").strip()
        if not (title and slug):
            continue
        published_raw = str(entry.get("publishedAt") or "").strip()
        published = _to_iso8601(published_raw) if published_raw else ""
        url = f"{BASE_URL}/articles/{slug}"
        results.append({
            "title": title,
            "url": url,
            "published": published,
            "source": SOURCE,
            "category": CATEGORY,
        })
    return results


def _parse_html_entries(html: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []

    if "\n# " in html or ("[" in html and "](http" in html):
        import re as _re
        for m in _re.finditer(r"\[([^\]]+)\]\((https?://www\.jiqizhixin\.com/[^)]+)\)", html):
            title = m.group(1).strip()
            url = m.group(2).strip()
            if title and url:
                items.append({
                    "title": title,
                    "url": url,
                    "published": "",
                    "source": SOURCE,
                    "category": CATEGORY,
                })

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        soup = None

    if soup is not None:
        candidates: Iterable[Tag] = (
            soup.select("a[href*='/articles/']")
            or soup.select("article, .article, .post, li, .news-item, .list-item, .card")
            or soup.find_all("a", href=True)
        )
        for node in candidates:
            href = None
            title = ""
            if isinstance(node, Tag) and node.name == "a" and node.get("href"):
                href = node.get("href")
                title = node.get_text(strip=True)
            elif isinstance(node, Tag):
                a = node.find("a", href=True)
                if a:
                    href = a.get("href")
                    title = a.get_text(strip=True)
            if not href:
                continue
            url = _normalize_url(href)
            if BASE_URL not in url:
                continue
            if not title and hasattr(node, "get_text"):
                title = node.get_text(strip=True)
            if not title:
                continue
            pub = ""
            t = node.find("time") if hasattr(node, "find") else None
            if t and (t.get("datetime") or t.get_text(strip=True)):
                pub = _to_iso8601(t.get("datetime") or t.get_text(strip=True))
            if not pub and hasattr(node, "find"):
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
    return items


def parse_list(data) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    if isinstance(data, dict):
        items.extend(_parse_json_entries(data))
        if items:
            return items
        data = data.get("html") if isinstance(data, dict) else data

    if isinstance(data, str):
        items.extend(_parse_html_entries(data))

    seen = set()
    unique: List[Dict[str, str]] = []
    for it in items:
        u = it.get("url")
        if u and u not in seen:
            seen.add(u)
            unique.append(it)

    def sort_key(it: Dict[str, str]):
        try:
            return datetime.fromisoformat((it.get("published") or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    unique.sort(key=sort_key, reverse=True)
    return unique


def collect_latest(limit: int = 20) -> List[dict]:
    data = fetch_list_page(per=max(limit * 2, 40))
    items = parse_list(data)
    if not items:
        # One more HTML attempt if JSON path failed completely
        html = fetch_list_page()
        if isinstance(html, str):
            items = parse_list(html)
    return items[:limit]


def collect_latest(limit: int = 20) -> List[dict]:
    html = fetch_list_page(LIST_URL)
    entries = parse_list(html)
    return entries[:limit]


# -----------------------
# Article detail fetching
# -----------------------


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\t", " ", s)
    s = re.sub(r" {2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    lines = [line.rstrip() for line in s.splitlines()]
    return "\n".join(lines).strip()


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
            "button",
        ]
    ):
        tag.decompose()

    for noisy in soup.select(
        "[aria-label*='share' i], [class*='share' i], [class*='social' i], [class*='related' i]"
    ):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "article .article-content",
        "article .post-content",
        "article .entry-content",
        "article .content",
        "div.article-content",
        "div.post-content",
        "div.entry-content",
        "article",
        "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    if soup.body and soup.body.get_text(strip=True):
        return soup.body
    return soup


def fetch_article_detail(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    _strip_noise(soup)
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


def main() -> None:
    items = collect_latest()
    for item in items[:10]:
        print(item["published"], "-", item["title"], "-", item["url"])


if __name__ == "__main__":
    main()
