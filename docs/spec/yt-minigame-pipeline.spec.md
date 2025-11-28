# YouTube Minigame Scout Pipeline SPEC

面向“新游戏副玩法推荐”场景的管线设计，复用现有 SQLite + pipeline runner + Feishu 投递能力，新增 YouTube 采集与阿里云 AI 评估。目标是定期把高契合度的小游戏（适配《万国觉醒》/《万龙觉醒》副玩法）推送到指定飞书群。

## 1. 目标与非目标
- 目标
  - 定义一条独立管线：采集指定 YouTube 频道的最新视频 → 阿里云模型生成简介与结合建议 → 选 Top N 推送飞书。
  - 与现有新闻管线并行运行，共享 DB 与 runner，不影响原有数据。
  - 允许后续调整频道列表、时间窗口、Top N、评分权重，而无需改代码（通过 DB 配置）。
- 非目标
  - 不做前端用户界面（仅后端/脚本链路）。
  - 不实现 Play Store 爬取；Play 链接可由模型推测或留空。

## 2. 数据与流程概览
```
YouTube 频道 (RSS/ API) → collector 写入 info(category=game_yt, source=yt.minigame)
                           → evaluator_yt_aliyun 读取 info，生成简介+结合建议+得分，写入 info_ai_review (+ 可选 metrics)
                           → pipeline_runner (pipeline=yt-minigame-scout) 调 writer 生成 Markdown
                           → feishu_deliver 发送到配置的群聊
```
- 调度：延续现有顺序（collect → evaluate → pipeline_runner），可挂到已有 sh/cron。

## 3. 数据库变更
### 3.1 新增分类与来源
- `categories` 增加一条：
  - `key=game_yt`, `label_zh=YouTube小游戏`, `enabled=1`
- `sources` 增加一条：
  - `key=yt.minigame`, `label_zh=YouTube Minigame Scout`, `category_key=game_yt`, `script_path=news-collector/collector/scraping/game/yt.minigame.py`, `enabled=1`
- `source_address`：为 `yt.minigame` 插入频道列表（channel_id / feed URL），便于后期运营调整。

### 3.2 AI 评估指标（长表评分）
- 在 `ai_metrics` 插入：
  - `rok_cod_fit`：label_zh="ROK/COD 副玩法契合度"，rate_guide_zh="5-高度适配；3-中等；1-不适配"，default_weight=1.0，sort_order 10（必备，用于评分/排序，与 `final_score` 保持一致）
  - `novelty_mini`（可选）：小游戏创意新颖度，default_weight 0.2
- Evaluator 输出 `rok_cod_fit` 的 1-5 分，写入 `info_ai_scores`；可选输出其他指标。

### 3.3 新管线配置（示例）
- `pipelines`: `name=yt-minigame-scout`, `enabled=1`
- `pipeline_filters`: `all_categories=0`, `categories_json=["game_yt"]`, `all_src=0`, `include_src_json=["yt.minigame"]`
- `pipeline_writers`:
  - `type=feishu_md_minigame`（如复用现有 writer 则用 `feishu_md` 并在代码中按 category 分支）
  - `hours=48`（采样窗口）
  - `limit_per_category="5"`（Top 5）
  - `per_source_cap=3`
  - `weights_json={"rok_cod_fit":1.0}`（如未用 metrics，可忽略）
- `pipeline_deliveries_feishu`: 配置 `app_id/app_secret`、`to_all_chat` 或 `chat_id`、`title_tpl="${date_zh} 新游戏副玩法推荐"`

## 4. 组件设计
### 4.1 Collector：`news-collector/collector/scraping/game/yt.minigame.py`
- 输入：`source_address` 里的频道列表；时间窗口参数（默认 48h，可通过 CLI 覆盖）；可用 RSS `https://www.youtube.com/feeds/videos.xml?channel_id=...` 或官方 API。
- 输出写入 `info`：
  - `source`=`yt.minigame`
  - `category`=`game_yt`
  - `publish`=视频发布时间（ISO8601 UTC）
  - `title`=视频标题
  - `link`=视频 URL
  - `detail`=视频描述（可选；若抓取不到填空）
- 特性：去重（依赖 `UNIQUE(link)`），失败记录日志不中断全局 collector。

