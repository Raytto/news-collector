from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Tuple

import feedparser
import requests
from bs4 import BeautifulSoup

SOURCE = "deepmind"
CATEGORY = "tech"

# DeepMind 在 2024 年迁移到了 *.google 域，公开 RSS 地址未写在页面上，
# 但仍然保留了若干常见的 feed 路径。这里按优先级尝试多个候选，
# 以便在站点将来调整时仍能自动发现可用的订阅源。
FEED_CANDIDATES: Tuple[str, ...] = (
    "https://deepmind.google/discover/blog/rss/",
    "https://deepmind.google/discover/blog/rss.xml",
    "https://deepmind.google/discover/blog/feed/",
    "https://deepmind.google/discover/blog/feed.xml",
    "https://deepmind.com/blog/rss/",
    "https://deepmind.com/blog/feed.xml",
)

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
TIMEOUT = 30


def fetch_feed(url: str) -> feedparser.FeedParserDict:
    feed = feedparser.parse(url)
    if feed.bozo:
        # bozo_exception 常见于轻微的格式告警，这里只打印提示不直接失败
        print("解析 RSS 时可能有问题:", getattr(feed, "bozo_exception", None))
    return feed


def discover_feed() -> Tuple[str, feedparser.FeedParserDict]:
    last_exception = None
    for url in FEED_CANDIDATES:
        try:
            feed = fetch_feed(url)
        except Exception as exc:  # pragma: no cover - feedparser 极少抛异常
            last_exception = exc
            continue
        entries = getattr(feed, "entries", [])
        if entries:
            return url, feed
        # 某些路径返回 200 但条目为空，记录最后一次异常用于报错
        last_exception = getattr(feed, "bozo_exception", None)
    raise RuntimeError(
        "未能找到可用的 DeepMind 博客 RSS 源" + (
            f": {last_exception}" if last_exception else ""
        )
    )


def _parse_datetime(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return datetime(*value[:6], tzinfo=timezone.utc)
            except Exception:
                continue
    for key in ("published", "updated"):
        raw = entry.get(key)
        if not raw:
            continue
        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            try:
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
    return None


def _normalize_url(link: str) -> str:
    if not link:
        return ""
    if link.startswith("http://") or link.startswith("https://"):
        return link
    if not link.startswith("/"):
        link = "/" + link
    return f"https://deepmind.google{link}"


def process_entries(feed: feedparser.FeedParserDict) -> List[dict]:
    results: List[dict] = []
    for entry in getattr(feed, "entries", []):
        title = entry.get("title", "").strip()
        link = _normalize_url(entry.get("link", ""))
        dt = _parse_datetime(entry)
        published = dt.isoformat() if dt else entry.get("published", "")
        if not (title and link):
            continue
        results.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )

    def sort_key(item: dict) -> datetime:
        try:
            return datetime.fromisoformat(item["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    results.sort(key=sort_key, reverse=True)
    return results


# -----------------------
# Article detail fetching
# -----------------------


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


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
    ]):
        tag.decompose()
    for noisy in soup.select('[aria-label*="share" i], [class*="share" i]'):
        noisy.decompose()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "article .article-content",
        "article [class*='article']",
        "main article",
        "main [class*='content']",
        "article",
        "main",
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=TIMEOUT)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    _strip_noise(soup)
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


def collect_latest(limit: int = 20) -> List[dict]:
    _, feed = discover_feed()
    entries = process_entries(feed)
    return entries[:limit]


def main() -> None:
    items = collect_latest()
    for item in items[:10]:
        print(item["published"], "-", item["title"], "-", item["url"])


if __name__ == "__main__":
    main()
