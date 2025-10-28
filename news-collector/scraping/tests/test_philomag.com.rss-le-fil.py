from __future__ import annotations

from pathlib import Path
import importlib.util
import types

import feedparser


def _load_module() -> types.ModuleType:
    root = Path(__file__).resolve().parents[2]
    path = root / "humanities" / "philomag.com.rss-le-fil.py"
    spec = importlib.util.spec_from_file_location(path.stem, str(path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


SAMPLE_RSS = """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<rss version=\"2.0\" xmlns:content=\"http://purl.org/rss/1.0/modules/content/\">
  <channel>
    <title>Le Fil – Philosophie Magazine</title>
    <link>https://www.philomag.com/</link>
    <item>
      <title>Brève A</title>
      <link>https://www.philomag.com/a</link>
      <pubDate>Mon, 28 Oct 2024 14:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <div class='content'><p>Texte <b>court</b>.</p><script>ignore()</script></div>
      ]]></content:encoded>
    </item>
    <item>
      <title>Brève B</title>
      <link>https://www.philomag.com/b</link>
      <pubDate>Sun, 27 Oct 2024 10:00:00 +0000</pubDate>
      <content:encoded><![CDATA[
        <p>Ancien</p>
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
    assert items[0]["title"] == "Brève A"
    assert items[0]["source"] == "philomag.com"
    assert items[0]["category"] == "humanities"
    assert items[0]["url"] == "https://www.philomag.com/a"
    assert "T" in items[0]["published"] or items[0]["published"] == ""

    detail = mod.fetch_article_detail("https://www.philomag.com/a")
    assert "Texte court." in detail
    assert "script" not in detail.lower()

