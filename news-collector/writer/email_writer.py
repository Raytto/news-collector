from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_HOURS = 24

DIMENSION_LABELS: Dict[str, str] = {
    "timeliness": "时效性",
    "game_relevance": "游戏相关性",
    "mobile_game_relevance": "手游相关性",
    "ai_relevance": "AI相关性",
    "tech_relevance": "科技相关性",
    "quality": "文章质量",
    "insight": "洞察力",
    "depth": "深度",
    "novelty": "新颖度",
}
DIMENSION_ORDER: Tuple[str, ...] = tuple(DIMENSION_LABELS.keys())

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


WRITER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WRITER_DIR.parent
DATA_DIR = PROJECT_ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
OUTPUT_BASE = DATA_DIR / "output" / "email"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Email HTML digest from SQLite (unified)")
    p.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="时间窗口（小时，默认 24）")
    p.add_argument("--output", type=str, default="", help="输出 HTML 路径；留空自动生成")
    p.add_argument("--db", type=str, default=str(DB_PATH), help="SQLite 数据库路径 (默认 data/info.db)")
    p.add_argument("--categories", default="", help="分类白名单，逗号分隔（为空表示全部）")
    p.add_argument("--weights", default="", help="覆盖默认权重的 JSON，例如 {\"timeliness\":0.2,...}")
    p.add_argument("--source-bonus", default="", help="来源加成 JSON，例如 '{\"openai.research\": 2}'")
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
    # writer
    w = cur.execute(
        "SELECT hours, COALESCE(weights_json,''), COALESCE(bonus_json,'') FROM pipeline_writers WHERE pipeline_id=?",
        (pipeline_id,),
    ).fetchone()
    # filters
    f = cur.execute(
        "SELECT all_categories, COALESCE(categories_json,'') FROM pipeline_filters WHERE pipeline_id=?",
        (pipeline_id,),
    ).fetchone()
    out: Dict[str, Any] = {}
    if w:
        out["hours"] = int(w[0]) if w[0] is not None else None
        out["weights_json"] = str(w[1] or "")
        out["bonus_json"] = str(w[2] or "")
    if f:
        out["all_categories"] = int(f[0]) if f[0] is not None else 1
        out["categories_json"] = str(f[1] or "")
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


def compute_weighted_score(eva: Dict[str, Any], weights: Dict[str, float]) -> float:
    total = 0.0
    wsum = 0.0
    for dim in DIMENSION_ORDER:
        w = float(weights.get(dim, 0.0))
        if w <= 0:
            continue
        v = float(eva.get(dim, 0) or 0)
        total += v * w
        wsum += w
    if wsum <= 0:
        return 0.0
    return round(max(1.0, min(5.0, total / wsum)), 2)


def human_time(publish: str) -> str:
    dt = try_parse_dt(publish)
    if not dt:
        return publish
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 北京时间")


def fetch_recent(conn: sqlite3.Connection, cutoff: datetime) -> List[Dict[str, Any]]:
    has_review = bool(
        conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='info_ai_review'").fetchone()
    )
    if has_review:
        sql = """
            SELECT i.id, i.category, i.source, i.publish, i.title, i.link,
                   r.timeliness_score,
                   r.game_relevance_score,
                   r.mobile_game_relevance_score,
                   r.ai_relevance_score,
                   r.tech_relevance_score,
                   r.quality_score,
                   r.insight_score,
                   r.depth_score,
                   r.novelty_score,
                   r.ai_comment,
                   r.ai_summary
            FROM info AS i
            LEFT JOIN info_ai_review AS r ON r.info_id = i.id
        """
    else:
        sql = """
            SELECT i.id, i.category, i.source, i.publish, i.title, i.link
            FROM info AS i
        """
    rows = conn.execute(sql).fetchall()
    items: List[Dict[str, Any]] = []
    for row in rows:
        publish = str(row[3] or "")
        dt = try_parse_dt(publish)
        if not dt or dt < cutoff:
            continue
        evaluation: Optional[Dict[str, Any]] = None
        if len(row) >= 17:  # joined with review table
            evaluation = {
                "timeliness": int(row[6]) if row[6] is not None else 0,
                "game_relevance": int(row[7]) if row[7] is not None else 0,
                "mobile_game_relevance": int(row[8]) if row[8] is not None else 0,
                "ai_relevance": int(row[9]) if row[9] is not None else 0,
                "tech_relevance": int(row[10]) if row[10] is not None else 0,
                "quality": int(row[11]) if row[11] is not None else 0,
                "insight": int(row[12]) if row[12] is not None else 0,
                "depth": int(row[13]) if row[13] is not None else 0,
                "novelty": int(row[14]) if row[14] is not None else 0,
                "comment": str(row[15] or ""),
                "summary": str(row[16] or ""),
            }
        items.append(
            {
                "id": int(row[0]),
                "category": str(row[1] or ""),
                "source": str(row[2] or ""),
                "publish": publish,
                "title": str(row[4] or ""),
                "link": str(row[5] or ""),
                "evaluation": evaluation,
            }
        )
    return items


