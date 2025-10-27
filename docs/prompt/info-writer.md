manager 下的 info_writer.py 脚本需要支持参数设置近 X 小时（默认 24 小时）。

根据近 X 小时参数，从 info.db 的 info 表中过滤信息（仅保留“发布时间 publish 能被解析为时间且位于窗口内”的记录）。

将符合条件的信息（来源、发布时间、标题、链接）输出为便于阅读的 HTML 文件，保存到 data/output 目录下，文件名格式：

- YYYMMDD-HHMMSS-info.html（例如 20251026-142355-info.html）

HTML 输出规范：
- 文档结构：
  - <!doctype html>
  - <html lang="zh-CN">，<meta charset="utf-8">，<title>最近 X 小时资讯汇总</title>
- 头部信息：页面顶部展示“生成时间（北京时间）”与“合计条数”。
- 分组展示：按“品类 category”分组，组内不再按来源拆分；每条标题显示为“{source}:{title}”。
- 条目格式：使用卡片样式；time 显示为北京时间，`datetime` 为 ISO-8601（+08:00）。
- 字符集：UTF-8；可在 <head> 中内联一小段 CSS 以提升可读性（如字号、行距、颜色）。

排序与筛选：
- 仅包含能够解析为时间的 publish 值；其余记录忽略。
- 组内按“加权总分”降序，再以发布时间降序打破并列。
- 默认权重遵循 docs/prompt/ai-evaluation-spec.md：
  - timeliness 0.20，game_relevance 0.25，ai_relevance 0.20，tech_relevance 0.15，quality 0.20。
  - Writer 在渲染阶段根据各维度分数动态计算总分，不依赖数据库中的 final_score。
  - 如需为不同用户群体定制，可按需求调整 info_writer.py 中的权重映射。

可选增强（非强制）：
- 在页面顶部加入来源统计（每个来源的条目数）。
- 支持 --since 绝对时间参数（如 2025-10-24T00:00:00Z）作为 --hours 的替代。
