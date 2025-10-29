from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "info.db"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipelines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL UNIQUE,
  enabled      INTEGER NOT NULL DEFAULT 1,
  description  TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipeline_filters (
  pipeline_id      INTEGER NOT NULL,
  all_categories   INTEGER NOT NULL DEFAULT 1,
  categories_json  TEXT,
  all_src          INTEGER NOT NULL DEFAULT 1,
  include_src_json TEXT,
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE TABLE IF NOT EXISTS pipeline_writers (
  pipeline_id         INTEGER NOT NULL,
  type                TEXT NOT NULL,
  hours               INTEGER NOT NULL,
  weights_json        TEXT,
  bonus_json          TEXT,
  limit_per_category  TEXT,
  per_source_cap      INTEGER,
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE TABLE IF NOT EXISTS pipeline_deliveries_email (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  email        TEXT NOT NULL,
  subject_tpl  TEXT NOT NULL,
  deliver_type TEXT NOT NULL DEFAULT 'email',
  UNIQUE(pipeline_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE TABLE IF NOT EXISTS pipeline_deliveries_feishu (
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
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  started_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  finished_at  TEXT,
  status       TEXT,
  summary      TEXT,
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
"""

# Defaults aligned with writers (email_writer.py / feishu_writer.py)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "timeliness": 0.20,
    "game_relevance": 0.40,
    "mobile_game_relevance": 0.20,
    "ai_relevance": 0.10,
    "tech_relevance": 0.05,
    "quality": 0.25,
    "insight": 0.35,
    "depth": 0.25,
    "novelty": 0.20,
}

DEFAULT_SOURCE_BONUS: Dict[str, float] = {
    "openai.research": 3.0,
    "deepmind": 1.0,
    "qbitai-zhiku": 2.0,
}


def ensure_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(pipeline_writers)")
        existing_cols = {row[1] for row in cur.fetchall()}
        if "limit_per_category" not in existing_cols:
            cur.execute("ALTER TABLE pipeline_writers ADD COLUMN limit_per_category TEXT")
        if "per_source_cap" not in existing_cols:
            cur.execute("ALTER TABLE pipeline_writers ADD COLUMN per_source_cap INTEGER")
        conn.commit()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_limit_map(value: Any) -> Optional[Dict[str, int]]:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, (int, float)):
        return {"default": int(value)}
    if isinstance(value, str):
        s = value.strip()
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
            value = parsed
    if isinstance(value, dict):
        result: Dict[str, int] = {}
        for k, v in value.items():
            key = str(k).strip()
            if not key:
                continue
            try:
                result[key] = int(v)
            except (TypeError, ValueError):
                continue
        return result
    return None


def _limit_map_to_json(limit_map: Optional[Dict[str, int]]) -> Optional[str]:
    if limit_map is None:
        return None
    return json.dumps(limit_map, ensure_ascii=False)


def fetch_pipeline_list(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    rows = cur.execute(
        """
        WITH latest_writer AS (
          SELECT pipeline_id, MAX(rowid) AS rid
          FROM pipeline_writers
          GROUP BY pipeline_id
        )
        SELECT p.id, p.name, p.enabled, p.description, p.updated_at,
               w.type AS writer_type, w.hours AS writer_hours,
               CASE WHEN e.pipeline_id IS NOT NULL THEN 'email'
                    WHEN f.pipeline_id IS NOT NULL THEN 'feishu'
                    ELSE NULL END AS delivery_kind
        FROM pipelines AS p
        LEFT JOIN latest_writer lw ON lw.pipeline_id = p.id
        LEFT JOIN pipeline_writers AS w ON w.rowid = lw.rid
        LEFT JOIN pipeline_deliveries_email AS e ON e.pipeline_id = p.id
        LEFT JOIN pipeline_deliveries_feishu AS f ON f.pipeline_id = p.id
        GROUP BY p.id
        ORDER BY p.id
        """
    ).fetchall()
    result: list[dict] = []
    for r in rows:
        result.append({
            "id": int(r["id"]),
            "name": r["name"],
            "enabled": int(r["enabled"]),
            "description": r["description"],
            "updated_at": r["updated_at"],
            "writer_type": r["writer_type"],
            "writer_hours": r["writer_hours"],
            "delivery_kind": r["delivery_kind"],
        })
    return result


def fetch_pipeline(conn: sqlite3.Connection, pid: int) -> Optional[dict]:
    cur = conn.cursor()
    p = cur.execute(
        "SELECT id, name, enabled, COALESCE(description,'') AS description FROM pipelines WHERE id=?",
        (pid,),
    ).fetchone()
    if not p:
        return None
    f = cur.execute(
        "SELECT all_categories, categories_json, all_src, include_src_json FROM pipeline_filters WHERE pipeline_id=? ORDER BY rowid DESC LIMIT 1",
        (pid,),
    ).fetchone()
    w = cur.execute(
        "SELECT type, hours, COALESCE(weights_json,''), COALESCE(bonus_json,''), limit_per_category, per_source_cap FROM pipeline_writers WHERE pipeline_id=? ORDER BY rowid DESC LIMIT 1",
        (pid,),
    ).fetchone()
    e = cur.execute(
        "SELECT email, subject_tpl FROM pipeline_deliveries_email WHERE pipeline_id=?",
        (pid,),
    ).fetchone()
    fs = cur.execute(
        "SELECT app_id, app_secret, to_all_chat, chat_id, COALESCE(title_tpl,''), to_all, COALESCE(content_json,'') FROM pipeline_deliveries_feishu WHERE pipeline_id=?",
        (pid,),
    ).fetchone()

    filters = None
    if f:
        filters = {
            "all_categories": int(f[0]),
            "categories_json": json.loads(f[1]) if f[1] else None,
            "all_src": int(f[2]),
            "include_src_json": json.loads(f[3]) if f[3] else None,
        }

    writer = None
    if w:
        writer = {
            "type": str(w[0] or ""),
            "hours": int(w[1] or 24),
            "weights_json": json.loads(w[2]) if w[2] else None,
            "bonus_json": json.loads(w[3]) if w[3] else None,
            "limit_per_category": _normalize_limit_map(w[4]),
            "per_source_cap": int(w[5]) if w[5] is not None else None,
        }
        # Provide effective defaults for editing convenience
        if writer["limit_per_category"] is None:
            writer["limit_per_category"] = {"default": 10}
        if writer["per_source_cap"] is None or (isinstance(writer["per_source_cap"], int) and writer["per_source_cap"] <= 0):
            writer["per_source_cap"] = 3
        if writer["weights_json"] is None:
            writer["weights_json"] = DEFAULT_WEIGHTS.copy()
        if writer["bonus_json"] is None:
            writer["bonus_json"] = DEFAULT_SOURCE_BONUS.copy()

    delivery: Optional[dict] = None
    if e:
        delivery = {
            "kind": "email",
            "email": e[0],
            "subject_tpl": e[1],
        }
    elif fs:
        delivery = {
            "kind": "feishu",
            "app_id": fs[0],
            "app_secret": fs[1],
            "to_all_chat": int(fs[2] or 0),
            "chat_id": fs[3],
            "title_tpl": fs[4],
            "to_all": int(fs[5] or 0),
            "content_json": json.loads(fs[6]) if fs[6] else None,
        }

    return {
        "pipeline": {
            "id": int(p["id"]),
            "name": p["name"],
            "enabled": int(p["enabled"]),
            "description": p["description"],
        },
        "filters": filters,
        "writer": writer,
        "delivery": delivery,
    }


def _to_json_text(val: Any) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return json.dumps(val, ensure_ascii=False)
    if isinstance(val, (int, float, str)):
        return str(val)
    return None


def create_or_update_pipeline(conn: sqlite3.Connection, payload: dict, pid: Optional[int] = None) -> int:
    cur = conn.cursor()
    base = payload.get("pipeline") or {}
    name = str(base.get("name") or "").strip()
    if not name:
        raise ValueError("pipeline.name is required")
    enabled = 1 if base.get("enabled", 1) else 0
    description = str(base.get("description") or "")

    if pid is None:
        cur.execute(
            "INSERT INTO pipelines (name, enabled, description) VALUES (?, ?, ?)",
            (name, enabled, description),
        )
        pid = int(cur.execute("SELECT last_insert_rowid()").fetchone()[0])
    else:
        cur.execute(
            "UPDATE pipelines SET name=?, enabled=?, description=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, enabled, description, pid),
        )

    # filters
    f = payload.get("filters") or {}
    if f:
        # replace semantics
        cur.execute("DELETE FROM pipeline_filters WHERE pipeline_id=?", (pid,))
        cur.execute(
            "INSERT OR REPLACE INTO pipeline_filters (pipeline_id, all_categories, categories_json, all_src, include_src_json) VALUES (?, ?, ?, ?, ?)",
            (
                pid,
                int(f.get("all_categories", 1)),
                _to_json_text(f.get("categories_json")),
                int(f.get("all_src", 1)),
                _to_json_text(f.get("include_src_json")),
            ),
        )

    # writer
    w = payload.get("writer") or {}
    if w:
        cur.execute("DELETE FROM pipeline_writers WHERE pipeline_id=?", (pid,))
        limit_map = _normalize_limit_map(w.get("limit_per_category"))
        cur.execute(
            "INSERT OR REPLACE INTO pipeline_writers (pipeline_id, type, hours, weights_json, bonus_json, limit_per_category, per_source_cap) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                pid,
                str(w.get("type") or ""),
                int(w.get("hours") or 24),
                _to_json_text(w.get("weights_json")),
                _to_json_text(w.get("bonus_json")),
                _limit_map_to_json(limit_map),
                int(w.get("per_source_cap")) if w.get("per_source_cap") is not None else None,
            ),
        )

    # delivery
    d = payload.get("delivery") or {}
    if d:
        kind = str(d.get("kind") or "").strip().lower()
        if kind == "email":
            cur.execute("DELETE FROM pipeline_deliveries_feishu WHERE pipeline_id=?", (pid,))
            cur.execute("DELETE FROM pipeline_deliveries_email WHERE pipeline_id=?", (pid,))
            cur.execute(
                "INSERT OR REPLACE INTO pipeline_deliveries_email (pipeline_id, email, subject_tpl) VALUES (?, ?, ?)",
                (pid, str(d.get("email") or ""), str(d.get("subject_tpl") or "")),
            )
        elif kind == "feishu":
            cur.execute("DELETE FROM pipeline_deliveries_email WHERE pipeline_id=?", (pid,))
            cur.execute("DELETE FROM pipeline_deliveries_feishu WHERE pipeline_id=?", (pid,))
            cur.execute(
                "INSERT OR REPLACE INTO pipeline_deliveries_feishu (pipeline_id, app_id, app_secret, to_all_chat, chat_id, title_tpl, to_all, content_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    str(d.get("app_id") or ""),
                    str(d.get("app_secret") or ""),
                    int(d.get("to_all_chat") or 0),
                    (str(d.get("chat_id") or "") or None),
                    str(d.get("title_tpl") or "通知"),
                    int(d.get("to_all") or 0),
                    _to_json_text(d.get("content_json")),
                ),
            )

    conn.commit()
    return int(pid)


def delete_pipeline(conn: sqlite3.Connection, pid: int) -> None:
    cur = conn.cursor()
    for t in (
        "pipeline_filters",
        "pipeline_writers",
        "pipeline_deliveries_email",
        "pipeline_deliveries_feishu",
        "pipeline_runs",
    ):
        cur.execute(f"DELETE FROM {t} WHERE pipeline_id=?", (pid,))
    cur.execute("DELETE FROM pipelines WHERE id=?", (pid,))
    conn.commit()


def fetch_options(conn: sqlite3.Connection) -> dict:
    cur = conn.cursor()
    rows = cur.execute("SELECT DISTINCT category FROM info WHERE category IS NOT NULL AND TRIM(category) <> '' ORDER BY category").fetchall()
    categories = [r[0] for r in rows]
    return {
        "categories": categories,
        "writer_types": ["feishu_md", "info_html"],
        "delivery_kinds": ["email", "feishu"],
    }
