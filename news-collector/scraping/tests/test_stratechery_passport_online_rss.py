from __future__ import annotations

from importlib import util
from pathlib import Path
from time import gmtime
from types import SimpleNamespace

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tech"
    / "stratechery.passport.online.rss.py"
)


def load_module():
    spec = util.spec_from_file_location("stratechery_passport_rss", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load Stratechery Passport RSS module")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def stratechery_module():
    return load_module()


def test_process_entries_extracts_detail_from_feed(stratechery_module):
    feed = SimpleNamespace(
        entries=[
            {
                "title": "Latest Analysis",
                "link": "https://stratechery.com/2024/latest-analysis/",
                "published_parsed": gmtime(1714608000),
                "content": [
                    {
                        "value": "<article><p><strong>Key</strong> insight.</p><div class='share'>Share</div></article>",
                    }
                ],
            },
            {
                "title": "Older Note",
                "link": "https://stratechery.com/2023/older-note/",
                "published": "Tue, 12 Dec 2023 10:30:00 GMT",
                "summary": "<p>Older&nbsp;content</p>",
            },
        ]
    )

    results = stratechery_module.process_entries(feed)

    assert [item["title"] for item in results] == ["Latest Analysis", "Older Note"]
    assert results[0]["detail"] == "Key insight."
    assert results[1]["detail"] == "Older content"
    assert results[0]["published"].endswith("+00:00")
    assert all(item["source"] == stratechery_module.SOURCE for item in results)
    assert all(item["category"] == stratechery_module.CATEGORY for item in results)


def test_process_entries_skips_incomplete_rows(stratechery_module):
    feed = SimpleNamespace(
        entries=[
            {"title": "", "link": "https://stratechery.com/2024/missing-title/"},
            {"title": "Missing Link"},
        ]
    )

    results = stratechery_module.process_entries(feed)

    assert results == []
