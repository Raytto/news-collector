from __future__ import annotations

from pathlib import Path
import importlib.util
import types

import feedparser


def _load_module() -> types.ModuleType:
    root = Path(__file__).resolve().parents[1]
    path = root / "tech" / "semianalysis.feed.py"
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


SAMPLE_RSS = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\" xmlns:content=\"http://purl.org/rss/1.0/modules/content/\">
  <channel>
    <title>Semianalysis</title>
    <link>https://semianalysis.com/</link>
    <item>
      <title>Breaking Node Supply</title>
      <link>https://semianalysis.com/breaking-node-supply</link>
      <pubDate>Mon, 28 Oct 2024 10:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <div class='entry-content'>
          <p>TSMC faces a supply crunch.</p>
          <p><strong>Key Takeaway:</strong> Capacity is limited.</p>
          <script>ignore()</script>
        </div>
      ]]></content:encoded>
    </item>
    <item>
      <title>Legacy Foundry Economics</title>
      <link>https://semianalysis.com/legacy-foundry</link>
      <pubDate>Sun, 27 Oct 2024 09:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <p>Older article</p>
      ]]></content:encoded>
    </item>
  </channel>
</rss>
"""


def test_collect_and_detail_cache() -> None:
    mod = _load_module()
    feed = feedparser.parse(SAMPLE_RSS)

    items = mod.collect_entries(feed, limit=10)
    assert len(items) == 2
    assert items[0]["title"] == "Breaking Node Supply"
    assert items[0]["source"] == "semianalysis"
    assert items[0]["category"] == "tech"

    detail = mod.fetch_article_detail("https://semianalysis.com/breaking-node-supply")
    assert "TSMC faces a supply crunch." in detail
    assert "script" not in detail.lower()

