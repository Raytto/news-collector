from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Dict, List, Tuple
from urllib.parse import urljoin

import feedparser
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

SOURCE = "pubmed-trending"
SOURCE_LABEL_ZH = "PubMed 趋势"
CATEGORY = "general"

BASE_URL = "https://pubmed.ncbi.nlm.nih.gov"
TRENDING_URL = f"{BASE_URL}/trending/?sort=date"
CREATE_RSS_PATH = "/create-rss-feed-url/"
MAX_ITEMS = 30  # keep runs lightweight; PubMed allows up to 100
TIMEOUT = 20


def _build_session() -> requests.Session:
    return requests.Session()


def _extract_form_fields(html: str) -> Tuple[str, str, str]:
    """Pull CSRF token, search term list, and RSS endpoint from the trending page."""
    soup = BeautifulSoup(html, "html.parser")
    token_input = soup.find("input", {"name": "csrfmiddlewaretoken"})
    form = soup.select_one("#rss-action-panel-form")
    if not token_input or not form:
        raise ValueError("未找到 RSS 表单或 CSRF token")
    token = (token_input.get("value") or "").strip()
    term = (form.get("data-search-form-term-value") or "").strip()
    create_path = (form.get("data-create-rss-feed-url") or CREATE_RSS_PATH).strip()
    if not (token and term):
        raise ValueError("RSS 表单字段缺失")
    create_url = urljoin(TRENDING_URL, create_path)
    return token, term, create_url


def _create_feed_url(session: requests.Session, limit: int = MAX_ITEMS) -> str:
    resp = session.get(TRENDING_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    token, term, create_url = _extract_form_fields(resp.text)
    payload = {
        "csrfmiddlewaretoken": token,
        "name": "pubmed-trending",
        "limit": str(max(1, min(int(limit or MAX_ITEMS), 100))),
        "term": term,
        "sort": "date",
    }
    resp = session.post(create_url, data=payload, headers={"Referer": TRENDING_URL}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    feed_url = data.get("rss_feed_url")
    if not isinstance(feed_url, str) or not feed_url:
        raise ValueError(f"未获取到 RSS 链接: {json.dumps(data, ensure_ascii=False)}")
    return feed_url


def _fetch_feed_content(session: requests.Session, url: str) -> bytes:
    resp = session.get(
        url,
        timeout=TIMEOUT,
        headers={
            "Accept": "application/rss+xml,application/xml;q=0.9,text/xml;q=0.8,*/*;q=0.5",
            "Referer": TRENDING_URL,
        },
    )
    resp.raise_for_status()
    return resp.content


def fetch_feed(limit: int = MAX_ITEMS) -> feedparser.FeedParserDict:
    session = _build_session()
    feed_url = _create_feed_url(session, limit=limit)
    content = _fetch_feed_content(session, feed_url)
    return feedparser.parse(content)


def _parse_published(entry) -> str:
    raw = entry.get("published") or entry.get("updated") or ""
    dt: datetime | None = None
    if raw:
        try:
            dt = parsedate_to_datetime(raw)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except Exception:
                dt = None
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    return normalize_published_datetime(dt, str(raw or ""))


def _clean_text(text: str) -> str:
    if not text:
        return ""
    soup = BeautifulSoup(text, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "svg", "img"]):
        tag.decompose()
    cleaned = soup.get_text("\n", strip=True).replace("\r\n", "\n").replace("\r", "\n")
    cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
    return cleaned.strip()


def _parse_published_text(raw: str) -> str:
    raw = (raw or "").strip()
    if not raw:
        return ""
    normalized = normalize_published_datetime(None, raw)
    if normalized:
        return normalized
    m = re.search(r"(?:19|20)\\d{2}[^;,.]*", raw)
    if m:
        normalized = normalize_published_datetime(None, m.group(0))
        if normalized:
            return normalized
    parts = raw.split(";")[0]
    return normalize_published_datetime(None, parts)


def fetch_list_page(session: requests.Session | None = None) -> str:
    sess = session or _build_session()
    resp = sess.get(TRENDING_URL, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def parse_list(html: str, limit: int = MAX_ITEMS) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, str]] = []
    seen: set[str] = set()
    max_items = max(1, min(int(limit or MAX_ITEMS), MAX_ITEMS))
    for art in soup.select("article.full-docsum"):
        if len(items) >= max_items:
            break
        title_tag = art.select_one("a.docsum-title")
        if not title_tag:
            continue
        url = urljoin(BASE_URL, title_tag.get("href") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        title = title_tag.get_text(" ", strip=True)
        pub_tag = art.select_one("span.docsum-pubdate") or art.select_one(
            "span.docsum-journal-citation.full-journal-citation"
        )
        published = _parse_published_text(pub_tag.get_text(" ", strip=True) if pub_tag else "")
        abstract = ""
        snippet = art.select_one(".docsum-snippet, .full-view-snippet")
        if snippet:
            abstract = _clean_text(snippet.get_text(" ", strip=True))
        items.append(
            {
                "title": title,
                "url": url,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
                "detail": abstract,
            }
        )
    return items


def process_entries(feed: feedparser.FeedParserDict, limit: int = MAX_ITEMS) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    seen: set[str] = set()
    max_items = max(1, min(int(limit or MAX_ITEMS), MAX_ITEMS))
    for entry in getattr(feed, "entries", []):
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        if not (title and link) or link in seen:
            continue
        seen.add(link)
        published = _parse_published(entry)
        summary = entry.get("summary") or entry.get("description") or ""
        items.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
                "detail": _clean_text(summary),
            }
        )
        if len(items) >= max_items:
            break
    return items


def collect_latest(limit: int = MAX_ITEMS) -> List[Dict[str, str]]:
    """Convenience wrapper for compatibility with other collectors."""
    try:
        feed = fetch_feed(limit=limit)
        return process_entries(feed, limit=limit)
    except Exception as ex:
        try:
            print(f"[pubmed-trending] RSS 获取失败，改用 HTML 解析: {ex}")
        except Exception:
            pass
        html = fetch_list_page()
        return parse_list(html, limit=limit)
