from __future__ import annotations

from importlib import util
from pathlib import Path
from types import SimpleNamespace
from time import gmtime

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tech" / "a16z.substack.feed.py"


def load_module():
    spec = util.spec_from_file_location("a16z_substack_feed", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load a16z Substack feed module")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def a16z_module():
    return load_module()


def test_process_entries_orders_and_formats_results(a16z_module):
    feed = SimpleNamespace(
        entries=[
            {
                "title": "Older Insight",
                "link": "https://a16z.substack.com/p/older-insight",
                "published": "Tue, 02 Apr 2024 10:00:00 GMT",
                "content": [{"value": "<p>Older body</p>"}],
            },
            {
                "title": "Latest Perspective",
                "link": "/p/latest-perspective",
                "published_parsed": gmtime(1712380800),
                "summary": "<p><strong>Breaking</strong> tech insight</p>",
            },
        ]
    )

    results = a16z_module.process_entries(feed)

    assert [item["title"] for item in results] == [
        "Latest Perspective",
        "Older Insight",
    ]
    assert all(item["source"] == a16z_module.SOURCE for item in results)
    assert all(item["category"] == a16z_module.CATEGORY for item in results)
    assert results[0]["url"].startswith("https://a16z.substack.com/")
    assert results[0]["published"].endswith("+00:00")
    assert results[0]["detail"] == "Breaking tech insight"
    assert results[1]["detail"] == "Older body"


def test_process_entries_ignores_incomplete_items(a16z_module):
    feed = SimpleNamespace(
        entries=[
            {"title": "", "link": "https://a16z.substack.com/p/missing-title"},
            {"title": "No Link"},
            {
                "title": "Valid Item",
                "link": "https://a16z.substack.com/p/valid-item",
                "published": "",
            },
        ]
    )

    results = a16z_module.process_entries(feed)

    assert len(results) == 1
    assert results[0]["title"] == "Valid Item"
    assert results[0]["published"] == ""
    assert results[0]["detail"] == ""
