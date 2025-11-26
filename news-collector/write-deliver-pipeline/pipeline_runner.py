from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
import re
import html as htmllib
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import textwrap
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore


ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "info.db"

DATE_PLACEHOLDER_VARIANTS = ("${date_zh}", "$(date_zh)", "${data_zh}", "$(data_zh)")
TS_PLACEHOLDER_VARIANTS = ("${ts}", "$(ts)")

# Script paths
WRITER_DIR = ROOT / "news-collector" / "writer"
DELIVER_DIR = ROOT / "news-collector" / "deliver"
PY = os.environ.get("PYTHON") or sys.executable or "python3"

# Weekday helpers (runner-local domain module)
try:
    # If executed as a package module
    from .weekday import (
        coerce as weekday_coerce,
        is_allowed as weekday_is_allowed,
        normalize as weekday_normalize,
    )
except Exception:  # executed as a script; fall back to path import
    try:
        import importlib.util as _importlib_util
        _wk_path = Path(__file__).with_name("weekday.py")
        _spec = _importlib_util.spec_from_file_location("_runner_weekday", str(_wk_path))
        if _spec and _spec.loader:
            _mod = _importlib_util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)  # type: ignore[arg-type]
            weekday_coerce = _mod.coerce  # type: ignore[attr-defined]
            weekday_is_allowed = _mod.is_allowed  # type: ignore[attr-defined]
            weekday_normalize = _mod.normalize  # type: ignore[attr-defined]
        else:  # pragma: no cover
            raise ImportError("cannot load weekday helpers")
    except Exception as _e:  # pragma: no cover
        raise SystemExit(f"Failed to load weekday helpers: {_e}")


@dataclass
class Pipeline:
    id: int
    name: str
    enabled: int
    description: str
    weekdays_json: Optional[str] = None


def _fetchone_dict(cur: sqlite3.Cursor, sql: str, args: Tuple[Any, ...]) -> Dict[str, Any]:
    row = cur.execute(sql, args).fetchone()
    if not row:
        return {}
    cols = [d[0] for d in cur.description]
    return {cols[i]: row[i] for i in range(len(cols))}


