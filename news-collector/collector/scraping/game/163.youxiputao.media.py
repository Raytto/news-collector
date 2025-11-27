from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

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

SOURCE = "youxiputao-163"
SOURCE_LABEL_ZH = "游戏葡萄（网易号）"
CATEGORY = "game"
MOBILE_URL = "https://m.163.com/news/sub/T1441783781035.html"
PC_URL = "https://www.163.com/dy/media/T1441783781035.html"
MAX_ITEMS = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}


def fetch_mobile_page(url: str = MOBILE_URL) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return resp.text


def _extract_json_list(html: str, var_name: str) -> List[Dict[str, Any]]:
    """Extract the JSON array assigned to window.<var_name> = [...]"""
    pattern = re.compile(
        rf"window\.{re.escape(var_name)}\s*=\s*(\[[\s\S]*?\])\s*(?=window\.|</script>)",
    )
    m = pattern.search(html)
    if not m:
        return []
    raw = m.group(1)
    try:
        return json.loads(raw)
    except Exception:
        # Some pages may append stray tokens after the array; trim at the first closing bracket.
        if "]" in raw:
            try:
                return json.loads(raw.split("]")[0] + "]")
            except Exception:
                return []
        return []


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1_000_000_000_000:
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone(timedelta(hours=8)))
                return dt.astimezone(timezone.utc)
            except Exception:
                continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    return None


def _normalize_url(url: str) -> str:
    return url.split("?")[0].strip()


def _collect_raw_articles(html: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for var in ("tab_list_data", "doc_list_data"):
        for item in _extract_json_list(html, var):
            docid = str(item.get("docid") or item.get("postid") or "").strip()
            if docid and docid in seen_ids:
                continue
            if docid:
                seen_ids.add(docid)
            items.append(item)
    return items


def _to_entry(item: Dict[str, Any]) -> Optional[Dict[str, str]]:
    title = str(item.get("title") or item.get("ltitle") or "").strip()
    url = str(item.get("url") or item.get("link") or "").strip()
    if not title or not url:
        return None
    url = _normalize_url(url)
    raw_time = (
        item.get("ptime")
        or item.get("mtime")
        or item.get("lmodify")
        or item.get("timestamp")
        or ""
    )
    dt = _parse_datetime(raw_time)
    published = normalize_published_datetime(dt, str(raw_time or ""))
    return {"title": title, "url": url, "published": published, "source": SOURCE, "category": CATEGORY}


def collect_latest(limit: int = 20) -> List[Dict[str, str]]:
    html = fetch_mobile_page()
    entries: List[Dict[str, str]] = []
    seen_links: set[str] = set()
    for item in _collect_raw_articles(html):
        entry = _to_entry(item)
        if not entry:
            continue
        if entry["url"] in seen_links:
            continue
        seen_links.add(entry["url"])
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


# -----------------------
# Article detail fetching
# -----------------------

def _clean_text(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\u00a0", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.splitlines()).strip()


def _pick_main(soup: BeautifulSoup):
    for sel in ("div.post_body", "article", "div#endText", "div.article-body", "div.ne_article"):
        node = soup.select_one(sel)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup.find_all(["script", "style", "noscript", "iframe", "video", "img", "svg"]):
        tag.decompose()
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    return _clean_text(text)


def main(limit: int = 10) -> None:
    for item in collect_latest(limit=limit):
        print(f"{item['source']} - {item['published']} - {item['title']} - {item['url']}")


if __name__ == "__main__":
    main()
