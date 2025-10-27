from __future__ import annotations

from importlib import util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "tech" / "jiqizhixin.com.rss.py"


def load_module():
    spec = util.spec_from_file_location("jiqizhixin_com_rss", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load jiqizhixin.com RSS module")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def jiqi_module():
    return load_module()


def test_process_entries_extracts_detail_and_sorts(jiqi_module):
    feed = SimpleNamespace(
        entries=[
            {
                "title": "Older Insight",
                "link": "https://www.jiqizhixin.com/articles/older",
                "published": "Mon, 01 Jan 2024 10:00:00 GMT",
                "content": [
                    {
                        "type": "text/html",
                        "value": "<div class='article-content'><p>Old&nbsp;text</p></div>",
                    }
                ],
            },
            {
                "title": "Fresh Research",
                "link": "/articles/new",
                "published": "Tue, 02 Jan 2024 12:00:00 GMT",
                "content": [
                    {
                        "type": "text/html",
                        "value": (
                            "<article class='article-content'><p>Latest</p>"
                            "<p>Insights</p><script>void(0)</script></article>"
                        ),
                    }
                ],
            },
        ]
    )

    results = jiqi_module.process_entries(feed)

    assert [item["title"] for item in results] == [
        "Fresh Research",
        "Older Insight",
    ]
    assert results[0]["url"] == "https://www.jiqizhixin.com/articles/new"
    assert results[0]["detail"] == "Latest\nInsights"
    assert all(item["source"] == jiqi_module.SOURCE for item in results)
    assert all(item["category"] == jiqi_module.CATEGORY for item in results)


def test_process_entries_ignores_missing_title_or_link(jiqi_module):
    feed = SimpleNamespace(
        entries=[
            {"title": "", "link": "https://www.jiqizhixin.com/articles/skip"},
            {"title": "No Link"},
            {
                "title": "Valid Entry",
                "link": "https://www.jiqizhixin.com/articles/valid",
                "summary": "<p>Summary only</p>",
            },
        ]
    )

    results = jiqi_module.process_entries(feed)

    assert len(results) == 1
    assert results[0]["title"] == "Valid Entry"
    assert results[0]["published"] == ""
    assert results[0]["detail"] == "Summary only"
