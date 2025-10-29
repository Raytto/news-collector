import feedparser
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
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
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        sys.modules[module_name] = helper
    normalize_published_datetime = helper.normalize_published_datetime

RSS_URL = "https://www.deconstructoroffun.com/blog?format=rss"
SOURCE = "deconstructoroffun"
CATEGORY = "game"


def fetch_feed(url: str):
    # Squarespace RSS sometimes includes HTML5 entities that sgmllib3k
    # doesn't recognize, which triggers feedparser's sanitizer and raises
    # "undefined entity" bozo warnings. Disable HTML sanitization so we
    # can still parse entries without noisy warnings.
    d = feedparser.parse(url, sanitize_html=False, resolve_relative_uris=False)
    if d.bozo:
        bex = getattr(d, "bozo_exception", None)
        # Suppress the frequent Squarespace "undefined entity" noise.
        if bex and "undefined entity" in str(bex).lower():
            pass
        else:
            print(f"解析 RSS 时可能有问题: {url} ({SOURCE}) ->", bex)
    return d


def parse_dt(entry):
    for key in ("published_parsed", "updated_parsed"):
        if getattr(entry, key, None):
            try:
                return datetime(*entry[key][:6], tzinfo=timezone.utc)
            except Exception:
                pass

    for key in ("published", "updated"):
        val = entry.get(key)
        if val:
            try:
                dt = parsedate_to_datetime(val)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                try:
                    dt = datetime.fromisoformat(val)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except Exception:
                    continue
    return None


def process_entries(feed):
    results = []
    for e in feed.entries:
        title = e.get("title", "")
        link = e.get("link", "")
        dt = parse_dt(e)
        raw_published = e.get("published") or e.get("updated") or ""
        published = normalize_published_datetime(dt, raw_published)
        results.append(
            {
                "title": title,
                "url": link,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )

    def sort_key(x):
        try:
            return datetime.fromisoformat(x["published"])
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    results.sort(key=sort_key, reverse=True)
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
        "article .post-content",
        "div.post-content",
        "div.entry-content",
        "article",
        "main .content",
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
