from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


WENHAO_CATEGORIES: Tuple[str, ...] = ("humanities", "tech")
DEFAULT_LIMIT = 10
DEFAULT_HOURS = 24

# For general mode (aligned with info_writer)
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
    parser = argparse.ArgumentParser(
        description="Generate Email HTML digest (general or wenhao mode)",
    )
    parser.add_argument("--mode", choices=["general", "wenhao"], default="general", help="输出模式：general 通用资讯；wenhao 文浩精选")
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="时间窗口，默认 24 小时")
    parser.add_argument("--output", type=str, default="", help="输出 HTML 路径；留空自动生成")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="（wenhao 模式）每个分类最大条目数 (默认 10)")
    parser.add_argument("--db", type=str, default=str(DB_PATH), help="SQLite 数据库路径")
    parser.add_argument("--source-bonus", type=str, default="", help="（general 模式）JSON 格式的来源加成映射")
    return parser.parse_args()


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


def fetch_rows(conn: sqlite3.Connection) -> List[tuple]:
    sql = """
    SELECT i.id, i.category, i.source, i.publish, i.title, i.link,
           r.timeliness_score, r.depth_score, r.novelty_score,
           r.ai_summary, r.ai_comment
    FROM info AS i
    LEFT JOIN info_ai_review AS r ON r.info_id = i.id
    """
    return conn.execute(sql).fetchall()


