from __future__ import annotations

from pathlib import Path
import importlib.util
import types

import feedparser


def _load_module() -> types.ModuleType:
    scraping_root = Path(__file__).resolve().parents[1]
    path = scraping_root / "game" / "indienova.com.feed.py"
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>indienova</title>
    <link>https://indienova.com/</link>
    <item>
      <title>Post A</title>
      <link>https://indienova.com/post-a</link>
      <pubDate>Mon, 28 Oct 2024 10:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <div class="entry-content">
          <p>Hello <strong>IndieNova</strong>!</p>
          <img src="x.jpg"/> <script>console.log(1)</script>
          <p>Line 2</p>
        </div>
      ]]></content:encoded>
    </item>
    <item>
      <title>Post B</title>
      <link>https://indienova.com/post-b</link>
      <pubDate>Sun, 27 Oct 2024 09:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <p>Older content</p>
      ]]></content:encoded>
    </item>
  </channel>
  </rss>
"""


def test_collect_and_detail_from_feed():
    mod = _load_module()
    feed = feedparser.parse(SAMPLE_RSS)

    items = mod.collect_entries(feed, limit=10)
    # Should parse 2 items, sorted by time desc
    assert len(items) == 2
    assert items[0]["title"] == "Post A"
    assert items[1]["title"] == "Post B"
    assert items[0]["source"] == "indienova"
    assert items[0]["category"] == "game"
    assert items[0]["url"] == "https://indienova.com/post-a"

    # published normalized to ISO8601 string containing 'T'
    assert "T" in items[0]["published"] or items[0]["published"] == ""

    # fetch_article_detail should return cleaned content from feed (no <img>, no <script>)
    detail = mod.fetch_article_detail("https://indienova.com/post-a")
    assert "Hello IndieNova!" in detail
    assert "Line 2" in detail
    assert "script" not in detail.lower()
