% 管线配置化与模块化（写作/推送定制版，数据库驱动）

> 更新要点：采集（Collect）与 AI 评估（Evaluate）采用统一全局流程，仅在“撰写（Write）”与“推送（Deliver）”两步进行个性化配置与执行。

## 1. 目标与边界
- 全局统一：Collect 与 AI Evaluate 按既定策略每日运行一次（或固定频率），把最新数据与评分写入 SQLite。
- 管线多样：下游“撰写 + 推送”按订阅人/场景定制，支持不同过滤、权重/加成、投递目标。
- 配置优先：通过 YAML/JSON 描述多条管线（仅 write/deliver），避免脚本分叉与膨胀。
- 复用能力：沿用现有 `info_writer.py`、`wenhao_writer.py`、`feishu_writer.py` 与投递工具。

## 2. 总体结构
- Ingest（统一阶段）
  - 由简单 sh 脚本依次触发：`collect_to_sqlite.py` → `ai_evaluate.py`。
  - 参数固定或少量全局参数（如 `--hours 40`、`--limit 400`）。
  - 输出：统一 SQLite `data/info.db`（含 `info` 与 `info_ai_review`）。
- Pipeline Runner（仅 Write/Deliver）
  - 从数据库读取多条“写作/推送”管线配置（见第 3 节）。
  - 对每条管线：按配置过滤 DB → 选择 writer → 生成文件 → 选择 deliver 方式推送。
  - Runner 仅负责计算输出路径并设置 `PIPELINE_ID` 环境变量，具体参数（hours/categories/weights/bonus/收件人/Feishu 目标与凭证）由 writer/deliver 脚本按 `PIPELINE_ID` 默认自取，无需额外 flag。
  - 顺序执行各管线；单条失败不影响其它管线。

## 3. 数据库模型（仅 write/deliver）
为降低脚本分叉与配置分散，所有“撰写/推送”管线改为存储在 SQLite 中。推荐表结构如下（字段可按需要裁剪或合并）：

```sql
-- 管线定义
CREATE TABLE IF NOT EXISTS pipelines (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  name         TEXT NOT NULL UNIQUE,
  enabled      INTEGER NOT NULL DEFAULT 1,
  description  TEXT,
  created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

-- 过滤条件（精简：按需选择“全部”或仅使用白名单 JSON）
CREATE TABLE IF NOT EXISTS pipeline_filters (
  pipeline_id      INTEGER NOT NULL,
  all_categories   INTEGER NOT NULL DEFAULT 1,  -- 1=全部类别；0=只取 categories_json
  categories_json  TEXT,                        -- ["game","tech"]（all_categories=0 时生效）
  all_src          INTEGER NOT NULL DEFAULT 1,  -- 1=全部来源；0=只取 include_src_json
  include_src_json TEXT,                        -- ["openai.research", ...]（all_src=0 时生效）
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

-- Writer 配置
CREATE TABLE IF NOT EXISTS pipeline_writers (
  pipeline_id     INTEGER NOT NULL,
  type            TEXT NOT NULL,      -- info_html | wenhao_html | feishu_md
  hours           INTEGER NOT NULL,   -- 仅 writer 过滤窗口
  weights_json    TEXT,               -- {"timeliness":0.1,...}
  bonus_json      TEXT,               -- {"openai.research":2,"deepmind":2}
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

-- 投递配置（按渠道拆分，单管线单投递）
-- Email 投递
CREATE TABLE IF NOT EXISTS pipeline_deliveries_email (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  email        TEXT NOT NULL,         -- 单一收件人邮箱地址
  subject_tpl  TEXT NOT NULL,         -- 标题模板，例如 "${date_zh} 整合"
  deliver_type TEXT NOT NULL DEFAULT 'email', -- 投递类型：email | feishu（此表固定为 email）
  UNIQUE(pipeline_id),                -- 每条管线仅一条 email 投递
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

-- Feishu 投递（统一 feishu_card）
CREATE TABLE IF NOT EXISTS pipeline_deliveries_feishu (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  app_id       TEXT NOT NULL,         -- 机器人 App ID（可被环境变量覆盖）
  app_secret   TEXT NOT NULL,         -- 机器人 App Secret（敏感信息）
  to_all_chat  INTEGER NOT NULL DEFAULT 0,  -- 1=推送所有所在群；0=仅推送到指定 chat_id
  chat_id      TEXT,                  -- 目标群聊 ID（to_all_chat=0 时必填）
  title_tpl    TEXT,                  -- 标题模板（card/post 可用）
  to_all       INTEGER DEFAULT 0,     -- 是否全员通知（card 可用）
  UNIQUE(pipeline_id),                -- 每条管线仅一条 feishu 投递
  FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);

-- 运行记录（可选，用于审计与排障）
CREATE TABLE IF NOT EXISTS pipeline_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  pipeline_id  INTEGER NOT NULL,
  started_at   TEXT DEFAULT CURRENT_TIMESTAMP,
  finished_at  TEXT,
  status       TEXT,                  -- success | failed | partial
  summary      TEXT,
 FOREIGN KEY (pipeline_id) REFERENCES pipelines(id)
);
```

### 投递约束
- 单管线单投递：同一 `pipeline_id` 仅允许在 email 表或 feishu 表二选一存在一条记录；如两表同时存在，runner 视为配置错误并拒绝执行。

