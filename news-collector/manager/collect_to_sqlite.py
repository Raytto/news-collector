from __future__ import annotations

import os
import sqlite3
import sys
from dataclasses import dataclass
import re
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
    for name in ("collect_latest", "collect_latest_posts", "collect_latest_digest"):
        fn = getattr(mod, name, None)
        if callable(fn):
            return list(fn())

    # Priority 2: homepage + collector pattern (e.g., youxituoluo)
    if hasattr(mod, "fetch_homepage") and hasattr(mod, "collect_articles"):
        html = getattr(mod, "fetch_homepage")()
        items = list(getattr(mod, "collect_articles")(html))
        # Optional sort if provided
        if hasattr(mod, "sort_articles"):
            items = list(getattr(mod, "sort_articles")(items))
        return items

    # Priority 3: “trending API” style (e.g., huggingface.papers.trending)
    if hasattr(mod, "fetch_trending") and hasattr(mod, "process_papers"):
        raw = getattr(mod, "fetch_trending")()
        return list(getattr(mod, "process_papers")(raw))

    # Priority 4: list page + parser (e.g., openai.research.index)
    if hasattr(mod, "fetch_list_page") and hasattr(mod, "parse_list"):
        html = getattr(mod, "fetch_list_page")()
        return list(getattr(mod, "parse_list")(html))

    # Priority 5: RSS style with (fetch_feed, collect_entries|process_entries)
    if hasattr(mod, "fetch_feed"):
        fetch = getattr(mod, "fetch_feed")
        try:
            feed = fetch()  # Prefer module defaults
        except TypeError:
            url = (
                getattr(mod, "RSS_URL", None)
                or getattr(mod, "FEED_URL", None)
                or getattr(mod, "URL", None)
            )
            feed = fetch(url)
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


ISO_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+\-]\d{2}:\d{2})$"
)


def _is_iso8601_full(text: str) -> bool:
    if not text:
        return False
    s = text.strip()
    if not ISO_PATTERN.match(s):
        # Quick parse attempt to tolerate rare variants
        try:
            from datetime import datetime
            datetime.fromisoformat(s.replace("Z", "+00:00"))
            # But still require the presence of 'T' (full timestamp)
            return "T" in s
        except Exception:
            return False
    return True


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            publish TEXT NOT NULL,
            title TEXT NOT NULL,
            link TEXT NOT NULL,
            category TEXT,
            detail TEXT
        )
        """
    )
    # Backfill: add category/detail column if missing in existing DB
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(info)")}
        if "category" not in cols:
            conn.execute("ALTER TABLE info ADD COLUMN category TEXT")
        if "detail" not in cols:
            conn.execute("ALTER TABLE info ADD COLUMN detail TEXT")
    except Exception:
        pass
    # New dedup rule for new DBs: unique by link only (no migration performed)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique
        ON info (link)
        """
    )
    conn.commit()


def _insert_entries(conn: sqlite3.Connection, entries: Iterable[Entry]) -> list[Entry]:
    cur = conn.cursor()
    newly_added: list[Entry] = []
    for e in entries:
        try:
            cur.execute(
                """
                INSERT INTO info (source, publish, title, link, category, detail)
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(link) DO NOTHING
                """,
                (e.source, e.publish, e.title, e.link, e.category),
            )
            if cur.rowcount:
                newly_added.append(e)
        except sqlite3.OperationalError:
            # For older SQLite lacking DO NOTHING, emulate via IGNORE
            cur.execute(
                "INSERT OR IGNORE INTO info (source, publish, title, link, category, detail) VALUES (?, ?, ?, ?, ?, NULL)",
                (e.source, e.publish, e.title, e.link, e.category),
            )
            if cur.rowcount:
                newly_added.append(e)
    conn.commit()
    return newly_added


def _update_detail(conn: sqlite3.Connection, link: str, detail: str) -> None:
    conn.execute("UPDATE info SET detail = ? WHERE link = ?", (detail, link))
    conn.commit()


