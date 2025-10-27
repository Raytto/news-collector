from __future__ import annotations

from importlib import util
from pathlib import Path
from typing import Any, Dict, List

import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "tech"
    / "huggingface.papers.trending.py"
)


def load_module():
    spec = util.spec_from_file_location("huggingface_papers_trending", MODULE_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("Failed to load Hugging Face papers module")
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture(scope="module")
def hf_module():
    return load_module()


def test_process_papers_extracts_expected_fields(hf_module):
    raw_items: List[Dict[str, Any]] = [
        {
            "title": "Paper A",
            "url": "https://huggingface.co/papers/abc123",
            "published_at": "2024-04-21T09:15:00Z",
        },
        {
            "paper_title": "Paper B",
            "slug": "2404.12345",
            "publishedAt": "2024-04-20T12:00:00",
        },
        {
            "name": "Paper C",
            "arxiv_id": "2404.54321",
            "timestamp": 1713489600,
        },
    ]

    results = hf_module.process_papers(raw_items)

    assert [entry["title"] for entry in results] == ["Paper A", "Paper B", "Paper C"]
    assert all(entry["source"] == hf_module.SOURCE for entry in results)
    assert all(entry["category"] == hf_module.CATEGORY for entry in results)
    assert results[0]["url"] == "https://huggingface.co/papers/abc123"
    assert results[1]["url"].endswith("2404.12345")
    assert results[2]["url"].endswith("2404.54321")
    assert results[0]["published"].endswith("+00:00")
    assert results[1]["published"].startswith("2024-04-20")
    assert results[2]["published"].startswith("2024-04-19")


def test_process_papers_handles_missing_fields(hf_module):
    raw_items: List[Dict[str, Any]] = [
        {
            "title": "Untitled",
            "link": "https://example.com/paper",
            "createdAt": "",
        },
        {
            "foo": "bar",
        },
    ]

    results = hf_module.process_papers(raw_items)

    assert len(results) == 2
    assert results[-1]["title"] == ""
    assert results[0]["url"] == "https://example.com/paper"
    assert results[0]["published"] == ""
