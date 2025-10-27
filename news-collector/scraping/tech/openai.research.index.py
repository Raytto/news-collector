from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ARTICLE_BASE_URL = "https://openai.com/research/"
PRIMARY_LIST_URL = "https://openai.com/zh-Hans-CN/research/index/"
# Fallbacks in case localized path is blocked or removed
FALLBACK_LIST_URLS = (
    "https://openai.com/zh-Hans-CN/research/index/",
    "https://openai.com/zh-cn/research/",
    "https://openai.com/research/",
    "https://openai.com/research/index/",
)
SOURCE = "openai.research"
CATEGORY = "tech"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 30

DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
    "Referer": "https://openai.com/research/",
    "Cache-Control": "no-cache",
}

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update(DEFAULT_HEADERS)
    return s


def fetch_list_page(url: str | None = None) -> str:
    session = _build_session()
    candidates = [url or PRIMARY_LIST_URL, *FALLBACK_LIST_URLS]
    last_exc: Exception | None = None
    for u in candidates:
        try:
            resp = session.get(u, timeout=REQUEST_TIMEOUT)
            # Some geo/locale variants return 403; try next on 4xx except 404 redirects
            if resp.status_code >= 400:
                last_exc = Exception(f"HTTP {resp.status_code}")
                continue
            text = resp.text
            if text and "__NEXT_DATA__" in text:
                return text
            # Accept non-Next pages too; we'll fallback parse
            if text and len(text) > 2000:
                return text
        except Exception as exc:
            last_exc = exc
            continue
    # Final fallbacks: readability proxy (Markdown) for localized + en paths
    for j in (
        "https://r.jina.ai/https://openai.com/zh-Hans-CN/research/index/",
        "https://r.jina.ai/https://openai.com/zh-cn/research/",
        "https://r.jina.ai/https://openai.com/research/",
    ):
        try:
            resp = session.get(j, timeout=REQUEST_TIMEOUT)
            if resp.status_code < 400 and resp.text:
                return resp.text
        except Exception:
            continue
    if last_exc:
        raise last_exc
    raise RuntimeError("无法获取 OpenAI Research 列表页")


def _load_next_data(html: str) -> Any:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if not script or not script.string:
        # Allow caller to fallback to HTML parsing
        return None
    return json.loads(script.string)


def _iter_dicts(data: Any) -> Iterable[Dict[str, Any]]:
    stack: List[Any] = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            yield node
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("text", "value", "title", "plainText"):
            if key in value:
                text = _extract_text(value[key])
                if text:
                    return text
        return ""
    if isinstance(value, list):
        parts = [_extract_text(v) for v in value]
        return " ".join(part for part in parts if part).strip()
    return ""


def _extract_slug(node: Dict[str, Any]) -> str:
    slug = node.get("slug") or node.get("permalink") or node.get("href")
    if isinstance(slug, dict):
        slug = slug.get("value") or slug.get("text")
    if isinstance(slug, list) and slug:
        slug = slug[0]
    if isinstance(slug, str):
        slug = slug.strip()
    if slug and slug.startswith("/"):
        slug = slug.lstrip("/")
    return slug or ""