def _backfill_missing_details(
    conn: sqlite3.Connection,
    mod,
    source_hint: Optional[str] = None,
    limit: int = 10,
) -> None:
    """Fetch and store details for recent rows that are missing it.

    - Restricts by source using the module's SOURCE constant when available.
    - Only runs when the module provides fetch_article_detail.
    - Limits the number of backfilled rows per module per run to avoid overload.
    """
    fetcher = getattr(mod, "fetch_article_detail", None)
    if not callable(fetcher):
        return
    src = source_hint or (getattr(mod, "SOURCE", "") or "").strip()
    if not src:
        return
    cur = conn.cursor()
    cur.execute(
        """
        SELECT link FROM info
        WHERE source = ? AND (detail IS NULL OR TRIM(detail) = '')
        ORDER BY id DESC
        LIMIT ?
        """,
        (src, int(limit)),
    )
    rows = cur.fetchall()
    for (link,) in rows:
        try:
            detail = (fetcher(link) or "").strip()
            if detail:
                _update_detail(conn, link, detail)
                try:
                    print(f"  明细回填成功: {link} - {len(detail)} 字符")
                except Exception:
                    print(f"  明细回填成功: {link}")
        except Exception as ex:
            print(f"  明细回填失败: {link} - {ex}")


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
        # Skip test modules if present under scraping/tests
        if path.name.startswith("test_") or "tests" in path.parts:
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
                    raw_publish = str(item.get("published") or item.get("publish") or "").strip()
                    e = _coerce_entry(item)
                    if e:
                        # Backfill source/category from module-level constants when missing
                        if not e.source and hasattr(mod, "SOURCE"):
                            e.source = str(getattr(mod, "SOURCE"))
                        if not e.category and hasattr(mod, "CATEGORY"):
                            e.category = str(getattr(mod, "CATEGORY"))
                        # Validate publish time format and print hint if suspicious
                        if raw_publish and not _is_iso8601_full(raw_publish):
                            print(
                                f"  [时间格式疑似异常] {path.name}({e.source}) -> '{raw_publish}'"
                            )
                        elif not raw_publish and not e.publish:
                            print(
                                f"  [时间缺失] {path.name}({e.source}) -> link={e.link}"
                            )
                        elif e.publish and not _is_iso8601_full(e.publish):
                            print(
                                f"  [时间非标准] {path.name}({e.source}) -> '{e.publish}'"
                                + (f" (原始:'{raw_publish}')" if raw_publish else "")
                            )
                        entries.append(e)
                newly_added = _insert_entries(conn, entries)
                total_new += len(newly_added)
                print(f"{path.name}: 解析 {len(items)} 条，新增 {len(newly_added)} 条")

                # For newly added links only, try to fetch and store details
                if newly_added:
                    fetcher = getattr(mod, "fetch_article_detail", None)
                    if callable(fetcher):
                        for e in newly_added:
                            try:
                                detail = fetcher(e.link)
                                # Normalize and keep it as plain text
                                detail = (detail or "").strip()
                                if detail:
                                    _update_detail(conn, e.link, detail)
                                    # Log success for visibility when detail is stored
                                    try:
                                        print(f"  明细抓取成功: {e.link} - {len(detail)} 字符")
                                    except Exception:
                                        # Length calculation/logging should not break flow
                                        print(f"  明细抓取成功: {e.link}")
                            except Exception as ex:
                                # Non-fatal: continue with others
                                print(f"  明细抓取失败: {e.link} - {ex}")
                    else:
                        # No site-specific fetcher provided; skip silently
                        pass

                # Backfill: for this source, attempt to fill missing details on recent rows
                try:
                    _backfill_missing_details(
                        conn,
                        mod,
                        source_hint=str(getattr(mod, "SOURCE", "")),
                        limit=5,
                    )
                except Exception:
                    # Non-fatal
                    pass
            except Exception as exc:
                print(f"{path.name}: 处理失败 - {exc}")

        print(f"完成，数据库: {DB_PATH}，新增总计 {total_new} 条")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
