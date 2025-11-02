from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "info.db"


def dedupe_table(conn: sqlite3.Connection, table: str, key_col: str) -> int:
    cur = conn.cursor()
    dup_rows = cur.execute(
        f"SELECT {key_col}, COUNT(*) FROM {table} GROUP BY {key_col} HAVING COUNT(*) > 1"
    ).fetchall()
    removed = 0
    for key, _ in dup_rows:
        # keep the latest row (max rowid)
        rowid = cur.execute(
            f"SELECT MAX(rowid) FROM {table} WHERE {key_col}=?",
            (key,),
        ).fetchone()[0]
        # delete all except the kept row
        removed += cur.execute(
            f"DELETE FROM {table} WHERE {key_col}=? AND rowid<>?",
            (key, rowid),
        ).rowcount
    return removed


def ensure_unique_indexes(conn: sqlite3.Connection) -> None:
    # After dedupe, enforce uniqueness via unique indexes
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_writers_pipeline_id ON pipeline_writers(pipeline_id)"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_filters_pipeline_id ON pipeline_filters(pipeline_id)"
    )


def run(db_path: Path) -> None:
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    with sqlite3.connect(str(db_path)) as conn:
        conn.isolation_level = None  # autocommit off for explicit transactions
        conn.execute("BEGIN")
        try:
            removed_w = dedupe_table(conn, "pipeline_writers", "pipeline_id")
            removed_f = dedupe_table(conn, "pipeline_filters", "pipeline_id")
            ensure_unique_indexes(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    print(
        f"Deduped and enforced uniques on {db_path}. Removed duplicates: "
        f"writers={removed_w}, filters={removed_f}"
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fix duplicate pipeline writer/filter rows and enforce uniqueness")
    p.add_argument("--db", default=str(DB_PATH), help="SQLite DB path (default: data/info.db)")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    run(Path(args.db))


if __name__ == "__main__":
    main()

