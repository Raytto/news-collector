# News Collector Database Specification

This document describes the SQLite schema used by the manager scripts to persist scraped articles and AI evaluation results.

## Overview

- Engine: SQLite 3
- File path: `data/info.db` (relative to repo root)
- Writers:
  - Articles: `news-collector/manager/collect_to_sqlite.py`
  - AI evaluation: `news-collector/manager/ai_evaluate.py`
- De-duplication: unique on `link` in `info` (for new databases created by the manager). Existing databases may still use the older `(source, publish, title)` index unless migrated manually.

## Schema

Tables: `info` (articles) and `info_ai_review` (AI scores)

### Table: `info`

```sql
CREATE TABLE IF NOT EXISTS info (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  source   TEXT NOT NULL,
  publish  TEXT NOT NULL,
  title    TEXT NOT NULL,
  link     TEXT NOT NULL,
  category TEXT,
  detail   TEXT
);

-- De-duplication constraint (new DBs)
CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique
  ON info (link);
```

#### Columns (`info`)

- `id` (INTEGER): Surrogate primary key, auto-incremented.
- `source` (TEXT): Source identifier of the scraper (e.g., `gamedeveloper`, `gamesindustry.biz`, `youxituoluo`).
- `publish` (TEXT): Publication time as text.
  - Preferred format is ISO-8601 UTC with seconds (e.g., `2025-10-24T14:27:00+00:00`).
  - When a source only exposes coarse strings (e.g., `October 2025`), values are stored verbatim.
- `title` (TEXT): Article title (HTML-decoded, trimmed).
- `link` (TEXT): Absolute URL to the article.
- `category` (TEXT, nullable): High-level category label for the source (e.g., `game`, `tech`).
- `detail` (TEXT, nullable): Plain‑text body extracted from the article detail page. Initially NULL; populated by site‑specific `fetch_article_detail` when available.

### Table: `info_ai_review`

Created by `ai_evaluate.py` when first executed.

```sql
CREATE TABLE IF NOT EXISTS info_ai_review (
  info_id                INTEGER PRIMARY KEY,
  final_score            REAL    NOT NULL,
  timeliness_score       INTEGER NOT NULL,
  game_relevance_score   INTEGER,
  ai_relevance_score     INTEGER,
  tech_relevance_score   INTEGER,
  quality_score          INTEGER,
  ai_comment             TEXT    NOT NULL,
  ai_summary             TEXT    NOT NULL,
  raw_response           TEXT,
  created_at             TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at             TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (info_id) REFERENCES info(id)
);
```

Scores are 1–5 integers per dimension; `final_score` is a weighted 1–5 float. The manager adds missing columns via `ALTER TABLE` if you are upgrading from an older schema.

## Insertion, Detail Fetch & De-duplication

The manager inserts rows and skips duplicates using SQLite upsert semantics:

```sql
INSERT INTO info (source, publish, title, link, category, detail)
VALUES (?, ?, ?, ?, ?, NULL)
ON CONFLICT(link) DO NOTHING;
```

If `DO NOTHING` is unsupported, it falls back to `INSERT OR IGNORE`.

De-duplication key: exact match on `link`.

Detail fetching/updating:

- For newly inserted rows, if the module exposes `fetch_article_detail(url)`, the manager fetches and updates `info.detail`:

```sql
UPDATE info SET detail = ? WHERE link = ?;
```

- The manager also performs a light backfill step per source after each module run, fetching details for a small batch of the most recent rows that still have empty `detail`.

## Typical Queries

- Latest 20 across all sources:
```sql
SELECT source, publish, title, link
FROM info
ORDER BY publish DESC
LIMIT 20;
```

- Latest 10 per source (example for one source):
```sql
SELECT source, publish, title, link
FROM info
WHERE source = 'youxituoluo'
ORDER BY publish DESC
LIMIT 10;
```

- Keyword search in title:
```sql
SELECT source, publish, title, link
FROM info
WHERE title LIKE '%AI%'
ORDER BY publish DESC;
```

- Join latest entries with AI scores for rendering:
```sql
SELECT i.id, i.source, i.category, i.publish, i.title, i.link,
       r.final_score, r.timeliness_score, r.relevance_score,
       r.insightfulness_score, r.actionability_score,
       r.ai_comment, r.ai_summary
FROM info AS i
LEFT JOIN info_ai_review AS r ON r.info_id = i.id
ORDER BY i.id DESC
LIMIT 100;
```

- Find sources missing details (to improve scrapers):
```sql
SELECT source, COUNT(*) AS missing_count
FROM info
WHERE detail IS NULL OR TRIM(detail) = ''
GROUP BY source
ORDER BY missing_count DESC;
```

## Data Notes

- `publish` is stored as TEXT to allow ISO timestamps and coarse strings from sources that lack precise times. For ISO-8601 values, lexicographic order matches chronological order.
- Scripts attempt to normalize times to ISO-8601 UTC when possible; otherwise keep the original string.
- `category` is optional and may be empty for historical rows; the manager will add the column automatically if missing (via `ALTER TABLE`) and insert values for new rows when provided by scrapers.

## Migration Note

- October 2025 (A): Added `category` column. Existing databases are migrated in-place by the manager script on startup.
- October 2025 (B): New installs use `UNIQUE(link)` for de-duplication. Existing databases keep their previous unique index unless you explicitly migrate:
  - `DROP INDEX IF EXISTS idx_info_unique;`
  - `CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique ON info(link);`
- October 2025 (C): Added `detail` column to `info` and introduced `info_ai_review` table. The manager adds `detail` automatically if missing; `ai_evaluate.py` creates `info_ai_review` on first run.

## Maintenance

- Vacuum/Analyze (optional) for size/performance:
```sql
VACUUM;
ANALYZE;
```

- Backup the DB file by copying `data/info.db` while no writer is running. SQLite supports online backup via the CLI if needed.

## Future Extensions

Potential columns (not present today): `summary`, `author`, `fetched_at`, `tags`. These can be added via `ALTER TABLE info ADD COLUMN ...` and won’t affect the unique `link` index.
