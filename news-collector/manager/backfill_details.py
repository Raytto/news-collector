from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
SCRAPING_DIR = ROOT / "scraping"
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"


Fetcher = Callable[[str], str]


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def _scan_sources() -> Dict[str, Path]:
    """Text-scan scraper files to map SOURCE->module path without importing.

    This avoids import-time dependency errors in environments lacking optional libs.
    """
    mapping: Dict[str, Path] = {}
    for path in sorted(SCRAPING_DIR.rglob("*.py")):
        if path.name.startswith("__"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if "fetch_article_detail" not in text:
            continue
        m = None
        # naive SOURCE = "..." extractor
        import re as _re
        m = _re.search(r"^SOURCE\s*=\s*['\"]([^'\"]+)['\"]", text, flags=_re.MULTILINE)
        if m:
            src = m.group(1).strip()
            if src:
                mapping[src] = path
    return mapping


def discover_fetchers() -> Dict[str, Fetcher]:
    mapping: Dict[str, Fetcher] = {}
    by_source = _scan_sources()
    for src, path in by_source.items():
        try:
            mod = _load_module(path)
        except Exception:
            continue
        fetcher = getattr(mod, "fetch_article_detail", None)
        if callable(fetcher):
            mapping[src] = fetcher
    return mapping


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill missing article details into SQLite DB")
    p.add_argument("--db", default=str(DB_PATH), help="Path to SQLite DB (default: data/info.db)")
    p.add_argument("--source", default="", help="Only backfill this source (optional)")
    p.add_argument("--limit", type=int, default=200, help="Max rows to backfill (default: 200)")
    p.add_argument("--overwrite", action="store_true", help="Force refetch even if detail exists")
    p.add_argument("--contains", default="", help="Only overwrite rows whose detail contains this substring")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    fetchers = discover_fetchers()
    if not fetchers:
        print("未发现任何可用的 fetch_article_detail 函数（可能是依赖未安装，但将尝试按需导入）")
        # Fallback to on-demand import using textual SOURCE mapping
        source_to_path = _scan_sources()
    else:
        print(f"发现可用来源: {', '.join(sorted(fetchers))}")
        source_to_path = {}

    conn = sqlite3.connect(str(db_path))
    try:
        cur = conn.cursor()
        params: list = []
        if args.overwrite:
            if args.contains:
                where = "(detail LIKE '%' || ? || '%')"
                params.append(args.contains)
            else:
                where = "1=1"
        else:
            where = "(detail IS NULL OR TRIM(detail)='')"
        if args.source:
            where += " AND source = ?"
            params.append(args.source)
        sql = f"SELECT id, source, link FROM info WHERE {where} ORDER BY id DESC LIMIT ?"
        params.append(args.limit)
        rows = list(cur.execute(sql, params))
        print(f"待回填: {len(rows)} 条")

        updated = 0
        for _id, source, link in rows:
            fetcher = fetchers.get(source)
            if not fetcher:
                # Try on-demand import for this source
                p = source_to_path.get(source)
                if p is not None:
                    try:
                        mod = _load_module(p)
                        f = getattr(mod, "fetch_article_detail", None)
                        if callable(f):
                            fetcher = f
                            fetchers[source] = f
                    except Exception:
                        pass
            if not fetcher:
                continue
            try:
                detail = (fetcher(link) or "").strip()
            except Exception as exc:
                print(f"  回填失败: {source} - {link} - {exc}")
                continue
            if not detail:
                continue
            cur.execute("UPDATE info SET detail=? WHERE id=?", (detail, _id))
            updated += 1
            if updated % 10 == 0:
                conn.commit()
        conn.commit()
        print(f"完成: 成功回填 {updated} 条")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
