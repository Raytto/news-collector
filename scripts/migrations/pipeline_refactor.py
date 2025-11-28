#!/usr/bin/env python3
"""
Idempotent pipeline refactor migration.

Adds pipeline_class columns, creates class mapping tables, source run tracking,
and upgrades info_ai_review to (info_id, evaluator_key) composite PK. Designed
to be re-runnable on existing databases without failing on duplicate columns.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (name,)
    )
    return cur.fetchone() is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def add_column_if_missing(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    col_name = column_def.split()[0]
    if column_exists(conn, table, col_name):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def migrate_info_ai_review(conn: sqlite3.Connection) -> None:
    # If table missing entirely, create the new version directly.
    if not table_exists(conn, "info_ai_review"):
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS info_ai_review (
              info_id         INTEGER NOT NULL,
              evaluator_key   TEXT    NOT NULL DEFAULT 'news_evaluator',
              final_score     REAL    NOT NULL DEFAULT 0.0,
              ai_comment      TEXT    NOT NULL,
              ai_summary      TEXT    NOT NULL,
              ai_key_concepts TEXT,
              ai_summary_long TEXT,
              raw_response    TEXT,
              created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
              PRIMARY KEY (info_id, evaluator_key),
              FOREIGN KEY (info_id) REFERENCES info(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS ux_info_ai_review_info_eval
              ON info_ai_review (info_id, evaluator_key);
            """
        )
        return

    cols = conn.execute("PRAGMA table_info(info_ai_review)").fetchall()
    col_names = {row[1] for row in cols}
    pk_cols = [row[1] for row in cols if int(row[5] or 0) > 0]
    has_composite_pk = set(pk_cols) == {"info_id", "evaluator_key"} and len(pk_cols) == 2
    if has_composite_pk:
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_info_ai_review_info_eval ON info_ai_review (info_id, evaluator_key)"
        )
        return

    evaluator_expr = "COALESCE(evaluator_key, 'news_evaluator')" if "evaluator_key" in col_names else "'news_evaluator'"
    final_expr = "COALESCE(final_score, 0.0)" if "final_score" in col_names else "0.0"
    comment_expr = "COALESCE(ai_comment, '')" if "ai_comment" in col_names else "''"
    summary_expr = "COALESCE(ai_summary, '')" if "ai_summary" in col_names else "''"
    key_concepts_expr = "ai_key_concepts" if "ai_key_concepts" in col_names else "NULL"
    summary_long_expr = "ai_summary_long" if "ai_summary_long" in col_names else "NULL"
    raw_expr = "raw_response" if "raw_response" in col_names else "NULL"
    created_expr = "created_at" if "created_at" in col_names else "CURRENT_TIMESTAMP"
    updated_expr = "updated_at" if "updated_at" in col_names else "CURRENT_TIMESTAMP"

    conn.executescript(
        f"""
        PRAGMA foreign_keys=off;
        BEGIN TRANSACTION;
        CREATE TABLE IF NOT EXISTS info_ai_review_new (
          info_id        INTEGER NOT NULL,
          evaluator_key  TEXT    NOT NULL DEFAULT 'news_evaluator',
          final_score    REAL    NOT NULL DEFAULT 0.0,
          ai_comment     TEXT    NOT NULL,
          ai_summary     TEXT    NOT NULL,
          ai_key_concepts TEXT,
          ai_summary_long TEXT,
          raw_response   TEXT,
          created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at     TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY (info_id, evaluator_key),
          FOREIGN KEY (info_id) REFERENCES info(id)
        );
        INSERT OR IGNORE INTO info_ai_review_new (info_id, evaluator_key, final_score, ai_comment, ai_summary, ai_key_concepts, ai_summary_long, raw_response, created_at, updated_at)
          SELECT info_id,
                 {evaluator_expr},
                 {final_expr},
                 {comment_expr},
                 {summary_expr},
                 {key_concepts_expr},
                 {summary_long_expr},
                 {raw_expr},
                 {created_expr},
                 {updated_expr}
          FROM info_ai_review;
        DROP TABLE IF EXISTS info_ai_review;
        ALTER TABLE info_ai_review_new RENAME TO info_ai_review;
        CREATE UNIQUE INDEX IF NOT EXISTS ux_info_ai_review_info_eval
          ON info_ai_review (info_id, evaluator_key);
        COMMIT;
        PRAGMA foreign_keys=on;
        """
    )


