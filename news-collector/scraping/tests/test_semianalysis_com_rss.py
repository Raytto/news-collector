from __future__ import annotations

from importlib import util
from pathlib import Path
from types import SimpleNamespace

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "tech" / "semianalysis.com.rss.py"
)


def load_module():
    spec = util.spec_from_file_location("semianalysis_com_rss", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load SemiAnalysis RSS module")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def semianalysis_module():
    return load_module()


def test_process_entries_returns_sorted_items(semianalysis_module):
    feed = SimpleNamespace(
        entries=[
            {
                "title": "Deep Dive into NVIDIA's Roadmap",
                "link": "https://semianalysis.com/p/deep-dive-nvidia-roadmap",
                "published": "Mon, 01 Jul 2024 10:00:00 GMT",
            },
            {
                "title": "Chip Supply Chain Update",
                "link": "/p/chip-supply-chain-update",
                "published": "Mon, 08 Jul 2024 15:30:00 GMT",
            },
        ]
    )

    results = semianalysis_module.process_entries(feed)

    assert [item["title"] for item in results] == [
        "Chip Supply Chain Update",
        "Deep Dive into NVIDIA's Roadmap",
    ]
    assert all(item["source"] == semianalysis_module.SOURCE for item in results)
    assert all(item["category"] == semianalysis_module.CATEGORY for item in results)
    assert results[0]["url"].startswith("https://semianalysis.com/")
    assert results[0]["published"].endswith("+00:00")


def test_process_entries_skips_invalid_items(semianalysis_module):
    feed = SimpleNamespace(
        entries=[
            {"title": "", "link": "https://semianalysis.com/p/empty"},
            {"title": "Missing Link"},
            {
                "title": "Valid Item",
                "link": "https://semianalysis.com/p/valid-item",
                "published": "",
            },
        ]
    )

    results = semianalysis_module.process_entries(feed)

    assert len(results) == 1
    assert results[0]["title"] == "Valid Item"
    assert results[0]["published"] == ""
