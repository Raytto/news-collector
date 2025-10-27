from __future__ import annotations

import argparse
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
    "relevance": "相关性",
    "insightfulness": "洞察力",
    "actionability": "可行动性",
}
DIMENSION_ORDER: Tuple[str, ...] = tuple(DIMENSION_LABELS.keys())


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


def fetch_recent(conn: sqlite3.Connection, cutoff: datetime) -> List[Dict[str, Any]]:
    """Return recent entries enriched with AI 评分数据。"""
    has_review = bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='info_ai_review'"
        ).fetchone()
    )
    if has_review:
        sql = """
            SELECT i.id, i.category, i.source, i.publish, i.title, i.link,
                   r.final_score, r.timeliness_score, r.relevance_score,
                   r.insightfulness_score, r.actionability_score,
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
                "timeliness": int(row[7]),
                "relevance": int(row[8]),
                "insightfulness": int(row[9]),
                "actionability": int(row[10]),
                "comment": str(row[11] or ""),
                "summary": str(row[12] or ""),
            }
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
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def render_html(entries: Iterable[Dict[str, Any]], hours: int) -> str:
    by_cat: Dict[str, Dict[str, List[Dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    count = 0
    for entry in entries:
        by_cat[entry["category"]][entry["source"]].append(entry)
        count += 1

    now_utc = datetime.now(timezone.utc)
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
<p class=\"meta\">生成时间：{now_utc.strftime('%Y-%m-%d %H:%M UTC')} · 合计：{count} 条</p>
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

    sections: List[str] = []
    for cat in categories:
        cat_label = cat or "(未分类)"
        sections.append(f"<h2>{escape(cat_label)}</h2>")

        for source in sorted(by_cat[cat].keys()):
            sections.append("<div class=\"source-group\">")
            sections.append(f"<h3>{escape(source or '未注明来源')}</h3>")
            for entry in by_cat[cat][source]:
                sections.append(_render_article_card(entry))
            sections.append("</div>")

    tail = "\n</body>\n</html>\n"
    return head + header + "\n".join(sections) + tail


def _render_article_card(entry: Dict[str, Any]) -> str:
    publish = entry.get("publish", "")
    dt = try_parse_dt(publish)
    if dt:
        iso = dt.isoformat()
        shown = human_time(publish)
    else:
        iso = escape(publish)
        shown = escape(publish)

    link = escape(entry.get("link", ""))
    title = escape(entry.get("title", ""))
    evaluation = entry.get("evaluation")

    if evaluation:
        final_score = float(evaluation["final_score"])
        stars = _render_stars(final_score)
        dims = " · ".join(
            f"{DIMENSION_LABELS[key]}：{evaluation.get(key, '-')}" for key in DIMENSION_ORDER
        )
        rating_html = (
            "<div class=\"ai-summary\">"
            f"<div class=\"ai-rating\"><span class=\"stars\">{stars}</span>"
            f"<span class=\"score-number\">{final_score:.2f}/5</span></div>"
            f"<div class=\"ai-dimensions\">{escape(dims)}</div>"
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

    with sqlite3.connect(str(DB_PATH)) as conn:
        entries = fetch_recent(conn, cutoff)
        doc = render_html(entries, hours)
        out_path.write_text(doc, encoding="utf-8")

    print(f"已生成: {out_path} ({len(entries)} 条)")


if __name__ == "__main__":
    main()
