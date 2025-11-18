from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

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
        helper = sys.modules[module_name] = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(helper)  # type: ignore[attr-defined]
    normalize_published_datetime = helper.normalize_published_datetime  # type: ignore[attr-defined]


BASE_URL = "https://www.guancha.cn"
MAINNEWS_URL = f"{BASE_URL}/mainnews"
SOURCE = "guancha.cn"
CATEGORY = "general"
MAX_ITEMS = 20
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 20

ARTICLE_PATH_RE = re.compile(r"/\d{4}_\d{1,2}_\d{1,2}_\d+\.shtml")


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Referer": BASE_URL,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }
    )
    return session


def fetch_list_page(url: str = MAINNEWS_URL) -> str:
    """抓取观察者网要闻列表页面 HTML。"""

    session = _build_session()
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    try:
        if resp.apparent_encoding:
            resp.encoding = resp.apparent_encoding
    except Exception:
        pass
    return resp.text


def _extract_published_from_url(url: str) -> str:
    """从形如 /section/YYYY_MM_DD_ID.shtml 的路径中提取日期。"""

    m = ARTICLE_PATH_RE.search(url)
    if not m:
        return ""
    year, month, day, *_ = re.findall(r"\d+", m.group(0))
    try:
        y = int(year)
        mth = int(month)
        d = int(day)
    except ValueError:
        return ""
    raw = f"{y:04d}-{mth:02d}-{d:02d}"
    return normalize_published_datetime(None, raw)


def _clean_text(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def collect_articles(html: str, limit: int = MAX_ITEMS) -> List[Dict[str, str]]:
    """从要闻页面 HTML 中提取文章条目。"""

    soup = BeautifulSoup(html, "html.parser")
    articles: List[Dict[str, str]] = []
    seen: set[str] = set()

    def add_from_links(links) -> None:
        nonlocal articles
        for a in links:
            if len(articles) >= limit:
                break
            href = (a.get("href") or "").strip()
            title = (a.get_text() or "").strip()
            if not href or not title:
                continue
            full_url = urljoin(MAINNEWS_URL, href)
            if not full_url.startswith(BASE_URL):
                continue
            if not ARTICLE_PATH_RE.search(full_url):
                continue
            if full_url in seen:
                continue
            seen.add(full_url)
            published = _extract_published_from_url(full_url)
            articles.append(
                {
                    "title": title,
                    "url": full_url,
                    "published": published,
                    "source": SOURCE,
                    "category": CATEGORY,
                }
            )

    # 头条
    add_from_links(soup.select("div.content-headline h3 a[href]"))
    # 列表模块
    add_from_links(soup.select("h4.module-title a[href]"))

    return articles


def sort_articles(articles: List[Dict[str, str]]) -> List[Dict[str, str]]:
    def sort_key(item: Dict[str, str]) -> datetime:
        published = (item.get("published") or "").replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(published)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt

    return sorted(articles, key=sort_key, reverse=True)


def collect_latest(limit: int = MAX_ITEMS) -> List[Dict[str, str]]:
    """采集观察者网要闻的最新若干条。"""

    html = fetch_list_page()
    items = collect_articles(html, limit=limit * 2)
    return sort_articles(items)[:limit]


def fetch_article_detail(url: str) -> str:
    """抓取单篇文章正文（纯文本）。"""

    session = _build_session()
    resp = session.get(url, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    try:
        if resp.apparent_encoding:
            resp.encoding = resp.apparent_encoding
    except Exception:
        pass
    soup = BeautifulSoup(resp.text, "html.parser")

    for tag in soup.find_all(
        [
            "script",
            "style",
            "noscript",
            "svg",
            "img",
            "video",
            "iframe",
            "figure",
            "header",
            "footer",
            "nav",
            "form",
            "aside",
        ]
    ):
        tag.decompose()

    main = soup.select_one("div.content.all-txt, div.all-txt") or soup.body or soup
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


if __name__ == "__main__":  # pragma: no cover - manual verification helper
    try:
        latest = collect_latest(10)
    except Exception as exc:  # noqa: BLE001
        print("采集失败:", exc)
    else:
        for item in latest:
            print(item.get("published", ""), "-", item.get("title", ""), "-", item.get("url", ""))
