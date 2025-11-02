from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
OUT_DIR = DATA_DIR / "feishu-msg"

DEFAULT_SOURCE_BONUS: Dict[str, float] = {
    "openai.research": 3.0,
    "deepmind": 1.0,
    "qbitai-zhiku": 2.0,
}

DEFAULT_LIMIT_PER_CATEGORY = 10
DEFAULT_PER_SOURCE_CAP = 3

# Helpers for presentation in Feishu message
# Use emoji/text star for better visibility in Feishu
STAR_FILLED = os.getenv("STAR_FULL_CHAR", "⭐")  # full star
# Default half indicator uses sparkles for visibility; override with HALF_STAR_CHAR if needed.
HALF_STAR = os.getenv("HALF_STAR_CHAR", "✨")

def score_to_stars(score: float) -> str:
    """Convert numeric score (1.0–5.0) to star string.

    - Full stars: floor(score)
    - Half star: append one half glyph if fractional part >= 0.5
    """
    try:
        s = float(score)
    except Exception:
        s = 0.0
    s = max(0.0, min(5.0, s))
    full = int(s)
    has_half = (s - full) >= 0.5 and full < 5
    return (STAR_FILLED * full) + (HALF_STAR if has_half else "")


@dataclass(frozen=True)
class MetricDefinition:
    id: int
    key: str
    label_zh: str
    default_weight: Optional[float]


def parse_limit_config(raw: Any) -> Tuple[Dict[str, int], int]:
    """Return (per-category map, default limit) parsed from config/CLI."""
    limit_map: Dict[str, int] = {}
    default_limit = DEFAULT_LIMIT_PER_CATEGORY
    value: Any = raw
    if value is None:
        return limit_map, default_limit
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="ignore").strip()
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return limit_map, default_limit
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            try:
                default_limit = int(float(s))
            except (TypeError, ValueError):
                return limit_map, default_limit
            return limit_map, default_limit
        else:
            value = parsed
    if isinstance(value, (int, float)):
        default_limit = int(value)
        return limit_map, default_limit
    if isinstance(value, dict):
        temp_default = default_limit
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
            if key_str.lower() == "default":
                temp_default = int_val
            else:
                limit_map[key_str] = int_val
        return limit_map, temp_default
    return limit_map, default_limit


def limit_for_category(limit_map: Dict[str, int], default_limit: int, category: str) -> int:
    return int(limit_map.get(category, default_limit))


def parse_weight_overrides(
    raw: str,
    valid_keys: Optional[Set[str]] = None,
    *,
    allow_negative: bool = False,
) -> Dict[str, float]:
    overrides: Dict[str, float] = {}
    data = raw.strip()
    if not data:
        return overrides
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        return overrides
    if not isinstance(parsed, dict):
        return overrides
    for key, value in parsed.items():
        key_str = str(key)
        if valid_keys is not None and key_str not in valid_keys:
            continue
        if isinstance(value, (int, float)):
            value_f = float(value)
            if not allow_negative and value_f < 0:
                continue
            overrides[key_str] = value_f
    return overrides


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Feishu-friendly markdown summary from SQLite")
    p.add_argument("--db", default=str(DB_PATH), help="SQLite 数据库路径 (默认: data/info.db)")
    p.add_argument("--hours", type=int, default=24, help="时间窗口（小时，默认 24）")
    p.add_argument(
        "--limit-per-cat",
        type=str,
        default="",
        help=(
            f"每个分类的最大条目数；可传整数或 JSON（例如 '12' 或 '{{\"default\":10,\"tech\":5}}'；"
            f"默认 {DEFAULT_LIMIT_PER_CATEGORY}）"
        ),
    )
    p.add_argument(
        "--per-source-cap",
        type=int,
        default=None,
        help=f"每个来源在同一分类内的最大条目数（默认 {DEFAULT_PER_SOURCE_CAP}；<=0 表示不限制）",
    )
    p.add_argument("--categories", default="game,tech", help="要输出的分类，逗号分隔（默认 game,tech）")
    p.add_argument("--min-score", type=float, default=0.0, help="最小推荐分阈值，低于此值将被过滤（默认 0）")
    p.add_argument("--weights", default="", help="覆盖权重的 JSON，例如 {\"timeliness\":0.2,...}")
    p.add_argument("--output", default="", help="输出文件路径，默认 data/feishu-msg/YYYYMMDD-feishu-msg.md")
    p.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    p.add_argument(
        "--source-bonus",
        default="",
        help="JSON mapping for manual bonus per source before clipping (例如 '{\"openai.research\": 2}')",
    )
    return p.parse_args()


