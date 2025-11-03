# News Collector / 情报聚合与投递系统

一个可配置的资讯采集、AI 评价与投递系统：从多源抓取 RSS/网页，去重入库到 SQLite，经大模型打分与摘要后，按管线配置生成飞书 Markdown 或邮件 HTML 摘要，并通过飞书开放平台或 SMTP 投递。

## 功能概览
- 采集：支持游戏/科技/人文等多站点脚本，统一写入 `data/info.db`。
- 评价：调用大模型为文章多维度打分并生成摘要（表：`ai_metrics`、`info_ai_scores`、`info_ai_review`）。
- 写作：根据打分与权重生成飞书 Markdown 或邮件 HTML 摘要。
- 投递：飞书卡片/富文本或邮件（本地 sendmail / SMTP）。
- 管线：基于 SQLite 的可视/可导入配置（分类/来源筛选、时间窗、权重、配额、投递目标）。
- 自动化：一键脚本与可选的后台 + 前端管理界面。

## 目录结构
- `news-collector/collector/scraping/` 各来源采集脚本（`game/`、`tech/`、`humanities/`）。
- `news-collector/collector/collect_to_sqlite.py` 采集入口，去重写入 `data/info.db`。
- `news-collector/collector/backfill_details.py` 按需回填文章详情（调用脚本内 `fetch_article_detail`）。
- `news-collector/evaluator/ai_evaluate.py` 大模型打分/摘要，产出 AI 相关表。
- `news-collector/writer/feishu_writer.py` 生成飞书 Markdown 摘要。
- `news-collector/writer/email_writer.py` 生成邮件 HTML 摘要。
- `news-collector/deliver/feishu_deliver.py` 飞书消息/卡片发送。
- `news-collector/deliver/mail_deliver.py` 邮件发送（sendmail/SMTP）。
- `news-collector/write-deliver-pipeline/` 管线表管理与执行器：
  - `pipeline_admin.py` 初始化/导入/导出/列出/启禁用管线。
  - `pipeline_runner.py` 顺序执行启用的管线（写作→投递）。
- `data/` SQLite 数据库、输出目录与示例管线 JSON。
- `docs/` 运行手册与规格说明（如 `docs/pipelines-guide.md`、`docs/config/email-smtp.md`）。
- `scripts/` 自动化与启动脚本（如 `auto-pipelines-930.sh`、`start-backend.sh`）。
- `backend/` FastAPI 管理 API；`frontend/` 前端（可选）。

## 快速开始（本地）
1) 准备 Python 3.11 环境
- `python -m venv .venv && source .venv/bin/activate`
- `pip install -r requirements.txt`
- 若需后台 API：`pip install -r backend/requirements.txt`

2) 配置必要环境变量（最少需要 AI 与飞书/邮件其一）
- AI 评价：`AI_API_BASE_URL`、`AI_API_MODEL`、`AI_API_KEY`
- 飞书：`FEISHU_APP_ID`、`FEISHU_APP_SECRET`（可选 `FEISHU_DEFAULT_CHAT_ID`）
- 邮件（可选）：`SMTP_HOST`、`SMTP_PORT`、`SMTP_USER`、`SMTP_PASS`、`SMTP_USE_SSL/TLS`、`MAIL_FROM`
- 建议复制并修改 `environment-template.yml` 或使用 `.env`（参见 `docs/config/email-smtp.md`）。请勿将真实密钥提交到版本库。

3) 初始化管线表（一次）
- `python news-collector/write-deliver-pipeline/pipeline_admin.py init`
- 可导入示例管线：
  - `python news-collector/write-deliver-pipeline/pipeline_admin.py import --input data/pipelines/all_settings.json --mode replace`

4) 采集 → 评价 → 写作/投递
- 采集：`python news-collector/collector/collect_to_sqlite.py`
- 可选回填详情：`python news-collector/collector/backfill_details.py --limit 200`
- AI 评价（示例处理最近 24 小时 50 条）：
  - `python news-collector/evaluator/ai_evaluate.py --limit 50 --hours 24`
- 执行全部启用管线（生成并投递）：
  - `python news-collector/write-deliver-pipeline/pipeline_runner.py --all`

5) 一键运行脚本（可选）
- 单次：`scripts/auto-pipelines-once.sh`（采集→评价→按管线投递）
- 每日 09:30 循环：`scripts/auto-pipelines-930.sh`（建议放到 tmux/screen 或 systemd）

## 后台与前端（可选）
- 启动后台 API：`scripts/start-backend.sh`（默认端口 8000；读取 `.env`）
- 启动前端：`scripts/start-frontend.sh`
- 管线表结构、运行器与完整操作命令详见 `docs/pipelines-guide.md`。

## 关键环境变量
- 采集限速与并发（可按站点限流、全局并发、重试等）：
  - `COLLECTOR_SOURCE_CONCURRENCY`、`COLLECTOR_PER_SOURCE_CONCURRENCY`、`COLLECTOR_GLOBAL_HTTP_CONCURRENCY`
  - `COLLECTOR_PER_HOST_MIN_INTERVAL_MS`、`COLLECTOR_TIMEOUT_CONNECT/READ`、`COLLECTOR_RETRY_MAX`、`COLLECTOR_RETRY_BACKOFF_BASE`
- 写作/投递细节：
  - `PIPELINE_ID`（由运行器自动传入，写作/投递脚本据此自取 DB 配置）
  - `MAIL_PLAIN_ONLY=1`（仅发送 text/plain，且旁写 `.txt` 副本）
  - `MAIL_DUMP_MSG=path.eml`（发送前导出 RFC822 邮件包）

## 安全与合规
- 不要在仓库中提交真实 `API_KEY`、`FEISHU_APP_SECRET` 等敏感信息。
- 采集遵守各站点 robots.txt，合理设置并发/限速，避免触发封禁。
- 投递前请确认邮箱/飞书应用权限与速率限制，注意群发频率。

## 参考文档
- 管线与运行器：`docs/pipelines-guide.md`
- 邮件 SMTP 配置：`docs/config/email-smtp.md`
- 采集并发/节流：`docs/report/collector-concurrency.md`
- Nginx/部署建议：`docs/nginx-guide.md`

---
如需新增采集脚本或扩展管线，请参考现有模块命名与函数风格，保持纯函数与可复用性，并在 `docs/` 中补充相应说明或样例。
