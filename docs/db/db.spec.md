# News Collector Database Specification

This document describes the SQLite schema used by the project to persist scraped articles, AI evaluation results, and DB‑backed write/deliver pipelines.

## Overview

- Engine: SQLite 3
- File path: `data/info.db` (relative to repo root)
- Producers/Consumers:
  - Collection: `news-collector/collector/collect_to_sqlite.py`
  - AI evaluation: `news-collector/evaluator/ai_evaluate.py`
  - Pipelines (DB‑backed write + deliver):
    - Admin: `news-collector/write-deliver-pipeline/pipeline_admin.py`
    - Runner: `news-collector/write-deliver-pipeline/pipeline_runner.py`
    - Writers: `news-collector/writer/email_writer.py`, `news-collector/writer/feishu_writer.py`
    - Deliveries: `news-collector/deliver/mail_deliver.py`, `news-collector/deliver/feishu_deliver.py`
- De-duplication: unique on `link` in `info` (for new databases). Existing DBs may keep older indexes unless migrated.

## Schema

Tables are grouped by purpose.

### Articles and Reviews

#### Table: `info`

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

Columns (`info`):

- `id` (INTEGER): Surrogate PK.
- `source` (TEXT): Source identifier (e.g., `gamedeveloper`, `gamesindustry.biz`, `youxituoluo`).
- `publish` (TEXT): Publication time; prefer ISO‑8601 UTC (e.g., `2025-10-24T14:27:00+00:00`). May be coarse strings when precise time is unavailable.
- `title` (TEXT): Article title.
- `link` (TEXT): Absolute URL; unique de‑dup key.
- `category` (TEXT, nullable): High‑level category such as `game`, `tech`.
- `detail` (TEXT, nullable): Plain‑text content fetched from detail pages when available.

#### Table: `info_ai_review`

Created by `ai_evaluate.py`.

```sql
CREATE TABLE IF NOT EXISTS info_ai_review (
  info_id                       INTEGER PRIMARY KEY,
  final_score                   REAL    NOT NULL,
  timeliness_score              INTEGER NOT NULL,
  game_relevance_score          INTEGER,
  mobile_game_relevance_score   INTEGER,
  ai_relevance_score            INTEGER,
  tech_relevance_score          INTEGER,
  quality_score                 INTEGER,
  insight_score                 INTEGER,
  depth_score                   INTEGER,
  novelty_score                 INTEGER,
  ai_comment                    TEXT    NOT NULL,
  ai_summary                    TEXT    NOT NULL,
  raw_response                  TEXT,
  created_at                    TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at                    TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (info_id) REFERENCES info(id)
);
```

Notes:

- Scores are 1–5 integers; `final_score` is a weighted 1–5 float used by writers.
- Newer dimensions include `depth` and `novelty`; the evaluator idempotently `ALTER TABLE`s to add any missing columns.

### DB‑Backed Pipelines (Write + Deliver)

These tables are created/ensured by `pipeline_admin.py` and used by `pipeline_runner.py`.

#### Table: `pipelines`

