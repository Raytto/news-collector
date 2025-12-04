from __future__ import annotations

import os
import argparse
import sqlite3
import sys
from dataclasses import dataclass
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
import importlib.util
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time
import random
from collections import defaultdict
from urllib.parse import urlparse
import multiprocessing


COLLECTOR_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = COLLECTOR_DIR.parent
SCRAPING_DIR = COLLECTOR_DIR / "scraping"
DATA_DIR = PROJECT_ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ------------------------
# Concurrency configuration
# ------------------------

def _get_int(name: str, default: int) -> int:
    try:
        v = os.getenv(name, str(default)).strip()
        return int(v)
    except Exception:
        return default


def _get_float(name: str, default: float) -> float:
    try:
        v = os.getenv(name, str(default)).strip()
        return float(v)
    except Exception:
        return default


SOURCE_CONCURRENCY = _get_int("COLLECTOR_SOURCE_CONCURRENCY", 10)
PER_SOURCE_DETAIL_CONCURRENCY = _get_int("COLLECTOR_PER_SOURCE_CONCURRENCY", 1)
GLOBAL_HTTP_CONCURRENCY = _get_int("COLLECTOR_GLOBAL_HTTP_CONCURRENCY", 16)
PER_HOST_MIN_INTERVAL_MS = _get_int("COLLECTOR_PER_HOST_MIN_INTERVAL_MS", 500)
TIMEOUT_CONNECT = _get_float("COLLECTOR_TIMEOUT_CONNECT", 5.0)
TIMEOUT_READ = _get_float("COLLECTOR_TIMEOUT_READ", 10.0)
SOURCE_TIMEOUT_SEC = _get_float("COLLECTOR_SOURCE_TIMEOUT_SEC", 40.0)
RETRY_MAX = _get_int("COLLECTOR_RETRY_MAX", 3)
RETRY_BACKOFF_BASE = _get_float("COLLECTOR_RETRY_BACKOFF_BASE", 0.6)
DISABLE_CONCURRENCY = os.getenv("COLLECTOR_DISABLE_CONCURRENCY", "").strip().lower() in {"1", "true", "yes"}


# ------------------------
# Global HTTP throttling for requests
# ------------------------

_http_sem = threading.Semaphore(GLOBAL_HTTP_CONCURRENCY if GLOBAL_HTTP_CONCURRENCY > 0 else 1)
_host_last_access: Dict[str, float] = defaultdict(float)
_host_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
_http_patch_installed = False


def _respect_host_rate_limit(host: str) -> None:
    if not host:
        return
    lock = _host_locks[host]
    with lock:
        now = time.time()
        min_gap = PER_HOST_MIN_INTERVAL_MS / 1000.0
        last = _host_last_access.get(host, 0.0)
        wait = min_gap - (now - last)
        if wait > 0:
            # add small jitter ±10%
            jitter = random.uniform(-0.1, 0.1) * min_gap
            time.sleep(max(0.0, wait + jitter))
        _host_last_access[host] = time.time()


def _install_http_limits() -> None:
    """Patch requests' Session.request to enforce global concurrency, per-host pacing, and default timeouts.

    - Applies to all sessions created after import (and most existing ones as they call Session.request).
    - Keeps behavior minimal-risk: we do not alter returned responses except adding timeouts when missing.
    """
    global _http_patch_installed
    if _http_patch_installed:
        return
    try:
        import requests  # type: ignore
        from requests.sessions import Session as _Session  # type: ignore
    except Exception:
        return

    orig_request = _Session.request

    def wrapped_request(self, method, url, *args, **kwargs):  # type: ignore[no-redef]
        # default timeout if not provided
        if "timeout" not in kwargs or kwargs["timeout"] is None:
            kwargs["timeout"] = (TIMEOUT_CONNECT, TIMEOUT_READ)

        parsed = None
        try:
            parsed = urlparse(url or "")
        except Exception:
            parsed = None
        host = parsed.hostname if parsed else ""

        attempt = 0
        while True:
            _http_sem.acquire()
            try:
                _respect_host_rate_limit(host or "")
                resp = orig_request(self, method, url, *args, **kwargs)
                # Simple retry on transient statuses
                if resp is not None and resp.status_code in (429, 500, 502, 503, 504) and attempt < max(0, RETRY_MAX):
                    # honor Retry-After when present
                    ra = resp.headers.get("Retry-After")
                    sleep_s: float = 0.0
                    if ra:
                        try:
                            sleep_s = float(ra)
                        except Exception:
                            sleep_s = 0.0
                    if sleep_s <= 0:
                        sleep_s = (RETRY_BACKOFF_BASE or 0.5) * (2 ** attempt)
                    time.sleep(min(sleep_s, 30.0))
                    attempt += 1
                    continue
                return resp
            except Exception:
                # Simple retry for network errors
                if attempt < max(0, RETRY_MAX):
                    sleep_s = (RETRY_BACKOFF_BASE or 0.5) * (2 ** attempt)
                    time.sleep(min(sleep_s, 30.0))
                    attempt += 1
                    continue
                raise
            finally:
                try:
                    _http_sem.release()
                except Exception:
                    pass

    # Install patch
    try:
        _Session.request = wrapped_request  # type: ignore[assignment]
        _http_patch_installed = True
    except Exception:
        _http_patch_installed = False


