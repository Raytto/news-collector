from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

LIST_URL = "https://openai.com/zh-Hans-CN/research/index/"
ARTICLE_BASE_URL = "https://openai.com/zh-Hans-CN/research/"
SOURCE = "openai.research"
CATEGORY = "tech"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
    "Referer": "https://openai.com/zh-Hans-CN/research/",
}
REQUEST_TIMEOUT = 30


def fetch_list_page(url: str = LIST_URL) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.text


def _load_next_data(html: str) -> Any:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if not script or not script.string:
        raise ValueError("未找到 __NEXT_DATA__ 脚本，页面结构可能已变化")
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
    data = _load_next_data(html)
    articles: List[Dict[str, str]] = []
    seen_urls: set[str] = set()
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
    def sort_key(item: Dict[str, str]) -> datetime:
        try:
            return datetime.fromisoformat(item["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
    articles.sort(key=sort_key, reverse=True)
    return articles


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
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
