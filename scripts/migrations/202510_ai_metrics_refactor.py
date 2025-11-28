from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DEFAULT_DB = DATA_DIR / "info.db"

DEFAULT_METRICS: List[Tuple[str, str, str, float, int]] = [
    (
        "rok_cod_fit",
        "ROK/COD 副玩法结合可能性",
        "5-高度可行；3-有限可行；1-不合适",
        1.0,
        10,
    ),
    (
        "timeliness",
        "时效性",
        "5-当天/最新；3-一月内或时间无关（长期有价值）；1-过时",
        0.14,
        10,
    ),
    (
        "game_relevance",
        "游戏相关性",
        "5-核心聚焦游戏议题/数据/案例；3-泛娱乐与游戏相关；1-无关",
        0.20,
        20,
    ),
    (
        "mobile_game_relevance",
        "手游相关性",
        "5-聚焦手游（产品/发行/买量/市场数据）；3-部分相关；1-无关",
        0.09,
        30,
    ),
    (
        "ai_relevance",
        "AI相关性",
        "5-模型/算法/评测/标杆案例；3-泛AI应用；1-无关",
        0.14,
        40,
    ),
    (
        "tech_relevance",
        "科技相关性",
        "5-芯片/云/硬件/基础设施；3-泛科技商业动态；1-无关",
        0.11,
        50,
    ),
    (
        "quality",
        "文章质量",
        "5-结构严谨数据充分；3-结构一般信息适中；1-水文/缺依据",
        0.13,
        60,
    ),
    (
        "insight",
        "洞察力",
        "5-罕见且深刻的观点/关联/因果；3-常见分析；1-无洞见",
        0.08,
        70,
    ),
    (
        "depth",
        "深度",
        "5-分层拆解背景充分逻辑完整；3-覆盖关键事实；1-浅尝辄止",
        0.06,
        80,
    ),
    (
        "novelty",
        "新颖度",
        "5-罕见消息或独到观点；3-常见进展/整合；1-无新意",
        0.05,
        90,
    ),
]

LEGACY_COLUMNS = {
    "timeliness_score": "timeliness",
    "game_relevance_score": "game_relevance",
    "mobile_game_relevance_score": "mobile_game_relevance",
    "ai_relevance_score": "ai_relevance",
    "tech_relevance_score": "tech_relevance",
    "quality_score": "quality",
    "insight_score": "insight",
    "depth_score": "depth",
    "novelty_score": "novelty",
}


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_metrics (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            key            TEXT NOT NULL UNIQUE,
            label_zh       TEXT NOT NULL,
            rate_guide_zh  TEXT,
            default_weight REAL,
            active         INTEGER NOT NULL DEFAULT 1,
            sort_order     INTEGER NOT NULL DEFAULT 0,
            created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_ai_metrics_active
        ON ai_metrics (active, sort_order);

        CREATE TABLE IF NOT EXISTS info_ai_scores (
            info_id    INTEGER NOT NULL,
            metric_id  INTEGER NOT NULL,
            score      INTEGER NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (info_id, metric_id),
            FOREIGN KEY (info_id) REFERENCES info(id),
            FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
        );

        CREATE INDEX IF NOT EXISTS idx_info_ai_scores_info
        ON info_ai_scores (info_id);

        CREATE INDEX IF NOT EXISTS idx_info_ai_scores_metric
        ON info_ai_scores (metric_id);

        CREATE TABLE IF NOT EXISTS info_ai_review (
            info_id     INTEGER PRIMARY KEY,
            final_score REAL    NOT NULL DEFAULT 0.0,
            ai_comment  TEXT    NOT NULL,
            ai_summary  TEXT    NOT NULL,
            raw_response TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (info_id) REFERENCES info(id)
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
        """
    )
    conn.commit()


def seed_metrics(conn: sqlite3.Connection) -> None:
    existing = {row[0] for row in conn.execute("SELECT key FROM ai_metrics")}
    inserts = [row for row in DEFAULT_METRICS if row[0] not in existing]
    if inserts:
        conn.executemany(
            """
            INSERT INTO ai_metrics (key, label_zh, rate_guide_zh, default_weight, sort_order)
            VALUES (?, ?, ?, ?, ?)
            """,
            inserts,
        )
        conn.commit()


def migrate_scores(conn: sqlite3.Connection) -> None:
    # Only attempt migration when legacy score columns exist
    columns = {row[1] for row in conn.execute("PRAGMA table_info(info_ai_review)").fetchall()}
    legacy_cols = [col for col in LEGACY_COLUMNS if col in columns]
    if not legacy_cols:
        return

    metric_rows = conn.execute("SELECT id, key FROM ai_metrics").fetchall()
    metric_index: Dict[str, int] = {key: mid for mid, key in metric_rows}
    if not metric_index:
        raise SystemExit("ai_metrics 表为空，无法迁移历史得分")

    rows = conn.execute(
        f"""
        SELECT info_id, {', '.join(legacy_cols)}
        FROM info_ai_review
        WHERE info_id NOT IN (SELECT info_id FROM info_ai_scores)
        """
    ).fetchall()
    if not rows:
        return

    payload: List[Tuple[int, int, int]] = []
    for row in rows:
        info_id = int(row[0])
        for idx, col in enumerate(legacy_cols, start=1):
            score_val = row[idx]
            if score_val is None:
                continue
            metric_key = LEGACY_COLUMNS[col]
            metric_id = metric_index.get(metric_key)
            if metric_id is None:
                continue
            payload.append((info_id, metric_id, int(score_val)))
    if payload:
        conn.executemany(
            """
            INSERT OR REPLACE INTO info_ai_scores (info_id, metric_id, score, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            payload,
        )
        conn.commit()


def normalize_weights_json(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT pipeline_id, COALESCE(weights_json,'') FROM pipeline_writers").fetchall()
    if not rows:
        return
    updated = False
    for pipeline_id, raw in rows:
        text = (raw or "").strip()
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        changed = False
        normalized: Dict[str, float] = {}
        for key, value in data.items():
            metric_key = str(key).strip()
            if metric_key.isdigit():
                row = conn.execute("SELECT key FROM ai_metrics WHERE id=?", (int(metric_key),)).fetchone()
                if row:
                    metric_key = str(row[0])
                    changed = True
            exists = conn.execute("SELECT 1 FROM ai_metrics WHERE key=?", (metric_key,)).fetchone()
            if not exists:
                continue
            try:
                normalized[metric_key] = float(value)
            except (TypeError, ValueError):
                continue
        if changed:
            conn.execute(
                "UPDATE pipeline_writers SET weights_json=? WHERE pipeline_id=?",
                (json.dumps(normalized, ensure_ascii=False), pipeline_id),
            )
            updated = True
    if updated:
        conn.commit()


def run(db_path: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        ensure_tables(conn)
        seed_metrics(conn)
        migrate_scores(conn)
        normalize_weights_json(conn)
    print(f"[done] ai metrics refactor migration applied to {db_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply AI metrics refactor schema updates")
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