@dataclass
class Entry:
    source: str
    publish: str
    title: str
    link: str
    store_link: str = ""
    creator: str = ""
    category: str = ""
    img_link: str = ""
    detail: str = ""


@dataclass
class SourceSpec:
    id: int
    source: str
    category: str
    path: Path
    allow_parallel: bool = True


DEFAULT_CATEGORY_LABELS = {
    "game": "游戏",
    "tech": "科技",
    "humanities": "人文",
    "general": "综合",
}

SOURCE_LABEL_FIELDS = ("SOURCE_LABEL_ZH", "SOURCE_LABEL", "SOURCE_NAME")


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    mod = importlib.util.module_from_spec(spec)
    # Ensure dataclass and other decorators can resolve module via sys.modules during exec
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _extract_metadata(path: Path, keys: tuple[str, ...]) -> Dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = path.read_text(encoding="utf-8", errors="ignore")
    metadata: Dict[str, str] = {}
    for key in keys:
        pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(['\"])(.*?)\1", re.MULTILINE)
        match = pattern.search(text)
        if match:
            metadata[key] = match.group(2).strip()
    return metadata


def _seed_sources_from_fs(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    row = cur.execute("SELECT COUNT(*) FROM sources").fetchone()
    if row and int(row[0] or 0):
        return

    print("sources 表为空，首次运行将自动注册 scraping 目录下的脚本")
    inserted_categories = 0
    inserted_sources = 0
    keys = ("SOURCE", "CATEGORY") + SOURCE_LABEL_FIELDS

    for path in sorted(SCRAPING_DIR.rglob("*.py")):
        if path.name.startswith("__"):
            continue
        if path.name == "_datetime.py":
            continue
        if path.name.startswith("test_") or "tests" in path.parts:
            continue

        metadata = _extract_metadata(path, keys)
        source_key = (metadata.get("SOURCE") or "").strip()
        if not source_key:
            try:
                rel_hint = path.relative_to(SCRAPING_DIR)
            except ValueError:
                rel_hint = path
            print(f"{rel_hint}: 未找到 SOURCE 常量，跳过注册")
            continue

        category_key = (metadata.get("CATEGORY") or "").strip()
        if not category_key:
            try:
                rel_parts = path.relative_to(SCRAPING_DIR).parts
            except ValueError:
                rel_parts = ()
            if rel_parts:
                category_key = rel_parts[0]
        category_key = str(category_key or "").strip()
        if not category_key:
            print(f"{path.name}: 无法推断分类，跳过注册")
            continue

        label = source_key
        for field in SOURCE_LABEL_FIELDS:
            val = (metadata.get(field) or "").strip()
            if val:
                label = val
                break

        category_label = DEFAULT_CATEGORY_LABELS.get(category_key, category_key)
        cur.execute(
            "INSERT OR IGNORE INTO categories (key, label_zh, enabled) VALUES (?, ?, 1)",
            (category_key, category_label),
        )
        if cur.rowcount:
            inserted_categories += 1

        try:
            script_rel = path.relative_to(PROJECT_ROOT.parent).as_posix()
        except ValueError:
            script_rel = path.as_posix()

        cur.execute(
            """
            INSERT OR IGNORE INTO sources (key, label_zh, enabled, category_key, script_path)
            VALUES (?, ?, 1, ?, ?)
            """,
            (source_key, label, category_key, script_rel),
        )
        if cur.rowcount:
            inserted_sources += 1

    conn.commit()
    if inserted_categories or inserted_sources:
        details: list[str] = []
        if inserted_sources:
            details.append(f"{inserted_sources} 个信息源")
        if inserted_categories:
            details.append(f"{inserted_categories} 个分类")
        print("已自动注册 " + "、".join(details) + "。")



def _get_module_feed_urls(mod) -> List[str]:
    urls: List[str] = []
    for attr in ("RSS_URL", "FEED_URL", "URL"):
        val = getattr(mod, attr, None)
        if not val:
            continue
        try:
            if isinstance(val, (list, tuple)):
                urls.extend([str(x) for x in val if x])
            else:
                urls.append(str(val))
        except Exception:
            continue
    # Deduplicate while preserving order
    seen: set[str] = set()
    uniq: List[str] = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq


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
        urls = _get_module_feed_urls(mod)
        try:
            # Prefer module defaults when available
            try:
                feed = fetch()
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
        except Exception as ex:
            ctx = f"feed_urls={urls!r}" if urls else "feed_url=unknown"
            raise RuntimeError(f"解析 RSS 时出错({ctx}): {ex}")

    raise RuntimeError(f"未找到可用于采集的入口函数: {mod.__name__}")


def _coerce_entry(item: Dict[str, Any]) -> Optional[Entry]:
    title = str(item.get("title") or "").strip()
    link = str(item.get("url") or item.get("link") or "").strip()
    publish = str(item.get("published") or item.get("publish") or "").strip()
    source = str(item.get("source") or "").strip()
    category = str(item.get("category") or "").strip()
    img_link = str(item.get("img") or item.get("image") or item.get("thumbnail") or "").strip()
    store_link = str(
        item.get("store_link")
        or item.get("store_url")
        or item.get("store")
        or ""
    ).strip()
    creator = str(item.get("creator") or "").strip()
    detail = str(item.get("detail") or "").strip()
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
    return Entry(
        source=source,
        publish=publish,
        title=title,
        link=link,
        store_link=store_link,
        creator=creator,
        category=category,
        img_link=img_link,
        detail=detail,
    )


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
            store_link TEXT,
            creator TEXT,
            category TEXT,
            detail TEXT,
            img_link TEXT
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
        if "img_link" not in cols:
            conn.execute("ALTER TABLE info ADD COLUMN img_link TEXT")
        if "store_link" not in cols:
            conn.execute("ALTER TABLE info ADD COLUMN store_link TEXT")
        if "creator" not in cols:
            conn.execute("ALTER TABLE info ADD COLUMN creator TEXT")
    except Exception:
        pass
    # New dedup rule for new DBs: unique by link only (no migration performed)
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique
        ON info (link)
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS categories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            key        TEXT NOT NULL UNIQUE,
            label_zh   TEXT NOT NULL,
            enabled    INTEGER NOT NULL DEFAULT 1,
            allow_parallel INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    try:
        cat_cols = {row[1] for row in conn.execute("PRAGMA table_info(categories)")}
        if "allow_parallel" not in cat_cols:
            conn.execute("ALTER TABLE categories ADD COLUMN allow_parallel INTEGER NOT NULL DEFAULT 1")
    except Exception:
        pass
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            key          TEXT NOT NULL UNIQUE,
            label_zh     TEXT NOT NULL,
            enabled      INTEGER NOT NULL DEFAULT 1,
            category_key TEXT NOT NULL,
            script_path  TEXT NOT NULL,
            created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_key) REFERENCES categories(key)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sources_enabled
        ON sources (enabled)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_sources_category
        ON sources (category_key, enabled)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS source_runs (
            source_id   INTEGER PRIMARY KEY,
            last_run_at TEXT NOT NULL,
            FOREIGN KEY (source_id) REFERENCES sources(id)
        )
        """
    )
    conn.commit()


def _insert_entries(conn: sqlite3.Connection, entries: Iterable[Entry]) -> list[Entry]:
    cur = conn.cursor()
    existing_title_creator: set[tuple[str, str]] = set()
    try:
        rows = cur.execute(
            "SELECT lower(trim(title)), lower(trim(creator)) FROM info WHERE creator IS NOT NULL AND trim(creator) != ''"
        ).fetchall()
        for t, c in rows:
            if t and c:
                existing_title_creator.add((str(t), str(c)))
    except sqlite3.OperationalError:
        existing_title_creator = set()
    newly_added: list[Entry] = []
    for e in entries:
        normalized_tc: Optional[tuple[str, str]] = None
        if e.creator and e.title:
            normalized_tc = (e.title.strip().lower(), e.creator.strip().lower())
            if normalized_tc in existing_title_creator:
                continue
        try:
            cur.execute(
                """
                INSERT INTO info (source, publish, title, link, store_link, creator, category, detail, img_link)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(link) DO NOTHING
                """,
                (
                    e.source,
                    e.publish,
                    e.title,
                    e.link,
                    e.store_link or None,
                    e.creator or None,
                    e.category,
                    e.detail or None,
                    e.img_link or None,
                ),
            )
            if cur.rowcount:
                if normalized_tc:
                    existing_title_creator.add(normalized_tc)
                newly_added.append(e)
        except sqlite3.OperationalError:
            # For older SQLite lacking DO NOTHING, emulate via IGNORE
            cur.execute(
                "INSERT OR IGNORE INTO info (source, publish, title, link, store_link, creator, category, detail, img_link) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    e.source,
                    e.publish,
                    e.title,
                    e.link,
                    e.store_link or None,
                    e.creator or None,
                    e.category,
                    e.detail or None,
                    e.img_link or None,
                ),
            )
            if cur.rowcount:
                if normalized_tc:
                    existing_title_creator.add(normalized_tc)
                newly_added.append(e)
    conn.commit()
    return newly_added


def _resolve_script_path(script_path: str) -> Path:
    path = Path(script_path)
    if not path.is_absolute():
        base = PROJECT_ROOT.parent
        path = (base / script_path).resolve()
    return path


def _load_sources_from_db(conn: sqlite3.Connection, allow_keys: Optional[set[str]] = None) -> list[SourceSpec]:
    cursor = conn.cursor()
    fallback_allow_parallel = False
    try:
        rows = cursor.execute(
            """
            SELECT s.id, s.key, s.category_key, s.script_path, COALESCE(c.allow_parallel, 1) AS allow_parallel
            FROM sources AS s
            LEFT JOIN categories AS c ON c.key = s.category_key
            WHERE s.enabled = 1
            ORDER BY s.id
            """
        ).fetchall()
    except sqlite3.OperationalError:
        rows = cursor.execute(
            """
            SELECT id, key, category_key, script_path
            FROM sources
            WHERE enabled = 1
            ORDER BY id
            """
        ).fetchall()
        fallback_allow_parallel = True

    specs: list[SourceSpec] = []
    for row in rows:
        if fallback_allow_parallel:
            sid, key, category_key, script_path = row
            allow_parallel = 1
        else:
            sid, key, category_key, script_path, allow_parallel = row
        key_str = str(key or "").strip()
        script_path_str = str(script_path or "").strip()
        if not (key_str and script_path_str):
            continue
        if allow_keys is not None and key_str not in allow_keys:
            continue
        try:
            resolved = _resolve_script_path(script_path_str)
        except Exception:
            continue
        try:
            allow_parallel_flag = 1 if int(allow_parallel or 0) else 0
        except Exception:
            allow_parallel_flag = 1
        specs.append(
            SourceSpec(
                id=int(sid),
                source=key_str,
                category=str(category_key or "").strip(),
                path=resolved,
                allow_parallel=bool(allow_parallel_flag),
            )
        )
    return specs


def _update_detail(conn: sqlite3.Connection, link: str, detail: str) -> None:
    conn.execute("UPDATE info SET detail = ? WHERE link = ?", (detail, link))
    conn.commit()


def _update_source_run(conn: sqlite3.Connection, source_id: int) -> None:
    now_iso = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    conn.execute(
        """
        INSERT INTO source_runs (source_id, last_run_at)
        VALUES (?, ?)
        ON CONFLICT(source_id) DO UPDATE SET last_run_at=excluded.last_run_at
        """,
        (source_id, now_iso),
    )
    conn.commit()


def _backfill_missing_details(
    conn: sqlite3.Connection,
    mod,
    source_hint: Optional[str] = None,
    limit: int = 30,
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


def _process_source_spec(spec: SourceSpec) -> Tuple[str, int]:
    """Process a single source spec and return (source_key, newly_added_count).

    Uses its own SQLite connection to avoid cross-thread usage.
    """
    path = spec.path
    # Open a dedicated connection per worker
    conn = sqlite3.connect(str(DB_PATH))
    try:
        if not path.exists():
            print(f"{path}: 脚本不存在，跳过 (source={spec.source or 'unknown'})")
            return spec.source or "", 0
        try:
            mod = _load_module(path)
        except Exception as exc:
            print(f"{path.name}: 加载模块失败 - {exc}")
            return spec.source or "", 0
        source_key = spec.source or str(getattr(mod, "SOURCE", "") or "").strip()
        category_key = spec.category or str(getattr(mod, "CATEGORY", "") or "").strip()
        if not source_key:
            print(f"{path.name}: 缺少来源标识 (SOURCE)，已跳过执行")
            return "", 0
        try:
            items = _to_entry_dicts(mod)
            entries: List[Entry] = []
            for item in items:
                raw_publish = str(item.get("published") or item.get("publish") or "").strip()
                try:
                    e = _coerce_entry(item)
                except Exception as ex_item:
                    try:
                        link_hint = str(item.get("url") or item.get("link") or "")
                    except Exception:
                        link_hint = ""
                    src_hint = source_key
                    print(
                        f"  [条目解析失败] {path.name}({src_hint}) -> link={link_hint or '(unknown)'} - {ex_item}"
                    )
                    continue
                if e:
                    # Standardize source/category from DB when available
                    e.source = source_key
                    if category_key:
                        e.category = category_key
                    elif not e.category and hasattr(mod, "CATEGORY"):
                        e.category = str(getattr(mod, "CATEGORY"))
                    # Validate publish time format and print hint if suspicious
                    if raw_publish and not _is_iso8601_full(raw_publish):
                        print(
                            f"  [时间格式疑似异常] {path.name}({source_key}) -> '{raw_publish}'"
                        )
                    elif not raw_publish and not e.publish:
                        print(
                            f"  [时间缺失] {path.name}({source_key}) -> link={e.link}"
                        )
                    elif e.publish and not _is_iso8601_full(e.publish):
                        print(
                            f"  [时间非标准] {path.name}({source_key}) -> '{e.publish}'"
                            + (f" (原始:'{raw_publish}')" if raw_publish else "")
                        )
                    entries.append(e)
            newly_added = _insert_entries(conn, entries)
            print(f"{path.name}({source_key}): 解析 {len(items)} 条，新增 {len(newly_added)} 条")

            # For newly added links only, try to fetch and store details (default serial)
            if newly_added:
                fetcher = getattr(mod, "fetch_article_detail", None)
                if callable(fetcher):
                    if PER_SOURCE_DETAIL_CONCURRENCY <= 1:
                        for e in newly_added:
                            try:
                                detail = (fetcher(e.link) or "").strip()
                                if detail:
                                    _update_detail(conn, e.link, detail)
                                    try:
                                        print(f"  明细抓取成功: {e.link} - {len(detail)} 字符")
                                    except Exception:
                                        print(f"  明细抓取成功: {e.link}")
                            except Exception as ex:
                                print(f"  明细抓取失败: {e.link} - {ex}")
                    else:
                        # Optional small concurrency within source
                        with ThreadPoolExecutor(max_workers=PER_SOURCE_DETAIL_CONCURRENCY) as pool:
                            futures = {pool.submit(fetcher, e.link): e for e in newly_added}
                            for fut in as_completed(futures):
                                e = futures[fut]
                                try:
                                    detail = (fut.result() or "").strip()
                                    if detail:
                                        _update_detail(conn, e.link, detail)
                                        try:
                                            print(f"  明细抓取成功: {e.link} - {len(detail)} 字符")
                                        except Exception:
                                            print(f"  明细抓取成功: {e.link}")
                                except Exception as ex:
                                    print(f"  明细抓取失败: {e.link} - {ex}")
                else:
                    # No site-specific fetcher provided; skip silently
                    pass

            # Backfill: for this source, attempt to fill missing details on recent rows
            try:
                _backfill_missing_details(
                    conn,
                    mod,
                    source_hint=source_key,
                    limit=5,
                )
            except Exception:
                # Non-fatal
                pass
            try:
                if spec.id:
                    _update_source_run(conn, spec.id)
            except Exception as exc:
                print(f"[WARN] 更新 source_runs 失败 ({source_key}): {exc}")
        except Exception as exc:
            urls = _get_module_feed_urls(mod) if mod else []
            extra = f" (feed_urls={urls!r})" if urls else ""
            print(f"{path.name}({source_key}): 处理失败 - {exc}{extra}")
            return source_key or "", 0
        return source_key or "", len(newly_added)
    finally:
        conn.close()


def _process_source_spec_worker(spec: SourceSpec, queue: "multiprocessing.Queue[Any]") -> None:
    """Worker wrapper so we can retrieve results across process boundaries."""
    try:
        queue.put(("ok", _process_source_spec(spec)))
    except Exception as exc:  # pragma: no cover - defensive
        queue.put(("err", exc))


def _run_source_with_timeout(spec: SourceSpec, timeout: float) -> Tuple[str, int]:
    """Execute one source with a wall-clock timeout."""
    if timeout is None or timeout <= 0:
        return _process_source_spec(spec)

    result_queue: "multiprocessing.Queue[Any]" = multiprocessing.Queue(maxsize=1)
    proc = multiprocessing.Process(
        target=_process_source_spec_worker,
        args=(spec, result_queue),
    )
    proc.start()
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join()
        source_hint = spec.source or spec.path.stem
        print(f"{spec.path.name}({source_hint}): 超时 {int(timeout)}s，已跳过")
        result_queue.close()
        result_queue.join_thread()
        return source_hint, 0

    try:
        status, payload = result_queue.get_nowait()
    except Exception:
        result_queue.close()
        result_queue.join_thread()
        return spec.source or "", 0

    result_queue.close()
    result_queue.join_thread()

    if status == "ok":
        return payload
    print(f"{spec.path.name}({spec.source or 'unknown'}): 执行异常 - {payload}")
    return spec.source or "", 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect sources into SQLite")
    parser.add_argument(
        "--sources",
        help="逗号分隔的来源 key 列表（仅运行这些来源）",
        default="",
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="重复传入以限定来源 key（与 --sources 合并）",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    allow_sources: set[str] = set()
    if args.sources:
        allow_sources.update({s.strip() for s in str(args.sources).split(",") if s.strip()})
    if args.source:
        allow_sources.update({s.strip() for s in args.source if s.strip()})
    if not allow_sources:
        allow_sources = set()

    print(f"收集目录: {SCRAPING_DIR}")
    if not SCRAPING_DIR.exists():
        print("未找到 scraping 目录")
        sys.exit(1)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    try:
        _ensure_db(conn)
        _seed_sources_from_fs(conn)
        source_specs = _load_sources_from_db(conn, allow_sources or None)
        if source_specs:
            print(f"从 sources 表加载 {len(source_specs)} 个启用来源")
        else:
            print("sources 表暂无启用来源，采集已跳过。请在数据库中启用至少一个来源。")
            return
        # Install HTTP concurrency/rate limits before executing scrapers
        _install_http_limits()

        total_new = 0
        parallel_specs = [s for s in source_specs if s.allow_parallel]
        serial_specs = [s for s in source_specs if not s.allow_parallel]

        def _run_serial(specs: list[SourceSpec]) -> None:
            nonlocal total_new
            for spec in specs:
                _, added = _run_source_with_timeout(spec, SOURCE_TIMEOUT_SEC)
                total_new += added

        if DISABLE_CONCURRENCY or SOURCE_CONCURRENCY <= 1:
            _run_serial(source_specs)
        else:
            if parallel_specs:
                with ThreadPoolExecutor(max_workers=SOURCE_CONCURRENCY) as pool:
                    future_map = {
                        pool.submit(_run_source_with_timeout, spec, SOURCE_TIMEOUT_SEC): spec
                        for spec in parallel_specs
                    }
                    for fut in as_completed(future_map):
                        try:
                            _src, added = fut.result()
                            total_new += added
                        except Exception as exc:
                            spec = future_map[fut]
                            print(f"{spec.path.name}({spec.source or 'unknown'}): 并发执行失败 - {exc}")
            if serial_specs:
                print(f"检测到 {len(serial_specs)} 个串行类别来源，按顺序执行...")
                _run_serial(serial_specs)

        print(f"完成，数据库: {DB_PATH}，新增总计 {total_new} 条")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
