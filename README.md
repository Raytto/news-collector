# News Collector / 情报聚合与投递系统

可配置的资讯采集、AI 评价与投递流水线：多源抓取 → SQLite 去重入库 → 大模型打分与摘要 → 按 DB 管线生成飞书 Markdown / 邮件 HTML 并投递。配套 FastAPI + React 管理台（邮箱验证码登录、管线/来源/指标/用户管理、手动推送、退订页）。

## 数据流与模块
- 采集：`news-collector/collector/collect_to_sqlite.py` 按数据库 `sources.enabled=1` 执行脚本（首次运行自动从 `collector/scraping` 扫描并注册 `SOURCE`/`CATEGORY`）；支持明细回填、并发与限速控制，写入 `data/info.db`。
- AI 评价：`news-collector/evaluator/ai_evaluate.py` 为资讯多维度打分与摘要，支持按 `--pipeline-id` 读取 `evaluators/ai_metrics` 配置。
- 写作：`writer/email_writer.py`（HTML digest）、`writer/feishu_writer.py`（飞书 Markdown）、`writer/feishu_legou_game_writer.py`（小游戏场景）；按管线配置的权重/加成/配额生成文件，输出到 `data/output/pipeline-<id>/<ts>.{html,md}`。
- 投递：`deliver/mail_deliver.py`（Resend / SMTP / sendmail，自动附管理/退订链接）、`deliver/feishu_deliver.py`（卡片）。
- 管线：`write-deliver-pipeline/pipeline_admin.py` 管理 SQLite 管线表（含管线类别/评估器/指标权重），`pipeline_runner.py` 负责按 DB 筛选来源、2 小时内自动跳过重复采集、调用 AI 评价、写作并投递。
- 管理台：`backend/` FastAPI 提供邮箱验证码登录、用户/管线/来源/类别/指标/评估器/管线类别 CRUD、手动推送（冷却与日额度）、退订；`frontend/` React + Ant Design 管理界面，默认通过 `/api` 反代。

## 目录
- `news-collector/collector/scraping/` 采集脚本（`game/`、`tech/`、`humanities/` 等）。
- `news-collector/evaluator/` AI 评价脚本。
- `news-collector/writer/`/`deliver/` 写作与投递工具。
- `news-collector/write-deliver-pipeline/` 管线表、运行器与星期限制工具。
- `backend/` FastAPI；`frontend/` 管理前端；`scripts/` 自动化/启动/迁移脚本。
- `docs/` 运行手册与规格；`slg-scout-&-analyst/` 为独立的 AI Studio 前端示例。

## 环境准备
1) Python 3.11  
`python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`  
后台开发可额外安装 `pip install -r backend/requirements.txt`。

2) 前端（可选）  
需 Node.js 18+（脚本会自动拉起 v20 LTS）；`cd frontend && npm install`。

3) 环境变量  
复制并修改 `environment-template.yml` 或 `.env`，切勿提交真实密钥。