def _env_pipeline_id() -> Optional[int]:
    raw = (os.getenv("PIPELINE_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _load_pipeline_cfg(conn: sqlite3.Connection, pipeline_id: int) -> Dict[str, Any]:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(pipeline_writers)")
    writer_cols = {row[1] for row in cur.fetchall()}
    has_limit_cols = {"limit_per_category", "per_source_cap"} <= writer_cols
    if has_limit_cols:
        w = cur.execute(
            """
            SELECT hours, COALESCE(weights_json,''), COALESCE(bonus_json,''),
                   limit_per_category, per_source_cap
            FROM pipeline_writers
            WHERE pipeline_id=?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (pipeline_id,),
        ).fetchone()
    else:
        w = cur.execute(
            """
            SELECT hours, COALESCE(weights_json,''), COALESCE(bonus_json,'')
            FROM pipeline_writers
            WHERE pipeline_id=?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (pipeline_id,),
        ).fetchone()
    f = cur.execute(
        """
        SELECT all_categories, COALESCE(categories_json,'')
        FROM pipeline_filters
        WHERE pipeline_id=?
        ORDER BY rowid DESC
        LIMIT 1
        """,
        (pipeline_id,),
    ).fetchone()
    metric_rows = cur.execute(
        """
        SELECT m.key, w.weight, w.enabled
        FROM pipeline_writer_metric_weights AS w
        JOIN ai_metrics AS m ON m.id = w.metric_id
        WHERE w.pipeline_id=?
        """,
        (pipeline_id,),
    ).fetchall()

    out: Dict[str, Any] = {}
    if w:
        out["hours"] = int(w[0]) if w[0] is not None else None
        out["weights_json"] = str(w[1] or "")
        out["bonus_json"] = str(w[2] or "")
        if has_limit_cols:
            out["limit_per_category"] = w[3]
            out["per_source_cap"] = int(w[4]) if w[4] is not None else None
    if f:
        out["all_categories"] = int(f[0]) if f[0] is not None else 1
        out["categories_json"] = str(f[1] or "")
    if metric_rows:
        out["metric_weight_rows"] = [
            {"key": row[0], "weight": float(row[1]), "enabled": int(row[2] or 0)}
            for row in metric_rows
        ]
    return out


def try_parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    low = raw.lower()
    try:
        import re as _re

        m = _re.match(r"^(\d+)\s+(day|hour|minute|second)s?\s+ago$", low)
        if m:
            n = int(m.group(1))
            unit = m.group(2)
            delta = {
                "day": timedelta(days=n),
                "hour": timedelta(hours=n),
                "minute": timedelta(minutes=n),
                "second": timedelta(seconds=n),
            }[unit]
            return datetime.now(timezone.utc) - delta
        if low == "yesterday":
            return datetime.now(timezone.utc) - timedelta(days=1)
        if low == "today":
            return datetime.now(timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def load_active_metrics(conn: sqlite3.Connection) -> List[MetricDefinition]:
    try:
        rows = conn.execute(
            """
            SELECT id, key, label_zh, default_weight
            FROM ai_metrics
            WHERE active = 1
            ORDER BY sort_order ASC, id ASC
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit("缺少 AI 指标定义表 (ai_metrics)，请先运行 evaluator 初始化。") from exc
    if not rows:
        raise SystemExit("ai_metrics 表为空，无法生成 Feishu 摘要")
    return [
        MetricDefinition(
            id=row[0],
            key=row[1],
            label_zh=row[2],
            default_weight=row[3],
        )
        for row in rows
    ]


def resolve_weights(
    metrics: Sequence[MetricDefinition],
    metric_weight_rows: Optional[List[Dict[str, Any]]],
    weights_json: str,
    cli_override: str,
) -> Dict[str, float]:
    valid_keys = {m.key for m in metrics}
    weights = {m.key: float(m.default_weight or 0.0) for m in metrics}
    active_keys = set(valid_keys)

    if metric_weight_rows:
        active_keys = {
            str(row.get("key"))
            for row in metric_weight_rows
            if row.get("enabled")
        }
        for row in metric_weight_rows:
            key = str(row.get("key") or "")
            if key not in valid_keys:
                continue
            weight = float(row.get("weight") or 0.0)
            weights[key] = max(0.0, weight)
        for key in valid_keys - active_keys:
            weights[key] = 0.0
        if not active_keys:
            for key in list(weights.keys()):
                weights[key] = 0.0
    else:
        for key, value in parse_weight_overrides(weights_json, valid_keys).items():
            weights[key] = max(0.0, value)
        active_keys = {key for key, value in weights.items() if value > 0}

    for key, value in parse_weight_overrides(cli_override, valid_keys).items():
        weights[key] = max(0.0, value)
        if value > 0:
            active_keys.add(key)
        elif key in active_keys and value == 0:
            active_keys.remove(key)

    if active_keys:
        for key in list(weights.keys()):
            if key not in active_keys:
                weights[key] = 0.0
    return weights


def compute_weighted_score(scores: Dict[str, int], weights: Dict[str, float]) -> float:
    total = 0.0
    wsum = 0.0
    for key, weight in weights.items():
        if weight <= 0:
            continue
        total += float(scores.get(key, 0)) * weight
        wsum += weight
    if wsum <= 0:
        return 0.0
    score = total / wsum
    return round(max(1.0, min(5.0, score)), 2)


def load_article_scores(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    try:
        rows = conn.execute(
            """
            SELECT
                i.id,
                i.category,
                i.source,
                i.publish,
                i.title,
                i.link,
                r.ai_summary,
                m.key,
                s.score
            FROM info AS i
            JOIN info_ai_scores AS s ON s.info_id = i.id
            JOIN ai_metrics AS m ON m.id = s.metric_id AND m.active = 1
            LEFT JOIN info_ai_review AS r ON r.info_id = i.id
            """
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise SystemExit("缺少 AI 评分数据表 (info_ai_scores)，请先运行 evaluator 生成评分。") from exc
    articles: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        info_id = int(row[0])
        article = articles.setdefault(
            info_id,
            {
                "id": info_id,
                "category": str(row[1] or ""),
                "source": str(row[2] or ""),
                "publish": str(row[3] or ""),
                "title": str(row[4] or ""),
                "link": str(row[5] or ""),
                "ai_summary": str(row[6] or ""),
                "scores": {},
            },
        )
        metric_key = str(row[7])
        score = int(row[8])
        article["scores"][metric_key] = score
    return list(articles.values())


def apply_source_bonus(score: float, bonus: float) -> float:
    if bonus == 0:
        return score
    adjusted = score + bonus
    return round(max(1.0, min(5.0, adjusted)), 2)


def format_section(title: str, items: List[Dict[str, Any]]) -> str:
    lines: List[str] = [f"**{title}**"]
    for idx, item in enumerate(items, start=1):
        score = float(item.get("score", 0.0))
        # Show stars instead of numeric score; star count == floor(score)
        stars = score_to_stars(score)
        source = item.get("source", "")
        title_txt = item.get("title", "")
        link = item.get("link", "")
        if len(title_txt) > 100:
            title_txt = title_txt[:100] + "…"
        source_label = source or "查看原文"
        line = f"{idx}. (AI推荐:{stars}) {title_txt} ([{source_label}]({link}))"
        lines.append(line)
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    effective_hours = max(1, int(args.hours))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=effective_hours)
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    limit_map: Dict[str, int] = {}
    limit_default = DEFAULT_LIMIT_PER_CATEGORY
    per_source_cap = DEFAULT_PER_SOURCE_CAP
    min_score = float(args.min_score)
    source_bonus = DEFAULT_SOURCE_BONUS.copy()

    out_path = Path(args.output) if args.output else (OUT_DIR / f"{datetime.now():%Y%m%d}-feishu-msg.md")

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        metrics = load_active_metrics(conn)
        metric_keys = {m.key for m in metrics}

        pid = _env_pipeline_id()
        metric_weight_rows: Optional[List[Dict[str, Any]]] = None
        pipeline_weights_json = ""

        if pid is not None:
            cfg = _load_pipeline_cfg(conn, pid)
            if isinstance(cfg.get("hours"), int) and int(cfg["hours"]) > 0:
                effective_hours = int(cfg["hours"])
                cutoff = datetime.now(timezone.utc) - timedelta(hours=effective_hours)
            pipeline_weights_json = cfg.get("weights_json", "")
            metric_weight_rows = cfg.get("metric_weight_rows")

            bonus_json = cfg.get("bonus_json", "")
            if bonus_json:
                for key, value in parse_weight_overrides(
                    bonus_json,
                    None,
                    allow_negative=True,
                ).items():
                    source_bonus[str(key)] = value

            limit_raw = cfg.get("limit_per_category")
            if limit_raw not in (None, ""):
                limit_map, limit_default = parse_limit_config(limit_raw)
            if cfg.get("per_source_cap") is not None:
                try:
                    per_source_cap = int(cfg["per_source_cap"])
                except (TypeError, ValueError):
                    pass
            all_cats = int(cfg.get("all_categories", 1) or 1)
            if all_cats == 0:
                try:
                    cats = json.loads(cfg.get("categories_json") or "[]")
                    if isinstance(cats, list):
                        categories = [str(c).strip() for c in cats if str(c).strip()]
                except json.JSONDecodeError:
                    pass

        print(f"[WRITER] pipeline={pid} using hours={effective_hours}")
        weights = resolve_weights(metrics, metric_weight_rows, pipeline_weights_json, args.weights)

        if args.source_bonus.strip():
            for key, value in parse_weight_overrides(
                args.source_bonus,
                None,
                allow_negative=True,
            ).items():
                source_bonus[str(key)] = value
        if args.limit_per_cat and args.limit_per_cat.strip():
            limit_map, limit_default = parse_limit_config(args.limit_per_cat)
        if args.per_source_cap is not None:
            per_source_cap = int(args.per_source_cap)

        articles = load_article_scores(conn)

    by_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categories}
    seen_links: Set[str] = set()

    for article in articles:
        dt = try_parse_dt(article.get("publish", ""))
        if not dt or dt < cutoff:
            continue
        category = article.get("category", "")
        if categories and category not in by_cat:
            continue
        link = article.get("link", "").strip()
        if not link:
            continue
        if link in seen_links:
            continue
        seen_links.add(link)

        title = article.get("ai_summary", "").strip() or article.get("title", "").strip()
        if not title:
            continue

        scores = {key: int(value) for key, value in article.get("scores", {}).items() if key in metric_keys}
        weighted = compute_weighted_score(scores, weights)
        if weighted <= 0:
            continue
        bonus = float(source_bonus.get(article.get("source", ""), 0.0))
        weighted = apply_source_bonus(weighted, bonus)
        if weighted < min_score:
            continue

        entry = {
            "id": article["id"],
            "category": category,
            "source": article.get("source", ""),
            "publish": article.get("publish", ""),
            "title": title,
            "link": link,
            "score": weighted,
            "bonus": bonus,
        }
        if categories:
            by_cat.setdefault(category, []).append(entry)

    # 排序与截取
    for cat in list(by_cat.keys()):
        items = by_cat[cat]
        items.sort(
            key=lambda it: (
                float(it.get("score", 0.0)),
                try_parse_dt(it.get("publish", "")) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        if per_source_cap > 0:
            per_source_trimmed: List[Dict[str, Any]] = []
            per_source_groups: Dict[str, List[Dict[str, Any]]] = {}
            for it in items:
                per_source_groups.setdefault(it.get("source", ""), []).append(it)
            for group in per_source_groups.values():
                per_source_trimmed.extend(group[:per_source_cap])
        else:
            per_source_trimmed = list(items)

        per_source_trimmed.sort(
            key=lambda it: (
                float(it.get("score", 0.0)),
                try_parse_dt(it.get("publish", "")) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        cat_limit = limit_for_category(limit_map, limit_default, cat)
        if cat_limit > 0:
            by_cat[cat] = per_source_trimmed[:cat_limit]
        else:
            by_cat[cat] = per_source_trimmed

    total_items = sum(len(items) for items in by_cat.values())
    if total_items == 0:
        print("没有符合条件的资讯，未生成文件")
        return

    sections: List[str] = []
    for cat in categories:
        items = by_cat.get(cat, [])
        if not items:
            continue
        sections.append(format_section(cat.upper(), items))

    content = "\n".join(sections).strip() + "\n"

    if args.dry_run:
        print(content)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"已生成: {out_path} ({total_items} 条)")


if __name__ == "__main__":
    main()
