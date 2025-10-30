from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

DEFAULT_HOURS = 24
DEFAULT_SOURCE_BONUS: Dict[str, float] = {
    "openai.research": 3.0,
    "deepmind": 1.0,
    "qbitai-zhiku": 2.0,
}
DEFAULT_LIMIT_PER_CATEGORY = 10
DEFAULT_PER_SOURCE_CAP = 0  # <=0 表示不限制

WRITER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WRITER_DIR.parent
DATA_DIR = PROJECT_ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
OUTPUT_BASE = DATA_DIR / "output" / "email"


@dataclass(frozen=True)
class MetricDefinition:
    id: int
    key: str
    label_zh: str
    default_weight: Optional[float]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Email HTML digest from SQLite (AI scored)")
    p.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="时间窗口（小时，默认 24）")
    p.add_argument("--output", type=str, default="", help="输出 HTML 路径；留空自动生成")
    p.add_argument("--db", type=str, default=str(DB_PATH), help="SQLite 数据库路径 (默认 data/info.db)")
    p.add_argument("--categories", default="", help="分类白名单，逗号分隔（为空表示全部）")
    p.add_argument("--weights", default="", help="覆盖权重的 JSON，例如 {\"timeliness\":0.2,...}")
    p.add_argument("--source-bonus", default="", help="来源加成 JSON，例如 '{\"openai.research\": 2}'")
    p.add_argument(
        "--limit-per-cat",
        type=str,
        default="",
        help=(
            f"每个分类的最大条目数；支持整数或 JSON (如 '12' 或 '{{\"default\":10,\"tech\":5}}'；"
            f"默认 {DEFAULT_LIMIT_PER_CATEGORY})"
        ),
    )
    p.add_argument(
        "--per-source-cap",
        type=int,
        default=None,
        help="同一分类内每个来源的最大条目数（<=0 表示不限制；默认不限制）",
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


def parse_limit_config(raw: Any) -> Tuple[Dict[str, int], int]:
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
            """,
            (pipeline_id,),
        ).fetchone()
    else:
        w = cur.execute(
            """
            SELECT hours, COALESCE(weights_json,''), COALESCE(bonus_json,'')
            FROM pipeline_writers
            WHERE pipeline_id=?
            """,
            (pipeline_id,),
        ).fetchone()
    f = cur.execute(
        """
        SELECT all_categories, COALESCE(categories_json,'')
        FROM pipeline_filters
        WHERE pipeline_id=?
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
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            dt = datetime.strptime(raw, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def human_time(publish: str) -> str:
    dt = try_parse_dt(publish)
    if not dt:
        return publish
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 北京时间")


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
        raise SystemExit("ai_metrics 表为空，无法生成邮件摘要")
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
                r.ai_comment,
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
                "ai_comment": str(row[6] or ""),
                "ai_summary": str(row[7] or ""),
                "scores": {},
            },
        )
        metric_key = str(row[8])
        score = int(row[9])
        article["scores"][metric_key] = score
    return list(articles.values())


