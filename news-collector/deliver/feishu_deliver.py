from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
import time
from typing import Optional

import requests


DEFAULT_API_BASE = "https://open.feishu.cn"
TIMEOUT = 10


@dataclass
class FeishuConfig:
    api_base: str
    app_id: str
    app_secret: str
    default_chat_id: Optional[str]


def load_config() -> FeishuConfig:
    api_base = (os.getenv("FEISHU_API_BASE") or DEFAULT_API_BASE).rstrip("/")
    app_id = (os.getenv("FEISHU_APP_ID") or "").strip()
    app_secret = (os.getenv("FEISHU_APP_SECRET") or "").strip()
    default_chat_id = (os.getenv("FEISHU_DEFAULT_CHAT_ID") or "").strip() or None
    if not app_id or not app_secret:
        raise SystemExit(
            "缺少 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量，请在 environment.yml variables 中配置"
        )
    return FeishuConfig(
        api_base=api_base,
        app_id=app_id,
        app_secret=app_secret,
        default_chat_id=default_chat_id,
    )


def _env_pipeline_id() -> int | None:
    raw = (os.getenv("PIPELINE_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _load_feishu_delivery_from_db(db_path: Path, pipeline_id: int) -> dict:
    """Load Feishu delivery config for a pipeline from SQLite."""
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT app_id, app_secret, to_all_chat, chat_id, COALESCE(title_tpl,'') FROM pipeline_deliveries_feishu WHERE pipeline_id=?",
                (pipeline_id,),
            ).fetchone()
            if not row:
                return {}
            return {
                "app_id": str(row[0] or ""),
                "app_secret": str(row[1] or ""),
                "to_all_chat": int(row[2] or 0),
                "chat_id": (str(row[3] or "").strip() or None),
                "title_tpl": str(row[4] or ""),
            }
    except Exception:
        return {}


def _render_title_from_tpl(tpl: str | None) -> str:
    ts = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    date_zh = __import__("datetime").datetime.now().strftime("%Y年%m月%d日")
    s = (tpl or "").strip()
    s = s.replace("${date_zh}", date_zh)
    s = s.replace("${ts}", ts)
    return s or "通知"


def get_tenant_access_token(cfg: FeishuConfig) -> str:
    url = f"{cfg.api_base}/open-apis/auth/v3/tenant_access_token/internal"
    resp = requests.post(
        url,
        json={"app_id": cfg.app_id, "app_secret": cfg.app_secret},
        timeout=TIMEOUT,
    )
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise SystemExit(f"获取 tenant_access_token 失败: {exc}")
    data = resp.json()
    if int(data.get("code", -1)) != 0 or not data.get("tenant_access_token"):
        raise SystemExit(f"获取 tenant_access_token 失败: {data}")
    return str(data["tenant_access_token"]).strip()


def send_text(cfg: FeishuConfig, token: str, chat_id: str, text: str) -> dict:
    url = f"{cfg.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "receive_id": chat_id,
        "msg_type": "text",
        # Feishu 要求 content 为字符串化的 JSON
        "content": json.dumps({"text": text}, ensure_ascii=False),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise SystemExit(f"发送消息失败(HTTP): {exc}")
    data = resp.json()
    if int(data.get("code", -1)) != 0:
        raise SystemExit(f"发送消息失败(API): {data}")
    return data


def send_card_md(cfg: FeishuConfig, token: str, chat_id: str, md_text: str, title: str = "通知") -> dict:
    """Send an interactive card with Markdown content.

    Note: Some clients render lists/numbering more consistently when using
    the `markdown` element instead of `div + lark_md`. Use the `markdown`
    element here for better compatibility across Feishu apps.
    """
    url = f"{cfg.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": title[:80]}},
        "elements": [
            {"tag": "markdown", "content": md_text[:18000]}
        ],
    }
    payload = {
        "receive_id": chat_id,
        "msg_type": "interactive",
        "content": json.dumps(card, ensure_ascii=False),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    try:
        resp.raise_for_status()
    except Exception as exc:
        raise SystemExit(f"发送卡片失败(HTTP): {exc}")
    data = resp.json()
    if int(data.get("code", -1)) != 0:
        raise SystemExit(f"发送卡片失败(API): {data}")
    return data


def _md_to_post_paragraphs(md_text: str) -> list[list[dict]]:
    """Very lightweight Markdown -> Feishu post paragraphs.

    - Split by lines; collapse multiple blank lines to one empty line.
    - Keep list markers as plain text so the client renders in a readable way.
    """
    lines = md_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    paragraphs: list[list[dict]] = []
    for raw in lines:
        s = raw.rstrip()
        if not s:
            # 跳过完全空行，避免生成空段落导致 400 错误
            continue
        # Trim heading markers to keep it clean in bubble
        if s.lstrip().startswith("#"):
            s = s.lstrip("# ")
        paragraphs.append([{ "tag": "text", "text": s }])
    if not paragraphs:
        paragraphs = [[{"tag": "text", "text": md_text.strip()}]]
    return paragraphs


def send_post(cfg: FeishuConfig, token: str, chat_id: str, md_text: str, title: str = "通知") -> dict:
    """Send a rich-text post (chat bubble)."""
    url = f"{cfg.api_base}/open-apis/im/v1/messages?receive_id_type=chat_id"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    paragraphs = _md_to_post_paragraphs(md_text[:20000])
    post_content = {
        "zh_cn": {
            "title": title[:80],
            "content": paragraphs,
        }
    }
    payload = {
        "receive_id": chat_id,
        "msg_type": "post",
        "content": json.dumps({"post": post_content}, ensure_ascii=False),
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    # 优先读取返回体以便输出具体错误
    try:
        data = resp.json()
    except Exception:
        resp.raise_for_status()
        data = {"code": 0}
    if resp.status_code >= 400:
        raise SystemExit(f"发送 post 失败(HTTP {resp.status_code}): {data}")
    if int(data.get("code", -1)) != 0:
        raise SystemExit(f"发送 post 失败(API): {data}")
    return data


def _list_all_chats(cfg: FeishuConfig, token: str, limit: int = 200) -> list[dict]:
    """List chats visible to the bot (requires im:chat:readonly)."""
    items: list[dict] = []
    page_token = None
    fetched = 0
    while True:
        params = {"page_size": min(50, limit - fetched)}
        if page_token:
            params["page_token"] = page_token
        url = f"{cfg.api_base}/open-apis/im/v1/chats"
        headers = {"Authorization": f"Bearer {token}"}
        resp = requests.get(url, headers=headers, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        if int(data.get("code", -1)) != 0:
            raise SystemExit(f"获取群列表失败(API): {data}")
        page = (data.get("data") or {})
        batch = page.get("items") or []
        items.extend(batch)
        fetched += len(batch)
        page_token = page.get("page_token")
        if not page_token or fetched >= limit:
            break
    return items


def _resolve_chat_id_by_name(cfg: FeishuConfig, token: str, name: str) -> str:
    """Resolve chat_id by case-insensitive name match among visible chats."""
    target = name.strip().lower()
    if not target:
        return ""
    # fetch up to 200 chats and match locally
    for it in _list_all_chats(cfg, token, limit=200):
        nm = (it.get("name") or "").strip().lower()
        if not nm:
            continue
        if nm == target or target in nm:
            cid = (it.get("chat_id") or "").strip()
            if cid:
                return cid
    return ""


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="向飞书群发送文本消息；可单群、按名称解析，或向所有群群发；支持从文件读取内容",
    )
    p.add_argument("--chat-id", default="", help="目标群 chat_id（缺省使用 FEISHU_DEFAULT_CHAT_ID）")
    p.add_argument("--chat-name", default="", help="按群名称查找 chat_id（需要应用具备 im:chat:readonly 权限）")
    p.add_argument("--list-chats", action="store_true", help="列出机器人可见的群（名称与 chat_id），不发送消息")
    p.add_argument("--text", default="test", help="要发送的文本内容（默认: test）")
    p.add_argument("--file", default="", help="从文件读取要发送的文本内容（例如 data/output/test.md）")
    p.add_argument("--to-all", action="store_true", help="向所有机器人所在的群发送（需要 im:chat:readonly 权限）")
    p.add_argument("--sleep", type=float, default=0.2, help="群发时每条消息之间的间隔秒数，默认 0.2")
    p.add_argument("--as-card", action="store_true", help="以交互卡片(支持 Markdown) 发送内容")
    p.add_argument("--as-post", action="store_true", help="以富文本 post（聊天气泡）发送内容")
    p.add_argument("--title", default="通知", help="卡片/富文本标题（--as-card/--as-post 时生效）")
    p.add_argument("--dry-run", action="store_true", help="仅打印将要发送的内容，不调用 API")
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # DB-driven defaults via PIPELINE_ID
    pid = _env_pipeline_id()
    repo_root = Path(__file__).resolve().parents[2]
    db_path = repo_root / "data" / "info.db"
    db_delivery: dict = {}
    if pid is not None and db_path.exists():
        db_delivery = _load_feishu_delivery_from_db(db_path, pid)
        # If credentials present in DB, set env for load_config()
        if db_delivery.get("app_id") and db_delivery.get("app_secret"):
            os.environ["FEISHU_APP_ID"] = str(db_delivery["app_id"])  # override
            os.environ["FEISHU_APP_SECRET"] = str(db_delivery["app_secret"])  # override

    cfg = load_config()
    token = get_tenant_access_token(cfg)

    # 列举群后退出（调试/查找 chat_id）
    if args.list_chats:
        items = _list_all_chats(cfg, token)
        if not items:
            print("未获取到任何群（检查应用权限是否已授予 im:chat:readonly，并确认机器人已加入群）")
            return
        for it in items:
            name = (it.get("name") or "").strip()
            cid = it.get("chat_id")
            print(f"{name}\t{cid}")
        return

    # 读取文本内容
    text = args.text
    if args.file:
        p = Path(args.file)
        if not p.exists():
            raise SystemExit(f"指定文件不存在: {p}")
        text = p.read_text(encoding="utf-8")

    # If DB says to broadcast to all and CLI didn't specify a target, respect DB
    if (not args.to_all) and (not args.chat_id) and (not args.chat_name) and db_delivery.get("to_all_chat") == 1:
        args.to_all = True

    # Default title from DB when not provided
    if (args.as_card or args.as_post) and (not args.title) and db_delivery.get("title_tpl"):
        args.title = _render_title_from_tpl(db_delivery.get("title_tpl") or "")

    # 群发
    if args.to_all:
        chats = _list_all_chats(cfg, token)
        if not chats:
            raise SystemExit("未获取到任何群，无法群发（检查权限与机器人是否进群）")
        if args.dry_run:
            mode = 'post' if args.as_post else ('card' if args.as_card else 'text')
            print(f"[DRY-RUN] 将向 {len(chats)} 个群群发，文本长度 {len(text)} via {cfg.api_base} as {mode}")
            for it in chats:
                print("  -", (it.get("name") or ""), it.get("chat_id"))
            return
        sent = 0
        for it in chats:
            cid = (it.get("chat_id") or "").strip()
            if not cid:
                continue
            try:
                if args.as_post:
                    data = send_post(cfg, token, cid, text, args.title)
                elif args.as_card:
                    data = send_card_md(cfg, token, cid, text, args.title)
                else:
                    data = send_text(cfg, token, cid, text)
                mid = (data.get("data") or {}).get("message_id")
                print(f"发送成功: chat_id={cid}, message_id={mid}")
                sent += 1
            except SystemExit as e:
                print(f"发送失败: chat_id={cid} - {e}")
            time.sleep(max(0.0, args.sleep))
        print(f"群发完成，共成功 {sent}/{len(chats)} 条")
        return

    # 单群发送
    chat_id = (args.chat_id or cfg.default_chat_id or "").strip()
    if not chat_id and (db_delivery.get("chat_id") or ""):
        chat_id = str(db_delivery.get("chat_id") or "").strip()
    if not chat_id and args.chat_name:
        chat_id = _resolve_chat_id_by_name(cfg, token, args.chat_name)
    if not chat_id:
        raise SystemExit("未提供 chat_id（--chat-id / FEISHU_DEFAULT_CHAT_ID）且无法根据 --chat-name 解析，无法发送")

    if args.dry_run:
        mode = 'post' if args.as_post else ('card' if args.as_card else 'text')
        print(f"[DRY-RUN] 将向 chat_id={chat_id} 发送({len(text)}字) via {cfg.api_base} as {mode}")
        return

    if args.as_post:
        data = send_post(cfg, token, chat_id, text, args.title)
    elif args.as_card:
        data = send_card_md(cfg, token, chat_id, text, args.title)
    else:
        data = send_text(cfg, token, chat_id, text)
    message_id = (data.get("data") or {}).get("message_id")
    print(f"发送成功: chat_id={chat_id}, message_id={message_id}")


if __name__ == "__main__":
    main()