def select_items(rows: List[tuple], hours: int, limit: int) -> Dict[str, List[Dict[str, Any]]]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    grouped: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in WENHAO_CATEGORIES}

    for row in rows:
        (
            _id,
            category,
            source,
            publish,
            title,
            link,
            timeliness,
            depth,
            novelty,
            summary,
            comment,
        ) = row

        if category not in grouped:
            continue
        dt = try_parse_dt(str(publish or ""))
        if not dt or dt < cutoff:
            continue
        if any(score is None for score in (timeliness, depth, novelty)):
            continue
        link = str(link or "").strip()
        title = str(title or "").strip()
        if not (link and title):
            continue

        entry = {
            "id": int(_id),
            "source": str(source or ""),
            "publish": str(publish or ""),
            "title": title,
            "link": link,
            "summary": str(summary or ""),
            "comment": str(comment or ""),
            "timeliness": int(timeliness or 0),
            "depth": int(depth or 0),
            "novelty": int(novelty or 0),
        }
        entry["score"] = compute_score(entry["depth"], entry["novelty"], entry["timeliness"])
        grouped[category].append(entry)

    for cat, items in grouped.items():
        items.sort(
            key=lambda it: (
                float(it.get("score", 0.0)),
                try_parse_dt(it.get("publish", "")) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        grouped[cat] = items[:max(1, limit)]

    return grouped


def render_html_wenhao(groups: Dict[str, List[Dict[str, Any]]], hours: int) -> str:
    total = sum(len(v) for v in groups.values())
    now_bj = datetime.now(timezone(timedelta(hours=8)))
    head = """<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>WH精选</title>
  <style>
    body { font: 16px/1.6 -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, 'Noto Sans', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif; margin: 28px; color: #1f2937; }
    h1 { font-size: 24px; margin: 0 0 6px; }
    .meta { color: #6b7280; margin-bottom: 18px; }
    h2 { font-size: 19px; margin: 26px 0 12px; border-bottom: 2px solid #e5e7eb; padding-bottom: 4px; }
    .card { border: 1px solid #e5e7eb; border-radius: 10px; padding: 16px 18px; margin-bottom: 14px; background: #fff; box-shadow: 0 2px 6px rgba(15, 23, 42, 0.08); }
    .card h3 { margin: 0 0 8px; font-size: 17px; }
    .card a { color: #0b5ed7; text-decoration: none; }
    .card a:hover { text-decoration: underline; }
    .meta-line { color: #6b7280; font-size: 13px; margin-bottom: 6px; }
    .scores { font-size: 14px; color: #374151; margin-bottom: 6px; }
    .summary { font-size: 14px; color: #1f2937; margin-bottom: 6px; }
    .comment { font-size: 14px; color: #4b5563; }
  </style>
</head>
<body>
"""
    header = f"""
<h1>WH精选 · 近 {hours} 小时</h1>
<p class=\"meta\">生成时间：{now_bj.strftime('%Y-%m-%d %H:%M 北京时间')} · 合计 {total} 篇</p>
"""

    sections: List[str] = []
    label_map = {"humanities": "人文精选", "tech": "科技精选"}
    for cat in CATEGORIES:
        items = groups.get(cat, [])
        if not items:
            continue
        sections.append(f"<h2>{escape(label_map.get(cat, cat.title()))}</h2>")
        for idx, item in enumerate(items, start=1):
            publish = escape(human_time(item["publish"]))
            link = escape(item["link"])
            title = escape(item["title"]) or "Untitled"
            source = escape(item.get("source", ""))
            summary = escape(item.get("summary", ""))
            comment = escape(item.get("comment", ""))
            scores = (
                f"深度：{item['depth']} · 新颖度：{item['novelty']} · 时效性：{item['timeliness']} · 总分：{item['score']:.2f}"
            )
            sections.append(
                "<div class=\"card\">"
                f"<h3>{idx}. <a href=\"{link}\" target=\"_blank\" rel=\"noopener noreferrer\">{title}</a></h3>"
                f"<div class=\"meta-line\">来源：{source or '未知'} · 发布时间：{publish}</div>"
                f"<div class=\"scores\">{scores}</div>"
                + (f"<div class=\"summary\">概要：{summary}</div>" if summary else "")
                + (f"<div class=\"comment\">点评：{comment}</div>" if comment else "")
                + "</div>"
            )

    if not sections:
        return ""
    tail = "\n</body>\n</html>\n"
    return head + header + "\n".join(sections) + tail


def fetch_recent_general(
    conn: sqlite3.Connection,
    cutoff: datetime,
    source_bonus: Dict[str, float],
) -> List[Dict[str, Any]]:
    has_review = bool(
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='info_ai_review'"
        ).fetchone()
    )
    if has_review:
        sql = """
            SELECT i.id, i.category, i.source, i.publish, i.title, i.link,
                   r.timeliness_score, r.game_relevance_score, r.mobile_game_relevance_score,
                   r.ai_relevance_score, r.tech_relevance_score, r.quality_score,
                   r.insight_score, r.depth_score, r.novelty_score,
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
            eva = {
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
            eva["final_score"] = compute_weighted_score(eva, {k: 1.0 for k in DIMENSION_ORDER})
            bonus = float(source_bonus.get(str(row[2] or ""), 0.0))
            if bonus:
                eva["final_score"] = round(max(1.0, min(5.0, eva["final_score"] + bonus)), 2)
                eva["bonus"] = bonus
            evaluation = eva
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


def render_html_general(entries: List[Dict[str, Any]], hours: int) -> str:
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
    def human_time(p: str) -> str:
        dt = try_parse_dt(p)
        if not dt:
            return p
        return dt.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M 北京时间")
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
            final_score = float(evaluation.get("final_score", 0.0))
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
    # category order
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
        cat_entries = sorted(by_cat[cat], key=lambda e: try_parse_dt(e.get("publish","")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        for entry in cat_entries:
            sections.append(_render_article_card(entry))
    tail = "\n</body>\n</html>\n"
    return head + header + "\n".join(sections) + tail


def main() -> None:
    import json
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
        suffix = "general" if args.mode == "general" else "wenhao"
        out_path = OUTPUT_BASE / f"{ts}-{suffix}.html"

    with sqlite3.connect(str(db_path)) as conn:
        if args.mode == "wenhao":
            rows = fetch_rows(conn)
            groups = select_items(rows, args.hours, args.limit)
            if sum(len(v) for v in groups.values()) == 0:
                print("没有符合条件的资讯，未生成文件")
                return
            html = render_html_wenhao(groups, args.hours)
        else:
            # general mode: include all categories; optional source bonus
            source_bonus = DEFAULT_SOURCE_BONUS.copy()
            if args.source_bonus.strip():
                try:
                    overrides = json.loads(args.source_bonus)
                    if isinstance(overrides, dict):
                        for k, v in overrides.items():
                            if isinstance(v, (int, float)):
                                source_bonus[str(k)] = float(v)
                except json.JSONDecodeError:
                    pass
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, args.hours))
            entries = fetch_recent_general(conn, cutoff, source_bonus)
            if not entries:
                print("没有符合条件的资讯，未生成文件")
                return
            html = render_html_general(entries, args.hours)
    if not html:
        print("没有符合条件的资讯，未生成文件")
        return
    out_path.write_text(html, encoding="utf-8")
    print(f"已生成: {out_path}")


if __name__ == "__main__":
    main()
