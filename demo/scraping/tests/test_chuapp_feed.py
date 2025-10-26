from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

feedparser = pytest.importorskip("feedparser")

MODULE_PATH = Path(__file__).resolve().parents[1] / "chuapp.feed.py"
spec = importlib.util.spec_from_file_location("chuapp_feed", MODULE_PATH)
chuapp_feed = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(chuapp_feed)

FIXTURE_PATH = Path(__file__).parent / "data" / "chuapp_feed_sample.xml"


def _load_sample_feed():
    return feedparser.parse(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_collect_entries_parses_sample_feed():
    feed = _load_sample_feed()
    items = chuapp_feed.collect_entries(feed, limit=5)

    assert len(items) == 2
    assert items[0]["title"] == "测试文章二"
    assert items[0]["url"] == "https://www.chuapp.com/article/2"
    assert items[0]["published"].startswith("2024-11-01T01:30:00+00:00")


def test_collect_entries_respects_limit():
    feed = _load_sample_feed()
    items = chuapp_feed.collect_entries(feed, limit=1)

    assert len(items) == 1
    assert items[0]["title"] == "测试文章二"


def test_main_uses_mock_feed(monkeypatch, capsys):
    feed = _load_sample_feed()

    monkeypatch.setattr(chuapp_feed, "fetch_feed", lambda url=chuapp_feed.RSS_URL: feed)
    chuapp_feed.main()

    stdout = capsys.readouterr().out.strip().splitlines()
    assert len(stdout) == 2
    assert "2024-11-01T01:30:00+00:00 - 测试文章二 - https://www.chuapp.com/article/2" == stdout[0]
