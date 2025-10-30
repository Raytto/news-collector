from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "info.db"

DATE_PLACEHOLDER_VARIANTS = ("${date_zh}", "$(date_zh)", "${data_zh}", "$(data_zh)")
DEFAULT_DATE_PLACEHOLDER = "${date_zh}"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS categories (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  key        TEXT NOT NULL UNIQUE,
  label_zh   TEXT NOT NULL,
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sources (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,
  label_zh     TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  category_key TEXT NOT NULL,
  script_path  TEXT NOT NULL,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (category_key) REFERENCES categories(key)
);

CREATE INDEX IF NOT EXISTS idx_sources_enabled
  ON sources (enabled);

CREATE INDEX IF NOT EXISTS idx_sources_category
  ON sources (category_key, enabled);

CREATE TABLE IF NOT EXISTS source_address (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id  INTEGER NOT NULL,
  address    TEXT NOT NULL,
  FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_source_address_source
  ON source_address (source_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_address_unique
  ON source_address (source_id, address);

CREATE TABLE IF NOT EXISTS pipelines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS pipeline_writer_metric_weights (
  pipeline_id INTEGER NOT NULL,
  metric_id   INTEGER NOT NULL,
  weight      REAL    NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (pipeline_id, metric_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id),
  FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
);

CREATE INDEX IF NOT EXISTS idx_wm_weights_pipeline
  ON pipeline_writer_metric_weights (pipeline_id);

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

-- Users and Auth
CREATE TABLE IF NOT EXISTS users (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  email          TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL,
  is_admin       INTEGER NOT NULL DEFAULT 0,
  enabled        INTEGER NOT NULL DEFAULT 1,
  avatar_url     TEXT,
  created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  verified_at    TEXT,
  last_login_at  TEXT
);

CREATE TABLE IF NOT EXISTS user_sessions (
  id            TEXT PRIMARY KEY,
  user_id       INTEGER NOT NULL,
  token_hash    TEXT NOT NULL,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  last_seen_at  TEXT,
  expires_at    TEXT NOT NULL,
  revoked_at    TEXT,
  ip            TEXT,
  user_agent    TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_sessions_token_hash
  ON user_sessions (token_hash);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user
  ON user_sessions (user_id, expires_at);

CREATE TABLE IF NOT EXISTS auth_email_codes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL,
  user_id       INTEGER,
  purpose       TEXT NOT NULL,
  code_hash     TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  consumed_at   TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 5,
  resent_count  INTEGER NOT NULL DEFAULT 0,
  created_ip    TEXT,
  user_agent    TEXT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_codes_active_unique
ON auth_email_codes (email, purpose)
WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_auth_codes_lookup
  ON auth_email_codes (email, purpose, expires_at);
"""

# Defaults aligned with writers (email_writer.py / feishu_writer.py)
DEFAULT_METRICS: Tuple[Dict[str, object], ...] = (
    {"key": "timeliness", "label_zh": "时效性", "default_weight": 0.14, "sort_order": 10},
    {"key": "game_relevance", "label_zh": "游戏相关性", "default_weight": 0.20, "sort_order": 20},
    {"key": "mobile_game_relevance", "label_zh": "手游相关性", "default_weight": 0.09, "sort_order": 30},
    {"key": "ai_relevance", "label_zh": "AI相关性", "default_weight": 0.14, "sort_order": 40},
    {"key": "tech_relevance", "label_zh": "科技相关性", "default_weight": 0.11, "sort_order": 50},
    {"key": "quality", "label_zh": "文章质量", "default_weight": 0.13, "sort_order": 60},
    {"key": "insight", "label_zh": "洞察力", "default_weight": 0.08, "sort_order": 70},
    {"key": "depth", "label_zh": "深度", "default_weight": 0.06, "sort_order": 80},
    {"key": "novelty", "label_zh": "新颖度", "default_weight": 0.05, "sort_order": 90},
)

DEFAULT_WEIGHTS: Dict[str, float] = {
    str(metric["key"]): float(metric.get("default_weight") or 0.0) for metric in DEFAULT_METRICS
}

DEFAULT_SOURCE_BONUS: Dict[str, float] = {
    "openai.research": 3.0,
    "deepmind": 1.0,
    "qbitai-zhiku": 2.0,
}

_MISSING = object()


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
        # Add owner_user_id to pipelines if missing
        cur.execute("PRAGMA table_info(pipelines)")
        p_cols = {row[1] for row in cur.fetchall()}
        if "owner_user_id" not in p_cols:
            cur.execute("ALTER TABLE pipelines ADD COLUMN owner_user_id INTEGER")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_pipelines_owner ON pipelines (owner_user_id)")
        # Add enabled column to users if missing
        cur.execute("PRAGMA table_info(users)")
        u_cols = {row[1] for row in cur.fetchall()}
        if "enabled" not in u_cols:
            cur.execute("ALTER TABLE users ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
        # Migrate pipelines table to drop UNIQUE constraint on name if present
        row = cur.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='pipelines'"
        ).fetchone()
        table_sql = row[0] if row and row[0] else ""
        # If the original table was created with a UNIQUE constraint on name, rebuild table
        if "UNIQUE" in table_sql.upper():
            # Disable foreign key checks during migration
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                cur.execute("ALTER TABLE pipelines RENAME TO pipelines_old")
                # Recreate without UNIQUE constraint on name (keep NOT NULL to avoid None values)
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS pipelines (
                      id           INTEGER PRIMARY KEY AUTOINCREMENT,
                      name         TEXT NOT NULL,
                      enabled      INTEGER NOT NULL DEFAULT 1,
                      description  TEXT,
                      created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                      updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
                      owner_user_id INTEGER
                    )
                    """
                )
                # Copy data across
                cur.execute(
                    """
                    INSERT INTO pipelines (id, name, enabled, description, created_at, updated_at, owner_user_id)
                    SELECT id, name, enabled, description, created_at, updated_at, owner_user_id
                    FROM pipelines_old
                    """
                )
                # Drop old table
                cur.execute("DROP TABLE pipelines_old")
                # Recreate index for owner if missing
                cur.execute("CREATE INDEX IF NOT EXISTS idx_pipelines_owner ON pipelines (owner_user_id)")
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
        conn.commit()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
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


def _normalize_email_subject_tpl(value: Any) -> str:
    raw = str(value or "")
    for placeholder in DATE_PLACEHOLDER_VARIANTS:
        raw = raw.replace(placeholder, "")
    stripped = raw.strip()
    if not stripped:
        return DEFAULT_DATE_PLACEHOLDER
    return f"{stripped}{DEFAULT_DATE_PLACEHOLDER}"


def _ensure_metric_key(conn: sqlite3.Connection, raw_key: Any) -> Optional[str]:
    key = str(raw_key or "").strip()
    if not key:
        return None
    row = conn.execute("SELECT key FROM ai_metrics WHERE key=?", (key,)).fetchone()
    if row:
        return str(row[0])
    if key.isdigit():
        row = conn.execute("SELECT key FROM ai_metrics WHERE id=?", (int(key),)).fetchone()
        if row:
            return str(row[0])
    return None


def _normalize_weights_json(conn: sqlite3.Connection, raw_value: Any) -> Optional[str]:
    if raw_value is None:
        return None
    value = raw_value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore")
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            return s
        else:
            value = parsed
    if isinstance(value, dict):
        normalized: Dict[str, float] = {}
        for key, val in value.items():
            metric_key = _ensure_metric_key(conn, key)
            if metric_key is None:
                print(f"[WARN] normalize_weights_json: 跳过未知指标 {key!r}")
                continue
            try:
                normalized[metric_key] = float(val)
            except (TypeError, ValueError):
                continue
        return json.dumps(normalized, ensure_ascii=False)
    return str(value)


def _resolve_metric_id(conn: sqlite3.Connection, raw_key: Any) -> Optional[int]:
    key = _ensure_metric_key(conn, raw_key)
    if key is None:
        return None
    row = conn.execute("SELECT id FROM ai_metrics WHERE key=?", (key,)).fetchone()
    if not row:
        return None
    return int(row[0])


def _load_metric_defaults(conn: sqlite3.Connection) -> Dict[str, float]:
    metrics = _list_active_metrics(conn)
    return {
        metric["key"]: float(metric.get("default_weight") or 0.0)
        for metric in metrics
    }


def _list_active_metrics(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            """
            SELECT key, label_zh, default_weight, sort_order
            FROM ai_metrics
            WHERE active = 1
            ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return [
            {
                "key": str(metric["key"]),
                "label_zh": str(metric["label_zh"]),
                "default_weight": float(metric.get("default_weight") or 0.0),
                "sort_order": int(metric.get("sort_order") or 0),
            }
            for metric in DEFAULT_METRICS
        ]
    if not rows:
        return [
            {
                "key": str(metric["key"]),
                "label_zh": str(metric["label_zh"]),
                "default_weight": float(metric.get("default_weight") or 0.0),
                "sort_order": int(metric.get("sort_order") or 0),
            }
            for metric in DEFAULT_METRICS
        ]
    return [
        {
            "key": str(row[0]),
            "label_zh": str(row[1]),
            "default_weight": float(row[2] or 0.0),
            "sort_order": int(row[3] or 0),
        }
        for row in rows
    ]


def _fetch_metric_weights(conn: sqlite3.Connection, pipeline_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT m.key, w.weight, w.enabled
        FROM pipeline_writer_metric_weights AS w
        JOIN ai_metrics AS m ON m.id = w.metric_id
        WHERE w.pipeline_id=?
        ORDER BY m.sort_order ASC, m.id ASC
        """,
        (pipeline_id,),
    ).fetchall()
    return [
        {"key": str(row[0]), "weight": float(row[1]), "enabled": int(row[2] or 0)}
        for row in rows
    ]


def fetch_pipeline_list(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.cursor()
    rows = cur.execute(
        """
        WITH latest_writer AS (
          SELECT pipeline_id, MAX(rowid) AS rid
          FROM pipeline_writers
          GROUP BY pipeline_id
        )
        SELECT p.id, p.name, p.enabled, p.description, p.updated_at, p.owner_user_id,
               u.name AS owner_user_name, u.email AS owner_user_email,
               w.type AS writer_type, w.hours AS writer_hours,
               CASE WHEN e.pipeline_id IS NOT NULL THEN 'email'
                    WHEN f.pipeline_id IS NOT NULL THEN 'feishu'
                    ELSE NULL END AS delivery_kind
        FROM pipelines AS p
        LEFT JOIN users AS u ON u.id = p.owner_user_id
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
            "owner_user_id": int(r["owner_user_id"]) if r["owner_user_id"] is not None else None,
            "owner_user_name": r["owner_user_name"],
            "owner_user_email": r["owner_user_email"],
            "writer_type": r["writer_type"],
            "writer_hours": r["writer_hours"],
            "delivery_kind": r["delivery_kind"],
        })
    return result


def fetch_pipeline(conn: sqlite3.Connection, pid: int) -> Optional[dict]:
    cur = conn.cursor()
    p = cur.execute(
        "SELECT id, name, enabled, COALESCE(description,'') AS description, owner_user_id FROM pipelines WHERE id=?",
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
        defaults = _load_metric_defaults(conn)
        normalized_weights = _normalize_weights_json(conn, w[2])
        weights_dict: Dict[str, float]
        if normalized_weights:
            try:
                weights_dict = json.loads(normalized_weights)
            except json.JSONDecodeError:
                weights_dict = defaults.copy()
            else:
                if not isinstance(weights_dict, dict):
                    weights_dict = defaults.copy()
        else:
            weights_dict = defaults.copy()
        writer = {
            "type": str(w[0] or ""),
            "hours": int(w[1] or 24),
            "weights_json": weights_dict,
            "bonus_json": json.loads(w[3]) if w[3] else None,
            "limit_per_category": _normalize_limit_map(w[4]),
            "per_source_cap": int(w[5]) if w[5] is not None else None,
            "metric_weights": _fetch_metric_weights(conn, pid),
        }
        # Provide effective defaults for editing convenience
        if writer["limit_per_category"] is None:
            writer["limit_per_category"] = {"default": 10}
        if writer["per_source_cap"] is None or (isinstance(writer["per_source_cap"], int) and writer["per_source_cap"] <= 0):
            writer["per_source_cap"] = 3
        if writer["weights_json"] is None:
            writer["weights_json"] = defaults.copy()
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
            "owner_user_id": int(p["owner_user_id"]) if p["owner_user_id"] is not None else None,
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


def create_or_update_pipeline(
    conn: sqlite3.Connection,
    payload: dict,
    pid: Optional[int] = None,
    *,
    owner_user_id: Optional[int] = None,
) -> int:
    cur = conn.cursor()
    base = payload.get("pipeline") or {}
    # Name is optional and can be duplicated; store empty string if omitted
    name = str(base.get("name") or "").strip()
    enabled = 1 if base.get("enabled", 1) else 0
    description = str(base.get("description") or "")
    # Prefer explicit owner in payload, fallback to parameter
    raw_owner = base.get("owner_user_id")
    owner_id: Optional[int] = None
    if raw_owner is not None and str(raw_owner).strip() != "":
        try:
            owner_id = int(raw_owner)
        except (TypeError, ValueError):
            owner_id = None
    if owner_id is None:
        owner_id = owner_user_id

    if pid is None:
        cur.execute(
            "INSERT INTO pipelines (name, enabled, description, owner_user_id) VALUES (?, ?, ?, ?)",
            (name, enabled, description, owner_id),
        )
        pid = int(cur.execute("SELECT last_insert_rowid()").fetchone()[0])
    else:
        cur.execute(
            "UPDATE pipelines SET name=?, enabled=?, description=?, owner_user_id=COALESCE(?, owner_user_id), updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, enabled, description, owner_id, pid),
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
        weights_json_norm = _normalize_weights_json(conn, w.get("weights_json"))
        cur.execute(
            "INSERT OR REPLACE INTO pipeline_writers (pipeline_id, type, hours, weights_json, bonus_json, limit_per_category, per_source_cap) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                pid,
                str(w.get("type") or ""),
                int(w.get("hours") or 24),
                weights_json_norm,
                _to_json_text(w.get("bonus_json")),
                _limit_map_to_json(limit_map),
                int(w.get("per_source_cap")) if w.get("per_source_cap") is not None else None,
            ),
        )
        cur.execute("DELETE FROM pipeline_writer_metric_weights WHERE pipeline_id=?", (pid,))
        metric_weights_payload = w.get("metric_weights") or []
        if isinstance(metric_weights_payload, list):
            rows_to_insert: list[Tuple[int, int, float, int]] = []
            for item in metric_weights_payload:
                if not isinstance(item, dict):
                    continue
                metric_id = _resolve_metric_id(conn, item.get("key"))
                if metric_id is None:
                    continue
                try:
                    weight_val = float(item.get("weight"))
                except (TypeError, ValueError):
                    continue
                enabled_flag = 1 if int(item.get("enabled", 1) or 0) else 0
                rows_to_insert.append((pid, metric_id, weight_val, enabled_flag))
            if rows_to_insert:
                cur.executemany(
                    """
                    INSERT OR REPLACE INTO pipeline_writer_metric_weights (pipeline_id, metric_id, weight, enabled)
                    VALUES (?, ?, ?, ?)
                    """,
                    rows_to_insert,
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
                (
                    pid,
                    str(d.get("email") or ""),
                    _normalize_email_subject_tpl(d.get("subject_tpl")),
                ),
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


# -------------------- Auth Helpers --------------------

def _normalize_email(email: str | bytes | None) -> str:
    if email is None:
        return ""
    if isinstance(email, (bytes, bytearray)):
        email = email.decode("utf-8", errors="ignore")
    return str(email).strip().lower()


def get_user_by_email(conn: sqlite3.Connection, email: str) -> Optional[dict]:
    norm = _normalize_email(email)
    row = conn.execute(
        "SELECT id, email, name, is_admin, enabled, avatar_url, created_at, verified_at, last_login_at FROM users WHERE email=?",
        (norm,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "is_admin": int(row["is_admin"] or 0),
        "enabled": int(row["enabled"] or 0),
        "avatar_url": row["avatar_url"],
        "created_at": row["created_at"],
        "verified_at": row["verified_at"],
        "last_login_at": row["last_login_at"],
    }


def get_user_by_id(conn: sqlite3.Connection, uid: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT id, email, name, is_admin, enabled, avatar_url, created_at, verified_at, last_login_at FROM users WHERE id=?",
        (uid,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "email": row["email"],
        "name": row["name"],
        "is_admin": int(row["is_admin"] or 0),
        "enabled": int(row["enabled"] or 0),
        "avatar_url": row["avatar_url"],
        "created_at": row["created_at"],
        "verified_at": row["verified_at"],
        "last_login_at": row["last_login_at"],
    }


def list_users(
    conn: sqlite3.Connection,
    *,
    offset: int = 0,
    limit: int = 20,
    q: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> list[dict]:
    sql = (
        "SELECT id, email, name, is_admin, enabled, avatar_url, created_at, verified_at, last_login_at "
        "FROM users WHERE 1=1"
    )
    params: list[object] = []
    if q:
        like = f"%{str(q).strip().lower()}%"
        sql += " AND (lower(email) LIKE ? OR lower(name) LIKE ?)"
        params.extend([like, like])
    if start:
        sql += " AND created_at >= ?"
        params.append(start)
    if end:
        sql += " AND created_at <= ?"
        params.append(end)
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])
    rows = conn.execute(sql, params).fetchall()
    items: list[dict] = []
    for r in rows:
        items.append(
            {
                "id": int(r["id"]),
                "email": r["email"],
                "name": r["name"],
                "is_admin": int(r["is_admin"] or 0),
                "enabled": int(r["enabled"] or 0),
                "avatar_url": r["avatar_url"],
                "created_at": r["created_at"],
                "verified_at": r["verified_at"],
                "last_login_at": r["last_login_at"],
            }
        )
    return items


def count_users(
    conn: sqlite3.Connection,
    *,
    q: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> int:
    sql = "SELECT COUNT(1) FROM users WHERE 1=1"
    params: list[object] = []
    if q:
        like = f"%{str(q).strip().lower()}%"
        sql += " AND (lower(email) LIKE ? OR lower(name) LIKE ?)"
        params.extend([like, like])
    if start:
        sql += " AND created_at >= ?"
        params.append(start)
    if end:
        sql += " AND created_at <= ?"
        params.append(end)
    return int(conn.execute(sql, params).fetchone()[0])


def update_user(
    conn: sqlite3.Connection,
    uid: int,
    *,
    name: Optional[str] = None,
    is_admin: Optional[int] = None,
    enabled: Optional[int] = None,
) -> None:
    sets: list[str] = []
    params: list[object] = []
    if name is not None:
        sets.append("name=?")
        params.append(str(name))
    if is_admin is not None:
        try:
            flag = 1 if int(is_admin) else 0
        except (TypeError, ValueError):
            flag = 0
        sets.append("is_admin=?")
        params.append(flag)
    if enabled is not None:
        try:
            eflag = 1 if int(enabled) else 0
        except (TypeError, ValueError):
            eflag = 0
        sets.append("enabled=?")
        params.append(eflag)
    if not sets:
        return
    sql = f"UPDATE users SET {' ,'.join(sets)} WHERE id=?"
    params.append(int(uid))
    conn.execute(sql, params)
    conn.commit()


def fetch_pipeline_list_by_owner(conn: sqlite3.Connection, owner_user_id: int) -> list[dict]:
    rows = conn.execute(
        """
        WITH lw AS (
            SELECT pipeline_id AS pid, rowid AS rid
            FROM pipeline_writers
        )
        SELECT
            p.id, p.name, p.enabled, p.updated_at, p.owner_user_id,
            w.type AS writer_type, w.hours AS writer_hours,
            CASE WHEN e.id IS NOT NULL THEN 'email'
                 WHEN f.id IS NOT NULL THEN 'feishu'
                 ELSE NULL END AS delivery_kind
        FROM pipelines AS p
        LEFT JOIN lw ON lw.pid = p.id
        LEFT JOIN pipeline_writers AS w ON w.rowid = lw.rid
        LEFT JOIN pipeline_deliveries_email AS e ON e.pipeline_id = p.id
        LEFT JOIN pipeline_deliveries_feishu AS f ON f.pipeline_id = p.id
        WHERE p.owner_user_id = ?
        GROUP BY p.id
        ORDER BY p.id
        """,
        (int(owner_user_id),),
    ).fetchall()
    result: list[dict] = []
    for r in rows:
        result.append(
            {
                "id": int(r["id"]),
                "name": r["name"],
                "enabled": int(r["enabled"]),
                "description": None,
                "updated_at": r["updated_at"],
                "owner_user_id": int(r["owner_user_id"]) if r["owner_user_id"] is not None else None,
                "writer_type": r["writer_type"],
                "writer_hours": r["writer_hours"],
                "delivery_kind": r["delivery_kind"],
            }
        )
    return result


def create_user(conn: sqlite3.Connection, *, email: str, name: str, is_admin: int = 0, verified: bool = True) -> int:
    norm = _normalize_email(email)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (email, name, is_admin, enabled, verified_at) VALUES (?, ?, ?, 1, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END)",
        (norm, name.strip() or norm, 1 if is_admin else 0, 1 if verified else 0),
    )
    conn.commit()
    return int(cur.execute("SELECT last_insert_rowid()").fetchone()[0])


def create_session(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    user_id: int,
    token_hash: str,
    expires_at: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO user_sessions (id, user_id, token_hash, expires_at, ip, user_agent)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (session_id, user_id, token_hash, expires_at, ip, user_agent),
    )
    conn.commit()


def get_session_with_user(conn: sqlite3.Connection, token_hash: str) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT s.id, s.user_id, s.created_at, s.last_seen_at, s.expires_at, s.revoked_at,
               u.email, u.name, u.is_admin, u.enabled
        FROM user_sessions AS s
        JOIN users AS u ON u.id = s.user_id
        WHERE s.token_hash=?
        """,
        (token_hash,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "user_id": int(row["user_id"]),
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
        "expires_at": row["expires_at"],
        "revoked_at": row["revoked_at"],
        "user": {
            "id": int(row["user_id"]),
            "email": row["email"],
            "name": row["name"],
            "is_admin": int(row["is_admin"] or 0),
            "enabled": int(row["enabled"] or 0),
        },
    }


def touch_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        "UPDATE user_sessions SET last_seen_at=CURRENT_TIMESTAMP WHERE id=?",
        (session_id,),
    )
    conn.commit()


def revoke_session(conn: sqlite3.Connection, session_id: str) -> None:
    conn.execute(
        "UPDATE user_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE id=?",
        (session_id,),
    )
    conn.commit()


def revoke_sessions_for_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute(
        "UPDATE user_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE user_id=? AND revoked_at IS NULL",
        (int(user_id),),
    )
    conn.commit()


def set_user_last_login(conn: sqlite3.Connection, uid: int) -> None:
    conn.execute(
        "UPDATE users SET last_login_at=CURRENT_TIMESTAMP WHERE id=?",
        (uid,),
    )
    conn.commit()


def get_active_code(conn: sqlite3.Connection, email: str, purpose: str) -> Optional[sqlite3.Row]:
    norm = _normalize_email(email)
    row = conn.execute(
        """
        SELECT * FROM auth_email_codes
        WHERE email=? AND purpose=? AND consumed_at IS NULL AND expires_at > CURRENT_TIMESTAMP
        ORDER BY id DESC
        LIMIT 1
        """,
        (norm, purpose),
    ).fetchone()
    return row


def _get_unconsumed_code(conn: sqlite3.Connection, email: str, purpose: str) -> Optional[sqlite3.Row]:
    norm = _normalize_email(email)
    row = conn.execute(
        """
        SELECT * FROM auth_email_codes
        WHERE email=? AND purpose=? AND consumed_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (norm, purpose),
    ).fetchone()
    return row


def upsert_email_code(
    conn: sqlite3.Connection,
    *,
    email: str,
    purpose: str,
    code_hash: str,
    ttl_seconds: int,
    max_attempts: int,
    ip: str | None,
    user_agent: str | None,
    user_id: Optional[int] = None,
) -> None:
    norm = _normalize_email(email)
    existing = get_active_code(conn, norm, purpose)
    if existing is not None:
        conn.execute(
            """
            UPDATE auth_email_codes
            SET code_hash=?, expires_at=datetime('now', ?||' seconds'), resent_count=resent_count+1, user_id=COALESCE(?, user_id)
            WHERE id=?
            """,
            (code_hash, ttl_seconds, user_id, int(existing["id"])),
        )
    else:
        # If there is any unconsumed (even expired) record, reuse it to avoid partial index unique conflicts.
        pending = _get_unconsumed_code(conn, norm, purpose)
        if pending is not None:
            conn.execute(
                """
                UPDATE auth_email_codes
                SET code_hash=?, expires_at=datetime('now', ?||' seconds'), attempt_count=0,
                    resent_count=resent_count+1, user_id=COALESCE(?, user_id)
                WHERE id=?
                """,
                (code_hash, ttl_seconds, user_id, int(pending["id"]))
            )
        else:
            conn.execute(
                """
                INSERT INTO auth_email_codes (email, user_id, purpose, code_hash, expires_at, max_attempts, created_ip, user_agent)
                VALUES (?, ?, ?, ?, datetime('now', ?||' seconds'), ?, ?, ?)
                """,
                (norm, user_id, purpose, code_hash, ttl_seconds, max_attempts, ip, user_agent),
            )
    conn.commit()


def count_email_requests(conn: sqlite3.Connection, *, email: str, hours: int) -> int:
    norm = _normalize_email(email)
    return int(
        conn.execute(
            """
            SELECT COUNT(1)
            FROM auth_email_codes
            WHERE email=? AND created_at > datetime('now', ?||' hours')
            """,
            (norm, -abs(hours)),
        ).fetchone()[0]
    )


def count_ip_requests(conn: sqlite3.Connection, *, ip: str | None, hours: int) -> int:
    if not ip:
        return 0
    return int(
        conn.execute(
            """
            SELECT COUNT(1)
            FROM auth_email_codes
            WHERE created_ip=? AND created_at > datetime('now', ?||' hours')
            """,
            (ip, -abs(hours)),
        ).fetchone()[0]
    )


def verify_email_code(
    conn: sqlite3.Connection,
    *,
    email: str,
    purpose: str,
    input_hash: str,
) -> tuple[bool, Optional[int]]:
    norm = _normalize_email(email)
    row = conn.execute(
        """
        SELECT * FROM auth_email_codes
        WHERE email=? AND purpose=? AND consumed_at IS NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (norm, purpose),
    ).fetchone()
    if not row:
        return False, None
    # Check expiry (if expired, consider invalid)
    exp = row["expires_at"]
    expired = conn.execute("SELECT CASE WHEN ? <= CURRENT_TIMESTAMP THEN 1 ELSE 0 END", (str(exp),)).fetchone()[0]
    if int(expired):
        return False, None
    if row["code_hash"] != input_hash:
        attempts = int(row["attempt_count"] or 0) + 1
        conn.execute("UPDATE auth_email_codes SET attempt_count=? WHERE id=?", (attempts, int(row["id"])))
        try:
            max_attempts = int(row["max_attempts"] or 5)
        except (TypeError, ValueError):
            max_attempts = 5
        if attempts >= max_attempts:
            conn.execute("UPDATE auth_email_codes SET consumed_at=CURRENT_TIMESTAMP WHERE id=?", (int(row["id"]),))
        conn.commit()
        return False, None
    # Success: consume this and invalidate others of same (email,purpose)
    conn.execute("UPDATE auth_email_codes SET consumed_at=CURRENT_TIMESTAMP WHERE id=?", (int(row["id"]),))
    conn.execute(
        "UPDATE auth_email_codes SET consumed_at=CURRENT_TIMESTAMP WHERE email=? AND purpose=? AND consumed_at IS NULL AND id<>?",
        (norm, purpose, int(row["id"]))
    )
    conn.commit()
    uid = row["user_id"]
    return True, (int(uid) if uid is not None else None)


def delete_pipeline(conn: sqlite3.Connection, pid: int) -> None:
    cur = conn.cursor()
    for t in (
        "pipeline_filters",
        "pipeline_writers",
        "pipeline_writer_metric_weights",
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
        "metrics": _list_active_metrics(conn),
    }


def fetch_categories(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, key, label_zh, enabled, created_at, updated_at
        FROM categories
        ORDER BY id
        """
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "key": row["key"],
            "label_zh": row["label_zh"],
            "enabled": int(row["enabled"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def fetch_category(conn: sqlite3.Connection, cid: int) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT id, key, label_zh, enabled, created_at, updated_at
        FROM categories
        WHERE id=?
        """,
        (cid,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "key": row["key"],
        "label_zh": row["label_zh"],
        "enabled": int(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_category(conn: sqlite3.Connection, payload: dict) -> int:
    cur = conn.cursor()
    key = str(payload.get("key") or "").strip()
    label = str(payload.get("label_zh") or "").strip()
    enabled = 1 if int(payload.get("enabled", 1) or 0) else 0
    if not key:
        raise ValueError("类别 key 不能为空")
    if not label:
        raise ValueError("类别名称不能为空")
    cur.execute(
        "INSERT INTO categories (key, label_zh, enabled) VALUES (?, ?, ?)",
        (key, label, enabled),
    )
    conn.commit()
    return int(cur.execute("SELECT last_insert_rowid()").fetchone()[0])


def update_category(conn: sqlite3.Connection, cid: int, payload: dict) -> None:
    cur = conn.cursor()
    existing = fetch_category(conn, cid)
    if not existing:
        raise ValueError("未找到类别")
    new_key = str(payload.get("key") or "").strip() or existing["key"]
    new_label = str(payload.get("label_zh") or existing["label_zh"]).strip()
    if not new_label:
        raise ValueError("类别名称不能为空")
    new_enabled = 1 if int(payload.get("enabled", existing["enabled"]) or 0) else 0
    if new_key != existing["key"]:
        ref_count = cur.execute(
            "SELECT COUNT(1) FROM sources WHERE category_key=?",
            (existing["key"],),
        ).fetchone()[0]
        if ref_count:
            raise ValueError("该类别仍有关联来源，无法修改 key")
    cur.execute(
        """
        UPDATE categories
        SET key=?, label_zh=?, enabled=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (new_key, new_label, new_enabled, cid),
    )
    conn.commit()


def delete_category(conn: sqlite3.Connection, cid: int) -> None:
    cur = conn.cursor()
    existing = fetch_category(conn, cid)
    if not existing:
        raise ValueError("未找到类别")
    key = existing["key"]
    ref_sources = cur.execute(
        "SELECT COUNT(1) FROM sources WHERE category_key=?",
        (key,),
    ).fetchone()[0]
    if ref_sources:
        raise ValueError("该类别仍有关联来源，无法删除")
    ref_info = cur.execute(
        "SELECT COUNT(1) FROM info WHERE category=?",
        (key,),
    ).fetchone()[0]
    if ref_info:
        raise ValueError("该类别仍有关联资讯，无法删除")
    cur.execute("DELETE FROM categories WHERE id=?", (cid,))
    conn.commit()


def fetch_sources(conn: sqlite3.Connection) -> list[dict]:
    addresses_map = _fetch_source_addresses_map(conn)
    rows = conn.execute(
        """
        SELECT s.id, s.key, s.label_zh, s.enabled, s.category_key,
               s.script_path, s.created_at, s.updated_at,
               c.label_zh AS category_label
        FROM sources AS s
        LEFT JOIN categories AS c ON c.key = s.category_key
        ORDER BY s.id
        """
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "key": row["key"],
            "label_zh": row["label_zh"],
            "enabled": int(row["enabled"]),
            "category_key": row["category_key"],
            "script_path": row["script_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "category_label": row["category_label"],
            "addresses": addresses_map.get(int(row["id"]), []),
        }
        for row in rows
    ]


def fetch_source(conn: sqlite3.Connection, sid: int) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT id, key, label_zh, enabled, category_key, script_path,
               created_at, updated_at
        FROM sources
        WHERE id=?
        """,
        (sid,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "key": row["key"],
        "label_zh": row["label_zh"],
        "enabled": int(row["enabled"]),
        "category_key": row["category_key"],
        "script_path": row["script_path"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "addresses": fetch_source_addresses(conn, sid),
    }


def _ensure_category_exists(conn: sqlite3.Connection, category_key: str) -> None:
    row = conn.execute(
        "SELECT 1 FROM categories WHERE key=?",
        (category_key,),
    ).fetchone()
    if not row:
        raise ValueError("关联的类别不存在")


def _normalize_addresses(addresses: Any) -> List[str]:
    if addresses is None:
        return []
    if isinstance(addresses, (str, bytes, bytearray)):
        raw_items: List[Any] = [addresses]
    elif isinstance(addresses, Iterable):
        raw_items = list(addresses)
    else:
        raw_items = [addresses]

    normalized: List[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        if isinstance(raw, (bytes, bytearray)):
            text = raw.decode("utf-8", errors="ignore")
        else:
            text = str(raw)
        text = text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _replace_source_addresses(
    conn: sqlite3.Connection,
    source_id: int,
    addresses: List[str],
) -> None:
    cur = conn.cursor()
    cur.execute("DELETE FROM source_address WHERE source_id=?", (source_id,))
    if addresses:
        cur.executemany(
            "INSERT INTO source_address (source_id, address) VALUES (?, ?)",
            [(source_id, addr) for addr in addresses],
        )


def fetch_source_addresses(conn: sqlite3.Connection, source_id: int) -> List[str]:
    rows = conn.execute(
        """
        SELECT address
        FROM source_address
        WHERE source_id=?
        ORDER BY id
        """,
        (source_id,),
    ).fetchall()
    return [row["address"] for row in rows]


def _fetch_source_addresses_map(conn: sqlite3.Connection) -> Dict[int, List[str]]:
    rows = conn.execute(
        "SELECT source_id, address FROM source_address ORDER BY id"
    ).fetchall()
    mapping: Dict[int, List[str]] = {}
    for row in rows:
        sid = int(row["source_id"])
        mapping.setdefault(sid, []).append(row["address"])
    return mapping


def create_source(conn: sqlite3.Connection, payload: dict) -> int:
    cur = conn.cursor()
    key = str(payload.get("key") or "").strip()
    label = str(payload.get("label_zh") or "").strip()
    category_key = str(payload.get("category_key") or "").strip()
    script_path = str(payload.get("script_path") or "").strip()
    enabled = 1 if int(payload.get("enabled", 1) or 0) else 0
    addresses = _normalize_addresses(payload.get("addresses"))
    if not key:
        raise ValueError("来源 key 不能为空")
    if not label:
        raise ValueError("来源名称不能为空")
    if not category_key:
        raise ValueError("来源所属类别不能为空")
    if not script_path:
        raise ValueError("脚本路径不能为空")
    _ensure_category_exists(conn, category_key)
    cur.execute(
        """
        INSERT INTO sources (key, label_zh, enabled, category_key, script_path)
        VALUES (?, ?, ?, ?, ?)
        """,
        (key, label, enabled, category_key, script_path),
    )
    new_id = int(cur.execute("SELECT last_insert_rowid()").fetchone()[0])
    _replace_source_addresses(conn, new_id, addresses)
    conn.commit()
    return new_id


def update_source(conn: sqlite3.Connection, sid: int, payload: dict) -> None:
    cur = conn.cursor()
    existing = fetch_source(conn, sid)
    if not existing:
        raise ValueError("未找到来源")
    new_key = str(payload.get("key") or "").strip() or existing["key"]
    new_label = str(payload.get("label_zh") or existing["label_zh"]).strip()
    if not new_label:
        raise ValueError("来源名称不能为空")
    new_category_key = str(payload.get("category_key") or existing["category_key"]).strip()
    if not new_category_key:
        raise ValueError("来源所属类别不能为空")
    new_script_path = str(payload.get("script_path") or existing["script_path"]).strip()
    if not new_script_path:
        raise ValueError("脚本路径不能为空")
    new_enabled = 1 if int(payload.get("enabled", existing["enabled"]) or 0) else 0
    _ensure_category_exists(conn, new_category_key)
    cur.execute(
        """
        UPDATE sources
        SET key=?, label_zh=?, enabled=?, category_key=?, script_path=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=?
        """,
        (new_key, new_label, new_enabled, new_category_key, new_script_path, sid),
    )
    if new_key != existing["key"]:
        cur.execute(
            "UPDATE info SET source=? WHERE source=?",
            (new_key, existing["key"]),
        )
    if new_category_key != existing["category_key"]:
        cur.execute(
            "UPDATE info SET category=? WHERE category=?",
            (new_category_key, existing["category_key"]),
        )
    addresses_data = payload.get("addresses", _MISSING)
    if addresses_data is not _MISSING:
        new_addresses = _normalize_addresses(addresses_data)
        _replace_source_addresses(conn, sid, new_addresses)
    conn.commit()


def delete_source(conn: sqlite3.Connection, sid: int) -> None:
    cur = conn.cursor()
    existing = fetch_source(conn, sid)
    if not existing:
        raise ValueError("未找到来源")
    cur.execute("DELETE FROM source_address WHERE source_id=?", (sid,))
    cur.execute("DELETE FROM sources WHERE id=?", (sid,))
    conn.commit()


def fetch_info_list(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
    category: Optional[str] = None,
    source: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    clauses: List[str] = []
    params: List[Any] = []
    if category:
        cat_key = category.strip()
        if cat_key:
            clauses.append("i.category = ?")
            params.append(cat_key)
    if source:
        src_key = source.strip()
        if src_key:
            clauses.append("i.source = ?")
            params.append(src_key)
    term = search.strip() if isinstance(search, str) else ""
    if term:
        clauses.append("(i.title LIKE ? OR i.link LIKE ?)")
        like = f"%{term}%"
        params.extend([like, like])
    where_clause = "WHERE " + " AND ".join(clauses) if clauses else ""
    total = conn.execute(
        f"SELECT COUNT(1) FROM info AS i {where_clause}",
        params,
    ).fetchone()[0]
    rows = conn.execute(
        f"""
        SELECT i.id,
               i.title,
               i.source,
               COALESCE(src.label_zh, i.source) AS source_label,
               i.category,
               COALESCE(cat.label_zh, i.category) AS category_label,
               i.publish,
               i.link,
               r.final_score,
               r.updated_at AS review_updated_at
        FROM info AS i
        LEFT JOIN sources AS src ON src.key = i.source
        LEFT JOIN categories AS cat ON cat.key = i.category
        LEFT JOIN info_ai_review AS r ON r.info_id = i.id
        {where_clause}
        ORDER BY i.publish DESC, i.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()
    items = [
        {
            "id": int(row["id"]),
            "title": row["title"],
            "source": row["source"],
            "source_label": row["source_label"],
            "category": row["category"],
            "category_label": row["category_label"],
            "publish": row["publish"],
            "link": row["link"],
            "final_score": float(row["final_score"]) if row["final_score"] is not None else None,
            "review_updated_at": row["review_updated_at"],
        }
        for row in rows
    ]
    return {"items": items, "total": int(total)}


def fetch_info_detail(conn: sqlite3.Connection, info_id: int) -> Optional[dict]:
    row = conn.execute(
        """
        SELECT i.id, i.title, i.source, i.category, i.publish, i.link, i.detail,
               COALESCE(src.label_zh, i.source) AS source_label,
               COALESCE(cat.label_zh, i.category) AS category_label
        FROM info AS i
        LEFT JOIN sources AS src ON src.key = i.source
        LEFT JOIN categories AS cat ON cat.key = i.category
        WHERE i.id = ?
        """,
        (info_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "title": row["title"],
        "source": row["source"],
        "source_label": row["source_label"],
        "category": row["category"],
        "category_label": row["category_label"],
        "publish": row["publish"],
        "link": row["link"],
        "detail": row["detail"],
    }


def fetch_info_ai_review(conn: sqlite3.Connection, info_id: int) -> dict:
    review = conn.execute(
        """
        SELECT final_score,
               ai_comment,
               ai_summary,
               ai_key_concepts,
               ai_summary_long,
               raw_response,
               updated_at,
               created_at
        FROM info_ai_review
        WHERE info_id=?
        """,
        (info_id,),
    ).fetchone()
    scores = conn.execute(
        """
        SELECT m.key, m.label_zh, s.score
        FROM info_ai_scores AS s
        JOIN ai_metrics AS m ON m.id = s.metric_id
        WHERE s.info_id=?
        ORDER BY m.sort_order ASC, m.id ASC
        """,
        (info_id,),
    ).fetchall()
    concepts: list[str] = []
    if review:
        raw_concepts = review["ai_key_concepts"]
        if isinstance(raw_concepts, (bytes, bytearray)):
            raw_concepts = raw_concepts.decode("utf-8", errors="ignore")
        if raw_concepts:
            if isinstance(raw_concepts, str):
                text = raw_concepts.strip()
            else:
                text = str(raw_concepts)
            if text:
                try:
                    parsed = json.loads(text)
                except (json.JSONDecodeError, TypeError):
                    normalized = text.replace("，", ",").replace("、", ",").replace(";", ",")
                    concepts = [item.strip() for item in normalized.split(",") if item.strip()]
                else:
                    if isinstance(parsed, list):
                        concepts = [str(item).strip() for item in parsed if str(item).strip()]
                    elif isinstance(parsed, str):
                        normalized = parsed.replace("，", ",").replace("、", ",").replace(";", ",")
                        concepts = [item.strip() for item in normalized.split(",") if item.strip()]
                    else:
                        concepts = []

    return {
        "final_score": float(review["final_score"]) if review and review["final_score"] is not None else None,
        "ai_comment": review["ai_comment"] if review else None,
        "ai_summary": review["ai_summary"] if review else None,
        "ai_key_concepts": concepts,
        "ai_summary_long": review["ai_summary_long"] if review else None,
        "raw_response": review["raw_response"] if review else None,
        "updated_at": review["updated_at"] if review else None,
        "created_at": review["created_at"] if review else None,
        "scores": [
            {
                "metric_key": row["key"],
                "metric_label": row["label_zh"],
                "score": int(row["score"]),
            }
            for row in scores
        ],
    }


def fetch_ai_metrics(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, key, label_zh, rate_guide_zh, default_weight, active, sort_order, created_at, updated_at
        FROM ai_metrics
        ORDER BY sort_order ASC, id ASC
        """
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "key": row["key"],
            "label_zh": row["label_zh"],
            "rate_guide_zh": row["rate_guide_zh"],
            "default_weight": float(row["default_weight"]) if row["default_weight"] is not None else None,
            "active": int(row["active"]),
            "sort_order": int(row["sort_order"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def create_ai_metric(conn: sqlite3.Connection, payload: dict) -> int:
    cur = conn.cursor()
    key = str(payload.get("key") or "").strip()
    label = str(payload.get("label_zh") or "").strip()
    rate_guide = payload.get("rate_guide_zh")
    default_weight = payload.get("default_weight")
    sort_order = payload.get("sort_order", 0)
    active = 1 if int(payload.get("active", 1) or 0) else 0
    if not key:
        raise ValueError("指标 key 不能为空")
    if not label:
        raise ValueError("指标名称不能为空")
    weight_value = None
    if default_weight is not None and str(default_weight).strip() != "":
        try:
            weight_value = float(default_weight)
        except (TypeError, ValueError):
            raise ValueError("默认权重需要是数值")
    try:
        sort_value = int(sort_order)
    except (TypeError, ValueError):
        sort_value = 0
    cur.execute(
        """
        INSERT INTO ai_metrics (key, label_zh, rate_guide_zh, default_weight, active, sort_order)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (key, label, rate_guide, weight_value, active, sort_value),
    )
    conn.commit()
    return int(cur.execute("SELECT last_insert_rowid()").fetchone()[0])


def update_ai_metric(conn: sqlite3.Connection, metric_id: int, payload: dict) -> None:
    cur = conn.cursor()
    existing = cur.execute(
        "SELECT id, key FROM ai_metrics WHERE id=?",
        (metric_id,),
    ).fetchone()
    if not existing:
        raise ValueError("未找到指标")
    label = payload.get("label_zh")
    rate_guide = payload.get("rate_guide_zh")
    default_weight = payload.get("default_weight")
    sort_order = payload.get("sort_order")
    active = payload.get("active")
    updates: List[str] = []
    params: List[Any] = []
    if label is not None:
        name = str(label).strip()
        if not name:
            raise ValueError("指标名称不能为空")
        updates.append("label_zh=?")
        params.append(name)
    if rate_guide is not None:
        updates.append("rate_guide_zh=?")
        params.append(rate_guide)
    if default_weight is not None:
        if str(default_weight).strip() == "":
            updates.append("default_weight=?")
            params.append(None)
        else:
            try:
                weight_value = float(default_weight)
            except (TypeError, ValueError):
                raise ValueError("默认权重需要是数值")
            updates.append("default_weight=?")
            params.append(weight_value)
    if sort_order is not None:
        try:
            sort_value = int(sort_order)
        except (TypeError, ValueError):
            raise ValueError("排序需要是整数")
        updates.append("sort_order=?")
        params.append(sort_value)
    if active is not None:
        active_flag = 1 if int(active or 0) else 0
        updates.append("active=?")
        params.append(active_flag)
    if not updates:
        return
    updates.append("updated_at=CURRENT_TIMESTAMP")
    cur.execute(
        f"UPDATE ai_metrics SET {', '.join(updates)} WHERE id=?",
        [*params, metric_id],
    )
    conn.commit()


def delete_ai_metric(conn: sqlite3.Connection, metric_id: int) -> None:
    cur = conn.cursor()
    existing = cur.execute("SELECT id FROM ai_metrics WHERE id=?", (metric_id,)).fetchone()
    if not existing:
        raise ValueError("未找到指标")
    refs_scores = cur.execute(
        "SELECT COUNT(1) FROM info_ai_scores WHERE metric_id=?",
        (metric_id,),
    ).fetchone()[0]
    if refs_scores:
        raise ValueError("仍有关联的资讯评分记录，无法删除")
    refs_weights = cur.execute(
        "SELECT COUNT(1) FROM pipeline_writer_metric_weights WHERE metric_id=?",
        (metric_id,),
    ).fetchone()[0]
    if refs_weights:
        raise ValueError("仍有关联的投递配置指标，无法删除")
    cur.execute("DELETE FROM ai_metrics WHERE id=?", (metric_id,))
    conn.commit()
