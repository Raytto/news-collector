# News Collector Database Specification

This document describes the SQLite schema used by the manager script to persist scraped articles.

## Overview

- Engine: SQLite 3
- File path: `data/info.db` (relative to repo root)
- Writer: `news-collector/manager/collect_to_sqlite.py`
- De-duplication: unique on `link` (for new databases created by the manager). Existing databases may still use the older `(source, publish, title)` index unless migrated manually.

## Schema

Single table named `info`:

```sql
CREATE TABLE IF NOT EXISTS info (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  source   TEXT NOT NULL,
  publish  TEXT NOT NULL,
  title    TEXT NOT NULL,
  link     TEXT NOT NULL,
  category TEXT
);

-- De-duplication constraint (new DBs)
CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique
  ON info (link);
```

### Columns

- `id` (INTEGER): Surrogate primary key, auto-incremented.
- `source` (TEXT): Source identifier of the scraper (e.g., `gamedeveloper`, `gamesindustry.biz`, `youxituoluo`).
- `publish` (TEXT): Publication time as text.
  - Preferred format is ISO-8601 UTC with seconds (e.g., `2025-10-24T14:27:00+00:00`).
  - When a source only exposes coarse strings (e.g., `October 2025`), values are stored verbatim.
- `title` (TEXT): Article title (HTML-decoded, trimmed).
- `link` (TEXT): Absolute URL to the article.
- `category` (TEXT, nullable): High-level category label for the source. All scrapers currently emit `"game"`.

## Insertion & De-duplication

The manager inserts rows and skips duplicates using SQLite upsert semantics:

```sql
INSERT INTO info (source, publish, title, link, category)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(link) DO NOTHING;
```

If `DO NOTHING` is unsupported, it falls back to `INSERT OR IGNORE`.

De-duplication key: exact match on `link`.

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

## Data Notes

- `publish` is stored as TEXT to allow ISO timestamps and coarse strings from sources that lack precise times. For ISO-8601 values, lexicographic order matches chronological order.
- Scripts attempt to normalize times to ISO-8601 UTC when possible; otherwise keep the original string.
- `category` is optional and may be empty for historical rows; the manager will add the column automatically if missing (via `ALTER TABLE`) and insert values for new rows when provided by scrapers.

## Migration Note

- October 2025 (A): Added `category` column. Existing databases are migrated in-place by the manager script on startup.
- October 2025 (B): New installs use `UNIQUE(link)` for de-duplication. Existing databases keep their previous unique index unless you explicitly migrate:
  - `DROP INDEX IF EXISTS idx_info_unique;`
  - `CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique ON info(link);`

## Maintenance

- Vacuum/Analyze (optional) for size/performance:
```sql
VACUUM;
ANALYZE;
```

- Backup the DB file by copying `data/info.db` while no writer is running. SQLite supports online backup via the CLI if needed.

## Future Extensions

Potential columns (not present today): `summary`, `author`, `fetched_at`, `tags`. These can be added via `ALTER TABLE info ADD COLUMN ...` without affecting the unique index on `(source, publish, title)`.
