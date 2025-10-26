from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

feedparser = pytest.importorskip("feedparser")

MODULE_PATH = Path(__file__).resolve().parents[1] / "nikopartners.blog.py"
spec = importlib.util.spec_from_file_location("nikopartners_blog", MODULE_PATH)
niko_blog = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(niko_blog)

FIXTURE_PATH = Path(__file__).parent / "data" / "nikopartners_feed_sample.xml"


def _load_sample_feed():
    return feedparser.parse(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_collect_entries_parses_sample_feed():
    feed = _load_sample_feed()
    items = niko_blog.collect_entries(feed, limit=5)

    assert len(items) == 3
    assert items[0]["title"] == "October 23, 2025"
    assert items[0]["url"] == "https://nikopartners.com/october-23-2025/"
    assert items[0]["published"].startswith("2025-10-23T10:08:04+00:00")


def test_collect_entries_respects_limit():
    feed = _load_sample_feed()
    items = niko_blog.collect_entries(feed, limit=2)

    assert len(items) == 2
    assert items[-1]["title"] == "How Hollow Knight: Silksong Rose to Success Despite Mixed Reaction"


def test_main_uses_local_feed(monkeypatch, capsys):
    feed = _load_sample_feed()

    monkeypatch.setattr(niko_blog, "fetch_feed", lambda url=niko_blog.RSS_URL: feed)
    niko_blog.main()

    stdout = capsys.readouterr().out.strip().splitlines()
    assert len(stdout) == 3
    assert "October 23, 2025 - October 23, 2025 - https://nikopartners.com/october-23-2025/" in stdout[0]
