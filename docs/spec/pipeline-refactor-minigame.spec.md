# 推送管线重构（管线类别 + 来源树 + 新副玩法管线）SPEC

目标：重构管线与配置模型，让现有工程能够并行支撑“综合资讯”与“乐狗副玩法（YouTube 小游戏推荐）”两类管线；统一 DB 存储、源选择逻辑、AI 评估器/Writer 组合，并在前端 Admin 中提供对应配置体验。

## 1. 范围与不做的事
- 范围：DB 结构扩展、管线类别与来源选择逻辑、管线配置（评估器、Writer、推送）、Runner/Writer/Evaluator/Deliver 约束、Admin 前端改造、迁移与回滚方案、测试要点。
- 不做：新闻/文章采集逻辑调整；Play Store 爬取；用户系统改动；收件人/飞书推送协议改动。

## 2. 概念与分类
- 管线类别（Pipeline Class）：限定一条管线的“业务类型”和可用的“来源类别/评估器/Writer”集合。当前两类：
  1) `general_news`（综合资讯）
  2) `legou_minigame`（乐狗副玩法 / YouTube 小游戏推荐）
- 来源类别（Category）：沿用 `categories.key`，如 `game`、`tech`、`game_yt`。
- 评估器（Evaluator）：二选一
  - `news_evaluator`：现有资讯评估器。
  - `legou_minigame_evaluator`：新副玩法评估器（阿里云）。
- Writer 类型：
  - `email_news`
  - `feishu_news`
  - `feishu_legou_game`

- Play Store：副玩法管线暂不抓取/解析 Play Store，链接可空或由 AI 猜测，不在本迭代实现。

## 3. 数据库变更
### 3.1 新表：`pipeline_classes`
```sql
CREATE TABLE IF NOT EXISTS pipeline_classes (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  key          TEXT NOT NULL UNIQUE,      -- 'general_news', 'legou_minigame'
  label_zh     TEXT NOT NULL,
  description  TEXT,
  enabled      INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);
```

### 3.2 新表：`pipeline_class_categories`
记录某管线类别允许使用的 `categories.key`。
```sql
CREATE TABLE IF NOT EXISTS pipeline_class_categories (
  pipeline_class_id INTEGER NOT NULL,
  category_key      TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, category_key),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id),
  FOREIGN KEY (category_key) REFERENCES categories(key)
);
CREATE INDEX IF NOT EXISTS idx_pcc_class ON pipeline_class_categories(pipeline_class_id);
```
- 示例：`general_news` 默认绑定 `game`,`tech`,`general`,`humanities`；`legou_minigame` 绑定 `game_yt`。

### 3.3 修改表：`pipelines`
新增列：
- `pipeline_class_id` INTEGER NOT NULL REFERENCES pipeline_classes(id)
- `debug_enabled` INTEGER NOT NULL DEFAULT 0
- `evaluator_key` TEXT NOT NULL DEFAULT 'news_evaluator'  -- 取值枚举：`news_evaluator` / `legou_minigame_evaluator`

说明：
- `pipelines` 仍存储：name（唯一）、enabled、description、created_at、updated_at。
- 业务逻辑根据 `pipeline_class_id` 限制来源选择和 Writer 类型。

### 3.4 修改表：`pipeline_filters`
- 删除/废弃列：`all_src`。
- 解释保留列：
  - `all_categories` INTEGER NOT NULL DEFAULT 1
  - `categories_json` TEXT
  - `include_src_json` TEXT
- 选择逻辑（三级判断）：
  1) `all_categories=1` → 使用该管线类别允许的全部 `categories`（忽略 `categories_json` / `include_src_json`）。
  2) `all_categories=0` 且 `categories_json` 中的类别 → 视为该类别下全部源。
  3) 若某源所属类别未在 `categories_json` 中，则可通过 `include_src_json` 单独补充源白名单。

