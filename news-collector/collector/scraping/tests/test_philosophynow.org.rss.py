from __future__ import annotations

from pathlib import Path
import importlib.util
import types

import feedparser


def _load_module() -> types.ModuleType:
    scraping_root = Path(__file__).resolve().parents[1]
    path = scraping_root / "humanities" / "philosophynow.org.rss.py"
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


SAMPLE_RSS = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\" xmlns:content=\"http://purl.org/rss/1.0/modules/content/\">
  <channel>
    <title>Philosophy Now</title>
    <link>https://philosophynow.org/</link>
    <item>
      <title>Piece One</title>
      <link>https://philosophynow.org/one</link>
      <pubDate>Mon, 28 Oct 2024 15:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <div><p>First <strong>piece</strong>.</p><img src='x.jpg'/></div>
      ]]></content:encoded>
    </item>
    <item>
      <title>Piece Two</title>
      <link>https://philosophynow.org/two</link>
      <pubDate>Sun, 27 Oct 2024 12:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <p>Second piece</p>
      ]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def test_collect_and_detail_cache():
    mod = _load_module()
    feed = feedparser.parse(SAMPLE_RSS)

    items = mod.collect_entries(feed, limit=10)
    assert len(items) == 2
    assert items[0]["title"] == "Piece One"
    assert items[0]["source"] == "philosophynow.org"
    assert items[0]["category"] == "humanities"
    assert items[0]["url"] == "https://philosophynow.org/one"
    assert "T" in items[0]["published"] or items[0]["published"] == ""

    detail = mod.fetch_article_detail("https://philosophynow.org/one")
    assert "First piece." in detail
    assert "img" not in detail.lower()
