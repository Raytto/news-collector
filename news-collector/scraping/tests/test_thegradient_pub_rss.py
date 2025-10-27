from __future__ import annotations

from importlib import util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tech" / "thegradient.pub.rss.py"
)


def load_module():
    spec = util.spec_from_file_location("thegradient_pub_rss", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load The Gradient RSS module")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def gradient_module():
    return load_module()


def test_process_entries_extracts_expected_fields(gradient_module):
    feed = SimpleNamespace(
        entries=[
            {
                "title": "Understanding Large Language Models",
                "link": "https://thegradient.pub/understanding-llms/",
                "published": "Wed, 24 Apr 2024 12:00:00 GMT",
            },
            {
                "title": "AI Policy Weekly",
                "link": "/ai-policy-weekly/",
                "published": "Wed, 01 May 2024 15:30:00 GMT",
            },
        ]
    )

    results = gradient_module.process_entries(feed)

    assert [item["title"] for item in results] == [
        "AI Policy Weekly",
        "Understanding Large Language Models",
    ]
    assert all(item["source"] == gradient_module.SOURCE for item in results)
    assert all(item["category"] == gradient_module.CATEGORY for item in results)
    assert results[0]["url"].startswith("https://thegradient.pub/")
    assert results[0]["published"].endswith("+00:00")


def test_process_entries_ignores_items_missing_title_or_link(gradient_module):
    feed = SimpleNamespace(
        entries=[
            {"title": "", "link": "https://thegradient.pub/foo/"},
            {"title": "No Link"},
            {
                "title": "Valid",
                "link": "https://thegradient.pub/valid/",
                "published": "",
            },
        ]
    )

    results = gradient_module.process_entries(feed)

    assert len(results) == 1
    assert results[0]["title"] == "Valid"
    assert results[0]["published"] == ""
