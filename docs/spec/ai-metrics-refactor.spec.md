# AI 评估指标重构（Clean Rebuild SPEC）

本规范基于“可重建数据库”的前提，直接采用全新设计：将“指标定义”与“评分数据”解耦，支持指标的集中管理、扩展与稳定引用；不考虑旧库迁移与兼容，按新表结构初始化并配套代码改造。

## 目标与非目标

- 目标
  - 将评估指标抽象为可配置的“指标表”，支持新增/下线/重命名与描述更新。
  - 评分数据改为“长表”（一条资讯 × 多指标），避免频繁 `ALTER TABLE`。
  - Writer 权重配置改为按“指标 key”对齐，跨环境稳定且可读。
  - Evaluator 动态读取指标并生成提示词，引导模型输出 `key → score` 映射。
-  - 清晰的冷启动流程：新库直接建表并落入默认指标，无需数据迁移。
- 非目标
  - 不兼容旧表的分数字段；旧列不再创建。
  - 不改变 pipelines 的基础结构与运行流程（仅解释 weights_json 的新语义）。

## 数据库变更（SQLite）

### 1) 新表：`ai_metrics`（指标定义）

```sql
CREATE TABLE IF NOT EXISTS ai_metrics (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  key            TEXT NOT NULL UNIQUE,              -- 稳定引用（如 'game_relevance'）
  label_zh       TEXT NOT NULL,                     -- 中文显示名（如 '游戏相关性'）
  rate_guide_zh  TEXT,                              -- 评分指导（中文长文）
  default_weight REAL,                              -- 建议默认权重（可空）
  active         INTEGER NOT NULL DEFAULT 1,        -- 1=启用，0=停用
  sort_order     INTEGER NOT NULL DEFAULT 0,        -- UI/Writer 展示顺序
  created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ai_metrics_active
  ON ai_metrics (active, sort_order);
```

说明：

- 采用 `key` 作为跨环境“稳定标识”（推荐在配置与提示词中使用）。
- `default_weight` 为建议值，Writer 若没配置覆盖则可引用。
- `sort_order` 用于统一维度展示与计算顺序（升序）。

建议初始化（示例）：

```sql
INSERT OR IGNORE INTO ai_metrics (key, label_zh, rate_guide_zh, default_weight, sort_order) VALUES
  ('timeliness', '时效性', '5-当天/最新；3-一月内或时间无关（长期有价值）；1-过时', 0.14, 10),
  ('game_relevance', '游戏相关性', '5-核心聚焦游戏议题/数据/案例；3-泛娱乐与游戏相关；1-无关', 0.20, 20),
  ('mobile_game_relevance', '手游相关性', '5-聚焦手游（产品/发行/买量/市场数据）；3-部分相关；1-无关', 0.09, 30),
  ('ai_relevance', 'AI相关性', '5-模型/算法/评测/标杆案例；3-泛AI应用；1-无关', 0.14, 40),
  ('tech_relevance', '科技相关性', '5-芯片/云/硬件/基础设施；3-泛科技商业动态；1-无关', 0.11, 50),
  ('quality', '文章质量', '5-结构严谨数据充分；3-结构一般信息适中；1-水文/缺依据', 0.13, 60),
  ('insight', '洞察力', '5-罕见且深刻的观点/关联/因果；3-常见分析；1-无洞见', 0.08, 70),
  ('depth', '深度', '5-分层拆解背景充分逻辑完整；3-覆盖关键事实；1-浅尝辄止', 0.06, 80),
  ('novelty', '新颖度', '5-罕见消息或独到观点；3-常见进展/整合；1-无新意', 0.05, 90);
```

### 2) 新表：`info_ai_scores`（评分长表）