def apply_limits(
    entries: List[Dict[str, Any]],
    limit_map: Dict[str, int],
    limit_default: int,
    per_source_cap: int,
) -> List[Dict[str, Any]]:
    if (not limit_map and limit_default <= 0) and (per_source_cap is None or per_source_cap <= 0):
        return entries
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for entry in entries:
        cat = str(entry.get("category") or "")
        by_cat.setdefault(cat, []).append(entry)

    trimmed: List[Dict[str, Any]] = []
    for cat, items in by_cat.items():
        sorted_items = sorted(
            items,
            key=lambda e: (
                float(e.get("final_score") or 0.0),
                try_parse_dt(e.get("publish", "") or "") or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        per_src_counts: Dict[str, int] = {}
        kept: List[Dict[str, Any]] = []
        cat_limit = limit_for_category(limit_map, limit_default, cat)
        for it in sorted_items:
            if per_source_cap is not None and per_source_cap > 0:
                src = str(it.get("source") or "")
                seen = per_src_counts.get(src, 0)
                if seen >= per_source_cap:
                    continue
                per_src_counts[src] = seen + 1
            kept.append(it)
            if cat_limit > 0 and len(kept) >= cat_limit:
                break
        trimmed.extend(kept)
    return trimmed


def render_html(
    entries: List[Dict[str, Any]],
    hours: int,
    weights: Dict[str, float],
    metrics: Sequence[MetricDefinition],
) -> str:
    from collections import defaultdict

    by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    count = 0
    for e in entries:
        by_cat[e.get("category", "")].append(e)
        count += 1

    now_bj = datetime.now(timezone(timedelta(hours=8)))
    head = f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>最近 {hours} 小时资讯汇总</title>
  <style>
    body {{ font: 16px/1.55 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; margin: 24px; color: #222; }}
    h1 {{ font-size: 22px; margin: 0 0 4px; }}
    .meta {{ color: #666; margin: 0 0 16px; }}
    h2 {{ font-size: 19px; margin: 24px 0 10px; padding-top: 8px; border-top: 2px solid #eee; }}
    .article-card {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; background: #fff; box-shadow: 0 2px 4px rgba(15, 23, 42, 0.05); }}
    .article-title {{ font-size: 17px; font-weight: 600; color: #0b5ed7; text-decoration: none; display: inline-block; margin-bottom: 6px; }}
    .article-title:hover {{ text-decoration: underline; }}
    .article-meta {{ color: #5f6368; font-size: 13px; margin-bottom: 10px; }}
    .ai-summary {{ background: #f8fafc; border-radius: 8px; padding: 12px 14px; line-height: 1.6; color: #1f2937; }}
    .ai-summary + .ai-summary {{ margin-top: 8px; }}
    .ai-missing {{ background: #fff4e6; border: 1px dashed #f59e0b; color: #b45309; }}
    .ai-rating {{ display: flex; align-items: baseline; gap: 8px; font-size: 16px; font-weight: 600; margin-bottom: 6px; color: #b45309; }}
    .stars {{ font-size: 18px; letter-spacing: 2px; color: #f97316; }}
    .score-number {{ color: #b45309; font-size: 15px; }}
    .ai-dimensions {{ font-size: 14px; color: #334155; margin-bottom: 6px; }}
    .ai-comment, .ai-summary-text {{ font-size: 14px; color: #1f2937; }}
    time {{ color: #555; }}
  </style>
  </head>
<body>
"""

    header = f"""
<h1>最近 {hours} 小时资讯汇总</h1>
<p class=\"meta\">生成时间：{now_bj.strftime('%Y-%m-%d %H:%M 北京时间')} · 合计：{count} 条</p>
"""

    active_metrics = [m for m in metrics if weights.get(m.key, 0.0) > 0]
    if not active_metrics:
        active_metrics = list(metrics)

    def _render_article_card(entry: Dict[str, Any]) -> str:
        publish = entry.get("publish", "")
        dt = try_parse_dt(publish)
        if dt:
            dt_bj = dt.astimezone(timezone(timedelta(hours=8)))
            iso = dt_bj.isoformat()
            shown = human_time(publish)
        else:
            iso = escape(publish)
            shown = escape(publish)
        link = escape(entry.get("link", ""))
        source = entry.get("source", "") or ""
        raw_title = entry.get("title", "") or ""
        title = escape(f"{source}:{raw_title}")
        scores = entry.get("scores") or {}
        comment = entry.get("ai_comment", "")
        summary = entry.get("ai_summary", "")
        final_score = float(entry.get("final_score") or 0.0)
        if scores:
            rounded = int(final_score + 0.5)
            rounded = max(1, min(5, rounded))
            stars = "★" * rounded + "☆" * (5 - rounded)
            dims = " · ".join(
                f"{m.label_zh}：{scores.get(m.key, '-')}"
                for m in active_metrics
            )
            bonus = entry.get("bonus")
            bonus_note = ""
            if bonus:
                sign = "+" if bonus > 0 else ""
                bonus_note = f"（手动加成 {sign}{bonus:g}）"
            rating_html = (
                "<div class=\"ai-summary\">"
                f"<div class=\"ai-rating\"><span class=\"stars\">{stars}</span>"
                f"<span class=\"score-number\">{final_score:.2f}/5</span></div>"
                f"<div class=\"ai-dimensions\">{escape(dims)}{escape(bonus_note)}</div>"
                f"<div class=\"ai-comment\">评价：{escape(comment)}</div>"
                f"<div class=\"ai-summary-text\">概要：{escape(summary)}</div>"
                "</div>"
            )
        else:
            rating_html = "<div class=\"ai-summary ai-missing\">AI 评估：暂无数据</div>"
        return (
            "<article class=\"article-card\">"
            f"<a class=\"article-title\" href=\"{link}\" target=\"_blank\" rel=\"noopener noreferrer\">{title}</a>"
            f"<div class=\"article-meta\"><time datetime=\"{iso}\">{shown}</time></div>"
            f"{rating_html}"
            "</article>"
        )

    categories = list(by_cat.keys())

    def cat_key(c: str) -> Tuple[int, str]:
        if c == "game":
            return (0, "")
        if c:
            return (1, c.lower())
        return (2, "")

    categories.sort(key=cat_key)

    sections: List[str] = []
    for cat in categories:
        label = cat or "(未分类)"
        sections.append(f"<h2>{escape(label)}</h2>")
        cat_entries = sorted(
            by_cat[cat],
            key=lambda e: (
                float(e.get("final_score") or 0.0),
                try_parse_dt(e.get("publish", "") or "") or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        for entry in cat_entries:
            sections.append(_render_article_card(entry))

    tail = "\n</body>\n</html>\n"
    return head + header + "\n".join(sections) + tail


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    OUTPUT_BASE.mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = OUTPUT_BASE / f"{ts}-email.html"

    weights_cli_override = args.weights
    bonus_cli_override = args.source_bonus
    categories_filter: List[str] = []
    limit_map: Dict[str, int] = {}
    limit_default = DEFAULT_LIMIT_PER_CATEGORY
    per_source_cap = DEFAULT_PER_SOURCE_CAP
    effective_hours = max(1, int(args.hours))

    pid = _env_pipeline_id()

    with sqlite3.connect(str(db_path)) as conn:
        metrics = load_active_metrics(conn)
        metric_keys = {m.key for m in metrics}

        metric_weight_rows: Optional[List[Dict[str, Any]]] = None
        pipeline_weights_json = ""
        source_bonus = DEFAULT_SOURCE_BONUS.copy()

        if pid is not None:
            cfg = _load_pipeline_cfg(conn, pid)
            if isinstance(cfg.get("hours"), int) and int(cfg["hours"]) > 0:
                effective_hours = int(cfg["hours"])
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

            all_cats = int(cfg.get("all_categories", 1) or 1)
            if all_cats == 0:
                try:
                    cats = json.loads(cfg.get("categories_json") or "[]")
                    if isinstance(cats, list):
                        categories_filter = [str(c).strip() for c in cats if str(c).strip()]
                except json.JSONDecodeError:
                    pass

            limit_raw = cfg.get("limit_per_category")
            if limit_raw not in (None, ""):
                limit_map, limit_default = parse_limit_config(limit_raw)
            if cfg.get("per_source_cap") is not None:
                try:
                    per_source_cap = int(cfg["per_source_cap"])
                except (TypeError, ValueError):
                    pass

        weights = resolve_weights(metrics, metric_weight_rows, pipeline_weights_json, weights_cli_override)

        if bonus_cli_override.strip():
            for key, value in parse_weight_overrides(
                bonus_cli_override,
                None,
                allow_negative=True,
            ).items():
                source_bonus[str(key)] = value
        if args.categories.strip():
            categories_filter = [c.strip() for c in args.categories.split(",") if c.strip()]
        if args.limit_per_cat and args.limit_per_cat.strip():
            limit_map, limit_default = parse_limit_config(args.limit_per_cat)
        if args.per_source_cap is not None:
            per_source_cap = int(args.per_source_cap)

        articles = load_article_scores(conn)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, effective_hours))
    entries: List[Dict[str, Any]] = []
    seen_links: Set[str] = set()

    for article in articles:
        dt = try_parse_dt(article.get("publish", ""))
        if not dt or dt < cutoff:
            continue
        category = article.get("category", "")
        if categories_filter and category not in categories_filter:
            continue
        link = article.get("link", "").strip()
        if not link:
            continue
        title = article.get("ai_summary", "").strip() or article.get("title", "").strip()
        if not title:
            continue
        if link in seen_links:
            continue
        seen_links.add(link)
        scores = {key: int(value) for key, value in article.get("scores", {}).items() if key in metric_keys}
        weighted = compute_weighted_score(scores, weights)
        if weighted <= 0:
            continue
        bonus = float(source_bonus.get(article.get("source", ""), 0.0))
        if bonus:
            weighted = round(max(1.0, min(5.0, weighted + bonus)), 2)
        entry = {
            "id": article["id"],
            "category": category,
            "source": article.get("source", ""),
            "publish": article.get("publish", ""),
            "title": title,
            "link": link,
            "scores": scores,
            "ai_comment": article.get("ai_comment", ""),
            "ai_summary": article.get("ai_summary", ""),
            "final_score": weighted,
            "bonus": bonus if bonus else None,
        }
        entries.append(entry)

    if not entries:
        print("没有符合条件的资讯，未生成文件")
        return

    entries = apply_limits(entries, limit_map, limit_default, per_source_cap)
    if not entries:
        print("没有符合条件的资讯，未生成文件")
        return

    html = render_html(entries, effective_hours, weights, metrics)
    out_path.write_text(html, encoding="utf-8")
    print(f"已生成: {out_path}")


if __name__ == "__main__":
    main()
