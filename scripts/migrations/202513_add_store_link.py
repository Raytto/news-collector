from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "info.db"


def add_store_link(conn: sqlite3.Connection) -> bool:
    rows = conn.execute("PRAGMA table_info(info)").fetchall()
    if not rows:
        raise SystemExit("info 表不存在，无法添加 store_link 列")
    cols = {row[1] for row in rows}
    if "store_link" in cols:
        return False
    conn.execute("ALTER TABLE info ADD COLUMN store_link TEXT")
    return True


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    with sqlite3.connect(str(DB_PATH)) as conn:
        added = add_store_link(conn)
        conn.commit()
    print("已向 info 添加 store_link 列" if added else "store_link 已存在，无需迁移")


if __name__ == "__main__":
    main()
