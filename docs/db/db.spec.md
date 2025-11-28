# News Collector Database Specification

This document describes the SQLite schema used by the project to persist scraped articles, AI evaluation results, and DB‑backed write/deliver pipelines. It also reflects the pipeline refactor that introduces pipeline classes、允许的评估器/Writer 绑定、源运行记录、以及多评估器并存的 AI 评审表结构。

## Overview

- Engine: SQLite 3
- File path: `data/info.db` (relative to repo root)
- Producers/Consumers:
  - Collection: `news-collector/collector/collect_to_sqlite.py`（遍历 DB `sources.enabled=1` 的脚本路径并执行）
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
  detail   TEXT,
  img_link TEXT,
  FOREIGN KEY (category) REFERENCES categories(key)
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
- `category` (TEXT, nullable): High‑level category such as `game`, `tech`; constrained to `categories.key`.
- `detail` (TEXT, nullable): Plain‑text content fetched from detail pages when available.
- `img_link` (TEXT, nullable): Teaser image URL captured by collectors when available.

### Sources and Categories

To standardize source management and decouple it from filesystem scanning, the collector reads two admin tables and executes enabled sources.

#### Table: `categories`

```sql
CREATE TABLE IF NOT EXISTS categories (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  key        TEXT NOT NULL UNIQUE,
  label_zh   TEXT NOT NULL,
  enabled    INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

Semantics:

- `key` is a stable identifier used in `info.category` (e.g., `game`, `tech`).
- Disable a category by setting `enabled=0` (writers may still filter by explicit lists).

#### Table: `sources`

```sql
CREATE TABLE IF NOT EXISTS sources (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,     -- e.g., 'sensortower', 'gamedeveloper'
  label_zh     TEXT NOT NULL,            -- 中文名
  enabled      INTEGER NOT NULL DEFAULT 1,
  category_key TEXT NOT NULL,            -- references categories.key
  script_path  TEXT NOT NULL,            -- repo-relative python path to the scraper file
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (category_key) REFERENCES categories(key)
);

CREATE INDEX IF NOT EXISTS idx_sources_enabled
  ON sources (enabled);
CREATE INDEX IF NOT EXISTS idx_sources_category
  ON sources (category_key, enabled);
