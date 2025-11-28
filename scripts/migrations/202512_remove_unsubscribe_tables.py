from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "info.db"


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return bool(row)


def drop_tables(conn: sqlite3.Connection, names: list[str]) -> list[str]:
    dropped: list[str] = []
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for tbl in names:
            if not table_exists(conn, tbl):
                continue
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
            dropped.append(tbl)
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return dropped


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"DB not found: {DB_PATH}")
    with sqlite3.connect(str(DB_PATH)) as conn:
        removed = drop_tables(conn, ["pipeline_unsubscribed", "unsubscribed_emails"])
        conn.commit()
    print(f"Removed tables: {', '.join(removed) if removed else 'none'}")


if __name__ == "__main__":
    main()
