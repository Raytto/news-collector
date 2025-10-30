from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "info.db"


def ensure_base_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS info_ai_review (
            info_id     INTEGER PRIMARY KEY,
            final_score REAL    NOT NULL DEFAULT 0.0,
            ai_comment  TEXT    NOT NULL,
            ai_summary  TEXT    NOT NULL,
            raw_response TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (info_id) REFERENCES info(id)
        )
        """
    )


def add_missing_columns(conn: sqlite3.Connection) -> bool:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(info_ai_review)").fetchall()}
    changed = False
    if "ai_key_concepts" not in columns:
        conn.execute("ALTER TABLE info_ai_review ADD COLUMN ai_key_concepts TEXT")
        changed = True
    if "ai_summary_long" not in columns:
        conn.execute("ALTER TABLE info_ai_review ADD COLUMN ai_summary_long TEXT")
        changed = True
    if changed:
        conn.commit()
    return changed


def backfill_summary_long(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE info_ai_review
        SET ai_summary_long = ai_summary
        WHERE ai_summary_long IS NULL OR TRIM(ai_summary_long) = ''
        """
    )
    conn.commit()


def run(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        ensure_base_table(conn)
        changed = add_missing_columns(conn)
        if changed:
            backfill_summary_long(conn)
    print(f"[done] ai review text expansion applied to {db_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add extended AI review text fields")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite DB path (default: data/info.db)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")
    run(db_path)


if __name__ == "__main__":
    main()