```sql
CREATE TABLE IF NOT EXISTS info_ai_scores (
  info_id   INTEGER NOT NULL,
  metric_id INTEGER NOT NULL,
  score     INTEGER NOT NULL,               -- 1-5
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

说明：

- 每条资讯对每个“活动中的指标”各有一条评分记录，方便扩展与统计。
- 如未来新增指标，仅插入新指标的评分，不需要 `ALTER TABLE`。

### 3) 文本输出表：`info_ai_review`

用于存放 AI 生成的“文本类结果”与原始响应，不再包含各维度分数字段：

```sql
CREATE TABLE IF NOT EXISTS info_ai_review (
  info_id     INTEGER PRIMARY KEY,
  final_score REAL NOT NULL DEFAULT 0.0,    -- 可按需保留；Writer 通常动态计算
  ai_comment  TEXT    NOT NULL,
  ai_summary  TEXT    NOT NULL,
  raw_response TEXT,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (info_id) REFERENCES info(id)
);
```

### 4) `pipeline_writers` 的权重 JSON（weights_json）

- 新语义：以“指标 key”为键，值为权重（浮点数 ≥0）。示例：
  ```json
  {
    "timeliness": 0.20,
    "game_relevance": 0.40,
    "insight": 0.35,
    "depth": 0.25,
    "novelty": 0.20
  }
  ```
- 解析规则：
  - 仅接受存在于 `ai_metrics(active=1)` 的 key；未知 key 忽略。
  - 未配置的指标，Writer 读取 `ai_metrics.default_weight`；若为空则当作 0 处理。

### 5) 新表：`pipeline_writer_metric_weights`（每个 writer 的指标与权重长表）

为避免 JSON 配置的稳健性问题，引入标准化权重表；当该表存在且某个 pipeline 有配置行时，Writer 以该表为权重“权威来源”。

```sql
CREATE TABLE IF NOT EXISTS pipeline_writer_metric_weights (
  pipeline_id INTEGER NOT NULL,
  metric_id   INTEGER NOT NULL,
  weight      REAL    NOT NULL,           -- >= 0；0 表示参与集合但不计分
  enabled     INTEGER NOT NULL DEFAULT 1, -- 1 表示该指标在该 writer 中启用
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (pipeline_id, metric_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id),
  FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
);

CREATE INDEX IF NOT EXISTS idx_wm_weights_pipeline
  ON pipeline_writer_metric_weights (pipeline_id);
```

使用约定：

- 优先级：`pipeline_writer_metric_weights` > `pipeline_writers.weights_json` > `ai_metrics.default_weight`。
- 维度集合：
  - 若权重表中存在该 pipeline 的记录，则集合默认取表中 `enabled=1` 的 metrics；
  - 否则，集合取 `ai_metrics.active=1`。
- 计算原则：仅对“权重 > 0”的指标参与加权；`enabled=0` 的指标不参与。
- 管理：建议在 Admin CLI 增加 set/unset/list 命令以维护该表（后续实现）。

示例初始化（将三个指标纳入 pipeline 1 的计算）：

```sql
INSERT OR REPLACE INTO pipeline_writer_metric_weights (pipeline_id, metric_id, weight, enabled)
SELECT 1, m.id, v.weight, 1
FROM ai_metrics AS m
JOIN (
  VALUES
    ('timeliness', 0.20),
    ('game_relevance', 0.40),
    ('insight', 0.35)
) AS v(key, weight) ON v.key = m.key;
```

## 完整表结构（Clean Rebuild Snapshot）

以下为本次改造后，数据库中所有表的期望结构（便于整体审阅与一次性初始化）。

### 1. Articles / AI

1) `info`

```sql
CREATE TABLE IF NOT EXISTS info (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  source   TEXT NOT NULL,
  publish  TEXT NOT NULL,
  title    TEXT NOT NULL,
  link     TEXT NOT NULL,
  category TEXT,                         -- FK to categories.key
  detail   TEXT,
  FOREIGN KEY (category) REFERENCES categories(key)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_info_link_unique
  ON info (link);
```

2) `ai_metrics`

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

3) `info_ai_scores`

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

4) `info_ai_review`

```sql
CREATE TABLE IF NOT EXISTS info_ai_review (
  info_id     INTEGER PRIMARY KEY,
  final_score REAL    NOT NULL DEFAULT 0.0,
  ai_comment  TEXT    NOT NULL,
  ai_summary  TEXT    NOT NULL,
  raw_response TEXT,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (info_id) REFERENCES info(id)
);
```

### 2. Sources / Categories

1) `categories`

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

2) `sources`

```sql
CREATE TABLE IF NOT EXISTS sources (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,
  label_zh     TEXT NOT NULL,
  enabled      INTEGER NOT NULL DEFAULT 1,
  category_key TEXT NOT NULL,
  script_path  TEXT NOT NULL,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (category_key) REFERENCES categories(key)
);

CREATE INDEX IF NOT EXISTS idx_sources_enabled
  ON sources (enabled);
CREATE INDEX IF NOT EXISTS idx_sources_category
  ON sources (category_key, enabled);
```

### 3. Pipelines (Write + Deliver)

1) `pipelines`

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

2) `pipeline_filters`

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

3) `pipeline_writers`

```sql
CREATE TABLE IF NOT EXISTS pipeline_writers (
  pipeline_id         INTEGER NOT NULL,
  type                TEXT NOT NULL,
  hours               INTEGER NOT NULL,
  weights_json        TEXT,
  bonus_json          TEXT,
  limit_per_category  TEXT,
  per_source_cap      INTEGER,
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
```

4) `pipeline_writer_metric_weights`

```sql
CREATE TABLE IF NOT EXISTS pipeline_writer_metric_weights (
  pipeline_id INTEGER NOT NULL,
  metric_id   INTEGER NOT NULL,
  weight      REAL    NOT NULL,
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (pipeline_id, metric_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id),
  FOREIGN KEY (metric_id) REFERENCES ai_metrics(id)
);

CREATE INDEX IF NOT EXISTS idx_wm_weights_pipeline
  ON pipeline_writer_metric_weights (pipeline_id);
```

5) `pipeline_deliveries_email`

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
```

6) `pipeline_deliveries_feishu`

```sql
CREATE TABLE IF NOT EXISTS pipeline_deliveries_feishu (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  app_id       TEXT NOT NULL,
  app_secret   TEXT NOT NULL,
  to_all_chat  INTEGER NOT NULL DEFAULT 0,
  chat_id      TEXT,
  title_tpl    TEXT,
  to_all       INTEGER DEFAULT 0,
  content_json TEXT,
  deliver_type TEXT NOT NULL DEFAULT 'feishu',
  UNIQUE(pipeline_id),
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
```

7) `pipeline_runs`（可选）

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

