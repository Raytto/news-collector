import json
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup

API_URL = "https://r.jina.ai/https://naavik.co/wp-json/wp/v2/posts"
DIGEST_CATEGORY_ID = 3
MAX_ITEMS = 10


def extract_json_from_jina(text: str):
    """
    r.jina.ai 会把响应包成一段 Markdown，真正的 JSON 体在 `Markdown Content:` 之后。
    这里简单地切掉包装，返回 Python 对象。
    """
    marker = "Markdown Content:\n"
    idx = text.find(marker)
    payload = text[idx + len(marker) :] if idx != -1 else text
    payload = payload.strip()

    start = next((i for i, ch in enumerate(payload) if ch in "[{"), None)
    if start is None:
        raise ValueError("无法从代理响应中找到 JSON 起始字符")
    payload = payload[start:]

    end_char = "]" if payload[0] == "[" else "}"
    end = payload.rfind(end_char)
    if end == -1:
        raise ValueError("代理响应缺少 JSON 结束字符")

    return json.loads(payload[: end + 1])


def fetch_latest_digest(limit: int = MAX_ITEMS):
    params = [
        ("categories", DIGEST_CATEGORY_ID),
        ("per_page", limit),
        ("orderby", "date"),
        ("order", "desc"),
        ("_fields[]", "date"),
        ("_fields[]", "date_gmt"),
        ("_fields[]", "link"),
        ("_fields[]", "title"),
    ]
    resp = requests.get(API_URL, params=params, timeout=20)
    resp.raise_for_status()
    return extract_json_from_jina(resp.text)


def normalize_dt(post):
    for key in ("date_gmt", "date"):
        raw = post.get(key)
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    return ""


def clean_title(raw_title: str):
    if not raw_title:
        return ""
    text = BeautifulSoup(raw_title, "html.parser").get_text()
    return unescape(text).strip()


if __name__ == "__main__":
    posts = fetch_latest_digest()
    for post in posts[:MAX_ITEMS]:
        title = clean_title(post.get("title", {}).get("rendered", ""))
        url = post.get("link", "")
        published = normalize_dt(post)
        print(published, "-", title, "-", url)
