from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


CATEGORIES: Tuple[str, ...] = ("humanities", "tech")
DEFAULT_LIMIT = 10
DEFAULT_HOURS = 24
DEFAULT_WEIGHTS: Dict[str, float] = {
    "depth": 0.8,
    "novelty": 0.6,
    "timeliness": 0.6,
}


WRITER_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WRITER_DIR.parent
DATA_DIR = PROJECT_ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
OUTPUT_BASE = DATA_DIR / "output" / "wenhao"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Wenhao HTML digest for humanities and tech articles.",
    )
    parser.add_argument("--hours", type=int, default=DEFAULT_HOURS, help="时间窗口，默认 24 小时")
    parser.add_argument("--output", type=str, default="", help="输出 HTML 路径；留空自动生成")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="每个分类的最大条目数 (默认 10)")
    parser.add_argument("--db", type=str, default=str(DB_PATH), help="SQLite 数据库路径")
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


def compute_score(depth: int, novelty: int, timeliness: int) -> float:
    weights = DEFAULT_WEIGHTS
    weighted = (
        depth * weights["depth"]
        + novelty * weights["novelty"]
        + timeliness * weights["timeliness"]
    )
    total = sum(weights.values())
    if total <= 0:
        return 0.0
    return round(max(1.0, min(5.0, weighted / total)), 2)


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
    grouped: Dict[str, List[Dict[str, Any]]] = {cat: [] for cat in CATEGORIES}

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


def render_html(groups: Dict[str, List[Dict[str, Any]]], hours: int) -> str:
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
        out_path = OUTPUT_BASE / f"{ts}-wenhao.html"

    with sqlite3.connect(str(db_path)) as conn:
        rows = fetch_rows(conn)
    groups = select_items(rows, args.hours, args.limit)
    if sum(len(v) for v in groups.values()) == 0:
        print("没有符合条件的资讯，未生成文件")
        return
    html = render_html(groups, args.hours)
    if not html:
        print("没有符合条件的资讯，未生成文件")
        return
    out_path.write_text(html, encoding="utf-8")
    print(f"已生成: {out_path}")


if __name__ == "__main__":
    main()

