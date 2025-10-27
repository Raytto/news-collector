from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"
OUT_DIR = DATA_DIR / "feishu-msg"


# 与 info_writer 中保持一致的维度与默认权重
DIMENSION_LABELS: Dict[str, str] = {
    "timeliness": "时效性",
    "game_relevance": "游戏相关性",
    "ai_relevance": "AI相关性",
    "tech_relevance": "科技相关性",
    "quality": "文章质量",
    "insight": "洞察力",
}
DIMENSION_ORDER: Tuple[str, ...] = tuple(DIMENSION_LABELS.keys())
DEFAULT_WEIGHTS: Dict[str, float] = {
    "timeliness": 0.10,
    "game_relevance": 0.25,
    "ai_relevance": 0.10,
    "tech_relevance": 0.05,
    "quality": 0.20,
    "insight": 0.30,
}

DEFAULT_SOURCE_BONUS: Dict[str, float] = {
    "openai.research": 2.0,
    "deepmind": 2.0,
    "qbitai-zhiku": 2.0,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Feishu-friendly markdown summary from SQLite")
    p.add_argument("--db", default=str(DB_PATH), help="SQLite 数据库路径 (默认: data/info.db)")
    p.add_argument("--hours", type=int, default=24, help="时间窗口（小时，默认 24）")
    p.add_argument("--limit-per-cat", type=int, default=10, help="每个分类的最大条目数（默认 10）")
    p.add_argument("--per-source-cap", type=int, default=3, help="每个来源在同一分类内的最大条目数（默认 3；<=0 表示不限制）")
    p.add_argument("--categories", default="game,tech", help="要输出的分类，逗号分隔（默认 game,tech）")
    p.add_argument("--min-score", type=float, default=0.0, help="最小推荐分阈值，低于此值将被过滤（默认 0）")
    p.add_argument("--weights", default="", help="覆盖默认权重的 JSON，例如 {\"timeliness\":0.2,...}")
    p.add_argument("--output", default="", help="输出文件路径，默认 data/feishu-msg/YYYYMMDD-feishu-msg.md")
    p.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    p.add_argument(
        "--source-bonus",
        default="",
        help="JSON mapping for manual bonus per source before clipping (例如 '{\"openai.research\": 2}')",
    )
    return p.parse_args()


def try_parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    # relative forms like "7 days ago"
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
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:  # noqa: E722
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
    score = total / wsum
    # 对齐 info_writer：限制在 [1.0, 5.0] 区间，并保留两位小数
    return round(max(1.0, min(5.0, score)), 2)


def load_rows(conn: sqlite3.Connection) -> List[tuple]:
    sql = """
    SELECT i.id, i.category, i.source, i.publish, i.title, i.link,
           r.timeliness_score, r.game_relevance_score, r.ai_relevance_score,
           r.tech_relevance_score, r.quality_score, r.insight_score,
           r.ai_summary
    FROM info AS i
    LEFT JOIN info_ai_review AS r ON r.info_id = i.id
    """
    return conn.execute(sql).fetchall()


def format_section(title: str, items: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append(f"**{title}**")
    idx = 1
    for it in items:
        score = it.get("score", 0.0)
        bonus = float(it.get("bonus") or 0.0)
        score_label = f"{score:.2f}"
        if bonus:
            sign = "+" if bonus > 0 else ""
            score_label = f"{score_label}({sign}{bonus:g})"
        source = it.get("source", "")
        title_txt = it.get("title", "")
        link = it.get("link", "")
        # 控制标题长度
        if len(title_txt) > 100:
            title_txt = title_txt[:100] + "…"
        # lark_md 链接格式 [text](url)
        line = f"{idx}. (AI推荐:{score_label})({source}) [{title_txt}]({link})"
        lines.append(line)
        idx += 1
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    hours = max(1, int(args.hours))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    weights = DEFAULT_WEIGHTS.copy()
    if args.weights.strip():
        try:
            overrides = json.loads(args.weights)
            if isinstance(overrides, dict):
                for k, v in overrides.items():
                    if k in weights and isinstance(v, (int, float)):
                        weights[k] = float(v)
        except json.JSONDecodeError:
            pass

    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    limit_per_cat = max(1, int(args.limit_per_cat))
    per_source_cap = int(args.per_source_cap)
    min_score = float(args.min_score)
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

    out_path = Path(args.output) if args.output else (OUT_DIR / f"{datetime.now():%Y%m%d}-feishu-msg.md")

    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    with sqlite3.connect(str(db_path)) as conn:
        rows = load_rows(conn)

    # 聚合并评分
    by_cat: Dict[str, List[Dict[str, Any]]] = {c: [] for c in categories}
    seen_links: set[str] = set()
    for row in rows:
        _id, cat, source, publish, title, link, t, g, a, te, q, ins, ai_summary = row
        dt = try_parse_dt(str(publish or ""))
        if not dt or dt < cutoff:
            continue
        link = str(link or "").strip()
        # Prefer AI summary as title if available; fallback to raw title
        title = str(ai_summary or "").strip() or str(title or "").strip()
        if not (link and title):
            continue
        if link in seen_links:
            continue
        seen_links.add(link)
        eva = {
            "timeliness": int(t) if t is not None else 0,
            "game_relevance": int(g) if g is not None else 0,
            "ai_relevance": int(a) if a is not None else 0,
            "tech_relevance": int(te) if te is not None else 0,
            "quality": int(q) if q is not None else 0,
            "insight": int(ins) if ins is not None else 0,
        }
        source_str = str(source or "")
        score = compute_weighted_score(eva, weights)
        bonus = float(source_bonus.get(source_str, 0.0))
        if bonus:
            score = max(1.0, min(5.0, score + bonus))
        if score < min_score:
            continue
        item = {
            "id": int(_id),
            "category": str(cat or ""),
            "source": source_str,
            "publish": str(publish or ""),
            "title": title,
            "link": link,
            "score": score,
            "bonus": bonus,
        }
        if item["category"] in by_cat:
            by_cat[item["category"]].append(item)

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
        # 先对每个来源取评分最高的前三（或 --per-source-cap 指定的数量）
        if per_source_cap > 0:
            per_source_trimmed: List[Dict[str, Any]] = []
            per_source_groups: Dict[str, List[Dict[str, Any]]] = {}
            for it in items:
                per_source_groups.setdefault(it.get("source", ""), []).append(it)
            for group in per_source_groups.values():
                per_source_trimmed.extend(group[:per_source_cap])
        else:
            per_source_trimmed = list(items)

        # 将各来源的候选重新按分数排序后取前 limit_per_cat 条
        per_source_trimmed.sort(
            key=lambda it: (
                float(it.get("score", 0.0)),
                try_parse_dt(it.get("publish", "")) or datetime.min.replace(tzinfo=timezone.utc),
            ),
            reverse=True,
        )
        by_cat[cat] = per_source_trimmed[:limit_per_cat]

    # 生成文本
    sections: List[str] = []
    for cat in categories:
        label = cat.upper()
        items = by_cat.get(cat, [])
        sections.append(format_section(label, items))

    content = "\n".join(sections).strip() + "\n"

    if args.dry_run:
        print(content)
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    print(f"已生成: {out_path} ({sum(len(v) for v in by_cat.values())} 条)")


if __name__ == "__main__":
    main()
