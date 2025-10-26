from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import importlib.util


ROOT = Path(__file__).resolve().parents[1]
SCRAPING_DIR = ROOT / "scraping"
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"


@dataclass
class Entry:
    source: str
    publish: str
    title: str
    link: str
    category: str = ""


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _to_entry_dicts(mod) -> List[Dict[str, Any]]:
    # Priority 1: explicit collectors that already return list[dict]
    if hasattr(mod, "collect_latest_posts"):
        return list(getattr(mod, "collect_latest_posts")())
    if hasattr(mod, "collect_latest_digest"):
        return list(getattr(mod, "collect_latest_digest")())

    # Priority 2: homepage + collector pattern (e.g., youxituoluo)
    if hasattr(mod, "fetch_homepage") and hasattr(mod, "collect_articles"):
        html = getattr(mod, "fetch_homepage")()
        items = list(getattr(mod, "collect_articles")(html))
        # Optional sort if provided
        if hasattr(mod, "sort_articles"):
            items = list(getattr(mod, "sort_articles")(items))
        return items

    # Priority 3: RSS style with (fetch_feed, collect_entries|process_entries)
    if hasattr(mod, "fetch_feed"):
        feed = getattr(mod, "fetch_feed")(getattr(mod, "RSS_URL", None))
        if hasattr(mod, "collect_entries"):
            return list(getattr(mod, "collect_entries")(feed))
        if hasattr(mod, "process_entries"):
            return list(getattr(mod, "process_entries")(feed))

    raise RuntimeError(f"未找到可用于采集的入口函数: {mod.__name__}")


def _coerce_entry(item: Dict[str, Any]) -> Optional[Entry]:
    title = str(item.get("title") or "").strip()
    link = str(item.get("url") or item.get("link") or "").strip()
    publish = str(item.get("published") or item.get("publish") or "").strip()
    source = str(item.get("source") or "").strip()
    category = str(item.get("category") or "").strip()
    if not (title and link):
        return None
    # Try to normalize publish to seconds if it looks like a YYYY-MM-DD HH:MM
    # Leave as-is for coarse strings like "October 2025".
    try:
        # Replace 'T' with space for parsing; accept timezone suffixes
        raw = publish.replace("T", " ")
        # If no seconds part, add :00 for parsing
        if raw and raw.count(":") == 1:
            raw += ":00"
        # Attempt strict parse; if tz present, datetime.fromisoformat handles it
        dt = datetime.fromisoformat(publish.replace("Z", "+00:00"))
        publish = dt.isoformat()
    except Exception:
        # Keep original string
        pass
    return Entry(source=source, publish=publish, title=title, link=link, category=category)


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            publish TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            category TEXT
        )
        """
    )
    # Backfill: add category column if missing in existing DB
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(info)")}
        if "category" not in cols:
            conn.execute("ALTER TABLE info ADD COLUMN category TEXT")
    except Exception:
        pass
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_info_unique
        ON info (source, publish, title)
        """
    )
    conn.commit()


def _insert_entries(conn: sqlite3.Connection, entries: Iterable[Entry]) -> int:
    cur = conn.cursor()
    inserted = 0
    for e in entries:
        try:
            cur.execute(
                """
                INSERT INTO info (source, publish, title, link, category)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source, publish, title) DO NOTHING
                """,
                (e.source, e.publish, e.title, e.link, e.category),
            )
            if cur.rowcount:
                inserted += 1
        except sqlite3.OperationalError:
            # For older SQLite lacking DO NOTHING, emulate via IGNORE
            cur.execute(
                "INSERT OR IGNORE INTO info (source, publish, title, link, category) VALUES (?, ?, ?, ?, ?)",
                (e.source, e.publish, e.title, e.link, e.category),
            )
            if cur.rowcount:
                inserted += 1
    conn.commit()
    return inserted


def main() -> None:
    print(f"收集目录: {SCRAPING_DIR}")
    if not SCRAPING_DIR.exists():
        print("未找到 scraping 目录")
        sys.exit(1)

    modules = []
    # Recursively discover scraper scripts (e.g., scraping/game/*.py)
    for path in sorted(SCRAPING_DIR.rglob("*.py")):
        if path.name.startswith("__"):
            continue
        modules.append(path)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_db(conn)

        total_new = 0
        for path in modules:
            try:
                mod = _load_module(path)
                items = _to_entry_dicts(mod)
                entries: List[Entry] = []
                for item in items:
                    e = _coerce_entry(item)
                    if e:
                        # Backfill source/category from module-level constants when missing
                        if not e.source and hasattr(mod, "SOURCE"):
                            e.source = str(getattr(mod, "SOURCE"))
                        if not e.category and hasattr(mod, "CATEGORY"):
                            e.category = str(getattr(mod, "CATEGORY"))
                        entries.append(e)
                added = _insert_entries(conn, entries)
                total_new += added
                print(f"{path.name}: 解析 {len(items)} 条，新增 {added} 条")
            except Exception as exc:
                print(f"{path.name}: 处理失败 - {exc}")

        print(f"完成，数据库: {DB_PATH}，新增总计 {total_new} 条")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