def migrate(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    with conn:
        # 1) New tables
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_classes (
              id           INTEGER PRIMARY KEY AUTOINCREMENT,
              key          TEXT NOT NULL UNIQUE,
              label_zh     TEXT NOT NULL,
              description  TEXT,
              enabled      INTEGER NOT NULL DEFAULT 1,
              created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
              updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_class_categories (
              pipeline_class_id INTEGER NOT NULL,
              category_key      TEXT NOT NULL,
              PRIMARY KEY (pipeline_class_id, category_key),
              FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id),
              FOREIGN KEY (category_key) REFERENCES categories(key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_class_evaluators (
              pipeline_class_id INTEGER NOT NULL,
              evaluator_key     TEXT NOT NULL,
              PRIMARY KEY (pipeline_class_id, evaluator_key),
              FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pipeline_class_writers (
              pipeline_class_id INTEGER NOT NULL,
              writer_type       TEXT NOT NULL,
              PRIMARY KEY (pipeline_class_id, writer_type),
              FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
            )
            """
        )

        # 2) Extend pipelines
        if table_exists(conn, "pipelines"):
            add_column_if_missing(
                conn,
                "pipelines",
                "pipeline_class_id INTEGER REFERENCES pipeline_classes(id)",
            )
            add_column_if_missing(
                conn,
                "pipelines",
                "debug_enabled INTEGER NOT NULL DEFAULT 0",
            )
            add_column_if_missing(
                conn,
                "pipelines",
                "evaluator_key TEXT NOT NULL DEFAULT 'news_evaluator'",
            )

        # 4) Source runs
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS source_runs (
              source_id   INTEGER PRIMARY KEY,
              last_run_at TEXT NOT NULL,
              FOREIGN KEY (source_id) REFERENCES sources(id)
            )
            """
        )

        # 5) info_ai_review migration
        migrate_info_ai_review(conn)

        # Seeds
        conn.executescript(
            """
            INSERT OR IGNORE INTO pipeline_classes (key, label_zh, description) VALUES
              ('general_news', '综合资讯', '新闻/资讯类管线'),
              ('legou_minigame', '乐狗副玩法', 'YouTube 小游戏推荐管线');

            INSERT OR IGNORE INTO pipeline_class_categories (pipeline_class_id, category_key)
              SELECT pc.id, 'game' FROM pipeline_classes pc WHERE pc.key='general_news';
            INSERT OR IGNORE INTO pipeline_class_categories (pipeline_class_id, category_key)
              SELECT pc.id, 'tech' FROM pipeline_classes pc WHERE pc.key='general_news';
            INSERT OR IGNORE INTO pipeline_class_categories (pipeline_class_id, category_key)
              SELECT pc.id, 'game_yt' FROM pipeline_classes pc WHERE pc.key='legou_minigame';

            INSERT OR IGNORE INTO pipeline_class_evaluators (pipeline_class_id, evaluator_key)
              SELECT pc.id, 'news_evaluator' FROM pipeline_classes pc WHERE pc.key='general_news';
            INSERT OR IGNORE INTO pipeline_class_evaluators (pipeline_class_id, evaluator_key)
              SELECT pc.id, 'legou_minigame_evaluator' FROM pipeline_classes pc WHERE pc.key='legou_minigame';

            INSERT OR IGNORE INTO pipeline_class_writers (pipeline_class_id, writer_type)
              SELECT pc.id, 'email_news' FROM pipeline_classes pc WHERE pc.key='general_news';
            INSERT OR IGNORE INTO pipeline_class_writers (pipeline_class_id, writer_type)
              SELECT pc.id, 'feishu_news' FROM pipeline_classes pc WHERE pc.key='general_news';
            INSERT OR IGNORE INTO pipeline_class_writers (pipeline_class_id, writer_type)
              SELECT pc.id, 'feishu_legou_game' FROM pipeline_classes pc WHERE pc.key='legou_minigame';
            """
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Idempotent pipeline refactor migration")
    parser.add_argument("--db", required=True, help="Path to SQLite DB (info.db)")
    args = parser.parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    migrate(db_path)
    print(f"[done] pipeline refactor migration applied to {db_path}")


if __name__ == "__main__":
    main()
