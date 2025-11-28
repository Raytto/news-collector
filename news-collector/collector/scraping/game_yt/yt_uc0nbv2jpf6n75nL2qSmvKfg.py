from __future__ import annotations

from typing import Dict, List

try:
    from ._yt_feed import collect_entries, fetch_detail
except ImportError:  # pragma: no cover - allow running as a script
    import importlib.util
    import sys
    from pathlib import Path

    helper_path = Path(__file__).with_name("_yt_feed.py")
    module_name = "game_yt_feed_helper"
    helper = sys.modules.get(module_name)
    if helper is None:
        spec = importlib.util.spec_from_file_location(module_name, str(helper_path))
        if spec and spec.loader:
            helper = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(helper)  # type: ignore[attr-defined]
            sys.modules[module_name] = helper
        else:  # pragma: no cover
            raise ImportError("cannot load _yt_feed helper")
    collect_entries = helper.collect_entries  # type: ignore[attr-defined]
    fetch_detail = helper.fetch_detail  # type: ignore[attr-defined]

CHANNEL_ID = "UC0nBV2jPf6n75nL2qSmvKfg"
SOURCE = "youtube_cute_games"
CATEGORY = "game_yt"

_DESC_CACHE: Dict[str, str] = {}


def collect_latest() -> List[Dict[str, str]]:
    return collect_entries(CHANNEL_ID, SOURCE, CATEGORY, _DESC_CACHE)


def fetch_article_detail(url: str) -> str:
    return fetch_detail(CHANNEL_ID, url, _DESC_CACHE)


if __name__ == "__main__":
    items = collect_latest()[:5]
    for item in items:
        print(item)
    if items:
        print(fetch_article_detail(items[0]["link"])[:500])