### 输出路径规范
- 目录：每条管线的输出互相隔离，统一放在 `data/output/pipeline-${pipeline_id}` 下（固定，不从 DB 配置覆盖）。
- 文件名：固定为 `${ts}.html`（email）或 `${ts}.md`（feishu），其中 `ts=YYYYMMDD-HHMMSS`。
- 生成：runner 负责创建目录与计算最终路径，并以 `--output` 传入对应 writer。
- 读取：mail 读取 `.html` 文件作为正文内联；feishu 读取 `.md` 文件并渲染为卡片（如提供 `content_json` 则以其为准）。

常见字段与模板示意：
- email（pipeline_deliveries_email）：
  - `email`: `a@b.com`
  - `subject_tpl`: `${date_zh} 整合`
- feishu（pipeline_deliveries_feishu，统一 feishu_card）：
  - 公共鉴权：`app_id`, `app_secret`
  - 广播范围：`to_all_chat = 1`（推送所有所在群）；或 `to_all_chat = 0` 且提供 `chat_id`
  - 卡片参数：`title_tpl`、`to_all`；可选 `content_json`（如为空，runner 从标准目录 `${runner.output_dir}/${ts}.md` 读取 Markdown 并填充卡片）

变量模板由 runner 注入，支持：
- `ts`（时间戳，格式 `YYYYMMDD-HHMMSS`）
- `date_zh`（中文日期）
- `runner.output_dir`（实际输出目录，默认 `data/output/pipeline-${pipeline_id}`）
- `runner.output_file`（实际输出文件路径，email 为 `.html`，feishu 为 `.md`）

## 4. 组件职责
- Ingest（统一执行）
  - Collect：运行全部 scraper，明细回填，统一时间校验与日志。
  - AI Evaluate：固定窗口与上限，对最新内容打分并入库。
- Writer（按管线）
  - 输入：SQLite + 过滤（类别/来源）+ 权重/来源加成 + 小时窗口。
  - 输出：文件（HTML/Markdown）。
  - 输出路径：由 runner 统一计算为 `data/output/pipeline-${pipeline_id}/${ts}.{html|md}`，并通过 `--output` 传入 writer；各表不再存储路径模板。
  - DB 优先：当存在 `PIPELINE_ID` 时，writer 默认从 DB 读取 `pipeline_writers` 与 `pipeline_filters`（hours/categories/weights/bonus）；仍保留 CLI 覆盖能力用于单次调试。
- Deliver（按管线）
  - 渠道：email 与 feishu 分表管理；Feishu 统一使用 feishu_card。
  - DB 优先：当存在 `PIPELINE_ID` 时，`mail_deliver.py` 默认自 DB 读取收件人与标题模板；`feishu_deliver.py` 默认自 DB 读取 App 凭证、发送范围（to_all_chat/chat_id）与标题模板。Runner 仅传入待发送文件路径（`--html` 或 `--file`）与展示形式（如 `--as-card`）。
  - 仍支持通过 CLI 与环境变量手动覆盖，便于独立调试。

## 5. 过滤与排序策略
- 过滤优先放在 writer 层（避免重复评估）。
- 类别/来源采用“全选（all_*=1）或白名单 JSON（all_*=0）”两段式控制，避免复杂条件导致的误配置。
- 排序：按 writer 的综合得分（动态权重 + 来源加成）与时间回退；支持 per-source cap/top-k。

## 6. 可观测与可靠性
- 日志：`logs/ingest-YYYYMMDD.log`（统一阶段）与 `logs/pipelines-YYYYMMDD.log`（写作/推送阶段）。
- 失败策略：每个 deliver 重试 N 次（指数退避）；单管线失败不影响其它管线。
 - 并发：当前不启用并发；Runner 依次执行。DB 只读，避免与 Ingest 写冲突。

## 7. CLI 与调度
- Ingest：由简单的 sh 脚本串联触发（先运行采集与评估，再执行各 pipeline 的写作/投递）。
- Pipeline Runner（DB 驱动）：
  - `pipeline_admin.py`：DB 中的管线 CRUD（增删改查、启停、克隆、导入/导出 JSON）。
  - `pipeline_runner.py --name <pipeline>`：按名称执行单条管线（仅 write/deliver）。
  - `pipeline_runner.py --all`：按顺序执行所有启用管线。
- 调度方式：暂不使用 cron/systemd，先用仓库内 sh 脚本管理（如 `scripts/auto-do-scripts.sh`、`scripts/auto-do-scripts-930.sh`），可通过 `nohup`/`tmux`/CI 触发。

## 8. 渐进迁移
1) 添加上述表结构；提供 `pipeline_admin.py` 的最小命令：`init`, `add`, `list`, `enable/disable`。
2) 从当前实际用到的两条脚本（default/wenhao）写入 DB（提供一份 `INSERT` 示例或 `admin import`）。
3) 实现 `pipeline_runner.py` 读取 DB、调用现有 writer/deliver（先用子进程），验证稳定性。
4) 后续可把 runner 与 writer 的调用改为内部函数，提高性能与日志统一性。

## 9. 后续扩展
- 模板：邮件/卡片标题、文件命名支持占位符与 Jinja2 模板。
- 权限：对不同管线做最少可见（不同邮箱/群可读范围）。
- 指标：每条管线展示“入选条数/评分分布/来源覆盖率”。

## 10. 结论
- 把“差异化”收敛到 write/deliver 层，维持 ingest 的统一，能显著降低复杂度。
- 配置可读、可管理、可审计，适合持续扩展订阅人群与场景。
