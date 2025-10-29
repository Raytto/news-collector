from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Dict, List, Optional, Tuple


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
        alterations.append(("limit_per_category", "TEXT"))
    if "per_source_cap" not in existing:
        alterations.append(("per_source_cap", "INTEGER"))

    for column, col_type in alterations:
        conn.execute(f"ALTER TABLE pipeline_writers ADD COLUMN {column} {col_type}")


def normalize_limit_value(raw: object) -> Optional[Dict[str, int]]:
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, (int, float)):
        return {"default": int(raw)}
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            try:
                return {"default": int(s)}
            except (TypeError, ValueError):
                return None
        else:
            raw = parsed
    if isinstance(raw, dict):
        out: Dict[str, int] = {}
        for key, val in raw.items():
            if key is None:
                continue
            key_str = str(key).strip()
            if not key_str:
                continue
            try:
                int_val = int(val)
            except (TypeError, ValueError):
                continue
            out[key_str] = int_val
        return out
    return None


def normalize_existing_limits(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT pipeline_id, limit_per_category FROM pipeline_writers"
    ).fetchall()
    updated = 0
    for pid, raw in rows:
        normalized = normalize_limit_value(raw)
        if normalized is None:
            if raw is None:
                continue
            conn.execute(
                "UPDATE pipeline_writers SET limit_per_category=NULL WHERE pipeline_id=?",
                (pid,),
            )
            updated += 1
            continue
        json_text = json.dumps(normalized, ensure_ascii=False)
        if str(raw) != json_text:
            conn.execute(
                "UPDATE pipeline_writers SET limit_per_category=? WHERE pipeline_id=?",
                (json_text, pid),
            )
            updated += 1
    return updated


def apply_defaults(
    conn: sqlite3.Connection,
    limit_per_cat: int,
    per_source_cap: int,
) -> Tuple[int, int]:
    types = ("feishu_md", "info_html")
    placeholders = ",".join("?" for _ in types)

    limit_json = json.dumps({"default": limit_per_cat}, ensure_ascii=False)

    limit_updated = conn.execute(
        f"""
        UPDATE pipeline_writers
        SET limit_per_category = ?
        WHERE limit_per_category IS NULL
          AND type IN ({placeholders})
        """,
        (limit_json, *types),
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
        print("[DRY-RUN] would normalize existing limit_per_category values into JSON form")
        print(
            f"[DRY-RUN] would backfill limit_per_category={args.limit_per_cat}, "
            f"per_source_cap={args.per_source_cap} for feishu_md/info_html writers with NULL values"
        )
        return

    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_table(conn)
        ensure_columns(conn)
        normalized = normalize_existing_limits(conn)
        limit_updates, cap_updates = apply_defaults(conn, args.limit_per_cat, args.per_source_cap)
        conn.commit()

    print(f"Updated pipeline_writers in {db_path}")
    print(f"- limit_per_category normalized rows: {normalized}")
    print(f"- limit_per_category backfilled rows: {limit_updates}")
    print(f"- per_source_cap backfilled rows: {cap_updates}")


if __name__ == "__main__":
    main()