def load_pipelines(conn: sqlite3.Connection, name: Optional[str], all_flag: bool, debug_only: bool = False) -> list[Pipeline]:
    cur = conn.cursor()
    rows: list[tuple] = []
    if name:
        # Try selecting weekdays_json; fallback if column missing
        try:
            rows = cur.execute(
                "SELECT id, name, enabled, COALESCE(description,''), weekdays_json FROM pipelines WHERE name=?",
                (name,),
            ).fetchall()
            with_weekdays = True
        except sqlite3.OperationalError:
            rows = cur.execute(
                "SELECT id, name, enabled, COALESCE(description,'') FROM pipelines WHERE name=?",
                (name,),
            ).fetchall()
            with_weekdays = False
    elif all_flag:
        # When debug_only is set, select by debug flag instead of enabled
        if debug_only:
            # If debug_enabled column is missing, treat as empty set to avoid crashing
            try:
                rows = cur.execute(
                    "SELECT id, name, enabled, COALESCE(description,''), weekdays_json FROM pipelines WHERE debug_enabled=1 ORDER BY id",
                ).fetchall()
                with_weekdays = True
            except sqlite3.OperationalError:
                try:
                    rows = cur.execute(
                        "SELECT id, name, enabled, COALESCE(description,'') FROM pipelines WHERE debug_enabled=1 ORDER BY id",
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                with_weekdays = False
        else:
            try:
                rows = cur.execute(
                    "SELECT id, name, enabled, COALESCE(description,''), weekdays_json FROM pipelines WHERE enabled=1 ORDER BY id",
                ).fetchall()
                with_weekdays = True
            except sqlite3.OperationalError:
                rows = cur.execute(
                    "SELECT id, name, enabled, COALESCE(description,'') FROM pipelines WHERE enabled=1 ORDER BY id",
                ).fetchall()
                with_weekdays = False
    else:
        raise SystemExit("必须指定 --name 或 --all")
    if not rows:
        return []
    if with_weekdays:
        return [Pipeline(int(r[0]), str(r[1]), int(r[2]), str(r[3]), r[4] if len(r) > 4 else None) for r in rows]
    return [Pipeline(int(r[0]), str(r[1]), int(r[2]), str(r[3]), None) for r in rows]


def _allowed_today(weekdays_json_text: Optional[str]) -> tuple[bool, str]:
    """Return (allowed, debug_msg) using unified weekday helpers."""
    if weekdays_json_text is None or str(weekdays_json_text).strip() == "":
        return True, "no weekday restriction"
    tz_name = os.getenv("PIPELINE_TZ", "Asia/Shanghai")
    if ZoneInfo is not None:
        try:
            today = datetime.now(ZoneInfo(tz_name)).isoweekday()
        except Exception:
            today = datetime.now().isoweekday()
    else:
        today = datetime.now().isoweekday()
    days = weekday_normalize(weekday_coerce(weekdays_json_text)) or []
    if not days:
        return False, f"weekday not allowed (today={today}; allowed=[] )"
    if not weekday_is_allowed(days, tz=tz_name):
        return False, f"weekday not allowed (today={today}; allowed={days})"
    return True, f"weekday allowed (today={today}; allowed={days})"


def ensure_output_dir(pipeline_id: int) -> Path:
    out_dir = DATA_DIR / "output" / f"pipeline-{pipeline_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def render_subject(tpl: str, ts: str, date_zh: str) -> str:
    subject = str(tpl or "")
    for placeholder in TS_PLACEHOLDER_VARIANTS:
        subject = subject.replace(placeholder, ts)
    for placeholder in DATE_PLACEHOLDER_VARIANTS:
        subject = subject.replace(placeholder, "")
    subject = subject.strip()
    return f"{subject}{date_zh}" if subject else date_zh


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

    # Map writer types to concrete scripts. We unify all email HTML
    # generation to email_writer.py, including the legacy "wenhao_html".
    if wtype == "feishu_md":
        out_path = out_dir / f"{ts}.md"
        cmd = [
            PY,
            str(WRITER_DIR / "feishu_writer.py"),
            "--output",
            str(out_path),
            "--hours",
            str(hours),
        ]
    elif wtype in {"info_html", "wenhao_html"}:
        # Unified email writer for all HTML digests
        out_path = out_dir / f"{ts}.html"
        cmd = [
            PY,
            str(WRITER_DIR / "email_writer.py"),
            "--output",
            str(out_path),
            "--hours",
            str(hours),
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
    # If caller enforces plain-only mode, pass explicit flag and dump RFC message
    plain_only = (env.get("MAIL_PLAIN_ONLY", "").strip().lower() in {"1", "true", "yes", "on"})
    if plain_only:
        cmd.append("--plain-only")
        dump_path = str(html_file.with_suffix(".eml"))
        cmd.extend(["--dump-msg", dump_path])
    print(f"[DELIVER] email via DB (pipeline={pipeline_id}): {' '.join(cmd)}")
    subprocess.run(cmd, check=True, env=env)


def _write_plain_copy_if_needed(html_file: Path) -> Path | None:
    """When MAIL_PLAIN_ONLY is enabled, write a .txt copy derived from HTML.

    This mirrors the actual sent content in plain-text mode so that the
    recorded artifact matches delivery format.
    """
    if os.getenv("MAIL_PLAIN_ONLY", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        def html_to_wrapped_text(html: str, width: int = 78) -> str:
            x = html
            x = re.sub(r"(?i)<br\s*/?>", "\n", x)
            x = re.sub(r"(?i)</(p|div|section|article|h[1-6]|tr)>", "\n", x)
            x = re.sub(r"(?i)<li[^>]*>", "\n- ", x)
            x = re.sub(r"(?i)</li>", "\n", x)
            x = re.sub(r"(?is)<script.*?</script>", " ", x)
            x = re.sub(r"(?is)<style.*?</style>", " ", x)
            x = re.sub(r"<[^>]+>", " ", x)
            x = htmllib.unescape(x)
            x = re.sub(r"[\t\x0b\x0c\r ]+", " ", x)
            x = re.sub(r"\n{3,}", "\n\n", x)
            parts = [p.strip() for p in x.split("\n\n")]
            wrapped = []
            for p in parts:
                if not p:
                    continue
                wrapped.append(textwrap.fill(p, width=78, break_long_words=False, replace_whitespace=False))
            return ("\n\n".join(wrapped).strip() or "(digest content)")

        body = html_file.read_text(encoding="utf-8", errors="ignore")
        txt = html_to_wrapped_text(body)
        txt_path = html_file.with_suffix(".txt")
        txt_path.write_text(txt, encoding="utf-8")
        print(f"[DELIVER] wrote plain copy: {txt_path}")
        return txt_path
    except Exception as e:
        print(f"[WARN] failed to write plain copy for {html_file}: {e}")
        return None


def deliver_feishu(md_file: Path, pipeline_id: int, delivery: Dict[str, Any]) -> None:
    env = os.environ.copy()
    env["PIPELINE_ID"] = str(pipeline_id)
    base_cmd = [
        PY,
        str(DELIVER_DIR / "feishu_deliver.py"),
        "--file",
        str(md_file),
        "--as-card",
    ]
    delivery = delivery or {}
    target_all = int(delivery.get("to_all_chat") or 0) == 1
    cmd = list(base_cmd)
    if target_all:
        cmd.append("--to-all")
    else:
        chat_id = str(delivery.get("chat_id") or "").strip()
        if not chat_id:
            raise SystemExit(f"pipeline {pipeline_id} 缺少 Feishu chat_id 配置")
        cmd.extend(["--chat-id", chat_id])
    log_cmd = list(cmd)
    if not target_all and "--chat-id" in log_cmd:
        idx = log_cmd.index("--chat-id")
        if idx >= 0 and idx + 1 < len(log_cmd):
            log_cmd[idx + 1] = "<hidden>"
    print(f"[DELIVER] feishu via DB (pipeline={pipeline_id}): {' '.join(log_cmd)}")
    subprocess.run(cmd, check=True, env=env)


def run_one(conn: sqlite3.Connection, p: Pipeline) -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    date_zh = datetime.now().strftime("%Y年%m月%d日")
    out_dir = ensure_output_dir(p.id)
    cur = conn.cursor()

    filters = _fetchone_dict(
        cur,
        "SELECT all_categories, categories_json, all_src, include_src_json "
        "FROM pipeline_filters WHERE pipeline_id=? ORDER BY rowid DESC LIMIT 1",
        (p.id,),
    )
    writer = _fetchone_dict(
        cur,
        "SELECT type, hours, weights_json, bonus_json "
        "FROM pipeline_writers WHERE pipeline_id=? ORDER BY rowid DESC LIMIT 1",
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
    feishu_delivery: Dict[str, Any] = {}
    if has_email and has_feishu:
        raise SystemExit(f"pipeline {p.name} 同时存在 email 与 feishu 投递，拒绝执行")
    if not (has_email or has_feishu):
        raise SystemExit(f"pipeline {p.name} 未配置投递")
    if has_feishu:
        feishu_delivery = _fetchone_dict(
            cur,
            "SELECT to_all_chat, chat_id FROM pipeline_deliveries_feishu WHERE pipeline_id=?",
            (p.id,),
        )

    # If writer depends on AI review table, ensure it exists before running
    writer_type = str(writer.get("type", "")).strip()
    needs_ai = writer_type in {"feishu_md", "info_html", "wenhao_html"}
    if needs_ai:
        required_tables = ("ai_metrics", "info_ai_scores", "info_ai_review")
        missing = [
            tbl
            for tbl in required_tables
            if not cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone()
        ]
        if missing:
            print(f"[SKIP] {p.name}: 缺少 {', '.join(missing)} 表，跳过需要 AI 评分的数据写作")
            return

    out_path = run_writer(p.id, writer, filters, out_dir, ts)

    # If configured to send plain-only, also persist a .txt copy next to HTML
    _write_plain_copy_if_needed(out_path)

    if has_email:
        deliver_email(out_path, p.id)
    else:
        # If content_json is present, we could pass --text instead of --file, but current bot expects file for card.
        deliver_feishu(out_path, p.id, feishu_delivery)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run write/deliver pipelines from SQLite configuration")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--name", default="", help="Run pipeline by name")
    g.add_argument("--all", action="store_true", help="Run all enabled pipelines sequentially")
    p.add_argument(
        "--debug-only",
        action="store_true",
        help="Run pipelines marked debug_enabled=1 instead of enabled=1 when used with --all",
    )
    p.add_argument(
        "--ignore-weekday",
        action="store_true",
        help="Ignore per-pipeline weekday restriction when deciding to run",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    if not DB_PATH.exists():
        raise SystemExit(f"未找到数据库: {DB_PATH}")
    with sqlite3.connect(str(DB_PATH)) as conn:
        ps = load_pipelines(conn, args.name or None, args.all, debug_only=bool(getattr(args, "debug_only", False)))
        if not ps:
            print("没有匹配的管线可执行")
            return
        for p in ps:
            debug_only = bool(getattr(args, "debug_only", False))
            # In debug-only mode, run purely by debug flag; otherwise respect enabled toggle.
            if not debug_only and int(p.enabled) != 1:
                print(f"[SKIP] {p.name} (disabled)")
                continue
            # Weekday gating is ignored for debug runs.
            if not debug_only and not getattr(args, "ignore_weekday", False) and os.getenv("FORCE_RUN", "").strip().lower() not in {"1", "true", "yes", "on"}:
                ok, why = _allowed_today(p.weekdays_json)
                if not ok:
                    print(f"[SKIP] {p.name}: {why}")
                    continue
                # Emit debug line when allowed if DEBUG_WEEKDAY is enabled
                if str(os.getenv("DEBUG_WEEKDAY", "")).strip().lower() in {"1", "true", "yes", "on"}:
                    print(f"[DEBUG] {p.name}: {why}")
            print(f"[RUN] {p.name} (id={p.id})")
            try:
                run_one(conn, p)
                print(f"[DONE] {p.name}")
            except SystemExit as e:
                print(f"[FAIL] {p.name}: {e}")
            except Exception as e:
                print(f"[FAIL] {p.name}: {e}")


if __name__ == "__main__":
    main()