### 3.5 `pipeline_writers`
- 继续使用现有字段，约束 `type` 取值：`email_news` / `feishu_news` / `feishu_legou_game`。
- `weights_json`（评分权重）在 `legou_minigame` 可用于 `rok_cod_fit` 等指标；无指标时可留空。
- `bonus_json`：保留，按源加减分。

### 3.6 AI 指标（副玩法契合度）
- 新增指标 `rok_cod_fit`（key 同名）：label_zh="ROK/COD 副玩法结合可能性"，rate_guide_zh="5-高度可行；3-有限可行；1-不合适"，default_weight=1.0，sort_order=10，active=1。
- `legou_minigame_evaluator` 必须写入该指标（1-5 分）到 `info_ai_scores`；无其他指标时，`final_score` 直接等于 `rok_cod_fit` 便于排序。
- `pipeline_class=legou_minigame` 的配置/前端仅暴露 `rok_cod_fit` 这一指标，暂不提供其他指标或权重项。
- 评审文本要求：`ai_summary` 一句话游戏介绍；`ai_comment` 一句话说明如何与 ROK/COD 做副玩法结合（不可行时需用一句话说明原因）。

### 3.6bis `info_ai_review` 结构调整（支持多评估器）
- 新增列 `evaluator_key` TEXT NOT NULL DEFAULT 'news_evaluator'。
- 主键改为 (`info_id`, `evaluator_key`)；若无法直接改主键，可迁移为新表再交换，或在现有表上新增唯一索引 (`info_id`,`evaluator_key`) 并取消原主键。
- 评估器写入时必须带 evaluator_key，避免不同评估器互相覆盖；读取时按当前管线的 evaluator_key 过滤。
- 迁移：历史数据默认补 `evaluator_key='news_evaluator'`，然后建立唯一索引/新主键。

### 3.7 新表：`pipeline_class_evaluators`
声明每个管线类别允许的评估器集合，供后端强校验。
```sql
CREATE TABLE IF NOT EXISTS pipeline_class_evaluators (
  pipeline_class_id INTEGER NOT NULL,
  evaluator_key     TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, evaluator_key),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
);
CREATE INDEX IF NOT EXISTS idx_pce_class ON pipeline_class_evaluators(pipeline_class_id);
```
- 示例：general_news → news_evaluator；legou_minigame → legou_minigame_evaluator。

### 3.8 新表：`pipeline_class_writers`
声明每个管线类别允许的 writer 类型集合，供后端校验。
```sql
CREATE TABLE IF NOT EXISTS pipeline_class_writers (
  pipeline_class_id INTEGER NOT NULL,
  writer_type       TEXT NOT NULL,
  PRIMARY KEY (pipeline_class_id, writer_type),
  FOREIGN KEY (pipeline_class_id) REFERENCES pipeline_classes(id)
);
CREATE INDEX IF NOT EXISTS idx_pcw_class ON pipeline_class_writers(pipeline_class_id);
```
- 示例：general_news → email_news, feishu_news；legou_minigame → feishu_legou_game。

### 3.9 新表：`source_runs`
记录每个源最近运行时间，用于“2 小时内不重复抓取”策略。
```sql
CREATE TABLE IF NOT EXISTS source_runs (
  source_id   INTEGER PRIMARY KEY,
  last_run_at TEXT NOT NULL, -- ISO8601
  FOREIGN KEY (source_id) REFERENCES sources(id)
);
```
- 采集成功后更新 `last_run_at`；暂不新增历史表/错误计数（后续如需告警再扩展）。

## 4. 后端逻辑改动
### 4.1 Runner / Orchestrator（积木式全链路）
- 遍历管线：
  - 跳过 `enabled=0`；`debug_enabled=1` 时整体跳过该管线（仅记录日志，不执行 collect/evaluate/write/deliver）。
  - 校验：管线类别启用；`evaluator_key` 在 `pipeline_class_evaluators` 中；`writer.type` 在 `pipeline_class_writers` 中；来源类别在 `pipeline_class_categories` 中。
