from __future__ import annotations

import argparse
import sqlite3
from collections import defaultdict
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


def render_markdown(entries: Iterable[Tuple[str, str, str, str]], hours: int) -> str:
    by_source: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)
    count = 0
    for source, publish, title, link in entries:
        by_source[source].append((publish, title, link))
        count += 1

    now_utc = datetime.now(timezone.utc)
    header = [
        f"# 最近 {hours} 小时资讯汇总",
        "",
        f"生成时间：{now_utc.strftime('%Y-%m-%d %H:%M UTC')}",
        f"合计：{count} 条",
        "",
    ]

    body: List[str] = []
    # Stable order by source name
    for source in sorted(by_source.keys()):
        body.append(f"## {source}")
        body.append("")
        for publish, title, link in by_source[source]:
            body.append(f"- {human_time(publish)} — [{title}]({link})")
        body.append("")

    return "\n".join(header + body).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    hours = max(1, int(args.hours))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库: {DB_PATH}")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_path = OUTPUT_DIR / f"{ts}-info.md"

    with sqlite3.connect(str(DB_PATH)) as conn:
        entries = fetch_recent(conn, cutoff)
        doc = render_markdown(entries, hours)
        out_path.write_text(doc, encoding="utf-8")

    print(f"已生成: {out_path} ({len(entries)} 条)")


if __name__ == "__main__":
    main()