```

Semantics:

- `script_path` is a repository-relative file path (e.g., `news-collector/collector/scraping/game/sensortower.blog.py`).
- The collector enumerates rows where `enabled=1`, imports and runs each `script_path`, and writes rows with:
  - `info.source` = `sources.key`
  - `info.category` = `sources.category_key`
- If a `script_path` is missing or import fails, the collector logs an error for that source and continues (non-zero exit optional). The failure is visible in the run log.

#### Table: `source_address`

Maintains the set of upstream URLs (RSS feeds, JSON APIs, etc.) associated with each logical source. The admin UI surfaces these addresses for quick inspection and editing.

```sql
CREATE TABLE IF NOT EXISTS source_address (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  source_id INTEGER NOT NULL,
  address   TEXT NOT NULL,
  FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_source_address_source
  ON source_address (source_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_source_address_unique
  ON source_address (source_id, address);
```

Semantics:

- `address` records the concrete fetch endpoint used by the scraper (RSS feed URL, REST endpoint, etc.).
- `source_id` references `sources.id`; cascade delete keeps the table in sync when a source is removed.
- UI validation requires at least one address per source; duplicates are deduplicated before insert.

#### Table: `ai_metrics`

Defines the available evaluation metrics (clean rebuild; no migration of old columns).

```sql
CREATE TABLE IF NOT EXISTS ai_metrics (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  key            TEXT NOT NULL UNIQUE,
  label_zh       TEXT NOT NULL,
  rate_guide_zh  TEXT,
  default_weight REAL,
  active         INTEGER NOT NULL DEFAULT 1,
  sort_order     INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_metrics_active
  ON ai_metrics (active, sort_order);
```
 - Seed metric for 副玩法/小游戏：
  - `rok_cod_fit`：label_zh="ROK/COD 副玩法结合可能性"，rate_guide_zh="5-高度可行；3-有限可行；1-不合适"，default_weight=1.0，sort_order=10，active=1。`legou_minigame_evaluator` 可只写入这一项，也可将 `final_score` 与该分数保持一致。

#### Table: `info_ai_scores`

Stores per-article per-metric scores (long table; 1..N rows per article).

```sql
CREATE TABLE IF NOT EXISTS info_ai_scores (
  info_id   INTEGER NOT NULL,
  metric_id INTEGER NOT NULL,
  score     INTEGER NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (info_id, metric_id),
  FOREIGN KEY (info_id) REFERENCES info(id),
  FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
);

CREATE INDEX IF NOT EXISTS idx_info_ai_scores_info
  ON info_ai_scores (info_id);
CREATE INDEX IF NOT EXISTS idx_info_ai_scores_metric
  ON info_ai_scores (metric_id);
```

#### Table: `info_ai_review`

Created by evaluators。支持多评估器并存（按 `evaluator_key` 区分）。

```sql
CREATE TABLE IF NOT EXISTS info_ai_review (
  info_id        INTEGER NOT NULL,
  evaluator_key  TEXT    NOT NULL DEFAULT 'news_evaluator',
  final_score    REAL    NOT NULL DEFAULT 0.0,
  ai_comment     TEXT    NOT NULL,
  ai_summary     TEXT    NOT NULL,
  ai_key_concepts TEXT,
  ai_summary_long TEXT,
  raw_response   TEXT,
  created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (info_id, evaluator_key)
);
```

Notes:

- Metric scores live in `info_ai_scores`。Writers 按 `evaluator_key` 读取对应评审；同一资讯可被多个评估器写入不同记录。
- `ai_key_concepts` stores a JSON 数组（或空值）描述文章的关键词；`ai_summary_long` 为约 50 字的拓展摘要。
- 对 `legou_minigame_evaluator`：`ai_summary` 填一句话游戏介绍，`ai_comment` 填一句话说明与 ROK/COD 副玩法结合的可行性/建议（不可行时要说明原因），`final_score` 建议与 `rok_cod_fit` 同值。`rok_cod_fit` 必须落地到 `info_ai_scores`（1-5 分），无需其他指标。
- 现有 DB 在 `(info_id, evaluator_key)` 上同时拥有主键与唯一索引；未声明外键，约束由应用逻辑保证。

#### Table: `evaluators`

Registers available评估器（用于前端配置与管线类的 allowlist）。

```sql
CREATE TABLE IF NOT EXISTS evaluators (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,
  label_zh     TEXT NOT NULL,
  description  TEXT,
  prompt       TEXT,
  active       INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### Table: `evaluator_metrics`

Evaluator 允许的指标列表（长表形式）。

```sql
CREATE TABLE IF NOT EXISTS evaluator_metrics (
  evaluator_id INTEGER NOT NULL,
  metric_id    INTEGER NOT NULL,
  PRIMARY KEY (evaluator_id, metric_id),
  FOREIGN KEY (evaluator_id) REFERENCES evaluators(id) ON DELETE CASCADE,
  FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
);

CREATE INDEX IF NOT EXISTS idx_evaluator_metrics_eval
  ON evaluator_metrics (evaluator_id);
```

Semantics:

- 仅允许在此映射表中出现的 metric 写入/展示；未配额者会被跳过。
- 删除 evaluator 会级联清空其映射（`ON DELETE CASCADE`）。

### DB‑Backed Pipelines (Write + Deliver)

These tables are created/ensured by `pipeline_admin.py` and used by `pipeline_runner.py`.

#### Table: `pipeline_classes`

```sql
CREATE TABLE IF NOT EXISTS pipeline_classes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,
  label_zh     TEXT NOT NULL,
  description  TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### Table: `pipeline_class_categories`

```sql
CREATE TABLE IF NOT EXISTS pipeline_class_categories (
  pipeline_class_id INTEGER NOT NULL,
  category_key      TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, category_key),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id),
  FOREIGN KEY (category_key) REFERENCES categories(key)
);
```

#### Table: `pipeline_class_evaluators`

```sql
CREATE TABLE IF NOT EXISTS pipeline_class_evaluators (
  pipeline_class_id INTEGER NOT NULL,
  evaluator_key     TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, evaluator_key),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
);
```

#### Table: `pipeline_class_writers`

```sql
CREATE TABLE IF NOT EXISTS pipeline_class_writers (
  pipeline_class_id INTEGER NOT NULL,
  writer_type       TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, writer_type),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
);
```

#### Table: `pipelines`

```sql
CREATE TABLE IF NOT EXISTS pipelines (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  name          TEXT NOT NULL,
  enabled       INTEGER NOT NULL DEFAULT 1,
  description   TEXT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  owner_user_id INTEGER,
  debug_enabled INTEGER NOT NULL DEFAULT 0,
  weekdays_json TEXT,
  pipeline_class_id INTEGER REFERENCES pipeline_classes(id),
  evaluator_key TEXT NOT NULL DEFAULT 'news_evaluator'
);