def render_html(entries: List[Dict[str, Any]], hours: int, weights: Dict[str, float]) -> str:
    from collections import defaultdict
    by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    count = 0
    for e in entries:
        eva = e.get("evaluation") or {}
        if eva:
            e["final_score"] = compute_weighted_score(eva, weights)
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
        evaluation = entry.get("evaluation")
        if evaluation:
            final_score = float(entry.get("final_score") or 0.0)
            rounded = int(final_score + 0.5)
            rounded = max(1, min(5, rounded))
            stars = "★" * rounded + "☆" * (5 - rounded)
            dims = " · ".join(
                f"{DIMENSION_LABELS[key]}：{evaluation.get(key, '-')}" for key in DIMENSION_ORDER
            )
            bonus_note = ""
            bonus_val = evaluation.get("bonus")
            if bonus_val:
                sign = "+" if bonus_val > 0 else ""
                bonus_note = f"（手动加成 {sign}{bonus_val:g}）"
            rating_html = (
                "<div class=\"ai-summary\">"
                f"<div class=\"ai-rating\"><span class=\"stars\">{stars}</span>"
                f"<span class=\"score-number\">{final_score:.2f}/5</span></div>"
                f"<div class=\"ai-dimensions\">{escape(dims)}{escape(bonus_note)}</div>"
                f"<div class=\"ai-comment\">评价：{escape(evaluation.get('comment', ''))}</div>"
                f"<div class=\"ai-summary-text\">概要：{escape(evaluation.get('summary', ''))}</div>"
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

    # Order categories: 'game' first, then others alphabetically, then empty
    categories = list(by_cat.keys())
    def cat_key(c: str):
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
        # Sort entries by final_score then time
        def key_fn(e: Dict[str, Any]):
            score = float(e.get("final_score") or 0.0)
            dt = try_parse_dt(e.get("publish", "") or "") or datetime.min.replace(tzinfo=timezone.utc)
            return (score, dt)
        cat_entries = sorted(by_cat[cat], key=key_fn, reverse=True)
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

    # Base config from CLI defaults
    weights = DEFAULT_WEIGHTS.copy()
    source_bonus = DEFAULT_SOURCE_BONUS.copy()
    effective_hours = max(1, int(args.hours))
    categories_filter: list[str] = []

    # If running under pipeline, load config from DB by default (no extra flags)
    pid = _env_pipeline_id()
    with sqlite3.connect(str(db_path)) as conn:
        if pid is not None:
            cfg = _load_pipeline_cfg(conn, pid)
            if isinstance(cfg.get("hours"), int) and int(cfg["hours"]) > 0:
                effective_hours = int(cfg["hours"])  # DB overrides CLI default
            # weights_json
            wj = (cfg.get("weights_json") or "").strip()
            if wj:
                try:
                    w_map = json.loads(wj)
                    if isinstance(w_map, dict):
                        for k, v in w_map.items():
                            if k in DIMENSION_LABELS and isinstance(v, (int, float)):
                                weights[k] = float(v)
                except json.JSONDecodeError:
                    pass
            # bonus_json
            bj = (cfg.get("bonus_json") or "").strip()
            if bj:
                try:
                    b_map = json.loads(bj)
                    if isinstance(b_map, dict):
                        for k, v in b_map.items():
                            if isinstance(v, (int, float)):
                                source_bonus[str(k)] = float(v)
                except json.JSONDecodeError:
                    pass
            # categories from filters when all_categories=0
            all_cats = int(cfg.get("all_categories", 1) or 1)
            if all_cats == 0:
                try:
                    cats = json.loads(cfg.get("categories_json") or "[]")
                    if isinstance(cats, list):
                        categories_filter = [str(c).strip() for c in cats if str(c).strip()]
                except json.JSONDecodeError:
                    pass

        # CLI overrides remain supported for ad-hoc runs
        if args.weights.strip():
            try:
                overrides = json.loads(args.weights)
                if isinstance(overrides, dict):
                    for k, v in overrides.items():
                        if k in DIMENSION_LABELS and isinstance(v, (int, float)):
                            weights[k] = float(v)
            except json.JSONDecodeError:
                pass
        if args.source_bonus.strip():
            try:
                overrides = json.loads(args.source_bonus)
                if isinstance(overrides, dict):
                    for k, v in overrides.items():
                        if isinstance(v, (int, float)):
                            source_bonus[str(k)] = float(v)
            except json.JSONDecodeError:
                pass
        # explicit CLI categories (highest precedence)
        if args.categories.strip():
            categories_filter = [c.strip() for c in args.categories.split(",") if c.strip()]

        cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, effective_hours))
        entries = fetch_recent(conn, cutoff)
        if categories_filter:
            entries = [e for e in entries if (e.get("category") or "") in categories_filter]
        # Apply source bonus and compute final_score
        for e in entries:
            eva = e.get("evaluation") or {}
            if eva:
                bonus = float(source_bonus.get(e.get("source", ""), 0.0))
                if bonus:
                    eva["bonus"] = bonus
                score = compute_weighted_score(eva, weights)
                if bonus:
                    score = max(1.0, min(5.0, score + bonus))
                e["final_score"] = round(score, 2)

    if not entries:
        print("没有符合条件的资讯，未生成文件")
        return

    html = render_html(entries, effective_hours, weights)
    if not html:
        print("没有符合条件的资讯，未生成文件")
        return
    out_path.write_text(html, encoding="utf-8")
    print(f"已生成: {out_path}")


if __name__ == "__main__":
    main()