- 单条管线执行顺序：
  1) **Collect**：遍历选中的源，查 `source_runs.last_run_at`，若 <2 小时则跳过，否则运行对应采集脚本，成功后更新 `last_run_at`。
  2) **Evaluate**：调用 `evaluator_key` 对符合管线条件（类别/时间窗口等）且未评估的数据做评估。未评估的判定：`info_ai_review` 不存在或缺少该评估器标记（可通过评估器 key 写入 raw_response 元数据或单独字段）。
  3) **Write**：按 writer 类型生成输出（排序=评分/bonus，限额=limit_per_category + per_source_cap）。
  4) **Deliver**：按管线 delivery 发送；`debug_enabled` 时可 dry-run。
- 调度脚本：更新现有 sh/cron，改为单次调用 orchestrator，让 orchestrator 内部完成 collect→evaluate→write→deliver，而不是仅后两步管线化。

### 4.2 Evaluators
- `news_evaluator`：沿用现有 `ai_evaluate.py`。
- `legou_minigame_evaluator`（新）：对 `source=yt.minigame`、`category=game_yt` 数据在时间窗口内评估：
  - 写入 `info_ai_review`：`ai_summary`=一句话游戏介绍；`ai_comment`=一句话描述与 ROK/COD 副玩法结合的可行性/建议；`final_score`=rok_cod_fit（1-5，或 1-10 再归一）。
  - 写入 `info_ai_scores`：metric=rok_cod_fit（必填，1-5）。
- Runner/调度脚本在执行前选对 evaluator（可通过 env/flag）。

### 4.3 Writers
- `email_news`、`feishu_news`：行为不变。
- `feishu_legou_game`（新或在现有 writer 中加分支）：
  - 读取副玩法数据，按评分降序、`limit_per_category`、`per_source_cap` 取 Top N。
  - Markdown/卡片字段：游戏名（title）、视频链接、AI 简介（ai_summary）、结合建议（ai_comment）、评分（final_score 或 `rok_cod_fit`）。
  - 与新闻 Writer 分离，避免对其他管线产生影响。

### 4.4 Deliver
- 逻辑保持，沿用 `pipeline_deliveries_email` / `pipeline_deliveries_feishu`。
- 可在 Feishu deliver 中增加 `feishu_legou_game` 卡片样式分支（标题、评分徽章、按钮）。

### 4.5 Admin CLI (`pipeline_admin.py`)
- 支持 CRUD 时写入 `pipeline_class_id`、`evaluator_key`、`debug_enabled`。
- 列表/导入导出需包含新字段。
- 校验：创建/更新时检查 `evaluator_key`、writer 是否在该 `pipeline_class` 的允许列表内。
- 克隆：新增“克隆管线”命令，复制 pipelines + filters + writers + deliveries（名称必填，默认 enabled=0, debug=0）。

## 5. 前端 Admin 改造
### 5.1 新建/编辑管线的页签
1) 基础：名称、启用、按星期推送（沿用）、`pipeline_class` 下拉、`debug` 开关。
   - 管线类别选项：综合资讯 / 乐狗副玩法。
2) 来源：树状多选（一级：全选；二级：分类；三级：源）。
   - 一级选中 → `all_categories=1`，二/三级自动选中。
   - 选中二级（分类） → 该分类全选，写入 `categories_json`，不写 `include_src_json`。
   - 若未选中一级且某分类未全选，则可在该分类下勾选具体源 → 写入 `include_src_json`（仅当该分类未在 `categories_json` 中）。
   - UI 应限制只显示当前 `pipeline_class` 支持的分类/源。
3) Writer：选择 `writer type`（受 `pipeline_class` 约束）、数量限制（`limit_per_category`、`per_source_cap`）、`bonus_json` 配置（每源加减分），可选权重 JSON。
   - 来源加权区域的“来源”字段改为下拉/搜索选择，不允许自由输入；下拉候选仅列出当前 `pipeline_class` 可用的来源（受分类过滤），避免拼写错误。
   - 若选择的管线类别为“乐狗副玩法”，指标/权重列表仅展示 `rok_cod_fit`（默认权重 1），不出现其他指标项。