CREATE INDEX IF NOT EXISTS idx_pipelines_owner
  ON pipelines (owner_user_id);
```

Semantics:

- `name` is not unique in the current DB；管理端应按 `id`/owner 识别。
- `pipeline_class_id` 可为空（兼容旧库）；初始化时会尝试回填到默认 `general_news`。
- `owner_user_id` 记录管线创建者，便于权限与退订通知。
- `evaluator_key` 为管线默认评估器；用于 writers 读取对应的 `info_ai_review`。

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

CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_filters_pipeline_id
  ON pipeline_filters(pipeline_id);
```

Semantics:

- `all_categories=1` means writers should include全部允许的类别（由 `pipeline_class_categories` 决定）。When `0`, writers read `categories_json` (JSON array of category names, e.g., `["game","tech"]`).
- `all_src`：1=默认包含所有源；0 时 `include_src_json` 才生效。
- `include_src_json`：当 `all_src=0` 且某分类未被全选时，允许按源白名单补充。
- `pipeline_id` 唯一（`ux_pipeline_filters_pipeline_id`），每个管线仅一条过滤配置。

#### Table: `pipeline_writers`

```sql
CREATE TABLE IF NOT EXISTS pipeline_writers (
  pipeline_id         INTEGER NOT NULL,
  type                TEXT NOT NULL,   -- e.g., 'email_news','feishu_news','feishu_legou_game'
  hours               INTEGER NOT NULL, -- lookback window
  weights_json        TEXT,             -- JSON overrides: metric-key -> weight
  bonus_json          TEXT,             -- JSON per-source bonus map
  limit_per_category  TEXT,             -- JSON or integer text; see below
  per_source_cap      INTEGER,          -- <=0 means unlimited
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pipeline_writers_pipeline_id
  ON pipeline_writers(pipeline_id);
```

Semantics:

- `pipeline_id` 唯一，当前 DB 仅允许每条管线存在一条 writer 配置记录（通过 `ux_pipeline_writers_pipeline_id` 强制）。

#### Table: `pipeline_writer_metric_weights`

Stores per-writer (per-pipeline) metric weights in a normalized long table. Preferred over JSON for robustness.

```sql
CREATE TABLE IF NOT EXISTS pipeline_writer_metric_weights (
  pipeline_id INTEGER NOT NULL,
  metric_id   INTEGER NOT NULL,
  weight      REAL    NOT NULL,           -- >= 0; 0 means included but not scored
  enabled     INTEGER NOT NULL DEFAULT 1, -- 1=enabled, 0=disabled
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (pipeline_id, metric_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id),
  FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
);

CREATE INDEX IF NOT EXISTS idx_wm_weights_pipeline
  ON pipeline_writer_metric_weights (pipeline_id);
```

Limits fields used by writers:

- `limit_per_category` (TEXT): Stored as JSON or integer string. Writers accept:
  - Integer (e.g., `"10"`) → same cap for all categories.
  - JSON object (recommended), e.g. `{"default":10, "tech":5}` → use per‑category override with a default.
- `per_source_cap` (INTEGER): Max items per source per category after sorting; `<=0` disables per‑source cap. Default behavior in writers is 3 when unspecified.

Writers read these configs when `PIPELINE_ID` is present in the environment:

- `feishu_writer.py`: loads hours/weights/bonus; weights are keyed by active metric `key` (see `ai_metrics`); applies `limit_per_category` and `per_source_cap` to produce Markdown sections per category。
- `email_writer.py`: same interpretation to produce HTML digest (unified writer for email output)。

Display note:

- Weight precedence: `pipeline_writer_metric_weights` > `pipeline_writers.weights_json` > `ai_metrics.default_weight`.
- Metric set: when long-table rows exist for the pipeline, use rows with `enabled=1`; otherwise use `ai_metrics(active=1)`.
- Writers compute final scores only from metrics whose weights are greater than 0.0. Metrics and labels are loaded from `ai_metrics(active=1 ORDER BY sort_order)`.

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

Unsubscribe behavior:

- `GET /unsubscribe` / `GET /api/unsubscribe` 仅将目标管线的 `enabled` 置为 0，不再写入退订表。
- 旧版本若仍包含退订表，可运行 `scripts/migrations/202512_remove_unsubscribe_tables.py` 删除 `unsubscribed_emails` / `pipeline_unsubscribed`。

### Auth & Users

#### Table: `users`

```sql
CREATE TABLE IF NOT EXISTS users (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  email          TEXT NOT NULL UNIQUE,
  name           TEXT NOT NULL,
  is_admin       INTEGER NOT NULL DEFAULT 0,
  avatar_url     TEXT,
  created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  verified_at    TEXT,
  last_login_at  TEXT,
  enabled        INTEGER NOT NULL DEFAULT 1,
  manual_push_count    INTEGER NOT NULL DEFAULT 0, -- 当日手动推送计数
  manual_push_date     TEXT,                       -- 计数对应的日期（YYYY-MM-DD）
  manual_push_last_at  TEXT                        -- 最近一次手动推送时间戳
);
```

Semantics:

- `manual_push_count` / `manual_push_date` / `manual_push_last_at` track per-user manual “立即推送”使用频率。服务端默认限制：10 秒冷却、每日最多 20 次（见 `MANUAL_PUSH_COOLDOWN_SECONDS` / `MANUAL_PUSH_DAILY_LIMIT` 环境变量）。
- `enabled=0` 会阻止登录与手动推送。

#### Table: `user_sessions`

```sql
CREATE TABLE IF NOT EXISTS user_sessions (
  id            TEXT PRIMARY KEY,
  user_id       INTEGER NOT NULL,
  token_hash    TEXT NOT NULL,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  last_seen_at  TEXT,
  expires_at    TEXT NOT NULL,
  revoked_at    TEXT,
  ip            TEXT,
  user_agent    TEXT,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_user_sessions_token_hash
  ON user_sessions (token_hash);

CREATE INDEX IF NOT EXISTS idx_user_sessions_user
  ON user_sessions (user_id, expires_at);
```

Semantics: stores hashed session tokens; `expires_at` controls validity, `revoked_at` marks explicit logout/revocation.

#### Table: `auth_email_codes`

```sql
CREATE TABLE IF NOT EXISTS auth_email_codes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  email         TEXT NOT NULL,
  user_id       INTEGER,
  purpose       TEXT NOT NULL,
  code_hash     TEXT NOT NULL,
  expires_at    TEXT NOT NULL,
  consumed_at   TEXT,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 5,
  resent_count  INTEGER NOT NULL DEFAULT 0,
  created_ip    TEXT,
  user_agent    TEXT,
  created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_codes_active_unique
ON auth_email_codes (email, purpose)
WHERE consumed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_auth_codes_lookup
  ON auth_email_codes (email, purpose, expires_at);
```

Semantics: one active code per `(email, purpose)`；`attempt_count`/`max_attempts` 控制输错上限，`consumed_at` 标记成功使用。

### Source run tracking

#### Table: `source_runs`

```sql
CREATE TABLE IF NOT EXISTS source_runs (
  source_id   INTEGER PRIMARY KEY,
  last_run_at TEXT NOT NULL
);
```

 Semantics: 每个源最近一次抓取时间，用于 orchestrator 判断是否在 2 小时内已跑过（当前表未声明外键约束）。

### Relationships & Access Patterns

- `info.category` 受 `categories.key` 外键约束；`info_ai_scores.metric_id` 指向 `ai_metrics.id`；`info_ai_review` 以 `(info_id, evaluator_key)` 为主键但当前 DB 未声明外键。
- `pipeline_*` 表通过 `pipeline_id` 关联；`pipeline_filters`/`pipeline_writers` 各自仅一条记录；`pipelines.pipeline_class_id`（如有）受 `pipeline_classes` 约束；`pipeline_class_*` 列表控制允许的类别/评估器/Writer（评估器 key 未声明 FK）。
- Orchestrator 执行顺序：collect（按 source_runs 跳过近期源）→ evaluate（按 evaluator_key 写评审）→ write → deliver。

