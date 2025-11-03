from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "info.db"

"""Weekday helpers import
Support both package execution (python -m ...) and script execution
(python path/to/file.py) by falling back to file-location import.
"""
try:
    from .weekday import coerce as weekday_coerce, normalize as weekday_normalize
except Exception:
    try:
        import importlib.util as _importlib_util
        _wk_path = Path(__file__).with_name("weekday.py")
        _spec = _importlib_util.spec_from_file_location("_admin_weekday", str(_wk_path))
        if _spec and _spec.loader:
            _mod = _importlib_util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)  # type: ignore[arg-type]
            weekday_coerce = _mod.coerce  # type: ignore[attr-defined]
            weekday_normalize = _mod.normalize  # type: ignore[attr-defined]
        else:  # pragma: no cover
            raise ImportError("cannot load weekday helpers")
    except Exception as _e:  # pragma: no cover
        raise SystemExit(f"Failed to load weekday helpers: {_e}")


SCHEMA_SQL = """
-- 管线定义
CREATE TABLE IF NOT EXISTS pipelines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL UNIQUE,
  enabled      INTEGER NOT NULL DEFAULT 1,
  -- 允许运行的星期（ISO 1-7）；NULL 表示不限制
  weekdays_json TEXT,
  description  TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 过滤条件
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

-- Email 投递（单管线单投递）
CREATE TABLE IF NOT EXISTS pipeline_deliveries_email (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  email        TEXT NOT NULL,
  subject_tpl  TEXT NOT NULL,
  deliver_type TEXT NOT NULL DEFAULT 'email',
  UNIQUE(pipeline_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

-- Feishu 投递（统一 feishu_card，单管线单投递）
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

-- 运行记录（可选）
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


def ensure_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.executescript(SCHEMA_SQL)
        cur = conn.cursor()
        # Ensure pipelines.weekdays_json exists (idempotent)
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='pipelines'")
        if cur.fetchone():
            cur.execute("PRAGMA table_info(pipelines)")
            p_cols = {row[1] for row in cur.fetchall()}
            if "weekdays_json" not in p_cols:
                cur.execute("ALTER TABLE pipelines ADD COLUMN weekdays_json TEXT")
        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_writers'"
        )
        if cur.fetchone():
            cur.execute("PRAGMA table_info(pipeline_writers)")
            existing_cols = {row[1] for row in cur.fetchall()}
            if "limit_per_category" not in existing_cols:
                cur.execute("ALTER TABLE pipeline_writers ADD COLUMN limit_per_category TEXT")
            if "per_source_cap" not in existing_cols:
                cur.execute("ALTER TABLE pipeline_writers ADD COLUMN per_source_cap INTEGER")
        # Enforce uniqueness for single-row tables keyed by pipeline_id.
        # First, dedupe keeping latest row (max rowid), then create unique indexes.
        try:
            # Dedupe pipeline_writers
            dup_w = cur.execute(
                "SELECT pipeline_id, COUNT(*) FROM pipeline_writers GROUP BY pipeline_id HAVING COUNT(*)>1"
            ).fetchall()
            for pid, _ in dup_w:
                keep = cur.execute(
                    "SELECT MAX(rowid) FROM pipeline_writers WHERE pipeline_id=?",
                    (pid,),
                ).fetchone()[0]
                cur.execute(
                    "DELETE FROM pipeline_writers WHERE pipeline_id=? AND rowid<>?",
                    (pid, keep),
                )
            # Dedupe pipeline_filters
            dup_f = cur.execute(
                "SELECT pipeline_id, COUNT(*) FROM pipeline_filters GROUP BY pipeline_id HAVING COUNT(*)>1"
            ).fetchall()
            for pid, _ in dup_f:
                keep = cur.execute(
                    "SELECT MAX(rowid) FROM pipeline_filters WHERE pipeline_id=?",
                    (pid,),
                ).fetchone()[0]
                cur.execute(
                    "DELETE FROM pipeline_filters WHERE pipeline_id=? AND rowid<>?",
                    (pid, keep),
                )
            # Unique indexes (idempotent)
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_writers_pipeline_id ON pipeline_writers(pipeline_id)"
            )
            cur.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_filters_pipeline_id ON pipeline_filters(pipeline_id)"
            )
        except sqlite3.DatabaseError:
            # Best effort; ignore if tables missing during early bootstrap
            pass
        conn.commit()


def cmd_init(_: argparse.Namespace) -> None:
    ensure_db()
    print(f"Initialized pipeline tables in {DB_PATH}")


def pipeline_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute("SELECT 1 FROM pipelines WHERE name=?", (name,)).fetchone()
    return bool(row)


def insert_pipeline(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    enabled: int = 1,
) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO pipelines (name, enabled, description) VALUES (?, ?, ?)",
        (name, int(enabled), description),
    )
    row = conn.execute("SELECT id FROM pipelines WHERE name=?", (name,)).fetchone()
    return int(row[0])


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
        for key, val in value.items():
            if key is None:
                continue
            key_str = str(key).strip()
            if not key_str:
                continue
            try:
                int_val = int(val)
            except (TypeError, ValueError):
                continue
            result[key_str] = int_val
        return result
    if isinstance(value, list):
        # Unsupported structure; ignore silently
        return None
    return None


def _limit_map_to_json(limit_map: Optional[Dict[str, int]]) -> Optional[str]:
    if limit_map is None:
        return None
    return json.dumps(limit_map, ensure_ascii=False)


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
                print(f"[WARN] 跳过未知指标权重 key={key!r}")
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


def _export_one(conn: sqlite3.Connection, pid: int) -> dict:
    cur = conn.cursor()
    # Base fields
    prow = cur.execute(
        "SELECT id, name, enabled, COALESCE(description,'') FROM pipelines WHERE id=?",
        (pid,)
    ).fetchone()
    if not prow:
        return {}
    pid, name, enabled, desc = int(prow[0]), str(prow[1]), int(prow[2]), str(prow[3])
    # Optional weekdays_json
    weekdays: Optional[list[int]] = None
    try:
        wrow = cur.execute(
            "SELECT weekdays_json FROM pipelines WHERE id=?",
            (pid,),
        ).fetchone()
        if wrow and wrow[0] is not None:
            arr = weekday_coerce(wrow[0])
            weekdays = weekday_normalize(arr)
    except sqlite3.OperationalError:
        # Column missing; ignore for backward compatibility
        weekdays = None
    # filters
    frow = cur.execute(
        "SELECT all_categories, categories_json, all_src, include_src_json FROM pipeline_filters WHERE pipeline_id=?",
        (pid,)
    ).fetchone()
    filters = {}
    if frow:
        filters = {
            "all_categories": int(frow[0]) if frow[0] is not None else 1,
            "categories_json": frow[1] if frow[1] is not None else None,
            "all_src": int(frow[2]) if frow[2] is not None else 1,
            "include_src_json": frow[3] if frow[3] is not None else None,
        }
    # writer
    wrow = cur.execute(
        "SELECT type, hours, COALESCE(weights_json,''), COALESCE(bonus_json,''), limit_per_category, per_source_cap FROM pipeline_writers WHERE pipeline_id=?",
        (pid,)
    ).fetchone()
    writer = {}
    if wrow:
        limit_map = _normalize_limit_map(wrow[4])
        weights_raw = str(wrow[2] or "") or None
        weights_payload: Optional[Any] = None
        if weights_raw:
            normalized = _normalize_weights_json(conn, weights_raw)
            if normalized:
                try:
                    weights_payload = json.loads(normalized)
                except json.JSONDecodeError:
                    weights_payload = normalized
        writer = {
            "type": str(wrow[0] or ""),
            "hours": int(wrow[1] or 24),
            "weights_json": weights_payload,
            "bonus_json": str(wrow[3] or "") or None,
            "limit_per_category": limit_map,
            "per_source_cap": int(wrow[5]) if wrow[5] is not None else None,
        }
        mrows = cur.execute(
            """
            SELECT m.key, w.weight, w.enabled
            FROM pipeline_writer_metric_weights AS w
            JOIN ai_metrics AS m ON m.id = w.metric_id
            WHERE w.pipeline_id=?
            ORDER BY m.sort_order ASC, m.id ASC
            """,
            (pid,),
        ).fetchall()
        if mrows:
            writer["metric_weights"] = [
                {"key": str(r[0]), "weight": float(r[1]), "enabled": int(r[2] or 0)}
                for r in mrows
            ]
    # delivery (email or feishu)
    drow = cur.execute(
        "SELECT email, subject_tpl FROM pipeline_deliveries_email WHERE pipeline_id=?",
        (pid,)
    ).fetchone()
    if drow:
        delivery = {
            "kind": "email",
            "email": str(drow[0] or ""),
            "subject_tpl": str(drow[1] or ""),
        }
    else:
        drow = cur.execute(
            "SELECT app_id, app_secret, to_all_chat, chat_id, COALESCE(title_tpl,''), to_all, COALESCE(content_json,'') FROM pipeline_deliveries_feishu WHERE pipeline_id=?",
            (pid,),
        ).fetchone()
        if drow:
            delivery = {
                "kind": "feishu",
                "app_id": str(drow[0] or ""),
                "app_secret": str(drow[1] or ""),
                "to_all_chat": int(drow[2] or 0),
                "chat_id": (str(drow[3] or "") or None),
                "title_tpl": str(drow[4] or ""),
                "to_all": int(drow[5] or 0),
                "content_json": (str(drow[6] or "") or None),
            }
        else:
            delivery = {}
    # expose id as well so exports/imports can optionally match by id
    return {
        "pipeline": {"id": pid, "name": name, "enabled": enabled, "description": desc, "weekdays_json": weekdays},
        "filters": filters,
        "writer": writer,
        "delivery": delivery,
    }


def cmd_export(args: argparse.Namespace) -> None:
    ensure_db()
    out_path: Path
    if args.output:
        out_path = Path(args.output)
    else:
        outdir = DATA_DIR / "pipelines"
        outdir.mkdir(parents=True, exist_ok=True)
        out_path = outdir / (datetime.now().strftime("export-%Y%m%d-%H%M%S.json"))

    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.cursor()
        pids: list[int] = []
        if args.name:
            row = cur.execute("SELECT id FROM pipelines WHERE name=?", (args.name,)).fetchone()
            if not row:
                raise SystemExit(f"pipeline 不存在: {args.name}")
            pids = [int(row[0])]
        else:
            rows = cur.execute("SELECT id FROM pipelines ORDER BY id").fetchall()
            pids = [int(r[0]) for r in rows]

        items = [_export_one(conn, pid) for pid in pids]
        payload = {"version": 1, "pipelines": items}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Exported {len(items)} pipeline(s) to {out_path}")


def _get_or_create_pipeline(conn: sqlite3.Connection, name: str, enabled: int, description: str, mode: str) -> int:
    cur = conn.cursor()
    row = cur.execute("SELECT id FROM pipelines WHERE name=?", (name,)).fetchone()
    if row:
        pid = int(row[0])
        if mode == "replace":
            # Clear children
            for t in (
                "pipeline_filters",
                "pipeline_writers",
                "pipeline_writer_metric_weights",
                "pipeline_deliveries_email",
                "pipeline_deliveries_feishu",
            ):
                cur.execute(f"DELETE FROM {t} WHERE pipeline_id=?", (pid,))
        # Update base fields
        cur.execute(
            "UPDATE pipelines SET enabled=?, description=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (int(enabled), description, pid),
        )
        return pid
    # Create
    cur.execute(
        "INSERT INTO pipelines (name, enabled, description) VALUES (?, ?, ?)",
        (name, int(enabled), description),
    )
    return int(cur.execute("SELECT last_insert_rowid()").fetchone()[0])


def cmd_import(args: argparse.Namespace) -> None:
    ensure_db()
    in_path = Path(args.input)
    if not in_path.exists():
        raise SystemExit(f"输入文件不存在: {in_path}")
    try:
        payload = json.loads(in_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"JSON 解析失败: {e}")
    items = payload.get("pipelines") or []
    if not isinstance(items, list) or not items:
        raise SystemExit("导入数据为空或格式不正确 (missing pipelines array)")

    def _to_json_text(val):
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            return json.dumps(val, ensure_ascii=False)
        s = str(val)
        return s

    def _to_optional_int(val):
        if val is None:
            return None
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, (int, float)):
            return int(val)
        try:
            return int(str(val).strip())
        except (TypeError, ValueError):
            return None

    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.cursor()
        for it in items:
            meta = it.get("pipeline") or {}
            name = str(meta.get("name") or "").strip()
            raw_id = meta.get("id")
            if not name:
                print("[SKIP] 缺少 pipeline.name")
                continue
            # Respect explicit zeros: only use default when key missing
            enabled = int(meta["enabled"]) if ("enabled" in meta and meta.get("enabled") is not None) else 1
            desc = str(meta.get("description") or "")
            raw_weekdays = meta.get("weekdays_json")
            weekdays_norm: Optional[str]
            if raw_weekdays is None or raw_weekdays == "":
                weekdays_norm = None
            else:
                arr = weekday_coerce(raw_weekdays)
                # Warn on non-array inputs during transition
                if isinstance(raw_weekdays, (str, bytes, bytearray)):
                    print("[WARN] weekdays_json provided as string; coerced for compatibility")
                days = weekday_normalize(arr) or []
                weekdays_norm = json.dumps(days, ensure_ascii=False)
            # Prefer matching by id when provided and valid; otherwise, fall back to name
            pid: Optional[int] = None
            if isinstance(raw_id, int):
                row = cur.execute("SELECT id, name FROM pipelines WHERE id=?", (raw_id,)).fetchone()
                if row:
                    pid = int(row[0])
                    # Update name if different
                    db_name = str(row[1] or "")
                    if name and name != db_name:
                        try:
                            cur.execute(
                                "UPDATE pipelines SET name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                (name, pid),
                            )
                        except sqlite3.IntegrityError:
                            # Name conflict; fall back to name-based resolution below
                            pid = None
            if pid is None:
                pid = _get_or_create_pipeline(conn, name, enabled, desc, args.mode)
            else:
                # id path: honor replace semantics for child rows
                if args.mode == "replace":
                    for t in (
                        "pipeline_filters",
                        "pipeline_writers",
                        "pipeline_writer_metric_weights",
                        "pipeline_deliveries_email",
                        "pipeline_deliveries_feishu",
                    ):
                        cur.execute(f"DELETE FROM {t} WHERE pipeline_id=?", (pid,))
                # Ensure base fields up-to-date
                cur.execute(
                    "UPDATE pipelines SET enabled=?, description=?, updated_at=CURRENT_TIMESTAMP, weekdays_json=? WHERE id=?",
                    (int(enabled), desc, weekdays_norm, pid),
                )
            # When created via _get_or_create_pipeline, also set weekdays_json (since base helper ignores it)
            if pid is not None and weekdays_norm is not None:
                cur.execute(
                    "UPDATE pipelines SET weekdays_json=? WHERE id=?",
                    (weekdays_norm, pid),
                )

            # filters
            f = it.get("filters") or {}
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
            w = it.get("writer") or {}
            limit_map = _normalize_limit_map(w.get("limit_per_category"))
            weights_json_norm = _normalize_weights_json(conn, w.get("weights_json"))
            cur.execute(
                "INSERT OR REPLACE INTO pipeline_writers (pipeline_id, type, hours, weights_json, bonus_json, limit_per_category, per_source_cap) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    pid,
                    str(w.get("type") or ""),
                    # Respect explicit zeros; default only when key missing
                    int(w["hours"]) if ("hours" in w and w.get("hours") is not None) else 24,
                    weights_json_norm,
                    _to_json_text(w.get("bonus_json")),
                    _limit_map_to_json(limit_map),
                    _to_optional_int(w.get("per_source_cap")),
                ),
            )
            metric_weights = w.get("metric_weights")
            if args.mode == "replace":
                cur.execute("DELETE FROM pipeline_writer_metric_weights WHERE pipeline_id=?", (pid,))
            if isinstance(metric_weights, list):
                rows_to_insert: List[Tuple[int, int, float, int]] = []
                for mw in metric_weights:
                    if not isinstance(mw, dict):
                        continue
                    metric_id = _resolve_metric_id(conn, mw.get("key"))
                    if metric_id is None:
                        print(f"[WARN] {name}: metric_weights 跳过未知指标 {mw.get('key')!r}")
                        continue
                    try:
                        weight_val = float(mw.get("weight"))
                    except (TypeError, ValueError):
                        print(f"[WARN] {name}: metric_weights 无效权重 {mw.get('weight')!r}")
                        continue
                    enabled_flag = 1 if int(mw.get("enabled", 1) or 0) else 0
                    rows_to_insert.append((pid, metric_id, weight_val, enabled_flag))
                if rows_to_insert:
                    cur.executemany(
                        """
                        INSERT OR REPLACE INTO pipeline_writer_metric_weights (pipeline_id, metric_id, weight, enabled)
                        VALUES (?, ?, ?, ?)
                        """,
                        rows_to_insert,
                    )
            elif metric_weights:
                print(f"[WARN] {name}: metric_weights 需为列表，已忽略")
            # delivery
            d = it.get("delivery") or {}
            kind = str(d.get("kind") or "").strip().lower()
            if kind == "email":
                # Ensure feishu cleared if replace
                if args.mode == "replace":
                    cur.execute("DELETE FROM pipeline_deliveries_feishu WHERE pipeline_id=?", (pid,))
                cur.execute(
                    "INSERT OR REPLACE INTO pipeline_deliveries_email (pipeline_id, email, subject_tpl) VALUES (?, ?, ?)",
                    (
                        pid,
                        str(d.get("email") or ""),
                        str(d.get("subject_tpl") or ""),
                    ),
                )
            elif kind == "feishu":
                if args.mode == "replace":
                    cur.execute("DELETE FROM pipeline_deliveries_email WHERE pipeline_id=?", (pid,))
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
            else:
                print(f"[WARN] {name}: 未识别的 delivery.kind，已跳过该投递")

        conn.commit()
    print(f"Imported {len(items)} pipeline(s) from {in_path} (mode={args.mode})")


def list_pipelines(_: argparse.Namespace) -> None:
    ensure_db()
    with sqlite3.connect(str(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT id, name, enabled, COALESCE(description,'') FROM pipelines ORDER BY id"
        ).fetchall()
        if not rows:
            print("(no pipelines)")
            return
        for r in rows:
            print(f"{r[0]}\t{r[1]}\tenabled={r[2]}\t{r[3]}")


def cmd_seed(_: argparse.Namespace) -> None:
    """Seed three example pipelines: two email and one Feishu broadcast."""
    ensure_db()
    with sqlite3.connect(str(DB_PATH)) as conn:
        # 1) Email to 306483372@qq.com using info_writer (40h)
        p1 = insert_pipeline(conn, "email_306483372", "每日资讯 HTML 发给 306483372@qq.com", 1)
        # Filters: take all categories/sources
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_filters (pipeline_id, all_categories, categories_json, all_src, include_src_json) VALUES (?, 1, NULL, 1, NULL)",
            (p1,),
        )
        # Writer: info_html 40h
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_writers (pipeline_id, type, hours, weights_json, bonus_json, limit_per_category, per_source_cap) VALUES (?, ?, ?, NULL, NULL, NULL, NULL)",
            (p1, "info_html", 40),
        )
        # Delivery: email single recipient
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_deliveries_email (pipeline_id, email, subject_tpl) VALUES (?, ?, ?)",
            (p1, "306483372@qq.com", "${date_zh}整合"),
        )

        # 2) Email to 410861858@qq.com using unified email_writer (24h)
        p2 = insert_pipeline(conn, "email_410861858_wenhao", "WH 精选发给 410861858@qq.com", 1)
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_filters (pipeline_id, all_categories, categories_json, all_src, include_src_json) VALUES (?, 1, NULL, 1, NULL)",
            (p2,),
        )
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_writers (pipeline_id, type, hours, weights_json, bonus_json, limit_per_category, per_source_cap) VALUES (?, ?, ?, NULL, NULL, NULL, NULL)",
            (p2, "info_html", 24),
        )
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_deliveries_email (pipeline_id, email, subject_tpl) VALUES (?, ?, ?)",
            (p2, "410861858@qq.com", "HW精选"),
        )

        # 3) Feishu broadcast using feishu_writer (40h), to all chats
        p3 = insert_pipeline(conn, "feishu_broadcast", "飞书卡片群发（所有所在群）", 1)
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_filters (pipeline_id, all_categories, categories_json, all_src, include_src_json) VALUES (?, 0, ?, 1, NULL)",
            (
                p3,
                json.dumps(["game", "tech"], ensure_ascii=False),
            ),
        )
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_writers (pipeline_id, type, hours, weights_json, bonus_json, limit_per_category, per_source_cap) VALUES (?, ?, ?, NULL, NULL, ?, 3)",
            (p3, "feishu_md", 40, json.dumps({"default": 10}, ensure_ascii=False)),
        )
        # App credentials from environment.yml (as requested); set to_all_chat=1
        conn.execute(
            "INSERT OR REPLACE INTO pipeline_deliveries_feishu (pipeline_id, app_id, app_secret, to_all_chat, chat_id, title_tpl, to_all, content_json) VALUES (?, ?, ?, 1, NULL, ?, 1, NULL)",
            (
                p3,
                "cli_a875d3094efed00d",
                "LR7tbUTyaW7FC2DMVUzLCctPTzUNJWd5",
                "24小时最新情报",
            ),
        )

        conn.commit()
    print("Seeded 3 pipelines: email_306483372, email_410861858_wenhao, feishu_broadcast")


def cmd_enable_disable(args: argparse.Namespace) -> None:
    ensure_db()
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute("UPDATE pipelines SET enabled=? WHERE name=?", (1 if args.enable else 0, args.name))
        conn.commit()
    print(f"{args.name}: enabled={args.enable}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Admin CLI for write/deliver pipelines (DB-backed)")
    sub = p.add_subparsers(dest="cmd", required=True)

    s_init = sub.add_parser("init", help="Create pipeline tables if missing")
    s_init.set_defaults(func=cmd_init)

    s_list = sub.add_parser("list", help="List pipelines")
    s_list.set_defaults(func=list_pipelines)

    s_seed = sub.add_parser("seed", help="Insert three example pipelines (2 email + 1 feishu)")
    s_seed.set_defaults(func=cmd_seed)

    s_en = sub.add_parser("enable", help="Enable a pipeline by name")
    s_en.add_argument("name")
    s_en.set_defaults(func=lambda a: cmd_enable_disable(argparse.Namespace(name=a.name, enable=True)))

    s_dis = sub.add_parser("disable", help="Disable a pipeline by name")
    s_dis.add_argument("name")
    s_dis.set_defaults(func=lambda a: cmd_enable_disable(argparse.Namespace(name=a.name, enable=False)))

    # export: dump one or all pipelines and their related configs to JSON
    s_exp = sub.add_parser("export", help="Export pipeline(s) to JSON")
    g = s_exp.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", default="", help="Pipeline name to export")
    g.add_argument("--all", action="store_true", help="Export all pipelines")
    s_exp.add_argument("--output", default="", help="Output JSON path (default: data/pipelines/export-YYYYMMDD-HHMMSS.json)")
    s_exp.set_defaults(func=cmd_export)

    # import: read JSON and create/replace pipelines in DB
    s_imp = sub.add_parser("import", help="Import pipeline(s) from JSON")
    s_imp.add_argument("--input", required=True, help="Input JSON file path")
    s_imp.add_argument("--mode", choices=["replace", "merge"], default="replace", help="replace: drop existing pipeline with same name before import; merge: overwrite parts")
    s_imp.set_defaults(func=cmd_import)

    return p.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
