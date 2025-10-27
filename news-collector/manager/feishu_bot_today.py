from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
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


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="向飞书群发送一条文本消息（默认 test）",
    )
    p.add_argument("--chat-id", default="", help="目标群 chat_id（缺省使用 FEISHU_DEFAULT_CHAT_ID）")
    p.add_argument("--text", default="test", help="要发送的文本内容（默认: test）")
    p.add_argument("--dry-run", action="store_true", help="仅打印将要发送的内容，不调用 API")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    cfg = load_config()
    chat_id = (args.chat_id or cfg.default_chat_id or "").strip()
    if not chat_id:
        raise SystemExit("未提供 chat_id（--chat-id 或 FEISHU_DEFAULT_CHAT_ID），无法发送")

    if args.dry_run:
        print(
            f"[DRY-RUN] 将向 chat_id={chat_id} 发送文本: {args.text!r} via {cfg.api_base}"
        )
        return

    token = get_tenant_access_token(cfg)
    data = send_text(cfg, token, chat_id, args.text)
    message_id = (data.get("data") or {}).get("message_id")
    print(f"发送成功: chat_id={chat_id}, message_id={message_id}")


if __name__ == "__main__":
    main()

