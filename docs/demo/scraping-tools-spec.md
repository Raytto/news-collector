## Demo Scraping Tools Spec

These demo scripts illustrate a lightweight convention for fetching and printing the latest articles from third-party gaming-industry sources. They live under `demo/scraping/` and are meant to be runnable directly (no CLI options). All examples assume the `news-collector` conda environment (`source ~/miniconda3/etc/profile.d/conda.sh && conda activate news-collector`).

### Shared Output Format

- Each script prints **at most 10 entries** to STDOUT.
- Every entry is a single line:\
  `PUBLISHED - TITLE - URL`
    - `PUBLISHED`: prefer ISO-8601 UTC timestamps if the source exposes one. When only coarse strings are available (e.g., “October 2025”), emit them verbatim.
    - `TITLE`: already-decoded text with HTML stripped.
    - `URL`: absolute link to the article.
- No surrounding JSON/CSV headers; callers can split on `' - '` to parse.
- If parsing fails or the feed warns, log a short message in Chinese (see feedparser bozo notices) but continue emitting whatever data is available.

Example (from `deconstructoroffun.rss.py`):

```
2025-10-20T12:54:58+00:00 - 8 Trends That Will Redefine Gaming in Asia (and Beyond) - https://www.deconstructoroffun.com/blog/2025/10/20/8-trends-that-will-redefine-gaming-in-asia-and-beyond
```

### Source Notes

| Script | Source | Data Path | Notes |
| --- | --- | --- | --- |
| `gamedeveloper.rss.py` | https://www.gamedeveloper.com/rss.xml | RSS via `feedparser` | Normalizes timestamps via `published_parsed` / `updated_parsed`, falling back to raw strings. |
| `deconstructoroffun.rss.py` | https://www.deconstructoroffun.com/blog?format=rss | RSS via `feedparser` | Same logic as above; prints a bozo warning if Squarespace emits malformed entities but still processes entries. |
| `naavik.digest.py` | https://naavik.co/digest/ | WordPress JSON proxied through `https://r.jina.ai/` | The site blocks bots directly, so we hit the proxy and strip its “Markdown Content” wrapper before decoding JSON. The script limits `_fields[]` to keep responses small, sorts by `date_gmt`, and prints the 10 newest digests. |
| `sensortower.blog.py` | https://sensortower.com/blog | Next.js SSR JSON + public GraphQL endpoint | 1) Parse `__NEXT_DATA__` to discover the collection ID and locale. 2) Query Sensor Tower’s Netlify GraphQL (`collection` + `contents`) for the latest cards and matching blog metadata. If the GraphQL call fails, fall back to the SSR-embedded items. Sensor Tower only exposes month-level `pubDate`, so those strings appear in the `PUBLISHED` column. |

### Implementation Checklist

When adding another scraper that conforms to this spec:

1. Ensure it prints exactly one line per article using the shared format.
2. Trim to 10 entries at the call site (after sorting if needed).
3. Normalize titles/dates as far as the source allows (UTC ISO when possible).
4. Keep dependencies within what’s already listed in `requirements.txt` (requests, beautifulsoup4, feedparser).
5. Document any non-trivial fetching workaround (e.g., proxies, GraphQL) inside the script so future contributors understand why it exists.
