import feedparser

RSS_URL = "https://www.gamesindustry.biz/rss/gamesindustry_news_feed.rss"
SOURCE = "gamesindustry.biz"
CATEGORY = "game"

def fetch_feed(url):
    d = feedparser.parse(url)
    if d.bozo:
        print("解析 RSS 时出错:", d.bozo_exception)
    return d

def process_entries(feed):
    entries = feed.entries
    results = []
    for e in entries:
        title = e.get("title", "")
        link  = e.get("link", "")
        published = e.get("published", e.get("updated", ""))
        # 你可解析 published 为 datetime
        results.append({
            "title": title,
            "url": link,
            "published": published,
            "source": SOURCE,
            "category": CATEGORY,
        })
    return results

if __name__ == "__main__":
    feed = fetch_feed(RSS_URL)
    items = process_entries(feed)
    for it in items[:10]:
        print(it["published"], "-", it["title"], "-", it["url"])
