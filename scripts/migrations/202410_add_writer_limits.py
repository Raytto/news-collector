from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import List, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DEFAULT_DB = DATA_DIR / "info.db"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add writer-level category/source limits to pipeline_writers table"
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"SQLite database path (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--limit-per-cat",
        type=int,
        default=10,
        help="Default limit per category for writers missing the value (default: 10)",
    )
    parser.add_argument(
        "--per-source-cap",
        type=int,
        default=3,
        help="Default per-source cap for writers missing the value (default: 3; <=0 means unlimited)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show intended SQL changes without mutating the database",
    )
    return parser.parse_args()


def ensure_table(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_writers'"
    ).fetchone()
    if not row:
        raise SystemExit("pipeline_writers table not found; run pipeline_admin init first")


def ensure_columns(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(pipeline_writers)")
    existing = {row[1] for row in cur.fetchall()}
    alterations: List[Tuple[str, str]] = []
    if "limit_per_category" not in existing:
        alterations.append(("limit_per_category", "INTEGER"))
    if "per_source_cap" not in existing:
        alterations.append(("per_source_cap", "INTEGER"))

    for column, col_type in alterations:
        conn.execute(f"ALTER TABLE pipeline_writers ADD COLUMN {column} {col_type}")


def apply_defaults(
    conn: sqlite3.Connection,
    limit_per_cat: int,
    per_source_cap: int,
) -> Tuple[int, int]:
    types = ("feishu_md", "info_html")
    placeholders = ",".join("?" for _ in types)

    limit_updated = conn.execute(
        f"""
        UPDATE pipeline_writers
        SET limit_per_category = ?
        WHERE limit_per_category IS NULL
          AND type IN ({placeholders})
        """,
        (limit_per_cat, *types),
    ).rowcount

    cap_updated = conn.execute(
        f"""
        UPDATE pipeline_writers
        SET per_source_cap = ?
        WHERE per_source_cap IS NULL
          AND type IN ({placeholders})
        """,
        (per_source_cap, *types),
    ).rowcount

    return limit_updated, cap_updated


def main() -> None:
    args = parse_args()
    db_path = args.db
    if not db_path.exists():
        raise SystemExit(f"database not found: {db_path}")

    if args.dry_run:
        print(f"[DRY-RUN] would open DB at {db_path}")
        print("[DRY-RUN] would add missing pipeline_writers.limit_per_category / per_source_cap columns")
        print(
            f"[DRY-RUN] would backfill limit_per_category={args.limit_per_cat}, "
            f"per_source_cap={args.per_source_cap} for feishu_md/info_html writers with NULL values"
        )
        return

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_table(conn)
        ensure_columns(conn)
        limit_updates, cap_updates = apply_defaults(conn, args.limit_per_cat, args.per_source_cap)
        conn.commit()

    print(f"Updated pipeline_writers in {db_path}")
    print(f"- limit_per_category backfilled rows: {limit_updates}")
    print(f"- per_source_cap backfilled rows: {cap_updates}")


if __name__ == "__main__":
    main()
