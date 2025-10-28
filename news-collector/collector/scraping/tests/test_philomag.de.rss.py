from __future__ import annotations

from pathlib import Path
import importlib.util
import types

import feedparser


def _load_module() -> types.ModuleType:
    scraping_root = Path(__file__).resolve().parents[1]
    path = scraping_root / "humanities" / "philomag.de.rss.py"
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>PhiloMag</title>
    <link>https://www.philomag.de/</link>
    <item>
      <title>Artikel A</title>
      <link>https://www.philomag.de/a</link>
      <pubDate>Mon, 28 Oct 2024 12:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <div class="content">
          <p>Hallo <b>Welt</b>!</p>
          <p>Zeile 2</p>
          <script>bad()</script>
        </div>
      ]]></content:encoded>
    </item>
    <item>
      <title>Artikel B</title>
      <link>https://www.philomag.de/b</link>
      <pubDate>Sun, 27 Oct 2024 11:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <p>Älterer Text</p>
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
    # Most recent first
    assert items[0]["title"] == "Artikel A"
    assert items[0]["source"] == "philomag.de"
    assert items[0]["category"] == "humanities"
    assert items[0]["url"] == "https://www.philomag.de/a"
    assert "T" in items[0]["published"] or items[0]["published"] == ""

    # fetch_article_detail uses cached cleaned content
    detail = mod.fetch_article_detail("https://www.philomag.de/a")
    assert "Hallo Welt!" in detail
    assert "Zeile 2" in detail
    assert "script" not in detail.lower()