## 核心配置（环境变量）
- AI：`AI_API_BASE_URL`、`AI_API_MODEL`、`AI_API_KEY`、`AI_API_TIMEOUT`、`AI_REQUEST_INTERVAL`。
- 飞书：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`、`FEISHU_DEFAULT_CHAT_ID`（可选）、`FEISHU_API_BASE`。
- 邮件/验证码：`RESEND_API_KEY`、`RESEND_FROM`，或 `SMTP_HOST/PORT/USER/PASS/SMTP_USE_SSL/SMTP_USE_TLS`；`MAIL_FROM`、`MAIL_SUBJECT_PREFIX`。
- 认证与手动推送：`AUTH_SESSION_DAYS`、`AUTH_CODE_TTL_MINUTES`、`AUTH_CODE_LENGTH`、`AUTH_CODE_COOLDOWN_SECONDS`、`AUTH_HOURLY_PER_EMAIL`、`AUTH_DAILY_PER_EMAIL`、`AUTH_HOURLY_PER_IP`、`AUTH_COOKIE_SECURE`、`MANUAL_PUSH_COOLDOWN_SECONDS`、`MANUAL_PUSH_DAILY_LIMIT`。
- 采集并发：`COLLECTOR_SOURCE_CONCURRENCY`、`COLLECTOR_PER_SOURCE_CONCURRENCY`、`COLLECTOR_GLOBAL_HTTP_CONCURRENCY`、`COLLECTOR_PER_HOST_MIN_INTERVAL_MS`、`COLLECTOR_TIMEOUT_CONNECT/READ`、`COLLECTOR_RETRY_MAX`、`COLLECTOR_RETRY_BACKOFF_BASE`、`COLLECTOR_DISABLE_CONCURRENCY`。
- 其他：`BACKEND_PORT`（默认 8000）、`FRONTEND_PORT`（默认 5180 dev）、`FRONTEND_BASE_URL`（邮件页脚管理/退订链接）、`FRONTEND_SITE_ICON`。

## 数据库初始化与迁移
- 采集脚本首次运行会创建 `info/categories/sources` 等表并自动注册 scraping 目录；后续新增来源需在 DB `sources` 表维护（可通过后台“信息源”页面或 SQL），否则不会执行。
- 管线表初始化：`python news-collector/write-deliver-pipeline/pipeline_admin.py init`。示例：`python news-collector/write-deliver-pipeline/pipeline_admin.py seed` 或 `import --input data/pipelines/all_settings.json --mode replace`。
- 迁移：旧库请按需运行 `scripts/migrations/202510_ai_metrics_refactor.py --db data/info.db`、`scripts/migrations/pipeline_refactor.sql`、`scripts/migrations/202512_remove_unsubscribe_tables.py` 等（均幂等）。
- 后台注册新用户时会自动为其创建 2 条默认邮件管线（按周几+时长+默认权重）。

## 采集与 AI 评价
- 采集：`python news-collector/collector/collect_to_sqlite.py [--sources game,tech]`，去重写入 `data/info.db`；支持 `backfill_details.py --limit 200`、`backfill_publish.py` 补充详情/时间。
- AI 评价：`python news-collector/evaluator/ai_evaluate.py --hours 24 --limit 50 [--category game --source openai.research --pipeline-id 3]`，写入 `ai_metrics`、`info_ai_scores`、`info_ai_review`（含长摘要/关键词）。

## 写作与投递管线
- 管线管理：`pipeline_admin.py list/enable/disable/clone/export/import`，支持管线类别（`pipeline_classes`）限制允许的类别/评估器/Writer。
- 运行：`python news-collector/write-deliver-pipeline/pipeline_runner.py --all` 或 `--name/--id`，支持 `--debug-only`（只跑 `debug_enabled=1`）、`--ignore-weekday` 或设置 `FORCE_RUN=1` 忽略周几限制。Runner 会筛选来源、2 小时内跳过重复采集，并按 `pipeline_writers` / `pipeline_deliveries_*` 自动写作与投递。
- Writer/Delivery 细节：`PIPELINE_ID` 由 runner 注入，Writer 自动读取 `weights_json` / `pipeline_writer_metric_weights`、`bonus_json`、`limit_per_category`、`per_source_cap`；邮件投递支持 `MAIL_PLAIN_ONLY=1` 纯文本、副本落盘 `MAIL_DUMP_MSG=path.eml`；邮件页脚依赖 `FRONTEND_BASE_URL` 生成管理/退订链接。

## 自动化脚本
- 单次全链路：`scripts/auto-pipelines-once.sh`（采集→评价→全部管线写作+投递，日志写入 `log/<ts>-auto-once-log.txt`）。
- 每日 09:30 循环：`scripts/auto-pipelines-930.sh`（包含过期输出清理）。
- 仅采集+评价：`scripts/collect-evalue.sh`。

## 后台与前端管理
- 后台：`scripts/start-backend.sh`（默认 8000，自动加载 `.env`，邮箱验证码登录/注册；Resend→SMTP→sendmail 逐级兜底）。功能：用户与权限、管线 CRUD、来源/类别/评估器/指标管理、手动推送（限流）、查找飞书群列表、退订接口。
- 前端：`scripts/start-frontend-dev.sh` 本地调试（Vite dev server，默认 5180，通过 `/api` 代理后台）；`scripts/start-frontend.sh` 构建并部署到 `/var/www/news-collector-jp`（可用 `FRONTEND_DEPLOY_DIR` 覆盖）。生产环境 Nginx 静态目录同上，刷新缓存即可。

## 参考文档
- 管线与运行器：`docs/pipelines-guide.md`
- 新增来源：`docs/add-new-souce.md`
- 新增 Writer：`docs/add-new-writer.md`
- 邮件 SMTP：`docs/config/email-smtp.md`
- 并发/节流：`docs/report/collector-concurrency.md`
- Nginx/部署：`docs/nginx-guide.md`

如需新增采集脚本或扩展管线，请遵循现有命名与函数风格，并在 `docs/` 中补充说明。