### 4.2 Evaluator（阿里云）：`news-collector/evaluator/ai_evaluate_yt_aliyun.py`
- 作用：读取 `info` 中 `source=yt.minigame` 且在窗口内的记录，调用阿里云 LLM，生成：
  - 简介：一句话中文介绍
  - 结合建议：如何与《万国觉醒》/《万龙觉醒》做副玩法结合
  - 评分：契合度 1-5（或 1-10，再归一）
  - 可选：Play Store 关键词/链接猜测（字段放在 `ai_comment` 或 `ai_summary_long` 里）
- 写入：
  - `info_ai_review`: `ai_summary`=简介，`ai_comment`=结合建议，`final_score`=契合度（或 0），`raw_response`=原始 JSON
  - 若使用指标：写 `info_ai_scores(info_id, metric_id=rok_cod_fit, score=1-5)`
- Prompt 要点：提供视频标题、描述、期望输出 JSON 格式；强调只用公开信息，不虚构发布时间。
- 并行与重试：可参考现有 evaluator 模式（批量拉取、逐条调用、失败重试 N 次、跳过已评估）。
- 配置：读取 `ALIYUN_API_KEY`、`ALIYUN_MODEL`（如 qwen-max）等环境变量。

### 4.3 Writer
新建专用 writer `writer/feishu_minigame_writer.py`
- 按同样逻辑生成 Markdown，`pipeline_runner` 依据 `type=feishu_md_minigame` 分发调用。

### 4.4 Deliver
- 复用 `deliver/feishu_deliver.py`：
  - 读取 DB `pipeline_deliveries_feishu` 配置的 bot 凭证和群。
  - 输入：writer 产出的 Markdown 路径，支持 `--as-card` 生成卡片。
  - 如需专用卡片样式，可在 deliver 脚本内增加一个模板分支（标题、副玩法评分徽章、按钮：YouTube 链接）。

### 4.5 Runner 集成
- `pipeline_runner.py` 新增对 `type=feishu_md_minigame` 的分发（若采用新 writer），或沿用默认 writer。
- 调度示例：
  - `python collector/collect_to_sqlite.py --source yt.minigame --hours 48`
  - `python evaluator/ai_evaluate_yt_aliyun.py --hours 48 --limit 200`
  - `PIPELINE_ID=<id> python write-deliver-pipeline/pipeline_runner.py --name yt-minigame-scout`

## 5. 配置与环境
- 环境变量：
  - `ALIYUN_API_KEY`, `ALIYUN_MODEL`（评估）
  - `FEISHU_APP_ID`, `FEISHU_APP_SECRET`（可由 deliver 脚本覆盖 DB 值）
  - `YOUTUBE_API_KEY`（如走官方 API；RSS 模式可不需要）
- 频道列表：优先从 DB `source_address` 读取；备选从 YAML/JSON 常量。
- 时间窗口、Top N：由 `pipeline_writers.hours/limit_per_category/per_source_cap` 控制。

## 6. 迁移与回滚
- 创建分类/来源/可选指标的 SQL 可以作为一次性迁移脚本（幂等 `INSERT OR IGNORE`）。
- 新增 writer/evaluator 代码不改动现有逻辑；默认禁用管线（`enabled=0`），验证后开启。
- 回滚：禁用管线（`pipelines.enabled=0`），停止调度；代码可保留。

## 7. 测试计划
- 单元/集成
  - Collector：对单频道 RSS/XML 解析测试；确保写入 `info` 去重。
  - Evaluator：对固定输入做 mock 阿里云响应，验证 JSON 解析与 DB 写入；空/失败重试路径。
  - Writer：用假数据检查排序/限额与 Markdown 结构。
  - Deliver：用沙箱 bot、测试群验证卡片渲染。
- 端到端：在测试数据库跑一轮（小窗口/少量频道），确认 pipeline_runner 输出文件与飞书推送成功。

## 8. 风险与缓解
- YouTube 访问受限/CORS：服务器侧抓取（避免浏览器），失败时记录并重试。
- 阿里云模型波动：增加重试与超时，必要时缓存成功结果，避免重复计费。
- 评分口径：需对 `rok_cod_fit` 的评分标准做提示，避免模型输出漂移。
- 凭证安全：所有 Key 只存环境变量；DB 中的 Feishu 凭证限制权限，并避免前端暴露。

## 9. 待定/开放问题
- 是否需要 Play Store 真正抓取补充信息？（若需要，考虑后端代理 + 解析，另行扩展字段）
- 是否要将 AI 结果暴露到 Web 前端查看/复审？当前方案仅飞书推送。
