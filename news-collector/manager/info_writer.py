from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from html import escape
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
OUTPUT_DIR = DATA_DIR / "output"

DIMENSION_LABELS: Dict[str, str] = {
    "timeliness": "时效性",
    "game_relevance": "游戏相关性",
    "mobile_game_relevance": "手游相关性",
    "ai_relevance": "AI相关性",
    "tech_relevance": "科技相关性",
    "quality": "文章质量",
    "insight": "洞察力",
}
DIMENSION_ORDER: Tuple[str, ...] = tuple(DIMENSION_LABELS.keys())

# 默认权重（与 docs/prompt/ai-evaluation-spec.md 保持一致），可在此按用户群体调整
DEFAULT_WEIGHTS: Dict[str, float] = {
    "timeliness": 0.09,
    "game_relevance": 0.22,
    "mobile_game_relevance": 0.10,
    "ai_relevance": 0.16,
    "tech_relevance": 0.04,
    "quality": 0.14,
    "insight": 0.25,
}

# Optional manual bonus per source, e.g. {"openai.research": 2}
DEFAULT_SOURCE_BONUS: Dict[str, float] = {
    "openai.research": 2.0,
    "deepmind": 2.0,
    "qbitai-zhiku": 2.0,
}


def compute_weighted_score(eva: Dict[str, Any], weights: Dict[str, float] = DEFAULT_WEIGHTS) -> float:
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Write recent info entries from SQLite into a Markdown digest.",
    )
    p.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Look back this many hours from now (UTC). Default: 24.",
    )
    p.add_argument(
        "--output",
        type=str,
        default="",
        help="Optional output HTML path. If omitted, a timestamped name is used in data/output.",
    )
    p.add_argument(
        "--source-bonus",
        type=str,
        default="",
        help="JSON mapping of source->bonus score to add before clipping (e.g. '{\"openai.research\": 2}')",
    )
    return p.parse_args()


