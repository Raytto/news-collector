#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlencode

import requests

MIXIN_KEY_ENC_TABLE: List[int] = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    40,
    12,
    38,
    57,
    4,
    52,
    1,
    30,
    56,
    7,
    16,
    41,
    22,
    13,
    6,
    55,
    24,
    28,
    54,
    25,
    14,
    39,
    0,
    37,
    44,
    17,
    34,
    11,
    26,
    51,
    20,
    21,
    36,
]

JSON_HEADERS_TEMPLATE: Mapping[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Origin": "https://space.bilibili.com",
    "Referer": "https://space.bilibili.com/",
    "Connection": "keep-alive",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

HTML_HEADERS: Mapping[str, str] = {
    "User-Agent": JSON_HEADERS_TEMPLATE["User-Agent"],
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": JSON_HEADERS_TEMPLATE["Accept-Language"],
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


@dataclass
class VideoEntry:
    title: str
    published_at: datetime
    link: str


def parse_cookie_string(raw: str) -> Dict[str, str]:
    cookies: Dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies[name] = value.strip()
    return cookies


def build_session(mid: int, cookie_override: Optional[str]) -> requests.Session:
    """Initialize a session with basic cookies to reduce risk blocks."""
    session = requests.Session()
    session.headers.update(JSON_HEADERS_TEMPLATE)
    session.headers["Referer"] = f"https://space.bilibili.com/{mid}/video"
    now = int(time.time())
    session.cookies.set(
        "_uuid", f"{uuid.uuid4().hex.upper()}infoc", domain=".bilibili.com"
    )
    session.cookies.set(
        "buvid4",
        f"{uuid.uuid4().hex[:8].upper()}-{uuid.uuid4().hex[:4].upper()}-"
        f"{uuid.uuid4().hex[:4].upper()}-{uuid.uuid4().hex[:4].upper()}-"
        f"{uuid.uuid4().hex.upper()}infoc",
        domain=".bilibili.com",
    )
    session.cookies.set("i-wanna-go-back", "2", domain=".bilibili.com")
    session.cookies.set("CURRENT_FNVAL", "4048", domain=".bilibili.com")
    session.cookies.set(
        "b_lsid", f"{uuid.uuid4().hex}_{now}", domain=".bilibili.com"
    )
    session.cookies.set("b_timer", f"{now}|{now}", domain=".bilibili.com")
    fingerprint = uuid.uuid4().hex
    session.cookies.set("fingerprint", fingerprint, domain=".bilibili.com")
    session.cookies.set("fingerprint3", fingerprint, domain=".bilibili.com")
    session.cookies.set(
        "fingerprint4", f"{fingerprint}__{now}", domain=".bilibili.com"
    )
    session.cookies.set("buvid_fp", fingerprint, domain=".bilibili.com")
    session.cookies.set("buvid_fp_plain", fingerprint, domain=".bilibili.com")
    if cookie_override:
        for key, value in parse_cookie_string(cookie_override).items():
            session.cookies.set(key, value, domain=".bilibili.com")
    try:
        session.get("https://www.bilibili.com/", headers=HTML_HEADERS, timeout=10)
        space_response = session.get(
            f"https://space.bilibili.com/{mid}", headers=HTML_HEADERS, timeout=10
        )
    except requests.RequestException:
        space_response = None
    if space_response is not None:
        token = space_response.cookies.get("X-BILI-SEC-TOKEN")
        if token:
            session.headers["X-Bili-Sec-Token"] = token
    return session


def fetch_wbi_keys(session: requests.Session) -> tuple[str, str]:
    """Retrieve the latest WBI keys needed to sign API requests."""
    response = session.get(
        "https://api.bilibili.com/x/web-interface/nav",
        timeout=10,
    )
    response.raise_for_status()
    data = response.json().get("data", {})
    wbi_img = data.get("wbi_img", {})
    img_url = wbi_img.get("img_url")
    sub_url = wbi_img.get("sub_url")
    if not img_url or not sub_url:
        raise RuntimeError("Unable to fetch WBI image keys from nav endpoint")
    return (
        img_url.rsplit("/", 1)[-1].split(".")[0],
        sub_url.rsplit("/", 1)[-1].split(".")[0],
    )


def build_mixin_key(img_key: str, sub_key: str) -> str:
    """Derive the deterministic mixin key used for signing."""
    orig = (img_key + sub_key).encode("utf-8")
    mixed = bytearray(len(MIXIN_KEY_ENC_TABLE))
    for idx, orig_idx in enumerate(MIXIN_KEY_ENC_TABLE):
        mixed[idx] = orig[orig_idx]
    return mixed.decode("utf-8")[:32]


def sign_wbi_params(params: Mapping[str, Any], mixin_key: str) -> Dict[str, Any]:
    """Attach WBI signature fields to the provided parameters."""
    params_with_wts: Dict[str, str] = {
        key: str(value) for key, value in params.items()
    }
    params_with_wts["wts"] = str(int(time.time()))
    sanitized_items = []
    for key, value in sorted(params_with_wts.items()):
        filtered_value = "".join(ch for ch in value if ch not in "!'()*")
        sanitized_items.append((key, filtered_value))
    query = urlencode(sanitized_items)
    w_rid = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
    sanitized_items.append(("w_rid", w_rid))
    return dict(sanitized_items)


def fetch_latest_videos(
    mid: int, limit: int = 30, cookie_override: Optional[str] = None
) -> List[VideoEntry]:
    """Fetch the latest videos for the given user ID."""
    session = build_session(mid, cookie_override)
    img_key, sub_key = fetch_wbi_keys(session)
    mixin_key = build_mixin_key(img_key, sub_key)
    base_params: Dict[str, Any] = {
        "mid": mid,
        "ps": limit,
        "tid": 0,
        "pn": 1,
        "keyword": "",
        "order": "pubdate",
    }
    signed_params = sign_wbi_params(base_params, mixin_key)
    response = session.get(
        "https://api.bilibili.com/x/space/wbi/arc/search",
        params=signed_params,
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") == -352:
        voucher = payload.get("data", {}).get("v_voucher")
        if voucher:
            session.headers["X-Bili-Gaia-Vtoken"] = voucher
            session.cookies.set("bili_gaia_vtoken", voucher, domain=".bilibili.com")
            response = session.get(
                "https://api.bilibili.com/x/space/wbi/arc/search",
                params=signed_params,
                timeout=10,
            )
            response.raise_for_status()
            payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"API error: {payload.get('code')} {payload.get('message')}")
    vlist = payload.get("data", {}).get("list", {}).get("vlist", [])
    entries: List[VideoEntry] = []
    for video in vlist:
        bvid = video.get("bvid")
        title = video.get("title")
        created = video.get("created")
        if not bvid or not title or not created:
            continue
        link = f"https://www.bilibili.com/video/{bvid}"
        published_at = datetime.fromtimestamp(int(created), tz=timezone.utc)
        entries.append(VideoEntry(title=title, published_at=published_at, link=link))
    return entries


def print_entries(entries: Iterable[VideoEntry]) -> None:
    for idx, entry in enumerate(entries, start=1):
        print(f"{idx}. {entry.title}")
        print(f"   Published: {entry.published_at.isoformat()}")
        print(f"   Link: {entry.link}")


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch latest bilibili space videos."
    )
    parser.add_argument(
        "--mid",
        type=int,
        default=504018708,
        help="Bilibili user mid (default: %(default)s)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Maximum number of videos to fetch (default: %(default)s)",
    )
    parser.add_argument(
        "--cookie",
        type=str,
        default=None,
        help="Optional cookie string (or set BILIBILI_COOKIE env var)",
    )
    args = parser.parse_args(argv[1:])
    cookie_string = args.cookie or os.getenv("BILIBILI_COOKIE")
    try:
        entries = fetch_latest_videos(
            mid=args.mid, limit=args.limit, cookie_override=cookie_string
        )
    except Exception as exc:  # noqa: BLE001 - surface raw error
        print(f"Failed to fetch videos: {exc}", file=sys.stderr)
        return 1
    if not entries:
        print("No videos found.")
        return 0
    print_entries(entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
