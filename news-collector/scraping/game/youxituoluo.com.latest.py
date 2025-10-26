import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

BASE_URL = "https://www.youxituoluo.com"
HOMEPAGE_URL = f"{BASE_URL}/"
MAX_ITEMS = 10
SOURCE = "youxituoluo"
CATEGORY = "game"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def fetch_homepage() -> str:
    """Fetch the homepage HTML of 游戏陀螺."""

    headers = {"User-Agent": USER_AGENT, "Referer": BASE_URL}
    response = requests.get(HOMEPAGE_URL, headers=headers, timeout=20)
    response.raise_for_status()
    return response.text


def parse_timestamp(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        timestamp = float(value)
        # Guess millisecond timestamps.
        if timestamp > 1_000_000_000_000:
            timestamp /= 1000.0
        try:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OSError, OverflowError):
            return ""
        return dt.isoformat()

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""

        if raw.isdigit():
            return parse_timestamp(float(raw))

        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y/%m/%d %H:%M",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d",
        ):
            try:
                dt = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            except ValueError:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return dt.isoformat()

        return raw

    return ""


def clean_text(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    return unescape(soup.get_text()).strip()


TITLE_KEYS = ("title", "name")
URL_KEYS = ("url", "link", "shareUrl", "jumpUrl", "targetUrl", "articleUrl")
PUBLISHED_KEYS = (
    "publishTime",
    "publish_time",
    "publishAt",
    "publish_at",
    "publishDate",
    "publish_date",
    "pubDate",
    "releaseTime",
    "release_time",
    "createTime",
    "create_time",
    "created_at",
    "ctime",
    "time",
    "date",
)


def iter_dicts(node: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_dicts(value)
    elif isinstance(node, list):
        for item in node:
            yield from iter_dicts(item)


def normalize_article(data: Dict[str, Any]) -> Optional[Dict[str, str]]:
    title: Optional[str] = None
    for key in TITLE_KEYS:
        raw = data.get(key)
        if isinstance(raw, str) and raw.strip():
            title = clean_text(raw)
            break
    if not title:
        return None

    url: Optional[str] = None
    for key in URL_KEYS:
        raw_url = data.get(key)
        if isinstance(raw_url, str) and raw_url.strip():
            candidate = raw_url.strip()
            if candidate.startswith("/"):
                candidate = urljoin(BASE_URL, candidate)
            elif candidate.startswith("//"):
                candidate = "https:" + candidate
            url = candidate
            break
    if not url:
        return None

    published: str = ""
    for key in PUBLISHED_KEYS:
        if key not in data:
            continue
        published = parse_timestamp(data.get(key))
        if published:
            break

    return {"title": title, "url": url, "published": published, "source": SOURCE, "category": CATEGORY}


def extract_from_nuxt_payload(payload: str) -> List[Dict[str, str]]:
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        print("无法解析页面中的 JSON 数据")
        return []

    articles: List[Dict[str, str]] = []
    for item in iter_dicts(data):
        normalized = normalize_article(item)
        if not normalized:
            continue
        articles.append(normalized)
    return articles


def extract_articles_from_json(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    # Next.js style payload.
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        return extract_from_nuxt_payload(next_data.string)

    # Nuxt style payload.
    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string.strip()
        if text.startswith("window.__NUXT__"):
            prefix = "window.__NUXT__="
            payload = text[len(prefix) :]
            if payload.endswith(";"):
                payload = payload[:-1]
            return extract_from_nuxt_payload(payload)
    return []


def extract_articles_from_html(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    articles: List[Dict[str, str]] = []

    def collect_from_elements(elements: Iterable[Tag]):
        for element in elements:
            link = element.find("a", href=True)
            if not link:
                continue
            title = clean_text(link.get_text())
            if not title:
                continue
            url = link["href"]
            if url.startswith("/"):
                url = urljoin(BASE_URL, url)
            elif url.startswith("//"):
                url = "https:" + url

            time_text = ""
            time_tag = element.find("time")
            if time_tag and time_tag.get("datetime"):
                time_text = time_tag["datetime"].strip()
            elif time_tag:
                time_text = clean_text(time_tag.get_text())
            if not time_text:
                # Many cards render an <i class="icon-time"> inside a parent span
                # like: <span><i class="iconfont icon-time"></i> 2025-10-24 14:27</span>
                candidate = element.find(class_=lambda x: x and ("time" in x or "date" in x))
                if candidate:
                    # Prefer the parent's text when the candidate is an icon element
                    parent = candidate.parent if hasattr(candidate, "parent") else None
                    text_src = parent.get_text() if parent is not None else candidate.get_text()
                    time_text = clean_text(text_src)
            if not time_text:
                # Fallback: search for a YYYY-MM-DD[ HH:MM[:SS]] pattern within the element
                full_text = clean_text(element.get_text())
                m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?)", full_text)
                if m:
                    time_text = m.group(1)

            articles.append(
                {
                    "title": title,
                    "url": url,
                    "published": parse_timestamp(time_text) if time_text else "",
                    "source": SOURCE,
                    "category": CATEGORY,
                }
            )

    # 1) Semantic article tags
    collect_from_elements(soup.find_all("article"))
    if articles:
        return articles

    # 2) Common list blocks on the site (e.g. the homepage uses ul.article_list > li)
    candidates = soup.select("ul.article_list > li, .article_list li, div.item, li.item")
    if not candidates:
        candidates = soup.select("div[class*='article'], li[class*='article']")
    if not candidates:
        candidates = soup.select("div[class*='news'], li[class*='news']")
    collect_from_elements(candidates)
    return articles


def deduplicate(articles: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    unique: List[Dict[str, str]] = []
    for article in articles:
        url = article.get("url")
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(article)
    return unique


def sort_articles(articles: Iterable[Dict[str, str]]) -> List[Dict[str, str]]:
    def sort_key(item: Dict[str, str]):
        published = item.get("published") or ""
        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt

    return sorted(articles, key=sort_key, reverse=True)


def collect_articles(html: str) -> List[Dict[str, str]]:
    articles = extract_articles_from_json(html)
    if not articles:
        articles = extract_articles_from_html(html)
    return deduplicate(articles)


if __name__ == "__main__":
    html = fetch_homepage()
    articles = collect_articles(html)
    if not articles:
        print("没有找到任何文章，请检查页面结构是否发生变化")
    else:
        for article in sort_articles(articles)[:MAX_ITEMS]:
            print(article.get("source", ""), "-",article.get("published", ""), "-", article.get("title", ""), "-", article.get("url", ""))
