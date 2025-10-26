import feedparser
import re
from typing import Optional
import requests
from bs4 import BeautifulSoup

RSS_URL = "https://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss"
SOURCE = "gamesindustry.biz"
CATEGORY = "game"

def fetch_feed(url):
    d = feedparser.parse(url)
    if d.bozo:
        print("解析 RSS 时出错:", d.bozo_exception)
    return d

def process_entries(feed):
    entries = feed.entries
    results = []
    for e in entries:
        title = e.get("title", "")
        link  = e.get("link", "")
        published = e.get("published", e.get("updated", ""))
        # 你可解析 published 为 datetime
        results.append({
            "title": title,
            "url": link,
            "published": published,
            "source": SOURCE,
            "category": CATEGORY,
        })
    return results

if __name__ == "__main__":
    feed = fetch_feed(RSS_URL)
    items = process_entries(feed)
    for it in items[:10]:
        print(it["published"], "-", it["title"], "-", it["url"])


# -----------------------
# Article detail fetching
# -----------------------

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def _clean_text(text: str) -> str:
    s = re.sub(r"\r\n?", "\n", text)
    s = re.sub(r"\u00a0", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = "\n".join(line.rstrip() for line in s.splitlines())
    return s.strip()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "div.article-body",
        "div.article__content",
        "article .article-body",
        "article .content",
        "article",
        "main .content",
        ".rich-text",
        ".content",
    ]
    for sel in selectors:
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=30)
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
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)