## 冷启动与代码配合（无迁移）

1) 初始化新库：建表 `info`（原样）、`ai_metrics`、`info_ai_scores`、`info_ai_review`，并插入默认指标（见上）。

2) Evaluator：
- 动态读取 `ai_metrics(active=1 ORDER BY sort_order)`，组装提示词（见下节）。
- 期望模型返回 `{"dimension_scores": {<key>: 1-5, ...}, "comment": ..., "summary": ...}`。
- 将打分写入 `info_ai_scores`（按 key → metric_id 映射），文本写入 `info_ai_review`；`final_score` 可置 0 由 Writer 动态计算。

3) Writers（Feishu/Email）：
- 读取 `ai_metrics` 获取 label 与默认权重；若存在 `pipeline_writer_metric_weights` 的该 pipeline 配置，则以其为权重与集合来源；否则使用 `weights_json`（key 覆盖），仍缺失则用 `default_weight`。
- 从 `info_ai_scores` 聚合得到 `key→score`，参与加权与排序；per-source bonus、limit 等逻辑不变。
- 仅显示 `权重>0` 的指标在“维度”行（如需）。

## 受影响的代码与修改要点

- 文件：`news-collector/evaluator/ai_evaluate.py`
  - 新增：
    - 读取指标：`SELECT id, key, label_zh, rate_guide_zh, default_weight FROM ai_metrics WHERE active=1 ORDER BY sort_order, id`。
    - 动态组装提示词（见“提示词变更”）。
    - 响应解析：校验 `dimension_scores` 仅包含 active 指标 key，值为 1–5 整数。
  - 存储：
    - 批量 `INSERT OR REPLACE INTO info_ai_scores (info_id, metric_id, score)`。
    - `info_ai_review` 仅写 `ai_comment/ai_summary/raw_response`；`final_score` 可置 0（由 Writer 计算）。
    - 迁移期：若 `AI_LEGACY_BACKFILL=1`，同步更新旧列。
  - 失败兜底：当 `ai_metrics` 不存在时，自动落表+初始化默认指标（保证冷启动）。

- 文件：`news-collector/writer/feishu_writer.py`
  - 移除硬编码 `DIMENSION_LABELS/ORDER/DEFAULT_WEIGHTS` 的强依赖；
  - 启动时查询 `ai_metrics`：得到顺序、label、默认权重；若表缺失→回退到旧常量。
  - 查询评分：
    - 方案一（推荐）：一次查询拉取最近窗口内的 `info.id, key, score, summary`，在 Python 聚合为 `dict[key]=score`。
    - 权重：解析 `weights_json`（key 语义）→ 覆盖 `default_weight`；未知 key 忽略。
  - 计算：仅对 `权重>0` 的指标参与加权；应用 per-source bonus 与阈值过滤逻辑保持不变。

- 文件：`news-collector/writer/email_writer.py`
  - 同 `feishu_writer.py` 的改造方式（动态维度/权重/评分聚合）。

- 文件：`news-collector/write-deliver-pipeline/pipeline_admin.py`
  - 文档化 `weights_json` 为“指标 key”→权重；导出/导入沿用字符串 key。
  - 兼容：如遇数字键（疑似 id），导入时可尝试转 key（通过 `SELECT key FROM ai_metrics WHERE id=?`）。

- 文件：`news-collector/write-deliver-pipeline/pipeline_runner.py`
  - 无需结构性修改；仅更新帮助信息注释，标注 weights_json 的新语义。

