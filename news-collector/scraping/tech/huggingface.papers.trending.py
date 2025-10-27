from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, List, Dict

import requests
from bs4 import BeautifulSoup

API_URL = "https://huggingface.co/api/papers/trending"
SOURCE = "huggingface-papers"
CATEGORY = "tech"

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def fetch_trending(api_url: str = API_URL) -> List[Dict[str, Any]]:
    """Fetch trending papers from Hugging Face."""
    headers = {"User-Agent": UA, "Accept": "application/json"}
    response = requests.get(api_url, headers=headers, timeout=30)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:  # pragma: no cover - defensive branch
        raise ValueError("Expected JSON response from Hugging Face trending API") from exc
    return _extract_items(payload)


def _extract_items(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("papers", "items", "results", "data", "entries"):
            maybe = payload.get(key)
            if isinstance(maybe, list):
                return [item for item in maybe if isinstance(item, dict)]
            if isinstance(maybe, dict):
                return [value for value in maybe.values() if isinstance(value, dict)]
    return []


def _first_non_empty(item: Dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str):
            text = value.strip()
            if text:
                return text
    return ""


def _extract_url(item: Dict[str, Any]) -> str:
    for key in (
        "url",
        "link",
        "paper_url",
        "paperUrl",
        "href",
        "paperLink",
        "web_url",
        "webUrl",
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    slug = _first_non_empty(item, ("slug", "paper_slug"))
    if slug:
        if slug.startswith("http"):
            return slug
        return f"https://huggingface.co/papers/{slug}"
    arxiv_id = _first_non_empty(item, ("arxiv_id", "arxivId", "arxiv_identifier"))
    if arxiv_id:
        if arxiv_id.startswith("http"):
            return arxiv_id
        return f"https://huggingface.co/papers/{arxiv_id}"
    identifier = _first_non_empty(item, ("id",))
    if identifier:
        if identifier.startswith("http"):
            return identifier
        return f"https://huggingface.co/papers/{identifier}"
    return ""


def _coerce_to_iso8601(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return ""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ""
        if text.isdigit():
            try:
                return datetime.fromtimestamp(int(text), tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                pass
        iso_candidates = [text]
        if text.endswith("Z"):
            iso_candidates.insert(0, text[:-1] + "+00:00")
        if len(text) == 10 and text.count("-") == 2:
            try:
                return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat()
            except ValueError:
                pass
        for candidate in iso_candidates:
            try:
                dt = datetime.fromisoformat(candidate)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc).isoformat()
            except ValueError:
                continue
        for fmt in ("%Y/%m/%d", "%d %b %Y", "%d %B %Y"):
            try:
                dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue
    return ""


def _extract_published(item: Dict[str, Any]) -> str:
    for key in (
        "published_at",
        "publishedAt",
        "published",
        "date",
        "created_at",
        "createdAt",
        "updated_at",
        "updatedAt",
        "last_updated",
        "lastUpdated",
        "timestamp",
        "time",
        "datetime",
    ):
        if key in item:
            iso = _coerce_to_iso8601(item.get(key))
            if iso:
                return iso
    return ""


def _parse_iso8601(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _sort_key(entry: Dict[str, str]) -> datetime:
    parsed = _parse_iso8601(entry.get("published", ""))
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return parsed


def process_papers(raw_items: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    results: List[Dict[str, str]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        title = _first_non_empty(item, ("title", "paper_title", "name"))
        url = _extract_url(item)
        published = _extract_published(item)
        results.append(
            {
                "title": title,
                "url": url,
                "published": published,
                "source": SOURCE,
                "category": CATEGORY,
            }
        )
    results.sort(key=_sort_key, reverse=True)
    return results


def _clean_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    while "\n\n\n" in normalized:
        normalized = normalized.replace("\n\n\n", "\n\n")
    normalized = "\n".join(line.rstrip() for line in normalized.splitlines())
    return normalized.strip()


def _pick_main(soup: BeautifulSoup):
    selectors = [
        "[data-testid='paper-abstract']",
        "[data-testid='paper-content']",
        "section.paper-content",
        "section.paper-page",
        "article",
        "main",
        ".paper-content",
        ".content",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node and node.get_text(strip=True):
            return node
    return soup.body or soup


def fetch_article_detail(url: str) -> str:
    response = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
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
            "button",
            "form",
            "nav",
            "aside",
            "footer",
            "header",
        ]
    ):
        tag.decompose()
    main = _pick_main(soup)
    text = main.get_text("\n", strip=True)
    cleaned = _clean_text(text)
    if cleaned:
        return cleaned
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    og_desc = soup.find("meta", attrs={"property": "og:description"})
    if og_desc and og_desc.get("content"):
        return og_desc["content"].strip()
    return ""


if __name__ == "__main__":
    try:
        items = process_papers(fetch_trending())
    except Exception as exc:  # pragma: no cover - manual debug helper
        print(f"Failed to fetch trending papers: {exc}")
    else:
        for entry in items[:10]:
            print(entry["published"], "-", entry["title"], "-", entry["url"])
