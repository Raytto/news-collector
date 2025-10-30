#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import db  # noqa: E402

# Addresses extracted from the individual scraper modules in
# news-collector/collector/scraping.
ADDRESS_MAP: dict[str, list[str]] = {
    "chuapp": ["https://www.chuapp.com/feed"],
    "deconstructoroffun": ["https://www.deconstructoroffun.com/blog?format=rss"],
    "gamedeveloper": ["https://www.gamedeveloper.com/rss.xml"],
    "gamesindustry.biz": ["https://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss"],
    "indienova": ["https://indienova.com/feed/"],
    "naavik": ["https://r.jina.ai/https://naavik.co/wp-json/wp/v2/posts"],
    "nikopartners": ["https://nikopartners.com/feed/?nocache=1"],
    "sensortower": [
        "https://sensortower.com",
        "https://sensortower.com/blog",
        "https://68fbc366dfb50000086e3939--sensortower-prod.netlify.app/.netlify/functions/graphql",
    ],
    "youxituoluo": ["https://www.youxituoluo.com/"],
    "philomag.com": ["https://www.philomag.com/rss-le-fil"],
    "philomag.de": ["https://www.philomag.de/rss.xml"],
    "philosophynow.org": ["https://philosophynow.org/rss"],
    "a16z": ["https://a16z.substack.com/feed"],
    "arxiv.cs_ai": [
        "https://export.arxiv.org/api/query?search_query=cat:cs.AI&sortBy=submittedDate&sortOrder=descending&max_results=50"
    ],
    "deepmind": ["https://deepmind.google", "https://deepmind.google/discover/blog/"],
    "huggingface-papers": ["https://huggingface.co", "https://huggingface.co/blog"],
    "jiqizhixin": [
        "https://www.jiqizhixin.com/",
        "https://www.jiqizhixin.com/api/article_library/articles.json",
    ],
    "openai.research": ["https://openai.com/blog/rss.xml"],
    "qbitai-news": ["https://www.qbitai.com/category/资讯/feed"],
    "qbitai-zhiku": ["https://www.qbitai.com/category/zhiku/feed"],
    "semianalysis": ["https://semianalysis.com/feed/"],
    "stratechery": ["https://stratechery.passport.online/feed/rss/CUXZnvi6sHPnV39z2Hje1"],
    "thegradient": ["https://thegradient.pub/rss/"],
}


def _normalize(addresses: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in addresses:
        text = (raw or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def main() -> None:
    db.ensure_db()
    conn = sqlite3.connect(db.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    rows = cur.execute("SELECT id, key FROM sources").fetchall()
    key_to_id = {row[1]: int(row[0]) for row in rows}

    inserted = 0
    skipped: list[str] = []

    for key, addresses in ADDRESS_MAP.items():
        source_id = key_to_id.get(key)
        if not source_id:
            skipped.append(key)
            continue
        values = _normalize(addresses)
        cur.execute("DELETE FROM source_address WHERE source_id=?", (source_id,))
        for addr in values:
            cur.execute(
                "INSERT OR IGNORE INTO source_address (source_id, address) VALUES (?, ?)",
                (source_id, addr),
            )
            inserted += 1

    conn.commit()
    conn.close()

    print(f"Inserted/updated {inserted} source addresses.")
    if skipped:
        print("Sources missing from ADDRESS_MAP:", ", ".join(sorted(skipped)))


if __name__ == "__main__":
    main()
