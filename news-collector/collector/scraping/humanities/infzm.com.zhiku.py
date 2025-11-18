from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

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
        helper = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(helper)  # type: ignore[attr-defined]
        sys.modules[module_name] = helper
    normalize_published_datetime = helper.normalize_published_datetime


BASE_URL = "https://www.infzm.com"
LIST_URL = f"{BASE_URL}/topics/t219.html"
SOURCE = "infzm-zhiku"
CATEGORY = "general"
SOURCE_LABEL_ZH = "南方周末·智库"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Referer": LIST_URL,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        }
    )
    return session


def fetch_list_page(url: str = LIST_URL) -> str:
    """Fetch the list page HTML for 南方周末·智库.

    Kept simple: rely on the global HTTP limits installed by the collector.
    """

    session = _build_session()
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    return resp.text


def _normalize_url(href: str) -> str:
    if not href:
        return ""
    href = href.strip()
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return urljoin(BASE_URL, href)


def _parse_timestamp(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return ""
        return normalize_published_datetime(dt, str(value))

    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return ""

        try:
            import re as _re

            # Relative forms: “1小时前”, “3天前”, “昨天”等
            m = _re.match(r"^(\d+)\s*小时前$", raw)
            if m:
                hours = int(m.group(1))
                dt = datetime.now(timezone.utc) - timedelta(hours=hours)
                return normalize_published_datetime(dt, raw)

            m = _re.match(r"^(\d+)\s*分钟前$", raw)
            if m:
                minutes = int(m.group(1))
                dt = datetime.now(timezone.utc) - timedelta(minutes=minutes)
                return normalize_published_datetime(dt, raw)

            m = _re.match(r"^(\d+)\s*天前$", raw)
            if m:
                days = int(m.group(1))
                dt = datetime.now(timezone.utc) - timedelta(days=days)
                return normalize_published_datetime(dt, raw)

            if raw in {"昨天", "昨日"}:
                dt = datetime.now(timezone.utc) - timedelta(days=1)
                return normalize_published_datetime(dt, raw)

            if raw in {"前天"}:
                dt = datetime.now(timezone.utc) - timedelta(days=2)
                return normalize_published_datetime(dt, raw)

            if raw in {"刚刚", "今天", "今日"}:
                dt = datetime.now(timezone.utc)
                return normalize_published_datetime(dt, raw)

            # "11-17" / "11-17 12:34" (no year) -> assume current year
            m = _re.match(
                r"^(\d{1,2})-(\d{1,2})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$",
                raw,
            )
            if m:
                now = datetime.now(timezone.utc)
                month = int(m.group(1))
                day = int(m.group(2))
                hour = int(m.group(3) or 0)
                minute = int(m.group(4) or 0)
                second = int(m.group(5) or 0)
                try:
                    dt = datetime(now.year, month, day, hour, minute, second, tzinfo=timezone.utc)
                except Exception:
                    dt = datetime(now.year, month, day, tzinfo=timezone.utc)
                return normalize_published_datetime(dt, raw)

        except Exception:
            # Fall through to more generic parsing below
            pass

        # Pure digits: maybe a timestamp
        if raw.isdigit():
            return _parse_timestamp(float(raw))  # type: ignore[arg-type]

        # Try a few common absolute formats with year
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S%z",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y/%m/%d %H:%M",
            "%Y.%m.%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d",
            "%Y.%m.%d",
        ):
            try:
                dt = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            except Exception:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            return normalize_published_datetime(dt, raw)

        # Fallback: let helper try best-effort normalization
        return normalize_published_datetime(None, raw)

    return ""


def _extract_items_from_soup(soup: BeautifulSoup) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []

    # Primary list block on 智库频道
    container = soup.select_one("section.nfzm-panel--list ul.nfzm-list")
    if container is None:
        # Fallback to any UL that looks like a content list
        container = soup.select_one("ul.nfzm-list")

    anchors: Iterable[Tag]
    if container is not None:
        anchors = container.select("li > a[href]")
    else:
        anchors = soup.select("a[href^='/contents/']")

    for a in anchors:
        href = a.get("href") or ""
        url = _normalize_url(href)
        if not url:
            continue

        title_node = a.select_one(".nfzm-content-item__title h5") or a.select_one("h5")
        if not title_node:
            continue
        title = title_node.get_text(strip=True)
        if not title:
            continue

        # Meta block holds tag + time + optional comments count
        published = ""
        meta = a.select_one(".nfzm-content-item__meta")
        if meta is not None:
            span_texts = [
                s.get_text(strip=True)
                for s in meta.find_all("span")
                if s.get_text(strip=True)
            ]
            for text in reversed(span_texts):
                ts = _parse_timestamp(text)
                if ts:
                    published = ts
                    break

        items.append(
            {
                "title": title,
                "url": url,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )

    # Deduplicate by URL while preserving order
    seen = set()
    unique: List[Dict[str, str]] = []
    for item in items:
        u = item.get("url")
        if not u or u in seen:
            continue
        seen.add(u)
        unique.append(item)

    # Sort by published time when available
    def sort_key(it: Dict[str, str]):
        ts = it.get("published") or ""
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    unique.sort(key=sort_key, reverse=True)
    return unique


def parse_list(html: str) -> List[Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    return _extract_items_from_soup(soup)


def collect_latest(limit: int = 20) -> List[Dict[str, str]]:
    html = fetch_list_page(LIST_URL)
    items = parse_list(html)
    return items[:limit]


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
        "[aria-label*='share' i], [class*='share' i], [class*='social' i], "
        "[class*='related' i], .nfzm-article-jumbotron"
    ):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "div.nfzm-content__fulltext",
        "div.nfzm-content__content",
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
    session = _build_session()
    resp = session.get(url, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    _strip_noise(soup)
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


def main() -> None:  # pragma: no cover - manual smoke test
    items = collect_latest(limit=10)
    for item in items:
        print(item["published"], "-", item["title"], "-", item["url"])


if __name__ == "__main__":  # pragma: no cover
    main()