## Insertion, Detail Fetch & De‑duplication

The collector skips duplicates via upsert:

```sql
INSERT INTO info (source, publish, title, link, category, detail, img_link)
VALUES (?, ?, ?, ?, ?, NULL, ?)
ON CONFLICT(link) DO NOTHING;
```

Fallback for older SQLite: `INSERT OR IGNORE`.

Detail fetching for new rows (when scraper provides `fetch_article_detail(url)`):

```sql
UPDATE info SET detail = ? WHERE link = ?;
```

The collector also backfills missing details for a small, recent batch per source.

Notes on collector behavior (with `sources` table):

- `info.source` is always set from `sources.key`; `info.category` is set from `sources.category_key` (overrides script-provided values if any).
- `img_link` 会在抓取到封面图时落库，否则为 NULL。
- The collector iterates `sources` where `enabled=1`; if a `script_path` does not exist or cannot be imported, it logs an error for that source and continues.

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

- Join with AI scores (long table):
```sql
SELECT i.id,
       i.source,
       i.category,
       i.publish,
       i.title,
       i.link,
       m.key   AS metric_key,
       s.score AS metric_score,
       r.ai_comment,
       r.ai_summary
FROM info AS i
LEFT JOIN info_ai_review AS r ON r.info_id = i.id
LEFT JOIN info_ai_scores AS s ON s.info_id = i.id
LEFT JOIN ai_metrics AS m ON m.id = s.metric_id AND m.active = 1
ORDER BY i.id DESC, m.sort_order ASC
LIMIT 200;
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
- Category/source filters come from `pipeline_filters`: `all_categories=0` 时使用 `categories_json`，`all_src=0` 时使用 `include_src_json`。
- Item limits come from:
  - `limit_per_category`: integer or JSON map (e.g., `{ "default": 10, "tech": 5 }`).
  - `per_source_cap`: integer; `<=0` disables per‑source limiting.
- CLI flags still override DB when provided (ad‑hoc runs).

Weights semantics (clean rebuild):

- `weights_json` uses metric `key` as JSON field names, e.g. `{ "timeliness": 0.2, "game_relevance": 0.4 }`。
- Unknown keys are ignored. For missing keys, writers use `ai_metrics.default_weight` (null → treat as 0.0).
- Writers compute final scores from active metrics only (`ai_metrics.active=1`), ordered by `sort_order`.

## Release Notes

- 2025‑10 A: Added `category` to `info`.
- 2025‑10 B: New installs use `UNIQUE(link)` on `info(link)`.
- 2025‑10 C: Added `detail` to `info`.
- 2025‑10 D: Introduced DB‑backed pipelines (`pipelines`, `pipeline_filters`, `pipeline_writers`, deliveries).
- 2025‑10 E: Added writer limit fields to `pipeline_writers`: `limit_per_category` (TEXT as integer/JSON) and `per_source_cap` (INTEGER).
- 2025‑10 F: Rebuilt AI metrics architecture (clean): added `ai_metrics` + `info_ai_scores`; `info_ai_review` holds text outputs only; `weights_json` uses metric `key`.
- 2025‑10 G: Pipeline refactor：新增 pipeline_classes / 允许映射表；pipelines 增加 evaluator/debug/class；source_runs 记录最近抓取；info_ai_review 引入 evaluator_key（复合主键）。
- 2025‑11 A: Added `img_link` to `info` 并对 `category` 声明外键到 `categories.key`。
- 2025‑11 B: Added evaluator registry tables：`evaluators`、`evaluator_metrics`。
- 2025‑11 C: Pipelines cleanup：`pipelines.name` 不再唯一，新增 `owner_user_id` 索引；`pipeline_filters` 增加 `all_src` 且一管线一行；`pipeline_writers` 仅允许一行配置。
- 2025‑12 A: 移除退订记录表（`unsubscribed_emails`、`pipeline_unsubscribed`），退订仅将管线 `enabled` 置 0。

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
