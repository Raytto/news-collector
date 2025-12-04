from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT.parent / "data"
DB_PATH = DATA_DIR / "info.db"

DEFAULT_LIMIT_PER_CATEGORY = 5
DEFAULT_PER_SOURCE_CAP = 2


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate Feishu markdown for minigame pipeline")
    p.add_argument("--db", default=str(DB_PATH), help="SQLite 数据库路径 (默认: data/info.db)")
    p.add_argument("--hours", type=int, default=48, help="时间窗口（小时）")
    p.add_argument("--output", default="", help="输出文件路径")
    return p.parse_args()


def _env_pipeline_id() -> Optional[int]:
    raw = (os.getenv("PIPELINE_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _load_pipeline_meta(conn: sqlite3.Connection, pid: int) -> Dict[str, Any]:
    cur = conn.cursor()
    meta: Dict[str, Any] = {"evaluator_key": "legou_minigame_evaluator"}
    try:
        row = cur.execute(
            "SELECT evaluator_key FROM pipelines WHERE id=?",
            (pid,),
        ).fetchone()
        if row and row[0]:
            meta["evaluator_key"] = str(row[0])
    except sqlite3.OperationalError:
        pass
    return meta


def _load_pipeline_cfg(conn: sqlite3.Connection, pid: int) -> Dict[str, Any]:
    cur = conn.cursor()
    cfg: Dict[str, Any] = {}
    try:
        row = cur.execute(
            """
            SELECT all_categories, COALESCE(categories_json,''), COALESCE(include_src_json,'')
            FROM pipeline_filters
            WHERE pipeline_id=?
            ORDER BY rowid DESC LIMIT 1
            """,
            (pid,),
        ).fetchone()
        if row:
            cfg["all_categories"] = int(row[0]) if row[0] is not None else 1
            cfg["categories_json"] = str(row[1] or "")
            cfg["include_src_json"] = str(row[2] or "")
    except sqlite3.OperationalError:
        pass
    try:
        row = cur.execute(
            """
            SELECT hours, limit_per_category, per_source_cap
            FROM pipeline_writers WHERE pipeline_id=? ORDER BY rowid DESC LIMIT 1
            """,
            (pid,),
        ).fetchone()
        if row:
            cfg["hours"] = int(row[0]) if row[0] is not None else None
            cfg["limit_per_category"] = row[1]
            cfg["per_source_cap"] = int(row[2]) if row[2] is not None else None
    except sqlite3.OperationalError:
        pass
    return cfg


def parse_limit_config(raw: Any) -> Tuple[Dict[str, int], int]:
    limit_map: Dict[str, int] = {}
    default_limit = DEFAULT_LIMIT_PER_CATEGORY
    if raw is None or raw == "":
        return limit_map, default_limit
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="ignore")
    if isinstance(raw, (int, float)):
        return limit_map, int(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return limit_map, default_limit
        try:
            parsed = json.loads(s)
        except json.JSONDecodeError:
            try:
                default_limit = int(float(s))
            except Exception:
                return limit_map, default_limit
            return limit_map, default_limit
        else:
            raw = parsed
    if isinstance(raw, dict):
        temp_default = default_limit
        for k, v in raw.items():
            if k is None:
                continue
            key = str(k).strip()
            if not key:
                continue
            try:
                iv = int(v)
            except Exception:
                continue
            if key.lower() == "default":
                temp_default = iv
            else:
                limit_map[key] = iv
        default_limit = temp_default
    return limit_map, default_limit


def limit_for_category(limit_map: Dict[str, int], default_limit: int, category: str) -> int:
    return int(limit_map.get(category, default_limit))


def load_articles(
    conn: sqlite3.Connection,
    evaluator_key: str,
    categories: List[str],
    include_sources: Set[str],
    cutoff: datetime,
) -> List[Dict[str, Any]]:
    placeholders = ",".join(["?"] * len(categories)) if categories else ""
    params: List[Any] = [evaluator_key]
    where_fragments = ["r.evaluator_key=?"]
    if categories:
        where_fragments.append(f"i.category IN ({placeholders})")
        params.extend(categories)
    rows = conn.execute(
        f"""
        SELECT i.id, i.title, i.link, i.store_link, i.source, i.category, i.publish, i.img_link,
               r.ai_summary, r.ai_comment, r.final_score
        FROM info AS i
        JOIN info_ai_review AS r ON r.info_id = i.id
        WHERE {' AND '.join(where_fragments)}
        ORDER BY r.final_score DESC, i.publish DESC
        """,
        tuple(params),
    ).fetchall()
    items: List[Dict[str, Any]] = []
    seen_sources: Dict[Tuple[str, str], int] = {}
    for row in rows:
        (
            info_id,
            title,
            link,
            store_link,
            source,
            category,
            publish,
            img_link,
            ai_summary,
            ai_comment,
            final_score,
        ) = row
        publish_dt = None
        try:
            publish_dt = datetime.fromisoformat(str(publish).replace("Z", "+00:00"))
        except Exception:
            publish_dt = None
        if publish_dt and publish_dt.tzinfo is None:
            publish_dt = publish_dt.replace(tzinfo=timezone.utc)
        if publish_dt and publish_dt < cutoff:
            continue
        src = str(source or "")
        cat = str(category or "")
        if categories and cat not in categories and src not in include_sources:
            continue
        items.append(
            {
                "id": int(info_id),
                "title": str(title or ""),
                "link": str(link or ""),
                "store_link": str(store_link or ""),
                "source": src,
                "category": cat,
                "publish": str(publish or ""),
                "img_link": str(img_link or ""),
                "ai_summary": str(ai_summary or ""),
                "ai_comment": str(ai_comment or ""),
                "final_score": float(final_score) if final_score is not None else 0.0,
            }
        )
    return items


def apply_limits(
    items: List[Dict[str, Any]],
    limit_map: Dict[str, int],
    limit_default: int,
    per_source_cap: int,
) -> List[Dict[str, Any]]:
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    for item in items:
        cat = item.get("category", "")
        by_cat.setdefault(cat, []).append(item)
    result: List[Dict[str, Any]] = []
    for cat, lst in by_cat.items():
        cap = limit_for_category(limit_map, limit_default, cat)
        pruned: List[Dict[str, Any]] = []
        source_counts: Dict[str, int] = {}
        for it in lst:
            if cap and len(pruned) >= cap:
                break
            src = it.get("source", "")
            if per_source_cap and per_source_cap > 0:
                if source_counts.get(src, 0) >= per_source_cap:
                    continue
                source_counts[src] = source_counts.get(src, 0) + 1
            pruned.append(it)
        result.extend(pruned)
    return result


def score_to_stars(score: Any, max_stars: int = 5) -> str:
    try:
        val = float(score)
    except Exception:
        return "未评分"
    if val <= 0:
        return "未评分"
    clamped = max(1, min(max_stars, int(round(val))))
    return "⭐" * clamped


def render_markdown(items: List[Dict[str, Any]], hours: int) -> str:
    lines: List[str] = []
    for idx, it in enumerate(items, start=1):
        score = score_to_stars(it.get("final_score", 0))
        title = it.get("title", "")
        summary = it.get("ai_summary", "")
        comment = it.get("ai_comment", "")
        link = it.get("link", "")
        src = it.get("source", "") or "未知来源"
        img = it.get("img_link", "").strip()
        source_part = f"[{src}]({link})" if link else src
        img_line = f"\n   - 封面：![]({img})" if img else ""
        lines.append(
            f"{idx}. (AI结合评估:{score}) {title}（{source_part}）\n"
            f"    - 游戏简介：{summary}\n"
            f"    - 结合猜想：{comment}"
            f"{img_line}"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    if not db_path.exists():
        raise SystemExit(f"数据库不存在: {db_path}")

    pid = _env_pipeline_id()
    limit_map: Dict[str, int] = {}
    limit_default = DEFAULT_LIMIT_PER_CATEGORY
    per_source_cap = DEFAULT_PER_SOURCE_CAP
    categories: List[str] = []
    include_sources: Set[str] = set()
    effective_hours = max(1, int(args.hours))
    evaluator_key = "legou_minigame_evaluator"

    with sqlite3.connect(str(db_path)) as conn:
        if pid is not None:
            cfg = _load_pipeline_cfg(conn, pid)
            meta = _load_pipeline_meta(conn, pid)
            evaluator_key = str(meta.get("evaluator_key") or evaluator_key)
            if isinstance(cfg.get("hours"), int) and cfg.get("hours"):
                effective_hours = int(cfg["hours"])
            if cfg.get("limit_per_category") not in (None, ""):
                limit_map, limit_default = parse_limit_config(cfg.get("limit_per_category"))
            if cfg.get("per_source_cap") is not None:
                try:
                    per_source_cap = int(cfg.get("per_source_cap") or 0)
                except Exception:
                    pass
            try:
                if int(cfg.get("all_categories", 1) or 1) == 0:
                    cats = json.loads(cfg.get("categories_json") or "[]")
                    if isinstance(cats, list):
                        categories = [str(c).strip() for c in cats if str(c).strip()]
            except Exception:
                pass
            if cfg.get("include_src_json"):
                try:
                    parsed = json.loads(cfg.get("include_src_json") or "[]")
                    if isinstance(parsed, list):
                        include_sources = {str(x).strip() for x in parsed if str(x).strip()}
                except json.JSONDecodeError:
                    pass

        cutoff = datetime.now(timezone.utc) - timedelta(hours=effective_hours)
        articles = load_articles(conn, evaluator_key, categories, include_sources, cutoff)
        if not articles:
            print("没有符合条件的记录，未生成文件")
            return
        articles = apply_limits(articles, limit_map, limit_default, per_source_cap)
        if not articles:
            print("没有符合条件的记录，未生成文件")
            return
        md = render_markdown(articles, effective_hours)

    out_path = Path(args.output) if args.output else DATA_DIR / "feishu-msg" / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-legou.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"已生成: {out_path}")


if __name__ == "__main__":
    main()
