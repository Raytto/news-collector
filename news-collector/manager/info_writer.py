from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
from html import escape
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
OUTPUT_DIR = DATA_DIR / "output"


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


def fetch_recent(conn: sqlite3.Connection, cutoff: datetime) -> List[Tuple[str, str, str, str]]:
    # Load all rows; filter by parseable publish >= cutoff in Python to handle mixed formats.
    cur = conn.cursor()
    cur.execute("SELECT source, publish, title, link FROM info")
    rows = cur.fetchall()
    results: List[Tuple[str, str, str, str]] = []
    for source, publish, title, link in rows:
        dt = try_parse_dt(publish or "")
        if not dt:
            continue
        if dt >= cutoff:
            results.append((source or "", publish or "", title or "", link or ""))

    # Sort by publish desc (parseable ones only are included)
    results.sort(key=lambda r: try_parse_dt(r[1]) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return results


def human_time(publish: str) -> str:
    dt = try_parse_dt(publish)
    if not dt:
        return publish
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def render_html(entries: Iterable[Tuple[str, str, str, str]], hours: int) -> str:
    by_source: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    count = 0
    for source, publish, title, link in entries:
        by_source[source].append((publish, title, link))
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
    h2 {{ font-size: 18px; margin: 20px 0 8px; padding-top: 8px; border-top: 1px solid #eee; }}
    ul {{ list-style: disc; margin: 8px 0 16px 20px; padding: 0; }}
    li {{ margin: 6px 0; }}
    time {{ color: #555; }}
    a {{ color: #0a5; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
  </style>
  </head>
<body>
"""

    header = f"""
<h1>最近 {hours} 小时资讯汇总</h1>
<p class=\"meta\">生成时间：{now_utc.strftime('%Y-%m-%d %H:%M UTC')} · 合计：{count} 条</p>
"""

    sections: List[str] = []
    for source in sorted(by_source.keys()):
        sections.append(f"<h2>{escape(source)}</h2>")
        sections.append("<ul>")
        for publish, title, link in by_source[source]:
            dt = try_parse_dt(publish)
            iso = dt.isoformat() if dt else escape(publish)
            shown = human_time(publish) if dt else escape(publish)
            t = escape(title)
            href = escape(link)
            sections.append(
                f"<li><time datetime=\"{iso}\">{shown}</time> — "
                f"<a href=\"{href}\" target=\"_blank\" rel=\"noopener noreferrer\">{t}</a></li>"
            )
        sections.append("</ul>")

    tail = "\n</body>\n</html>\n"
    return head + header + "\n".join(sections) + tail


def main() -> None:
    args = parse_args()
    hours = max(1, int(args.hours))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库: {DB_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = OUTPUT_DIR / f"{ts}-info.html"

    with sqlite3.connect(str(DB_PATH)) as conn:
        entries = fetch_recent(conn, cutoff)
        doc = render_html(entries, hours)
        out_path.write_text(doc, encoding="utf-8")

    print(f"已生成: {out_path} ({len(entries)} 条)")


if __name__ == "__main__":
    main()
