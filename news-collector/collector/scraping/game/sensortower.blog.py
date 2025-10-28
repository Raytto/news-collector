import json
import re
import calendar
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from typing import Dict, List

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

BASE_URL = "https://sensortower.com"
BLOG_URL = f"{BASE_URL}/blog"
GRAPHQL_ENDPOINT = (
    "https://68fbc366dfb50000086e3939--sensortower-prod.netlify.app/"
    ".netlify/functions/graphql"
)
DEFAULT_LOCALE = "en-US"
MAX_ITEMS = 10
SOURCE = "sensortower"
CATEGORY = "game"

# Map English month names/abbreviations to numbers for month-level dates like "October 2025".
_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def _parse_month_year(text: str):
    if not text:
        return None
    s = text.strip()
    # Pattern: MonthName YYYY (e.g., "October 2025" or "Oct 2025")
    m = re.match(r"^([A-Za-z]+)[\s,]+(\d{4})$", s)
    if m:
        month_name = m.group(1).lower()
        year = int(m.group(2))
        month = _MONTHS.get(month_name)
        if month:
            return year, month
    # Pattern: YYYY-MM / YYYY/M / YYYY.MM
    m = re.match(r"^(\d{4})[\-/.](\d{1,2})$", s)
    if m:
        year = int(m.group(1))
        month = int(m.group(2))
        if 1 <= month <= 12:
            return year, month
    return None


def _normalize_published(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    ym = _parse_month_year(raw)
    if ym:
        year, month = ym
        dt = datetime(year, month, 1, tzinfo=timezone.utc)
        synthetic_raw = f"{year}-{month:02d}"
        return normalize_published_datetime(dt, synthetic_raw)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        dt = None
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return normalize_published_datetime(dt, raw)

LISTING_QUERY = """
query BlogListing($id: String!, $limit: Int!, $locale: String!) {
  content(id: $id, locale: $locale) {
    __typename
    ... on Collection {
      itemsConnection(limit: $limit, offset: 0, filter: {order: "-fields.pubDate"}) {
        items {
          __typename
          ... on Card {
            id
            subtitle
            body {
              json
            }
            link {
              href
              text
            }
          }
        }
      }
    }
  }
}
"""

POSTS_QUERY = """
query BlogPosts($ids: [String!], $locale: String!) {
  contents(filter: {ids: $ids, locale: $locale, contentTypes: ["blog"]}) {
    __typename
    ... on Blog {
      id
      title
      slug
      pubDate
    }
  }
}
"""


def fetch_next_data():
    resp = requests.get(BLOG_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script or not script.string:
        raise RuntimeError("无法从博客首页解析 __NEXT_DATA__")
    return json.loads(script.string)


def extract_collection_info(data):
    page = (
        data.get("props", {})
        .get("pageProps", {})
        .get("pageData", {})
        .get("page", {})
    )
    sections = page.get("contents", [])
    for section in sections:
        coll = section.get("collection")
        if coll and coll.get("id"):
            items = (
                (coll.get("itemsConnection") or {})
                .get("items")
                or []
            )
            return coll["id"], items
    raise RuntimeError("未找到可用的集合 ID")


def gql_request(query: str, variables: Dict):
    resp = requests.post(
        GRAPHQL_ENDPOINT,
        json={"query": query, "variables": variables},
        timeout=30,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        raise RuntimeError(f"GraphQL 请求失败: {payload['errors']}")
    return payload["data"]


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
        ".rich-text",
        "article .rich-text",
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


def fetch_listing(collection_id: str, locale: str) -> List[Dict]:
    data = gql_request(
        LISTING_QUERY,
        {"id": collection_id, "limit": MAX_ITEMS, "locale": locale},
    )
    content = data.get("content") or {}
    connection = content.get("itemsConnection") or {}
    return connection.get("items") or []


def fetch_post_meta(ids: List[str], locale: str) -> Dict[str, Dict]:
    if not ids:
        return {}
    data = gql_request(
        POSTS_QUERY,
        {"ids": ids, "locale": locale},
    )
    results = {}
    for entry in data.get("contents", []):
        if entry and entry.get("__typename") == "Blog":
            results[entry["id"]] = entry
    return results


def extract_heading(card: Dict) -> str:
    body = card.get("body") or {}
    doc = body.get("json") or {}
    for block in doc.get("content", []):
        for node in block.get("content", []):
            value = (node or {}).get("value")
            if value:
                return value.strip()
    return ""


def normalize_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if not href.startswith("/"):
        href = "/" + href
    return BASE_URL + href


def collect_latest_posts(limit: int = MAX_ITEMS):
    data = fetch_next_data()
    collection_id, fallback_items = extract_collection_info(data)
    locale = data.get("locale", DEFAULT_LOCALE)

    try:
        cards = fetch_listing(collection_id, locale)
        if not cards:
            cards = fallback_items
    except Exception:
        cards = fallback_items

    cards = cards[:limit]
    ids = [card.get("id") for card in cards if card.get("id")]
    meta = {}
    try:
        meta = fetch_post_meta(ids, locale)
    except Exception:
        pass

    entries = []
    for card in cards:
        cid = card.get("id")
        detail = meta.get(cid, {})
        title = (detail.get("title") or extract_heading(card) or "").strip()
        url = normalize_url((card.get("link") or {}).get("href", ""))
        published_raw = (detail.get("pubDate") or card.get("subtitle") or "").strip()
        published = _normalize_published(published_raw)
        if not (title and url):
            continue
        entries.append(
            {
                "title": title,
                "url": url,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )
    return entries


def main():
    entries = collect_latest_posts()
    for entry in entries:
        print(entry["published"], "-", entry["title"], "-", entry["url"])


if __name__ == "__main__":
    main()
