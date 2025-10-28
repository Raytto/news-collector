实现一个“飞书消息写入脚本”，用于生成适配飞书发送的精简 Markdown 文本（更适合 `--as-card` 发送），按类别输出最近资讯的“AI 推荐榜”。脚本位于 `news-collector/manager/feishu_writer.py`。

## 1. 目标与产物
- 从 SQLite（`data/info.db`）读取最近 N 小时的资讯，并按“AI 推荐度”挑选每个大类的 Top K。
- 生成一段“类 Markdown 文本”（lark_md 友好），供 `feishu_bot_today.py` 读取并通过 `--as-card` 或 `--as-post` 发送到群聊。
- 默认输出文件：`data/feishu-msg/YYYYMMDD-feishu-msg.md`（注意 feishu 的拼写）。

## 2. 输入数据与筛选逻辑
- 数据表：
  - `info`：id, source, category, publish, title, link
  - `info_ai_review`：各维度分（timeliness, game/mobile_game/ai/tech_relevance, quality, insight, depth, novelty）、comment、summary
- 时间窗口：默认近 24 小时，可通过 `--hours` 指定。
- 类别范围：默认 `game`、`tech`，可通过 `--categories game,tech` 覆盖。
- 去重：按 link 去重；同一条不应重复出现在不同分类中（优先保留其自身分类）。
- 分数：按 docs/prompt/ai-evaluation-spec.md 中的维度与默认权重计算“当前展示用的总分”，与 `info_writer.py` 保持一致。
  - 无评分的条目：按 0 处理（可通过 `--min-score` 过滤掉低分/无分项）。
  - 权重覆盖：支持 `--weights '{"timeliness":0.2,...}'` 或读取 `AI_SCORE_WEIGHTS` 环境变量。

## 3. 排序与选取规则
- 每个分类独立排序：
  1) 总分（降序）
  2) 发布时间（降序）
- 选择 Top K（默认 10，可通过 `--limit-per-cat` 覆盖）。不足 K 条则按现有数量输出。

## 4. 输出格式（lark_md 友好）
- 顶层结构：
  ```
  **GAME**
  1. (AI推荐:4.8)(sensortower) [Sensor Tower宣布获新韩证券战略投资，将用于推进生成式AI数据产品及游戏、Web端数字洞察服务。](https://sensortower.com/blog/....)
  2. ...

  **TECH**
  1. (AI推荐:4.2)(qbitai-news) [美团视频生成模型来了！一出手就是开源SOTA](https://www.qbitai.com/....)
  ```
- 要求：
  - 标题用加粗行（`**GAME**`、`**TECH**`），不要用 `#`，以提升在卡片 `markdown` 元素中的兼容性。
  - 每条一行，前缀为“序号 + 评分 + 来源”，超链接文本为标题，指向原文 `link`。
  - 评分保留两位小数，形如 `AI推荐:4.80`。
  - 标题建议截断至 80~100 字符，避免超长。
  - 处理 HTML 实体与多余空白；确保链接为绝对 URL。

## 5. CLI 设计
- 位置：`news-collector/manager/feishu_writer.py`
- 参数：
  - `--hours 24`：时间窗口，默认 24。
  - `--limit-per-cat 10`：每类最多条目数。
  - `--categories game,tech`：逗号分隔的分类列表。
  - `--min-score 0`：过滤低于阈值的条目（默认 0 不过滤）。
  - `--weights '{"timeliness":0.20,"game_relevance":0.25,...}'`：覆盖默认权重。
  - `--output data/feishu-msg/YYYYMMDD-feishu-msg.md`：自定义输出路径（目录自动创建）。
  - `--tz bj`：时间显示/排序所用时区（默认北京时区，排序仍按 UTC 时间戳）。
  - `--dry-run`：仅打印预览，不写文件。

## 6. 计算细节（与 info_writer 对齐）
- 维度：`timeliness`、`game_relevance`、`mobile_game_relevance`、`ai_relevance`、`tech_relevance`、`quality`、`insight`、`depth`、`novelty`。
- 默认权重：`0.09/0.14/0.14/0.09/0.05/0.18/0.18/0.08/0.05`（可覆盖，与脚本常量保持一致）。
- 总分计算：加权平均，范围限制在 `[1.0, 5.0]`，四舍五入两位小数；若所有权重为 0，则总分返回 0（该条可被 `--min-score` 过滤）。

## 7. 异常与降级
- 无可用条目：输出带有“暂无数据”的占位文本，或者返回非 0 退出码（由 `--strict` 开关控制）。
- 数据缺字段：
  - 无 `publish`：按 id 倒序作为次级排序依据。
  - 无 `source`：显示为 `(unknown)`。
  - 标题/链接为空：丢弃该条。

## 8. 发送到飞书的推荐路径
- 使用 `feishu_bot_today.py` 读取该 md 并发送：
  - 推荐 `--as-card`：卡片的 `markdown` 元素对本格式兼容性最好。
  - 可选 `--as-post`：若需要“普通对话气泡”，脚本已提供简易 Markdown→post 的转换；对复杂列表/样式容忍度不如卡片。
- 示例：
  ```
  export FILE=$(python -c 'import datetime as d;print(d.datetime.now().strftime("data/feishu-msg/%Y%m%d-feishu-msg.md"))')
  python news-collector/manager/feishu_writer.py --hours 24 --output "$FILE"
  python news-collector/manager/feishu_bot_today.py --chat-name "日报群" --file "$FILE" --as-card --title "今日推荐"
  ```

## 9. 实现建议
- 循环复用 `info_writer.py` 的“评分计算”函数或复制其权重逻辑，避免分散定义。
- SQL Join 一次性取齐字段；Python 侧进行权重合成与排序。
- 输出前做一层“标题/链接清洗与长度截断”。
- 单元测试：
  - 评分合成函数的边界（权重全 0、缺维度）；
  - 排序/选取（同分按时间、按 id）；
  - 渲染格式（示例行结构与链接合法性）。

## 10. 示例输出
```
**GAME**
1. (AI推荐:4.85)(sensortower) [Top 10 Worldwide Mobile Games By Revenue and Downloads in September 2025](https://sensortower.com/...)
2. (AI推荐:4.60)(naavik) [Holiday ASO Best Practices for 2025 — 5 Quick Tips to Maximize Success](https://naavik.co/...)

**TECH**
1. (AI推荐:4.70)(qbitai-news) [美团视频生成模型来了！一出手就是开源SOTA](https://www.qbitai.com/...)
2. (AI推荐:4.45)(deepmind) [Introducing the Gemini 2.5 Computer Use model](https://deepmind.google/...)
```
