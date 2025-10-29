from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "info.db"

# Script paths
WRITER_DIR = ROOT / "news-collector" / "writer"
DELIVER_DIR = ROOT / "news-collector" / "deliver"
PY = os.environ.get("PYTHON") or sys.executable or "python3"


@dataclass
class Pipeline:
    id: int
    name: str
    enabled: int
    description: str


def _fetchone_dict(cur: sqlite3.Cursor, sql: str, args: Tuple[Any, ...]) -> Dict[str, Any]:
    row = cur.execute(sql, args).fetchone()
    if not row:
        return {}
    cols = [d[0] for d in cur.description]
    return {cols[i]: row[i] for i in range(len(cols))}


def load_pipelines(conn: sqlite3.Connection, name: Optional[str], all_flag: bool) -> list[Pipeline]:
    cur = conn.cursor()
    rows: list[tuple] = []
    if name:
        rows = cur.execute(
            "SELECT id, name, enabled, COALESCE(description,'') FROM pipelines WHERE name=?",
            (name,),
        ).fetchall()
    elif all_flag:
        rows = cur.execute(
            "SELECT id, name, enabled, COALESCE(description,'') FROM pipelines WHERE enabled=1 ORDER BY id",
        ).fetchall()
    else:
        raise SystemExit("必须指定 --name 或 --all")
    return [Pipeline(int(r[0]), str(r[1]), int(r[2]), str(r[3])) for r in rows]


def ensure_output_dir(pipeline_id: int) -> Path:
    out_dir = DATA_DIR / "output" / f"pipeline-{pipeline_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def render_subject(tpl: str, ts: str, date_zh: str) -> str:
    s = (tpl or "").strip()
    s = s.replace("${date_zh}", date_zh)
    s = s.replace("${ts}", ts)
    return s or date_zh


def run_writer(
    pipeline_id: int,
    writer: Dict[str, Any],
    filters: Dict[str, Any],
    out_dir: Path,
    ts: str,
) -> Path:
    """Call the configured writer script to generate output.

    Returns: output file path
    """
    wtype = str(writer.get("type", "")).strip()
    # Respect explicit zeros; only default when key missing
    hours = int(writer["hours"]) if ("hours" in writer and writer.get("hours") is not None) else 24
    weights_json = (writer.get("weights_json") or "").strip()
    bonus_json = (writer.get("bonus_json") or "").strip()
    # Determine whether to pass category filters (0 means not all -> filtered)
    all_cats = int(filters["all_categories"]) if ("all_categories" in filters and filters.get("all_categories") is not None) else 1

    if wtype == "feishu_md":
        out_path = out_dir / f"{ts}.md"
        cmd = [
            PY,
            str(WRITER_DIR / "feishu_writer.py"),
            "--output",
            str(out_path),
        ]
    elif wtype == "wenhao_html":
        # Use unified email_writer with DB-provided categories/weights/bonus
        out_path = out_dir / f"{ts}.html"
        cmd = [
            PY,
            str(WRITER_DIR / "email_writer.py"),
            "--output",
            str(out_path),
        ]
    elif wtype == "info_html":
        # Use unified email_writer in general mode with optional categories/weights/bonus
        out_path = out_dir / f"{ts}.html"
        cmd = [
            PY,
            str(WRITER_DIR / "email_writer.py"),
            "--output",
            str(out_path),
        ]
    else:
        raise SystemExit(f"未知 writer 类型: {wtype}")

    print(f"[PIPELINE {pipeline_id}] Running writer: {' '.join(cmd)}")
    env = os.environ.copy()
    env["PIPELINE_ID"] = str(pipeline_id)
    subprocess.run(cmd, check=True, env=env)
    if not out_path.exists():
        raise SystemExit(f"writer 未生成输出文件: {out_path}")
    return out_path

def deliver_email(html_file: Path, pipeline_id: int) -> None:
    cmd = [
        PY,
        str(DELIVER_DIR / "mail_deliver.py"),
        "--html",
        str(html_file),
    ]
    env = os.environ.copy()
    env["PIPELINE_ID"] = str(pipeline_id)
    print(f"[DELIVER] email via DB (pipeline={pipeline_id}): {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=env)


def deliver_feishu(md_file: Path, pipeline_id: int) -> None:
    env = os.environ.copy()
    env["PIPELINE_ID"] = str(pipeline_id)
    base_cmd = [
        PY,
        str(DELIVER_DIR / "feishu_deliver.py"),
        "--file",
        str(md_file),
        "--as-card",
    ]
    print(f"[DELIVER] feishu via DB (pipeline={pipeline_id}): {' '.join(base_cmd)}")
    subprocess.run(base_cmd, check=True, env=env)


def run_one(conn: sqlite3.Connection, p: Pipeline) -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    date_zh = datetime.now().strftime("%Y年%m月%d日")
    out_dir = ensure_output_dir(p.id)
    cur = conn.cursor()

    filters = _fetchone_dict(
        cur,
        "SELECT all_categories, categories_json, all_src, include_src_json FROM pipeline_filters WHERE pipeline_id=?",
        (p.id,),
    )
    writer = _fetchone_dict(
        cur,
        "SELECT type, hours, weights_json, bonus_json FROM pipeline_writers WHERE pipeline_id=?",
        (p.id,),
    )
    if not writer:
        raise SystemExit(f"pipeline {p.name} 缺少 writer 配置")

    # Validate deliveries: exactly one in either table
    has_email = bool(
        cur.execute("SELECT 1 FROM pipeline_deliveries_email WHERE pipeline_id=?", (p.id,)).fetchone()
    )
    has_feishu = bool(
        cur.execute("SELECT 1 FROM pipeline_deliveries_feishu WHERE pipeline_id=?", (p.id,)).fetchone()
    )
    if has_email and has_feishu:
        raise SystemExit(f"pipeline {p.name} 同时存在 email 与 feishu 投递，拒绝执行")
    if not (has_email or has_feishu):
        raise SystemExit(f"pipeline {p.name} 未配置投递")

    # If writer depends on AI review table, ensure it exists before running
    needs_ai = str(writer.get("type", "")) in {"feishu_md"}
    has_ai_table = bool(cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='info_ai_review'"
    ).fetchone())
    if needs_ai and not has_ai_table:
        print(f"[SKIP] {p.name}: 缺少 info_ai_review 表，跳过需要 AI 评分的数据写作")
        return

    out_path = run_writer(p.id, writer, filters, out_dir, ts)

    if has_email:
        deliver_email(out_path, p.id)
    else:
        # If content_json is present, we could pass --text instead of --file, but current bot expects file for card.
        deliver_feishu(out_path, p.id)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run write/deliver pipelines from SQLite configuration")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", default="", help="Run pipeline by name")
    g.add_argument("--all", action="store_true", help="Run all enabled pipelines sequentially")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库: {DB_PATH}")
    with sqlite3.connect(str(DB_PATH)) as conn:
        ps = load_pipelines(conn, args.name or None, args.all)
        if not ps:
            print("没有匹配的管线可执行")
            return
        for p in ps:
            if int(p.enabled) != 1:
                print(f"[SKIP] {p.name} (disabled)")
                continue
            print(f"[RUN] {p.name} (id={p.id})")
            try:
                run_one(conn, p)
                print(f"[DONE] {p.name}")
            except Exception as e:
                print(f"[FAIL] {p.name}: {e}")


if __name__ == "__main__":
    main()
