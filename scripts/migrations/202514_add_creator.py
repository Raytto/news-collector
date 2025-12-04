import sqlite3
from pathlib import Path


def add_creator(conn: sqlite3.Connection) -> bool:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(info)")
    cols = {row[1] for row in cur.fetchall()}
    if "creator" in cols:
        return False
    cur.execute("ALTER TABLE info ADD COLUMN creator TEXT")
    return True


def main(db_path: str = "data/info.db") -> None:
    path = Path(db_path)
    if not path.exists():
        raise SystemExit(f"数据库不存在: {path}")
    with sqlite3.connect(str(path)) as conn:
        added = add_creator(conn)
    print("已向 info 添加 creator 列" if added else "creator 已存在，无需迁移")


if __name__ == "__main__":
    main()