4) 推送：沿用现有，邮箱/飞书配置。

### 5.2 回显与校验
- 根据 DB 填充树选中状态，确保三级逻辑一致。
- Writer 页签来源加权：来源字段为下拉+搜索（不可手输），下拉列表仅包含该 `pipeline_class` 支持的源。
- 禁止选择与 `pipeline_class` 不兼容的 Writer / 分类 / 评估器。
- 保存时将树的状态序列化为 `all_categories` / `categories_json` / `include_src_json`。

### 5.3 克隆管线
- 在管线列表添加“复制”按钮：弹窗输入新名称/是否启用，后端调用克隆接口复制相关配置（默认 debug=0, enabled=0）。

## 6. 迁移方案
1) DDL：创建 `pipeline_classes`、`pipeline_class_categories`、`pipeline_class_evaluators`、`pipeline_class_writers`、`source_runs`，为 `pipelines` 增列 `pipeline_class_id`、`debug_enabled`、`evaluator_key`，为 `pipeline_filters` 删除/忽略 `all_src`；调整/迁移 `info_ai_review` 主键以支持 `evaluator_key`。
2) 初始化数据：
   - 插入 `pipeline_classes` 两条（general_news, legou_minigame）。
  - 填充 `pipeline_class_categories`：general_news → 现有资讯类（如 game, tech, general, humanities）；legou_minigame → game_yt。
   - 填充 `pipeline_class_evaluators`：general_news → news_evaluator；legou_minigame → legou_minigame_evaluator。
   - 填充 `pipeline_class_writers`：general_news → email_news, feishu_news；legou_minigame → feishu_legou_game。
3) 迁移现有管线：
   - 全部归类为 `general_news`，`debug_enabled=0`，`evaluator_key=news_evaluator`。
   - `pipeline_filters.all_src` 若存在，按原逻辑转化：
     - 若 all_src=1 → 设置 `all_categories=1`。
     - 若 all_src=0 仅 include 源 → 将这些源所属分类加入 `include_src_json`，必要时补 `categories_json`。
4) 新增乐狗副玩法管线：按照 `docs/spec/yt-minigame-pipeline.spec.md` 的新管线配置写入 DB（可用 admin 工具）。
5) 回滚：禁用新管线，忽略新列/表；如需彻底回滚，删除新表并移除新增列（需手工）。

## 7. 测试要点
- DDL 幂等：重复运行建表/加列不报错。
- Admin UI：树状选择与 DB 值往返一致；不允许跨类选择。
- Runner：针对两类管线分别跑 collect→evaluate→write→deliver，确认过滤与分发正确；`debug_enabled` 时整条管线跳过（仅日志）。
- Evaluator 路由：general_news 走旧评估器，legou_minigame 走新评估器。
- Writer：`feishu_legou_game` 对无评分/无建议的记录做降级处理（过滤或打标）；Top N/每源限额生效。
- 兼容性：旧管线配置不受影响，现有 Feishu/Email 投递正常。

## 8. 开放问题
- 未评估判断：需要实现“按 evaluator_key 判定未评估”，可能在 `info_ai_review` 增加 evaluator_key 字段后，增加落库前去重/覆盖策略。

## 9. 脚本（scripts/*.sh）调整
- 将现有分步调用（先 collect，再 ai_evaluate，再 pipeline_runner）的 sh 改为统一调用 orchestrator 的单入口（示例：`python write-deliver-pipeline/pipeline_runner.py --all --orchestrate` 或新脚本 `scripts/run-pipelines.sh` 内部按新顺序执行）。
- 确保 sh 中尊重 `debug_enabled`（跳过管线）与管线按类别的源/评估器/Writer 校验；按需传递 ENV（如 evaluator 选择、回溯小时、日志路径）。
- 
