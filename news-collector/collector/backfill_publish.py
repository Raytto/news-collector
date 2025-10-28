from __future__ import annotations

import argparse
import importlib.util
import sqlite3
from pathlib import Path
from typing import Dict


COLLECTOR_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = COLLECTOR_DIR.parent
DATA_DIR = PROJECT_ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"


def _load_module(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="从 scraper 解析列表页，回填 DB 中缺失的 publish 时间")
    p.add_argument("--db", default=str(DB_PATH), help="SQLite 数据库路径，默认 data/info.db")
    p.add_argument("--source", required=True, help="来源标识（例如 deepmind）")
    p.add_argument("--scraper", required=True, help="scraper 脚本相对路径（例如 news-collector/collector/scraping/tech/deepmind.google.blog.py）")
    p.add_argument("--limit", type=int, default=500, help="最多检查的缺失条数，默认 500")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    scraper_path = Path(args.scraper)
    if not scraper_path.exists():
        raise SystemExit(f"scraper 文件不存在: {scraper_path}")

    mod = _load_module(scraper_path)
    # 解析列表页，构建 url->published 映射
    if hasattr(mod, "fetch_list_page") and hasattr(mod, "parse_list"):
        html = mod.fetch_list_page()  # type: ignore[attr-defined]
        items = mod.parse_list(html)  # type: ignore[attr-defined]
    elif hasattr(mod, "collect_latest"):
        items = mod.collect_latest()  # type: ignore[attr-defined]
    else:
        raise SystemExit("该 scraper 未提供 fetch_list_page/parse_list 或 collect_latest")

    mapping: Dict[str, str] = {}
    for it in items:
        u = str(it.get("url") or "").strip()
        p = str(it.get("published") or "").strip()
        if u and p:
            mapping[u] = p

    if not mapping:
        raise SystemExit("未从 scraper 解析到任何 (url,published) 对")

    updated = 0
    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT id, link FROM info WHERE source=? AND (publish IS NULL OR TRIM(publish)='') ORDER BY id DESC LIMIT ?",
            (args.source, int(args.limit)),
        ).fetchall()
        for _id, link in rows:
            pub = mapping.get(link)
            if not pub:
                continue
            cur.execute("UPDATE info SET publish=? WHERE id=?", (pub, int(_id)))
            updated += 1
        conn.commit()

    print(f"回填完成: 发现 {len(mapping)} 条列表项，更新 {updated} 条 DB 记录 (source={args.source})")


if __name__ == "__main__":
    main()