def _normalize_datetime(value: Any) -> str:
    if isinstance(value, (int, float)):
        dt = datetime.fromtimestamp(value, tz=timezone.utc)
        return dt.isoformat()
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    text = text.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(text[:10], fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        dt = datetime.strptime(match.group(0), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return ""


def _extract_published(node: Dict[str, Any]) -> str:
    for key in ("publishedAt", "publishDate", "publishedDate", "date"):
        if key not in node:
            continue
        value = node[key]
        if isinstance(value, dict):
            for sub_key in ("value", "text", "iso", "date"):
                if sub_key in value:
                    result = _normalize_datetime(value[sub_key])
                    if result:
                        return result
        elif isinstance(value, list):
            for item in value:
                result = _normalize_datetime(item)
                if result:
                    return result
        else:
            result = _normalize_datetime(value)
            if result:
                return result
    return ""


def parse_list(html: str) -> List[Dict[str, str]]:
    articles: List[Dict[str, str]] = []
    seen_urls: set[str] = set()

    data = _load_next_data(html)
    if data is not None:
        for node in _iter_dicts(data):
            if not {"slug", "title"}.intersection(node.keys()):
                continue
            title = _extract_text(node.get("title")) or _extract_text(node.get("headline"))
            if not title:
                continue
            slug = _extract_slug(node)
            if not slug:
                continue
            url = urljoin(ARTICLE_BASE_URL, slug)
            if url in seen_urls:
                continue
            published = _extract_published(node)
            articles.append({
                "title": title,
                "url": url,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            })
            seen_urls.add(url)

    # Fallback: parse from visible anchors if Next data missing
    if not articles:
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href^='/research/'], a[href*='/research/']"):
            href = a.get("href") or ""
            title = a.get_text(strip=True)
            if not href or not title:
                continue
            # Normalize localized paths as well
            url = urljoin("https://openai.com", href)
            if url in seen_urls:
                continue
            # Try to find a nearby time element
            published = ""
            time_tag = a.find_next("time")
            if time_tag:
                published = _extract_text(time_tag.get("datetime") or time_tag.get_text())
            articles.append({
                "title": title,
                "url": url,
                "published": _extract_published({"date": published}) if published else "",
                "source": SOURCE,
                "category": CATEGORY,
            })
            seen_urls.add(url)

    # Final fallback: try to parse Markdown links (first on provided html; if none, fetch from r.jina.ai)
    if not articles:
        found = False
        for m in re.finditer(r"\[([^\]]+)\]\((https?://openai\.com/(?:zh-[^/]+/)?research/[^\s)]+)\)", html):
            title = m.group(1).strip()
            url = m.group(2).strip()
            if url in seen_urls or not title:
                continue
            articles.append({
                "title": title,
                "url": url,
                "published": "",
                "source": SOURCE,
                "category": CATEGORY,
            })
            seen_urls.add(url)
            found = True
        if not found:
            for j in (
                "https://r.jina.ai/https://openai.com/zh-Hans-CN/research/index/",
                "https://r.jina.ai/https://openai.com/zh-cn/research/",
                "https://r.jina.ai/https://openai.com/research/",
            ):
                try:
                    r = requests.get(j, headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
                    if r.status_code >= 400 or not r.text:
                        continue
                    md = r.text
                    for m in re.finditer(r"\[([^\]]+)\]\((https?://openai\.com/(?:zh-[^/]+/)?research/[^\s)]+)\)", md):
                        title = m.group(1).strip()
                        url = m.group(2).strip()
                        if url in seen_urls or not title:
                            continue
                        articles.append({
                            "title": title,
                            "url": url,
                            "published": "",
                            "source": SOURCE,
                            "category": CATEGORY,
                        })
                        seen_urls.add(url)
                    if articles:
                        break
                except Exception:
                    continue

    def sort_key(item: Dict[str, str]) -> datetime:
        try:
            return datetime.fromisoformat(item["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    articles.sort(key=sort_key, reverse=True)
    return articles


def fetch_article_detail(url: str) -> str:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        # Fallback to readability proxy
        r = requests.get(f"https://r.jina.ai/{url}", headers={"User-Agent": UA}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        # r.jina.ai returns Markdown; return as-is after light cleanup
        text = r.text
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    soup = BeautifulSoup(html, "html.parser")
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
    ]):
        tag.decompose()
    candidates = [
        "main article",
        "article",
        "main",
        "[data-testid='article-content']",
        "div[data-component='ArticleBody']",
        "div[data-testid='markdown']",
    ]
    main = None
    for selector in candidates:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            main = node
            break
    if main is None:
        main = soup.body or soup
    text = main.get_text("\n", strip=True)
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\u00a0", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.rstrip() for line in text.splitlines())
    return text.strip()


if __name__ == "__main__":
    html = fetch_list_page()
    items = parse_list(html)
    for item in items[:10]:
        print(item["published"], "-", item["title"], "-", item["url"])
