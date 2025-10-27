from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, List, Dict, Iterable

import requests
from bs4 import BeautifulSoup

SOURCE = "jiqizhixin"
CATEGORY = "tech"

BASE_URL = "https://www.jiqizhixin.com"
LIST_URL = f"{BASE_URL}/"
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


def fetch_list_page(url: str = LIST_URL) -> str:
    """Fetch homepage HTML; fallback to readability proxy on failure.

    Some regions or anti-bot mechanisms may serve minimal/blocked pages. As a
    resilience fallback, we try r.jina.ai which returns Markdown content.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code < 400 and len(resp.text or "") > 1024:
            return resp.text
    except Exception:
        pass
    # Fallback to readability proxy (Markdown)
    try:
        r = requests.get(f"https://r.jina.ai/{url}", headers={"User-Agent": UA}, timeout=TIMEOUT)
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
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                dt = datetime.strptime(raw[:10], fmt).replace(tzinfo=timezone.utc)
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


def parse_list(html: str) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []

    # If the content looks like Markdown from r.jina.ai, extract links first
    if "\n# " in html or "[" in html and "](http" in html:
        try:
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
        except Exception:
            pass

    # HTML parsing path
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        soup = None

    if soup is not None:
        # Prefer right-side daily/news area when available
        candidates: Iterable[Any] = (
            soup.select(".home__right .post__item, .home__right a[href], .home__right li a[href]")
            or soup.select("article, .article, .post, li, .news-item, .list-item, .card")
            or soup.find_all("a", href=True)
        )
        for node in candidates:
            a = node.find("a", href=True) if hasattr(node, "find") else node
            if not a:
                continue
            href = a.get("href") or ""
            if not href:
                continue
            url = _normalize_url(href)
            if BASE_URL not in url:
                continue
            title = a.get_text(strip=True) or (node.get_text(strip=True) if hasattr(node, "get_text") else "")
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
            if not pub and hasattr(node, "get_text"):
                txt = node.get_text(" ", strip=True)
                m = re.search(r"(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})", txt)
                if m:
                    pub = _to_iso8601(m.group(1))
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

    def sort_key(it: Dict[str, str]):
        try:
            return datetime.fromisoformat((it.get("published") or "").replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    unique.sort(key=sort_key, reverse=True)
    return unique


def collect_latest(limit: int = 20) -> List[dict]:
    """Collect latest posts with an additional readability fallback when HTML yields none."""
    html = fetch_list_page(LIST_URL)
    items = parse_list(html)
    if not items:
        # Force readability fallback and parse Markdown
        try:
            r = requests.get(f"https://r.jina.ai/{LIST_URL}", headers={"User-Agent": UA}, timeout=TIMEOUT)
            if r.status_code < 400 and r.text:
                items = parse_list(r.text)
        except Exception:
            pass
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
