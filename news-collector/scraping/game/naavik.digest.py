import json
import re
from datetime import datetime, timezone
from html import unescape

import requests
from bs4 import BeautifulSoup
from typing import Optional

API_URL = "https://r.jina.ai/https://naavik.co/wp-json/wp/v2/posts"
DIGEST_CATEGORY_ID = 3
MAX_ITEMS = 10
SOURCE = "naavik"
CATEGORY = "game"


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


def collect_latest_digest(limit: int = MAX_ITEMS):
    posts = fetch_latest_digest(limit)
    entries = []
    for post in posts[:limit]:
        title = clean_title(post.get("title", {}).get("rendered", ""))
        url = post.get("link", "")
        published = normalize_dt(post)
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


if __name__ == "__main__":
    entries = collect_latest_digest()
    for entry in entries[:MAX_ITEMS]:
        print(entry["published"], "-", entry["title"], "-", entry["url"])


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
        "article .entry-content",
        "div.entry-content",
        "article .post-content",
        "div.post-content",
        ".prose",
        ".rich-text",
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
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://naavik.co/",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=20)
        resp.raise_for_status()
        html = resp.text
    except Exception:
        # Fallback: use r.jina.ai readability proxy to bypass WAF and return markdown
        try:
            jurl = f"https://r.jina.ai/{url}"
            jresp = requests.get(jurl, headers={"User-Agent": UA}, timeout=25)
            jresp.raise_for_status()
            # The proxy returns readable Markdown. Strip simple markdown marks to get text.
            md = jresp.text
            # strip links [text](url)
            md = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", md)
            # remove emphasis and headers markers
            md = re.sub(r"(^|\s)[#*_`]+|[#*_`]+($|\s)", " ", md)
            # normalize newlines
            md = re.sub(r"\r\n?", "\n", md)
            md = re.sub(r"\n{3,}", "\n\n", md)
            return md.strip()
        except Exception:
            raise
    soup = BeautifulSoup(html, "html.parser")
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
