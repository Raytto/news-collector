import json
from typing import Dict, List

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://sensortower.com"
BLOG_URL = f"{BASE_URL}/blog"
GRAPHQL_ENDPOINT = (
    "https://68fbc366dfb50000086e3939--sensortower-prod.netlify.app/"
    ".netlify/functions/graphql"
)
DEFAULT_LOCALE = "en-US"
MAX_ITEMS = 10
SOURCE = "sensortower"

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
        published = (detail.get("pubDate") or card.get("subtitle") or "").strip()
        if not (title and url):
            continue
        entries.append(
            {
                "title": title,
                "url": url,
                "published": published,
                "source": SOURCE,
            }
        )
    return entries


def main():
    entries = collect_latest_posts()
    for entry in entries:
        print(entry["published"], "-", entry["title"], "-", entry["url"])


if __name__ == "__main__":
    main()
