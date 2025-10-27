# 增加信息源的实施指南

为了保持各信息源脚本的一致性，新增信息源时请遵循以下约定。可参考 `news-collector/scraping` 目录下现有的脚本（例如 `game/gamedeveloper.rss.py`、`game/naavik.digest.py`、`game/nikopartners.blog.py` 等）完成实现。

## 目录结构与命名
- 所有脚本放在 `news-collector/scraping/<category>/` 目录下，`<category>` 与返回的 `category` 字段保持一致，例如游戏资讯放在 `news-collector/scraping/game/`。
- 文件名使用来源域名或品牌 + 数据来源类型（`rss.py`、`feed.py`、`blog.py` 等），全部小写并以点分隔，参考 `gameindustry.biz.rss.py`、`sensortower.blog.py`。
- 在脚本顶层定义 `SOURCE`（信息源标识）、`CATEGORY`（所属分类）、`UA`（必要时的 User-Agent）等常量，方便统一复用。

## 产出字段要求
- 每条列表结果需包含 `title`、`url`（或 `link`）、`source`、`category`、`published`（ISO 8601，UTC 时区）等字段，可按 `game/gamedeveloper.rss.py` 的 `process_entries` 实现。
- 若原始数据缺失发布时间，优先补齐为 UTC 时间；无法获取时可设为空字符串，但需在代码注释中说明原因。
- 额外字段（例如 `summary`、`author`）需保证下游消费者有兼容逻辑，默认不要随意增加。

## 列表抓取流程建议
- 在调研阶段先确认目标站点是否提供可用的 RSS/Atom 源（包括发布时间、链接是否完整），若存在且内容及时更新，请优先编写 RSS 抓取脚本；仅当确无可用订阅源时再实现 HTML 抓取。
- 将抓取逻辑拆分为纯函数：`fetch_feed`/`fetch_list_page` 负责网络请求；`parse_*` 负责解析和结构化数据。
- 网络请求统一使用 `requests` 或 `feedparser`，并设置合理的 `timeout`、`headers`。参考 `game/youxituoluo.com.latest.py`（HTTP 页面）与 `game/deconstructoroffun.rss.py`（RSS）。
- 解析时加入最小限度的容错：
  - RSS：像 `parse_dt` 一样按 `published_parsed`、`updated` 等字段兜底。
  - HTML：在选择器列表中按优先级尝试多个候选，避免单点失败，参考 `game/sensortower.blog.py` 的 `parse_article_list`。
- 结果列表默认按发布时间倒序排列。

## 详情页内容提取
- 提供 `fetch_article_detail(url: str) -> str` 或同等函数，返回正文纯文本，便于后续清洗。
- 在正文清洗阶段：
  - 删除脚本、广告、导航等噪声节点（参见 `game/gamedeveloper.rss.py` 的标签过滤）。
  - 统一替换特殊空白字符、合并多余换行（参考 `_clean_text` 实现）。
  - 允许根据站点特性补充额外规则，务必在注释中记录原因。

## 调试与自检
- 在模块末尾保留 `if __name__ == "__main__":` 块，打印最近若干条结果，便于快速验证抓取是否成功。
- 新增脚本后，至少本地运行一次 `python news-collector/scraping/<category>/<script>.py` 确认无异常。
- 若新增公共工具函数，可在模块顶部定义，保持纯函数便于复用。

## 依赖与文档
- 新增外部依赖请同步更新根目录的 `requirements.txt`，并在提交说明中解释用途。
- 若目标站点需要特殊配置（如 Cookie、API Key、访问频率限制），请在 `docs/` 目录补充说明，确保他人可以复现。

遵循以上要求可以保证新的信息源脚本与现有实现保持一致，便于维护和扩展。
