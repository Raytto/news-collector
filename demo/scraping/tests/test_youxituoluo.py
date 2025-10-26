from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "youxituoluo.com.latest.py"
spec = importlib.util.spec_from_file_location("youxituoluo", MODULE_PATH)
youxituoluo = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(youxituoluo)


def test_extract_articles_from_json_uses_embedded_payload():
    html = """
    <html>
      <head></head>
      <body>
        <script id="__NEXT_DATA__" type="application/json">
        {"props":{"pageProps":{"articles":[{"title":"测试文章","shareUrl":"/article/123","publishTime":"2024-06-01 08:30:00"}]}}}
        </script>
      </body>
    </html>
    """
    articles = youxituoluo.extract_articles_from_json(html)
    assert articles[0]["title"] == "测试文章"
    assert articles[0]["url"].endswith("/article/123")
    assert articles[0]["published"].startswith("2024-06-01")


def test_extract_articles_from_html_falls_back_to_dom():
    html = """
    <div class="article-card">
      <a href="/article/456">第二篇文章</a>
      <span class="time">2024-05-31 12:00</span>
    </div>
    """
    articles = youxituoluo.extract_articles_from_html(html)
    assert articles[0]["title"] == "第二篇文章"
    assert articles[0]["url"].endswith("/article/456")
    assert articles[0]["published"].startswith("2024-05-31")


def test_parse_timestamp_supports_ms_epoch():
    iso = youxituoluo.parse_timestamp(1717228800000)
    assert iso.startswith("2024-")


def test_collect_and_sort_articles_deduplicate_and_limit():
    html = """
    <script id="__NEXT_DATA__" type="application/json">
    {"props":{"pageProps":{"articles":[
      {"title":"A","shareUrl":"/article/a","publishTime":"2024-01-01 00:00:00"},
      {"title":"B","shareUrl":"/article/b","publishTime":"2024-01-02 00:00:00"},
      {"title":"B","shareUrl":"/article/b","publishTime":"2024-01-02 00:00:00"}
    ]}}}
    </script>
    """
    articles = youxituoluo.collect_articles(html)
    assert len(articles) == 2
    sorted_articles = youxituoluo.sort_articles(articles)
    assert [item["title"] for item in sorted_articles] == ["B", "A"]
