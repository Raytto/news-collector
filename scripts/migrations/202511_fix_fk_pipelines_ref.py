from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "info.db"


def table_sql_contains(conn: sqlite3.Connection, table: str, needle: str) -> bool:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not row or not row[0]:
        return False
    return needle.lower() in str(row[0]).lower()


def rebuild_tables(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        cur = conn.cursor()
        # 1) pipeline_filters
        if table_sql_contains(conn, "pipeline_filters", "pipelines_old"):
            cur.execute("ALTER TABLE pipeline_filters RENAME TO pipeline_filters_old")
            cur.execute(
                """
                CREATE TABLE pipeline_filters (
                  pipeline_id      INTEGER NOT NULL,
                  all_categories   INTEGER NOT NULL DEFAULT 1,
                  categories_json  TEXT,
                  all_src          INTEGER NOT NULL DEFAULT 1,
                  include_src_json TEXT,
                  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
                )
                """
            )
            cur.execute(
                "INSERT INTO pipeline_filters SELECT pipeline_id, all_categories, categories_json, all_src, include_src_json FROM pipeline_filters_old"
            )
            cur.execute("DROP TABLE pipeline_filters_old")

        # 2) pipeline_writers
        if table_sql_contains(conn, "pipeline_writers", "pipelines_old"):
            cur.execute("ALTER TABLE pipeline_writers RENAME TO pipeline_writers_old")
            cur.execute(
                """
                CREATE TABLE pipeline_writers (
                  pipeline_id         INTEGER NOT NULL,
                  type                TEXT NOT NULL,
                  hours               INTEGER NOT NULL,
                  weights_json        TEXT,
                  bonus_json          TEXT,
                  limit_per_category  TEXT,
                  per_source_cap      INTEGER,
                  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
                )
                """
            )
            cur.execute(
                "INSERT INTO pipeline_writers SELECT pipeline_id, type, hours, weights_json, bonus_json, limit_per_category, per_source_cap FROM pipeline_writers_old"
            )
            cur.execute("DROP TABLE pipeline_writers_old")
            # Recreate unique index if our earlier migration added it
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_writers_pipeline_id ON pipeline_writers(pipeline_id)"
            )

        # 3) pipeline_writer_metric_weights
        if table_sql_contains(conn, "pipeline_writer_metric_weights", "pipelines_old"):
            cur.execute("ALTER TABLE pipeline_writer_metric_weights RENAME TO pipeline_writer_metric_weights_old")
            cur.execute(
                """
                CREATE TABLE pipeline_writer_metric_weights (
                  pipeline_id INTEGER NOT NULL,
                  metric_id   INTEGER NOT NULL,
                  weight      REAL    NOT NULL,
                  enabled     INTEGER NOT NULL DEFAULT 1,
                  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                  PRIMARY KEY (pipeline_id, metric_id),
                  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id),
                  FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
                )
                """
            )
            cur.execute(
                "INSERT INTO pipeline_writer_metric_weights SELECT pipeline_id, metric_id, weight, enabled, created_at, updated_at FROM pipeline_writer_metric_weights_old"
            )
            cur.execute("DROP TABLE pipeline_writer_metric_weights_old")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_wm_weights_pipeline ON pipeline_writer_metric_weights (pipeline_id)"
            )

        # 4) deliveries email
        if table_sql_contains(conn, "pipeline_deliveries_email", "pipelines_old"):
            cur.execute("ALTER TABLE pipeline_deliveries_email RENAME TO pipeline_deliveries_email_old")
            cur.execute(
                """
                CREATE TABLE pipeline_deliveries_email (
                  id           INTEGER PRIMARY KEY AUTOINCREMENT,
                  pipeline_id  INTEGER NOT NULL,
                  email        TEXT NOT NULL,
                  subject_tpl  TEXT NOT NULL,
                  deliver_type TEXT NOT NULL DEFAULT 'email',
                  UNIQUE(pipeline_id),
                  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
                )
                """
            )
            cur.execute(
                "INSERT INTO pipeline_deliveries_email SELECT id, pipeline_id, email, subject_tpl, deliver_type FROM pipeline_deliveries_email_old"
            )
            cur.execute("DROP TABLE pipeline_deliveries_email_old")

        # 5) deliveries feishu
        if table_sql_contains(conn, "pipeline_deliveries_feishu", "pipelines_old"):
            cur.execute("ALTER TABLE pipeline_deliveries_feishu RENAME TO pipeline_deliveries_feishu_old")
            cur.execute(
                """
                CREATE TABLE pipeline_deliveries_feishu (
                  id           INTEGER PRIMARY KEY AUTOINCREMENT,
                  pipeline_id  INTEGER NOT NULL,
                  app_id       TEXT NOT NULL,
                  app_secret   TEXT NOT NULL,
                  to_all_chat  INTEGER NOT NULL DEFAULT 0,
                  chat_id      TEXT,
                  title_tpl    TEXT,
                  to_all       INTEGER DEFAULT 0,
                  content_json TEXT,
                  deliver_type TEXT NOT NULL DEFAULT 'feishu',
                  UNIQUE(pipeline_id),
                  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
                )
                """
            )
            cur.execute(
                "INSERT INTO pipeline_deliveries_feishu SELECT id, pipeline_id, app_id, app_secret, to_all_chat, chat_id, title_tpl, to_all, content_json, deliver_type FROM pipeline_deliveries_feishu_old"
            )
            cur.execute("DROP TABLE pipeline_deliveries_feishu_old")

        # 6) pipeline_runs
        if table_sql_contains(conn, "pipeline_runs", "pipelines_old"):
            cur.execute("ALTER TABLE pipeline_runs RENAME TO pipeline_runs_old")
            cur.execute(
                """
                CREATE TABLE pipeline_runs (
                  id           INTEGER PRIMARY KEY AUTOINCREMENT,
                  pipeline_id  INTEGER NOT NULL,
                  started_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                  finished_at  TEXT,
                  status       TEXT,
                  summary      TEXT,
                  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
                )
                """
            )
            cur.execute(
                "INSERT INTO pipeline_runs SELECT id, pipeline_id, started_at, finished_at, status, summary FROM pipeline_runs_old"
            )
            cur.execute("DROP TABLE pipeline_runs_old")

        # Recreate unique index for filters if we added previously
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_filters_pipeline_id ON pipeline_filters(pipeline_id)"
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def main() -> None:
    db_path = DB_PATH
    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    with sqlite3.connect(str(db_path)) as conn:
        rebuild_tables(conn)
        conn.commit()
    print("Fixed foreign keys to reference pipelines(id) instead of pipelines_old")


if __name__ == "__main__":
    main()