- 文档：
  - `docs/db/db.spec.md`：补充 `ai_metrics` 与 `info_ai_scores` 的表定义、关系与查询示例；标注 `info_ai_review` 旧列已废弃。
  - `docs/pipelines-guide.md`：更新 weights_json 示例与说明。
  - `docs/add-new-writer.md`：标注维度来源于 `ai_metrics`。

- 迁移脚本：`scripts/migrations/202510_ai_metrics_refactor.py`
  - 落表、初始化、回填长表、（可选）创建旧视图。

## 提示词变更（动态指标）

将评估器的 Prompt（存储在数据库 `evaluators.prompt`）模板化，保留 `<<SYS>>/<<USER>>`，新增占位：`{{metrics_block}}` 与 `{{schema_example}}`。

示例（片段）：

```
<<SYS>>
你是一名资深的中文科技与游戏行业资讯编辑...（略）
<<USER>>
请阅读以下文章信息并完成评估：

文章标题：{{title}}
文章来源：{{source}}
发布时间：{{publish}}
正文内容：
{{detail}}

维度与评分指导（仅按以下 key 给分 1-5）：
{{metrics_block}}

请仅输出一个 JSON：
{{schema_example}}
```

由 Evaluator 运行时填充：

- `metrics_block`：逐行罗列 active 指标，例如：
  - `timeliness`（时效性）：5-当天/最新；3-一月内或时间无关（长期有价值）；1-过时
  - `game_relevance`（游戏相关性）：...
- `schema_example`：
  ```json
  {
    "dimension_scores": {"timeliness": 5, "game_relevance": 4, ...},
    "comment": "一句话中文评价",
    "summary": "一句话介绍文章内容"
  }
  ```

解析要求不变：确保所有 active key 均返回 1–5；若缺失/非法则报错/回退。

## 回退与健壮性

- 若运行时缺少 `ai_metrics`/`info_ai_scores`，Evaluator 可自动建表并初始化默认指标；
- Writer 在表缺失时可直接报告“缺少 AI 评分表”，鼓励先跑 Evaluator；不再提供旧列回退。

## 示例查询

- 最近 20 条的评分（动态）：
  ```sql
  SELECT i.id, i.source, i.category, i.publish, i.title, i.link,
         m.key AS metric_key, s.score, r.ai_summary
  FROM info AS i
  LEFT JOIN info_ai_review AS r ON r.info_id = i.id
  JOIN info_ai_scores AS s ON s.info_id = i.id
  JOIN ai_metrics AS m ON m.id = s.metric_id AND m.active = 1
  ORDER BY i.id DESC, m.sort_order ASC
  LIMIT 200;
  ```

- Writer 侧计算权重的示意（Python 聚合）：
  - 取 `weights_json`（key→float）覆盖 `ai_metrics.default_weight`。
  - 仅对 `weight>0` 指标参与加权，得到 `final_score`。

## 风险与缓解

- 模型输出 key 可能大小写/拼写漂移：严格验证，仅接受 `ai_metrics` 中的 key；其余忽略并告警。
- 指标变更导致历史 `weights_json` 失效：
  - 在 `pipeline_admin export` 时输出当前有效 key，便于审阅；
  - 导入时遇到未知 key 记录 warning；
  - 可提供 `scripts/audit_weights.py` 审计脚本（后续补充）。
- SQLite JSON1 缺失：避免强依赖 JSON 聚合，采用 Python 聚合或 CASE pivot。

## 落地步骤

1. 把当前的 db 文件改名追加 _old 
2. 提交本次 DB 与代码改造
3. 初始化表结构。
4. 复刻当前（old 数据库）的 pipeline 1(给 306483372@qq.com 发邮件) 和 pipeline 3 （给飞书广播消息）配置，写进新数据库的 pipeline 相关配置
5. 在 Evaluator 增加“写长表+回填旧列”的并行写路径；在 Writers 增加“优先读长表”的读路径。
6. 修改其他相关文件和代码（如 prompt 与其相关的 evaluator）
7. 文档更新(如 db.spec.sh)、示例 pipelines 更新 `weights_json` 为 key 形式
8. 更新各个 auto sh 脚本（如果需要）
9. 执行 auto-pipeline-once.sh 看是否能顺利运行完成（如果发现问题则及时修改）
10. 检查前后端代码，看是否有需要匹配着修改的
11. 测试访问前端，并模拟用户进行简单的查看和修改操作，看看是否符合预期（不符合就及时修改）

---

附：本次涉及主要文件

- 评估：`news-collector/evaluator/ai_evaluate.py`
- Writer：`news-collector/writer/feishu_writer.py`、`news-collector/writer/email_writer.py`
- Pipeline：`news-collector/write-deliver-pipeline/pipeline_admin.py`、`pipeline_runner.py`
- 提示词：数据库 `evaluators.prompt`（模板化占位，可通过 CLI 导出调试）