```sql
CREATE TABLE IF NOT EXISTS pipelines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL UNIQUE,
  enabled      INTEGER NOT NULL DEFAULT 1,
  description  TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### Table: `pipeline_filters`

```sql
CREATE TABLE IF NOT EXISTS pipeline_filters (
  pipeline_id      INTEGER NOT NULL,
  all_categories   INTEGER NOT NULL DEFAULT 1,
  categories_json  TEXT,
  all_src          INTEGER NOT NULL DEFAULT 1,
  include_src_json TEXT,
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
```

Semantics:

- `all_categories=1` means writers should include all categories. When `0`, writers read `categories_json` (JSON array of category names, e.g., `["game","tech"]`).
- `all_src`/`include_src_json` are reserved for future source whitelisting.

#### Table: `pipeline_writers`

```sql
CREATE TABLE IF NOT EXISTS pipeline_writers (
  pipeline_id         INTEGER NOT NULL,
  type                TEXT NOT NULL,   -- e.g., 'feishu_md', 'info_html'
  hours               INTEGER NOT NULL, -- lookback window
  weights_json        TEXT,             -- JSON overrides for dimension weights
  bonus_json          TEXT,             -- JSON per-source bonus map
  limit_per_category  TEXT,             -- JSON or integer text; see below
  per_source_cap      INTEGER,          -- <=0 means unlimited
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
```

Limits fields used by writers:

- `limit_per_category` (TEXT): Stored as JSON or integer string. Writers accept:
  - Integer (e.g., `"10"`) → same cap for all categories.
  - JSON object (recommended), e.g. `{"default":10, "tech":5}` → use per‑category override with a default.
- `per_source_cap` (INTEGER): Max items per source per category after sorting; `<=0` disables per‑source cap. Default behavior in writers is 3 when unspecified.

Writers read these configs when `PIPELINE_ID` is present in the environment:

- `feishu_writer.py`: loads hours/weights/bonus; applies `limit_per_category` and `per_source_cap` to produce Markdown sections per category.
- `email_writer.py`: same interpretation to produce HTML digest (unified writer for email output).

Display note:

- Writers compute final scores only from dimensions whose weights are greater than 0.0. For readability, `email_writer.py` also displays only those enabled dimensions in the per‑article “维度”行; if all weights are 0, it falls back to displaying all dimensions.

#### Delivery Tables

```sql
CREATE TABLE IF NOT EXISTS pipeline_deliveries_email (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  email        TEXT NOT NULL,
  subject_tpl  TEXT NOT NULL,
  deliver_type TEXT NOT NULL DEFAULT 'email',
  UNIQUE(pipeline_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE TABLE IF NOT EXISTS pipeline_deliveries_feishu (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  app_id       TEXT NOT NULL,
  app_secret   TEXT NOT NULL,
  to_all_chat  INTEGER NOT NULL DEFAULT 0,
  chat_id      TEXT,
  title_tpl    TEXT,
  to_all       INTEGER DEFAULT 0,      -- reserved
  content_json TEXT,                   -- reserved
  deliver_type TEXT NOT NULL DEFAULT 'feishu',
  UNIQUE(pipeline_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
```

Semantics:

- Exactly one delivery per pipeline (either email or feishu).
- Email delivery uses `subject_tpl` (supports `${date_zh}` and `${ts}` placeholders).
- Feishu delivery stores bot credentials; the deliver script will prefer DB values and set `FEISHU_APP_ID/FEISHU_APP_SECRET` at runtime. `to_all_chat=1` broadcasts to all chats the bot is in when no explicit `chat_id`/`chat_name` is provided.

#### Table: `pipeline_runs` (optional)

```sql
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  started_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  finished_at  TEXT,
  status       TEXT,
  summary      TEXT,
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
```

### Relationships & Access Patterns

- `info_ai_review.info_id` → `info.id` (1:1).
- `pipeline_*` rows are joined by `pipeline_id`. `pipelines.name` is unique for human‑friendly selection.
- `pipeline_runner.py` selects enabled pipelines, then:
  1) Runs the configured writer with `PIPELINE_ID` in env (so writers pick up DB config),
  2) Delivers via the matching delivery table.

## Insertion, Detail Fetch & De‑duplication

The collector skips duplicates via upsert:

```sql
INSERT INTO info (source, publish, title, link, category, detail)
VALUES (?, ?, ?, ?, ?, NULL)
ON CONFLICT(link) DO NOTHING;
```

Fallback for older SQLite: `INSERT OR IGNORE`.

Detail fetching for new rows (when scraper provides `fetch_article_detail(url)`):

```sql
UPDATE info SET detail = ? WHERE link = ?;
```

The collector also backfills missing details for a small, recent batch per source.

## Typical Queries

- Latest 20 across all sources:
```sql
SELECT source, publish, title, link
FROM info
ORDER BY publish DESC
LIMIT 20;
```

- Latest 10 for one source:
```sql
SELECT source, publish, title, link
FROM info
WHERE source = 'youxituoluo'
ORDER BY publish DESC
LIMIT 10;
```

- Join with AI scores:
```sql
SELECT i.id, i.source, i.category, i.publish, i.title, i.link,
       r.final_score,
       r.timeliness_score,
       r.game_relevance_score,
       r.mobile_game_relevance_score,
       r.ai_relevance_score,
       r.tech_relevance_score,
       r.quality_score,
       r.insight_score,
       r.depth_score,
       r.novelty_score,
       r.ai_comment,
       r.ai_summary
FROM info AS i
LEFT JOIN info_ai_review AS r ON r.info_id = i.id
ORDER BY i.id DESC
LIMIT 100;
```

- Pipelines overview (enabled):
```sql
SELECT p.id, p.name, p.enabled,
       w.type, w.hours, w.limit_per_category, w.per_source_cap
FROM pipelines AS p
LEFT JOIN pipeline_writers AS w ON w.pipeline_id = p.id
WHERE p.enabled = 1
ORDER BY p.id;
```

## DB‑Driven Writer Behavior

When `PIPELINE_ID` is present in the environment:

- Writers read `hours`, `weights_json`, `bonus_json` from `pipeline_writers`.
- Category filter comes from `pipeline_filters` when `all_categories=0`.
- Item limits come from:
  - `limit_per_category`: integer or JSON map (e.g., `{ "default": 10, "tech": 5 }`).
  - `per_source_cap`: integer; `<=0` disables per‑source limiting.
- CLI flags still override DB when provided (ad‑hoc runs).

## Migration Notes

- 2025‑10 A: Added `category` to `info`; backfilled by collector via `ALTER TABLE` if missing.
- 2025‑10 B: New installs use `UNIQUE(link)`; migrate old DBs if desired:
  - `DROP INDEX IF EXISTS idx_info_unique;`
  - `CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique ON info(link);`
- 2025‑10 C: Added `detail` to `info`; introduced `info_ai_review` with 9+ dimensions. Evaluator ensures columns via idempotent `ALTER TABLE`.
- 2025‑10 D: Introduced DB‑backed pipelines (`pipelines`, `pipeline_filters`, `pipeline_writers`, deliveries). `pipeline_admin.py init` creates tables.
- 2025‑10 E: Added writer limit fields to `pipeline_writers`:
  - `limit_per_category` (TEXT as integer/JSON) and `per_source_cap` (INTEGER). Admin ensures columns via `ALTER TABLE` and migration helper `scripts/migrations/202410_add_writer_limits.py` normalizes/backs fills values (defaults: 10 per category, per‑source cap 3).

## Maintenance

- Vacuum/Analyze (optional):
```sql
VACUUM;
ANALYZE;
```

- Backups: copy `data/info.db` when writers aren’t running. SQLite CLI also supports online backups.

## Appendix: Configuration Examples

- Feishu broadcast pipeline (`feishu_md`):
  - Filters: `all_categories=0`, `categories_json=["game","tech"]`
  - Writer: `hours=40`, `limit_per_category={"default":10,"tech":5}`, `per_source_cap=3`
  - Delivery: app_id/secret in `pipeline_deliveries_feishu`, `to_all_chat=1`