def try_parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None

    # ISO 8601 attempt (supports timezone offsets). 'Z' -> '+00:00'
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass

    # Relative forms: "X days ago", "X hours ago", "yesterday", "today"
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
            return (datetime.now(timezone.utc) - delta)
        if low == "yesterday":
            return datetime.now(timezone.utc) - timedelta(days=1)
        if low == "today":
            return datetime.now(timezone.utc)
    except Exception:
        pass

    # Common fallback formats (assume UTC if no tz given)
    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ]
    for fmt in fmts:
        try:
            dt = datetime.strptime(raw, fmt)
            dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def fetch_recent(
    conn: sqlite3.Connection,
    cutoff: datetime,
    source_bonus: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Return recent entries enriched with AI 评分数据。"""
    has_review = bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='info_ai_review'"
        ).fetchone()
    )
    if has_review:
        sql = """
            SELECT i.id, i.category, i.source, i.publish, i.title, i.link,
                   r.final_score,
                   r.timeliness_score, r.game_relevance_score, r.mobile_game_relevance_score,
                   r.ai_relevance_score, r.tech_relevance_score, r.quality_score,
                   r.insight_score,
                   r.ai_comment, r.ai_summary
            FROM info AS i
            LEFT JOIN info_ai_review AS r ON r.info_id = i.id
        """
    else:
        sql = """
            SELECT i.id, i.category, i.source, i.publish, i.title, i.link
            FROM info AS i
        """
    rows = conn.execute(sql).fetchall()
    entries: List[Dict[str, Any]] = []
    for row in rows:
        publish = str(row[3] or "")
        dt = try_parse_dt(publish)
        if not dt or dt < cutoff:
            continue
        evaluation: Optional[Dict[str, Any]] = None
        if has_review:
            final_score = row[6]
        else:
            final_score = None
        if final_score is not None:
            evaluation = {
                "final_score": float(final_score),
                "timeliness": int(row[7]) if row[7] is not None else 0,
                "game_relevance": int(row[8]) if row[8] is not None else 0,
                "mobile_game_relevance": int(row[9]) if row[9] is not None else 0,
                "ai_relevance": int(row[10]) if row[10] is not None else 0,
                "tech_relevance": int(row[11]) if row[11] is not None else 0,
                "quality": int(row[12]) if row[12] is not None else 0,
                "insight": int(row[13]) if row[13] is not None else 0,
                "comment": str(row[14] or ""),
                "summary": str(row[15] or ""),
            }
            # 动态计算当前展示所需的加权总分（忽略数据库中的旧 final_score）
            evaluation["final_score"] = compute_weighted_score(evaluation)
            bonus = float(source_bonus.get(str(row[2] or ""), 0.0))
            if bonus:
                evaluation["final_score"] = round(
                    max(1.0, min(5.0, evaluation["final_score"] + bonus)), 2
                )
                evaluation["bonus"] = bonus
        entries.append(
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

    entries.sort(
        key=lambda item: try_parse_dt(item["publish"]) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return entries


def human_time(publish: str) -> str:
    dt = try_parse_dt(publish)
    if not dt:
        return publish
    return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 北京时间")


def render_html(entries: Iterable[Dict[str, Any]], hours: int) -> str:
    # Group only by category; within each category, sort by final score desc
    by_cat: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    count = 0
    for entry in entries:
        by_cat[entry["category"]].append(entry)
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
    h3 {{ font-size: 17px; margin: 18px 0 12px; }}
    .source-group {{ margin: 0 0 20px; }}
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

    # Order categories: 'game' first, then others (non-empty) alphabetically, then empty category last
    categories = list(by_cat.keys())
    def cat_key(c: str):
        if c == "game":
            return (0, "")
        if c:
            return (1, c.lower())
        return (2, "")
    categories.sort(key=cat_key)

    def score_key(e: Dict[str, Any]) -> tuple:
        eva = e.get("evaluation") or {}
        score = float(eva.get("final_score") or 0.0)
        # publish desc as tiebreaker
        dt = try_parse_dt(e.get("publish", "") or "") or datetime.min.replace(tzinfo=timezone.utc)
        return (score, dt)

    sections: List[str] = []
    for cat in categories:
        cat_label = cat or "(未分类)"
        sections.append(f"<h2>{escape(cat_label)}</h2>")

        # Sort entries of this category by final_score desc; missing scores go last
        cat_entries = sorted(by_cat[cat], key=score_key, reverse=True)
        for entry in cat_entries:
            sections.append(_render_article_card(entry))

    tail = "\n</body>\n</html>\n"
    return head + header + "\n".join(sections) + tail


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
    # Combine source and title: "{source}:{title}"
    source = entry.get("source", "") or ""
    raw_title = entry.get("title", "") or ""
    title = escape(f"{source}:{raw_title}")
    evaluation = entry.get("evaluation")

    if evaluation:
        final_score = float(evaluation["final_score"])
        stars = _render_stars(final_score)
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


def _render_stars(score: float) -> str:
    rounded = int(score + 0.5)
    rounded = max(1, min(5, rounded))
    return "★" * rounded + "☆" * (5 - rounded)


def main() -> None:
    args = parse_args()
    hours = max(1, int(args.hours))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库: {DB_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        out_path = OUTPUT_DIR / f"{ts}-info.html"

    source_bonus = DEFAULT_SOURCE_BONUS.copy()
    if args.source_bonus.strip():
        try:
            bonus_mapping = json.loads(args.source_bonus)
            if isinstance(bonus_mapping, dict):
                for key, value in bonus_mapping.items():
                    if isinstance(value, (int, float)):
                        source_bonus[str(key)] = float(value)
        except json.JSONDecodeError:
            pass

    with sqlite3.connect(str(DB_PATH)) as conn:
        entries = fetch_recent(conn, cutoff, source_bonus)
        doc = render_html(entries, hours)
        out_path.write_text(doc, encoding="utf-8")

    print(f"已生成: {out_path} ({len(entries)} 条)")


if __name__ == "__main__":
    main()
