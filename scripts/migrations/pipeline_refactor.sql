-- Pipeline refactor DDL (pipeline classes, evaluator/writer bindings, source runs, evaluator_key)
-- For idempotent application use scripts/migrations/pipeline_refactor.py.
-- SQLite ALTER TABLE ADD COLUMN here is non-idempotent; run only on fresh DBs.

-- 1) New tables for pipeline classes and allowed categories/evaluators/writers
CREATE TABLE IF NOT EXISTS pipeline_classes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,
  label_zh     TEXT NOT NULL,
  description  TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pipeline_class_categories (
  pipeline_class_id INTEGER NOT NULL,
  category_key      TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, category_key),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id),
  FOREIGN KEY (category_key) REFERENCES categories(key)
);

CREATE TABLE IF NOT EXISTS pipeline_class_evaluators (
  pipeline_class_id INTEGER NOT NULL,
  evaluator_key     TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, evaluator_key),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
);

CREATE TABLE IF NOT EXISTS pipeline_class_writers (
  pipeline_class_id INTEGER NOT NULL,
  writer_type       TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, writer_type),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
);

-- 2) Extend pipelines with class/debug/evaluator fields
ALTER TABLE pipelines ADD COLUMN pipeline_class_id INTEGER REFERENCES pipeline_classes(id);
ALTER TABLE pipelines ADD COLUMN debug_enabled INTEGER NOT NULL DEFAULT 0;
ALTER TABLE pipelines ADD COLUMN evaluator_key TEXT NOT NULL DEFAULT 'news_evaluator';

-- 3) Deprecate pipeline_filters.all_src (keep column for compatibility); logic should rely on all_categories/categories_json/include_src_json.

-- 4) Source run tracking (prevent re-run within 2 hours)
CREATE TABLE IF NOT EXISTS source_runs (
  source_id   INTEGER PRIMARY KEY,
  last_run_at TEXT NOT NULL,
  FOREIGN KEY (source_id) REFERENCES sources(id)
);

-- 5) info_ai_review supports multiple evaluators
-- If legacy table exists with PRIMARY KEY(info_id)，rebuild to composite PK(info_id, evaluator_key)
PRAGMA foreign_keys=off;
BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS info_ai_review_new (
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
  PRIMARY KEY (info_id, evaluator_key),
  FOREIGN KEY (info_id) REFERENCES info(id)
);
INSERT OR IGNORE INTO info_ai_review_new (info_id, evaluator_key, final_score, ai_comment, ai_summary, ai_key_concepts, ai_summary_long, raw_response, created_at, updated_at)
  SELECT info_id, 'news_evaluator', final_score, ai_comment, ai_summary, ai_key_concepts, ai_summary_long, raw_response, created_at, updated_at
  FROM info_ai_review;
DROP TABLE IF EXISTS info_ai_review;
ALTER TABLE info_ai_review_new RENAME TO info_ai_review;
CREATE UNIQUE INDEX IF NOT EXISTS ux_info_ai_review_info_eval
  ON info_ai_review (info_id, evaluator_key);
COMMIT;
PRAGMA foreign_keys=on;

-- Seed pipeline classes (adjust keys/labels as needed)
INSERT OR IGNORE INTO pipeline_classes (key, label_zh, description) VALUES
  ('general_news', '综合资讯', '新闻/资讯类管线'),
  ('legou_minigame', '乐狗副玩法', 'YouTube 小游戏推荐管线');

-- Example allowed mappings (edit per environment)
INSERT OR IGNORE INTO pipeline_class_categories (pipeline_class_id, category_key)
  SELECT pc.id, 'game' FROM pipeline_classes pc WHERE pc.key='general_news';
INSERT OR IGNORE INTO pipeline_class_categories (pipeline_class_id, category_key)
  SELECT pc.id, 'tech' FROM pipeline_classes pc WHERE pc.key='general_news';
INSERT OR IGNORE INTO pipeline_class_categories (pipeline_class_id, category_key)
  SELECT pc.id, 'game_yt' FROM pipeline_classes pc WHERE pc.key='legou_minigame';

INSERT OR IGNORE INTO pipeline_class_evaluators (pipeline_class_id, evaluator_key)
  SELECT pc.id, 'news_evaluator' FROM pipeline_classes pc WHERE pc.key='general_news';
INSERT OR IGNORE INTO pipeline_class_evaluators (pipeline_class_id, evaluator_key)
  SELECT pc.id, 'legou_minigame_evaluator' FROM pipeline_classes pc WHERE pc.key='legou_minigame';

INSERT OR IGNORE INTO pipeline_class_writers (pipeline_class_id, writer_type)
  SELECT pc.id, 'email_news' FROM pipeline_classes pc WHERE pc.key='general_news';
INSERT OR IGNORE INTO pipeline_class_writers (pipeline_class_id, writer_type)
  SELECT pc.id, 'feishu_news' FROM pipeline_classes pc WHERE pc.key='general_news';
INSERT OR IGNORE INTO pipeline_class_writers (pipeline_class_id, writer_type)
  SELECT pc.id, 'feishu_legou_game' FROM pipeline_classes pc WHERE pc.key='legou_minigame';

-- TODO: migrate existing pipelines to general_news with defaults:
-- UPDATE pipelines SET pipeline_class_id = (SELECT id FROM pipeline_classes WHERE key='general_news'), evaluator_key='news_evaluator' WHERE pipeline_class_id IS NULL;
